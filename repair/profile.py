"""Loads a user's speech profile: their own phrases and commands."""

import json
from pathlib import Path


def load_profile(path) -> dict:
    return json.loads(Path(path).read_text())


def phrase_codes(profile: dict) -> dict[str, str]:
    """Short code for each phrase, in profile order: {"P1": "I'm thirsty, ...", ...}.
    The model answers with these codes (much shorter, so faster), never free text."""
    return {f"P{i}": phrase for i, phrase in enumerate(profile["phrases"], 1)}


def model_choices(profile: dict) -> list[str]:
    """What the model may answer: the phrase codes, then the command names."""
    return list(phrase_codes(profile)) + [c["name"] for c in profile["commands"]]


def decode(profile: dict, answer) -> str | None:
    """Turn a model answer (a code or a command name) into the real phrase or
    command name. Anything else gives None."""
    codes = phrase_codes(profile)
    if answer in codes:
        return codes[answer]
    if answer in [c["name"] for c in profile["commands"]]:
        return answer
    return None


def all_choices(profile: dict) -> list[str]:
    """Every allowed answer: the phrases, then the command names."""
    return profile["phrases"] + [c["name"] for c in profile["commands"]]


def all_arg_names(profile: dict) -> list[str]:
    names = []
    for command in profile["commands"]:
        names += [a for a in command["args"] if a not in names]
    return names


def find_command(profile: dict, name: str) -> dict | None:
    return next((c for c in profile["commands"] if c["name"] == name), None)


def is_risky(profile: dict, choice: str) -> bool:
    """Risky commands (anything that sends, calls or deletes) are always confirmed
    first. A command without a clear "safe" mark counts as risky. Phrases are
    spoken sentences, not actions, so they are not risky."""
    command = find_command(profile, choice)
    return command is not None and command.get("safety") != "safe"
