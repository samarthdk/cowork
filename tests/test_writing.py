"""Writing mode (bug: after "reply to this email", "I am doing good, thanks" was guessed as something else, and
"press send" only showed "heard press send").

After a command that opens a place to write, everything said is typed there as dictation, until "send" (Mail),
"done" / "stop writing", or a clear new command. Sending: the permission card; really sent only to your own
address. With the fakes and a pretend Mail whose screen follows what is done. Run:
    .venv/bin/python tests/test_writing.py
"""

import copy
import os
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
from repair.store import open_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import App  # noqa: E402

results = []
BASE = open_profile(config.ROOT / "profiles" / "cowork_base.json", config.ROOT / "profiles" / "cowork_base.json")
TYPED = "I am doing good, thanks. See you tomorrow."


def check(name, got, expected):
    ok = got == expected
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}, expected {expected!r}")


def wait_for(condition, seconds=6.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def make():
    """A pretend Mac: Mail's screen changes as the commands are done (inbox -> message -> reply)."""
    actions = FakeActions(apps=("Mail", "Microsoft Word", "Safari", "Notes"), target="Mail")
    now = {"screen": Screen(app="Finder", pid=3, place="")}
    reply = Screen(app="Mail", pid=2, window="Re: Testing", place="new email", field="body", focus="the message", typeable=True)
    plain_run, plain_context = actions.run, actions.context

    def run(command, args):
        result = plain_run(command, args)
        if command == "open_app":
            now["screen"] = Screen(app=args.get("app", ""), pid=2, place="inbox" if args.get("app") == "Mail" else "")
        elif command == "open_latest_email":
            now["screen"] = Screen(app="Mail", pid=2, window="Testing", place="message")
        elif command in ("reply_email", "reply_all_email", "forward_email", "new_email"):
            now["screen"] = reply
        elif command == "send_email":
            now["screen"] = Screen(app="Mail", pid=2, place="inbox")
            return ActionResult(True, detail="[fake] sent")
        elif command == "quit_app":
            now["screen"] = Screen(app="Finder", pid=3, place="")
        return result

    actions.run = run
    actions.screen = lambda: now["screen"]
    actions.context = lambda: dict(plain_context(), _screen=now["screen"])
    core = App(copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=actions, question_wait_s=20)
    core.refresh_catalog()
    return core, actions, now


def say(core, text, answer="yes"):
    """Say something and answer any card the way the person would (yes; the undo window: keep)."""
    n = len(core.state["history"])
    core.say(text, source="speech")
    cards = []
    end = time.monotonic() + 8
    while time.monotonic() < end and not (len(core.state["history"]) > n and core.state["status"] == "ready"):
        q = core.state["question"]
        if q is not None:
            cards.append(((q.get("card") or {}).get("title") or q["kind"]))
            core.answer(answer if q["kind"] == "yes_no" else ("yes" if q["kind"] == "window" else "none"))
            wait_for(lambda: core.state["question"] is None or core.state["question"]["id"] != q["id"], 3)
        time.sleep(0.02)
    return cards


for finish in ("press send", "send it", "send mail", "done"):
    core, actions, now = make()
    say(core, "open Mail")
    say(core, "open my latest email")
    say(core, "reply to this email")
    check(f"[{finish}] a reply opened: writing mode, 'Writing your reply'", core.state["writing"], {"label": "your reply", "app": "Mail"})
    actions.done.clear()
    cards = say(core, TYPED)   # free text (none of the phrases), not heard for sure: "Type ...?" first, then typed
    check(f"[{finish}] free text: checked once ('Did you mean: Type ...'), then typed as said",
          ([c for c in cards if c != "window"], [a.get("text") for c, a in actions.done if c == "type_text"]),
          (["Did you mean\u2026?"], ["I am doing good, thanks. See you tomorrow. "]))
    check(f"[{finish}]   ...not forced onto a phrase", core.state["history"][-1]["request"], f"Type \u201c{TYPED}\u201d")
    check(f"[{finish}]   ...still writing", bool(core.state["writing"]), True)
    actions.done.clear()
    cards = say(core, finish)
    if finish == "done":
        check("['done'] writing mode ends, the reply stays open, nothing sent",
              (core.state["writing"], [c for c, _ in actions.done]), (None, []))
        check("['done']   ...and it says so", core.state["history"][-1]["items"][-1]["text"],
              "Done writing. Your reply stays open in Mail.")
    else:
        check(f"[{finish}] recognised at once as sending: the permission card", "This can't be undone. Allow it?" in cards, True)
        check(f"[{finish}]   ...confirmed: sent", [c for c, _ in actions.done], ["send_email"])
        check(f"[{finish}]   ...writing mode is over", core.state["writing"], None)

core, actions, now = make()
say(core, "open Mail")
say(core, "reply to this email")
say(core, "Thanks for the update")
say(core, "new paragraph")
check("'new paragraph' while writing: the command, and writing goes on",
      ([c for c, _ in actions.done][-1:], bool(core.state["writing"])), (["new_paragraph"], True))
say(core, "delete that")
check("'delete that' removes the last sentence", [c for c, _ in actions.done][-1:], ["delete_text"])
before = len(actions.done)
cards = say(core, "We can get a joke on the beach.")   # what WhisperKit heard for TORGO clip 2
check("a garbled sentence is checked first ('Did you mean...?'), then the PHRASE is typed (bug: raw text was typed)",
      ("Did you mean\u2026?" in cards, [a.get("text") for c, a in actions.done[before:] if c == "type_text"]),
      (True, ["We gathered shells on the beach. "]))
before = len(actions.done)
say(core, "We can get a joke on the beach.")   # learned: now instant
check("  ...the same words again: typed at once as the phrase",
      [a.get("text") for c, a in actions.done[before:] if c == "type_text"], ["We gathered shells on the beach. "])
say(core, "open Word")
check("a clear new command ('open Word') ends writing the reply and is done (with a blank document to write in)",
      ((core.state["writing"] or {}).get("label"), [c for c, _ in actions.done][-2:]), ("your document", ["open_app", "new_document"]))

core, actions, now = make()
say(core, "open Mail")
say(core, "reply to this email")
say(core, "close Mail")
check("'close Mail' ends writing and closes Mail", (core.state["writing"], [c for c, _ in actions.done][-1:]), (None, ["quit_app"]))

core, actions, now = make()
say(core, "open Mail")
say(core, "reply to this email")
now["screen"] = Screen(app="Safari", pid=5, place="")   # the person switched to Safari by hand
say(core, "scroll down")
check("switching to another app by hand ends writing (Cowork follows the screen)",
      (core.state["writing"], [c for c, _ in actions.done][-1:]), (None, ["scroll_down"]))

core, actions, now = make()
say(core, "open Mail")
say(core, "reply to this email")
before = len(actions.done)
core._requests.put(("hear", {"text": "Thanks for letting me know.", "source": "microphone", "rehear": None, "asr_ms": 100,
                             "logprob": -0.12}))   # heard clearly by WhisperKit
wait_for(lambda: len(actions.done) > before)
time.sleep(0.3)
check("free text heard clearly: typed at once, no question",
      ([a.get("text") for c, a in actions.done[before:] if c == "type_text"], core.state["question"]),
      (["Thanks for letting me know. "], None))
cards = say(core, "Lovely evening tonight", answer="no")
check("  ...'no' to 'Type ...?': nothing typed, and it says so", core.state["history"][-1]["items"][-1].get("text"),
      "Okay, nothing was typed. Say it again.")
core.say("stop", source="speech")
check("'stop' ends writing too", wait_for(lambda: core.state["writing"] is None), True)

# "open Microsoft Word": Word and a new blank document in one go, ready for writing
core, actions, now = make()
say(core, "open Microsoft Word")
check("'open Microsoft Word' also makes a new blank document", [c for c, _ in actions.done], ["open_app", "new_document"])
check("  ...and writing mode is on for it", (core.state["writing"] or {}).get("label"), "your document")
check("  ...one step on screen says so", "new blank document ready" in core.state["history"][-1]["items"][-2].get("detail", "")
      if len(core.state["history"][-1]["items"]) > 1 else
      any("new blank document ready" in (i.get("detail") or "") for i in core.state["history"][-1]["items"]), True)

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
