"""The LLM interface: every language-model version must look like this."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMReply:
    text: str                 # the raw reply (should be JSON)
    latency_ms: float
    completion_tokens: int
    tokens_per_s: float
    finish_reason: str = "stop"   # "length" = cut off at the token limit
    thinking_tokens: int = 0      # should always be 0 (thinking is off)


class LLM(Protocol):
    def complete(self, messages: list[dict], schema: dict, purpose: str = "intent",
                 temperature: float = 0.0) -> LLMReply:
        """One call: send the messages, get back a reply that follows `schema`.
        `purpose` ("intent" or "guess") is only used for logging. Temperature 0
        = always the most likely answer; higher = more varied."""
        ...
