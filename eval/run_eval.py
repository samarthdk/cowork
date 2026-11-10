"""The before/after evaluation on held-out TORGO recordings.

    .venv/bin/python -m eval.run_eval M04            (print the table, save results)
    .venv/bin/python -m eval.run_eval M04 --chart    (also draw the chart for the slides)

"Before" = raw WhisperKit: does its text match what was said?
"After"  = Second Voice: does the person end up with the right phrase or
           command, and how many questions did it take?

Every test clip goes through the real conversation engine (engine/session.py)
with a simulated user who answers truthfully: "yes" only when the guess is
right, picks the right square when it is offered, and never corrects by voice.
Outcomes, from best to worst:

    instant         right, at high confidence, no question asked
    one question    right after "Did you mean ...?" -> yes
    two questions   right after "no" -> picked from the options grid
    not found       the right answer was never offered
    wrong           a WRONG phrase or action at high confidence (the worst case)

Model answers are cached (temperature 0, so they are repeatable), which makes
reruns instant. Results are saved under eval/results/.
"""

import copy
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from choose.answers import Answer  # noqa: E402
from engine.session import Session  # noqa: E402
from eval.torgo_data import ROOT, load_transcripts, normalize, word_error_rate  # noqa: E402
from eval.torgo_profile import COMMAND_WORDS, build, enrolled_profile  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.rank import SOMETHING_ELSE  # noqa: E402

RESULTS = config.ROOT / "eval" / "results"
CACHE = ROOT / "llm_cache.jsonl"
OUTCOMES = ("instant", "one question", "two questions", "not found", "wrong")


class CachedLLM:
    """Remembers every model answer on disk, keyed by the exact request."""

    def __init__(self, llm):
        self.llm, self.cache, self.new_calls = llm, {}, 0
        if CACHE.exists():
            for line in CACHE.read_text().splitlines():
                record = json.loads(line)
                self.cache[record["key"]] = record

    def complete(self, messages, schema, purpose="intent", temperature=0.0) -> LLMReply:
        key = hashlib.sha1(json.dumps([config.LLM_MODEL, messages, schema, purpose, temperature],
                                      sort_keys=True).encode()).hexdigest()
        if key not in self.cache:
            reply = self.llm.complete(messages, schema, purpose=purpose, temperature=temperature)
            self.cache[key] = {"key": key, "text": reply.text, "latency_ms": reply.latency_ms,
                               "tokens": reply.completion_tokens, "tps": reply.tokens_per_s,
                               "finish": reply.finish_reason}
            self.new_calls += 1
            with open(CACHE, "a") as f:
                f.write(json.dumps(self.cache[key]) + "\n")
        r = self.cache[key]
        return LLMReply(r["text"], r["latency_ms"], r["tokens"], r["tps"], r["finish"])


class Silent:
    def speak(self, text):
        pass


def simulate(profile: dict, llm, heard: str, truth: str | None) -> dict:
    """One clip through the real engine with a truthful simulated user."""
    events = []
    session = Session(copy.deepcopy(profile), llm, Silent(), on_event=events.append)
    flow = session.hear(heard)
    questions, performed = 0, None
    try:
        question = next(flow)
        while True:
            if question.kind == "yes_no" and question.stage == "did_you_mean":
                questions += 1
                answer = Answer("yes" if question.data["choice"] == truth else "no")
            elif question.kind == "choice":
                questions += 1
                answer = (Answer("pick", question.options.index(truth)) if truth in question.options
                          else Answer("none"))
            else:   # correction window, guesses: the simulated user stays silent
                answer = None
            question = flow.send(answer)
    except StopIteration:
        pass
    brain = next((e for e in events if e["kind"] in ("brain", "fast_path")), None)
    spoken = [e for e in events if e["kind"] == "speak"]
    learned = any(e["kind"] == "learned" for e in events)
    if spoken:
        performed = brain["intent"]["choice"] if not learned else truth
    intent = brain["intent"] if brain else {}
    if performed is None:
        outcome = "not found"
    elif performed != truth:
        outcome = "wrong"
    else:
        outcome = ("instant", "one question", "two questions")[min(questions, 2)]
    return {"heard": heard, "truth": truth, "outcome": outcome, "questions": questions,
            "choice": intent.get("choice"), "confidence": intent.get("confidence"),
            "problems": intent.get("problems", []), "source": intent.get("stats", {}).get("source"),
            "brain_ms": intent.get("stats", {}).get("latency_ms")}


