"""The ASR interface: every speech-to-text version must look like this."""

from typing import Protocol


class ASR(Protocol):
    def transcribe(self, audio_path: str) -> str:
        """Turn a recorded audio file into text."""
        ...
