"""Checks the low-confidence flow in scripts/demo_text.py, with a stand-in model
(no LM Studio needed) and a throwaway copy of the profile. Run:
    .venv/bin/python tests/test_low_confidence.py
"""

import builtins
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import config  # noqa: E402
import demo_text  # noqa: E402
from repair import repair  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.profile import load_profile  # noqa: E402
from repair.rank import SOMETHING_ELSE, rank_options  # noqa: E402

BASE = load_profile(config.BASE_PROFILE_PATH)
P = BASE["phrases"]
NO_ARGS = {"app": "", "text": ""}
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


class Model:
    """Stand-in LLM: a fixed intent reply, and a fixed free guess."""
    def __init__(self, reply, guess="Can you open the window?"):
        self.reply, self.guess = reply, guess

    def complete(self, messages, schema, purpose="intent"):
        if purpose == "guess":
            return LLMReply(json.dumps({"guess": self.guess}), 1, 0, 0)
        return LLMReply(json.dumps(self.reply), 1, 0, 0)


class Voice:
    def __init__(self):
        self.said = []

    def speak(self, text):
        self.said.append(text)


def run_demo(reply, typed, heard="some garbled words", guess="Can you open the window?", profile=None):
    """Run the demo for one sentence with the given keyboard answers.
    Returns (spoken, questions asked, printed lines, saved profile)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "profile.json"
        path.write_text(json.dumps(profile or BASE))
        inputs = iter([heard] + typed + ["q"])
        asked, printed, voice = [], [], Voice()
        real_input, real_print = builtins.input, builtins.print
        builtins.input = lambda prompt="": (asked.append(prompt.strip()), next(inputs))[1]
        builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
        demo_text.get_llm, demo_text.get_tts = (lambda: Model(reply, guess)), (lambda: voice)
        try:
            demo_text.main(["--profile", str(path)])
        finally:
            builtins.input, builtins.print = real_input, real_print
        return voice.said, asked, printed, json.loads(path.read_text())


def options_shown(printed):
    start = printed.index("\nWhich one did you mean?")
    return [line.split(". ", 1)[1] for line in printed[start + 1:start + 5]]


print("-- Option order --")
i = repair("x", BASE, Model({"choice": "P3", "args": NO_ARGS, "confidence": "low",
                             "options": ["P3", "P4", "P1", "P2"]}))
check("intent options keep the model's order", i.options, [P[2], P[3], P[0], P[1]])
ranked = rank_options("plees cal my dotter", BASE, [], exclude=(P[0],))
check("ranked by similarity: daughter first", ranked[0], P[2])
check("rejected guess left out; 3 options + 'Something else'",
      (P[0] in ranked, len(ranked), ranked[-1]), (False, 4, SOMETHING_ELSE))
ranked = rank_options("uh", BASE, [P[3], P[1]], exclude=())
check("no similarity at all: model's order breaks the tie", ranked[:2], [P[3], P[1]])

print("-- 'Did you mean' --")
low_p3 = {"choice": "P3", "args": NO_ARGS, "confidence": "low", "options": ["P3", "P4", "P1", "P2"]}
said, asked, _, _ = run_demo(low_p3, ["y"])
check("asks 'Did you mean' (full stop dropped)", asked[1], "Did you mean: Please call my daughter? (y/n)")
check("yes: acts on it", said, [P[2]])
said, asked, _, _ = run_demo(low_p3, ["what", "y"])
check("unclear answer: asks again", asked[1] == asked[2], True)
_, asked, _, _ = run_demo({"choice": "P1", "args": NO_ARGS, "confidence": "low", "options": []}, ["y"])
check("phrase ending in '?': no double '??'", asked[1], f"Did you mean: {P[0]} (y/n)")

print("-- After 'no': next-best options --")
said, asked, printed, _ = run_demo(low_p3, ["n", "1"], heard="aim in payn")
shown = options_shown(printed)
check("rejected guess not shown again", P[2] in shown, False)
check("most similar first", shown[0], P[3])
check("'Something else' last", shown[-1], SOMETHING_ELSE)
check("pick 1: speaks it", said, [P[3]])
said, _, _, _ = run_demo(low_p3, ["n", ""])
check("pick nothing: says nothing", said, [])

print("-- No usable choice --")
said, asked, printed, _ = run_demo({"choice": "P9", "args": NO_ARGS, "confidence": "high", "options": []},
                                   ["1"], heard="baffroom")
check("no 'Did you mean', straight to options", any("Did you mean" in q for q in asked), False)
check("  ...most similar first", (options_shown(printed)[0], said), (P[1], [P[1]]))

print("-- 'Something else' --")
said, asked, printed, saved = run_demo(low_p3, ["n", "4", "y"], heard="kan yu opn da windo")
check("guess clearly labelled", any('My guess (NOT from your list): "Can you open the window?"' in q
                                    for q in asked), True)
check("yes: speaks the guess", said, ["Can you open the window?"])
check("yes: added to phrases as P5", (saved["phrases"][-1], "New phrase added as P5: \"Can you open the window?\""
                                      in printed), ("Can you open the window?", True))
said, _, _, saved = run_demo(low_p3, ["n", "4", "n"])
check("no to the guess: nothing spoken, nothing added", (said, len(saved["phrases"])), ([], 4))
said, _, printed, saved = run_demo(low_p3, ["n", "4", "y"], guess="Please call my daughter!")
check("guess that is an existing phrase: reused, not added",
      (said, len(saved["phrases"])), ([P[2]], 4))
said, _, printed, _ = run_demo(low_p3, ["n", "4"], guess="x" * 500)
check("unusable guess (too long): says so, nothing spoken",
      (said, any("Couldn't make a guess" in line for line in printed)), ([], True))

print("-- Commands --")
said, asked, _, _ = run_demo({"choice": "open_app", "args": NO_ARGS, "confidence": "high", "options": []},
                             ["y", "Notes"])
check("open_app with no app: asks 'Did you mean'", asked[1], "Did you mean: open_app? (y/n)")
check("  ...yes: asks which app, then acts", (asked[2], said), ("app?", ["Opening Notes."]))
said, asked, _, _ = run_demo({"choice": "open_app", "args": {"app": "Safari", "text": ""}, "confidence": "low",
                              "options": []}, ["y"])
check("open_app with Safari: shows the app", asked[1], "Did you mean: open_app (app: Safari)? (y/n)")
check("  ...yes: no extra question", said, ["Opening Safari."])
said, asked, _, _ = run_demo({"choice": "scroll_down", "args": NO_ARGS, "confidence": "high", "options": []}, [])
check("high confidence: no question at all", (len(asked), said), (2, ["Scrolling down."]))

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
