"""Real LLM: Bonsai 2 4B running in LM Studio's local server (localhost only)."""

import time

from openai import OpenAI

import config
from metrics import log_call
from repair.llm import LLMReply


class LMStudioLLM:
    def __init__(self):
        # LM Studio ignores the key, but the library requires one.
        self.client = OpenAI(base_url=config.LLM_BASE_URL, api_key="lm-studio",
                             timeout=config.LLM_TIMEOUT_S, max_retries=0)

    def complete(self, messages: list[dict], schema: dict, purpose: str = "intent",
                 temperature: float = 0.0) -> LLMReply:
        start = time.perf_counter()
        response = self.client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens={"guess": config.GUESS_MAX_TOKENS, "warmup": 4}.get(purpose, config.LLM_MAX_TOKENS),
            # Strict JSON: LM Studio forces the reply to follow this schema.
            response_format={"type": "json_schema",
                             "json_schema": {"name": purpose, "strict": True, "schema": schema}},
            # Thinking OFF. Tested 2026-11-10 with LM Studio + Bonsai 2 4B: only
            # reasoning_effort "none" works. chat_template_kwargs enable_thinking
            # and a /no_think instruction are both ignored; "low" still thinks.
            extra_body={"reasoning_effort": "none"},
        )
        latency = (time.perf_counter() - start) * 1000
        usage = response.usage
        tokens = usage.completion_tokens if usage else 0
        details = getattr(usage, "completion_tokens_details", None) if usage else None
        thinking = (getattr(details, "reasoning_tokens", 0) or 0) if details else 0
        tps = tokens / (latency / 1000) if latency else 0.0
        finish = response.choices[0].finish_reason or ""
        log_call("llm", "lmstudio", latency, purpose=purpose, temperature=temperature, model=config.LLM_MODEL, tokens=tokens,
                 thinking_tokens=thinking, tokens_per_s=round(tps, 1), finish_reason=finish)
        return LLMReply(response.choices[0].message.content or "", latency, tokens, tps,
                        finish_reason=finish, thinking_tokens=thinking)
