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
EMPTYABLE = {Role.GENERIC, Role.PANE, Role.GROUP, Role.DOCUMENT}
PASSIVE_ACTIONS = {ActionKind.FOCUS, ActionKind.SCROLL_INTO_VIEW}


class PruneStats(BaseModel):
    nodes_in: int = 0  # nodes in the given tree
    nodes_out: int = 0  # nodes that got their own line
    hoisted: int = 0  # unnamed single-child generic/pane wrappers skipped
    hidden: int = 0  # offscreen nodes (incl. subtrees) not shown
    truncated: int = 0  # children announced as "… N more" instead of shown
    collapsed: int = 0  # children folded into their parent's line
    text_truncated: int = 0  # names/values cut at max_text
    glyphs: int = 0  # icon-font glyph texts dropped
    empty: int = 0  # unnamed containers with nothing to show (incl. their empty subtrees)


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


def is_glyph(text: str | None) -> bool:
    """Icon-font glyphs (Segoe MDL2/Fluent icons) live in the Unicode Private Use Area; as text
    they carry no meaning for the agent (e.g. Calculator's "\\ue947" next to "Multiply by")."""
    stripped = (text or "").strip()
    return bool(stripped) and all(0xE000 <= ord(c) <= 0xF8FF or ord(c) >= 0xF0000 for c in stripped)


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

    def skip_reason(node: Node) -> str | None:
        """Why a node gets no line: offscreen, an icon glyph, or an empty unnamed container."""
        if not include_offscreen and State.OFFSCREEN in node.states:
            return "hidden"
        if node.role == Role.TEXT and node.child_count == 0 and is_glyph(node.name):
            return "glyphs"
        if (node.role in EMPTYABLE and not node.name and not node.value
                and not set(node.actions) - PASSIVE_ACTIONS and node.child_count == len(node.children)
                and all(skip_reason(c) for c in node.children)):
            return "empty"  # e.g. Chromium's empty WebView/NativeViewHost panes around web content
        return None

    def shown_children(node: Node, count: bool) -> list[Node]:
        shown = []
        for child in node.children:
            reason = skip_reason(child)
            if reason is None:
                shown.append(child)
            elif count:
                size = 1 if reason == "glyphs" else sum(1 for _ in child.walk())
                setattr(stats, reason, getattr(stats, reason) + size)
        return shown

    def hoist(node: Node) -> Node:
        while not node.name and node.role in HOISTABLE and node.child_count == len(node.children):
            shown = shown_children(node, count=False)
            if len(shown) != 1:
                break
            shown_children(node, count=True)  # account for the skipped siblings
            stats.hoisted += 1
            node = shown[0]
        return node

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
        if node.value and node.value != node.name:  # tree items often repeat their name as value
            return f"{text}: {quote(node.value)}", False
        if (
            len(kids) == 1
            and node.child_count == 1
            and kids[0].role == Role.TEXT
            and kids[0].name
            and kids[0].child_count == 0
        ):
            stats.collapsed += 1
            if is_glyph(kids[0].name):
                stats.glyphs += 1
                return text, True
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
            more = len(shown_children(node, count=False)) + node.child_count - len(node.children)
            if more:  # children that would all be skipped are not announced
                stats.truncated += more
                lines.append(f"{pad}… {more} more (expand {node.ref})")
            return
        visible = shown_children(node, count=True)
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
