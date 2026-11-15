"""Intent repair: garbled text in, a validated Intent out.

    from repair import get_llm, repair
    intent = repair("wanna watr", profile, get_llm())
"""

import config
from repair import support
from repair.prompt import build_messages, build_schema
from repair.validate import BadJSON, Intent, failed, parse_json, validate


def get_llm():
    if config.LLM == "fake":
        from repair.fake_llm import FakeLLM
        return FakeLLM()
    if config.LLM == "real":
        from repair.real_llm import LMStudioLLM
        return LMStudioLLM()
    raise ValueError(f'config.LLM must be "fake" or "real", not "{config.LLM}"')


def repair(heard: str, profile: dict, llm, context: dict | None = None, exclude: tuple = ()) -> Intent:
    """One schema-constrained call, plus one retry if the reply is unusable.
    `context`: real values for command details (e.g. {"app": [installed apps]}).
    `exclude`: choices that may not be answered (the person already said no to them)."""
    messages = build_messages(profile, heard, context, exclude)
    schema = build_schema(profile, context, exclude)
    problem = ""
    for attempt in (1, 2):
        try:
            reply = llm.complete(messages, schema)
            stats = {"source": "llm", "latency_ms": round(reply.latency_ms),
                     "tokens": reply.completion_tokens, "tokens_per_s": round(reply.tokens_per_s, 1)}
            if reply.finish_reason == "length":
                # Cut off at the token limit. Retrying would give the same answer
                # (temperature 0), so go straight to low confidence.
                intent = failed(profile, f"answer cut off at {reply.completion_tokens} tokens "
                                         f"({reply.thinking_tokens} spent thinking)")
                intent.stats = stats
                return intent
            intent = validate(parse_json(reply.text), profile, context)
            intent.stats = stats
            _echo(heard, intent, profile)
            if intent.choice:
                score = support.evidence(heard, intent.choice, profile, intent.args)
                intent.stats["evidence"] = round(score, 2)
            if intent.confidence == "high" and intent.choice:
                # "High" from the model is not enough on its own (measured on real
                # impaired speech: its confident answers were often wrong). The heard
                # words must back the choice up, and no part may contradict it.
                ok, reason = support.check(heard, intent.choice, profile)
                if ok and score < config.HIGH_EVIDENCE:
                    ok, reason = False, (f"the heard words only match it {score:.0%} "
                                         f"(at least {config.HIGH_EVIDENCE:.0%} needed to act without asking)")
                if not ok:
                    intent.confidence = "low"
                    intent.problems.append(f"high confidence lowered: {reason}")
            if attempt == 2:
                intent.problems.insert(0, f"first try failed ({problem}), retry worked")
            return intent
        except BadJSON as error:
            problem = str(error)
        except Exception as error:  # e.g. LM Studio not running
            problem = f"LLM call failed: {type(error).__name__}: {error}"
    return failed(profile, f"{problem} (after 1 retry)")


def words_after(heard: str, starts: list[str]) -> str:
    """The person's own words after "type" / "search for" ... ("" if it doesn't start that way)."""
    from rapidfuzz import fuzz
    words = heard.split()
    for start in sorted(starts, key=len, reverse=True):
        lead = start.split()
        said = [w.strip(".,!?").lower() for w in words[:len(lead)]]
        if len(words) > len(lead) and all(fuzz.ratio(a, b) >= 80 for a, b in zip(said, lead)):
            return " ".join(words[len(lead):]).strip()
    return ""


def _echo(heard: str, intent, profile: dict) -> None:
    """For "type ..." and "search for ...", the words are exactly the ones heard after them (measured:
    Bonsai shortened "type this is a test from Cowork" to "Cowork.")."""
    from repair.profile import find_command
    command = find_command(profile, intent.choice) if intent.choice else None
    if command and command.get("echo"):   # measured: Bonsai sometimes leaves JSON bits at the end ("...Boston?'}")
        for name in command["args"]:
            if intent.args.get(name):
                intent.args[name] = intent.args[name].strip().rstrip("'\"{}[] ").lstrip("'\"{[ ").strip()
    if command and command.get("echo_after") and command["args"]:
        rest = words_after(heard, command["echo_after"])
        name = next(iter(command["args"]))
        if rest:
            intent.args[name] = rest if name == "text" else rest.rstrip(".!?")   # a search: no full stop


def warm_up(profile: dict, llm, context: dict | None = None) -> float:
    """Send the current prompt once so LM Studio has it ready (the first call
    after the prompt changes, e.g. after learning, is about 2 s slower).
    Returns the time it took in ms; never raises."""
    try:
        reply = llm.complete(build_messages(profile, "hello", context), build_schema(profile, context),
                             purpose="warmup")
        return reply.latency_ms
    except Exception:
        return 0.0
