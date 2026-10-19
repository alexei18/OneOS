"""Adapter protocol (SPEC §7). Adapters return Node / ActionResult, never native objects."""

from __future__ import annotations

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
