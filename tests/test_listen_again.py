"""Checks "listening again" (repair/confirm.py and its use in the conversation),
without WhisperKit or LM Studio: the recording is faked by a function that
answers what WhisperKit would hear when given a hint.

  - confirm(): hint with only the Brain's guess; if it comes back clearly, the
    guess is spoken without asking.
  - scan(): after a "no", look through the phrases for one that fits the
    sound; it is only ever OFFERED, never acted on.
Run:
    .venv/bin/python tests/test_listen_again.py
"""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from asr.base import Transcript  # noqa: E402
from choose.answers import Answer  # noqa: E402
from engine.session import Session  # noqa: E402
from repair import confirm as listen  # noqa: E402
from repair.llm import LLMReply  # noqa: E402
from repair.profile import load_profile  # noqa: E402
from repair.rank import SOMETHING_ELSE  # noqa: E402

BASE = load_profile(config.BASE_PROFILE_PATH)
BASE["examples"] = []
P = BASE["phrases"]
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


class Ear:
    """A fake recording. Given a hint, it 'hears' `said` clearly if `said` is one
    of the hinted texts, otherwise mumble. Remembers every hint it was given."""
    def __init__(self, said=None, logprob=-0.1, fail=False):
        self.said, self.logprob, self.fail, self.hints = said, logprob, fail, []

    def __call__(self, hint):
        self.hints.append(hint)
        if self.fail:
            raise RuntimeError("WhisperKit server not running")
        if self.said and self.said in hint:
            return Transcript(self.said, avg_logprob=self.logprob)
        return Transcript("uh mm", avg_logprob=-0.9)


class Model:
    """Answers `first` (with `confidence`); asked again after a 'no', answers `second`."""
    def __init__(self, first="P1", confidence="low", second="P3"):
        self.first, self.confidence, self.second, self.calls = first, confidence, second, 0

    def complete(self, messages, schema, purpose="intent", temperature=0.0):
        self.calls += 1
        again = "NOT" in messages[-1]["content"]
        return LLMReply(json.dumps({"choice": self.second if again else self.first,
                                    "confidence": self.confidence}), 1, 0, 0)


def converse(heard, answers, model, ear=None, profile=None):
    events, said, asked = [], [], []
    session = Session(copy.deepcopy(profile or BASE), model,
                      type("V", (), {"speak": lambda s, t: said.append(t)})(), on_event=events.append)
    flow, answers = session.hear(heard, source="microphone", rehear=ear), list(answers)
    try:
        question = next(flow)
        while True:
            asked.append(question)
            question = flow.send(answers.pop(0) if answers else None)
    except StopIteration:
        return said, asked, events, session.profile


def kinds(events):
    return [e["kind"] for e in events]


def with_command_word(word="down", risky=False):
    """The sample profile, with a spoken word for scroll_down (and optionally risky)."""
    p = copy.deepcopy(BASE)
    c = next(c for c in p["commands"] if c["name"] == "scroll_down")
    c["said_as"] = [word]
    if risky:
        c["safety"] = "risky"
    return p


print("-- What to listen for --")
check("a phrase: its own words", listen.hint_text(P[0], BASE), P[0])
check("a command that needs details (open_app): nothing definite", listen.hint_text("open_app", BASE), None)
check("a command with no spoken word: nothing", listen.hint_text("scroll_down", BASE), None)
check("a command with a spoken word: that word", listen.hint_text("scroll_down", with_command_word()), "down")

print("-- Does it fit? --")
check("same words, WhisperKit sure: fits", listen.fits(Transcript(P[1], avg_logprob=-0.1), P[1])[0], True)
check("same words, WhisperKit unsure: does not fit", listen.fits(Transcript(P[1], avg_logprob=-0.8), P[1])[0], False)
check("only part of the words came back: does not fit",
      listen.fits(Transcript("use the", avg_logprob=-0.1), P[1])[0], False)
check("capitals and punctuation don't matter",
      listen.fits(Transcript("i need to use the bathroom", avg_logprob=-0.1), P[1])[0], True)

print("-- confirm() --")
ear = Ear(P[2])
ok, details = listen.confirm(P[2], BASE, ear)
check("the right guess is confirmed", ok, True)
check("  ...WhisperKit was hinted with exactly the guess", ear.hints, [P[2]])
check("  ...details for the screen", sorted(details), ["heard_again", "logprob", "match", "took_ms"])
check("a wrong guess is not confirmed", listen.confirm(P[0], BASE, Ear(P[2]))[0], False)
check("no recording (typed): nothing happens", listen.confirm(P[0], BASE, None), (False, {}))
ear = Ear(P[2])
check("nothing definite to listen for: WhisperKit is not even asked",
      (listen.confirm("open_app", BASE, ear), ear.hints), ((False, {}), []))
ok, details = listen.confirm(P[0], BASE, Ear(fail=True))
check("WhisperKit failing just means 'not confirmed'", (ok, "problem" in details), (False, True))

