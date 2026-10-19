import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uibridge.adapters.fake import FakeAdapter
from uibridge.core.model import Action, ActionKind
from uibridge.core.router import Router
from uibridge.core.tracer import Tracer
from uibridge.viewer import traces as tr
from uibridge.viewer.app import create_app

REAL_MCP_TRACE = Path(__file__).resolve().parents[1] / "traces" / "20261019-153419_mcp"


@pytest.fixture
def session(tmp_path):
    """One session written by the real Router: attach, snapshot, set_value, failed click on a disabled button."""
    traces = tmp_path / "traces"
    fake = FakeAdapter.load("win32_dialog")
    tracer = Tracer(traces, "run")
    router = Router(tracer, {"base_uia": lambda t: fake}, traces / "_refs", settle_s=0)
    router.attach(fake.target)
    tree = router.snapshot(fake.target.app_id).tree
    combo = next(n.ref for n in tree.walk() if n.role == "combobox")
    ok_button = next(n.ref for n in tree.walk() if n.name == "OK")
    router.act(fake.target.app_id, combo, Action(kind=ActionKind.SET_VALUE, value="notepad"))
    router.act(fake.target.app_id, ok_button, Action(kind=ActionKind.CLICK))
    tracer.close()
    return traces, tracer.session, combo


@pytest.fixture
def client(session):
    return TestClient(create_app(session[0]))


def events_of(client, sid, **params):
    return client.get(f"/api/sessions/{sid}/events", params=params).json()


def test_session_list_summary(client, session):
    _, sid, _ = session
    rows = client.get("/api/sessions").json()
    assert [r["id"] for r in rows] == [sid]  # _refs is not a session
    row = rows[0]
    assert (row["framework"], row["adapter"], row["actions"], row["errors"]) == ("win32", "base_uia", 2, 1)
    assert row["strategies"] == {"pattern": 1, "none": 1} and row["live"] is True and row["mcp"] is False


def test_events_after_returns_only_newer(client, session):
    _, sid, _ = session
    all_events = events_of(client, sid)
    later = events_of(client, sid, after=all_events[4]["id"])
    assert [e["id"] for e in later] == [e["id"] for e in all_events[5:]]


def test_snapshot_group_links_all_three_layers(client, session):
    _, sid, _ = session
    ai_event = next(e for e in events_of(client, sid) if e["kind"] == "snapshot_ai_view")
    group = client.get(f"/api/sessions/{sid}/snapshot/{ai_event['id']}").json()
    assert all(group["event_ids"].values())
    normalized_refs = {}

    def walk(node):
        normalized_refs[node["ref"]] = node["meta"]["locator"]
        for c in node["children"]:
            walk(c)

    walk(group["normalized"])
    view_refs = set(re.findall(r"\[(e\d+)\]", group["ai_view"]["text"]))
    assert view_refs <= set(normalized_refs)

    raw_locators = set()

    def walk_raw(node, loc):  # fake raw nodes carry no locator: derive it like the UI does
        loc = node.get("locator", loc)
        raw_locators.add(loc)
        for i, c in enumerate(node.get("children", [])):
            walk_raw(c, f"{loc.rstrip('/')}/{i}")

    walk_raw(group["raw"]["tree"], group["raw"]["locator"])
    assert set(normalized_refs.values()) <= raw_locators
    # any layer works as the anchor
    norm_anchor = client.get(f"/api/sessions/{sid}/snapshot/{group['event_ids']['normalized']}").json()
    assert norm_anchor["event_ids"] == group["event_ids"]


def test_action_group_has_every_step(client, session):
    _, sid, combo = session
    request = next(e for e in events_of(client, sid) if e["kind"] == "action_request")
    group = client.get(f"/api/sessions/{sid}/action/{request['id']}").json()
    assert group["request"]["payload"]["ref"] == combo
    assert group["resolution"]["payload"]["via"] == "locator"
    assert group["native"]["payload"]["strategy"] == "pattern"
    assert group["diff"]["payload"]["changed"] == {combo: {"value": ["", "notepad"]}}
    assert group["result"]["payload"]["ok"] is True
    assert group["pre_snapshot_id"] < group["native"]["id"] < group["post_snapshot_id"]
    assert group["mcp"] is None


def test_failed_action_group(client, session):
    _, sid, _ = session
    request = [e for e in events_of(client, sid) if e["kind"] == "action_request"][1]
    group = client.get(f"/api/sessions/{sid}/action/{request['id']}").json()
    assert group["result"]["payload"]["ok"] is False and "disabled" in group["result"]["payload"]["error"]
    assert group["diff"] is None and group["post_snapshot_id"] is None


@pytest.mark.parametrize("url", [
    "/api/sessions/_refs/events",
    "/api/sessions/nope/events",
    "/api/sessions/..%2F..%2Fsrc/events",
])
def test_bad_session_ids_are_404(client, url):
    assert client.get(url).status_code == 404


def test_sidecar_names_are_validated(client, session):
    _, sid, _ = session
    raw = next(e for e in events_of(client, sid) if e["kind"] == "snapshot_raw")
    assert client.get(f"/api/sessions/{sid}/sidecar/{raw['payload']['sidecar']}").status_code == 200
    for name in ["events.jsonl", "..%2F..%2Fpyproject.toml", "raw_ev1.json"]:
        assert client.get(f"/api/sessions/{sid}/sidecar/{name}").status_code == 404


def test_index_and_static_are_served(client):
    assert "uibridge" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


@pytest.mark.skipif(not REAL_MCP_TRACE.exists(), reason="local Phase 3 trace not present")
def test_real_phase3_trace_parses():
    traces, sid = REAL_MCP_TRACE.parent, REAL_MCP_TRACE.name
    events = tr.read_events(traces, sid)
    for request in (e for e in events if e["kind"] == "action_request"):
        group = tr.action_group(traces, sid, request["id"])
        assert group["mcp"]["request"]["payload"]["tool"] in ("act", "launch")
        assert group["mcp"]["response"] is not None
    for view in (e for e in events if e["kind"] == "snapshot_ai_view"):
        assert tr.snapshot_group(traces, sid, view["id"])["normalized"] is not None
