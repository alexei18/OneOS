"""Adapter protocol (SPEC §7). Adapters return Node / ActionResult, never native objects."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from uibridge.core.model import Action, ActionResult, Node, Ref

FULL_DEPTH = 64


class LocatorNotFound(Exception):
    """The adapter could not find an element at the given locator."""


class WindowTarget(BaseModel):
    hwnd: int | None = None
    pid: int
    title: str
    process_name: str
    class_name: str | None = None


class Adapter(Protocol):
    name: str

    def can_handle(self, target: WindowTarget) -> int: ...  # score 0..100

    def attach(self, target: WindowTarget) -> None: ...

    def snapshot(self, root: Ref | None, depth: int) -> Node: ...

    def read(self, ref: Ref) -> Node: ...  # raises LocatorNotFound

    def act(self, ref: Ref, action: Action) -> ActionResult: ...

    def raw_dump(self, root: Ref | None, depth: int) -> dict: ...  # native tree as-is, for debug

    def detach(self) -> None: ...
