"""Try TTS on its own:  .venv/bin/python -m tts "Hello there" """

import sys

from tts import get_tts

get_tts().speak(" ".join(sys.argv[1:]) or "Hello, this is Second Voice.")
