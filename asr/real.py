"""Real ASR: WhisperKit (small.en) through its local server.

Every clip is gently boosted first (quiet speech is never discarded), the
non-speech markers are removed from the result, and the time taken is logged.
If the server is not running it is started; if that fails, the slower one-shot
command is used, and if that fails too the transcript says what went wrong
instead of raising an error.
"""

import json
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import config
from asr import server
from asr.audio import boosted_copy
from asr.base import Transcript
from asr.clean import clean_text, is_probably_noise
from metrics import log_call


def _post_clip(path: Path, prompt: str) -> dict:
    """Send one audio file to the local server (multipart form, like a web upload)."""
    boundary = uuid.uuid4().hex
    parts = []
    fields = {"model": "small.en", "language": "en"}
    if prompt:
        fields["prompt"] = prompt
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
                 f'Content-Type: audio/wav\r\n\r\n'.encode() + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(server.url("/v1/audio/transcriptions"), b"".join(parts),
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=config.ASR_TIMEOUT_S) as response:
        return json.load(response)


def _one_shot(path: Path, prompt: str) -> dict:
    """Fallback without the server: load the model, transcribe, exit (slow)."""
    command = ["whisperkit-cli", "transcribe", "--model-path", str(config.WHISPERKIT_MODEL_PATH),
               "--language", "en", "--skip-special-tokens", "--without-timestamps",
               "--audio-path", str(path)]
    if prompt:
        command += ["--prompt", prompt]
    done = subprocess.run(command, capture_output=True, text=True, timeout=config.ASR_TIMEOUT_S * 3, check=True)
    return {"text": done.stdout.strip(), "segments": []}


class WhisperKitASR:
    def __init__(self, boost: bool = True):
        self.boost = boost

    def transcribe(self, audio_path: str, prompt: str = "") -> Transcript:
        start = time.perf_counter()
        backend, problem, data, gain_db = "whisperkit-server", "", {}, 0.0
        try:
            clip, gain_db = boosted_copy(audio_path) if self.boost else (Path(audio_path), 0.0)
            running, message = server.ensure_running()
            if running:
                data = _post_clip(Path(clip), prompt)
            else:
                backend = "whisperkit-cli"
                data = _one_shot(Path(clip), prompt)
        except Exception as error:  # never crash the app because of one clip
            problem = f"{type(error).__name__}: {error}"

        segments = data.get("segments") or []
        seconds = sum(max(s.get("end", 0) - s.get("start", 0), 0.01) for s in segments) or 1.0
        avg_logprob = sum(s.get("avg_logprob", 0) * max(s.get("end", 0) - s.get("start", 0), 0.01)
                          for s in segments) / seconds if segments else 0.0
        no_speech = max((s.get("no_speech_prob", 0) for s in segments), default=0.0)
        raw = (data.get("text") or "").strip()
        text = clean_text(raw)
        if is_probably_noise(text, avg_logprob, no_speech):
            text = ""
        latency = (time.perf_counter() - start) * 1000
        log_call("asr", backend, latency, audio=Path(audio_path).name, chars=len(text),
                 avg_logprob=round(avg_logprob, 3), gain_db=round(gain_db, 1),
                 primed=bool(prompt), problem=problem)
        return Transcript(text, raw, avg_logprob, no_speech, data.get("duration", 0.0) or 0.0,
                          latency, backend, problem)
