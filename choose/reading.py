"""Reading a whole sentence said (or typed) while a question is open.

People answer in sentences, not single words: "No, I meant to say open Microsoft Word."
The rules, in order:

  1. "No / nope / not that" + more words: No for the current guess (nothing is learned from it);
     the rest ("open Microsoft Word") is handled as a fresh request.
  2. "Yes / yeah / right" + more words ("Yes, then open Mail"): confirm, then the rest is the next request.
  3. Words that clearly match one of the options shown ("the first one", "number two", "the last one",
     or the option's own words): that option.
  4. A clear new request with no yes/no and no option match: the question is dropped quietly and the
     request is handled.
  5. Otherwise None: the question stays open (and the screen shows how to answer).

    reading = understand("No, I meant to say open Microsoft Word.", question, shown=[...], profile=profile)
    reading.answer  -> Answer("none")      given to the open question
    reading.then    -> "open Microsoft Word"   handled next, as a fresh request
    reading.drop    -> True                    any further question in this request is dropped too
"""

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from choose.answers import NO_WORDS, YES_WORDS, Answer, _tidy, from_text

# Words that mean yes/no even when more words follow. "right", "ok", "sure", "correct" only count
# when followed by a comma or full stop ("Right, open Mail"), never in "right click the button".
STRONG_YES = ("yes", "yeah", "yep", "yup", "yes please", "that's right", "thats right", "exactly")
WEAK_YES = ("right", "ok", "okay", "sure", "correct")
STRONG_NO = ("no", "nope", "nah", "not that", "no no", "no thanks", "wrong", "not that one")
FILLERS = ("i meant to say", "i meant to", "i meant", "i mean", "i said", "i want to", "i want", "i wanted to",
           "i wanted", "instead", "actually", "please", "then", "and then", "and", "but", "so", "just", "rather",
           "can you", "could you", "would you")
ORDINALS = {"first": 0, "1st": 0, "one": 0, "1": 0, "second": 1, "2nd": 1, "two": 1, "2": 1,
            "third": 2, "3rd": 2, "three": 2, "3": 2, "fourth": 3, "4th": 3, "four": 3, "4": 3}
REQUEST_WORDS = ("open", "close", "quit", "write", "compose", "start", "new", "type", "search", "go", "scroll",
                 "read", "reply", "forward", "send", "show", "save", "print", "delete", "archive", "mark", "select",
                 "make", "play", "call", "find", "look", "create", "put", "press", "hit", "copy", "paste", "bold",
                 "what", "what's", "whats", "how", "who", "where", "when", "why", "which", "is", "are", "can",
                 "could", "will", "do", "does", "tell", "say", "let")
OPTION_MATCH = 88       # an option's own words, said (most of them)
REQUEST_MATCH = 85      # a phrase or command's words, said


@dataclass
class Reading:
    answer: Answer
    then: str = ""       # a request to handle after this question (or instead of it)
    drop: bool = False   # drop any further question in this request (the person moved on)


def _strip_fillers(said: str) -> str:
    changed = True
    while changed and said:
        changed = False
        for filler in FILLERS:
            if said == filler:
                return ""
            if said.startswith(filler + " "):
                said, changed = said[len(filler) + 1:], True
    return said


def _lead(text: str) -> tuple[str | None, str]:
    """('yes' | 'no' | None, the words after it) for a sentence that starts with yes or no."""
    raw = (text or "").strip().lower()
    said = _tidy(raw)
    for kind, words, need_mark in (("no", STRONG_NO, False), ("yes", STRONG_YES, False), ("yes", WEAK_YES, True)):
        for word in sorted(words, key=len, reverse=True):
            if said == word:
                return kind, ""
            if said.startswith(word + " "):
                if need_mark and not re.match(re.escape(word) + r"\s*[,.!;:]", raw):
                    continue
                return kind, _strip_fillers(said[len(word) + 1:])
    return None, said


