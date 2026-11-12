"""Start the Second Voice app (the screen) on this Mac.

    .venv/bin/python -m ui --fake           everything fake: no LM Studio, WhisperKit or microphone needed
    .venv/bin/python -m ui                  the real parts chosen in config.py
    .venv/bin/python -m ui --mic            also listen on the microphone ("Listen now")
    .venv/bin/python -m ui --fresh          start learning from scratch (the old profile is backed up)
    .venv/bin/python -m ui --no-browser     don't open Safari

Then open http://localhost:8765 (Safari opens by itself). Ctrl+C stops it.
With --fake, learning goes into profiles/fake_profile.json so your real
profile is never touched by the fake model.
"""

import argparse
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

FAKE_PROFILE = config.ROOT / "profiles" / "fake_profile.json"


def lm_studio_ready() -> str:
    """'' if LM Studio's server answers on localhost, else what to do about it."""
    try:
        with urllib.request.urlopen(config.LLM_BASE_URL + "/models", timeout=2) as reply:
            models = reply.read().decode()
    except Exception:
        return ("LM Studio's server isn't answering at " + config.LLM_BASE_URL + ".\n"
                "  Open LM Studio, go to the Developer tab, load " + config.LLM_MODEL + ", and switch the server on.\n"
                "  Or try the app with everything fake:  .venv/bin/python -m ui --fake")
    if config.LLM_MODEL not in models:
        return f"LM Studio is running, but {config.LLM_MODEL} isn't loaded. Load it in the Developer tab."
    return ""


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Second Voice app")
    parser.add_argument("--fake", action="store_true", help="use the fake ASR, LLM, voice and Mac actions")
    parser.add_argument("--mic", action="store_true", help="listen on the microphone")
    parser.add_argument("--fresh", action="store_true", help="start learning from scratch")
    parser.add_argument("--port", type=int, default=config.UI_PORT)
    parser.add_argument("--no-browser", action="store_true")
    options = parser.parse_args(argv)

    if options.fake:
        config.ASR = config.LLM = config.TTS = config.ACTIONS = "fake"
    profile_path = FAKE_PROFILE if options.fake else config.PROFILE_PATH

    from actions import get_actions
    from asr import get_asr
    from repair import get_llm
    from repair.store import fresh_profile, open_profile
    from tts import get_tts
    from ui.app import App
    from ui.server import create_server

    if config.LLM == "real":
        problem = lm_studio_ready()
        if problem:
            sys.exit(problem)
    if options.fresh:
        backup = fresh_profile(profile_path)
        print("FRESH START" + (f" (old profile saved to {backup})" if backup else ""))
    profile = open_profile(profile_path)

    tts = get_tts()
    if config.TTS == "fake":
        tts.quiet = True
    backends = {"Speech in": config.ASR, "Brain": config.LLM, "Voice": config.TTS, "Mac actions": config.ACTIONS}
    core = App(profile, get_llm(), tts, asr=get_asr(), actions=get_actions(), profile_path=profile_path,
               backends=backends, warm=config.LLM == "real")
    core.start_metrics()

    if hasattr(tts, "prepare"):   # make the known phrases ready in the background (real voice only)
        from engine.speech import sentences_to_prepare
        threading.Thread(target=tts.prepare, args=(sentences_to_prepare(profile),), daemon=True).start()
    if config.LLM == "real":
        core.session.warm_up()    # LM Studio reads the instructions now, so the first request is fast

    if options.mic:
        from asr.mic import Listener
        listener = Listener(core.heard_audio, on_level=core.mic_level, on_timeout=core.mic_timeout)
        if not listener.start():
            print(listener.problem)
        core.attach_listener(listener)

    import uvicorn
    url = f"http://localhost:{options.port}"
    print(f"Second Voice is running at {url}   (speech in: {config.ASR}, brain: {config.LLM}, "
          f"voice: {config.TTS}, Mac actions: {config.ACTIONS}, microphone: {'on' if options.mic else 'off'})")
    print("Press Ctrl+C to stop.")
    if not options.no_browser:
        def open_page():
            time.sleep(1.0)
            subprocess.run(["open", "-a", "Safari", url], check=False)
        threading.Thread(target=open_page, daemon=True).start()
    uvicorn.run(create_server(core), host="127.0.0.1", port=options.port, log_level="warning")


if __name__ == "__main__":
    main()
