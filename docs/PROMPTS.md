# uibridge: Prompturi pentru Claude Code

**Cum le folosești**
1. Creezi folderul proiectului. Pui `CLAUDE.md` în rădăcină, `SPEC.md` și `PROMPTS.md` în `docs/`.
2. Pornești Claude Code în folder. Pentru fiecare fază: intri în **Plan mode**, lipești promptul,
   verifici planul, apoi aprobi.
3. Nu treci la faza următoare până nu e îndeplinit "Done when". La final de fază, Claude face commit.
4. După fazele 2, 3, 4 și 7 testezi tu manual (pașii sunt la final).

Prompturile sunt în engleză, pentru că specificația și codul sunt în engleză.

---

## Phase 0: Setup
```
Read CLAUDE.md and docs/SPEC.md fully. Then set up the project:
- uv project, Python 3.12, src layout, package name `uibridge`, console script `uibridge`.
- Dependencies: pydantic, xa11y, pywin32, psutil, playwright, mcp, fastapi, uvicorn, pytest.
- .gitignore with traces/, .venv/.
- Create docs/FINDINGS.md with a table header: date | app | framework | adapter | result | notes.
- Before writing any code that uses them, look up current docs (Context7 or official) for:
  xa11y Python API, mcp Python SDK (server + tools), Playwright Python connect_over_cdp and
  accessibility/aria snapshot APIs. Write a short docs/API_NOTES.md with the exact calls we
  will use, versions installed, and anything deprecated to avoid.
- Write a 10-line probe script that uses xa11y to list top-level windows and print the first
  2 levels of one window. Run it and paste the output into API_NOTES.md.
Done when: `uv sync` works, the probe runs on this machine, API_NOTES.md exists. Commit.
```

## Phase 1: Core, tracer, serializer
```
Implement the core from docs/SPEC.md sections 3, 4, 5, 7, 9 (tracer only):
- core/model.py: Node, Role, State, ActionKind, Action, ActionResult, NodeMeta (pydantic v2).
- core/adapter.py: Adapter protocol, WindowTarget.
- core/refs.py: RefRegistry with locator + fingerprint, ref reuse across snapshots, resolution
  with explicit ambiguity errors.
- core/serializer.py: AI view exactly as specified, with pruning rules and a token estimate.
  Return the text plus pruning stats (nodes in, nodes out, hoisted, truncated).
- core/tracer.py: JSONL session writer, sidecar files for large payloads, event ids. Every
  public function in core that reads or acts must emit trace events.
- adapters/fake.py: FakeAdapter built from JSON fixture trees (create 3 fixtures: a Win32-like
  dialog, a tree-heavy app, a long list of 300 items).
- Tests: role mapping, ref stability across two snapshots with small changes, ambiguity error,
  pruning rules, truncation, tracer output shape. Print token estimates for all fixtures.
Done when: all tests pass and `pytest -s` prints fixture AI views + token counts. Commit.
```

## Phase 2: Base adapter, detector, CLI
```
Implement docs/SPEC.md sections 6 and 7 (base_uia only):
- adapters/base_uia.py on xa11y: snapshot, read, raw_dump, act. Map UIA control types to our
  roles and UIA patterns to actions. Action strategy 1 (patterns) and 3 (synthetic input)
  only for now. Record the strategy in ActionResult.
- core/detector.py: signals table from SPEC section 6 using pywin32 + psutil. Score adapters,
  pick one, fall back to base_uia. Emit a `detect` trace event with all signals.
- core/router.py: routes snapshot/read/act to the chosen adapter, owns RefRegistry and Tracer.
- cli.py: `uibridge apps`, `uibridge inspect --app <regex> [--depth N]`,
  `uibridge act --app <regex> --ref eN --action X [--value V] [--dry-run]`.
  `inspect` writes raw.json, normalized.json, ai_view.txt, detect.json into a new traces/
  session folder and prints the AI view + token estimate.
- Test live on Calculator and Notepad: inspect both, then via CLI compute 12*7 in Calculator
  by refs only, and read the result back. Record both in docs/FINDINGS.md.
Done when: the Calculator scenario works from the CLI and both apps have FINDINGS rows. Commit.
```

