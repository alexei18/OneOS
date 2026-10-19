"""ai-mode aria snapshot parsing, on a real snapshot captured from Chrome 155 via CDP (Phase 6 probe)."""

from pathlib import Path

from uibridge.adapters.aria_parse import ARIA_ROLES, AriaItem, parse, to_tree
from uibridge.core.model import Role, State

SAMPLE = (Path(__file__).parent / "fixtures" / "aria_sample.yaml").read_text(encoding="utf-8")


def find(node, **kw):
    return next(n for n in node.walk() if all(getattr(n, k) == v for k, v in kw.items()))


def test_parse_structure_refs_and_props():
    roots = parse(SAMPLE)
    assert [r.role for r in roots] == ["main"] and roots[0].ref == "e2"
    main = roots[0]
    roles = [c.role for c in main.children]
    assert roles[:5] == ["heading", "generic", "generic", "combobox", "button"]
    link = next(c for c in main.children if c.role == "link")
    assert (link.name, link.ref, link.props, link.attrs) == ("About us", "e11", {"/url": "/about"}, {"cursor": "pointer"})
    combo = next(c for c in main.children if c.role == "combobox")
    assert [(o.name, o.ref, o.attrs) for o in combo.children] == [("Red", None, {"selected": None}), ("Green", None, {})]
    iframe = next(c for c in main.children if c.role == "iframe")
    assert iframe.children[0].ref == "f1e2"
    heading = main.children[0]
    assert heading.attrs == {"level": "1"} and heading.name == "Hello"


def test_text_lines_and_quoted_text():
    items = parse('- generic [ref=e1]:\n  - text: "a: b"\n  - listitem [ref=e2]: One\n')
    assert items[0].children[0] == AriaItem(role="text", text="a: b")
    assert items[0].children[1].text == "One"


def test_to_tree_maps_roles_states_values_and_locators():
    tree = to_tree(SAMPLE, "Probe page")
    assert (tree.role, tree.name, tree.meta.locator) == (Role.DOCUMENT, "Probe page", "cdp:page")
    checkbox = find(tree, name="I agree", role=Role.CHECKBOX)
    assert checkbox.meta.locator == "cdp:e7" and State.UNCHECKED in checkbox.states
    off = find(tree, name="Off")
    assert State.DISABLED in off.states and State.ENABLED not in off.states
    green = find(tree, name="Green")
    assert green.role == Role.OPTION and green.meta.locator == "cdp:e8>option:Green"
    assert State.SELECTED in find(tree, name="Red").states
    assert find(tree, name="Word Wrap").role == Role.MENUITEM and State.CHECKED in find(tree, name="Word Wrap").states
    assert find(tree, name="About us").description == "/about"
    item = find(tree, meta=find(tree, name="Items").children[0].meta)
    assert item.role == Role.LISTITEM and item.children[0].name == "One"  # trailing text becomes a text child
    static = find(tree, role=Role.TEXT, name="Name")
    assert static.meta.locator == "cdp:e4>text:Name"


def test_textbox_text_is_its_value():
    tree = to_tree('- textbox "Name" [active] [ref=e5]: Ada', None)
    box = tree.children[0]
    assert (box.value, box.children) == ("Ada", []) and State.FOCUSED in box.states


def test_aria_hidden_is_offscreen_and_unknown_role_is_generic():
    tree = to_tree('- generic [aria-hidden] [ref=e14]: File\n- widget-x "W" [ref=e9]', None)
    assert State.OFFSCREEN in tree.children[0].states
    assert tree.children[1].role == Role.GENERIC and tree.children[1].meta.native_role == "widget-x"


def test_role_table_targets_are_roles():
    assert all(isinstance(r, Role) for r in ARIA_ROLES.values())
