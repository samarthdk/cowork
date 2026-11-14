"""Checks for the Cowork screen's core: the conversation (requests, step cards,
permission cards), wording, test clips, the new Mac actions' helpers and the
web endpoints. Fakes only: no models, microphone, camera, browser or real Mac
actions. Run:
    .venv/bin/python tests/test_cowork.py
"""

import json
import socket
import sys
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
    return core, tts, actions


def asking(core, kind=None):
    return wait_for(lambda: core.state["question"] is not None
                    and (kind is None or core.state["question"]["kind"] == kind))


def finished(core, n):
    return wait_for(lambda: len(core.state["history"]) >= n and core.state["status"] == "ready", 8)


# ---- the profile ----------------------------------------------------------
names = [c["name"] for c in BASE["commands"]]
check("Cowork profile has the new actions", all(n in names for n in (
    "open_app", "open_website", "search_web", "compose_email", "send_email", "read_latest_email",
    "type_text", "close_app", "scroll_down", "scroll_up")), True)
check("  ...sending is risky, writing is not",
      {c["name"]: c["safety"] for c in BASE["commands"] if c["name"] in ("send_email", "compose_email")},
      {"send_email": "risky", "compose_email": "safe"})
check("  ...flaky actions are left out (click, reply_email)", "click" in names or "reply_email" in names, False)

# ---- wording ----------------------------------------------------------------
check("request wording: command", wording.request_text("open_app", {"app": "Mail"}, BASE), "Open Mail")
check("request wording: missing detail", wording.request_text("open_app", {}, BASE), "Open an app")
check("request wording: phrase", wording.request_text(BASE["phrases"][0], {}, BASE), BASE["phrases"][0])
check("from the engine's description", wording.from_meaning("search_web (query: cats, dogs)", BASE),
      "Search Google for “cats, dogs”")
check("step title", wording.step_title("Opening Mail."), "Opening Mail")

# ---- helpers of the real actions (no Mac touched) ---------------------------
check("known website", site_url("youtube"), "https://www.youtube.com")
check("unknown website", site_url("my bank"), None)
check("search address", search_url("weather in boston"), "https://www.google.com/search?q=weather+in+boston")
check("contact name to address", recipient("anna", {"Anna": "anna@example.com"}), "anna@example.com")
check("an address stays", recipient("x@y.org", {"Anna": "a@b"}), "x@y.org")

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
check("  ...'Don't allow' then offers other options", asking(core, "choice"), True)
check("  ...the card says how it was answered",
      next(i for i in core.state["turn"]["items"] if i["type"] == "permission")["by"], "by voice")
core.answer("none")
check("  ...and nothing was done", finished(core, 1) and actions.done, [])

# ---- email card shows the fields ----------------------------------------------
core, tts, actions = make()
core.say("email anna saying lunch at noon")
asking(core, "yes_no")
email = core.state["question"]["card"]["email"]
check("email card shows to / message", (email["to"], email["body"]), ("Anna", "lunch at noon"))

# ---- test clips -----------------------------------------------------------------
clips = load_clips()
check("TORGO test clips found", len(clips), len(CLIP_IDS))
check("  ...credit names the dataset", "TORGO" in CREDIT and "Rudzicz" in CREDIT, True)
core, tts, actions = make()
core.set_clips(clips, CREDIT)
check("clips are listed on screen (no file paths)", "path" in core.state["clips"][0], False)
check("unknown clip refused", core.play_clip("nope", play_sound=False), "No such clip.")
check("a clip plays into the pipeline", core.play_clip("M04_2_0015", play_sound=False), "")
check("  ...as a 'test clip' request with what WhisperKit heard",
      wait_for(lambda: core.state["turn"] is not None) and
      (core.state["turn"]["source"], core.state["turn"]["heard"]), ("test clip", clips[0]["heard"]))

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
check("clip endpoint", post("/api/clip", {"id": "M04_2_0034"})["ok"], True)
check("new task clears the screen", finished(core, 1) and core.state["question"] is None and
      (core.answer("yes") or True) and post("/api/new", {})["ok"] and wait_for(lambda: core.state["history"] == [] or core.busy), True)
server.should_exit = True

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
