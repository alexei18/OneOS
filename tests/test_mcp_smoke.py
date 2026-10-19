"""MCP smoke test: spawn `uibridge serve` over stdio with the fake backend and drive it like an agent."""

import json
import os
import re
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

REPO = Path(__file__).resolve().parents[1]
TOOLS = {"list_apps", "launch", "attach", "snapshot", "expand", "read", "act", "wait_for"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def server(tmp_path: Path, *flags: str) -> StdioServerParameters:
    env = {**os.environ, "UIBRIDGE_FAKE": "1", "UIBRIDGE_TRACES": str(tmp_path / "traces")}
    return StdioServerParameters(command="uv", args=["run", "uibridge", "serve", *flags], env=env, cwd=REPO)


def text(result) -> str:
    return "\n".join(block.text for block in result.content)


def ref_of(view: str, pattern: str) -> str:
    match = re.search(pattern + r".*?\[(e\d+)\]", view)
    assert match, f"{pattern!r} not in:\n{view}"
    return match.group(1)


@pytest.mark.anyio
async def test_agent_loop_over_stdio(tmp_path):
    async with Client(server(tmp_path, "--debug"), read_timeout_seconds=60) as client:
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == TOOLS
        assert all(t.description for t in tools)

        apps = text(await client.call_tool("list_apps", {}))
        assert "fake-4120" in apps and "'Run'" in apps and "fake-7300" in apps

        attached = text(await client.call_tool("attach", {"app_id": "fake-4120"}))
        assert attached.startswith('app "Run" [win32] pid=4120')
        assert re.search(r"\ntrace: ev\d{5}$", attached)

        view = text(await client.call_tool("snapshot", {"app_id": "fake-4120"}))
        combo = ref_of(view, '- combobox "Open:"')
        acted = await client.call_tool("act", {"ref": combo, "action": "set_value", "value": "notepad"})
        assert not acted.is_error
        assert "ok  strategy=pattern" in text(acted)
        assert f"changed {combo} value: '' -> 'notepad'" in text(acted)

        assert f'combobox "Open:" [{combo}] focused collapsed: "notepad"' in text(
            await client.call_tool("snapshot", {"app_id": "fake-4120"}))
        assert "value: 'notepad'" in text(await client.call_tool("read", {"ref": combo}))

        root = ref_of(view, '- dialog "Run"')
        expanded = text(await client.call_tool("expand", {"ref": root, "depth": 1}))
        assert '  - button "Cancel"' in expanded and "    - textbox" not in expanded  # depth = levels below the ref

        found = text(await client.call_tool("wait_for", {"app_id": "fake-4120", "role": "button", "name": "browse"}))
        assert found.startswith('- button "Browse..."')

        stale = await client.call_tool("act", {"ref": "e999", "action": "click"})
        assert stale.is_error and "run snapshot" in text(stale)

        disabled = await client.call_tool("act", {"ref": ref_of(view, '- button "OK"'), "action": "click"})
        assert disabled.is_error and "FAILED" in text(disabled) and "disabled" in text(disabled)

    events = [json.loads(line) for line in next((tmp_path / "traces").glob("*_mcp/events.jsonl")).open(encoding="utf-8")]
    kinds = [e["kind"] for e in events]
    assert kinds.count("mcp_request") == kinds.count("mcp_response") == 10
    assert "action_native" in kinds and "post_action_diff" in kinds


@pytest.mark.anyio
async def test_dry_run_never_executes(tmp_path):
    async with Client(server(tmp_path, "--dry-run"), read_timeout_seconds=60) as client:
        view = text(await client.call_tool("snapshot", {"app_id": "fake-4120"}))
        combo = ref_of(view, '- combobox "Open:"')
        acted = text(await client.call_tool("act", {"ref": combo, "action": "set_value", "value": "notepad"}))
        assert acted.startswith("DRY-RUN: nothing executed")
        assert "native: DRY-RUN: FakeAdapter.set_value" in acted
        assert "value: ''" in text(await client.call_tool("read", {"ref": combo}))
        assert "trace:" not in acted  # no --debug
