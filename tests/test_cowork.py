"""Checks for the Cowork screen's core: the conversation (requests, step cards,
permission cards), wording, test clips, the new Mac actions' helpers and the
web endpoints. Fakes only: no models, microphone, camera, browser or real Mac
actions. Run:
    .venv/bin/python tests/test_cowork.py
"""

import json
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

config.ASR = config.LLM = config.TTS = config.ACTIONS = "fake"
from actions.fake import FakeActions  # noqa: E402
from actions.mac import recipient, search_url, site_url  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.profile import load_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui import wording  # noqa: E402
from ui.app import App  # noqa: E402
from ui.clips import CLIP_IDS, CREDIT, load_clips  # noqa: E402

BASE = load_profile(config.ROOT / "profiles" / "cowork_base.json")
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


def wait_for(condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def make():
    import copy
    tts = FakeTTS(quiet=True)
    actions = FakeActions()
    core = App(copy.deepcopy(BASE), FakeLLM(), tts, actions=actions)
    core.set_voice(True)   # the tests below exercise speaking; the app itself starts silent
    return core, tts, actions


def asking(core, kind=None):
    return wait_for(lambda: core.state["question"] is not None
                    and (kind is None or core.state["question"]["kind"] == kind))


def finished(core, n):
    return wait_for(lambda: len(core.state["history"]) >= n and core.state["status"] == "ready", 8)


# ---- speaking is off by default (turned on in Settings) ----------------------
import copy
silent = App(copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=FakeActions())
check("a fresh app starts silent (speaking off by default in Computer mode)", silent.state["voice"]["on"], False)
silent.set_voice(True)
check("  ...the Settings toggle turns speaking on", silent.state["voice"]["on"], True)
silent.set_voice(False)
check("  ...and back off", silent.state["voice"]["on"], False)


# ---- the profile ----------------------------------------------------------
names = [c["name"] for c in BASE["commands"]]
check("Cowork profile has the new actions", all(n in names for n in (
    "open_app", "new_email", "next_field", "previous_field", "go_to_field", "send_email", "read_latest_email",
    "new_document", "save_document", "new_tab", "search_for", "answer_question",
    "type_text", "close_app", "scroll_down", "scroll_up")), True)
check("  ...websites and search are hidden (they need the internet); email is written box by box",
      [n for n in ("open_website", "search_web", "compose_email") if n in names], [])
check("  ...sending is risky, writing is not",
      {c["name"]: c["safety"] for c in BASE["commands"] if c["name"] in ("send_email", "new_email")},
      {"send_email": "risky", "new_email": "safe"})
check("  ...the flaky free 'click' is left out (the menu fallback replaces it)", "click" in names, False)
check("  ...reply is in (opens a reply by keyboard shortcut; nothing is sent)", "reply_email" in names, True)

check("apps can be closed by name ('close Word') as well as 'close the app'",
      {c["name"]: list(c["args"]) for c in BASE["commands"] if c["name"] in ("quit_app", "close_app")},
      {"quit_app": ["app"], "close_app": []})
from actions.mac import NEVER_CLOSE, NOT_A_TARGET  # noqa: E402
check("  ...never Finder, Terminal, Cowork or LM Studio (it runs the Brain)",
      all(a in NEVER_CLOSE and a in NOT_A_TARGET for a in ("Finder", "Terminal", "Python", "LM Studio")), True)

# ---- wording ----------------------------------------------------------------
check("request wording: command", wording.request_text("open_app", {"app": "Mail"}, BASE), "Open Mail")
check("request wording: close an app", wording.request_text("quit_app", {"app": "Microsoft Word"}, BASE), "Close Microsoft Word")
check("request wording: missing detail", wording.request_text("open_app", {}, BASE), "Open an app")
check("request wording: phrase", wording.request_text(BASE["phrases"][0], {}, BASE), BASE["phrases"][0])
check("from the engine's description", wording.from_meaning("type_text (text: cats, dogs)", BASE), "Type \u201ccats, dogs\u201d")

check("step title", wording.step_title("Opening Mail."), "Opening Mail")

# ---- helpers of the real actions (no Mac touched) ---------------------------
check("known website", site_url("youtube"), "https://www.youtube.com")
check("unknown website", site_url("my bank"), None)
check("search address", search_url("weather in boston"), "https://www.google.com/search?q=weather+in+boston")
check("contact name to address", recipient("anna", {"Anna": "anna@example.com"}), "anna@example.com")
check("an address stays", recipient("x@y.org", {"Anna": "a@b"}), "x@y.org")

# ---- camera: the built-in FaceTime HD Camera by name, never anything else ----
import types  # noqa: E402


def with_cameras(*devices):
    """Swap in a fake macOS camera list: (name, is_iphone) pairs."""
    class Device:
        def __init__(self, name, iphone):
            self.name, self.iphone = name, iphone

        def localizedName(self):
            return self.name

        def isContinuityCamera(self):
            return self.iphone

    fake = types.SimpleNamespace(AVMediaTypeVideo="vide", AVCaptureDevice=types.SimpleNamespace(
        devicesWithMediaType_=lambda kind: [Device(n, i) for n, i in devices]))
    sys.modules["AVFoundation"] = fake


def pick():
    try:
        return config.pick_camera()
    except config.CameraNotFound as error:
        return str(error)


real_av = sys.modules.get("AVFoundation")
with_cameras(("Samarth's iPhone Camera", True), ("FaceTime HD Camera", False))
check("built-in camera chosen by name, after an iPhone", pick(), 1)
with_cameras(("FaceTime HD Camera", True))
check("an iPhone pretending to be it is not used", isinstance(pick(), str), True)
with_cameras(("Samarth's iPhone Camera", True), ("USB Webcam", False))
message = pick()
check("missing built-in camera: no fallback, a clear message",
      isinstance(message, str) and "FaceTime HD Camera" in message and "No other camera" in message, True)
check("  ...names the cameras it saw", "USB Webcam" in message, True)
if real_av is not None:
    sys.modules["AVFoundation"] = real_av
else:
    sys.modules.pop("AVFoundation", None)
camera_messages = []
from choose.camera import Camera  # noqa: E402
cam = Camera.__new__(Camera)
cam.on_state, cam._wanted, cam._wanted_until = camera_messages.append, False, 99e9
cam._give_up("The built-in camera was not found.")
check("  ...the camera reports it and stays off", (camera_messages[-1]["on"], cam._needed()), (False, False))
page_text = (config.ROOT / "ui" / "static" / "cowork.html").read_text()
check("the page itself never opens a camera", "getUserMedia" in page_text or "mediaDevices" in page_text, False)
core, tts, actions = make()
core.attach_camera(types.SimpleNamespace(want=lambda on: None, jpeg=lambda: None))
core.camera_state({"on": False, "problem": "The built-in camera was not found."})
check("  ...the screen shows the message", (core.state["camera"]["problem"], core.state["notice"]["text"]),
      ("The built-in camera was not found.", "The built-in camera was not found."))

# (which voice speaks, and when a line stays silent: tests/test_voice.py)

# ---- sending only ever to "Me" -----------------------------------------------
import actions.mac as mac_module  # noqa: E402
from actions.mac import MacActions  # noqa: E402
from actions.screen import Screen  # noqa: E402
mac = MacActions()
mac.contacts = {"Me": "me@example.org", "Anna": "anna@example.com"}
draft = {"to": "anna@example.com"}
mac.screen = lambda: Screen(app="Mail", pid=1, place="new email", field="body", typeable=True)
saved_fields, saved_value = mac_module.mail_fields, mac_module.value_of
mac_module.mail_fields = lambda pid: {"to": "to"}
mac_module.value_of = lambda element: draft[element]
pressed = []
mac._key = lambda key, **kw: (pressed.append((key, kw)), types.SimpleNamespace(ok=True, problem=""))[1]   # never really pressed here
mac._wait_for = lambda test, seconds=0, every=0: Screen(app="Mail", pid=1, place="inbox")
REFUSED = "Sending to other people is turned off in this demo. The email is still open in Mail."
check("sending to anyone but 'Me' is refused, the draft stays", (mac.run("send_email", {}).problem, pressed), (REFUSED, []))
draft["to"] = "me@example.org"
sent = mac.run("send_email", {})
check("  ...to 'Me' only, it is really sent (Send pressed)", (sent.ok, pressed[-1:]), (True, [("d", {"command_key": True, "shift": True})]))
check("  ...EMAIL_REALLY_SEND is unchanged (off)", config.EMAIL_REALLY_SEND, False)
pressed.clear()
draft["to"] = "me@example.org, anna@example.com"
check("  ...one other address with 'Me' is refused too", (mac.run("send_email", {}).problem, pressed), (REFUSED, []))
mac.screen = lambda: Screen(app="Mail", pid=1, place="inbox")
check("  ...with no email open there is nothing to send", "no email open" in mac.run("send_email", {}).problem, True)
mac_module.mail_fields, mac_module.value_of = saved_fields, saved_value
mac.front.stop()

# ---- microphone off: the screen says why ---------------------------------------
core, tts, actions = make()
core.mic_off("Started with --no-mic.")
check("mic off: the reason is kept for the screen", (core.state["mic"]["on"], core.state["mic"]["off_reason"]),
      (False, "Started with --no-mic."))
page_text = (config.ROOT / "ui" / "static" / "cowork.html").read_text()
check("  ...and shown under the Speak button", 'id="micWhy"' in page_text and "Speak is off: " in page_text, True)
main_text = (config.ROOT / "ui" / "__main__.py").read_text()
check("--real turns the microphone on unless --no-mic",
      "use_mic = not options.no_mic and (options.mic or options.real)" in main_text, True)

# ---- microphone: unclear speech gets one hinted retry; Speak toggles ------------
from asr.base import Transcript  # noqa: E402


class HintASR:
    """First try: nothing ("[Inaudible]"). With a hint: `text` at confidence `logprob`."""
    def __init__(self, text, logprob):
        self.text, self.logprob, self.prompts = text, logprob, []

    def transcribe(self, path, prompt=""):
        self.prompts.append(prompt)
        if not prompt:
            return Transcript("", "[ Inaudible ]", -0.52)
        return Transcript(self.text, self.text, self.logprob)


import copy as _copy  # noqa: E402
for text, logprob, kept in (("Open Mail.", -0.02, True), ("Watch water.", -0.62, False)):
    asr = HintASR(text, logprob)
    core = App(_copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), asr=asr, actions=FakeActions())
    core.heard_audio("x.wav", segment=types.SimpleNamespace(speech_s=1.0))
    got = wait_for(lambda: core.state["turn"] is not None, 1.0)
    check(f"'[Inaudible]' then hinted {text!r} at {logprob}: {'kept' if kept else 'rejected'}",
          (got and core.state["turn"]["heard"] == text) if kept else
          (not got and "Couldn't make out" in core.state["notice"]["text"]), True)
    check("  ...the hint is the person's own phrases", bool(asr.prompts[-1]) and BASE["phrases"][0][:20] in asr.prompts[-1], True)
    if kept:
        check("  ...and the screen says it listened again",
              any("hint" in i.get("text", "") for i in core.state["turn"]["items"]), True)
