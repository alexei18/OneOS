import pytest

from conftest import read_events
from uibridge.adapters.fake import FakeAdapter
from uibridge.core.model import Action, ActionKind
from uibridge.core.refs import StaleRefError
from uibridge.core.router import Router
from uibridge.core.tracer import Tracer


def make_router(tracer, fake, state_dir) -> Router:
    return Router(tracer, {"base_uia": lambda target: fake}, state_dir, settle_s=0)


@pytest.fixture
def fake():
    return FakeAdapter.load("win32_dialog")


@pytest.fixture
def router(tracer, fake, tmp_path):
    r = make_router(tracer, fake, tmp_path / "refs")
    r.attach(fake.target)
    return r


def ref_named(router, app_id, name):
    return next(n.ref for n in router.snapshot(app_id).tree.walk() if n.name == name)


def test_attach_emits_detect_with_signals(router, tracer, fake):
    event = read_events(tracer)[0]
    assert event["kind"] == "detect"
    p = event["payload"]
    assert (p["framework"], p["preferred"], p["chosen"]) == ("win32", "win32", "base_uia")
    assert "not available" in p["why"] and p["target"]["class_name"] == "#32770"


def test_snapshot_emits_three_layers(router, tracer, fake):
    result = router.snapshot(fake.target.app_id)
    assert result.view.text.startswith('app "Run" [win32] pid=4120')
    kinds = [e["kind"] for e in read_events(tracer)][1:]
    assert kinds == ["snapshot_raw", "snapshot_normalized", "snapshot_ai_view"]
    raw_event = read_events(tracer)[1]
    assert (tracer.session_dir / raw_event["payload"]["sidecar"]).exists()
    assert all(n.meta.framework == "win32" for n in result.tree.walk())


def test_act_emits_full_chain_and_diff(router, tracer, fake):
    app_id = fake.target.app_id
    combo = next(n.ref for n in router.snapshot(app_id).tree.walk() if n.role == "combobox")
    result = router.act(app_id, combo, Action(kind=ActionKind.SET_VALUE, value="notepad"))
    assert result.ok and result.changed_refs == [combo]
    assert router.last_diff.changed == {combo: {"value": ["", "notepad"]}}
    kinds = [e["kind"] for e in read_events(tracer)]
    tail = kinds[kinds.index("action_request"):]
    assert tail == ["action_request", "snapshot_normalized", "action_resolution", "action_native",
                    "snapshot_normalized", "post_action_diff", "action_result"]


def test_dry_run_resolves_but_does_not_execute(router, tracer, fake):
    app_id = fake.target.app_id
    combo = next(n.ref for n in router.snapshot(app_id).tree.walk() if n.role == "combobox")
    result = router.act(app_id, combo, Action(kind=ActionKind.SET_VALUE, value="notepad"), dry_run=True)
    assert result.ok and result.native_call.startswith("DRY-RUN: ")
    assert router.read(app_id, combo).value == ""
    native = [e for e in read_events(tracer) if e["kind"] == "action_native"][-1]
    assert native["payload"]["dry_run"] is True and "post_action_diff" not in [e["kind"] for e in read_events(tracer)]


def test_refs_persist_across_router_instances(router, fake, tmp_path):
    app_id = fake.target.app_id
    cancel = ref_named(router, app_id, "Cancel")
    second = make_router(Tracer(tmp_path / "t2", "second"), fake, tmp_path / "refs")  # like a new CLI process
    second.attach(fake.target)
    assert second.read(app_id, cancel).name == "Cancel"
    assert second.act(app_id, cancel, Action(kind=ActionKind.CLICK)).ok


def test_refs_from_another_process_are_discarded(router, fake, tmp_path):
    cancel = ref_named(router, fake.target.app_id, "Cancel")
    restarted = FakeAdapter.load("win32_dialog")
    restarted.app["pid"] = 9999
    second = make_router(Tracer(tmp_path / "t2", "second"), restarted, tmp_path / "refs")
    second.attach(restarted.target.model_copy(update={"app_id": fake.target.app_id}))
    with pytest.raises(Exception, match="unknown ref"):
        second.read(fake.target.app_id, cancel)


def test_resolution_errors_are_traced_and_raised(router, tracer, fake):
    app_id = fake.target.app_id
    cancel = ref_named(router, app_id, "Cancel")
    children = fake.fixture["root"]["children"]
    children.remove(next(c for c in children if c.get("name") == "Cancel"))
    with pytest.raises(StaleRefError):
        router.act(app_id, cancel, Action(kind=ActionKind.CLICK))
    error = [e for e in read_events(tracer) if e["kind"] == "error"][-1]
    assert error["payload"]["context"]["op"] == "act" and "StaleRefError" in error["payload"]["message"]


def test_app_of_finds_the_refs_app(router, fake):
    ref = ref_named(router, fake.target.app_id, "Cancel")
    assert router.app_of(ref) == fake.target.app_id


def test_wait_for_returns_all_matches(router, fake):
    app_id = fake.target.app_id
    assert [n.name for n in router.wait_for(app_id, role="button", name="cancel")] == ["Cancel"]
    assert len(router.wait_for(app_id, role="button")) == 4  # OK, Cancel, Browse..., Close
    assert router.wait_for(app_id, name="hidden")[0].name == "Hidden hint"


