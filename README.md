# uibridge

Turns running Windows desktop apps into a compact semantic UI tree an AI agent can read, and turns the
agent's actions back into real native interactions. No screenshots, no OCR: UI Automation (and later
Win32 messages and CDP) only. Exposed to agents over MCP. Design: `docs/SPEC.md`.

```
uv sync
uv run uibridge apps                                   # windows + detected framework/adapter
uv run uibridge inspect --app "Calculator" --depth 8   # raw / normalized / AI view into traces/
uv run uibridge act --app "Calculator" --ref e64 --action click [--dry-run]
uv run uibridge serve [--debug] [--dry-run]            # MCP server on stdio
```

## MCP server

Tools: `list_apps`, `launch`, `attach`, `snapshot`, `expand`, `read`, `act`, `wait_for`.
The agent loop is: `list_apps` → `attach(app_id)` → `snapshot` → `act(ref, action)` → `snapshot` again to verify.

- `--debug`: every response ends with `trace: evNNNNN`, the event id of that call in `traces/<ts>_mcp/events.jsonl`.
- `--dry-run`: `act` resolves the element and logs the exact native call, but executes nothing.
  Responses start with `DRY-RUN: nothing executed`.

### Register in Claude Code

```
claude mcp add uibridge -- uv run --directory D:\Proiecte\OneOS uibridge serve --debug
claude mcp list
```

For a dry-run variant next to it:
`claude mcp add uibridge-dry -- uv run --directory D:\Proiecte\OneOS uibridge serve --debug --dry-run`

### Register in Claude Desktop

`%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "uibridge": {
      "command": "uv",
      "args": ["run", "--directory", "D:\Proiecte\OneOS", "uibridge", "serve", "--debug"]
    }
  }
}
```

## Traces and environment

- Every CLI command and every server process writes one session folder: `traces/<YYYYMMDD-HHMMSS>_<app>/`
  with `events.jsonl` and `raw_<event>.json` sidecars. `inspect` also writes `raw.json`, `normalized.json`,
  `ai_view.txt`, `detect.json`. Refs survive across processes via `traces/_refs/`.
- `UIBRIDGE_TRACES=<dir>`: trace folder (default `traces`).
- `UIBRIDGE_FAKE=1`: serve the JSON fixtures in `src/uibridge/adapters/fixtures/` as if they were open windows
  (tests, demos; used by the MCP smoke test).

## Tests

```
uv run pytest        # unit tests + MCP smoke test (spawns the server with UIBRIDGE_FAKE=1)
uv run pytest -s     # also prints the fixture AI views with token counts
```
