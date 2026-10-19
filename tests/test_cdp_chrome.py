"""CdpContent end to end on the system Chrome (headless, temp profile, loopback port). Skipped without Chrome."""

import os
import subprocess
import urllib.parse
from pathlib import Path

import psutil
import pytest

from uibridge.adapters.cdp import CdpContent, CdpLauncher, ensure_loopback, find_port, free_port, listener_ips
from uibridge.core.adapter import ContentUnavailable, LaunchPlan, LocatorNotFound, WindowTarget
from uibridge.core.model import Action, ActionKind, Fingerprint, Ref, Role, State

CHROME = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe"
pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="system Chrome not installed")

HTML = """<title>CDP test</title><main><h1>Hello</h1>
<label>Name <input></label>
<label><input type=checkbox> I agree</label>
<select aria-label="Color"><option>Red</option><option>Green</option></select>
<button onclick="document.querySelector('h1').textContent='Clicked'">Run</button>
<input aria-label="Keys" onkeydown="this.value=event.key"></main>"""


@pytest.fixture(scope="module")
def chrome(tmp_path_factory):
    port = free_port()
    url = "data:text/html," + urllib.parse.quote(HTML)
    cmd = [str(CHROME), "--headless=new", f"--remote-debugging-port={port}",
           f"--user-data-dir={tmp_path_factory.mktemp('chrome-profile')}", "--no-first-run",
           "--no-default-browser-check", url]
    proc = subprocess.Popen(cmd)
    CdpLauncher().wait(LaunchPlan(cmd=cmd, env=None, port=port, note=""), proc)
    yield proc, port
    for child in psutil.Process(proc.pid).children(recursive=True):
        child.terminate()
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def content(chrome):
    proc, _ = chrome
    c = CdpContent()
    c.attach(WindowTarget(app_id="chrome-test", hwnd=None, pid=proc.pid, title="CDP test", process_name="chrome.exe"))
    yield c
    c.detach()


def ref_for(node):
    return Ref(id="t", app_id="chrome-test", locator=node.meta.locator, fingerprint=Fingerprint(role=node.role))


def node(content, **kw):
    tree = content.snapshot(None, 64)
    return next(n for n in tree.walk() if all(getattr(n, k) == v for k, v in kw.items()))


def test_port_is_found_and_loopback_only(chrome):
    proc, port = chrome
    assert find_port(proc.pid) == port
    assert set(listener_ips(port)) <= {"127.0.0.1", "::1"}
    ensure_loopback(port)


def test_snapshot_maps_the_page(content):
    tree = content.snapshot(None, 64)
    assert (tree.role, tree.name, tree.meta.adapter) == (Role.DOCUMENT, "CDP test", "cdp")
    names = {(n.role, n.name) for n in tree.walk()}
    assert {(Role.HEADING, "Hello"), (Role.BUTTON, "Run"), (Role.CHECKBOX, "I agree"), (Role.OPTION, "Green")} <= names
    assert "[ref=" in content.raw_dump()["yaml"]


def test_fill_click_check_select_and_keys(content):
    box = node(content, role=Role.TEXTBOX, name="Name")
    assert content.act(ref_for(box), Action(kind=ActionKind.SET_VALUE, value="Ada")).ok
    assert node(content, role=Role.TEXTBOX, name="Name").value == "Ada"

    run = content.act(ref_for(node(content, role=Role.BUTTON, name="Run")), Action(kind=ActionKind.CLICK))
    assert run.ok and run.strategy == "pattern" and "get_by_role(\"button\", name=\"Run\")" in run.native_call
    assert node(content, role=Role.HEADING, name="Clicked")

    agree = node(content, role=Role.CHECKBOX, name="I agree")
    assert content.act(ref_for(agree), Action(kind=ActionKind.CHECK)).ok
    assert State.CHECKED in node(content, role=Role.CHECKBOX, name="I agree").states
    again = content.act(ref_for(node(content, role=Role.CHECKBOX, name="I agree")), Action(kind=ActionKind.CHECK))
    assert again.native_call.startswith("Playwright no-op")

    green = node(content, role=Role.OPTION, name="Green")
    selected = content.act(ref_for(green), Action(kind=ActionKind.SELECT))
    assert selected.ok and "select_option(label='Green')" in selected.native_call
    assert State.SELECTED in node(content, role=Role.OPTION, name="Green").states

    keys = node(content, role=Role.TEXTBOX, name="Keys")
    assert content.act(ref_for(keys), Action(kind=ActionKind.PRESS_KEYS, value="Ctrl+A Enter")).ok
    assert node(content, role=Role.TEXTBOX, name="Keys").value == "Enter"


def test_page_level_keys_and_unsupported_actions(content):
    page = content.snapshot(None, 64)
    assert content.act(ref_for(page), Action(kind=ActionKind.PRESS_KEYS, value="Tab")).ok
    refused = content.act(ref_for(page), Action(kind=ActionKind.CLICK))
    assert not refused.ok and refused.strategy == "none" and "needs an element" in refused.error


def test_dry_run_changes_nothing(content):
    box = node(content, role=Role.TEXTBOX, name="Name")
    before = box.value
    result = content.act(ref_for(box), Action(kind=ActionKind.SET_VALUE, value="other"), dry_run=True)
    assert result.native_call.startswith("DRY-RUN: Playwright page.get_by_ref(")
    assert node(content, role=Role.TEXTBOX, name="Name").value == before


def test_unknown_locator_is_not_found(content):
    content.snapshot(None, 64)
    with pytest.raises(LocatorNotFound):
        content.read(Ref(id="t", app_id="x", locator="cdp:e9999", fingerprint=Fingerprint(role=Role.BUTTON)))


def test_app_without_debugging_port_is_unavailable():
    me = WindowTarget(app_id="py", pid=os.getpid(), title="python", process_name="python.exe", exe="python.exe")
    with pytest.raises(ContentUnavailable, match=r"without a debugging port .*with_cdp=true"):
        CdpContent().attach(me)
