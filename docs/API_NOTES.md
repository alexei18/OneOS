# API notes

Verified 2026-10-19 against Context7 docs **and** the installed packages (type stubs,
`inspect.signature`, live probe). Where they differ, the installed package wins.

## Installed versions (Python 3.12.15, uv 0.12.23)

| package | version | used for |
|---|---|---|
| xa11y | 0.15.2 | base_uia adapter (UI Automation) |
| mcp | 2.3.0 | MCP server (stdio) + smoke-test client |
| playwright | 1.64.0 | cdp adapter (`connect_over_cdp`) |
| pywin32 | 312 | win32 adapter, detector |
| psutil | 7.2.2 | detector (process name, modules) |
| pydantic | 2.14.1 | models |
| fastapi | 0.147.0 | debug viewer |
| uvicorn | 0.54.0 | debug viewer |
| pytest | 9.1.1 | tests (dev group) |

No `playwright install` needed: we only attach over CDP to apps we launch ourselves.

---

## xa11y 0.15.2

Source of truth: `.venv/Lib/site-packages/xa11y/_native.pyi`. Sync API, no asyncio.

### Discovery
```python
xa11y.App.list() -> list[App]                       # single enumeration, no polling
xa11y.App.by_pid(pid, *, timeout=None) -> App        # polls; attaches to the process directly on UIA
xa11y.App.by_name(name, *, timeout=None) -> App      # exact name, e.g. "Notepad", "Code", "chrome"
xa11y.App.find(predicate, *, timeout=None) -> App
xa11y.App.foreground(*, timeout=None) -> App
app.name: str; app.pid: int | None
app.windows() -> list[Element]                        # top-level window/dialog, one App node per process on Windows
app.children() / app.as_element() / app.locator(selector)
xa11y.set_default_timeout(seconds) / get_default_timeout()   # default 5 s, env XA11Y_DEFAULT_TIMEOUT
```
`timeout=0` means one attempt, no waiting.

### Element (live, lazy: each `children()` call queries the provider)
Properties: `role: str` (snake_case, e.g. `window`, `button`, `menu_bar`, `web_area`, `group`),
`name`, `value`, `description`, `numeric_value`, `min_value`, `max_value`,
`stable_id: str | None`, `pid`, `actions: list[str]`, `bounds: Rect | None` (logical px),
`raw: dict`, `enabled`, `visible`, `focused`, `active`, `checked: 'on'|'off'|'mixed'|None`,
`selected`, `expanded: bool | None`, `editable`, `focusable`, `modal`, `required`, `busy`,
`minimized/maximized/fullscreen: bool | None`.

Navigation: `children()`, `parent()`, `tree(max_depth=None) -> dict` (keys: `role, name, value,
children` only), `dump(max_depth=None) -> str`. `max_depth=0` = node only.

Actions (Element and Locator): `press, focus, blur, toggle, expand, collapse, select,
show_menu, scroll_into_view, increment, decrement, set_value(str), set_numeric_value(float),
type_text(str), select_text(start, end), perform_action(name)`; window ops `activate, close,
minimize, maximize, restore, move_to, resize_to`. `el.actions` lists what the element supports.

**Observed on Windows (probe below):**
- `stable_id` = `"hwnd:0x80692"` for top-level windows, otherwise the UIA AutomationId
  (`"TitleBar"`, `"Minimize-Restore"`) or `None`. Not unique by itself. It is a fingerprint
  input, not a locator.
- `raw` keys seen: `control_type_id` (UIA ControlTypeId, e.g. 50032 Window, 50000 Button,
  50037 TitleBar), `class_name`, `automation_id`, `uia_name`, `uia_value`. Use
  `control_type_id` → `meta.native_role`, `class_name` → `meta.class_name`.
- No UIA RuntimeId exposed. Locators will have to be a selector path plus the fingerprint (Phase 2).
- `tree()` drops states/actions. For the raw dump we must walk `children()` ourselves and
  read properties + `raw`.

