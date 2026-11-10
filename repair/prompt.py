"""Builds the instructions and the strict JSON schema sent to the model.

The reply is the shortest shape that still pins everything down (the model
writes only about 20 tokens a second, so every token saved is time saved):

    {"choice":"P1","confidence":"high"}                          a phrase, or a command with no details
    {"choice":"open_app","app":"Safari","confidence":"high"}     a command plus exactly the details it needs

The schema is "one of these shapes" (JSON schema anyOf). `choice` can only be a
phrase code or a command name, and a detail that has a list of real values
(`context`, e.g. the apps installed on this Mac, or the buttons on screen) can
only be one of those values. So the model cannot invent a phrase, a command,
or an app that does not exist.
"""

from repair.profile import phrase_codes

CONFIDENCE = {"type": "string", "enum": ["high", "low"]}
MAX_FREE_TEXT = 120   # characters, for details with no fixed list (e.g. a reply message)
MAX_LISTED = 40       # at most this many real values are listed per detail


def usable_commands(profile: dict, context: dict | None = None) -> list[dict]:
    """Commands that can be chosen right now. A command whose detail must come
    from a list (e.g. something to click) is left out when that list is empty:
    the model only gets to choose things that exist."""
    context = context or {}
    return [c for c in profile["commands"]
            if all(context.get(arg) != [] for arg in c["args"])]


def listed_values(arg: str, context: dict | None) -> list[str] | None:
    """The real values a detail may take, or None if it is free text."""
    values = (context or {}).get(arg)
    return list(values)[:MAX_LISTED] if values else None


def _shape(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def build_schema(profile: dict, context: dict | None = None) -> dict:
    commands = usable_commands(profile, context)
    simple = list(phrase_codes(profile)) + [c["name"] for c in commands if not c["args"]]
    shapes = [_shape({"choice": {"type": "string", "enum": simple}, "confidence": CONFIDENCE})]
    for command in commands:
        if not command["args"]:
            continue
        properties = {"choice": {"type": "string", "enum": [command["name"]]}}
        for arg in command["args"]:
            values = listed_values(arg, context)
            properties[arg] = ({"type": "string", "enum": values} if values
                               else {"type": "string", "maxLength": MAX_FREE_TEXT})
        properties["confidence"] = CONFIDENCE
        shapes.append(_shape(properties))
    return {"anyOf": shapes}


def schema_choices(schema: dict) -> list[str]:
    """Every value `choice` may take under this schema (works for old and new shapes)."""
    found = []
    for shape in schema.get("anyOf", [schema]):
        found += [c for c in shape["properties"]["choice"]["enum"] if c not in found]
    return found


def schema_details(schema: dict, choice: str) -> dict:
    """The extra keys the schema wants for this choice: {name: list of values or None}."""
    for shape in schema.get("anyOf", [schema]):
        properties = shape["properties"]
        if choice in properties["choice"]["enum"]:
            return {name: spec.get("enum") for name, spec in properties.items()
                    if name not in ("choice", "confidence", "args", "options")}
    return {}


def build_messages(profile: dict, heard: str, context: dict | None = None) -> list[dict]:
    phrases = "\n".join(f"- {code}: {text}" for code, text in phrase_codes(profile).items())
    commands = []
    for c in usable_commands(profile, context):
        needs = []
        for arg, meaning in c["args"].items():
            values = listed_values(arg, context)
            needs.append(f'"{arg}": one of {", ".join(values)}' if values else f'"{arg}": {meaning}')
        commands.append(f"- {c['name']}: {c['description']}" + (f" Needs {'; '.join(needs)}." if needs else ""))
    # Learned examples go near the end, so the unchanging start of the prompt
    # can be reused by LM Studio between calls.
    from repair.learning import prompt_examples
    learned = prompt_examples(profile)
    examples = ""
    if learned:
        lines = "\n".join(f'- "{said}" -> {label}' for said, label in learned)
        examples = ("\nHow this person's speech has been misheard before "
                    f"(what was heard -> what they meant):\n{lines}\n\n")

    system = f"""You help a person whose speech is impaired. What they said may be garbled,
misheard or incomplete. Decide what they most likely mean, choosing ONLY from this list.

PHRASES (sentences to say out loud to other people), each with its code:
{phrases}

COMMANDS (actions on the Mac):
{chr(10).join(commands)}

Rules:
- "choice" must be exactly one phrase CODE (like P1) or one command name from the list above.
- If the command's line says it needs something, add that key too, taken from what the person said.
- "confidence" is "high" only if you are sure; otherwise "low".
{examples}Reply with JSON only, on ONE line with no spaces or line breaks."""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"The person said: \"{heard}\""},
    ]
