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
"""

import argparse
import json
import select
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from repair import get_llm, repair  # noqa: E402
from repair.guess import free_guess  # noqa: E402
from repair.profile import find_command, phrase_codes  # noqa: E402
from repair.rank import SOMETHING_ELSE, rank_options  # noqa: E402
from repair.learning import fast_path  # noqa: E402
from repair.store import (add_example, add_phrase, fresh_profile, open_profile,  # noqa: E402
                          save_profile, undo_last)
from tts import get_tts  # noqa: E402


CORRECTION_S = 3.0  # after acting, this long to say "no" and cancel
YES_WORDS = ("y", "yes")
NO_WORDS = ("n", "no")
UNDO_WORDS = ("undo", "undo last")


def sentence_for(choice: str, args: dict, profile: dict) -> str:
    """What to say out loud: the phrase itself, or a short confirmation for a command."""
    command = find_command(profile, choice)
    if command is None:
        return choice
    return command["say"].format(**args)


def describe(choice: str, args: dict, profile: dict) -> str:
    """How to show a choice: the phrase itself, or the command and its details."""
    command = find_command(profile, choice)
    if command is None:
        return choice
    details = ", ".join(f"{k}: {v}" for k, v in args.items() if v and k in command["args"])
    return f"{choice} ({details})" if details else choice


def did_you_mean(choice: str, args: dict, profile: dict) -> str:
    text = describe(choice, args, profile)
    if text.endswith("?"):
        return f"\nDid you mean: {text}"
    return f"\nDid you mean: {text.rstrip('.!')}?"


def ask_yes_no(question: str) -> bool:
    """Wait for yes or no. Keyboard (y/n) for now; later the yes/no hand gesture."""
    while True:
        try:
            answer = input(f"{question} (y/n) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please type y or n.")


def ask_user_to_pick(options: list[str]) -> str | None:
    """Stand-in for the 2x2 choice grid (built later in choose/)."""
    print("\nWhich one did you mean?")
    for number, option in enumerate(options, 1):
        print(f"  {number}. {option}")
    picked = input("Pick a number (or Enter for none): ").strip()
    if picked.isdigit() and 1 <= int(picked) <= len(options):
        return options[int(picked) - 1]
    return None


def fill_missing_args(choice: str, args: dict, profile: dict) -> dict:
    """A command may still be missing details (e.g. which app): ask for them."""
    args = dict(args)
    command = find_command(profile, choice)
    if command:
        for name in command["args"]:
            if not args.get(name):
                args[name] = input(f"{name}? ").strip()
    return args


def something_else(heard: str, profile: dict, llm) -> tuple[str | None, bool]:
    """Ask the model for a free guess. Returns (the confirmed sentence or None,
    whether it was added as a new phrase)."""
    start = time.perf_counter()
    guess, problem = free_guess(heard, profile, llm)
    took_ms = (time.perf_counter() - start) * 1000
    if guess is None:
        print(f"Couldn't make a guess ({problem}).")
        return None, False
    print(f"(guess took {took_ms:.0f} ms)")
    if not ask_yes_no(f'\nMy guess (NOT from your list): "{guess}"\nIs that right?'):
        return None, False
    phrase, added = add_phrase(profile, guess)
    if added:
        code = next(c for c, p in phrase_codes(profile).items() if p == phrase)
        print(f'New phrase added as {code}: "{phrase}"')
    else:
        print(f'That is already one of your phrases: "{phrase}"')
    return phrase, added


def wait_for_cancel(seconds: float) -> str | None:
    """Correction window: wait up to `seconds` for a typed line (later: voice or
    the left-hand gesture). Returns the line, or None if nothing was typed."""
    print(f"  (type n + Enter within {seconds:.0f} s to cancel)")
    if not sys.stdin.isatty():  # input from a script or file: just read the next line
        line = sys.stdin.readline()
        return line.strip() if line else None
    try:
        ready, _, _ = select.select([sys.stdin], [], [], seconds)
    except (ValueError, OSError):
        return None
    return sys.stdin.readline().strip() if ready else None


def decide(heard: str, intent, profile: dict, llm):
    """Work out what the person meant. Returns (choice, args, how) or None.
    how: "confident" (not learned from), "confirmed", "picked" or "new phrase"."""
    if intent.confidence == "high":
        return intent.choice, intent.args, "confident"

    rejected = ()
    if intent.choice:
        if ask_yes_no(did_you_mean(intent.choice, intent.args, profile)):
            return intent.choice, fill_missing_args(intent.choice, intent.args, profile), "confirmed"
        rejected = (intent.choice,)
    return choose_from_options(heard, intent, profile, llm, rejected)


def choose_from_options(heard: str, intent, profile: dict, llm, rejected: tuple):
    """Next-best options (most similar first, rejected ones left out) plus
    "Something else". Returns (choice, args, how) or None."""
    options = rank_options(heard, profile, intent.options, exclude=rejected)
    picked = ask_user_to_pick(options)
    if picked is None:
        return None
    if picked == SOMETHING_ELSE:
        phrase, added = something_else(heard, profile, llm)
        if phrase is None:
            return None
        return phrase, {}, ("new phrase" if added else "picked")
    args = intent.args if find_command(profile, picked) else {}
    return picked, fill_missing_args(picked, args, profile), "picked"


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
    llm, tts = get_llm(), get_tts()
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
            removed, phrase_removed = undo_last(profile)
            if removed is None:
                print("Nothing to undo.\n")
                continue
            save_profile(profile, options.profile)
            meaning = describe(removed["meant"], removed["args"], profile)
            print(f"UNDONE: forgot '{removed['heard']}' -> '{meaning}'")
            if phrase_removed:
                print(f"  (and removed the phrase it added: '{removed['meant']}')")
            print()
            continue

        match = fast_path(heard, profile)
        if match:
            intent = match.intent
            print(f"FAST PATH: matched a learned example ({match.similarity:.0f}% similar) "
                  f"in {match.latency_ms:.1f} ms")
            print(f"  learned earlier: '{match.example['heard']}' -> "
                  f"'{describe(intent.choice, intent.args, profile)}'  (LLM not called)")
        else:
            start = time.perf_counter()
            intent = repair(heard, profile, llm)
            took_ms = (time.perf_counter() - start) * 1000
            print(json.dumps(intent.to_dict(), indent=2))
            print(f"took {took_ms:.0f} ms (LLM)")

        result = decide(heard, intent, profile, llm)
        rejected = ()
        while result is not None:
            choice, args, how = result
            learned = how != "confident"  # only confirmed answers are learned from
            if learned:
                add_example(profile, heard, choice, args, new_phrase=(how == "new phrase"))
                print(f"Learned: '{heard}' -> '{describe(choice, args, profile)}'")
            save_profile(profile, options.profile)
            tts.speak(sentence_for(choice, args, profile))

            # Correction window: "no" cancels, undoes what was learned, shows the options.
            line = wait_for_cancel(CORRECTION_S)
            if line is None or line.lower() in ("",) + YES_WORDS:
                break
            if line.lower() not in NO_WORDS + UNDO_WORDS:
                pending = line  # not a correction: it's the next sentence
                break
            print(f"CANCELLED: '{describe(choice, args, profile)}'")
            if learned:
                removed, phrase_removed = undo_last(profile)
                print(f"UNDONE: forgot '{removed['heard']}' -> '{describe(choice, args, profile)}'")
                if phrase_removed:
                    print(f"  (and removed the phrase it added: '{removed['meant']}')")
                save_profile(profile, options.profile)
            rejected += (choice,)
            result = choose_from_options(heard, intent, profile, llm, rejected)
        else:
            print("Nothing chosen.")
        print()


if __name__ == "__main__":
    main()
