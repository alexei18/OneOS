"""Router: sends snapshot/read/act to the adapter chosen for each window, through refs and the tracer.

Every read and action leaves trace events (CLAUDE.md rule 4):
  snapshot: snapshot_raw -> snapshot_normalized -> snapshot_ai_view
  act:      action_request -> snapshot_normalized (pre) -> action_resolution -> action_native
            -> snapshot_normalized (post) -> post_action_diff -> action_result
Refs are persisted per app in state_dir so separate processes (CLI calls) share them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from uibridge.core.adapter import FULL_DEPTH, Adapter, WindowTarget
from uibridge.core.detector import Detection, detect
from uibridge.core.model import Action, ActionResult, Node
from uibridge.core.refs import AssignStats, RefRegistry
from uibridge.core.serializer import AIView, app_header, render
from uibridge.core.tracer import Tracer

SETTLE_S = 0.3  # let the UI update before the post-action snapshot


@dataclass
class Attached:
    target: WindowTarget
    detection: Detection
    adapter: Adapter


@dataclass
class SnapshotResult:
    view: AIView
    tree: Node
    raw: dict


class Router:
    def __init__(self, tracer: Tracer, adapters: dict[str, Callable[[], Adapter]], state_dir: Path | str,
                 settle_s: float = SETTLE_S) -> None:
        self.tracer = tracer
        self.adapters = adapters
        self.state_dir = Path(state_dir)
        self.settle_s = settle_s
        self.registry = RefRegistry(tracer)
        self.apps: dict[str, Attached] = {}
        self.last_diff: AssignStats | None = None

    def detect(self, target: WindowTarget) -> Detection:
        detection = detect(target, set(self.adapters))
        self.tracer.emit(
            "detect",
            {"target": target.model_dump(), **detection.model_dump()},
            app_id=target.app_id,
        )
        return detection

    def attach(self, target: WindowTarget) -> Attached:
        detection = self.detect(target)
        adapter = self.adapters[detection.chosen]()
        try:
            adapter.attach(target)
        except Exception as e:
            self.tracer.error(e, {"op": "attach", "adapter": detection.chosen}, app_id=target.app_id)
            raise
        self.registry.load(self._state_path(target.app_id), target.app_id, target.pid)
        attached = Attached(target, detection, adapter)
        self.apps[target.app_id] = attached
        return attached

    def snapshot(self, app_id: str, ref: str | None = None, depth: int = 4,
                 include_offscreen: bool = False) -> SnapshotResult:
        app = self.apps[app_id]
        try:
            root = self.registry.resolve(ref, app.adapter)[0] if ref else None
            raw = app.adapter.raw_dump(root, FULL_DEPTH)
            self.tracer.emit("snapshot_raw", {"root_ref": ref, "adapter": app.adapter.name, "raw": raw},
                             app_id=app_id)
            tree, _ = self._snapshot_tree(app, root_ref=ref)
            header = None if ref else app_header(app.target.title, app.detection.framework, app.target.pid)
            view = render(tree, self.tracer, app_id=app_id, header=header, depth=depth,
                          include_offscreen=include_offscreen)
        except Exception as e:
            self.tracer.error(e, {"op": "snapshot", "ref": ref, "depth": depth}, app_id=app_id)
            raise
        self._save(app)
        return SnapshotResult(view=view, tree=tree, raw=raw)

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
            self._snapshot_tree(app)  # fresh baseline: refs current, diff measures only this action
            resolved, _ = self.registry.resolve(ref, app.adapter)
            result = app.adapter.act(resolved, action, dry_run=dry_run)
            self.tracer.emit("action_native", {"ref": ref, "locator": resolved.locator, "strategy": result.strategy,
                                               "native_call": result.native_call, "dry_run": dry_run},
                             app_id=app_id)
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

    def _snapshot_tree(self, app: Attached, root_ref: str | None = None) -> tuple[Node, AssignStats]:
        root = self.registry.get(root_ref) if root_ref else None
        tree = app.adapter.snapshot(root, FULL_DEPTH)
        for node in tree.walk():
            node.meta.framework = app.detection.framework
        stats = self.registry.assign(tree, app.target.app_id, root_ref=root_ref)
        return tree, stats

    def _state_path(self, app_id: str) -> Path:
        return self.state_dir / f"{app_id}.json"

    def _save(self, app: Attached) -> None:
        self.registry.save(self._state_path(app.target.app_id), app.target.app_id, app.target.pid)
