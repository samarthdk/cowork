"""Which lines Cowork speaks (TTSKit only), which stay silent, and the time to first sound.

    .venv/bin/python eval/voice_report.py [--conference]

Runs the 3 TORGO demo clips (dictation on) and the Mail flow with the REAL WhisperKit,
Bonsai (LM Studio) and TTSKit voice, so sound comes out of the speakers. The Mac actions are
the FAKE ones: nothing is opened, typed or sent (the spoken lines are the same: they come
from the commands, not from the apps). The profile is a temporary copy of the Cowork base
profile (nothing is learned into the real one). The microphone is not used.

--conference runs clips 1-3 in Conference mode and reports the time from confirm to first sound.
  --uncached          first moves the demo sentences' ready-made audio aside (put back at the end), so
                      TTSKit has to make it while the "Did you mean...?" card is up
  --confirm-after S   wait S seconds after a card appears before confirming (a person reading it)

Results: eval/results/voice_report.json (or voice_conference.json) and a table on screen.
"""

import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

config.ASR, config.LLM, config.TTS, config.ACTIONS = "real", "real", "real", "fake"

CONFERENCE = "--conference" in sys.argv
UNCACHED = "--uncached" in sys.argv
CONFIRM_AFTER = float(sys.argv[sys.argv.index("--confirm-after") + 1]) if "--confirm-after" in sys.argv else 0.0
NAME = "voice_conference" + ("_uncached" if UNCACHED else "") if CONFERENCE else "voice_report"
OUT = config.ROOT / "eval" / "results" / f"{NAME}.json"


