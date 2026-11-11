"""Checks the Brain's compact answer shape and "only real choices" rules,
without LM Studio. Run:
    .venv/bin/python tests/test_brain.py
"""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from repair import repair, support, warm_up  # noqa: E402
from repair.fake_llm import FakeLLM  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.profile import load_profile  # noqa: E402
from repair.prompt import build_messages, build_schema, schema_choices, usable_commands  # noqa: E402
from repair.validate import validate  # noqa: E402

BASE = load_profile(config.BASE_PROFILE_PATH)
BASE["examples"] = []
P = BASE["phrases"]
APPS = {"app": ["Safari", "Mail", "Notes", "Music"]}
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


class Model:
    def __init__(self, reply, finish="stop"):
        self.reply, self.finish, self.seen = reply, finish, []

    def complete(self, messages, schema, purpose="intent", temperature=0.0):
        self.seen.append((messages, schema, purpose))
        return LLMReply(json.dumps(self.reply), 900, 11, 12.2, self.finish)


print("-- The answer shape --")
schema = build_schema(BASE, APPS)
check("'one of these shapes'", list(schema), ["anyOf"])
simple = schema["anyOf"][0]
check("shape 1: phrase codes and commands without details",
      simple["properties"]["choice"]["enum"][:4] + [c for c in simple["properties"]["choice"]["enum"] if not c.startswith("P")],
      ["P1", "P2", "P3", "P4", "read_latest_email", "scroll_down"] if "scroll_up" not in schema_choices(schema)
      else ["P1", "P2", "P3", "P4"] + [c["name"] for c in BASE["commands"] if not c["args"]])
check("shape 1 has only choice and confidence", simple["required"], ["choice", "confidence"])
open_app = next(s for s in schema["anyOf"] if s["properties"]["choice"]["enum"] == ["open_app"])
check("open_app: the app must be one of the real apps", open_app["properties"]["app"]["enum"], APPS["app"])
check("open_app: needs choice, app, confidence", open_app["required"], ["choice", "app", "confidence"])
reply = next(s for s in schema["anyOf"] if s["properties"]["choice"]["enum"] == ["reply_email"])
check("reply_email: message is free text with a length limit", reply["properties"]["text"],
      {"type": "string", "maxLength": 120})
check("every profile choice can be answered", sorted(schema_choices(schema)),
      sorted(["P1", "P2", "P3", "P4"] + [c["name"] for c in usable_commands(BASE, APPS)]))
check("no context: app is free text", build_schema(BASE)["anyOf"][1]["properties"]["app"].get("enum"), None)

print("-- Only things that exist --")
no_apps = {"app": []}
check("no apps available: open_app cannot be chosen", "open_app" in schema_choices(build_schema(BASE, no_apps)), False)
check("  ...and is not offered in the prompt", "open_app" in build_messages(BASE, "x", no_apps)[0]["content"], False)
prompt = build_messages(BASE, "x", APPS)[0]["content"]
check("the prompt lists the real apps", 'Needs "app": one of Safari, Mail, Notes, Music.' in prompt, True)
many = {"app": [f"App{i}" for i in range(100)]}
check("long lists are capped", len(build_schema(BASE, many)["anyOf"][1]["properties"]["app"]["enum"]), 40)

print("-- Reading the model's answer --")
i = validate({"choice": "P2", "confidence": "high"}, BASE, APPS)
check("phrase", (i.choice, i.kind, i.confidence, i.problems), (P[1], "phrase", "high", []))
i = validate({"choice": "open_app", "app": "safari", "confidence": "high"}, BASE, APPS)
check("command with a real app (case fixed)", (i.choice, i.args["app"], i.confidence), ("open_app", "Safari", "high"))
i = validate({"choice": "open_app", "app": "Netflix", "confidence": "high"}, BASE, APPS)
check("an app that does not exist: low confidence", (i.confidence, "not one of the real choices" in i.problems[0]),
      ("low", True))
i = validate({"choice": "open_app", "confidence": "high"}, BASE, APPS)
check("command missing its detail: low confidence", i.confidence, "low")
i = validate({"choice": "P1", "app": "Safari", "confidence": "high"}, BASE, APPS)
check("details that don't belong to the choice are dropped", i.args, {"app": "", "text": ""})
i = validate({"choice": "open_app", "args": {"app": "Mail", "text": ""}, "confidence": "high", "options": ["P1"]}, BASE, APPS)
check("the older nested shape still works", (i.choice, i.args["app"], i.confidence), ("open_app", "Mail", "high"))
i = validate({"choice": "P9", "confidence": "high"}, BASE)
check("unknown code: low confidence, no choice", (i.choice, i.confidence), (None, "low"))
i = validate({"choice": "P1", "confidence": "certain"}, BASE)
check("bad confidence word: low", i.confidence, "low")

