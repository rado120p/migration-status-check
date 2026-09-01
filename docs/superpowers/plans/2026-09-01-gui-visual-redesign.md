# GUI Visual Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the GUI full-width with a per-screen guide rail, replace the results rendering with a CLI-aligned service table + detail blocks, and add the missing Nespárováno/Nezařazeno safety-net sections.

**Architecture:** Frontend-only. A new `view.js` ports the pure view-building logic from `migration_validator/reporting/view.py` to JS (tested with `node --test`); `app.js` consumes it to render a run-wide results table with in-place expansion. The evaluation JSON already carries everything (`scopes[].checks[]` with family/group/label/value/baseline_value/delta/mode, plus `unmatched` and `unassigned`). No backend, API, or CLI changes.

**Tech Stack:** Vanilla JS (no framework, no build step), CSS, `node --test` (Node 22, zero deps) for JS logic, pytest for the existing backend route tests.

**Spec:** `docs/superpowers/specs/2026-09-01-gui-visual-redesign-design.md`

## Global Constraints

- No changes under `migration_validator/` except the `gui/static/` directory.
- JS logic strings mirror the CLI byte-for-byte (ASCII, no diacritics): `"bez baseline"`, `"bylo X   Y"` (three spaces before delta), `"Ostatni checky"`, `"L2 cast"`, `"blok nize"`/`"bloky nize"`/`"blok vyse"` — parity with `reporting/view.py` must stay greppable/comparable.
- Status values are uppercase strings in JSON: `"PASS" | "WARN" | "FAIL" | "SKIP" | "INFO"`.
- JSON omits null fields: a check may lack `label`, `group`, `family`, `value`, `baseline_value`, `delta`, `details` entirely — every access needs a default.
- `app.js` has no modules; scripts are plain `<script>` tags. `view.js` exposes a `MigView` global and a CommonJS export guard for node tests.
- Run the full pytest suite before every commit: `pyats-venv/bin/python -m pytest -q` — must stay green (it never touches JS, so any failure is collateral damage you caused elsewhere).
- Manual GUI verification uses: `pyats-venv/bin/mig-validate gui --run-root runs` → http://127.0.0.1:8321

---

### Task 1: `view.js` — JS port of the CLI view logic

**Files:**
- Create: `migration_validator/gui/static/view.js`
- Test: `tests/js/view.test.js`
- Reference (read, do not modify): `migration_validator/reporting/view.py`

**Interfaces:**
- Consumes: check/scope dicts exactly as serialized by `CheckResult.to_dict()` / `ScopeResult.to_dict()` (see Global Constraints for omitted-field behavior).
- Produces (all on the `MigView` global):
  - `changeText(row, hasBaseline) -> string`
  - `mergeDeactivationSkips(checks) -> checks`
  - `checkRow(check, qualify) -> {status, label, value, baseline_value, delta, mode}`
  - `worstMessage(scope) -> string`
  - `buildView(scope, {detail}) -> {status, description, service_type, routing_instance, baseline_interfaces, subject_interfaces, worst_message, sections, link_role, link_note}` where each section is `{family, addresses, virtual_gw, rows, groups}` and each group `{title, rows}`
  - `countStatuses(statusStrings) -> {pass, warn, fail, skip, info}`

- [ ] **Step 1: Write the failing tests**

Create `tests/js/view.test.js`:

