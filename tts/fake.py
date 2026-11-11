"""Fake TTS: prints what it would say instead of making sound."""

import time

from metrics import log_call


class FakeTTS:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self.said: list[str] = []

    def speak(self, text: str) -> None:
        start = time.perf_counter()
        self.said.append(text)
        if not self.quiet:
            print(f"[would say] {text}")
        log_call("tts", "fake", (time.perf_counter() - start) * 1000, chars=len(text))

    def prepare(self, texts: list[str]) -> None:
        pass

    def is_cached(self, text: str) -> bool:
        return True

    def waiting(self) -> int:
        return 0

    def stop(self) -> None:
        pass
