"""ComposedAdapter: a base adapter for the native frame + a content provider for what is inside it
(e.g. base_uia window chrome + CDP web content). To the router it is one Adapter; each node keeps
meta.adapter of the adapter that produced it, and calls are dispatched by locator.
"""

from __future__ import annotations

from uibridge.core.adapter import Adapter, ContentProvider, WindowTarget
from uibridge.core.model import Action, ActionResult, Node, Ref


class ComposedAdapter:
    def __init__(self, base: Adapter, content: ContentProvider) -> None:
        self.base = base
        self.content = content
        self.name = f"{base.name}+{content.name}"

    def can_handle(self, target: WindowTarget) -> int:
        return self.base.can_handle(target)

    def attach(self, target: WindowTarget) -> None:
        self.base.attach(target)
        self.content.attach(target)  # may raise ContentUnavailable: the base then works on its own
        if hasattr(self.base, "opaque_classes"):  # only now: don't walk what the content replaces
            self.base.opaque_classes = set(self.content.host_classes)

    def detach(self) -> None:
        try:
            self.content.detach()
        finally:
            self.base.detach()

    def snapshot(self, root: Ref | None, depth: int) -> Node:
        if root is not None and self.content.owns(root.locator):
            return self.content.snapshot(root, depth)
        tree = self.base.snapshot(root, depth)
        host = next((n for n in tree.walk() if n.meta.class_name in self.content.host_classes), None)
        page = None
        if host is not None:
            page = self.content.snapshot(None, depth)
            host.children, host.child_count = [page], 1
        elif root is None:  # host element not exposed by the base tree: content sits next to the frame
            page = self.content.snapshot(None, depth)
            tree.children.append(page)
            tree.child_count = len(tree.children)
        return tree

    def read(self, ref: Ref) -> Node:
        return (self.content if self.content.owns(ref.locator) else self.base).read(ref)

    def act(self, ref: Ref, action: Action, dry_run: bool = False) -> ActionResult:
        return (self.content if self.content.owns(ref.locator) else self.base).act(ref, action, dry_run=dry_run)

    def raw_dump(self, root: Ref | None, depth: int) -> dict:
        if root is not None and self.content.owns(root.locator):
            return {"adapter": self.name, self.content.name: self.content.raw_dump()}
        return {**self.base.raw_dump(root, depth), self.content.name: self.content.raw_dump()}
