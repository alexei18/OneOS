from conftest import find, snap
from uibridge.adapters.fake import FakeAdapter
from uibridge.core.model import Action, ActionKind, State


def act(adapter, registry, tree, name, kind, value=None):
    return adapter.act(registry.get(find(tree, name).ref), Action(kind=kind, value=value))


def test_set_value_changes_state_and_reports_call(registry):
    adapter = FakeAdapter.load("win32_dialog")
    tree = snap(adapter, registry)
    combo = next(n for n in tree.walk() if n.role == "combobox")
    result = adapter.act(registry.get(combo.ref), Action(kind=ActionKind.SET_VALUE, value="notepad"))
    assert result.ok and result.strategy == "pattern"
    assert result.native_call == "FakeAdapter.set_value(locator='/2/0', value='notepad')"
    assert result.before_fingerprint != result.after_fingerprint
    assert adapter.read(registry.get(combo.ref)).value == "notepad"


def test_unsupported_and_disabled_actions_fail_clearly(registry):
    adapter = FakeAdapter.load("win32_dialog")
    tree = snap(adapter, registry)
    unsupported = act(adapter, registry, tree, "Cancel", ActionKind.TOGGLE)
    assert not unsupported.ok and unsupported.strategy == "none"
    assert "toggle not supported by button (supported: click, focus)" == unsupported.error
    disabled = act(adapter, registry, tree, "OK", ActionKind.CLICK)
    assert not disabled.ok and "disabled" in disabled.error


def test_select_moves_selection_between_siblings(registry):
    adapter = FakeAdapter.load("long_list")
    tree = snap(adapter, registry)
    assert act(adapter, registry, tree, "Item 002", ActionKind.SELECT).ok
    assert act(adapter, registry, tree, "Item 005", ActionKind.SELECT).ok
    items = snap(adapter, registry).children[0].children
    assert [i.name for i in items if State.SELECTED in i.states] == ["Item 005"]


def test_expand_collapse(registry):
    adapter = FakeAdapter.load("tree_app")
    tree = snap(adapter, registry)
    assert act(adapter, registry, tree, "HKEY_USERS", ActionKind.EXPAND).ok
    node = find(snap(adapter, registry), "HKEY_USERS")
    assert State.EXPANDED in node.states and State.COLLAPSED not in node.states