### Locator (re-resolves on each call, auto-waits up to default timeout)
```python
app.locator("button[name='OK']")       # CSS-like: role, [name='x'], ^= *=, '>' child, descendant, :nth(n)
loc.element() / elements() / first() / nth(n) / count() / exists()
loc.child(selector) / loc.descendant(selector)
loc.wait_visible|wait_attached|wait_enabled|...(timeout=None) -> Element
loc.wait_until(predicate, timeout=None)
```

### Errors
`XA11yError` base; `SelectorNotMatchedError` (has `.candidates`, `.scope`, `.last_observed`),
`TimeoutError` (same diagnosis fields + `.elapsed`), `ActionNotSupportedError`,
`InvalidSelectorError`, `InvalidActionDataError`, `PlatformError`, `PermissionDeniedError`,
`AccessibilityNotEnabledError`. Map these to `ActionResult.error` and pass candidates through.

### Input simulation (action strategy 3 only)
`xa11y.input_sim` / `InputSim`: `click(target, button=, count=)`, `double_click`, `right_click`,
`press(key)`, `chord(key, held=[...])`, `type_text(text)`, `scroll(target, dx, dy)`.
Keys: Pascal names (`"Enter"`, `"F5"`), modifiers `"Shift" "Ctrl" "Alt" "Meta"`.

### Avoid
- `xa11y.screenshot`, `Screenshot`, `LegendEntry`, `Omission`: vision. **Forbidden** (CLAUDE.md rule 2).
- `App.focused`: deprecated, use `App.is_foreground`.
- `enter_fullscreen` raises on Windows.

---

## mcp Python SDK 2.3.0

**`FastMCP` is gone in 2.x.** `mcp.server.fastmcp` raises `ModuleNotFoundError` on import.
Use `MCPServer`.

### Server (stdio)
```python
from mcp.server.mcpserver import MCPServer        # also re-exported: from mcp.server import MCPServer

mcp = MCPServer("uibridge", instructions="...", version="0.1.0")

@mcp.tool(name=None, title=None, description=None, annotations=None, structured_output=None)
def snapshot(app_id: str, ref: str | None = None, depth: int = 4) -> str:
    """Docstring becomes the description if description= is not given."""
    ...

mcp.run()            # transport="stdio" is the default; blocks
# async alternative: await mcp.run_stdio_async()
```
- The input schema comes from type hints, as in v1. A `str` return becomes a text content block.
- `lifespan=` constructor arg is unchanged (`MCPServer[State]`). Use it to own Router/Tracer.
- Context: declare a `ctx: Context` parameter. `get_context()` is removed, `ctx.fastmcp` → `ctx.mcp_server`.
- Exception base `FastMCPError` → `MCPServerError`.
- stdio: **never print to stdout** in the server process; it is the protocol channel. Log to stderr/tracer.

### Client (smoke test, Phase 3)
```python
from mcp import Client, StdioServerParameters

params = StdioServerParameters(command="uv", args=["run", "uibridge", "serve"], env={...})
async with Client(params) as client:          # spawns subprocess, negotiates protocol
    tools = await client.list_tools()
    res = await client.call_tool("snapshot", {"app_id": "fake-1"})
    # res.content[0].text ; res.structured_content (snake_case in v2)

async with Client(mcp) as client: ...         # in-memory, no subprocess (unit tests)
```
Async tests: use the anyio pytest plugin (anyio is already installed with mcp): `@pytest.mark.anyio`.
Low-level form `stdio_client(params)` + `ClientSession(read, write)` + `await session.initialize()`
still exists, but `Client` is the v2 way.

### Avoid
- `mcp.server.fastmcp.*`, `create_connected_server_and_client_session` (removed).
- `timedelta` timeouts: v2 takes float seconds (`read_timeout_seconds`).
- camelCase result attributes (`structuredContent`). v2 uses snake_case.

---

## Playwright Python 1.64.0

