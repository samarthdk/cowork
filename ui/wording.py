"""How the Cowork screen words things: the person's request as a plain sentence
("Open Mail"), step titles ("Opening Mail"), and permission card text.
Only wording: what is meant and what happens is decided by the engine."""

from repair.profile import find_command

GENERIC = {   # a command chosen before its details are known
    "open_app": "Open an app", "open_website": "Open a website", "search_web": "Search Google",
    "compose_email": "Write an email", "type_text": "Type something", "quit_app": "Close an app",
}


def request_text(choice: str | None, args: dict | None, profile: dict) -> str:
    """The person's request as they might have typed it."""
    if not choice:
        return ""
    command = find_command(profile, choice)
    if command is None:
        return choice                       # a phrase: their own words
    args = {k: v for k, v in (args or {}).items() if v}
    template = command.get("request") or choice.replace("_", " ").capitalize()
    if any(name not in args for name in command["args"]):
        return GENERIC.get(choice, template.split("{")[0].strip() or template)
    try:
        return template.format(**args)
    except (KeyError, IndexError):
        return template


def step_title(say: str) -> str:
    """'Opening Mail.' -> 'Opening Mail'."""
    return (say or "").strip().rstrip(".")


def email_preview(args: dict) -> dict | None:
    if not any(args.get(k) for k in ("to", "subject", "body")):
        return None
    return {"to": args.get("to", ""), "subject": args.get("subject", ""), "body": args.get("body", "")}


def is_command(choice: str | None, profile: dict) -> bool:
    return bool(choice) and find_command(profile, choice) is not None


def permission(question: dict, profile: dict, turn: dict | None) -> dict:
    """What the card for an open question says.
    Returns {"title", "body", "allow", "deny", "options": [{"text", "kind"}], "risky", "guess", "email"}."""
    stage, data = question["stage"], question.get("data") or {}
    card = {"title": "", "body": "", "allow": "Allow", "deny": "Don't allow", "options": [],
            "risky": bool(data.get("risky")), "guess": False, "email": None, "say_aloud": False}
    if stage == "did_you_mean":
        choice, args = data.get("choice"), data.get("args") or {}
        text = request_text(choice, args, profile)
        if card["risky"]:
            card["title"] = "This can't be undone. Allow it?"
            card["allow"], card["deny"] = "Allow", "Don't allow"
        else:
            card["title"] = "Did you mean…?"
            card["allow"], card["deny"] = "Yes", "No"
        card["body"] = text
        card["say_aloud"] = not is_command(choice, profile)
        if choice == "compose_email":
            card["email"] = email_preview(args)
    elif stage == "guess":
        card["title"] = f"My guess {data.get('attempt', 1)} of {data.get('total', 3)} — not from your list"
        card["body"] = data.get("guess", "")
        card["allow"], card["deny"] = "Yes, say it", "No"
        card["guess"] = card["say_aloud"] = True
    elif stage == "options":
        card["title"] = "Which one did you mean?"
        intent_args = (turn or {}).get("intent_args") or {}
        for option in question.get("options", []):
            if option == "Something else":
                card["options"].append({"text": "Something else…", "kind": "else"})
            elif is_command(option, profile):
                card["options"].append({"text": request_text(option, intent_args, profile), "kind": "command"})
            else:
                card["options"].append({"text": option, "kind": "phrase"})
    elif stage == "target":
        card["title"] = "Which app should I type into?"
        card["options"] = [{"text": o if o != "Something else" else "Something else\u2026",
                            "kind": "else" if o == "Something else" else "value"} for o in question.get("options", [])]
    elif stage == "target_other":
        card["title"] = "Which app?"
        card["options"] = [{"text": v, "kind": "value"} for v in (data.get("values") or [])[:8]]
    elif stage == "split":
        card["title"] = "Did you mean\u2026?"
        card["body"] = f"\u201c{data.get('first', '')}\u201d, then type \u201c{data.get('rest', '')}\u201d"
        card["allow"], card["deny"] = "Yes, do both", "No, it's one request"
    elif stage == "detail":
        name = data.get("name", "")
        nice = {"app": "Which app?", "site": "Which website?", "to": "Who is it for?", "query": "Search for what?",
                "text": "What should I type?", "subject": "What is the subject?", "body": "What should it say?"}
        card["title"] = nice.get(name, f"{name}?")
        card["body"] = request_text(data.get("command"), {}, profile)
        values = data.get("values") or []
        card["options"] = [{"text": v, "kind": "value"} for v in values[:8]]
    return card


def from_meaning(meaning: str | None, profile: dict) -> str:
    """The engine describes a choice as 'open_app (app: Mail)'; word it as 'Open Mail'."""
    import re
    if not meaning:
        return ""
    for command in profile["commands"]:
        name = command["name"]
        if meaning == name:
            return request_text(name, {}, profile)
        if meaning.startswith(name + " (") and meaning.endswith(")"):
            inside = meaning[len(name) + 2:-1]
            keys = "|".join(re.escape(k) for k in command["args"])
            args = {}
            if keys:
                parts = re.split(rf"(?:^|, )({keys}): ", inside)
                for key, value in zip(parts[1::2], parts[2::2]):
                    args[key] = value
            return request_text(name, args, profile)
    return meaning
