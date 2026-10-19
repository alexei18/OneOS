"""ComposedAdapter + router: base frame (FakeAdapter) + inner content (fake provider), as for CDP."""

import pytest

from conftest import read_events
from uibridge.adapters.fake import FakeAdapter
from uibridge.core.adapter import ContentUnavailable, LocatorNotFound
from uibridge.core.compose import ComposedAdapter
from uibridge.core.model import Action, ActionKind, ActionResult, Node, NodeMeta, Role, State
from uibridge.core.router import Router

FRAME = {"app": {"title": "Code - Probe", "process": "Code.exe", "pid": 900, "framework": "electron",
                 "class_name": "Chrome_WidgetWin_1"},
         "root": {"role": "Window", "name": "Code - Probe", "class_name": "Chrome_WidgetWin_1", "children": [
             {"role": "Button", "name": "Minimize", "automation_id": "Minimize"},
             {"role": "Pane", "class_name": "Chrome_RenderWidgetHostHWND", "children": [
                 {"role": "Document", "name": "UIA copy of the web content"}]}]}}


class FakeContent:
    name = "cdp"
    host_classes = {"Chrome_RenderWidgetHostHWND"}

    def __init__(self, available=True):
        self.available = available
        self.heading = "Welcome"
        self.acted = []
        self.detached = False

    def owns(self, locator):
        return bool(locator) and locator.startswith("cdp:")

    def attach(self, target):
        if not self.available:
            raise ContentUnavailable("electron app started without CDP; use launch(..., with_cdp=true)")

    def _tree(self):
        def n(loc, role, name, **kw):
            return Node(role=role, name=name, meta=NodeMeta(adapter="cdp", locator=loc), **kw)
        return n("cdp:page", Role.DOCUMENT, "Probe", children=[
            n("cdp:e3", Role.HEADING, self.heading),
            n("cdp:e9", Role.BUTTON, "Run", actions=[ActionKind.CLICK], states={State.ENABLED})])

    def snapshot(self, root, depth):
        tree = self._tree()
        if root is None:
            return tree
        return next(x for x in tree.walk() if x.meta.locator == root.locator)

    def read(self, ref):
        node = next((x for x in self._tree().walk() if x.meta.locator == ref.locator), None)
        if node is None:
            raise LocatorNotFound(ref.locator)
        node.children = []
        return node

    def act(self, ref, action, dry_run=False):
        self.acted.append((ref.locator, action.kind, dry_run))
        if not dry_run:
            self.heading = "Clicked"
        return ActionResult(ok=True, strategy="pattern", native_call=f"Playwright get_by_ref({ref.locator[4:]!r}).click()")

    def raw_dump(self):
        return {"yaml": "- heading \"Welcome\" [ref=e3]"}

    def detach(self):
        self.detached = True


def router_with(tracer, tmp_path, content):
    frame = FakeAdapter(FRAME)
    frame.opaque_classes = set()
    target = frame.target.model_copy(update={"electron_resources": "C:/x/resources/app.asar"})
    r = Router(tracer, {"base_uia": lambda t: frame}, tmp_path / "refs", settle_s=0, contents={"cdp": lambda t: content})
    r.attach(target)
    return r, target.app_id


def test_content_replaces_the_host_subtree(tracer, tmp_path):
    content = FakeContent()
    router, app_id = router_with(tracer, tmp_path, content)
    app = router.apps[app_id]
    assert isinstance(app.adapter, ComposedAdapter) and app.detection.chosen == "cdp"
    assert "base_uia frame + cdp content" in app.detection.why
    result = router.snapshot(app_id)
    host = next(n for n in result.tree.walk() if n.meta.class_name == "Chrome_RenderWidgetHostHWND")
    assert [c.meta.adapter for c in host.walk()] == ["fake", "cdp", "cdp", "cdp"]  # UIA copy is gone
    assert "UIA copy" not in result.view.text and '- heading "Welcome"' in result.view.text
    assert result.raw["cdp"] == {"yaml": "- heading \"Welcome\" [ref=e3]"} and "tree" in result.raw


def test_refs_stable_and_actions_dispatched_to_content(tracer, tmp_path):
    content = FakeContent()
    router, app_id = router_with(tracer, tmp_path, content)
    tree = router.snapshot(app_id).tree
    run = next(n.ref for n in tree.walk() if n.name == "Run")
    heading = next(n.ref for n in tree.walk() if n.role == Role.HEADING)
    result = router.act(app_id, run, Action(kind=ActionKind.CLICK))
    assert result.ok and content.acted == [("cdp:e9", ActionKind.CLICK, False)]
    # a renamed node without an automation id is a different node (Playwright re-refs it too: e3 -> e22)
    assert heading in router.last_diff.gone and router.last_diff.new
    assert router.snapshot(app_id).tree.children[0].ref == tree.children[0].ref  # frame refs stable too


def test_frame_actions_go_to_the_base(tracer, tmp_path):
    content = FakeContent()
    router, app_id = router_with(tracer, tmp_path, content)
    minimize = next(n.ref for n in router.snapshot(app_id).tree.walk() if n.name == "Minimize")
    assert router.act(app_id, minimize, Action(kind=ActionKind.CLICK)).native_call.startswith("FakeAdapter.click")
    assert content.acted == []


def test_expand_inside_content(tracer, tmp_path):
    content = FakeContent()
    router, app_id = router_with(tracer, tmp_path, content)
    page = next(n.ref for n in router.snapshot(app_id).tree.walk() if n.meta.locator == "cdp:page")
    sub = router.snapshot(app_id, ref=page, depth=2)
    assert sub.view.text.splitlines()[0].startswith(f'- document "Probe" [{page}]')


def test_without_cdp_falls_back_and_says_so(tracer, tmp_path):
    router, app_id = router_with(tracer, tmp_path, FakeContent(available=False))
    app = router.apps[app_id]
    assert app.detection.chosen == "base_uia" and "cdp unavailable" in app.detection.why
    header = router.snapshot(app_id).view.text.splitlines()[0]
    assert "electron app started without CDP; use launch(..., with_cdp=true)" in header
    detects = [e for e in read_events(tracer) if e["kind"] == "detect"]
    assert len(detects) == 1 and detects[0]["payload"]["chosen"] == "base_uia"


def test_close_detaches_content(tracer, tmp_path):
    content = FakeContent()
    router, _ = router_with(tracer, tmp_path, content)
    router.close()
    assert content.detached and router.apps == {}


def test_launch_with_cdp_requires_a_launcher(tracer, tmp_path):
    router, _ = router_with(tracer, tmp_path, FakeContent())
    with pytest.raises(RuntimeError, match="CDP launcher"):
        router.launch("x.exe", [], lambda: [], with_cdp=True)


def test_fallback_leaves_the_base_walking_everything(tracer, tmp_path):
    frame = FakeAdapter(FRAME)
    frame.opaque_classes = set()
    target = frame.target.model_copy(update={"electron_resources": "C:/x/resources/app.asar"})
    r = Router(tracer, {"base_uia": lambda t: frame}, tmp_path / "refs", settle_s=0,
               contents={"cdp": lambda t: FakeContent(available=False)})
    r.attach(target)
    assert frame.opaque_classes == set()  # Docker Desktop bug: the render widget must stay walkable
    assert "UIA copy of the web content" in r.snapshot(target.app_id).view.text
