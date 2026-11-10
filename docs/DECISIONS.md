# Decisions log

Decisions made on Samarth's behalf during the `fable-build` work, newest section last.
Each entry: what was chosen, and why. Measured numbers are from this MacBook Air M1 (8 GB).

## Setup

- **Branch `fable-build`**, started from `main` at `6c82d6e` with all 192 existing checks passing.
- **New packages** (all small, installed with `uv`):
  - `pyarrow` (about 30 MB): to read the TORGO dataset files (Parquet format).
  - `psutil`: to measure memory use of the running models.
  - Already present and now used: `fastapi`, `uvicorn`, `sounddevice`, `jiwer`, `matplotlib`.

## Speech recognition (WhisperKit)

- **Use `whisperkit-cli serve` (a local server on port 50060), not one process per clip.**
  Measured: 0.3 s per short clip once the model is loaded, versus several seconds when the
  model is loaded each time. The server is started with `--model-path` pointing at the local
  model folder, so it never needs the network.
- **The server reports a confidence per segment** (`avg_logprob`, `no_speech_prob`). These are
  kept with every transcript and used to filter noise.
- **Hint WhisperKit with the person's own phrases** (the server accepts a per-request
  `prompt`). Decision on whether and how to use it is recorded under "Evaluation" below,
  based on measurements, because a hint can also make it "hear" a listed phrase that was
  not said.

## Speech synthesis (TTSKit)

- **Measured: 6 to 8.5 s per sentence** when `whisperkit-cli tts` is started fresh (the model
  loads every time). Too slow for conversation.
- **Decision: pre-make the audio for known phrases.** The person's phrases are a fixed list,
  so they are synthesized once with TTSKit in the background and cached as audio files;
  speaking a known phrase is then instant (played with macOS `afplay`). A brand-new sentence
  is synthesized live with TTSKit (slower, same voice), with macOS `say` as the fallback.

## The Brain (Bonsai via LM Studio)

- **Compact "one of these shapes" answer** (JSON schema `anyOf`). Tested in LM Studio: it
  works. A phrase answer is now `{"choice":"P1","confidence":"high"}` (about 11 tokens instead
  of 35), and each command carries exactly the details it needs. Measured 0.7 to 1.3 s per
  answer (was 2.0 to 2.7 s), same answers on the test sentences.
  - The model's "options" list was dropped: after a "no", options are ranked by similarity
    in code (the small model only ranked its first pick reliably), so the list cost about
    one second per answer for little value.
  - Command details come from real lists where possible (for `open_app`, the apps actually
    installed), so the model cannot invent an app name.
