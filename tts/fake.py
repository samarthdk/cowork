"""Fake TTS: prints what it would say instead of making sound."""

import time

from metrics import log_call


class FakeTTS:
    def speak(self, text: str) -> None:
        start = time.perf_counter()
        print(f"[would say] {text}")
        log_call("tts", "fake", (time.perf_counter() - start) * 1000, chars=len(text))
