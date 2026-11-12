"""The conversation, written once and used by every front-end.

    session = Session(profile, llm, tts)
    flow = session.hear("aim thur stee cud eye haf sum wah tur")

`hear()` runs the whole flow for one thing the person said: the fast path or
the Brain, "Did you mean ...?", the options grid, "Something else" guesses,
speaking or acting, learning, and the correction window. Whenever it needs an
answer it pauses and hands back a Question; the front-end (terminal, web page,
voice, hand signals) sends in an Answer and it carries on:

    question = next(flow)
    while True:
        answer = ...get an Answer from the person, by any means...
        try:
            question = flow.send(answer)
        except StopIteration as finished:
            next_sentence = finished.value   # something said during the correction window
            break

Everything worth showing (what was heard, the Brain's answer, "Learned: ...",
FAST PATH ...) is reported through `on_event` as small dictionaries, so each
front-end can display it in its own way.

Rules kept from testing:
  - Risky commands (send, call, delete) are always confirmed first and have no
    correction window afterwards (they cannot be taken back).
  - Only confirmed answers are learned from.
  - Cancelling in the correction window undoes what was learned that turn.
"""

import threading
import time
from dataclasses import dataclass, field

import config
from choose.answers import Answer
from repair import confirm as listen_again
from repair import repair, warm_up
from repair.guess import MAX_GUESSES, free_guess, is_repeat
from repair.learning import fast_path
from repair.profile import all_choices, find_command, is_risky, phrase_codes
from repair.rank import SOMETHING_ELSE, group_of, rank_options, score
from repair.store import (add_example, add_phrase, add_vocabulary, delete_example,
                          save_profile, similarity, undo_last)

CORRECTION_S = 3.0        # after acting, this long to say "no" and cancel
FORGET_SIMILARITY = 95    # cancelling a fast-path answer this similar deletes its example


@dataclass
class Question:
    kind: str                     # "yes_no", "choice", "window", "detail"
    stage: str                    # "did_you_mean", "guess", "options", "correction", "detail"
    text: str                     # what is being asked, in plain words
    options: list = field(default_factory=list)   # "choice": up to 4, in grid order
    seconds: float | None = None  # "window": how long it stays open
    data: dict = field(default_factory=dict)       # extras for display


@dataclass
class Result:
    choice: str
    args: dict
    how: str                 # "confident" (not learned from), "confirmed", "picked", "new phrase"
    new_words: list = field(default_factory=list)  # added to the vocabulary


def sentence_for(choice: str, args: dict, profile: dict) -> str:
    """What to say out loud: the phrase itself, or a short confirmation for a command."""
    command = find_command(profile, choice)
    if command is None:
        return choice
    try:
        return command["say"].format(**args)
    except (KeyError, IndexError):
        return command["say"]


def describe(choice: str, args: dict, profile: dict) -> str:
    """How to show a choice: the phrase itself, or the command and its details."""
    command = find_command(profile, choice)
    if command is None:
        return choice
    details = ", ".join(f"{k}: {v}" for k, v in args.items() if v and k in command["args"])
    return f"{choice} ({details})" if details else choice


def did_you_mean(choice: str, args: dict, profile: dict) -> str:
    text = describe(choice, args, profile)
    if text.endswith("?"):
        return f"Did you mean: {text}"
    return f"Did you mean: {text.rstrip('.!')}?"


