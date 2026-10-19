"""Router: sends snapshot/read/act to the adapter chosen for each window, through refs and the tracer.

Every read and action leaves trace events (CLAUDE.md rule 4):
  snapshot: snapshot_raw -> snapshot_normalized -> snapshot_ai_view
  act:      action_request -> snapshot_normalized (pre) -> action_resolution -> action_native
            -> snapshot_normalized (post) -> post_action_diff -> action_result
Refs are persisted per app in state_dir so separate processes (CLI calls) share them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from uibridge.core.adapter import (
    FULL_DEPTH,
    Adapter,
    ContentProvider,
    ContentUnavailable,
    Declined,
    Enricher,
    Launcher,
    WindowTarget,
)
from uibridge.core.compose import ComposedAdapter
from uibridge.core.detector import FALLBACK, Detection, detect
from uibridge.core.model import Action, ActionResult, Node, Ref
from uibridge.core.refs import AssignStats, RefRegistry
from uibridge.core.serializer import AIView, app_header, render
from uibridge.core.tracer import Tracer

SETTLE_S = 0.3  # let the UI update before the post-action snapshot


@dataclass
class Attached:
    target: WindowTarget
    detection: Detection
    adapter: Adapter
    enricher: Enricher | None = None  # e.g. win32 facts + native messages on top of the base_uia tree
    notice: str | None = None  # shown in the snapshot header, e.g. "started without CDP"


@dataclass
class SnapshotResult:
    view: AIView
    tree: Node
    raw: dict


class Router:
    def __init__(self, tracer: Tracer, adapters: dict[str, Callable[[WindowTarget], Adapter]], state_dir: Path | str,
                 settle_s: float = SETTLE_S,
                 enrichers: dict[str, Callable[[WindowTarget], Enricher]] | None = None,
                 contents: dict[str, Callable[[WindowTarget], ContentProvider]] | None = None,
                 launcher: Launcher | None = None) -> None:
        self.tracer = tracer
        self.adapters = adapters
        self.enrichers = enrichers or {}
        self.contents = contents or {}
        self.launcher = launcher
        self.launched_with_cdp: list[int] = []  # pids this session started with a debugging port
        self.state_dir = Path(state_dir)
        self.settle_s = settle_s
        self.registry = RefRegistry(tracer)
        self.apps: dict[str, Attached] = {}
        self.last_diff: AssignStats | None = None

    def detect(self, target: WindowTarget) -> Detection:
        detection = self._detect(target)
        self._emit_detect(target, detection)
        return detection

    def _detect(self, target: WindowTarget) -> Detection:
        detection = detect(target, set(self.adapters) | set(self.enrichers) | set(self.contents))
        if detection.chosen in self.enrichers:
            detection.why += f" (base_uia tree + {detection.chosen} enrichment)"
        if detection.chosen in self.contents:
            detection.why += f" (base_uia frame + {detection.chosen} content)"
        return detection

    def _emit_detect(self, target: WindowTarget, detection: Detection) -> None:
        self.tracer.emit(
            "detect",
            {"target": target.model_dump(), **detection.model_dump()},
            app_id=target.app_id,
        )

    def attach(self, target: WindowTarget) -> Attached:
        detection = self._detect(target)
        layered = detection.chosen in self.enrichers or detection.chosen in self.contents
        base = self.adapters[FALLBACK if layered else detection.chosen](target)
        enricher = self.enrichers[detection.chosen](target) if detection.chosen in self.enrichers else None
        adapter: Adapter = base
        notice = None
        try:
            if detection.chosen in self.contents:
                composed = ComposedAdapter(base, self.contents[detection.chosen](target))
                try:
                    composed.attach(target)
                    adapter = composed
                except ContentUnavailable as e:
                    notice = str(e)
                    detection.why += f"; {detection.chosen} unavailable, using {FALLBACK} only: {e}"
                    detection.chosen = FALLBACK  # the base adapter is already attached
            else:
                base.attach(target)
            if enricher:
                enricher.attach(target)
        except Exception as e:
            self._emit_detect(target, detection)
            self.tracer.error(e, {"op": "attach", "adapter": detection.chosen}, app_id=target.app_id)
            raise
        self._emit_detect(target, detection)
        self.registry.load(self._state_path(target.app_id), target.app_id, target.pid)
        attached = Attached(target, detection, adapter, enricher, notice)
        self.apps[target.app_id] = attached
        return attached

    def snapshot(self, app_id: str, ref: str | None = None, depth: int = 4,
                 include_offscreen: bool = False) -> SnapshotResult:
        app = self.apps[app_id]
        try:
            root = self.registry.resolve(ref, app.adapter)[0] if ref else None
            raw = app.adapter.raw_dump(root, FULL_DEPTH)
            if app.enricher:
                raw = {**raw, app.enricher.name: app.enricher.raw_dump()}
            self.tracer.emit("snapshot_raw", {"root_ref": ref, "adapter": app.adapter.name, "raw": raw},
                             app_id=app_id)
            tree, _ = self._snapshot_tree(app, root_ref=ref)
            header = None
            if ref is None:
                header = app_header(app.target.title, app.detection.framework, app.target.pid)
                if app.notice:
                    header += f" ({app.notice})"
                if (raw.get("window_state") or {}).get("minimized"):
                    header += " minimized"
                    if tree.child_count == 0:
                        header += " (this app exposes no content while minimized; restore its window, then snapshot)"
            view = render(tree, self.tracer, app_id=app_id, header=header, depth=depth,
                          include_offscreen=include_offscreen)
        except Exception as e:
            self.tracer.error(e, {"op": "snapshot", "ref": ref, "depth": depth}, app_id=app_id)
            raise
        self._save(app)
        return SnapshotResult(view=view, tree=tree, raw=raw)

    def app_of(self, ref: str) -> str:
        """Refs are session-scoped; find which attached app a ref belongs to."""
        app_id = self.registry.get(ref).app_id
        if app_id not in self.apps:
            raise KeyError(f"ref {ref} belongs to {app_id}, which is not attached; call attach({app_id!r})")
        return app_id

    def wait_for(self, app_id: str, role: str | None = None, name: str | None = None, timeout_s: float = 10.0,
                 poll_s: float = 0.5) -> list[Node]:
        """Poll full snapshots until nodes match (role equal, name/value contains `name`, case-insensitive).
        Returns every match of the first snapshot that has any; raises TimeoutError otherwise."""
        if role is None and name is None:
            raise ValueError("wait_for needs a role, a name, or both")
        app = self.apps[app_id]
        needle = (name or "").lower()

        def matches(n: Node) -> bool:
            if role is not None and n.role != role:
                return False
            return not needle or needle in (n.name or "").lower() or needle in (n.value or "").lower()

        deadline = time.monotonic() + timeout_s
        try:
            while True:
                tree, _ = self._snapshot_tree(app)
                found = [n for n in tree.walk() if matches(n)]
                self._save(app)
                if found:
                    return found
                if time.monotonic() >= deadline:
                    seen = sorted({n.name for n in tree.walk() if n.name and (role is None or n.role == role)})
                    raise TimeoutError(
                        f"nothing matched role={role!r} name={name!r} after {timeout_s:g}s; "
                        f"{role or 'node'} names seen: {', '.join(repr(s) for s in seen[:15]) or 'none'}"
                    )
                time.sleep(poll_s)
        except Exception as e:
            self.tracer.error(e, {"op": "wait_for", "role": role, "name": name}, app_id=app_id)
            raise

    def render_node(self, app_id: str, node: Node, depth: int = 2) -> AIView:
        return render(node, self.tracer, app_id=app_id, depth=depth)

    def launch(self, path_or_name: str, args: list[str], windows: Callable[[], list[WindowTarget]],
               timeout_s: float = 15.0, run_as_invoker: bool = False, with_cdp: bool = False) -> Attached:
        """Start a program and attach to the window it opens. UWP launchers (calc.exe) hand off to
        another process, so a single new window from any process is accepted. with_cdp opens a
        debugging port on 127.0.0.1 (via the launcher) and the CDP content provider attaches to it."""
        exe = shutil.which(path_or_name) or path_or_name
        before = {w.app_id for w in windows()}
        cmd = [exe, *args]
        env = {**os.environ, "__COMPAT_LAYER": "RunAsInvoker"} if run_as_invoker else None
        env_note = ", env __COMPAT_LAYER=RunAsInvoker" if run_as_invoker else ""
        plan = None
        self.tracer.emit("action_request", {"launch": path_or_name, "args": args, "run_as_invoker": run_as_invoker,
                                            "with_cdp": with_cdp})
        if with_cdp:
            if self.launcher is None:
                raise RuntimeError("with_cdp needs the CDP launcher, which is not configured")
            plan = self.launcher.prepare(exe, args, env)
            cmd, env, env_note = plan.cmd, plan.env, env_note + plan.note
        try:
            proc = subprocess.Popen(cmd, env=env)
        except OSError as e:
            self.tracer.error(e, {"op": "launch", "cmd": cmd})
            raise FileNotFoundError(f"cannot start {path_or_name!r}: {e}; pass a full path") from e
        self.tracer.emit("action_native", {"strategy": "process", "native_call": f"subprocess.Popen({cmd!r}{env_note})",
                                           "pid": proc.pid})
        if plan is not None:
            try:
                endpoint = self.launcher.wait(plan, proc)
            except Exception as e:
                self.tracer.error(e, {"op": "launch", "cmd": cmd, "with_cdp": True})
                raise
            self.launched_with_cdp.append(proc.pid)
            self.tracer.emit("action_result", {"ok": True, "strategy": "process", "native_call": f"CDP endpoint {endpoint}",
                                               "error": None, "duration_ms": 0})
        deadline = time.monotonic() + timeout_s
        new: list[WindowTarget] = []
        while time.monotonic() < deadline:
            time.sleep(0.5)
            new = [w for w in windows() if w.app_id not in before]
            own = [w for w in new if w.pid == proc.pid]
            if len(own) == 1 or (not own and len(new) == 1):
                return self.attach((own or new)[0])
        listed = "; ".join(f"{w.app_id} {w.process_name} {w.title!r}" for w in new)
        error = RuntimeError(
            f"started {path_or_name!r} (pid {proc.pid}) but found {len(new)} new windows in {timeout_s:g}s"
            + (f": {listed}; attach one of these app_ids" if new else "; call list_apps and attach it")
        )
        self.tracer.error(error, {"op": "launch", "cmd": cmd})
        raise error

    def close(self) -> None:
        """End of session: detach every adapter and close the apps this session launched with a
        debugging port, so no CDP endpoint outlives the session (CLAUDE.md rule 6)."""
        for app_id, app in list(self.apps.items()):
            try:
                app.adapter.detach()
            except Exception as e:
                self.tracer.error(e, {"op": "close", "detach": app.adapter.name}, app_id=app_id)
        self.apps.clear()
        for pid in self.launched_with_cdp:
            try:
                call = self.launcher.close_app(pid)
                self.tracer.emit("action_native", {"strategy": "process", "native_call": call, "pid": pid,
                                                   "reason": "session end: close app launched with CDP"})
            except Exception as e:
                self.tracer.error(e, {"op": "close", "pid": pid})
        self.launched_with_cdp.clear()

    def read(self, app_id: str, ref: str) -> Node:
        app = self.apps[app_id]
        try:
            return self.registry.resolve(ref, app.adapter)[1]
        except Exception as e:
            self.tracer.error(e, {"op": "read", "ref": ref}, app_id=app_id)
            raise

    def act(self, app_id: str, ref: str, action: Action, dry_run: bool = False) -> ActionResult:
        app = self.apps[app_id]
        self.tracer.emit("action_request", {"ref": ref, "action": action.model_dump(mode="json"),
                                            "dry_run": dry_run}, app_id=app_id)
        try:
            pre_tree, _ = self._snapshot_tree(app)  # fresh baseline: refs current, diff measures only this action
            resolved, _ = self.registry.resolve(ref, app.adapter)
            result, fallbacks = self._act_composed(app, pre_tree, resolved, action, dry_run)
            self.tracer.emit("action_native", {"ref": ref, "locator": resolved.locator, "strategy": result.strategy,
                                               "native_call": result.native_call, "dry_run": dry_run,
                                               "fallbacks": fallbacks}, app_id=app_id)
            self.last_diff = None
            if result.ok and not dry_run:
                time.sleep(self.settle_s)
                _, diff = self._snapshot_tree(app)
                self.last_diff = diff
                result.changed_refs = sorted({*diff.changed, *diff.new, *diff.gone}, key=lambda r: int(r[1:]))
                self.tracer.emit("post_action_diff", {"ref": ref, "added": diff.new, "removed": diff.gone,
                                                      "changed": diff.changed}, app_id=app_id)
            self.tracer.emit("action_result", result.model_dump(mode="json"), app_id=app_id)
        except Exception as e:
            self.tracer.error(e, {"op": "act", "ref": ref, "action": action.model_dump(mode="json")},
                              app_id=app_id)
            raise
        finally:
            self._save(app)
        return result

    # -- internals --------------------------------------------------------------------------

    def _act_composed(self, app: Attached, pre_tree: Node, ref: Ref, action: Action,
                      dry_run: bool) -> tuple[ActionResult, list[str]]:
        """Enricher first (native messages, e.g. BM_CLICK), then the base adapter (pattern, then synthetic).
        Every step that did not do the action is recorded with its reason."""
        fallbacks: list[str] = []
        if app.enricher is not None:
            name = app.enricher.name
            try:
                outcome = app.enricher.act(pre_tree, ref.id, action, dry_run=dry_run)
            except Exception as e:
                self.tracer.error(e, {"op": "act", "enricher": name, "ref": ref.id}, app_id=app.target.app_id)
                fallbacks.append(f"{name} failed: {type(e).__name__}: {e}")
            else:
                if isinstance(outcome, Declined):
                    fallbacks.append(f"{name} declined: {outcome.reason}")
                elif outcome.ok:
                    return outcome, fallbacks
                else:
                    fallbacks.append(f"{name} failed: {outcome.error} ({outcome.native_call})")
        result = app.adapter.act(ref, action, dry_run=dry_run)
        if fallbacks:
            prefix = "; ".join(fallbacks)
            if result.native_call.startswith("DRY-RUN: "):
                result.native_call = f"DRY-RUN: {prefix}; {result.native_call[9:]}"
            else:
                result.native_call = f"{prefix}; {result.native_call}"
        return result, fallbacks

    def _snapshot_tree(self, app: Attached, root_ref: str | None = None) -> tuple[Node, AssignStats]:
        root = self.registry.get(root_ref) if root_ref else None
        tree = app.adapter.snapshot(root, FULL_DEPTH)
        if app.enricher is not None:
            app.enricher.enrich(tree)
        for node in tree.walk():
            node.meta.framework = app.detection.framework
        stats = self.registry.assign(tree, app.target.app_id, root_ref=root_ref)
        return tree, stats

    def _state_path(self, app_id: str) -> Path:
        return self.state_dir / f"{app_id}.json"

    def _save(self, app: Attached) -> None:
        self.registry.save(self._state_path(app.target.app_id), app.target.app_id, app.target.pid)