def _option(said: str, shown: list[str]) -> int | None:
    """Which option these words name, or None. Ordinals ("the second one", "number two",
    "the last one") or the option's own words (clearly, and only one option)."""
    if not said or not shown:
        return None
    words = said.split()
    real = [i for i, text in enumerate(shown) if not _tidy(text).startswith("something else")]
    trimmed = [w for w in words if w not in ("the", "one", "number", "option", "choice", "please", "that", "pick", "choose")]
    if trimmed in (["last"], ["final"]) and real:
        return real[-1]
    if len(trimmed) == 1 and trimmed[0] in ORDINALS:
        index = ORDINALS[trimmed[0]]
        return index if index < len(shown) else None
    scores = [max(fuzz.token_set_ratio(said, _tidy(text)), fuzz.ratio(said, _tidy(text))) if _tidy(text) else 0
              for text in shown]
    best = max(range(len(scores)), key=scores.__getitem__)
    if scores[best] >= OPTION_MATCH and sorted(scores)[-2:].count(scores[best]) == 1 and len(_tidy(shown[best]).split()) >= 1:
        return best
    return None


def looks_like_request(said: str, profile: dict | None) -> bool:
    """A clear request on its own: starts with a request word (open, write, what...) and has
    at least two words, or closely matches one of the person's phrases or commands."""
    words = said.split()
    if len(words) >= 2 and words[0] in REQUEST_WORDS:
        return True
    if profile and len(words) >= 2:
        known = list(profile.get("phrases", []))
        for command in profile.get("commands", []):
            known += list(command.get("said_as", [])) + [command.get("request", "").split("{")[0]]
        return any(fuzz.ratio(said, _tidy(k)) >= REQUEST_MATCH for k in known if _tidy(k))
    return False


def understand(text: str, question, shown: list[str] | None = None, body: str = "",
               profile: dict | None = None, source: str = "speech") -> Reading | None:
    """How a sentence answers the open question (see the rules above), or None to keep it open.
    shown: the option texts as on screen (choice); body: what "Did you mean ...?" asks about (yes/no)."""
    said = _tidy(text)
    if not said:
        return None
    kind = question.kind
    shown = list(shown or [])
    lead, rest = _lead(text)

    if kind == "detail":   # a missing detail: the words are the value
        plain = from_text(text, question, source=source)
        return Reading(plain) if plain else None

    if lead is not None and rest:
        if kind == "choice":
            index = _option(rest, shown)
            if index is not None:             # "no, the second one" / "yes, number two": that option
                return Reading(Answer("pick", index, source))
        if kind == "yes_no" and body and lead == "yes" and fuzz.token_set_ratio(rest, _tidy(body)) >= OPTION_MATCH:
            return Reading(Answer("yes", source=source))   # "yes, open Mail" while asking about "Open Mail"
        if lead == "no":
            if kind == "window":              # undo what was just done, then the new request
                return Reading(Answer("no", source=source), then=rest, drop=True)
            return Reading(Answer("none", source=source), then=rest, drop=True)
        # lead == "yes": confirm, then the rest is the next request
        if kind == "choice":
            return None                       # "yes" alone does not say which option
        return Reading(Answer("yes", source=source), then=rest)

    plain = from_text(text, question, source=source)
    if plain is not None and not (kind == "window" and plain.kind == "text"):
        return Reading(plain)

    if kind == "choice":
        index = _option(_strip_fillers(said), shown)
        if index is not None:
            return Reading(Answer("pick", index, source))
    if kind == "yes_no" and body and fuzz.ratio(_strip_fillers(said), _tidy(body)) >= OPTION_MATCH:
        return Reading(Answer("yes", source=source))      # repeating the guess itself means yes

    request = _strip_fillers(said)
    if kind == "window":   # after an action: anything else is the next request (what was done is kept)
        return Reading(Answer("yes", source=source), then=text.strip()) if request else None
    if looks_like_request(request, profile):
        return Reading(Answer("none", source=source), then=text.strip(), drop=True)
    return None
