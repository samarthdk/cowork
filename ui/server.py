"""The local web server: one HTML page plus a few small endpoints.
It only listens on this Mac (127.0.0.1); nothing is sent anywhere.

    GET  /              the Cowork page (ui/static/cowork.html)
    GET  /classic       the earlier Second Voice page (ui/static/index.html)
    GET  /mini          the small window in the corner (ui/static/mini.html)
    POST /api/window    {"mode": "mini"|"full"}      shrink to the corner / back to the full window
    POST /api/handsfree {"on": false}                 stop (or start) listening hands-free
    GET  /api/camera.jpg the newest camera preview frame (only while a question is open)
    POST /api/clip      {"id": "M04_2_0015"}    play a TORGO test clip as if it were said
    POST /api/new       save the conversation as a task (Recent tasks), then clear the screen
    POST /api/task      {"action": "view"|"continue"|"close", "id": "..."}   a saved task: show it, carry on with it
    POST /api/ui        {"quick": true}  Quick Actions is open (speech filters it, thumbs pick from it)
    POST /api/dictation {"on": true}            type confirmed sentences into the app you were using
    POST /api/voice    {"on": true}             speak confirmations and read replies aloud (on by default)
    POST /api/mode     {"mode": "computer"|"conference"}   Conference mode: speak confirmed sentences to the room
    POST /api/quick    {"index": 0}             Conference mode: say a meeting quick phrase
    GET  /api/state     everything the page shows, once
    GET  /api/stream    live updates (Server-Sent Events: the page keeps this open)
    POST /api/say       {"text": "..."}             something the person said (typed)
    POST /api/answer    {"kind": "yes"|"no"|"pick"|"none"|"text", "value": ...}
    POST /api/signal    {"signal": "RIGHT_UP"}      a hand signal (for testing without the camera)
    POST /api/listen    "listen now"
    POST /api/undo      forget the last thing learned
    POST /api/stop      stop everything now (speaking, typing, waiting requests, the open question)
"""

import asyncio
import json
import os
from pathlib import Path

from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, Response, StreamingResponse

from ui.app import App

PAGE = Path(__file__).parent / "static" / "cowork.html"
CLASSIC = Path(__file__).parent / "static" / "index.html"
MINI = Path(__file__).parent / "static" / "mini.html"
KEEP_ALIVE_S = 15


def create_server(core: App) -> FastAPI:
    server = FastAPI(title="Second Voice", docs_url=None, redoc_url=None, openapi_url=None)

    @server.get("/")
    def page():
        return FileResponse(PAGE, headers={"Cache-Control": "no-store"})

    @server.get("/classic")
    def classic():
        return FileResponse(CLASSIC, headers={"Cache-Control": "no-store"})

    if os.environ.get("COWORK_TEST_AUDIO"):   # tests only (off unless set): feed a recording as if the microphone heard it
        @server.post("/api/test_audio")
        def test_audio(body: dict = Body(...)):
            import threading
            threading.Thread(target=core.heard_audio, args=(str(body.get("path", "")),), daemon=True).start()
            return {"ok": True}

    @server.get("/mini")
    def mini():
        return FileResponse(MINI, headers={"Cache-Control": "no-store"})

    @server.post("/api/window")
    def window(body: dict = Body(...)):
        core.set_window(str(body.get("mode", "")))
        return {"ok": True, "window": core.state["window"]}

    @server.post("/api/handsfree")
    def hands_free(body: dict = Body(...)):
        core.set_hands_free(bool(body.get("on")))
        return {"ok": True, "window": core.state["window"]}

    @server.get("/api/camera.jpg")
    def camera_frame():
        frame = core.camera.jpeg() if core.camera is not None else None
        if frame is None:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @server.post("/api/clip")
    def clip(body: dict = Body(...)):
        problem = core.play_clip(str(body.get("id", "")))
        return {"ok": not problem, "problem": problem}

    @server.post("/api/dictation")
    def dictation(body: dict = Body(...)):
        core.set_dictation(bool(body.get("on")))
        return {"ok": True, "dictation": core.state["dictation"]}

    @server.post("/api/voice")
    def voice(body: dict = Body(...)):
        core.set_voice(bool(body.get("on")))
        return {"ok": True, "voice": core.state["voice"]}

    @server.post("/api/mode")
    def mode(body: dict = Body(...)):
        core.set_mode(str(body.get("mode", "")))
        return {"ok": True, "mode": core.state["mode"]}

    @server.post("/api/quick")
    def quick(body: dict = Body(...)):
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            return {"ok": False}
        return {"ok": core.quick_phrase(index)}

    @server.post("/api/stop")
    def stop_everything():
        core.stop_all()
        return {"ok": True}

    @server.post("/api/task")
    def task(body: dict = Body(...)):
        action, task_id = body.get("action"), body.get("id")
        if action == "view":
            return {"ok": core.view_task(task_id)}
        if action == "close":
            return {"ok": core.view_task(None)}
        if action == "continue":
            return {"ok": core.continue_task(task_id)}
        return {"ok": False}

    @server.post("/api/ui")
    def ui(body: dict = Body(...)):
        core.set_ui(bool(body.get("quick")))
        return {"ok": True}

    @server.post("/api/new")
    def new_task():
        core.new_task()
        return {"ok": True}

    @server.get("/api/state")
    def state():
        return core.snapshot()

    @server.get("/api/stream")
    async def stream():
        loop = asyncio.get_running_loop()
        inbox: asyncio.Queue = asyncio.Queue(maxsize=500)

        def deliver(message):   # called from the conversation's thread
            def put():
                if inbox.full():           # a stalled tab: drop old updates, keep the newest
                    inbox.get_nowait()
                inbox.put_nowait(message)
            loop.call_soon_threadsafe(put)

        async def events():
            core.subscribe(deliver)
            try:
                yield f"data: {json.dumps({'type': 'state', 'event': None, 'state': core.snapshot()})}\n\n"
                while True:
                    try:
                        message = await asyncio.wait_for(inbox.get(), KEEP_ALIVE_S)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"data: {json.dumps(message, default=str)}\n\n"
            finally:
                core.unsubscribe(deliver)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @server.post("/api/say")
    def say(body: dict = Body(...)):
        return {"done": core.say(str(body.get("text", "")), source="keyboard")}

    @server.post("/api/answer")
    def answer(body: dict = Body(...)):
        return {"ok": core.answer(str(body.get("kind", "")), body.get("value"), source="screen")}

    @server.post("/api/signal")
    def signal(body: dict = Body(...)):
        return {"ok": core.signal(str(body.get("signal", "")), source=str(body.get("source", "hand")))}

    @server.post("/api/listen")
    def listen(body: dict = Body(default={})):
        if body.get("toggle"):
            return {"ok": True, "listening": core.listen_toggle()}
        return {"ok": core.listen_now()}

    @server.post("/api/undo")
    def undo():
        core.undo()
        return {"ok": True}

    return server
