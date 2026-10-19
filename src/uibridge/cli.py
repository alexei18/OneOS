"""uibridge CLI: apps / inspect / act. Each command is its own traced session under traces/."""

from __future__ import annotations

import argparse
import json
import re
import sys

from pydantic import ValidationError

from uibridge import backends
from uibridge.core.adapter import WindowTarget
from uibridge.core.model import Action, ActionKind
from uibridge.core.refs import RefResolutionError
from uibridge.core.router import Router
from uibridge.core.tracer import Tracer

def _router(app: str) -> Router:
    traces = backends.traces_dir()
    return Router(Tracer(traces, app), backends.factories(), traces / "_refs")


def _pick(pattern: str) -> WindowTarget | None:
    windows = backends.windows()
    exact = [w for w in windows if w.app_id == pattern]
    matches = exact or [w for w in windows if re.search(pattern, w.title, re.IGNORECASE)]
    if len(matches) == 1:
        return matches[0]
    what = "no window matches" if not matches else f"{len(matches)} windows match"
    print(f"error: {what} --app {pattern!r}; pass a more specific title regex or an app_id:", file=sys.stderr)
    for w in matches or windows:
        print(f"  {w.app_id:<10} {w.process_name:<24} {w.title}", file=sys.stderr)
    return None


def cmd_apps(_: argparse.Namespace) -> int:
    router = _router("apps")
    rows = [(w, router.detect(w)) for w in backends.windows()]
    print(f"{'app_id':<10} {'pid':>6}  {'process':<24} {'framework':<10} {'adapter':<22} title")
    for w, d in rows:
        adapter = d.chosen if d.preferred == d.chosen else f"{d.preferred}->{d.chosen}"
        print(f"{w.app_id:<10} {w.pid:>6}  {w.process_name:<24} {d.framework:<10} {adapter:<22} {w.title}")
    print(f"\ntrace: {router.tracer.session_dir}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    target = _pick(args.app)
    if target is None:
        return 2
    router = _router(target.process_name)
    attached = router.attach(target)
    result = router.snapshot(target.app_id, depth=args.depth, include_offscreen=args.offscreen)
    out = router.tracer.session_dir
    (out / "raw.json").write_text(json.dumps(result.raw, ensure_ascii=False, indent=1, default=str),
                                  encoding="utf-8")
    (out / "normalized.json").write_text(result.tree.model_dump_json(indent=1), encoding="utf-8")
    (out / "ai_view.txt").write_text(result.view.text, encoding="utf-8")
    (out / "detect.json").write_text(json.dumps({"target": target.model_dump(), **attached.detection.model_dump()},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    print(result.view.text)
    s = result.view.stats
    print(f"\n~{result.view.tokens} tokens | nodes {s.nodes_in} -> {s.nodes_out} | hoisted {s.hoisted}, "
          f"hidden {s.hidden}, truncated {s.truncated}, collapsed {s.collapsed}, glyphs {s.glyphs}")
    print(f"trace: {out}")
    return 0


def cmd_act(args: argparse.Namespace) -> int:
    try:
        action = Action(kind=ActionKind(args.action), value=args.value)
    except ValidationError as e:
        print(f"error: {e.errors()[0]['msg']}", file=sys.stderr)
        return 2
    target = _pick(args.app)
    if target is None:
        return 2
    router = _router(target.process_name)
    router.attach(target)
    try:
        result = router.act(target.app_id, args.ref, action, dry_run=args.dry_run)
    except RefResolutionError as e:
        print(f"FAILED: {e}")
        print(f"trace: {router.tracer.session_dir}")
        return 1
    print(f"{'ok' if result.ok else 'FAILED'}  strategy={result.strategy}  {result.duration_ms:.0f} ms")
    print(f"native: {result.native_call}")
    if result.error:
        print(f"error: {result.error}")
    diff = router.last_diff
    if diff:
        for ref, fields in diff.changed.items():
            for field, (old, new) in fields.items():
                print(f"  changed {ref} {field}: {old!r} -> {new!r}")
        if diff.new:
            print(f"  added: {', '.join(diff.new)}")
        if diff.gone:
            print(f"  removed: {', '.join(diff.gone)}")
    print(f"trace: {router.tracer.session_dir}")
    return 0 if result.ok else 1


def cmd_serve(args: argparse.Namespace) -> int:
    from uibridge.mcp_server import serve  # stdout is the MCP channel from here on: no prints

    serve(debug=args.debug, dry_run=args.dry_run)
    return 0


def cmd_viewer(args: argparse.Namespace) -> int:
    from uibridge.viewer.app import serve

    serve(backends.traces_dir(), port=args.port, open_browser=not args.no_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # window titles contain non-cp1252 characters
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="uibridge", description="AI <-> native Windows app bridge")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("apps", help="list attachable windows with detected framework and adapter")

    p = sub.add_parser("inspect", help="write raw / normalized / AI view of a window to a trace folder")
    p.add_argument("--app", required=True, help="title regex (case-insensitive) or app_id")
    p.add_argument("--depth", type=int, default=4, help="AI view depth (default 4)")
    p.add_argument("--offscreen", action="store_true", help="include offscreen elements")

    a = sub.add_parser("act", help="run one action on a ref from the last inspect")
    a.add_argument("--app", required=True, help="title regex (case-insensitive) or app_id")
    a.add_argument("--ref", required=True, help="element ref, e.g. e12")
    a.add_argument("--action", required=True, choices=[k.value for k in ActionKind])
    a.add_argument("--value", help="text for set_value/type_text, keys for press_keys (e.g. Ctrl+S)")
    a.add_argument("--dry-run", action="store_true", help="resolve and log the native call, do not execute")

    s = sub.add_parser("serve", help="run the MCP server on stdio")
    s.add_argument("--debug", action="store_true", help="append the trace event id to every response")
    s.add_argument("--dry-run", action="store_true", help="never execute actions, only resolve and log them")

    v = sub.add_parser("viewer", help="debug viewer for traces on http://127.0.0.1:8765")
    v.add_argument("--port", type=int, default=8765)
    v.add_argument("--no-browser", action="store_true", help="do not open the browser")

    args = parser.parse_args(argv)
    return {"apps": cmd_apps, "inspect": cmd_inspect, "act": cmd_act, "serve": cmd_serve,
            "viewer": cmd_viewer}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