```js
const test = require("node:test");
const assert = require("node:assert");
const MigView = require("../../migration_validator/gui/static/view.js");

test("changeText: no baseline run -> empty", () => {
  const row = { mode: "value", status: "WARN", value: "9", baseline_value: "12", delta: "-3" };
  assert.strictEqual(MigView.changeText(row, false), "");
});

test("changeText: state mode -> empty even with baseline", () => {
  const row = { mode: "state", status: "PASS", value: "up/up", baseline_value: null, delta: null };
  assert.strictEqual(MigView.changeText(row, true), "");
});

test("changeText: missing baseline value -> 'bez baseline', except SKIP", () => {
  const row = { mode: "value", status: "WARN", value: "9", baseline_value: null, delta: null };
  assert.strictEqual(MigView.changeText(row, true), "bez baseline");
  assert.strictEqual(MigView.changeText({ ...row, status: "SKIP" }, true), "");
});

test("changeText: equal values -> empty; differing -> 'bylo X', with delta appended", () => {
  const same = { mode: "value", status: "PASS", value: "12", baseline_value: "12", delta: null };
  assert.strictEqual(MigView.changeText(same, true), "");
  const diff = { mode: "value", status: "WARN", value: "9", baseline_value: "12", delta: null };
  assert.strictEqual(MigView.changeText(diff, true), "bylo 12");
  const withDelta = { ...diff, delta: "-3" };
  assert.strictEqual(MigView.changeText(withDelta, true), "bylo 12   -3");
});

test("mergeDeactivationSkips: only marked rows merge, count is merged rows only", () => {
  const marked = { id: "a", mode: "value", status: "SKIP", message: "m",
    details: { skipped_because: "service_deactivated" } };
  const plainSkip = { id: "b", mode: "value", status: "SKIP", message: "bez baseline" };
  const out = MigView.mergeDeactivationSkips([marked, plainSkip, { ...marked, id: "c" }]);
  assert.strictEqual(out.length, 2);
  assert.strictEqual(out[0].id, "b");
  const merged = out[1];
  assert.strictEqual(merged.id, "deactivation_skips");
  assert.strictEqual(merged.label, "Ostatni checky");
  assert.strictEqual(merged.value, "2 preskoceno");
  assert.strictEqual(merged.mode, "state");
});

test("mergeDeactivationSkips: nothing marked -> input unchanged", () => {
  const checks = [{ id: "a", mode: "value", status: "SKIP", message: "m" }];
  assert.strictEqual(MigView.mergeDeactivationSkips(checks), checks);
});

test("checkRow: label fallback to id, address qualifier, dash for missing value", () => {
  const check = { id: "arp_present", mode: "state", status: "PASS", message: "ok",
    details: { address: "10.0.0.1" } };
  assert.strictEqual(MigView.checkRow(check, false).label, "arp_present");
  assert.strictEqual(MigView.checkRow(check, true).label, "arp_present (10.0.0.1)");
  assert.strictEqual(MigView.checkRow(check, false).value, "-");
  const labeled = { ...check, label: "ARP zaznam", value: "ano" };
  assert.strictEqual(MigView.checkRow(labeled, false).label, "ARP zaznam");
  assert.strictEqual(MigView.checkRow(labeled, false).value, "ano");
});

test("worstMessage: PASS scope -> empty; else message of worst status in FAIL>WARN>SKIP order", () => {
  assert.strictEqual(MigView.worstMessage({ status: "PASS", checks: [] }), "");
  const scope = { status: "WARN", checks: [
    { status: "PASS", message: "fine" },
    { status: "SKIP", message: "skipped" },
    { status: "WARN", message: "prefixu mene" },
  ]};
  assert.strictEqual(MigView.worstMessage(scope), "prefixu mene");
});

function sampleScope() {
  return {
    scope_id: "svc:et-0/0/8.13:ACME",
    status: "WARN",
    identity: {
      description: "ACME-L3VPN", service_type: "l3vpn", routing_instance: "ACME-VRF",
      interfaces: ["et-0/0/8.13"], ipv4: ["10.1.2.1/30"], ipv6: [],
    },
    match: { status: "matched", baseline_interfaces: ["et-0/0/10.0"],
      subject_interfaces: ["et-0/0/8.13"] },
    checks: [
      { id: "interface_state", mode: "state", status: "PASS", message: "up",
        label: "Stav rozhrani", value: "up/up" },
      { id: "arp_present", mode: "state", status: "PASS", message: "ok",
        label: "ARP zaznam", value: "ano", family: 4,
        details: { address: "10.0.0.1" } },
      { id: "bgp_session", mode: "value", status: "PASS", message: "est",
        label: "BGP session", value: "Established", baseline_value: "Established",
        family: 4, group: "BGP peer 10.1.2.2 (ACME-VRF.inet.0)" },
      { id: "bgp_received_prefixes", mode: "value", status: "WARN", message: "prefixu mene",
        label: "Prijate prefixy", value: "9", baseline_value: "12", delta: "-3",
        family: 4, group: "BGP peer 10.1.2.2 (ACME-VRF.inet.0)" },
    ],
  };
}

test("buildView: sections in FAMILY_ORDER, empty families dropped, groups keyed by title", () => {
  const view = MigView.buildView(sampleScope(), {});
  assert.strictEqual(view.description, "ACME-L3VPN");
  assert.strictEqual(view.service_type, "l3vpn");
  assert.strictEqual(view.worst_message, "prefixu mene");
  assert.deepStrictEqual(view.baseline_interfaces, ["et-0/0/10.0"]);
  assert.strictEqual(view.sections.length, 2);          // null family + IPv4, no IPv6
  assert.strictEqual(view.sections[0].family, null);
  assert.strictEqual(view.sections[0].rows.length, 1);
  assert.strictEqual(view.sections[1].family, 4);
  assert.deepStrictEqual(view.sections[1].addresses, ["10.1.2.1/30"]);
  assert.strictEqual(view.sections[1].rows.length, 1);  // ARP, ungrouped
  assert.strictEqual(view.sections[1].groups.length, 1);
  assert.strictEqual(view.sections[1].groups[0].rows.length, 2);
});

test("buildView: no match -> ports from identity, baseline side empty", () => {
  const scope = sampleScope();
  scope.match = null;
  const view = MigView.buildView(scope, {});
  assert.deepStrictEqual(view.baseline_interfaces, []);
  assert.deepStrictEqual(view.subject_interfaces, ["et-0/0/8.13"]);
});

test("buildView: l2 link -> '(L2 cast)' type suffix and 'blok vyse' note; l3 multi-peer -> 'bloky nize'", () => {
  const l2 = sampleScope();
  l2.link = { role: "l2", peer_interface: "irb.100", peer_instance: "BB", peer_scope_id: "x" };
  const l2view = MigView.buildView(l2, {});
  assert.strictEqual(l2view.service_type, "l3vpn (L2 cast)");
  assert.strictEqual(l2view.link_note, "L3 cast: irb.100 v BB (blok vyse)");
  const l3 = sampleScope();
  l3.link = { role: "l3", peers: [
    { interface: "et-0/0/8.1", instance: "A", scope_id: "s1" },
    { interface: "et-0/0/8.2", instance: "B", scope_id: "s2" },
  ]};
  assert.strictEqual(MigView.buildView(l3, {}).link_note,
    "L2 cast: et-0/0/8.1 v A, et-0/0/8.2 v B (bloky nize)");
});

test("buildView: detail=false merges deactivation skips per-section, detail=true keeps them", () => {
  const scope = sampleScope();
  scope.checks.push(
    { id: "d1", mode: "value", status: "SKIP", message: "m", family: 4,
      details: { skipped_because: "service_deactivated" } },
    { id: "d2", mode: "value", status: "SKIP", message: "m", family: 4,
      details: { skipped_because: "service_deactivated" } },
  );
  const merged = MigView.buildView(scope, {});
  const ipv4 = merged.sections.find((s) => s.family === 4);
  assert.ok(ipv4.rows.some((r) => r.label === "Ostatni checky" && r.value === "2 preskoceno"));
  const full = MigView.buildView(scope, { detail: true });
  const ipv4Full = full.sections.find((s) => s.family === 4);
  assert.ok(!ipv4Full.rows.some((r) => r.label === "Ostatni checky"));
});

test("buildView: address qualifier only when family has >1 address", () => {
  const scope = sampleScope();
  scope.identity.ipv4 = ["10.1.2.1/30", "10.9.9.1/30"];
  const view = MigView.buildView(scope, {});
  const ipv4 = view.sections.find((s) => s.family === 4);
  assert.strictEqual(ipv4.rows[0].label, "ARP zaznam (10.0.0.1)"); // details.address below
});

test("countStatuses: five counters, lowercase keys", () => {
  assert.deepStrictEqual(MigView.countStatuses(["PASS", "PASS", "WARN", "INFO"]),
    { pass: 2, warn: 1, fail: 0, skip: 0, info: 1 });
});
```

The qualifier test relies on `sampleScope()`'s `arp_present` check carrying `details: { address: "10.0.0.1" }` — with a single IPv4 address the label stays bare, with two it gains the qualifier.

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/js/view.test.js`
Expected: FAIL — `Cannot find module '.../gui/static/view.js'`

- [ ] **Step 3: Write `view.js`**

Create `migration_validator/gui/static/view.js`:

```js
// Port of migration_validator/reporting/view.py - keep the two in lockstep.
// Strings are byte-identical to the CLI (ASCII) so outputs stay comparable.
"use strict";

const FAMILY_ORDER = [null, 4, 6];
const NO_BASELINE = "bez baseline";
const MERGED_LABEL = "Ostatni checky";

function changeText(row, hasBaseline) {
  if (!hasBaseline) return "";
  if (row.mode === "state") return "";
  if (row.baseline_value == null) {
    return row.status === "SKIP" ? "" : NO_BASELINE;
  }
  if (row.baseline_value === row.value) return "";
  if (row.delta) return `bylo ${row.baseline_value}   ${row.delta}`;
  return `bylo ${row.baseline_value}`;
}

