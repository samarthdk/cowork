"""Fake ASR: no model. Lets everything else be built and tested without WhisperKit.

It returns, in this order of preference:
  1. the text in a file next to the clip with the same name and ".txt"
     (so recorded demo clips can carry their own transcript),
  2. the fixed transcript given when it was created.
"""

import time
from pathlib import Path

from asr.base import Transcript
from metrics import log_call


class FakeASR:
    def __init__(self, transcript: str = "wanna watr pleez"):
        self.transcript = transcript

    def transcribe(self, audio_path: str, prompt: str = "") -> Transcript:
        start = time.perf_counter()
        sidecar = Path(str(audio_path)).with_suffix(".txt")
        text = sidecar.read_text().strip() if sidecar.exists() else self.transcript
        latency = (time.perf_counter() - start) * 1000
        log_call("asr", "fake", latency, audio=Path(str(audio_path)).name)
        return Transcript(text, text, -0.1, 0.0, 0.0, latency, "fake")
