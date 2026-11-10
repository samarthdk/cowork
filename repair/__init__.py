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


def repair(heard: str, profile: dict, llm) -> Intent:
    """One schema-constrained call, plus one retry if the reply is unusable."""
    messages, schema = build_messages(profile, heard), build_schema(profile)
    problem = ""
    for attempt in (1, 2):
        try:
            reply = llm.complete(messages, schema)
            if reply.finish_reason == "length":
                # Cut off at the token limit. Retrying would give the same answer
                # (temperature 0), so go straight to low confidence.
                return failed(profile, f"answer cut off at {reply.completion_tokens} tokens "
                                       f"({reply.thinking_tokens} spent thinking)")
            intent = validate(parse_json(reply.text), profile)
            if intent.confidence == "high" and intent.choice:
                # Don't trust "high" when what was heard only partly matches.
                ok, reason = support.check(heard, intent.choice, profile)
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
