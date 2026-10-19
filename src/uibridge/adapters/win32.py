"""win32 enricher: HWND-level facts and native-message actions (strategy 2) on top of the base_uia tree.

UIA nodes are matched to HWNDs exactly: base_uia puts the element's window handle in meta.hwnd
(xa11y stable_id "hwnd:0x…"). Only messages Windows marshals across processes are used
(WM_GETTEXT/WM_SETTEXT, BM_*, CB_*, LB_*, EM_*, WM_COMMAND notifications). TreeView/ListView
messages need writes into the target's memory and are declined, so those actions stay on UIA.
"""

from __future__ import annotations

import ctypes
import hashlib
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from typing import Any

import win32api
import win32con
import win32gui

from uibridge.adapters._dpi import ensure_dpi_aware
from uibridge.core.adapter import Declined, WindowTarget
from uibridge.core.model import Action, ActionKind, ActionResult, Node, Role, State

ensure_dpi_aware()

A = ActionKind
TIMEOUT_MS = 300
SMTO_ABORTIFHUNG = 0x0002
LB_ERR = -1

_user32 = ctypes.windll.user32
_SendMessageTimeoutW = _user32.SendMessageTimeoutW
_SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT,
                                 wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
_SendMessageTimeoutW.restype = wintypes.LPARAM

# Button styles (style & 0xF) -> role
_BUTTON_KINDS: dict[int, tuple[str, Role]] = {
    0x0: ("push", Role.BUTTON), 0x1: ("defpush", Role.BUTTON), 0x2: ("checkbox", Role.CHECKBOX),
    0x3: ("autocheckbox", Role.CHECKBOX), 0x4: ("radio", Role.RADIO), 0x5: ("3state", Role.CHECKBOX),
    0x6: ("auto3state", Role.CHECKBOX), 0x7: ("groupbox", Role.GROUP), 0x9: ("autoradio", Role.RADIO),
    0xB: ("ownerdraw", Role.BUTTON), 0xC: ("split", Role.BUTTON), 0xD: ("defsplit", Role.BUTTON),
    0xE: ("commandlink", Role.BUTTON), 0xF: ("defcommandlink", Role.BUTTON),
}

WIN32_CLASS_ROLES: dict[str, Role] = {
    "Edit": Role.TEXTBOX, "RichEdit20W": Role.TEXTBOX, "RICHEDIT50W": Role.TEXTBOX,
    "ComboBox": Role.COMBOBOX, "ComboBoxEx32": Role.COMBOBOX, "ListBox": Role.LISTBOX,
    "SysTreeView32": Role.TREE, "SysListView32": Role.TABLE, "SysTabControl32": Role.TABLIST,
    "msctls_statusbar32": Role.STATUSBAR, "ToolbarWindow32": Role.TOOLBAR, "msctls_progress32": Role.PROGRESSBAR,
    "msctls_trackbar32": Role.SLIDER, "msctls_updown32": Role.SPINBUTTON, "Static": Role.TEXT,
    "SysLink": Role.LINK, "ScrollBar": Role.SCROLLBAR, "#32770": Role.DIALOG,
}
EDIT_CLASSES = {"Edit", "RichEdit20W", "RICHEDIT50W"}
NAME_FROM_TEXT = {"Button", "Static", "SysLink", "#32770"}
MEMORY_CLASSES = {"SysTreeView32", "SysListView32", "SysTabControl32", "SysHeader32", "ToolbarWindow32"}
_SS_TYPEMASK, _SS_ICON, _SS_BITMAP = 0x1F, 0x3, 0xE
_ES_PASSWORD, _ES_READONLY, _ES_MULTILINE = 0x20, 0x800, 0x4
_CBS_TYPEMASK, _CBS_DROPDOWNLIST = 0x3, 0x3
_CBS_OWNERDRAW, _CBS_HASSTRINGS = 0x30, 0x200
_LBS_OWNERDRAW, _LBS_HASSTRINGS, _LBS_MULTI = 0x30, 0x40, 0x0008 | 0x0800


class MessageFailed(Exception):
    pass


