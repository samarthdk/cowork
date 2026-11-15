"""'Something else': the model's best free-text guess of the sentence meant.

This is the one deliberate exception to "the model only picks from a list"
(see CLAUDE.md). It is only used when the user asks for it, is shown clearly
labeled as a guess, must be confirmed with yes/no, and is only ever a sentence
to speak, never an action on the Mac.
"""

import json
from dataclasses import dataclass, field

import config
from repair.store import similarity
from repair.validate import BadJSON, parse_json

MAX_GUESSES = 3         # "guess again" up to this many times in total
SAME_GUESS = 90         # a new guess this similar to a rejected one is a repeat
MAX_KEY_WORDS = 3


@dataclass
class Guess:
    text: str
    key_words: list = field(default_factory=list)  # names/things in it, for the vocabulary
    kind: str = "say"                               # "say": a sentence for other people; "question": to answer


def guess_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "guess": {"type": "string", "maxLength": config.MAX_GUESS_CHARS},
            "key_words": {"type": "array", "items": {"type": "string", "maxLength": 30},
                          "maxItems": MAX_KEY_WORDS},
            "kind": {"type": "string", "enum": ["say", "question"]},
        },
        "required": ["guess", "key_words", "kind"],
        "additionalProperties": False,
    }


def guess_messages(profile: dict, heard: str, rejected: list | None = None, screen: str = "") -> list[dict]:
    phrases = "\n".join(f"- {p}" for p in profile["phrases"])
    vocabulary = ", ".join(profile.get("vocabulary", []))
    # Wording tested on the real model: a plain list made it cram every word in
    # ("Anna wants some tea and coffee."); this keeps it to words actually said.
    words = (f"\nNames and words this person often uses: {vocabulary}. Use one ONLY if it "
             "sounds like a word they actually said. Never add words they did not say.\n"
             "Each garbled word is usually a misheard real word: keep their words in the same order.\n"
             if vocabulary else "")
    tried = ""
    if rejected:
        tried = ("\nThese guesses were already REJECTED by the person. Do NOT repeat them "
                 "or say the same thing in other words:\n" + "\n".join(f'- "{g}"' for g in rejected) + "\n")
    system = f"""You help a person whose speech is impaired. What they said may be garbled,
misheard or incomplete. They did NOT mean any of their usual phrases:
{phrases}
{words}{tried}
Write your best guess of the sentence they meant, in plain English: ONE short
sentence (under {config.MAX_GUESS_CHARS} characters), built from the words they actually said.
"kind": "question" if it is a question they are asking (what, who, when, where, how, why...),
otherwise "say" (a sentence for another person).
"key_words": up to {MAX_KEY_WORDS} important words from your sentence (names, drinks,
foods, places, things), or [] if none.
Reply with JSON only, on ONE line: {{"guess": "...", "key_words": [...], "kind": "say"}}"""
    now = ""
    if screen:   # only background: the words heard decide, not what happened to be open before
        now = (f"On their screen now: {screen} (only background: do NOT assume they are still doing "
               "an earlier task; go by the words heard).\n")
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"{now}The person said: \"{heard}\""}]


def clean_guess(data: dict) -> str | None:
    """A usable one-line sentence, or None."""
    text = data.get("guess")
    if not isinstance(text, str):
        return None
    text = " ".join(text.split()).strip().strip('"').strip()
    if not text or len(text) > config.MAX_GUESS_CHARS:
        return None
    return text


def is_repeat(text: str, rejected: list) -> bool:
    return any(similarity(text, old) >= SAME_GUESS for old in rejected)


def free_guess(heard: str, profile: dict, llm, rejected: list | None = None,
               screen: str = "") -> tuple[Guess | None, str]:
    """Returns (the guess or None, a problem description or "").
    Later attempts (after a rejection) use some randomness: at temperature 0 the
    small model tends to give the same answer even when told it was rejected."""
    temperature = config.GUESS_AGAIN_TEMPERATURE if rejected else 0.0
    messages, schema = guess_messages(profile, heard, rejected, screen), guess_schema()
    problem = ""
    for _ in (1, 2):  # one retry, like every model call
        try:
            reply = llm.complete(messages, schema, purpose="guess", temperature=temperature)
            if reply.finish_reason == "length":
                return None, "guess was cut off"
            data = parse_json(reply.text)
            text = clean_guess(data)
            if text:
                words = data.get("key_words") if isinstance(data.get("key_words"), list) else []
                kind = "question" if data.get("kind") == "question" else "say"
                return Guess(text, words[:MAX_KEY_WORDS], kind), ""
            problem = f"unusable guess: {reply.text[:80]!r}"
        except BadJSON as error:
            problem = str(error)
        except Exception as error:  # e.g. LM Studio not running
            problem = f"LLM call failed: {type(error).__name__}: {error}"
    return None, problem


if __name__ == "__main__":
    # Try on its own: .venv/bin/python -m repair.guess "wanna oppen windo"
    import sys
    from repair import get_llm
    from repair.store import open_profile
    heard = " ".join(sys.argv[1:]) or "can yu opn the windo"
    guess, problem = free_guess(heard, open_profile(), get_llm())
    print(json.dumps({"guess": guess.text if guess else None,
                      "key_words": guess.key_words if guess else [], "problem": problem}))
