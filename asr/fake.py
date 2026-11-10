"""Fake ASR: no model. Returns a fixed transcript so other parts can be tested."""

import time

from metrics import log_call


class FakeASR:
    def __init__(self, transcript: str = "wanna watr pleez"):
        self.transcript = transcript

    def transcribe(self, audio_path: str) -> str:
        start = time.perf_counter()
        text = self.transcript
        log_call("asr", "fake", (time.perf_counter() - start) * 1000, audio=audio_path)
        return text
