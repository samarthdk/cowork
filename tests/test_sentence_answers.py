"""Answers said as whole sentences (bug: "No, I meant to say open Microsoft Word." was rejected
with "Heard '...', which doesn't answer the question.").

Rules (choose/reading.py): 1. "no" + more words: No (nothing learned), the rest is a fresh request;
2. "yes" + more: confirm, the rest is the next request; 3. an option named ("the second one",
"number two", "the last one", its own words): picked; 4. a clear new request: the question is
dropped and the request handled; 5. otherwise the question stays open with a hint.
Same in the full window, the mini window and Conference mode. Run:
    .venv/bin/python tests/test_sentence_answers.py
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
from choose.reading import understand  # noqa: E402
from engine.session import Question  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.store import open_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import App  # noqa: E402

results = []
BASE = open_profile(config.ROOT / "profiles" / "cowork_base.json", config.ROOT / "profiles" / "cowork_base.json")
EXACT = "No, I meant to say open Microsoft Word."


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


def r(text, kind="yes_no", shown=(), body=""):
    reading = understand(text, Question(kind, "x", "", options=list(shown)), shown=list(shown), body=body, profile=BASE)
    return None if reading is None else (reading.answer.kind, reading.answer.value, reading.then, reading.drop)


# ---- the reading rules on their own --------------------------------------------------------
GRID = ["Open Mail", "Open Microsoft Word", "I need to use the bathroom.", "Something else…"]
check("the exact sentence (yes/no card)", r(EXACT, body="Open Notes"), ("none", None, "open microsoft word", True))
check("the exact sentence (options grid)", r(EXACT, "choice", GRID), ("pick", 1, "", False))
check("the exact sentence (undo window): undo, then the request", r(EXACT, "window"), ("no", None, "open microsoft word", True))
check("'nope, open Mail'", r("nope, open Mail", body="Open Notes"), ("none", None, "open mail", True))
check("'not that, scroll down'", r("not that, scroll down"), ("none", None, "scroll down", True))
check("'no' alone is still a plain no", r("no"), ("no", None, "", False))
check("'Yes, then open Mail': confirm, then the rest", r("Yes, then open Mail", body="Open Notes"), ("yes", None, "open mail", False))
check("'yeah open Safari'", r("yeah open Safari"), ("yes", None, "open safari", False))
check("'right, open Mail' (with a comma) is yes", r("right, open Mail"), ("yes", None, "open mail", False))
check("'right click the button' is NOT a yes", (r("right click the button") or ("kept open",))[0] != "yes", True)
check("'yes, open Notes' while asking about Open Notes: just yes", r("yes, open Notes", body="Open Notes"), ("yes", None, "", False))
check("repeating the guess means yes", r("open notes", body="Open Notes"), ("yes", None, "", False))
check("'the first one'", r("the first one", "choice", GRID), ("pick", 0, "", False))
check("'number two'", r("number two", "choice", GRID), ("pick", 1, "", False))
check("'the second'", r("the second", "choice", GRID), ("pick", 1, "", False))
check("'the last one' (not 'Something else')", r("the last one", "choice", GRID), ("pick", 2, "", False))
check("an option's own words", r("I need to use the bathroom", "choice", GRID), ("pick", 2, "", False))
check("'I meant open Microsoft Word' picks it", r("I meant open Microsoft Word", "choice", GRID), ("pick", 1, "", False))
check("'no, the second one' picks it", r("no, the second one", "choice", GRID), ("pick", 1, "", False))
check("a clear new request drops the question", r("open Safari", body="Open Notes"), ("none", None, "open Safari", True))
check("  ...a question too", r("what's the weather in Boston", body="Open Notes"), ("none", None, "what's the weather in Boston", True))
check("mumbling keeps the question open", r("mm the uh"), None)
check("one unclear word keeps it open", r("banana"), None)
check("'yes' + words in a grid doesn't pick", r("yes please do it now", "choice", GRID), None)

# ---- in the app: full window, mini window, Conference mode ----------------------------------------
def make():
    actions = FakeActions(apps=("Safari", "Mail", "Notes", "Music", "Microsoft Word"))
    core = App(copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=actions, question_wait_s=30)
    return core, actions


def card(core, kind=None):
    ok = wait_for(lambda: core.state["question"] is not None and core.state["question"]["kind"] != "window"
                  and (kind is None or core.state["question"]["kind"] == kind))
    return core.state["question"] if ok else None


for where in ("full window", "mini window"):
    core, actions = make()
    if where == "mini window":
        core.set_window("mini")
    learned_before = len(core.session.profile["examples"])
    core.say("wan oop en noat")                 # unclear: "Did you mean...?"
    q = card(core, "yes_no")
    check(f"{where}: a 'Did you mean' card", bool(q), True)
    check(f"{where}: the exact sentence is accepted", core.say(EXACT, source="speech"), "answer")
    check(f"{where}:   ...then Word is opened as a fresh request", wait_for(lambda: ("open_app", {"app": "Microsoft Word"}) in actions.done
                                                                            or bool(core.state["question"] and core.state["question"]["stage"] == "did_you_mean"
                                                                                    and "Word" in (core.state["question"].get("card") or {}).get("body", ""))), True)
    if core.state["question"] and core.state["question"]["kind"] == "yes_no":
        core.answer("yes")
    wait_for(lambda: ("open_app", {"app": "Microsoft Word"}) in actions.done)
    check(f"{where}:   ...Word opened", ("open_app", {"app": "Microsoft Word"}) in actions.done, True)
    meant = [e["meant"] for e in core.session.profile["examples"][learned_before:]]
    check(f"{where}:   ...nothing learned from the rejected guess", [m for m in meant if m != "open_app"], [])

    core, actions = make()
    if where == "mini window":
        core.set_window("mini")
    core.say("wan oop en noat")
    card(core, "yes_no")
    first = core.state["question"]["id"]
    check(f"{where}: 'yes, then open Mail' accepted", core.say("Yes, then open Mail", source="speech"), "answer")
    check(f"{where}:   ...the guess (Open Notes) is done", wait_for(lambda: ("open_app", {"app": "Notes"}) in actions.done), True)
    if wait_for(lambda: core.state["question"] is not None and core.state["question"]["id"] != first
                and core.state["question"]["kind"] == "yes_no", 4):
        core.answer("yes")   # the fake Brain checks "open Mail" first
    check(f"{where}:   ...then Mail is opened next", wait_for(lambda: ("open_app", {"app": "Mail"}) in actions.done), True)

    core, actions = make()
    if where == "mini window":
        core.set_window("mini")
    core.say("wan oop en noat")
    card(core, "yes_no")
    before = core.state["question"]["id"]
    check(f"{where}: mumbling keeps the card", (core.say("mm uh", source="speech"), core.state["question"]["id"]), ("ignored", before))
    check(f"{where}:   ...with a hint", "Say yes or no" in ((core.state["notice"] or {}).get("text") or ""), True)
    core.answer("none")

# Conference mode
core, actions = make()
core.set_mode("conference")
core.say("we gathered shells on the beach")
check("conference: a 'Did you mean' card", bool(card(core, "yes_no")), True)
first = core.state["question"]["id"]
check("conference: 'No, I meant to say I need to use the bathroom' accepted",
      core.say("No, I meant to say I need to use the bathroom.", source="speech"), "answer")
wait_for(lambda: core.state["question"] is not None and core.state["question"]["id"] != first)
q = card(core, "yes_no")
check("conference:   ...a fresh 'Did you mean' for the new sentence", bool(q) and "bathroom" in q["card"]["body"].lower(), True)
check("conference:   ...nothing said for the rejected guess", core.voice.tts.said, [])
core.answer("yes")
check("conference:   ...the new sentence is said", wait_for(lambda: any("bathroom" in s for s in core.voice.tts.said)), True)

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