asr = HintASR("", 0.0)
core = App(_copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), asr=asr, actions=FakeActions())
core.heard_audio("x.wav", segment=types.SimpleNamespace(speech_s=0.1))
check("a click (0.1 s of sound) gets no hinted retry", asr.prompts, [""])


class FakeListener:
    running, listening = True, False

    def trigger(self, wait_s=None):
        self.listening = True

    def cancel(self):
        self.listening = False

    def set_always(self, on):
        self.listening = on

    def mute(self): pass

    def unmute(self): pass


core = App(_copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=FakeActions())
core.attach_listener(FakeListener())
check("Speak starts listening, a second press stops it", (core.listen_toggle(), core.listen_toggle()), (True, False))

# ---- the dictation target -------------------------------------------------------
from ui.compound import split as split_compound  # noqa: E402
check("split: command, then type the rest", split_compound("open notes and type see you at noon"),
      ("open notes", "see you at noon", True))
check("  ...a near match is only a guess (ask first)", split_compound("open chrome and right weather in boston"),
      ("open chrome", "weather in boston", False))
check("  ...one request stays one request", split_compound("I need to use the bathroom"), None)

core = App(_copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=FakeActions(target=""))
core.set_dictation(True)
fa = core.session.actions._actions
core.say("I need to use the bathroom")
check("no target yet: 'Which app should I type into?'", asking(core, "choice") and core.state["question"]["card"]["title"],
      "Which app should I type into?")
