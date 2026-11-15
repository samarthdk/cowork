"""Which commands fit what is on screen, and command vs. typing vs. question.

    hide_for(screen, "what's the weather in Boston", dictation=True)  -> ("type_text", ...)
    remap("go_back", screen)   -> "previous_field" while an email is open ("back" = back a box)

The Brain still chooses; these rules only take away choices that cannot be
right just now (the schema leaves them out, see repair/prompt.py not_now):

  - typing only when the person says "type ..."/"write ..." with the words, or
    when dictation is on and a place for words has the cursor; never for a
    question ("what's the weather in Boston?" is answered, never typed);
  - "next box", "go to subject" and "send" only while an email is open;
  - "go back"/"go forward" (browser pages) not while an email is open.
"""

import re

from rapidfuzz import fuzz

TYPE_WORDS = ("type", "write", "dictate")
QUESTION_WORDS = {"what", "whats", "what's", "who", "whos", "who's", "when", "where", "wheres", "where's", "how",
                  "hows", "how's", "why", "which", "whose", "is", "are", "will", "would", "does", "do", "did",
                  "can", "could", "should", "tell"}
EMAIL_WORDS = ("an email", "a email", "email", "a new email", "an e-mail", "a message", "a new message", "mail",
               "a mail", "an mail", "e-mail")


def words_of(text: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z' ]", " ", (text or "").lower()).split() if w]


def says_type(text: str) -> bool:
    """'type see you at noon', 'write hello there' (but not 'write an email': that is a command)."""
    words = words_of(text)
    if len(words) < 2 or max(fuzz.ratio(words[0], w) for w in TYPE_WORDS) < 85:
        return False
    rest = " ".join(words[1:])
    return not any(rest == e or rest.startswith(e + " ") for e in EMAIL_WORDS)


def is_question(text: str) -> bool:
    words = words_of(text)
    if not words:
        return False
    if (text or "").strip().endswith("?"):
        return True
    if words[0] == "can" and len(words) > 2 and words[1] == "you" and words[2] in ("open", "close", "type", "write",
                                                                                  "send", "start", "make", "save"):
        return False   # "can you open Mail" is a request, not a question
    return words[0] in QUESTION_WORDS


def hide_for(screen, heard: str, dictation: bool) -> tuple:
    """Commands that can't be meant right now."""
    hidden = []
    in_email = screen is not None and screen.place == "new email"
    typing_ok = says_type(heard) or (dictation and screen is not None and screen.typeable and not is_question(heard))
    if not typing_ok or (is_question(heard) and not says_type(heard)):
        hidden.append("type_text")
    if not in_email:
        hidden += ["go_to_field", "send_email"]
        if not (screen is not None and screen.typeable and screen.place != "document" and screen.field != "address"):
            hidden += ["next_field", "previous_field"]
    else:
        hidden += ["go_back", "go_forward"]
    return tuple(dict.fromkeys(hidden))


def remap(command: str, screen) -> str:
    """The same word means something else on this screen: "back" in an email is back a box."""
    if screen is not None and screen.place == "new email":
        return {"go_back": "previous_field", "go_forward": "next_field"}.get(command, command)
    return command
