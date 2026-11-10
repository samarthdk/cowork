"""The ASR interface: every speech-to-text version must look like this."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Transcript:
    text: str                    # cleaned text; "" means nothing usable was said
    raw_text: str = ""           # exactly what the recognizer returned
    avg_logprob: float = 0.0     # recognizer's own confidence: closer to 0 = more sure
    no_speech_prob: float = 0.0  # chance the clip has no speech at all
    duration_s: float = 0.0
    latency_ms: float = 0.0
    backend: str = ""            # "whisperkit-server", "whisperkit-cli", "fake"
    problem: str = ""            # why there is no text, if something went wrong

    def __str__(self) -> str:
        return self.text


class ASR(Protocol):
    def transcribe(self, audio_path: str, prompt: str = "") -> Transcript:
        """Turn a recorded audio file into text. `prompt` is an optional hint
        (for example the person's own phrases) for the recognizer."""
        ...