check("  ...3 recent apps plus Something else", [o["text"] for o in core.state["question"]["card"]["options"]],
      ["Google Chrome", "Notes", "Microsoft Word", "Something else\u2026"])
core.answer("pick", 1)
check("  ...the chosen app is the target and the sentence is typed there",
      wait_for(lambda: ("type_text", {"text": "I need to use the bathroom. "}) in fa.done) and fa.target, "Notes")
check("  ...the pill shows it", wait_for(lambda: core.state["dictation"]["app"] == "Notes"), True)
core.answer("yes")
finished(core, 1)

core = App(_copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=FakeActions(apps=("Notes", "Mail"), target=""))
fa = core.session.actions._actions
core.say("open notes and type see you at noon")
if asking(core, "yes_no"):
    core.answer("yes")   # the fake Brain may ask "Did you mean: Open Notes?"
check("'open notes and type ...': opens Notes, then types the rest there",
      wait_for(lambda: ("type_text", {"text": "See you at noon "}) in fa.done, 5) and
      [c for c, _ in fa.done][:2], ["open_app", "type_text"])
check("  ...one message on screen with both", wait_for(lambda: "then type" in (core.state["turn"]["request"] or "")), True)
if asking(core, "window"):
    core.answer("yes")
finished(core, 1)

