"""Checking a guess against the sound itself ("listening again").

WhisperKit accepts a text hint with a clip. Two uses, both measured on real
impaired speech (TORGO speakers M04 and M05; see eval/relisten.py):

1. confirm(): hint WhisperKit with ONLY the Brain's guess (about 0.5 s). If the
   guess comes back nearly word for word and WhisperKit was confident, the
   sound backs the guess up and it can be spoken without asking. Measured:
   approved 157 of 171 right guesses and 0 of 166 wrong ones.

2. scan(): after the person says "no", hint with the profile's phrases a
   dozen at a time and look for one that fits the sound. Slower (WhisperKit's
   time grows with the hint), so it is only used after a "no", stops at the
   first fit and has a time limit. Measured on M04: when a phrase fits it is
   the right one 62 times in 63, and it finds about half of the phrases the
   transcript alone missed. It can also "hear" a listed phrase for a word that
   was not in the profile (4 in 60), so a scan result is only ever OFFERED to
   the person, never acted on.

`rehear` is a function the front-end provides when the request came from
audio: rehear(hint_text) -> Transcript of the same clip. Typed requests have
no audio, so none of this applies to them.
"""

import time

import config
from repair.profile import find_command
from repair.store import normalize
from repair.support import _coverage


def hint_text(choice: str, profile: dict) -> str | None:
    """The words to listen for: a phrase's own words, or the word this person
    uses for a command. None if there is nothing definite to listen for
    (a command with details, or with no known spoken word)."""
    command = find_command(profile, choice)
    if command is None:
        return choice
    if command["args"] or not command.get("said_as"):
        return None
    return command["said_as"][0]


def fits(transcript, text: str) -> tuple[bool, float]:
    """(does the hinted text fit what came back, how well its words match 0..1)."""
    a, b = _coverage(normalize(transcript.text).split(), normalize(text).split())
    match = min(a, b)
    return (match >= config.AUDIO_CONFIRM_MATCH and transcript.avg_logprob >= config.AUDIO_CONFIRM_LOGPROB), match


def confirm(choice: str, profile: dict, rehear) -> tuple[bool, dict]:
    """Does the sound back this guess up? Returns (confirmed, details for display)."""
    text = hint_text(choice, profile)
    if text is None or rehear is None:
        return False, {}
    start = time.perf_counter()
    try:
        transcript = rehear(text)
    except Exception as error:   # the recognizer failing just means "not confirmed"
        return False, {"problem": f"{type(error).__name__}: {error}"}
    ok, match = fits(transcript, text)
    return ok, {"heard_again": transcript.text, "match": round(match, 2),
                "logprob": round(transcript.avg_logprob, 2), "took_ms": round((time.perf_counter() - start) * 1000)}


def scan(profile: dict, rehear, order: list[str], exclude: tuple = (), on_progress=None) -> tuple[str | None, dict]:
    """Look through the profile for a phrase that fits the sound. `order`: the
    choices to try, likeliest first. Returns (the best-fitting choice or None, details)."""
    start = time.perf_counter()
    candidates = [(c, hint_text(c, profile)) for c in order if c not in exclude]
    candidates = [(c, t) for c, t in candidates if t]
    size, tried = config.RELISTEN_CHUNK, 0
    for i in range(0, len(candidates), size):
        if time.perf_counter() - start > config.RELISTEN_MAX_S:
            break
        chunk = candidates[i:i + size]
        if on_progress:
            on_progress(i, len(candidates))
        try:
            transcript = rehear(" ".join(text for _, text in chunk))
        except Exception as error:
            return None, {"problem": f"{type(error).__name__}: {error}"}
        tried += len(chunk)
        found = [(match, choice) for choice, text in chunk for ok, match in [fits(transcript, text)] if ok]
        if found:
            match, choice = max(found)
            return choice, {"heard_again": transcript.text, "match": round(match, 2), "tried": tried,
                            "logprob": round(transcript.avg_logprob, 2),
                            "took_ms": round((time.perf_counter() - start) * 1000)}
    return None, {"tried": tried, "took_ms": round((time.perf_counter() - start) * 1000)}
