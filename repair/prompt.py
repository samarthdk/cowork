"""Builds the instructions and the strict JSON schema sent to the model."""

from repair.profile import all_arg_names, model_choices, phrase_codes


def build_schema(profile: dict) -> dict:
    """The reply shape. `choice` and every option may ONLY be a phrase code
    (P1, P2, ...) or a command name, so the model cannot invent text."""
    choices = model_choices(profile)
    arg_names = all_arg_names(profile)
    return {
        "type": "object",
        "properties": {
            "choice": {"type": "string", "enum": choices},
            "args": {
                "type": "object",
                "properties": {name: {"type": "string"} for name in arg_names},
                "required": arg_names,
                "additionalProperties": False,
            },
            "confidence": {"type": "string", "enum": ["high", "low"]},
            "options": {
                "type": "array",
                "items": {"type": "string", "enum": choices},
                "minItems": 1,
                "maxItems": 4,
            },
        },
        "required": ["choice", "args", "confidence", "options"],
        "additionalProperties": False,
    }


def build_messages(profile: dict, heard: str) -> list[dict]:
    phrases = "\n".join(f"- {code}: {text}" for code, text in phrase_codes(profile).items())
    commands = []
    for c in profile["commands"]:
        args = "; ".join(f'{k} = {v}' for k, v in c["args"].items()) or "none"
        commands.append(f"- {c['name']}: {c['description']} (args: {args})")
    arg_names = ", ".join(f'"{a}"' for a in all_arg_names(profile))

    system = f"""You help a person whose speech is impaired. What they said may be garbled,
misheard or incomplete. Decide what they most likely mean, choosing ONLY from this list.

PHRASES (sentences to say out loud to other people), each with its code:
{phrases}

COMMANDS (actions on the Mac):
{chr(10).join(commands)}

Rules:
- "choice" must be exactly one phrase CODE (like P1) or one command name from the list above.
- "args" always has the keys {arg_names}. Fill the ones the chosen command needs
  from what the person said; set all others to "".
- "confidence" is "high" only if you are sure; otherwise "low".
- "options" lists the 4 most likely choices (codes or command names), best first.
Reply with JSON only, on ONE line with no spaces or line breaks."""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"The person said: \"{heard}\""},
    ]