function mergeDeactivationSkips(checks) {
  const marked = new Set(
    checks.filter((c) => (c.details || {}).skipped_because === "service_deactivated")
  );
  if (marked.size === 0) return checks;
  const kept = checks.filter((c) => !marked.has(c));
  return [
    ...kept,
    {
      id: "deactivation_skips",
      mode: "state",
      status: "SKIP",
      severity: "advisory",
      message: `${marked.size} dalsich checku preskoceno, sluzba je deaktivovana`,
      label: MERGED_LABEL,
      value: `${marked.size} preskoceno`,
    },
  ];
}

function checkRow(check, qualify) {
  let label = check.label || check.id;
  const address = (check.details || {}).address;
  if (qualify && address) label = `${label} (${address})`;
  return {
    status: check.status,
    label,
    value: check.value != null ? check.value : "-",
    baseline_value: check.baseline_value != null ? check.baseline_value : null,
    delta: check.delta != null ? check.delta : null,
    mode: check.mode,
  };
}

function worstMessage(scope) {
  if (scope.status === "PASS") return "";
  for (const status of ["FAIL", "WARN", "SKIP"]) {
    for (const check of scope.checks || []) {
      if (check.status === status) return check.message;
    }
  }
  return "";
}

function buildView(scope, opts) {
  const detail = !!(opts && opts.detail);
  const identity = scope.identity || {};
  const addresses = { 4: identity.ipv4 || [], 6: identity.ipv6 || [] };
  const gateways = { 4: identity.virtual_gw_v4 || [], 6: identity.virtual_gw_v6 || [] };

  const sections = [];
  for (const family of FAMILY_ORDER) {
    let checks = (scope.checks || []).filter(
      (c) => (c.family != null ? c.family : null) === family
    );
    if (checks.length === 0) continue;
    if (!detail) checks = mergeDeactivationSkips(checks);
    const own = addresses[family] || [];
    const qualify = own.length > 1;
    const rows = [];
    const groups = [];
    const byTitle = new Map();
    for (const check of checks) {
      const row = checkRow(check, qualify);
      if (check.group == null) {
        rows.push(row);
        continue;
      }
      let group = byTitle.get(check.group);
      if (!group) {
        group = { title: check.group, rows: [] };
        byTitle.set(check.group, group);
        groups.push(group);
      }
      group.rows.push(row);
    }
    sections.push({
      family,
      addresses: own,
      virtual_gw: gateways[family] || [],
      rows,
      groups,
    });
  }

  const link = scope.link || null;
  const linkRole = link ? link.role : null;
  let linkNote = null;
  if (link) {
    if (linkRole === "l3") {
      const peers = link.peers || [];
      const parts = peers.map((p) => `${p.interface} v ${p.instance}`).join(", ");
      const blocks = peers.length > 1 ? "bloky nize" : "blok nize";
      linkNote = parts ? `L2 cast: ${parts} (${blocks})` : null;
    } else if (linkRole === "l2") {
      linkNote = `L3 cast: ${link.peer_interface} v ${link.peer_instance} (blok vyse)`;
    }
  }

  let serviceType = identity.service_type || "-";
  if (linkRole === "l2") serviceType = `${serviceType} (L2 cast)`;

  const match = scope.match || null;
  return {
    status: scope.status,
    description: identity.description || scope.scope_id,
    service_type: serviceType,
    routing_instance: identity.routing_instance || null,
    baseline_interfaces: match ? match.baseline_interfaces || [] : [],
    subject_interfaces: match
      ? match.subject_interfaces || []
      : identity.interfaces || [],
    worst_message: worstMessage(scope),
    sections,
    link_role: linkRole,
    link_note: linkNote,
  };
}

function countStatuses(statuses) {
  const counts = { pass: 0, warn: 0, fail: 0, skip: 0, info: 0 };
  for (const status of statuses) counts[status.toLowerCase()] += 1;
  return counts;
}

const MigView = {
  FAMILY_ORDER,
  changeText,
  mergeDeactivationSkips,
  checkRow,
  worstMessage,
  buildView,
  countStatuses,
};

if (typeof module !== "undefined" && module.exports) module.exports = MigView;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/js/view.test.js`
Expected: all tests PASS

- [ ] **Step 5: Run pytest, commit**

```bash
pyats-venv/bin/python -m pytest -q
git add migration_validator/gui/static/view.js tests/js/view.test.js
git commit -m "feat(gui): view.js - JS port of reporting/view.py view logiky s node testy"
```

---

### Task 2: Full-width shell + guide rail

**Files:**
- Modify: `migration_validator/gui/static/style.css` (`.page`, `.shell`, `.body-grid`; new `.guide*` rules)
- Modify: `migration_validator/gui/static/index.html` (guide column, `view.js` script tag)
- Modify: `migration_validator/gui/static/app.js` (`render()` fills the guide; new `renderGuide()` + `GUIDE_TEXT`)

**Interfaces:**
- Consumes: nothing from Task 1 at runtime (the script tag is added here so later tasks can call `MigView`).
- Produces: `<div class="guide" id="guide">` filled by `renderGuide()` on every `render()`; `GUIDE_TEXT` map keyed by `this.state.view` values (`"run"`, `"snapshot"`, `"checks"`, `"capture"`, `"new-run"`, `"edit-mapping"`). Verify the exact view-state strings in `app.js` `render()`'s switch before wiring, and key the map with those.

- [ ] **Step 1: CSS — unfix the shell, add the guide column**

In `style.css` replace the `.shell` width and `.body-grid` columns:

```css
.page {
  min-height: 100vh;
  overflow-x: auto;
  padding: 24px 20px;
}

.shell {
  width: auto;
  max-width: none;
  margin: 0 auto;
  /* rest unchanged: background, border, radius, shadow, grid rows */
}

.body-grid {
  display: grid;
  grid-template-columns: 232px 1fr 280px;
  min-height: 620px;
}
```

Append the guide styles:

```css
/* Guide rail */
.guide {
  background: #ffffff;
  border-left: 1px solid #e2e5ea;
  padding: 16px 14px;
  font-size: 12.5px;
  color: #4b5563;
}

.guide h4 {
  margin: 0 0 10px;
  font: 600 11px 'IBM Plex Sans', sans-serif;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #9ca3af;
}

