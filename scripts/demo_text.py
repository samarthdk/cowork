"""Type a garbled sentence; see what Second Voice thinks you meant, and hear it.

Run from the project folder:
    .venv/bin/python scripts/demo_text.py
    .venv/bin/python scripts/demo_text.py --fresh   (start learning from scratch)
Type q (or press Enter on an empty line) to quit, or undo to forget the last
thing learned.

When unsure it asks "Did you mean ...?" (y/n). If not, it shows the next-best
options plus "Something else" (the model's free guess, which you confirm).
A confirmed new sentence is added to your phrases (profiles/profile.json).

Learning: every confirmed answer is saved as an example. Saying something very
close to a saved example again answers instantly ("FAST PATH"), without the LLM.
--fresh starts from a clean copy of the base profile (the old one is backed up).

This is the keyboard-only front-end. The conversation itself lives in
engine/session.py and is shared with the full app (ui/), where the same
questions can be answered by voice or hand signals.
"""

import argparse
import json
import select
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from choose.answers import Answer  # noqa: E402
from engine.session import Session  # noqa: E402
from repair import get_llm  # noqa: E402
from repair.store import fresh_profile, open_profile  # noqa: E402
from tts import get_tts  # noqa: E402

YES_WORDS = ("y", "yes")
NO_WORDS = ("n", "no")
UNDO_WORDS = ("undo", "undo last")


def show(event: dict) -> None:
    """Print what the conversation engine reports, in the terminal's wording."""
    kind = event["kind"]
    if kind == "fast_path":
        print(f"FAST PATH: matched a learned example ({event['similarity']:.0f}% similar) "
              f"in {event['latency_ms']:.1f} ms")
        print(f"  learned earlier: '{event['example_heard']}' -> '{event['meaning']}'  (LLM not called)")
    elif kind == "brain":
        print(json.dumps(event["intent"], indent=2))
        print(f"took {event['took_ms']:.0f} ms (LLM)")
    elif kind == "risky_notice":
        print("(risky action: always confirmed first)")
    elif kind == "guess_failed":
        print(f"Couldn't make a guess ({event['problem']}).")
    elif kind == "guess_repeat":
        print(f'(guess {event["attempt"]} repeated a rejected one: "{event["text"]}" - skipped)')
    elif kind == "guess_ready":
        print(f"(guess {event['attempt']} of {event['total']} took {event['took_ms']:.0f} ms)")
    elif kind == "new_phrase":
        print(f'New phrase added as {event["code"]}: "{event["phrase"]}"')
    elif kind == "known_phrase":
        print(f'That is already one of your phrases: "{event["phrase"]}"')
    elif kind == "new_words":
        print(f"New words for your vocabulary: {', '.join(event['words'])}")
    elif kind == "no_more_guesses":
        print(f"No more guesses ({event['total']} tried).")
    elif kind == "learned":
        print(f"Learned: '{event['heard']}' -> '{event['meaning']}'")
    elif kind == "risky_done":
        print("  (risky action: it was confirmed first, and can't be cancelled afterwards)")
    elif kind == "cancelled":
        print(f"CANCELLED: '{event['meaning']}'")
    elif kind == "forgot":
        print(f"FORGOT the learned example that caused it: '{event['heard']}' -> '{event['meaning']}'")
    elif kind == "undone":
        print(f"UNDONE: forgot '{event['heard']}' -> '{event['meaning']}'")
        if event["phrase"]:
            print(f"  (and removed the phrase it added: '{event['phrase']}')")
        if event["words"]:
            print(f"  (and removed from the vocabulary: {', '.join(event['words'])})")
    elif kind == "nothing_chosen":
        print("Nothing chosen.")
    elif kind == "nothing_to_undo":
        print("Nothing to undo.\n")
    elif kind == "acted" and not event["ok"]:
        print(f"  (couldn't do that: {event['problem']})")
    elif kind == "problem":
        print(f"  ({event['text']})")


