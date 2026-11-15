"""Fake LLM: no model. Picks the profile choice whose words best match what was
typed (allowing for misspellings), and replies in the same JSON shape as the
real model. Good enough to build and test everything else."""

import json
import re
import time
from difflib import SequenceMatcher

from metrics import log_call
from repair.llm import LLMReply
from repair.prompt import heard_from, schema_choices, schema_details

STOPWORDS = {"i", "im", "i'm", "a", "an", "the", "my", "me", "to", "some", "could",
             "have", "need", "please", "use", "of", "can", "you", "is", "it", "in"}
# Extra words that hint at each command (the real model works this out itself).
COMMAND_HINTS = {
    "open_app": "open start launch app",
    "read_latest_email": "read latest new email mail message",
    "reply_email": "reply answer respond email write back",
    "scroll_down": "scroll down page lower",
    "scroll_up": "scroll up page higher",
    "open_website": "website site go",
    "search_web": "search google look find",
    "compose_email": "write email mail message",
    "send_email": "send it",
    "type_text": "type write",
    "close_app": "close quit shut",
    "new_email": "write new email mail compose draft message",
    "next_field": "next box field",
    "previous_field": "previous box back",
    "new_document": "new blank document page",
    "new_tab": "new tab",
    "search_for": "search look find google",
    "answer_question": "what whats who when where how why weather question",
}
ARG_AFTER = {   # the fake takes the words after one of these as that detail
    "query": ("search", "google", "for"), "text": ("type",), "body": ("saying", "say"),
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
        for name, starts in ARG_AFTER.items():
            if name in args and not args[name] and w in starts and i + 1 < len(words):
                args[name] = " ".join(x for x in words[i + 1:] if x.lower() not in ("for", "saying"))
        if "subject" in args and not args["subject"]:
            args["subject"] = "Hello"
        if "question" in args and not args["question"]:
            args["question"] = heard_text.strip()
        if "text" in args and _similar(w, "reply") >= 0.7 and i + 1 < len(words):
            rest = [x for x in words[i + 1:] if x.lower() not in {"to", "email", "with", "saying"}]
            args["text"] = " ".join(rest)
    return args


class FakeLLM:
    def complete(self, messages: list[dict], schema: dict, purpose: str = "intent",
                 temperature: float = 0.0) -> LLMReply:
        start = time.perf_counter()
        heard_text = heard_from(messages)
        if "guess" in schema.get("properties", {}):
            # Free-text guess: the fake tidies up what was typed, and numbers
            # later attempts so "guess again" gives something different.
            guess = heard_text.strip().capitalize().rstrip(".")
            tries = messages[0]["content"].count('\n- "')
            guess = f"{guess} (guess {tries + 1})." if tries else guess + "."
            words = [w.strip(".,!?") for w in guess.split()[1:] if w[:1].isupper()]
            latency = (time.perf_counter() - start) * 1000
            log_call("llm", "fake", latency, purpose=purpose, tokens=0, tokens_per_s=0)
            kind = "question" if heard_text.strip().lower().split()[:1] in (["what"], ["who"], ["where"], ["how"], ["when"], ["why"]) else "say"
            return LLMReply(json.dumps({"guess": guess, "key_words": words, "kind": kind}), latency, 0, 0.0)
        if "answer" in schema.get("properties", {}):   # answering a question (repair/answer.py)
            question = re.search(r"^Question: (.*)$", messages[-1]["content"], re.MULTILINE)
            text = f"[fake answer to: {question.group(1) if question else '?'}] I'm offline, so I can't check today's information."
            return LLMReply(json.dumps({"answer": text}), (time.perf_counter() - start) * 1000, 0, 0.0)
        heard = _words(heard_text)
        choices = schema_choices(schema)
        # Phrase codes (P1...) mean nothing on their own; read what each code
        # stands for from the prompt, exactly as the real model has to.
        meaning = dict(re.findall(r"^- (P\d+): (.+)$", messages[0]["content"], re.MULTILINE))

        def score(choice):
            # A command also "sounds like" its real detail values ("open mail" -> open_app, Mail).
            values = " ".join(v for vs in schema_details(schema, choice).values() if vs for v in vs)
            return _score(heard, meaning.get(choice, choice) + (" " + values if values else ""))

        ranked = sorted(choices, key=score, reverse=True)
        best, second = score(ranked[0]), score(ranked[1]) if len(ranked) > 1 else 0.0
        confident = best >= 0.5 and best - second >= 0.2

        answer = {"choice": ranked[0]}
        details = schema_details(schema, ranked[0])
        guessed = _guess_args(heard_text, list(details))
        for name, real_values in details.items():
            value = guessed.get(name, "")
            if real_values:  # must be one of the real values: take the closest to any word said
                words = heard_text.split() or [""]
                value = max(real_values, key=lambda v: max(_similar(w.lower(), v.lower()) for w in words))
            answer[name] = value
        answer["confidence"] = "high" if confident else "low"
        reply = json.dumps(answer)
        latency = (time.perf_counter() - start) * 1000
        log_call("llm", "fake", latency, purpose=purpose, tokens=0, tokens_per_s=0)
        return LLMReply(reply, latency, 0, 0.0)
