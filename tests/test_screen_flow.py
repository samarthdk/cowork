"""Checks for "look before acting" (the screen state), commands vs. typing vs.
questions, spoken email addresses, and the email flow. Fakes only: a fake Mac
whose screen can be set by the test. Run:
    .venv/bin/python tests/test_screen_flow.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

config.ASR = config.LLM = config.TTS = config.ACTIONS = "fake"
from actions.base import ActionResult  # noqa: E402
from actions.fake import FakeActions  # noqa: E402
from actions.screen import Screen  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.guess import guess_messages  # noqa: E402
from repair.prompt import build_messages, build_schema, schema_choices  # noqa: E402
from repair.profile import load_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui import wording  # noqa: E402
from ui.app import App  # noqa: E402
from ui.screen_rules import hide_for, is_question, remap, says_type  # noqa: E402
from ui.spoken import email_address  # noqa: E402

BASE = load_profile(config.ROOT / "profiles" / "cowork_base.json")
results = []


def check(name, got, expected):
    ok = got == expected
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}, expected {expected!r}")


def wait_for(test, seconds=6.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.02)
    return False


START = Screen(app="Microsoft Word", pid=1, window="Open new and recent files", place="start screen",
               choices=["Blank document", "Open a recent file"])
SEARCH = Screen(app="Microsoft Word", pid=1, place="start screen", focus="a search box", search=True)
DOC = Screen(app="Microsoft Word", pid=1, window="Document1", place="document", focus="the document", typeable=True)
TO = Screen(app="Mail", pid=2, place="new email", field="to", focus="the To box", typeable=True)
INBOX = Screen(app="Mail", pid=2, place="inbox")


class ScreenActions(FakeActions):
    """Fake Mac actions with a screen the test sets; new_document makes a document."""
    def __init__(self, screen):
        super().__init__(apps=("Mail", "Microsoft Word", "Google Chrome"), target=screen.app)
        self.now = screen

    def screen(self):
        return self.now

    def context(self):
        found = super().context()
        found["_screen"] = self.now
        found["field"] = ["To", "Cc", "Subject", "Message"]
        return found

    def run(self, command, args):
        result = super().run(command, args)
        if command == "new_document":
            self.now = DOC
        if command == "new_email":
            self.now = TO
        return result


def make(screen, dictation=False):
    actions = ScreenActions(screen)
    core = App(BASE, FakeLLM(), FakeTTS(quiet=True), actions=actions, question_wait_s=4.0)
    core.session.save = lambda: None
    if dictation:
        core.set_dictation(True)
    return core, actions


def asking(core, kind=None):
    return wait_for(lambda: core.state["question"] is not None and (kind is None or core.state["question"]["kind"] == kind))


def past_did_you_mean(core):
    """The fake Brain is often unsure: say yes to its "Did you mean ...?" first."""
    asking(core)
    if core.state["question"] and core.state["question"]["stage"] == "did_you_mean":
        qid = core.state["question"]["id"]
        core.answer("yes")
        wait_for(lambda: core.state["question"] is None or core.state["question"]["id"] != qid)


def finished(core, n=1):
    return wait_for(lambda: len(core.state["history"]) >= n and core.state["status"] == "ready", 8)


# ---- what the screen says, in words ------------------------------------------------
check("Word start screen, in words", START.words(), "Word open, start screen")
check("a new blank document", DOC.words(), "Blank document ready")
check("a new email", TO.words(), "Mail open, new email, cursor in To")
check("for the Brain: app, place, choices, cursor",
      START.brief(), "Microsoft Word, start screen (choices: Blank document, Open a recent file); nothing to type into.")

# ---- commands vs. typing vs. questions -------------------------------------------------
check("'type see you at noon' is typing", says_type("type see you at noon"), True)
check("'write an email' is NOT typing (a command)", says_type("write an email"), False)
check("'what's the weather in Boston?' is a question", is_question("what's the weather in Boston?"), True)
check("'can you open Mail' is a request, not a question", is_question("can you open Mail"), False)
check("a question is never typed, even with dictation on in a document",
      "type_text" in hide_for(DOC, "what's the weather in Boston", dictation=True), True)
check("dictation on + a document: typing allowed", "type_text" in hide_for(DOC, "see you at noon", dictation=True), False)
check("dictation on but Word's start screen: no typing",
      "type_text" in hide_for(START, "see you at noon", dictation=True), True)
check("dictation off: only 'type ...' types", ("type_text" in hide_for(DOC, "see you at noon", False),
                                               "type_text" in hide_for(DOC, "type see you at noon", False)), (True, False))
check("'next box' and 'send' only with an email open",
      [n in hide_for(INBOX, "next", False) for n in ("next_field", "go_to_field", "send_email")], [True, True, True])
check("  ...and in an email they are allowed", [n in hide_for(TO, "next", False) for n in ("next_field", "send_email")],
      [False, False])
check("'back' in an email is back a box", (remap("go_back", TO), remap("go_back", DOC)), ("previous_field", "go_back"))

# ---- the Brain is told what is on screen, in the request (the instructions stay cached) ---------
context = {"app": ["Mail", "Microsoft Word"], "field": ["To", "Cc", "Subject", "Message"], "_screen": START,
           "_hide": hide_for(START, "see you at noon", True)}
messages = build_messages(BASE, "see you at noon", context)
check("screen state is in the request", "On screen now: Microsoft Word, start screen" in messages[1]["content"], True)
check("  ...not in the instructions (so LM Studio reuses them)", "start screen" in messages[0]["content"], False)
plain = build_messages(BASE, "see you at noon", dict(context, _screen=DOC, _hide=()))
check("  ...the instructions are the same whatever is on screen", plain[0]["content"] == messages[0]["content"], True)
check("hidden commands can't be chosen", "type_text" in schema_choices(build_schema(BASE, context)), False)
check("  ...but stay in the instructions", "type_text" in messages[0]["content"], True)

# ---- spoken email addresses -----------------------------------------------------------
check("'sam at gmail dot com'", email_address("sam at gmail dot com"), "sam@gmail.com")
check("spelled letters", email_address("s a m at gmail dot com"), "sam@gmail.com")
check("a dot in the name", email_address("sam dot lee at cornell dot edu"), "sam.lee@cornell.edu")
check("already written by the recognizer", email_address("Sam@gmail.com."), "sam@gmail.com")
check("not an address", (email_address("open mail"), email_address("I am at home")), (None, None))

core, actions = make(TO)
core.say("sam at gmail dot com")
check("an address is checked first ('Did you mean this address?')", asking(core, "yes_no")
      and core.state["question"]["stage"], "address")
card = core.state["question"]["card"]
check("  ...the card shows the real address", (card["body"], card["allow"]), ("sam@gmail.com", "Yes, type it"))
core.answer("yes")
finished(core)
check("  ...then it is typed", actions.done[-1], ("type_text", {"text": "sam@gmail.com"}))

core, actions = make(TO)
actions.contacts = {"Me": "me@example.org", "Anna": "anna@example.com"}
core.seen_screen(TO)
core.say("me")
check("in To, a contact's name ('me') is offered as their address",
      asking(core, "yes_no") and core.state["question"]["card"]["body"], "me@example.org")
core.answer("yes")
finished(core)
check("  ...and typed", actions.done[-1], ("type_text", {"text": "me@example.org"}))
core.state["question"] = None

# ---- never type unless a place for words has the cursor ----------------------------------
core, actions = make(START, dictation=True)
core.say("type see you at noon")
past_did_you_mean(core)
check("typing on Word's start screen asks first", asking(core, "choice") and core.state["question"]["stage"], "screen")
card = core.state["question"]["card"]
check("  ...with the choices on screen", (card["title"], [o["text"] for o in card["options"]]),
      ("Word is on its start screen. Blank document, open a recent file, or something else?",
       ["Blank document", "Open a recent file", "Something else…"]))
check("  ...nothing typed yet", [c for c, _ in actions.done], [])
core.answer("pick", 0)
finished(core)
check("  ...'Blank document' makes one, then types", [c for c, _ in actions.done], ["new_document", "type_text"])
items = core.state["history"][-1]["items"]
check("  ...and Progress says what it saw", [i["text"] for i in items if i["type"] == "seen"][-1], "Blank document ready")

core, actions = make(SEARCH, dictation=True)
core.say("type see you at noon")
past_did_you_mean(core)
check("a search box: asks before typing", asking(core, "yes_no") and core.state["question"]["stage"], "screen_search")
core.answer("no")
finished(core)
check("  ...'Don't type' types nothing", [c for c, _ in actions.done], [])

# ---- questions are answered, never typed --------------------------------------------------
core, actions = make(DOC, dictation=True)
core.say("what's the weather in Boston")
past_did_you_mean(core)
finished(core)
turn = core.state["history"][-1]
replies = [i for i in turn["items"] if i["type"] == "reply"]
check("a question is answered (shown and read aloud)", bool(replies) and replies[-1].get("read_aloud"), True)
check("  ...nothing was typed", [c for c, _ in actions.done if c == "type_text"], [])

# ---- guesses: the screen and the words heard; a guess can be a question ----------------------
messages = guess_messages(BASE, "wat iz da wedder", [], screen=INBOX.brief())
check("guesses are told what is on screen now", "On their screen now: Mail, inbox" in messages[1]["content"], True)
check("  ...and not to assume an earlier task", "do NOT assume" in messages[1]["content"], True)
check("a question guess offers to answer it",
      wording.permission({"stage": "guess", "data": {"guess": "What's the weather?", "kind": "question"}}, BASE, None)["allow"],
      "Yes, answer it")

# ---- the person's own words are kept exactly, and never learned ----------------------------
from repair import _echo, words_after  # noqa: E402
from repair.validate import Intent  # noqa: E402
check("the words after 'type' are exactly the ones heard",
      words_after("Type this as a test from Cowork.", ["type", "write"]), "this as a test from Cowork.")
intent = Intent("type_text", "command", {"text": "Cowork."}, "low", [])
_echo("Type this as a test from Cowork.", intent, BASE)
check("  ...even when the Brain shortened them (measured)", intent.args["text"], "this as a test from Cowork.")
intent = Intent("answer_question", "command", {"question": "what's the weather in Boston?'}"}, "high", [])
_echo("what's the weather in Boston", intent, BASE)
check("leftover JSON bits are tidied from a question", intent.args["question"], "what's the weather in Boston?")
core, actions = make(DOC, dictation=True)
core.say("type see you at noon")
past_did_you_mean(core)
finished(core)
check("typed words are not learned (the fast path must not retype them)",
      [e for e in core.session.profile.get("examples", []) if e["meant"] == "type_text"], [])

# ---- keys only go to the app that has the keyboard -------------------------------------------
import actions.mac as mac_module  # noqa: E402
front = mac_module.FrontApp.__new__(mac_module.FrontApp)
front.pid = 111
saved = mac_module.front_windows, mac_module.focused_pid
mac_module.front_windows = lambda: [{"pid": 111, "app": "Mail", "bounds": {}}]
mac_module.focused_pid = lambda: 222   # Mail's window is on top, but Word has the keyboard (measured)
check("an app whose window is on top but without the keyboard is NOT in front", front.in_front(), False)
mac_module.focused_pid = lambda: 111
check("  ...with the keyboard it is", front.in_front(), True)
mac_module.front_windows, mac_module.focused_pid = saved

# ---- the mini window ------------------------------------------------------------------------
class FakeListener:
    """Stands in for the microphone: records when Cowork listens."""
    running, muted = True, False
    def __init__(self):
        self.armed, self.triggers = False, 0
    @property
    def listening(self):
        return self.armed
    def trigger(self, wait_s=None):
        self.armed, self.triggers = True, self.triggers + 1
    def cancel(self):
        self.armed = False
    def mute(self):
        pass
    def unmute(self):
        pass


core, actions = make(INBOX)
listener = FakeListener()
core.attach_listener(listener)
switched = []
core.window_hook = switched.append
spoken = []
core.voice.tts.speak = spoken.append
core.say("open microsoft word")
past_did_you_mean(core)
finished(core)
check("opening an app shrinks Cowork to the corner", (core.state["window"]["mode"], switched), ("mini", ["mini"]))
check("  ...listening hands-free for the next request", (core.state["window"]["hands_free"], listener.armed), (True, True))
before = listener.triggers
listener.armed = False
core.mic_timeout()
check("  ...and again after a quiet spell", listener.triggers, before + 1)
core.say("stop listening")
check("'stop listening' stops it", (core.state["window"]["hands_free"], listener.armed), (False, False))
check("Esc with no question passes through to the app", core.mini_key(53), False)
core.set_hands_free(True)
core.mini_key(53)
check("  ...and stops listening too", core.state["window"]["hands_free"], False)
check("keys with no question open are not taken from the app", core.mini_key(36), False)

core.say("send the email")   # risky: always asks, and in the corner the question is said aloud
check("a question in the mini window", asking(core, "yes_no"), True)
check("  ...is spoken aloud", any("can't be undone" in t for t in spoken), True)
check("  ...Esc answers it (and is kept from the app)", core.mini_key(53), True)
wait_for(lambda: core.state["question"] is None or core.state["question"]["kind"] != "yes_no")
core.answer("none")
finished(core, 2)
core.say("show cowork")
check("'show Cowork' brings back the full window", (core.state["window"]["mode"], switched[-1]), ("full", "full"))

core, actions = make(INBOX)
core.window_hook = switched.append
core.set_window("mini")
core._opened = ["Mail"]
core._after_action("quit_app", {"app": "Mail"}, ActionResult(True), INBOX)
check("closing the last app Cowork opened brings back the full window", core.state["window"]["mode"], "full")
check("the question is said in a few words",
      App.spoken_question({"title": "Did you mean\u2026?", "body": "Open Mail"}, []), "Did you mean: Open Mail?")

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.exit(0 if all(results) else 1)
