"""Checks the fixes from the stress test (no LM Studio needed):
1. "high" is not trusted when what was heard only partly matches the choice
   or its learned examples (the Pepsi case);
2. the 3-second correction window: "no" cancels, undoes learning, shows options;
3. y / n / yes / no / undo are never treated as sentences.
Run:
    .venv/bin/python tests/test_corrections.py
"""

import builtins
import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
import config  # noqa: E402
import demo_text  # noqa: E402
from repair import repair, support  # noqa: E402
from repair.learning import fast_path  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.profile import load_profile  # noqa: E402
from repair.rank import SOMETHING_ELSE  # noqa: E402
from repair.store import add_example, open_profile  # noqa: E402

BASE = load_profile(config.BASE_PROFILE_PATH)
BASE["examples"] = []
P = BASE["phrases"]
WATER = P[0]
LEARNED = "aim thur stee cud eye haf sum wah tur"
PEPSI = "cud eye haf sum pep see"
NO_ARGS = {"app": "", "text": ""}
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


def with_water_example():
    p = copy.deepcopy(BASE)
    add_example(p, LEARNED, WATER)
    return p


class Model:
    """Stand-in LLM. Like the real one in the stress test: HIGH -> water."""
    def __init__(self, choice="P1", confidence="high", guess="Could I have some Pepsi?"):
        self.choice, self.confidence, self.guess, self.calls = choice, confidence, guess, 0

    def complete(self, messages, schema, purpose="intent"):
        self.calls += 1
        if purpose == "guess":
            return LLMReply(json.dumps({"guess": self.guess}), 1, 0, 0)
        return LLMReply(json.dumps({"choice": self.choice, "args": NO_ARGS, "confidence": self.confidence,
                                    "options": ["P1", "P2", "P3", "P4"]}), 1, 0, 0)


print("-- 1. Don't trust 'high' on a partial match --")
verdict = support.compare(PEPSI, LEARNED)
check("Pepsi vs learned example: contradicts", verdict[0], "contradicts")
check("  ...and says why", "'pep see' instead of 'wah tur'" in verdict[1], True)
check("Pepsi, clear speech, vs the phrase", support.compare("could I have some pepsi", WATER)[0], "contradicts")
check("exact repeat supports", support.compare(LEARNED, LEARNED)[0], "supports")
check("one letter off supports", support.compare("aim thur stee cud ey haf sum wah tur", LEARNED)[0], "supports")
check("one extra short word supports", support.compare(LEARNED + " pleez", LEARNED)[0], "supports")
check("start dropped supports", support.compare("cud eye haf sum wah tur", LEARNED)[0], "supports")
check("two unexplained extra words contradict", support.compare(PEPSI, "cud eye haf sum")[0], "contradicts")
check("garbled vs clean phrase: unrelated (model decides)", support.compare(PEPSI, WATER)[0], "unrelated")

p = with_water_example()
intent = repair(PEPSI, p, Model())
check("THE PEPSI CASE: model says HIGH water -> lowered to low", intent.confidence, "low")
check("  ...reason recorded", any("pep see" in x for x in intent.problems), True)
check("learned sentence itself stays high", repair(LEARNED, p, Model()).confidence, "high")
check("learned sentence + 'pleez' stays high", repair(LEARNED + " pleez", p, Model()).confidence, "high")
check("nothing learned, garbled: model's high stands", repair(PEPSI, copy.deepcopy(BASE), Model()).confidence, "high")
check("clear 'could I have some pepsi': lowered", repair("could I have some pepsi", copy.deepcopy(BASE),
                                                         Model()).confidence, "low")
p = copy.deepcopy(BASE)
add_example(p, "cud eye haf sum", WATER)
check("fast path refused when extra words contradict", fast_path("cud eye haf sum pep", p) is None
      and fast_path(PEPSI, p) is None, True)
check("fast path still used for a true repeat", fast_path("cud eye haf sum", p).intent.choice, WATER)


print("-- Demo sessions --")


