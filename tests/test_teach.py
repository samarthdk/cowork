"""Teach me (F5): the person picks a phrase and says it a few times; each saying is stored as a learned
example, so that way of saying it goes straight through next time (fast path, no model). Run:
    .venv/bin/python tests/test_teach.py
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
from repair.learning import fast_path  # noqa: E402
from repair.store import open_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import App  # noqa: E402

results = []
BASE = open_profile(config.ROOT / "profiles" / "cowork_base.json", config.ROOT / "profiles" / "cowork_base.json")
PHRASE = "Please call my daughter."
SAYINGS = ["plea caw my dor ter", "pease call ma daughta", "please caw my daw"]


def check(name, got, expected):
    ok = got == expected
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}, expected {expected!r}")


core = App(copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=FakeActions())
check("before: 'plea caw my dor ter' is not recognised instantly", fast_path(SAYINGS[0], core.session.profile), None)
before = core.state["counts"]["examples"]
check("start teaching a phrase", core.start_teach(PHRASE), True)
check("  ...the screen shows it", core.state["teach"]["phrase"], PHRASE)
check("an unknown phrase can't be taught", App.start_teach.__name__ and core.start_teach("Not a phrase of mine") is False, True)
core.start_teach(PHRASE)
for n, said in enumerate(SAYINGS, 1):
    check(f"saying {n} is taken (not a request)", core.say(said, source="speech"), "teach")
check("after 3 sayings it is finished", core.state["teach"], None)
check("  ...3 ways learned", core.state["counts"]["examples"], before + 3)
check("  ...and said so", (core.state["notice"] or {}).get("text"), f"Learned 3 ways you say “{PHRASE}”.")
check("  ...nothing done on the Mac", core.session.actions._actions.done, [])
match = fast_path(SAYINGS[0], core.session.profile)
check("now the same way of saying it goes straight through", match.intent.choice if match else None, PHRASE)
core.start_teach(PHRASE)
core.say("pleeze call my dotter", source="speech")
core.stop_teach()
check("'Done' after one saying keeps it", (core.state["teach"], core.state["counts"]["examples"]), (None, before + 4))
core.start_teach(PHRASE)
core.say("stop", source="speech")
check("'stop' ends teaching", core.state["teach"], None)

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
