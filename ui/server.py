"""The local web server: one HTML page plus a few small endpoints.
It only listens on this Mac (127.0.0.1); nothing is sent anywhere.

    GET  /              the page (ui/static/index.html)
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
from fastapi.responses import FileResponse, StreamingResponse

from ui.app import App

PAGE = Path(__file__).parent / "static" / "index.html"
KEEP_ALIVE_S = 15


def create_server(core: App) -> FastAPI:
    server = FastAPI(title="Second Voice", docs_url=None, redoc_url=None, openapi_url=None)

    @server.get("/")
    def page():
        return FileResponse(PAGE, headers={"Cache-Control": "no-store"})

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
    def listen():
        return {"ok": core.listen_now()}

    @server.post("/api/undo")
    def undo():
        core.undo()
        return {"ok": True}

    return server
