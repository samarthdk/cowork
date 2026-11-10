"""Ranking the options shown when the top guess was wrong (or there was none).

The small model reliably ranks only its first pick, so the rest are ranked by
how similar each phrase or command is to what was heard (and to what this
person said before when they meant it). The model's own order breaks ties.
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


def rank_options(heard: str, profile: dict, model_order: list[str],
                 exclude: tuple = ()) -> list[str]:
    """The best SHOWN choices (not excluded), most similar first, then
    'Something else' last."""
    candidates = [c for c in all_choices(profile) if c not in exclude]

    def tie_break(choice):
        return model_order.index(choice) if choice in model_order else len(model_order)

    ranked = sorted(candidates, key=lambda c: (-score(heard, c, profile), tie_break(c)))
    return ranked[:SHOWN] + [SOMETHING_ELSE]
