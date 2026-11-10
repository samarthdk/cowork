"""Real TTS: Argmax TTSKit through whisperkit-cli; falls back to macOS `say`."""

import subprocess
import tempfile
import time
from pathlib import Path

import config
from metrics import log_call


class RealTTS:
    def speak(self, text: str) -> None:
        start = time.perf_counter()
        backend = "whisperkit"
        try:
            self._whisperkit(text)
        except Exception as error:  # missing tool, crash, timeout...
            print(f"(TTSKit failed: {error}; using macOS say instead)")
            backend = "say"
            subprocess.run(["say", text], check=False)
        log_call("tts", backend, (time.perf_counter() - start) * 1000, chars=len(text))

    def _whisperkit(self, text: str) -> None:
        # whisperkit-cli also saves the audio to a file; keep it out of the project.
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["whisperkit-cli", "tts", "--play", "--text", text,
                 "--model", config.TTS_MODEL, "--output-path", str(Path(tmp) / "speech")],
                check=True, capture_output=True, timeout=config.TTS_TIMEOUT_S,
            )
