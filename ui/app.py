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
import subprocess
import threading
import time

import config
from choose.answers import Answer, from_signal, from_text, hint
from engine.session import Session
from ui import wording
from ui.compound import split as split_compound
from ui.spoken import email_address

QUESTION_WAIT_S = 60.0     # an unanswered question closes after this (nothing chosen; silence is not a "no")
FEED_MAX = 30              # items kept in the learning feed
METRICS_EVERY_S = 5.0      # memory/offline checks (gentle on the fanless Air)
LEVEL_EVERY_S = 0.15       # microphone level updates for the screen

HINT_MIN_SPEECH_S = 0.3   # a request with at least this much speech gets a second, hinted try
HINT_MIN_LOGPROB = -0.5   # ...whose answer is kept only if WhisperKit was this sure. Measured on 16 TORGO
                          # clips + 2 real requests: invented answers scored -0.62 or lower, real ones -0.37 or higher
HISTORY_MAX = 20           # finished requests kept on screen
VOICE_WAIT_S = 30.0        # longest wait for queued speech before speaking something new
HANDS_FREE_WAIT_S = 30.0   # mini window: listen this long for the next request, then listen again
MINI_AFTER = ("open_app", "new_email", "new_document", "new_tab", "open_latest_email")   # these bring another app forward
OPENS = {"new_email": "Mail", "new_document": "Microsoft Word", "open_latest_email": "Mail"}   # the app it works in
KEY_RETURN, KEY_ENTER, KEY_ESC, KEY_Y, KEY_N = 36, 76, 53, 16, 45
KEY_DIGITS = {18: 0, 19: 1, 20: 2, 21: 3}                             # 1-4 above the letters

FEED_KINDS = {"learned", "fast_path", "undone", "cancelled", "forgot", "new_phrase", "new_words",
              "sound_check", "relisten"}


from tts.lines import MEETING as QUICK_PHRASES   # Conference mode's quick phrases (made in advance)

SAID_MAX = 30               # Conference mode: sentences kept in the transcript
NUMBER_WORDS = {"one": 0, "1": 0, "two": 1, "2": 1, "three": 2, "3": 2, "four": 3, "4": 3}   # a spoken quick phrase
                                                                                        # is confirmed first
CHIME = "/System/Library/Sounds/Tink.aiff"   # one short soft sound (not a voice)
CHIME_VOLUME = "0.35"


class _Voice:
    """Wraps the voice (TTSKit only) so the microphone is paused while anything plays.

    A command's spoken step ("Opening Mail.") is queued and spoken while the
    action runs, so a slow voice never holds the action up. Everything else
    (phrases said for other people, an email read aloud) is spoken in order,
    waiting for the queue first. A line is spoken only if TTSKit has it ready
    (tts/real.py); otherwise it stays silent and the text is on screen as usual."""
    def __init__(self, tts, app):
        self.tts, self.app = tts, app
        self.next_in_background = False
        self.enabled = False   # "Speaking" in Settings: off by default in Computer mode (always on in Conference mode)
        self.conference = False   # Conference mode: a confirmed sentence is always said (waits for TTSKit)
        self._queue: queue.Queue = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        threading.Thread(target=self._work, daemon=True, name="voice").start()

    def speak(self, text, wait=False):
        """Say a line (a sentence or a list of parts). Returns True if it was spoken
        (None when it was queued to be said while an action runs)."""
        if self.next_in_background:
            self.next_in_background = False
            self._idle.clear()
            self._queue.put(text)
            return None
        self._idle.wait(VOICE_WAIT_S)
        return self._say(text, wait=wait)

    def later(self, line):
        """Say a line in the background (queued in order); returns at once."""
        self._idle.clear()
        self._queue.put(line)

    def prefetch(self, line):
        """Start making a line now (TTSKit), so it can be said the moment it is needed."""
        prefetch = getattr(self.tts, "prefetch", None)
        if prefetch is not None and self.enabled:
            try:
                prefetch(line)
            except Exception:
                pass

    def ready(self, line) -> bool:
        ready = getattr(self.tts, "ready", None)
        return bool(ready(line)) if ready is not None else True

    def chime(self):
        """One short soft chime (the microphone is paused meanwhile)."""
        if self.app.listener:
            self.app.listener.mute()
        try:
            subprocess.run(["afplay", "-v", CHIME_VOLUME, CHIME], timeout=3, capture_output=True)
        except Exception:
            pass
        finally:
            if self.app.listener:
                self.app.listener.unmute()

    def _work(self):
        while True:
            text = self._queue.get()
            try:
                self._say(text)
            except Exception as error:
                self.app._notice(f"Couldn't speak: {error}")
            if self._queue.empty():
                self._idle.set()

    def _say(self, line, wait=False):
        from tts.lines import text_of
        text = text_of(line)
        if not self.enabled:
            self.app._mark_voice(text, "off")   # the screen says it was not said aloud (never a silent "Said")
            return False
        ready = self.ready(line)
        self.app._mark_voice(text, "TTSKit" if ready else "preparing voice…")
        conference = self.conference
        if conference:   # said to the room: always, however long TTSKit takes ("Preparing voice…" meanwhile)
            self.app._said_status(text, "speaking" if ready else "preparing")
        self.app._set(status="speaking", speaking=text)
        if self.app.listener:
            self.app.listener.mute()   # never hear itself (also while waiting for TTSKit)
        spoken = False
        try:
            if conference:
                spoken = self.tts.speak(line, wait=True, on_sound=lambda: self.app._said_status(text, "speaking")) is not False
            else:
                spoken = self.tts.speak(line, wait=wait) is not False
        finally:
            if self.app.listener:
                self.app.listener.unmute()
            self.app._set(speaking=None)
        self.app._mark_voice(text, "TTSKit" if spoken else ("stopped" if conference else "not spoken (voice not ready)"))
        if conference:
            self.app._said_status(text, "said" if spoken else "stopped")
        return spoken

    def wait(self, timeout=VOICE_WAIT_S) -> bool:
        return self._idle.wait(timeout)

    def stop(self):
        from tts.lines import text_of
        while not self._queue.empty():
            try:
                dropped = self._queue.get_nowait()
            except queue.Empty:
                break
            self.app._said_status(text_of(dropped), "stopped")
        stop = getattr(self.tts, "stop", None)
        if stop:
            stop()


NEEDS_TARGET = ("type_text", "press_return")   # these act on the app being typed into
SOMETHING_ELSE_APP = "Something else"
SCREEN_CHOICES = {   # what can be chosen on a screen, and what each choice does
    "start screen": [("Blank document", "new_document"), ("Open a recent file", "recent"),
                     ("Something else", None)],
    "inbox": [("Write a new email", "new_email"), ("Something else", None)],
}


