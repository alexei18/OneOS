# uibridge Phase 1 test matrix: report

Date: 2026-10-19. uibridge at `ba707fc` (Phases 0–6) plus the Phase 7 test harness. Windows 11 Pro 10.0.26200, Romanian UI,
display scaled 125 %.

**Method.** Every scenario was run by a headless Claude Code agent (`claude -p`, only the `mcp__uibridge__*` tools) against
`uibridge serve --debug`. When the window was still open afterwards, a `uibridge inspect --depth 8` was taken. Numbers come
from `scripts/matrix_report.py`, using the manifest `docs/matrix.json`, and every result also has a row in `docs/FINDINGS.md`. Nothing was
fixed during this phase. The 12 agent runs cost $3.17 in total. Docker Desktop reuses its Phase 6 run, which used the same adapter code.

## Summary

- **13 apps, 8 frameworks: 10 pass, 3 fail.** WSL Settings passed, but only through UIA, because CDP failed.
  - **Win32 (4 apps):** regedit, charmap and the Run dialog pass, mostly through native messages. The classic Open dialog fails: a UIA
    Invoke on the button that opens it blocked the whole process.
  - **UWP / WinUI** (Calculator, Settings, Notepad): pass, almost entirely through UIA patterns.
  - **Electron:** VS Code over CDP works, but the scenario failed on VS Code-specific gaps (no editor open, the Monaco text is
    inaccessible, quick-pick rows aren't exposed). Docker can only be read through UIA, and it works.
  - **WebView2:** the CDP launch did not reach WSL Settings' WebView2, and UIA was enough.
  - **WPF and WinForms:** pass through plain UIA.
  - **Java (Swing):** invisible without the Java Access Bridge.
- **36 element actions: 80 % pattern, 11 % native message, 5 % synthetic input, 2 % refused.** Synthetic input was only
  needed for a custom grid without UIA children (charmap) and a hover-only button (Notepad tab close).
- **Token cost:** typical snapshots are **50–200 tokens** (median over the 53 AI views of the matrix). The large ones are UIA views of
  rich apps at depth 8: Settings 1361, PowerShell ISE 1578, Docker (Chromium through UIA) 747 median / 1236 max.

## Results per app

| app | framework | result | actions | strategies | fallbacks | AI-view tokens | raw nodes → lines (largest view) | failed calls | traces |
|---|---|---|---|---|---|---|---|---|---|
| regedit | win32 | PASS | 1 | pattern 1 (100%) | 1 | median 144, max 428 (5 views) | 192 → 6 | 0 | `traces/20261019-205302_mcp`, `traces/20261019-205333_regedit-exe` |
| charmap | win32 | PASS | 4 | pattern 1 (25%), native_message 2 (50%), synthetic_input 1 (25%) | 2 | median 202, max 216 (4 views) | 28 → 16 | 0 | `traces/20261019-205347_mcp`, `traces/20261019-205415_charmap-exe` |
| Run dialog | win32 | PASS | 2 | native_message 2 (100%) | 0 | median 116, max 132 (2 views) | 14 → 9 | 0 | `traces/20261019-205422_mcp` |
| classic Open dialog (WinForms OpenFileDialog) | win32 | FAIL | 1 | pattern 1 (100%) | 0 | median 75, max 125 (2 views) | 20 → 10 | 7 | `traces/20261019-205447_mcp`, `traces/20261019-205711_powershell-exe` |
| Calculator | uwp | PASS | 6 | pattern 6 (100%) | 0 | median 52, max 541 (4 views) | 89 → 3 | 0 | `traces/20261019-210043_mcp` |
| Settings (About) | uwp | PASS | 0 | — | 0 | median 51, max 1361 (3 views) | 213 → 3 | 0 | `traces/20261019-210121_mcp` |
| Notepad (Win11) | winui | PASS | 4 | pattern 3 (75%), synthetic_input 1 (25%) | 0 | median 54, max 77 (5 views) | 66 → 5 | 0 | `traces/20261019-210145_mcp` |
| VS Code (temp profile, CDP) | electron | FAIL | 6 | pattern 6 (100%) | 0 | median 141, max 554 (8 views) | 352 → 11 | 2 | `traces/20261019-210233_mcp` |
| Docker Desktop (no CDP possible) | electron | PASS | 1 | pattern 1 (100%) | 0 | median 747, max 1236 (5 views) | 254 → 34 | 0 | `traces/20261019-183315_mcp` |
| WSL Settings | webview2 | PASS (base_uia; CDP failed) | 1 | pattern 1 (100%) | 0 | median 338, max 490 (3 views) | 73 → 4 | 1 | `traces/20261019-210353_mcp`, `traces/20261019-210436_wslsettings-exe` |
| PowerShell ISE | wpf | PASS | 2 | pattern 2 (100%) | 0 | median 167, max 1578 (5 views) | 251 → 71 | 0 | `traces/20261019-210442_mcp`, `traces/20261019-210525_powershell-ise-exe` |
| WinForms demo (tests/apps) | winforms | PASS | 8 | pattern 7 (87%), none 1 (12%) | 0 | median 46, max 125 (4 views) | 20 → 10 | 1 | `traces/20261019-210533_mcp`, `traces/20261019-210609_powershell-exe` |
| jconsole (JDK 21, Swing) | java | FAIL | 0 | — | 0 | median 83, max 98 (3 views) | 7 → 2 | 2 | `traces/20261019-210615_mcp`, `traces/20261019-210725_jconsole-exe` |

"raw nodes → lines" is the largest AI view of the run: the native nodes it was built from, and the lines the agent got.
"fallbacks" counts actions where a strategy declined and the next one ran (here: win32 declining tree-view messages).

## Per framework

| framework | apps | actions | strategy distribution | fallbacks | AI-view tokens | failed MCP calls |
|---|---|---|---|---|---|---|
| win32 | regedit, charmap, Run dialog, classic Open dialog | 8 | pattern 3 (37%), native_message 4 (50%), synthetic_input 1 (12%) | 3 | median 132, max 428 (13 views) | 7 |
| uwp | Calculator, Settings | 6 | pattern 6 (100%) | 0 | median 51, max 1361 (7 views) | 0 |
| winui | Notepad | 4 | pattern 3 (75%), synthetic_input 1 (25%) | 0 | median 54, max 77 (5 views) | 0 |
| electron | VS Code (CDP), Docker Desktop (UIA) | 7 | pattern 7 (100%) | 0 | median 210, max 1236 (13 views) | 2 |
| webview2 | WSL Settings | 1 | pattern 1 (100%) | 0 | median 338, max 490 (3 views) | 1 |
| wpf | PowerShell ISE | 2 | pattern 2 (100%) | 0 | median 167, max 1578 (5 views) | 0 |
| winforms | WinForms demo | 8 | pattern 7 (87%), none 1 (12%) | 0 | median 46, max 125 (4 views) | 1 |
| java | jconsole | 0 | — | 0 | median 83, max 98 (3 views) | 2 |

### Win32 (regedit, charmap, Run dialog, classic Open dialog)
- **Works:**
  - The win32 enrichment matches every HWND-backed node exactly (via the xa11y `stable_id`).
  - Buttons go through a posted `WM_COMMAND(BN_CLICKED)`, and edits/comboboxes through `WM_SETTEXT`. Both work while the window is covered
    or not in the foreground (Run dialog, charmap Select/Copy).
  - Tree views fall back to UIA patterns with a recorded reason (regedit).
  - regedit runs without elevation (`run_as_invoker`).
- **Missing:**
  - **charmap's character grid** (`CharGridWClass`) has no UIA children. A character can only be picked by synthetic input, which works only when
    charmap is in the foreground (it was this time; in Phase 5 it was covered, and the attempt was refused).
  - **A button that opens a modal dialog through UIA Invoke blocks the target process** (failure 1).
- **Tokens:** median 132, max 428. **Strategies:** native message 50 %, pattern 37 %, synthetic 12 %.
- **Traces:** `traces/20261019-205302_mcp`, `traces/20261019-205347_mcp`, `traces/20261019-205422_mcp`, `traces/20261019-205447_mcp`.

### UWP (Calculator, Settings)
- **Works:** stable automation ids. Calculator's display keeps its ref while its name changes, and every action is a pattern. Settings opens via
  `explorer.exe ms-settings:about` and reads cleanly.
- **Missing:** a minimized UWP app exposes no tree. The header says so (Phase 3), but there is no restore tool. Views at
  depth 8 are large (Settings 1361 tokens).
- **Tokens:** median 51 (depth-2 views), max 1361. **Strategies:** pattern 100 %.
- **Traces:** `traces/20261019-210043_mcp`, `traces/20261019-210121_mcp`.

### WinUI (Notepad Win11)
- **Works:** typing and reading back through the ValuePattern; save-prompt buttons.
- **Missing:** the tab's close button only exists while the mouse hovers, so the agent had to synthetic-click a position. Menu
  items carry a redundant child button (Phase 2 note).
- **Tokens:** median 54, max 77. **Strategies:** pattern 75 %, synthetic 25 %.
- **Trace:** `traces/20261019-210145_mcp`.

### Electron (VS Code over CDP, Docker Desktop over UIA)
- **Works:**
  - **CDP launch** (separate VS Code profile): loopback-only port, closed at session end.
  - **The merged frame + web tree.**
  - **Playwright actions, which need no OS foreground.**
  - **Docker Desktop:** its UI is fully readable through UIA once the fallback is in place.
- **Missing:**
  - The Monaco editor's text (the Output panel) is "not accessible" unless VS Code's screen-reader mode is on (failure 5).
  - Quick-pick rows aren't exposed as options (failure 6).
  - Docker's backend restarts its UI without the CDP flag (failure 4).
  - Through UIA, Chromium content costs the most tokens in the matrix (Docker median 747).
- **Tokens:** VS Code (CDP) median 141, max 554. Docker (UIA) median 747, max 1236. **Strategies:** pattern 100 % (CDP actions count as pattern).
- **Traces:** `traces/20261019-210233_mcp`, `traces/20261019-183315_mcp` (plus the Phase 6 passing VS Code run `traces/20261019-182750_mcp`).

### WebView2 (WSL Settings)
- **Works:** the app is WinUI around WebView2, and its pages read fully through UIA.
- **Missing:** `launch(with_cdp)` got no endpoint in 20 s, because `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` didn't reach the WebView2 (failure 4).
  CDP on WebView2 is therefore still unproven.
- **Tokens:** median 338, max 490. **Strategies:** pattern 100 %.
- **Traces:** `traces/20261019-210353_mcp`, `traces/20261019-210436_wslsettings-exe`.

### WPF (PowerShell ISE)
- **Works:** menus, toolbar, tabs, editors, the Commands pane, the status bar. Typing into the AvalonEdit script pane reads back exactly.
- **Missing:** nothing blocking. Full views are large (251 raw nodes, 71 lines, 1578 tokens at depth 8).
- **Tokens:** median 167, max 1578. **Strategies:** pattern 100 %.
- **Traces:** `traces/20261019-210442_mcp`, `traces/20261019-210525_powershell-ise-exe`.

### WinForms (demo app)
- **Works:** textbox, checkbox, list selection, button, and reading the status label back. Every control has a name.
- **Missing:**
  - `select` with a value on a combobox has no path (strategy none). The agent expanded the list and picked the item instead.
  - WinForms buttons are Win32 HWNDs (`WindowsForms10.BUTTON…`), but they aren't enriched, so a click opening a modal dialog
    blocks (failure 1).
- **Tokens:** median 46, max 125. **Strategies:** pattern 87 %, refused 12 %.
- **Traces:** `traces/20261019-210533_mcp`, `traces/20261019-210609_powershell-exe`.

### Java (jconsole, Swing)
- **Works:** detection and the window frame.
- **Missing:** everything inside the window (7 raw nodes: the window plus its title-bar buttons). The Java Access Bridge is not enabled (failure 2).
- **Tokens:** median 83, max 98.
- **Traces:** `traces/20261019-210615_mcp`, `traces/20261019-210725_jconsole-exe`.

## Top 10 failures, ranked by impact

Impact = blocks the scenario × how common it is across real apps × whether no workaround exists.

| # | failure | impact | trace |
|---|---|---|---|
| 1 | **UIA Invoke on a button that opens a modal dialog blocks the target process's UIA.** `Element.press()` hung 22.5 s and failed, and every later UIA call to that process timed out (`0x80131505`), so the Open dialog was unreachable. It hits every WinForms/WPF app whose buttons open dialogs. | blocks; very common; no in-session workaround | `traces/20261019-205447_mcp` |
| 2 | **Java Swing UI is invisible** without the Java Access Bridge. Only the title bar is visible. | blocks the whole framework | `traces/20261019-210615_mcp` |
| 3 | **Synthetic input is refused when the target isn't in the foreground** (foreground lock, safety hit-test), and custom controls without UIA children (charmap's grid) rely on it. Charmap passed in Phase 7 only because it was in front; in Phase 5 it was covered. | blocks when covered; common for custom-drawn controls | `traces/20261019-164723_mcp`, `traces/20261019-205347_mcp` |
| 4 | **CDP doesn't reach apps whose launcher/backend starts the UI process (Docker), or WebView2 apps that ignore `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` (WSL Settings).** They fall back to UIA, which works but is the most expensive view (Docker median 747 tokens). | degrades; common for Electron/WebView2 | `traces/20261019-183315_mcp`, `traces/20261019-210353_mcp` |
| 5 | **Monaco editor content isn't readable** (VS Code Output panel, and editors in general) unless VS Code's screen-reader mode is on. | blocks reading code/logs in VS Code-like apps | `traces/20261019-210233_mcp` |
| 6 | **VS Code quick-pick rows aren't exposed** in the aria snapshot, so the agent selects blind with Enter. | degrades; every command-palette flow | `traces/20261019-210233_mcp`, `traces/20261019-182750_mcp` |
| 7 | **Combobox `select(value)` is unsupported** in base_uia (WinForms). It needs expand + pick. | extra turns; common form control | `traces/20261019-210533_mcp` |
| 8 | **Hover-only controls** (the Notepad tab close button) aren't in the tree until hovered. The agent resorted to a synthetic click at a guessed position. | extra turns + synthetic input | `traces/20261019-210145_mcp` |
| 9 | **Deep frame wrappers around composed web content.** The VS Code page starts 4 levels below the window, so the default depth-4 snapshot shows no web content, and agents need expand/depth 8 (more turns, more tokens). | extra turns on every Electron app | `traces/20261019-182750_mcp`, `traces/20261019-210233_mcp` |
| 10 | **Minimized UWP apps expose no tree.** It's explained in the header, but there is no restore action, so the agent must ask the user. | blocks while minimized; easy for the user to fix | `traces/20261019-153054_applicationframehost-exe` |

