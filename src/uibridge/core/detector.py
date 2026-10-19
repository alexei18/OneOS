"""Detector (SPEC §6): which UI framework a window uses and which adapter should handle it."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from uibridge.core.adapter import WindowTarget

FALLBACK = "base_uia"

STANDARD_WIN32_CLASSES = {
    "#32770", "Button", "Edit", "Static", "ComboBox", "ComboBoxEx32", "ListBox", "SysTreeView32",
    "SysListView32", "SysTabControl32", "msctls_statusbar32", "msctls_trackbar32", "msctls_progress32",
    "ToolbarWindow32", "ReBarWindow32", "SysHeader32", "RichEdit20W", "RICHEDIT50W", "ScrollBar",
}
_DELPHI_CLASS = re.compile(r"^T[A-Z][A-Za-z0-9]+$")


def _modules(t: WindowTarget, *names: str) -> str | None:
    found = sorted({m for m in t.modules if m.lower() in {n.lower() for n in names}})
    return f"modules: {', '.join(found)}" if found else None


def _electron(t: WindowTarget) -> str | None:
    if (t.class_name or "").startswith("Chrome_WidgetWin_") and t.electron_resources:
        return f"class {t.class_name} + {t.electron_resources}"
    return None


def _chromium(t: WindowTarget) -> str | None:
    if (t.class_name or "").startswith("Chrome_WidgetWin_") and not t.electron_resources:
        browser_dll = _modules(t, "chrome.dll", "msedge.dll")
        return f"class {t.class_name}, {browser_dll or 'no Electron resources'}"
    return None


def _webview2(t: WindowTarget) -> str | None:
    if "msedgewebview2.exe" in [p.lower() for p in t.child_processes]:
        return "child process msedgewebview2.exe"
    return _modules(t, "WebView2Loader.dll", "EmbeddedBrowserWebView.dll")


def _class_prefix(prefix: str) -> Callable[[WindowTarget], str | None]:
    def check(t: WindowTarget) -> str | None:
        hits = [c for c in [t.class_name or "", *t.child_classes] if c.startswith(prefix)]
        return f"class {hits[0]}" if hits else None

    return check


def _java(t: WindowTarget) -> str | None:
    if t.class_name == "SunAwtFrame":
        return "class SunAwtFrame"
    return _modules(t, "jvm.dll")


def _qt(t: WindowTarget) -> str | None:
    return _modules(t, "Qt5Core.dll", "Qt6Core.dll", "Qt5Cored.dll", "Qt6Cored.dll")


def _delphi(t: WindowTarget) -> str | None:
    if not _DELPHI_CLASS.match(t.class_name or ""):
        return None
    bpl = [m for m in t.modules if m.lower().endswith(".bpl") and m.lower().startswith(("vcl", "rtl"))]
    t_children = [c for c in t.child_classes if _DELPHI_CLASS.match(c)]
    if bpl or t_children:
        return f"class {t.class_name} + {'modules ' + ', '.join(bpl) if bpl else 'child classes ' + ', '.join(t_children[:3])}"
    return None


def _win32(t: WindowTarget) -> str | None:
    hits = sorted({t.class_name or "", *t.child_classes} & STANDARD_WIN32_CLASSES)
    return f"standard Win32 classes: {', '.join(hits)}" if hits else None


def _uwp(t: WindowTarget) -> str | None:
    return "class ApplicationFrameWindow" if t.class_name == "ApplicationFrameWindow" else None


def _winui(t: WindowTarget) -> str | None:
    bridges = {"Microsoft.UI.Content.DesktopChildSiteBridge", "Windows.UI.Composition.DesktopWindowContentBridge"}
    hits = sorted(bridges & set(t.child_classes))
    return f"child class {hits[0]}" if hits else None


@dataclass(frozen=True)
class Rule:
    framework: str
    adapter: str
    score: int
    check: Callable[[WindowTarget], str | None]


RULES: list[Rule] = [
    Rule("electron", "cdp", 90, _electron),
    Rule("webview2", "cdp", 85, _webview2),
    Rule("chromium", "cdp", 80, _chromium),
    Rule("wpf", "base_uia", 80, _class_prefix("HwndWrapper[")),
    Rule("winforms", "base_uia", 80, _class_prefix("WindowsForms10.")),
    Rule("java", "base_uia", 80, _java),
    Rule("qt", "base_uia", 80, _qt),
    Rule("delphi", "win32", 75, _delphi),
    Rule("uwp", "base_uia", 70, _uwp),
    Rule("win32", "win32", 60, _win32),
    # Below win32: a XAML island inside a host with real Win32 controls (Win11 Explorer) is a
    # Win32 app; WinUI wins only when no standard classes are present (Win11 Notepad, Terminal).
    Rule("winui", "base_uia", 55, _winui),
]


class Detection(BaseModel):
    framework: str
    preferred: str
    chosen: str
    why: str
    scores: dict[str, int]
    matched: list[dict]  # every rule that fired: framework, adapter, score, signal
    facts: dict  # the inputs the rules looked at


def detect(target: WindowTarget, available: set[str]) -> Detection:
    matched = []
    for rule in RULES:
        signal = rule.check(target)
        if signal:
            matched.append({"framework": rule.framework, "adapter": rule.adapter, "score": rule.score,
                            "signal": signal})
    scores: dict[str, int] = {FALLBACK: 10}
    for m in matched:
        scores[m["adapter"]] = max(scores.get(m["adapter"], 0), m["score"])

    if matched:
        top = max(matched, key=lambda m: m["score"])  # first rule wins ties (table order)
        framework, preferred = top["framework"], top["adapter"]
    else:
        framework, preferred = "unknown", FALLBACK

    if preferred in available:
        chosen, why = preferred, f"{framework} -> {preferred}"
    else:
        chosen = FALLBACK
        why = f"{framework} -> {preferred} preferred but not available yet, using {FALLBACK}"
    if not matched:
        why = f"no framework signal matched -> {FALLBACK}"

    facts = {
        "class_name": target.class_name, "process": target.process_name, "exe": target.exe,
        "child_classes": target.child_classes, "child_processes": target.child_processes,
        "electron_resources": target.electron_resources, "module_count": len(target.modules), "notes": target.notes,
    }
    return Detection(framework=framework, preferred=preferred, chosen=chosen, why=why, scores=scores,
                     matched=matched, facts=facts)
