"""Make the TTSKit voice ready for every expected sentence (run once before a demo).

    .venv/bin/python scripts/prepare_voice.py

The same list Cowork prepares in the background when it starts: the commands'
spoken steps, "Opening <app>." for the most-used apps and the websites, and
every phrase. Already-prepared sentences are skipped. Anything not on this
list is spoken by the Mac voice (instant) and prepared for next time.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from repair.store import open_profile  # noqa: E402


if __name__ == "__main__":
    from actions.mac import MacActions
    from tts.real import RealTTS
    base = config.ROOT / "profiles" / "cowork_base.json"
    profile = open_profile(config.ROOT / "profiles" / "cowork_profile.json", base)
    from tts.plan import sentences
    todo = sentences(profile, MacActions().context()["app"])
    tts = RealTTS()
    missing = [s for s in todo if not tts.is_cached(s)]
    print(f"{len(todo)} sentences, {len(missing)} still to make (about 4 s each)")
    start = time.perf_counter()
    for n, text in enumerate(missing, 1):
        t = time.perf_counter()
        try:
            tts._synthesize(text, play=False)
            print(f"[{n}/{len(missing)}] {time.perf_counter() - t:4.1f} s  {text}", flush=True)
        except Exception as error:
            print(f"[{n}/{len(missing)}] FAILED {text}: {error}", flush=True)
    print(f"done in {time.perf_counter() - start:.0f} s; ready: {sum(tts.is_cached(s) for s in todo)}/{len(todo)}")
