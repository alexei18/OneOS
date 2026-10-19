"""AI view (SPEC §5): compact, aria-snapshot-style text of a Node tree, plus pruning stats."""

from __future__ import annotations

import math

from pydantic import BaseModel

from uibridge.core.model import ActionKind, Node, Role, State
from uibridge.core.tracer import Tracer

HOISTABLE = {Role.GENERIC, Role.PANE}
ONE_LINE_CONTAINERS = {Role.MENUBAR, Role.TOOLBAR}
SIMPLE_ITEMS = {Role.MENUITEM, Role.BUTTON}
ROW_COUNTED = {Role.TABLE, Role.GRID}
DEFAULT_STATES = {State.ENABLED}


class PruneStats(BaseModel):
    nodes_in: int = 0  # nodes in the given tree
    nodes_out: int = 0  # nodes that got their own line
    hoisted: int = 0  # unnamed single-child generic/pane wrappers skipped
    hidden: int = 0  # offscreen nodes (incl. subtrees) not shown
    truncated: int = 0  # children announced as "… N more" instead of shown
    collapsed: int = 0  # children folded into their parent's line
    text_truncated: int = 0  # names/values cut at max_text


class AIView(BaseModel):
    text: str
    tokens: int
    stats: PruneStats


_ESCAPES = {'"': '\\"', "\r": "\\r", "\n": "\\n", "\t": "\\t"}


def _quote(text: str) -> str:
    """Double-quote for the AI view. Backslashes stay as-is (Windows paths are common);
    only quotes and control characters are escaped."""
    out = "".join(_ESCAPES.get(c, f"\\x{ord(c):02x}" if ord(c) < 32 else c) for c in text)
    return f'"{out}"'


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def app_header(title: str, framework: str | None, pid: int | None) -> str:
    return f"app {_quote(title)} [{framework or 'unknown'}] pid={pid}"


def render(
    root: Node,
    tracer: Tracer,
    *,
    app_id: str,
    header: str | None = None,
    depth: int = 4,
    include_offscreen: bool = False,
    max_items: int = 25,
    max_text: int = 80,
) -> AIView:
    stats = PruneStats(nodes_in=sum(1 for _ in root.walk()))
    lines: list[str] = [header] if header else []
    used_actions: set[ActionKind] = set()

    def quote(text: str) -> str:
        if len(text) > max_text:
            stats.text_truncated += 1
            text = text[:max_text].rstrip() + "…"
        return _quote(text)

    def hoist(node: Node) -> Node:
        while not node.name and node.role in HOISTABLE and len(node.children) == 1 and node.child_count == 1:
            stats.hoisted += 1
            node = node.children[0]
        return node

    def hidden(node: Node) -> bool:
        if include_offscreen or State.OFFSCREEN not in node.states:
            return False
        stats.hidden += sum(1 for _ in node.walk())
        return True

    def is_simple_item(n: Node) -> bool:
        return n.role in SIMPLE_ITEMS and bool(n.name) and n.child_count == 0 and n.states <= DEFAULT_STATES

    def line(node: Node, level: int, is_root: bool) -> tuple[str, bool]:
        """Return the node's line and whether its children were folded into it."""
        parts = [f"{'  ' * level}- {node.role}"]
        if node.name:
            parts.append(quote(node.name))
        if node.ref:
            parts.append(f"[{node.ref}]")
        if node.role in ROW_COUNTED:
            parts.append(f"rows={sum(1 for c in node.children if c.role == Role.ROW)}")
        parts += [s.value for s in State if s in node.states and s not in DEFAULT_STATES]
        text = " ".join(parts)

        kids = node.children
        if (
            not is_root
            and node.role in ONE_LINE_CONTAINERS
            and kids
            and len(kids) == node.child_count
            and all(is_simple_item(k) for k in kids)
        ):
            stats.collapsed += len(kids)
            return f"{text}: {', '.join(k.name for k in kids)}", True
        if node.value:
            return f"{text}: {quote(node.value)}", False
        if (
            len(kids) == 1
            and node.child_count == 1
            and kids[0].role == Role.TEXT
            and kids[0].name
            and kids[0].child_count == 0
        ):
            stats.collapsed += 1
            return f"{text}: {quote(kids[0].name)}", True
        return text, False

    def emit(node: Node, level: int, is_root: bool = False) -> None:
        text, folded = line(node, level, is_root)
        lines.append(text)
        stats.nodes_out += 1
        used_actions.update(node.actions)
        if folded or node.child_count == 0:
            return
        pad = "  " * (level + 1)
        if level + 1 >= depth:
            stats.truncated += node.child_count
            lines.append(f"{pad}… {node.child_count} more (expand {node.ref})")
            return
        visible = [k for k in node.children if not hidden(k)]
        for child in visible[:max_items]:
            emit(hoist(child), level + 1)
        if len(visible) > max_items:
            stats.truncated += len(visible) - max_items
            lines.append(f"{pad}… {len(visible) - max_items} more (of {len(visible)})")
        missing = node.child_count - len(node.children)
        if missing > 0:
            stats.truncated += missing
            lines.append(f"{pad}… {missing} more (expand {node.ref})")

    emit(root, 0, is_root=True)
    if used_actions:
        lines.append("actions: " + ", ".join(a.value for a in ActionKind if a in used_actions))
    text = "\n".join(lines)
    view = AIView(text=text, tokens=estimate_tokens(text), stats=stats)
    tracer.emit(
        "snapshot_ai_view",
        {
            "root_ref": root.ref,
            "text": text,
            "tokens": view.tokens,
            "stats": stats.model_dump(),
            "options": {"depth": depth, "include_offscreen": include_offscreen,
                        "max_items": max_items, "max_text": max_text},
        },
        app_id=app_id,
    )
    return view