def send(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> int:
    """SendMessage with a timeout so a hung target cannot freeze us. Returns the signed result."""
    result = ctypes.c_size_t()
    if not _SendMessageTimeoutW(hwnd, msg, wparam, lparam, SMTO_ABORTIFHUNG, TIMEOUT_MS, ctypes.byref(result)):
        raise MessageFailed(f"SendMessageTimeout({hwnd:#x}, {msg:#x}) failed or timed out after {TIMEOUT_MS} ms")
    value = result.value
    return value - (1 << 64) if value >= (1 << 63) else value


def get_text(hwnd: int) -> str:
    length = send(hwnd, win32con.WM_GETTEXTLENGTH)
    buf = ctypes.create_unicode_buffer(length + 1)
    send(hwnd, win32con.WM_GETTEXT, length + 1, ctypes.addressof(buf))
    return buf.value


def item_text(hwnd: int, len_msg: int, get_msg: int, index: int) -> str | None:
    length = send(hwnd, len_msg, index)
    if length == LB_ERR:
        return None
    buf = ctypes.create_unicode_buffer(length + 1)
    send(hwnd, get_msg, index, ctypes.addressof(buf))
    return buf.value


def strip_accelerator(text: str) -> str:
    """'&Browse...' -> 'Browse...', 'R&&D' -> 'R&D' (Win32 mnemonic markers)."""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&")


@dataclass
class HwndInfo:
    hwnd: int
    cls: str
    control_id: int
    style: int
    enabled: bool
    visible: bool
    text: str | None  # None: not read (password edit) or not readable
    parent: int
    extra: dict[str, Any] = field(default_factory=dict)  # per class: button kind/check, combobox count/cursel...

    @property
    def role(self) -> Role | None:
        if self.cls == "Button":
            return _BUTTON_KINDS.get(self.style & 0xF, ("?", Role.BUTTON))[1]
        if self.cls == "Static" and self.style & _SS_TYPEMASK in (_SS_ICON, _SS_BITMAP):
            return Role.IMAGE
        return WIN32_CLASS_ROLES.get(self.cls)


def describe(hwnd: int) -> HwndInfo:
    cls = win32gui.GetClassName(hwnd)
    style = win32api.GetWindowLong(hwnd, win32con.GWL_STYLE) & 0xFFFFFFFF
    extra: dict[str, Any] = {}
    text: str | None = None
    try:
        if cls in EDIT_CLASSES and style & _ES_PASSWORD:
            extra["password"] = True  # never read a password field
        else:
            text = get_text(hwnd)
        if cls == "Button":
            extra["kind"] = _BUTTON_KINDS.get(style & 0xF, ("?", Role.BUTTON))[0]
            if _BUTTON_KINDS.get(style & 0xF, ("", Role.BUTTON))[1] in (Role.CHECKBOX, Role.RADIO):
                extra["check"] = send(hwnd, win32con.BM_GETCHECK)
        elif cls in EDIT_CLASSES:
            extra["readonly"] = bool(style & _ES_READONLY)
            extra["multiline"] = bool(style & _ES_MULTILINE)
        elif cls == "ComboBox":
            extra["editable"] = (style & _CBS_TYPEMASK) != _CBS_DROPDOWNLIST
            extra["count"] = send(hwnd, win32con.CB_GETCOUNT)
            extra["cursel"] = send(hwnd, win32con.CB_GETCURSEL)
            extra["strings"] = not (style & _CBS_OWNERDRAW) or bool(style & _CBS_HASSTRINGS)
        elif cls == "ListBox":
            extra["count"] = send(hwnd, win32con.LB_GETCOUNT)
            extra["cursel"] = send(hwnd, win32con.LB_GETCURSEL)
            extra["strings"] = not (style & _LBS_OWNERDRAW) or bool(style & _LBS_HASSTRINGS)
            extra["multi"] = bool(style & _LBS_MULTI)
    except MessageFailed as e:
        extra["error"] = str(e)
    return HwndInfo(hwnd=hwnd, cls=cls, control_id=win32gui.GetDlgCtrlID(hwnd), style=style,
                    enabled=bool(win32gui.IsWindowEnabled(hwnd)), visible=bool(win32gui.IsWindowVisible(hwnd)),
                    text=text, parent=win32gui.GetParent(hwnd), extra=extra)


def _hash(*parts: Any) -> str:
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:8]


