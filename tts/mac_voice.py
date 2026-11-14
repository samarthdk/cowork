"""The Mac's own voice, kept loaded inside the app (AVSpeechSynthesizer).

Used for sentences TTSKit has not prepared yet. Measured on this Mac:
  - the `say` command takes about 1 s to start for every sentence;
  - this voice, kept loaded, starts the first sound in about 25 ms
    (0.4 s for the very first sentence, so `warm()` is called at start-up).

    voice = MacVoice()
    voice.warm()
    voice.speak("Searching for the weather in Boston.")   # returns when finished
    voice.stop()                                          # cuts it off

Which voice: config.MAC_VOICE if set, otherwise the best-quality US English
male voice installed (TTSKit's voice, "aiden", is male too), preferring the
names in PREFERRED. Better voices can be added in System Settings >
Accessibility > Spoken Content > System Voice > Manage Voices.
"""

import threading
import time

import config

PREFERRED = ("Evan", "Nathan", "Alex", "Tom", "Aaron", "Reed", "Eddy", "Fred")


def choose_voice():
    import AVFoundation as AV
    voices = [v for v in AV.AVSpeechSynthesisVoice.speechVoices() if str(v.language()) == "en-US"]
    if getattr(config, "MAC_VOICE", ""):
        named = [v for v in voices if str(v.name()) == config.MAC_VOICE]
        if named:
            return named[0]

    def rank(v):
        name = str(v.name())
        preferred = PREFERRED.index(name) if name in PREFERRED else len(PREFERRED)
        return (-int(v.quality()), preferred, int(v.gender()) != 1)   # quality, then the list, then male

    voices.sort(key=rank)
    return voices[0] if voices else AV.AVSpeechSynthesisVoice.voiceWithLanguage_("en-US")


class MacVoice:
    def __init__(self):
        import AVFoundation as AV
        self._AV = AV
        self.synth = AV.AVSpeechSynthesizer.alloc().init()
        self.voice = choose_voice()
        self.name = str(self.voice.name()) if self.voice else "system"
        self._lock = threading.Lock()
        self._stopped = False

    def warm(self) -> None:
        """Load the voice now (silently), so the first real sentence starts at once."""
        utterance = self._utterance("Hello.")
        done = threading.Event()
        self.synth.writeUtterance_toBufferCallback_(utterance, lambda buffer: done.set())
        done.wait(3.0)

    def _utterance(self, text: str):
        utterance = self._AV.AVSpeechUtterance.speechUtteranceWithString_(text)
        if self.voice is not None:
            utterance.setVoice_(self.voice)
        return utterance

    def speak(self, text: str, timeout_s: float = 120.0) -> None:
        """Speak and wait until finished (or stopped)."""
        with self._lock:
            self._stopped = False
            self.synth.speakUtterance_(self._utterance(text))
            start = time.monotonic()
            while not self.synth.isSpeaking() and time.monotonic() - start < 1.0 and not self._stopped:
                time.sleep(0.01)   # it starts within a few ms
            while self.synth.isSpeaking() and time.monotonic() - start < timeout_s and not self._stopped:
                time.sleep(0.03)

    def stop(self) -> None:
        self._stopped = True
        self.synth.stopSpeakingAtBoundary_(0)   # immediately


if __name__ == "__main__":
    # Try on its own: .venv/bin/python -m tts.mac_voice "Some words"
    import sys
    voice = MacVoice()
    voice.warm()
    print("voice:", voice.name)
    start = time.perf_counter()
    voice.speak(" ".join(sys.argv[1:]) or "This is the Mac voice, used for sentences that are not prepared yet.")
    print(f"done in {time.perf_counter() - start:.1f} s")
