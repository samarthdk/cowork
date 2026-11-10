"""Type a garbled sentence; see what Second Voice thinks you meant, and hear it.

Run from the project folder:
    .venv/bin/python scripts/demo_text.py
Type q (or press Enter on an empty line) to quit.

When unsure it asks "Did you mean ...?" (y/n). If not, it shows the next-best
options plus "Something else" (the model's free guess, which you confirm).
A confirmed new sentence is added to your phrases (profiles/profile.json).
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from repair import get_llm, repair  # noqa: E402
from repair.guess import free_guess  # noqa: E402
from repair.profile import find_command, phrase_codes  # noqa: E402
from repair.rank import SOMETHING_ELSE, rank_options  # noqa: E402
from repair.store import add_phrase, open_profile, save_profile  # noqa: E402
from tts import get_tts  # noqa: E402


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


def something_else(heard: str, profile: dict, llm) -> str | None:
    """Ask the model for a free guess; return the confirmed sentence or None."""
    start = time.perf_counter()
    guess, problem = free_guess(heard, profile, llm)
    took_ms = (time.perf_counter() - start) * 1000
    if guess is None:
        print(f"Couldn't make a guess ({problem}).")
        return None
    print(f"(guess took {took_ms:.0f} ms)")
    if not ask_yes_no(f'\nMy guess (NOT from your list): "{guess}"\nIs that right?'):
        return None
    phrase, added = add_phrase(profile, guess)
    if added:
        code = next(c for c, p in phrase_codes(profile).items() if p == phrase)
        print(f'New phrase added as {code}: "{phrase}"')
    else:
        print(f'That is already one of your phrases: "{phrase}"')
    return phrase


def decide(heard: str, intent, profile: dict, llm):
    """Work out what the person meant. Returns (choice, args, how) or None.
    how: "confident", "confirmed", "picked" or "new phrase"."""
    if intent.confidence == "high":
        return intent.choice, intent.args, "confident"

    rejected = ()
    if intent.choice:
        if ask_yes_no(did_you_mean(intent.choice, intent.args, profile)):
            return intent.choice, fill_missing_args(intent.choice, intent.args, profile), "confirmed"
        rejected = (intent.choice,)

    # Said no, or no usable choice: next-best options (most similar first) + "Something else".
    options = rank_options(heard, profile, intent.options, exclude=rejected)
    picked = ask_user_to_pick(options)
    if picked is None:
        return None
    if picked == SOMETHING_ELSE:
        phrase = something_else(heard, profile, llm)
        return (phrase, {}, "new phrase") if phrase else None
    args = intent.args if find_command(profile, picked) else {}
    return picked, fill_missing_args(picked, args, profile), "picked"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Second Voice text demo")
    parser.add_argument("--profile", default=str(config.PROFILE_PATH), help=argparse.SUPPRESS)
    options = parser.parse_args(argv)

    profile = open_profile(options.profile)
    llm, tts = get_llm(), get_tts()
    print(f"Second Voice text demo  (LLM: {config.LLM}, TTS: {config.TTS})")
    print("Type what the person said, garbled is fine. q to quit.\n")

    while True:
        try:
            heard = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if heard.lower() in ("", "q", "quit"):
            break

        start = time.perf_counter()
        intent = repair(heard, profile, llm)
        took_ms = (time.perf_counter() - start) * 1000
        print(json.dumps(intent.to_dict(), indent=2))
        print(f"took {took_ms:.0f} ms")

        result = decide(heard, intent, profile, llm)
        if result is None:
            print("Nothing chosen.\n")
            continue
        choice, args, how = result
        save_profile(profile, options.profile)
        tts.speak(sentence_for(choice, args, profile))
        print()


if __name__ == "__main__":
    main()
