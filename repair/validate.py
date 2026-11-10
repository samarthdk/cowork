"""Checks the model's reply. If ANYTHING is wrong (bad JSON, unknown choice,
missing args...), the result becomes low confidence so the user gets to choose."""

import json
import re
from dataclasses import dataclass, field

from repair.profile import all_arg_names, all_choices, decode, find_command


class BadJSON(ValueError):
    pass


@dataclass
class Intent:
    choice: str | None          # a phrase, a command name, or None if unusable
    kind: str | None            # "phrase", "command" or None
    args: dict
    confidence: str             # "high" or "low"
    options: list[str]          # up to 4 choices for the grid
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def parse_json(text: str) -> dict:
    """Read the reply as JSON, ignoring any leftover thinking text or ``` fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise BadJSON(f"reply is not valid JSON: {error}") from None
    if not isinstance(data, dict):
        raise BadJSON("reply is JSON but not an object")
    return data


def validate(data: dict, profile: dict) -> Intent:
    allowed = all_choices(profile)
    problems = []

    # The model answers with codes (P1...) or command names; turn them into the real text.
    choice = decode(profile, data.get("choice"))
    if choice is None:
        problems.append(f"unknown choice: {data.get('choice')!r}")

    raw_args = data.get("args") if isinstance(data.get("args"), dict) else {}
    if not isinstance(data.get("args"), dict):
        problems.append("args missing or not an object")
    args = {name: str(raw_args.get(name, "") or "").strip() for name in all_arg_names(profile)}

    kind = None
    if choice is not None:
        command = find_command(profile, choice)
        kind = "command" if command else "phrase"
        if command:
            for needed in command["args"]:
                if not args.get(needed):
                    problems.append(f"{choice} needs '{needed}' but it is empty")

    confidence = data.get("confidence")
    if confidence not in ("high", "low"):
        problems.append(f"bad confidence value: {confidence!r}")

    raw_options = data.get("options") if isinstance(data.get("options"), list) else []
    options = []
    for raw in raw_options:
        option = decode(profile, raw)
        if option is None:
            problems.append(f"unknown option: {raw!r}")
        elif option not in options:
            options.append(option)

    if problems:
        confidence = "low"
    return Intent(choice, kind, args, confidence, fill_options(choice, options, allowed), problems)


def fill_options(choice, options: list[str], allowed: list[str]) -> list[str]:
    """Best choice first, then the model's options, topped up to 4 from the profile."""
    result = [choice] if choice else []
    for item in options + allowed:
        if item not in result:
            result.append(item)
    return result[:4]


def failed(profile: dict, problem: str) -> Intent:
    """Used when there is no usable reply at all."""
    allowed = all_choices(profile)
    return Intent(None, None, {n: "" for n in all_arg_names(profile)}, "low",
                  fill_options(None, [], allowed), [problem])
