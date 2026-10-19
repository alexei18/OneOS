import json
from pathlib import Path

import pytest

from uibridge.adapters.fake import FakeAdapter
from uibridge.core.adapter import FULL_DEPTH
from uibridge.core.model import Node
from uibridge.core.refs import RefRegistry
from uibridge.core.tracer import Tracer

FIXTURE_NAMES = ["win32_dialog", "tree_app", "long_list"]


@pytest.fixture
def tracer(tmp_path: Path):
    t = Tracer(tmp_path / "traces", "test")
    yield t
    t.close()


@pytest.fixture
def registry(tracer: Tracer) -> RefRegistry:
    return RefRegistry(tracer)


def snap(adapter: FakeAdapter, registry: RefRegistry, app_id: str = "app1") -> Node:
    tree = adapter.snapshot(None, FULL_DEPTH)
    registry.assign(tree, app_id)
    return tree


def find(tree: Node, name: str) -> Node:
    matches = [n for n in tree.walk() if n.name == name]
    assert len(matches) == 1, f"{len(matches)} nodes named {name!r}"
    return matches[0]


def read_events(tracer: Tracer) -> list[dict]:
    return [json.loads(line) for line in tracer.events_path.read_text(encoding="utf-8").splitlines()]
