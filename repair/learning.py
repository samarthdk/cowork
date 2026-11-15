"""Learning from confirmations.

Every confirmed result is saved as an example (heard text -> meant phrase or
command, see repair/store.py). This file uses them in two ways:

1. Fast path: if new heard text closely matches a saved example, answer at
   once with high confidence, without calling the LLM. When unsure, nothing:
   - it must be at least FAST_PATH_SIMILARITY % similar,
   - very short text must match exactly (short strings look alike too easily),
   - if examples with DIFFERENT meanings both match closely, the LLM decides.
2. The most recent examples are shown to the LLM in the prompt, as examples
   of how this person's speech gets misheard.
"""

import time
from dataclasses import dataclass

import config
from metrics import log_call
from repair import support
from repair.profile import all_arg_names, all_choices, find_command, phrase_codes
from repair.store import normalize, similarity
from repair.validate import Intent, fill_options

AMBIGUITY_MARGIN = 5  # another meaning within 5 points of the threshold = unsure


@dataclass
class FastMatch:
    intent: Intent
    similarity: float   # 0-100
    example: dict       # the learned example that matched
    latency_ms: float


def _usable(example: dict, profile: dict) -> bool:
    """Its meaning must still exist (a phrase may have been undone)."""
    return example["meant"] in all_choices(profile)


def fast_path(heard: str, profile: dict) -> FastMatch | None:
    start = time.perf_counter()
    text = normalize(heard)
    if not text:
        return None
    # A phrase heard word for word (or the exact word this person uses for a
    # command) needs no model at all.
    # "type ..." / "dictate ...": always typing the words that follow (measured: words like "question mark"
    # in the dictation pulled the model towards other commands, e.g. mark as unread).
    first, _, rest = heard.strip().partition(" ")
    typing = next((c for c in profile["commands"] if c["name"] == "type_text"), None)
    if typing and rest.strip() and first.lower().strip(".,!?") in ("type", "dictate"):
        intent = Intent("type_text", "command", {name: "" for name in all_arg_names(profile)}, "high",
                        fill_options("type_text", [], all_choices(profile)))
        intent.args["text"] = rest.strip()
        latency = (time.perf_counter() - start) * 1000
        log_call("repair", "fast_path", latency, similarity=100, matched="type ...", meant="type_text")
        return FastMatch(intent, 100.0, {"heard": heard, "meant": "type_text", "args": {}, "builtin": True}, latency)
    exact = [p for p in profile["phrases"] if normalize(p) == text]
    exact += [c["name"] for c in profile["commands"] if (not c["args"] or c.get("whole_request"))
              and text in [normalize(w) for w in c.get("said_as", [])]]
    if len(exact) == 1:
        meant = exact[0]
        intent = Intent(meant, "command" if find_command(profile, meant) else "phrase",
                        {name: "" for name in all_arg_names(profile)}, "high",
                        fill_options(meant, [], all_choices(profile)))
        command = find_command(profile, meant)
        if command and command.get("whole_request"):   # e.g. "print this": the words are the request
            intent.args[next(iter(command["args"]))] = heard.strip()
        latency = (time.perf_counter() - start) * 1000
        log_call("repair", "fast_path", latency, similarity=100, matched="(the phrase itself)", meant=meant)
        return FastMatch(intent, 100.0, {"heard": heard, "meant": meant, "args": {}, "builtin": True}, latency)
    examples = [e for e in profile.get("examples", []) if _usable(e, profile)]
    if not examples:
        return None
    scored = sorted(((similarity(text, e["heard"]), e) for e in examples),
                    key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]

    needed = 100 if len(text) < config.FAST_PATH_MIN_CHARS else config.FAST_PATH_SIMILARITY
    if best_score < needed:
        return None
    for score, other in scored[1:]:
        if score < needed - AMBIGUITY_MARGIN:
            break
        if (other["meant"], other["args"]) != (best["meant"], best["args"]):
            return None  # two different meanings match closely: let the LLM decide
    if not support.check(heard, best["meant"], profile)[0]:
        return None  # a part of it contradicts the learned meaning: let the LLM decide

    args = {name: best["args"].get(name, "") for name in all_arg_names(profile)}
    kind = "command" if find_command(profile, best["meant"]) else "phrase"
    intent = Intent(best["meant"], kind, args, "high",
                    fill_options(best["meant"], [], all_choices(profile)))
    latency = (time.perf_counter() - start) * 1000
    log_call("repair", "fast_path", latency, similarity=round(best_score, 1),
             matched=best["heard"], meant=best["meant"])
    return FastMatch(intent, best_score, best, latency)


def meaning_label(meant: str, args: dict, profile: dict) -> str:
    """For the prompt: a phrase's code, or a command with its details."""
    for code, phrase in phrase_codes(profile).items():
        if phrase == meant:
            return code
    details = ", ".join(f"{k}: {v}" for k, v in args.items() if v)
    return f"{meant} ({details})" if details else meant


def prompt_examples(profile: dict, count: int | None = None) -> list[tuple[str, str]]:
    """The most recent usable examples, oldest first: [(heard, meaning label), ...]."""
    count = config.PROMPT_EXAMPLES if count is None else count
    usable = [e for e in profile.get("examples", []) if _usable(e, profile)]
    return [(e["heard"], meaning_label(e["meant"], e["args"], profile)) for e in usable[-count:]] if count else []


OPEN_WORDS = ("open", "launch", "start", "can you open", "please open", "open up")


CLOSE_WORDS = ("close", "quit", "exit", "please close", "can you close")


def close_app_match(heard: str, profile: dict, apps: list[str]) -> FastMatch | None:
    """"close Mail", "quit Word": an app named clearly. Recognised without the model."""
    return open_app_match(heard, profile, apps, words=CLOSE_WORDS, command="quit_app")


def open_app_match(heard: str, profile: dict, apps: list[str], words=OPEN_WORDS, command: str = "open_app") -> FastMatch | None:
    """"open Safari", "launch Microsoft Word", "open word": an app on this Mac named clearly. Recognised
    without the model (measured: Bonsai sometimes took "open Safari" for "new tab")."""
    from rapidfuzz import fuzz
    start = time.perf_counter()
    if not apps or not any(c["name"] == command for c in profile["commands"]):
        return None
    text = normalize(heard)
    rest = next((text[len(w) + 1:] for w in sorted(words, key=len, reverse=True) if text.startswith(w + " ")), "")
    rest = rest.removeprefix("the ").removesuffix(" app").strip()
    if not rest or len(rest.split()) > 3:
        return None
    scored = []
    for app in apps:
        short = app.replace("Microsoft ", "").replace("Google ", "")
        scored.append((max(fuzz.ratio(rest, normalize(app)), fuzz.ratio(rest, normalize(short))), app))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 90 or (len(scored) > 1 and scored[1][0] >= 90 and scored[1][1] != scored[0][1]):
        return None   # not clearly one app: the Brain decides (and asks if unsure)
    app = scored[0][1]
    intent = Intent(command, "command", {name: "" for name in all_arg_names(profile)}, "high",
                    fill_options(command, [], all_choices(profile)))
    intent.args["app"] = app
    latency = (time.perf_counter() - start) * 1000
    log_call("repair", "fast_path", latency, similarity=round(scored[0][0]), matched=f"{command} <app>", meant=f"{command} {app}")
    return FastMatch(intent, scored[0][0], {"heard": heard, "meant": command, "args": {"app": app}, "builtin": True}, latency)
