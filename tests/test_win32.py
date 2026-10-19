"""win32 enricher against real Win32 controls created in this test process (no external app)."""

import ctypes

import pytest
import win32api
import win32con
import win32gui

from uibridge.adapters.win32 import (
    WIN32_CLASS_ROLES,
    Win32Enricher,
    describe,
    get_text,
    send,
    strip_accelerator,
)
from uibridge.core.adapter import Declined, WindowTarget
from uibridge.core.model import Action, ActionKind, Node, NodeMeta, Role, State

A = ActionKind
IDS = {"edit": 101, "password": 102, "push": 103, "check": 104, "static": 105, "list": 106, "combo": 107,
       "tree": 108, "disabled": 109, "dupes": 110}


@pytest.fixture(scope="module")
def ui():
    """A hidden host window with standard child controls; records WM_COMMAND notifications."""
    ctypes.windll.comctl32.InitCommonControls()
    commands = []

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == win32con.WM_COMMAND:
            commands.append((win32api.HIWORD(wparam), win32api.LOWORD(wparam), lparam))
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    hinst = win32api.GetModuleHandle(None)
    wc = win32gui.WNDCLASS()
    wc.lpszClassName = "UibridgeWin32TestHost"
    wc.lpfnWndProc = wndproc
    wc.hInstance = hinst
    atom = win32gui.RegisterClass(wc)
    host = win32gui.CreateWindowEx(0, atom, "Test host", win32con.WS_OVERLAPPEDWINDOW, 0, 0, 500, 400, 0, 0, hinst, None)
    child = win32con.WS_CHILD | win32con.WS_VISIBLE

    def make(cls, text, style, key):
        return win32gui.CreateWindowEx(0, cls, text, child | style, 10, 10, 120, 80, host, IDS[key], hinst, None)

    h = {
        "edit": make("Edit", "hello", win32con.ES_AUTOHSCROLL, "edit"),
        "password": make("Edit", "secret", win32con.ES_PASSWORD, "password"),
        "push": make("Button", "&Browse...", win32con.BS_PUSHBUTTON, "push"),
        "check": make("Button", "Remember me", win32con.BS_AUTOCHECKBOX, "check"),
        "static": make("Static", "&Name:", 0, "static"),
        "list": make("ListBox", "", win32con.LBS_NOTIFY | win32con.LBS_HASSTRINGS, "list"),
        "combo": make("ComboBox", "", win32con.CBS_DROPDOWN | win32con.CBS_HASSTRINGS, "combo"),
        "tree": make("SysTreeView32", "", 0, "tree"),
        "disabled": make("Button", "Off", win32con.BS_PUSHBUTTON | win32con.WS_DISABLED, "disabled"),
        "dupes": make("ListBox", "", win32con.LBS_HASSTRINGS, "dupes"),
    }
    for text in ("alpha", "beta", "gamma"):
        buf = ctypes.create_unicode_buffer(text)
        send(h["list"], win32con.LB_ADDSTRING, 0, ctypes.addressof(buf))
        send(h["combo"], win32con.CB_ADDSTRING, 0, ctypes.addressof(buf))
    for text in ("same", "same"):
        buf = ctypes.create_unicode_buffer(text)
        send(h["dupes"], win32con.LB_ADDSTRING, 0, ctypes.addressof(buf))
    yield host, h, commands
    win32gui.DestroyWindow(host)
    win32gui.UnregisterClass(atom, hinst)


def enricher_for(host) -> Win32Enricher:
    e = Win32Enricher()
    e.attach(WindowTarget(app_id=f"{host:#x}", hwnd=host, pid=1, title="Test host", process_name="python.exe"))
    return e


def tree_for(host, h) -> Node:
    """A tree shaped like base_uia output, including the mistakes UIA makes on classic controls."""
    def n(ref, role, hwnd=None, **kw):
        return Node(ref=ref, role=role, meta=NodeMeta(adapter="base_uia", hwnd=hwnd), **kw)

    listbox = n("e6", Role.PANE, h["list"], children=[n(f"e6{i}", Role.LISTITEM, name=t)
                                                      for i, t in enumerate(("alpha", "beta", "gamma"))])
    dupes = n("e10", Role.LIST, h["dupes"], children=[n("e101", Role.LISTITEM, name="same"),
                                                      n("e102", Role.LISTITEM, name="same")])
    return n("e0", Role.WINDOW, host, name="Test host", children=[
        n("e1", Role.TEXTBOX, h["edit"]), n("e2", Role.TEXTBOX, h["password"]),
        n("e3", Role.BUTTON, h["push"], name="Browse...", states={State.ENABLED}),
        n("e4", Role.BUTTON, h["check"], name="Remember me", states={State.ENABLED}),
        n("e5", Role.TEXT, h["static"]), listbox,
        n("e7", Role.COMBOBOX, h["combo"], states={State.ENABLED, State.READONLY}),
        n("e8", Role.TREE, h["tree"], children=[n("e81", Role.TREEITEM, name="root")]),
        n("e9", Role.BUTTON, h["disabled"], name="Off", states={State.ENABLED}), dupes,
    ])


def by_ref(tree, ref):
    return next(x for x in tree.walk() if x.ref == ref)


def test_class_role_table():
    assert all(isinstance(r, Role) for r in WIN32_CLASS_ROLES.values())
    assert strip_accelerator("&Browse...") == "Browse..." and strip_accelerator("R&&D") == "R&D"


