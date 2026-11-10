"""Checks the one input system for answers: speech, hand signals, keyboard and
screen all become the same Answer, and a whole conversation can be completed
without the keyboard. No camera, microphone or model needed. Run:
    .venv/bin/python tests/test_answers.py
"""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from choose.answers import Answer, from_signal, from_text, hint, yes_or_no  # noqa: E402
from engine.session import Question, Session  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.profile import load_profile  # noqa: E402
from repair.rank import SOMETHING_ELSE  # noqa: E402

BASE = load_profile(config.BASE_PROFILE_PATH)
BASE["examples"] = []
P = BASE["phrases"]
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


def kv(answer):
    return None if answer is None else (answer.kind, answer.value)


YES_NO = Question("yes_no", "did_you_mean", "Did you mean: water?")
GRID = Question("choice", "options", "Which one?", options=[P[1], P[2], P[3], SOMETHING_ELSE])
GRID3 = Question("choice", "options", "Which one?", options=[P[1], P[2], SOMETHING_ELSE])
WINDOW = Question("window", "correction", "cancel?", seconds=3)
DETAIL = Question("detail", "detail", "app?")

print("-- Yes/no question --")
for signal, want in [("RIGHT", "yes"), ("RIGHT_UP", "yes"), ("LEFT_UP", "yes"),
                     ("LEFT", "no"), ("RIGHT_DOWN", "no"), ("LEFT_DOWN", "no")]:
    check(f"hand signal {signal}", kv(from_signal(signal, YES_NO)), (want, None))
check("no signal", from_signal(None, YES_NO), None)
for said, want in [("yes", "yes"), ("Yeah.", "yes"), ("Yep!", "yes"), ("OK", "yes"),
                   ("No.", "no"), ("nope", "no"), ("Know", "no"), ("no no", "no")]:
    check(f"spoken {said!r}", kv(from_text(said, YES_NO, "speech")), (want, None))
check("typed y", kv(from_text("y", YES_NO)), ("yes", None))
check("typed n", kv(from_text("n", YES_NO)), ("no", None))
check("unclear speech answers nothing", from_text("uh mm", YES_NO, "speech"), None)
check("a whole sentence is not a yes", from_text("yes I would like some water", YES_NO, "speech"), None)
check("this person's own 'yes' (learned)", yes_or_no("yet", {"yes": ["yet", "yeth"], "no": ["new"]}), "yes")
check("this person's own 'no' (learned)", yes_or_no("New.", {"yes": ["yet"], "no": ["new"]}), "no")
check("a word learned for both is unsure", yes_or_no("yo", {"yes": ["yo"], "no": ["yo"]}), None)

print("-- The 2x2 grid --")
for signal, want in [("LEFT_UP", 0), ("RIGHT_UP", 1), ("LEFT_DOWN", 2), ("RIGHT_DOWN", 3)]:
    check(f"hand signal {signal} = option {want + 1}", kv(from_signal(signal, GRID)), ("pick", want))
check("plain RIGHT hand: no selection (ask again)", from_signal("RIGHT", GRID), None)
check("plain LEFT hand: no selection (ask again)", from_signal("LEFT", GRID), None)
check("three options: the empty square selects nothing", from_signal("RIGHT_DOWN", GRID3), None)
check("three options: bottom left is 'Something else'", kv(from_signal("LEFT_DOWN", GRID3)), ("pick", 2))
check("typed 2", kv(from_text("2", GRID)), ("pick", 1))
check("typed 5 (no such option)", from_text("5", GRID), None)
check("typed Enter = none of these", kv(from_text("", GRID)), ("none", None))
check("spoken 'three'", kv(from_text("three", GRID, "speech")), ("pick", 2))
check("spoken 'no' = none of these", kv(from_text("no", GRID, "speech")), ("none", None))
check("saying the option itself picks it", kv(from_text("please call my daughter", GRID, "speech")), ("pick", 1))
check("saying 'something else' picks it", kv(from_text("Something else.", GRID, "speech")), ("pick", 3))
check("unclear speech picks nothing", from_text("uh the the", GRID, "speech"), None)
check("spoken 'to' is not treated as 'two'", from_text("to", GRID, "speech"), None)

print("-- Correction window --")
check("LEFT hand cancels", kv(from_signal("LEFT", WINDOW)), ("no", None))
check("thumb down cancels", kv(from_signal("RIGHT_DOWN", WINDOW)), ("no", None))
check("RIGHT hand keeps it", kv(from_signal("RIGHT", WINDOW)), ("yes", None))
check("spoken 'no' cancels", kv(from_text("No!", WINDOW, "speech")), ("no", None))
check("typed undo cancels", kv(from_text("undo", WINDOW)), ("no", None))
check("anything else is the next sentence", kv(from_text("baffroom pleez", WINDOW, "speech")), ("text", "baffroom pleez"))
check("silence is nothing", from_text("", WINDOW, "speech"), None)