def test_wait_for_times_out_with_seen_names(router, fake, tracer):
    with pytest.raises(TimeoutError, match="'Cancel'"):
        router.wait_for(fake.target.app_id, role="button", name="Apply", timeout_s=0, poll_s=0)
    assert read_events(tracer)[-1]["kind"] == "error"


def test_minimized_window_is_called_out_in_the_header(router, fake, monkeypatch):
    raw = fake.raw_dump
    monkeypatch.setattr(fake, "raw_dump", lambda root, depth: {**raw(root, depth), "window_state": {"minimized": True}})
    fake.fixture["root"]["children"] = []
    header = router.snapshot(fake.target.app_id).view.text.splitlines()[0]
    assert header.startswith('app "Run" [win32] pid=4120 minimized (this app exposes no content while minimized')


# -- composition: base adapter + enricher (win32-style) -----------------------------------------

from uibridge.core.adapter import Declined  # noqa: E402
from uibridge.core.model import ActionResult  # noqa: E402


class FakeEnricher:
    name = "win32"

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def attach(self, target):
        pass

    def enrich(self, tree):
        for node in tree.walk():
            if node.meta.native_role == "TitleBar" and not node.name:
                node.name = "Run title bar"
                node.meta.sources["name"] = self.name

    def raw_dump(self):
        return {"hwnds": 13}

    def act(self, tree, ref, action, dry_run=False):
        self.calls.append((ref, action.kind, dry_run))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def composed(tracer, fake, tmp_path, outcome):
    enricher = FakeEnricher(outcome)
    r = Router(tracer, {"base_uia": lambda t: fake}, tmp_path / "refs", settle_s=0,
               enrichers={"win32": lambda t: enricher})
    r.attach(fake.target)
    return r, enricher


def combo_ref(router, fake):
    return next(n.ref for n in router.snapshot(fake.target.app_id).tree.walk() if n.role == "combobox")


def test_enricher_is_chosen_and_enriches_the_tree(tracer, fake, tmp_path):
    router, _ = composed(tracer, fake, tmp_path, Declined("n/a"))
    attached = router.apps[fake.target.app_id]
    assert attached.detection.chosen == "win32" and "base_uia tree + win32 enrichment" in attached.detection.why
    result = router.snapshot(fake.target.app_id)
    title = next(n for n in result.tree.walk() if n.name == "Run title bar")
    assert title.meta.sources == {"name": "win32"} and title.meta.adapter == "fake"
    assert result.raw["win32"] == {"hwnds": 13}


def test_native_message_runs_first(tracer, fake, tmp_path):
    ok = ActionResult(ok=True, strategy="native_message", native_call="SendMessageTimeout(0x1, WM_SETTEXT, 'x')")
    router, enricher = composed(tracer, fake, tmp_path, ok)
    combo = combo_ref(router, fake)
    result = router.act(fake.target.app_id, combo, Action(kind=ActionKind.SET_VALUE, value="x"))
    assert result.strategy == "native_message" and enricher.calls == [(combo, ActionKind.SET_VALUE, False)]
    native = [e for e in read_events(tracer) if e["kind"] == "action_native"][-1]["payload"]
    assert native["fallbacks"] == [] and native["native_call"].startswith("SendMessageTimeout")


def test_declined_falls_back_to_pattern_and_says_why(tracer, fake, tmp_path):
    router, _ = composed(tracer, fake, tmp_path, Declined("no HWND for this element"))
    combo = combo_ref(router, fake)
    result = router.act(fake.target.app_id, combo, Action(kind=ActionKind.SET_VALUE, value="notepad"))
    assert result.ok and result.strategy == "pattern"
    assert result.native_call == "win32 declined: no HWND for this element; FakeAdapter.set_value(locator='/2/0', value='notepad')"
    native = [e for e in read_events(tracer) if e["kind"] == "action_native"][-1]["payload"]
    assert native["fallbacks"] == ["win32 declined: no HWND for this element"]


def test_failing_enricher_falls_back_and_is_traced(tracer, fake, tmp_path):
    router, _ = composed(tracer, fake, tmp_path, RuntimeError("SendMessageTimeout timed out"))
    combo = combo_ref(router, fake)
    result = router.act(fake.target.app_id, combo, Action(kind=ActionKind.SET_VALUE, value="v"))
    assert result.ok and result.native_call.startswith("win32 failed: RuntimeError: SendMessageTimeout timed out; ")
    assert any(e["kind"] == "error" and e["payload"]["context"].get("enricher") == "win32" for e in read_events(tracer))


def test_failed_message_result_falls_back(tracer, fake, tmp_path):
    bad = ActionResult(ok=False, strategy="native_message", native_call="PostMessage(BM_CLICK)", error="window gone")
    router, _ = composed(tracer, fake, tmp_path, bad)
    combo = combo_ref(router, fake)
    result = router.act(fake.target.app_id, combo, Action(kind=ActionKind.SET_VALUE, value="v"))
    assert result.strategy == "pattern" and "win32 failed: window gone (PostMessage(BM_CLICK))" in result.native_call


def test_dry_run_passes_through_the_enricher(tracer, fake, tmp_path):
    router, enricher = composed(tracer, fake, tmp_path, Declined("tree items need cross-process memory"))
    combo = combo_ref(router, fake)
    result = router.act(fake.target.app_id, combo, Action(kind=ActionKind.SET_VALUE, value="v"), dry_run=True)
    assert enricher.calls[-1][2] is True
    assert result.native_call.startswith("DRY-RUN: win32 declined: tree items need cross-process memory; FakeAdapter")
