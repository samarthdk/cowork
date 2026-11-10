"""Checks speech-recognition helpers without a microphone or WhisperKit:
cleaning non-speech markers, noise filtering, the gentle volume boost, and
the fake recognizer. Run:
    .venv/bin/python tests/test_asr.py
"""

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from asr.audio import MAX_GAIN_DB, PEAK_LIMIT, boost, boosted_copy, read_wav, speech_level_dbfs, write_wav  # noqa: E402
from asr.clean import clean_text, is_probably_noise  # noqa: E402
from asr.fake import FakeASR  # noqa: E402

results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


print("-- Cleaning what WhisperKit returns --")
check("[BLANK_AUDIO] alone is nothing", clean_text("[BLANK_AUDIO]"), "")
check("[Inaudible] alone is nothing", clean_text(" [Inaudible] "), "")
check("*panting* alone is nothing", clean_text("*Panting*"), "")
check("(music) alone is nothing", clean_text("(music)"), "")
check("marker inside speech is removed", clean_text("I need [Inaudible] water"), "I need water")
check("special tokens removed", clean_text("<|startoftranscript|> hello <|endoftext|>"), "hello")
check("only punctuation is nothing", clean_text(" ... "), "")
check("normal speech untouched", clean_text("I'm thirsty. Could I have some water?"),
      "I'm thirsty. Could I have some water?")

print("-- Noise filtering --")
check("empty text is noise", is_probably_noise("", -0.1, 0.0), True)
check("'no speech' flag is noise", is_probably_noise("hello there", -0.2, 0.8), True)
check("'Thank you.' said unsurely is noise (a known silence artefact)", is_probably_noise("Thank you.", -1.1, 0.1), True)
check("'Thank you.' said clearly is kept", is_probably_noise("Thank you.", -0.1, 0.0), False)
check("unsure but real words are kept (impaired speech is often 'unsure')",
      is_probably_noise("she adds more than three", -0.9, 0.1), False)

print("-- Gentle volume boost --")
rate = 16000
t = np.arange(rate) / rate
tone = np.sin(2 * np.pi * 220 * t).astype(np.float32)
quiet = np.concatenate([np.zeros(rate, np.float32), 0.01 * tone, np.zeros(rate, np.float32)])  # soft speech
louder, gain = boost(quiet, rate)
check("quiet speech is turned up", gain > 10, True)
check("  ...to about the target level", abs(speech_level_dbfs(louder, rate) - (-20)) < 2 or gain == MAX_GAIN_DB, True)
check("  ...never more than the maximum", gain <= MAX_GAIN_DB, True)
normal = 0.3 * tone
_, gain = boost(normal, rate)
check("normal speech is left alone", gain, 0.0)
loud = 0.9 * tone
out, gain = boost(loud, rate)
check("loud speech is never turned down", (gain, float(np.max(np.abs(out))) <= 0.9001), (0.0, True))
spiky = quiet.copy()
spiky[100] = 0.9   # one loud click must not cause clipping
out, gain = boost(spiky, rate)
check("a loud click does not make it clip", float(np.max(np.abs(out))) <= PEAK_LIMIT + 1e-6, True)
check("silence stays silence", boost(np.zeros(rate, np.float32), rate)[1] <= MAX_GAIN_DB, True)
check("empty clip is fine", boost(np.zeros(0, np.float32), rate)[1], 0.0)
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "quiet.wav"
    write_wav(path, quiet, rate)
    samples, r = read_wav(path)
    check("write then read keeps the audio", (r, len(samples)), (rate, len(quiet)))
    boosted, gain = boosted_copy(path)
    check("boosted copy is a different file, louder", (boosted != path, gain > 10), (True, True))
    path2 = Path(tmp) / "normal.wav"
    write_wav(path2, normal, rate)
    check("clip that needs no boost is used as it is", boosted_copy(path2), (path2, 0.0))

    print("-- Fake recognizer --")
    check("fixed transcript", FakeASR("hello").transcribe(str(path)).text, "hello")
    path.with_suffix(".txt").write_text("aim thur stee\n")
    check("a .txt next to the clip is its transcript", FakeASR("hello").transcribe(str(path)).text, "aim thur stee")

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
