"""Debug viewer (SPEC §9): FastAPI on 127.0.0.1, plain HTML + vanilla JS from static/."""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from uibridge.viewer import traces as tr

STATIC = Path(__file__).parent / "static"
HOST = "127.0.0.1"  # local only, never configurable


def create_app(traces_dir: Path | str) -> FastAPI:
    traces = Path(traces_dir)
    app = FastAPI(title="uibridge viewer", docs_url=None, redoc_url=None, openapi_url=None)

    def guard(fn, *args):
        try:
            return fn(traces, *args)
        except tr.NotFound as e:
            raise HTTPException(status_code=404, detail=f"not found: {e}") from e

    @app.get("/api/sessions")
    def sessions() -> list[dict]:
        return tr.list_sessions(traces)

    @app.get("/api/sessions/{sid}")
    def session(sid: str) -> dict:
        return guard(tr.summarize, sid)

    @app.get("/api/sessions/{sid}/events")
    def events(sid: str, after: str | None = None) -> list[dict]:
        return guard(tr.read_events, sid, after)

    @app.get("/api/sessions/{sid}/sidecar/{name}")
    def sidecar(sid: str, name: str):
        return guard(tr.load_sidecar, sid, name)

    @app.get("/api/sessions/{sid}/snapshot/{event_id}")
    def snapshot(sid: str, event_id: str) -> dict:
        return guard(tr.snapshot_group, sid, event_id)

    @app.get("/api/sessions/{sid}/action/{event_id}")
    def action(sid: str, event_id: str) -> dict:
        return guard(tr.action_group, sid, event_id)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def serve(traces_dir: Path | str, port: int = 8765, open_browser: bool = True) -> None:
    url = f"http://{HOST}:{port}/"
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    print(f"uibridge viewer on {url}  (traces: {Path(traces_dir).resolve()})")
    uvicorn.run(create_app(traces_dir), host=HOST, port=port, log_level="warning")
