"""Listening on the microphone and cutting the sound into requests.

Two parts:

  Endpointer   pure logic, no microphone: fed 20 ms pieces of audio, it decides
               when speech starts and when a request has ended (a pause of
               about 1.5 s). Tested with made-up audio in tests/test_mic.py.
  Listener     the real microphone (sounddevice) feeding an Endpointer, writing
               each finished request to a .wav file and handing it on.

Design choices:
  - Quiet speech is never discarded. "Speech" is judged against the room's own
    background level (which is tracked all the time), not against a fixed
    loudness, so a soft voice in a quiet room still counts.
  - "Listen now" (trigger mode, the default): the Listener only accepts a
    request after trigger() is called (a raised hand, a key, the on-screen
    button). This is the main defence against bystanders' speech. In "always"
    mode every stretch of speech becomes a request.
  - mute() while the Mac itself is speaking, so it never hears its own voice.
"""

import queue
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from asr.audio import RATE, write_wav

FRAME = 320                 # 20 ms at 16 kHz
END_SILENCE_S = 1.5         # a pause this long ends the request
START_FRAMES = 3            # this many speech frames in a row (60 ms) start a request
PRE_ROLL_S = 0.4            # audio kept from just before speech started
MIN_SPEECH_S = 0.20         # less speech than this in total is a click, not a request
MAX_REQUEST_S = 20.0        # safety limit
SPEECH_OVER_NOISE = 3.0     # speech = this many times louder than the room
MIN_SPEECH_RMS = 0.0015     # ...and at least this loud (about -56 dBFS: very quiet)
NOISE_WINDOW_S = 5.0        # the room level is the quietest moments of the last few seconds
NOISE_PERCENTILE = 15       # ...measured all the time, even during speech (speech has gaps)
WARM_UP_FRAMES = 25         # no request can start before the room has been measured (0.5 s)


@dataclass
class Segment:
    samples: np.ndarray
    speech_s: float          # how much of it was speech
    duration_s: float
    level_dbfs: float        # loudness of the speech
    noise_dbfs: float        # loudness of the room


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2) + 1e-12))


def dbfs(value: float) -> float:
    return float(20 * np.log10(max(value, 1e-9)))


class Endpointer:
    """Feed it 20 ms frames; it returns a Segment when a request has ended."""

    def __init__(self, end_silence_s: float = END_SILENCE_S):
        self.end_frames = int(end_silence_s * RATE / FRAME)
        self.pre_roll = deque(maxlen=int(PRE_ROLL_S * RATE / FRAME))
        self.recent_levels = deque(maxlen=int(NOISE_WINDOW_S * RATE / FRAME))
        self.noise = MIN_SPEECH_RMS / SPEECH_OVER_NOISE
        self.reset()

    def reset(self) -> None:
        self.frames: list[np.ndarray] = []
        self.in_speech = False
        self.run = 0             # speech frames in a row (before starting)
        self.silence = 0         # quiet frames in a row (while in speech)
        self.speech_frames = 0
        self.speech_levels: list[float] = []

    @property
    def threshold(self) -> float:
        return max(self.noise * SPEECH_OVER_NOISE, MIN_SPEECH_RMS)

    def track_room(self, level: float) -> None:
        """Update the room level. Called for every frame, also during speech:
        otherwise a room that gets noisier would look like speech that never ends."""
        self.recent_levels.append(level)
        if len(self.recent_levels) >= WARM_UP_FRAMES:
            self.noise = float(np.percentile(self.recent_levels, NOISE_PERCENTILE))

    def feed(self, frame: np.ndarray) -> Segment | None:
        level = rms(frame)
        self.track_room(level)
        loud = level > self.threshold
        if not self.in_speech:
            self.pre_roll.append(frame)
            self.run = self.run + 1 if loud else 0
            if self.run >= START_FRAMES and len(self.recent_levels) >= WARM_UP_FRAMES:
                self.in_speech = True
                self.frames = list(self.pre_roll)
                self.speech_frames, self.silence = self.run, 0
                self.speech_levels = [level]
            return None

        self.frames.append(frame)
        if loud:
            self.speech_frames += 1
            self.speech_levels.append(level)
            self.silence = 0
        else:
            self.silence += 1
        too_long = len(self.frames) * FRAME / RATE >= MAX_REQUEST_S
        if self.silence < self.end_frames and not too_long:
            return None

        # The request has ended. Drop most of the trailing pause.
        keep = len(self.frames) - max(self.silence - int(0.3 * RATE / FRAME), 0)
        samples = np.concatenate(self.frames[:keep]) if keep > 0 else np.zeros(0, np.float32)
        speech_s = self.speech_frames * FRAME / RATE
        segment = Segment(samples, speech_s, len(samples) / RATE,
                          dbfs(float(np.mean(self.speech_levels))) if self.speech_levels else -120.0,
                          dbfs(self.noise))
        self.reset()
        self.pre_roll.clear()
        return segment if speech_s >= MIN_SPEECH_S else None


