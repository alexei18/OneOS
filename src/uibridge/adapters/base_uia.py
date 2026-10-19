"""base_uia: the universal UI Automation adapter, on xa11y (SPEC §7).

Locators are "hwnd:0x<top-level hwnd>" plus a child-index path ("hwnd:0x1b068e/2/0/5"), walked
with Element.children(). Action strategies: 1 = pattern (xa11y element methods), 3 = synthetic
input (xa11y.input_sim). Strategy 2 (native messages) comes with the win32 adapter.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

import win32con
import win32gui
import xa11y

from uibridge.adapters.uia_roles import native_role_name, uia_actions, uia_role, uia_states
from uibridge.core.adapter import LocatorNotFound, WindowTarget
from uibridge.core.model import Action, ActionKind, ActionResult, Node, NodeMeta, Rect, Ref, Strategy

A = ActionKind
_PROPS = ("role", "name", "value", "description", "enabled", "visible", "focused", "selected", "checked",
          "expanded", "editable", "modal", "actions", "stable_id")

# action -> (xa11y action name that must be in el.actions, method name, passes value)
_PATTERNS: dict[ActionKind, tuple[str, str, bool]] = {
    A.CLICK: ("press", "press", False),
    A.FOCUS: ("focus", "focus", False),
    A.SET_VALUE: ("set_value", "set_value", True),
    A.TYPE_TEXT: ("set_value", "type_text", True),
    A.TOGGLE: ("toggle", "toggle", False),
    A.CHECK: ("toggle", "toggle", False),
    A.UNCHECK: ("toggle", "toggle", False),
    A.EXPAND: ("expand", "expand", False),
    A.COLLAPSE: ("collapse", "collapse", False),
    A.SELECT: ("select", "select", False),
    A.SCROLL_INTO_VIEW: ("scroll_into_view", "scroll_into_view", False),
    A.RIGHT_CLICK: ("show_menu", "show_menu", False),
}
_MOUSE = {A.CLICK: ("left", 1), A.DOUBLE_CLICK: ("left", 2), A.RIGHT_CLICK: ("right", 1)}
_MODIFIERS = {"ctrl": "Ctrl", "control": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Meta", "meta": "Meta"}


class SyntheticInputBlocked(Exception):
    """Synthetic input would not land on the target window."""


def read_props(el: xa11y.Element) -> dict[str, Any]:
    """Every property we use, read once. Failures are recorded, never hidden."""
    props: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key in _PROPS:
        try:
            props[key] = getattr(el, key)
        except xa11y.XA11yError as e:
            props[key], errors[key] = None, f"{type(e).__name__}: {e}"
    try:
        b = el.bounds
        props["bounds"] = [b.x, b.y, b.width, b.height] if b else None
    except xa11y.XA11yError as e:
        props["bounds"], errors["bounds"] = None, f"{type(e).__name__}: {e}"
    try:
        props["raw"] = el.raw
    except xa11y.XA11yError as e:
        props["raw"], errors["raw"] = {}, f"{type(e).__name__}: {e}"
    props["actions"] = props["actions"] or []
    if errors:
        props["errors"] = errors
    return props


def parse_keys(keys: str) -> list[tuple[str, list[str]]]:
    """"Ctrl+Shift+S Enter" -> [("S", ["Ctrl", "Shift"]), ("Enter", [])]."""
    steps = []
    for combo in keys.split():
        *mods, key = combo.split("+")
        steps.append((key, [_MODIFIERS.get(m.lower(), m) for m in mods]))
    return steps


def _state_hash(props: dict[str, Any]) -> str:
    blob = json.dumps([props.get("name"), props.get("value"), props.get("checked"), props.get("expanded"),
                       props.get("selected"), props.get("focused")], default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:8]


class BaseUIAAdapter:
    name = "base_uia"

    def __init__(self) -> None:
        self.target: WindowTarget | None = None
        self._window: xa11y.Element | None = None
        self._sid = ""

    # -- protocol ---------------------------------------------------------------------------

    def can_handle(self, target: WindowTarget) -> int:
        return 50  # UIA reaches every window; specialised adapters score higher

    def attach(self, target: WindowTarget) -> None:
        if target.hwnd is None:
            raise LocatorNotFound(f"{target.app_id}: base_uia needs a window handle")
        self.target = target
        self._sid = f"hwnd:{target.hwnd:#x}"
        self._window = self._find_window()

    def detach(self) -> None:
        self.target, self._window = None, None

    def snapshot(self, root: Ref | None, depth: int) -> Node:
        loc = root.locator if root and root.locator else self._sid
        return self._node(self._locate(loc), loc, depth)

    def read(self, ref: Ref) -> Node:
        loc = ref.locator or self._sid
        return self._node(self._locate(loc), loc, 0)

    def raw_dump(self, root: Ref | None, depth: int) -> dict:
        loc = root.locator if root and root.locator else self._sid
        tree = self._raw(self._locate(loc), loc, depth)
        try:
            minimized = self._window.minimized
        except xa11y.XA11yError:
            minimized = None
        return {"adapter": self.name, "window": self._sid, "window_state": {"minimized": minimized}, "tree": tree}

    def act(self, ref: Ref, action: Action, dry_run: bool = False) -> ActionResult:
        start = time.perf_counter()

        def result(ok: bool, strategy: Strategy, call: str, error: str | None = None, **kw: Any) -> ActionResult:
            return ActionResult(ok=ok, strategy=strategy, native_call=call, error=error,
                                duration_ms=(time.perf_counter() - start) * 1000, **kw)

        try:
            el = self._locate(ref.locator or self._sid)
        except LocatorNotFound as e:
            return result(False, "none", f"locate({ref.locator!r})", str(e))
        props = read_props(el)
        role = uia_role(props["raw"].get("control_type_id"), props["role"] or "", bool(props["modal"]))

        if action.kind == A.INVOKE_MENU:
            return result(False, "none", "invoke_menu", "invoke_menu is not implemented in base_uia yet; "
                          "expand/click the menu items by ref instead")
        if not props["enabled"]:
            return result(False, "none", f"{action.kind} on {role} {props['name']!r}", "element is disabled")

        plans = self._plans(el, props, action)
        if not plans:
            return result(False, "none", f"{action.kind} on {role} {props['name']!r}",
                          f"no way to {action.kind} this {role}: patterns available: "
                          f"{', '.join(props['actions']) or 'none'}; no bounds for synthetic input")
        if dry_run:
            strategy, call, _ = plans[0]
            return result(True, strategy, f"DRY-RUN: {call}")

        before = _state_hash(props)
        notes: list[str] = []
        for strategy, call, run in plans:
            try:
                note = run()
            except xa11y.ActionNotSupportedError as e:
                notes.append(f"{call} -> ActionNotSupportedError: {e}")
                continue
            except (xa11y.XA11yError, SyntheticInputBlocked) as e:
                return result(False, strategy, "; ".join([*notes, call]), f"{type(e).__name__}: {e}")
            after = _state_hash(read_props(el))
            return result(True, strategy, "; ".join([*notes, *([note] if note else []), call]),
                          before_fingerprint=before, after_fingerprint=after)
        return result(False, "none", "; ".join(notes), "every strategy failed")

    # -- strategies -------------------------------------------------------------------------

    def _plans(self, el: xa11y.Element, props: dict[str, Any], action: Action) -> list[
            tuple[Strategy, str, Callable[[], None]]]:
        """Candidate executions in strategy order: pattern first, synthetic input last."""
        plans: list[tuple[Strategy, str, Callable[[], None]]] = []
        kind, value = action.kind, action.value

        if kind in _PATTERNS:
            needs, method, takes_value = _PATTERNS[kind]
            if needs in props["actions"]:
                if kind in (A.CHECK, A.UNCHECK) and (props["checked"] == "on") == (kind == A.CHECK):
                    plans.append(("pattern", f"no-op: already {kind}ed (checked={props['checked']!r})", lambda: None))
                else:
                    fn = getattr(el, method)
                    call = f"Element.{method}({value!r})" if takes_value else f"Element.{method}()"
                    plans.append(("pattern", call, (lambda: fn(value)) if takes_value else fn))

        bounds = props["bounds"]
        if kind in _MOUSE and bounds:
            button, count = _MOUSE[kind]
            center = (bounds[0] + bounds[2] // 2, bounds[1] + bounds[3] // 2)

            def click() -> str:
                note = self._ensure_mouse_target(center)
                xa11y.input_sim().click(el, button=button, count=count)
                return note

            plans.append(("synthetic_input", f"input_sim.click({center}, button={button!r}, count={count})", click))
        elif kind == A.TYPE_TEXT:
            def type_text() -> str:
                note = self._ensure_keyboard_target()
                el.focus()
                xa11y.input_sim().type_text(value or "")
                return note

            plans.append(("synthetic_input", f"Element.focus(); input_sim.type_text({value!r})", type_text))
        elif kind == A.PRESS_KEYS:
            steps = parse_keys(value or "")

            def press() -> str:
                note = self._ensure_keyboard_target()
                if "focus" in props["actions"]:
                    el.focus()
                sim = xa11y.input_sim()
                for key, held in steps:
                    sim.chord(key, held=held) if held else sim.press(key)
                return note

            plans.append(("synthetic_input", f"focus; keys {steps}", press))
        return plans

    # -- synthetic input safety -------------------------------------------------------------
    # Synthetic input goes wherever the OS sends it. Windows may refuse to bring our window to
    # the foreground (foreground lock), so before any input we verify where it will land.

    def _try_activate(self) -> str | None:
        try:
            self._window.activate()
            return None
        except xa11y.XA11yError as e:
            return f"{type(e).__name__}: {e}"

    def _ensure_mouse_target(self, point: tuple[int, int]) -> str:
        denied = self._try_activate()
        hit = win32gui.GetAncestor(win32gui.WindowFromPoint(point), win32con.GA_ROOT)
        if hit != self.target.hwnd:
            raise SyntheticInputBlocked(
                f"point {point} is covered by {win32gui.GetWindowText(hit)!r} ({hit:#x})"
                + (f"; activation failed: {denied}" if denied else "")
            )
        return f"activation failed ({denied}) but {point} hit-tests to the target window" if denied \
            else "activated window"

    def _ensure_keyboard_target(self) -> str:
        denied = self._try_activate()
        fg = win32gui.GetAncestor(win32gui.GetForegroundWindow(), win32con.GA_ROOT)
        if fg != self.target.hwnd:
            raise SyntheticInputBlocked(
                f"keyboard input would go to {win32gui.GetWindowText(fg)!r} ({fg:#x}), not the target window"
                + (f"; activation failed: {denied}" if denied else "")
            )
        return "target window is in the foreground"

    # -- internals --------------------------------------------------------------------------

    def _find_window(self) -> xa11y.Element:
        try:
            for w in xa11y.App.by_pid(self.target.pid, timeout=0).windows():
                if w.stable_id == self._sid:
                    return w
        except xa11y.XA11yError:
            pass
        for app in xa11y.App.list():  # window hosted by another process (UWP frame host)
            for w in app.windows():
                if w.stable_id == self._sid:
                    return w
        raise LocatorNotFound(f"window {self._sid} ({self.target.title!r}) not found via UI Automation")

    def _locate(self, loc: str) -> xa11y.Element:
        if self._window is None:
            raise LocatorNotFound("adapter is not attached")
        sid, _, path = loc.partition("/")
        if sid != self._sid:
            raise LocatorNotFound(f"locator {loc!r} belongs to another window")
        for attempt in (1, 2):
            try:
                el = self._window
                for part in [p for p in path.split("/") if p]:
                    children = el.children()
                    i = int(part)
                    if i >= len(children):
                        raise LocatorNotFound(f"no element at locator {loc!r}")
                    el = children[i]
                return el
            except xa11y.XA11yError:
                if attempt == 2:
                    raise
                self._window = self._find_window()  # window handle went stale; re-acquire once
        raise AssertionError("unreachable")

    def _node(self, el: xa11y.Element, loc: str, depth: int) -> Node:
        props = read_props(el)
        raw = props["raw"]
        ctid = raw.get("control_type_id")
        role = uia_role(ctid, props["role"] or "", bool(props["modal"]))
        children_els = el.children()
        children = [self._node(c, f"{loc}/{i}", depth - 1) for i, c in enumerate(children_els)] if depth > 0 else []
        b = props["bounds"]
        return Node(
            role=role,
            name=props["name"],
            value=props["value"],
            description=props["description"],
            states=uia_states(props, role),
            actions=uia_actions(props["actions"]),
            bounds=Rect(x=b[0], y=b[1], width=b[2], height=b[3]) if b else None,
            children=children,
            child_count=len(children_els),
            meta=NodeMeta(
                adapter=self.name,
                native_role=native_role_name(ctid),
                automation_id=raw.get("automation_id") or None,
                class_name=raw.get("class_name") or None,
                process=self.target.process_name if self.target else None,
                locator=loc,
            ),
        )

    def _raw(self, el: xa11y.Element, loc: str, depth: int) -> dict:
        out: dict[str, Any] = {"locator": loc, **read_props(el)}
        if depth > 0:
            out["children"] = [self._raw(c, f"{loc}/{i}", depth - 1) for i, c in enumerate(el.children())]
        return out

