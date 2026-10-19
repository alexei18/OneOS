"use strict";
// uibridge viewer. Hash routes: #/  #/s/<sid>  #/s/<sid>/snap/<ev>  #/s/<sid>/act/<ev>
// Trace data is untrusted text (window titles, values): it is only ever set via textContent.

const app = document.getElementById("app");
const crumbs = document.getElementById("crumbs");
const KINDS = ["mcp_request", "mcp_response", "detect", "snapshot_raw", "snapshot_normalized", "snapshot_ai_view",
  "action_request", "action_resolution", "action_native", "action_result", "post_action_diff", "error"];
let pollTimer = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "onclick") node.addEventListener("click", v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${(await res.json().catch(() => ({}))).detail || path}`);
  return res.json();
}

function setCrumbs(...parts) {
  crumbs.replaceChildren(...parts.flatMap((p, i) => (i ? [el("span", { class: "sep" }, "›"), p] : [p])));
}

const enc = encodeURIComponent;
const sessionLink = (sid) => el("a", { href: `#/s/${enc(sid)}` }, sid);
const short = (v, n = 90) => { const s = typeof v === "string" ? v : JSON.stringify(v); return s.length > n ? s.slice(0, n) + "…" : s; };
const strategyOf = (p) => (p.dry_run ? "dry_run" : p.strategy || "?");

function isError(e) {
  const p = e.payload;
  return e.kind === "error" || (e.kind === "action_result" && !p.ok) || (e.kind === "action_resolution" && p.ok === false)
    || (e.kind === "mcp_response" && "error" in p);
}

function summary(e) {
  const p = e.payload;
  switch (e.kind) {
    case "detect": return `${p.framework} → ${p.chosen}: ${p.why}`;
    case "snapshot_raw": return `raw tree from ${p.adapter}${p.root_ref ? ` under ${p.root_ref}` : ""} (${p.bytes ?? "?"} bytes)`;
    case "snapshot_normalized": {
      const c = p.counts || {};
      return `${p.root_ref ? `subtree ${p.root_ref}: ` : ""}refs new ${c.new ?? "?"}, reused ${c.reused ?? "?"}, gone ${c.gone ?? "?"}, changed ${c.changed ?? 0}`;
    }
    case "snapshot_ai_view": return `${p.tokens} tokens · nodes ${p.stats?.nodes_in} → ${p.stats?.nodes_out} · ${short(p.text.split("\n")[0], 70)}`;
    case "action_request": return p.launch ? `launch ${p.launch} ${(p.args || []).join(" ")}`
      : `${p.action?.kind} ${p.ref}${p.action?.value != null ? ` ${JSON.stringify(p.action.value)}` : ""}${p.dry_run ? " (dry-run)" : ""}`;
    case "action_resolution": return p.ok ? `${p.ref} via ${p.via} → ${p.found?.role} ${JSON.stringify(p.found?.name)} @ ${p.locator}`
      : `FAILED ${p.ref || ""}: ${short(p.error, 120)}`;
    case "action_native": return `${strategyOf(p)} · ${p.native_call}`;
    case "action_result": return `${p.ok ? "ok" : "FAILED"} · ${Math.round(p.duration_ms)} ms${p.error ? ` · ${p.error}` : ""}`;
    case "post_action_diff": {
      const changed = Object.entries(p.changed || {}).flatMap(([ref, f]) => Object.entries(f).map(([k, [a, b]]) => `${ref} ${k}: ${short(a, 25)} → ${short(b, 25)}`));
      return [...changed.slice(0, 2), changed.length > 2 ? `+${changed.length - 2} more` : "",
        p.added?.length ? `added ${p.added.length}` : "", p.removed?.length ? `removed ${p.removed.length}` : ""].filter(Boolean).join(" · ") || "no change";
    }
    case "mcp_request": return `${p.tool}(${short(p.args, 80)})`;
    case "mcp_response": return "error" in p ? `ERROR ${short(p.error, 120)}` : short((p.text || "").split("\n")[0], 110);
    case "error": return p.message;
    default: return short(p);
  }
}

// ---------- session list ----------
async function renderSessions() {
  setCrumbs(el("span", {}, "sessions"));
  const rows = await api("/api/sessions");
  const strategyMix = (s) => Object.entries(s).map(([k, n]) => el("span", { class: `badge ${k}` }, `${k} ${n}`));
  app.replaceChildren(
    el("h2", {}, `${rows.length} sessions`),
    el("table", {},
      el("tr", {}, ...["session", "framework", "adapter", "started", "duration", "events", "actions", "errors", "strategies", ""].map((h) => el("th", {}, h))),
      ...rows.map((s) => el("tr", {},
        el("td", {}, sessionLink(s.id), s.mcp ? el("span", { class: "muted" }, " · mcp") : null),
        el("td", {}, s.framework || ""), el("td", {}, s.adapter || ""),
        el("td", { class: "mono" }, (s.started || "").replace("T", " ").slice(0, 19)),
        el("td", {}, `${s.duration_s}s`), el("td", {}, s.events), el("td", {}, s.actions),
        el("td", { class: s.errors ? "mono" : "muted" }, s.errors ? el("span", { class: "badge fail" }, s.errors) : "0"),
        el("td", {}, ...strategyMix(s.strategies)),
        el("td", {}, s.live ? el("span", { class: "live" }, "● live") : ""),
      ))));
}

// ---------- timeline ----------
function parseQuery(q) {
  const params = new URLSearchParams(q || "");
  return { kinds: params.get("k") ? params.get("k").split(",") : [], ref: params.get("ref") || "", errors: params.get("err") === "1" };
}

async function renderTimeline(sid, query) {
  const f = parseQuery(query);
  setCrumbs(el("a", { href: "#/" }, "sessions"), el("span", {}, sid));
  const [info, events] = await Promise.all([api(`/api/sessions/${enc(sid)}`), api(`/api/sessions/${enc(sid)}/events`)]);
  const t0 = events.length ? Date.parse(events[0].ts) : 0;

  const updateHash = () => {
    const params = new URLSearchParams();
    if (f.kinds.length) params.set("k", f.kinds.join(","));
    if (f.ref) params.set("ref", f.ref);
    if (f.errors) params.set("err", "1");
    history.replaceState(null, "", `#/s/${enc(sid)}${params.toString() ? "?" + params : ""}`);
  };
  const visible = (e) => (!f.kinds.length || f.kinds.includes(e.kind)) && (!f.errors || isError(e))
    && (!f.ref || JSON.stringify(e.payload).includes(`"${f.ref}"`) || JSON.stringify(e.payload).includes(`[${f.ref}]`));

  const table = el("table", {}, el("tr", {}, ...["id", "+s", "kind", "app", "summary", ""].map((h) => el("th", {}, h))));
  const count = el("span", { class: "muted" });
  const liveBadge = el("span", { class: info.live ? "live" : "muted" }, info.live ? "● live" : "finished");

  const link = (e) => {
    if (e.kind.startsWith("snapshot_")) return el("a", { href: `#/s/${enc(sid)}/snap/${e.id}` }, "snapshot view");
    if (e.kind === "action_request") return el("a", { href: `#/s/${enc(sid)}/act/${e.id}` }, "action view");
    return null;
  };
  const addRow = (e) => {
    const row = el("tr", { class: `event${isError(e) ? " err" : ""}`, dataset: { id: e.id } },
      el("td", { class: "mono" }, e.id),
      el("td", { class: "mono muted" }, ((Date.parse(e.ts) - t0) / 1000).toFixed(2)),
      el("td", {}, el("span", { class: `badge ${e.kind === "action_native" ? strategyOf(e.payload) : ""}` }, e.kind)),
      el("td", { class: "mono muted" }, e.app_id || ""),
      el("td", { class: "mono" }, summary(e)),
      el("td", {}, link(e)));
    row.addEventListener("click", (ev) => {
      if (ev.target.closest("a")) return;
      const next = row.nextElementSibling;
      if (next && next.classList.contains("payload")) { next.remove(); return; }
      const side = e.payload.sidecar ? el("div", {}, el("a", { href: `/api/sessions/${enc(sid)}/sidecar/${e.payload.sidecar}`, target: "_blank" }, `open ${e.payload.sidecar}`)) : null;
      row.after(el("tr", { class: "payload" }, el("td", { colspan: 6 }, side, el("pre", {}, JSON.stringify(e.payload, null, 2)))));
    });
    row.hidden = !visible(e);
    table.append(row);
  };
  const refresh = () => {
    let shown = 0;
    for (const row of table.querySelectorAll("tr.event")) {
      const e = byId.get(row.dataset.id);
      row.hidden = !visible(e);
      if (!row.hidden) shown++;
      const p = row.nextElementSibling;
      if (p && p.classList.contains("payload")) p.hidden = row.hidden;
    }
    count.textContent = `${shown} of ${byId.size} events`;
    updateHash();
  };

  const byId = new Map(events.map((e) => [e.id, e]));
  events.forEach(addRow);

  const kindBoxes = KINDS.map((k) => el("label", {}, el("input", { type: "checkbox", value: k, checked: f.kinds.includes(k) }), " ", k));
  const filters = el("div", { class: "filters" },
    el("b", {}, "kind:"), ...kindBoxes,
    el("label", {}, "ref ", el("input", { type: "text", placeholder: "e12", value: f.ref })),
    el("label", {}, el("input", { type: "checkbox", id: "errors-only", checked: f.errors }), " errors only"));
  filters.addEventListener("input", () => {
    f.kinds = [...filters.querySelectorAll("input[type=checkbox][value]")].filter((b) => b.checked).map((b) => b.value);
    f.ref = filters.querySelector("input[type=text]").value.trim();
    f.errors = filters.querySelector("#errors-only").checked;
    refresh();
  });

  const st = Object.entries(info.strategies).map(([k, n]) => el("span", { class: `badge ${k}` }, `${k} ${n}`));
  app.replaceChildren(
    el("h2", {}, sid, " ", liveBadge),
    el("div", { class: "stats" }, el("span", {}, "framework ", el("b", {}, info.framework || "?")),
      el("span", {}, "adapter ", el("b", {}, info.adapter || "?")), el("span", {}, "duration ", el("b", {}, `${info.duration_s}s`)),
      el("span", {}, "actions ", el("b", {}, info.actions)), el("span", {}, "errors ", el("b", {}, info.errors)), el("span", {}, ...st)),
    filters, count, table);
  refresh();

  if (info.live) {
    let idle = 0;
    pollTimer = setInterval(async () => {
      const last = [...byId.keys()].pop();
      const fresh = await api(`/api/sessions/${enc(sid)}/events${last ? `?after=${last}` : ""}`).catch(() => []);
      const atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 40;
      for (const e of fresh) { byId.set(e.id, e); addRow(e); }
      if (fresh.length) { idle = 0; refresh(); if (atBottom) window.scrollTo(0, document.body.scrollHeight); }
      else if (++idle > 60) { clearInterval(pollTimer); liveBadge.textContent = "finished"; liveBadge.className = "muted"; }
    }, 1000);
  }
}

// ---------- snapshot view ----------
const childLoc = (loc, i) => `${loc.replace(/\/$/, "")}/${i}`;

async function renderSnapshot(sid, ev) {
  setCrumbs(el("a", { href: "#/" }, "sessions"), sessionLink(sid), el("span", {}, `snapshot ${ev}`));
  const g = await api(`/api/sessions/${enc(sid)}/snapshot/${ev}`);
  const normByRef = new Map(), normByLoc = new Map(), rawByLoc = new Map();
  const aiText = g.ai_view?.text || "";
  const aiRefs = new Set([...aiText.matchAll(/\[(e\d+)\]/g)].map((m) => m[1]));

  const normPane = el("div", { class: "body" });
  const walkNorm = (n, depth) => {
    normByRef.set(n.ref, n); normByLoc.set(n.meta.locator, n);
    const states = (n.states || []).filter((s) => s !== "enabled").join(" ");
    normPane.append(el("div", { class: "node", dataset: { ref: n.ref, loc: n.meta.locator } },
      "  ".repeat(depth), `${n.role}${n.name ? ` ${JSON.stringify(n.name)}` : ""} `, el("span", { class: "ref" }, `[${n.ref}]`),
      states ? ` ${states}` : "",
      Object.keys(n.meta.sources || {}).length ? el("span", { class: "src" }, `  ·${[...new Set(Object.values(n.meta.sources))].join("+")}: ${Object.keys(n.meta.sources).join(", ")}`) : "",
      aiRefs.has(n.ref) ? "" : el("span", { class: "muted" }, "  ·not in AI view")));
    (n.children || []).forEach((c) => walkNorm(c, depth + 1));
  };
  if (g.normalized) walkNorm(g.normalized, 0); else normPane.append(el("div", { class: "muted" }, "no normalized tree for this snapshot"));

  const rawPane = el("div", { class: "body" });
  const walkRaw = (n, loc, depth) => {
    loc = n.locator || loc;
    rawByLoc.set(loc, n);
    const r = n.raw || {};
    const bits = [n.role, n.name ? JSON.stringify(n.name) : "", r.control_type_id ? `ct=${r.control_type_id}` : "",
      r.automation_id || n.automation_id ? `aid=${r.automation_id || n.automation_id}` : "", r.class_name || n.class_name ? `cls=${r.class_name || n.class_name}` : ""];
    rawPane.append(el("div", { class: "node", dataset: { loc } }, "  ".repeat(depth), bits.filter(Boolean).join(" ")));
    (n.children || []).forEach((c, i) => walkRaw(c, childLoc(loc, i), depth + 1));
  };
  if (g.raw) walkRaw(g.raw.tree, g.raw.locator || g.raw.window || "/", 0);
  else rawPane.append(el("div", { class: "muted" }, "no raw dump for this snapshot (act pre/post snapshots and wait_for polls only record the normalized tree)"));

  const aiPane = el("div", { class: "body" });
  if (g.ai_view) {
    for (const line of aiText.split("\n")) {
      const parts = []; let last = 0;
      for (const m of line.matchAll(/\[(e\d+)\]/g)) {
        parts.push(line.slice(last, m.index), el("span", { class: "ref", dataset: { ref: m[1] } }, m[0]));
        last = m.index + m[0].length;
      }
      parts.push(line.slice(last));
      aiPane.append(el("div", { class: "node" }, ...parts));
    }
  } else aiPane.append(el("div", { class: "muted" }, "no AI view rendered for this snapshot"));

  const detail = el("div", { class: "detail muted" }, "Click a ref or a node in any pane to highlight it in all three.");
  const select = (ref, loc) => {
    const norm = ref ? normByRef.get(ref) : normByLoc.get(loc);
    ref = norm?.ref ?? ref; loc = norm?.meta.locator ?? loc;
    document.querySelectorAll(".hl").forEach((n) => n.classList.remove("hl"));
    const targets = [
      ...normPane.querySelectorAll(`[data-ref="${ref}"]`), ...aiPane.querySelectorAll(`[data-ref="${ref}"]`),
      ...[...rawPane.querySelectorAll(".node")].filter((n) => n.dataset.loc === loc)];
    targets.forEach((t) => t.classList.add("hl"));
    for (const pane of [rawPane, normPane, aiPane]) pane.querySelector(".hl")?.scrollIntoView({ block: "center" });
    const rawNode = rawByLoc.get(loc);
    const strip = (n) => n && Object.fromEntries(Object.entries(n).filter(([k]) => k !== "children"));
    detail.className = "detail";
    detail.replaceChildren(
      el("div", {}, el("b", {}, ref || "(no ref)"), ` · locator ${loc || "?"} · `,
        ref && aiRefs.has(ref) ? "shown in AI view" : "not shown in the AI view (hoisted wrapper, offscreen, icon glyph, folded into its parent, or beyond depth)"),
      el("div", { class: "cols" },
        el("div", {}, el("div", { class: "muted" }, "raw"), el("pre", {}, rawNode ? JSON.stringify(strip(rawNode), null, 2) : "—")),
        el("div", {}, el("div", { class: "muted" }, "normalized"), el("pre", {}, norm ? JSON.stringify(strip(norm), null, 2) : "—"))));
  };
  const onClick = (pane, fromRaw) => pane.addEventListener("click", (e) => {
    const node = e.target.closest("[data-ref], [data-loc]");
    if (!node) return;
    if (fromRaw) select(null, node.dataset.loc); else select(node.dataset.ref, node.dataset.loc);
  });
  onClick(rawPane, true); onClick(normPane, false); onClick(aiPane, false);

  const s = g.ai_view?.stats || {};
  const countNodes = (n) => (n ? 1 + (n.children || []).reduce((a, c) => a + countNodes(c), 0) : 0);
  const stat = (label, value) => el("span", {}, `${label} `, el("b", {}, value ?? "—"));
  const evLink = (kind) => g.event_ids[kind] ? el("a", { href: `#/s/${enc(sid)}?k=${kind === "ai_view" ? "snapshot_ai_view" : "snapshot_" + kind}` }, g.event_ids[kind]) : "—";
  app.replaceChildren(
    el("h2", {}, `snapshot of ${g.app_id}`, el("span", { class: "muted" }, "  raw ", evLink("raw"), " · normalized ", evLink("normalized"), " · ai view ", evLink("ai_view"))),
    el("div", { class: "stats" },
      stat("tokens", g.ai_view?.tokens), stat("nodes in → out", s.nodes_in != null ? `${s.nodes_in} → ${s.nodes_out}` : null),
      stat("hoisted", s.hoisted), stat("hidden", s.hidden), stat("truncated", s.truncated), stat("collapsed", s.collapsed),
      stat("glyphs", s.glyphs), stat("text cut", s.text_truncated),
      stat("raw nodes", g.raw ? rawByLoc.size : null), stat("normalized nodes", countNodes(g.normalized) || null),
      stat("AI view lines", g.ai_view ? aiText.split("\n").length : null),
      stat("refs new/reused/gone", g.counts ? `${g.counts.new}/${g.counts.reused}/${g.counts.gone}` : null)),
    el("div", { class: "panes" },
      el("div", { class: "pane" }, el("h3", {}, "Raw native"), rawPane),
      el("div", { class: "pane" }, el("h3", {}, "Normalized"), normPane),
      el("div", { class: "pane" }, el("h3", {}, "AI view (exact text sent to the agent)"), aiPane)),
    detail);
}

// ---------- action view ----------
function kv(pairs) {
  return el("div", { class: "kv" }, ...pairs.filter(([, v]) => v !== undefined && v !== null && v !== "")
    .flatMap(([k, v]) => [el("div", {}, k), el("div", { class: "mono" }, v instanceof Node ? v : typeof v === "string" ? v : JSON.stringify(v))]));
}
const card = (step, title, ...body) => el("div", { class: "card" }, el("h3", {}, el("span", { class: "step" }, step), title), ...body);
const arrow = () => el("div", { class: "arrow" }, "↓");

async function renderAction(sid, ev) {
  setCrumbs(el("a", { href: "#/" }, "sessions"), sessionLink(sid), el("span", {}, `action ${ev}`));
  const g = await api(`/api/sessions/${enc(sid)}/action/${ev}`);
  const req = g.request.payload, res = g.resolution?.payload, nat = g.native?.payload, out = g.result?.payload, diff = g.diff?.payload;
  const snapLink = (id, label) => id ? el("a", { href: `#/s/${enc(sid)}/snap/${id}` }, `${label} (${id})`) : null;
  const cards = [];

  if (g.mcp) {
    const r = g.mcp.response?.payload || {};
    cards.push(card("MCP", `${g.mcp.request.payload.tool}`, kv([["args", g.mcp.request.payload.args], ["request event", g.mcp.request.id]]),
      el("pre", { style: "margin-top:6px" }, "error" in r ? `ERROR: ${r.error}` : r.text || "(no response recorded)")), arrow());
  }
  cards.push(card("1", "Request", req.launch ? kv([["launch", req.launch], ["args", req.args]])
    : kv([["ref", req.ref], ["action", req.action?.kind], ["value", req.action?.value], ["dry_run", String(req.dry_run)], ["event", g.request.id]]),
  el("div", {}, snapLink(g.pre_snapshot_id, "pre-action snapshot"))), arrow());

  cards.push(card("2", "Resolution", res ? kv([
    ["result", el("span", { class: `badge ${res.ok ? "ok" : "fail"}` }, res.ok ? `found via ${res.via}` : "failed")],
    ["locator", res.locator], ["found", res.found ? `${res.found.role} ${JSON.stringify(res.found.name)} @ ${res.found.locator}` : null],
    ["path", res.found?.path], ["reason", res.reason], ["error", res.error],
    ["candidates", res.candidates?.length ? res.candidates.map((c) => `${c.ref} ${c.role} ${JSON.stringify(c.name)} (${c.path})`).join("\n") : null]])
    : el("div", { class: "muted" }, req.launch ? "launch has no element to resolve" : "no resolution recorded")), arrow());

  cards.push(card("3", "Native call", nat ? kv([
    ["strategy", el("span", { class: `badge ${strategyOf(nat)}` }, nat.dry_run ? `DRY-RUN (${nat.strategy})` : nat.strategy)],
    ["native call", nat.native_call], ["locator", nat.locator], ["pid", nat.pid]])
    : el("div", { class: "muted" }, "nothing was executed")), arrow());

  cards.push(card("4", "Result", out ? kv([
    ["result", el("span", { class: `badge ${out.ok ? "ok" : "fail"}` }, out.ok ? "ok" : "FAILED")],
    ["duration", `${Math.round(out.duration_ms)} ms`], ["error", out.error],
    ["state hash", out.before_fingerprint ? `${out.before_fingerprint} → ${out.after_fingerprint}` : null],
    ["changed refs", out.changed_refs?.length ? out.changed_refs.join(", ") : null]])
    : el("div", { class: "muted" }, "no action_result (the action raised before finishing; see errors)")), arrow());

  const rows = Object.entries(diff?.changed || {}).flatMap(([ref, fields]) =>
    Object.entries(fields).map(([k, [a, b]]) => el("tr", {}, el("td", { class: "mono" }, ref), el("td", {}, k),
      el("td", { class: "mono" }, JSON.stringify(a)), el("td", { class: "mono" }, JSON.stringify(b)))));
  cards.push(card("5", "Diff", diff ? [
    rows.length ? el("table", {}, el("tr", {}, ...["ref", "field", "before", "after"].map((h) => el("th", {}, h))), ...rows) : el("div", { class: "muted" }, "no field changed"),
    kv([["added", diff.added?.join(", ")], ["removed", diff.removed?.join(", ")]]),
    el("div", {}, snapLink(g.post_snapshot_id, "post-action snapshot"))]
    : el("div", { class: "muted" }, nat?.dry_run ? "dry-run: no post-action snapshot" : "no diff (action failed or produced no post snapshot)")));

  if (g.errors.length) cards.push(card("!", "Errors", ...g.errors.map((e) => el("details", {}, el("summary", { class: "mono" }, e.payload.message), el("pre", {}, e.payload.stack || "")))));
  app.replaceChildren(el("h2", {}, `action ${ev} on ${g.request.app_id || "(launch)"}`), ...cards);
}

// ---------- routing ----------
async function route() {
  clearInterval(pollTimer);
  const [path, query] = location.hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  try {
    if (parts[0] === "s" && parts[2] === "snap") await renderSnapshot(parts[1], parts[3]);
    else if (parts[0] === "s" && parts[2] === "act") await renderAction(parts[1], parts[3]);
    else if (parts[0] === "s" && parts[1]) await renderTimeline(parts[1], query);
    else await renderSessions();
  } catch (err) {
    app.replaceChildren(el("div", { class: "card" }, el("b", {}, "Could not load: "), String(err.message || err)));
  }
}
window.addEventListener("hashchange", route);
route();
