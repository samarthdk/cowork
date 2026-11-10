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
    examples = [e for e in profile.get("examples", []) if _usable(e, profile)]
    if not text or not examples:
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
