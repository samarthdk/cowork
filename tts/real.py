"""Real TTS: Argmax TTSKit (0.6B) only, through whisperkit-cli, with a cache of wav files.
The Mac voice and the `say` command are never used.

Measured on this Mac: TTSKit cannot stay loaded between sentences (whisperkit-cli has
no speech server); every run spends about 2 s starting up, and a sentence takes
about 3-7 s in all. So:

  - everything Cowork expects to say is made in advance, as short pieces
    (tts/lines.py: "Did you mean", "Open", "Mail", every phrase...), saved as wav files;
  - a line is played from its pieces, back to back with a short natural gap, as one
    sound (first sound in a few tens of ms);
  - a line is spoken only if EVERY piece of it is ready. Otherwise generation starts
    at once in the background (and is cached for next time); if it is ready within
    LATE_S of when it should have played, it plays, else nothing is said (the text is
    on screen as usual). Never half a line;
  - `speak(..., wait=True)` (Conference mode) waits for TTSKit however long it takes.

TTSKit's model is always read from this Mac (config.TTSKIT_MODELS_PATH): if it isn't
there, nothing is generated (whisperkit-cli would otherwise download it).

`stop()` cuts off whatever is playing (and a line still waiting is not played).
"""

import hashlib
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import config
from metrics import log_call
from tts import lines

LATE_S = 2.0          # a line made after it was due is still played if it is ready within this long
SILENCE_LEVEL = 300   # 16-bit samples below this at the start and end of a piece are trimmed
EDGE_S = 0.03         # ...keeping this much of the quiet edge


