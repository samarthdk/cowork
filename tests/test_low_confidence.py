"""Checks the low-confidence flow in scripts/demo_text.py and the option order,
with a stand-in model (no LM Studio needed). Run:
    .venv/bin/python tests/test_low_confidence.py
"""

import builtins
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import config  # noqa: E402
import demo_text  # noqa: E402
from repair import repair  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.profile import load_profile  # noqa: E402

PROFILE = load_profile(config.PROFILE_PATH)
P = PROFILE["phrases"]
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


class Model:
    """Stand-in LLM that always gives the same reply."""
    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages, schema):
        return LLMReply(json.dumps(self.reply), 1, 0, 0)


class Voice:
    def __init__(self):
        self.said = []

    def speak(self, text):
        self.said.append(text)


def run_demo(reply, typed):
    """Run the demo with one sentence and the given keyboard answers.
    Returns (what was spoken, the questions asked)."""
    inputs = iter(["some garbled words"] + typed + ["q"])
    asked, voice = [], Voice()
    real_input, real_print = builtins.input, builtins.print
    builtins.input = lambda prompt="": (asked.append(prompt.strip()), next(inputs))[1]
    builtins.print = lambda *a, **k: None
    demo_text.get_llm, demo_text.get_tts = (lambda: Model(reply)), (lambda: voice)
    try:
        demo_text.main()
    finally:
        builtins.input, builtins.print = real_input, real_print
    return voice.said, asked


NO_ARGS = {"app": "", "text": ""}

print("-- Option order --")
i = repair("x", PROFILE, Model({"choice": "P3", "args": NO_ARGS, "confidence": "low",
                                "options": ["P3", "P4", "P1", "P2"]}))
check("kept in the model's order", i.options, [P[2], P[3], P[0], P[1]])
i = repair("x", PROFILE, Model({"choice": "P4", "args": NO_ARGS, "confidence": "low", "options": ["P2"]}))
check("choice first, then model's options, then the rest", i.options, [P[3], P[1], P[0], P[2]])

print("-- Low confidence with a choice --")
low_p3 = {"choice": "P3", "args": NO_ARGS, "confidence": "low", "options": ["P3", "P4", "P1", "P2"]}
said, asked = run_demo(low_p3, ["y"])
check("asks 'Did you mean' (full stop dropped)", asked[1], "Did you mean: Please call my daughter? (y/n)")
check("yes: acts on it", said, [P[2]])
said, asked = run_demo(low_p3, ["n", "2"])
check("no: shows the options, then speaks the pick", said, [P[3]])
said, asked = run_demo(low_p3, ["n", ""])
check("no, then picks nothing: says nothing", said, [])
said, asked = run_demo(low_p3, ["what", "y"])
check("unclear answer: asks again", asked[1:3], [asked[1], asked[1]])
said, _ = run_demo({"choice": "P1", "args": NO_ARGS, "confidence": "low", "options": []}, ["y"])
check("phrase ending in '?': no double '??'", run_demo({"choice": "P1", "args": NO_ARGS, "confidence": "low",
                                                       "options": []}, ["y"])[1][1], f"Did you mean: {P[0]} (y/n)")

print("-- Low confidence without a choice --")
said, asked = run_demo({"choice": "P9", "args": NO_ARGS, "confidence": "high", "options": []}, ["3"])
check("unknown choice: no 'Did you mean', straight to options", any("Did you mean" in q for q in asked), False)
check("  ...and speaks the pick", said, [P[2]])

print("-- Commands --")
said, asked = run_demo({"choice": "open_app", "args": NO_ARGS, "confidence": "high", "options": []}, ["y", "Notes"])
check("open_app with no app: asks 'Did you mean'", asked[1], "Did you mean: open_app? (y/n)")
check("  ...yes: asks which app, then acts", (asked[2], said), ("app?", ["Opening Notes."]))
said, asked = run_demo({"choice": "open_app", "args": {"app": "Safari", "text": ""}, "confidence": "low",
                        "options": []}, ["y"])
check("open_app with Safari: shows the app in the question", asked[1], "Did you mean: open_app (app: Safari)? (y/n)")
check("  ...yes: no extra question", said, ["Opening Safari."])
said, asked = run_demo({"choice": "scroll_down", "args": NO_ARGS, "confidence": "high", "options": []}, [])
check("high confidence: no question at all", (len(asked), said), (2, ["Scrolling down."]))

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
