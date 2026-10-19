"""Top-level window enumeration + detector signals (pywin32, dwmapi via ctypes, psutil).

Lives in adapters/ because only adapters may import win32* (CLAUDE.md rule 5). The detector in
core/ scores the plain WindowTarget data this module returns.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

import psutil
import win32con
import win32gui
import win32process

from uibridge.adapters._dpi import ensure_dpi_aware
from uibridge.core.adapter import WindowTarget

_DWMWA_CLOAKED = 14

ensure_dpi_aware()


def _is_cloaked(hwnd: int) -> bool:
    cloaked = wintypes.DWORD(0)
    hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd), _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return hr == 0 and cloaked.value != 0


def _is_app_window(hwnd: int) -> bool:
    return (
        win32gui.IsWindowVisible(hwnd)
        and bool(win32gui.GetWindowText(hwnd))
        and not win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW
        and not _is_cloaked(hwnd)
    )


def _child_classes(hwnd: int) -> list[str]:
    classes: set[str] = set()

    def collect(child: int, _: object) -> bool:
        classes.add(win32gui.GetClassName(child))
        return True

    try:
        win32gui.EnumChildWindows(hwnd, collect, None)
    except win32gui.error:
        pass  # windows without children raise on some builds
    return sorted(classes)


def _electron_resources(exe: str | None) -> str | None:
    """Electron apps ship resources/app.asar or resources/app, next to the exe or one folder
    down (VS Code: <install>/<commit>/resources/app)."""
    if not exe:
        return None
    base = Path(exe).parent
    for resources in [base / "resources", *base.glob("*/resources")]:
        for app in ("app.asar", "app"):
            if (resources / app).exists():
                return str(resources / app)
    return None


def describe_window(hwnd: int) -> WindowTarget:
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    notes: list[str] = []
    process_name, exe, modules, children = "?", None, [], []
    try:
        proc = psutil.Process(pid)
        process_name = proc.name()
        exe = proc.exe()
        children = sorted({c.name() for c in proc.children()})
        modules = sorted({Path(m.path).name for m in proc.memory_maps() if m.path.lower().endswith((".dll", ".bpl"))})
    except psutil.AccessDenied:
        notes.append("process details: access denied")
    except psutil.NoSuchProcess:
        notes.append("process exited")
    return WindowTarget(
        app_id=f"{hwnd:#x}",
        hwnd=hwnd,
        pid=pid,
        title=win32gui.GetWindowText(hwnd),
        process_name=process_name,
        class_name=win32gui.GetClassName(hwnd),
        exe=exe,
        child_classes=_child_classes(hwnd),
        modules=modules,
        child_processes=children,
        electron_resources=_electron_resources(exe),
        notes=notes,
    )


def list_windows() -> list[WindowTarget]:
    hwnds: list[int] = []
    win32gui.EnumWindows(lambda h, _: hwnds.append(h) or True, None)
    return [describe_window(h) for h in hwnds if _is_app_window(h)]
