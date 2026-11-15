"""The question card must render once and stay stable (bug: after "what's the
weather in Boston?" the Yes/No card flickered constantly).

Cause (measured): the camera reports hold-to-confirm progress on every frame
(15-30 a second) even when no hand is held up; each report re-sent the whole
screen state, and the page rebuilt the conversation, replaying the card's
slide-in animation every time.

This test runs the real page (in WebKit, off screen) against the fakes, opens a
question, then sends camera-style updates for a few seconds, and counts how often
the card is rebuilt. Run:
    .venv/bin/python tests/test_no_flicker.py
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
from actions.fake import FakeActions  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.store import open_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import App  # noqa: E402
from ui.server import create_server  # noqa: E402

PORT = 8797
results = []


def check(name, got, expected):
    ok = got == expected
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}, expected {expected!r}")


work = Path(tempfile.mkdtemp())
shutil.copyfile(config.ROOT / "profiles" / "cowork_base.json", work / "p.json")
profile = open_profile(work / "p.json", work / "p.json")
core = App(profile, FakeLLM(), FakeTTS(quiet=True), actions=FakeActions(), profile_path=work / "p.json",
           question_wait_s=60)

# ---- the app: camera noise is not sent on ----------------------------------------------
sent = []
core.subscribe(lambda m: sent.append(m["type"]))
for _ in range(100):                 # a camera frame with nothing held up, 100 times
    core.hold(None, 0.0)
check("no hand held up: 100 camera frames send nothing", len(sent), 0)
for i in range(100):                 # a slowly filling hold: only real changes go out
    core.hold("RIGHT_UP", i / 100)
check("  ...a hold filling up is sent in steps (at most 21), not every frame", len(sent) <= 21, True)
core.hold(None, 0.0)

# ---- the page: the card is drawn once ---------------------------------------------------
import uvicorn  # noqa: E402
from AppKit import NSApplication, NSBackingStoreBuffered, NSMakeRect, NSWindow  # noqa: E402
from Foundation import NSURL, NSDate, NSRunLoop, NSURLRequest  # noqa: E402
from WebKit import WKWebView, WKWebViewConfiguration  # noqa: E402

server = uvicorn.Server(uvicorn.Config(create_server(core), host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
time.sleep(1.0)


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.02))


def js(code):
    out = {}
    web.evaluateJavaScript_completionHandler_(code, lambda value, error: out.update(v=value, e=error))
    end = time.monotonic() + 5
    while "v" not in out and time.monotonic() < end:
        pump(0.02)
    return out.get("v")


NSApplication.sharedApplication()
window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    NSMakeRect(-4000, -4000, 1300, 850), 0, NSBackingStoreBuffered, False)
web = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 1300, 850), WKWebViewConfiguration.alloc().init())
window.setContentView_(web)
window.orderBack_(None)
web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(f"http://localhost:{PORT}/")))
pump(2.0)

core.say("what is the weather in boston")    # the fake Brain is unsure: a Yes/No card
end = time.monotonic() + 8
while core.state["question"] is None and time.monotonic() < end:
    pump(0.05)
pump(0.8)
check("a question card is on the page", js("document.querySelectorAll('.perm').length"), 1)
js("""window.__built = 0; window.__card = document.querySelector('.perm');
new MutationObserver((list) => { for (const m of list) for (const n of m.addedNodes)
  if (n.nodeType === 1 && (n.matches('.perm') || n.querySelector('.perm'))) window.__built++; })
  .observe(document.getElementById('convo'), {childList: true, subtree: true});""")

# 3 seconds of what the real app sends while a card is open: camera holds coming and going,
# microphone partial words, metrics, camera state.
stop = time.monotonic() + 3.0
i = 0
while time.monotonic() < stop:
    i += 1
    core.hold("RIGHT_UP" if i % 40 < 20 else None, (i % 20) / 20)
    if i % 10 == 0:
        core._set(mic=dict(core.state["mic"], partial=f"words {i}"))
        core._set(metrics=dict(core.state["metrics"], memory={"rss_mb": 100 + i}))
    pump(1 / 30)
pump(0.5)
check("  ...it is not rebuilt while the camera and microphone send updates", js("window.__built"), 0)
check("  ...it is the very same card on the page (drawn once)", js("document.querySelector('.perm') === window.__card"), True)
check("  ...its slide-in played only when it first appeared",
      js("document.querySelector('.perm').classList.contains('fresh')") is not None, True)
core.hold("RIGHT_UP", 0.5)
pump(0.5)
check("the hold ring still shows (on the same card)",
      js("document.querySelector('.perm .allow').classList.contains('holding') && document.querySelector('.perm') === window.__card"), True)
core.answer("no")
pump(1.0)
check("answering replaces it (a new question is drawn fresh)", js("document.querySelectorAll('.perm.fresh').length"), 1)

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
import os  # noqa: E402
os._exit(0 if all(results) else 1)
