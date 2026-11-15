"""The voice: TTSKit only, never the Mac voice; lines made of ready pieces; never half a line.

No sound is played and TTSKit is not run: the player and TTSKit are stood in for, and
the pieces are short test tones written into a temporary cache. Run:
    .venv/bin/python tests/test_voice.py
"""

import copy
import shutil
import sys
import tempfile
import threading
import time
import types
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

config.ASR = config.LLM = config.TTS = config.ACTIONS = "fake"
import numpy as np  # noqa: E402

import tts.real as real  # noqa: E402
from actions.fake import FakeActions  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.store import open_profile  # noqa: E402
from tts import lines  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import App  # noqa: E402

results = []


def check(name, got, expected):
    ok = got == expected
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}, expected {expected!r}")


BASE = open_profile(config.ROOT / "profiles" / "cowork_base.json", config.ROOT / "profiles" / "cowork_base.json")

# ---- the Mac voice is gone ---------------------------------------------------------------
code = "".join(p.read_text() for d in ("tts", "ui", "engine", "actions", "repair") for p in (config.ROOT / d).rglob("*.py"))
import re  # noqa: E402
check("the `say` command is never run", bool(re.search(r"""(Popen|run|call|check_output)\(\s*\[\s*["'](/usr/bin/)?say["']""", code)), False)
check("no Mac voice (AVSpeechSynthesizer) anywhere", "AVSpeech" in code or "MacVoice" in code, False)
check("tts/mac_voice.py removed", (config.ROOT / "tts" / "mac_voice.py").exists(), False)

# ---- whole sentences only (never stitched) ---------------------------------------------------
check("no stitching code left", any(name in code for name in ("_join(", "gap_after", "lines.choose", "parts_of")), False)
check("a yes/no question is one sentence", lines.question_line({"title": "Did you mean\u2026?", "body": "Open Mail"}),
      "Did you mean: Open Mail?")
check("a grid question is one sentence",
      lines.question_line({"title": "Which one did you mean?", "options": [{"text": "Open Mail"}, {"text": "Something else\u2026"}]}),
      "Which one did you mean? 1: Open Mail, 2: Something else.")
check("ellipsis and quotes are not spoken", lines.clean("\u201cSomething else\u2026\u201d"), "Something else")
everything = lines.to_prepare(BASE, ["Mail"])
check("prepared whole: every phrase", all(lines.clean(p) in everything for p in BASE["phrases"]), True)
check("prepared whole: the meeting quick phrases", all(p in everything for p in lines.MEETING), True)
check("prepared whole: commands' steps and 'Opening <app>.'", all(p in everything for p in ("Scrolling down.", "Opening Mail.", "Opening Microsoft Word.")), True)
check("no short pieces prepared", any(p in everything for p in ("Did you mean", "Open", "One.", "Mail")), False)

# ---- the real voice, with TTSKit and the player stood in for -------------------------------------
cache = Path(tempfile.mkdtemp())
config.TTS_CACHE_DIR = cache
RATE = 24000