def raw_baseline(setup: dict, heard: dict, clips: list[str]) -> dict:
    """WhisperKit alone: is its text exactly what was said (ignoring case and
    punctuation)? And a simple text-matching baseline: the nearest profile entry."""
    from rapidfuzz import fuzz, process
    word_of = {name: word for word, (name, _, _) in COMMAND_WORDS.items()}
    entries = {normalize(p): p for p in setup["profile"]["phrases"]}
    entries.update({word_of[c["name"]]: c["name"] for c in setup["profile"]["commands"]})
    exact = nearest = 0
    for clip in clips:
        said = normalize(heard[clip]["heard"])
        truth = setup["truth"][clip]
        exact += entries.get(said) == truth
        best = process.extractOne(said, list(entries), scorer=fuzz.WRatio) if said else None
        nearest += bool(best) and entries[best[0]] == truth
    return {"exact": exact / len(clips), "nearest": nearest / len(clips), "clips": len(clips)}


def summarize(records: list[dict]) -> dict:
    n = len(records)
    counts = {o: sum(r["outcome"] == o for r in records) for o in OUTCOMES}
    right = counts["instant"] + counts["one question"] + counts["two questions"]
    times = [r["brain_ms"] for r in records if r["brain_ms"] is not None and r["source"] == "llm"]
    return {"clips": n, **{o: counts[o] / n for o in OUTCOMES}, "reached": right / n,
            "questions_per_request": sum(r["questions"] for r in records) / n,
            "fast_path": sum(r["source"] == "fast_path" for r in records) / n,
            "brain_ms_median": statistics.median(times) if times else None,
            "brain_ms_p90": sorted(times)[int(0.9 * (len(times) - 1))] if times else None}


