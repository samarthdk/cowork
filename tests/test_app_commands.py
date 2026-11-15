"""Requests inside the app in front (bug: "reply to this email" did nothing).

1. Common commands added directly (Mail, Word/Notes/TextEdit, Chrome/Safari).
2. Anything else: the app's own menus and buttons (repair/menu_pick.py), Bonsai picks one or none.
3. Risky items (send, delete, quit, erase...) always get the permission card.
4. Nothing fits: said on screen, with the closest items offered.
With the fakes (a made-up menu, nothing done on the Mac). Run:
    .venv/bin/python tests/test_app_commands.py
"""

import copy
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

config.ASR = config.LLM = config.TTS = config.ACTIONS = "fake"
from actions.fake import FakeActions  # noqa: E402
from repair import repair as brain  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.menu_pick import candidates, closest, is_risky  # noqa: E402
from repair.profile import is_risky as command_is_risky  # noqa: E402
from repair.store import open_profile  # noqa: E402
from tts.fake import FakeTTS  # noqa: E402
from ui.app import App  # noqa: E402

results = []
BASE = open_profile(config.ROOT / "profiles" / "cowork_base.json", config.ROOT / "profiles" / "cowork_base.json")


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


# ---- 1. the new commands are understood -----------------------------------------------------
WORDINGS = {
    "reply to this email": "reply_email", "write back": "reply_email", "reply all": "reply_all_email",
    "forward this": "forward_email", "forward this email": "forward_email", "mark it as unread": "mark_unread",
    "delete this email": "delete_email", "archive this": "archive_email", "undo that": "undo_edit",
    "select all": "select_all", "make it bold": "make_bold", "new line": "new_line", "new paragraph": "new_paragraph",
    "reload the page": "reload_page", "refresh": "reload_page", "close this tab": "close_tab", "print this": "do_in_app",
}
from repair.learning import fast_path  # noqa: E402
for said, meant in WORDINGS.items():   # as in the app: recognised instantly, else the Brain
    match = fast_path(said, BASE)
    check(f"'{said}'", match.intent.choice if match else brain(said, BASE, FakeLLM(), None).choice, meant)
check("'print this' carries the words as the request", fast_path("print this", BASE).intent.args.get("request"), "print this")
check("delete and archive are risky (always asked first)",
      (command_is_risky(BASE, "delete_email"), command_is_risky(BASE, "archive_email")), (True, True))
check("reply, forward, mark unread are not", any(command_is_risky(BASE, c) for c in ("reply_email", "forward_email", "mark_unread")), False)

# ---- 2. the menu fallback on its own ---------------------------------------------------------
MENU = [{"label": label, "kind": "menu"} for label in FakeActions.MENU]
check("'print this' shortlists File > Print", [i["label"] for i in candidates("print this", MENU)][:1], ["File > Print…"])
check("'make it bigger' finds Zoom In", "View > Zoom In" in [i["label"] for i in candidates("make it bigger", MENU)], True)
check("'mark it as unread' finds the submenu item", [i["label"] for i in candidates("mark it as unread", MENU)][:1],
      ["Message > Mark > As Unread"])
check("nothing shares a word: no shortlist", candidates("fly me to the moon", MENU), [])
check("  ...and nothing unrelated is offered as 'closest'", closest("fly me to the moon", MENU), [])
check("something alike is offered ('sidebar' vs 'Show Side Bar')",
      [i["label"] for i in closest("sidebar", [{"label": "View > Show Side Bar", "kind": "menu"}])], ["View > Show Side Bar"])
check("a word only in the menu's path is not enough ('find a word' vs Edit > Find > Select Line)",
      candidates("find a word", [{"label": "Edit > Find > Select Line", "kind": "menu"}]), [])
check("a match only through a similar word is marked as a guess (asked first)",
      [i["literal"] for i in candidates("make it bigger", [{"label": "View > Zoom In", "kind": "menu"}])], [0])