class Listener:
    """The real microphone. Calls on_request(path_to_wav, Segment) for each request.

    mode "trigger": only after trigger() (and until one request is heard or
                    `wait_s` passes with no speech, then on_timeout()).
    mode "always":  every stretch of speech is a request.
    """

    def __init__(self, on_request, on_level=None, on_timeout=None, on_speech_start=None,
                 mode: str = "trigger", wait_s: float = 8.0, device=None):
        self.on_request, self.on_level = on_request, on_level or (lambda db, noise_db, speaking: None)
        self.on_timeout = on_timeout or (lambda: None)
        self.on_speech_start = on_speech_start or (lambda: None)
        self.mode, self.wait_s, self.device = mode, wait_s, device
        self.endpointer = Endpointer()
        self.frames: queue.Queue = queue.Queue()
        self.armed_until = 0.0       # trigger mode: listening for a request until this time
        self.muted = False
        self.running = False
        self.problem = ""
        self._stream = None
        self._thread: threading.Thread | None = None
        self._count = 0
        self._was_armed = False

    # ---- control ----------------------------------------------------------

    def start(self) -> bool:
        """Open the microphone. Returns False (with .problem set) if it can't."""
        try:
            import sounddevice as sd
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME,
                                          device=self.device, callback=self._on_audio)
            self._stream.start()
        except Exception as error:
            self.problem = (f"Could not open the microphone ({error}). Check System Settings > Privacy & "
                            f"Security > Microphone and allow the app that runs Second Voice (Terminal).")
            return False
        self.running = True
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self.running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def trigger(self, wait_s: float | None = None) -> None:
        """Listen now: accept the next request."""
        self.endpointer.reset()
        self.armed_until = time.monotonic() + (wait_s or self.wait_s)

    def cancel(self) -> None:
        """Stop listening for a request (without reporting a timeout)."""
        self.armed_until = 0.0
        self._was_armed = False
        self.endpointer.reset()

    @property
    def listening(self) -> bool:
        return self.running and not self.muted and (self.mode == "always" or time.monotonic() < self.armed_until
                                                    or self.endpointer.in_speech)

    def mute(self) -> None:
        """Stop hearing (while the Mac itself speaks)."""
        self.muted = True
        self.endpointer.reset()

    def unmute(self) -> None:
        self.muted = False
        self.endpointer.reset()

    # ---- audio ----------------------------------------------------------

    def _on_audio(self, indata, frames, time_info, status) -> None:
        self.frames.put(indata[:, 0].copy())

    def _work(self) -> None:
        last_level = 0.0
        while self.running:
            try:
                frame = self.frames.get(timeout=0.2)
            except queue.Empty:
                continue
            now = time.monotonic()
            if now - last_level > 0.1:
                last_level = now
                self.on_level(dbfs(rms(frame)), dbfs(self.endpointer.noise), self.endpointer.in_speech)
            if self.muted:
                continue
            armed = self.mode == "always" or now < self.armed_until or self.endpointer.in_speech
            if not armed:
                if self._was_armed and self.mode == "trigger":
                    self.on_timeout()       # nobody spoke after "listen now"
                self._was_armed = False
                self._track_room(frame)
                continue
            self._was_armed = True
            started = self.endpointer.in_speech
            segment = self.endpointer.feed(frame)
            if self.endpointer.in_speech and not started:
                self.on_speech_start()
            if segment is not None:
                if self.mode == "trigger":
                    self.armed_until = 0.0
                    self._was_armed = False
                self._count += 1
                path = Path(tempfile.gettempdir()) / f"secondvoice_request_{self._count % 20}.wav"
                write_wav(path, segment.samples)
                try:
                    self.on_request(str(path), segment)
                except Exception as error:   # a failure downstream must not stop the microphone
                    self.problem = f"{type(error).__name__}: {error}"

    def _track_room(self, frame: np.ndarray) -> None:
        """While not listening for a request, keep measuring the room level."""
        self.endpointer.track_room(rms(frame))
        self.endpointer.pre_roll.append(frame)


if __name__ == "__main__":
    # Try on its own (uses the microphone and real WhisperKit):
    #   .venv/bin/python -m asr.mic            say something; it prints what it heard
    import sys
    from asr.real import WhisperKitASR
    recognizer = WhisperKitASR()

    def heard(path, segment):
        result = recognizer.transcribe(path)
        print(f"\nheard: {result.text!r}   (speech {segment.speech_s:.1f} s at {segment.level_dbfs:.0f} dBFS, "
              f"room {segment.noise_dbfs:.0f} dBFS, recognizer {result.latency_ms:.0f} ms)")

    listener = Listener(heard, mode="always")
    if not listener.start():
        sys.exit(listener.problem)
    print("Listening (always-on test). Speak, pause 1.5 s. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        listener.stop()
