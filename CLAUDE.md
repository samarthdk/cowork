# Second Voice

Offline assistant for people with impaired speech (Parkinson's, ALS, stroke, CP).
Speech -> repaired intent -> action on the Mac, or a spoken sentence for other people.

## About the developer
I am not an engineer. Explain what you are doing in plain language, tell me exactly how to run and test each thing, and ask before installing anything large.

## Hard constraints
- 100% offline at runtime. No network calls except to localhost.
- MacBook Air M1, 8 GB RAM. Keep total model memory under 3.5 GB.
- Python 3.12, virtual environment in .venv, packages installed with uv.
- LLM: Bonsai 2 4B ("ternary-bonsai-2-4b") via LM Studio's local server (OpenAI-compatible, http://localhost:1234/v1). Context 4096. Thinking must be disabled in every request.
- ASR: WhisperKit (small model). TTS: Argmax TTSKit 0.6B, fallback macOS `say`.
- Camera: MediaPipe hands + face landmarks.

## Core design (tested and confirmed)
- The model never guesses freely. It always chooses from a short list: the user's own phrases and commands (speech profile), plus what is on screen.
- Every reply is strict JSON with a "confidence" field ("high" or "low").
- High confidence: act or speak. Low confidence: show the top 4 options in a 2x2 grid and let the user choose.

## Modules
- asr/      audio capture, volume normalization, WhisperKit client
- repair/   speech profile store, prompt builder, LLM call with JSON output, one retry
- actions/  macOS accessibility: list clickable elements, open_app, click, type, scroll
- choose/   choice grid (2 or 4 options); inputs: speech, hands, head, keyboard -> the same Choice event
- tts/      speak(text)
- ui/       FastAPI + one local HTML page: transcript, intent, grid, metrics panel
- eval/     run test clips, report raw vs repaired success, WER, latency, peak memory

## Rules
- Every AI component (ASR, LLM, TTS) sits behind an interface with a fake version and a real version, switchable in one config file. Build with the fakes first.
- One-shot, schema-constrained calls only. No open-ended agent loops.
- Log latency and tokens per second for every model call.
- Every module has a small script that runs it on its own.
- Commit after every working step.
