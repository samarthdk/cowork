"""Answering a question from the Brain's own knowledge (no internet: Cowork is offline).

    answer, problem = answer_question("what's the weather in Boston", llm, screen_line)
    -> "Boston in November is usually cold, around 5 to 10 C. I'm offline, so I can't check today's forecast."

Like "Something else", this is free text, so it is kept narrow: one or two short
sentences, shown and spoken, and never done on the Mac and never typed.
"""

import config
from repair.validate import BadJSON, parse_json

MAX_ANSWER_CHARS = 240


def answer_schema() -> dict:
    return {"type": "object", "properties": {"answer": {"type": "string", "maxLength": MAX_ANSWER_CHARS}},
            "required": ["answer"], "additionalProperties": False}


def answer_messages(question: str, heard: str = "", screen: str = "") -> list[dict]:
    system = f"""You answer a question for a person whose speech is impaired, in ONE or TWO short sentences
(under {MAX_ANSWER_CHARS} characters), plainly, from your own general knowledge.
You are OFFLINE: you cannot look anything up. If the question needs live information (today's weather,
news, prices, scores, the time right now), give what is usually true and say plainly that you are offline
and can't check today's information. Example:
Q: what's the weather in Boston?
A: Boston in November is usually cold, around 5 to 10 C. I'm offline, so I can't check today's forecast.
Never pretend to have checked. If you don't know, say so.
Reply with JSON only, on ONE line: {{"answer": "..."}}"""
    user = f"Question: {question}"
    if heard and heard.strip().lower() != question.strip().lower():
        user += f"\n(What was heard, maybe garbled: \"{heard}\")"
    if screen:
        user += f"\n(On their screen: {screen})"
    import datetime
    user += f"\n(Today is {datetime.date.today():%A %d %B %Y}.)"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def answer_question(question: str, llm, heard: str = "", screen: str = "") -> tuple[str | None, str]:
    """(the answer or None, a problem or ""). One retry, like every model call."""
    problem = ""
    for _ in (1, 2):
        try:
            reply = llm.complete(answer_messages(question, heard, screen), answer_schema(), purpose="answer")
            text = " ".join(str(parse_json(reply.text).get("answer", "")).split()).strip()
            if text:
                return text[:MAX_ANSWER_CHARS], ""
            problem = "empty answer"
        except BadJSON as error:
            problem = str(error)
        except Exception as error:
            problem = f"LLM call failed: {type(error).__name__}: {error}"
    return None, problem


if __name__ == "__main__":
    # Try on its own: .venv/bin/python -m repair.answer "what's the weather in Boston"
    import sys
    from repair import get_llm
    config.LLM = "real" if config.LLM == "fake" and "--real" in sys.argv else config.LLM
    words = " ".join(a for a in sys.argv[1:] if a != "--real") or "what's the weather in Boston"
    print(answer_question(words, get_llm()))
