"""CDP content provider for Electron and WebView2 apps, on Playwright connect_over_cdp.

- Launch: uibridge starts the app with --remote-debugging-port on a free port (Electron) or with
  WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS (WebView2), and refuses any listener not bound to loopback.
- Attach: the port is read from the app's process tree command lines, so later sessions find it too.
- Read: page.aria_snapshot(mode="ai") parsed by aria_parse. Act: page.get_by_ref(ref) locators.
  Playwright refs stay valid for elements that are still in the latest snapshot.
- Playwright's sync API is bound to the thread that started it; MCP tools run on worker threads,
  so every Playwright call goes through one dedicated thread here.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import queue
import re
import socket
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import psutil
import win32con
import win32gui
import win32process
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from uibridge.adapters.aria_parse import to_tree
from uibridge.adapters.winenum import electron_resources
from uibridge.core.adapter import ContentUnavailable, LaunchPlan, LocatorNotFound, WindowTarget
from uibridge.core.model import Action, ActionKind, ActionResult, Node, Ref, State

A = ActionKind
PORT_FLAG = re.compile(r"--remote-debugging-port=(\d+)")
LOOPBACK = {"127.0.0.1", "::1"}
ACTION_TIMEOUT_MS = 5000
_KEY_NAMES = {"ctrl": "Control", "control": "Control", "shift": "Shift", "alt": "Alt", "win": "Meta", "meta": "Meta",
              "cmd": "Meta", "esc": "Escape", "del": "Delete", "return": "Enter", "up": "ArrowUp", "down": "ArrowDown",
              "left": "ArrowLeft", "right": "ArrowRight"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def listener_ips(port: int) -> list[str]:
    return [c.laddr.ip for c in psutil.net_connections(kind="inet")
            if c.status == psutil.CONN_LISTEN and c.laddr.port == port]


def ensure_loopback(port: int) -> None:
    exposed = [ip for ip in listener_ips(port) if ip not in LOOPBACK]
    if exposed:
        raise PermissionError(f"debugging port {port} is listening on {exposed}; only 127.0.0.1 is allowed")


def find_port(pid: int) -> int | None:
    """--remote-debugging-port=N on the app process itself (Electron) or on its msedgewebview2.exe
    descendants (WebView2). Other descendants are ignored: a browser the app happened to start is not
    the app's own web content."""
    try:
        root = psutil.Process(pid)
        procs = [root, *(c for c in root.children(recursive=True) if c.name().lower() == "msedgewebview2.exe")]
    except psutil.Error:
        return None
    for proc in procs:
        try:
            for arg in proc.cmdline():
                m = PORT_FLAG.search(arg)
                if m:
                    return int(m.group(1))
        except psutil.Error:
            continue
    return None


def playwright_keys(keys: str) -> list[str]:
    """'Ctrl+Shift+P Enter' -> ['Control+Shift+P', 'Enter']."""
    return ["+".join(_KEY_NAMES.get(part.lower(), part) for part in combo.split("+")) for combo in keys.split()]


def _windows_of(pid: int) -> list[int]:
    hwnds: list[int] = []

    def collect(h: int, _: Any) -> bool:
        if win32process.GetWindowThreadProcessId(h)[1] == pid and win32gui.IsWindowVisible(h):
            hwnds.append(h)
        return True

    win32gui.EnumWindows(collect, None)
    return hwnds


class CdpLauncher:
    """Starts apps with a loopback-only debugging port and closes them again at session end."""

    def prepare(self, exe: str, args: list[str], env: dict[str, str] | None) -> LaunchPlan:
        port = free_port()
        if electron_resources(exe):
            return LaunchPlan(cmd=[exe, f"--remote-debugging-port={port}", *args], env=env, port=port, note="")
        webview_env = {**(env or os.environ), "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port}"}
        return LaunchPlan(cmd=[exe, *args], env=webview_env, port=port,
                          note=f", env WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port={port}")

    def wait(self, plan: LaunchPlan, proc: Any, timeout_s: float = 20.0) -> str:
        url = f"http://127.0.0.1:{plan.port}"
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{url}/json/version", timeout=1) as res:
                    json.load(res)
                ensure_loopback(plan.port)
                return url
            except PermissionError:
                self.close_app(proc.pid)
                raise
            except OSError:
                pass
            if proc.poll() is not None and not psutil.pid_exists(proc.pid):
                break
            time.sleep(0.5)
        exited = proc.poll() is not None
        raise RuntimeError(
            f"no debugging endpoint on 127.0.0.1:{plan.port} after {timeout_s:g}s"
            + (" and the launched process exited: the app is probably already running and this instance handed its "
               "arguments to it (so the CDP flag was ignored). Close the running app, or start a separate instance "
               "(Electron: add args ['--user-data-dir', '<new folder>'])." if exited else
               ": the app may not pass the flag to its web content (e.g. a launcher/backend starts the UI process).")
        )

    def close_app(self, pid: int) -> str:
        if not psutil.pid_exists(pid):
            return f"pid {pid} already exited"
        windows = _windows_of(pid)
        for h in windows:
            win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        try:
            proc = psutil.Process(pid)
            proc.wait(timeout=5)
            return f"WM_CLOSE to {len(windows)} window(s) of pid {pid}; exited"
        except psutil.TimeoutExpired:
            for child in proc.children(recursive=True):
                child.terminate()
            proc.terminate()
            return f"WM_CLOSE to {len(windows)} window(s) of pid {pid}; still running after 5 s, terminated"
        except psutil.NoSuchProcess:
            return f"WM_CLOSE to {len(windows)} window(s) of pid {pid}; exited"


