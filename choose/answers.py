"""One input system for answers.

Whatever the person uses to answer (speech, a hand signal, a head movement,
the keyboard, a click on the screen), it becomes the same small Answer, and
every question in the conversation accepts it:

    question kind   what it asks                         possible answers
    "yes_no"        Did you mean ...? / Is that right?   yes, no
    "choice"        the 2x2 grid of options              pick (which square), none
    "window"        the correction window after acting   no (= cancel), yes (= keep), text (= next sentence)
    "detail"        a missing detail (which app?)        text

When an input does not clearly answer the question, the result is None: the
question simply stays open. When unsure, select nothing.
"""

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from choose import meaning

SQUARES = ("TOP_LEFT", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_RIGHT")   # option 1, 2, 3, 4

# Spoken or typed words. "know" is in the no list on purpose: speech recognizers
# often write a spoken "no" that way, and it is only used while a question is open.
YES_WORDS = ("yes", "y", "yeah", "yep", "yup", "yes please", "correct", "right", "that's right",
             "ok", "okay", "sure", "yes yes")
NO_WORDS = ("no", "n", "nope", "nah", "no no", "know", "wrong", "not that", "no thanks", "cancel")
NUMBER_WORDS = {"1": 0, "one": 0, "first": 0, "2": 1, "two": 1, "second": 1, "to": 1, "too": 1,
                "3": 2, "three": 2, "third": 2, "4": 3, "four": 3, "fourth": 3, "for": 3}
SAY_OPTION_SIMILARITY = 85   # saying an option's own words picks it at this similarity


@dataclass
class Answer:
    kind: str                 # "yes", "no", "pick", "none", "text"
    value: object = None      # pick: option number from 0; text: the words
    source: str = "keyboard"  # "keyboard", "screen", "speech", "hand", "head", "timeout"


def _tidy(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", (text or "").lower()).split())


def yes_or_no(text: str, extra: dict | None = None) -> str | None:
    """'yes', 'no' or None for a word or two. `extra` holds this person's own
    learned ways of saying yes and no: {"yes": [...], "no": [...]}."""
    said = _tidy(text)
    if not said:
        return None
    extra = extra or {}
    is_yes = said in YES_WORDS or said in map(_tidy, extra.get("yes", []))
    is_no = said in NO_WORDS or said in map(_tidy, extra.get("no", []))
    if is_yes == is_no:   # neither, or (after bad learning) both: unsure
        return None
    return "yes" if is_yes else "no"


def from_text(text: str, question, source: str = "keyboard", extra: dict | None = None) -> Answer | None:
    """An answer typed, clicked or spoken as words."""
    said = _tidy(text)
    kind = question.kind
    word = yes_or_no(said, extra)
    if kind == "yes_no":
        return Answer(word, source=source) if word else None
    if kind == "choice":
        if said in NUMBER_WORDS and (source != "speech" or said not in ("to", "too", "for")):
            index = NUMBER_WORDS[said]
            return Answer("pick", index, source) if index < len(question.options) else None
        if word == "no" or said in ("none", "none of these", "nothing"):
            return Answer("none", source=source)
        if said:  # saying (most of) an option's own words picks it
            scores = [fuzz.ratio(said, _tidy(option)) for option in question.options]
            best = max(range(len(scores)), key=scores.__getitem__)
            if scores[best] >= SAY_OPTION_SIMILARITY and sorted(scores)[-2:].count(scores[best]) == 1:
                return Answer("pick", best, source)
        if not said and source != "speech":
            return Answer("none", source=source)
        return None
    if kind == "window":
        if word:
            return Answer(word, source=source)
        if said in ("undo", "undo last"):
            return Answer("no", source=source)
        return Answer("text", text.strip(), source) if said else None
    if kind == "detail":
        return Answer("text", text.strip(), source) if said else None
    return None


def from_signal(signal: str | None, question, source: str = "hand") -> Answer | None:
    """An answer given as a hand (or head) signal: RIGHT, LEFT, RIGHT_UP, ...
    What a signal means depends on the question (see choose/meaning.py)."""
    if not signal:
        return None
    kind = question.kind
    if kind in ("yes_no", "window"):
        word = meaning.yes_no(signal)
        return Answer(word.lower(), source=source) if word else None
    if kind == "choice":
        square = meaning.four_choice(signal)      # a plain RIGHT/LEFT has no square: ask again
        if square is None:
            return None
        index = SQUARES.index(square)
        return Answer("pick", index, source) if index < len(question.options) else None
    return None


def hint(question, signals: bool = True) -> str:
    """One line telling the person how they can answer this question."""
    if question.kind == "yes_no":
        return "YES: raise your RIGHT hand or thumb up.  NO: raise your LEFT hand or thumb down." if signals \
            else "Answer yes or no."
    if question.kind == "choice":
        return "Point a thumb: LEFT hand = left column, RIGHT hand = right column; thumb UP = top row, DOWN = bottom row."
    if question.kind == "window":
        return "To cancel: say no, or raise your LEFT hand."
    return "Say or type it."