def test_describe_reads_classes_ids_text_and_never_passwords(ui):
    host, h, _ = ui
    edit, pw, check, lst = describe(h["edit"]), describe(h["password"]), describe(h["check"]), describe(h["list"])
    assert (edit.cls, edit.control_id, edit.text) == ("Edit", 101, "hello")
    assert pw.text is None and pw.extra["password"] is True
    assert check.role == Role.CHECKBOX and check.extra == {"kind": "autocheckbox", "check": 0}
    assert lst.role == Role.LISTBOX and lst.extra["count"] == 3 and lst.extra["strings"]
    assert describe(h["combo"]).extra["editable"] is True


def test_enrich_fills_and_fixes_with_sources(ui):
    host, h, _ = ui
    tree = tree_for(host, h)
    enricher_for(host).enrich(tree)
    edit, pw, check, static, lst, combo, disabled = (by_ref(tree, r) for r in ("e1", "e2", "e4", "e5", "e6", "e7", "e9"))
    assert edit.value == "hello" and edit.meta.sources["value"] == "win32" and edit.meta.control_id == 101
    assert pw.value is None
    assert check.role == Role.CHECKBOX and State.UNCHECKED in check.states
    assert check.meta.sources == {"class_name": "win32", "role": "win32", "states": "win32"}
    assert static.name == "Name:" and static.meta.sources["name"] == "win32"
    assert lst.role == Role.LISTBOX and lst.meta.sources["role"] == "win32"
    assert State.READONLY not in combo.states  # editable CBS_DROPDOWN
    assert State.DISABLED in disabled.states and State.ENABLED not in disabled.states
    assert by_ref(tree, "e3").meta.sources == {"class_name": "win32"}  # nothing else to change on a correct button


def test_trailing_cr_from_uia_is_corrected(ui):
    host, h, _ = ui
    win32gui.SetWindowText(h["edit"], "hello")
    tree = tree_for(host, h)
    by_ref(tree, "e1").value = "hello\r"  # what UIA reports for RichEdit (charmap "Characters to copy")
    enricher_for(host).enrich(tree)
    assert by_ref(tree, "e1").value == "hello" and by_ref(tree, "e1").meta.sources["value"] == "win32"


def test_set_value_via_wm_settext(ui):
    host, h, _ = ui
    result = enricher_for(host).act(tree_for(host, h), "e1", Action(kind=A.SET_VALUE, value="world"))
    assert result.ok and result.strategy == "native_message" and "WM_SETTEXT" in result.native_call
    assert get_text(h["edit"]) == "world" and result.before_fingerprint != result.after_fingerprint


def test_set_value_on_password_does_not_read_it(ui):
    host, h, _ = ui
    result = enricher_for(host).act(tree_for(host, h), "e2", Action(kind=A.SET_VALUE, value="new"))
    assert result.ok and result.before_fingerprint == result.after_fingerprint  # state capture is blind on purpose


def test_click_posts_bn_clicked_to_the_parent(ui):
    host, h, commands = ui
    commands.clear()
    result = enricher_for(host).act(tree_for(host, h), "e3", Action(kind=A.CLICK))
    win32gui.PumpWaitingMessages()
    assert result.ok and "BN_CLICKED" in result.native_call
    assert (win32con.BN_CLICKED, IDS["push"], h["push"]) in commands


def test_checkbox_check_and_noop(ui):
    host, h, commands = ui
    e = enricher_for(host)
    assert e.act(tree_for(host, h), "e4", Action(kind=A.CHECK)).ok
    assert send(h["check"], win32con.BM_GETCHECK) == 1
    again = e.act(tree_for(host, h), "e4", Action(kind=A.CHECK))
    assert again.ok and again.native_call.startswith("no-op")
    assert e.act(tree_for(host, h), "e4", Action(kind=A.UNCHECK)).ok and send(h["check"], win32con.BM_GETCHECK) == 0


def test_select_list_item_by_text_and_notify(ui):
    host, h, commands = ui
    commands.clear()
    result = enricher_for(host).act(tree_for(host, h), "e61", Action(kind=A.SELECT))
    win32gui.PumpWaitingMessages()
    assert result.ok and "LB_SETCURSEL, 1" in result.native_call
    assert send(h["list"], win32con.LB_GETCURSEL) == 1
    assert (win32con.LBN_SELCHANGE, IDS["list"], h["list"]) in commands


def test_duplicate_item_text_is_declined(ui):
    host, h, _ = ui
    out = enricher_for(host).act(tree_for(host, h), "e101", Action(kind=A.SELECT))
    assert isinstance(out, Declined) and "2 items named 'same'" in out.reason


def test_tree_view_is_declined_as_unsafe(ui):
    host, h, _ = ui
    out = enricher_for(host).act(tree_for(host, h), "e81", Action(kind=A.SELECT))
    assert isinstance(out, Declined) and "SysTreeView32" in out.reason and "not safe" in out.reason


def test_disabled_and_unsupported_are_declined(ui):
    host, h, _ = ui
    e = enricher_for(host)
    assert "disabled" in e.act(tree_for(host, h), "e9", Action(kind=A.CLICK)).reason
    assert "no native message for focus on Edit" in e.act(tree_for(host, h), "e1", Action(kind=A.FOCUS)).reason


def test_dry_run_changes_nothing(ui):
    host, h, _ = ui
    win32gui.SetWindowText(h["edit"], "keep")
    result = enricher_for(host).act(tree_for(host, h), "e1", Action(kind=A.SET_VALUE, value="other"), dry_run=True)
    assert result.native_call.startswith("DRY-RUN: SendMessageTimeout") and get_text(h["edit"]) == "keep"
