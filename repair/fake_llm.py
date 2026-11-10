"""Fake LLM: no model. Picks the profile choice whose words best match what was
typed (allowing for misspellings), and replies in the same JSON shape as the
real model. Good enough to build and test everything else."""

import json
import re
import time
from difflib import SequenceMatcher

from metrics import log_call
from repair.llm import LLMReply

STOPWORDS = {"i", "im", "i'm", "a", "an", "the", "my", "me", "to", "some", "could",
             "have", "need", "please", "use", "of", "can", "you", "is", "it", "in"}
# Extra words that hint at each command (the real model works this out itself).
COMMAND_HINTS = {
    "open_app": "open start launch app",
    "read_latest_email": "read latest new email mail message",
    "reply_email": "reply answer respond email write back",
    "scroll_down": "scroll down page lower",
}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z']+", text.lower()) if w not in STOPWORDS]


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _score(heard: list[str], choice: str) -> float:
    target = _words(choice.replace("_", " ") + " " + COMMAND_HINTS.get(choice, ""))
    if not heard or not target:
        return 0.0
    matches = [max(_similar(h, t) for t in target) for h in heard]
    return sum(m for m in matches if m >= 0.6) / len(heard)


def _guess_args(heard_text: str, arg_names: list[str]) -> dict:
    args = {name: "" for name in arg_names}
    words = heard_text.split()
    lowered = [w.lower() for w in words]
    for i, w in enumerate(lowered):
        if "app" in args and _similar(w, "open") >= 0.75 and i + 1 < len(words):
            args["app"] = words[i + 1].strip(".,!?").capitalize()
        if "text" in args and _similar(w, "reply") >= 0.7 and i + 1 < len(words):
            rest = [x for x in words[i + 1:] if x.lower() not in {"to", "email", "with", "saying"}]
            args["text"] = " ".join(rest)
    return args


class FakeLLM:
    def complete(self, messages: list[dict], schema: dict, purpose: str = "intent",
                 temperature: float = 0.0) -> LLMReply:
        start = time.perf_counter()
        heard_text = re.sub(r'^The person said: "|"$', "", messages[-1]["content"])
        if "guess" in schema["properties"]:
            # Free-text guess: the fake tidies up what was typed, and numbers
            # later attempts so "guess again" gives something different.
            guess = heard_text.strip().capitalize().rstrip(".")
            tries = messages[0]["content"].count('\n- "')
            guess = f"{guess} (guess {tries + 1})." if tries else guess + "."
            words = [w.strip(".,!?") for w in guess.split()[1:] if w[:1].isupper()]
            latency = (time.perf_counter() - start) * 1000
            log_call("llm", "fake", latency, purpose=purpose, tokens=0, tokens_per_s=0)
            return LLMReply(json.dumps({"guess": guess, "key_words": words}), latency, 0, 0.0)
        heard = _words(heard_text)
        choices = schema["properties"]["choice"]["enum"]
        arg_names = list(schema["properties"]["args"]["properties"])
        # Phrase codes (P1...) mean nothing on their own; read what each code
        # stands for from the prompt, exactly as the real model has to.
        meaning = dict(re.findall(r"^- (P\d+): (.+)$", messages[0]["content"], re.MULTILINE))

        def score(choice):
            return _score(heard, meaning.get(choice, choice))

        ranked = sorted(choices, key=score, reverse=True)
        best, second = score(ranked[0]), score(ranked[1])
        confident = best >= 0.5 and best - second >= 0.2

        reply = json.dumps({
            "choice": ranked[0],
            "args": _guess_args(heard_text, arg_names),
            "confidence": "high" if confident else "low",
            "options": ranked[:4],
        })
        latency = (time.perf_counter() - start) * 1000
        log_call("llm", "fake", latency, purpose=purpose, tokens=0, tokens_per_s=0)
        return LLMReply(reply, latency, 0, 0.0)
