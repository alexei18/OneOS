"""Common data model: the semantic tree every adapter produces (SPEC §3)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


class Role(StrEnum):
    WINDOW = "window"
    DIALOG = "dialog"
    ALERT = "alert"
    MENUBAR = "menubar"
    MENU = "menu"
    MENUITEM = "menuitem"
    TOOLBAR = "toolbar"
    STATUSBAR = "statusbar"
    TABLIST = "tablist"
    TAB = "tab"
    TABPANEL = "tabpanel"
    BUTTON = "button"
    LINK = "link"
    TEXTBOX = "textbox"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    SWITCH = "switch"
    COMBOBOX = "combobox"
    LISTBOX = "listbox"
    OPTION = "option"
    LIST = "list"
    LISTITEM = "listitem"
    TREE = "tree"
    TREEITEM = "treeitem"
    TABLE = "table"
    ROW = "row"
    CELL = "cell"
    COLUMNHEADER = "columnheader"
    ROWHEADER = "rowheader"
    GRID = "grid"
    SLIDER = "slider"
    SPINBUTTON = "spinbutton"
    PROGRESSBAR = "progressbar"
    SCROLLBAR = "scrollbar"
    SEPARATOR = "separator"
    HEADING = "heading"
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    GROUP = "group"
    PANE = "pane"
    GENERIC = "generic"


class State(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    FOCUSED = "focused"
    SELECTED = "selected"
    CHECKED = "checked"
    UNCHECKED = "unchecked"
    MIXED = "mixed"
    EXPANDED = "expanded"
    COLLAPSED = "collapsed"
    READONLY = "readonly"
    OFFSCREEN = "offscreen"
    MODAL = "modal"


class ActionKind(StrEnum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    FOCUS = "focus"
    SET_VALUE = "set_value"
    TYPE_TEXT = "type_text"
    PRESS_KEYS = "press_keys"
    TOGGLE = "toggle"
    CHECK = "check"
    UNCHECK = "uncheck"
    SELECT = "select"
    EXPAND = "expand"
    COLLAPSE = "collapse"
    SCROLL_INTO_VIEW = "scroll_into_view"
    INVOKE_MENU = "invoke_menu"


Strategy = Literal["pattern", "native_message", "synthetic_input", "none"]


class Rect(BaseModel):
    x: int
    y: int
    width: int
    height: int


class Fingerprint(BaseModel):
    """Identity used to keep refs stable and to re-find stale elements (SPEC §4)."""

    model_config = ConfigDict(frozen=True)

    role: Role
    name: str | None = None
    automation_id: str | None = None
    class_name: str | None = None
    index_in_parent: int = 0
    ancestors: tuple[tuple[str, str | None], ...] = ()

    def key(self) -> tuple:
        return (*self.local_key(), self.index_in_parent, self.ancestors)

    def local_key(self) -> tuple:
        return (self.role, self.name, self.automation_id, self.class_name)


class NodeMeta(BaseModel):
    adapter: str | None = None
    framework: str | None = None
    native_role: str | None = None
    automation_id: str | None = None
    class_name: str | None = None
    process: str | None = None
    locator: str | None = None
    fingerprint: Fingerprint | None = None


class Node(BaseModel):
    ref: str | None = None
    role: Role
    name: str | None = None
    value: str | None = None
    description: str | None = None
    states: set[State] = Field(default_factory=set)
    actions: list[ActionKind] = Field(default_factory=list)
    bounds: Rect | None = None
    children: list[Node] = Field(default_factory=list)
    child_count: int | None = None
    meta: NodeMeta = Field(default_factory=NodeMeta)

    @model_validator(mode="after")
    def _default_child_count(self) -> Node:
        if self.child_count is None:
            self.child_count = len(self.children)
        return self

    @field_serializer("states")
    def _sorted_states(self, states: set[State]) -> list[str]:
        return [s.value for s in State if s in states]

    def local_key(self) -> tuple:
        return (self.role, self.name, self.meta.automation_id, self.meta.class_name)

    def walk(self):
        """Pre-order iteration over this node and all descendants."""
        yield self
        for child in self.children:
            yield from child.walk()


_NEEDS_VALUE = {ActionKind.SET_VALUE, ActionKind.TYPE_TEXT, ActionKind.PRESS_KEYS}


class Action(BaseModel):
    kind: ActionKind
    value: str | None = None
    path: list[str] | None = None

    @model_validator(mode="after")
    def _check_args(self) -> Action:
        if self.kind in _NEEDS_VALUE and self.value is None:
            raise ValueError(f"action {self.kind} requires value")
        if self.kind == ActionKind.INVOKE_MENU and not self.path:
            raise ValueError("action invoke_menu requires path")
        return self


class ActionResult(BaseModel):
    ok: bool
    strategy: Strategy
    native_call: str
    error: str | None = None
    duration_ms: float = 0.0
    before_fingerprint: str | None = None  # hash of target's name/value/states before acting
    after_fingerprint: str | None = None
    changed_refs: list[str] = Field(default_factory=list)


class Ref(BaseModel):
    """What adapters receive: a session ref plus how to find the element again."""

    id: str
    app_id: str
    locator: str | None = None
    fingerprint: Fingerprint
