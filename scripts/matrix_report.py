"""Aggregate the Phase 7 test-matrix traces into the numbers used in docs/REPORT.md.

Usage: uv run python scripts/matrix_report.py [docs/matrix.json]

docs/matrix.json lists every matrix run: app, framework, scenario, result and its trace folders
(the MCP session of the agent run, plus the `uibridge inspect` session if one was taken). This
script only reads traces/ (via uibridge.viewer.traces) and prints Markdown tables to stdout.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from uibridge.viewer import traces as tr  # noqa: E402

TRACES = ROOT / "traces"
STRATEGIES = ["pattern", "native_message", "synthetic_input", "dry_run"]


def session_metrics(sid: str) -> dict:
    events = tr.read_events(TRACES, sid)
    strategies: Counter = Counter()
    fallbacks = 0
    for e in events:
        if e["kind"] != "action_native":
            continue
        p = e["payload"]
        if p.get("strategy") == "process":
            continue  # launching / closing apps is not an element action
        strategies["dry_run" if p.get("dry_run") else p.get("strategy", "?")] += 1
        fallbacks += bool(p.get("fallbacks"))
    views = [e["payload"] for e in events if e["kind"] == "snapshot_ai_view"]
    errors = [e["payload"]["error"] for e in events if e["kind"] == "mcp_response" and "error" in e["payload"]]
    tools = Counter(e["payload"]["tool"] for e in events if e["kind"] == "mcp_request")
    return {
        "actions": sum(1 for e in events if e["kind"] == "action_request" and "ref" in e["payload"]),
        "strategies": strategies,
        "fallbacks": fallbacks,
        "tokens": [v["tokens"] for v in views],
        "raw_vs_lines": [(v["stats"]["nodes_in"], v["stats"]["nodes_out"]) for v in views],
        "errors": errors,
        "tools": tools,
    }


def fmt_strategies(c: Counter) -> str:
    total = sum(c.values())
    if not total:
        return "—"
    return ", ".join(f"{k} {c[k]} ({c[k] * 100 // total}%)" for k in STRATEGIES + sorted(set(c) - set(STRATEGIES)) if c[k])


def fmt_tokens(tokens: list[int]) -> str:
    if not tokens:
        return "—"
    return f"median {int(statistics.median(tokens))}, max {max(tokens)} ({len(tokens)} views)"


def main() -> None:
    manifest = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "matrix.json").read_text("utf-8"))
    by_framework: dict[str, dict] = defaultdict(lambda: {"apps": [], "strategies": Counter(), "tokens": [],
                                                          "actions": 0, "fallbacks": 0, "errors": 0})
    print("| app | framework | result | actions | strategies | fallbacks | AI-view tokens | raw nodes → lines (largest view) "
          "| errors | trace |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for run in manifest["runs"]:
        strategies: Counter = Counter()
        tokens: list[int] = []
        errors: list[str] = []
        sizes: list[tuple[int, int]] = []
        actions = fallbacks = 0
        for sid in run["sessions"]:
            s = session_metrics(sid)
            strategies.update(s["strategies"])
            tokens += s["tokens"]
            actions += s["actions"]
            fallbacks += s["fallbacks"]
            errors += s["errors"]
            sizes += s["raw_vs_lines"]
        largest = max(sizes, key=lambda x: x[0]) if sizes else None
        f = by_framework[run["framework"]]
        f["apps"].append(run["app"])
        f["strategies"].update(strategies)
        f["tokens"] += tokens
        f["actions"] += actions
        f["fallbacks"] += fallbacks
        f["errors"] += len(errors)
        links = ", ".join(f"`traces/{sid}`" for sid in run["sessions"] + run.get("inspect", []))
        print(f"| {run['app']} | {run['framework']} | {run['result']} | {actions} | {fmt_strategies(strategies)} "
              f"| {fallbacks} | {fmt_tokens(tokens)} | {f'{largest[0]} → {largest[1]}' if largest else '—'} "
              f"| {len(errors)} | {links} |")
    print("\n| framework | apps | actions | strategy distribution | fallbacks | AI-view tokens | failed MCP calls |")
    print("|---|---|---|---|---|---|---|")
    for fw, f in by_framework.items():
        print(f"| {fw} | {', '.join(f['apps'])} | {f['actions']} | {fmt_strategies(f['strategies'])} | {f['fallbacks']} "
              f"| {fmt_tokens(f['tokens'])} | {f['errors']} |")
    total = Counter()
    for f in by_framework.values():
        total.update(f["strategies"])
    print(f"\nAll element actions: {sum(total.values())} — {fmt_strategies(total)}")


if __name__ == "__main__":
    main()
