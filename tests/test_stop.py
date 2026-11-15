"""Stop everything (F2): "stop" / "cancel" / Esc twice stops speaking, typing and anything waiting,
and closes any question. "Stopped." is shown. What was already done is kept. Run:
    .venv/bin/python tests/test_stop.py
"""

import copy
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

config.ASR = config.LLM = config.TTS = config.ACTIONS = "fake"
from actions.fake import FakeActions  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.store import open_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import KEY_ESC, App  # noqa: E402

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


class SlowVoice(FakeTTS):
    def __init__(self):
        super().__init__(quiet=True)
        self.cut = threading.Event()

    def speak(self, line, wait=False, on_sound=None):
        self.cut.clear()
        if self.cut.wait(2.0):
            return False
        return super().speak(line, wait=wait, on_sound=on_sound)

    def stop(self):
        self.cut.set()


def make(tts=None):
    actions = FakeActions()
    return App(copy.deepcopy(BASE), FakeLLM(), tts or FakeTTS(quiet=True), actions=actions, question_wait_s=30), actions


check("'stop', 'cancel', 'stop everything' are stop words", [App.is_stop(w) for w in ("stop", "Cancel.", "stop everything")], [True] * 3)
check("'stop listening' is not (it only stops the microphone)", App.is_stop("stop listening"), False)

core, actions = make()
core.say("wan oop en noat")
wait_for(lambda: core.state["question"] is not None)
check("'stop' with a question open", core.say("stop", source="speech"), "stop")
check("  ...the question is closed", wait_for(lambda: core.state["question"] is None), True)
check("  ...nothing was done", actions.done, [])
check("  ...'Stopped.' is shown", (core.state["notice"] or {}).get("text"), "Stopped.")

core, actions = make(SlowVoice())
core.set_mode("conference")
core.say("i need to use the bathroom")
wait_for(lambda: core.state["question"] is not None)
core.answer("yes")
wait_for(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "window")
core.say("cancel")
check("'cancel' while a sentence is being prepared: it is never said", wait_for(lambda: core.state["said"][-1]["status"] == "stopped"), True)
check("  ...nothing was spoken", core.voice.tts.said, [])

core, actions = make()
gate = threading.Event()
plain = actions.run
actions.run = lambda c, a: (gate.wait(3), plain(c, a))[1]
core.say("scroll down")
time.sleep(0.2)
core.say("scroll up")   # waiting its turn
core.say("open mail")
core.stop_all()
gate.set()
time.sleep(1.5)
check("requests waiting their turn are dropped", [c for c, _ in actions.done if c in ("scroll_up", "open_app")], [])
check("typing is told to stop", getattr(actions, "stop_typing", False), True)

core, actions = make()
core.set_window("mini")
core.say("wan oop en noat")
wait_for(lambda: core.state["question"] is not None)
core.mini_key(KEY_ESC)   # the first Esc answers "no" as usual
time.sleep(0.2)
core.mini_key(KEY_ESC)   # the second, within a second: stop everything
check("mini window: Esc twice stops everything", wait_for(lambda: (core.state["notice"] or {}).get("text") == "Stopped."), True)

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
