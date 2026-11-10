"""Speech-to-text. Use get_asr() to get the fake or real version chosen in config.py."""

import config


def get_asr():
    if config.ASR == "fake":
        from asr.fake import FakeASR
        return FakeASR()
    if config.ASR == "real":
        from asr.real import WhisperKitASR
        return WhisperKitASR()
    raise ValueError(f'config.ASR must be "fake" or "real", not "{config.ASR}"')