Also measured, lower impact:
- Large full views of rich apps at depth 8 (Settings 1361, ISE 1578 tokens): `traces/20261019-210121_mcp`, `traces/20261019-210442_mcp`.
- `invoke_menu` is not implemented: `traces/20261019-210233_mcp` used clicks instead.
- Items beyond 25 in a truncated list can't be reached: covered by unit tests only.

## Recommendations for Phase 2

1. **Never let a single action block the server.**
   - Run UIA Invoke with a timeout on a worker, and report "opened a modal dialog" instead of hanging.
   - Prefer the posted `BN_CLICKED` (strategy 2) for WinForms buttons too: extend the win32 enricher to the `WindowsForms10.*` classes
     (BUTTON, EDIT, COMBOBOX, LISTBOX), which are standard controls underneath.
   - Isolate UIA calls per target process, so one stuck app can't stall the others. *(failure 1)*
2. **Java Access Bridge adapter** (SPEC Phase 2: jab):
   - Detect when JAB is disabled and tell the agent and user exactly what to run (`jabswitch -enable`, then restart the app).
   - Map the JAB tree to Node, the same way UIA maps. *(failure 2)*
3. **An explicit, user-approved `activate`/`restore` tool** for when synthetic input or a minimized window needs the foreground. Keep the hit-test
   guard. For custom-drawn controls, prefer keyboard paths (focus + keys) and document per-app recipes (charmap: grid + arrows).
   *(failures 3, 10)*
