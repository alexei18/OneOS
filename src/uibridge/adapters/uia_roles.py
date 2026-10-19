"""UIA mapping tables for base_uia: ControlTypeId -> Role, xa11y action -> ActionKind, states.

ControlTypeIds verified 2026-10-19 against
https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controltype-ids
"""

from __future__ import annotations

from typing import Any

from uibridge.core.model import ActionKind, Role, State

CONTROL_TYPES: dict[int, str] = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit",
    50005: "Hyperlink", 50006: "Image", 50007: "ListItem", 50008: "List", 50009: "Menu",
    50010: "MenuBar", 50011: "MenuItem", 50012: "ProgressBar", 50013: "RadioButton",
    50014: "ScrollBar", 50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip", 50023: "Tree",
    50024: "TreeItem", 50025: "Custom", 50026: "Group", 50027: "Thumb", 50028: "DataGrid",
    50029: "DataItem", 50030: "Document", 50031: "SplitButton", 50032: "Window", 50033: "Pane",
    50034: "Header", 50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
    50039: "SemanticZoom", 50040: "AppBar",
}

# Types not listed here (Calendar, ToolTip, Thumb, SemanticZoom, AppBar) become generic.
UIA_ROLE_MAP: dict[int, Role] = {
    50000: Role.BUTTON, 50002: Role.CHECKBOX, 50003: Role.COMBOBOX, 50004: Role.TEXTBOX,
    50005: Role.LINK, 50006: Role.IMAGE, 50007: Role.LISTITEM, 50008: Role.LIST, 50009: Role.MENU,
    50010: Role.MENUBAR, 50011: Role.MENUITEM, 50012: Role.PROGRESSBAR, 50013: Role.RADIO,
    50014: Role.SCROLLBAR, 50015: Role.SLIDER, 50016: Role.SPINBUTTON, 50017: Role.STATUSBAR,
    50018: Role.TABLIST, 50019: Role.TAB, 50020: Role.TEXT, 50021: Role.TOOLBAR, 50023: Role.TREE,
    50024: Role.TREEITEM, 50025: Role.GENERIC, 50026: Role.GROUP, 50028: Role.GRID, 50029: Role.ROW,
    50030: Role.DOCUMENT, 50031: Role.BUTTON, 50032: Role.WINDOW, 50033: Role.PANE,
    50034: Role.GROUP, 50035: Role.COLUMNHEADER, 50036: Role.TABLE, 50037: Role.GENERIC,
    50038: Role.SEPARATOR,
}

A = ActionKind
# xa11y action name -> our pattern-backed actions. Window ops (close, minimize...) are not exposed.
XA11Y_ACTIONS: dict[str, list[ActionKind]] = {
    "press": [A.CLICK],
    "focus": [A.FOCUS],
    "set_value": [A.SET_VALUE, A.TYPE_TEXT],
    "toggle": [A.TOGGLE, A.CHECK, A.UNCHECK],
    "expand": [A.EXPAND],
    "collapse": [A.COLLAPSE],
    "select": [A.SELECT],
    "scroll_into_view": [A.SCROLL_INTO_VIEW],
    "show_menu": [A.RIGHT_CLICK],
}

TEXT_INPUT_ROLES = {Role.TEXTBOX, Role.COMBOBOX}


def uia_role(control_type_id: int | None, xa11y_role: str, modal: bool) -> Role:
    role = UIA_ROLE_MAP.get(control_type_id or 0, Role.GENERIC)
    if role == Role.WINDOW and (xa11y_role == "dialog" or modal):
        return Role.DIALOG
    return role


def native_role_name(control_type_id: int | None) -> str:
    return CONTROL_TYPES.get(control_type_id or 0, f"ControlType({control_type_id})")


def uia_actions(xa11y_actions: list[str]) -> list[ActionKind]:
    found = {k for name in xa11y_actions for k in XA11Y_ACTIONS.get(name, [])}
    return [k for k in ActionKind if k in found]


def uia_states(props: dict[str, Any], role: Role) -> set[State]:
    """props: enabled, focused, selected, modal, visible, editable, checked ('on'/'off'/'mixed'/None),
    expanded (bool/None) as read from an xa11y Element."""
    states = {State.ENABLED if props.get("enabled", True) else State.DISABLED}
    for key, state in (("focused", State.FOCUSED), ("selected", State.SELECTED), ("modal", State.MODAL)):
        if props.get(key):
            states.add(state)
    checked = props.get("checked")
    if checked is not None:
        states.add({"on": State.CHECKED, "off": State.UNCHECKED, "mixed": State.MIXED}[checked])
    expanded = props.get("expanded")
    if expanded is not None:
        states.add(State.EXPANDED if expanded else State.COLLAPSED)
    if props.get("visible") is False:
        states.add(State.OFFSCREEN)
    if role in TEXT_INPUT_ROLES and props.get("editable") is False:
        states.add(State.READONLY)
    return states
