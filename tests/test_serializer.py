import math

import pytest

from conftest import find, read_events, snap
from uibridge.adapters.fake import FakeAdapter
from uibridge.core.model import Node, NodeMeta, Role, State
from uibridge.core.serializer import app_header, render

DIALOG_VIEW = """\
app "Run" [win32] pid=4120
- dialog "Run" [e1] modal
  - image "Run icon" [e3]
  - text "Type the name of a program, folder, document, or Internet resource, and Windows…" [e4]
  - combobox "Open:" [e6] focused collapsed
    - textbox "Open:" [e7]
  - button "OK" [e8] disabled
  - button "Cancel" [e9]
  - button "Browse..." [e10]
  - button "Close" [e13]
actions: click, focus, set_value, type_text, expand, collapse"""


def view(tree, tracer, **kw):
    return render(tree, tracer, app_id="app1", **kw)


@pytest.fixture
def dialog_tree(registry):
    return snap(FakeAdapter.load("win32_dialog"), registry)


@pytest.fixture
def regedit_tree(registry):
    return snap(FakeAdapter.load("tree_app"), registry)


def test_dialog_golden(dialog_tree, tracer):
    v = view(dialog_tree, tracer, header=app_header("Run", "win32", 4120))
    assert v.text == DIALOG_VIEW
    s = v.stats
    assert (s.nodes_in, s.nodes_out, s.hoisted, s.hidden, s.text_truncated) == (13, 9, 3, 1, 1)


def test_hoists_unnamed_single_child_wrappers(dialog_tree, tracer):
    text = view(dialog_tree, tracer).text
    assert "pane" not in text and "generic" not in text  # 2 panes + unmapped TitleBar hoisted
    assert '  - image "Run icon"' in text  # hoisted child sits at the wrapper's level


def test_named_or_multi_child_panes_are_kept(regedit_tree, tracer):
    assert "  - pane [" in view(regedit_tree, tracer).text  # splitter pane has 2 children


def test_offscreen_hidden_unless_requested(dialog_tree, tracer):
    assert "Hidden hint" not in view(dialog_tree, tracer).text
    v = view(dialog_tree, tracer, include_offscreen=True)
    assert '- text "Hidden hint" [e11] offscreen' in v.text and v.stats.hidden == 0


def test_depth_cut_points_to_expand(regedit_tree, tracer):
    text = view(regedit_tree, tracer).text  # window > pane > tree > Computer is level 3
    computer = find(regedit_tree, "Computer")
    assert f"        … 5 more (expand {computer.ref})" in text.splitlines()
    assert 'treeitem "HKEY_CURRENT_USER"' not in text
    assert 'treeitem "HKEY_CURRENT_USER"' in view(regedit_tree, tracer, depth=5).text


def test_long_list_truncated_with_count(registry, tracer):
    tree = snap(FakeAdapter.load("long_list"), registry)
    v = view(tree, tracer)
    lines = v.text.splitlines()
    assert sum(1 for line in lines if "- listitem" in line) == 25
    assert '"Item 025"' in v.text and '"Item 026"' not in v.text
    assert "    … 275 more (of 300)" in lines
    assert v.stats.truncated == 275


def test_menubar_collapses_to_one_line_but_not_as_root(regedit_tree, tracer):
    menubar = find(regedit_tree, "Application")
    assert f'- menubar "Application" [{menubar.ref}]: File, Edit, View, Favorites, Help' in view(
        regedit_tree, tracer
    ).text
    expanded = view(menubar, tracer).text.splitlines()
    assert expanded[0] == f'- menubar "Application" [{menubar.ref}]'
    assert len([line for line in expanded if "- menuitem" in line]) == 5


def test_menubar_with_stateful_item_is_not_collapsed(tracer):
    items = [Node(role=Role.MENUITEM, name="File"), Node(role=Role.MENUITEM, name="Edit", states={State.DISABLED})]
    text = view(Node(role=Role.WINDOW, children=[Node(role=Role.MENUBAR, children=items)]), tracer).text
    assert '- menuitem "Edit" disabled' in text


def test_single_text_child_is_inlined(regedit_tree, tracer):
    statusbar = next(n for n in regedit_tree.walk() if n.role == Role.STATUSBAR)
    assert f'- statusbar [{statusbar.ref}]: "Computer\\HKEY_CURRENT_USER\\Software\\Microsoft"' in view(
        regedit_tree, tracer
    ).text


def test_only_non_default_states_shown(dialog_tree, tracer):
    text = view(dialog_tree, tracer).text
    assert "enabled" not in text
    assert '- button "OK" [e8] disabled' in text


def test_table_shows_row_count(regedit_tree, tracer):
    assert 'table "Values"' in view(regedit_tree, tracer).text
    assert "rows=3" in view(regedit_tree, tracer).text


def test_long_value_truncated_and_control_chars_escaped(tracer):
    value = 'line one\rline "two"\n' + "x" * 200
    node = Node(role=Role.DOCUMENT, name="Editor text", value=value, meta=NodeMeta())
    v = view(Node(role=Role.WINDOW, children=[node]), tracer, max_text=40)
    line = v.text.splitlines()[1]
    assert line == '  - document "Editor text": "line one\\rline \\"two\\"\\nxxxxxxxxxxxxxxxxxxxx…"'
    assert v.stats.text_truncated == 1


def test_value_equal_to_name_is_not_repeated(tracer):
    item = Node(ref="e3", role=Role.TREEITEM, name="Computer", value="Computer")
    assert view(Node(role=Role.TREE, children=[item]), tracer).text.splitlines()[1] == '  - treeitem "Computer" [e3]'


def test_icon_glyph_text_is_dropped(tracer):
    button = Node(ref="e2", role=Role.BUTTON, name="Multiply by", children=[Node(role=Role.TEXT, name="")])
    loose = Node(role=Role.TEXT, name="  ")
    digit = Node(ref="e4", role=Role.BUTTON, name="One", children=[Node(role=Role.TEXT, name="1")])
    v = view(Node(role=Role.WINDOW, children=[button, loose, digit]), tracer)
    assert v.text.splitlines()[1:3] == ['  - button "Multiply by" [e2]', '  - button "One" [e4]: "1"']
    assert v.stats.glyphs == 2


def test_child_count_beyond_loaded_children_is_announced(tracer):
    node = Node(ref="e9", role=Role.LIST, name="Lazy", child_count=40)
    assert "  … 40 more (expand e9)" in view(Node(role=Role.WINDOW, children=[node]), tracer).text


def test_token_estimate_and_trace_event(dialog_tree, tracer):
    v = view(dialog_tree, tracer)
    assert v.tokens == math.ceil(len(v.text) / 4)
    event = read_events(tracer)[-1]
    assert event["kind"] == "snapshot_ai_view"
    assert event["payload"]["text"] == v.text and event["payload"]["tokens"] == v.tokens
    assert event["payload"]["stats"]["hoisted"] == 3
