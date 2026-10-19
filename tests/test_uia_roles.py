import pytest

from uibridge.adapters.uia_roles import (
    CONTROL_TYPES,
    UIA_ROLE_MAP,
    XA11Y_ACTIONS,
    native_role_name,
    uia_actions,
    uia_role,
    uia_states,
)
from uibridge.core.model import ActionKind, Role, State


def test_control_type_table_is_complete():
    assert sorted(CONTROL_TYPES) == list(range(50000, 50041))
    assert set(UIA_ROLE_MAP) <= set(CONTROL_TYPES)
    assert all(isinstance(r, Role) for r in UIA_ROLE_MAP.values())


@pytest.mark.parametrize(
    "ctid, role",
    [(50000, Role.BUTTON), (50004, Role.TEXTBOX), (50020, Role.TEXT), (50033, Role.PANE),
     (50026, Role.GROUP), (50029, Role.ROW), (50037, Role.GENERIC), (50022, Role.GENERIC), (None, Role.GENERIC)],
)
def test_role_mapping(ctid, role):
    assert uia_role(ctid, "anything", modal=False) == role


def test_modal_or_dialog_window_becomes_dialog():
    assert uia_role(50032, "window", modal=True) == Role.DIALOG
    assert uia_role(50032, "dialog", modal=False) == Role.DIALOG
    assert uia_role(50032, "window", modal=False) == Role.WINDOW


def test_native_role_name():
    assert native_role_name(50033) == "Pane"
    assert native_role_name(12345) == "ControlType(12345)"


def test_action_mapping_is_ordered_and_ignores_window_ops():
    assert all(isinstance(k, ActionKind) for ks in XA11Y_ACTIONS.values() for k in ks)
    assert uia_actions(["toggle", "press", "close", "minimize"]) == [
        ActionKind.CLICK, ActionKind.TOGGLE, ActionKind.CHECK, ActionKind.UNCHECK]


def test_state_mapping():
    props = {"enabled": False, "focused": True, "checked": "mixed", "expanded": False,
             "visible": False, "editable": False, "selected": False, "modal": False}
    assert uia_states(props, Role.TEXTBOX) == {
        State.DISABLED, State.FOCUSED, State.MIXED, State.COLLAPSED, State.OFFSCREEN, State.READONLY}
    assert uia_states({"enabled": True, "visible": True, "editable": False}, Role.BUTTON) == {State.ENABLED}
