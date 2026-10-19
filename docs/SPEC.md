# uibridge: Specification v0.1

## 1. Goal
Make any running Windows desktop application readable and operable by an AI agent through
one common, compact semantic tree, without vision. The translation is bidirectional:

```
native app ──(adapter: read)──▶ Node tree ──(serializer)──▶ AI view (text) ──▶ agent
native app ◀─(adapter: act)─── ActionResult ◀──(router)◀── act(ref, action) ◀── agent
```

Everything in both directions is recorded by the Tracer so a human can inspect exactly what
was read, what the AI saw, what it asked for, and what was executed natively.

### Non-goals (v0.1)
- macOS / Linux
- Vision, OCR, screenshots
- In-process injection (WPF/WinForms deep, Qt, Delphi). Those are Phase 2–3.
- App-specific adapters (Photoshop etc.)

## 2. Architecture

```
            ┌──────────────── MCP server (stdio) ────────────────┐
 agent ◀──▶ │ list_apps · attach · snapshot · expand · act · ... │
            └──────────────┬─────────────────────────────────────┘
                           │
          ┌────────────── core ───────────────┐
          │ Router ─ RefRegistry ─ Serializer │──▶ Tracer ──▶ traces/<session>/events.jsonl
          │ Detector                          │                      ▲
          └──────┬──────────┬──────────┬──────┘                      │
                 │          │          │                     Viewer (127.0.0.1)
            base_uia      win32       cdp        (Phase 2+: jab, dotnet, qt …)
            (xa11y)     (pywin32)  (playwright)
```

- **Detector** decides which adapter handles a window.
- **Router** sends reads/actions to that adapter. It may combine adapters: e.g. base_uia for
  the window chrome, cdp for the web content inside it.
- **RefRegistry** gives every node a short session ref and can re-find it later.
- **Serializer** turns a Node tree into the compact AI view.
- **Tracer** records every step.

## 3. Data model

### Node
| field | type | notes |
|---|---|---|
| ref | str | session-scoped, short: `e1`, `e2`… |
| role | Role | normalized vocabulary (below) |
| name | str? | accessible name / label / text |
| value | str? | current value of editable or range controls |
| description | str? | help text / tooltip if available |
| states | set[State] | enabled, disabled, focused, selected, checked, unchecked, mixed, expanded, collapsed, readonly, offscreen, modal |
| actions | list[ActionKind] | what this node supports |
| bounds | Rect? | screen coords; kept for debug, never required by actions |
| children | list[Node] | |
| child_count | int | real count, even when children are truncated |
| meta | NodeMeta | adapter, framework, native_role, automation_id, class_name, process, locator, fingerprint |

### Role vocabulary (ARIA-like, models already know it)
window, dialog, alert, menubar, menu, menuitem, toolbar, statusbar, tablist, tab, tabpanel,
button, link, textbox, checkbox, radio, switch, combobox, listbox, option, list, listitem,
tree, treeitem, table, row, cell, columnheader, rowheader, grid, slider, spinbutton,
progressbar, scrollbar, separator, heading, text, image, document, group, pane, generic.

Unmapped native roles become `generic` and keep the native role in `meta.native_role`.
Every mapping table lives in one place per adapter and is unit-tested.

### Actions
`click, double_click, right_click, focus, set_value(text), type_text(text), press_keys(keys),
toggle, check, uncheck, select, expand, collapse, scroll_into_view, invoke_menu(path)`

Each adapter executes an action with the **most semantic strategy available**, in order:
1. **Pattern / API**: UIA Invoke/Value/Toggle/ExpandCollapse/SelectionItem, CDP DOM actions.
2. **Native message**: Win32 messages (BM_CLICK, WM_SETTEXT, CB_SETCURSEL…).
3. **Synthetic input**: keyboard/mouse at the element's center. Last resort.

The strategy used is always recorded in `ActionResult.strategy` and in the trace.

### ActionResult
`ok, strategy, native_call (human-readable description of the exact native operation),
error?, duration_ms, before_fingerprint, after_fingerprint, changed_refs`