print("-- Details and hints --")
check("a detail is just the words", kv(from_text("Safari", DETAIL, "speech")), ("text", "Safari"))
check("hand signals don't answer a detail", from_signal("RIGHT", DETAIL), None)
check("every question has a hint", all(hint(q) for q in (YES_NO, GRID, WINDOW, DETAIL)), True)


print("-- A whole conversation with no keyboard --")


class Model:
    def __init__(self, choice="P1", confidence="low"):
        self.choice, self.confidence = choice, confidence

    def complete(self, messages, schema, purpose="intent", temperature=0.0):
        if purpose == "guess":
            return LLMReply(json.dumps({"guess": "Could I have some Pepsi?", "key_words": ["Pepsi"]}), 1, 0, 0)
        return LLMReply(json.dumps({"choice": self.choice, "confidence": self.confidence}), 1, 0, 0)


def converse(heard, inputs, profile=None, model=None):
    """Drive the engine with a list of inputs: ("hand", SIGNAL) or ("speech", words)
    or None (nothing happened before the question timed out)."""
    profile = copy.deepcopy(profile or BASE)
    events, said = [], []
    tts = type("V", (), {"speak": lambda s, t: said.append(t)})()
    session = Session(profile, model or Model(), tts, on_event=events.append)
    flow = session.hear(heard, source="speech")
    asked, inputs = [], list(inputs)
    try:
        question = next(flow)
        while True:
            asked.append(question.stage)
            answer = None
            while inputs:
                item = inputs.pop(0)
                if item is None:
                    break
                answer = from_signal(item[1], question) if item[0] == "hand" else from_text(item[1], question, "speech")
                if answer is not None:
                    break   # inputs that don't answer the question are simply ignored
            question = flow.send(answer)
    except StopIteration as finished:
        return {"said": said, "asked": asked, "events": [e["kind"] for e in events],
                "pending": finished.value, "profile": profile}


r = converse("aim thur stee", [("hand", "RIGHT"), None])
check("unsure -> 'Did you mean?' -> RIGHT hand = yes -> spoken and learned",
      (r["said"], r["asked"], "learned" in r["events"]), ([P[0]], ["did_you_mean", "correction"], True))
r = converse("aim thur stee", [("speech", "uh"), ("hand", "RIGHT_UP"), None])
check("unclear speech is ignored; thumb up then answers yes", r["said"], [P[0]])
r = converse("cud eye haf sum pep see", [("hand", "LEFT"), ("hand", "RIGHT_DOWN"), ("hand", "RIGHT_UP"), None])
check("LEFT = no -> grid -> RIGHT_DOWN = 'Something else' -> thumb up confirms the guess",
      (r["said"], r["asked"]), (["Could I have some Pepsi?"], ["did_you_mean", "options", "guess", "correction"]))
check("  ...new phrase and new word learned", ("Could I have some Pepsi?" in r["profile"]["phrases"],
                                             r["profile"]["examples"][-1]["meant"]), (True, "Could I have some Pepsi?"))
r = converse("aim thur stee", [("hand", "LEFT"), ("hand", "RIGHT"), ("hand", "LEFT_UP"), None])
check("in the grid a plain hand selects nothing; a thumb then picks option 1",
      (r["asked"], len(r["said"])), (["did_you_mean", "options", "correction"], 1))
r = converse("scrol dwn", [("hand", "LEFT"), None], model=Model("scroll_down", "high"))
check("confident action, then LEFT hand in the window cancels it",
      ("cancelled" in r["events"], r["asked"]), (True, ["correction", "options"]))
r = converse("aim thur stee", [("speech", "yes"), ("speech", "baffroom pleez")])
check("spoken yes confirms; a new sentence in the window is kept for next", (r["said"], r["pending"]),
      ([P[0]], "baffroom pleez"))
r = converse("aim thur stee", [None])
check("no answer at all: nothing is spoken, and silence is not treated as 'no'",
      (r["said"], r["asked"], "nothing_chosen" in r["events"]), ([], ["did_you_mean"], True))
r = converse("cud eye haf sum pep see", [("hand", "LEFT"), ("hand", "RIGHT_DOWN"), None])
check("an unanswered guess stops the guessing", (r["said"], r["asked"]), ([], ["did_you_mean", "options", "guess"]))

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
