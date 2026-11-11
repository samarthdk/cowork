"""Which sentences the voice should have ready in advance."""

from repair.profile import find_command

FIXED = ["Sorry, I couldn't do that.", "Your reply is ready in Mail. It has not been sent."]


def sentences_to_prepare(profile: dict) -> list[str]:
    """Every phrase, plus the spoken confirmation of each command that has no
    details to fill in (e.g. "Scrolling down.")."""
    texts = list(profile["phrases"])
    texts += [c["say"] for c in profile["commands"] if "{" not in c["say"]]
    return texts + FIXED