class _Targeted:
    """Wraps the Mac actions so every action looks at the screen first and after
    (actions/screen.py), and so typing only ever lands where words belong:

      - before: the current app and what has the cursor are read again (never taken
        from the last command); "back" in an email means back a box (ui/screen_rules.py);
      - typing with nothing typeable asks first ("Word is on its start screen. Blank
        document, open a recent file, or something else?"); a search box asks too;
      - a question is answered from the Brain's own knowledge (repair/answer.py);
      - after: what is on screen now goes into Progress ("Blank document ready").
    The engine itself is unchanged by this: it just calls run()."""
    def __init__(self, actions, app):
        self._actions, self._app = actions, app

    def __getattr__(self, name):
        return getattr(self._actions, name)

    def context(self) -> dict:
        from ui.screen_rules import hide_for
        found = self._actions.context()
        screen = found.get("_screen")
        if screen is not None:
            self._app.seen_screen(screen)
            turn = self._app.state.get("turn") or {}
            found["_hide"] = hide_for(screen, turn.get("heard", ""), self._app.state["dictation"]["on"])
        return found

    def run(self, command: str, args: dict):
        from actions.base import ActionResult
        from ui.screen_rules import remap
        app, actions = self._app, self._actions
        screen = app.look()
        if remap(command, screen) != command:
            command = remap(command, screen)
            app.retitle(command)
        if command == "answer_question":
            return app.answer_question(args.get("question", ""), screen)
        if command == "read_latest_email" and not app.voice.enabled:
            # Never confirm something and then silently do nothing: with speaking off it can't be read
            # aloud, so the email is opened on screen instead, and the person is told.
            command = "open_latest_email"
            app.instead(command, "Speaking is off, opened it instead.")
        if command in NEEDS_TARGET and not app.ensure_target():
            return ActionResult(False, problem="No app was chosen to type into.")
        if command == "type_text" and not args.get("anyway"):
            screen = app.look()
            if screen is not None and screen.readable and not screen.typeable:
                how = app.ask_where_to_type(screen)
                if how == "anyway":
                    args = dict(args, anyway=True)
                elif how == "new_document":
                    made = actions.run("new_document", {})
                    app.saw(app.look())
                    if not made.ok:
                        return made
                else:
                    return ActionResult(False, problem=how or "Nothing was typed.")
        outcome = actions.run(command, args)
        app.saw(app.look())
        app._after_action(command, args, outcome, screen)
        return outcome


