"""Dictation polish (F6): spoken punctuation, "delete that", numbers, times and dates written sensibly
(and checked with the person first). Run:
    .venv/bin/python tests/test_dictation.py
"""

import copy
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

config.ASR = config.LLM = config.TTS = config.ACTIONS = "fake"
from actions.fake import FakeActions  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.store import open_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import App  # noqa: E402
from ui.dictation import editing, tidy  # noqa: E402

results = []
BASE = open_profile(config.ROOT / "profiles" / "cowork_base.json", config.ROOT / "profiles" / "cowork_base.json")


def check(name, got, expected):
    ok = got == expected
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}, expected {expected!r}")


def wait_for(condition, seconds=6.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


CASES = {
    "hello comma how are you question mark": ("Hello, how are you?", False),
    "see you soon period": ("See you soon.", False),
    "that is great exclamation mark": ("That is great!", False),
    "first line new line second line": ("First line\nSecond line", False),
    "dear sam new paragraph thanks": ("Dear sam\n\nThanks", False),
    "see you at three thirty pm period": ("See you at 3:30 PM.", True),
    "call me at ten o clock": ("Call me at 10:00", True),
    "meet at seven fifteen in the morning": ("Meet at 7:15 AM", True),
    "the meeting is on november fifteenth": ("The meeting is on November 15th", True),
    "on the twenty first of march": ("On March 21st", True),
    "I am forty two years old": ("I am 42 years old", True),
    "we have three cats": ("We have three cats", False),   # small numbers stay words
    "I feel I can play this weekend.": ("I feel I can play this weekend.", False),
}
for said, expected in CASES.items():
    check(f"{said!r}", tidy(said), expected)
check("email addresses are typed exactly", tidy("email sam@gmail.com period")[0], "Email sam@gmail.com.")
check("  ...also alone (no capital, no spaces added)", tidy("sam@gmail.com")[0], "sam@gmail.com")
check("web addresses and file names too", (tidy("go to www.example.org now")[0], tidy("see report.pdf period")[0]),
      ("Go to www.example.org now", "See report.pdf."))
check("decimals stay as they are", tidy("I paid 3.50 dollars")[0], "I paid 3.50 dollars")
check("no demo phrase is changed by the tidying (no extra question in the demo)",
      [p for p in BASE["phrases"] if tidy(p) != (p, False)], [])
check("numbers=False keeps the words", tidy("see you at three thirty pm period", numbers=False)[0], "See you at three thirty pm.")
check("editing words", [editing(w) for w in ("delete that", "Scratch that.", "period", "comma", "question mark", "open mail")],
      ["delete", "delete", ".", ",", "?", None])


def make():
    from actions.screen import Screen
    actions = FakeActions(target="Microsoft Word")
    doc = Screen(app="Microsoft Word", pid=1, window="Document1", place="document", focus="the document", typeable=True)
    actions.screen = lambda: doc
    plain = actions.context
    actions.context = lambda: dict(plain(), _screen=doc)
    core = App(copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=actions, question_wait_s=20)
    core.set_dictation(True)
    return core, actions


def run(core, text, answer="yes"):
    n = len(core.state["history"])
    core.say(text)
    seen = []
    end = time.monotonic() + 8
    while time.monotonic() < end and not (len(core.state["history"]) > n and core.state["status"] == "ready"):
        q = core.state["question"]
        if q is not None:
            seen.append(q["stage"])
            core.answer(answer if q["stage"] == "type_check" else ("yes" if q["kind"] in ("yes_no", "window") else "none"))
            wait_for(lambda: core.state["question"] is None or core.state["question"]["id"] != q["id"], 3)
        time.sleep(0.02)
    return seen


core, actions = make()
run(core, "type hello comma how are you question mark")
typed = [a["text"] for c, a in actions.done if c == "type_text"]
check("'type hello comma how are you question mark' types the marks", typed[-1:], ["Hello, how are you? "])
seen = run(core, "type see you at three thirty pm")
check("numbers written: checked first ('Type it like this?')", "type_check" in seen, True)
check("  ...yes: typed as shown", [a["text"] for c, a in actions.done if c == "type_text"][-1:], ["See you at 3:30 PM"])
seen = run(core, "type see you at three thirty pm", answer="no")
check("  ...no: typed as the words were said", [a["text"] for c, a in actions.done if c == "type_text"][-1:], ["See you at three thirty pm"])
run(core, "type see you soon period")
check("a typed sentence ends with a space (the next one doesn't run into it)",
      [a["text"] for c, a in actions.done if c == "type_text"][-1:], ["See you soon. "])
run(core, "type I will bring the cake")
before = len(actions.done)
run(core, "period")
check("'period' adds the mark right after the last sentence",
      actions.done[before:], [("type_text", {"text": ". ", "anyway": True})])
before = len(actions.done)
run(core, "comma")
check("  ...and after a mark's space, that space is removed first",
      actions.done[before:], [("delete_text", {"count": 1}), ("type_text", {"text": ", ", "anyway": True})])
before = len(actions.done)
run(core, "delete that")
deleted = [a for c, a in actions.done[before:] if c == "delete_text"]
check("'delete that' removes the last dictated sentence", len(deleted) == 1 and deleted[0]["count"] > 0, True)
before = len(actions.done)
core._last_typed = None
run(core, "delete that")
check("'delete that' with nothing typed: says so, deletes nothing", ([c for c, _ in actions.done[before:] if c == "delete_text"],
      (core.state["notice"] or {}).get("text")), ([], "There is nothing I typed to delete."))

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
