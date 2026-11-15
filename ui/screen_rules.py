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
    if says_type(heard):   # "type how are you": words to type, never a question to answer
        hidden.append("answer_question")
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


FILLER_SOUNDS = {"mm", "mmm", "hm", "hmm", "uh", "um", "umm", "uhm", "ah", "er", "erm", "eh", "huh", "oh", "ehm"}
CANNOT = [   # (pattern, what Cowork says): things it never attempts, said kindly instead of asking about them
    (r"\b(wi ?fi|wireless|bluetooth|shut ?down|restart|reboot|log ?out|sign out of the mac|turn off the (computer|mac)|"
     r"system settings|settings app|install|uninstall|update (the )?(mac|macos|system))\b",
     "I can't change the Mac's settings or install things. That is done in System Settings."),
    (r"\b(delete|erase|wipe|remove|format|trash)\b.*\b(all my|everything|files|folders?|disk|drive|my computer|the mac)\b",
     "I won't delete files or erase things. I only work in the app in front of you, and I ask before anything that "
     "can't be undone."),
    (r"^(please |can you |could you )?(book|buy|order|purchase|pay|transfer|send money)\b",
     "I can't book or buy things: I work offline, without the internet."),
    (r"\bignore (your|all|the|previous|my) (instructions|rules)\b",
     "I can't do that. I only do what you ask for, from my list of actions, and I check first."),
]


def mumble(text: str) -> bool:
    """Only sounds like "mm", "uh": nothing to understand (no guess is offered)."""
    words = words_of(text)
    return bool(words) and all(w in FILLER_SOUNDS for w in words)


def cannot_do(text: str) -> str:
    """'' or a kind sentence saying why Cowork won't attempt this (system settings, deleting files, buying...)."""
    said = " ".join(words_of(text))
    for pattern, reply in CANNOT:
        if re.search(pattern, said):
            return reply
    return ""
