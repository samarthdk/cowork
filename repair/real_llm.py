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

    def complete(self, messages: list[dict], schema: dict) -> LLMReply:
        start = time.perf_counter()
        response = self.client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            temperature=0,
            max_tokens=300,
            # Strict JSON: LM Studio forces the reply to follow this schema.
            response_format={"type": "json_schema",
                             "json_schema": {"name": "intent", "strict": True, "schema": schema}},
            # Thinking disabled (the prompt also ends with /no_think).
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        latency = (time.perf_counter() - start) * 1000
        tokens = response.usage.completion_tokens if response.usage else 0
        tps = tokens / (latency / 1000) if latency else 0.0
        log_call("llm", "lmstudio", latency, model=config.LLM_MODEL,
                 tokens=tokens, tokens_per_s=round(tps, 1))
        return LLMReply(response.choices[0].message.content or "", latency, tokens, tps)