## 4. Refs
- A ref is created for every node that appears in a snapshot.
- Each ref stores:
  - `locator`: the adapter's own way to re-find the element (e.g. UIA runtime id / selector,
    HWND + control id, CDP backend node id);
  - `fingerprint`: `(role, name, automation_id, class_name, index_in_parent, ancestor path of
    role+name)`.
- On a new snapshot, nodes whose fingerprint matches an existing ref **keep the same ref**.
- On `act`: resolve by locator → if stale, re-resolve by fingerprint → if 0 or >1 matches,
  fail with an explicit error listing candidates. Never guess.

## 5. AI view (serialization)
Compact indented text, Playwright-aria-snapshot style. Example:

```
app "Registry Editor" [win32] pid=4120
- window "Registry Editor" [e1]
  - menubar [e2]: File, Edit, View, Favorites, Help
  - tree "Registry" [e5]
    - treeitem "Computer" [e6] expanded
      - treeitem "HKEY_CLASSES_ROOT" [e7] collapsed
      - treeitem "HKEY_CURRENT_USER" [e8] collapsed
      … 3 more (expand e6)
  - table "Values" [e20] rows=4
  - statusbar [e30]: "Computer"
actions: click, expand, collapse, select, set_value, type_text, press_keys
```

Rules:
- Hoist unnamed `generic`/`pane` containers that have a single child.
- Hide `offscreen` nodes unless requested.
- Default depth 4; deeper branches show `… N more (expand eX)`.
- Lists/tables longer than 25 items are truncated with a count.
- Menubars and toolbars with only simple items collapse to one line.
- Show only non-default states.
- Every snapshot logs a token estimate (chars/4 is fine for v0.1).

## 6. Detector
Inputs: top-level HWND → window class, process name, loaded modules (psutil / pywin32).

| signal | framework | adapter |
|---|---|---|
| class `Chrome_WidgetWin_*` or `electron.exe`-style process with Chromium modules | electron/chromium | cdp (+ base_uia for chrome) |
| `WebView2Loader.dll` / `msedgewebview2.exe` child | webview2 | cdp (+ base_uia) |
| class `HwndWrapper[*]` | wpf | base_uia (Phase 2: dotnet) |
| class `WindowsForms10.*` | winforms | base_uia (Phase 2: dotnet) |
| class `SunAwtFrame` / `jvm.dll` | java | base_uia (Phase 2: jab) |
| `Qt5Core.dll` / `Qt6Core.dll` | qt | base_uia (Phase 3: qt) |
| class prefix `T` + VCL modules | delphi | win32 + base_uia |
| standard Win32 classes (`#32770`, Button, Edit, SysTreeView32…) | win32 | win32 + base_uia |
| `ApplicationFrameWindow` / WinUI | uwp/winui | base_uia |
| anything else | unknown | base_uia |

The detection result (signals found, chosen adapter, why) is a trace event and is shown by
`uibridge apps`.

## 7. Adapter protocol
```python
class Adapter(Protocol):
    name: str
    def can_handle(self, target: WindowTarget) -> int        # score 0..100
    def attach(self, target: WindowTarget) -> None
    def snapshot(self, root: Ref | None, depth: int) -> Node
    def read(self, ref: Ref) -> Node
    def act(self, ref: Ref, action: Action) -> ActionResult
    def raw_dump(self, root: Ref | None, depth: int) -> dict  # native tree as-is, for debug
    def detach(self) -> None
```

### Phase 1 adapters
- **base_uia** (xa11y): universal baseline over UI Automation. Maps UIA control types to roles,
  UIA patterns to actions.
- **win32** (pywin32): EnumChildWindows, GetClassName, GetWindowText/WM_GETTEXT, control ids;
  actions via BM_CLICK, WM_SETTEXT, CB_/LB_ messages. Used to enrich or correct base_uia on
  classic controls, and as action strategy 2.
- **cdp** (playwright `connect_over_cdp`): for Electron and WebView2. Uibridge launches the app
  itself with a debugging port on 127.0.0.1 (Electron: `--remote-debugging-port`; WebView2:
  `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` set before launch). Reads the accessibility / aria
  snapshot, maps to Node, acts through Playwright locators. Verify the current Playwright API
  for accessibility snapshots before implementing; do not use deprecated calls.

