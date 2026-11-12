"""Experiment: "listen again with the person's phrases as a hint".

    .venv/bin/python -m eval.relisten M04           (collect; about 6 min of WhisperKit)
    .venv/bin/python -m eval.relisten M04 --report  (analyse what was collected)
    .venv/bin/python -m eval.relisten M04 --verify  (fast use: confirm only the Brain's guess)

WhisperKit accepts a text hint with each clip. Here every test clip is
transcribed again several times, each time hinted with a different dozen of
the profile's phrases. If one of the hinted phrases comes back almost word for
word, and WhisperKit was confident about it, that phrase "fits the sound".

The question this answers honestly: how often does that find the right
phrase when the plain transcript could not, and how often does it "hear" a
listed phrase that was NOT said (measured on words outside the profile)?
Results are cached in datasets/torgo/relisten_<speaker>.jsonl.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.torgo_data import ROOT, WAV, extract, load_index  # noqa: E402
from eval.torgo_profile import build  # noqa: E402

CHUNK = 12


def hint_chunks(profile: dict) -> list[list[str]]:
    """The profile's phrases a dozen at a time, plus the command words as one chunk."""
    phrases = profile["phrases"]
    chunks = [phrases[i:i + CHUNK] for i in range(0, len(phrases), CHUNK)]
    words = [w for c in profile["commands"] for w in c.get("said_as", [])]
    return chunks + ([words] if words else [])


def collect(speaker: str) -> Path:
    from asr.real import WhisperKitASR
    setup = build(speaker)
    chunks = hint_chunks(setup["profile"])
    clips = sorted({c for fold in setup["folds"] for c in fold["test"]} | set(setup["outside"]))
    files = {u["id"]: u["file"] for u in load_index() if u["speaker"] == speaker}
    extract({files[c] for c in clips})
    out = ROOT / f"relisten_{speaker}.jsonl"
    done = {json.loads(line)["clip"] for line in out.read_text().splitlines()} if out.exists() else set()
    asr = WhisperKitASR()
    with open(out, "a") as f:
        for n, clip in enumerate(clips, 1):
            if clip in done:
                continue
            passes = []
            for chunk in chunks:
                t = asr.transcribe(str(WAV / files[clip]), prompt=" ".join(chunk))
                passes.append({"heard": t.text, "logprob": round(t.avg_logprob, 3), "ms": round(t.latency_ms)})
            f.write(json.dumps({"clip": clip, "passes": passes}) + "\n")
            f.flush()
            if n % 25 == 0:
                print(f"  {n}/{len(clips)}", flush=True)
    return out


def best_fit(passes: list[dict], chunks: list[list[str]], profile: dict) -> tuple[str | None, float, float]:
    """(the hinted phrase that fits best or None, how well its words match 0..1, WhisperKit's confidence)."""
    from repair.support import _coverage
    from repair.store import normalize
    names = {w: c["name"] for c in profile["commands"] for w in c.get("said_as", [])}
    best = (None, 0.0, -9.0)
    for one, chunk in zip(passes, chunks):
        heard = normalize(one["heard"]).split()
        for phrase in chunk:
            a, b = _coverage(heard, normalize(phrase).split())
            match = min(a, b)
            if (match, one["logprob"]) > (best[1], best[2]):
                best = (names.get(phrase, phrase), match, one["logprob"])
    return best


