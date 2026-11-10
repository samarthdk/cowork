"""Audio helpers: reading and writing clips, and the gentle volume boost.

Soft speech is a symptom (for example in Parkinson's), so quiet audio is never
thrown away. It is turned up instead: the gain is chosen from the loud parts
of the clip (the speech), capped so background hiss is not blown up, and
limited so nothing clips.
"""

import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

RATE = 16000            # what WhisperKit expects
TARGET_DBFS = -20.0     # where speech should sit after the boost
MAX_GAIN_DB = 24.0      # never turn up more than this (x16)
PEAK_LIMIT = 0.97       # leave headroom so nothing clips


def read_wav(path) -> tuple[np.ndarray, int]:
    """Read a 16-bit WAV file as mono float samples in [-1, 1]."""
    with wave.open(str(path), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"{path}: only 16-bit WAV files are supported")
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def write_wav(path, samples: np.ndarray, rate: int = RATE) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def to_wav(path) -> Path:
    """Return a WAV version of any audio file (m4a, mp3...), converting with the
    macOS tool `afconvert` if needed."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        return path
    out = Path(tempfile.gettempdir()) / f"secondvoice_{path.stem}.wav"
    subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1", str(path), str(out)],
                   check=True, capture_output=True)
    return out


def speech_level_dbfs(samples: np.ndarray, rate: int = RATE) -> float:
    """Loudness of the speech in the clip: the level of its loudest fifth,
    measured in 20 ms pieces, in dBFS (0 = as loud as possible)."""
    frame = max(1, int(rate * 0.02))
    count = len(samples) // frame
    if count == 0:
        return -120.0
    rms = np.sqrt(np.mean(samples[:count * frame].reshape(count, frame) ** 2, axis=1) + 1e-12)
    loud = np.sort(rms)[-max(1, count // 5):]
    return float(20 * np.log10(np.mean(loud) + 1e-12))


def boost(samples: np.ndarray, rate: int = RATE) -> tuple[np.ndarray, float]:
    """Gently turn quiet speech up. Returns (samples, gain in dB). Never turns down."""
    if len(samples) == 0:
        return samples, 0.0
    level = speech_level_dbfs(samples, rate)
    gain_db = min(max(TARGET_DBFS - level, 0.0), MAX_GAIN_DB)
    peak = float(np.max(np.abs(samples))) or 1e-9
    gain_db = min(gain_db, 20 * np.log10(PEAK_LIMIT / peak)) if peak * 10 ** (gain_db / 20) > PEAK_LIMIT else gain_db
    gain_db = max(gain_db, 0.0)
    return samples * (10 ** (gain_db / 20)), gain_db


def boosted_copy(path) -> tuple[Path, float]:
    """Write a boosted copy of a clip (if it needs it). Returns (path to use, gain dB)."""
    wav = to_wav(path)
    samples, rate = read_wav(wav)
    louder, gain_db = boost(samples, rate)
    if gain_db < 1.0:
        return wav, 0.0
    out = Path(tempfile.gettempdir()) / f"secondvoice_boost_{Path(path).stem}.wav"
    write_wav(out, louder, rate)
    return out, gain_db
