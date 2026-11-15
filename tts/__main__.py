"""Try the voice on its own:

    .venv/bin/python -m tts "Hello there"            (uses config.TTS: fake or real)
    .venv/bin/python -m tts --real "Opening Mail."             (real TTSKit; silent if not ready within 2 s)
    .venv/bin/python -m tts --real --wait "A new sentence"      (wait for TTSKit however long it takes)
    .venv/bin/python -m tts --prepare                (make every sentence Cowork expects to say)

Only TTSKit speaks, one whole sentence at a time (tts/real.py, tts/lines.py).
"""

import sys
import time

import config

words = [a for a in sys.argv[1:] if not a.startswith("--")]
if "--real" in sys.argv or "--prepare" in sys.argv:
    config.TTS = "real"
from repair.store import open_profile  # noqa: E402
from tts import get_tts  # noqa: E402

profile = open_profile(config.ROOT / "profiles" / "cowork_profile.json", config.ROOT / "profiles" / "cowork_base.json")
tts = get_tts()
if hasattr(tts, "profile"):
    tts.profile = profile
if "--prepare" in sys.argv:
    from tts.lines import to_prepare
    texts = to_prepare(profile, [])
    todo = [t for t in texts if not tts.is_cached(t)]
    print(f"{len(texts)} sentences, {len(todo)} still to make (about 3-7 s each)...")
    tts.prepare(todo)
    while tts.waiting() or (getattr(tts, "_worker", None) and tts._worker.is_alive()):
        time.sleep(1)
    print("done:", sum(tts.is_cached(t) for t in texts), "of", len(texts), "ready")
else:
    line = " ".join(words) or "Opening Mail."
    start = time.perf_counter()
    spoken = tts.speak(line, wait="--wait" in sys.argv)
    print(f"{'spoken' if spoken else 'NOT spoken (voice not ready)'} in {(time.perf_counter() - start) * 1000:.0f} ms;",
          getattr(tts, "last", ""))