core = App(_copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=FakeActions(apps=("Google Chrome",)))
core.say("open chrome and right weather in boston")
check("unsure split: 'Did you mean' first", asking(core, "yes_no") and core.state["question"]["card"]["body"],
      "\u201copen chrome\u201d, then type \u201cweather in boston\u201d")
core.answer("no")

# ---- screen-only commands and Quick Actions ------------------------------------------
core = App(_copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=FakeActions())
check("the catalog lists phrases, commands and apps", (len(core.state["catalog"]["phrases"]), "Mail" in core.state["catalog"]["apps"],
      any(c["request"] == "Press Enter" for c in core.state["catalog"]["commands"])), (len(BASE["phrases"]), True, True))
check("'show learned' opens the Learned row (screen only, no request)",
      (core.say("show learned"), core.state["ui"]["action"], core.state["ui"]["value"]), ("ui", "open", "learned"))
check("'quick actions' opens Quick Actions", (core.say("quick actions"), core.state["ui"]["quick"]), ("ui", True))
core.heard_audio("x.wav", known=Transcript("open notes", "open notes", -0.1))
check("  ...while open, speech filters it", (core.state["ui"]["action"], core.state["ui"]["value"]), ("filter", "open notes"))
check("  ...a thumb picks one of the first four", (core.signal("RIGHT_UP"), core.state["ui"]["action"], core.state["ui"]["value"]),
      (True, "pick", 1))
check("  ...a typed request is never swallowed as a filter", core.say("I need to use the bathroom"), "request")
core.set_ui(False)

