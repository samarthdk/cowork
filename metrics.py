"""Records timing for every model call including (ASR, LLM, TTS) in logs/model_calls.jsonl."""

import json
import time

import config


def log_call(component: str, backend: str, latency_ms: float, **extra) -> None:
    record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "component": component,
              "backend": backend, "latency_ms": round(latency_ms, 1), **extra}
    config.LOG_DIR.mkdir(exist_ok=True)
    with open(config.LOG_DIR / "model_calls.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
