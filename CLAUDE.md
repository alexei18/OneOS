# uibridge: AI ↔ native app translation layer

## What this is
A local translation layer that turns running Windows desktop applications into a compact
semantic UI tree an AI agent can read, and translates the agent's actions back into real
native interactions. Exposed to agents through MCP.

Mental model: "Playwright for any desktop app". One common tree format in the middle,
one adapter per UI framework (not per app) on the native side.

- Full design: `docs/SPEC.md`. Read it before any non-trivial change.
- Build plan: `docs/PROMPTS.md`. We work phase by phase. Do not jump ahead.

## Hard rules
1. **Windows 10/11 only** for now.
2. **No vision.** No screenshots-based grounding, no OCR, no vision models. Do not add them,
   even as a fallback.
3. **Verify every third-party API against current docs before using it** (Context7 or the
   official docs). Several libraries here are young (xa11y is pre-1.0). Never guess method
   names or signatures. If docs and reality disagree, write a tiny probe script, run it,
   and trust what runs.
4. **Debug visibility is a product requirement.** Every read and every action goes through
   the `Tracer`. There are no silent code paths. If something is not in the trace, it did
   not happen.
5. **Adapter boundary is strict.** Only `src/uibridge/adapters/` may import `xa11y`,
   `win32*`, or `playwright`. Adapters return `Node` / `ActionResult` models, never raw
   native objects.
6. **CDP endpoints bind to 127.0.0.1 only** and are closed when the session ends.
7. Do **not** use the `pywinauto` or `pywinauto-core` packages.
8. When something fails on a real app, record it in `docs/FINDINGS.md` (app, framework,
   what failed, what the trace showed) before fixing it.

## Stack
Python 3.12 · uv · pydantic v2 · xa11y (base accessibility tree) · pywin32 (Win32) ·
psutil · playwright (CDP for Electron/WebView2) · mcp (official Python SDK) ·
fastapi + uvicorn (debug viewer) · pytest

## Commands
```
uv sync
uv run pytest
uv run uibridge apps                          # list attachable windows + detected framework
uv run uibridge inspect --app "<title regex>" # dump raw / normalized / AI view to traces/
uv run uibridge act --app "<regex>" --ref e12 --action click [--dry-run]
uv run uibridge serve [--debug] [--dry-run]   # MCP server (stdio)
uv run uibridge viewer                        # debug viewer on http://127.0.0.1:8765
```

## Layout
```
src/uibridge/
  core/        model.py, refs.py, serializer.py, tracer.py, detector.py, router.py, adapter.py
  adapters/    fake.py, base_uia.py, win32.py, cdp.py
  mcp_server.py
  cli.py
  viewer/      app.py + static/
tests/
docs/          SPEC.md, PROMPTS.md, FINDINGS.md
traces/        (gitignored) one folder per session
```

## Conventions
- Type hints everywhere. Pydantic models for Node, Action, ActionResult, TraceEvent.
- Small modules. New logic is tested first against `FakeAdapter`, then against real apps.
- Commit after each phase is green. Commit message: `phase N: <summary>`.
- Keep the AI view token-efficient. Every serializer change reports token estimates
  before/after on the fixture trees.