def tone(path, seconds=0.4, quiet=0.2):
    """A test piece: quiet edges (as TTSKit leaves) around a tone."""
    t = np.arange(int(RATE * seconds)) / RATE
    body = (np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2")
    pad = np.zeros(int(RATE * quiet), dtype="<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
        w.writeframes(np.concatenate([pad, body, pad]).tobytes())


played = []


class FakePlayer:
    def __init__(self, command):
        played.append(command[-1])
        self.done = False
    def wait(self, timeout=None):
        return 0
    def poll(self):
        return 0
    def terminate(self):
        pass


real.subprocess = types.SimpleNamespace(Popen=FakePlayer, run=None, TimeoutExpired=Exception)
voice = real.RealTTS(profile=BASE)
check("TTSKit never downloads: refuses without the model on this Mac",
      bool(voice.available()) if not config.TTSKIT_MODELS_PATH.exists() else "models present",
      True if not config.TTSKIT_MODELS_PATH.exists() else "models present")
made = []
delay = {"s": 0.0}


def synth(text):
    made.append(text)
    time.sleep(delay["s"])
    tone(voice.cached_path(text))


voice._synthesize = synth
voice.available = lambda: ""
tone(voice.cached_path("Opening Mail."))

check("ready sentence", voice.ready("Opening Mail."), True)
start = time.perf_counter()
check("  ...is spoken", voice.speak("Opening Mail."), True)
took = (time.perf_counter() - start) * 1000
check("  ...first sound quickly (under 100 ms here)", took < 100, True)
with wave.open(played[-1], "rb") as w:
    played_s = w.getnframes() / w.getframerate()
check("  ...the click and quiet edges trimmed (one whole sentence, 0.4 s + edges)", round(played_s, 2), round(0.4 + 2 * real.EDGE_S, 2))
check("  ...no TTSKit needed", made, [])

delay["s"] = 0.5
check("not ready, made within 2 s: still spoken", voice.speak("Opening Safari."), True)
check("  ...made by TTSKit just now, whole", (made, voice.last.get("made_now")), (["Opening Safari."], True))
check("  ...and cached for next time", voice.ready("Opening Safari."), True)

delay["s"] = 3.0
made.clear()
before = len(played)
start = time.perf_counter()
check("not ready within 2 s: silent", voice.speak("Searching for cats."), False)
check("  ...after about 2 s", round(time.perf_counter() - start), 2)
check("  ...nothing played", len(played), before)
check("  ...but it is being made, whole, for next time", made, ["Searching for cats."])
time.sleep(1.2)
check("  ...and is ready afterwards", voice.ready("Searching for cats."), True)

delay["s"] = 0.3
check("wait=True (Conference mode): waits however long", voice.speak("A brand new sentence for the room.", wait=True), True)
delay["s"] = 1.0
outcome = {}
thread = threading.Thread(target=lambda: outcome.update(spoken=voice.speak("Another new sentence.", wait=True)))
thread.start()
time.sleep(0.3)
voice.stop()
thread.join()
check("stop() while waiting: never played", outcome["spoken"], False)
time.sleep(1.0)

# ---- the app: speaking on by default, mic paused while anything plays -----------------------
core = App(copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=FakeActions())
check("speaking is off by default (Computer mode)", (core.state["voice"]["on"], core.voice.enabled), (False, False))
check("  ...a line is then not said, and the screen is told so", core.voice.speak("Hello."), False)
core.set_voice(True)


class Listener:
    running = True
    def __init__(self):
        self.muted, self.log = False, []
    listening = False
    def trigger(self, wait_s=None): pass
    def cancel(self): pass
    def mute(self):
        self.muted = True; self.log.append("mute")
    def unmute(self):
        self.muted = False; self.log.append("unmute")


listener = Listener()
core.attach_listener(listener)
heard_while_playing = []
core.voice.tts.speak = lambda line, wait=False: heard_while_playing.append(listener.muted) or True
core.voice.speak("Hello there.")
check("the microphone is paused while it speaks", (heard_while_playing, listener.muted), ([True], False))
core.set_voice(False)
check("Settings toggle: off is silent", core.voice.speak("Hello again."), False)
core.set_voice(True)

# mini window: a question that can't be said -> one soft chime and a glow until answered
core = App(copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True, not_ready={"Send"}), actions=FakeActions())
core.attach_listener(Listener())
chimes = []
core.voice.chime = lambda: chimes.append(1)
core.set_window("mini")
core.say("send the email")   # risky: always asks
end = time.monotonic() + 5
while core.state["question"] is None and time.monotonic() < end:
    time.sleep(0.02)
time.sleep(0.2)
check("mini: question not ready to say -> one chime", len(chimes), 1)
check("  ...and the window glows", core.state["attention"], True)
check("  ...nothing was said", core.voice.tts.said, [])
qid = core.state["question"]["id"]
core.answer("no")
end = time.monotonic() + 5
while (core.state["question"] is None or core.state["question"]["id"] == qid) and time.monotonic() < end:
    time.sleep(0.02)
time.sleep(0.2)
check("  ...the next question (the grid) chimes and glows too", (len(chimes), core.state["attention"]), (2, True))
core.answer("none")
end = time.monotonic() + 5
while core.state["attention"] and time.monotonic() < end:
    time.sleep(0.02)
check("  ...the glow stops once answered", core.state["attention"], False)

shutil.rmtree(cache, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.exit(0 if all(results) else 1)
