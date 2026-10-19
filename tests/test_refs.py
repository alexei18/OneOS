import pytest

from conftest import find, read_events, snap
from uibridge.adapters.fake import FakeAdapter
from uibridge.core.refs import AmbiguousRefError, RefRegistry, StaleRefError, UnknownRefError


@pytest.fixture
def dialog() -> FakeAdapter:
    return FakeAdapter.load("win32_dialog")


def refs_by_name(tree):
    return {n.name: n.ref for n in tree.walk() if n.name}


def test_refs_are_preorder_and_stable_across_identical_snapshots(dialog, registry):
    first = snap(dialog, registry)
    assert [n.ref for n in first.walk()] == [f"e{i}" for i in range(1, 14)]
    second = snap(dialog, registry)
    assert [n.ref for n in second.walk()] == [n.ref for n in first.walk()]


def test_small_changes_keep_unchanged_refs(dialog, registry):
    before = refs_by_name(snap(dialog, registry))
    root = dialog.fixture["root"]
    root["name"] = "Run *"  # window title change
    find_child = {c.get("name"): c for c in root["children"]}
    find_child["Browse..."]["name"] = "Browse…"  # rename a leaf
    root["children"].remove(find_child["Cancel"])  # remove one
    root["children"].insert(0, {"role": "Button", "name": "Help", "class_name": "Button"})  # insert before

    tree = snap(dialog, registry)
    after = refs_by_name(tree)
    stats_event = [e for e in read_events(registry.tracer) if e["kind"] == "snapshot_normalized"][-1]
    gone = stats_event["payload"]["refs"]["gone"]

    assert after["Run *"] == before["Run"]
    for unchanged in ["OK", "Run icon", "Open:", "Close", "Hidden hint"]:
        assert after[unchanged] == before[unchanged], unchanged
    # renamed but has an automation id: same element, same ref, reported as changed
    assert after["Browse…"] == before["Browse..."]
    assert stats_event["payload"]["changed"][before["Browse..."]]["name"] == ["Browse...", "Browse…"]
    assert after["Help"] not in before.values()
    assert gone == [before["Cancel"]]


def test_renamed_node_without_automation_id_gets_new_ref(registry):
    adapter = FakeAdapter.load("long_list")
    before = refs_by_name(snap(adapter, registry))
    adapter.fixture["root"]["children"][0]["children"][1]["name"] = "Item two"
    after = refs_by_name(snap(adapter, registry))
    assert after["Item two"] not in before.values()
    assert after["Item 001"] == before["Item 001"] and after["Item 003"] == before["Item 003"]


def test_renamed_container_keeps_its_ref_and_its_subtree(registry):
    """VS Code: the RootView pane and the page document carry the window title; a new title must not
    re-ref everything below them."""
    adapter = FakeAdapter.load("tree_app")
    before = refs_by_name(snap(adapter, registry))
    splitter = adapter.fixture["root"]["children"][1]
    splitter["name"] = "Welcome - Registry"  # container without automation id, named like a title
    tree = snap(adapter, registry)
    after = refs_by_name(tree)
    renamed = next(n for n in tree.walk() if n.name == "Welcome - Registry")
    assert renamed.ref == next(n.ref for n in tree.walk() if n.meta.class_name == "RegEdit_Splitter")
    assert after["Computer"] == before["Computer"] and after["Values"] == before["Values"]


def test_changed_states_and_values_are_reported(registry):
    adapter = FakeAdapter.load("win32_dialog")
    tree = snap(adapter, registry)
    combo = next(n for n in tree.walk() if n.role == "combobox")
    adapter.fixture["root"]["children"][2]["children"][0]["value"] = "notepad"
    stats = registry.assign(adapter.snapshot(None, 64), "app1")
    assert stats.changed == {combo.ref: {"value": ["", "notepad"]}}


def test_save_and_load_roundtrip(dialog, registry, tracer, tmp_path):
    tree = snap(dialog, registry)
    path = tmp_path / "refs.json"
    registry.save(path, "app1", pid=4120)
    other = RefRegistry(tracer)
    assert not other.load(path, "app1", pid=999)  # different process: refs are meaningless
    assert other.load(path, "app1", pid=4120)
    cancel = find(tree, "Cancel")
    ref, node = other.resolve(cancel.ref, dialog)
    assert node.name == "Cancel"
    again = dialog.snapshot(None, 64)
    other.assign(again, "app1")
    assert [n.ref for n in again.walk()] == [n.ref for n in tree.walk()]


def test_subtree_snapshot_reuses_refs(dialog, registry):
    full = snap(dialog, registry)
    combo = next(n for n in full.walk() if n.role == "combobox")
    sub = dialog.snapshot(registry.get(combo.ref), depth=5)
    registry.assign(sub, "app1", root_ref=combo.ref)
    assert [n.ref for n in sub.walk()] == [n.ref for n in combo.walk()]


def test_resolve_via_locator(dialog, registry):
    cancel = find(snap(dialog, registry), "Cancel")
    ref, node = registry.resolve(cancel.ref, dialog)
    assert node.name == "Cancel" and ref.locator == "/4"
    event = read_events(registry.tracer)[-1]
    assert event["kind"] == "action_resolution" and event["payload"]["via"] == "locator"


def test_resolve_via_fingerprint_when_locator_is_stale(dialog, registry):
    browse = find(snap(dialog, registry), "Browse...")
    dialog.fixture["root"]["children"].insert(0, {"role": "Button", "name": "Help", "class_name": "Button"})
    ref, node = registry.resolve(browse.ref, dialog)
    assert ref.id == browse.ref and node.name == "Browse..."
    assert ref.locator == "/6"  # was /5
    event = read_events(registry.tracer)[-1]
    assert event["payload"]["via"] == "fingerprint"
    assert "now points to button 'Cancel'" in event["payload"]["reason"]


def test_ambiguous_ref_lists_candidates_and_does_not_guess(dialog, registry):
    cancel = find(snap(dialog, registry), "Cancel")
    children = dialog.fixture["root"]["children"]
    original = next(c for c in children if c.get("name") == "Cancel")
    children.remove(original)
    children.insert(0, dict(original))
    children.insert(1, dict(original))

    with pytest.raises(AmbiguousRefError) as info:
        registry.resolve(cancel.ref, dialog)
    candidates = info.value.candidates
    assert len(candidates) == 2
    assert all(c["name"] == "Cancel" and c["ref"] != cancel.ref for c in candidates)
    assert all(c["ref"] in str(info.value) for c in candidates)
    event = read_events(registry.tracer)[-1]
    assert event["kind"] == "action_resolution" and event["payload"]["ok"] is False
    assert len(event["payload"]["candidates"]) == 2


def test_stale_ref_when_element_is_gone(dialog, registry):
    cancel = find(snap(dialog, registry), "Cancel")
    children = dialog.fixture["root"]["children"]
    children.remove(next(c for c in children if c.get("name") == "Cancel"))
    with pytest.raises(StaleRefError, match="Re-run snapshot"):
        registry.resolve(cancel.ref, dialog)


def test_unknown_ref(dialog, registry):
    with pytest.raises(UnknownRefError, match="run snapshot"):
        registry.resolve("e999", dialog)
    assert read_events(registry.tracer)[-1]["payload"]["ok"] is False
