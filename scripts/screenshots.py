"""Screenshots of the Cowork screen, with the fakes, for checking and for slides.

Glass: the real window is see-through and macOS blurs the desktop behind it; that blur
cannot be captured without the Screen Recording permission. So these pictures place the
(see-through) page over a blurred, slightly darkened copy of the desktop wallpaper: an
approximation of what the real window looks like.

    .venv/bin/python scripts/screenshots.py [output folder]

No browser is opened: the page is drawn by WebKit inside this script (off
screen) and saved as PNG files. Nothing on the Mac is touched (fake actions),
the real profile is not changed (a temporary copy is used), and the camera is
not used: the camera preview shows a labelled test picture instead. The clips use
what the real WhisperKit heard; the Brain is the fake one (the real Bonsai results
for the same clips are in docs/COWORK.md).
"""

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

config.ASR = config.LLM = config.TTS = config.ACTIONS = "fake"

from AppKit import NSApplication, NSBackingStoreBuffered, NSBitmapImageRep, NSMakeRect, NSWindow  # noqa: E402
from Foundation import NSURL, NSDate, NSRunLoop, NSURLRequest  # noqa: E402
from WebKit import WKSnapshotConfiguration, WKWebView, WKWebViewConfiguration  # noqa: E402

from actions.fake import FakeActions  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.store import open_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import App  # noqa: E402
from ui.clips import CREDIT, load_clips  # noqa: E402
from ui.server import create_server  # noqa: E402

PORT = 8799
WIDTH, HEIGHT = 1440, 900


class TestPicture:
    """Stands in for the camera: a labelled picture, never the real camera."""
    def __init__(self):
        import cv2
        import numpy as np
        frame = np.full((270, 360, 3), (52, 48, 44), np.uint8)
        cv2.rectangle(frame, (0, 200), (360, 270), (40, 36, 33), -1)
        cv2.circle(frame, (180, 120), 46, (120, 140, 170), -1)          # a head
        cv2.rectangle(frame, (110, 170), (250, 270), (90, 110, 140), -1)  # shoulders
        cv2.line(frame, (262, 200), (300, 70), (180, 200, 230), 16)      # a raised arm
        cv2.putText(frame, "test picture", (12, 258), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1)
        self.frame = cv2.imencode(".jpg", frame)[1].tobytes()

    def jpeg(self):
        return self.frame

    def want(self, on):
        pass


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))


