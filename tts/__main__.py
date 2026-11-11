"""Try the voice on its own:

    .venv/bin/python -m tts "Hello there"            (uses config.TTS: fake or real)
    .venv/bin/python -m tts --real "Hello there"     (force the real voice; plays sound)
    .venv/bin/python -m tts --prepare                (pre-make audio for every profile phrase)
"""

import sys
import time

import config

words = [a for a in sys.argv[1:] if not a.startswith("--")]
if "--real" in sys.argv or "--prepare" in sys.argv:
    config.TTS = "real"
from tts import get_tts  # noqa: E402

tts = get_tts()
if "--prepare" in sys.argv:
    from engine.speech import sentences_to_prepare
    from repair.store import open_profile
    texts = sentences_to_prepare(open_profile())
    todo = [t for t in texts if not tts.is_cached(t)]
    print(f"{len(texts)} sentences, {len(todo)} still to make (about 7 s each)...")
    tts.prepare(todo)
    while tts.waiting() or (tts._worker and tts._worker.is_alive()):
        time.sleep(1)
    print("done:", sum(tts.is_cached(t) for t in texts), "of", len(texts), "ready")
else:
    start = time.perf_counter()
    tts.speak(" ".join(words) or "Hello, this is Second Voice.")
    print(f"(took {(time.perf_counter() - start) * 1000:.0f} ms)")
