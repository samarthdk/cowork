"""Checks how live audio is cut into requests, with made-up audio (no
microphone needed). Run:
    .venv/bin/python tests/test_mic.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from asr.audio import RATE  # noqa: E402
from asr.mic import FRAME, Endpointer  # noqa: E402

rng = np.random.default_rng(1)
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got!r}, expected {want!r}")


def room(seconds, level=0.0005):
    """Background hiss."""
    return (rng.standard_normal(int(seconds * RATE)) * level).astype(np.float32)


def voice(seconds, level=0.05):
    """Something speech-like: a buzzing tone that swells and fades, over the room hiss."""
    t = np.arange(int(seconds * RATE)) / RATE
    wave = np.sin(2 * np.pi * 140 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
    return (wave * level).astype(np.float32) + room(seconds)


def run(audio, endpointer=None):
    """Feed audio through an Endpointer; return (segments, the time each ended)."""
    endpointer = endpointer or Endpointer()
    found = []
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        segment = endpointer.feed(audio[i:i + FRAME])
        if segment is not None:
            found.append((segment, (i + FRAME) / RATE))
    return found


print("-- Finding requests --")
found = run(np.concatenate([room(1.0), voice(1.2), room(2.5)]))
check("one sentence then silence: one request", len(found), 1)
segment, ended = found[0]
check("  ...ended about 1.5 s after the speech stopped", 3.5 <= ended <= 3.9, True)
check("  ...contains the speech plus a little before", 1.3 <= segment.duration_s <= 2.2, True)
check("  ...speech time measured", 1.0 <= segment.speech_s <= 1.4, True)
check("silence only: nothing", len(run(room(5.0))), 0)

found = run(np.concatenate([room(1.0), voice(0.8), room(0.9), voice(0.8), room(2.5)]))
check("a 0.9 s pause inside a sentence does not split it", (len(found), found[0][0].speech_s > 1.4), (1, True))
found = run(np.concatenate([room(1.0), voice(0.8), room(2.0), voice(0.8), room(2.5)]))
check("a 2 s pause separates two requests", len(found), 2)

print("-- Quiet speech is never discarded --")
found = run(np.concatenate([room(1.5, 0.0003), voice(1.0, level=0.004), room(2.5, 0.0003)]))
check("very soft speech in a quiet room is still a request", len(found), 1)
check("  ...and its loudness is reported (so it can be boosted)", found[0][0].level_dbfs < -45 if found else None, True)
found = run(np.concatenate([room(1.5, 0.004), voice(1.0, level=0.08), room(2.5, 0.004)]))
check("normal speech in a noisy room is a request", len(found), 1)
found = run(np.concatenate([room(1.5, 0.004), voice(1.0, level=0.004), room(2.5, 0.004)]))
check("speech no louder than the room noise is not picked up (nothing to go on)", len(found), 0)

print("-- Things that are not requests --")
click = np.concatenate([room(1.0), voice(0.08, level=0.3), room(2.5)])
check("a short click or knock: ignored", len(run(click)), 0)
found = run(np.concatenate([room(1.0), voice(30.0), room(2.0)]))
check("endless sound is cut at the safety limit", (len(found) >= 1, found[0][0].duration_s <= 20.5), (True, True))

print("-- Adapting to the room --")
e = Endpointer()
run(room(3.0, 0.0003), e)
quiet_threshold = e.threshold
run(room(6.0, 0.006), e)
check("the threshold rises within about 5 s when the room gets noisier", e.threshold > quiet_threshold * 3, True)
found = run(np.concatenate([room(0.2, 0.006), room(6.0, 0.006)]), Endpointer())
check("starting in a noisy room does not get stuck 'hearing speech'", len(found) <= 1, True)
e = Endpointer()
run(np.concatenate([room(1.0), voice(1.0)]), e)
check("speech itself does not raise the room level", e.threshold < 0.01, True)
e.reset()
check("reset drops a half-heard request", (e.in_speech, len(e.frames)), (False, 0))
e2 = Endpointer(end_silence_s=0.6)
found = run(np.concatenate([room(1.0), voice(0.5), room(1.0)]), e2)
check("a shorter pause can be configured (for yes/no answers)", len(found), 1)

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