print("-- repair() --")
m = Model({"choice": "open_app", "app": "Music", "confidence": "high"})
i = repair("opun da musik", BASE, m, APPS)
check("context reaches the prompt and the schema",
      ("one of Safari, Mail, Notes, Music" in m.seen[0][0][0]["content"], "anyOf" in m.seen[0][1]), (True, True))
check("result", (i.choice, i.args["app"], i.confidence), ("open_app", "Music", "high"))
check("speed is recorded", (i.stats["source"], i.stats["latency_ms"], i.stats["tokens"], i.stats["tokens_per_s"]),
      ("llm", 900, 11, 12.2))
i = repair("x", BASE, Model({"choice": "P1", "confidence": "high"}, finish="length"))
check("cut-off answer: low confidence, one call", (i.confidence, i.choice), ("low", None))
m = Model({"choice": "reply_email", "text": "see you soon", "confidence": "high"})
i = repair("reply see you soon", BASE, m, APPS)
check("a command's own details count as heard, not as 'extra words'", (i.confidence, i.problems), ("high", []))
i = repair("uh the the reply thing maybe see you soon or so", BASE, m, APPS)
check("  ...but mostly unrelated words lower it", i.confidence, "low")
check("command vs its name is never a contradiction", support.check("reply to the email saying hello", "reply_email", BASE),
      (True, ""))
learned = copy.deepcopy(BASE)
learned["examples"] = [{"heard": "aim thur stee cud eye haf sum wah tur", "meant": P[0], "args": {}, "new_phrase": False}]
i = repair("cud eye haf sum pep see", learned, Model({"choice": "P1", "confidence": "high"}))
check("the Pepsi safeguard still lowers confidence", i.confidence, "low")

print("-- 'High' needs evidence from the heard words --")
high = Model({"choice": "P1", "confidence": "high"})
i = repair("you need me", BASE, high)
check("model says high, heard words share nothing with the phrase: lowered", i.confidence, "low")
check("  ...with the reason", "only match it" in i.problems[-1], True)
check("  ...and the evidence score is recorded", i.stats["evidence"] < 0.3, True)
check("phrase said clearly: high stands", repair("I'm thirsty, could I have some water?", BASE, high).confidence, "high")
check("most of the phrase heard: high stands", repair("thirsty could I have some water", BASE, high).confidence, "high")
check("only a keyword heard: ask", repair("water", BASE, high).confidence, "low")
i = repair("scrol dwn", BASE, Model({"choice": "scroll_down", "confidence": "high"}))
check("a command close to its own name: high stands", i.confidence, "high")
i = repair("opn safari", BASE, Model({"choice": "open_app", "app": "Safari", "confidence": "high"}), APPS)
check("open_app with the app heard: high stands", (i.confidence, i.args["app"]), ("high", "Safari"))
i = repair("dont", BASE, Model({"choice": "scroll_down", "confidence": "high"}))
check("a command from an unrelated word: ask", i.confidence, "low")
taught = copy.deepcopy(BASE)
taught["examples"] = [{"heard": "Don't!", "meant": "scroll_down", "args": {}, "new_phrase": False}]
i = repair("dont", taught, Model({"choice": "scroll_down", "confidence": "high"}))
check("...until this person's own word for it has been learned", i.confidence, "high")
said_as = copy.deepcopy(BASE)
next(c for c in said_as["commands"] if c["name"] == "scroll_down")["said_as"] = ["down"]
check("the profile can list the word a person uses for a command",
      repair("down", said_as, Model({"choice": "scroll_down", "confidence": "high"})).confidence, "high")
check("a model 'low' is never raised", repair("I'm thirsty, could I have some water?", BASE,
                                             Model({"choice": "P1", "confidence": "low"})).confidence, "low")
m = Model({"choice": "P1", "confidence": "high"})
check("warm-up sends one call marked as warm-up", (warm_up(BASE, m, APPS) > 0, m.seen[0][2]), (True, "warmup"))

print("-- The fake model speaks the same shape --")
fake = FakeLLM()
i = repair("thirsy watr", BASE, fake, APPS)
check("fake: right phrase from two garbled words, but not enough evidence to act without asking",
      (i.choice, i.confidence), (P[0], "low"))
i = repair("I'm thirsty could I have some water", BASE, fake, APPS)
check("fake: the phrase said clearly is high", (i.choice, i.confidence), (P[0], "high"))
i = repair("opn musik", BASE, fake, APPS)
check("fake: picks a real app", (i.choice, i.args["app"]), ("open_app", "Music"))
i = repair("scrol dwn", BASE, fake, APPS)
check("fake: command without details", (i.choice, i.confidence), ("scroll_down", "high"))

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