## 8. MCP tools
| tool | args | returns |
|---|---|---|
| list_apps | — | windows with app_id, title, process, framework, adapter |
| launch | path_or_name, args?, with_cdp? | app_id |
| attach | app_id | AI view of top level (depth 2) |
| snapshot | app_id, ref?, depth=4, include_offscreen=false | AI view |
| expand | ref, depth=2 | AI view of that subtree |
| read | ref | full detail of one node (all states, value, actions) |
| act | ref, action, value? | short result: ok/error, strategy, what changed (refs) |
| wait_for | app_id, role?, name?, timeout_s=10 | AI view of the match or timeout error |

Tool descriptions must explain refs, the role vocabulary and the "snapshot → act → snapshot"
loop in a few lines. In `--debug` mode every response ends with `trace: <event_id>`.

## 9. Debug mode
This is how the human verifies the vision is respected.

### Trace format
`traces/<YYYYMMDD-HHMMSS>_<app>/events.jsonl`, one JSON per line, all with
`id, ts, session, kind, app_id, payload`:

| kind | payload |
|---|---|
| mcp_request / mcp_response | tool, args / exact text returned to the agent |
| detect | signals, scores per adapter, chosen adapter |
| snapshot_raw | native tree exactly as the adapter got it |
| snapshot_normalized | Node tree JSON |
| snapshot_ai_view | exact text sent to the AI + token estimate + pruning stats |
| action_request | ref, action, value |
| action_resolution | ref → locator → element found (or candidates on failure) |
| action_native | strategy + exact native call (or "DRY-RUN") |
| action_result | ActionResult |
| post_action_diff | refs added / removed / changed (state, name, value) |
| error | message, stack, context |

Large payloads (raw trees) go to sidecar files `raw_<event_id>.json`, referenced from the event.

### Modes
- `--debug`: full tracing, trace ids in MCP responses.
- `--dry-run`: actions are resolved and logged but **not executed**. Lets you see exactly
  what would happen natively.
- `uibridge inspect`: same three layers (raw / normalized / AI view) written to a folder,
  without MCP. The fastest way to check an app.
- `uibridge act`: run one action from the CLI, without an agent.

### Viewer (local web UI, 127.0.0.1 only)
- Session list (app, framework, adapter, duration, #actions, #errors).
- Timeline of events with filters by kind and by ref.
- Three synchronized panes for any snapshot: **Raw native | Normalized | AI view**. Clicking a
  ref highlights it in all three.
- Action view: request → resolution → native call → result → diff, on one screen.
- Token estimate and pruning stats per snapshot.
- Plain HTML + vanilla JS served by FastAPI. No build step.

## 10. Test matrix (Phase 1)
| framework | apps | scenario |
|---|---|---|
| Win32 | regedit, charmap, Run dialog (Win+R), classic Open/Save dialog | navigate tree, select item, type text, press button |
| UWP / WinUI | Calculator, Settings, Notepad (Win11) | compute 12×7, open a settings page, type and read back text |
| Electron | VS Code, Slack or Discord | open command palette, run a command, read a panel |
| WebView2 | any WebView2 app installed | read content, click a link/button |
| WPF / WinForms / Java (baseline only) | whatever is installed | snapshot quality check, note gaps in FINDINGS.md |

For each app record in `docs/FINDINGS.md`: detected framework, adapter, nodes in raw vs AI
view, token estimate, scenario pass/fail, what was missing or wrong.

## 11. Phases
1. Core + Fake adapter + Tracer + serializer + tests
2. base_uia + detector + `apps`/`inspect`/`act` CLI
3. MCP server + dry-run
4. Debug viewer
5. win32 adapter
6. cdp adapter
7. Test matrix run + FINDINGS report
Later: Phase 2 frameworks (Java Access Bridge, .NET in-process), Phase 3 (Qt, Delphi via Frida),
learned app profiles.