.guide p { margin: 0 0 10px; line-height: 1.5; }
.guide .k { font: 600 12px 'IBM Plex Mono', monospace; color: #1a56db; }
```

- [ ] **Step 2: index.html — third column + view.js**

In `index.html` add the guide div after `<div class="main" id="main"></div>`:

```html
      <div class="main" id="main"></div>
      <div class="guide" id="guide"></div>
```

and load `view.js` before `app.js`:

```html
<script src="/static/view.js"></script>
<script src="/static/app.js"></script>
```

- [ ] **Step 3: app.js — per-screen guide content**

Near the top of `app.js` (after `statusClass`), add the copy map. Czech with diacritics is correct here — this is display copy, not CLI-parity logic:

```js
const GUIDE_TEXT = {
  run: {
    title: "Run overview",
    body: [
      "Tenhle screen porovnává služby mezi pre a post snímky namapovaných portů.",
      "Kliknutím na řádek v Results rozbalíš detail checků včetně změn proti baseline.",
      "Nespárováno = služba, která po migraci chybí. Vždy zkontroluj, než run uzavřeš.",
      "Nezařazeno = objekt (BGP peer, routa, BFD session), který si nenárokovala žádná služba — typicky mezera v parsování.",
    ],
  },
  snapshot: {
    title: "Snapshot",
    body: [
      "Samostatné vyhodnocení jednoho snímku — běží jen stavové checky (stav rozhraní, ARP/ND, ping). Srovnání s baseline najdeš v run overview.",
      "Nezařazeno = objekt bez služby — zkontroluj, jestli nechybí v inventáři.",
    ],
  },
  checks: {
    title: "Checks",
    body: ["Registr všech checků aktivního profilu: mód, severita a typy služeb, na které se check vztahuje."],
  },
  capture: {
    title: "New capture",
    body: [
      "Sebere stav zařízení do snapshotu. Vyber zařízení, port a fázi (pre/post/rollback).",
      "Průběh sběru uvidíš živě v run overview.",
    ],
  },
  "new-run": {
    title: "New run",
    body: ["Založí run adresář: pojmenuj run a vyplň obě zařízení. Mapování portů můžeš doplnit i později přes Edit mapping."],
  },
  "edit-mapping": {
    title: "Edit mapping",
    body: ["Páruje starý port s novým. Řádek s existujícím capture je zamčený — mapování, podle kterého už se sbíralo, se nemění."],
  },
};
```

In the `App` constructor add `this.guideEl = document.getElementById("guide");` next to the existing `mainEl` lookup, and at the top of `render()` call `this.renderGuide();`. Add the method:

```js
  renderGuide() {
    clear(this.guideEl);
    const entry = GUIDE_TEXT[this.state.view] || GUIDE_TEXT.run;
    this.guideEl.appendChild(el("h4", { text: "Guide — " + entry.title }));
    for (const paragraph of entry.body) {
      this.guideEl.appendChild(el("p", { text: paragraph }));
    }
  }
```

Check the actual view-state strings in `render()`'s switch (`this.state.view` cases) and make the `GUIDE_TEXT` keys match them exactly; fix keys, not the switch.

- [ ] **Step 4: Verify manually + pytest**

Run: `pyats-venv/bin/mig-validate gui --run-root runs`, open http://127.0.0.1:8321.
Expected: shell fills the window; guide rail on the right shows run-overview copy; switching to Checks / New capture swaps the guide text. No horizontal scrollbar at 1920px.
Run: `pyats-venv/bin/python -m pytest -q` — green.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/static/style.css migration_validator/gui/static/index.html migration_validator/gui/static/app.js
git commit -m "feat(gui): full-width shell a guide rail s per-screen napovedou"
```

---

### Task 3: Counts strip replaces summary cards

**Files:**
- Modify: `migration_validator/gui/static/app.js` (`renderRunOverview`, `computeSummaryTotals` area)
- Modify: `migration_validator/gui/static/style.css` (new `.counts*`, delete `.summary-card*` rules)

**Interfaces:**
- Consumes: `MigView.countStatuses` (Task 1); `this.cache.evaluation.evaluations[]` with `result.scopes[].status`, `result.summary` (`pass/warn/fail/skip/info`, `scopes_matched`, `unmatched_baseline`, `unmatched_subject`).
- Produces: `buildCountsStrip(services, checks, matchedLine) -> element` — reused verbatim by Task 6 (snapshot passes `matchedLine = null`).

- [ ] **Step 1: CSS**

Delete the `.summary-cards`, `.summary-card`, `.summary-card.fail-nonzero`, `.summary-count`, `.summary-label` rules. Append:

```css
/* Counts strip (Sluzby/Checky, mirrors CLI _counts_lines) */
.counts { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }

.counts-group {
  background: #fff;
  border: 1px solid #e2e5ea;
  border-radius: 6px;
  padding: 7px 14px;
  display: flex;
  gap: 14px;
  font-size: 13px;
}

.counts-group .counts-label { color: #6b7280; }
.counts-group b { font-family: 'IBM Plex Mono', monospace; font-weight: 600; }
.count-pass { color: #059669; }
.count-warn { color: #b45309; }
.count-fail { color: #b91c1c; }
.count-skip { color: #6b7280; }
.count-info { color: #2563eb; }
.counts-note { font-size: 12px; color: #6b7280; }
```

- [ ] **Step 2: app.js — builder + wiring**

Add to `App`:

```js
  buildCountsStrip(services, checks, matchedLine) {
    const order = ["pass", "warn", "fail", "skip", "info"];
    const group = (label, counts, keys) =>
      el("div", {
        className: "counts-group",
        children: [
          el("span", { className: "counts-label", text: label }),
          ...keys.map((key) =>
            el("span", {
              className: "count-" + key,
              children: [
                el("b", { text: String(counts[key] || 0) }),
                document.createTextNode(" " + key.toUpperCase()),
              ],
            })
          ),
        ],
      });
    const strip = el("div", {
      className: "counts",
      children: [
        group("Služby:", services, order),
        group("Checky:", checks, order),
      ],
    });
    if (matchedLine) strip.appendChild(el("span", { className: "counts-note", text: matchedLine }));
    return strip;
  }
```

In `renderRunOverview`, replace the `cardsDef`/`cardsRow` block with:

```js
    const evaluations = this.cache.evaluation ? this.cache.evaluation.evaluations : [];
    const results = evaluations.map((ev) => ev.result);
    const services = MigView.countStatuses(
      results.flatMap((r) => (r.scopes || []).map((s) => s.status))
    );
    const checks = { pass: 0, warn: 0, fail: 0, skip: 0, info: 0 };
    let matched = 0, unmatchedBaseline = 0, unmatchedSubject = 0;
    for (const result of results) {
      const summary = result.summary || {};
      for (const key of Object.keys(checks)) checks[key] += summary[key] || 0;
      matched += summary.scopes_matched || 0;
      unmatchedBaseline += summary.unmatched_baseline || 0;
      unmatchedSubject += summary.unmatched_subject || 0;
    }
    const matchedLine = results.length
      ? `Spárováno ${matched} služeb, ${unmatchedBaseline} nespárováno v baseline, ${unmatchedSubject} v subject`
      : null;
    this.mainEl.appendChild(this.buildCountsStrip(services, checks, matchedLine));
```

Delete `computeSummaryTotals` if nothing else uses it (grep first; if the snapshot view uses it, leave it until Task 6 removes the last caller).

- [ ] **Step 3: Verify manually + pytest, commit**

GUI: run overview shows the two-line strip + Spárováno note instead of five cards.
`pyats-venv/bin/python -m pytest -q` green, `node --test tests/js/view.test.js` green.

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): counts strip Sluzby/Checky misto summary karet"
```

---

### Task 4: Run-wide results table with headerless expansion; captures table simplified

**Files:**
- Modify: `migration_validator/gui/static/app.js`
- Modify: `migration_validator/gui/static/style.css`

**Interfaces:**
- Consumes: `MigView.buildView`, `MigView.changeText` (Task 1); `this.cache.evaluation.evaluations[]` (`subject`, `baseline`, `same_device`, `result`).
- Produces:
  - `collectServiceEntries(evaluations) -> [{key, view, hasBaseline, scope}]` — `key` is `"res|" + evaluation.subject + "|" + scope.scope_id` (unique per run; snapshot view in Task 6 passes its own prefix).
  - `buildResultsTable(entries, {singlePort}) -> element` — with `singlePort: true` renders one Port column (Task 6 reuses).
  - `buildDetailPanel(view, hasBaseline) -> element` — the headerless expansion (also reused by Task 6).
  - State: `this.state.openResults = {}` (replaces `openRows`/`openScopes` usage for results; toggled by `toggleResult(key)`).

- [ ] **Step 1: CSS**

Append (and delete the now-dead `.scopes-panel`, `.scope-card*`, `.scope-pill*`, `.scope-id`, `.scope-note`, `.scope-chevron*`, `.checks-panel`, `.checks-grid*` rules ONLY in Task 6 — snapshot view still uses them until then):

```css
/* Results table (CLI summary table) */
.results-table { background: #fff; border: 1px solid #e2e5ea; border-radius: 8px; overflow: hidden; }

.results-header-row, .results-row {
  display: grid;
  grid-template-columns: 56px 1.7fr 90px 110px 110px 100px 2fr 18px;
  gap: 0 10px;
  padding: 9px 16px;
  align-items: baseline;
}

.results-header-row.single-port, .results-row.single-port {
  grid-template-columns: 56px 1.8fr 90px 120px 100px 2fr 18px;
}

.results-header-row {
  background: #f9fafb;
  border-bottom: 1px solid #e2e5ea;
  font: 600 11px 'IBM Plex Sans', sans-serif;
  letter-spacing: 0.06em;
  color: #6b7280;
  text-transform: uppercase;
}

.results-row { border-bottom: 1px solid #f1f3f6; font-size: 13px; cursor: pointer; }
.results-row:hover { filter: brightness(0.985); }
.results-row.tint-warn { background: #fffbeb; }
.results-row.tint-fail { background: #fef2f2; }
.results-row .svc-name { font-weight: 600; color: #111827; }
.results-row .svc-cell { color: #374151; }
.results-row .port-cell { font-family: 'IBM Plex Mono', monospace; color: #111827; }
.results-row .find-cell { color: #4b5563; font-size: 12.5px; }
.results-row .chevron { color: #9ca3af; font-size: 10px; justify-self: end; transition: transform 0.15s; }
.results-row .chevron.open { transform: rotate(90deg); }
.status-token { font: 600 11.5px 'IBM Plex Mono', monospace; }
.status-token.pass { color: #059669; }
.status-token.warn { color: #b45309; }
.status-token.fail { color: #b91c1c; }
.status-token.skip { color: #9ca3af; }
.status-token.info { color: #2563eb; }

/* Detail panel (headerless expansion) */
.detail-cell { border-bottom: 1px solid #f1f3f6; background: #fbfcfd; padding: 8px 16px 14px; }

.detail-block {
  background: #fff;
  border: 1px solid #e2e5ea;
  border-left: 3px solid #d1d5db;
  border-radius: 6px;
  overflow: hidden;
  margin-left: 46px;
}

.detail-block.status-pass { border-left-color: #059669; }
.detail-block.status-warn { border-left-color: #d97706; }
.detail-block.status-fail { border-left-color: #dc2626; }
.detail-block.status-skip { border-left-color: #d1d5db; }

.detail-cols, .detail-row {
  display: grid;
  grid-template-columns: 52px 220px 1fr 1fr;
  gap: 0 12px;
  padding: 3px 14px;
  align-items: baseline;
  font-size: 12.5px;
}

.detail-cols.no-baseline, .detail-row.no-baseline { grid-template-columns: 52px 220px 1fr; }

.detail-cols {
  font: 600 10px 'IBM Plex Sans', sans-serif;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #9ca3af;
  padding-top: 9px;
  padding-bottom: 4px;
  border-bottom: 1px solid #f1f3f6;
}

.detail-row .lbl { color: #374151; }
.detail-row .val { font-family: 'IBM Plex Mono', monospace; color: #111827; }
.detail-row .chg { font-family: 'IBM Plex Mono', monospace; color: #b45309; }
.detail-row.grouped { padding-left: 40px; }
.detail-row:last-child { padding-bottom: 10px; }

.detail-section {
  font: 600 12px 'IBM Plex Mono', monospace;
  color: #4338ca;
  background: #eef2ff;
  padding: 4px 14px;
  margin-top: 6px;
}

.detail-group {
  font: 600 12px 'IBM Plex Mono', monospace;
  color: #6b7280;
  padding: 6px 14px 2px 28px;
}

.detail-note { font-size: 12px; color: #6b7280; padding: 6px 14px 0; }
```

- [ ] **Step 2: app.js — entry collection and builders**

Add `openResults: {}` to the state object in the constructor. Add to `App`:

```js
  toggleResult(key) {
    this.state.openResults[key] = !this.state.openResults[key];
    this.render();
  }

  collectServiceEntries(evaluations) {
    const entries = [];
    for (const evaluation of evaluations) {
      const result = evaluation.result || {};
      const hasBaseline = result.baseline != null;
      for (const scope of result.scopes || []) {
        entries.push({
          key: `res|${evaluation.subject}|${scope.scope_id}`,
          view: MigView.buildView(scope, {}),
          hasBaseline,
          scope,
        });
      }
    }
    return entries;
  }

  buildResultsTable(entries, opts) {
    const singlePort = !!(opts && opts.singlePort);
    const mode = singlePort ? " single-port" : "";
    const table = el("div", { className: "results-table" });
    const headers = singlePort
      ? ["Stav", "Služba", "Typ", "Port", "RI", "Nález", ""]
      : ["Stav", "Služba", "Typ", "Starý port", "Nový port", "RI", "Nález", ""];
    table.appendChild(
      el("div", {
        className: "results-header-row" + mode,
        children: headers.map((text) => el("span", { text })),
      })
    );
    for (const entry of entries) {
      const view = entry.view;
      const sClass = statusClass(view.status);
      const open = !!this.state.openResults[entry.key];
      const tint = view.status === "WARN" ? " tint-warn" : view.status === "FAIL" ? " tint-fail" : "";
      const oldPort = view.baseline_interfaces[0] || "-";
      const newPort = view.subject_interfaces[0] || "-";
      const portCells = singlePort
        ? [el("span", { className: "port-cell", text: newPort })]
        : [
            el("span", { className: "port-cell", text: oldPort }),
            el("span", { className: "port-cell", text: newPort }),
          ];
      table.appendChild(
        el("div", {
          className: "results-row" + mode + tint,
          onClick: () => this.toggleResult(entry.key),
          children: [
            el("span", { className: "status-token " + sClass, text: view.status }),
            el("span", { className: "svc-name", text: view.description }),
            el("span", { className: "svc-cell", text: view.service_type }),
            ...portCells,
            el("span", { className: "port-cell", text: view.routing_instance || "-" }),
            el("span", { className: "find-cell", text: view.worst_message }),
            el("span", { className: "chevron" + (open ? " open" : ""), html: "&#9654;" }),
          ],
        })
      );
      if (open) {
        table.appendChild(
          el("div", {
            className: "detail-cell",
            children: [this.buildDetailPanel(view, entry.hasBaseline)],
          })
        );
      }
    }
    return table;
  }

  buildDetailPanel(view, hasBaseline) {
    const block = el("div", { className: "detail-block status-" + statusClass(view.status) });
    if (view.link_note) {
      block.appendChild(el("div", { className: "detail-note", text: view.link_note }));
    }
    const nb = hasBaseline ? "" : " no-baseline";
    const oldPort = view.baseline_interfaces[0] || "-";
    const newPort = view.subject_interfaces[0] || "-";
    const cols = hasBaseline
      ? ["Stav", "Check", `Post (${newPort})`, `Změna proti ${oldPort}`]
      : ["Stav", "Check", "Hodnota"];
    block.appendChild(
      el("div", { className: "detail-cols" + nb, children: cols.map((text) => el("span", { text })) })
    );
    const row = (r, grouped) => {
      const change = MigView.changeText(r, hasBaseline);
      const cells = [
        el("span", { className: "status-token " + statusClass(r.status), text: r.status }),
        el("span", { className: "lbl", text: r.label }),
        el("span", { className: "val", text: r.value }),
      ];
      if (hasBaseline) cells.push(el("span", { className: "chg", text: change }));
      return el("div", {
        className: "detail-row" + nb + (grouped ? " grouped" : ""),
        children: cells,
      });
    };
    const familyTitle = { 4: "IPv4", 6: "IPv6" };
    for (const section of view.sections) {
      if (section.family != null) {
        const gateway = section.virtual_gw.length ? `   VGW ${section.virtual_gw.join(", ")}` : "";
        block.appendChild(
          el("div", {
            className: "detail-section",
            text: `${familyTitle[section.family]} — ${section.addresses.join(", ") || "-"}${gateway}`,
          })
        );
      }
      for (const r of section.rows) block.appendChild(row(r, false));
      for (const group of section.groups) {
        block.appendChild(el("div", { className: "detail-group", text: group.title }));
        for (const r of group.rows) block.appendChild(row(r, true));
      }
    }
    return block;
  }
```

- [ ] **Step 3: app.js — wire into renderRunOverview, simplify captures table**

In `renderRunOverview`, after the counts strip and captures table:

```js
    const pairEvaluations = evaluations.filter((ev) => !ev.same_device);
    const entries = this.collectServiceEntries(pairEvaluations);
    if (entries.length > 0) {
      this.mainEl.appendChild(
        el("div", { className: "subsection-title", text: `Results — ${entries.length} služeb` })
      );
      this.mainEl.appendChild(this.buildResultsTable(entries, {}));
    }
```

Simplify `buildPairingTable`: remove the `Result` header cell and pill column, the chevron cell, the `clickable`/`open` logic, the `rowTintClass` tint and the `buildScopesPanel` expansion (delete the `if (open) {...}` block). Keep: port cells, three flag cells, capture progress/failure `row-note` rows. Update `.pairing-header-row`/`.pairing-row` grid in CSS to `grid-template-columns: 1.3fr 1.3fr 90px 90px 110px;` (drop the 24px chevron and 130px result columns; keep `cols-same` variant consistent — Task 5 restyles same-device). Add a `subsection-title` "Captures" line above the table.

`findEvaluation`, `rowPresentation`, `pillText`, `rowTintClass`, `scopeNote`, `toggleRow`, `toggleScope` lose their last run-overview callers — delete them only if the snapshot/same-device views no longer reference them (grep; otherwise defer deletion to Task 6).

- [ ] **Step 4: Verify manually + tests, commit**

GUI checks: Captures table shows only flags; Results table lists every service with worst finding; WARN/FAIL rows tinted; clicking expands the headerless block with IPv4/IPv6 bands, groups, and Změna column; L2-část rows show the type suffix and note; no duplicated header line in the expansion.
`node --test tests/js/view.test.js` and `pyats-venv/bin/python -m pytest -q` green.

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): run-wide results tabulka s CLI sloupci a headerless expanzi, captures tabulka jen pro spravu captures"
```

---

### Task 5: Safety-net sections (Nespárováno / Nezařazeno) + same-device restyle

**Files:**
- Modify: `migration_validator/gui/static/view.js` (add `unassignedRow`, `UNASSIGNED_TITLES`)
- Modify: `migration_validator/gui/static/app.js`
- Modify: `migration_validator/gui/static/style.css`
- Test: `tests/js/view.test.js` (extend)

**Interfaces:**
- Consumes: `result.unmatched` (`{baseline: [], subject: []}`, items `{scope_id, description?, service_type?, reason}`), `result.unassigned` (`{bgp_peers, static_routes, bfd_sessions}`, item shapes per `text_report._unassigned_row`).
- Produces:
  - `MigView.unassignedRow(kind, item) -> {identity, detail}` and `MigView.UNASSIGNED_TITLES`
  - `buildUnmatchedSection(items) -> element|null` and `buildUnassignedSection(unassigned) -> element` on `App` — `buildUnassignedSection` reused by Task 6.
  - `items` for unmatched are pre-flattened by the caller: `{side, label, serviceType, reason, pairLabel}`.

- [ ] **Step 1: Extend view tests**

Append to `tests/js/view.test.js`:

```js
test("unassignedRow: bgp peer -> RI detail", () => {
  assert.deepStrictEqual(
    MigView.unassignedRow("bgp_peers", { peer: "192.0.2.99", routing_instance: "GOV-VRF" }),
    { identity: "192.0.2.99", detail: "RI GOV-VRF" });
  assert.deepStrictEqual(
    MigView.unassignedRow("bgp_peers", { peer: "192.0.2.99" }),
    { identity: "192.0.2.99", detail: "RI -" });
});

test("unassignedRow: static route -> next_hop arrow, via fallback, aggregate suffix", () => {
  assert.deepStrictEqual(
    MigView.unassignedRow("static_routes",
      { rib: "inet.0", prefix: "198.51.100.0/24", next_hop: ["10.1.2.2"] }),
    { identity: "inet.0 198.51.100.0/24", detail: "-> 10.1.2.2" });
  assert.deepStrictEqual(
    MigView.unassignedRow("static_routes",
      { rib: "inet.0", prefix: "0.0.0.0/0", via: ["et-0/0/8.13"] }),
    { identity: "inet.0 0.0.0.0/0", detail: "via et-0/0/8.13" });
  assert.deepStrictEqual(
    MigView.unassignedRow("static_routes",
      { rib: "inet.0", prefix: "10.0.0.0/8", protocol: "aggregate", via: [] }),
    { identity: "inet.0 10.0.0.0/8 (aggregate)", detail: "via -" });
});

test("unassignedRow: bfd session -> interface + state detail", () => {
  assert.deepStrictEqual(
    MigView.unassignedRow("bfd_sessions",
      { peer: "10.1.2.2", interface: "et-0/0/8.13", state: "Up" }),
    { identity: "10.1.2.2", detail: "et-0/0/8.13   Up" });
});
```

Run: `node --test tests/js/view.test.js` — new tests FAIL (`unassignedRow is not a function`).

- [ ] **Step 2: Implement in view.js**

Add before the `MigView` object literal (mirror of `text_report._unassigned_row` / `UNASSIGNED_TITLES`):

```js
const UNASSIGNED_TITLES = [
  ["bgp_peers", "BGP peer"],
  ["static_routes", "Staticka routa"],
  ["bfd_sessions", "BFD session"],
];

function unassignedRow(kind, item) {
  if (kind === "bgp_peers") {
    return { identity: item.peer, detail: `RI ${item.routing_instance || "-"}` };
  }
  if (kind === "static_routes") {
    const hops = item.next_hop || [];
    const detail = hops.length
      ? `-> ${hops.join(", ")}`
      : `via ${(item.via && item.via.length ? item.via : ["-"]).join(", ")}`;
    const suffix = item.protocol === "aggregate" ? " (aggregate)" : "";
    return { identity: `${item.rib} ${item.prefix}${suffix}`, detail };
  }
  return {
    identity: item.peer,
    detail: `${item.interface || "-"}   ${item.state || "-"}`,
  };
}
```

Add `UNASSIGNED_TITLES` and `unassignedRow` to the `MigView` object. Run the tests — PASS.

- [ ] **Step 3: CSS for the sections**

```css
/* Safety-net sections */
.safety-card { background: #fff; border: 1px solid #e2e5ea; border-radius: 8px; overflow: hidden; }
.safety-card.nonempty { border-color: #fde68a; }

.safety-row {
  display: grid;
  grid-template-columns: 110px 1.6fr 130px 2fr;
  gap: 0 12px;
  padding: 8px 16px;
  border-bottom: 1px solid #f1f3f6;
  align-items: baseline;
  font-size: 12.5px;
}

.safety-row:last-child { border-bottom: none; }
.safety-row .side { font: 600 11px 'IBM Plex Mono', monospace; text-transform: uppercase; }
.safety-row .side.baseline { color: #4338ca; }
.safety-row .side.subject { color: #047857; }
.safety-row .kind { font: 600 11px 'IBM Plex Mono', monospace; color: #047857; text-transform: uppercase; }
.safety-row .identity { font-family: 'IBM Plex Mono', monospace; color: #111827; }
.safety-row .detail { color: #4b5563; }
.safety-row .nothing { color: #9ca3af; }
```

- [ ] **Step 4: app.js — section builders + wiring**

```js
  buildUnmatchedSection(items) {
    const card = el("div", { className: "safety-card" + (items.length ? " nonempty" : "") });
    if (items.length === 0) {
      card.appendChild(
        el("div", { className: "safety-row", children: [el("span", { className: "nothing", text: "(nic)" })] })
      );
      return card;
    }
    for (const item of items) {
      card.appendChild(
        el("div", {
          className: "safety-row",
          children: [
            el("span", { className: "side " + item.side, text: item.side }),
            el("span", { className: "identity", text: item.label }),
            el("span", { className: "detail", text: `(${item.serviceType})` }),
            el("span", {
              className: "detail",
              text: item.pairLabel ? `${item.reason} — ${item.pairLabel}` : item.reason,
            }),
          ],
        })
      );
    }
    return card;
  }

  buildUnassignedSection(unassigned) {
    const rows = [];
    for (const [kind, title] of MigView.UNASSIGNED_TITLES) {
      for (const item of (unassigned && unassigned[kind]) || []) {
        const { identity, detail } = MigView.unassignedRow(kind, item);
        rows.push({ title, identity, detail });
      }
    }
    const card = el("div", { className: "safety-card" + (rows.length ? " nonempty" : "") });
    if (rows.length === 0) {
      card.appendChild(
        el("div", { className: "safety-row", children: [el("span", { className: "nothing", text: "(nic)" })] })
      );
      return card;
    }
    for (const row of rows) {
      card.appendChild(
        el("div", {
          className: "safety-row",
          children: [
            el("span", { className: "kind", text: row.title }),
            el("span", { className: "identity", text: row.identity }),
            el("span", {}),
            el("span", { className: "detail", text: row.detail }),
          ],
        })
      );
    }
    return card;
  }
```

In `renderRunOverview`, after the results table (aggregation across evaluations; label rows with the pair only when the run has more than one):

```js
    const unmatchedItems = [];
    const unassignedAgg = { bgp_peers: [], static_routes: [], bfd_sessions: [] };
    const multi = pairEvaluations.length > 1;
    for (const evaluation of pairEvaluations) {
      const result = evaluation.result || {};
      const pairLabel = multi ? evaluation.subject : null;
      for (const side of ["baseline", "subject"]) {
        for (const item of (result.unmatched && result.unmatched[side]) || []) {
          unmatchedItems.push({
            side,
            label: item.description || item.scope_id,
            serviceType: item.service_type || "-",
            reason: item.reason,
            pairLabel,
          });
        }
      }
      for (const kind of Object.keys(unassignedAgg)) {
        unassignedAgg[kind].push(...((result.unassigned && result.unassigned[kind]) || []));
      }
    }
    this.mainEl.appendChild(
      el("div", { className: "subsection-title", text: `Nespárováno — ${unmatchedItems.length}` })
    );
    this.mainEl.appendChild(this.buildUnmatchedSection(unmatchedItems));
    const unassignedCount = Object.values(unassignedAgg).reduce((n, list) => n + list.length, 0);
    this.mainEl.appendChild(
      el("div", { className: "subsection-title", text: `Nezařazeno (jen subject) — ${unassignedCount}` })
    );
    this.mainEl.appendChild(this.buildUnassignedSection(unassignedAgg));
```

Render both sections only when `this.cache.evaluation` exists (no evaluation yet → no sections, same as no results table).

- [ ] **Step 5: Same-device section restyle**

Rewrite `buildSameDeviceSection` to reuse the new language: keep its heading (`subsection-title` "Same device"), keep the small meta table of pre/post taken times if present, and render its services through `collectServiceEntries(sameDeviceEvaluations)` + `buildResultsTable(entries, { singlePort: true })`. Delete the old `cols-same` pairing markup for results (the `pill`/chevron/scopes-panel path).

- [ ] **Step 6: Verify + commit**

GUI: sections always present under results, "(nic)" when empty, amber border when not; a run with unmatched baseline service shows it without any filter. Same-device section uses the new table.
`node --test tests/js/view.test.js` and `pyats-venv/bin/python -m pytest -q` green.

```bash
git add migration_validator/gui/static/view.js migration_validator/gui/static/app.js migration_validator/gui/static/style.css tests/js/view.test.js
git commit -m "feat(gui): sekce Nesparovano a Nezarazeno + same-device pres novou results tabulku"
```

---

### Task 6: Snapshot view — standalone-only, new language, cleanup

**Files:**
- Modify: `migration_validator/gui/static/app.js`
- Modify: `migration_validator/gui/static/style.css`

**Interfaces:**
- Consumes: `buildCountsStrip` (Task 3), `collectServiceEntries`-shaped entries, `buildResultsTable(..., {singlePort: true})`, `buildDetailPanel` (Task 4), `buildUnassignedSection` (Task 5); snapshot eval payload `{snapshot, baseline, result}` from `/api/runs/{run}/snapshots/{file}/evaluation`.
- Produces: nothing new — this task is application + deletion.

- [ ] **Step 1: Remove the baseline picker**

In `app.js` delete: `buildSnapshotCompareBar`, `snapshotBaselineCandidates`, `selectSnapshotBaseline` and the `snapshotBaseline` state field plus every reference (grep `snapshotBaseline`). The snapshot evaluation fetch stops sending the `baseline` query param (backend keeps accepting it; the GUI just never uses it). Delete the `vs`-pill branch in the header (keep the phase pill and the `no baseline` concept out entirely — standalone is the only mode, so no pill needed for it either).

- [ ] **Step 2: Rebuild renderSnapshotView body**

Keep: breadcrumb, `h1` + phase pill, meta bar (device/platform/taken/collectors — drop "Baseline taken"), error notice path, Export JSON footer. Replace the chip row with the counts strip and the scopes panel with the results table + Nezařazeno:

```js
    const result = this.cache.snapshotEval.result;
    const services = MigView.countStatuses((result.scopes || []).map((s) => s.status));
    this.mainEl.appendChild(this.buildCountsStrip(services, result.summary || {}, null));

    const entries = (result.scopes || []).map((scope) => ({
      key: `snap|${this.state.selectedSnapshot}|${scope.scope_id}`,
      view: MigView.buildView(scope, {}),
      hasBaseline: false,
      scope,
    }));
    this.mainEl.appendChild(
      el("div", { className: "subsection-title", text: `Results — ${entries.length} služeb` })
    );
    this.mainEl.appendChild(this.buildResultsTable(entries, { singlePort: true }));

    const unassigned = result.unassigned || {};
    const unassignedCount = Object.values(unassigned).reduce((n, list) => n + list.length, 0);
    this.mainEl.appendChild(
      el("div", { className: "subsection-title", text: `Nezařazeno (jen subject) — ${unassignedCount}` })
    );
    this.mainEl.appendChild(this.buildUnassignedSection(unassigned));
```

Also delete the `chip-row` usage here and, if nothing else uses them, the `.chip*`, `.compare-bar` CSS rules.

- [ ] **Step 3: Dead code sweep**

Grep and delete now-unreferenced code and CSS: `buildScopesPanel`, `buildChecksTable`, `toggleScope`, `openScopes`, `toggleRow`, `openRows`, `scopeNote`, `rowPresentation`, `findEvaluation`, `pillText`, `rowTintClass`, `computeSummaryTotals`; CSS `.scopes-panel*`, `.scope-card*`, `.scope-pill*`, `.scope-id`, `.scope-note`, `.scope-header-spacer`, `.scope-chevron*`, `.checks-panel`, `.checks-grid*`, `.status-pill`, `.pill-*` (keep `.pill-waiting` only if the captures table still renders a waiting state — it should not; verify), `.summary-*` leftovers, `.chip*`, `.compare-bar`. Every deletion must be preceded by a grep proving zero references.

- [ ] **Step 4: Verify + commit**

GUI: snapshot view shows phase pill, meta bar, counts, single-Port results table with expansion, Nezařazeno; no compare picker anywhere.
`node --test tests/js/view.test.js` and `pyats-venv/bin/python -m pytest -q` green.

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): snapshot view standalone-only v novem jazyce, odstraneni baseline pickeru a mrtveho kodu"
```

---

### Task 7: Final pass — lab-shaped smoke check + docs

**Files:**
- Modify: `README.md` (GUI section, if it describes the old screens/picker)
- Reference: `runs/` (real run data in the repo root, e.g. the runs used for `172.20.20.4/5`)

**Interfaces:**
- Consumes: everything above.
- Produces: a verified, documented feature.

- [ ] **Step 1: Smoke check against real run data**

Run `pyats-venv/bin/mig-validate gui --run-root runs` and walk every screen with real data: run overview (counts, captures, results expand/collapse incl. an L2+L3 pair and a deactivated service's merged SKIP row, Nespárováno/Nezařazeno), snapshot view, checks, new capture, new run, edit mapping. Verify at a narrow window (~1100px) nothing breaks catastrophically (horizontal scroll on the page is acceptable; broken grid is not).

- [ ] **Step 2: Compare GUI vs CLI on the same run**

Run `pyats-venv/bin/mig-validate status`-equivalent evaluate for the same run in the CLI (`mig-validate evaluate ... --detail`) and put the outputs side by side: service count, per-service worst finding, Změna texts, Nespárováno/Nezařazeno contents must match item-for-item. Any mismatch is a bug in the JS port — fix in `view.js` with a regression test in `tests/js/view.test.js` before proceeding.

- [ ] **Step 3: Update README if stale, commit**

If `README.md` documents the GUI screens/summary cards/compare picker, update the wording to the new layout. Then:

```bash
pyats-venv/bin/python -m pytest -q
node --test tests/js/view.test.js
git add -A
git commit -m "docs: README aktualizace po redesignu GUI + finalni smoke check"
```
