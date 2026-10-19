"""FakeAdapter: serves a JSON fixture tree through the Adapter protocol, for tests.

Fixture format::

    {"app": {"title", "process", "pid", "framework", "class_name"},
     "root": {"role": <UIA ControlType name>, "name", "value", "states": [...],
              "automation_id", "class_name", "children": [...]}}

Locators are child-index paths from the root ("/" is the root, "/0/2" its first child's
third child), like a UIA tree path: inserting a sibling makes them stale.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from uibridge.core.adapter import LocatorNotFound, WindowTarget
from uibridge.core.model import Action, ActionKind, ActionResult, Node, NodeMeta, Ref, Role, State

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Native (UIA ControlType name) -> Role. Unmapped names become generic.
FAKE_ROLE_MAP: dict[str, Role] = {
    "Window": Role.WINDOW,
    "Dialog": Role.DIALOG,
    "Pane": Role.PANE,
    "Group": Role.GROUP,
    "Custom": Role.GENERIC,
    "Button": Role.BUTTON,
    "SplitButton": Role.BUTTON,
    "Hyperlink": Role.LINK,
    "Edit": Role.TEXTBOX,
    "Text": Role.TEXT,
    "CheckBox": Role.CHECKBOX,
    "RadioButton": Role.RADIO,
    "ComboBox": Role.COMBOBOX,
    "List": Role.LIST,
    "ListItem": Role.LISTITEM,
    "Tree": Role.TREE,
    "TreeItem": Role.TREEITEM,
    "Menu": Role.MENU,
    "MenuBar": Role.MENUBAR,
    "MenuItem": Role.MENUITEM,
    "ToolBar": Role.TOOLBAR,
    "StatusBar": Role.STATUSBAR,
    "Tab": Role.TABLIST,
    "TabItem": Role.TAB,
    "Table": Role.TABLE,
    "DataGrid": Role.GRID,
    "DataItem": Role.ROW,
    "Header": Role.GROUP,
    "HeaderItem": Role.COLUMNHEADER,
    "Image": Role.IMAGE,
    "Document": Role.DOCUMENT,
    "Slider": Role.SLIDER,
    "Spinner": Role.SPINBUTTON,
    "ProgressBar": Role.PROGRESSBAR,
    "ScrollBar": Role.SCROLLBAR,
    "Separator": Role.SEPARATOR,
}

A = ActionKind
ROLE_ACTIONS: dict[Role, list[ActionKind]] = {
    Role.WINDOW: [A.FOCUS],
    Role.DIALOG: [A.FOCUS],
    Role.BUTTON: [A.CLICK, A.FOCUS],
    Role.LINK: [A.CLICK],
    Role.MENUITEM: [A.CLICK],
    Role.TEXTBOX: [A.FOCUS, A.SET_VALUE, A.TYPE_TEXT],
    Role.COMBOBOX: [A.FOCUS, A.SET_VALUE, A.TYPE_TEXT, A.EXPAND, A.COLLAPSE],
    Role.CHECKBOX: [A.TOGGLE, A.CHECK, A.UNCHECK],
    Role.SWITCH: [A.TOGGLE, A.CHECK, A.UNCHECK],
    Role.RADIO: [A.SELECT],
    Role.LISTITEM: [A.SELECT],
    Role.OPTION: [A.SELECT],
    Role.TAB: [A.SELECT],
    Role.ROW: [A.SELECT],
    Role.TREEITEM: [A.SELECT, A.EXPAND, A.COLLAPSE],
    Role.SLIDER: [A.SET_VALUE],
    Role.SPINBUTTON: [A.SET_VALUE],
}


def map_role(native: str) -> Role:
    return FAKE_ROLE_MAP.get(native, Role.GENERIC)


def _child_loc(loc: str, i: int) -> str:
    return f"{loc.rstrip('/')}/{i}"


def _state_hash(d: dict[str, Any]) -> str:
    blob = json.dumps([d.get("name"), d.get("value"), sorted(d.get("states", []))])
    return hashlib.sha1(blob.encode()).hexdigest()[:8]


class FakeAdapter:
    name = "fake"

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = copy.deepcopy(fixture)
        self.app = self.fixture["app"]
        self.attached: WindowTarget | None = None

    @classmethod
    def load(cls, fixture_name: str) -> FakeAdapter:
        path = FIXTURES_DIR / f"{fixture_name}.json"
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @property
    def target(self) -> WindowTarget:
        return WindowTarget(
            app_id=f"fake-{self.app['pid']}",
            pid=self.app["pid"],
            title=self.app["title"],
            process_name=self.app["process"],
            class_name=self.app.get("class_name"),
        )

    # -- protocol ---------------------------------------------------------------------------

    def can_handle(self, target: WindowTarget) -> int:
        return 100 if target.pid == self.app["pid"] else 0

    def attach(self, target: WindowTarget) -> None:
        self.attached = target

    def detach(self) -> None:
        self.attached = None

    def snapshot(self, root: Ref | None, depth: int) -> Node:
        loc = root.locator if root and root.locator else "/"
        return self._build(self._locate(loc), loc, depth)

    def read(self, ref: Ref) -> Node:
        loc = ref.locator or "/"
        return self._build(self._locate(loc), loc, 0)

    def raw_dump(self, root: Ref | None, depth: int) -> dict:
        loc = root.locator if root and root.locator else "/"
        return {"locator": loc, "tree": self._cut(self._locate(loc), depth)}

    def act(self, ref: Ref, action: Action, dry_run: bool = False) -> ActionResult:
        start = time.perf_counter()
        loc = ref.locator or "/"
        call = f"FakeAdapter.{action.kind}(locator={loc!r}"
        call += f", value={action.value!r})" if action.value is not None else ")"

        def fail(msg: str) -> ActionResult:
            return ActionResult(ok=False, strategy="none", native_call=call, error=msg,
                                duration_ms=(time.perf_counter() - start) * 1000)

        try:
            d = self._locate(loc)
        except LocatorNotFound as e:
            return fail(str(e))
        role = map_role(d["role"])
        supported = ROLE_ACTIONS.get(role, [])
        if action.kind not in supported:
            names = ", ".join(supported) or "none"
            return fail(f"{action.kind} not supported by {role} (supported: {names})")
        states: list[str] = d.setdefault("states", [])
        if State.DISABLED in states:
            return fail(f"{role} {d.get('name')!r} is disabled")
        if dry_run:
            return ActionResult(ok=True, strategy="pattern", native_call=f"DRY-RUN: {call}",
                                duration_ms=(time.perf_counter() - start) * 1000)

        before = _state_hash(d)
        self._apply(d, loc, action)
        return ActionResult(
            ok=True,
            strategy="pattern",
            native_call=call,
            duration_ms=(time.perf_counter() - start) * 1000,
            before_fingerprint=before,
            after_fingerprint=_state_hash(d),
        )

    # -- internals --------------------------------------------------------------------------

    def _locate(self, loc: str) -> dict[str, Any]:
        d = self.fixture["root"]
        for part in [p for p in loc.split("/") if p]:
            children = d.get("children", [])
            i = int(part)
            if i >= len(children):
                raise LocatorNotFound(f"no element at locator {loc!r}")
            d = children[i]
        return d

    def _build(self, d: dict[str, Any], loc: str, depth: int) -> Node:
        states = {State(s) for s in d.get("states", [])}
        if State.DISABLED not in states:
            states.add(State.ENABLED)
        role = map_role(d["role"])
        raw_children = d.get("children", [])
        children = (
            [self._build(c, _child_loc(loc, i), depth - 1) for i, c in enumerate(raw_children)]
            if depth > 0
            else []
        )
        return Node(
            role=role,
            name=d.get("name"),
            value=d.get("value"),
            description=d.get("description"),
            states=states,
            actions=ROLE_ACTIONS.get(role, []),
            children=children,
            child_count=len(raw_children),
            meta=NodeMeta(
                adapter=self.name,
                framework=self.app.get("framework"),
                native_role=d["role"],
                automation_id=d.get("automation_id"),
                class_name=d.get("class_name"),
                process=self.app.get("process"),
                locator=loc,
            ),
        )

    def _cut(self, d: dict[str, Any], depth: int) -> dict[str, Any]:
        out = {k: v for k, v in d.items() if k != "children"}
        if depth > 0:
            out["children"] = [self._cut(c, depth - 1) for c in d.get("children", [])]
        return out

    def _apply(self, d: dict[str, Any], loc: str, action: Action) -> None:
        states: list[str] = d["states"]

        def swap(remove: set[str], add: str) -> None:
            states[:] = [s for s in states if s not in remove] + [add]

        k = action.kind
        if k == A.TOGGLE:
            k = A.UNCHECK if State.CHECKED in states else A.CHECK
        if k == A.CHECK:
            swap({State.CHECKED, State.UNCHECKED, State.MIXED}, State.CHECKED)
        elif k == A.UNCHECK:
            swap({State.CHECKED, State.UNCHECKED, State.MIXED}, State.UNCHECKED)
        elif k == A.EXPAND:
            swap({State.EXPANDED, State.COLLAPSED}, State.EXPANDED)
        elif k == A.COLLAPSE:
            swap({State.EXPANDED, State.COLLAPSED}, State.COLLAPSED)
        elif k == A.SET_VALUE:
            d["value"] = action.value
        elif k == A.TYPE_TEXT:
            d["value"] = (d.get("value") or "") + (action.value or "")
        elif k == A.SELECT:
            parent = self._locate(loc.rsplit("/", 1)[0] or "/")
            for sib in parent.get("children", []):
                sib["states"] = [s for s in sib.get("states", []) if s != State.SELECTED]
            d["states"].append(State.SELECTED)
        elif k == A.FOCUS:
            stack = [self.fixture["root"]]
            while stack:
                n = stack.pop()
                n["states"] = [s for s in n.get("states", []) if s != State.FOCUSED]
                stack.extend(n.get("children", []))
            d["states"].append(State.FOCUSED)
        # click: no observable state change in a fake
