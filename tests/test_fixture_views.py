"""Prints each fixture's AI view and token estimates (run with `pytest -s`)."""

import sys

import pytest

from conftest import FIXTURE_NAMES, snap
from uibridge.adapters.fake import FakeAdapter
from uibridge.core.serializer import app_header, estimate_tokens, render


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_ai_view(name, registry, tracer):
    adapter = FakeAdapter.load(name)
    tree = snap(adapter, registry)
    app = adapter.app
    v = render(tree, tracer, app_id=name, header=app_header(app["title"], app["framework"], app["pid"]))
    json_tokens = estimate_tokens(tree.model_dump_json(exclude_none=True))

    out = sys.stdout
    out.reconfigure(encoding="utf-8")
    print(f"\n===== {name} =====")
    print(v.text)
    print(f"--- tokens: normalized JSON {json_tokens} -> AI view {v.tokens} "
          f"({v.tokens / json_tokens:.1%}) | {v.stats}")
    assert v.tokens < json_tokens
