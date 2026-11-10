"""Try speech recognition on its own:

    .venv/bin/python -m asr path/to/clip.wav            (uses config.ASR: fake or real)
    .venv/bin/python -m asr path/to/clip.wav --real     (force real WhisperKit)
"""

import sys

import config

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if "--real" in sys.argv:
    config.ASR = "real"
from asr import get_asr  # noqa: E402

audio = args[0] if args else "test.wav"
result = get_asr().transcribe(audio)
print(f"ASR ({config.ASR}, {result.backend}) heard: {result.text!r}")
print(f"  raw: {result.raw_text!r}  confidence: {result.avg_logprob:.2f}  took {result.latency_ms:.0f} ms"
      + (f"  problem: {result.problem}" if result.problem else ""))
