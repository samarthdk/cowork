"""Start Cowork (Second Voice's screen) on this Mac.

    .venv/bin/python -m ui --fake           everything fake: no LM Studio, WhisperKit, microphone or camera needed
    .venv/bin/python -m ui                  the real parts chosen in config.py, with the camera
    .venv/bin/python -m ui --real --dictation   the dictation demo: everything real, typing into Word
    .venv/bin/python -m ui --mic            also listen on the microphone (on by default with --real)
    .venv/bin/python -m ui --fresh          start learning from scratch (the old profile is backed up)

Other switches:
    --no-mic           don't use the microphone (also with --real)
    --dictation        start with dictation on (confirmed sentences are typed into Word / the app you were using)
    --no-camera        don't use the camera (hand answers off)
    --camera           use the camera even with --fake
    --profile personal use your own profile (profiles/profile.json) instead of the Cowork demo profile
    --browser          show the page in Safari instead of Cowork's own window
    --no-window        only run the local server (open http://localhost:8765 yourself)

Cowork opens in its own window. Close the window (or press Ctrl+C here) to stop.
With --fake, learning goes into a separate fake profile, so the real one is never
touched by the fake model.
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

PROFILES = {   # name: (base, working copy, working copy with --fake)
    "cowork": (config.ROOT / "profiles" / "cowork_base.json", config.ROOT / "profiles" / "cowork_profile.json",
               config.ROOT / "profiles" / "fake_cowork_profile.json"),
    "personal": (config.BASE_PROFILE_PATH, config.PROFILE_PATH, config.ROOT / "profiles" / "fake_profile.json"),
}


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


def wait_for_server(url: str, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url + "/api/state", timeout=1):
                return True
        except Exception:
            time.sleep(0.1)
    return False


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Cowork")
    parser.add_argument("--fake", action="store_true", help="use the fake ASR, LLM, voice and Mac actions")
    parser.add_argument("--real", action="store_true",
                        help="use every real part (WhisperKit, Bonsai, TTSKit, Mac actions), whatever config.py says")
    parser.add_argument("--mic", action="store_true", help="listen on the microphone (on by default with --real)")
    parser.add_argument("--no-mic", action="store_true", help="don't use the microphone, even with --real")
    parser.add_argument("--fresh", action="store_true", help="start learning from scratch")
    parser.add_argument("--camera", action="store_true", help="use the camera even with --fake")
    parser.add_argument("--no-camera", action="store_true", help="don't use the camera")
    parser.add_argument("--dictation", action="store_true",
                        help="start with dictation on: confirmed sentences are typed into the app you were using")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="cowork")
    parser.add_argument("--work-profile", type=Path, default=None,
                        help="learn into this file instead (for tests: the person's own profile is not touched)")
    parser.add_argument("--port", type=int, default=config.UI_PORT)
    parser.add_argument("--browser", action="store_true", help="open the page in Safari instead of a window")
    parser.add_argument("--no-window", "--no-browser", dest="no_window", action="store_true")
    options = parser.parse_args(argv)

    if options.fake and options.real:
        sys.exit("Choose --fake or --real, not both.")
    if options.fake:
        config.ASR = config.LLM = config.TTS = config.ACTIONS = "fake"
    if options.real:
        config.ASR = config.LLM = config.TTS = config.ACTIONS = "real"
    base, working, fake_working = PROFILES[options.profile]
    profile_path = options.work_profile or (fake_working if options.fake else working)

    from actions import get_actions
    from asr import get_asr
    from repair import get_llm
    from repair.store import fresh_profile, open_profile
    from tts import get_tts
    from ui.app import App
    from ui.clips import CREDIT, load_clips
    from ui.server import create_server

    if config.LLM == "real":
        problem = lm_studio_ready()
        if problem:
            sys.exit(problem)
    if not base.exists():
        sys.exit(f"Missing {base}. Make it with:  .venv/bin/python scripts/make_cowork_profile.py")
    stem = profile_path.stem
    tasks_path = profile_path.with_name((stem.replace("profile", "tasks") if "profile" in stem else stem + "_tasks") + ".json")
    if options.fresh:
        backup = fresh_profile(profile_path, base)
        print("FRESH START" + (f" (old profile saved to {backup})" if backup else ""))
        if tasks_path.exists():   # Recent tasks are cleared with the learning (kept in the backups folder)
            config.PROFILE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            tasks_path.replace(config.PROFILE_BACKUP_DIR / f"{tasks_path.stem}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    profile = open_profile(profile_path, base)

    tts = get_tts()
    if hasattr(tts, "profile"):
        tts.profile = profile     # lines are split by the commands' own wording ("Opening" + "Mail")
    if config.TTS == "fake":
        tts.quiet = True
    actions = get_actions()
    backends = {"Speech in": config.ASR, "Brain": config.LLM, "Voice": config.TTS, "Mac actions": config.ACTIONS}
    core = App(profile, get_llm(), tts, asr=get_asr(), actions=actions, profile_path=profile_path,
               backends=backends, warm=config.LLM == "real", tasks_path=tasks_path)
    core.set_clips(load_clips(), CREDIT)
    core.watch_target()   # the dictation pill follows the app the person is working in
    if options.dictation:
        core.set_dictation(True)
    core.start_metrics()

    if hasattr(tts, "prepare"):   # make the TTSKit pieces ready in the background (real voice only)
        from tts.lines import to_prepare
        try:
            apps = actions.context().get("app", [])
        except Exception:
            apps = []
        threading.Thread(target=tts.prepare, args=(to_prepare(profile, apps),), daemon=True).start()
        problem = tts.available() if hasattr(tts, "available") else ""
        if problem:
            print(f"Voice: TTSKit can't run ({problem}); Cowork will stay silent and show everything on screen.")
    if config.LLM == "real":
        core.session.warm_up()    # LM Studio reads the instructions now, so the first request is fast

    use_mic = not options.no_mic and (options.mic or options.real)
    if not use_mic:
        core.mic_off("Started with --no-mic." if options.no_mic
                     else "Test mode (--fake): type, or use Test clips." if options.fake
                     else "Started without the microphone (add --mic).")
    if use_mic:
        from asr.mic import Listener
        listener = Listener(core.heard_audio, on_level=core.mic_level, on_timeout=core.mic_timeout,
                            on_partial=core.partial_audio)
        if not listener.start():
            print(listener.problem)
        core.attach_listener(listener)

    use_camera = not options.no_camera and (options.camera or not options.fake)
    if use_camera:
        from choose.camera import Camera
        problem = Camera.available()
        if problem:
            print("Camera off:", problem)
        else:
            core.attach_camera(Camera(on_signal=core.signal, on_hold=core.hold, on_state=core.camera_state))
            try:
                config.pick_camera()   # the built-in FaceTime HD Camera only; say so now if it's missing
            except config.CameraNotFound as missing:
                print("Camera:", missing)
                core.camera_state({"problem": str(missing)})

    import uvicorn
    url = f"http://localhost:{options.port}"
    print(f"Cowork is running at {url}   (speech in: {config.ASR}, brain: {config.LLM}, voice: {config.TTS}, "
          f"Mac actions: {config.ACTIONS}, microphone: {'on' if core.listener and core.listener.running else 'off'}, "
          f"camera: {'on' if core.camera else 'off'}, profile: {profile_path.name})")
    server = uvicorn.Server(uvicorn.Config(create_server(core), host="127.0.0.1", port=options.port,
                                           log_level="warning"))
    if options.no_window or options.browser:
        if options.browser:
            def open_page():
                time.sleep(1.0)
                subprocess.run(["open", "-a", "Safari", url], check=False)
            threading.Thread(target=open_page, daemon=True).start()
        print("Press Ctrl+C to stop.")
        server.run()
        return
    threading.Thread(target=server.run, daemon=True, name="server").start()
    if not wait_for_server(url):
        sys.exit("The local server did not start. Is another copy of Cowork already running?")
    print("Close the Cowork window (or press Ctrl+C here) to stop.")
    from ui.window import run_window
    run_window(url, core)


if __name__ == "__main__":
    main()
