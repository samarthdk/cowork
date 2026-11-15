"""Conference mode: Cowork speaks for the person in a meeting.

  - a confirmed sentence is always said aloud with TTSKit (waiting for it if needed);
  - nothing is said before the person confirms (even when sure); the confirmation is silent;
  - Undo in the 3 s window stops the speech;
  - the top guess is made by TTSKit while the card is up;
  - commands are ignored until the person switches back;
  - quick phrases by key, thumb or voice (voice is checked first);
  - learning works the same; speaking is always on, and restored afterwards.

With the fakes (no sound). Run:
    .venv/bin/python tests/test_conference.py
"""

import copy
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
from ui.app import App, QUICK_PHRASES  # noqa: E402

results = []
BASE = open_profile(config.ROOT / "profiles" / "cowork_base.json", config.ROOT / "profiles" / "cowork_base.json")


def check(name, got, expected):
    ok = got == expected
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}, expected {expected!r}")


def wait_for(condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class SlowTTS(FakeTTS):
    """Like TTSKit making a new sentence: speaking with wait=True takes a while, and stop() cuts it."""
    def __init__(self, seconds=0.0, **kw):
        super().__init__(quiet=True, **kw)
        self.seconds, self.stopped = seconds, threading.Event()
        self.started_at = []

    def speak(self, line, wait=False, on_sound=None):
        self.stopped.clear()
        if self.seconds and self.stopped.wait(self.seconds):
            self.silent.append(str(line))
            return False
        self.started_at.append(time.monotonic())
        return super().speak(line, wait=wait, on_sound=on_sound)

    def stop(self):
        self.stopped.set()


def make(tts=None):
    actions = FakeActions()
    core = App(copy.deepcopy(BASE), FakeLLM(), tts or SlowTTS(), actions=actions, question_wait_s=30)
    return core, core.voice.tts, actions


def question(core, kind=None, stage=None):
    ok = wait_for(lambda: core.state["question"] is not None
                  and (kind is None or core.state["question"]["kind"] == kind)
                  and (stage is None or core.state["question"]["stage"] == stage))
    return core.state["question"] if ok else None


def idle(core):
    return wait_for(lambda: core.state["question"] is None and core.state["status"] == "ready"
                    and (core.state["turn"] is None or core.state["turn"]["finished"]), 8)


# ---- switching ------------------------------------------------------------------------------
core, tts, actions = make()
core.set_voice(False)
core.set_mode("conference")
check("Conference mode on", (core.state["mode"], core.session.conference), ("conference", True))
check("  ...speaking is always on", (core.state["voice"]["on"], core.voice.enabled), (True, True))
core.set_voice(False)
check("  ...and can't be turned off there", core.voice.enabled, True)
core.set_mode("computer")
check("leaving: speaking goes back to the previous setting (off)", (core.state["voice"]["on"], core.voice.enabled), (False, False))

# ---- a sure sentence is still confirmed first, silently ------------------------------------
core, tts, actions = make()
core.set_mode("conference")
core.say("i need to use the bathroom")      # the fake Brain is sure about this one
q = question(core, "yes_no", "did_you_mean")
check("even when sure: 'Did you mean...?' first", bool(q), True)
check("  ...nothing said to the room yet (the question is silent too)", tts.said, [])
check("  ...its audio is being made while the card is up", tts.prefetched, ["I need to use the bathroom."])
examples_before = core.state["counts"]["examples"]
core.answer("yes")
check("confirmed: said aloud", wait_for(lambda: tts.said == ["I need to use the bathroom."]), True)
check("  ...waiting for TTSKit if needed (never skipped)", tts.waited, ["I need to use the bathroom."])
check("  ...in the transcript, marked said", wait_for(lambda: [(e["text"], e["status"]) for e in core.state["said"]]
                                                     == [("I need to use the bathroom.", "said")]), True)
check("  ...learned, and the 'ways you say things' counter is updated at once",
      wait_for(lambda: core.state["counts"]["examples"] == examples_before + 1), True)
check("  ...nothing done on the Mac", actions.done, [])
idle(core)

# ---- the fast path also asks first -----------------------------------------------------------
core.say("i need to use the bathroom")
check("the phrase word for word (not learned): still asked first", bool(question(core, "yes_no", "did_you_mean")), True)
core.answer("yes")
idle(core)

# ---- Cmd+4's words: asks the first time; after Yes, the second time it is said at once ----------------
HEARD4 = "Could grandpa like to be a model in his language?"
MEANT4 = "Grandfather likes to be modern in his language."
core, tts, actions = make()
core.set_mode("conference")
core.say(HEARD4)
q = question(core)
check("clip 4 the first time: a question first", bool(q) and q["kind"] in ("yes_no", "choice"), True)
if q["kind"] == "yes_no" and q["card"]["body"] == MEANT4:
    core.answer("yes")
else:   # the fake Brain may not guess it first: pick it the way the person would
    if q["kind"] == "yes_no":
        core.answer("no")
        q = question(core, "choice")
    core.answer("pick", [o["text"] for o in q["card"]["options"]].index(MEANT4))
check("  ...said after Yes", wait_for(lambda: MEANT4 in tts.said), True)
idle(core)
core.say(HEARD4)
check("clip 4 again: said immediately (learned), no 'Did you mean'", wait_for(lambda: tts.said.count(MEANT4) == 2), True)
check("  ...no yes/no question was asked", core.state["question"] is None or core.state["question"]["kind"] == "window", True)
idle(core)

core, tts, actions = make(SlowTTS(seconds=1.5))
core.set_mode("conference")
core.session.profile["examples"].append({"heard": HEARD4, "meant": MEANT4, "args": {}})
core.say(HEARD4)
check("said at once, then Undo within 3 s: stopped", bool(question(core, "window")), True)
core.answer("no")
idle(core)
check("  ...never said", tts.said, [])

# ---- no writing mode in Conference mode -----------------------------------------------------------
core, tts, actions = make()
core._set(writing={"label": "your document", "app": "Microsoft Word"})
core.set_mode("conference")
check("entering Conference mode ends writing mode", core.state["writing"], None)
core.set_mode("computer")
check("  ...leaving it doesn't restart it", core.state["writing"], None)
core, tts, actions = make()
core.set_mode("conference")

# ---- commands are ignored ---------------------------------------------------------------------
core.say("open mail")
wait_for(lambda: (core.state["notice"] or {}).get("text", "").startswith("Commands are off"))
idle(core)
check("a command is not carried out", actions.done, [])
check("  ...nor said", "Opening Mail." in tts.said, False)
check("  ...the person is told why", (core.state["notice"] or {}).get("text"), "Commands are off in Conference mode. Switch to Computer to use them.")

# ---- undo within the 3 s window stops the speech ------------------------------------------------
core, tts, actions = make(SlowTTS(seconds=1.5))
core.set_mode("conference")
core.say("we gathered shells on the beach")
question(core, "yes_no", "did_you_mean")
core.answer("yes")
check("the undo window opens while the voice is still being prepared", bool(question(core, "window")), True)
check("  ...the transcript says 'Preparing voice…' meanwhile", wait_for(lambda: core.state["said"] and core.state["said"][-1]["status"] in ("preparing", "speaking")), True)
core.answer("no")    # Undo
idle(core)
check("Undo: never said", tts.said, [])
check("  ...marked stopped", wait_for(lambda: core.state["said"][-1]["status"] == "stopped"), True)

# ---- the grid: phrases only, top one made in advance --------------------------------------
core, tts, actions = make()
core.set_mode("conference")
core.say("blah blah")   # unclear: "Did you mean...?", then after "no" the grid
q = question(core)
if q and q["kind"] == "yes_no":
    core.answer("no")
q = question(core, "choice")
check("unsure: a grid of phrases (no commands)", bool(q) and all(o["kind"] in ("phrase", "else") for o in q["card"]["options"]), True)
check("  ...its top phrase is being made in advance", tts.prefetched[-1:], [q["card"]["options"][0]["text"]] if q else [])
core.answer("none")
idle(core)

# ---- quick phrases -------------------------------------------------------------------------------
check("a key picks a quick phrase", core.quick_phrase(0), True)
check("  ...said at once", wait_for(lambda: QUICK_PHRASES[0] in tts.said), True)
check("  ...and in the transcript", core.state["said"][-1]["text"], QUICK_PHRASES[0])
check("a held thumb picks one too (right thumb up = 2)", core.signal("RIGHT_UP"), True)
check("  ...said", wait_for(lambda: QUICK_PHRASES[1] in tts.said), True)
core.say("four", source="speech")
q = question(core, "yes_no", "quick")
check("by voice ('four'): checked first", bool(q) and q["card"]["body"], QUICK_PHRASES[3])
check("  ...not said before 'yes'", QUICK_PHRASES[3] in tts.said, False)
core.answer("yes")
check("  ...said after 'yes'", wait_for(lambda: QUICK_PHRASES[3] in tts.said), True)
idle(core)

# ---- never "busy" after a quick phrase (bug: "Wait until the current request is finished") -----------
core, tts, actions = make(SlowTTS(seconds=0.3))
core.set_mode("conference")
core.set_clips([{"id": "demo1", "path": "/dev/null", "said": "x", "heard": "i need to use the bathroom", "seconds": 1.0}], "")
core.quick_phrase(3)
wait_for(lambda: QUICK_PHRASES[3] in tts.said)
time.sleep(0.2)
check("after a quick phrase is said, Cowork is not busy", (core.busy, core.state["status"]), (False, "ready"))
check("  ...the next quick phrase is said", core.quick_phrase(0) and wait_for(lambda: QUICK_PHRASES[0] in tts.said), True)
time.sleep(0.5)
check("  ...a test clip is not refused", core.play_clip("demo1", play_sound=False), "")
check("  ...and it is heard", wait_for(lambda: core.state["question"] is not None), True)
core.answer("no" if core.state["question"]["kind"] == "yes_no" else "none")
wait_for(lambda: core.state["question"] is None and not core.busy, 8)
core.answer("none")
wait_for(lambda: core.state["question"] is None and not core.busy, 8)

core, tts, actions = make(SlowTTS(seconds=0.3))
core.set_mode("conference")
for i in range(4):   # slowly: one after the other is finished
    core.quick_phrase(i)
    wait_for(lambda: QUICK_PHRASES[i] in tts.said)
check("1, 2, 3, 4 slowly: every one is said", [p for p in QUICK_PHRASES if p in tts.said], QUICK_PHRASES)
core, tts, actions = make(SlowTTS(seconds=0.3))
core.set_mode("conference")
for i in range(4):   # fast: each one cuts off the one before; none is refused
    check(f"  fast {i + 1}: accepted", core.quick_phrase(i), True)
check("  ...the last one picked is said", wait_for(lambda: QUICK_PHRASES[3] in tts.said), True)

core, tts, actions = make()
core.set_mode("conference")
core.say("i need to use the bathroom")
question(core, "yes_no")
core.answer("yes")
question(core, "window")   # the 3 s undo window right after a confirmed sentence
check("a quick phrase right after a confirmed sentence (undo window open) is not blocked", core.quick_phrase(1), True)
check("  ...both are said", wait_for(lambda: QUICK_PHRASES[1] in tts.said and "I need to use the bathroom." in tts.said), True)
idle(core)
check("typed text right after is a new request (not refused)", core.say("we gathered shells on the beach"), "request")
check("  ...and handled", bool(question(core, "yes_no")), True)
core.answer("none")
idle(core)

# ---- back to Computer mode: commands work again ---------------------------------------------------
core.set_mode("computer")
core.say("open mail")
wait_for(lambda: actions.done or core.state["question"] is not None)
if core.state["question"] is not None and core.state["question"]["kind"] != "window":
    core.answer("yes")
wait_for(lambda: actions.done)
check("Computer mode again: commands are carried out", [c for c, _ in actions.done][:1], ["open_app"])

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.stdout.flush()
import os  # noqa: E402
os._exit(0 if all(results) else 1)
