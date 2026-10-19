import json
import re

from conftest import read_events, snap
from uibridge.adapters.fake import FakeAdapter
from uibridge.core.serializer import render
from uibridge.core.tracer import SIDECAR_BYTES, TraceEvent, Tracer


def test_session_folder_and_event_shape(tracer):
    assert re.fullmatch(r"\d{8}-\d{6}_test", tracer.session)
    tracer.emit("detect", {"signals": ["#32770"], "chosen": "win32"}, app_id="a1")
    tracer.emit("action_request", {"ref": "e3", "action": "click"}, app_id="a1")
    events = read_events(tracer)
    for raw in events:
        assert set(raw) == {"id", "ts", "session", "kind", "app_id", "payload"}
        TraceEvent.model_validate(raw)
    assert [e["id"] for e in events] == ["ev00001", "ev00002"]
    assert all(e["session"] == tracer.session for e in events)


def test_second_session_in_same_second_gets_suffix(tmp_path):
    a, b = Tracer(tmp_path, "same"), Tracer(tmp_path, "same")
    assert a.session_dir != b.session_dir
    a.close()
    b.close()


def test_raw_snapshots_and_large_payloads_go_to_sidecars(tracer):
    raw_id = tracer.emit("snapshot_raw", {"tree": {"role": "Window"}})
    big_id = tracer.emit("snapshot_normalized", {"counts": {"new": 1}, "tree": "x" * (SIDECAR_BYTES + 1)})
    small_id = tracer.emit("snapshot_normalized", {"tree": "small"})
    events = {e["id"]: e for e in read_events(tracer)}

    assert events[raw_id]["payload"]["sidecar"] == f"raw_{raw_id}.json"
    sidecar = json.loads((tracer.session_dir / f"raw_{raw_id}.json").read_text(encoding="utf-8"))
    assert sidecar == {"tree": {"role": "Window"}}

    big = events[big_id]["payload"]
    assert big["sidecar"] == f"raw_{big_id}.json" and "tree" not in big
    assert big["counts"] == {"new": 1}  # small fields stay inline
    assert events[small_id]["payload"] == {"tree": "small"}


def test_error_event_has_message_and_stack(tracer):
    try:
        raise ValueError("boom")
    except ValueError as exc:
        tracer.error(exc, {"ref": "e4"})
    payload = read_events(tracer)[-1]["payload"]
    assert payload["message"] == "ValueError: boom"
    assert "Traceback" in payload["stack"] and payload["context"] == {"ref": "e4"}


def test_core_reads_and_resolutions_are_traced(registry, tracer):
    adapter = FakeAdapter.load("win32_dialog")
    tree = snap(adapter, registry)
    render(tree, tracer, app_id="app1")
    registry.resolve("e8", adapter)
    kinds = [e["kind"] for e in read_events(tracer)]
    assert kinds == ["snapshot_normalized", "snapshot_ai_view", "action_resolution"]