def report(speaker: str) -> None:
    setup = build(speaker)
    chunks = hint_chunks(setup["profile"])
    rows = [json.loads(line) for line in (ROOT / f"relisten_{speaker}.jsonl").read_text().splitlines()]
    results = json.loads((Path(__file__).parent / "results" / f"torgo_{speaker}.json").read_text())
    before = {r["clip"]: r for r in results["records"]["fresh"]}
    inside = [r for r in rows if r["clip"] in setup["truth"]]
    outside = [r for r in rows if r["clip"] not in setup["truth"]]
    ms = sorted(sum(p["ms"] for p in r["passes"]) for r in rows)
    print(f"{speaker}: {len(inside)} test clips, {len(outside)} outside-profile clips; {len(chunks)} hinted passes per clip, "
          f"median {ms[len(ms) // 2]} ms in total")
    print(f"  {'needs words>=':>14}{'and logprob>=':>15} | {'fits a phrase':>14}{'of those right':>16}{'rescues a miss':>16} | outside: 'hears' a phrase")
    for match_min in (0.8, 0.9, 1.0):
        for logprob_min in (-0.5, -0.3, -0.2):
            fit = right = rescued = 0
            for r in inside:
                phrase, match, logprob = best_fit(r["passes"], chunks, setup["profile"])
                if phrase and match >= match_min and logprob >= logprob_min:
                    fit += 1
                    ok = phrase == setup["truth"][r["clip"]]
                    right += ok
                    rescued += ok and before[r["clip"]]["outcome"] == "not found"
            false = sum(1 for r in outside
                        if (lambda p, m, l: p and m >= match_min and l >= logprob_min)(*best_fit(r["passes"], chunks, setup["profile"])))
            print(f"  {match_min:>14.1f}{logprob_min:>15.1f} | {fit:>10}/{len(inside):<3}{right:>10}/{fit:<5}{rescued:>12}/{sum(b['outcome'] == 'not found' for b in before.values()):<3} | {false}/{len(outside)}")


def verify(speaker: str) -> list[dict]:
    """The fast use: hint WhisperKit with ONLY the Brain's guess for each clip
    (taken from the saved evaluation results) and see whether the guess comes
    back. Cached in datasets/torgo/verify_<speaker>.json."""
    from asr.real import WhisperKitASR
    from repair.profile import find_command
    from repair.store import normalize
    from repair.support import _coverage
    out = ROOT / f"verify_{speaker}.json"
    if out.exists():
        return json.loads(out.read_text())
    setup = build(speaker)
    files = {u["id"]: u["file"] for u in load_index() if u["speaker"] == speaker}
    results = json.loads((Path(__file__).parent / "results" / f"torgo_{speaker}.json").read_text())
    asr, rows = WhisperKitASR(), []
    for group in ("fresh", "outside"):
        records = [r for r in results["records"][group] if r.get("choice") and r.get("heard")]
        extract({files[r["clip"]] for r in records})
        for r in records:
            command = find_command(setup["profile"], r["choice"])
            text = (command.get("said_as") or [r["choice"].replace("_", " ")])[0] if command else r["choice"]
            t = asr.transcribe(str(WAV / files[r["clip"]]), prompt=text)
            a, b = _coverage(normalize(t.text).split(), normalize(text).split())
            rows.append({"group": group, "clip": r["clip"], "right": r["choice"] == r["truth"], "match": min(a, b),
                         "logprob": t.avg_logprob, "ms": t.latency_ms, "heard2": t.text, "choice": r["choice"],
                         "truth": r["truth"], "outcome": r["outcome"]})
    out.write_text(json.dumps(rows))
    return rows


def verify_report(speaker: str) -> None:
    import statistics
    rows = verify(speaker)
    inside = [x for x in rows if x["group"] == "fresh"]
    outside = [x for x in rows if x["group"] == "outside"]
    right, wrong = [x for x in inside if x["right"]], [x for x in inside if not x["right"]]
    print(f"{speaker}: one confirming listen takes {statistics.median(x['ms'] for x in rows):.0f} ms (median). "
          f"Brain guess right for {len(right)} of {len(inside)} test clips; {len(outside)} outside-profile clips.")
    print(f"  {'words>=':>8}{'logprob>=':>10} | confirms a RIGHT guess | confirms a WRONG guess | confirms a guess for an outside word")
    for match_min in (0.8, 0.9):
        for logprob_min in (-0.4, -0.3, -0.2):
            ok = lambda x: x["match"] >= match_min and x["logprob"] >= logprob_min  # noqa: E731
            print(f"  {match_min:>8.1f}{logprob_min:>10.1f} | {sum(map(ok, right)):>10}/{len(right):<10} | "
                  f"{sum(map(ok, wrong)):>10}/{len(wrong):<10} | {sum(map(ok, outside))}/{len(outside)}")


if __name__ == "__main__":
    who = next((a for a in sys.argv[1:] if not a.startswith("--")), "M04")
    if "--verify" in sys.argv:
        verify_report(who)
    elif "--report" in sys.argv:
        report(who)
    else:
        print("saved", collect(who))
