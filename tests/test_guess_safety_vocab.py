"""Checks: guess again (up to 3), ranking by group, forgetting a wrong fast-path
example, safe/risky commands, and the personal vocabulary. Stand-in model and
throwaway profiles (no LM Studio needed). Run:
    .venv/bin/python tests/test_guess_safety_vocab.py
"""

import builtins
import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import config  # noqa: E402
import demo_text  # noqa: E402
from repair.guess import guess_messages, guess_schema  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.profile import is_risky, load_profile  # noqa: E402
from repair.rank import SOMETHING_ELSE, rank_options  # noqa: E402
from repair.store import add_example, add_vocabulary, open_profile, undo_last  # noqa: E402

BASE = load_profile(config.BASE_PROFILE_PATH)
BASE["examples"] = []
P = BASE["phrases"]
NO_ARGS = {"app": "", "text": ""}
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


class Model:
    """Stand-in LLM: a fixed intent, and a list of guesses given in turn."""
    def __init__(self, choice="P1", confidence="low", args=None, guesses=()):
        self.choice, self.confidence, self.args = choice, confidence, args or NO_ARGS
        self.guesses, self.guess_prompts, self.calls, self.temperatures = list(guesses), [], 0, []

    def complete(self, messages, schema, purpose="intent", temperature=0.0):
        self.calls += 1
        if purpose == "guess":
            self.guess_prompts.append(messages[0]["content"])
            self.temperatures.append(temperature)
            text, words = self.guesses.pop(0) if self.guesses else ("Nothing more.", [])
            return LLMReply(json.dumps({"guess": text, "key_words": words}), 1, 0, 0)
        return LLMReply(json.dumps({"choice": self.choice, "args": self.args, "confidence": self.confidence,
                                    "options": ["P1", "P2", "P3", "P4"]}), 1, 0, 0)