def session(typed, model=None, profile=None):
    """Run the demo. `typed` holds every keyboard line in order, including what is
    typed in each correction window ("" = nothing typed)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "profile.json"
        path.write_text(json.dumps(profile or BASE))
        inputs, printed, said = iter(typed + ["q"]), [], []
        model = model or Model()
        real_input, real_print = builtins.input, builtins.print
        builtins.input = lambda prompt="": (printed.append(str(prompt)), next(inputs))[1]
        builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
        demo_text.get_llm = lambda: model
        demo_text.get_tts = lambda: type("V", (), {"speak": lambda s, t: said.append(t)})()
        demo_text.wait_for_cancel = lambda seconds: next(inputs) or None
        try:
            demo_text.main(["--profile", str(path)])
        finally:
            builtins.input, builtins.print = real_input, real_print
        return printed, said, open_profile(path), model.calls


def has(printed, text):
    return any(text in line for line in printed)


# The full stress-test scenario.
printed, said, p, _ = session([LEARNED, "", PEPSI, "n", "4", "y", ""], profile=with_water_example())
check("Pepsi: asks 'Did you mean' instead of acting", has(printed, f"Did you mean: {WATER}"), True)
check("  ...'no' then options include 'Something else'", has(printed, f"4. {SOMETHING_ELSE}"), True)
check("  ...guess confirmed: Pepsi spoken and learned",
      (said[-1], has(printed, f"Learned: '{PEPSI}' -> 'Could I have some Pepsi?'")),
      ("Could I have some Pepsi?", True))
check("  ...water was not said for Pepsi", said, [WATER, "Could I have some Pepsi?"])

print("-- 2. Correction window --")
printed, said, p, _ = session([LEARNED, "n", "2", ""], profile=with_water_example())
check("high-confidence action, then 'n': cancelled", has(printed, f"CANCELLED: '{WATER}'"), True)
check("  ...options shown without the cancelled one, with 'Something else'",
      (has(printed, f". {WATER}"), has(printed, f"4. {SOMETHING_ELSE}")), (False, True))
picked = said[-1]
check("  ...option 2 spoken instead of water", (len(said), picked != WATER), (2, True))
check("  ...and learned", (has(printed, f"Learned: '{LEARNED}' -> '{picked}'"),
                           [e["meant"] for e in p["examples"]][-1]), (True, picked))

printed, said, p, _ = session([PEPSI, "y", "n", ""], model=Model(confidence="low"))
check("confirmed with 'y', then 'n' in the window: learning undone",
      (has(printed, f"Learned: '{PEPSI}' -> '{WATER}'"), has(printed, f"UNDONE: forgot '{PEPSI}'")), (True, True))
check("  ...nothing left saved", p["examples"], [])

printed, said, p, _ = session([PEPSI, "n", "4", "y", "no", ""], model=Model(confidence="low"))
check("new phrase confirmed, then cancelled: phrase removed too",
      (has(printed, "and removed the phrase it added"), "Could I have some Pepsi?" in p["phrases"]), (True, False))

printed, said, _, calls = session([LEARNED, "y"], profile=with_water_example())
check("'y' in the window keeps it", (has(printed, "CANCELLED"), said), (False, [WATER]))
printed, said, _, _ = session([LEARNED, ""], profile=with_water_example())
check("nothing typed in the window keeps it", (has(printed, "CANCELLED"), said), (False, [WATER]))
printed, said, _, _ = session([LEARNED, "baffroom pleez", ""], profile=with_water_example(),
                              model=Model(choice="P2"))
check("a sentence typed in the window is the next sentence, not a cancel",
      (has(printed, "CANCELLED"), has(printed, "> baffroom pleez")), (False, True))
printed, said, _, _ = session([LEARNED, "n", ""], profile=with_water_example())
check("cancel, then pick nothing: 'Nothing chosen'", has(printed, "Nothing chosen."), True)

print("-- 3. Answers are never sentences --")
for word in ["y", "n", "yes", "no", "Y", "No"]:
    printed, said, _, calls = session([word])
    check(f"'{word}' at the sentence prompt: not sent to the model", (calls, said), (0, []))
printed, _, _, calls = session(["undo"])
check("'undo' at the sentence prompt: not a sentence", (calls, has(printed, "Nothing to undo.")), (0, True))
printed, _, _, _ = session(["n"])
check("  ...and says why", has(printed, "is an answer, not a sentence"), True)

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
