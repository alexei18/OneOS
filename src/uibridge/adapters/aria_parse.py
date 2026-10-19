"""Parse Playwright's ai-mode aria snapshot (YAML-like text) into Nodes.

Grammar (verified on Chrome 155 / Playwright 1.64, tests/fixtures/aria_sample.yaml):
    - role "name" [attr] [attr=value] [ref=e5]: text      leaf with text (a value for text inputs)
    - role "name" [ref=e5]:                               element with children (indented by 2)
    - text: Some text                                     static text
    - /url: /about                                        property of the parent element
Elements without [ref=…] (e.g. <option>s of a <select>) cannot be targeted by ref; their
locator is the nearest ancestor's ref plus role and name.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from uibridge.core.model import ActionKind, Node, NodeMeta, Role, State

_LINE = re.compile(r"^(?P<indent> *)- (?P<body>.*)$")
_NAME = re.compile(r'"(?:[^"\\]|\\.)*"')
_ATTR = re.compile(r" ?\[([\w-]+)(?:=([^\]]*))?\]")

ARIA_ROLES: dict[str, Role] = {
    "alert": Role.ALERT, "alertdialog": Role.DIALOG, "application": Role.GROUP, "article": Role.GROUP,
    "banner": Role.GROUP, "blockquote": Role.GROUP, "button": Role.BUTTON, "caption": Role.TEXT, "cell": Role.CELL,
    "checkbox": Role.CHECKBOX, "code": Role.TEXT, "columnheader": Role.COLUMNHEADER, "combobox": Role.COMBOBOX,
    "complementary": Role.GROUP, "contentinfo": Role.GROUP, "definition": Role.TEXT, "deletion": Role.TEXT,
    "dialog": Role.DIALOG, "directory": Role.LIST, "document": Role.DOCUMENT, "emphasis": Role.TEXT,
    "feed": Role.LIST, "figure": Role.GROUP, "form": Role.GROUP, "generic": Role.GENERIC, "grid": Role.GRID,
    "gridcell": Role.CELL, "group": Role.GROUP, "heading": Role.HEADING, "iframe": Role.DOCUMENT, "img": Role.IMAGE,
    "image": Role.IMAGE, "insertion": Role.TEXT, "link": Role.LINK, "list": Role.LIST, "listbox": Role.LISTBOX,
    "listitem": Role.LISTITEM, "log": Role.GROUP, "main": Role.GROUP, "marquee": Role.GROUP, "math": Role.TEXT,
    "menu": Role.MENU, "menubar": Role.MENUBAR, "menuitem": Role.MENUITEM, "menuitemcheckbox": Role.MENUITEM,
    "menuitemradio": Role.MENUITEM, "meter": Role.PROGRESSBAR, "navigation": Role.GROUP, "none": Role.GENERIC,
    "note": Role.GROUP, "option": Role.OPTION, "paragraph": Role.GENERIC, "presentation": Role.GENERIC,
    "progressbar": Role.PROGRESSBAR, "radio": Role.RADIO, "radiogroup": Role.GROUP, "region": Role.GROUP,
    "row": Role.ROW, "rowgroup": Role.GENERIC, "rowheader": Role.ROWHEADER, "scrollbar": Role.SCROLLBAR,
    "search": Role.GROUP, "searchbox": Role.TEXTBOX, "separator": Role.SEPARATOR, "slider": Role.SLIDER,
    "spinbutton": Role.SPINBUTTON, "status": Role.STATUSBAR, "strong": Role.TEXT, "subscript": Role.TEXT,
    "superscript": Role.TEXT, "switch": Role.SWITCH, "tab": Role.TAB, "table": Role.TABLE, "tablist": Role.TABLIST,
    "tabpanel": Role.TABPANEL, "term": Role.TEXT, "text": Role.TEXT, "textbox": Role.TEXTBOX, "time": Role.TEXT,
    "timer": Role.TEXT, "toolbar": Role.TOOLBAR, "tooltip": Role.GENERIC, "tree": Role.TREE, "treegrid": Role.GRID,
    "treeitem": Role.TREEITEM,
}
VALUE_ROLES = {"textbox", "searchbox", "combobox", "spinbutton", "slider"}
CHECKABLE = {"checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"}

A = ActionKind
CDP_ROLE_ACTIONS: dict[Role, list[ActionKind]] = {
    Role.BUTTON: [A.CLICK, A.FOCUS], Role.LINK: [A.CLICK, A.FOCUS], Role.MENUITEM: [A.CLICK],
    Role.TAB: [A.CLICK, A.SELECT], Role.OPTION: [A.CLICK, A.SELECT], Role.TREEITEM: [A.CLICK, A.SELECT, A.EXPAND, A.COLLAPSE],
    Role.LISTITEM: [A.CLICK], Role.ROW: [A.CLICK, A.SELECT], Role.CELL: [A.CLICK],
    Role.TEXTBOX: [A.FOCUS, A.SET_VALUE, A.TYPE_TEXT, A.PRESS_KEYS],
    Role.COMBOBOX: [A.FOCUS, A.SET_VALUE, A.TYPE_TEXT, A.PRESS_KEYS, A.EXPAND, A.COLLAPSE],
    Role.CHECKBOX: [A.TOGGLE, A.CHECK, A.UNCHECK], Role.SWITCH: [A.TOGGLE, A.CHECK, A.UNCHECK], Role.RADIO: [A.CHECK],
    Role.SLIDER: [A.SET_VALUE], Role.SPINBUTTON: [A.SET_VALUE], Role.DOCUMENT: [A.PRESS_KEYS],
}


@dataclass
class AriaItem:
    role: str
    name: str | None = None
    ref: str | None = None
    attrs: dict[str, str | None] = field(default_factory=dict)
    text: str | None = None
    props: dict[str, str] = field(default_factory=dict)  # "/url" etc.
    children: list[AriaItem] = field(default_factory=list)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return json.loads(text)
        except ValueError:
            return text[1:-1]
    return text


def _parse_body(body: str) -> AriaItem:
    head, sep, rest = body.partition(":") if body.startswith(("text:", "/")) else (body, "", "")
    if sep:  # "text: …" or "/url: …"
        return AriaItem(role=head, text=_unquote(rest))
    m = re.match(r"[\w-]+", body)
    role = m.group(0) if m else body
    pos = len(role)
    name = None
    nm = _NAME.match(body, pos + 1) if body[pos:pos + 2] == ' "' else None
    if nm:
        name, pos = json.loads(nm.group(0)), nm.end()
    attrs: dict[str, str | None] = {}
    while True:
        am = _ATTR.match(body, pos)
        if not am:
            break
        attrs[am.group(1)] = am.group(2)
        pos = am.end()
    item = AriaItem(role=role, name=name, ref=attrs.pop("ref", None), attrs=attrs)
    tail = body[pos:]
    if tail.startswith(": "):
        item.text = _unquote(tail[2:])
    return item


def parse(snapshot: str) -> list[AriaItem]:
    """Parse the ai-mode text into a forest of AriaItems (nesting by 2-space indentation)."""
    roots: list[AriaItem] = []
    stack: list[tuple[int, AriaItem]] = []
    for line in snapshot.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        indent = len(m.group("indent"))
        item = _parse_body(m.group("body"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        if item.role.startswith("/"):  # property line belongs to the parent element
            if parent is not None:
                parent.props[item.role] = item.text or ""
            continue
        (parent.children if parent else roots).append(item)
        stack.append((indent, item))
    return roots


def _states(item: AriaItem) -> set[State]:
    a = item.attrs
    states = {State.DISABLED if "disabled" in a else State.ENABLED}
    if "checked" in a or "pressed" in a:
        value = a.get("checked", a.get("pressed"))
        states.add(State.MIXED if value == "mixed" else State.UNCHECKED if value == "false" else State.CHECKED)
    elif item.role in CHECKABLE:
        states.add(State.UNCHECKED)
    if "expanded" in a:
        states.add(State.COLLAPSED if a["expanded"] == "false" else State.EXPANDED)
    if "selected" in a and a["selected"] != "false":
        states.add(State.SELECTED)
    if "active" in a:
        states.add(State.FOCUSED)
    if "aria-hidden" in a:
        states.add(State.OFFSCREEN)  # duplicates of visible labels (e.g. VS Code menu titles)
    return states


def to_node(item: AriaItem, parent_locator: str, adapter: str = "cdp") -> Node:
    """Map one AriaItem (and its subtree) to a Node. Locator: "cdp:<ref>", or for ref-less items
    "<ancestor locator>><role>:<name>"."""
    role = ARIA_ROLES.get(item.role, Role.GENERIC)
    locator = f"cdp:{item.ref}" if item.ref else f"{parent_locator}>{item.role}:{item.name or item.text or ''}"
    value = item.text if item.role in VALUE_ROLES else None
    children = [to_node(c, locator, adapter) for c in item.children]
    name = item.name
    if item.text is not None and item.role not in VALUE_ROLES:
        if item.role == "text" and name is None:
            name = item.text
        else:
            children.append(Node(role=Role.TEXT, name=item.text,
                                 meta=NodeMeta(adapter=adapter, native_role="text",
                                               locator=f"{locator}>text:{item.text}")))
    return Node(
        role=role, name=name, value=value, description=item.props.get("/url"),
        states=_states(item), actions=CDP_ROLE_ACTIONS.get(role, []), children=children,
        meta=NodeMeta(adapter=adapter, native_role=item.role, locator=locator),
    )


def to_tree(snapshot: str, title: str | None, adapter: str = "cdp") -> Node:
    """The whole page as one document node (locator "cdp:page"), children = top-level items."""
    root = "cdp:page"
    return Node(role=Role.DOCUMENT, name=title, states={State.ENABLED}, actions=[A.PRESS_KEYS],
                children=[to_node(i, root, adapter) for i in parse(snapshot)],
                meta=NodeMeta(adapter=adapter, native_role="page", locator=root))