class RealTTS:
    def __init__(self, on_ready=None, profile: dict | None = None):
        self._player: subprocess.Popen | None = None
        self._lock = threading.Lock()           # one TTSKit process at a time (memory)
        self._queue_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._queue: list[str] = []
        self._making: str | None = None         # the text TTSKit is making right now
        self._failed: dict[str, str] = {}       # text -> why TTSKit could not make it
        self._stop_seq = 0
        self.on_ready = on_ready or (lambda text, took_ms: None)
        self.profile = profile or {}            # for splitting lines by the commands' templates
        self.last = {}                          # what happened to the last line (for the report and the screen)
        config.TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ---- cache ---------------------------------------------------------------

    def cached_path(self, text: str) -> Path:
        key = hashlib.sha1(f"{config.TTS_MODEL}|{config.TTS_VOICE}|{lines.clean(text)}".encode()).hexdigest()[:16]
        return config.TTS_CACHE_DIR / f"{key}.wav"

    def is_cached(self, text: str) -> bool:
        path = self.cached_path(text)
        return path.exists() and path.stat().st_size > 1000

    def pieces(self, line) -> list[str] | None:
        """The ready pieces that say this line, or None if it can't be said yet."""
        return lines.choose(line, self.profile, self.is_cached)

    def ready(self, line) -> bool:
        return self.pieces(line) is not None

    def which_voice(self, line) -> str:
        return "TTSKit" if self.ready(line) else "not ready"

    # ---- making audio (TTSKit) -------------------------------------------------

    def available(self) -> str:
        """'' if TTSKit can run offline here, else the reason in plain words."""
        import shutil
        if not shutil.which("whisperkit-cli"):
            return "whisperkit-cli is not installed"
        if not config.TTSKIT_MODELS_PATH.exists():
            return f"the TTSKit model is not on this Mac ({config.TTSKIT_MODELS_PATH})"
        return ""

    def _synthesize(self, text: str) -> None:
        """Run TTSKit once and save the audio in the cache (never downloads anything)."""
        problem = self.available()
        if problem:
            raise RuntimeError(problem)
        path = self.cached_path(text)
        making = path.with_name(path.stem + "-making")   # TTSKit writes here; the cache only ever sees finished files
        command = ["whisperkit-cli", "tts", "--text", lines.clean(text), "--model", config.TTS_MODEL,
                   "--speaker", config.TTS_VOICE, "--output-path", str(making),
                   "--output-format", "wav", "--models-path", str(config.TTSKIT_MODELS_PATH)]
        if config.TTSKIT_TOKENIZER_PATH.exists():
            command += ["--tokenizer", str(config.TTSKIT_TOKENIZER_PATH)]
        if config.TTSKIT_FAST:   # measured: real-time factor 2.0 -> 1.14 (first use compiles once, about 50 s)
            command += ["--speech-decoder-mode", "throughputOptimized", "--multi-code-decoder-mode", "fused"]
        with self._lock:
            subprocess.run(command, check=True, capture_output=True, timeout=config.TTS_TIMEOUT_S)
        to_16_bit(making.with_suffix(".wav"), path)

    def prepare(self, texts: list[str], urgent: bool = False) -> None:
        """Make these pieces in the background (urgent ones go to the front of the queue)."""
        with self._queue_lock:
            new = [lines.clean(t) for t in texts if lines.clean(t) and not self.is_cached(t)]
            new = [t for t in dict.fromkeys(new) if t != self._making]
            if urgent:
                self._queue = new + [t for t in self._queue if t not in new]
            else:
                self._queue += [t for t in new if t not in self._queue]
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._work, daemon=True, name="ttskit")
            self._worker.start()

    def _work(self):
        while True:
            with self._queue_lock:
                if not self._queue:
                    self._making = None
                    return
                text = self._making = self._queue.pop(0)
            if self.is_cached(text) or text in self._failed:
                continue
            start = time.perf_counter()
            try:
                self._synthesize(text)
                took = (time.perf_counter() - start) * 1000
                log_call("tts", "ttskit-make", took, chars=len(text))
                self.on_ready(text, took)
            except Exception as error:
                self._failed[text] = str(error)[:200]
                log_call("tts", "ttskit-make-failed", (time.perf_counter() - start) * 1000,
                         chars=len(text), problem=str(error)[:200])

    def prefetch(self, line) -> None:
        """Start making a line now, so it is ready by the time it is said. Each part
        that isn't ready is made whole (urgently): one TTSKit run per part."""
        missing = [p for p in lines.parts_of(line) if self.pieces([p]) is None]
        if missing:
            self.prepare(missing, urgent=True)

    def waiting(self) -> int:
        return len(self._queue)

    # ---- speaking --------------------------------------------------------------

    def speak(self, line, wait: bool = False, on_sound=None) -> bool:
        """Say a line (a sentence, or a list of parts). Returns True if it was spoken.
        wait=False: only if it is ready now, or within LATE_S. wait=True: however long TTSKit takes.
        on_sound(): called the moment the sound starts."""
        text = lines.text_of(line)
        if not text:
            return False
        start = time.perf_counter()
        seq = self._stop_seq
        pieces = self.pieces(line)
        made_now = pieces is None
        if made_now:
            self.prefetch(line)
            limit = config.TTS_TIMEOUT_S if wait else LATE_S
            while pieces is None and time.perf_counter() - start < limit and seq == self._stop_seq:
                if all(p in self._failed for p in lines.parts_of(line) if self.pieces([p]) is None):
                    break
                time.sleep(0.05)
                pieces = self.pieces(line)
        if pieces is None or seq != self._stop_seq:
            why = "stopped" if seq != self._stop_seq else "voice not ready"
            self.last = {"text": text, "spoken": False, "why": why, "waited_ms": round((time.perf_counter() - start) * 1000)}
            log_call("tts", "silent", (time.perf_counter() - start) * 1000, chars=len(text), why=why)
            return False
        first_ms = self._play(pieces, start, on_sound)
        if seq != self._stop_seq:   # cut off part-way (Undo): it does not count as said
            self.last = {"text": text, "spoken": False, "why": "stopped", "first_sound_ms": first_ms}
            log_call("tts", "stopped", (time.perf_counter() - start) * 1000, chars=len(text))
            return False
        self.last = {"text": text, "spoken": True, "pieces": pieces, "made_now": made_now, "first_sound_ms": first_ms}
        log_call("tts", "ttskit-made-now" if made_now else "ttskit-ready", (time.perf_counter() - start) * 1000,
                 chars=len(text), pieces=len(pieces), first_sound_ms=first_ms)
        return True

    def _play(self, pieces: list[str], start: float, on_sound=None) -> float:
        """Play the pieces as one sound; returns ms from the request to the player starting."""
        path = self._join(pieces)   # also for one piece: trims TTSKit's click and the quiet before the words
        self._player = subprocess.Popen(["afplay", str(path)])
        first_ms = round((time.perf_counter() - start) * 1000)
        if on_sound is not None:
            try:
                on_sound()
            except Exception:
                pass
        try:
            self._player.wait(timeout=config.TTS_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._player.terminate()
        return first_ms

    def _join(self, pieces: list[str]) -> Path:
        """One wav file: each piece trimmed of its quiet edges, with a short gap between pieces."""
        frames, params = [], None
        for n, piece in enumerate(pieces):
            with wave.open(str(self.cached_path(piece)), "rb") as w:
                params = params or w.getparams()
                data = w.readframes(w.getnframes())
            data = _trim(data, params.sampwidth, params.nchannels, params.framerate)
            frames.append(data)
            if n < len(pieces) - 1:
                gap = int(params.framerate * lines.gap_after(piece)) * params.sampwidth * params.nchannels
                frames.append(b"\0" * gap)
        out = Path(tempfile.gettempdir()) / "secondvoice_line.wav"
        with wave.open(str(out), "wb") as w:
            w.setparams(params)
            w.writeframes(b"".join(frames))
        return out

    def stop(self) -> None:
        """Cut off whatever is being spoken right now (and a line still waiting is not played)."""
        self._stop_seq += 1
        if self._player and self._player.poll() is None:
            self._player.terminate()


def to_16_bit(source: Path, target: Path | None = None) -> None:
    """TTSKit writes 32-bit float wav; pieces are kept as 16-bit wav so they can be joined
    (macOS afconvert, on this Mac). The finished file appears at `target` in one step (a file
    still being written or converted is never seen as ready). Already 16-bit files are just moved."""
    target = target or source
    try:
        with wave.open(str(source), "rb"):
            pass
        if source != target:
            source.replace(target)
        return
    except wave.Error:
        pass
    temp = target.with_name(target.stem + "-16bit.wav")
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", str(source), str(temp)], check=True,
                   capture_output=True, timeout=30)
    temp.replace(target)
    if source != target:
        source.unlink(missing_ok=True)


def _trim(data: bytes, width: int, channels: int, rate: int) -> bytes:
    """Remove quiet audio (and the click TTSKit leaves) at the start and end of a piece, keeping EDGE_S."""
    if width != 2 or not data:
        return data
    import numpy as np
    samples = np.frombuffer(data, dtype="<i2")
    frames = samples.reshape(-1, channels) if channels > 1 else samples.reshape(-1, 1)
    window = max(1, rate // 100)                       # 10 ms windows
    n = len(frames) // window
    if n == 0:
        return data
    peaks = np.abs(frames[: n * window].astype(np.int32)).reshape(n, window, -1).max(axis=(1, 2))
    above = peaks > SILENCE_LEVEL
    # sound that lasts (3 windows in a row): TTSKit leaves a click in the first 10 ms, then silence
    lasting = above[:-2] & above[1:-1] & above[2:] if n >= 3 else above
    loud = np.nonzero(lasting)[0]
    if len(loud) == 0:
        return data
    keep = int(rate * EDGE_S)
    first = max(0, loud[0] * window - keep)
    last = min(len(frames), (loud[-1] + 3) * window + keep)
    return frames[first:last].tobytes()
