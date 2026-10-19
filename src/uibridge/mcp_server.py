"""MCP server (SPEC §8): exposes the Router to agents over stdio. Every call is traced.

--debug   every response ends with `trace: <event id>` (the mcp_request event of that call)
--dry-run act resolves the element and logs the native call, but never executes it
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from uibridge import backends
from uibridge.core.model import Action, ActionKind, Node, Role, State
from uibridge.core.router import Router
from uibridge.core.tracer import Tracer

INSTRUCTIONS = """\
uibridge turns running Windows desktop apps into a compact text tree and runs actions on it. No screenshots.
Loop: list_apps -> attach(app_id) -> snapshot -> act(ref, action) -> snapshot again to verify the effect.
Refs like e12 identify elements for this session; always use refs from the latest snapshot. A ref that
fails as stale or ambiguous means: snapshot again and pick from the current refs.
Roles are ARIA-like: window dialog menubar menu menuitem toolbar statusbar tablist tab button link textbox
checkbox radio combobox listbox option list listitem tree treeitem table row cell grid slider text image
document group pane generic. Lines show only non-default states (disabled, focused, checked, expanded...).
`… N more (expand eX)` means children were cut: prefer expand(eX) over a larger snapshot depth."""

DRY_RUN_NOTE = "DRY-RUN: nothing executed. The native call below was resolved and logged only."


def _fmt_node(node: Node) -> str:
    m = node.meta
    b = node.bounds
    lines = [
        f"ref: {node.ref}",
        f"role: {node.role}" + (f" (native: {m.native_role})" if m.native_role else ""),
        f"name: {node.name!r}",
        f"value: {node.value!r}",
        f"description: {node.description!r}",
        "states: " + (", ".join(s.value for s in State if s in node.states) or "none"),
        "actions: " + (", ".join(a.value for a in node.actions) or "none (synthetic click may still work)"),
        f"children: {node.child_count}",
        f"bounds: {f'{b.x},{b.y} {b.width}x{b.height}' if b else 'none'}",
        f"automation_id: {m.automation_id!r}  class: {m.class_name!r}",
        f"adapter: {m.adapter}  framework: {m.framework}  locator: {m.locator}",
    ]
    if m.hwnd:
        lines.append(f"hwnd: {m.hwnd:#x}  control_id: {m.control_id}")
    if m.sources:
        lines.append("from other adapters: " + ", ".join(f"{f}={a}" for f, a in m.sources.items()))
    return "\n".join(lines)


def _one_line(node: Node) -> str:
    name = f' "{node.name}"' if node.name else ""
    return f"- {node.role}{name} [{node.ref}]"


class Bridge:
    """Server state: one Router (one trace session) per server process; one lock serializes UI work."""

    def __init__(self, debug: bool, dry_run: bool) -> None:
        traces = backends.traces_dir()
        self.router = Router(Tracer(traces, "mcp"), backends.factories(), traces / "_refs",
                             **backends.router_kwargs())
        self.debug = debug
        self.dry_run = dry_run
        self.lock = threading.Lock()

    def call(self, tool: str, args: dict, fn: Callable[[], str]) -> str:
        with self.lock:
            tracer = self.router.tracer
            event_id = tracer.emit("mcp_request", {"tool": tool, "args": args})
            suffix = f"\ntrace: {event_id}" if self.debug else ""
            try:
                text = fn()
            except ValidationError as e:
                message = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'action'}: {err['msg']}" for err in e.errors())
                tracer.emit("mcp_response", {"tool": tool, "error": message})
                raise ToolError(message + suffix) from e
            except Exception as e:
                message = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
                tracer.emit("mcp_response", {"tool": tool, "error": message, "type": type(e).__name__})
                raise ToolError(message + suffix) from e
            tracer.emit("mcp_response", {"tool": tool, "text": text})
            return text + suffix

    def ensure_attached(self, app_id: str) -> None:
        if app_id in self.router.apps:
            return
        target = next((w for w in backends.windows() if w.app_id == app_id), None)
        if target is None:
            raise KeyError(f"unknown app_id {app_id!r}; call list_apps to see the open windows")
        self.router.attach(target)


def build_server(debug: bool = False, dry_run: bool = False) -> MCPServer:
    bridge = Bridge(debug, dry_run)
    router = bridge.router
    @contextlib.asynccontextmanager
    async def lifespan(_server):
        try:
            yield {}
        finally:  # session end: disconnect CDP and close apps launched with a debugging port (rule 6)
            with bridge.lock:
                router.close()

    mcp = MCPServer("uibridge", instructions=INSTRUCTIONS + (f"\n{DRY_RUN_NOTE}" if dry_run else ""),
                    lifespan=lifespan)
    tool = mcp.tool

    @tool(structured_output=False, description="List open top-level windows: app_id, process, detected "
          "framework, adapter, title. Use an app_id with attach.")
    def list_apps() -> str:
        def run() -> str:
            lines = []
            for w in backends.windows():
                d = router.detect(w)
                adapter = backends.adapter_label(router, w, d)
                lines.append(f"{w.app_id}  {w.process_name}  {d.framework}  {adapter}  {w.title!r}")
            return "\n".join(lines) or "no windows"
        return bridge.call("list_apps", {}, run)

    @tool(structured_output=False, description="Start a program (name on PATH or full path) and attach to the "
          "window it opens. Returns the app_id and a depth-2 snapshot. with_cdp=true opens a debugging port on "
          "127.0.0.1 for Electron/WebView2 apps so their full web content becomes visible and actionable (the app is "
          "closed when this session ends; for an app that is already running, pass args to start a separate instance, "
          "e.g. VS Code ['--user-data-dir', '<new folder>']). run_as_invoker=true starts it without elevation (for "
          "programs like regedit that would otherwise ask for administrator rights).")
    def launch(path_or_name: str, args: list[str] | None = None, with_cdp: bool = False,
               run_as_invoker: bool = False) -> str:
        def run() -> str:
            attached = router.launch(path_or_name, args or [], backends.windows, run_as_invoker=run_as_invoker,
                                     with_cdp=with_cdp)
            app_id = attached.target.app_id
            return f"app_id: {app_id}\n" + router.snapshot(app_id, depth=2).view.text
        return bridge.call("launch", {"path_or_name": path_or_name, "args": args, "with_cdp": with_cdp,
                                      "run_as_invoker": run_as_invoker}, run)

    @tool(structured_output=False, description="Attach to a window by app_id (from list_apps). Returns its top "
          "two levels; use snapshot or expand to see more.")
    def attach(app_id: str) -> str:
        def run() -> str:
            bridge.ensure_attached(app_id)
            return router.snapshot(app_id, depth=2).view.text
        return bridge.call("attach", {"app_id": app_id}, run)

    @tool(structured_output=False, description="Current UI tree of an app as compact text with refs (eN). "
          "Pass ref to show only that subtree. Snapshot again after acting to verify the effect.")
    def snapshot(app_id: str, ref: str | None = None,
                 depth: Annotated[int, Field(ge=1, le=12)] = 4, include_offscreen: bool = False) -> str:
        def run() -> str:
            bridge.ensure_attached(app_id)
            return router.snapshot(app_id, ref=ref, depth=depth, include_offscreen=include_offscreen).view.text
        return bridge.call("snapshot", {"app_id": app_id, "ref": ref, "depth": depth,
                                        "include_offscreen": include_offscreen}, run)

    @tool(structured_output=False, description="Show the subtree under a ref, e.g. after `… N more (expand eX)`. "
          "depth = levels below the ref (1 = its direct children). Cheaper than a deeper full snapshot.")
    def expand(ref: str, depth: Annotated[int, Field(ge=1, le=12)] = 2) -> str:
        def run() -> str:  # render depth counts the ref's own line as a level
            return router.snapshot(router.app_of(ref), ref=ref, depth=depth + 1).view.text
        return bridge.call("expand", {"ref": ref, "depth": depth}, run)

    @tool(structured_output=False, description="Full detail of one element: value, all states, supported "
          "actions, bounds, automation id.")
    def read(ref: str) -> str:
        def run() -> str:
            return _fmt_node(router.read(router.app_of(ref), ref))
        return bridge.call("read", {"ref": ref}, run)

    @tool(structured_output=False, description="Perform an action on a ref. value is the text for "
          "set_value/type_text and the keys for press_keys (e.g. 'Ctrl+S', 'Enter'). Returns ok/FAILED, the "
          "strategy used (native_message / pattern / synthetic_input; fallbacks are listed) and which refs changed.")
    def act(ref: str, action: ActionKind, value: str | None = None) -> str:
        def run() -> str:
            app_id = router.app_of(ref)
            result = router.act(app_id, ref, Action(kind=action, value=value), dry_run=bridge.dry_run)
            lines = [DRY_RUN_NOTE] if bridge.dry_run else []
            lines.append(f"{'ok' if result.ok else 'FAILED'}  strategy={result.strategy}")
            lines.append(f"native: {result.native_call}")
            if result.error:
                lines.append(f"error: {result.error}")
            diff = router.last_diff
            if diff:
                for r, fields in diff.changed.items():
                    lines += [f"changed {r} {f}: {old!r} -> {new!r}" for f, (old, new) in fields.items()]
                if diff.new:
                    lines.append(f"added: {', '.join(diff.new)}")
                if diff.gone:
                    lines.append(f"removed: {', '.join(diff.gone)}")
                if not (diff.changed or diff.new or diff.gone):
                    lines.append("no visible change in the tree")
            text = "\n".join(lines)
            if not result.ok:
                raise RuntimeError(text)
            return text
        return bridge.call("act", {"ref": ref, "action": action, "value": value}, run)

    @tool(structured_output=False, description="Wait until an element with this role and/or name (substring, "
          "case-insensitive; also matches values) appears. Returns the match, or all matches if several.")
    def wait_for(app_id: str, role: Role | None = None, name: str | None = None,
                 timeout_s: Annotated[float, Field(ge=0, le=60)] = 10) -> str:
        def run() -> str:
            bridge.ensure_attached(app_id)
            found = router.wait_for(app_id, role=role, name=name, timeout_s=timeout_s)
            if len(found) == 1:
                return router.render_node(app_id, found[0]).text
            return f"{len(found)} matches:\n" + "\n".join(_one_line(n) for n in found)
        return bridge.call("wait_for", {"app_id": app_id, "role": role, "name": name, "timeout_s": timeout_s}, run)

    return mcp


def serve(debug: bool = False, dry_run: bool = False) -> None:
    build_server(debug=debug, dry_run=dry_run).run()
