"""Checks the app's core (ui/app.py) and its web server (ui/server.py) with the
fakes only: no LM Studio, WhisperKit, microphone, camera or browser needed.
Run:
    .venv/bin/python tests/test_app.py
"""

import copy
import json
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from asr.base import Transcript  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.profile import load_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from actions.fake import FakeActions  # noqa: E402
from ui.app import App  # noqa: E402

BASE = load_profile(config.BASE_PROFILE_PATH)
BASE["examples"] = []
P = BASE["phrases"]
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


def wait_for(condition, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


class Model:
    """Always answers `choice` with `confidence` (a second opinion: `second`)."""
    def __init__(self, choice="P1", confidence="low", second="P3", fail=False):
        self.choice, self.confidence, self.second, self.fail = choice, confidence, second, fail

    def complete(self, messages, schema, purpose="intent", temperature=0.0):
        if self.fail:
            raise RuntimeError("LM Studio is not running")
        again = "NOT" in messages[-1]["content"]
        return LLMReply(json.dumps({"choice": self.second if again else self.choice,
                                    "confidence": self.confidence}), 850, 11, 19.5)


def make(model=None, actions=None, wait=60.0):
    tts = FakeTTS(quiet=True)
    core = App(copy.deepcopy(BASE), model or Model(), tts, actions=actions,
               backends={"Brain": "fake"}, question_wait_s=wait)
    messages = []
    core.subscribe(messages.append)
    return core, tts, messages


def question(core, stage=None):
    return wait_for(lambda: core.state["question"] is not None
                    and (stage is None or core.state["question"]["stage"] == stage))


def idle(core):
    return wait_for(lambda: core.state["question"] is None and core.state["status"] == "ready")


print("-- A request, answered on the screen --")
core, tts, messages = make()
check("typed text becomes a request", core.say("aim thur stee"), "request")
check("'Did you mean?' is shown", question(core, "did_you_mean"), True)
q = core.snapshot()["question"]
check("  ...with its text and how to answer", (q["kind"], q["text"].startswith("Did you mean"), "RIGHT" in q["hint"]),
      ("yes_no", True, True))
turn = core.snapshot()["turn"]
check("the screen knows what was heard and understood",
      (turn["heard"], turn["meaning"], turn["confidence"], turn["how"]), ("aim thur stee", P[0], "low", "brain"))
check("  ...and the Brain's time and tokens/second", (turn["steps"][0]["name"], turn["tokens_per_s"]), ("Brain (Bonsai)", 19.5))
check("yes on the screen", core.answer("yes"), True)
check("then the correction window", question(core, "correction"), True)
check("  ...it was spoken", tts.said, [P[0]])
check("  ...and learned (feed)", [f["kind"] for f in core.state["feed"]][:1], ["learned"])
check("keep it", core.answer("yes"), True)
check("back to ready", idle(core), True)
check("  ...the counts show 1 learned example", core.state["counts"]["examples"], 1)
check("every change was sent to the screen", len(messages) > 5 and all("state" in m for m in messages), True)
check("the state is plain JSON", isinstance(json.dumps(core.snapshot()), str), True)

print("-- Same answers from any input --")
core, tts, _ = make()
core.say("aim thur stee")
question(core, "did_you_mean")
check("typed 'no' answers the question", core.say("no"), "answer")
check("the 2x2 grid appears", question(core, "options"), True)
options = core.state["question"]["options"]
check("  ...3 options + 'Something else'", (len(options), options[-1]), (4, "Something else"))
check("a plain raised hand doesn't pick a square (ask again)", core.signal("RIGHT"), False)
check("right thumb up = top right square", core.signal("RIGHT_UP"), True)
check("  ...it was spoken", wait_for(lambda: tts.said == [options[1]]), True)
question(core, "correction")
check("left hand in the correction window = cancel", core.signal("LEFT"), True)
check("  ...the options come back", question(core, "options"), True)
check("  ...the cancel is in the feed", "cancelled" in [f["kind"] for f in core.state["feed"]], True)
check("Esc (none of these)", core.answer("none"), True)
check("nothing chosen, back to ready", idle(core), True)

print("-- When unsure, select nothing --")
core, tts, _ = make()
check("an answer with no question open is ignored", core.answer("yes"), False)
check("typed 'yes' with nothing asked is not a request", core.say("yes"), "ignored")
check("  ...and the screen says why", "nothing is being asked" in core.state["notice"]["text"], True)
core.say("aim thur stee")
question(core, "did_you_mean")
check("words that don't answer the question are ignored", core.say("banana"), "ignored")
check("  ...the question stays open", core.state["question"] is not None, True)
check("a pick on a yes/no question is refused", core.answer("pick", 0), False)
core.answer("no")
question(core, "options")
check("a square that doesn't exist is refused", core.answer("pick", 7), False)
core.answer("none")
idle(core)
core, tts, _ = make(wait=0.3)
core.say("aim thur stee")
question(core, "did_you_mean")
check("nobody answers: the question closes with nothing chosen", idle(core) and tts.said == [], True)

print("-- Requests one at a time --")
core, tts, _ = make(Model("P2", "high"))
core.say("I need to use the bathroom")
question(core, "correction")
check("a sentence typed in the correction window is the next request", core.say("aim thur stee"), "answer")
check("  ...it is handled next", wait_for(lambda: core.state["turn"]["heard"] == "aim thur stee"), True)
question(core)
core.answer("no")
question(core, "options")
core.answer("none")
idle(core)
core, tts, _ = make()
core.say("aim thur stee")
question(core, "did_you_mean")
core.answer("yes")
question(core, "correction")
core.answer("yes")
idle(core)
core.undo()
check("undo forgets the last thing learned", wait_for(lambda: core.state["counts"]["examples"] == 0), True)
check("  ...shown in the feed", core.state["feed"][0]["kind"], "undone")

print("-- Mac actions and problems --")
actions = FakeActions()
core, tts, _ = make(FakeLLM(), actions=actions)
core.say("scroll down")
wait_for(lambda: core.state["question"] is not None or actions.done)
if core.state["question"] and core.state["question"]["stage"] == "did_you_mean":
    core.answer("yes")
check("a command is carried out (fake Mac)", wait_for(lambda: actions.done == [("scroll_down", {})]), True)
check("  ...and shown as done", wait_for(lambda: (core.state["turn"]["result"] or {}).get("command") == "scroll_down"), True)
core.answer("yes")
idle(core)
core, tts, _ = make(Model(fail=True))
core.say("aim thur stee")
wait_for(lambda: core.state["question"] is not None)
check("LM Studio down: no crash, the options are still offered", core.state["question"]["stage"], "options")
core.answer("none")
check("  ...and the app is ready for the next request", idle(core), True)


print("-- Microphone (fake recording) --")


class Ear:
    def __init__(self, text):
        self.text, self.calls = text, []

    def transcribe(self, path, prompt=""):
        self.calls.append(prompt)
        if prompt:
            return Transcript(prompt, avg_logprob=-0.1)
        return Transcript(self.text, avg_logprob=-0.5, latency_ms=160)


core, tts, _ = make()
core.asr = Ear("aim thur stee")
core.heard_audio("/tmp/none.wav")
check("speech from the microphone becomes a request", wait_for(lambda: core.state["turn"] is not None), True)
check("  ...recognition time is shown", core.state["turn"]["steps"][0], {"name": "Speech recognition", "ms": 160})
check("  ...listening again confirms the guess, so it is spoken without asking", question(core, "correction"), True)
check("  ...shown as confirmed by sound", core.state["turn"]["how"], "sound")
core.asr = Ear("yes")
core.heard_audio("/tmp/none.wav")
check("a spoken 'yes' answers the open question (keep)", idle(core), True)
core.asr = Ear("")
core.heard_audio("/tmp/none.wav")
check("nothing heard: a notice, no request", ("Didn't catch" in core.state["notice"]["text"], core.state["status"]),
      (True, "ready"))
check("'listen now' without a microphone explains how to switch it on", core.listen_now(), False)

print("-- Web server --")
from ui.server import create_server  # noqa: E402
import uvicorn  # noqa: E402

core, tts, _ = make()
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(create_server(core), host="127.0.0.1", port=port, log_level="error"))
threading.Thread(target=server.run, daemon=True).start()
wait_for(lambda: server.started, 5)
base = f"http://127.0.0.1:{port}"


def call(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as reply:
        return reply.read().decode()


page = call("/")
check("the page is served", "<title>Second Voice</title>" in page, True)
check("  ...and loads nothing from the internet", ("http://" in page.replace("http://localhost", ""), "https://" in page),
      (False, False))
check("/api/state", json.loads(call("/api/state"))["status"], "ready")
check("/api/say", json.loads(call("/api/say", {"text": "aim thur stee"})), {"done": "request"})
question(core, "did_you_mean")
check("/api/answer", json.loads(call("/api/answer", {"kind": "no"})), {"ok": True})
question(core, "options")
check("/api/signal (left thumb up = first square)", json.loads(call("/api/signal", {"signal": "LEFT_UP"})), {"ok": True})
question(core, "correction")
call("/api/answer", {"kind": "yes"})
idle(core)
with urllib.request.urlopen(base + "/api/stream", timeout=5) as stream:
    first = stream.readline().decode()
check("/api/stream sends the state at once", first.startswith("data: ") and '"status"' in first, True)
server.should_exit = True

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
