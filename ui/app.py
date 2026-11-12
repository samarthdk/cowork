"""The app's core, with no web code: runs the conversation and keeps the
"state" the screen shows. The web server (ui/server.py) only passes inputs
in and the state out, so this can be tested on its own with the fakes.

    core = App(profile, llm, tts, asr=..., actions=...)
    core.subscribe(lambda message: ...)   # every change, as a small dict
    core.say("aim thur stee")             # something the person said (typed)
    core.answer("yes")                    # a click on the screen
    core.signal("RIGHT_UP")               # a hand signal (from the camera)
    core.heard_audio(path)                # a recorded request from the microphone

One conversation runs at a time, on its own thread. When it asks a question,
answers can come from anywhere (screen, keyboard, speech, hand signals); they
all become the same Answer (choose/answers.py). When unsure, select nothing:
an input that does not clearly answer the open question is ignored.
"""

import copy
import json
import queue
import threading
import time

import config
from choose.answers import Answer, from_signal, from_text, hint
from engine.session import Session

QUESTION_WAIT_S = 60.0     # an unanswered question closes after this (nothing chosen; silence is not a "no")
FEED_MAX = 30              # items kept in the learning feed
METRICS_EVERY_S = 5.0      # memory/offline checks (gentle on the fanless Air)
LEVEL_EVERY_S = 0.15       # microphone level updates for the screen

FEED_KINDS = {"learned", "fast_path", "undone", "cancelled", "forgot", "new_phrase", "new_words",
              "sound_check", "relisten"}


class _Voice:
    """Wraps the voice so the microphone is muted while the Mac speaks."""
    def __init__(self, tts, app):
        self.tts, self.app = tts, app

    def speak(self, text):
        self.app._set(status="speaking", speaking=text)
        if self.app.listener:
            self.app.listener.mute()
        try:
            self.tts.speak(text)
        finally:
            if self.app.listener:
                self.app.listener.unmute()
            self.app._set(speaking=None)

    def stop(self):
        stop = getattr(self.tts, "stop", None)
        if stop:
            stop()