def ask_yes_no(question: str) -> bool:
    """Wait for y or n on the keyboard."""
    while True:
        try:
            answer = input(f"{question} (y/n) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer in YES_WORDS:
            return True
        if answer in NO_WORDS:
            return False
        print("Please type y or n.")


def ask_user_to_pick(options: list[str]) -> int | None:
    """The terminal's stand-in for the 2x2 choice grid."""
    print("\nWhich one did you mean?")
    for number, option in enumerate(options, 1):
        print(f"  {number}. {option}")
    picked = input("Pick a number (or Enter for none): ").strip()
    if picked.isdigit() and 1 <= int(picked) <= len(options):
        return int(picked) - 1
    return None


def wait_for_cancel(seconds: float) -> str | None:
    """Correction window: wait up to `seconds` for a typed line. Returns the
    line, or None if nothing was typed."""
    print(f"  (type n + Enter within {seconds:.0f} s to cancel)")
    if not sys.stdin.isatty():  # input from a script or file: just read the next line
        line = sys.stdin.readline()
        return line.strip() if line else None
    try:
        ready, _, _ = select.select([sys.stdin], [], [], seconds)
    except (ValueError, OSError):
        return None
    return sys.stdin.readline().strip() if ready else None


def answer_from_keyboard(question) -> Answer | None:
    """Ask one of the engine's questions on the keyboard."""
    if question.kind == "yes_no":
        prompt = f"\n{question.text}" + ("\nIs that right?" if question.stage == "guess" else "")
        return Answer("yes" if ask_yes_no(prompt) else "no")
    if question.kind == "choice":
        index = ask_user_to_pick(question.options)
        return Answer("none") if index is None else Answer("pick", index)
    if question.kind == "detail":
        return Answer("text", input(f"{question.text} ").strip())
    if question.kind == "window":
        line = wait_for_cancel(question.seconds)
        if line is None or line.lower() in ("",) + YES_WORDS:
            return None
        if line.lower() in NO_WORDS + UNDO_WORDS:
            return Answer("no")
        return Answer("text", line)
    return None


def run_turn(session: Session, heard: str) -> str | None:
    """One thing the person said, start to finish. Returns a sentence typed
    during the correction window (to handle next), or None."""
    flow = session.hear(heard)
    try:
        question = next(flow)
        while True:
            question = flow.send(answer_from_keyboard(question))
    except StopIteration as finished:
        return finished.value


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Second Voice text demo")
    parser.add_argument("--fresh", action="store_true",
                        help="start from a clean copy of the base profile (old one backed up)")
    parser.add_argument("--profile", default=str(config.PROFILE_PATH), help=argparse.SUPPRESS)
    options = parser.parse_args(argv)

    if options.fresh:
        backup = fresh_profile(options.profile, backup_dir=Path(options.profile).parent / "backups")
        print("FRESH START: learned examples and added phrases cleared.")
        if backup:
            print(f"  (previous profile backed up to {backup})")
    profile = open_profile(options.profile)
    learned_count = len(profile["examples"])
    seen = []

    def on_event(event):
        seen.append(event["kind"])
        show(event)

    session = Session(profile, get_llm(), get_tts(), profile_path=options.profile, on_event=on_event)
    print(f"Second Voice text demo  (LLM: {config.LLM}, TTS: {config.TTS})")
    print(f"Learned examples so far: {learned_count}")
    print("Type what the person said, garbled is fine. undo = forget the last thing learned, q = quit.\n")

    pending = None  # a sentence typed during the correction window
    while True:
        if pending is not None:
            heard, pending = pending, None
            print(f"> {heard}")
        else:
            try:
                heard = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
        if heard.lower() in ("", "q", "quit"):
            break
        if heard.lower() in YES_WORDS + NO_WORDS:
            print(f"('{heard}' is an answer, not a sentence. Nothing is waiting for an answer right now.)\n")
            continue
        if heard.lower() in UNDO_WORDS:
            seen.clear()
            session.undo()
            if "undone" in seen:
                print()
            continue
        pending = run_turn(session, heard)
        print()


if __name__ == "__main__":
    main()
