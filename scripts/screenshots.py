"""Screenshots of the Cowork screen, with the fakes, for checking and for slides.

    .venv/bin/python scripts/screenshots.py [output folder]

No browser is opened: the page is drawn by WebKit inside this script (off
screen) and saved as PNG files. Nothing on the Mac is touched (fake actions),
the real profile is not changed (a temporary copy is used), and the camera is
not used: the camera preview shows a labelled test picture instead.
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
    print("saved", path)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else config.ROOT / "docs" / "screenshots")
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp())
    profile_path = work / "profile.json"
    shutil.copyfile(config.ROOT / "profiles" / "cowork_base.json", profile_path)
    profile = open_profile(profile_path, profile_path)

    core = App(profile, FakeLLM(), FakeTTS(quiet=True), actions=FakeActions(), profile_path=profile_path,
               backends={"Speech in": "real", "Brain": "real", "Voice": "real", "Mac actions": "fake"},
               question_wait_s=600)
    core.set_clips(load_clips(), CREDIT)
    core.state["metrics"]["offline"] = {"offline": True}   # as on the demo Mac with Wi-Fi off
    core.attach_camera(TestPicture())
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

    snapshot(web, out / "1_start.png")

    core.say("plea op en male")
    wait(asking)
    core.hold("RIGHT", 0.65)
    pump(0.8)
    snapshot(web, out / "2_did_you_mean_with_camera.png")
    core.hold(None, 0)
    core.answer("yes")
    wait(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "window")
    pump(0.6)
    snapshot(web, out / "3_step_done_with_undo.png")
    wait(ready)

    core.say("plea op en male")
    wait(lambda: core.state["question"] is not None)
    core.answer("yes")   # keep it (closes the correction window)
    wait(ready)
    core.say("email anna saying lunch at noon")
    wait(asking)
    pump(0.6)
    snapshot(web, out / "4_email_card.png")
    core.answer("yes")
    wait(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "window")
    core.answer("yes")
    wait(ready)
    core.say("send it")
    wait(asking)
    pump(0.6)
    snapshot(web, out / "5_risky_send.png")
    core.answer("yes")
    wait(ready)

    core.say("blah blah")
    wait(asking)
    core.answer("no")
    wait(lambda: core.state["question"] is not None and core.state["question"]["kind"] == "choice")
    core.hold("RIGHT_UP", 0.4)
    pump(0.8)
    snapshot(web, out / "6_four_options.png")
    core.hold(None, 0)
    core.answer("pick", 1)
    wait(ready, 12)
    if core.state["question"]:
        core.answer("yes")
        wait(ready)

    web.evaluateJavaScript_completionHandler_("document.getElementById('clipBtn').click()", None)
    pump(0.6)
    snapshot(web, out / "7_test_clips.png")
    web.evaluateJavaScript_completionHandler_("document.getElementById('clipBtn').click()", None)
    core.play_clip("M04_2_0015", play_sound=False)   # "up" -> scroll up
    wait(lambda: core.state["turn"] and core.state["turn"]["source"] == "test clip", 5)
    wait(lambda: core.state["question"] is not None or ready(), 8)
    pump(0.6)
    snapshot(web, out / "8_torgo_clip.png")
    if core.state["question"]:
        core.answer("yes")
    wait(ready)
    pump(0.5)
    snapshot(web, out / "9_conversation.png")
    server.should_exit = True
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
