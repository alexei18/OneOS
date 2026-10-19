"""Synthetic-input safety checks in base_uia, with the Win32 calls monkeypatched (no live UI)."""

import pytest
import xa11y

from uibridge.adapters import base_uia
from uibridge.adapters.base_uia import BaseUIAAdapter, SyntheticInputBlocked, parse_keys
from uibridge.core.adapter import WindowTarget

TARGET, OTHER = 0x100, 0x200


class _Window:
    def __init__(self, denied: bool) -> None:
        self.denied = denied

    def activate(self) -> None:
        if self.denied:
            raise xa11y.PlatformError("SetForegroundWindow was denied (foreground lock)")


def adapter(monkeypatch, *, denied: bool, at_point: int, foreground: int) -> BaseUIAAdapter:
    a = BaseUIAAdapter()
    a.target = WindowTarget(app_id=f"{TARGET:#x}", hwnd=TARGET, pid=1, title="Calc", process_name="x.exe")
    a._window = _Window(denied)
    monkeypatch.setattr(base_uia.win32gui, "WindowFromPoint", lambda p: at_point)
    monkeypatch.setattr(base_uia.win32gui, "GetForegroundWindow", lambda: foreground)
    monkeypatch.setattr(base_uia.win32gui, "GetAncestor", lambda h, flag: h)
    monkeypatch.setattr(base_uia.win32gui, "GetWindowText", lambda h: "Editor")
    return a


def test_mouse_allowed_when_point_hits_target_even_if_activation_denied(monkeypatch):
    note = adapter(monkeypatch, denied=True, at_point=TARGET, foreground=OTHER)._ensure_mouse_target((5, 5))
    assert "activation failed" in note and "hit-tests to the target window" in note


def test_mouse_blocked_when_point_is_covered(monkeypatch):
    a = adapter(monkeypatch, denied=True, at_point=OTHER, foreground=OTHER)
    with pytest.raises(SyntheticInputBlocked, match="covered by 'Editor'"):
        a._ensure_mouse_target((5, 5))


def test_keys_blocked_unless_target_is_foreground(monkeypatch):
    with pytest.raises(SyntheticInputBlocked, match="would go to 'Editor'"):
        adapter(monkeypatch, denied=True, at_point=TARGET, foreground=OTHER)._ensure_keyboard_target()
    assert adapter(monkeypatch, denied=False, at_point=TARGET, foreground=TARGET)._ensure_keyboard_target()


def test_parse_keys():
    assert parse_keys("Ctrl+Shift+S Enter") == [("S", ["Ctrl", "Shift"]), ("Enter", [])]
    assert parse_keys("win+r") == [("r", ["Meta"])]