class Session:
    def __init__(self, profile: dict, llm, tts, actions=None, profile_path=None,
                 on_event=None, warm: bool = False):
        self.profile, self.llm, self.tts, self.actions = profile, llm, tts, actions
        self.profile_path = profile_path
        self.on_event = on_event or (lambda event: None)
        self.warm = warm          # prime LM Studio in the background after the prompt changes
        self._rehear = None       # set per request when it came from audio
        self._warming: threading.Thread | None = None

    # ---- helpers ---------------------------------------------------------

    def emit(self, kind: str, **data) -> None:
        self.on_event({"kind": kind, **data})

    def save(self) -> None:
        if self.profile_path is not None:
            save_profile(self.profile, self.profile_path)

    def context(self) -> dict | None:
        """Real values for command details (installed apps, things on screen)."""
        if self.actions is None:
            return None
        try:
            return self.actions.context()
        except Exception as error:   # never let a failed screen read stop the conversation
            self.emit("problem", text=f"Couldn't read what is available on the Mac: {error}")
            return None

    def warm_up(self, wait: bool = False) -> None:
        """Have LM Studio read the current prompt now, so the next real request is fast."""
        if self._warming and self._warming.is_alive():
            return
        context = self.context()

        def run():
            took = warm_up(self.profile, self.llm, context)
            self.emit("warmed", took_ms=round(took))

        self._warming = threading.Thread(target=run, daemon=True)
        self._warming.start()
        if wait:
            self._warming.join()

    def undo(self) -> None:
        """Forget the last thing learned (the `undo` command)."""
        removed, phrase_removed = undo_last(self.profile)
        if removed is None:
            self.emit("nothing_to_undo")
            return
        self.save()
        self._emit_undone(removed, phrase_removed, describe(removed["meant"], removed["args"], self.profile))
        self._changed()

    def _emit_undone(self, removed: dict, phrase_removed: bool, meaning: str) -> None:
        self.emit("undone", heard=removed["heard"], meaning=meaning,
                  phrase=removed["meant"] if phrase_removed else None,
                  words=list(removed.get("new_words", [])))

    def _changed(self) -> None:
        """The profile (and so the prompt) changed."""
        self.emit("profile_changed", examples=len(self.profile.get("examples", [])),
                  phrases=len(self.profile["phrases"]))
        if self.warm:
            self.warm_up()

    # ---- the flow --------------------------------------------------------

    def hear(self, heard: str, source: str = "keyboard", rehear=None):
        """Generator: yields Questions, receives Answers, returns a pending
        sentence (said during the correction window) or None.
        `rehear(hint) -> Transcript`: given when the request came from audio, so a
        guess can be checked against the sound itself (see repair/confirm.py)."""
        profile = self.profile
        self._rehear = rehear
        self.emit("heard", text=heard, source=source)
        context = self.context()

        match = fast_path(heard, profile)
        if match:
            intent = match.intent
            intent.stats = {"source": "fast_path", "latency_ms": round(match.latency_ms, 2),
                            "similarity": round(match.similarity)}
            self.emit("fast_path", similarity=match.similarity, latency_ms=match.latency_ms,
                      example_heard=match.example["heard"], exact_phrase=bool(match.example.get("builtin")),
                      meaning=describe(intent.choice, intent.args, profile), intent=intent.to_dict())
        else:
            start = time.perf_counter()
            intent = repair(heard, profile, self.llm, context)
            took_ms = (time.perf_counter() - start) * 1000
            self.emit("brain", intent=intent.to_dict(), took_ms=took_ms,
                      meaning=describe(intent.choice, intent.args, profile) if intent.choice else None)
            self._confirm_by_sound(intent)

        result = yield from self._decide(heard, intent, context)
        rejected = ()
        pending = None
        while result is not None:
            choice, args, how = result.choice, result.args, result.how
            learned = how != "confident"   # only confirmed answers are learned from
            if learned:
                add_example(profile, heard, choice, args, new_phrase=(how == "new phrase"),
                            new_words=result.new_words)
                self.emit("learned", heard=heard, meaning=describe(choice, args, profile))
            self.save()
            if learned:
                self._changed()
            outcome = self._perform(choice, args)

            if is_risky(profile, choice):
                self.emit("risky_done")
                break
            # Correction window: "no" cancels, undoes what was learned, shows the options.
            answer = yield Question("window", "correction", "Say no (or raise your LEFT hand) to cancel",
                                    seconds=CORRECTION_S,
                                    data={"meaning": describe(choice, args, profile)})
            if answer is None or answer.kind in ("none", "yes"):
                break
            if answer.kind == "text":
                pending = answer.value   # not a correction: it is the next sentence
                break
            self.emit("cancelled", meaning=describe(choice, args, profile))
            self._take_back(outcome)
            if (match and not match.example.get("builtin")
                    and similarity(heard, match.example["heard"]) >= FORGET_SIMILARITY):
                # The learned example that gave this answer was wrong for these words.
                delete_example(profile, match.example)
                self.emit("forgot", heard=match.example["heard"],
                          meaning=describe(match.example["meant"], match.example["args"], profile))
                self.save()
            match = None
            if learned:
                removed, phrase_removed = undo_last(profile)
                self._emit_undone(removed, phrase_removed, describe(choice, args, profile))
                self.save()
            self._changed()
            rejected += (choice,)
            result = yield from self._choose_from_options(heard, intent, rejected, context)
        else:
            self.emit("nothing_chosen")
        self.emit("done")
        return pending

    def _confirm_by_sound(self, intent) -> None:
        """Unsure, but there is audio: listen again for exactly this guess. If the
        sound backs it up, it can be spoken without asking. Never used when a
        part of what was heard contradicts the guess (the Pepsi case)."""
        if (self._rehear is None or not config.AUDIO_CONFIRM or intent.confidence != "low" or not intent.choice
                or any("instead of" in p or "extra words" in p for p in intent.problems)):
            return
        ok, details = listen_again.confirm(intent.choice, self.profile, self._rehear)
        if not details:
            return
        intent.stats["sound_check"] = details
        if ok:
            intent.confidence = "high"
            intent.stats["confirmed_by_sound"] = True
        self.emit("sound_check", confirmed=ok, meaning=describe(intent.choice, intent.args, self.profile),
                  intent=intent.to_dict(), **details)

    def _decide(self, heard: str, intent, context):
        """Work out what the person meant. Risky commands are always confirmed
        first, even at high confidence."""
        profile = self.profile
        if intent.confidence == "high" and not is_risky(profile, intent.choice):
            return Result(intent.choice, intent.args, "confident")

        rejected = ()
        if intent.choice:
            risky = is_risky(profile, intent.choice)
            if risky:
                self.emit("risky_notice")
            answer = yield Question("yes_no", "did_you_mean", did_you_mean(intent.choice, intent.args, profile),
                                    data={"choice": intent.choice, "args": intent.args, "risky": risky,
                                          "meaning": describe(intent.choice, intent.args, profile)})
            if answer is None or answer.kind == "none":
                return None   # nobody answered: silence is not a "no", so nothing is chosen
            if answer.kind == "yes":
                args = yield from self._fill_missing(intent.choice, intent.args, context)
                return Result(intent.choice, args, "confirmed")
            rejected = (intent.choice,)
        return (yield from self._choose_from_options(heard, intent, rejected, context))

    def _choose_from_options(self, heard: str, intent, rejected: tuple, context):
        """Next-best options (most similar first, rejected ones left out) plus
        "Something else"."""
        profile = self.profile
        second = None
        if rejected and self._rehear is not None and config.RELISTEN_AFTER_NO:
            # The guess was wrong, but there is audio: look through the phrases for
            # one that fits the sound (likeliest first). Its result is only offered.
            self.emit("relisten_start")
            order = sorted(all_choices(profile), key=lambda c: -score(heard, c, profile))
            second, details = listen_again.scan(profile, self._rehear, order, exclude=rejected,
                                                on_progress=lambda done, total: self.emit("relisten_progress", done=done, total=total))
            self.emit("relisten", meaning=second, **details)
        if rejected and second is None and config.SECOND_OPINION:
            # The first guess was wrong: ask the Brain once more with it ruled out,
            # and put its new pick in the first square.
            start = time.perf_counter()
            again = repair(heard, profile, self.llm, context, exclude=rejected)
            if again.choice and again.choice not in rejected:
                second = again.choice
            self.emit("second_opinion", meaning=second, took_ms=(time.perf_counter() - start) * 1000)
        options = rank_options(heard, profile, intent.options, exclude=rejected,
                               first_group=group_of(intent.choice, profile), first=second)
        answer = yield Question("choice", "options", "Which one did you mean?", options=options)
        if answer is None or answer.kind != "pick":
            return None
        picked = options[answer.value]
        if picked == SOMETHING_ELSE:
            return (yield from self._something_else(heard))
        args = intent.args if find_command(profile, picked) else {}
        args = yield from self._fill_missing(picked, args, context)
        return Result(picked, args, "picked")

    def _fill_missing(self, choice: str, args: dict, context):
        """A command may still be missing details (e.g. which app): ask for them."""
        args = dict(args)
        command = find_command(self.profile, choice)
        if command:
            for name in command["args"]:
                if not args.get(name):
                    answer = yield Question("detail", "detail", f"{name}?",
                                            data={"name": name, "command": choice,
                                                  "values": (context or {}).get(name)})
                    args[name] = str(answer.value).strip() if answer is not None and answer.kind == "text" else ""
        return args

    def _something_else(self, heard: str):
        """Free guesses, up to MAX_GUESSES. Each new guess is told the rejected ones.
        A confirmed guess becomes a phrase; its important words join the vocabulary."""
        profile = self.profile
        rejected = []
        for attempt in range(1, MAX_GUESSES + 1):
            start = time.perf_counter()
            guess, problem = free_guess(heard, profile, self.llm, rejected)
            took_ms = (time.perf_counter() - start) * 1000
            if guess is None:
                self.emit("guess_failed", attempt=attempt, problem=problem)
                continue
            if is_repeat(guess.text, rejected):
                self.emit("guess_repeat", attempt=attempt, text=guess.text)
                continue
            self.emit("guess_ready", attempt=attempt, total=MAX_GUESSES, took_ms=took_ms, text=guess.text)
            answer = yield Question("yes_no", "guess", f'My guess {attempt} (NOT from your list): "{guess.text}"',
                                    data={"attempt": attempt, "total": MAX_GUESSES, "guess": guess.text})
            if answer is None or answer.kind == "none":
                return None   # nobody answered: stop guessing
            if answer.kind != "yes":
                rejected.append(guess.text)
                continue
            phrase, added = add_phrase(profile, guess.text)
            if added:
                code = next(c for c, p in phrase_codes(profile).items() if p == phrase)
                self.emit("new_phrase", code=code, phrase=phrase)
            else:
                self.emit("known_phrase", phrase=phrase)
            new_words = add_vocabulary(profile, guess.key_words, phrase)
            if new_words:
                self.emit("new_words", words=new_words)
            return Result(phrase, {}, "new phrase" if added else "picked", new_words)
        self.emit("no_more_guesses", total=MAX_GUESSES)
        return None

    # ---- speaking and acting ----------------------------------------------

    def _perform(self, choice: str, args: dict):
        """Speak a phrase, or carry out a command. Returns what was done (for undo)."""
        profile = self.profile
        command = find_command(profile, choice)
        say = sentence_for(choice, args, profile)
        if command is None or self.actions is None:
            self.emit("speak", text=say, kind_of="phrase" if command is None else "command")
            self._speak(say)
            return None
        self.emit("act", command=choice, args={k: v for k, v in args.items() if v}, say=say)
        self._speak(say)
        try:
            outcome = self.actions.run(choice, args)
        except Exception as error:   # an action must never crash the conversation
            from actions.base import ActionResult
            outcome = ActionResult(False, problem=f"{type(error).__name__}: {error}")
        self.emit("acted", command=choice, ok=outcome.ok, detail=outcome.detail, problem=outcome.problem)
        if outcome.ok and outcome.say:
            self.emit("speak", text=outcome.say, kind_of="result")
            self._speak(outcome.say)
        elif not outcome.ok:
            self._speak("Sorry, I couldn't do that.")
        return outcome

    def _speak(self, text: str) -> None:
        start = time.perf_counter()
        try:
            self.tts.speak(text)
        except Exception as error:   # the voice failing must not stop the flow
            self.emit("problem", text=f"Couldn't speak: {error}")
        self.emit("spoke", text=text, took_ms=(time.perf_counter() - start) * 1000)

    def _take_back(self, outcome) -> None:
        """Best effort to reverse what was just done (stop speaking, scroll back...)."""
        stop = getattr(self.tts, "stop", None)
        if stop:
            stop()
        if outcome is not None and getattr(outcome, "undo", None):
            try:
                outcome.undo()
                self.emit("action_undone")
            except Exception as error:
                self.emit("problem", text=f"Couldn't undo the action: {error}")


def answer_none() -> Answer:
    return Answer("none", source="timeout")
