"""Checks two Brain shortcuts, without LM Studio:
  - after a "no", the Brain is asked again with that choice ruled out, and its
    new pick goes in the first square of the grid;
  - a phrase heard word for word is answered at once with no model call.
Run:
    .venv/bin/python tests/test_second_opinion.py
"""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from choose.answers import Answer  # noqa: E402
from engine.session import Session  # noqa: E402
from repair.learning import fast_path  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.profile import load_profile  # noqa: E402
from repair.prompt import build_messages, build_schema, schema_choices  # noqa: E402
from repair.rank import SOMETHING_ELSE, rank_options  # noqa: E402

BASE = load_profile(config.BASE_PROFILE_PATH)
BASE["examples"] = []
P = BASE["phrases"]
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


class Model:
    """First answers P1 (low). Asked again with P1 ruled out, answers `second`."""
    def __init__(self, second="P3"):
        self.second, self.requests = second, []

    def complete(self, messages, schema, purpose="intent", temperature=0.0):
        self.requests.append((messages[-1]["content"], schema_choices(schema)))
        again = "NOT" in messages[-1]["content"]
        return LLMReply(json.dumps({"choice": self.second if again else "P1", "confidence": "low"}), 1, 0, 0)


def converse(heard, answers, model, profile=None):
    events, said, asked = [], [], []
    session = Session(copy.deepcopy(profile or BASE), model,
                      type("V", (), {"speak": lambda s, t: said.append(t)})(), on_event=events.append)
    flow, answers = session.hear(heard), list(answers)
    try:
        question = next(flow)
        while True:
            asked.append(question)
            question = flow.send(answers.pop(0) if answers else None)
    except StopIteration:
        return said, asked, events


print("-- Ruling a choice out --")
schema = build_schema(BASE, exclude=(P[0], "scroll_down"))
check("ruled-out phrase and command are not in the schema",
      ("P1" in schema_choices(schema), "scroll_down" in schema_choices(schema), "P2" in schema_choices(schema)),
      (False, False, True))
messages = build_messages(BASE, "x", exclude=(P[0], "scroll_down"))
check("the request says what it is NOT", "The person already said it is NOT: P1, scroll_down" in messages[1]["content"], True)
check("the instructions are unchanged (so LM Studio keeps them cached)",
      messages[0]["content"] == build_messages(BASE, "x")[0]["content"], True)
check("first= puts a choice in the first square", rank_options("aim in payn", BASE, [], exclude=(P[0],), first=P[1])[0], P[1])
check("  ...and the rest stay ranked by similarity", rank_options("aim in payn", BASE, [], exclude=(P[0],), first=P[1])[1], P[3])

print("-- Second opinion after 'no' --")
m = Model("P3")
said, asked, events = converse("uh mm ehh", [Answer("no"), Answer("pick", 0), None], m)
grid = next(q for q in asked if q.kind == "choice")
check("asked the Brain twice", len(m.requests), 2)
check("second request rules out the rejected guess", ("NOT: P1" in m.requests[1][0], "P1" in m.requests[1][1]), (True, False))
check("its new pick is the first square", grid.options[0], P[2])
check("grid still has 3 options + 'Something else', none of them the rejected one",
      (len(grid.options), grid.options[-1], P[0] in grid.options), (4, SOMETHING_ELSE, False))
check("picking it speaks it", said, [P[2]])
m = Model("P1")   # a model that repeats the rejected choice anyway
said, asked, events = converse("uh mm ehh", [Answer("no"), Answer("none")], m)
grid = next(q for q in asked if q.kind == "choice")
check("a second opinion that repeats the rejected choice is ignored", P[0] in grid.options, False)
config.SECOND_OPINION = False
m = Model("P3")
converse("uh mm ehh", [Answer("no"), Answer("none")], m)
check("can be switched off (config.SECOND_OPINION)", len(m.requests), 1)
config.SECOND_OPINION = True
m = Model("P3")
converse("uh mm ehh", [Answer("yes"), None], m)
check("no second call when the first guess was right", len(m.requests), 1)

print("-- A phrase heard word for word --")
match = fast_path("I need to use the bathroom", BASE)
check("answered at once, high confidence", (match.intent.choice, match.intent.confidence, match.similarity), (P[1], "high", 100.0))
check("punctuation and capitals don't matter", fast_path("i'm thirsty could i have some water", BASE).intent.choice, P[0])
check("one word different: not the fast path (the Brain decides)", fast_path("I need to use the bedroom", BASE), None)
check("a part of the phrase: not the fast path", fast_path("the bathroom", BASE), None)
taught = copy.deepcopy(BASE)
next(c for c in taught["commands"] if c["name"] == "scroll_down")["said_as"] = ["down"]
check("the exact word a person uses for a command", fast_path("Down.", taught).intent.choice, "scroll_down")
check("a command that needs details is never matched by a bare word", fast_path("open app", BASE), None)
m = Model()
said, asked, events = converse("Please call my daughter.", [None], m)
check("in the conversation: spoken with no model call", (said, len(m.requests)), ([P[2]], 0))
said, asked, events = converse("Please call my daughter.", [Answer("no"), Answer("none")], m)
check("cancelling it never deletes anything (it was not a learned example)",
      ("cancelled" in [e["kind"] for e in events], "forgot" in [e["kind"] for e in events]), (True, False))

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
