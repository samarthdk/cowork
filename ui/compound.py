"""One sentence with a command and words to type: "open Notes and type see you at noon".

    split("open notes and type see you at noon")
      -> ("open notes", "see you at noon", True)     # the command first, then type the rest
    split("open notes and tipe see you at noon")
      -> ("open notes", "see you at noon", False)    # a near match: ask "Did you mean ...?" first
    split("I need to use the bathroom")  -> None

Only the joining words decide the split ("and type", "then write", ...). What the
first part means is still worked out by the Brain, as for any request.
"""

import re

from rapidfuzz import fuzz

JOINS = ("and", "then", "and then")
TYPE_WORDS = ("type", "write", "dictate")
NEAR = 75   # a joining verb this similar (but not exact) is only a guess: ask first
SOUND_ALIKE = ("right", "rite", "wright", "tape", "tight", "types", "typed", "writes")   # also only a guess


def split(text: str):
    words = (text or "").split()
    tidy = [re.sub(r"[^a-z']", "", w.lower()) for w in words]
    for i in range(1, len(words) - 2):
        if tidy[i] not in ("and", "then"):
            continue
        j = i + 1
        if tidy[i] == "and" and tidy[j] == "then":
            j += 1
        if j >= len(words) - 1:
            continue
        verb = tidy[j]
        best = max(fuzz.ratio(verb, w) for w in TYPE_WORDS)
        if best < NEAR and verb not in SOUND_ALIKE:
            continue
        first, rest = " ".join(words[:i]).strip(" ,."), " ".join(words[j + 1:]).strip()
        if first and rest:
            return first, rest, verb in TYPE_WORDS
    return None
