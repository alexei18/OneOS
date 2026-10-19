import pytest

from conftest import FIXTURE_NAMES
from uibridge.adapters.fake import FAKE_ROLE_MAP, ROLE_ACTIONS, FakeAdapter, map_role
from uibridge.core.adapter import FULL_DEPTH
from uibridge.core.model import Role


def test_map_targets_are_roles():
    assert all(isinstance(role, Role) for role in FAKE_ROLE_MAP.values())
    assert all(isinstance(role, Role) for role in ROLE_ACTIONS)


@pytest.mark.parametrize(
    "native, role",
    [("Button", Role.BUTTON), ("Edit", Role.TEXTBOX), ("TreeItem", Role.TREEITEM),
     ("DataItem", Role.ROW), ("TabItem", Role.TAB), ("Custom", Role.GENERIC)],
)
def test_known_native_roles(native, role):
    assert map_role(native) == role


def test_unmapped_native_role_becomes_generic_and_keeps_native_role():
    fixture = {"app": {"title": "X", "process": "x.exe", "pid": 1},
               "root": {"role": "Window", "children": [{"role": "SomethingWeird", "name": "w"}]}}
    child = FakeAdapter(fixture).snapshot(None, 1).children[0]
    assert child.role == Role.GENERIC
    assert child.meta.native_role == "SomethingWeird"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_roles_map_without_unintended_generic(name):
    tree = FakeAdapter.load(name).snapshot(None, FULL_DEPTH)
    unmapped = {n.meta.native_role for n in tree.walk() if n.role == Role.GENERIC}
    assert unmapped <= {"TitleBar"}  # the only deliberately unmapped role in fixtures
