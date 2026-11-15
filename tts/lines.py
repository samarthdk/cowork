"""What Cowork says, as whole sentences (one TTSKit generation each; never stitched from pieces).

TTSKit cannot stay loaded on this Mac (each run spends about 2 s starting up, a sentence
takes about 3-7 s in all), so the sentences Cowork expects to say are made in advance and
kept as whole wav files: the person's phrases, the meeting quick phrases, and the commands'
spoken steps. Anything else is made whole when it is needed (tts/real.py).

    .venv/bin/python -m tts.lines          prints what would be prepared for the Cowork profile
"""

import config

# Conference mode's quick phrases (said to the room).
MEETING = ["Can you repeat that?", "I agree.", "Give me a moment.", "I have a question."]
APPS_PREPARED = 12    # "Opening <app>." is prepared for this many apps (the running and common ones first)
DEMO_APPS = ["Microsoft Word", "Mail", "Google Chrome", "Safari"]   # always prepared


def clean(text: str) -> str:
    """The words TTSKit is given: no ellipsis, no surrounding quotes or spaces."""
    return (text or "").replace("…", "").strip().strip("“”\"").strip()


def text_of(line) -> str:
    """A line as one sentence (a list of parts is joined, for older callers)."""
    if isinstance(line, (list, tuple)):
        return " ".join(clean(p) for p in line if clean(p))
    return clean(line)


def question_line(card: dict) -> str:
    """What is said for a question card in the mini window, as one sentence:
    "Did you mean: Open Mail?" or "Which one did you mean? 1: Open Mail, 2: ...?"."""
    title = clean(card.get("title")).rstrip(" .?")
    options = card.get("options") or []
    if options:
        listed = ", ".join(f"{i + 1}: {clean(o.get('text'))}" for i, o in enumerate(options[:4]))
        return f"{title}? {listed}."
    body = clean(card.get("body"))
    return f"{title}: {body}?" if body else f"{title}?"


def to_prepare(profile: dict, apps: list[str] | None = None) -> list[str]:
    """Every whole sentence worth having ready, most useful first: phrases, quick phrases,
    the commands' spoken steps, and "Opening <app>." for the most likely apps."""
    from engine.speech import sentences_to_prepare
    apps = list(dict.fromkeys(DEMO_APPS + list(apps or [])))[:APPS_PREPARED]
    commands = profile.get("commands", [])
    sites = [f"Opening {site}." for site in config.SITES] if any(c["name"] == "open_website" for c in commands) else []
    everything = (list(profile.get("phrases", [])) + MEETING + sentences_to_prepare(profile)
                  + [f"Opening {app}." for app in apps] + sites)
    return list(dict.fromkeys(clean(t) for t in everything if clean(t)))


if __name__ == "__main__":
    from repair.store import open_profile
    profile = open_profile(config.ROOT / "profiles" / "cowork_profile.json", config.ROOT / "profiles" / "cowork_base.json")
    sentences = to_prepare(profile, [])
    print(len(sentences), "whole sentences:")
    for s in sentences:
        print("  ", s)