def session(typed, model, profile=None):
    """Run the demo; `typed` includes correction-window answers ("" = nothing typed)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "profile.json"
        path.write_text(json.dumps(profile or BASE))
        inputs, printed, said = iter(typed + ["q"]), [], []
        real_input, real_print = builtins.input, builtins.print
        builtins.input = lambda prompt="": (printed.append(str(prompt)), next(inputs))[1]
        builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
        demo_text.get_llm = lambda: model
        demo_text.get_tts = lambda: type("V", (), {"speak": lambda s, t: said.append(t)})()
        demo_text.wait_for_cancel = lambda seconds: (printed.append("[cancel window opened]"), next(inputs) or None)[1]
        try:
            demo_text.main(["--profile", str(path)])
        finally:
            builtins.input, builtins.print = real_input, real_print
        return printed, said, open_profile(path)


def has(printed, text):
    return any(text in line for line in printed)


GUESSES = [("Could you help me see?", []), ("Could I have some Pepsi?", ["Pepsi"]),
           ("Could I have some tea?", ["tea"])]

print("-- 1. Guess again --")
m = Model(guesses=GUESSES)
printed, said, p = session(["cud eye haf sum pep see", "n", "4", "n", "y", ""], m)
check("second guess accepted", said, ["Could I have some Pepsi?"])
check("second guess was told the rejected one",
      ("REJECTED" in m.guess_prompts[1], '"Could you help me see?"' in m.guess_prompts[1]), (True, True))
check("first guess had nothing rejected", "REJECTED" in m.guess_prompts[0], False)
check("first guess: temperature 0; guesses 2 and 3: more varied", m.temperatures[0] == 0
      and all(t == config.GUESS_AGAIN_TEMPERATURE for t in m.temperatures[1:]), True)
m = Model(guesses=GUESSES)
printed, said, p = session(["cud eye haf sum pep see", "n", "4", "n", "n", "n"], m)
check("three 'no's: no more guesses, nothing chosen",
      (said, has(printed, "No more guesses (3 tried)"), has(printed, "Nothing chosen.")), ([], True, True))
check("  ...exactly 3 guesses asked for", len(m.guess_prompts), 3)
check("  ...third guess told both rejected ones",
      ('"Could you help me see?"' in m.guess_prompts[2], '"Could I have some Pepsi?"' in m.guess_prompts[2]),
      (True, True))
m = Model(guesses=[("Could you help me see?", []), ("could you help me see", []), ("Could I have some Pepsi?", [])])
printed, said, _ = session(["cud eye haf sum pep see", "n", "4", "n", "y", ""], m)
check("a repeated guess is skipped, not asked again",
      (has(printed, "repeated a rejected one"), said), (True, ["Could I have some Pepsi?"]))

print("-- 2. Ranking by group --")
shown = rank_options("cud eye haf sum pep see", BASE, [], exclude=(P[0],), first_group="phrase")
check("top guess was a phrase: phrases first", shown, [P[2], P[1], P[3], SOMETHING_ELSE])
shown = rank_options("reed my lass ee mail", BASE, [], exclude=("read_latest_email",), first_group="command")
check("top guess was a command: commands first", all(s in ("open_app", "reply_email", "scroll_down")
                                                     for s in shown[:3]), True)
shown = rank_options("opn safari", BASE, [], exclude=(), first_group="command")
check("most similar command first within its group", shown[0], "open_app")
printed, _, _ = session(["scrol emale", "n", ""], Model(choice="scroll_down"))
start = printed.index("\nWhich one did you mean?")
check("demo: after 'no' to a command, commands are shown first",
      [line.split(". ", 1)[1] in ("open_app", "read_latest_email", "reply_email") for line in printed[start + 1:start + 4]],
      [True, True, True])

print("-- 3. Forgetting a wrong fast-path example --")
learned = copy.deepcopy(BASE)
add_example(learned, "aim thur stee cud eye haf sum wah tur", P[0])
printed, said, p = session(["aim thur stee cud eye haf sum wah tur", "n", "2", ""], Model(), learned)
check("exact repeat cancelled: old example deleted", has(printed, "FORGOT the learned example that caused it"), True)
check("  ...only the new meaning is left", [(e["heard"], e["meant"]) for e in p["examples"]],
      [("aim thur stee cud eye haf sum wah tur", said[-1])])
printed, said, p = session(["aim thur stee cud eye haf sum wah ter", "n", "2", ""], Model(), learned)
check("97% match cancelled: deleted too (95% or more)", has(printed, "FORGOT"), True)
add_example(learned, "nee drink", P[0])
printed, said, p = session(["nee drinks", "n", "", ], Model(), learned)
check("below 95% (90%): fast path cancelled, but the example is kept",
      (has(printed, "FAST PATH"), has(printed, "FORGOT"), "nee drink" in [e["heard"] for e in p["examples"]]),
      (True, False, True))

print("-- 4. Safe and risky commands --")
check("safety marks from the profile", [is_risky(BASE, c) for c in
                                        ("open_app", "scroll_down", "read_latest_email", "reply_email")],
      [False, False, False, True])
check("phrases are not risky", is_risky(BASE, P[2]), False)
unmarked = copy.deepcopy(BASE)
unmarked["commands"].append({"name": "call_contact", "description": "Call someone.", "args": {}, "say": "Calling."})
check("a command with no safety mark counts as risky", is_risky(unmarked, "call_contact"), True)
reply = Model(choice="reply_email", confidence="high", args={"app": "", "text": "see you soon"})
printed, said, _ = session(["reply see you soon", "y"], reply)
check("risky at HIGH confidence: asks first", has(printed, "Did you mean: reply_email (text: see you soon)?"), True)
check("  ...yes: acts, with no cancel window", (said, has(printed, "can't be cancelled afterwards"),
                                                has(printed, "[cancel window opened]")),
      (["Replying: see you soon"], True, False))
printed, said, _ = session(["reply see you soon", "n", ""], reply)
check("risky, answered no: not sent", "Replying: see you soon" in said, False)
learned = copy.deepcopy(BASE)
add_example(learned, "replai see yu soon", "reply_email", {"text": "see you soon"})
printed, said, _ = session(["replai see yu soon", "y"], Model(), learned)
check("risky from the FAST PATH: still asks first",
      (has(printed, "FAST PATH"), has(printed, "Did you mean: reply_email"), said),
      (True, True, ["Replying: see you soon"]))
printed, said, _ = session(["scrol dwn", ""], Model(choice="scroll_down", confidence="high"))
check("safe at high confidence: acts at once, then cancel window",
      (has(printed, "Did you mean"), said, has(printed, "[cancel window opened]")), (False, ["Scrolling down."], True))
with tempfile.TemporaryDirectory() as tmp:
    old = copy.deepcopy(BASE)
    for c in old["commands"]:
        c.pop("safety")
    old.pop("vocabulary")
    path = Path(tmp) / "profile.json"
    path.write_text(json.dumps(old))
    upgraded = open_profile(path)
    check("older working profile: safety marks and vocabulary filled in from the base",
          ([c.get("safety") for c in upgraded["commands"]], upgraded["vocabulary"]),
          ([c["safety"] for c in BASE["commands"]], BASE["vocabulary"]))

print("-- 5. Personal vocabulary --")
check("base vocabulary", BASE["vocabulary"], ["Pepsi", "tea", "coffee", "Anna"])
prompt = guess_messages(BASE, "x")[0]["content"]
check("vocabulary is in the free-guess prompt", "Pepsi, tea, coffee, Anna" in prompt, True)
check("  ...with 'only if it sounds like a word they said'", "ONLY if it sounds like a word they actually said"
      in prompt, True)
check("guess must return key words (and whether it is a question)", guess_schema()["required"], ["guess", "key_words", "kind"])
m = Model(guesses=[("Can Ben bring my glasses?", ["Ben", "glasses", "bring", "the"])])
printed, said, p = session(["kan ben brin ma glasses", "n", "4", "y", ""], m)
check("confirmed guess: new words added", p["vocabulary"][-3:], ["Ben", "glasses", "bring"])
check("  ...and announced", has(printed, "New words for your vocabulary: Ben, glasses, bring"), True)
m = Model(guesses=[("Could I have some Pepsi?", ["Pepsi", "Paris"])])
_, _, p = session(["cud eye haf sum pep see", "n", "4", "y", ""], m)
check("known word not duplicated; word not in the sentence not added", p["vocabulary"], BASE["vocabulary"])
m = Model(guesses=[("Can Ben bring my glasses?", ["Ben", "glasses"])])
printed, _, p = session(["kan ben brin ma glasses", "n", "4", "y", "n", ""], m)
check("cancelled in the window: words removed again",
      (has(printed, "removed from the vocabulary: Ben, glasses"), p["vocabulary"]), (True, BASE["vocabulary"]))
p = copy.deepcopy(BASE)
words = add_vocabulary(p, ["Anna", "kitchen"], "Anna is in the kitchen")
add_example(p, "ana kichen", "Anna is in the kitchen", new_words=words)
undo_last(p)
check("undo removes words that confirmation added", p["vocabulary"], BASE["vocabulary"])
check("common words never added", add_vocabulary(copy.deepcopy(BASE), ["the", "please", "could"],
                                                 "Could you please close the door"), [])

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
