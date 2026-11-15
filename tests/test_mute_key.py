"""Hold M to mute Cowork (any app): held for a moment -> muted until let go; a quick tap is still an "m".
Run:
    .venv/bin/python tests/test_mute_key.py
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

config.ASR = config.LLM = config.TTS = config.ACTIONS = "fake"
from actions.fake import FakeActions  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.store import open_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import App  # noqa: E402
from ui.mute_key import M_KEYCODE, MuteKey  # noqa: E402

results = []


def check(name, got, expected):
    ok = got == expected
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}, expected {expected!r}")


key = MuteKey(hold_s=0.3)
check("M down: kept from the app while deciding", key.event("down", M_KEYCODE, False, 0.0), ["swallow"])
check("  ...held (key repeat): mutes", key.event("down", M_KEYCODE, True, 0.5), ["swallow", "mute"])
check("  ...more repeats: nothing more", key.event("down", M_KEYCODE, True, 0.6), ["swallow"])
check("  ...let go: listening again", key.event("up", M_KEYCODE, False, 2.0), ["swallow", "unmute"])
check("a quick tap: the app gets its 'm'", (key.event("down", M_KEYCODE, False, 3.0), key.event("up", M_KEYCODE, False, 3.1)),
      (["swallow"], ["swallow", "type_m"]))
key.event("down", M_KEYCODE, False, 4.0)
check("key repeat off: the timer mutes after the hold time", (key.tick(4.1), key.tick(4.4)), ([], ["mute"]))
check("  ...and letting go unmutes", key.event("up", M_KEYCODE, False, 5.0), ["swallow", "unmute"])
check("other keys pass", key.event("down", 0, False, 6.0), ["pass"])
check("Cmd+M (minimise) passes untouched", key.event("down", M_KEYCODE, False, 7.0, modifiers=True), ["pass"])

core = App(copy.deepcopy(open_profile(config.ROOT / "profiles" / "cowork_base.json", config.ROOT / "profiles" / "cowork_base.json")),
           FakeLLM(), FakeTTS(quiet=True), actions=FakeActions())


class Listener:
    running, held, muted = True, False, False
    def hold(self, on): self.held = on
    def mute(self): self.muted = True
    def unmute(self): self.muted = False
    def trigger(self, wait_s=None): pass
    def cancel(self): pass
    listening = False


listener = Listener()
core.attach_listener(listener)
core.hold_mute(True)
check("holding M: the microphone doesn't hear, and 'Muted' is shown", (listener.held, core.state["mic"]["held"]), (True, True))
core.voice.speak("Hello.")   # Cowork speaking meanwhile must not unmute the person's hold
check("  ...Cowork speaking meanwhile doesn't undo it", listener.held, True)
core.hold_mute(False)
check("let go of M: listening again", (listener.held, core.state["mic"]["held"]), (False, False))

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.exit(0 if all(results) else 1)
