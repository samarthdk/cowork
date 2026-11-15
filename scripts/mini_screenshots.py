"""Pictures of the mini window (the small window in the top right corner), with the fakes.

    .venv/bin/python scripts/mini_screenshots.py [output folder]

Drawn off screen by WebKit, like scripts/screenshots.py (same blurred-wallpaper
backdrop). Nothing on the Mac is touched and the camera is not used.
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

from AppKit import NSApplication, NSBackingStoreBuffered, NSMakeRect, NSWindow  # noqa: E402
from Foundation import NSURL, NSURLRequest  # noqa: E402
from WebKit import WKWebView, WKWebViewConfiguration  # noqa: E402

from actions.fake import FakeActions  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.store import open_profile  # noqa: E402
from scripts.screenshots import TestPicture, pump, snapshot, wait  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import App  # noqa: E402
from ui.server import create_server  # noqa: E402

PORT = 8798


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else config.ROOT / "docs" / "screenshots")
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp())
    shutil.copyfile(config.ROOT / "profiles" / "cowork_base.json", work / "p.json")
    profile = open_profile(work / "p.json", work / "p.json")
    core = App(profile, FakeLLM(), FakeTTS(quiet=True), actions=FakeActions(apps=("Mail", "Microsoft Word")),
               profile_path=work / "p.json", question_wait_s=600)
    core.attach_camera(TestPicture())
    core.state["camera"]["on"] = True
    core.state["mic"].update(on=True, listening=True, level=-30)
    core.state["window"] = {"mode": "mini", "hands_free": True}

    import uvicorn
    server = uvicorn.Server(uvicorn.Config(create_server(core), host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(1.0)
    NSApplication.sharedApplication()
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(-4000, -4000, 360, 330), 0, NSBackingStoreBuffered, False)
    web = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 360, 330), WKWebViewConfiguration.alloc().init())
    web.setValue_forKey_(False, "drawsBackground")
    window.setContentView_(web)
    window.orderBack_(None)
    web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(f"http://localhost:{PORT}/mini")))
    pump(2.0)

    web.setFrameSize_((360, 100))
    pump(0.5)
    snapshot(web, out / "12_mini_listening.png")

    core.say("send the email")   # risky: always asks
    wait(lambda: core.state["question"] is not None)
    pump(1.5)
    web.setFrameSize_((360, 300))
    pump(0.8)
    snapshot(web, out / "13_mini_question.png")
    import os
    os._exit(0)


if __name__ == "__main__":
    main()
