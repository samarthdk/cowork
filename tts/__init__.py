"""Text-to-speech. Use get_tts() to get the fake or real version chosen in config.py."""

import config


def get_tts():
    if config.TTS == "fake":
        from tts.fake import FakeTTS
        return FakeTTS()
    if config.TTS == "real":
        from tts.real import RealTTS
        return RealTTS()
    raise ValueError(f'config.TTS must be "fake" or "real", not "{config.TTS}"')