print("-- scan() --")
order = list(BASE["phrases"])
choice, details = listen.scan(BASE, Ear(P[3]), order)
check("finds the phrase that fits the sound", choice, P[3])
check("  ...and says how many it tried", details["tried"], len(order))
choice, _ = listen.scan(BASE, Ear(P[3]), order, exclude=(P[3],))
check("never offers a phrase already rejected", choice, None)
choice, details = listen.scan(BASE, Ear(None), order)
check("nothing fits: offers nothing", (choice, details["tried"]), (None, len(order)))
config.RELISTEN_CHUNK = 2
ear = Ear(P[0])
choice, details = listen.scan(BASE, ear, order)
check("phrases are hinted a few at a time, and it stops at the first fit",
      (choice, len(ear.hints), details["tried"]), (P[0], 1, 2))
progress = []
listen.scan(BASE, Ear(None), order, on_progress=lambda done, total: progress.append((done, total)))
check("reports progress for the screen", progress, [(0, 4), (2, 4)])
config.RELISTEN_CHUNK = 12
saved, config.RELISTEN_MAX_S = config.RELISTEN_MAX_S, -1
ear = Ear(P[0])
check("time limit: gives up rather than keep the person waiting",
      (listen.scan(BASE, ear, order)[0], ear.hints), (None, []))
config.RELISTEN_MAX_S = saved
choice, details = listen.scan(BASE, Ear(fail=True), order)
check("WhisperKit failing: offers nothing, says why", (choice, "problem" in details), (None, True))
check("commands with nothing definite to listen for are skipped",
      listen.scan(BASE, Ear(None), ["open_app", "scroll_down"])[1]["tried"], 0)

print("-- In the conversation: before asking --")
m = Model("P1", "low")
said, asked, events, profile = converse("aim thur stee", [None], m, ear=Ear(P[0]))
check("unsure, but the sound backs the guess: spoken without asking",
      (said, [q.stage for q in asked]), ([P[0]], ["correction"]))
check("  ...shown on screen as a sound check",
      next(e for e in events if e["kind"] == "sound_check")["confirmed"], True)
check("  ...and NOT learned (only the person's own confirmations are)", "learned" in kinds(events), False)
said, asked, events, _ = converse("aim thur stee", [Answer("yes"), None], Model("P1", "low"), ear=Ear(P[2]))
check("the sound doesn't back the guess: 'Did you mean?' as before",
      (asked[0].stage, next(e for e in events if e["kind"] == "sound_check")["confirmed"]), ("did_you_mean", False))
said, asked, events, _ = converse("aim thur stee", [Answer("yes"), None], Model("P1", "low"))
check("typed (no recording): no sound check, asks as before",
      ("sound_check" in kinds(events), asked[0].stage), (False, "did_you_mean"))
ear = Ear(P[0])
converse("I'm thirsty, could I have some water?", [None], Model("P1", "high"), ear=ear)
check("already sure: WhisperKit is not asked again", ear.hints, [])
ear = Ear(P[0])
said, asked, events, _ = converse("im thirsty could i have some pepsi", [Answer("none")], Model("P1", "high"), ear=ear)
check("part of what was heard contradicts the guess (the Pepsi case): no sound check",
      (ear.hints, asked[0].stage), ([], "did_you_mean"))
config.AUDIO_CONFIRM = False
ear = Ear(P[0])
said, asked, _, _ = converse("aim thur stee", [Answer("none")], Model("P1", "low"), ear=ear)
check("can be switched off (config.AUDIO_CONFIRM)", (ear.hints, asked[0].stage), ([], "did_you_mean"))
config.AUDIO_CONFIRM = True
said, asked, events, _ = converse("dow", [Answer("none")], Model("scroll_down", "low"),
                                  ear=Ear("down"), profile=with_command_word(risky=True))
check("a risky command is still asked first, even when the sound backs it up",
      (asked[0].stage, said), ("did_you_mean", []))
said, asked, events, _ = converse("aim thur stee", [Answer("none")], Model("P1", "low"), ear=Ear(fail=True))
check("WhisperKit failing mid-conversation: carries on and asks", asked[0].stage, "did_you_mean")

print("-- In the conversation: after a 'no' --")
m = Model("P1", "low", second="P2")
said, asked, events, _ = converse("plee kaw dor", [Answer("no"), Answer("pick", 0), None], m, ear=Ear(P[2]))
grid = next(q for q in asked if q.kind == "choice")
check("the phrase that fits the sound is offered in the first square", grid.options[0], P[2])
check("  ...the Brain is not asked a second time", m.calls, 1)
check("  ...it was only offered: nothing spoken until the person picked it", said, [P[2]])
check("  ...grid: 3 options + 'Something else', rejected one left out",
      (len(grid.options), grid.options[-1], P[0] in grid.options), (4, SOMETHING_ELSE, False))
check("  ...events for the screen", ("relisten_start" in kinds(events),
                                    next(e for e in events if e["kind"] == "relisten")["meaning"]), (True, P[2]))
m = Model("P1", "low", second="P2")
said, asked, events, _ = converse("plee kaw dor", [Answer("no"), Answer("none")], m, ear=Ear(None))
grid = next(q for q in asked if q.kind == "choice")
check("nothing fits the sound: the Brain's second opinion is used instead", (m.calls, grid.options[0]), (2, P[1]))
config.RELISTEN_AFTER_NO = False
ear = Ear(P[2])
converse("plee kaw dor", [Answer("no"), Answer("none")], Model("P1", "low"), ear=ear)
check("can be switched off (config.RELISTEN_AFTER_NO): only the guess was listened for", ear.hints, [P[0]])
config.RELISTEN_AFTER_NO = True

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
