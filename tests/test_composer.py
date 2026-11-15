"""The composer: icon-only round buttons with tooltips (no text on the buttons, no pills).

Runs the real page (in WebKit, off screen) against the fakes and checks each button's
aria-label / tooltip follows the state: Speak, Listening, Dictation on/off, Test clips, Send.
Also checks Settings > About shows the TORGO credit, and the mini window has no text buttons
except the answer buttons. Run:
    .venv/bin/python tests/test_composer.py
"""

import os
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

PORT = 8796
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


def load(path):
    web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(f"http://localhost:{PORT}{path}")))
    pump(2.0)


NSApplication.sharedApplication()
window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    NSMakeRect(-4000, -4000, 1300, 850), 0, NSBackingStoreBuffered, False)
web = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 1300, 850), WKWebViewConfiguration.alloc().init())
window.setContentView_(web)
window.orderBack_(None)
load("/")

label = lambda i: js(f"document.getElementById('{i}').getAttribute('aria-label')")  # noqa: E731
check("composer buttons carry no visible text", js(
    "[...document.querySelectorAll('.composer-row button')].map(b => b.textContent.trim()).join('')"), "")
check("five round icon buttons (mic, dictation, clips, mode, send)", js("document.querySelectorAll('.composer-row button.ibtn').length"), 5)
check("no pills left in the composer", js("document.querySelectorAll('.composer .pill, .composer .cbtn').length"), 0)
check("dictation icon is a keyboard (with its status dot)", js("!!document.querySelector('#dictBtn svg rect') && !!document.getElementById('dictDot')"), True)
check("no 'Press the mic' chip on the start screen", js("document.querySelector('.empty') ? document.querySelector('.empty').textContent.includes('Press the mic') : 'no start screen'"), False)
check("placeholder", js("document.getElementById('typed').placeholder"), "Type what was said, or press the mic…")
check("Speak label", label("speak"), "Speak (microphone off)")   # the fakes have no microphone
core._set(mic=dict(core.state["mic"], on=True))
pump(0.4)
check("Speak label, microphone on", label("speak"), "Speak")
core._set(mic=dict(core.state["mic"], listening=True))
pump(0.4)
check("listening label", label("speak"), "Listening… (click to stop)")
check("  ...the mic turns red-orange", js("document.getElementById('speak').classList.contains('listening')"), True)
core._publish({"type": "level", "level": -20, "noise": -60, "speaking": True, "listening": True})
pump(0.4)
check("  ...the ring follows the sound level", float(js("document.getElementById('speak').style.getPropertyValue('--lvl')") or 0) > 0.5, True)
check("  ...partial words line still shown", js("document.getElementById('live').hidden"), False)
core._set(mic=dict(core.state["mic"], listening=False))
pump(0.4)
check("Dictation off label", label("dictBtn"), "Dictation off")
check("  ...status dot red-orange (not 'on')", js("document.getElementById('dictDot').classList.contains('on')"), False)
core.set_dictation(True)
pump(0.4)
check("Dictation on label", (label("dictBtn") or "").startswith("Dictation on"), True)
check("  ...status dot green", js("document.getElementById('dictDot').classList.contains('on')"), True)
check("Test clips label", label("clipBtn"), "Test clips")
check("Send label", label("sendBtn"), "Send")
check("Mode label", label("modeBtn"), "Computer mode")
core.set_mode("conference")
pump(0.4)
check("  ...Conference mode label", label("modeBtn"), "Conference mode")
check("  ...the dictation button is hidden (nothing is typed)", js("getComputedStyle(document.getElementById('dictBtn')).display"), "none")
check("  ...the conference view shows the quick phrases", js("document.querySelectorAll('.conf-quick button[data-quick]').length"), 4)
core.set_mode("computer")
pump(0.4)
check("Send not solid when empty", js("document.getElementById('sendBtn').classList.contains('ready')"), False)
js("const i=document.getElementById('typed'); i.value='open notes'; i.dispatchEvent(new Event('input'))")
check("Send solid with text", js("document.getElementById('sendBtn').classList.contains('ready')"), True)
js("document.getElementById('typed').value=''; document.getElementById('typed').dispatchEvent(new Event('input'))")

# tooltip: after half a second of hover
js("document.getElementById('clipBtn').dispatchEvent(new MouseEvent('mouseover', {bubbles: true}))")
pump(0.2)
check("tooltip waits ~half a second", js("document.getElementById('tip').hidden"), True)
pump(0.6)
check("tooltip shows the state", js("document.getElementById('tip').hidden ? '' : document.getElementById('tip').textContent"), "Test clips")
js("document.body.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}))")
pump(0.1)
check("tooltip goes away", js("document.getElementById('tip').hidden"), True)

js("openModal('settings')")
pump(0.3)
about = js("document.getElementById('aboutCredit').textContent") or ""
check("Settings > About has the TORGO credit",
      all(w in about for w in ("Rudzicz", "Namasivayam", "Wolff", "University of Toronto", "2012", "M04", "cerebral palsy")), True)
js("closeModal()")
readme = (config.ROOT / "README.md").read_text()
check("README.md has the credit", all(w in readme for w in ("Rudzicz", "Namasivayam", "Wolff", "Toronto", "2012", "M04", "cerebral palsy")), True)

load("/mini")
check("mini window: no text on its own button", js("document.getElementById('expand').textContent.trim()"), "")
check("  ...but an aria-label", js("document.getElementById('expand').getAttribute('aria-label')"), "Open Cowork")

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