Sync API (`playwright.sync_api`) unless the MCP server forces async. Decide in Phase 6.

### Connect over CDP
```python
from playwright.sync_api import sync_playwright
pw = sync_playwright().start()
browser = pw.chromium.connect_over_cdp("http://127.0.0.1:<port>", timeout=10_000, is_local=True,
                                       no_defaults=True)
ctx = browser.contexts[0]; page = ctx.pages[0]    # Electron: one page per BrowserWindow
...
browser.close(); pw.stop()                          # on detach
```
- Signature: `connect_over_cdp(endpoint_url, *, artifacts_dir=None, headers=None, is_local=None,
  no_defaults=None, slow_mo=None, timeout=None) -> Browser`.
- `no_defaults=True`: do not apply Playwright's emulation overrides to the attached app's context.
- Always use `127.0.0.1` in the URL, never `localhost`/`0.0.0.0` (CLAUDE.md rule 6).

### Accessibility snapshot
```python
page.aria_snapshot(mode="ai", depth=None, boxes=None, timeout=None) -> str   # YAML
locator.aria_snapshot(mode="ai", depth=..., boxes=..., timeout=...) -> str
```
- `mode="ai"` adds `[ref=eN]` element refs and includes iframes. It does not wait, and throws if
  the locator matches nothing. `mode="default"` is the assertion format.
- Output is YAML text (`- role "name":` + children). We parse it into `Node`.

### Acting
```python
page.get_by_role("button", name="Run", exact=True).click()
page.get_by_role(role, *, checked=, disabled=, exact=, expanded=, include_hidden=, level=,
                 name=, pressed=, selected=)
locator.fill(text) / press(keys) / check() / uncheck() / select_option(...)
```

### Avoid
- `page.accessibility.snapshot()`: **removed** (`Page.accessibility` does not exist in 1.64).
- `playwright install` / bundled browsers: not needed.

### Open questions (probe in Phase 6)
- Can AI-mode refs be resolved back with `page.locator("aria-ref=e5")`? The public docs don't
  document it, so probe it, otherwise act via `get_by_role(role, name=)` + fingerprint.
- Are ai-mode refs stable across two snapshots? Probably not. We keep our own refs anyway.

---

## Probe output

`uv run python scripts/probe_xa11y.py`, 2026-10-19, Windows 11 Pro 10.0.26200, Romanian UI.
Notepad's editor `value` (the full document text) is truncated here.

```
 12892  Notepad                      window   'varianta_1_examen_stat.txt - Notepad'
 11260  Code                         window   '◑ phase-0-project-setup - OneOS - Visual Studio Code'
 15680  chrome                       window   'alexei18/OneOS – Google Chrome'
  8640  WindowsTerminal              window   'Windows PowerShell'
  7736  explorer                     window   'OneOS - Explorer'

--- Notepad / 'varianta_1_examen_stat.txt - Notepad' (max_depth=2) ---
window "varianta_1_examen_stat.txt - Notepad"
  group
    group
  group
    web_area "Editor text" value="// VARIANTA 1 - Examen de stat - … [~11 000 chars, truncated]"
  group
    group
  group
    group
  group
    group
  group value="varianta_1_examen_stat.txt - Notepad"
    menu_bar "Sistem"
    button "Minimizare"
    button "Maximizare"
    button "Închidere"
```

What the probe taught us, for the next phases:
- **Console encoding:** printing window titles crashed with `UnicodeEncodeError` (cp1252, `◑` in
  the VS Code title). The CLI must `sys.stdout.reconfigure(encoding="utf-8")` or set `PYTHONUTF8=1`.
- **Values can be huge:** the Win11 Notepad editor is a `web_area` whose `value` is the whole
  document (~11k chars, bare `\r` line breaks). The serializer must truncate `value` and escape
  control characters (Phase 1).
- **Noise:** 5 unnamed single-child `group`s directly under the window. That is exactly the hoisting
  rule from SPEC §5.
