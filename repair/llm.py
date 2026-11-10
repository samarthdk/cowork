"""The LLM interface: every language-model version must look like this."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMReply:
    text: str                 # the raw reply (should be JSON)
    latency_ms: float
    completion_tokens: int
    tokens_per_s: float


class LLM(Protocol):
    def complete(self, messages: list[dict], schema: dict) -> LLMReply:
        """One call: send the messages, get back a reply that follows `schema`."""
        ...
