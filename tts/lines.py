"""Spoken lines made of ready-made TTSKit pieces.

TTSKit cannot stay loaded on this Mac (each run spends about 2 s starting up), so
anything spoken quickly must already be in the cache. A line is therefore spoken as
pieces that were made in advance and are played back to back with a short gap:

    "Did you mean: Open Mail?"  ->  "Did you mean" + "Open" + "Mail"
    "Which one did you mean? 1: Open Mail, 2: ..."  ->  "Which one did you mean?" + "One." + "Open Mail" + ...
    "Opening Microsoft Word."   ->  the whole sentence if it is ready, else "Opening" + "Microsoft Word"

A line may be put together in more than one way (`ways`). The voice uses the first
way whose pieces are ALL ready; if none is, the line is not spoken (never half a line).

    .venv/bin/python -m tts.lines          prints what would be prepared for the Cowork profile
"""

import re

import config

GAP_S = 0.14          # between two pieces of one sentence ("Open" + "Mail")
SENTENCE_GAP_S = 0.32  # after a piece that ends a sentence ("Which one did you mean?" + "One.")

# Short prompt pieces used to build the lines Cowork says.
PROMPTS = [
    "Did you mean", "Which one did you mean?", "Which app should I type into?", "Which app?",
    "This can't be undone. Allow it?", "Did you mean this address?",
    "Yes.", "No.", "Done.", "Okay.", "Your answer, please.", "Something else", "None of these",
    "Opening", "Open", "Closing", "Close", "Going to", "Go to", "Type", "Search for",
    "Sorry, I couldn't do that.", "Let me think.",
]
NUMBERS = ["One.", "Two.", "Three.", "Four."]
# Conference mode's quick phrases (said to the room).
MEETING = ["Can you repeat that?", "I agree.", "Give me a moment.", "I have a question."]
BOXES = ["To", "Subject", "Message", "the subject", "the message"]   # "Going to {field}"
APPS_PREPARED = 12    # app names prepared as pieces (the running and common ones first)
DEMO_APPS = ["Microsoft Word", "Mail", "Google Chrome", "Safari"]   # always prepared


def clean(text: str) -> str:
    """The words TTSKit is given for a piece: no ellipsis, no surrounding quotes or spaces."""
    return (text or "").replace("…", "").strip().strip("“”\"").strip()


def _templates(profile: dict) -> list[tuple[str, str]]:
    """(prefix, suffix) of every command sentence with exactly one detail to fill in,
    e.g. "Opening {app}." -> ("Opening", ".")."""
    found = []
    for command in (profile or {}).get("commands", []):
        for template in (command.get("say"), command.get("request")):
            if template and template.count("{") == 1:
                prefix, rest = template.split("{", 1)
                suffix = rest.split("}", 1)[1]
                found.append((prefix, suffix))
    return found


def _split_template(text: str, profile: dict) -> list[str] | None:
    for prefix, suffix in _templates(profile):
        pattern = "^" + re.escape(prefix) + r"(.+?)" + re.escape(suffix) + "$"
        match = re.match(pattern, text)
        if match and prefix.strip():
            return [clean(prefix), clean(match.group(1))]
    return None


def parts_of(line) -> list[str]:
    """A line as its parts: a sentence is one part; a list is already parts."""
    if isinstance(line, (list, tuple)):
        return [p for p in line if clean(p)]
    return [line] if clean(line) else []


def ways(part: str, profile: dict | None = None) -> list[list[str]]:
    """The ways one part can be said from pieces, best first: the whole part,
    else split by a command template ("Opening Mail." -> "Opening" + "Mail")."""
    text = (part or "").strip()
    if not clean(text):
        return []
    out = [[clean(text)]]
    split = _split_template(text, profile or {})
    if split:
        out.append(split)
    return out


def choose(line, profile, is_ready) -> list[str] | None:
    """The pieces to play for a line: for every part, the first way whose pieces are
    all ready. None if any part cannot be said (then nothing is said: never half a line)."""
    pieces = []
    for part in parts_of(line):
        way = next((w for w in ways(part, profile) if all(is_ready(p) for p in w)), None)
        if way is None:
            return None
        pieces += way
    return pieces or None


def question_line(card: dict) -> list[str]:
    """What is said for a question card (in the mini window, or in Conference mode), as parts."""
    title = card.get("title") or ""
    options = card.get("options") or []
    if options:
        parts = [title]
        for number, option in zip(NUMBERS, options[:4]):
            parts += [number, option.get("text", "")]
        return parts
    if title.startswith("Did you mean") and card.get("body"):
        return ["Did you mean", card["body"]]
    return [title, card.get("body") or ""]


def text_of(line) -> str:
    """A line as one sentence (for the screen and the logs)."""
    if isinstance(line, (list, tuple)):
        return " ".join(clean(p) for p in line if clean(p))
    return (line or "").strip()


def gap_after(piece: str) -> float:
    return SENTENCE_GAP_S if piece.rstrip().endswith((".", "?", "!")) else GAP_S


def to_prepare(profile: dict, apps: list[str] | None = None) -> list[str]:
    """Every piece worth having ready, most useful first."""
    from tts.plan import sentences   # whole sentences: phrases, commands' spoken steps, "Opening <app>."
    apps = list(dict.fromkeys(DEMO_APPS + list(apps or [])))[:APPS_PREPARED]
    commands = profile.get("commands", [])
    requests = [c["request"] for c in commands if c.get("request") and "{" not in c["request"]]
    prefixes = [clean(p) for p, _ in _templates(profile) if clean(p)]
    sites = list(config.SITES) if any(c["name"] == "open_website" for c in commands) else []
    everything = (PROMPTS + NUMBERS + MEETING + list(profile.get("phrases", [])) + requests + prefixes
                  + apps + BOXES + sentences(profile, apps) + sites)
    return list(dict.fromkeys(clean(t) for t in everything if clean(t)))


if __name__ == "__main__":
    from repair.store import open_profile
    profile = open_profile(config.ROOT / "profiles" / "cowork_profile.json", config.ROOT / "profiles" / "cowork_base.json")
    pieces = to_prepare(profile, ["Mail", "Microsoft Word", "Google Chrome", "Safari"])
    print(len(pieces), "pieces:")
    for p in pieces:
        print("  ", p)
    print(ways("Opening Microsoft Word.", profile))
    print(choose(["Did you mean", "Open Mail"], profile, lambda p: True))