check("the item's own name beats the menu path ('open a new window')",
      [i["label"] for i in candidates("open a new window", [{"label": "Window > Bring All to Front", "kind": "menu"},
                                                             {"label": "File > New", "kind": "menu"}])][:1], ["File > New"])
for label in ("Mailbox > Erase Deleted Items", "Message > Delete", "File > Quit", "Send", "Account > Sign Out", "Buy Now"):
    check(f"risky: {label}", is_risky(label), True)
for label in ("File > Print…", "View > Zoom In", "Message > Flag", "Format > Bold"):
    check(f"not risky: {label}", is_risky(label), False)


# ---- 3. in the app ------------------------------------------------------------------------------
def make():
    actions = FakeActions(apps=("Safari", "Mail", "Notes", "Microsoft Word"))
    return App(copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=actions, question_wait_s=20), actions


def run(core, text, answer="yes"):
    n = len(core.state["history"])
    core.say(text)
    seen = []
    end = time.monotonic() + 8
    while time.monotonic() < end and not (len(core.state["history"]) > n and core.state["status"] == "ready"):
        q = core.state["question"]
        if q is not None:
            seen.append((q["stage"], (q.get("card") or {}).get("title", "")))
            core.answer(answer if q["kind"] in ("yes_no",) else ("yes" if q["kind"] == "window" else "none"))
            wait_for(lambda: core.state["question"] is None or core.state["question"]["id"] != q["id"], 3)
        time.sleep(0.02)
    return seen


core, actions = make()
run(core, "print this")
check("'print this': the app's own Print menu item is clicked", ("press_item", {"label": "File > Print…"}) in actions.done, True)
core, actions = make()
run(core, "zoom in")
check("'zoom in': View > Zoom In", ("press_item", {"label": "View > Zoom In"}) in actions.done, True)
import threading  # noqa: E402
core, actions = make()
result = {}
threading.Thread(target=lambda: result.update(r=core.do_in_app("erase deleted items"))).start()
q = wait_for(lambda: core.state["question"] is not None) and core.state["question"]
check("a risky menu item gets the permission card", bool(q) and (q["stage"], q["card"]["title"]),
      ("menu", "This can't be undone. Allow it?"))
core.answer("no")
wait_for(lambda: "r" in result)
check("  ...declined: nothing clicked", [c for c, _ in actions.done if c == "press_item"], [])
check("  ...and it says so (not silent)", result["r"].detail, "Nothing was clicked in the app (you said no).")
core, actions = make()
core.session.actions._actions.menu_items = lambda: [{"label": "View > Zoom In", "kind": "menu", "element": None}]
r = core.do_in_app("fly me to the moon")
check("nothing fits and nothing alike: says so, no card", (r.ok, r.problem), (False, "I couldn't find a way to do that in the app."))
core, actions = make()
result = {}
core.session.actions._actions.menu_items = lambda: [{"label": "View > Show Side Bar", "kind": "menu", "element": None}]
import threading  # noqa: E402
threading.Thread(target=lambda: result.update(r=core.do_in_app("sidebar"))).start()
q = wait_for(lambda: core.state["question"] is not None) and core.state["question"]
check("nothing fits: the closest items are offered", bool(q) and q["stage"], "menu_closest")
check("  ...and it says so", bool(q) and q["card"]["title"].startswith("I couldn't find a way to do that"), True)
core.answer("none")
wait_for(lambda: "r" in result)
check("  ...'none': nothing clicked, and the reason is shown", (result["r"].ok, result["r"].problem.startswith("I couldn't find")), (False, True))

# ---- a sure, risky request declined: nothing done, and no grid of other options ----------------------
core, actions = make()
seen = run(core, "delete this email", answer="no")
check("'delete this email': the permission card, declined", [s for s, _ in seen], ["did_you_mean"])
check("  ...nothing done", actions.done, [])
last = core.state["history"][-1]["items"] if core.state["history"] else []
check("  ...and it says so", any(i.get("text") == "Okay, not allowed. Nothing was done." for i in last), True)

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