class _Worker:
    """Runs callables on one dedicated thread (Playwright's sync API is thread-bound)."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="uibridge-playwright", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            fn, future = self._queue.get()
            if fn is None:
                future.set_result(None)
                return
            try:
                future.set_result(fn())
            except BaseException as e:  # noqa: BLE001 - delivered to the caller
                future.set_exception(e)

    def call(self, fn: Callable[[], Any], timeout: float = 60.0) -> Any:
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._queue.put((fn, future))
        return future.result(timeout)

    def stop(self) -> None:
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._queue.put((None, future))
        future.result(10)


class CdpContent:
    name = "cdp"
    host_classes = {"Chrome_RenderWidgetHostHWND"}

    def __init__(self) -> None:
        self._worker: _Worker | None = None
        self._pw = self._browser = self._page = None
        self.port: int | None = None
        self.hwnd: int | None = None
        self._index: dict[str, Node] = {}
        self._yaml = ""

    def owns(self, locator: str | None) -> bool:
        return bool(locator) and locator.startswith("cdp:")

    # -- lifecycle ---------------------------------------------------------------------------

    def attach(self, target: WindowTarget) -> None:
        port = find_port(target.pid)
        if port is None:
            exe = target.exe or target.process_name
            raise ContentUnavailable(
                f"{target.process_name} was started without a debugging port (CDP), so its web content is only "
                f"partly visible through UIA. For the full tree use launch('{exe}', with_cdp=true): close this one "
                f"first, or start a separate instance (VS Code: args ['--user-data-dir', '<new folder>'])")
        try:
            ensure_loopback(port)
        except PermissionError as e:
            raise ContentUnavailable(f"refusing CDP: {e}") from e
        self.port, self.hwnd = port, target.hwnd
        self._worker = _Worker()
        try:
            self._worker.call(self._connect)
        except Exception as e:
            self._worker.stop()
            raise ContentUnavailable(f"cannot connect to CDP on 127.0.0.1:{port}: {e}") from e

    def _connect(self) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}", is_local=True,
                                                           no_defaults=True, timeout=10_000)

    def detach(self) -> None:
        if self._worker is None:
            return

        def close() -> None:
            if self._browser is not None:
                self._browser.close()  # disconnects; the app keeps running
            if self._pw is not None:
                self._pw.stop()

        try:
            self._worker.call(close, timeout=15)
        finally:
            self._worker.stop()
            self._worker = None

    # -- reading -----------------------------------------------------------------------------

    def _current_page(self):
        pages = [p for c in self._browser.contexts for p in c.pages if not p.url.startswith("devtools://")]
        if len(pages) == 1:
            return pages[0]
        title = win32gui.GetWindowText(self.hwnd) if self.hwnd else ""
        matching = [p for p in pages if p.title() and p.title() == title] or \
                   [p for p in pages if p.title() and p.title() in title]
        if len(matching) == 1:
            return matching[0]
        raise LocatorNotFound(f"cannot tell which page belongs to window {title!r}: "
                              f"{[p.title() for p in pages]}")

    def _take(self) -> tuple[str, str, str]:
        page = self._page = self._current_page()
        return page.aria_snapshot(mode="ai", timeout=10_000), page.title(), page.url

    def snapshot(self, root: Ref | None, depth: int) -> Node:
        text, title, _ = self._worker.call(self._take)
        self._yaml = text
        tree = to_tree(text, title, adapter=self.name)
        self._index = {n.meta.locator: n for n in tree.walk()}
        if root is None:
            return tree
        node = self._index.get(root.locator)
        if node is None:
            raise LocatorNotFound(f"{root.locator} is not in the latest page snapshot")
        return node

    def read(self, ref: Ref) -> Node:
        node = self._index.get(ref.locator)
        if node is None:
            raise LocatorNotFound(f"{ref.locator} is not in the latest page snapshot")
        return node.model_copy(update={"children": []})

    def raw_dump(self) -> dict:
        text, title, url = self._worker.call(self._take)
        return {"port": self.port, "url": url, "title": title, "yaml": text}

    # -- acting ------------------------------------------------------------------------------

    def act(self, ref: Ref, action: Action, dry_run: bool = False) -> ActionResult:
        return self._worker.call(lambda: self._act(ref, action, dry_run))

    def _act(self, ref: Ref, action: Action, dry_run: bool) -> ActionResult:
        start = time.perf_counter()
        page = self._page or self._current_page()
        node = self._index.get(ref.locator)
        parts = (ref.locator or "cdp:page")[4:].split(">")
        head, chain = parts[0], parts[1:]

        def result(ok: bool, call: str, error: str | None = None, attempted: bool = True) -> ActionResult:
            return ActionResult(ok=ok, strategy="pattern" if attempted else "none", native_call=call, error=error,
                                duration_ms=(time.perf_counter() - start) * 1000)

        loc, expr = (None, "page") if head == "page" else (page.get_by_ref(head), f"page.get_by_ref({head!r})")
        for part in chain:
            role, _, name = part.partition(":")
            base = loc if loc is not None else page
            if role == "text":
                loc, expr = base.get_by_text(name, exact=True), f"{expr}.get_by_text({name!r}, exact=True)"
            else:
                loc = base.get_by_role(role, name=name or None, exact=True)
                expr += f".get_by_role({role!r}, name={name!r}, exact=True)"

        kind, value = action.kind, action.value
        plan = self._plan(page, loc, expr, kind, value, node, head, chain)
        if isinstance(plan, str):
            return result(False, f"{kind} on {ref.locator}", plan, attempted=False)
        call, run = plan
        if loc is not None and not chain:
            try:
                call += f"  [= {loc.normalize()}]"
            except PlaywrightError:
                pass
        if dry_run:
            return result(True, f"DRY-RUN: Playwright {call}")
        if chain and loc is not None:
            count = loc.count()
            if count != 1:
                return result(False, f"Playwright {call}", f"{count} elements match {expr}; take a new snapshot",
                              attempted=False)
        try:
            run()
        except PlaywrightError as e:
            return result(False, f"Playwright {call}", str(e).strip().splitlines()[0])
        return result(True, f"Playwright {call}")

    def _plan(self, page, loc, expr: str, kind: ActionKind, value: str | None, node: Node | None, head: str,
              chain: list[str]) -> tuple[str, Callable[[], Any]] | str:
        t = ACTION_TIMEOUT_MS
        if loc is None:  # the page itself: keyboard only
            if kind == A.PRESS_KEYS:
                keys = playwright_keys(value or "")
                return f"page.keyboard.press {keys}", lambda: [page.keyboard.press(k) for k in keys]
            if kind == A.TYPE_TEXT:
                return f"page.keyboard.type({value!r})", lambda: page.keyboard.type(value or "")
            return f"{kind} needs an element; on the page only press_keys and type_text work"
        states = node.states if node else set()
        if kind == A.SELECT and head != "page" and len(chain) == 1 and chain[0].startswith("option:"):
            select = page.get_by_ref(head)  # <option>s of a native <select> have no ref of their own
            label = chain[0].split(":", 1)[1]
            return (f"page.get_by_ref({head!r}).select_option(label={label!r})",
                    lambda: select.select_option(label=label, timeout=t))
        if kind in (A.EXPAND, A.COLLAPSE):
            want = State.EXPANDED if kind == A.EXPAND else State.COLLAPSED
            if want in states:
                return f"no-op: {expr} is already {want.value}", lambda: None
        if kind in (A.CHECK, A.UNCHECK):
            want = State.CHECKED if kind == A.CHECK else State.UNCHECKED
            if want in states:
                return f"no-op: {expr} is already {want.value}", lambda: None
        calls: dict[ActionKind, tuple[str, Callable[[], Any]]] = {
            A.CLICK: ("click()", lambda: loc.click(timeout=t)),
            A.DOUBLE_CLICK: ("dblclick()", lambda: loc.dblclick(timeout=t)),
            A.RIGHT_CLICK: ("click(button='right')", lambda: loc.click(button="right", timeout=t)),
            A.FOCUS: ("focus()", lambda: loc.focus(timeout=t)),
            A.SET_VALUE: (f"fill({value!r})", lambda: loc.fill(value or "", timeout=t)),
            A.TYPE_TEXT: (f"press_sequentially({value!r})", lambda: loc.press_sequentially(value or "", timeout=t)),
            A.TOGGLE: ("click()", lambda: loc.click(timeout=t)),
            A.CHECK: ("check()", lambda: loc.check(timeout=t)),
            A.UNCHECK: ("uncheck()", lambda: loc.uncheck(timeout=t)),
            A.SELECT: ("click()", lambda: loc.click(timeout=t)),
            A.EXPAND: ("click()", lambda: loc.click(timeout=t)),
            A.COLLAPSE: ("click()", lambda: loc.click(timeout=t)),
            A.SCROLL_INTO_VIEW: ("scroll_into_view_if_needed()", lambda: loc.scroll_into_view_if_needed(timeout=t)),
        }
        if kind == A.PRESS_KEYS:
            keys = playwright_keys(value or "")
            return f"{expr}.press {keys}", lambda: [loc.press(k, timeout=t) for k in keys]
        if kind not in calls:
            return f"{kind} is not supported on web content"
        call, run = calls[kind]
        return f"{expr}.{call}", run