4. **CDP coverage:**
   - Per-app launch recipes in a small profile file: Electron flag, `--user-data-dir` for single-instance apps, WebView2 env.
   - Test WebView2 CDP on an **unpackaged** WebView2 app. Packaged/WinUI hosts like WSL Settings may not inherit the env, which needs a probe.
   - For apps whose UI is started by a backend (Docker), document that only UIA is available and keep the fallback. *(failure 4)*
5. **VS Code / Monaco:**
   - When uibridge launches VS Code, enable accessibility in the temp profile (`editor.accessibilitySupport: "on"`), so editor and panel
     text appear in the aria tree.
   - For quick-pick lists, read the rows from the DOM (CDP) when aria exposes none. *(failures 5, 6)*
6. **More semantic actions:**
   - `select(value)` on comboboxes: UIA ExpandCollapse + SelectionItem on the matching child, or `CB_SELECTSTRING` for HWND comboboxes.
   - A `hover` action for hover-only UI.
   - Implement `invoke_menu(path)`. *(failures 7, 8)*
7. **Flatten the frame of composed apps.**
   - Render the CDP document at the top of the AI view and fold the native frame into the header (title, minimize/close refs). That saves
     4 levels on every Electron app.
   - Add an `offset` to `expand` for truncated lists. *(failure 9)*
8. **Token budget.** Give the agent a `max_tokens` on `snapshot` that lowers depth automatically and announces `… expand eX`, so a
   1.5 k-token view (Settings, ISE) is opt-in. Keep the current typical cost (50–200 tokens) as the default target.

## Reproduce
```
uv run python scripts/matrix_report.py        # recomputes both tables from traces/ using docs/matrix.json
uv run uibridge viewer                        # open any trace folder above and follow each step
```
