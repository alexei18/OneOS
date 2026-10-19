import pytest

from uibridge.core.adapter import WindowTarget
from uibridge.core.detector import detect

ALL = {"base_uia", "win32", "cdp"}


def target(**kw) -> WindowTarget:
    base = {"app_id": "0x1", "hwnd": 1, "pid": 100, "title": "T", "process_name": "app.exe"}
    return WindowTarget(**{**base, **kw})


@pytest.mark.parametrize(
    "kw, framework, preferred",
    [
        ({"class_name": "Chrome_WidgetWin_1", "electron_resources": r"C:\x\resources\app", "process_name": "Code.exe"}, "electron", "cdp"),
        ({"class_name": "Chrome_WidgetWin_1", "process_name": "chrome.exe"}, "chromium", "cdp"),
        ({"class_name": "HostWnd", "child_processes": ["msedgewebview2.exe"]}, "webview2", "cdp"),
        ({"class_name": "HostWnd", "modules": ["WebView2Loader.dll"]}, "webview2", "cdp"),
        ({"class_name": "HwndWrapper[App;;1234]"}, "wpf", "base_uia"),
        ({"class_name": "WindowsForms10.Window.8.app.0.1"}, "winforms", "base_uia"),
        ({"class_name": "SunAwtFrame"}, "java", "base_uia"),
        ({"class_name": "Win", "modules": ["jvm.dll"]}, "java", "base_uia"),
        ({"class_name": "Qt5QWindowIcon", "modules": ["Qt5Core.dll"]}, "qt", "base_uia"),
        ({"class_name": "TFormMain", "child_classes": ["TPanel", "TButton"]}, "delphi", "win32"),
        ({"class_name": "TFormMain", "modules": ["vcl280.bpl"]}, "delphi", "win32"),
        ({"class_name": "#32770"}, "win32", "win32"),
        ({"class_name": "RegEdit_RegEdit", "child_classes": ["SysTreeView32", "SysListView32"]}, "win32", "win32"),
        ({"class_name": "ApplicationFrameWindow"}, "uwp", "base_uia"),
        ({"class_name": "Notepad", "child_classes": ["Microsoft.UI.Content.DesktopChildSiteBridge"]},
         "winui", "base_uia"),
        ({"class_name": "CabinetWClass",  # Win11 Explorer: XAML island inside real Win32 controls
          "child_classes": ["Microsoft.UI.Content.DesktopChildSiteBridge", "SysTreeView32"]}, "win32", "win32"),
        ({"class_name": "SomethingElse"}, "unknown", "base_uia"),
    ],
)
def test_signals_table(kw, framework, preferred):
    d = detect(target(**kw), ALL)
    assert (d.framework, d.preferred, d.chosen) == (framework, preferred, preferred)


def test_unavailable_adapter_falls_back_to_base_uia_and_says_why():
    d = detect(target(class_name="Chrome_WidgetWin_1", electron_resources="resources/app.asar"), {"base_uia"})
    assert (d.framework, d.preferred, d.chosen) == ("electron", "cdp", "base_uia")
    assert "not available" in d.why


def test_highest_score_wins_and_all_signals_are_listed():
    # a Qt app that also hosts a standard Win32 Button child
    d = detect(target(class_name="Qt5QWindowIcon", modules=["Qt5Core.dll"], child_classes=["Button"]), ALL)
    assert d.framework == "qt"
    assert {m["framework"] for m in d.matched} == {"qt", "win32"}
    assert d.scores == {"base_uia": 80, "win32": 60}


def test_no_signal_reports_unknown():
    d = detect(target(class_name="X"), ALL)
    assert d.matched == [] and d.chosen == "base_uia" and "no framework signal" in d.why