## Phase 3: MCP server + dry-run
```
Implement docs/SPEC.md section 8 with the official mcp Python SDK (stdio transport):
- Tools: list_apps, launch, attach, snapshot, expand, read, act, wait_for, all going through
  the router so everything is traced.
- Tool descriptions: short and precise. Explain refs, the role vocabulary, and the loop
  "snapshot → act → snapshot again to verify". Tell the agent to prefer `expand` over large
  depth.
- Flags: --debug (trace id appended to every response), --dry-run (act resolves and logs the
  native call but does not execute; response says DRY-RUN clearly).
- Errors returned to the agent must be actionable: stale ref → "re-run snapshot"; ambiguous
  → list candidates with refs.
- Add a README section: how to register the server in Claude Code
  (`claude mcp add uibridge -- uv run --directory <path> uibridge serve --debug`) and in
  Claude Desktop config.
- Write an MCP smoke test that spawns the server, lists tools, attaches to a FakeAdapter app
  (env flag UIBRIDGE_FAKE=1) and runs snapshot + act.
Done when: smoke test passes and the server registers in Claude Code. Commit.
```

## Phase 4: Debug viewer
```
Implement the viewer from docs/SPEC.md section 9:
- FastAPI on 127.0.0.1:8765, plain HTML + vanilla JS, no build step.
- Pages: session list; session timeline with filters (kind, ref, errors only).
- Snapshot view: three synchronized panes Raw | Normalized | AI view. Clicking a ref
  highlights the same element in all three panes. Show token estimate and pruning stats.
- Action view: request → resolution → native call (strategy) → result → diff, on one screen.
- Live mode: if a session is active, new events appear without refresh (polling is fine).
- `uibridge viewer` opens the browser.
Done when: I can open a trace from Phase 2 and Phase 3 and follow every step. Commit.
```

## Phase 5: Win32 adapter
```
Implement adapters/win32.py from docs/SPEC.md (pywin32 only):
- Enumerate child windows, class names, control ids, text. Map classic classes (Button,
  Edit, ComboBox, ListBox, SysTreeView32, SysListView32, #32770…) to roles.
- Router composition: for windows detected as win32, merge win32 data into the base_uia tree
  (fill missing names/values, fix roles) instead of replacing it. Record in meta which
  adapter provided each field.
- Action strategy 2: BM_CLICK, WM_SETTEXT, CB_SETCURSEL/LB_SETCURSEL, tree selection
  messages where safe. Fall back to strategy 1/3 and record why.
- Live test: regedit (navigate to HKEY_CURRENT_USER\Software, read values table, no writes),
  charmap (pick a character, copy), Run dialog (type `notepad`, press OK).
  Record FINDINGS rows including raw vs AI view node counts.
Done when: all three scenarios pass via MCP from Claude Code with --debug. Commit.
```

## Phase 6: CDP adapter (Electron + WebView2)
```
Implement adapters/cdp.py from docs/SPEC.md:
- `launch` with with_cdp=true starts the app with a free debugging port bound to 127.0.0.1
  (Electron flag; WebView2 env var set before launch). Detect the endpoint, connect with
  Playwright connect_over_cdp. Never expose the port beyond localhost; close on detach.
- Use the current Playwright accessibility/aria snapshot API (per API_NOTES.md). Map to Node,
  keep stable refs via fingerprints, act through Playwright locators.
- Router composition: base_uia for the native window frame, cdp for the web content inside.
  The AI view shows one merged tree; meta records the source of each node.
- If the app is already running without CDP, say so clearly and offer `launch` instead.
- Live test: VS Code (open command palette, run "View: Toggle Word Wrap", read the setting),
  plus one more Electron or WebView2 app. FINDINGS rows for each.
Done when: VS Code scenario passes via MCP. Commit.
```

## Phase 7: Test matrix and report
```
Run the full test matrix from docs/SPEC.md section 10 through MCP with --debug.
For each app: inspect, run the scenario, then fill docs/FINDINGS.md.
Then write docs/REPORT.md:
- per framework: what works, what is missing, token cost of typical snapshots,
  strategy distribution (pattern / native message / synthetic input);
- top 10 failures ranked by impact, each with the trace path;
- concrete recommendations for Phase 2.
Do not fix things in this phase, only measure and report.
Done when: REPORT.md is complete and every row links to a trace folder. Commit.
```

---

## Ce verifici tu manual (debug mode)

**După Phase 2:**
- `uibridge apps`: framework-ul detectat e corect pentru aplicațiile tale?
- `uibridge inspect`: deschide `raw.json` lângă `ai_view.txt`. Lipsește ceva important? E ceva zgomot care n-ar trebui să ajungă la AI?

**După Phase 3:** conectează serverul în Claude Code, cere-i o sarcină simplă și repet-o cu `--dry-run`. Verifică:
- ce a "văzut" AI-ul (răspunsurile `snapshot`);
- ce a decis să facă;
- ce comandă nativă ar fi rulat.

**După Phase 4:** în viewer, pentru fiecare acțiune: ref → element găsit → strategie → efect. Dacă strategia e des "synthetic input", traducerea nu e încă suficient de semantică. Asta e cel mai important indicator.

**După Phase 7:** citește `REPORT.md` împreună cu mine și decidem Phase 2.
