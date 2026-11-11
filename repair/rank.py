"""Ranking the options shown when the top guess was wrong (or there was none).

The small model reliably ranks only its first pick, so the rest are ranked by
how similar each phrase or command is to what was heard (and to what this
person said before when they meant it). The model's own order breaks ties.

Phrases and commands are ranked as two separate groups (their similarity
scores aren't comparable), and the group of the model's top guess comes first:
if it guessed a phrase, other phrases are the likeliest alternatives.
"""

from rapidfuzz import fuzz

from repair.profile import all_choices, find_command
from repair.store import normalize

SOMETHING_ELSE = "Something else"
SHOWN = 3  # plus "Something else" = 4 squares, like the 2x2 grid


def _texts_for(choice: str, profile: dict) -> list[str]:
    """What a choice 'sounds like': its own words, plus learned examples of it."""
    command = find_command(profile, choice)
    texts = [choice.replace("_", " ") if command else choice]
    texts += [e["heard"] for e in profile.get("examples", []) if e["meant"] == choice]
    return texts


def score(heard: str, choice: str, profile: dict) -> float:
    heard = normalize(heard)
    return max(fuzz.WRatio(heard, normalize(t)) for t in _texts_for(choice, profile))


def group_of(choice: str | None, profile: dict) -> str | None:
    if choice is None:
        return None
    return "command" if find_command(profile, choice) else "phrase"


def rank_options(heard: str, profile: dict, model_order: list[str],
                 exclude: tuple = (), first_group: str | None = None, first: str | None = None) -> list[str]:
    """The best SHOWN choices (not excluded), then 'Something else' last.
    first_group ("phrase" or "command"): the group of the model's top guess;
    if not given, the group of the model's first option, else phrases.
    first: a choice to put in the first square whatever its similarity (the
    Brain's second opinion after a "no")."""
    first_group = first_group or group_of(model_order[0] if model_order else None, profile) or "phrase"

    def tie_break(choice):
        return model_order.index(choice) if choice in model_order else len(model_order)

    def ranked(group):
        members = [c for c in all_choices(profile) if c not in exclude and group_of(c, profile) == group]
        return sorted(members, key=lambda c: (-score(heard, c, profile), tie_break(c)))

    second_group = "command" if first_group == "phrase" else "phrase"
    order = ranked(first_group) + ranked(second_group)
    if first in order:
        order.remove(first)
        order.insert(0, first)
    return order[:SHOWN] + [SOMETHING_ELSE]
