"""Read trace sessions (traces/<ts>_<app>/events.jsonl + raw_<ev>.json sidecars) for the viewer."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SIDECAR_NAME = re.compile(r"^raw_ev\d{5}\.json$")
LIVE_SECONDS = 30


class NotFound(Exception):
    pass


def session_dir(traces: Path, sid: str) -> Path:
    """Resolve a session id to its folder; only real child folders are accepted (no traversal)."""
    if sid.startswith(("_", ".")) or "/" in sid or "\\" in sid:
        raise NotFound(sid)
    path = traces / sid
    if not (path / "events.jsonl").is_file():
        raise NotFound(sid)
    return path


def read_events(traces: Path, sid: str, after: str | None = None) -> list[dict[str, Any]]:
    path = session_dir(traces, sid) / "events.jsonl"
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    if after:
        events = [e for e in events if e["id"] > after]  # ids are zero-padded, so string order = sequence order
    return events


def load_sidecar(traces: Path, sid: str, name: str) -> Any:
    if not SIDECAR_NAME.match(name):
        raise NotFound(name)
    path = session_dir(traces, sid) / name
    if not path.is_file():
        raise NotFound(name)
    return json.loads(path.read_text(encoding="utf-8"))


def _full_payload(traces: Path, sid: str, event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    payload = event["payload"]
    return load_sidecar(traces, sid, payload["sidecar"]) if "sidecar" in payload else payload


def _ts(event: dict[str, Any]) -> float:
    return datetime.fromisoformat(event["ts"]).timestamp()


def _failures(events: list[dict[str, Any]]) -> int:
    """Failed calls. In MCP sessions every failure surfaces as an mcp_response error; elsewhere count
    error events plus actions that returned ok=false (without double-counting one failure)."""
    if any(e["kind"] == "mcp_request" for e in events):
        return sum(1 for e in events if e["kind"] == "mcp_response" and "error" in e["payload"])
    errors = sum(1 for e in events if e["kind"] == "error")
    failed_results = sum(1 for e in events if e["kind"] == "action_result" and not e["payload"].get("ok"))
    return errors + failed_results


_summary_cache: dict[Path, tuple[float, dict[str, Any]]] = {}


def summarize(traces: Path, sid: str) -> dict[str, Any]:
    path = session_dir(traces, sid) / "events.jsonl"
    mtime = path.stat().st_mtime
    cached = _summary_cache.get(path)
    if cached and cached[0] == mtime:
        summary = dict(cached[1])
    else:
        events = read_events(traces, sid)
        detects = [e for e in events if e["kind"] == "detect"]
        apps = {e["app_id"] for e in detects}
        strategies: dict[str, int] = {}
        for e in events:
            if e["kind"] == "action_native":
                key = "dry_run" if e["payload"].get("dry_run") else e["payload"].get("strategy", "?")
                strategies[key] = strategies.get(key, 0) + 1
        first = detects[0]["payload"] if detects else {}
        summary = {
            "id": sid,
            "app": sid.split("_", 1)[1] if "_" in sid else sid,
            "framework": first.get("framework") if len(apps) <= 1 else f"{len(apps)} windows",
            "adapter": first.get("chosen") if len(apps) <= 1 else "",
            "started": events[0]["ts"] if events else None,
            "ended": events[-1]["ts"] if events else None,
            "duration_s": round(_ts(events[-1]) - _ts(events[0]), 2) if events else 0,
            "events": len(events),
            "actions": sum(1 for e in events if e["kind"] == "action_request"),
            "errors": _failures(events),
            "strategies": strategies,
            "mcp": any(e["kind"] == "mcp_request" for e in events),
        }
        _summary_cache[path] = (mtime, summary)
        summary = dict(summary)
    summary["live"] = time.time() - mtime < LIVE_SECONDS
    return summary


def list_sessions(traces: Path) -> list[dict[str, Any]]:
    if not traces.is_dir():
        return []
    sids = sorted((p.name for p in traces.iterdir() if (p / "events.jsonl").is_file() and not p.name.startswith("_")),
                  reverse=True)
    return [summarize(traces, sid) for sid in sids]


def _index(events: list[dict[str, Any]], event_id: str) -> int:
    for i, e in enumerate(events):
        if e["id"] == event_id:
            return i
    raise NotFound(event_id)


def snapshot_group(traces: Path, sid: str, event_id: str) -> dict[str, Any]:
    """The raw / normalized / AI-view layers of one snapshot. The router emits them back to back for
    one app; a layer that was not produced (act pre/post snapshots, wait_for) is None."""
    events = read_events(traces, sid)
    anchor = events[_index(events, event_id)]
    if not anchor["kind"].startswith("snapshot_"):
        raise NotFound(f"{event_id} is a {anchor['kind']} event, not a snapshot")
    same_app = [e for e in events if e["app_id"] == anchor["app_id"]]
    pos = next(i for i, e in enumerate(same_app) if e["id"] == event_id)

    if anchor["kind"] == "snapshot_normalized":
        n_pos = pos
    elif anchor["kind"] == "snapshot_ai_view":
        n_pos = next((i for i in range(pos - 1, -1, -1) if same_app[i]["kind"] == "snapshot_normalized"), None)
    else:  # snapshot_raw
        n_pos = next((i for i in range(pos + 1, len(same_app)) if same_app[i]["kind"] == "snapshot_normalized"), None)

    normalized = same_app[n_pos] if n_pos is not None else None
    raw = ai = None
    if n_pos is not None:
        if n_pos > 0 and same_app[n_pos - 1]["kind"] == "snapshot_raw":
            raw = same_app[n_pos - 1]
        if n_pos + 1 < len(same_app) and same_app[n_pos + 1]["kind"] == "snapshot_ai_view":
            ai = same_app[n_pos + 1]
    if anchor["kind"] == "snapshot_ai_view":
        ai = anchor
    if anchor["kind"] == "snapshot_raw":
        raw = anchor

    raw_full = _full_payload(traces, sid, raw)
    norm_full = _full_payload(traces, sid, normalized)
    return {
        "app_id": anchor["app_id"],
        "event_ids": {"raw": raw and raw["id"], "normalized": normalized and normalized["id"], "ai_view": ai and ai["id"]},
        "raw": raw_full["raw"] if raw_full else None,
        "normalized": norm_full["tree"] if norm_full else None,
        "refs": norm_full.get("refs") if norm_full else None,
        "counts": norm_full.get("counts") if norm_full else None,
        "changed": norm_full.get("changed") if norm_full else None,
        "ai_view": ai["payload"] if ai else None,
    }


def action_group(traces: Path, sid: str, event_id: str) -> dict[str, Any]:
    """One action from request to result: resolution, native call, diff, plus the enclosing MCP call."""
    events = read_events(traces, sid)
    start = _index(events, event_id)
    request = events[start]
    if request["kind"] != "action_request":
        raise NotFound(f"{event_id} is a {request['kind']} event, not an action_request")

    chain: list[dict[str, Any]] = []
    for e in events[start + 1:]:
        if e["kind"] in ("action_request", "mcp_request"):
            break
        chain.append(e)
        if e["kind"] in ("action_result", "mcp_response"):
            break

    def first(kind: str) -> dict[str, Any] | None:
        return next((e for e in chain if e["kind"] == kind), None)

    native_pos = next((i for i, e in enumerate(chain) if e["kind"] == "action_native"), len(chain))
    snapshots = [(i, e) for i, e in enumerate(chain) if e["kind"] == "snapshot_normalized"]
    pre = next((e["id"] for i, e in snapshots if i < native_pos), None)
    post = next((e["id"] for i, e in snapshots if i > native_pos), None)

    mcp_req = next((e for e in reversed(events[:start]) if e["kind"] in ("mcp_request", "mcp_response")), None)
    if mcp_req is not None and mcp_req["kind"] != "mcp_request":
        mcp_req = None
    mcp_resp = None
    if mcp_req is not None:
        mcp_resp = next((e for e in events[start:] if e["kind"] == "mcp_response"), None)
    return {
        "request": request,
        "resolution": first("action_resolution"),
        "native": first("action_native"),
        "result": first("action_result"),
        "diff": first("post_action_diff"),
        "errors": [e for e in chain if e["kind"] == "error"],
        "pre_snapshot_id": pre,
        "post_snapshot_id": post,
        "mcp": {"request": mcp_req, "response": mcp_resp} if mcp_req else None,
    }