def wait(condition, seconds=8.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        pump(0.05)
    return False


def backdrop_image(width: int, height: int):
    """The desktop wallpaper, blurred and darkened roughly like the window's dark glass."""
    import subprocess
    from AppKit import NSScreen, NSWorkspace
    from PIL import Image, ImageEnhance, ImageFilter
    url = NSWorkspace.sharedWorkspace().desktopImageURLForScreen_(NSScreen.mainScreen())
    tmp = Path(tempfile.mkdtemp()) / "wallpaper.png"
    subprocess.run(["sips", "-s", "format", "png", url.path(), "--out", str(tmp)], capture_output=True)
    try:
        wall = Image.open(tmp).convert("RGB")
    except Exception:
        wall = Image.new("RGB", (width, height), (90, 70, 60))
    wall = wall.resize((width, height)).filter(ImageFilter.GaussianBlur(30))
    return ImageEnhance.Brightness(wall).enhance(0.75)


BACKDROP = None


def snapshot(web, path: Path) -> None:
    done = {}

    def handler(image, error):
        done["image"], done["error"] = image, error

    web.takeSnapshotWithConfiguration_completionHandler_(WKSnapshotConfiguration.alloc().init(), handler)
    wait(lambda: done, 10)
    if not done.get("image"):
        raise RuntimeError(f"snapshot failed: {done.get('error')}")
    image = done["image"]
    rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    rep.representationUsingType_properties_(4, {}).writeToFile_atomically_(str(path), True)   # 4 = PNG
    from PIL import Image
    page = Image.open(path).convert("RGBA")
    global BACKDROP
    if BACKDROP is None or BACKDROP.size != page.size:
        BACKDROP = backdrop_image(*page.size).convert("RGBA")
    Image.alpha_composite(BACKDROP, page).convert("RGB").save(path)
    print("saved", path)


def screen_scenes(core, web, out, ready, asking) -> None:
    """Word's start screen, a question answered, and an email written box by box."""
    from actions.screen import Screen
    fake = core.session.actions._actions
    start = Screen(app="Microsoft Word", pid=1, place="start screen", choices=["Blank document", "Open a recent file"])
    doc = Screen(app="Microsoft Word", pid=1, window="Document1", place="document", focus="the document", typeable=True)
    order = ["to", "cc", "subject", "body"]

    def email(field):
        return Screen(app="Mail", pid=2, place="new email", field=field, typeable=True,
                      focus="the message" if field == "body" else f"the {field.capitalize()} box")

    now = {"screen": start}
    fake.screen = lambda: now["screen"]
    fake.contacts = {"Me": "me@example.org"}
    plain_context, plain_run = fake.context, fake.run

    def context():
        found = plain_context()
        found["_screen"] = now["screen"]
        found["field"] = ["To", "Cc", "Subject", "Message"]
        return found

    def run(command, args):
        result = plain_run(command, args)
        current = now["screen"]
        if command == "new_document":
            now["screen"] = doc
        elif command == "new_email":
            now["screen"] = email("to")
        elif command in ("next_field", "previous_field") and current.place == "new email":
            i = order.index(current.field) + (1 if command == "next_field" else -1)
            now["screen"] = email(order[max(0, min(3, i))])
        return result

    fake.context, fake.run = context, run

    def say_and_confirm(text):
        n = len(core.state["history"])
        core.say(text)
        wait(lambda: core.state["question"] is not None or len(core.state["history"]) > n, 10)
        q = core.state["question"]
        if q and q["stage"] in ("did_you_mean", "address"):
            core.answer("yes")
            wait(lambda: core.state["question"] is None or core.state["question"]["id"] != q["id"])
        wait(lambda: len(core.state["history"]) > n or (core.state["question"] or {}).get("stage") == "screen", 10)
        q = core.state["question"]
        if q and q["kind"] == "window":
            core.answer("yes")
            wait(ready)

    core.new_task()
    core.set_dictation(True)
    say_and_confirm("type see you at noon")
    pump(0.6)
    snapshot(web, out / "14_word_start_screen_choice.png")
    core.answer("pick", 0)
    wait(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "window")
    pump(0.6)
    snapshot(web, out / "15_blank_document_then_typed.png")
    core.answer("yes")
    wait(ready)

    import repair.answer   # the answer the real Bonsai gave in the live test (2026-11-14), not the fake's placeholder
    repair.answer.answer_question = lambda *a, **k: (
        "Boston in late November is usually cold and rainy. I'm offline, so I can't check today's forecast.", "")
    core.voice.tts.not_ready = {"Boston"}   # a new sentence: not ready in time, so it is not spoken (on screen only)
    core.new_task()
    say_and_confirm("what's the weather in Boston")
    pump(0.6)
    snapshot(web, out / "16_question_answered.png")

    core.new_task()
    fake.target = "Mail"
    for text in ("write an email", "me", "next box", "next box"):
        say_and_confirm(text)
    pump(0.6)
    snapshot(web, out / "17_email_box_by_box.png")


def conference_scenes(core, web, out, ready, asking) -> None:
    """Conference mode: quick phrases, a sentence to confirm (silent), 'Preparing voice…', then said."""
    import threading as _threading
    core.new_task()
    core.set_mode("conference")
    tts = core.voice.tts
    now = time.time()
    with core._lock:   # what was said earlier in the meeting (shown smaller, above the newest)
        core.state["said"] = [{"text": t, "status": "said", "quick": q, "time": now - 60 + i}
                              for i, (t, q) in enumerate([("Good morning, everyone.", False),
                                                          ("I agree.", True),
                                                          ("I feel I can play this weekend.", False)])]
    core._set()
    pump(0.6)
    snapshot(web, out / "18_conference_quick_phrases.png")

    gate = _threading.Event()   # the voice waits here, as TTSKit does while making a new sentence
    plain = tts.speak

    def slow(line, wait=False, on_sound=None):
        gate.wait(30)
        return plain(line, wait=wait, on_sound=on_sound)

    tts.speak = slow
    core.play_clip("demo1", play_sound=False)
    wait(lambda: core.state["question"] is not None and core.state["question"]["stage"] == "did_you_mean", 10)
    pump(0.6)
    snapshot(web, out / "19_conference_did_you_mean.png")
    core.answer("yes")
    wait(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "window", 10)
    with core._lock:
        core.state["said"][-1]["status"] = "preparing"
    core._set()
    pump(0.6)
    snapshot(web, out / "20_conference_preparing_voice.png")
    gate.set()
    core.answer("yes")
    wait(ready)
    pump(0.6)
    snapshot(web, out / "21_conference_said.png")
    tts.speak = plain
    core.set_mode("computer")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else config.ROOT / "docs" / "screenshots")
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp())
    profile_path = work / "profile.json"
    shutil.copyfile(config.ROOT / "profiles" / "cowork_base.json", profile_path)
    profile = open_profile(profile_path, profile_path)

    tts = FakeTTS(quiet=True)
    core = App(profile, FakeLLM(), tts, actions=FakeActions(), profile_path=profile_path,
               backends={"Speech in": "real", "Brain": "real", "Voice": "real", "Mac actions": "fake"},
               question_wait_s=600)
    core.set_clips(load_clips(), CREDIT)
    core.state["metrics"]["offline"] = {"offline": True}   # as on the demo Mac with Wi-Fi off
    core.attach_camera(TestPicture())
    core.state["mic"]["on"] = True   # as in the demo (--real turns the microphone on)
    core.state["camera"]["on"] = True

    import uvicorn
    server = uvicorn.Server(uvicorn.Config(create_server(core), host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(1.0)

    NSApplication.sharedApplication()
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(-4000, -4000, WIDTH, HEIGHT), 0, NSBackingStoreBuffered, False)
    web = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, WIDTH, HEIGHT),
                                                         WKWebViewConfiguration.alloc().init())
    web.setValue_forKey_(False, "drawsBackground")   # see-through, like the real window
    window.setContentView_(web)
    window.orderBack_(None)
    web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(f"http://localhost:{PORT}/?still")))
    pump(2.0)

    errors = {}

    def check_js():
        web.evaluateJavaScript_completionHandler_("document.querySelector('#convo') ? 'ok' : 'missing'",
                                                  lambda r, e: errors.update(result=r, error=e))
        wait(lambda: errors, 3)
        if errors.get("result") != "ok":
            raise RuntimeError(f"page did not load: {errors}")

    check_js()
    asking = lambda: core.state["question"] is not None and core.state["question"]["kind"] != "window"  # noqa: E731
    ready = lambda: core.state["question"] is None and core.state["status"] == "ready"  # noqa: E731

    for old in out.glob("*.png"):
        old.unlink()
    core.set_dictation(True)
    pump(0.5)
    snapshot(web, out / "01_start_dictation_on.png")
    web.evaluateJavaScript_completionHandler_(   # a tooltip on the icon-only composer buttons
        "document.getElementById('dictBtn').dispatchEvent(new MouseEvent('mouseover', {bubbles: true}))", None)
    pump(0.9)
    snapshot(web, out / "01c_tooltip_dictation.png")
    web.evaluateJavaScript_completionHandler_("hideTip()", None)

    core._set(mic=dict(core.state["mic"], listening=True, partial="We can get a joke on the"))
    core._publish({"type": "level", "level": -24, "noise": -60, "speaking": True, "listening": True})
    pump(0.5)
    snapshot(web, out / "01b_listening_partial_words.png")
    core._set(mic=dict(core.state["mic"], listening=False, partial=""))

    web.evaluateJavaScript_completionHandler_("document.getElementById('clipBtn').click()", None)
    pump(0.6)
    snapshot(web, out / "02_clip_list.png")
    web.evaluateJavaScript_completionHandler_("document.getElementById('clipBtn').click()", None)
    web.evaluateJavaScript_completionHandler_("openModal('quick')", None)
    pump(0.5)
    snapshot(web, out / "02b_quick_actions.png")
    web.evaluateJavaScript_completionHandler_("qaFilter='open'; renderModal()", None)
    pump(0.4)
    snapshot(web, out / "02c_quick_actions_filtered.png")
    web.evaluateJavaScript_completionHandler_("closeModal(); openModal('settings')", None)
    pump(0.4)
    snapshot(web, out / "02d_settings_about.png")
    web.evaluateJavaScript_completionHandler_("closeModal()", None)

    def play(clip):
        n = len(core.state["history"])
        core.play_clip(clip, play_sound=False)
        wait(lambda: core.state["question"] is not None or len(core.state["history"]) > n, 10)
        return n

    n = play("demo1")                       # understood directly -> typed
    wait(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "window")
    pump(0.6)
    snapshot(web, out / "03_clip1_typed_directly.png")
    core.answer("yes")
    wait(ready)

    play("demo2")                           # needs "Did you mean"
    wait(asking)
    core.hold("RIGHT", 0.6)
    pump(0.8)
    snapshot(web, out / "04_clip2_did_you_mean_camera.png")
    core.hold(None, 0)
    core.answer("yes", source="hand")
    wait(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "window")
    pump(0.6)
    snapshot(web, out / "05_clip2_typed_and_learned.png")
    core.answer("yes")
    wait(ready)

    play("demo3")                           # replay: instant
    wait(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "window")
    pump(0.6)
    snapshot(web, out / "06_clip3_replay_instant.png")
    core.answer("yes")
    wait(ready)

    core.say("send the email")
    wait(asking)
    pump(0.6)
    snapshot(web, out / "07_risky_send.png")
    core.answer("yes")
    wait(ready)

    core.say("blah blah")
    wait(asking)
    core.answer("no")
    wait(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "choice")
    core.hold("RIGHT_UP", 0.4)
    pump(0.8)
    snapshot(web, out / "08_four_options.png")
    core.hold(None, 0)
    core.answer("none")
    wait(ready)
    pump(0.4)
    snapshot(web, out / "09_conversation.png")

    core.new_task()
    core.camera_state({"on": False, "problem": f'The built-in "{config.CAMERA_NAME}" was not found, so the camera '
                                               "is off. No other camera is used."})
    core.say("wan oop en noat")
    wait(asking)
    pump(0.6)
    snapshot(web, out / "10_camera_not_found.png")
    core.answer("no")
    wait(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "choice")
    core.answer("none")
    wait(ready)
    core.new_task()
    core._set(mic=dict(core.state["mic"], on=False, problem="Could not open the microphone (no input device)."))
    pump(0.6)
    snapshot(web, out / "11_mic_unavailable.png")
    core._set(mic=dict(core.state["mic"], on=True, problem=""))
    core.camera_state({"on": True, "problem": ""})

    # ---- looking at the screen (a fake Mac whose screen the script sets) ----------------------
    screen_scenes(core, web, out, ready, asking)
    conference_scenes(core, web, out, ready, asking)
    server.should_exit = True
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
