"""Which windows and adapters the CLI / MCP server work with.

Real mode: visible top-level windows + base_uia. With UIBRIDGE_FAKE=1: the FakeAdapter fixtures act
as the open windows (tests, demos, the MCP smoke test). UIBRIDGE_TRACES sets the trace folder.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import cache
from pathlib import Path

from uibridge.core.adapter import Adapter, WindowTarget

FAKE_FIXTURES = ["win32_dialog", "tree_app", "long_list"]


def traces_dir() -> Path:
    return Path(os.environ.get("UIBRIDGE_TRACES", "traces"))


def fake_mode() -> bool:
    return os.environ.get("UIBRIDGE_FAKE") == "1"


@cache
def _fakes() -> dict:
    from uibridge.adapters.fake import FakeAdapter

    fakes = [FakeAdapter.load(name) for name in FAKE_FIXTURES]
    return {f.target.app_id: f for f in fakes}


def windows() -> list[WindowTarget]:
    if fake_mode():
        return [f.target for f in _fakes().values()]
    from uibridge.adapters.winenum import list_windows

    return list_windows()


def factories() -> dict[str, Callable[[WindowTarget], Adapter]]:
    if fake_mode():
        return {"base_uia": lambda target: _fakes()[target.app_id]}
    from uibridge.adapters.base_uia import BaseUIAAdapter

    return {"base_uia": lambda target: BaseUIAAdapter()}
