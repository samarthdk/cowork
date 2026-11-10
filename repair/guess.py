"""'Something else': the model's best free-text guess of the sentence meant.

This is the one deliberate exception to "the model only picks from a list"
(see CLAUDE.md). It is only used when the user asks for it, is shown clearly
labeled as a guess, must be confirmed with yes/no, and is only ever a sentence
to speak, never an action on the Mac.
"""

import json

import config
from repair.validate import BadJSON, parse_json


def guess_schema() -> dict:
    return {
        "type": "object",
        "properties": {"guess": {"type": "string", "maxLength": config.MAX_GUESS_CHARS}},
        "required": ["guess"],
        "additionalProperties": False,
    }


def guess_messages(profile: dict, heard: str) -> list[dict]:
    phrases = "\n".join(f"- {p}" for p in profile["phrases"])
    system = f"""You help a person whose speech is impaired. What they said may be garbled,
misheard or incomplete. They did NOT mean any of their usual phrases:
{phrases}

Write your best guess of the sentence they meant, in plain English: ONE short
sentence (under {config.MAX_GUESS_CHARS} characters) that they would say to another person.
Reply with JSON only, on ONE line: {{"guess": "..."}}"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"The person said: \"{heard}\""}]


def clean_guess(data: dict) -> str | None:
    """A usable one-line sentence, or None."""
    text = data.get("guess")
    if not isinstance(text, str):
        return None
    text = " ".join(text.split()).strip().strip('"').strip()
    if not text or len(text) > config.MAX_GUESS_CHARS:
        return None
    return text


def free_guess(heard: str, profile: dict, llm) -> tuple[str | None, str]:
    """Returns (the guessed sentence or None, a problem description or "")."""
    messages, schema = guess_messages(profile, heard), guess_schema()
    problem = ""
    for _ in (1, 2):  # one retry, like every model call
        try:
            reply = llm.complete(messages, schema, purpose="guess")
            if reply.finish_reason == "length":
                return None, "guess was cut off"
            text = clean_guess(parse_json(reply.text))
            if text:
                return text, ""
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
    print(json.dumps(dict(zip(("guess", "problem"), free_guess(heard, open_profile(), get_llm())))))