class App:
    def __init__(self, profile, llm, tts, asr=None, actions=None, profile_path=None,
                 backends=None, warm=False, question_wait_s=QUESTION_WAIT_S):
        self.asr, self.listener, self.camera = asr, None, None
        self.question_wait_s = question_wait_s
        self._continuous = False   # the Speak button keeps the microphone on until pressed again
        self.voice = _Voice(tts, self)
        if actions is not None and hasattr(actions, "target_name"):
            actions = _Targeted(actions, self)
        self.session = Session(profile, llm, self.voice, actions=actions, profile_path=profile_path,
                               on_event=self._on_event, warm=warm)
        self._turns = 0
        self._clips = {}
        self._lock = threading.RLock()
        self._subscribers = []
        self._requests: queue.Queue = queue.Queue()   # (kind, payload)
        self._answers: queue.Queue = queue.Queue()    # (question id, Answer)
        self._question = None                         # the open Question object
        self._then: list = []                         # requests said along with an answer ("no, open Word"), handled next
        self._drop = False                            # the person moved on: drop further questions in this request
        self._qid = 0
        self._last_level = 0.0
        self.state = {
            "status": "ready",          # ready, thinking, asking, speaking, listening, hearing
            "backends": backends or {},
            "mic": {"on": False, "listening": False, "problem": "", "level": None, "noise": None,
                    "off_reason": "",    # why the microphone is off, in a few words (shown under Speak)
                    "partial": ""},      # words heard so far while the person is still speaking
            "camera": {"connected": False, "on": False, "problem": "", "note": "Camera not connected."},
            "hold": None,               # hand hold-to-confirm progress: {"signal", "progress" 0..1}
            "turn": None,               # the request being handled (see _on_event)
            "history": [],              # finished requests, oldest first (the conversation)
            "clips": [],                # TORGO test clips (see ui/clips.py)
            "credit": "",               # where the test clips come from
            "dictation": {"on": False, "app": ""},   # type confirmed sentences into the app you were using
            "voice": {"on": False},   # "Speaking" in Settings (off by default): say steps and read replies aloud (TTSKit)
            "attention": False,       # mini window: a question couldn't be said aloud (soft chime + glow until answered)
            "mode": "computer",       # "computer": act on the Mac; "conference": speak confirmed sentences to the room
            "said": [],               # Conference mode: what the person said to the room, oldest first
            "quick_phrases": list(QUICK_PHRASES),   # Conference mode: meeting phrases (1-4, thumbs, voice)
            "screen": None,             # what is on screen in the app being worked in (actions/screen.py)
            "window": {"mode": "full", "hands_free": False},   # "mini": the small window in the corner
            "catalog": {"phrases": [], "commands": [], "apps": []},   # everything Quick Actions can do
            "ui": {"seq": 0, "action": None, "value": None, "quick": False},   # screen-only commands
            "question": None,
            "speaking": None,
            "feed": [],
            "metrics": {"memory": None, "system": None, "offline": None, "llm_tps": None},
            "notice": None,             # the last problem, in plain words
            "counts": self._counts(),
        }
        self.window_hook = None       # set by ui/window.py: window_hook("mini" | "full") switches the real windows
        self._opened: list[str] = []  # apps Cowork opened by voice, oldest first (closing the last one: full size)
        self.refresh_catalog()
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

    def refresh_catalog(self) -> None:
        """Every phrase, command and app, for Quick Actions (Command+K) and the sidebar lists."""
        from ui.wording import GENERIC
        profile = self.session.profile
        commands = [{"name": c["name"], "request": GENERIC.get(c["name"]) or c.get("request", "").split("{")[0].strip()
                     or c["name"].replace("_", " ").capitalize()} for c in profile["commands"]
                    if c["name"] != "open_app"]
        try:
            apps = list((self.session.context() or {}).get("app", []))
        except Exception:
            apps = []
        with self._lock:
            self.state["catalog"] = {"phrases": list(profile["phrases"]), "commands": commands, "apps": apps}

    def ui_command(self, text: str, spoken: bool = True) -> bool:
        """Spoken or typed screen commands ("show learned", "quick actions"), handled by the screen only.
        While Quick Actions is open, anything said filters its list. Returns True if handled."""
        from rapidfuzz import fuzz
        said = " ".join((text or "").lower().replace(".", " ").replace("!", " ").split())
        commands = {"show learned": ("open", "learned"), "show progress": ("open", "progress"),
                    "show camera": ("open", "camera"), "quick actions": ("quick", True),
                    "close quick actions": ("quick", False), "show cowork": ("window", "full"),
                    "open cowork": ("window", "full"), "stop listening": ("hands_free", False)}
        best = max(commands, key=lambda c: fuzz.ratio(said, c))
        if fuzz.ratio(said, best) >= 85:
            action, value = commands[best]
            if action == "window":
                self.set_window("full")
                return True
            if action == "hands_free":
                self.set_hands_free(False)
                return True
        elif spoken and self.state["ui"].get("quick") and said:   # only speech filters (typed/clicked requests never do)
            action, value = "filter", text.strip()
        else:
            return False
        self._ui(action, value)
        return True

    def _ui(self, action: str, value=None) -> None:
        ui = dict(self.state["ui"], seq=self.state["ui"]["seq"] + 1, action=action, value=value)
        if action == "quick":
            ui["quick"] = bool(value)
        self._set(ui=ui)

    def set_ui(self, quick: bool) -> None:
        """The page says whether Quick Actions is open (speech and thumbs then go to it)."""
        self._set(ui=dict(self.state["ui"], quick=bool(quick)))

    def _counts(self) -> dict:
        profile = self.session.profile
        return {"phrases": len(profile["phrases"]), "examples": len(profile.get("examples", []))}

    # ---- the mini window (Cowork shrinks to a corner while another app is used) ----------

    def set_window(self, mode: str) -> None:
        """"mini": the small always-on-top window in the top right corner (it never takes the keyboard
        from the app being used), listening hands-free. "full": the whole window, with the history."""
        if mode not in ("mini", "full") or mode == self.state["window"]["mode"]:
            return
        hands_free = mode == "mini" and bool(self.listener and self.listener.running)
        self._set(window={"mode": mode, "hands_free": hands_free})
        if self.window_hook is not None:
            try:
                self.window_hook(mode)
            except Exception as error:
                self._notice(f"Couldn't switch the window: {error}")
        if hands_free and not self.busy:
            self._listen_on()
        if mode == "full" and self.listener:
            self.listener.cancel()
            self._set(mic=dict(self.state["mic"], listening=False))

    def set_hands_free(self, on: bool) -> None:
        """Listen for the next request after each one (mini window). Off: "stop listening", Esc, a click."""
        on = bool(on) and bool(self.listener and self.listener.running)
        self._set(window=dict(self.state["window"], hands_free=on))
        if on:
            self._listen_on()
        elif self.listener and self._question is None:
            self.listener.cancel()
            self._set(mic=dict(self.state["mic"], listening=False, partial=""))

    def _listen_on(self) -> None:
        """Hands-free: listen for the next request (the microphone is muted while Cowork speaks)."""
        window = self.state["window"]
        if (window["mode"] == "mini" and window["hands_free"] and self.listener and self.listener.running
                and self._question is None and not self.listener.listening):
            self.listener.trigger(wait_s=HANDS_FREE_WAIT_S)
            self._set(mic=dict(self.state["mic"], listening=True))

    def mini_key(self, code: int) -> bool:
        """A key pressed in any app while the mini window shows. Returns True if Cowork used it (it then
        doesn't reach the app). Only while a question is open: Enter/Y yes, Esc/N no, 1-4 a square.
        With no question, Esc stops hands-free listening (and still reaches the app)."""
        if self.state["window"]["mode"] != "mini":
            return False
        question = self._question
        if question is None:
            if code == KEY_ESC and self.state["window"]["hands_free"]:
                self.set_hands_free(False)
            return False
        if question.kind == "yes_no":
            if code in (KEY_RETURN, KEY_ENTER, KEY_Y):
                return self.answer("yes", source="keyboard")
            if code in (KEY_ESC, KEY_N):
                return self.answer("no", source="keyboard")
        elif question.kind == "choice":
            if code in KEY_DIGITS and KEY_DIGITS[code] < len(question.options):
                return self.answer("pick", KEY_DIGITS[code], source="keyboard")
            if code == KEY_ESC:
                return self.answer("none", source="keyboard")
        return False

    def _after_action(self, command: str, args: dict, outcome, before) -> None:
        """Another app was brought forward: shrink to the corner. The last app Cowork opened was
        closed: back to full size."""
        if not outcome.ok:
            return
        if command in MINI_AFTER:
            app = args.get("app") or OPENS.get(command) or (self.state.get("screen") or {}).get("app", "")
            if app:
                if app in self._opened:
                    self._opened.remove(app)
                self._opened.append(app)
            self.set_window("mini")
        elif command in ("quit_app", "close_app"):
            closed = args.get("app") or (before.app if before is not None else "")
            last = self._opened[-1] if self._opened else None
            if closed in self._opened:
                self._opened.remove(closed)
            if closed and (closed == last or not self._opened):
                self.set_window("full")

    @staticmethod
    def spoken_question(card: dict, options: list | None = None) -> str:
        """What the mini window says aloud for a question, as one whole sentence:
        "Did you mean: Open Mail?" or "Which one did you mean? 1: Open Mail, 2: ...". """
        from tts.lines import question_line
        return question_line(dict(card, options=options if options is not None else card.get("options") or []))

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
        if self._question is not None:
            return "answer" if self._answer_words(text, source) else "ignored"
        if self.ui_command(text, spoken=source == "speech"):
            return "ui"
        number = " ".join(text.lower().strip(" .!?").replace("number ", "").split())
        if self.session.conference and number in NUMBER_WORDS:   # "two": quick phrase 2, confirmed first
            self._requests.put(("quick", {"index": NUMBER_WORDS[number], "source": source}))
            return "request"
        lowered = text.lower()
        if lowered in ("undo", "undo last"):
            self.undo()
            return "undo"
        if from_text(lowered, type("Q", (), {"kind": "yes_no"})()) is not None:
            self._notice(f"'{text}' is an answer, but nothing is being asked right now.")
            return "ignored"
        self._requests.put(("hear", {"text": text, "source": source, "rehear": None, "asr_ms": None}))
        return "request"

    def _answer_words(self, text: str, source: str) -> bool:
        """A sentence said or typed while a question is open (choose/reading.py): "No, I meant to say
        open Word" answers No and then handles "open Word"; "Yes, then open Mail" confirms and then
        opens Mail; "the second one" picks it; a clear new request drops the question. Otherwise the
        question stays open and the screen says how to answer."""
        from choose.reading import understand
        question = self._question
        if question is None:
            return False
        card = (self.state.get("question") or {}).get("card") or {}
        shown = [o.get("text", "") for o in card.get("options") or []] or list(question.options)
        reading = understand(text, question, shown=shown, body=card.get("body") or "",
                             profile=self.session.profile, source=source)
        if reading is None:
            how = {"yes_no": "Say yes or no, or say what you want instead.",
                   "choice": "Say a number (1 to 4) or an option's words, or say what you want instead.",
                   "window": "Say no to undo it."}.get(question.kind, "")
            self._notice(f"Heard \u201c{text}\u201d, which doesn't answer the question. {how}".strip())
            return False
        if reading.then:
            self._then.append((reading.then, source))
        if reading.drop:
            self._drop = True
        self._give(reading.answer)
        return True

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
        if question is None and self.state["ui"].get("quick"):
            square = {"LEFT_UP": 0, "RIGHT_UP": 1, "LEFT_DOWN": 2, "RIGHT_DOWN": 3}.get(name)
            if square is None:
                return False
            self._ui("pick", square)
            return True
        if question is None and self.session.conference:   # thumbs pick a quick phrase (hold to confirm)
            square = {"LEFT_UP": 0, "RIGHT_UP": 1, "LEFT_DOWN": 2, "RIGHT_DOWN": 3}.get(name)
            if square is not None:
                return self.quick_phrase(square)
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
        """Hand hold-to-confirm progress, for the ring on the screen. The camera reports this on
        every frame (15-30 a second), even when nothing is held: only a change is sent on
        (measured: sending every frame redrew the question card constantly, so it flickered)."""
        hold = {"signal": signal, "progress": round(round(progress * 20) / 20, 2)} if signal else None
        if hold == self.state.get("hold"):
            return
        self._set(hold=hold)

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

    def mic_off(self, reason: str) -> None:
        """The microphone was not started; say why under the Speak button."""
        self._set(mic=dict(self.state["mic"], on=False, off_reason=reason))

    def listen_toggle(self) -> bool:
        """The Speak button: start listening continuously, or stop (it does not
        time out after a pause; it keeps listening until it is pressed again).
        Returns True when it is now listening."""
        if not self.listener or not self.listener.running:
            self._notice("The microphone is off. Start the app with --mic to use it.")
            return False
        if self._continuous:
            self._continuous = False
            self.listener.set_always(False)
            self.listener.cancel()
            self._set(mic=dict(self.state["mic"], listening=False, partial=""))
            return False
        self._continuous = True
        self.listener.set_always(True)
        self._set(mic=dict(self.state["mic"], listening=True, partial=""))
        return True

    def partial_audio(self, samples) -> None:
        """Audio so far while the person is speaking (from the microphone thread):
        show the words heard so far. Only a display; one transcription at a time."""
        if self.asr is None or getattr(self, "_partial_busy", False):
            return
        self._partial_busy = True

        def run():
            import tempfile
            from pathlib import Path
            from asr.audio import write_wav
            try:
                path = Path(tempfile.gettempdir()) / "secondvoice_partial.wav"
                write_wav(path, samples)
                text = self.asr.transcribe(str(path)).text
                if self.listener and self.listener.listening:
                    self._set(mic=dict(self.state["mic"], partial=text or "…"))
            except Exception:
                pass
            finally:
                self._partial_busy = False

        threading.Thread(target=run, daemon=True, name="partial").start()

    def asr_hint(self) -> str:
        """The person's own phrases and commands, as a hint for the recognizer when
        it could not make out the words (see heard_audio)."""
        from ui.wording import GENERIC
        profile = self.session.profile
        words = list(profile["phrases"])
        for command in profile["commands"]:
            words.append(GENERIC.get(command["name"]) or command.get("request", "").split("{")[0].strip()
                         or command["name"].replace("_", " "))
        try:
            apps = (self.session.context() or {}).get("app", [])[:12]
        except Exception:
            apps = []
        words += [f"Open {app}" for app in apps]
        hint = ". ".join(w.rstrip(".") for w in words if w) + "."
        return hint[:900]

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
        self._set(mic=dict(self.state["mic"], listening=False, partial=""))
        if not self.busy:
            self._listen_on()

    def heard_audio(self, path: str, segment=None, known=None, source: str = "microphone") -> None:
        """A finished request from the microphone (called on the microphone's thread).
        `known`: a transcript already made for this recording (test clips with the
        fake speech recognizer); then it is not transcribed again or re-heard."""
        self._set(status="hearing", mic=dict(self.state["mic"], listening=False))
        hinted = False
        try:
            transcript = known if known is not None else self.asr.transcribe(path)
            speech_s = getattr(segment, "speech_s", 1.0) if segment is not None else 1.0
            if known is None and not transcript.text and speech_s >= HINT_MIN_SPEECH_S:
                # Measured on this Mac: quiet or unclear requests came back as "[Inaudible]";
                # with the person's own phrases as a hint the same recordings were understood.
                # The Brain still checks the result as usual ("Did you mean?" when unsure).
                again = self.asr.transcribe(path, prompt=self.asr_hint())
                if again.text and again.avg_logprob >= HINT_MIN_LOGPROB:
                    transcript, hinted = again, True
        except Exception as error:
            self._notice(f"Speech recognition failed: {error}")
            self._set(status="ready" if self._question is None else "asking")
            return
        self._set(mic=dict(self.state["mic"], partial=""))
        text = transcript.text
        if not text:
            self._notice("Couldn't make out the words. Try again, a little closer to the Mac." + (f" ({transcript.problem})" if transcript.problem else ""))
            self._set(status="ready" if self._question is None else "asking")
            return
        if self._question is not None:
            self._answer_words(text, "speech")
            self._set(status="asking" if self._question is not None else self.state["status"])
            return
        if self.ui_command(text):
            self._set(status="ready")
            return
        asr = self.asr

        def rehear(hint_text):   # listen to the same recording again, with a hint
            return asr.transcribe(path, prompt=hint_text)

        self._requests.put(("hear", {"text": text, "source": source,
                                     "rehear": rehear if known is None else None,
                                     "asr_ms": round(transcript.latency_ms), "hinted": hinted}))

    # ---- test clips (TORGO recordings, as if spoken) -------------------------

    def set_clips(self, clips: list[dict], credit: str) -> None:
        self._clips = {c["id"]: c for c in clips}
        shown = ("id", "said", "seconds", "label", "note", "demo")
        self._set(clips=[{k: c.get(k) for k in shown} for c in clips], credit=credit)

    def play_clip(self, clip_id: str, play_sound: bool = True) -> str:
        """Play a recording out loud and feed it in as if the person said it.
        Returns "" or why not."""
        clip = self._clips.get(clip_id)
        if clip is None:
            return "No such clip."
        if self.busy or self._question is not None:
            return "Wait until the current request is finished."

        def run():
            self._set(status="playing clip", playing=clip_id)
            if self.listener:
                self.listener.mute()   # the microphone must not hear the clip as a second request
            try:
                if play_sound:
                    import subprocess
                    subprocess.run(["afplay", clip["path"]], timeout=30, check=False)
            finally:
                if self.listener:
                    self.listener.unmute()
                self._set(playing=None)
            known = None
            if config.ASR == "fake":   # use what WhisperKit really heard in the evaluation
                from asr.base import Transcript
                known = Transcript(clip["heard"], clip["heard"], -0.3, 0.0, clip.get("seconds") or 0.0, 0.0, "recorded")
            self.heard_audio(clip["path"], known=known, source="test clip")

        threading.Thread(target=run, daemon=True, name="clip").start()
        return ""

    # ---- Conference mode (speaking for the person in a meeting) ------------------------

    def set_mode(self, mode: str) -> None:
        """"conference": a confirmed sentence is said aloud to the room (TTSKit, always), commands are
        ignored, speaking is always on. "computer": back to acting on the Mac, speaking as it was."""
        if mode not in ("computer", "conference") or mode == self.state["mode"]:
            return
        conference = mode == "conference"
        if conference:
            self._voice_before = self.state["voice"]["on"]
            self.voice.enabled = True
            self.set_window("full")
            self._set(voice={"on": True})
        self.session.conference = self.voice.conference = conference
        self._set(mode=mode)
        if not conference:
            self.set_voice(getattr(self, "_voice_before", self.state["voice"]["on"]))
        if self.camera is not None:
            self.camera.want(conference)   # thumbs pick quick phrases while in Conference mode
        if conference:   # the meeting phrases: make sure TTSKit has them (normally made at start-up)
            prepare = getattr(self.voice.tts, "prepare", None)
            if prepare is not None:
                prepare(list(QUICK_PHRASES))

    def quick_phrase(self, index: int) -> bool:
        """Conference mode: say a meeting phrase now (a key or a held thumb is a deliberate choice)."""
        if not self.session.conference or self._question is not None or not 0 <= index < len(QUICK_PHRASES):
            return False
        text = QUICK_PHRASES[index]
        self._said_add(text, quick=True)
        self.voice.later(text)
        return True

    def _quick_by_voice(self, index: int, source: str) -> None:
        """A spoken number ("two") picks a quick phrase, but it is checked first (it may be misheard)."""
        from engine.session import Question
        if not 0 <= index < len(QUICK_PHRASES):
            return
        text = QUICK_PHRASES[index]
        answer = self._ask(Question("yes_no", "quick", "Say this?", data={"phrase": text}))
        if answer is not None and answer.kind == "yes":
            self.quick_phrase(index)

    @staticmethod
    def _top_guess(card: dict) -> str:
        """The sentence most likely to be confirmed on a card (to make its audio early)."""
        if card.get("say_aloud") and card.get("body"):
            return card["body"]
        for option in card.get("options") or []:
            if option.get("kind") == "phrase":
                return option["text"]
        return ""

    def _said_add(self, text: str, quick: bool = False) -> None:
        with self._lock:
            ready = self.voice.ready(text)
            self.state["said"].append({"text": text, "status": "speaking" if ready else "preparing",
                                       "quick": quick, "time": time.time()})
            del self.state["said"][:-SAID_MAX]
        self._set()

    def _said_status(self, text: str, status: str) -> None:
        """preparing -> speaking -> said (or stopped): the newest unfinished entry with this text."""
        with self._lock:
            entry = next((e for e in reversed(self.state["said"])
                          if e["text"] == text and e["status"] in ("preparing", "speaking")), None)
            if entry is None:
                return
            entry["status"] = status
            if status == "speaking":
                entry["sound_at"] = time.time()
            turn = self.state["turn"]
            for item in reversed((turn or {}).get("items", [])):
                if item["type"] == "spoken" and item["text"] == text:
                    item["status"] = {"said": "done", "stopped": "undone"}.get(status, "running")
                    break
        self._set()

    # ---- dictation ----------------------------------------------------------

    def set_dictation(self, on: bool) -> None:
        """On: a confirmed sentence is typed into the app the person is working in (and read back)."""
        self._set(dictation={"on": bool(on), "app": self._typing_app()})

    def set_voice(self, on: bool) -> None:
        """On: Cowork speaks as it works (steps, replies read aloud), TTSKit only, one whole sentence at a time.
        Off (default in Computer mode): silent; everything is on screen."""
        on = bool(on)
        if self.state.get("mode") == "conference":   # always on in Conference mode; this is kept for afterwards
            self._voice_before = on
            if not on:
                self._notice("Speaking is always on in Conference mode. Your choice is kept for Computer mode.")
            return
        self.voice.enabled = on
        if not on:
            self.voice.stop()
        self._set(voice={"on": on})

    def _typing_app(self) -> str:
        actions = self.session.actions
        try:
            if actions is not None and hasattr(actions, "target_name"):
                return actions.target_name()
            return actions.front_app_name() if actions is not None and hasattr(actions, "front_app_name") else ""
        except Exception:
            return ""

    def watch_target(self, every_s: float = 0.5) -> None:
        """Keep the composer pill showing the app typing goes into (it follows the person)."""
        def run():
            while True:
                app = self._typing_app()
                if app != self.state["dictation"].get("app"):
                    self._set(dictation=dict(self.state["dictation"], app=app))
                time.sleep(every_s)
        threading.Thread(target=run, daemon=True, name="target").start()

    def ensure_target(self) -> bool:
        """There is an app to type into, or the person chooses one now:
        "Which app should I type into?" with the 3 most recent apps plus "Something else"."""
        actions = self.session.actions
        if actions is None or not hasattr(actions, "target_name") or actions.target_name():
            return True
        from engine.session import Question
        recent = list(actions.recent_targets(3)) if hasattr(actions, "recent_targets") else []
        answer = self._ask(Question("choice", "target", "Which app should I type into?",
                                    options=recent + [SOMETHING_ELSE_APP]))
        if answer is None or answer.kind != "pick":
            return False
        options = recent + [SOMETHING_ELSE_APP]
        name = options[answer.value]
        if name == SOMETHING_ELSE_APP:
            apps = (self.session.context() or {}).get("app", [])
            answer = self._ask(Question("detail", "target_other", "Which app?",
                                        data={"name": "app", "command": "type_text", "values": apps}))
            if answer is None or answer.kind != "text":
                return False
            typed = str(answer.value).strip().lower()
            name = next((a for a in apps if a.lower() == typed), None) or next(
                (a for a in apps if typed and typed in a.lower()), str(answer.value).strip())
        ok = actions.set_target(name)
        self._set(dictation=dict(self.state["dictation"], app=self._typing_app()))
        if not ok:
            self._notice(f"Couldn't open {name} to type into.")
        return ok

    def _type_sentence(self, text: str) -> dict:
        """Type a confirmed sentence (outside the state lock: it takes a moment)."""
        actions = self.session.actions
        typed = text.strip() + " "
        try:
            outcome = actions.run("type_text", {"text": typed})   # asks for a target first if there is none
        except Exception as error:
            from actions.base import ActionResult
            outcome = ActionResult(False, problem=f"{type(error).__name__}: {error}")
        app = self._typing_app() or "the app"
        return {"type": "typed", "text": text, "app": app, "chars": len(typed),
                "status": "done" if outcome.ok else "failed", "problem": outcome.problem}

    def _untype(self, item: dict) -> None:
        try:
            self.session.actions.run("delete_text", {"count": item["chars"]})
        except Exception as error:
            self._notice(f"Couldn't delete the typed text: {error}")

    # ---- the screen: what the person is looking at --------------------------

    def look(self):
        """Read the screen now (None with actions that can't, e.g. the fakes)."""
        actions = self.session.actions
        reader = getattr(actions, "screen", None)
        if reader is None:
            return None
        try:
            screen = reader()
        except Exception as error:
            self._notice(f"Couldn't read the screen: {error}")
            return None
        self.seen_screen(screen)
        return screen

    def seen_screen(self, screen) -> None:
        shown = screen.to_dict() if hasattr(screen, "to_dict") else None
        if shown != self.state.get("screen"):
            self._set(screen=shown)

    def saw(self, screen) -> None:
        """Show what was seen in Progress (and the conversation), in plain words: "Word open, start screen"."""
        if screen is None:
            return
        with self._lock:
            turn = self.state["turn"]
            if turn is None or turn.get("finished"):
                return
            seen = [i for i in turn["items"] if i["type"] == "seen"]
            if seen and seen[-1]["text"] == screen.words():
                return
            turn["items"].append({"type": "seen", "text": screen.words(), "ms": screen.took_ms})
        self._publish({"type": "state", "event": None, "state": self.snapshot()})

    def retitle(self, command: str) -> None:
        """The step card's title, when the same words meant another command on this screen."""
        from engine.session import sentence_for
        with self._lock:
            turn = self.state["turn"]
            step = next((i for i in reversed(turn["items"]) if i["type"] == "step"), None) if turn else None
            if step is not None:
                step["title"] = wording.step_title(sentence_for(command, {}, self.session.profile))
                step["command"] = command
                turn["request"] = wording.request_text(command, {}, self.session.profile)

    def instead(self, command: str, why: str) -> None:
        """Something else is done than what was confirmed (e.g. opened, because speaking is off):
        the step card says what is really done, with a short note why."""
        from engine.session import sentence_for
        with self._lock:
            turn = self.state["turn"]
            step = next((i for i in reversed(turn["items"]) if i["type"] == "step"), None) if turn else None
            if step is not None:
                step["title"] = wording.step_title(sentence_for(command, {}, self.session.profile))
                step["command"] = command
            if turn is not None:
                turn["items"].append({"type": "note", "icon": "speaker", "text": why})
        self._notice(why)

    def ask_where_to_type(self, screen) -> str:
        """Nothing typeable has the cursor: ask what to do. Returns "anyway", "new_document",
        or why nothing will be typed."""
        from engine.session import Question
        name = screen.app.replace("Microsoft ", "").replace("Google ", "") or "The app"
        if screen.search:
            answer = self._ask(Question("yes_no", "screen_search",
                                        f"The cursor is in a search box in {name}, not in a document. Type there anyway?"))
            return "anyway" if answer is not None and answer.kind == "yes" else "Nothing was typed (the cursor was in a search box)."
        choices = SCREEN_CHOICES.get(screen.place)
        if not choices:
            return f"Nothing was typed: no box in {name} has the cursor. Click where the words should go, then say it again."
        labels = [label for label, _ in choices]
        title = (f"{name} is on its start screen. Blank document, open a recent file, or something else?"
                 if screen.place == "start screen" else f"{name} has no email open. Write a new email, or something else?")
        answer = self._ask(Question("choice", "screen", title, options=labels))
        if answer is None or answer.kind != "pick":
            return "Nothing was typed."
        what = choices[answer.value][1]
        if what == "new_document":
            return "new_document"
        if what == "new_email":
            made = self.session.actions._actions.run("new_email", {}) if hasattr(self.session.actions, "_actions") else None
            self.saw(self.look())
            return ("Started a new email (cursor in To). Say the address, then the words again."
                    if made is not None and made.ok else "Couldn't start a new email.")
        if what == "recent":
            return f"Choose the file in {name}'s list, then say the words again."
        return "Nothing was typed."

    def answer_question(self, question: str, screen=None):
        """A question: answered in one or two sentences from the Brain's own knowledge (it is offline),
        shown and spoken. Never typed."""
        from actions.base import ActionResult
        from repair.answer import answer_question
        with self._lock:
            heard = (self.state["turn"] or {}).get("heard", "")
        question = question.strip() or heard
        start = time.perf_counter()
        text, problem = answer_question(question, self.session.llm, heard,
                                        screen.brief() if screen is not None else "")
        took = (time.perf_counter() - start) * 1000
        with self._lock:
            if self.state["turn"] is not None:
                self.state["turn"]["steps"].append({"name": "Answer (Bonsai)", "ms": round(took)})
        if text is None:
            return ActionResult(False, problem=f"Couldn't answer that: {problem}")
        return ActionResult(True, say=text, detail="Answered from the Brain's own knowledge (offline, no internet)")

    # ---- camera -----------------------------------------------------------

    def attach_camera(self, camera) -> None:
        self.camera = camera
        self._set(camera=dict(self.state["camera"], connected=True, note=""))

    def camera_state(self, changes: dict) -> None:
        problem = changes.get("problem")
        if problem and problem != self.state["camera"].get("problem"):
            self._notice(problem)   # say it once, in plain words
        self._set(camera=dict(self.state["camera"], **changes))

    # ---- the conversation thread ------------------------------------------

    def _work(self) -> None:
        while True:
            kind, payload = self._requests.get()
            if kind == "stop":
                return
            try:
                if kind == "undo":
                    self.session.undo()
                elif kind == "quick":
                    self._quick_by_voice(payload["index"], payload["source"])
                else:
                    self._drop = False
                    self._run_turn(payload)
            except Exception as error:   # never crash in front of judges
                self._question = None
                self._notice(f"Something went wrong: {type(error).__name__}: {error}")
            self._drop = False
            then, self._then = self._then, []
            for words, source in then:   # said along with an answer: the next request(s)
                self._requests.put(("hear", {"text": words, "source": source, "rehear": None, "asr_ms": None}))
            self._set(status="ready", question=None, hold=None, attention=False, counts=self._counts())
            if self.session.conference and self.camera is not None:
                self.camera.want(True)   # Conference mode: thumbs can pick a quick phrase at any time
            if self._requests.empty():
                self._listen_on()   # mini window: hands-free, listen for the next request

    def _run_turn(self, request: dict) -> None:
        with self._lock:
            self._turns += 1
            self.state["turn"] = {"id": self._turns, "heard": request["text"], "source": request["source"],
                                  "asr_ms": request["asr_ms"], "meaning": None, "confidence": None,
                                  "how": None, "reasons": [], "steps": [], "result": None,
                                  "request": None, "intent_args": {}, "items": [],
                                  "started": time.time(), "response_ms": None, "finished": False}
            if request.get("hinted"):
                self.state["turn"]["items"].append({"type": "note", "icon": "ear",
                                                    "text": "Unclear the first time; heard again with your phrases as a hint"})
            if request["asr_ms"] is not None:
                self.state["turn"]["steps"].append({"name": "Speech recognition", "ms": request["asr_ms"]})
            self.state["status"] = "thinking"
        if self.camera is not None:
            self.camera.want(False)   # warm the camera up now (about 2 s), in case a card follows
        heard, then_type = request["text"], None
        parts = split_compound(heard)
        if parts:
            first, rest, sure = parts
            if not sure:   # the joining word was only a near match: check first
                from engine.session import Question
                answer = self._ask(Question("yes_no", "split", "Did you mean...?",
                                            data={"first": first, "rest": rest}))
                sure = answer is not None and answer.kind == "yes"
            if sure:
                heard, then_type = first, rest
        address = None if parts else (email_address(heard) or self._contact_address(heard))
        if address and self.session.actions is not None:
            try:
                self._type_address(address)
            finally:
                self._finish_turn()
            return
        flow = self.session.hear(heard, source=request["source"],
                                 rehear=request["rehear"] if then_type is None else None)
        pending = None
        try:
            question = next(flow)
            while True:
                question = flow.send(self._ask(question))
        except StopIteration as finished:
            pending = finished.value
        try:
            if then_type is not None:
                self._then_type(then_type)
        finally:
            self._finish_turn()
        if pending:   # a sentence said during the correction window: the next request
            self._requests.put(("hear", {"text": pending, "source": request["source"], "rehear": None,
                                         "asr_ms": None}))

    def _contact_address(self, heard: str) -> str | None:
        """With the cursor in To or Cc, a contact's name ("me", "Anna") means their address."""
        from rapidfuzz import fuzz
        screen = self.state.get("screen") or {}
        contacts = getattr(self.session.actions, "contacts", None)
        if screen.get("field") not in ("to", "cc") or not isinstance(contacts, dict):
            return None
        said = " ".join(heard.lower().replace(".", " ").replace(",", " ").split())
        for prefix in ("to ", "send it to ", "it's for ", "for "):
            if said.startswith(prefix):
                said = said[len(prefix):]
        name = max(contacts, key=lambda n: fuzz.ratio(said, n.lower()), default=None)
        if name and fuzz.ratio(said, name.lower()) >= 90 and "@" in str(contacts[name]):
            return str(contacts[name])
        return None

    def _type_address(self, address: str) -> None:
        """A spoken email address ("sam at gmail dot com"): easy to mishear, so always checked first."""
        from engine.session import Question
        with self._lock:
            self.state["turn"]["request"] = f"Type the address {address}"
        answer = self._ask(Question("yes_no", "address", "Did you mean this address?", data={"address": address}))
        if answer is None or answer.kind != "yes":
            with self._lock:
                self.state["turn"]["items"].append({"type": "reply", "text": "Okay \u2014 I didn't type it."})
            return
        outcome = self.session.actions.run("type_text", {"text": address})
        with self._lock:
            self.state["turn"]["items"].append({"type": "typed", "text": address, "app": self._typing_app() or "the app",
                                                "chars": len(address), "status": "done" if outcome.ok else "failed",
                                                "problem": outcome.problem})
        self._publish({"type": "state", "event": None, "state": self.snapshot()})

    def _then_type(self, text: str) -> None:
        """The second half of "open Notes and type ...": typed once the command has been done."""
        with self._lock:
            turn = self.state["turn"]
            done = [i for i in turn["items"] if i["type"] in ("step", "spoken", "typed")]
            ok = bool(done) and done[-1]["status"] == "done"
        if not ok:
            with self._lock:
                turn["items"].append({"type": "reply", "text": f"I didn't type \u201c{text}\u201d because the first part wasn't done."})
            return
        item = self._type_sentence(text)
        with self._lock:
            turn["items"].append(item)
            turn["request"] = f"{turn['request'] or ''}, then type \u201c{text}\u201d".lstrip(", ")
        self._publish({"type": "state", "event": None, "state": self.snapshot()})

    def _mark_voice(self, text: str, voice: str) -> None:
        """Note on the card which voice says this (TTSKit, prepared, or the Mac voice)."""
        with self._lock:
            turn = self.state["turn"]
            if turn is None:
                return
            for item in reversed(turn["items"]):
                if ((item["type"] in ("spoken", "typed") and item["text"] == text)
                        or (item["type"] == "step" and item["title"] == wording.step_title(text))
                        or (item["type"] == "reply" and item["text"] == text)):
                    item["voice"] = voice
                    break

    def _finish_turn(self) -> None:
        """The request is over: it joins the conversation history."""
        with self._lock:
            turn = self.state["turn"]
            if turn is None or turn.get("finished"):
                return
            turn["finished"] = True
            for item in turn["items"]:
                if item["type"] == "step" and item["status"] == "running":
                    item["status"] = "done"
            if not any(i["type"] in ("step", "spoken", "typed", "reply") for i in turn["items"]):
                turn["items"].append({"type": "reply", "text": "Okay — I didn't do anything."})
            self.state["history"].append(copy.deepcopy(turn))
            del self.state["history"][:-HISTORY_MAX]

    def new_task(self) -> None:
        """Clear the conversation on screen (nothing learned is forgotten)."""
        if self.busy:
            return
        self._set(history=[], turn=None)

    def _card(self, shown: dict) -> dict:
        with self._lock:
            turn = self.state["turn"]
        card = wording.permission(shown, self.session.profile, turn)
        app = (self.state.get("screen") or {}).get("app")
        if app and (shown.get("data") or {}).get("choice") == "close_app":
            card["body"] = f"Close the app ({app})"   # say which app "the app" is
        return card

    def _ask(self, question):
        """Show a question and wait for an answer from any input."""
        if self._drop:   # the person already moved on to a new request ("no, open Word"): no more questions
            return Answer("yes" if question.kind == "window" else "none", source="speech")
        if self._then and question.kind == "window":   # "yes, then open Mail": keep it and move on at once
            return Answer("yes", source="speech")
        while not self._answers.empty():   # answers given before this question was asked don't count
            self._answers.get_nowait()
        self._qid += 1
        qid = self._qid
        wait = question.seconds or self.question_wait_s
        shown = {"id": qid, "kind": question.kind, "stage": question.stage, "text": question.text,
                 "options": list(question.options), "seconds": question.seconds, "data": question.data,
                 "hint": hint(question), "closes_at": time.time() + wait}
        if question.kind != "window":
            shown["card"] = self._card(shown)
        with self._lock:
            turn = self.state["turn"]
            if turn is not None:
                if turn["response_ms"] is None:
                    turn["response_ms"] = round((time.time() - turn["started"]) * 1000)
                if question.kind == "window":
                    steps = [i for i in turn["items"] if i["type"] in ("step", "spoken", "typed")]
                    if steps:
                        steps[-1]["undo_until"] = shown["closes_at"]
                else:
                    turn["items"].append({"type": "permission", "qid": qid, "kind": question.kind,
                                          "stage": question.stage, "card": shown["card"], "status": "open",
                                          "answer": None})
        self._question = question
        if self.camera is not None and question.kind in ("yes_no", "choice"):
            self.camera.want(True)
        self._set(event={"kind": "question", "stage": question.stage}, status="asking", question=shown, attention=False)
        if self.session.conference and question.kind in ("yes_no", "choice"):
            top = self._top_guess(shown.get("card") or {})
            if top:   # made by TTSKit while the card is up, so it plays the instant it is confirmed
                self.voice.prefetch(top)
        elif self.state["window"]["mode"] == "mini" and question.kind in ("yes_no", "choice"):
            try:   # in the corner it is easy to miss: say the question (the microphone is paused meanwhile)
                spoken = self.voice.speak(self.spoken_question(shown.get("card") or {}))
            except Exception:
                spoken = False
            if not spoken:   # can't be said (voice not ready, or Speaking off): a soft chime + glow until answered
                self._set(attention=True)
                self.voice.chime()
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
        if self.camera is not None:
            self.camera.want(False)
        self._resolve(qid, question, shown, answer)
        self._set(event={"kind": "answered", "answer": answer.__dict__ if answer else None},
                  status="thinking", question=None, hold=None, attention=False,
                  mic=dict(self.state["mic"], listening=False))
        return answer

    def _resolve(self, qid: int, question, shown: dict, answer) -> None:
        """Mark the question's card in the conversation with what was answered."""
        with self._lock:
            turn = self.state["turn"]
            if turn is None:
                return
            if question.kind == "window":
                for item in turn["items"]:
                    item.pop("undo_until", None)
                return
            item = next((i for i in turn["items"] if i.get("qid") == qid), None)
            if item is None:
                return
            card = shown.get("card") or {}
            source = {"hand": "by hand signal", "speech": "by voice", "keyboard": "by keyboard",
                      "screen": "", "head": "by head movement"}.get(answer.source if answer else "", "")
            if answer is None or answer.kind == "none":
                item["status"], item["answer"] = "none", "No answer — nothing was done"
            elif answer.kind == "yes":
                item["status"], item["answer"] = "allowed", card.get("allow", "Yes")
            elif answer.kind == "no":
                item["status"], item["answer"] = "denied", card.get("deny", "No")
            elif answer.kind == "pick":
                options = card.get("options") or []
                text = options[answer.value]["text"] if 0 <= answer.value < len(options) else str(answer.value)
                item["status"], item["answer"] = "picked", text
            elif answer.kind == "text":
                item["status"], item["answer"] = "picked", str(answer.value)
            item["by"] = source

    def stop(self) -> None:
        self._requests.put(("stop", None))

    # ---- what the conversation reports -------------------------------------

    def _conversation(self, turn: dict, event: dict) -> None:
        """Turn engine events into the Cowork conversation: the request, notes,
        step cards and replies (permission cards are added in _ask)."""
        kind, items, profile = event["kind"], turn["items"], self.session.profile
        if kind == "fast_path":
            intent = event["intent"]
            turn["request"] = wording.request_text(intent["choice"], intent["args"], profile)
            turn["intent_args"] = intent["args"]
            items.append({"type": "note", "icon": "bolt",
                          "text": f"Recognised instantly from what I've learned ({event['latency_ms']:.0f} ms, no model)"})
        elif kind == "brain":
            intent = event["intent"]
            turn["intent_args"] = intent.get("args") or {}
            if intent["confidence"] == "high" and intent.get("choice"):
                turn["request"] = wording.request_text(intent["choice"], intent["args"], profile)
        elif kind == "sound_check" and event.get("confirmed"):
            items.append({"type": "note", "icon": "ear", "text": "Listened again: the recording backs this up"})
        elif kind == "act":
            self.voice.next_in_background = True   # say the step while it happens
            turn["request"] = wording.request_text(event["command"], event.get("args") or {}, profile)
            if turn["response_ms"] is None:
                turn["response_ms"] = round((time.time() - turn["started"]) * 1000)
            step = {"type": "step", "title": wording.step_title(event.get("say", "")), "status": "running",
                    "command": event["command"], "detail": ""}
            if event["command"] == "compose_email":
                step["email"] = wording.email_preview(event.get("args") or {})
            items.append(step)
        elif kind == "acted":
            step = next((i for i in reversed(items) if i["type"] == "step"), None)
            if step:
                step["status"] = "done" if event["ok"] else "failed"
                step["detail"] = event.get("detail") or ""
                step["problem"] = event.get("problem") or ""
        elif kind == "speak" and event.get("kind_of") == "phrase":
            turn["request"] = turn["request"] or event["text"]
            if turn["response_ms"] is None:
                turn["response_ms"] = round((time.time() - turn["started"]) * 1000)
            if event.get("typed"):
                items.append(event["typed"])   # dictation: typed into the app, then read back
            else:
                items.append({"type": "spoken", "text": event["text"], "status": "running"})
        elif kind == "speak" and event.get("kind_of") == "result":
            items.append({"type": "reply", "text": event["text"], "read_aloud": True})
        elif kind == "spoke" and self.session.conference:
            pass   # said in the background: _said_status() marks it when the sound really starts and ends
        elif kind == "spoke":
            spoken = next((i for i in reversed(items) if i["type"] == "spoken" and i["status"] == "running"), None)
            if spoken is None:
                self.state["dictation"]["app"] = self._typing_app() or self.state["dictation"]["app"]
            if spoken:
                spoken["status"] = "done"
        elif kind == "command_off":
            items.append({"type": "reply", "text": f"“{wording.from_meaning(event.get('meaning'), profile)}” is a command. "
                                                   "Commands are off in Conference mode."})
        elif kind == "learned":
            meant = wording.request_text(*self._final(turn, event), profile)
            items.append({"type": "note", "icon": "learn", "learned_from": event["heard"],
                          "text": f"Learned: “{event['heard']}” means “{meant}”"})
        elif kind == "cancelled":
            for item in reversed(items):
                if item["type"] in ("step", "spoken", "typed"):
                    item["status"] = "undone"
                    break
            turn["request"] = None
        elif kind == "new_phrase":
            items.append({"type": "note", "icon": "learn", "text": f"Added to your phrases: “{event['phrase']}”"})
        elif kind in ("problem", "guess_failed"):
            items.append({"type": "reply", "error": True,
                          "text": event.get("text") or event.get("problem") or "Something went wrong."})
        elif kind == "no_more_guesses":
            items.append({"type": "reply", "text": "I'm out of guesses. Please try saying it again."})

    def _final(self, turn: dict, event: dict) -> tuple:
        """(choice, args) of what was just confirmed, for its wording."""
        result = turn.get("result") or {}
        meant = event.get("meaning", "")
        if result.get("command") and meant.startswith(result["command"]):
            return result["command"], result.get("args") or {}
        for command in self.session.profile["commands"]:
            if meant == command["name"] or meant.startswith(command["name"] + " ("):
                return command["name"], turn.get("intent_args") or {}
        return meant, {}

    def _on_event(self, event: dict) -> None:
        kind = event["kind"]
        typed = None
        if kind == "speak" and event.get("kind_of") == "phrase" and self.session.conference:
            self._said_add(event["text"])        # the transcript shows it at once ("Preparing voice…")
            self.voice.next_in_background = True   # said while the 3 s undo window is open, so Undo can stop it
        elif (kind == "speak" and event.get("kind_of") == "phrase" and self.state["dictation"]["on"]
                and self.session.actions is not None):
            typed = self._type_sentence(event["text"])   # typed first, then read back
        if kind == "command_off":
            self._notice("Commands are off in Conference mode. Switch to Computer to use them.")
        if kind == "cancelled":
            turn = self.state["turn"]
            last = next((i for i in reversed(turn["items"]) if i["type"] in ("typed", "step", "spoken")), None) if turn else None
            if last is not None and last["type"] == "typed" and last["status"] == "done":
                self._untype(last)
        with self._lock:
            if typed is not None and self.state["turn"] is not None:
                event = dict(event, typed=typed)
            turn = self.state["turn"]
            steps = turn["steps"] if turn else []
            if turn is not None and not turn.get("finished"):
                self._conversation(turn, event)
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
                self.state["feed"].insert(0, {**event, "time": time.time(),
                                              "request": wording.from_meaning(event.get("meaning"), self.session.profile)})
                del self.state["feed"][FEED_MAX:]
            if kind == "profile_changed":
                self.state["counts"] = self._counts()
                self.state["catalog"]["phrases"] = list(self.session.profile["phrases"])
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