# ---- conversation: unsure -> permission card -> step card --------------------
core, tts, actions = make()
core.say("plea op en male")
check("unsure: a permission card", asking(core, "yes_no"), True)
card = core.state["question"]["card"]
check("  ...worded as a request", (card["title"], card["body"], card["allow"]), ("Did you mean…?", "Open Mail", "Yes"))
item = core.state["turn"]["items"][-1]
check("  ...and in the conversation as an open card", (item["type"], item["status"]), ("permission", "open"))
core.answer("yes", source="screen")
check("  ...then the correction window", asking(core, "window"), True)
turn = core.state["turn"]
step = next(i for i in turn["items"] if i["type"] == "step")
check("  ...a step card, done", (step["title"], step["status"]), ("Opening Mail", "done"))
check("  ...with the undo countdown", "undo_until" in step, True)
check("  ...the request is shown as typed", turn["request"], "Open Mail")
check("  ...the card shows what was answered", turn["items"][0]["answer"], "Yes")
core.answer("yes")
check("  ...the request joins the history", finished(core, 1), True)
check("  ...the undo countdown is gone", "undo_until" in core.state["history"][0]["items"][2], False)
check("  ...it was done on the Mac", actions.done, [("open_app", {"app": "Mail"})])
check("  ...the step was spoken", wait_for(lambda: "Opening Mail." in tts.said), True)
learned = [i["text"] for i in core.state["history"][0]["items"] if i["type"] == "note"]
check("  ...'Learned' note in plain words", learned, ["Learned: “plea op en male” means “Open Mail”"])
feed = core.state["feed"][0]
check("  ...learning feed in plain words", (feed["kind"], feed["request"]), ("learned", "Open Mail"))

core.say("plea op en male")
check("the second time: instant (fast path), no card", asking(core, "window"), True)
check("  ...noted in the conversation", core.state["turn"]["items"][0]["icon"], "bolt")
core.answer("no", source="hand")
check("  ...'no' in the window offers the other options", asking(core, "choice"), True)
check("  ...and the step shows as undone",
      next(i for i in core.state["turn"]["items"] if i["type"] == "step")["status"], "undone")
core.answer("none")
finished(core, 2)

# ---- risky: always asks, worded as permission --------------------------------
core, tts, actions = make()
core.say("send it")
check("sending always asks", asking(core, "yes_no"), True)
card = core.state["question"]["card"]
check("  ...as a risky permission card", (card["risky"], card["allow"], card["deny"]), (True, "Allow", "Don't allow"))
core.answer("no", source="speech")
check("  ...'Don't allow' (Cowork was sure): it stops, no other options", finished(core, 1) and core.state["question"], None)
last = core.state["history"][-1]["items"]
check("  ...the card says how it was answered", next(i for i in last if i["type"] == "permission")["by"], "by voice")
check("  ...nothing sent", actions.done, [])
check("  ...and nothing was done", finished(core, 1) and actions.done, [])

# ---- "write an email": one new email, by any wording -----------------------------
core, tts, actions = make()
core.say("write an email")
finished(core, 1)
check("'write an email' starts one new email (a command, not typing)", [c for c, _ in actions.done], ["new_email"])

# ---- test clips -----------------------------------------------------------------
clips = load_clips()
if not (config.ROOT / "datasets" / "torgo").exists():
    print("SKIP  TORGO test clips: datasets/torgo is not on this Mac (it is not in the repo; see README.md)")
else:
    check("TORGO test clips found (4 demo + the rest)", len(clips), len(CLIP_IDS) + 4)
    check("  ...the demo four first, in order", [c["label"] for c in clips[:4]],
          ["Understood directly", "Needs \u201cDid you mean\u201d", "Replay: now instant", "Conference: new sentence"])
    from tts.lines import to_prepare  # noqa: E402
    check("  ...clip 4's sentence is made whole at startup (instant in Conference mode)",
          "Grandfather likes to be modern in his language." in to_prepare(BASE, []), True)
    check("  ...the replay is the same recording", clips[1]["recording"] == clips[2]["recording"], True)
    check("  ...credit names the dataset", "TORGO" in CREDIT and "Rudzicz" in CREDIT, True)
    core, tts, actions = make()
    core.set_clips(clips, CREDIT)
    check("clips are listed on screen (no file paths)", "path" in core.state["clips"][0], False)
    check("unknown clip refused", core.play_clip("nope", play_sound=False), "No such clip.")
    up = next(c for c in clips if c["id"] == "M04_2_0015")
    check("a clip plays into the pipeline", core.play_clip("M04_2_0015", play_sound=False), "")
    check("  ...as a 'test clip' request with what WhisperKit heard",
          wait_for(lambda: core.state["turn"] is not None) and
          (core.state["turn"]["source"], core.state["turn"]["heard"]), ("test clip", up["heard"]))