def wait(condition, seconds=30.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def main() -> None:
    from actions.fake import FakeActions
    from actions.screen import Screen
    from asr import get_asr
    from asr.server import ensure_running
    from repair import get_llm
    from repair.store import open_profile
    from tts.real import RealTTS
    from ui.app import App
    from ui.clips import CREDIT, load_clips

    ok, problem = ensure_running()
    if not ok:
        sys.exit(f"WhisperKit isn't running: {problem}")
    work = Path(tempfile.mkdtemp())
    shutil.copyfile(config.ROOT / "profiles" / "cowork_base.json", work / "profile.json")
    profile = open_profile(work / "profile.json", work / "profile.json")
    tts = RealTTS(profile=profile)
    if tts.available():
        sys.exit(f"TTSKit can't run: {tts.available()}")
    actions = FakeActions()
    core = App(profile, get_llm(), tts, asr=get_asr(), actions=actions, profile_path=work / "profile.json",
               warm=True, question_wait_s=120)
    core.set_clips(load_clips(), CREDIT)
    core.session.warm_up()
    if "--trace" in sys.argv:   # print every event (to see what happened)
        core.subscribe(lambda m: m.get("event") and True and
                       print("   event:", m["event"].get("kind"), str({k: v for k, v in m["event"].items() if k in ("text", "meaning", "stage", "kind_of")})[:120]))

    log = []          # every line Cowork tried to say
    section = {"name": ""}
    plain_speak = tts.speak

    def speak(line, wait=False, **kw):
        asked = time.perf_counter()
        spoken = plain_speak(line, wait=wait, **kw)
        last = dict(tts.last)
        log.append({"part": section["name"], "text": last.get("text", ""), "spoken": spoken,
                    "first_sound_ms": last.get("first_sound_ms"), "made_now": last.get("made_now", False),
                    "why": last.get("why", ""), "waited_ms": last.get("waited_ms"),
                    "pieces": last.get("pieces", []), "asked_at": asked,
                    "since_confirm_ms": round((asked - confirmed["t"]) * 1000 + (last.get("first_sound_ms") or 0))
                    if confirmed["t"] else None})
        return spoken

    tts.speak = speak
    confirmed = {"t": None}

    def answer(kind, value=None):
        confirmed["t"] = time.perf_counter()
        core.answer(kind, value)

    def settle(n_before, seconds=40):
        """Answer every question the way the person would (yes), until the request is finished."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            q = core.state["question"]
            if q is None and len(core.state["history"]) > n_before and core.state["status"] == "ready":
                return True
            if q is not None:
                qid = q["id"]
                if CONFIRM_AFTER and q["kind"] != "window":
                    time.sleep(CONFIRM_AFTER)
                if q["kind"] == "window":
                    core.answer("yes")   # the undo window: keep it
                elif q["kind"] == "choice":
                    answer("pick", 0)   # the first option (what the person meant, in these flows)
                else:
                    answer("yes")
                wait(lambda: core.state["question"] is None or core.state["question"]["id"] != qid, 30)
            time.sleep(0.05)
        return False

    def run_clip(clip):
        section["name"] = clip
        n = len(core.state["history"])
        confirmed["t"] = None
        problem = core.play_clip(clip, play_sound=False)
        if problem:
            print("clip problem:", problem)
        settle(n)
        wait(lambda: not core.state.get("speaking"), 20)
        time.sleep(0.5)

    moved = []
    if UNCACHED:   # the demo sentences' audio is moved aside: TTSKit must make it while the card is up
        aside = Path(tempfile.mkdtemp())
        for clip in load_clips()[:3]:
            for text in (clip["said"], clip["said"].rstrip(".") + "."):
                for path in (tts.cached_path(text),):
                    if path.exists():
                        shutil.move(str(path), aside / path.name)
                        moved.append((aside / path.name, path))
        print(f"moved {len(moved)} ready-made sentences aside")
    if CONFERENCE:
        core.set_mode("conference")   # Part 3
        for clip in ("demo1", "demo2", "demo3"):
            run_clip(clip)
    else:
        core.set_dictation(True)
        for clip in ("demo1", "demo2", "demo3"):
            run_clip(clip)
        core.set_dictation(False)

        # The Mail flow (fake Mail: an inbox, then a new email box by box)
        order = ["to", "cc", "subject", "body"]
        inbox = Screen(app="Mail", pid=2, place="inbox", window="Inbox")

        def email(field):
            return Screen(app="Mail", pid=2, place="new email", field=field, typeable=True,
                          focus="the message" if field == "body" else f"the {field.capitalize()} box")

        now = {"screen": Screen(app="Finder", pid=1, place="")}
        actions.screen = lambda: now["screen"]
        actions.contacts = {"Me": "me@example.org"}
        plain_context, plain_run = actions.context, actions.run

        def context():
            found = plain_context()
            found["_screen"] = now["screen"]
            found["field"] = ["To", "Cc", "Subject", "Message"]
            return found

        def run(command, args):
            result = plain_run(command, args)
            current = now["screen"]
            if command == "open_app" and args.get("app") == "Mail":
                now["screen"] = inbox
            elif command == "new_email":
                now["screen"] = email("to")
            elif command in ("next_field", "previous_field") and current.place == "new email":
                i = order.index(current.field) + (1 if command == "next_field" else -1)
                now["screen"] = email(order[max(0, min(3, i))])
            elif command == "quit_app":
                now["screen"] = Screen(app="Finder", pid=1, place="")
            return result

        actions.context, actions.run = context, run
        for text in ("open Mail", "write an email", "me", "next box", "next box", "type hello from cowork",
                     "next box", "type see you at noon", "send the email", "close Mail"):
            section["name"] = f"Mail: {text}"
            n = len(core.state["history"])
            confirmed["t"] = None
            core.say(text)
            settle(n)
            wait(lambda: not core.state.get("speaking"), 20)
            time.sleep(0.3)

    for entry in log:
        entry.pop("asked_at", None)
    for kept, path in moved:   # put the ready-made audio back (unless TTSKit just made a new one)
        if not path.exists():
            shutil.move(str(kept), path)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(log, indent=1))
    print(f"\n{'part':28} {'line':58} {'result':26} first sound")
    for e in log:
        result = ("spoken" + (" (made just now)" if e["made_now"] else "")) if e["spoken"] else f"SILENT ({e['why']})"
        first = f"{e['first_sound_ms']} ms" if e["spoken"] else "-"
        if CONFERENCE and e["since_confirm_ms"] is not None and e["spoken"]:
            first += f"  ({e['since_confirm_ms']} ms after confirm)"
        print(f"{e['part'][:28]:28} {e['text'][:58]:58} {result:26} {first}")
    spoken = [e for e in log if e["spoken"]]
    print(f"\n{len(spoken)} of {len(log)} lines spoken; saved {OUT}")
    sys.stdout.flush()


if __name__ == "__main__":
    threading.excepthook = lambda args: print("thread error:", args.exc_value)
    main()
