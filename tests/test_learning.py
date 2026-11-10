"""Checks learning from confirmations: saving examples, the fast path (and when
it must NOT be used), learned examples in the prompt, undo, and --fresh.
Uses a stand-in model and throwaway profiles (no LM Studio needed). Run:
    .venv/bin/python tests/test_learning.py
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
from repair.learning import fast_path, prompt_examples  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.profile import load_profile  # noqa: E402
from repair.prompt import build_messages  # noqa: E402
from repair.store import add_example, fresh_profile, open_profile, undo_last  # noqa: E402

BASE = load_profile(config.BASE_PROFILE_PATH)
P = BASE["phrases"]
NO_ARGS = {"app": "", "text": ""}
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


def profile_with(*examples):
    p = copy.deepcopy(BASE)
    p["examples"] = []
    for heard, meant, *args in examples:
        add_example(p, heard, meant, args[0] if args else None)
    return p


print("-- Fast path --")
p = profile_with(("aim thur stee cud eye haf sum wah tur", P[0]), ("opn safri", "open_app", {"app": "Safari"}))
m = fast_path("aim thur stee cud eye haf sum wah tur", p)
check("same words again: fast path, 100%", (m.intent.choice, m.intent.confidence, round(m.similarity)),
      (P[0], "high", 100))
m = fast_path("aim thur stee cud eye haf sum wah ter", p)
check("one letter different: fast path", (m.intent.choice, m.similarity >= 90), (P[0], True))
check("command example keeps its details", fast_path("opn safri", p).intent.args, {"app": "Safari", "text": ""})
check("only loosely similar: no fast path (LLM decides)", fast_path("aim thirsty water", p), None)
check("nothing learned: no fast path", fast_path("anything", profile_with()), None)
p = profile_with(("uh", P[1]))
check("short text, exact: fast path", fast_path("uh", p).intent.choice, P[1])
check("short text, one letter off: no fast path", fast_path("uhh", profile_with(("uh", P[1]))), None)
p = profile_with(("pleez hep mee", P[3]), ("pleez hep mii", P[2]))
check("in between two different meanings (96% vs 88%): no fast path", fast_path("pleez hep me", p), None)
check("exact repeat of one of them (100% vs 85%): fast path", fast_path("pleez hep mee", p).intent.choice, P[3])
p = profile_with(("pleez hep mee", P[3]), ("pleez hep mii", P[3]))
check("two examples, same meaning: fast path", fast_path("pleez hep me", p).intent.choice, P[3])

print("-- Learned examples in the prompt --")
p = profile_with(*[(f"sentence number {i}", P[i % 4]) for i in range(15)])
shown = prompt_examples(p)
check("only the 10 most recent", (len(shown), shown[0][0], shown[-1][0]),
      (10, "sentence number 5", "sentence number 14"))
prompt = build_messages(profile_with(("opn safri", "open_app", {"app": "Safari"}), ("dotter", P[2])), "x")[0]["content"]
check("shown with codes and details", ('- "opn safri" -> open_app (app: Safari)' in prompt, '- "dotter" -> P3' in prompt),
      (True, True))
check("no examples: no examples section", "misheard before" in build_messages(profile_with(), "x")[0]["content"], False)

print("-- Undo --")
p = profile_with(("a b c d e", P[0]), ("f g h i j", P[1]))
removed, phrase_removed = undo_last(p)
check("removes the last example", (removed["heard"], len(p["examples"]), phrase_removed), ("f g h i j", 1, False))
p = profile_with()
p["phrases"].append("Open the window.")
add_example(p, "opn windo", "Open the window.", new_phrase=True)
undo_last(p)
check("undoing a new phrase's confirmation removes the phrase", "Open the window." in p["phrases"], False)
p = profile_with()
p["phrases"].append("Open the window.")
add_example(p, "opn da windo", "Open the window.", new_phrase=True)
add_example(p, "opn windo", "Open the window.")
p["examples"][-1]["new_phrase"] = True
undo_last(p)
check("...but keeps it if another example still uses it", "Open the window." in p["phrases"], True)
check("nothing to undo", undo_last(profile_with()), (None, False))

print("-- --fresh --")
with tempfile.TemporaryDirectory() as tmp:
    path, backups = Path(tmp) / "profile.json", Path(tmp) / "backups"
    p = open_profile(path)
    add_example(p, "x y z w v", P[0])
    path.write_text(json.dumps(p))
    backup = fresh_profile(path, backup_dir=backups)
    check("old profile backed up, with its example", len(json.loads(backup.read_text())["examples"]), 1)
    check("new profile is clean", len(open_profile(path)["examples"]), 0)
    check("base profile untouched", load_profile(config.BASE_PROFILE_PATH), BASE)


print("-- Live demo session (what judges would see) --")


class Model:
    def __init__(self, confidence="low"):
        self.calls, self.confidence = 0, confidence

    def complete(self, messages, schema, purpose="intent"):
        self.calls += 1
        if purpose == "guess":
            return LLMReply(json.dumps({"guess": "Can you open the window?"}), 1, 0, 0)
        return LLMReply(json.dumps({"choice": "P1", "args": NO_ARGS, "confidence": self.confidence,
                                    "options": ["P1", "P2", "P3", "P4"]}), 1, 0, 0)


def session(typed, argv=(), path=None, confidence="low"):
    inputs, printed, model = iter(typed + ["q"]), [], Model(confidence)
    real_input, real_print = builtins.input, builtins.print
    builtins.input = lambda prompt="": next(inputs)
    builtins.print = lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    demo_text.get_llm, demo_text.get_tts = (lambda: model), (lambda: type("V", (), {"speak": lambda s, t: None})())
    demo_text.wait_for_cancel = lambda seconds: None  # nothing typed in the correction window
    try:
        demo_text.main(["--profile", str(path), *argv])
    finally:
        builtins.input, builtins.print = real_input, real_print
    return printed, model.calls


def has(printed, text):
    return any(text in line for line in printed)


with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "profile.json"
    heard = "aim thur stee cud eye haf sum wah tur"
    printed, calls = session([heard, "y", heard, "aim thur stee cud eye haf sum wah ter"], ["--fresh"], path)
    check("--fresh announced", has(printed, "FRESH START"), True)
    check("confirm: 'Learned' printed",
          has(printed, f"Learned: '{heard}' -> '{P[0]}'"), True)
    check("same sentence again: FAST PATH printed", has(printed, "FAST PATH: matched a learned example (100% similar) in"), True)
    check("slightly different: FAST PATH too", has(printed, "FAST PATH: matched a learned example (97% similar)"), True)
    check("LLM called only once (the first time)", calls, 1)
    check("learned example saved in the profile", [e["heard"] for e in open_profile(path)["examples"]], [heard])

    printed, calls = session(["undo", heard, "n", "4", "y"], [], path)
    check("undo: says what was forgotten", has(printed, f"UNDONE: forgot '{heard}' -> '{P[0]}'"), True)
    check("after undo: LLM used again, no fast path", (has(printed, "FAST PATH"), calls >= 1), (False, True))
    check("'Something else' confirmed: new phrase + Learned",
          (has(printed, 'New phrase added as P5: "Can you open the window?"'),
           has(printed, f"Learned: '{heard}' -> 'Can you open the window?'")), (True, True))
    printed, _ = session(["undo"], [], path)
    check("undo after new phrase: phrase removed too",
          (has(printed, "and removed the phrase it added"), "Can you open the window?" in open_profile(path)["phrases"]),
          (True, False))
    printed, _ = session(["undo"], [], path)
    check("nothing left to undo", has(printed, "Nothing to undo."), True)

    printed, calls = session(["wah tur pleez"], [], path, confidence="high")
    p = open_profile(path)
    check("high-confidence answers are NOT learned (nobody confirmed them)", len(p["examples"]), 0)
    printed, _ = session([], ["--fresh"], path)
    check("--fresh again: backup made", has(printed, "previous profile backed up to"), True)

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
