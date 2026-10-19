"""Adapter protocol (SPEC §7). Adapters return Node / ActionResult, never native objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from uibridge.core.model import Action, ActionResult, Node, Ref

FULL_DEPTH = 64


class LocatorNotFound(Exception):
    """The adapter could not find an element at the given locator."""


class WindowTarget(BaseModel):
    """A top-level window plus the raw signals the detector scores (collected in adapters/winenum.py)."""

    app_id: str
    hwnd: int | None = None
    pid: int
    title: str
    process_name: str
    class_name: str | None = None
    exe: str | None = None
    child_classes: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)  # loaded DLL basenames
    child_processes: list[str] = Field(default_factory=list)
    electron_resources: str | None = None  # resources/app(.asar) found near the exe (Electron)
    notes: list[str] = Field(default_factory=list)  # e.g. "modules: access denied"


class Adapter(Protocol):
    name: str

    def can_handle(self, target: WindowTarget) -> int: ...  # score 0..100

    def attach(self, target: WindowTarget) -> None: ...

    def snapshot(self, root: Ref | None, depth: int) -> Node: ...

    def read(self, ref: Ref) -> Node: ...  # raises LocatorNotFound

    def act(self, ref: Ref, action: Action, dry_run: bool = False) -> ActionResult: ...

    def raw_dump(self, root: Ref | None, depth: int) -> dict: ...  # native tree as-is, for debug

    def detach(self) -> None: ...


@dataclass
class Declined:
    """An enricher has no safe native way to perform this action; the base adapter should."""

    reason: str


class Enricher(Protocol):
    """Adds framework-specific facts and actions on top of a base adapter's tree (e.g. win32 on base_uia)."""

    name: str

    def attach(self, target: WindowTarget) -> None: ...

    def enrich(self, tree: Node) -> None: ...  # mutate in place; record changed fields in meta.sources

    def raw_dump(self) -> dict: ...

    def act(self, tree: Node, ref: str, action: Action, dry_run: bool = False) -> ActionResult | Declined: ...


class ContentUnavailable(Exception):
    """A content provider cannot attach (e.g. an Electron app started without a debugging port)."""


class ContentProvider(Protocol):
    """Supplies the inner content of a window (e.g. CDP for Electron/WebView2) while a base adapter
    supplies the native frame. Its nodes' locators are recognised by owns()."""

    name: str
    host_classes: set[str]  # base nodes (by class) whose subtree this content replaces

    def owns(self, locator: str | None) -> bool: ...

    def attach(self, target: WindowTarget) -> None: ...  # raises ContentUnavailable

    def snapshot(self, root: Ref | None, depth: int) -> Node: ...

    def read(self, ref: Ref) -> Node: ...  # raises LocatorNotFound

    def act(self, ref: Ref, action: Action, dry_run: bool = False) -> ActionResult: ...

    def raw_dump(self) -> dict: ...

    def detach(self) -> None: ...


@dataclass
class LaunchPlan:
    cmd: list[str]
    env: dict[str, str] | None
    port: int
    note: str  # human-readable addition to the traced command line


class Launcher(Protocol):
    """Starts apps with a debugging endpoint and closes them again (implemented in adapters/cdp.py)."""

    def prepare(self, exe: str, args: list[str], env: dict[str, str] | None) -> LaunchPlan: ...

    def wait(self, plan: LaunchPlan, proc: object) -> str: ...  # endpoint URL; raises with an actionable message

    def close_app(self, pid: int) -> str: ...  # description of what was done
