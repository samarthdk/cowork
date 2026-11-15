"""Recent tasks (bug: after "New task", Recent tasks said "No tasks yet").

"New task" saves the conversation (named from its first request, with the time), Recent tasks
lists them newest first, one can be shown read-only and continued, they are kept in a local
gitignored file across restarts, and empty conversations are not saved. Run:
    .venv/bin/python tests/test_tasks.py
"""

import copy
import os
import subprocess
import sys
import tempfile
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

results = []
BASE = open_profile(config.ROOT / "profiles" / "cowork_base.json", config.ROOT / "profiles" / "cowork_base.json")
FOLDER = Path(tempfile.mkdtemp())
TASKS = FOLDER / "cowork_tasks.json"


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
    return App(copy.deepcopy(BASE), FakeLLM(), FakeTTS(quiet=True), actions=FakeActions(), question_wait_s=20,
               tasks_path=TASKS)


def request(core, text):
    n = len(core.state["history"])
    core.say(text)
    end = time.monotonic() + 8
    while time.monotonic() < end and not (len(core.state["history"]) > n and core.state["status"] == "ready"):
        q = core.state["question"]
        if q is not None:
            core.answer("yes" if q["kind"] in ("yes_no", "window") else "none")
            wait_for(lambda: core.state["question"] is None or core.state["question"]["id"] != q["id"], 3)
        time.sleep(0.02)


core = make()
check("no tasks at first", core.state["tasks"], [])
core.new_task()
check("an empty conversation is not saved", (core.state["tasks"], TASKS.exists()), ([], False))
request(core, "open mail")
request(core, "scroll down")
core.new_task()
tasks = core.state["tasks"]
check("'New task' saves the conversation", len(tasks), 1)
check("  ...named from its first request", tasks[0]["title"], "Open Mail")
check("  ...with the time and how many requests", (isinstance(tasks[0]["time"], float), tasks[0]["requests"]), (True, 2))
check("  ...and the screen is clear", core.state["history"], [])
check("  ...kept in a local file", TASKS.exists(), True)
request(core, "i need to use the bathroom")
core.new_task()
check("newest first", [t["title"] for t in core.state["tasks"]], ["I need to use the bathroom.", "Open Mail"])

check("shown read-only", core.view_task(core.state["tasks"][1]["id"]) and core.state["viewing"]["title"], "Open Mail")
check("  ...with its conversation", len(core.state["viewing"]["turns"]), 2)
check("  ...the live conversation is untouched", core.state["history"], [])
core.view_task(None)
check("back", core.state["viewing"], None)

request(core, "scroll up")
older = core.state["tasks"][1]["id"]
check("continue a task", core.continue_task(older), True)
check("  ...its conversation is back on screen", [t.get("request") for t in core.state["history"]], ["Open Mail", "Scroll down"])
check("  ...the one on screen before was saved first", [t["title"] for t in core.state["tasks"]], ["Scroll up", "I need to use the bathroom."])

again = make()   # a restart
check("tasks survive a restart", [t["title"] for t in again.state["tasks"]], ["Scroll up", "I need to use the bathroom."])
again.view_task(again.state["tasks"][0]["id"])
request(again, "scroll down")
check("a new request closes the read-only view", again.state["viewing"], None)

ignored = subprocess.run(["git", "check-ignore", "profiles/cowork_tasks.json", "profiles/fake_cowork_tasks.json"],
                         cwd=config.ROOT, capture_output=True, text=True).stdout.split()
check("the tasks files are gitignored", ignored, ["profiles/cowork_tasks.json", "profiles/fake_cowork_tasks.json"])

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.stdout.flush()
os._exit(0 if all(results) else 1)
