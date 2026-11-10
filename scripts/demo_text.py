"""Type a garbled sentence; see what Second Voice thinks you meant, and hear it.

Run from the project folder:
    .venv/bin/python scripts/demo_text.py
Type q (or press Enter on an empty line) to quit.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from repair import get_llm, repair  # noqa: E402
from repair.profile import find_command, load_profile  # noqa: E402
from tts import get_tts  # noqa: E402


def sentence_for(choice: str, args: dict, profile: dict) -> str:
    """What to say out loud: the phrase itself, or a short confirmation for a command."""
    command = find_command(profile, choice)
    if command is None:
        return choice
    return command["say"].format(**args)


def describe(choice: str, args: dict, profile: dict) -> str:
    """How to show a choice in a question: the phrase itself, or the command and its details."""
    if find_command(profile, choice) is None:
        return choice
    details = ", ".join(f"{k}: {v}" for k, v in args.items() if v)
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


def main() -> None:
    profile = load_profile(config.PROFILE_PATH)
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

        if intent.confidence == "high":
            choice = intent.choice
        elif intent.choice and ask_yes_no(did_you_mean(intent.choice, intent.args, profile)):
            choice = intent.choice  # confirmed: act on it as if confidence were high
        else:
            # Said no, or the model had no usable choice: show the options, best first.
            choice = ask_user_to_pick(intent.options)
        if choice is None:
            print("Nothing chosen.\n")
            continue
        command = find_command(profile, choice)
        if command:
            # A command picked or confirmed may still be missing details (e.g. which app).
            for name in command["args"]:
                if not intent.args.get(name):
                    intent.args[name] = input(f"{name}? ").strip()
        tts.speak(sentence_for(choice, intent.args, profile))
        print()


if __name__ == "__main__":
    main()