class App:
    def __init__(self, profile, llm, tts, asr=None, actions=None, profile_path=None,
                 backends=None, warm=False, question_wait_s=QUESTION_WAIT_S):
        self.asr, self.listener = asr, None
        self.question_wait_s = question_wait_s
        self.session = Session(profile, llm, _Voice(tts, self), actions=actions, profile_path=profile_path,
                               on_event=self._on_event, warm=warm)
        self._lock = threading.RLock()
        self._subscribers = []
        self._requests: queue.Queue = queue.Queue()   # (kind, payload)
        self._answers: queue.Queue = queue.Queue()    # (question id, Answer)
        self._question = None                         # the open Question object
        self._qid = 0
        self._last_level = 0.0
        self.state = {
            "status": "ready",          # ready, thinking, asking, speaking, listening, hearing
            "backends": backends or {},
            "mic": {"on": False, "listening": False, "problem": "", "level": None, "noise": None},
            "camera": {"on": False, "note": "Camera not connected yet."},
            "hold": None,               # hand hold-to-confirm progress: {"signal", "progress" 0..1}
            "turn": None,               # the request being handled (see _on_event)
            "question": None,
            "speaking": None,
            "feed": [],
            "metrics": {"memory": None, "system": None, "offline": None, "llm_tps": None},
            "notice": None,             # the last problem, in plain words
            "counts": self._counts(),
        }
        self._worker = threading.Thread(target=self._work, daemon=True, name="conversation")
        self._worker.start()

    # ---- state ------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state, default=str))

    def subscribe(self, callback) -> None:
        """callback(message) for every change. message: {"type": "state"|"event"|"level", ...}."""
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def _publish(self, message: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(message)
            except Exception:
                pass   # a closed browser tab must never stop the conversation

    def _set(self, event=None, **changes) -> None:
        with self._lock:
            self.state.update(changes)
        self._publish({"type": "event" if event else "state", "event": event, "state": self.snapshot()})

    def _notice(self, text: str) -> None:
        self._set(event={"kind": "notice", "text": text}, notice={"text": text, "time": time.time()})

    def _counts(self) -> dict:
        profile = self.session.profile
        return {"phrases": len(profile["phrases"]), "examples": len(profile.get("examples", []))}

    # ---- inputs (any thread) ------------------------------------------------

    @property
    def busy(self) -> bool:
        return self.state["turn"] is not None and self.state["status"] != "ready"

    def say(self, text: str, source: str = "keyboard") -> str:
        """Something typed (or said) by the person. If a question is open and this
        answers it, it is the answer; otherwise it is a new request.
        Returns what was done with it: "answer", "request", "undo" or "ignored"."""
        text = (text or "").strip()
        if not text:
            return "ignored"
        question = self._question
        if question is not None:
            answer = from_text(text, question, source=source)
            if answer is None:
                self._notice(f"'{text}' doesn't answer the question on screen.")
                return "ignored"
            self._give(answer)
            return "answer"
        lowered = text.lower()
        if lowered in ("undo", "undo last"):
            self.undo()
            return "undo"
        if from_text(lowered, type("Q", (), {"kind": "yes_no"})()) is not None:
            self._notice(f"'{text}' is an answer, but nothing is being asked right now.")
            return "ignored"
        self._requests.put(("hear", {"text": text, "source": source, "rehear": None, "asr_ms": None}))
        return "request"

    def answer(self, kind: str, value=None, source: str = "screen") -> bool:
        """A direct answer (a button on the screen): kind yes/no/pick/none/text."""
        if self._question is None or kind not in ("yes", "no", "pick", "none", "text"):
            return False
        if kind == "pick":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return False
            if not 0 <= value < len(self._question.options):
                return False
        self._give(Answer(kind, value, source))
        return True

    def signal(self, name: str, source: str = "hand") -> bool:
        """A hand (or head) signal: RIGHT, LEFT, RIGHT_UP, ... Its meaning depends
        on the question. With no question open, a raised hand means 'listen now'."""
        question = self._question
        if question is None:
            if name in ("RIGHT", "LEFT") and self.listener and not self.busy:
                self.listen_now()
                return True
            return False
        answer = from_signal(name, question, source=source)
        if answer is None:
            return False
        self._give(answer)
        return True

    def hold(self, signal: str | None, progress: float) -> None:
        """Hand hold-to-confirm progress, for the ring on the screen."""
        self._set(hold={"signal": signal, "progress": round(progress, 2)} if signal else None)

    def undo(self) -> None:
        self._requests.put(("undo", None))

    def _give(self, answer: Answer) -> None:
        self._answers.put((self._qid, answer))

    # ---- microphone ----------------------------------------------------

    def attach_listener(self, listener) -> None:
        """Use a started asr.mic.Listener (made with on_request=app.heard_audio)."""
        self.listener = listener
        mic = dict(self.state["mic"], on=listener is not None and listener.running,
                   problem=getattr(listener, "problem", ""))
        self._set(mic=mic)

    def listen_now(self) -> bool:
        if not self.listener or not self.listener.running:
            self._notice("The microphone is off. Start the app with --mic to use it.")
            return False
        self.listener.trigger()
        self._set(mic=dict(self.state["mic"], listening=True))
        return True

    def mic_level(self, db: float, noise_db: float, speaking: bool) -> None:
        now = time.monotonic()
        if now - self._last_level < LEVEL_EVERY_S:
            return
        self._last_level = now
        listening = bool(self.listener and self.listener.listening)
        with self._lock:
            self.state["mic"].update(level=round(db), noise=round(noise_db), listening=listening)
        self._publish({"type": "level", "level": round(db), "noise": round(noise_db),
                       "speaking": speaking, "listening": listening})

    def mic_timeout(self) -> None:
        self._set(mic=dict(self.state["mic"], listening=False))

    def heard_audio(self, path: str, segment=None) -> None:
        """A finished request from the microphone (called on the microphone's thread)."""
        self._set(status="hearing", mic=dict(self.state["mic"], listening=False))
        try:
            transcript = self.asr.transcribe(path)
        except Exception as error:
            self._notice(f"Speech recognition failed: {error}")
            self._set(status="ready" if self._question is None else "asking")
            return
        text = transcript.text
        if not text:
            self._notice("Didn't catch anything. Try again." + (f" ({transcript.problem})" if transcript.problem else ""))
            self._set(status="ready" if self._question is None else "asking")
            return
        if self._question is not None:
            answer = from_text(text, self._question, source="speech")
            if answer is None:
                self._notice(f"Heard '{text}', which doesn't answer the question.")
            else:
                self._give(answer)
            self._set(status="asking")
            return
        asr = self.asr

        def rehear(hint_text):   # listen to the same recording again, with a hint
            return asr.transcribe(path, prompt=hint_text)

        self._requests.put(("hear", {"text": text, "source": "microphone", "rehear": rehear,
                                     "asr_ms": round(transcript.latency_ms)}))

    # ---- the conversation thread ------------------------------------------

    def _work(self) -> None:
        while True:
            kind, payload = self._requests.get()
            if kind == "stop":
                return
            try:
                if kind == "undo":
                    self.session.undo()
                else:
                    self._run_turn(payload)
            except Exception as error:   # never crash in front of judges
                self._question = None
                self._notice(f"Something went wrong: {type(error).__name__}: {error}")
            self._set(status="ready", question=None, hold=None, counts=self._counts())

    def _run_turn(self, request: dict) -> None:
        with self._lock:
            self.state["turn"] = {"heard": request["text"], "source": request["source"],
                                  "asr_ms": request["asr_ms"], "meaning": None, "confidence": None,
                                  "how": None, "reasons": [], "steps": [], "result": None}
            if request["asr_ms"] is not None:
                self.state["turn"]["steps"].append({"name": "Speech recognition", "ms": request["asr_ms"]})
            self.state["status"] = "thinking"
        flow = self.session.hear(request["text"], source=request["source"], rehear=request["rehear"])
        pending = None
        try:
            question = next(flow)
            while True:
                question = flow.send(self._ask(question))
        except StopIteration as finished:
            pending = finished.value
        if pending:   # a sentence said during the correction window: the next request
            self._requests.put(("hear", {"text": pending, "source": request["source"], "rehear": None,
                                         "asr_ms": None}))

    def _ask(self, question):
        """Show a question and wait for an answer from any input."""
        while not self._answers.empty():   # answers given before this question was asked don't count
            self._answers.get_nowait()
        self._qid += 1
        qid = self._qid
        wait = question.seconds or self.question_wait_s
        shown = {"id": qid, "kind": question.kind, "stage": question.stage, "text": question.text,
                 "options": list(question.options), "seconds": question.seconds, "data": question.data,
                 "hint": hint(question), "closes_at": time.time() + wait}
        self._question = question
        self._set(event={"kind": "question", "stage": question.stage}, status="asking", question=shown)
        if self.listener and self.listener.running and question.kind in ("yes_no", "choice", "window"):
            self.listener.trigger(wait_s=wait)   # a spoken "yes"/"no"/"two" can answer
        deadline = time.monotonic() + wait
        answer = None
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                given_for, answer = self._answers.get(timeout=left)
            except queue.Empty:
                answer = None
                break
            if given_for == qid:
                break
            answer = None
        self._question = None
        if self.listener:
            self.listener.cancel()
        self._set(event={"kind": "answered", "answer": answer.__dict__ if answer else None},
                  status="thinking", question=None, hold=None,
                  mic=dict(self.state["mic"], listening=False))
        return answer

    def stop(self) -> None:
        self._requests.put(("stop", None))

    # ---- what the conversation reports -------------------------------------

    def _on_event(self, event: dict) -> None:
        kind = event["kind"]
        with self._lock:
            turn = self.state["turn"]
            steps = turn["steps"] if turn else []
            if kind == "fast_path" and turn:
                turn.update(meaning=event["meaning"], confidence="high", how="fast_path",
                            similarity=round(event["similarity"]), example=event["example_heard"],
                            exact_phrase=event["exact_phrase"])
                steps.append({"name": "Fast path (no model)", "ms": round(event["latency_ms"], 1)})
            elif kind == "brain" and turn:
                intent = event["intent"]
                stats = intent.get("stats", {})
                turn.update(meaning=event["meaning"], confidence=intent["confidence"], how="brain",
                            reasons=list(intent.get("problems", [])), evidence=stats.get("evidence"),
                            tokens=stats.get("tokens"), tokens_per_s=stats.get("tokens_per_s"))
                steps.append({"name": "Brain (Bonsai)", "ms": round(event["took_ms"])})
                if stats.get("tokens_per_s"):
                    self.state["metrics"]["llm_tps"] = stats["tokens_per_s"]
            elif kind == "sound_check" and turn:
                turn["sound_check"] = {k: event.get(k) for k in ("confirmed", "heard_again", "match", "logprob")}
                if event["confirmed"]:
                    turn.update(confidence="high", how="sound")
                steps.append({"name": "Listened again", "ms": event.get("took_ms")})
            elif kind == "relisten_start":
                self.state["status"] = "listening again"
            elif kind == "relisten" and event.get("took_ms") is not None:
                steps.append({"name": "Looked through phrases by sound", "ms": event["took_ms"]})
            elif kind == "second_opinion":
                steps.append({"name": "Second opinion", "ms": round(event["took_ms"])})
            elif kind == "guess_ready":
                steps.append({"name": f"Guess {event['attempt']}", "ms": round(event["took_ms"])})
            elif kind == "spoke":
                steps.append({"name": "Voice", "ms": round(event["took_ms"])})
            elif kind in ("speak", "act") and turn:
                turn["result"] = {"kind": kind, "text": event.get("text") or event.get("say"),
                                  "command": event.get("command"), "args": event.get("args")}
            elif kind == "acted" and turn and turn.get("result"):
                turn["result"].update(ok=event["ok"], detail=event["detail"], problem=event["problem"])
            elif kind == "learned" and turn:
                turn["final"] = event["meaning"]   # what the person confirmed or picked
            elif kind == "cancelled" and turn:
                turn.update(result=None, final=None)
            if kind in FEED_KINDS and not (kind in ("sound_check", "relisten") and not event.get("meaning")):
                self.state["feed"].insert(0, {**event, "time": time.time()})
                del self.state["feed"][FEED_MAX:]
            if kind == "profile_changed":
                self.state["counts"] = self._counts()
            if kind in ("problem", "guess_failed") or (kind == "acted" and not event["ok"]):
                text = event.get("text") or event.get("problem") or "Something went wrong."
                self.state["notice"] = {"text": text, "time": time.time()}
            if kind in ("nothing_chosen", "done"):
                self.state["status"] = "ready"
        self._publish({"type": "event", "event": copy.deepcopy(event), "state": self.snapshot()})

    # ---- metrics -----------------------------------------------------------

    def start_metrics(self, every_s: float = METRICS_EVERY_S) -> None:
        """Memory and offline status in the background (never sends anything anywhere)."""
        def run():
            import sysinfo
            while True:
                try:
                    metrics = dict(self.state["metrics"], memory=sysinfo.memory(),
                                   system=sysinfo.system_memory(), offline=sysinfo.offline_status(),
                                   budget_mb=round(config.MODEL_MEMORY_BUDGET_GB * 1024))
                    self._set(metrics=metrics)
                except Exception as error:
                    self._set(metrics=dict(self.state["metrics"], problem=str(error)))
                time.sleep(every_s)

        threading.Thread(target=run, daemon=True, name="metrics").start()
