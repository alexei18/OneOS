"""Session refs (SPEC §4): short ids that survive re-snapshots and re-find elements.

Matching a new snapshot against known refs, top-down, one sibling list at a time:
  1. exact fingerprint (role, name, automation_id, class_name, index_in_parent, ancestors);
  2. under the same (already matched) parent ref, siblings sharing a local key
     (role + automation_id if set, else role + name; + class_name, see model.identity_key)
     are paired in order with the old unclaimed refs
     of that key, only if both groups have the same size. Keeps refs when other siblings
     are inserted or removed, including identical unnamed wrappers (common in UIA trees).
  3. a renamed container (it has children, on both sides) keeps its ref when it is the only
     unmatched container of its role + class under that parent; leaves never match this way.
The root of a full snapshot reuses the app's previous root ref when role, automation_id and
class_name match, so a window title change (e.g. gaining "*") keeps every ref below it.
Otherwise the node gets a new ref. Groups of different sizes never match: we do not guess.

Known limit: identical siblings are told apart only by order, so a reorder among them
binds refs by position.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from uibridge.core.adapter import FULL_DEPTH, Adapter, LocatorNotFound
from uibridge.core.model import Fingerprint, Node, Ref, State
from uibridge.core.tracer import Tracer


class RefResolutionError(Exception):
    def __init__(self, message: str, ref: str, candidates: list[dict] | None = None) -> None:
        super().__init__(message)
        self.ref = ref
        self.candidates = candidates or []


class UnknownRefError(RefResolutionError):
    pass


class StaleRefError(RefResolutionError):
    pass


class AmbiguousRefError(RefResolutionError):
    pass


@dataclass
class _Entry:
    id: str
    app_id: str
    fingerprint: Fingerprint
    locator: str | None
    parent: str | None
    observed: dict = field(default_factory=dict)  # last seen name/value/states, for diffs

    def to_ref(self) -> Ref:
        return Ref(id=self.id, app_id=self.app_id, locator=self.locator, fingerprint=self.fingerprint)

    def to_json(self) -> dict:
        return {"id": self.id, "app_id": self.app_id, "fingerprint": self.fingerprint.model_dump(mode="json"),
                "locator": self.locator, "parent": self.parent, "observed": self.observed}

    @classmethod
    def from_json(cls, d: dict) -> _Entry:
        return cls(d["id"], d["app_id"], Fingerprint.model_validate(d["fingerprint"]), d["locator"],
                   d["parent"], d["observed"])


def _observe(node: Node) -> dict:
    return {"name": node.name, "value": node.value, "states": [s.value for s in State if s in node.states]}


@dataclass
class AssignStats:
    new: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    changed: dict[str, dict[str, list]] = field(default_factory=dict)  # ref -> field -> [old, new]


def _describe(node: Node) -> dict:
    fp = node.meta.fingerprint
    path = " > ".join(f"{r} {n!r}" if n else r for r, n in fp.ancestors) if fp else ""
    return {"ref": node.ref, "role": node.role, "name": node.name, "locator": node.meta.locator, "path": path}


class RefRegistry:
    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer
        self._entries: dict[str, _Entry] = {}
        self._live: dict[str, set[str]] = {}  # app_id -> refs in last full snapshot
        self._roots: dict[str, str] = {}  # app_id -> root ref of last full snapshot
        self._next = 1

    def save(self, path: Path, app_id: str, pid: int) -> None:
        """Persist one app's refs so a later process (e.g. the next CLI call) can resolve them."""
        state = {
            "app_id": app_id,
            "pid": pid,
            "next": self._next,
            "root": self._roots.get(app_id),
            "live": sorted(self._live.get(app_id, set())),
            "entries": [e.to_json() for e in self._entries.values() if e.app_id == app_id],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def load(self, path: Path, app_id: str, pid: int) -> bool:
        """Load refs saved by `save`. Returns False (and loads nothing) if absent or from another process."""
        if not path.exists():
            return False
        state = json.loads(path.read_text(encoding="utf-8"))
        if state["app_id"] != app_id or state["pid"] != pid:
            return False
        for d in state["entries"]:
            self._entries[d["id"]] = _Entry.from_json(d)
        self._next = max(self._next, state["next"])
        if state["root"]:
            self._roots[app_id] = state["root"]
        self._live[app_id] = set(state["live"])
        return True

    def get(self, ref_id: str) -> Ref:
        entry = self._entries.get(ref_id)
        if entry is None:
            raise UnknownRefError(f"unknown ref {ref_id!r}; run snapshot to get current refs", ref_id)
        return entry.to_ref()

    def assign(self, tree: Node, app_id: str, root_ref: str | None = None) -> AssignStats:
        """Give every node in `tree` a ref and fingerprint, reusing known refs where they match."""
        if root_ref is not None:
            base = self._entries[root_ref]
            ancestors, index, parent = base.fingerprint.ancestors, base.fingerprint.index_in_parent, base.parent
        else:
            ancestors, index, parent = (), 0, None

        by_exact: dict[tuple, list[str]] = {}
        by_parent_local: dict[tuple, list[str]] = {}
        by_parent: dict[str | None, list[str]] = {}
        for e in self._entries.values():
            if e.app_id == app_id:
                by_exact.setdefault(e.fingerprint.key(), []).append(e.id)
                by_parent_local.setdefault((e.parent, e.fingerprint.local_key()), []).append(e.id)
                by_parent.setdefault(e.parent, []).append(e.id)

        claimed: set[str] = set()
        stats = AssignStats()

        def unique_unclaimed(ids: list[str] | None) -> str | None:
            free = [i for i in ids or [] if i not in claimed]
            return free[0] if len(free) == 1 else None

        def fingerprint(node: Node, ancestors: tuple, index: int) -> Fingerprint:
            return Fingerprint(
                role=node.role,
                name=node.name,
                automation_id=node.meta.automation_id,
                class_name=node.meta.class_name,
                index_in_parent=index,
                ancestors=ancestors,
            )

        def bind(node: Node, fp: Fingerprint, ref: str | None, parent: str | None) -> None:
            if ref is None:
                ref = f"e{self._next}"
                self._next += 1
                stats.new.append(ref)
            else:
                stats.reused.append(ref)
                before, now = self._entries[ref].observed, _observe(node)
                diff = {k: [before.get(k), v] for k, v in now.items() if before.get(k) != v}
                if diff:
                    stats.changed[ref] = diff
            claimed.add(ref)
            self._entries[ref] = _Entry(ref, app_id, fp, node.meta.locator, parent, _observe(node))
            node.ref = ref
            node.meta.fingerprint = fp
            match_children(node, (*fp.ancestors, (str(node.role), node.name)))

        def match_children(node: Node, ancestors: tuple) -> None:
            fps = [fingerprint(c, ancestors, i) for i, c in enumerate(node.children)]
            refs: list[str | None] = []
            for fp in fps:  # rule 1: exact fingerprint
                ref = unique_unclaimed(by_exact.get(fp.key()))
                if ref:
                    claimed.add(ref)
                refs.append(ref)
            groups: dict[tuple, list[int]] = {}
            for i, fp in enumerate(fps):
                if refs[i] is None:
                    groups.setdefault(fp.local_key(), []).append(i)
            for key, idxs in groups.items():  # rule 2: same parent + local key, equal-size groups
                old = sorted(
                    (r for r in by_parent_local.get((node.ref, key), []) if r not in claimed),
                    key=lambda r: self._entries[r].fingerprint.index_in_parent,
                )
                if len(old) == len(idxs):
                    for i, ref in zip(idxs, old):
                        refs[i] = ref
                        claimed.add(ref)
            # rule 3: a renamed *container* (no automation id) is still the same element when it is the only
            # unmatched container child of this parent with its role + class, on both sides (e.g. a pane or a
            # page named after the window title). Leaves never match this way: a removed button and an
            # inserted one must not be paired just because they are the last ones left.
            by_kind: dict[tuple, list[int]] = {}
            for i, fp in enumerate(fps):
                if refs[i] is None and node.children[i].children:
                    by_kind.setdefault((fp.role, fp.class_name), []).append(i)
            for (role, cls), idxs in by_kind.items():
                old = [r for r in by_parent.get(node.ref, []) if r not in claimed and r in by_parent
                       and (self._entries[r].fingerprint.role, self._entries[r].fingerprint.class_name) == (role, cls)]
                if len(idxs) == 1 and len(old) == 1:
                    refs[idxs[0]] = old[0]
                    claimed.add(old[0])
            for child, fp, ref in zip(node.children, fps, refs):
                bind(child, fp, ref, node.ref)

        root_fp = fingerprint(tree, ancestors, index)
        if root_ref is not None:
            root = root_ref  # caller resolved this element already
        else:
            root = unique_unclaimed(by_exact.get(root_fp.key()))
            prev = self._entries.get(self._roots.get(app_id, ""))
            if root is None and prev is not None:
                old = prev.fingerprint
                if (old.role, old.automation_id, old.class_name) == (root_fp.role, root_fp.automation_id,
                                                                     root_fp.class_name):
                    root = prev.id
        bind(tree, root_fp, root, parent)

        if root_ref is None:
            stats.gone = sorted(self._live.get(app_id, set()) - claimed, key=lambda r: int(r[1:]))
            self._live[app_id] = claimed
            self._roots[app_id] = tree.ref

        self.tracer.emit(
            "snapshot_normalized",
            {
                "root_ref": root_ref,
                "refs": {"new": stats.new, "reused": stats.reused, "gone": stats.gone},
                "counts": {"new": len(stats.new), "reused": len(stats.reused), "gone": len(stats.gone),
                           "changed": len(stats.changed)},
                "changed": stats.changed,
                "tree": tree.model_dump(mode="json"),
            },
            app_id=app_id,
        )
        return stats

    def resolve(self, ref_id: str, adapter: Adapter) -> tuple[Ref, Node]:
        """Locator first; if stale, re-snapshot and match by fingerprint; else fail explicitly."""
        try:
            ref = self.get(ref_id)
        except UnknownRefError as e:
            self.tracer.emit("action_resolution", {"ref": ref_id, "ok": False, "error": str(e)})
            raise

        try:
            node = adapter.read(ref)
            if node.local_key() == ref.fingerprint.local_key():
                node.ref = ref_id
                node.meta.fingerprint = ref.fingerprint
                self._emit_found(ref, node, via="locator")
                return ref, node
            reason = f"locator {ref.locator!r} now points to {node.role} {node.name!r}"
        except LocatorNotFound as e:
            reason = str(e)

        tree = adapter.snapshot(None, FULL_DEPTH)
        self.assign(tree, ref.app_id)
        for node in tree.walk():
            if node.ref == ref_id:
                fresh = self.get(ref_id)
                self._emit_found(fresh, node, via="fingerprint", reason=reason)
                return fresh, node

        local = ref.fingerprint.local_key()
        candidates = [_describe(n) for n in tree.walk() if n.local_key() == local]
        target = f"{ref.fingerprint.role} {ref.fingerprint.name!r}"
        listed = "; ".join(f"{c['ref']} {c['role']} {c['name']!r} ({c['path']})" for c in candidates)
        if len(candidates) >= 2:
            err: RefResolutionError = AmbiguousRefError(
                f"ref {ref_id} ({target}) is ambiguous after the UI changed: "
                f"{len(candidates)} candidates: {listed}. Pick one of these refs.",
                ref_id,
                candidates,
            )
        else:
            hint = f" Similar element: {listed}." if candidates else ""
            err = StaleRefError(
                f"ref {ref_id} ({target}) is stale: {reason}.{hint} Re-run snapshot.", ref_id, candidates
            )
        self.tracer.emit(
            "action_resolution",
            {"ref": ref_id, "locator": ref.locator, "ok": False, "reason": reason,
             "error": str(err), "candidates": candidates},
            app_id=ref.app_id,
        )
        raise err

    def _emit_found(self, ref: Ref, node: Node, via: str, reason: str | None = None) -> None:
        self.tracer.emit(
            "action_resolution",
            {"ref": ref.id, "locator": ref.locator, "ok": True, "via": via, "reason": reason,
             "found": _describe(node)},
            app_id=ref.app_id,
        )