# ---- web endpoints ------------------------------------------------------------------
core, tts, actions = make()
core.set_clips(clips, CREDIT)
from ui.server import create_server  # noqa: E402
import uvicorn  # noqa: E402
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(create_server(core), host="127.0.0.1", port=port, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
wait_for(lambda: server.started, 5)
base = f"http://127.0.0.1:{port}"


def get(path):
    with urllib.request.urlopen(base + path, timeout=3) as r:
        return r.status, r.read()


def post(path, body):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=3) as r:
        return json.loads(r.read())


status, page = get("/")
check("the Cowork page is served", status == 200 and b"<title>Cowork</title>" in page, True)
check("  ...loads nothing from the internet", b"http://" in page or b"https://" in page, False)
check("  ...never says 'Claude'", b"claude" in page.lower(), False)
check("the earlier page is still at /classic", get("/classic")[0], 200)
check("no camera: no frame", get("/api/camera.jpg")[0], 204)
if clips:   # needs the TORGO recordings (not in the repo)
    check("clip endpoint", post("/api/clip", {"id": "M04_2_0034"})["ok"], True)
else:
    check("clip endpoint: without the recordings it says so", post("/api/clip", {"id": "M04_2_0034"})["ok"], False)
for _ in range(6):   # finish the clip's request, answering whatever it asks
    if finished(core, 1) and core.state["question"] is None:
        break
    if core.state["question"] is not None:
        core.answer("none" if core.state["question"]["kind"] == "choice" else "yes")
        time.sleep(0.2)
check("new task clears the screen", post("/api/new", {})["ok"] and core.state["history"], [])
server.should_exit = True

# ---- "open my latest email"; "read" with speaking off opens it instead ----------------------------
from repair import repair as brain  # noqa: E402
for wording_ in ("open my latest email", "open the newest email", "show my last email"):
    check(f"'{wording_}' -> open_latest_email", brain(wording_, BASE, FakeLLM(), None).choice, "open_latest_email")
core, tts, actions = make()
core.set_voice(False)
core.say("read out my latest email")
if asking(core, "yes_no"):
    core.answer("yes")
wait_for(lambda: actions.done)
check("speaking off: 'read my latest email' opens it instead", [c for c, _ in actions.done][:1], ["open_latest_email"])
wait_for(lambda: (core.state["notice"] or {}).get("text") == "Speaking is off, opened it instead.")
check("  ...with a short note", (core.state["notice"] or {}).get("text"), "Speaking is off, opened it instead.")
turn = core.state["turn"] or (core.state["history"] or [{}])[-1]
check("  ...the step says what was really done",
      [i["title"] for i in turn.get("items", []) if i["type"] == "step"][:1], ["Opening your latest email"])
core, tts, actions = make()   # make() turns speaking on: then it is read aloud as before
core.say("read out my latest email")
if asking(core, "yes_no"):
    core.answer("yes")
wait_for(lambda: actions.done)
check("speaking on: it is read (not opened)", [c for c, _ in actions.done][:1], ["read_latest_email"])

# ---- a working profile made before a command existed gets it (learning kept) ----------------------
import json as _json  # noqa: E402
from repair.store import open_profile as _open  # noqa: E402
old = copy.deepcopy(BASE)
old["commands"] = [c for c in old["commands"] if c["name"] != "open_latest_email"]
old["examples"].append({"heard": "my own words", "meant": BASE["phrases"][0], "args": {}})
folder = Path(tempfile.mkdtemp())
(folder / "work.json").write_text(_json.dumps(old))
upgraded = _open(folder / "work.json", config.ROOT / "profiles" / "cowork_base.json")
check("an older working profile gets the new command", any(c["name"] == "open_latest_email" for c in upgraded["commands"]), True)
check("  ...and keeps what it learned", upgraded["examples"][-1]["heard"], "my own words")

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