- Names are localized (`"Minimizare"`, `"Sistem"`). Do not match on English names; use
  `automation_id` (`Minimize-Restore`, `SystemMenuBar`) in fingerprints.

---

## Phase 2 additions (2026-10-19)

### UIA ControlTypeIds
`raw["control_type_id"]` is the UIA ControlTypeId (50000 Button … 50040 AppBar), verified against
https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controltype-ids. Table: `adapters/uia_roles.py`.
We map from the id, not from `Element.role`: xa11y's snake_case role is lossy (Pane 50033 and
TitleBar 50037 both come back as `group`; Edit/Document as `text_field`/`web_area`).

xa11y role strings seen: `window, dialog, group, web_area, tab_group, tab, list, static_text, button,
menu_bar, menu_item, progress_bar, image`.
xa11y action strings seen: `press, focus, set_value, toggle, expand, collapse, select, activate, close,
minimize, maximize, restore, move_to, resize_to`. `show_menu`, `scroll_into_view`, `increment`,
`decrement` exist in the stub.

### Synthetic input
- `xa11y.input_sim()` is a **factory function** returning an `InputSim`; it is not a module-level object.
- `Element.activate()` raises `PlatformError: SetForegroundWindow was denied (foreground lock)` when our
  process may not take the foreground (normal when run from a terminal). Never assume activation worked:
  base_uia hit-tests mouse targets and checks the foreground window before keys (FINDINGS 2026-10-19).

### Windows
- UWP apps (Calculator) appear in xa11y under the **ApplicationFrameHost** process; the window's
  `stable_id` is the top-level `ApplicationFrameWindow` hwnd. Find windows by `stable_id == "hwnd:0x…"`
  (`App.by_pid(pid, timeout=0).windows()` first, then a scan of `App.list()`).
- Cloaked UWP `Windows.UI.Core.CoreWindow` top-levels must be filtered with `DwmGetWindowAttribute(DWMWA_CLOAKED=14)`.
- Electron signal: `resources/app.asar` *or* `resources/app` next to the exe or one folder below
  (VS Code: `<install>/<commit>/resources/app`). Chrome loads `chrome.dll`, Electron apps do not.
- Win11 Notepad and Terminal host WinUI content (`Microsoft.UI.Content.DesktopChildSiteBridge` /
  `Windows.UI.Composition.DesktopWindowContentBridge`); Win11 Explorer has those *and* real Win32 controls.

### Performance
Full `children()` walk with all properties: Notepad 66 nodes 0.09–0.13 s, Calculator 89 nodes 0.10–0.14 s.
`psutil.Process.memory_maps()`: 7–100 ms per process.

## Phase 3 additions: mcp 2.3.0 in practice (2026-10-19)

- Sync `@mcp.tool` functions run via `anyio.to_thread.run_sync` (worker threads). xa11y elements work
  across threads (probed), but Router/RefRegistry are not thread-safe → every tool body runs under one lock.
- `@mcp.tool(structured_output=False, description=...)` keeps a `str` return as plain text content (no
  `{"result": ...}` structured copy).
- Raise `mcp.server.mcpserver.exceptions.ToolError(msg)`: the client gets `CallToolResult(is_error=True)`
  with `msg` as text. Other exceptions are also turned into `is_error` results with `str(exc)`.
- Parameter constraints via `Annotated[int, Field(ge=1, le=12)]`; enums (`ActionKind`, `Role`) become JSON
  schema enums.
- Smoke-test client: `Client(StdioServerParameters(command="uv", args=[...], env=..., cwd=...),
  read_timeout_seconds=60)`; `(await client.list_tools()).tools`, `await client.call_tool(name, args)` →
  `.content[i].text`, `.is_error`. Tests use `@pytest.mark.anyio` with an `anyio_backend` fixture = "asyncio".
- `claude mcp add` health check spawns the server once: that leaves an empty `traces/<ts>_mcp` session.