def evaluate(speaker: str, variant: str = "plain", verbose: bool = True) -> dict:
    from repair import get_llm
    config.LLM = "real"
    llm = CachedLLM(get_llm())
    setup = build(speaker)
    heard = load_transcripts(speaker, variant)
    plain = load_transcripts(speaker, "plain")
    start = time.monotonic()

    fresh_records, enrolled_records, outside_records = [], [], []
    seen = set()
    for fold in range(len(setup["folds"])):
        enrolled = enrolled_profile(setup, fold, variant)
        for clip in setup["folds"][fold]["test"]:
            truth, text = setup["truth"][clip], heard[clip]["heard"]
            enrolled_records.append({"clip": clip, "fold": fold, "enrolled_item": normalize(setup["reference"][clip]) in setup["enrolled_once"],
                                     **simulate(enrolled, llm, text, truth)} if text else
                                    {"clip": clip, "fold": fold, "outcome": "not found", "questions": 0, "heard": "",
                                     "truth": truth, "source": None, "brain_ms": None, "confidence": None,
                                     "enrolled_item": normalize(setup["reference"][clip]) in setup["enrolled_once"]})
            if clip not in seen:
                seen.add(clip)
                fresh_records.append({"clip": clip, **simulate(setup["profile"], llm, text, truth)} if text else
                                     {"clip": clip, "outcome": "not found", "questions": 0, "heard": "", "truth": truth,
                                      "source": None, "brain_ms": None, "confidence": None})
        if fold == 0:
            for clip in setup["outside"]:
                text = heard[clip]["heard"]
                if text:
                    outside_records.append({"clip": clip, **simulate(enrolled, llm, text, None)})

    test_clips = sorted(seen)
    all_pairs = [(setup["reference"][c], plain[c]["heard"]) for c in test_clips]
    result = {
        "speaker": speaker, "transcripts": variant, "model": config.LLM_MODEL,
        "profile": {"phrases": len(setup["profile"]["phrases"]), "commands": len(setup["profile"]["commands"])},
        "test_clips": len(test_clips), "enrollment_clips_per_fold": len(setup["folds"][0]["enroll"]),
        "raw_whisperkit": {**raw_baseline(setup, plain, test_clips), "wer": word_error_rate(all_pairs)},
        "second_voice_fresh": summarize(fresh_records),
        "second_voice_enrolled": summarize(enrolled_records),
        "enrolled_items_only": summarize([r for r in enrolled_records if r["enrolled_item"]]),
        "never_heard_items_only": summarize([r for r in enrolled_records if not r["enrolled_item"]]),
        "outside_profile": {"clips": len(outside_records),
                            "confident_wrong": sum(r["outcome"] == "wrong" for r in outside_records) / max(len(outside_records), 1),
                            "questions_per_request": sum(r["questions"] for r in outside_records) / max(len(outside_records), 1)},
        "asr_ms_median": statistics.median(h["latency_ms"] for h in plain.values()),
        "records": {"fresh": fresh_records, "enrolled": enrolled_records, "outside": outside_records},
    }
    if verbose:
        show(result)
        print(f"({llm.new_calls} new model calls, {time.monotonic() - start:.0f} s)")
    return result


def show(result: dict) -> None:
    raw = result["raw_whisperkit"]
    print(f"\nSpeaker {result['speaker']}: {result['profile']['phrases']} phrases + {result['profile']['commands']} commands; "
          f"{result['test_clips']} held-out test clips; {result['enrollment_clips_per_fold']} enrollment clips")
    print(f"  Raw WhisperKit:  exactly right {raw['exact']:.0%}   (word error rate {raw['wer']:.0%}; "
          f"nearest-text matching would get {raw['nearest']:.0%})")
    header = f"  {'':34}" + "".join(f"{o:>15}" for o in OUTCOMES) + f"{'reached':>10}{'questions':>11}{'fast path':>11}"
    print(header)
    for label, key in (("Second Voice, no enrollment", "second_voice_fresh"),
                       ("Second Voice, after enrollment", "second_voice_enrolled"),
                       ("   - items it has heard before", "enrolled_items_only"),
                       ("   - items known only as text", "never_heard_items_only")):
        s = result[key]
        if not s["clips"]:
            continue
        print(f"  {label:34}" + "".join(f"{s[o]:>15.0%}" for o in OUTCOMES)
              + f"{s['reached']:>10.0%}{s['questions_per_request']:>11.2f}{s['fast_path']:>11.0%}   (n={s['clips']})")
    o = result["outside_profile"]
    print(f"  Words outside the profile ({o['clips']} clips): confidently wrong {o['confident_wrong']:.0%}, "
          f"{o['questions_per_request']:.1f} questions each")
    s = result["second_voice_enrolled"]
    if s["brain_ms_median"]:
        print(f"  Time: WhisperKit {result['asr_ms_median']:.0f} ms median; Brain {s['brain_ms_median']:.0f} ms median, "
              f"{s['brain_ms_p90']:.0f} ms 90th percentile")


def save(result: dict, tag: str = "") -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"torgo_{result['speaker']}{tag}.json"
    path.write_text(json.dumps(result, indent=1))
    return path


if __name__ == "__main__":
    who = next((a for a in sys.argv[1:] if not a.startswith("--")), "M04")
    result = evaluate(who)
    print("saved", save(result))
    if "--chart" in sys.argv:
        from eval.chart import draw
        print("chart", draw(result))
