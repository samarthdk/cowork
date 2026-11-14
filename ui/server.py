"""The local web server: one HTML page plus a few small endpoints.
It only listens on this Mac (127.0.0.1); nothing is sent anywhere.

    GET  /              the Cowork page (ui/static/cowork.html)
    GET  /classic       the earlier Second Voice page (ui/static/index.html)
    GET  /api/camera.jpg the newest camera preview frame (only while a question is open)
    POST /api/clip      {"id": "M04_2_0015"}    play a TORGO test clip as if it were said
    POST /api/new       clear the conversation on screen (nothing learned is forgotten)
    POST /api/ui        {"quick": true}  Quick Actions is open (speech filters it, thumbs pick from it)
    POST /api/dictation {"on": true}            type confirmed sentences into the app you were using
    GET  /api/state     everything the page shows, once
    GET  /api/stream    live updates (Server-Sent Events: the page keeps this open)
    POST /api/say       {"text": "..."}             something the person said (typed)
    POST /api/answer    {"kind": "yes"|"no"|"pick"|"none"|"text", "value": ...}
    POST /api/signal    {"signal": "RIGHT_UP"}      a hand signal (for testing without the camera)
    POST /api/listen    "listen now"
    POST /api/undo      forget the last thing learned
"""

import asyncio
import json
from pathlib import Path

from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, Response, StreamingResponse

from ui.app import App

PAGE = Path(__file__).parent / "static" / "cowork.html"
CLASSIC = Path(__file__).parent / "static" / "index.html"
KEEP_ALIVE_S = 15


def create_server(core: App) -> FastAPI:
    server = FastAPI(title="Second Voice", docs_url=None, redoc_url=None, openapi_url=None)

    @server.get("/")
    def page():
        return FileResponse(PAGE, headers={"Cache-Control": "no-store"})

    @server.get("/classic")
    def classic():
        return FileResponse(CLASSIC, headers={"Cache-Control": "no-store"})

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
