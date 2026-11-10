"""Try ASR on its own:  .venv/bin/python -m asr [audio_file]"""

import sys

import config
from asr import get_asr

audio = sys.argv[1] if len(sys.argv) > 1 else "test.wav"
print(f"ASR ({config.ASR}) heard: {get_asr().transcribe(audio)!r}")
