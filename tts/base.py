"""The TTS interface: every text-to-speech version must look like this."""

from typing import Protocol


class TTS(Protocol):
    def speak(self, text: str) -> None:
        """Say the text out loud."""
        ...
