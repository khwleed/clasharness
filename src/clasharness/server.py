"""FastAPI app: REST commands, a WebSocket state stream, and the static UI."""

from __future__ import annotations

import asyncio
import struct
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .harness import CommandError, Harness

WEB = Path(__file__).parent / "web"


def create_app(harness: Harness, on_startup=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await harness.startup()
        if on_startup:
            on_startup()
        yield
        await harness.shutdown()

    app = FastAPI(title="clasharness", lifespan=lifespan)
    app.state.harness = harness

    @app.middleware("http")
    async def revalidate_ui(request, call_next):
        # Without this, browsers may reuse an old app.js after an update and pair it
        # with a newer index.html. no-cache still uses the cached copy when it's current.
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/state")
    def state():
        return harness.snapshot()

    @app.get("/api/cards")
    def cards():
        return harness.catalog.to_list()

    @app.post("/api/command")
    def command(cmd: dict):
        try:
            return {"ok": True, **harness.dispatch(cmd)}
        except CommandError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/matches")
    def matches(limit: int = 20):
        return harness.db.recent_matches(limit)

    @app.get("/api/matches/{match_id}/events")
    def match_events(match_id: int):
        return harness.db.match_events(match_id)

    @app.get("/api/vision/{which}.png")
    def vision_image(which: str):
        if which not in ("last_screen", "last_crop"):
            raise HTTPException(404)
        p = harness.var / "vision" / f"{which}.png"
        if not p.is_file():
            raise HTTPException(404, "no capture yet")
        return FileResponse(p, headers={"Cache-Control": "no-store"})

    @app.post("/api/vision/crop")
    def set_crop(box: list[float]):
        if len(box) != 4 or not all(0 <= v <= 1 for v in box) or box[0] >= box[2] or box[1] >= box[3]:
            raise HTTPException(400, "crop must be [x0, y0, x1, y1] fractions with x0<x1, y0<y1")
        harness.cfg["vision"]["crop"] = box
        harness.touch()
        return {"ok": True, "note": "applies until restart; copy it into config.toml to keep it"}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        last = -1
        try:
            while True:
                running = harness.match is not None and harness.match.started and not harness.match.ended
                if running or harness.version != last:
                    last = harness.version
                    await socket.send_json(harness.snapshot())
                await asyncio.sleep(0.1)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.websocket("/ws/phone")
    async def phone_ws(socket: WebSocket):
        """Phone video out (binary: 1 kind byte + H.264 packet), status as JSON, taps in as JSON."""
        await socket.accept()
        phone = harness.phone
        if phone is None:
            await socket.send_json({"type": "status", "state": "disabled",
                                    "detail": "The embedded phone is off ([phone].embed in config.toml)."})
            await socket.close()
            return
        queue = phone.subscribe()

        async def outgoing():
            while True:
                item = await queue.get()
                if isinstance(item, dict):
                    await socket.send_json(item)
                else:
                    await socket.send_bytes(item)

        async def incoming():
            while True:
                msg = await socket.receive_json()
                if isinstance(msg, dict):
                    try:
                        phone.handle_input(msg)
                    except (KeyError, TypeError, ValueError, OverflowError, struct.error):
                        pass

        tasks = [asyncio.create_task(outgoing()), asyncio.create_task(incoming())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            phone.unsubscribe(queue)

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
