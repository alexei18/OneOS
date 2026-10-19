import pytest
from pydantic import ValidationError

from uibridge.core.model import Action, ActionKind, Node, Role, State


def test_child_count_defaults_to_children():
    node = Node(role=Role.LIST, children=[Node(role=Role.LISTITEM), Node(role=Role.LISTITEM)])
    assert node.child_count == 2
    assert Node(role=Role.LIST, child_count=300).child_count == 300


def test_states_serialize_in_enum_order():
    node = Node(role=Role.CHECKBOX, states={State.FOCUSED, State.CHECKED, State.ENABLED})
    assert node.model_dump(mode="json")["states"] == ["enabled", "focused", "checked"]


@pytest.mark.parametrize("kind", [ActionKind.SET_VALUE, ActionKind.TYPE_TEXT, ActionKind.PRESS_KEYS])
def test_text_actions_require_value(kind):
    with pytest.raises(ValidationError):
        Action(kind=kind)
    assert Action(kind=kind, value="x").value == "x"


def test_invoke_menu_requires_path():
    with pytest.raises(ValidationError):
        Action(kind=ActionKind.INVOKE_MENU)
    assert Action(kind=ActionKind.INVOKE_MENU, path=["File", "Open"]).path == ["File", "Open"]
