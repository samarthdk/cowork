"""Fake TTS: prints what it would say instead of making sound.

Behaves like the real voice (tts/real.py): a line is spoken only if it is "ready".
By default everything is ready; pass not_ready={"some text", ...} to make lines whose
text contains one of these stay silent (to test the quiet path)."""

import time

from metrics import log_call
from tts.lines import text_of


class FakeTTS:
    def __init__(self, quiet: bool = False, not_ready: set | None = None):
        self.quiet = quiet
        self.not_ready = set(not_ready or ())
        self.said: list[str] = []
        self.silent: list[str] = []
        self.prefetched: list[str] = []
        self.waited: list[str] = []    # lines spoken with wait=True (Conference mode)

    def ready(self, line) -> bool:
        text = text_of(line)
        return not any(word in text for word in self.not_ready)

    def which_voice(self, line) -> str:
        return "TTSKit" if self.ready(line) else "not ready"

    def speak(self, line, wait: bool = False) -> bool:
        start = time.perf_counter()
        text = text_of(line)
        if not text:
            return False
        if not self.ready(line) and not wait:
            self.silent.append(text)
            log_call("tts", "fake-silent", (time.perf_counter() - start) * 1000, chars=len(text))
            return False
        if wait:
            self.waited.append(text)
        self.said.append(text)
        if not self.quiet:
            print(f"[would say] {text}")
        log_call("tts", "fake", (time.perf_counter() - start) * 1000, chars=len(text))
        return True

    def prefetch(self, line) -> None:
        self.prefetched.append(text_of(line))

    def prepare(self, texts: list[str], urgent: bool = False) -> None:
        pass

    def is_cached(self, text: str) -> bool:
        return self.ready(text)

    def waiting(self) -> int:
        return 0

    def stop(self) -> None:
        pass