class Win32Enricher:
    name = "win32"

    def __init__(self) -> None:
        self.hwnd: int | None = None

    def attach(self, target: WindowTarget) -> None:
        self.hwnd = target.hwnd

    def collect(self) -> dict[int, HwndInfo]:
        hwnds = [self.hwnd]
        win32gui.EnumChildWindows(self.hwnd, lambda h, _: hwnds.append(h) or True, None)
        return {h: describe(h) for h in hwnds if win32gui.IsWindow(h)}

    def raw_dump(self) -> dict:
        return {"hwnds": [{**asdict(i), "hwnd": f"{i.hwnd:#x}", "parent": f"{i.parent:#x}"}
                          for i in self.collect().values()]}

    # -- enrichment -------------------------------------------------------------------------

    def enrich(self, tree: Node) -> None:
        infos = self.collect()
        for node in tree.walk():
            info = infos.get(node.meta.hwnd or 0)
            if info is not None:
                self._enrich_node(node, info)

    def _enrich_node(self, node: Node, info: HwndInfo) -> None:
        src = node.meta.sources

        def changed(field_name: str) -> None:
            src[field_name] = self.name

        node.meta.control_id = info.control_id
        if not node.meta.class_name:
            node.meta.class_name = info.cls
            changed("class_name")
        expected = info.role
        role_fix = expected and expected != node.role and (
            node.role in (Role.GENERIC, Role.PANE)
            or (info.cls == "Button" and node.role in (Role.BUTTON, Role.CHECKBOX, Role.RADIO, Role.GROUP)))
        if role_fix:
            node.role = expected
            changed("role")
        if not node.name and info.text and info.cls in NAME_FROM_TEXT:
            node.name = strip_accelerator(info.text).strip()
            changed("name")
        if info.cls in EDIT_CLASSES | {"ComboBox"} and info.text is not None:
            uia_value = node.value
            # UIA misses Edit values or (RichEdit) appends a trailing CR that the control does not hold
            if node.value != info.text and (uia_value is None or not uia_value.strip()
                                            or uia_value.rstrip("\r") == info.text):
                node.value = info.text
                changed("value")

        states = set(node.states)
        if info.extra.get("readonly"):
            states.add(State.READONLY)
        if info.cls == "ComboBox" and info.extra.get("editable"):
            states.discard(State.READONLY)  # CBS_DROPDOWN is editable even if UIA says read-only
        if not info.enabled and State.ENABLED in states:
            states = (states - {State.ENABLED}) | {State.DISABLED}
        check = info.extra.get("check")
        if check is not None and not states & {State.CHECKED, State.UNCHECKED, State.MIXED}:
            states.add({0: State.UNCHECKED, 1: State.CHECKED}.get(check, State.MIXED))
        if states != node.states:
            node.states = states
            changed("states")

    # -- strategy 2: native messages ---------------------------------------------------------

    def act(self, tree: Node, ref: str, action: Action, dry_run: bool = False) -> ActionResult | Declined:
        path = _path_to(tree, ref)
        if not path:
            return Declined(f"{ref} is not in the pre-action tree")
        node, ancestors = path[-1], path[:-1]
        start = time.perf_counter()

        own = describe(node.meta.hwnd) if node.meta.hwnd and win32gui.IsWindow(node.meta.hwnd) else None
        host = next((describe(a.meta.hwnd) for a in reversed(ancestors)
                     if a.meta.hwnd and win32gui.IsWindow(a.meta.hwnd)), None)
        memory_cls = next((i.cls for i in (own, host) if i and i.cls in MEMORY_CLASSES), None)
        if memory_cls:
            return Declined(f"{memory_cls} messages (TVM_/LVM_) need writes into the target process memory; "
                            "not safe, using UIA")

        plan = None
        if own is not None:
            if not own.enabled:
                return Declined("control is disabled (WS_DISABLED)")
            plan = self._plan_own(own, action)
        elif action.kind == A.SELECT and host is not None and host.cls in ("ListBox", "ComboBox"):
            plan = self._plan_select(host, node.name)
        if plan is None or isinstance(plan, Declined):
            cls = own.cls if own else (host.cls if host else "an element without its own HWND")
            return plan or Declined(f"no native message for {action.kind} on {cls}")

        description, run, state = plan
        if dry_run:
            return ActionResult(ok=True, strategy="native_message", native_call=f"DRY-RUN: {description}",
                                duration_ms=(time.perf_counter() - start) * 1000)
        before = state()
        try:
            error = run()
        except MessageFailed as e:
            error = str(e)
        after = state()
        return ActionResult(ok=error is None, strategy="native_message", native_call=description, error=error,
                            duration_ms=(time.perf_counter() - start) * 1000,
                            before_fingerprint=_hash(before), after_fingerprint=_hash(after))

    def _plan_own(self, info: HwndInfo, action: Action):
        h, k, cid, parent = info.hwnd, action.kind, info.control_id, info.parent
        kind = info.extra.get("kind")

        def notify(code: int) -> None:  # what a real click/edit tells the dialog
            win32gui.PostMessage(parent, win32con.WM_COMMAND, (code << 16) | (cid & 0xFFFF), h)

        if info.cls == "Button" and kind != "groupbox":
            state = lambda: send(h, win32con.BM_GETCHECK)  # noqa: E731
            if k == A.CLICK and info.role == Role.BUTTON:
                def click() -> None:
                    notify(win32con.BN_CLICKED)
                return (f"PostMessage(parent {parent:#x}, WM_COMMAND, BN_CLICKED, id={cid}, {h:#x})", click, state)
            if k in (A.CLICK, A.TOGGLE, A.CHECK, A.UNCHECK) and info.role in (Role.CHECKBOX, Role.RADIO):
                current = info.extra.get("check", 0)
                target = {A.CHECK: 1, A.UNCHECK: 0}.get(k, 0 if current == 1 else 1)
                if info.role == Role.RADIO:
                    target = 1
                if target == current:
                    return (f"no-op: BM_GETCHECK({h:#x}) is already {current}", lambda: None, state)

                def set_check() -> None:
                    send(h, win32con.BM_SETCHECK, target)
                    notify(win32con.BN_CLICKED)
                return (f"SendMessageTimeout({h:#x}, BM_SETCHECK, {target}); PostMessage(parent {parent:#x}, "
                        f"WM_COMMAND, BN_CLICKED, id={cid})", set_check, state)
            return None

        if info.cls in EDIT_CLASSES or (info.cls == "ComboBox" and info.extra.get("editable")):
            target_hwnd = h
            if info.cls == "ComboBox":  # write into the combo's own edit child, so it notifies like typing
                child = win32gui.FindWindowEx(h, 0, "Edit", None)
                target_hwnd = child or h
            if info.extra.get("password"):
                state = lambda: None  # noqa: E731  (a password field is never read)
            else:
                state = lambda: get_text(target_hwnd)  # noqa: E731
            if info.extra.get("readonly"):
                return Declined("edit control is read-only (ES_READONLY)")
            if k == A.SET_VALUE:
                buf = ctypes.create_unicode_buffer(action.value or "")

                def set_text() -> str | None:
                    send(target_hwnd, win32con.WM_SETTEXT, 0, ctypes.addressof(buf))
                    got = get_text(target_hwnd) if not info.extra.get("password") else action.value
                    return None if got == action.value else f"read back {got!r} after WM_SETTEXT"
                return (f"SendMessageTimeout({target_hwnd:#x}, WM_SETTEXT, {action.value!r})", set_text, state)
            if k == A.TYPE_TEXT and info.cls in EDIT_CLASSES:
                buf = ctypes.create_unicode_buffer(action.value or "")

                def replace_sel() -> None:
                    send(h, win32con.EM_REPLACESEL, 1, ctypes.addressof(buf))
                return (f"SendMessageTimeout({h:#x}, EM_REPLACESEL, undo=1, {action.value!r})", replace_sel, state)

        if info.cls == "ComboBox" and k in (A.EXPAND, A.COLLAPSE):
            show = 1 if k == A.EXPAND else 0

            def dropdown() -> None:
                send(h, win32con.CB_SHOWDROPDOWN, show)
            return (f"SendMessageTimeout({h:#x}, CB_SHOWDROPDOWN, {show})", dropdown,
                    lambda: send(h, win32con.CB_GETDROPPEDSTATE))
        return None

    def _plan_select(self, host: HwndInfo, name: str | None):
        h, cid, parent = host.hwnd, host.control_id, host.parent
        is_list = host.cls == "ListBox"
        if not host.extra.get("strings"):
            return Declined(f"owner-drawn {host.cls} without HASSTRINGS: item text not readable")
        if is_list and host.extra.get("multi"):
            return Declined("multi-select ListBox: single-item select message would drop other selections")
        len_msg, get_msg, set_msg, code = ((win32con.LB_GETTEXTLEN, win32con.LB_GETTEXT, win32con.LB_SETCURSEL,
                                            win32con.LBN_SELCHANGE) if is_list else
                                           (win32con.CB_GETLBTEXTLEN, win32con.CB_GETLBTEXT, win32con.CB_SETCURSEL,
                                            win32con.CBN_SELCHANGE))
        matches = [i for i in range(host.extra.get("count", 0)) if item_text(h, len_msg, get_msg, i) == name]
        if len(matches) != 1:
            return Declined(f"{len(matches)} items named {name!r} in {host.cls} {h:#x}; not guessing")
        index = matches[0]
        cursel_msg = win32con.LB_GETCURSEL if is_list else win32con.CB_GETCURSEL

        def select() -> str | None:
            send(h, set_msg, index)
            win32gui.PostMessage(parent, win32con.WM_COMMAND, (code << 16) | (cid & 0xFFFF), h)
            got = send(h, cursel_msg)
            return None if got == index else f"selection is {got} after setting {index}"
        set_name = "LB_SETCURSEL" if is_list else "CB_SETCURSEL"
        note = "LBN_SELCHANGE" if is_list else "CBN_SELCHANGE"
        return (f"SendMessageTimeout({h:#x}, {set_name}, {index}); PostMessage(parent {parent:#x}, WM_COMMAND, "
                f"{note}, id={cid})", select, lambda: send(h, cursel_msg))


def _path_to(tree: Node, ref: str) -> list[Node]:
    stack: list[tuple[Node, list[Node]]] = [(tree, [])]
    while stack:
        node, path = stack.pop()
        here = [*path, node]
        if node.ref == ref:
            return here
        stack.extend((c, here) for c in reversed(node.children))
    return []
