"""Session tracer (SPEC §9): one JSONL file per session, sidecars for large payloads."""

from __future__ import annotations

import json
import re
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

TraceKind = Literal[
    "mcp_request",
    "mcp_response",
    "detect",
    "snapshot_raw",
    "snapshot_normalized",
    "snapshot_ai_view",
    "action_request",
    "action_resolution",
    "action_native",
    "action_result",
    "post_action_diff",
    "error",
]

SIDECAR_BYTES = 32_000
INLINE_FIELD_BYTES = 1_024
ALWAYS_SIDECAR: set[str] = {"snapshot_raw"}


class TraceEvent(BaseModel):
    id: str
    ts: str
    session: str
    kind: TraceKind
    app_id: str | None = None
    payload: dict[str, Any]


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:40] or "app"


class Tracer:
    def __init__(self, base_dir: Path | str, app: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = Path(base_dir)
        name = f"{stamp}_{_slug(app)}"
        session_dir = base / name
        n = 2
        while session_dir.exists():
            session_dir = base / f"{name}-{n}"
            n += 1
        session_dir.mkdir(parents=True)
        self.session_dir = session_dir
        self.session = session_dir.name
        self.events_path = session_dir / "events.jsonl"
        self._file = self.events_path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._seq = 0

    def emit(self, kind: TraceKind, payload: dict[str, Any], app_id: str | None = None) -> str:
        with self._lock:
            self._seq += 1
            event_id = f"ev{self._seq:05d}"
            body = json.dumps(payload, ensure_ascii=False, default=str)
            size = len(body.encode("utf-8"))
            if kind in ALWAYS_SIDECAR or size > SIDECAR_BYTES:
                sidecar = f"raw_{event_id}.json"
                (self.session_dir / sidecar).write_text(body, encoding="utf-8")
                # Full payload lives in the sidecar; small fields stay inline for the timeline.
                full = json.loads(body)
                stored: dict[str, Any] = {
                    k: v for k, v in full.items() if len(json.dumps(v, ensure_ascii=False)) <= INLINE_FIELD_BYTES
                }
                stored.update(sidecar=sidecar, bytes=size)
            else:
                stored = json.loads(body)  # JSON-safe copy (default=str applied)
            event = TraceEvent(
                id=event_id,
                ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                session=self.session,
                kind=kind,
                app_id=app_id,
                payload=stored,
            )
            self._file.write(event.model_dump_json() + "\n")
            self._file.flush()
            return event_id

    def error(self, exc: BaseException, context: dict[str, Any], app_id: str | None = None) -> str:
        stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        return self.emit(
            "error",
            {"message": f"{type(exc).__name__}: {exc}", "stack": stack, "context": context},
            app_id=app_id,
        )

    def close(self) -> None:
        with self._lock:
            self._file.close()
