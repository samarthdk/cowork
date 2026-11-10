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


def ask_user_to_pick(options: list[str]) -> str | None:
    """Stand-in for the 2x2 choice grid (built later in choose/)."""
    print("\nNot sure. Did you mean:")
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

        choice = intent.choice if intent.confidence == "high" else ask_user_to_pick(intent.options)
        if choice is None:
            print("Nothing chosen.\n")
            continue
        if find_command(profile, choice) and choice != intent.choice:
            # Picked a command from the list; its details (e.g. app name) may be missing.
            missing = [a for a in find_command(profile, choice)["args"] if not intent.args.get(a)]
            for name in missing:
                intent.args[name] = input(f"{name}? ").strip()
        tts.speak(sentence_for(choice, intent.args, profile))
        print()


if __name__ == "__main__":
    main()
