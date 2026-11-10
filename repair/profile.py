"""Loads a user's speech profile: their own phrases and commands."""

import json
from pathlib import Path


def load_profile(path) -> dict:
    return json.loads(Path(path).read_text())


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
