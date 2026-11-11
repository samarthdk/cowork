"""Real TTS: Argmax TTSKit (0.6B) through whisperkit-cli, with a phrase cache.

Measured on this Mac: starting TTSKit fresh takes 6-8.5 s per sentence (the
model loads every time), which is too slow for a conversation. But the
person's phrases are a known list, so:

  - known phrases are synthesized once, in the background (`prepare`), and
    saved as audio files; speaking one is then instant (macOS `afplay`);
  - a brand-new sentence is synthesized live with TTSKit (slow, same voice)
    and saved, so the second time it is instant too;
  - if TTSKit fails for any reason, macOS `say` speaks instead.

`stop()` cuts off whatever is playing (used when the person cancels).
"""

import hashlib
import shutil
import subprocess
import threading
import time
from pathlib import Path

import config
from metrics import log_call


class RealTTS:
    def __init__(self, on_ready=None):
        self._player: subprocess.Popen | None = None
        self._lock = threading.Lock()           # one TTSKit process at a time (memory)
        self._worker: threading.Thread | None = None
        self._queue: list[str] = []
        self.on_ready = on_ready or (lambda text, took_ms: None)
        config.TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ---- cache ---------------------------------------------------------------

    def cached_path(self, text: str) -> Path:
        key = hashlib.sha1(f"{config.TTS_MODEL}|{config.TTS_VOICE}|{text.strip()}".encode()).hexdigest()[:16]
        return config.TTS_CACHE_DIR / f"{key}.m4a"

    def is_cached(self, text: str) -> bool:
        path = self.cached_path(text)
        return path.exists() and path.stat().st_size > 1000

    def _synthesize(self, text: str, play: bool) -> None:
        """Run TTSKit once. Saves the audio in the cache (and plays it if asked)."""
        path = self.cached_path(text)
        command = ["whisperkit-cli", "tts", "--text", text, "--model", config.TTS_MODEL,
                   "--speaker", config.TTS_VOICE, "--output-path", str(path.with_suffix(""))]
        if config.TTSKIT_MODELS_PATH.exists():
            command += ["--models-path", str(config.TTSKIT_MODELS_PATH)]
        if config.TTSKIT_TOKENIZER_PATH.exists():
            command += ["--tokenizer", str(config.TTSKIT_TOKENIZER_PATH)]
        if play:
            command.append("--play")
        with self._lock:
            subprocess.run(command, check=True, capture_output=True, timeout=config.TTS_TIMEOUT_S)

    def prepare(self, texts: list[str]) -> None:
        """Synthesize these sentences in the background so they are instant later."""
        self._queue += [t for t in texts if t and not self.is_cached(t) and t not in self._queue]
        if self._worker and self._worker.is_alive():
            return

        def work():
            while self._queue:
                text = self._queue.pop(0)
                if self.is_cached(text):
                    continue
                start = time.perf_counter()
                try:
                    self._synthesize(text, play=False)
                    took = (time.perf_counter() - start) * 1000
                    log_call("tts", "ttskit-prepare", took, chars=len(text))
                    self.on_ready(text, took)
                except Exception as error:
                    log_call("tts", "ttskit-prepare-failed", (time.perf_counter() - start) * 1000,
                             chars=len(text), problem=str(error)[:200])

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def waiting(self) -> int:
        return len(self._queue)

    # ---- speaking --------------------------------------------------------------

    def speak(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        start = time.perf_counter()
        backend = "cache"
        try:
            if self.is_cached(text):
                self._play(self.cached_path(text))
            elif config.TTS_NEW_SENTENCES == "ttskit" and shutil.which("whisperkit-cli"):
                backend = "ttskit-live"
                self._synthesize(text, play=True)
            else:
                backend = "say"
                self._say(text)
                self.prepare([text])     # next time it will be the TTSKit voice, instantly
        except Exception as error:       # missing tool, crash, timeout...
            log_call("tts", f"{backend}-failed", (time.perf_counter() - start) * 1000, problem=str(error)[:200])
            backend = "say"
            self._say(text)
        log_call("tts", backend, (time.perf_counter() - start) * 1000, chars=len(text))

    def _play(self, path: Path) -> None:
        self._player = subprocess.Popen(["afplay", str(path)])
        self._player.wait(timeout=config.TTS_TIMEOUT_S)

    def _say(self, text: str) -> None:
        self._player = subprocess.Popen(["say", text])
        self._player.wait(timeout=config.TTS_TIMEOUT_S)

    def stop(self) -> None:
        """Cut off whatever is being spoken right now."""
        if self._player and self._player.poll() is None:
            self._player.terminate()
