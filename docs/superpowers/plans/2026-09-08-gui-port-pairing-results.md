# GUI port-pairing results — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In a mapped two-device migration run, the GUI run overview groups captures, service results, port checks and unmatched findings under their old → new port pairing, with a display-only service-type filter over the paired service results.

**Architecture:** A new pure frontend module `migration_validator/gui/static/run_results.js` (global `MigRunResults`, `module.exports` for `node:test`) turns the immutable run-detail + evaluation payloads into a grouped view model (pairing groups, rollback/other/same-device partitions, per-subject unassigned findings) and implements the filter, counts and linked-context rules. `app.js` gets a new mapped-migration rendering branch (`renderPairedResults`) that reuses `buildResultsTable`, `buildDetailPanel`, `buildFlagCell`, `buildUnmatchedSection`, `buildUnassignedSection` and `buildPairingTable`. No backend, API, manifest or snapshot changes; one route regression test is added.

**Tech Stack:** Vanilla JS (no build step), CSS, `node --test tests/js/*.test.js`, Python 3.13 + pytest (`.venv/bin/python -m pytest`), FastAPI test client (httpx2 now installed in `.venv`, dev extra updated).

**Spec:** `docs/superpowers/specs/2026-09-08-gui-port-pairing-results-design.md`

## Global Constraints

- Branch `gui-port-pairing-2026-09-08` from `main`. Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01SF2SAEk8NuMjDHQ8eEU2j5`.
- Do not touch backend matching, engine, collectors, pairing, manifests, API routes. Do not change single-device, mapping-less migration, snapshot, group/bulk screens.
- Keep the API payloads immutable: helpers must never mutate `evaluations`, `result`, `scopes`, `rows` or `snapshots`.
- New labels in English (decided with the user 2026-09-08). Existing Czech strings (`Služby:`, `Checky:`, `Nespárováno`, `Nezařazeno (jen subject)`, column headers `Stav/Služba/Typ/…`) stay as they are.
- Exact new strings (copy verbatim):
  - section title `Port pairings`; captures area `Other captures`; `Rollback — original device`; `Other evaluations`; `Unassigned — subject snapshot`
  - filter label `Filter paired service results`; chip `All types`; shown line `X of Y service results shown`
  - pending pills `Awaiting captures`, `Awaiting post`, `Awaiting evaluation`, `Evaluation unavailable`
  - header pills `Shared destination`, `Whole-device baseline`, `No baseline — service ownership unverified`, `Port checks need attention`, `Not in run mapping`, `Hidden WARN/FAIL`
  - whole-device text: `This comparison uses a whole-device baseline. Its services may span several old ports and are not guaranteed to belong only to this pairing.`
  - empty filter: `No <type> services in this pairing`; zero scopes: `No service results in this evaluation`
  - excluded note: `N other services on <new port> outside this comparison`
  - linked context tag `Linked context`; count `+ N linked context rows`; missing partner note `Related context unavailable in this evaluation`
  - top summary label `Full run (current profile)`
- No recency features: no highlighting, auto-focus, sorting by capture time, capture history.
- Keep `view.js` untouched (Python/JS parity).
- Suites green after every task: `node --test tests/js/*.test.js` and `.venv/bin/python -m pytest -q -p no:warnings tests/gui tests/runs/test_pairing.py tests/test_engine.py`.

---

## File Structure

| File | Responsibility |
|---|---|
| `migration_validator/gui/static/run_results.js` (new) | Pure view-model: scope classification, pairing keys, grouping/partitioning, whole-device fallback detection, unassigned dedupe, type filter with linked context, counts and choices. No DOM. |
| `tests/js/run_results.test.js` (new) | `node:test` coverage for the helper with fixtures for separate pairings, shared LAG, fallback, rollback, links. |
| `migration_validator/gui/static/app.js` | New state (`openPairings`, `serviceTypeFilter`, `pendingFocus`), `renderPairedResults` and its builders, small extension of `buildResultsTable` (context tag, link-note override), `buildCountsStrip` label. |
| `migration_validator/gui/static/style.css` | Pairing group, header, badges, chips, scroll wrapper. |
| `migration_validator/gui/static/index.html` | Load `run_results.js` before `app.js`. |
| `tests/gui/test_evaluation_routes.py` | Regression: shared destination with two per-port baselines yields two steps with correct service sets. |

---

## Task 1: Helper module — classification, pairing keys, grouping

**Files:**
- Create: `migration_validator/gui/static/run_results.js`
- Create: `tests/js/run_results.test.js`
- Modify: `migration_validator/gui/static/index.html:57` (add script tag)

**Interfaces:**
- Produces (global `MigRunResults` / `module.exports`):
  - `SERVICE_TYPE_ORDER: string[]`, `ALL_TYPES = "All"`, `UNKNOWN_TYPE = "Unknown"`
  - `classifyScope(scope) -> "device" | "layer1" | "service"`
  - `rawServiceType(scope) -> string`
  - `pairingKey(runName, step) -> string` (JSON tuple)
  - `evaluationLabel(evaluation) -> string`
  - `buildEvaluationModel(evaluation, index, snapshots, sectionKey) -> EvaluationModel`
  - `buildPairingGroups({runName, rows, evaluations, snapshots}) -> { groups, rollback, other, sameDevice }`
  - `EvaluationModel = { key, label, evaluation, subjectRecord, baselineRecord, warning, hasBaseline, wholeDeviceBaseline, baselineMetaMissing, subjectMetaMissing, serviceEntries, infrastructureEntries, unmatchedBaseline, unmatchedSubject, excludedCount, filtered }`
  - `Entry = { key, scope, kind, serviceType }` (`serviceType` null for non-service)
  - `Group = { key, old, new, captureRow, sharedDestination, metadataMismatch, pending, evaluations }`

- [ ] **Step 1: Write the failing tests**

Create `tests/js/run_results.test.js`:

```js
const test = require("node:test");
const assert = require("node:assert");
const R = require("../../migration_validator/gui/static/run_results.js");

function scope(id, type, extra) {
  return {
    scope_id: id,
    key: { description: id, service_type: type, service_subtype: null },
    identity: { description: id, service_type: type, interfaces: [] },
    status: "PASS",
    match: null,
    checks: [],
    ...(extra || {}),
  };
}
const L1 = (port) => scope(`l1:${port}`, "Layer1", { identity: { service_type: "Layer1", interfaces: [port] } });
const DEVICE = { scope_id: "device", key: null, identity: { service_type: null }, status: "PASS", checks: [] };

const step = (oldPort, newPort) => ({
  old: { node: "MX1", port: oldPort },
  new: { node: "PTX1", port: newPort },
});
const row = (oldPort, newPort, flags) => ({ ...step(oldPort, newPort), pre: false, post: false, rollback: false, ...(flags || {}) });

const SNAPSHOTS = [
  { file: "pre-ge4.json", phase: "pre", device: "MX1", port: "ge-0/0/4", taken: "t1" },
  { file: "pre-ge5.json", phase: "pre", device: "MX1", port: "ge-0/0/5", taken: "t1" },
  { file: "pre-all.json", phase: "pre", device: "MX1", port: null, taken: "t1" },
  { file: "post-ae0.json", phase: "post", device: "PTX1", port: "ae0", taken: "t2" },
  { file: "post-et8.json", phase: "post", device: "PTX1", port: "et-0/0/8", taken: "t2" },
  { file: "rb-ge6.json", phase: "rollback", device: "MX1", port: "ge-0/0/6", taken: "t3" },
];

function evaluation(subject, baseline, stepValue, scopes, extra) {
  return {
    subject, baseline, warning: null, step: stepValue, same_device: false,
    result: { baseline: baseline ? {} : null, scopes, summary: {}, unmatched: { baseline: [], subject: [] }, unassigned: {} },
    ...(extra || {}),
  };
}

test("classifyScope: device by id, layer1 by raw type, everything else service", () => {
  assert.strictEqual(R.classifyScope(DEVICE), "device");
  assert.strictEqual(R.classifyScope(L1("ae0")), "layer1");
  assert.strictEqual(R.classifyScope(scope("A", "IPVPN")), "service");
  assert.strictEqual(R.classifyScope({ scope_id: "x", key: { service_type: "Layer1" }, identity: {} }), "layer1");
  assert.strictEqual(R.classifyScope({ scope_id: "weird", identity: {} }), "service");
});

test("rawServiceType: identity first, then key, Unknown fallback; never the '(L2 cast)' suffix", () => {
  assert.strictEqual(R.rawServiceType(scope("A", "E-LAN", { link: { role: "l2", peer_scope_id: "B" } })), "E-LAN");
  assert.strictEqual(R.rawServiceType({ scope_id: "x", key: { service_type: "Core" }, identity: {} }), "Core");
  assert.strictEqual(R.rawServiceType({ scope_id: "x", identity: {} }), R.UNKNOWN_TYPE);
});

test("pairingKey: full tuple including run name, stable across equal objects", () => {
  const a = R.pairingKey("mig01", step("ge-0/0/4", "ae0"));
  const b = R.pairingKey("mig01", { old: { node: "MX1", port: "ge-0/0/4" }, new: { node: "PTX1", port: "ae0" } });
  assert.strictEqual(a, b);
  assert.notStrictEqual(a, R.pairingKey("mig02", step("ge-0/0/4", "ae0")));
  assert.notStrictEqual(a, R.pairingKey("mig01", step("ge-0/0/5", "ae0")));
});

test("buildPairingGroups: independent pairings keep manifest order and own services", () => {
  const rows = [row("ge-0/0/6", "et-0/0/8", { pre: true, post: true }), row("ge-0/0/4", "ae0", { pre: true, post: true })];
  const evaluations = [
    evaluation("post-ae0.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), [L1("ae0"), scope("A", "Internet")]),
    evaluation("post-et8.json", "pre-ge6.json", step("ge-0/0/6", "et-0/0/8"), [scope("C", "Core")]),
  ];
  const model = R.buildPairingGroups({ runName: "mig01", rows, evaluations, snapshots: SNAPSHOTS });
  assert.strictEqual(model.groups.length, 2);
  assert.strictEqual(model.groups[0].old.port, "ge-0/0/6");
  assert.deepStrictEqual(model.groups[0].evaluations[0].serviceEntries.map((e) => e.scope.scope_id), ["C"]);
  assert.deepStrictEqual(model.groups[1].evaluations[0].serviceEntries.map((e) => e.scope.scope_id), ["A"]);
  assert.deepStrictEqual(model.groups[1].evaluations[0].infrastructureEntries.map((e) => e.scope.scope_id), ["l1:ae0"]);
  assert.strictEqual(model.groups[0].sharedDestination, false);
  assert.strictEqual(model.groups[0].pending, false);
});

test("buildPairingGroups: shared LAG stays separate by full endpoint, shared flag set, keys differ", () => {
  const rows = [row("ge-0/0/4", "ae0", { pre: true, post: true }), row("ge-0/0/5", "ae0", { pre: true, post: true })];
  const evaluations = [
    evaluation("post-ae0.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), [scope("A", "Internet")]),
    evaluation("post-ae0.json", "pre-ge5.json", step("ge-0/0/5", "ae0"), [scope("B", "IPVPN")]),
  ];
  const model = R.buildPairingGroups({ runName: "mig01", rows, evaluations, snapshots: SNAPSHOTS });
  assert.strictEqual(model.groups.length, 2);
  assert.ok(model.groups.every((g) => g.sharedDestination));
  assert.deepStrictEqual(model.groups[0].evaluations[0].serviceEntries.map((e) => e.scope.scope_id), ["A"]);
  assert.deepStrictEqual(model.groups[1].evaluations[0].serviceEntries.map((e) => e.scope.scope_id), ["B"]);
});

test("buildPairingGroups: whole-device fallback -> identical subject/baseline/scope ids still get distinct keys", () => {
  const rows = [row("ge-0/0/4", "ae0"), row("ge-0/0/5", "ae0")];
  const evaluations = [
    evaluation("post-ae0.json", "pre-all.json", step("ge-0/0/4", "ae0"), [scope("A", "Internet"), scope("B", "IPVPN")]),
    evaluation("post-ae0.json", "pre-all.json", step("ge-0/0/5", "ae0"), [scope("A", "Internet"), scope("B", "IPVPN")]),
  ];
  const model = R.buildPairingGroups({ runName: "mig01", rows, evaluations, snapshots: SNAPSHOTS });
  const [g4, g5] = model.groups;
  assert.strictEqual(g4.evaluations[0].wholeDeviceBaseline, true);
  assert.strictEqual(g5.evaluations[0].wholeDeviceBaseline, true);
  assert.notStrictEqual(g4.evaluations[0].serviceEntries[0].key, g5.evaluations[0].serviceEntries[0].key);
  // exact capture flags are not touched by the fallback
  assert.strictEqual(g4.captureRow.pre, false);
});

test("buildPairingGroups: pending pairing kept, rollback/other/same-device partitioned, nothing lost", () => {
  const rows = [row("ge-0/0/7", "et-0/0/9", { pre: true })];
  const evaluations = [
    evaluation("rb-ge6.json", "pre-ge6.json", null, [scope("R", "Core")]),
    evaluation("post-all.json", "pre-all.json", null, [scope("W", "Core")]),
    { ...evaluation("post-ae0.json", "pre-ae0.json", null, [scope("S", "Core")]), same_device: true },
  ];
  const model = R.buildPairingGroups({ runName: "mig01", rows, evaluations, snapshots: SNAPSHOTS });
  assert.strictEqual(model.groups.length, 1);
  assert.strictEqual(model.groups[0].pending, true);
  assert.deepStrictEqual(model.rollback.map((m) => m.evaluation.subject), ["rb-ge6.json"]);
  assert.deepStrictEqual(model.other.map((m) => m.evaluation.subject), ["post-all.json"]);
  assert.strictEqual(model.other[0].subjectMetaMissing, true);
  assert.deepStrictEqual(model.sameDevice.map((m) => m.evaluation.subject), ["post-ae0.json"]);
  const total = model.groups.reduce((n, g) => n + g.evaluations.length, 0) + model.rollback.length + model.other.length + model.sameDevice.length;
  assert.strictEqual(total, evaluations.length);
});

test("buildPairingGroups: step absent from rows -> appended group flagged metadataMismatch; second evaluation for one pairing kept as subgroup", () => {
  const rows = [row("ge-0/0/4", "ae0")];
  const evaluations = [
    evaluation("post-ae0.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), [scope("A", "Internet")]),
    evaluation("post-ae0-2.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), [scope("A", "Internet")]),
    evaluation("post-et8.json", "pre-ge6.json", step("ge-0/0/6", "et-0/0/8"), [scope("C", "Core")]),
  ];
  const model = R.buildPairingGroups({ runName: "mig01", rows, evaluations, snapshots: SNAPSHOTS });
  assert.strictEqual(model.groups.length, 2);
  assert.strictEqual(model.groups[0].evaluations.length, 2);
  assert.strictEqual(model.groups[1].metadataMismatch, true);
  assert.strictEqual(model.groups[1].captureRow, null);
});

test("buildEvaluationModel: missing baseline, missing baseline metadata, excluded count, warning", () => {
  const noBaseline = evaluation("post-ae0.json", null, step("ge-0/0/4", "ae0"), [scope("A", "Internet")], { warning: "chybi pre snimek MX1:ge-0/0/4" });
  const m1 = R.buildEvaluationModel(noBaseline, 0, SNAPSHOTS, "pair");
  assert.strictEqual(m1.hasBaseline, false);
  assert.strictEqual(m1.wholeDeviceBaseline, false);
  assert.strictEqual(m1.warning, "chybi pre snimek MX1:ge-0/0/4");
  const unresolved = evaluation("post-ae0.json", "pre-ghost.json", step("ge-0/0/4", "ae0"), []);
  unresolved.result.excluded_services = [{ scope_id: "B" }, { scope_id: "C" }];
  const m2 = R.buildEvaluationModel(unresolved, 1, SNAPSHOTS, "pair");
  assert.strictEqual(m2.baselineMetaMissing, true);
  assert.strictEqual(m2.excludedCount, 2);
  assert.strictEqual(R.evaluationLabel(noBaseline), "MX1:ge-0/0/4 -> PTX1:ae0");
  assert.strictEqual(R.evaluationLabel({ subject: "x.json", baseline: null, step: null }), "x.json");
});

test("buildPairingGroups: payload is not mutated", () => {
  const rows = [row("ge-0/0/4", "ae0")];
  const evaluations = [evaluation("post-ae0.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), [scope("A", "Internet")])];
  const before = JSON.stringify({ rows, evaluations, SNAPSHOTS });
  R.buildPairingGroups({ runName: "mig01", rows, evaluations, snapshots: SNAPSHOTS });
  assert.strictEqual(JSON.stringify({ rows, evaluations, SNAPSHOTS }), before);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/run_results.test.js`
Expected: FAIL, `Cannot find module '../../migration_validator/gui/static/run_results.js'`

- [ ] **Step 3: Write the module**

Create `migration_validator/gui/static/run_results.js`:

```js
// Pure view-model for the mapped-migration run overview: groups the
// /api/runs/{run} rows and /api/runs/{run}/evaluation payload by old -> new
// port pairing. No DOM, no fetch; app.js renders what comes out of here.
// Payloads are treated as immutable - never assign into them.
"use strict";

const SERVICE_TYPE_ORDER = ["Internet", "IPVPN", "E-Line", "E-LAN", "Core"];
const ALL_TYPES = "All";
const UNKNOWN_TYPE = "Unknown";
const LAYER1_TYPE = "Layer1";
const DEVICE_SCOPE_ID = "device";
const STATUS_KEYS = ["pass", "recv", "warn", "fail", "skip", "info"];

function classifyScope(scope) {
  if (scope.scope_id === DEVICE_SCOPE_ID) return "device";
  const identity = scope.identity || {};
  const key = scope.key || {};
  if (identity.service_type === LAYER1_TYPE || key.service_type === LAYER1_TYPE) return "layer1";
  return "service";
}

function rawServiceType(scope) {
  const identity = scope.identity || {};
  const key = scope.key || {};
  return identity.service_type || key.service_type || UNKNOWN_TYPE;
}

function pairingKey(runName, step) {
  return JSON.stringify([runName, step.old.node, step.old.port, step.new.node, step.new.port]);
}

function sameEndpoint(a, b) {
  return !!a && !!b && a.node === b.node && (a.port ?? null) === (b.port ?? null);
}

function findRecord(snapshots, file) {
  if (!file) return null;
  return (snapshots || []).find((s) => s.file === file) || null;
}

function evaluationLabel(evaluation) {
  if (evaluation.step) {
    const { old: o, new: n } = evaluation.step;
    return `${o.node}:${o.port} -> ${n.node}:${n.port}`;
  }
  return evaluation.subject;
}

function buildEvaluationModel(evaluation, index, snapshots, sectionKey) {
  const result = evaluation.result || {};
  const subjectRecord = findRecord(snapshots, evaluation.subject);
  const baselineRecord = findRecord(snapshots, evaluation.baseline);
  const key = `${sectionKey}|${index}|${evaluation.subject}|${evaluation.baseline || ""}`;
  const serviceEntries = [];
  const infrastructureEntries = [];
  (result.scopes || []).forEach((scope) => {
    const kind = classifyScope(scope);
    const entry = {
      key: `${key}|${scope.scope_id}`,
      scope,
      kind,
      serviceType: kind === "service" ? rawServiceType(scope) : null,
    };
    (kind === "service" ? serviceEntries : infrastructureEntries).push(entry);
  });
  const unmatched = result.unmatched || {};
  return {
    key,
    label: evaluationLabel(evaluation),
    evaluation,
    subjectRecord,
    baselineRecord,
    warning: evaluation.warning || null,
    hasBaseline: result.baseline != null,
    wholeDeviceBaseline: !!(evaluation.baseline && baselineRecord && baselineRecord.port === null),
    baselineMetaMissing: !!(evaluation.baseline && !baselineRecord),
    subjectMetaMissing: !subjectRecord,
    serviceEntries,
    infrastructureEntries,
    unmatchedBaseline: unmatched.baseline || [],
    unmatchedSubject: unmatched.subject || [],
    excludedCount: (result.excluded_services || []).length,
    filtered: result.filtered || null,
  };
}

function buildPairingGroups({ runName, rows, evaluations, snapshots }) {
  const groups = [];
  const byKey = new Map();
  for (const row of rows || []) {
    if (!row.old || !row.new) continue;
    const key = pairingKey(runName, row);
    if (byKey.has(key)) continue;
    const group = {
      key,
      old: row.old,
      new: row.new,
      captureRow: row,
      sharedDestination: false,
      metadataMismatch: false,
      pending: true,
      evaluations: [],
    };
    groups.push(group);
    byKey.set(key, group);
  }
  const rollback = [];
  const other = [];
  const sameDevice = [];
  (evaluations || []).forEach((evaluation, index) => {
    if (evaluation.same_device === true) {
      sameDevice.push(buildEvaluationModel(evaluation, index, snapshots, "same"));
      return;
    }
    if (evaluation.step) {
      const key = pairingKey(runName, evaluation.step);
      let group = byKey.get(key);
      if (!group) {
        group = {
          key,
          old: evaluation.step.old,
          new: evaluation.step.new,
          captureRow: null,
          sharedDestination: false,
          metadataMismatch: true,
          pending: true,
          evaluations: [],
        };
        groups.push(group);
        byKey.set(key, group);
      }
      group.evaluations.push(buildEvaluationModel(evaluation, index, snapshots, "pair"));
      return;
    }
    const model = buildEvaluationModel(evaluation, index, snapshots, "aux");
    if (model.subjectRecord && model.subjectRecord.phase === "rollback") rollback.push(model);
    else other.push(model);
  });
  for (const group of groups) {
    group.sharedDestination = groups.some((g) => g !== group && sameEndpoint(g.new, group.new));
    group.pending = group.evaluations.length === 0;
  }
  return { groups, rollback, other, sameDevice };
}

const MigRunResults = {
  SERVICE_TYPE_ORDER,
  ALL_TYPES,
  UNKNOWN_TYPE,
  STATUS_KEYS,
  classifyScope,
  rawServiceType,
  pairingKey,
  evaluationLabel,
  buildEvaluationModel,
  buildPairingGroups,
};

if (typeof module !== "undefined" && module.exports) module.exports = MigRunResults;
```

- [ ] **Step 4: Load it in the page**

In `migration_validator/gui/static/index.html`, replace

```html
<script src="/static/view.js"></script>
<script src="/static/profile_diff.js"></script>
<script src="/static/app.js"></script>
```

with

```html
<script src="/static/view.js"></script>
<script src="/static/profile_diff.js"></script>
<script src="/static/run_results.js"></script>
<script src="/static/app.js"></script>
```

- [ ] **Step 5: Run tests**

Run: `node --test tests/js/*.test.js`
Expected: all pass (existing view/profile_diff tests plus the new file).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui/static/run_results.js tests/js/run_results.test.js migration_validator/gui/static/index.html
git commit -m "feat(gui): run_results.js - grouping of evaluations by port pairing"
```

---

## Task 2: Helper module — filter, linked context, counts, choices, attention

**Files:**
- Modify: `migration_validator/gui/static/run_results.js`
- Modify: `tests/js/run_results.test.js`

**Interfaces:**
- Produces:
  - `countStatuses(statuses) -> {pass,recv,warn,fail,skip,info}`
  - `linkedIds(scope) -> string[]`
  - `filterServiceEntries(entries, selectedType) -> { visible: [{entry, linkedContext}], matchedCount, linkedContextCount }`
  - `missingPartners(entry, entries) -> string[]` (scope ids the link points to that are not in `entries`)
  - `countByType(entries) -> {[type]: n}`
  - `serviceTypeChoices(catalogueTypes, entries) -> [{type, count}]` (never includes `All`)
  - `groupNeedsAttention(group, selectedType) -> boolean`
  - `mainEvaluationModels(model) -> EvaluationModel[]` (groups' evaluations + rollback + other; no same-device)

- [ ] **Step 1: Write the failing tests**

Append to `tests/js/run_results.test.js`:

```js
function linked(l3Id, l2Id, l3Type, l2Type) {
  const l3 = scope(l3Id, l3Type, { link: { role: "l3", peers: [{ scope_id: l2Id, interface: "ae0.10", instance: "X" }] } });
  const l2 = scope(l2Id, l2Type, { link: { role: "l2", peer_scope_id: l3Id, peer_interface: "irb.10", peer_instance: "X" } });
  return [l3, l2];
}
const entriesOf = (scopes) => R.buildEvaluationModel(evaluation("s", "b", step("ge-0/0/4", "ae0"), scopes), 0, [], "pair").serviceEntries;

test("filterServiceEntries: All keeps everything, type narrows, backend order kept", () => {
  const entries = entriesOf([scope("A", "Internet"), scope("B", "IPVPN"), scope("C", "Internet")]);
  const all = R.filterServiceEntries(entries, R.ALL_TYPES);
  assert.strictEqual(all.visible.length, 3);
  assert.strictEqual(all.matchedCount, 3);
  const net = R.filterServiceEntries(entries, "Internet");
  assert.deepStrictEqual(net.visible.map((v) => v.entry.scope.scope_id), ["A", "C"]);
  assert.strictEqual(net.matchedCount, 2);
  assert.strictEqual(net.linkedContextCount, 0);
});

test("filterServiceEntries: linked partner retained as context in both directions, counted separately", () => {
  const [l3, l2] = linked("IRB", "VPLS", "Internet", "E-LAN");
  const entries = entriesOf([l3, l2, scope("Z", "Core")]);
  const byL2 = R.filterServiceEntries(entries, "E-LAN");
  assert.deepStrictEqual(byL2.visible.map((v) => [v.entry.scope.scope_id, v.linkedContext]), [["IRB", true], ["VPLS", false]]);
  assert.strictEqual(byL2.matchedCount, 1);
  assert.strictEqual(byL2.linkedContextCount, 1);
  const byL3 = R.filterServiceEntries(entries, "Internet");
  assert.deepStrictEqual(byL3.visible.map((v) => [v.entry.scope.scope_id, v.linkedContext]), [["IRB", false], ["VPLS", true]]);
});

test("missingPartners: partner absent from the evaluation is reported, present partner is not", () => {
  const [l3, l2] = linked("IRB", "VPLS", "Internet", "E-LAN");
  const both = entriesOf([l3, l2]);
  assert.deepStrictEqual(R.missingPartners(both[1], both), []);
  const only = entriesOf([l2]);
  assert.deepStrictEqual(R.missingPartners(only[0], only), ["IRB"]);
});

test("countByType / serviceTypeChoices: canonical order first, catalogue extras, data-only and Unknown after", () => {
  const entries = entriesOf([scope("A", "Internet"), scope("B", "Core"), scope("C", "Core"), scope("U", null), scope("X", "Exotic")]);
  assert.deepStrictEqual(R.countByType(entries), { Internet: 1, Core: 2, Unknown: 1, Exotic: 1 });
  const choices = R.serviceTypeChoices(["Core", "E-LAN", "E-Line", "IPVPN", "Internet", "Zeta"], entries);
  assert.deepStrictEqual(choices.map((c) => c.type), ["Internet", "IPVPN", "E-Line", "E-LAN", "Core", "Zeta", "Exotic", "Unknown"]);
  assert.deepStrictEqual(choices.map((c) => c.count), [1, 0, 0, 0, 2, 0, 1, 1]);
  // absent catalogue: choices still derive from the data
  assert.deepStrictEqual(R.serviceTypeChoices(null, entries).map((c) => c.type), ["Internet", "IPVPN", "E-Line", "E-LAN", "Core", "Exotic", "Unknown"]);
});

test("countStatuses: all six statuses, lower-case keys", () => {
  assert.deepStrictEqual(R.countStatuses(["PASS", "RECV", "WARN", "FAIL", "SKIP", "INFO", "PASS"]), { pass: 2, recv: 1, warn: 1, fail: 1, skip: 1, info: 1 });
});

test("groupNeedsAttention: hidden WARN/FAIL, infrastructure WARN/FAIL, unmatched findings", () => {
  const mk = (scopes, unmatched) => {
    const ev = evaluation("post-ae0.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), scopes);
    if (unmatched) ev.result.unmatched = unmatched;
    return R.buildPairingGroups({ runName: "r", rows: [row("ge-0/0/4", "ae0")], evaluations: [ev], snapshots: SNAPSHOTS }).groups[0];
  };
  const hiddenFail = mk([scope("A", "Internet"), scope("B", "IPVPN", { status: "FAIL" })]);
  assert.strictEqual(R.groupNeedsAttention(hiddenFail, R.ALL_TYPES), false);
  assert.strictEqual(R.groupNeedsAttention(hiddenFail, "Internet"), true);
  const infra = mk([L1("ae0"), scope("A", "Internet")]);
  infra.evaluations[0].infrastructureEntries[0].scope.status; // PASS -> no attention
  assert.strictEqual(R.groupNeedsAttention(infra, R.ALL_TYPES), false);
  const infraWarn = mk([{ ...L1("ae0"), status: "WARN" }, scope("A", "Internet")]);
  assert.strictEqual(R.groupNeedsAttention(infraWarn, R.ALL_TYPES), true);
  const unmatched = mk([scope("A", "Internet")], { baseline: [{ scope_id: "B", description: "B", service_type: "IPVPN", reason: "no match" }], subject: [] });
  assert.strictEqual(R.groupNeedsAttention(unmatched, R.ALL_TYPES), true);
  const pending = R.buildPairingGroups({ runName: "r", rows: [row("ge-0/0/4", "ae0")], evaluations: [], snapshots: [] }).groups[0];
  assert.strictEqual(R.groupNeedsAttention(pending, R.ALL_TYPES), false);
});

test("mainEvaluationModels: pairs + rollback + other, never same-device", () => {
  const evaluations = [
    evaluation("post-ae0.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), [scope("A", "Internet")]),
    evaluation("rb-ge6.json", "pre-ge6.json", null, [scope("R", "Core")]),
    { ...evaluation("post-ae0.json", "pre-ae0.json", null, [scope("S", "Core")]), same_device: true },
  ];
  const model = R.buildPairingGroups({ runName: "r", rows: [row("ge-0/0/4", "ae0")], evaluations, snapshots: SNAPSHOTS });
  assert.deepStrictEqual(R.mainEvaluationModels(model).map((m) => m.evaluation.subject), ["post-ae0.json", "rb-ge6.json"]);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/run_results.test.js`
Expected: FAIL, `R.filterServiceEntries is not a function` (and siblings).

- [ ] **Step 3: Implement**

Insert before `const MigRunResults = {` in `run_results.js`:

```js
function countStatuses(statuses) {
  const counts = { pass: 0, recv: 0, warn: 0, fail: 0, skip: 0, info: 0 };
  for (const status of statuses) {
    const key = String(status || "").toLowerCase();
    if (key in counts) counts[key] += 1;
  }
  return counts;
}

function linkedIds(scope) {
  const link = scope.link || null;
  if (!link) return [];
  const ids = [];
  if (link.peer_scope_id) ids.push(link.peer_scope_id);
  for (const peer of link.peers || []) if (peer && peer.scope_id) ids.push(peer.scope_id);
  return ids;
}

function filterServiceEntries(entries, selectedType) {
  if (selectedType === ALL_TYPES) {
    return {
      visible: entries.map((entry) => ({ entry, linkedContext: false })),
      matchedCount: entries.length,
      linkedContextCount: 0,
    };
  }
  const ids = new Set(entries.map((e) => e.scope.scope_id));
  const matched = new Set(
    entries.filter((e) => e.serviceType === selectedType).map((e) => e.scope.scope_id)
  );
  const context = new Set();
  for (const entry of entries) {
    const id = entry.scope.scope_id;
    if (matched.has(id)) {
      for (const pid of linkedIds(entry.scope)) if (ids.has(pid) && !matched.has(pid)) context.add(pid);
    } else if (linkedIds(entry.scope).some((pid) => matched.has(pid))) {
      context.add(id);
    }
  }
  const visible = entries
    .filter((e) => matched.has(e.scope.scope_id) || context.has(e.scope.scope_id))
    .map((entry) => ({ entry, linkedContext: context.has(entry.scope.scope_id) }));
  return { visible, matchedCount: matched.size, linkedContextCount: context.size };
}

function missingPartners(entry, entries) {
  const ids = new Set(entries.map((e) => e.scope.scope_id));
  return linkedIds(entry.scope).filter((id) => !ids.has(id));
}

function countByType(entries) {
  const counts = {};
  for (const entry of entries) {
    const type = entry.serviceType || UNKNOWN_TYPE;
    counts[type] = (counts[type] || 0) + 1;
  }
  return counts;
}

function serviceTypeChoices(catalogueTypes, entries) {
  const counts = countByType(entries);
  const ordered = [];
  const seen = new Set();
  const push = (type) => {
    if (seen.has(type)) return;
    seen.add(type);
    ordered.push({ type, count: counts[type] || 0 });
  };
  SERVICE_TYPE_ORDER.forEach(push);
  (catalogueTypes || []).slice().sort().forEach(push);
  Object.keys(counts).filter((t) => t !== UNKNOWN_TYPE).sort().forEach(push);
  if (counts[UNKNOWN_TYPE]) push(UNKNOWN_TYPE);
  return ordered;
}

function groupNeedsAttention(group, selectedType) {
  const bad = (status) => status === "WARN" || status === "FAIL";
  for (const model of group.evaluations) {
    if (model.infrastructureEntries.some((e) => bad(e.scope.status))) return true;
    if (model.unmatchedBaseline.length || model.unmatchedSubject.length) return true;
    const shown = new Set(filterServiceEntries(model.serviceEntries, selectedType).visible.map((v) => v.entry));
    if (model.serviceEntries.some((e) => !shown.has(e) && bad(e.scope.status))) return true;
  }
  return false;
}

function mainEvaluationModels(model) {
  return [...model.groups.flatMap((g) => g.evaluations), ...model.rollback, ...model.other];
}
```

and add to the `MigRunResults` object: `countStatuses, linkedIds, filterServiceEntries, missingPartners, countByType, serviceTypeChoices, groupNeedsAttention, mainEvaluationModels,`.

- [ ] **Step 4: Run tests**

Run: `node --test tests/js/*.test.js`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/static/run_results.js tests/js/run_results.test.js
git commit -m "feat(gui): run_results.js - service type filter, linked context, counts"
```

---

## Task 3: Helper module — unassigned findings per subject snapshot

**Files:**
- Modify: `migration_validator/gui/static/run_results.js`
- Modify: `tests/js/run_results.test.js`

**Interfaces:**
- Produces: `collectUnassigned(models) -> [{ subject, record, variants: [{ payload, labels: string[] }] }]` — one entry per distinct subject filename, in first-seen order; identical payloads (deep JSON equality) collapse into one variant listing every evaluation label; differing payloads stay as separate labelled variants.

- [ ] **Step 1: Write the failing tests**

Append to `tests/js/run_results.test.js`:

```js
test("collectUnassigned: identical payload from one subject shown once; different subjects and differing payloads preserved", () => {
  const peers = { bgp_peers: [{ peer: "10.0.0.1", routing_instance: "X" }], static_routes: [], bfd_sessions: [] };
  const other = { bgp_peers: [], static_routes: [{ prefix: "10.0.0.0/8" }], bfd_sessions: [] };
  const withU = (ev, payload) => ({ ...ev, result: { ...ev.result, unassigned: payload } });
  const evaluations = [
    withU(evaluation("post-ae0.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), []), peers),
    withU(evaluation("post-ae0.json", "pre-ge5.json", step("ge-0/0/5", "ae0"), []), peers),
    withU(evaluation("post-et8.json", "pre-ge6.json", step("ge-0/0/6", "et-0/0/8"), []), other),
    withU(evaluation("post-et8.json", "pre-all.json", null, []), peers),
  ];
  const model = R.buildPairingGroups({ runName: "r", rows: [row("ge-0/0/4", "ae0"), row("ge-0/0/5", "ae0"), row("ge-0/0/6", "et-0/0/8")], evaluations, snapshots: SNAPSHOTS });
  const found = R.collectUnassigned(R.mainEvaluationModels(model));
  assert.deepStrictEqual(found.map((f) => f.subject), ["post-ae0.json", "post-et8.json"]);
  assert.strictEqual(found[0].variants.length, 1);
  assert.deepStrictEqual(found[0].variants[0].labels, ["MX1:ge-0/0/4 -> PTX1:ae0", "MX1:ge-0/0/5 -> PTX1:ae0"]);
  assert.strictEqual(found[0].record.device, "PTX1");
  assert.strictEqual(found[1].variants.length, 2);
  assert.deepStrictEqual(found[1].variants[1].labels, ["post-et8.json"]);
  assert.strictEqual(found[1].variants[0].payload, other);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test tests/js/run_results.test.js`
Expected: FAIL, `R.collectUnassigned is not a function`.

- [ ] **Step 3: Implement**

Insert before `const MigRunResults = {`:

```js
function collectUnassigned(models) {
  const bySubject = new Map();
  for (const model of models) {
    const payload = (model.evaluation.result || {}).unassigned || {};
    const serialized = JSON.stringify(payload);
    let entry = bySubject.get(model.evaluation.subject);
    if (!entry) {
      entry = { subject: model.evaluation.subject, record: model.subjectRecord, variants: [] };
      bySubject.set(model.evaluation.subject, entry);
    }
    let variant = entry.variants.find((v) => v.serialized === serialized);
    if (!variant) {
      variant = { serialized, payload, labels: [] };
      entry.variants.push(variant);
    }
    variant.labels.push(model.label);
  }
  return [...bySubject.values()];
}
```

Add `collectUnassigned,` to the exported object.

- [ ] **Step 4: Run tests**

Run: `node --test tests/js/*.test.js`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/static/run_results.js tests/js/run_results.test.js
git commit -m "feat(gui): run_results.js - unassigned findings deduplicated per subject snapshot"
```

---

## Task 4: app.js — state, focus retention, results-table extensions

**Files:**
- Modify: `migration_validator/gui/static/app.js` (constructor `this.state` at ~119; the three `this.state.openResults = {};` resets at ~391, ~927, ~3224; `render()` at ~1955; `buildResultsTable` at ~761; `buildCountsStrip` at ~2092)

**Interfaces:**
- Produces:
  - `this.state.openPairings: {[groupKey]: boolean}` — explicit user choices only
  - `this.state.serviceTypeFilter: string` (`"All"` or a raw type)
  - `this.pendingFocus: string | null` — `data-focus-key` to focus after next render
  - `resetResultState()` — clears the three above plus `openResults`
  - `buildResultsTable(entries, opts)` accepts entries with optional `contextTag: string` and `linkNoteOverride: string`
  - `buildCountsStrip(services, checks, matchedLine, passUnchanged, title)` — optional leading label

No new tests here (DOM code is verified in Task 8); keep the JS suite green.

- [ ] **Step 1: State + reset helper**

In the constructor, after `openResults: {},` add:

```js
      openPairings: {},
      serviceTypeFilter: "All",
```

After `this.groupPollTimer = null;` add `this.pendingFocus = null;`.

Add a method right after `toggleResult(key)`:

```js
  resetResultState() {
    this.state.openResults = {};
    this.state.openPairings = {};
    this.state.serviceTypeFilter = MigRunResults.ALL_TYPES;
  }

  togglePairing(key) {
    this.state.openPairings[key] = !this.isPairingOpen(key);
    this.pendingFocus = `pair:${key}`;
    this.render();
  }

  isPairingOpen(key, defaultOpen = false) {
    const explicit = this.state.openPairings[key];
    return explicit === undefined ? defaultOpen : explicit;
  }

  setAllPairings(groups, open) {
    for (const group of groups) this.state.openPairings[group.key] = open;
    this.render();
  }

  setServiceTypeFilter(type) {
    this.state.serviceTypeFilter = type;
    this.pendingFocus = `chip:${type}`;
    this.render();
  }
```

Replace each of the three `this.state.openResults = {};` lines (archive confirm ~391, `selectRun` ~927, `submitNewRun` ~3224) with `this.resetResultState();`.

- [ ] **Step 2: Focus retention in `render()`**

In `render()`, before the `switch` add (so a polling rerender keeps focus on whatever pairing control or chip the user is on):

```js
    if (!this.pendingFocus) {
      const active = document.activeElement;
      const key = active && active.dataset ? active.dataset.focusKey : null;
      if (key && this.mainEl.contains(active)) this.pendingFocus = key;
    }
```

and after the `switch`, before `this.syncCapturePolling();` add:

```js
    if (this.pendingFocus) {
      const key = this.pendingFocus;
      this.pendingFocus = null;
      const target = this.mainEl.querySelector(`[data-focus-key="${CSS.escape(key)}"]`);
      if (target) target.focus();
    }
```

- [ ] **Step 3: Extend `buildResultsTable`**

In `buildResultsTable`, replace

```js
            el("span", { className: "svc-cell", text: view.service_type }),
```

with

```js
            el("span", {
              className: "svc-cell",
              children: [
                document.createTextNode(view.service_type),
                entry.contextTag ? el("span", { className: "ctx-tag", text: entry.contextTag }) : null,
              ],
            }),
```

and replace the `if (open)` block's detail panel call with

```js
      if (open) {
        const detailView = entry.linkNoteOverride ? { ...view, link_note: entry.linkNoteOverride } : view;
        table.appendChild(
          el("div", {
            className: "detail-cell",
            children: [this.buildDetailPanel(detailView, entry.hasBaseline)],
          })
        );
      }
```

Also make the row keyboard-operable: in the row `el("div", { className: "results-row" ..., onClick ... })` add `attrs: { tabindex: "0", role: "button", "aria-expanded": open ? "true" : "false" }` and after creating the row element attach `rowEl.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this.toggleResult(entry.key); } });` (assign the created element to `const rowEl` before `table.appendChild(rowEl)`).

- [ ] **Step 4: Optional label on the counts strip**

Change the signature to `buildCountsStrip(services, checks, matchedLine, passUnchanged = 0, title = null)` and, right after `const strip = el("div", { className: "counts", children: [...] });`, add:

```js
    if (title) strip.insertBefore(el("span", { className: "counts-title", text: title }), strip.firstChild);
```

- [ ] **Step 5: Verify nothing broke**

Run: `node --test tests/js/*.test.js` (pass) and `node --check migration_validator/gui/static/app.js` (no syntax error).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui/static/app.js
git commit -m "feat(gui): pairing/filter state, focus retention, results-table context tags"
```

---

## Task 5: app.js — `renderPairedResults` with pairing headers, panels and auxiliary sections

**Files:**
- Modify: `migration_validator/gui/static/app.js` (`renderRunOverview` ~2129-2386; new methods placed after `buildSameDeviceSection`)

**Interfaces:**
- Consumes: everything from Tasks 1–4; `MigView.buildView`, `buildResultsTable`, `buildDetailPanel`, `buildFlagCell`, `buildPairingTable`, `buildUnmatchedSection`, `buildUnassignedSection`, `buildCaptureStepsLine`, `buildCaptureIssueLines`, `hasCaptureIssues`, `rowMatchesCapture`.
- Produces: `renderPairedResults(detail, evaluations, wholeRows)`, `buildPairingGroup(group, filterType, evaluationFailed)`, `buildPairingHeader(...)`, `buildEvaluationPanel(model, filterType, group)`, `buildAuxiliarySection(title, models, opts)`, `buildUnassignedBySubject(found)`, `resultEntries(entries, opts)`.

- [ ] **Step 1: Branch in `renderRunOverview`**

Replace the block from `const evaluations = this.cache.evaluation ? ...` down to (and including) the `if (this.cache.evaluation) { ... unassigned ... }` block with:

```js
    const evaluations = this.cache.evaluation ? this.cache.evaluation.evaluations : [];
    const allRows = detail.rows || [];
    const mappingRows = allRows.filter((r) => r.old && r.new);
    const wholeRows = allRows.filter((r) => !r.old || !r.new);
    const mapped = !single && mappingRows.length > 0;

    if (mapped) {
      this.renderPairedResults(detail, evaluations, wholeRows);
    } else {
      this.renderFlatResults(detail, evaluations, single, allRows, wholeRows, mappingRows);
    }
```

Then move the original code (counts strip, captures table, same-device section, flat results, unmatched, unassigned) verbatim into a new method `renderFlatResults(detail, evaluations, single, allRows, wholeRows, mappingRows)` — the only edit inside it is deleting the now-duplicated `const allRows/mappingRows/wholeRows` declarations. The standalone capture notice and the footer stay in `renderRunOverview` after the branch (they use `allRows`, still in scope).

- [ ] **Step 2: `renderPairedResults`**

Add after `buildSameDeviceSection`:

```js
  // -- mapped migration: results grouped by port pairing ------------------

  renderPairedResults(detail, evaluations, wholeRows) {
    const R = MigRunResults;
    const model = R.buildPairingGroups({
      runName: detail.name,
      rows: detail.rows || [],
      evaluations,
      snapshots: detail.snapshots || [],
    });
    const mainModels = R.mainEvaluationModels(model);
    const filterType = this.state.serviceTypeFilter;
    const evaluationFailed = !!this.cache.evaluationError;

    // Top summary: unfiltered, main population, service rows exclude device/L1.
    const services = R.countStatuses(mainModels.flatMap((m) => m.serviceEntries.map((e) => e.scope.status)));
    const checks = { pass: 0, recv: 0, warn: 0, fail: 0, skip: 0, info: 0 };
    let matched = 0, unmatchedBaseline = 0, unmatchedSubject = 0, passUnchanged = 0;
    for (const m of mainModels) {
      const summary = (m.evaluation.result || {}).summary || {};
      for (const key of Object.keys(checks)) checks[key] += summary[key] || 0;
      passUnchanged += summary.pass_unchanged || 0;
      matched += summary.scopes_matched || 0;
      unmatchedBaseline += summary.unmatched_baseline || 0;
      unmatchedSubject += summary.unmatched_subject || 0;
    }
    const matchedLine = mainModels.length
      ? `Spárováno ${matched} služeb, ${unmatchedBaseline} nespárováno v baseline, ${unmatchedSubject} v subject`
      : null;
    this.mainEl.appendChild(
      this.buildCountsStrip(services, checks, matchedLine, passUnchanged, "Full run (current profile)")
    );

    // Filter over paired service results only.
    const pairedEntries = model.groups.flatMap((g) => g.evaluations).flatMap((m) => m.serviceEntries);
    const catalogueTypes = this.cache.catalogue ? this.cache.catalogue.service_types : null;
    this.mainEl.appendChild(this.buildServiceTypeFilter(pairedEntries, catalogueTypes, mainModels));

    const shown = R.filterServiceEntries(pairedEntries, filterType);
    const head = el("div", {
      className: "section-head",
      children: [
        el("div", { className: "subsection-title", text: "Port pairings" }),
        el("span", {
          className: "section-note",
          text: `${shown.matchedCount} of ${pairedEntries.length} service results shown`
            + (shown.linkedContextCount ? ` + ${shown.linkedContextCount} linked context rows` : ""),
        }),
        el("span", { className: "section-spacer" }),
        el("button", { className: "link-btn", text: "Expand all", attrs: { type: "button" },
          onClick: () => this.setAllPairings(model.groups, true) }),
        el("button", { className: "link-btn", text: "Collapse all", attrs: { type: "button" },
          onClick: () => this.setAllPairings(model.groups, false) }),
      ],
    });
    this.mainEl.appendChild(head);
    for (const group of model.groups) {
      this.mainEl.appendChild(this.buildPairingGroup(group, filterType, evaluationFailed));
    }

    if (wholeRows.length > 0) {
      this.mainEl.appendChild(el("div", { className: "subsection-title", text: "Other captures" }));
      this.mainEl.appendChild(this.buildPairingTable(wholeRows));
    }
    if (model.rollback.length) {
      this.mainEl.appendChild(this.buildAuxiliarySection("Rollback — original device", model.rollback, { singlePort: true }));
    }
    if (model.other.length) {
      this.mainEl.appendChild(this.buildAuxiliarySection("Other evaluations", model.other, { singlePort: false }));
    }
    const sameDevice = this.buildSameDeviceSection();
    if (sameDevice) {
      this.mainEl.appendChild(sameDevice);
      const items = model.sameDevice.flatMap((m) => this.unmatchedItems(m, `same-device ${m.evaluation.subject}`));
      if (items.length) {
        sameDevice.appendChild(el("div", { className: "subsection-title", text: `Nespárováno — ${items.length}` }));
        sameDevice.appendChild(this.buildUnmatchedSection(items));
      }
    }
    if (this.cache.evaluation) {
      const found = R.collectUnassigned([...mainModels, ...model.sameDevice]);
      this.mainEl.appendChild(this.buildUnassignedBySubject(found));
    }
  }

  unmatchedItems(model, pairLabel = null) {
    const items = [];
    for (const [side, list] of [["baseline", model.unmatchedBaseline], ["subject", model.unmatchedSubject]]) {
      for (const item of list) {
        items.push({
          side,
          label: item.description || item.scope_id,
          serviceType: item.service_type || "-",
          reason: item.reason,
          pairLabel,
        });
      }
    }
    return items;
  }

  resultEntries(entries, opts) {
    const all = (opts && opts.all) || entries;
    const contextKeys = (opts && opts.contextKeys) || new Set();
    return entries.map((entry) => {
      const view = MigView.buildView(entry.scope, {});
      const missing = MigRunResults.missingPartners(entry, all);
      return {
        key: entry.key,
        view,
        hasBaseline: !!(opts && opts.hasBaseline),
        scope: entry.scope,
        contextTag: contextKeys.has(entry.key) ? "Linked context" : null,
        linkNoteOverride: missing.length ? "Related context unavailable in this evaluation" : null,
      };
    });
  }
```

- [ ] **Step 3: Pairing group and header**

```js
  buildPairingGroup(group, filterType, evaluationFailed) {
    const open = this.isPairingOpen(group.key, !group.pending);
    const panelId = `pair-panel-${btoa(group.key).replace(/[^a-z0-9]/gi, "")}`;
    const wrap = el("section", { className: "pairing-group" + (open ? " open" : "") });
    wrap.appendChild(this.buildPairingHeader(group, filterType, evaluationFailed, open, panelId));
    if (open) {
      const body = el("div", { className: "pairing-body", attrs: { id: panelId } });
      if (group.pending) {
        body.appendChild(el("div", { className: "pairing-notice", text: this.pendingExplanation(group, evaluationFailed) }));
      } else {
        group.evaluations.forEach((m, i) => {
          if (group.evaluations.length > 1) {
            body.appendChild(el("div", { className: "eval-subgroup-title", text: `Evaluation ${i + 1}: ${m.evaluation.subject} vs ${m.evaluation.baseline || "(no baseline)"}` }));
          }
          body.appendChild(this.buildEvaluationPanel(m, filterType, group));
        });
      }
      wrap.appendChild(body);
    }
    return wrap;
  }

  pendingLabel(group, evaluationFailed) {
    const row = group.captureRow || {};
    if (evaluationFailed) return "Evaluation unavailable";
    if (row.post) return "Awaiting evaluation";
    if (row.pre) return "Awaiting post";
    return "Awaiting captures";
  }

  pendingExplanation(group, evaluationFailed) {
    const row = group.captureRow || {};
    const oldEp = `${group.old.node}:${group.old.port}`;
    const newEp = `${group.new.node}:${group.new.port}`;
    if (evaluationFailed) return `The evaluation request failed, so this pairing has no results to show. Captures: pre ${row.pre ? "yes" : "no"}, post ${row.post ? "yes" : "no"}.`;
    if (row.post) return `A post capture of ${newEp} exists but no evaluation was returned for this pairing. Run Evaluate run.`;
    if (row.pre) return `Pre-migration baseline of ${oldEp} captured. Capture the new port ${newEp} (post) to evaluate this pairing.`;
    return `No captures yet. Capture ${oldEp} (pre) before the migration and ${newEp} (post) after it.`;
  }

  buildPairingHeader(group, filterType, evaluationFailed, open, panelId) {
    const R = MigRunResults;
    const task = this.cache.captureProgress;
    const row = group.captureRow || { old: group.old, new: group.new, pre: false, post: false, rollback: false };
    const fullLabel = `${group.old.node}:${group.old.port} → ${group.new.node}:${group.new.port}`;

    const toggle = el("button", {
      className: "pairing-toggle",
      attrs: {
        type: "button",
        "aria-expanded": open ? "true" : "false",
        "aria-controls": panelId,
        "aria-label": `${open ? "Collapse" : "Expand"} pairing ${fullLabel}`,
        "data-focus-key": `pair:${group.key}`,
      },
      onClick: () => this.togglePairing(group.key),
      children: [
        el("span", { className: "chevron" + (open ? " open" : ""), html: "&#9654;" }),
        el("span", { className: "port-cell mono", text: group.old.port }),
        el("span", { className: "arrow", text: "→" }),
        el("span", { className: "port-cell mono", text: group.new.port }),
      ],
    });

    const flags = el("div", { className: "pairing-flags", children: [
      this.buildFlagCell(row, "pre", task), el("span", { className: "flag-label", text: "Pre" }),
      this.buildFlagCell(row, "post", task), el("span", { className: "flag-label", text: "Post" }),
      this.buildFlagCell(row, "rollback", task), el("span", { className: "flag-label", text: "Rollback" }),
    ] });
    if (group.sharedDestination) flags.appendChild(el("span", { className: "badge-pill neutral", text: "Shared destination" }));

    const badges = el("div", { className: "pairing-badges" });
    if (group.pending) {
      badges.appendChild(el("span", { className: "badge-pill neutral", text: this.pendingLabel(group, evaluationFailed) }));
    } else {
      const entries = group.evaluations.flatMap((m) => m.serviceEntries);
      const shown = R.filterServiceEntries(entries, filterType);
      const filtering = filterType !== R.ALL_TYPES;
      const countText = filtering
        ? `${shown.matchedCount} / ${entries.length} service results`
        : `${entries.length} service results`;
      badges.appendChild(el("span", { className: "pair-count", text: countText,
        attrs: { title: filtering ? "visible results under the current type filter" : "" } }));
      const counts = R.countStatuses(shown.visible.map((v) => v.entry.scope.status));
      for (const key of R.STATUS_KEYS) {
        if (!counts[key]) continue;
        badges.appendChild(el("span", {
          className: `badge-pill ${key}`,
          text: `${key.toUpperCase()} ${counts[key]}`,
          attrs: { title: filtering ? `${key.toUpperCase()} among visible results` : "" },
        }));
      }
      const unmatched = group.evaluations.reduce((n, m) => n + m.unmatchedBaseline.length + m.unmatchedSubject.length, 0);
      if (unmatched) badges.appendChild(el("span", { className: "badge-pill unmatched", text: `${unmatched} unmatched` }));
    }

    // Notices that must survive collapse and filtering.
    const notices = el("div", { className: "pairing-notices" });
    const notice = (cls, text) => notices.appendChild(el("span", { className: "badge-pill " + cls, text }));
    if (group.metadataMismatch) notice("warn", "Not in run mapping");
    for (const m of group.evaluations) {
      if (!m.hasBaseline) notice("warn", "No baseline — service ownership unverified");
      else if (m.wholeDeviceBaseline) notice("warn", "Whole-device baseline");
      else if (m.baselineMetaMissing) notice("warn", "Baseline metadata unavailable");
      if (m.infrastructureEntries.some((e) => e.scope.status === "WARN" || e.scope.status === "FAIL")) notice("warn", "Port checks need attention");
    }
    if (filterType !== R.ALL_TYPES && R.groupNeedsAttention(group, filterType)) {
      const hiddenBad = group.evaluations.some((m) => {
        const shown = new Set(R.filterServiceEntries(m.serviceEntries, filterType).visible.map((v) => v.entry));
        return m.serviceEntries.some((e) => !shown.has(e) && (e.scope.status === "WARN" || e.scope.status === "FAIL"));
      });
      if (hiddenBad) notice("warn", "Hidden WARN/FAIL");
    }

    const head = el("div", { className: "pairing-head", children: [
      el("div", { className: "pairing-head-main", children: [toggle, flags] }),
      el("div", { className: "pairing-head-side", children: [notices, badges] }),
    ] });

    // Live capture progress / failure / warnings for this pairing (shared
    // LAG: every pairing with that new endpoint matches).
    if (this.rowMatchesCapture(row, task)) {
      if (task.state === "running") {
        const parts = this.buildCaptureStepsLine(task);
        head.appendChild(el("div", { className: "row-note", children: parts.length ? parts : [el("span", { text: "starting…" })] }));
      } else if (task.state === "failed") {
        head.appendChild(el("div", { className: "row-note row-note-fail", text: task.error || "capture selhal" }));
      } else if (task.state === "done" && this.hasCaptureIssues(task)) {
        head.appendChild(el("div", { className: "row-note row-note-warn", children: this.buildCaptureIssueLines(task) }));
      }
    }
    return head;
  }
```

- [ ] **Step 4: Evaluation panel**

```js
  buildEvaluationPanel(model, filterType, group) {
    const R = MigRunResults;
    const panel = el("div", { className: "eval-panel" });
    const ev = model.evaluation;

    if (model.warning) panel.appendChild(el("div", { className: "notice notice-warn", text: model.warning }));
    if (!model.hasBaseline) {
      panel.appendChild(el("div", { className: "pairing-notice", text: "No baseline — service ownership unverified. Results below are standalone state checks of the new port, not a comparison." }));
    } else if (model.wholeDeviceBaseline) {
      panel.appendChild(el("div", { className: "notice notice-warn", text: "This comparison uses a whole-device baseline. Its services may span several old ports and are not guaranteed to belong only to this pairing." }));
    } else if (model.baselineMetaMissing) {
      panel.appendChild(el("div", { className: "notice notice-warn", text: `Baseline ${ev.baseline} is not listed in the run's snapshots — service ownership unavailable.` }));
    }
    if (group.metadataMismatch) {
      panel.appendChild(el("div", { className: "notice notice-warn", text: "This pairing is not in the current run mapping (metadata mismatch, reload to refresh)." }));
    }
    panel.appendChild(el("div", { className: "eval-refs mono", text: `subject ${ev.subject} · baseline ${ev.baseline || "—"}` }));

    if (model.infrastructureEntries.length) {
      panel.appendChild(el("div", { className: "eval-subtitle", text: "Port checks" }));
      panel.appendChild(el("div", { className: "table-scroll", children: [
        this.buildResultsTable(this.resultEntries(model.infrastructureEntries, { hasBaseline: model.hasBaseline }), { singlePort: false }),
      ] }));
    }

    const shown = R.filterServiceEntries(model.serviceEntries, filterType);
    if (model.serviceEntries.length === 0) {
      panel.appendChild(el("div", { className: "pairing-notice", text: "No service results in this evaluation" }));
    } else if (shown.visible.length === 0) {
      panel.appendChild(el("div", { className: "pairing-notice", text: `No ${filterType} services in this pairing` }));
    } else {
      const contextKeys = new Set(shown.visible.filter((v) => v.linkedContext).map((v) => v.entry.key));
      const entries = this.resultEntries(shown.visible.map((v) => v.entry), {
        all: model.serviceEntries, contextKeys, hasBaseline: model.hasBaseline,
      });
      panel.appendChild(el("div", { className: "table-scroll", children: [this.buildResultsTable(entries, { singlePort: false })] }));
    }

    const items = this.unmatchedItems(model);
    if (items.length) {
      panel.appendChild(el("div", { className: "eval-subtitle", text: `Nespárováno — ${items.length}` }));
      panel.appendChild(this.buildUnmatchedSection(items));
    }
    if (model.excludedCount) {
      panel.appendChild(el("div", { className: "eval-note", text: `${model.excludedCount} other services on ${group.new.port} outside this comparison` }));
    }
    return panel;
  }
```

- [ ] **Step 5: Auxiliary sections and unassigned by subject**

```js
  buildAuxiliarySection(title, models, opts) {
    const section = el("div", { className: "aux-section" });
    section.appendChild(el("div", { className: "subsection-title", text: title }));
    for (const m of models) {
      const rec = m.subjectRecord;
      const label = rec ? `${rec.device}:${rec.port || "all"} (${rec.phase})` : `${m.evaluation.subject} — metadata unavailable`;
      section.appendChild(el("div", { className: "eval-refs mono", text: `${label} · subject ${m.evaluation.subject} · baseline ${m.evaluation.baseline || "—"}` }));
      if (m.warning) section.appendChild(el("div", { className: "notice notice-warn", text: m.warning }));
      const entries = this.resultEntries([...m.infrastructureEntries, ...m.serviceEntries], { hasBaseline: m.hasBaseline });
      if (entries.length) {
        section.appendChild(el("div", { className: "table-scroll", children: [this.buildResultsTable(entries, { singlePort: !!opts.singlePort })] }));
      } else {
        section.appendChild(el("div", { className: "pairing-notice", text: "No service results in this evaluation" }));
      }
      const items = this.unmatchedItems(m);
      if (items.length) {
        section.appendChild(el("div", { className: "eval-subtitle", text: `Nespárováno — ${items.length}` }));
        section.appendChild(this.buildUnmatchedSection(items));
      }
    }
    return section;
  }

  buildUnassignedBySubject(found) {
    const section = el("div", { className: "aux-section" });
    const total = found.reduce((n, f) => n + f.variants.reduce((k, v) =>
      k + Object.values(v.payload || {}).reduce((c, list) => c + (Array.isArray(list) ? list.length : 0), 0), 0), 0);
    section.appendChild(el("div", { className: "subsection-title", text: `Unassigned — subject snapshot (Nezařazeno) — ${total}` }));
    if (found.length === 0) {
      section.appendChild(this.buildUnassignedSection({}));
      return section;
    }
    for (const f of found) {
      const rec = f.record;
      const label = rec ? `${rec.device}:${rec.port || "all"} (${rec.phase}) · ${f.subject}` : `${f.subject} — metadata unavailable`;
      f.variants.forEach((v, i) => {
        const suffix = f.variants.length > 1 ? ` · variant ${i + 1} (${v.labels.join(", ")})` : "";
        section.appendChild(el("div", { className: "eval-refs mono", text: label + suffix }));
        section.appendChild(this.buildUnassignedSection(v.payload));
      });
    }
    return section;
  }
```

- [ ] **Step 6: Load the catalogue for the run view**

In `loadRun()`, at the very top, add `this.loadCatalogue();` (fire-and-forget; cached after the first success, failure only leaves `cache.catalogue` null and the chips derive from data).

- [ ] **Step 7: Syntax check and JS suite**

Run: `node --check migration_validator/gui/static/app.js && node --test tests/js/*.test.js`
Expected: no syntax error, tests pass. (`buildServiceTypeFilter` is added in Task 6; until then the mapped view throws at runtime — do not start the browser check before Task 6.)

- [ ] **Step 8: Commit**

```bash
git add migration_validator/gui/static/app.js
git commit -m "feat(gui): run overview groups mapped results by port pairing"
```

---

## Task 6: app.js — service-type filter bar, profile restriction, export note

**Files:**
- Modify: `migration_validator/gui/static/app.js` (`exportJson` ~597; footer in `renderRunOverview`; new `buildServiceTypeFilter`)

**Interfaces:**
- Consumes: `MigRunResults.serviceTypeChoices`, `countByType`, `ALL_TYPES`; `this.state.serviceTypeFilter`; `setServiceTypeFilter`.
- Produces: `buildServiceTypeFilter(pairedEntries, catalogueTypes, mainModels) -> HTMLElement`.

- [ ] **Step 1: Filter bar**

Add after `buildUnassignedBySubject`:

```js
  buildServiceTypeFilter(pairedEntries, catalogueTypes, mainModels) {
    const R = MigRunResults;
    const selected = this.state.serviceTypeFilter;
    const choices = R.serviceTypeChoices(catalogueTypes, pairedEntries);
    const bar = el("div", { className: "type-filter", attrs: { role: "group", "aria-label": "Filter paired service results" } });
    bar.appendChild(el("span", { className: "type-filter-label", text: "Filter paired service results" }));
    const chips = el("div", { className: "type-chips" });
    const chip = (type, label, count, disabled) => {
      const pressed = selected === type;
      return el("button", {
        className: "type-chip" + (pressed ? " active" : ""),
        attrs: {
          type: "button",
          "aria-pressed": pressed ? "true" : "false",
          "data-focus-key": `chip:${type}`,
          ...(disabled && !pressed ? { disabled: "disabled" } : {}),
        },
        onClick: () => this.setServiceTypeFilter(type),
        children: [el("span", { text: label }), el("span", { className: "chip-count mono", text: String(count) })],
      });
    };
    chips.appendChild(chip(R.ALL_TYPES, "All types", pairedEntries.length, false));
    for (const c of choices) chips.appendChild(chip(c.type, c.type, c.count, c.count === 0));
    bar.appendChild(chips);
    bar.appendChild(el("span", { className: "type-filter-note", text: "Port checks, unmatched findings and capture state stay visible" }));

    const restricted = mainModels.map((m) => m.filtered).find((f) => f && Array.isArray(f.service_types));
    if (restricted) {
      const list = restricted.service_types.length ? restricted.service_types.join(", ") : "(none)";
      bar.appendChild(el("div", { className: "type-filter-profile", text:
        `Profile restricts evaluated service types to: ${list}. "All types" shows everything returned under that profile; change the profile to evaluate other types.` }));
    }
    return bar;
  }
```

- [ ] **Step 2: Export button tooltip**

In `renderRunOverview` footer, change the Export JSON button to:

```js
      el("button", {
        className: "btn btn-secondary",
        text: "Export JSON",
        attrs: { title: this.state.serviceTypeFilter !== MigRunResults.ALL_TYPES
          ? "exports the complete evaluation response — the type filter does not narrow it"
          : "exports the complete evaluation response" },
        onClick: () => this.exportJson(),
      }),
```

- [ ] **Step 3: Syntax check and JS suite**

Run: `node --check migration_validator/gui/static/app.js && node --test tests/js/*.test.js`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add migration_validator/gui/static/app.js
git commit -m "feat(gui): service-type filter chips over paired results"
```

---

## Task 7: CSS for pairing groups, badges, chips

**Files:**
- Modify: `migration_validator/gui/static/style.css` (append after the `.results-row .chevron.open` rule ~456)

- [ ] **Step 1: Append styles**

```css
/* Port pairing groups (mapped migration run overview) */
.section-head { display: flex; align-items: baseline; gap: 12px; }
.section-head .subsection-title { margin: 0; }
.section-note { font-size: 12px; color: #6b7280; }
.section-spacer { flex: 1; }
.counts-title { font: 600 11px 'IBM Plex Sans', sans-serif; letter-spacing: 0.06em; text-transform: uppercase; color: #6b7280; margin-right: 6px; }

.pairing-group { background: #fff; border: 1px solid #e2e5ea; border-radius: 8px; overflow: hidden; }
.pairing-head { display: flex; flex-direction: column; gap: 6px; padding: 12px 16px; }
.pairing-head-main, .pairing-head-side { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.pairing-head-side { justify-content: flex-end; }
.pairing-toggle {
  display: inline-flex; align-items: center; gap: 10px;
  border: 0; background: none; padding: 2px 4px; cursor: pointer;
  font: 600 14px 'IBM Plex Mono', monospace; color: #111827; border-radius: 4px;
}
.pairing-toggle:focus-visible { outline: 2px solid #1a56db; outline-offset: 2px; }
.pairing-toggle .chevron { color: #9ca3af; font-size: 10px; transition: transform 0.15s; }
.pairing-toggle .chevron.open { transform: rotate(90deg); }
.pairing-toggle .arrow { color: #9ca3af; font-weight: 400; }
.pairing-flags { display: inline-flex; align-items: center; gap: 4px 6px; font-size: 12px; color: #6b7280; flex-wrap: wrap; }
.pairing-flags .flag-cell { font-weight: 600; }
.pairing-flags .flag-cell.on { color: #059669; }
.pairing-flags .flag-cell.off { color: #c3c8cf; }
.pairing-flags .flag-label { margin-right: 8px; }
.pairing-badges, .pairing-notices { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.pair-count { font-size: 12px; color: #6b7280; }
.badge-pill {
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  font: 600 10.5px 'IBM Plex Mono', monospace; letter-spacing: 0.02em;
  background: #f3f4f6; color: #6b7280;
}
.badge-pill.pass { background: #d1fae5; color: #047857; }
.badge-pill.recv { background: #cffafe; color: #0e7490; }
.badge-pill.warn { background: #fef3c7; color: #b45309; }
.badge-pill.fail { background: #fee2e2; color: #b91c1c; }
.badge-pill.skip { background: #f3f4f6; color: #6b7280; }
.badge-pill.info { background: #dbeafe; color: #2563eb; }
.badge-pill.unmatched { background: #fef3c7; color: #b45309; }
.badge-pill.neutral { background: #f3f4f6; color: #6b7280; font-family: 'IBM Plex Sans', sans-serif; font-weight: 500; }

.pairing-body { border-top: 1px solid #e2e5ea; background: #fbfcfd; padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 12px; }
.eval-panel { display: flex; flex-direction: column; gap: 10px; }
.eval-subgroup-title { font: 600 12px 'IBM Plex Sans', sans-serif; color: #374151; margin-top: 6px; }
.eval-subtitle { font: 600 11px 'IBM Plex Sans', sans-serif; letter-spacing: 0.06em; text-transform: uppercase; color: #6b7280; }
.eval-refs { font-size: 11.5px; color: #6b7280; }
.eval-note { font-size: 12px; color: #6b7280; font-style: italic; }
.pairing-notice { font-size: 12.5px; color: #4b5563; padding: 10px 12px; background: #fff; border: 1px dashed #d1d5db; border-radius: 6px; }
.aux-section { display: flex; flex-direction: column; gap: 8px; }
.table-scroll { overflow-x: auto; }
.table-scroll .results-table { min-width: 860px; }
.ctx-tag { margin-left: 6px; padding: 1px 6px; border-radius: 4px; background: #eef2ff; color: #4338ca; font: 500 10.5px 'IBM Plex Sans', sans-serif; }
.results-row:focus-visible { outline: 2px solid #1a56db; outline-offset: -2px; }

/* Service type filter */
.type-filter { display: flex; flex-direction: column; gap: 8px; }
.type-filter-label { font: 600 11px 'IBM Plex Sans', sans-serif; letter-spacing: 0.06em; text-transform: uppercase; color: #6b7280; }
.type-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.type-chip {
  display: inline-flex; align-items: center; gap: 6px;
  border: 1px solid #d1d5db; background: #fff; color: #374151;
  border-radius: 6px; padding: 5px 10px; font: 500 12.5px 'IBM Plex Sans', sans-serif; cursor: pointer;
}
.type-chip .chip-count { color: #6b7280; font-size: 11px; }
.type-chip.active { border-color: #1a56db; color: #1a56db; background: #eef2ff; }
.type-chip:disabled { color: #9ca3af; cursor: default; background: #f9fafb; }
.type-chip:focus-visible { outline: 2px solid #1a56db; outline-offset: 2px; }
.type-filter-note, .type-filter-profile { font-size: 12px; color: #6b7280; }
.type-filter-profile { color: #b45309; }
```

- [ ] **Step 2: Commit**

```bash
git add migration_validator/gui/static/style.css
git commit -m "style(gui): pairing groups, status badges, type filter chips"
```

---

## Task 8: Route regression — shared destination with two per-port baselines

**Files:**
- Modify: `tests/gui/test_evaluation_routes.py`

- [ ] **Step 1: Write the failing-then-passing test**

Append:

```python
def _write_multi_snapshot(store, phase, node, port, address, services):
    """Snapshot s vice sluzbami: services = [(description, service_type, interface)]."""
    scopes = [
        Scope(
            id=f"svc:{desc}:{stype}",
            kind="service",
            key=ScopeKey(desc, stype, None),
            selectors=Selectors(interfaces=[iface]),
        )
        for desc, stype, iface in services
    ]
    facts = {
        "interfaces": {
            iface: {
                "admin_status": "up", "oper_status": "up",
                "input_pps": 400, "output_pps": 400,
                "input_errors": 0, "output_errors": 0,
            }
            for _, _, iface in services
        }
    }
    snapshot = Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(
            started_at=NOW, finished_at=NOW, phase=phase,
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts=facts, probes={"ping": []}, scopes=scopes, inventory=[],
    )
    path = store.snapshot_path(phase, node, port)
    save_snapshot(snapshot, path)
    return path


def test_run_evaluation_sdileny_cil_dve_baseline(tmp_path):
    """Dva stare porty na jeden LAG: kazdy krok dostane jen sve sluzby,
    cizi sluzba na sdilenem portu jde do excluded_services."""
    store = RunStore(tmp_path, "mig02")
    manifest = RunManifest(
        devices={
            "MX1": RunDevice(host="10.0.0.1", platform="junos", role="old"),
            "PTX1": RunDevice(host="10.0.0.2", platform="junos-evo", role="new"),
        },
        interface_mapping=[
            InterfaceMapping(
                old=MappingEndpoint(node="MX1", port="ge-0/0/4"),
                new=MappingEndpoint(node="PTX1", port="ae0"),
            ),
            InterfaceMapping(
                old=MappingEndpoint(node="MX1", port="ge-0/0/5"),
                new=MappingEndpoint(node="PTX1", port="ae0"),
            ),
        ],
    )
    pre4 = _write_multi_snapshot(store, "pre", "MX1", "ge-0/0/4", "10.0.0.1",
                                 [("A", "Internet", "ge-0/0/4.100")])
    pre5 = _write_multi_snapshot(store, "pre", "MX1", "ge-0/0/5", "10.0.0.1",
                                 [("B", "IPVPN", "ge-0/0/5.200")])
    post = _write_multi_snapshot(store, "post", "PTX1", "ae0", "10.0.0.2",
                                 [("A", "Internet", "ae0.100"), ("B", "IPVPN", "ae0.200")])
    manifest.record_capture(CaptureRecord("pre", "MX1", "ge-0/0/4", pre4.name, NOW))
    manifest.record_capture(CaptureRecord("pre", "MX1", "ge-0/0/5", pre5.name, NOW))
    manifest.record_capture(CaptureRecord("post", "PTX1", "ae0", post.name, NOW))
    store.save(manifest)

    client = TestClient(create_app(run_root=tmp_path))
    data = client.get("/api/runs/mig02/evaluation").json()
    steps = [ev for ev in data["evaluations"] if ev["step"] is not None]
    assert [ev["step"]["old"]["port"] for ev in steps] == ["ge-0/0/4", "ge-0/0/5"]
    assert all(ev["step"]["new"]["port"] == "ae0" for ev in steps)
    assert all(ev["subject"] == post.name for ev in steps)
    assert [ev["baseline"] for ev in steps] == [pre4.name, pre5.name]

    def services(ev):
        return sorted(
            s["identity"]["description"] for s in ev["result"]["scopes"]
            if s["scope_id"] != "device" and s["identity"].get("service_type") != "Layer1"
        )

    assert services(steps[0]) == ["A"]
    assert services(steps[1]) == ["B"]
    assert [x["description"] for x in steps[0]["result"]["excluded_services"]] == ["B"]
    assert [x["description"] for x in steps[1]["result"]["excluded_services"]] == ["A"]
    assert steps[0]["result"]["unmatched"]["subject"] == []
    assert not any(ev["same_device"] for ev in data["evaluations"])
```

- [ ] **Step 2: Run**

Run: `.venv/bin/python -m pytest tests/gui/test_evaluation_routes.py -q -p no:warnings -k sdileny`
Expected: PASS (this documents existing backend behaviour the GUI relies on). If it fails, the failure is a real finding about matching on a shared destination: report it, do not patch the engine in this plan.

- [ ] **Step 3: Full regression**

Run: `.venv/bin/python -m pytest -q -p no:warnings tests/gui tests/runs/test_pairing.py tests/test_engine.py && node --test tests/js/*.test.js`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/gui/test_evaluation_routes.py
git commit -m "test(gui): sdileny cilovy LAG se dvema per-port baseline vraci dva kroky"
```

---

## Task 9: Browser acceptance on a synthetic run

**Files:**
- Create (scratchpad, not committed): `<scratchpad>/synthetic_run.py`

- [ ] **Step 1: Synthetic run builder**

Write `<scratchpad>/synthetic_run.py` (run with `.venv/bin/python`), reusing the same models as the route tests, producing under `<scratchpad>/runs/`:

- run `pop1`, devices MX1 (old) / PTX1 (new), mappings: ge-0/0/4→ae0, ge-0/0/5→ae0, ge-0/0/6→et-0/0/8, ge-0/0/7→et-0/0/9.
- pre snapshots for ge-0/0/4 (services ATLAS Internet, ATLAS-VPN IPVPN, METRO E-Line), ge-0/0/5 (NORTH Internet, NORTH-VPN IPVPN with `oper="down"` on its post interface to force a FAIL, CAMPUS E-LAN, plus baseline-only NORTH-BACKUP IPVPN → unmatched baseline), ge-0/0/6 (TRANSIT Core, WEST Internet), ge-0/0/7 (pre only).
- post snapshots for ae0 (all six services) and et-0/0/8 (two services).
- run `pop2`: same as pop1 but with only a whole-device pre snapshot `MX1` port None (fallback case) and one rollback capture of MX1 ge-0/0/6.

Script skeleton:

```python
from pathlib import Path
import sys
sys.path.insert(0, ".")
from tests.gui.test_evaluation_routes import NOW  # noqa: E402
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot, save_snapshot
from migration_validator.runs.manifest import CaptureRecord, InterfaceMapping, MappingEndpoint, RunDevice, RunManifest
from migration_validator.runs.store import RunStore

ROOT = Path(sys.argv[1])

def write(store, phase, node, port, address, services, down=()):
    scopes = [Scope(id=f"svc:{d}:{t}", kind="service", key=ScopeKey(d, t, None),
                    selectors=Selectors(interfaces=[i])) for d, t, i in services]
    facts = {"interfaces": {i: {"admin_status": "up", "oper_status": "down" if i in down else "up",
             "input_pps": 400, "output_pps": 400, "input_errors": 0, "output_errors": 0}
             for _, _, i in services}}
    snap = Snapshot(device=DeviceMeta(address=address),
                    capture=CaptureMeta(started_at=NOW, finished_at=NOW, phase=phase,
                                        collectors={"interfaces": {"status": "ok"}}),
                    facts=facts, probes={"ping": []}, scopes=scopes, inventory=[])
    path = store.snapshot_path(phase, node, port)
    save_snapshot(snap, path)
    return path

def mapping(o, n):
    return InterfaceMapping(old=MappingEndpoint(node="MX1", port=o), new=MappingEndpoint(node="PTX1", port=n))

def build(name, whole_device=False, rollback=False):
    store = RunStore(ROOT, name)
    m = RunManifest(devices={"MX1": RunDevice(host="10.0.0.1", platform="junos", role="old"),
                             "PTX1": RunDevice(host="10.0.0.2", platform="junos-evo", role="new")},
                    interface_mapping=[mapping("ge-0/0/4", "ae0"), mapping("ge-0/0/5", "ae0"),
                                       mapping("ge-0/0/6", "et-0/0/8"), mapping("ge-0/0/7", "et-0/0/9")])
    ge4 = [("ATLAS", "Internet", "ge-0/0/4.113"), ("ATLAS-VPN", "IPVPN", "ge-0/0/4.210"), ("METRO", "E-Line", "ge-0/0/4.310")]
    ge5 = [("NORTH", "Internet", "ge-0/0/5.120"), ("NORTH-VPN", "IPVPN", "ge-0/0/5.220"), ("CAMPUS", "E-LAN", "ge-0/0/5.320"), ("NORTH-BACKUP", "IPVPN", "ge-0/0/5.221")]
    ge6 = [("TRANSIT", "Core", "ge-0/0/6.0"), ("WEST", "Internet", "ge-0/0/6.130")]
    ge7 = [("EAST", "Internet", "ge-0/0/7.140")]
    rec = lambda phase, node, port, path: m.record_capture(CaptureRecord(phase, node, port, path.name, NOW))
    if whole_device:
        rec("pre", "MX1", None, write(store, "pre", "MX1", None, "10.0.0.1", ge4 + ge5 + ge6 + ge7))
    else:
        for port, svcs in [("ge-0/0/4", ge4), ("ge-0/0/5", ge5), ("ge-0/0/6", ge6), ("ge-0/0/7", ge7)]:
            rec("pre", "MX1", port, write(store, "pre", "MX1", port, "10.0.0.1", svcs))
    ae0 = [(d, t, i.replace("ge-0/0/4", "ae0").replace("ge-0/0/5", "ae0")) for d, t, i in ge4 + ge5 if d != "NORTH-BACKUP"]
    rec("post", "PTX1", "ae0", write(store, "post", "PTX1", "ae0", "10.0.0.2", ae0, down=("ae0.220",)))
    et8 = [(d, t, i.replace("ge-0/0/6", "et-0/0/8")) for d, t, i in ge6]
    rec("post", "PTX1", "et-0/0/8", write(store, "post", "PTX1", "et-0/0/8", "10.0.0.2", et8))
    if rollback:
        rec("rollback", "MX1", "ge-0/0/6", write(store, "rollback", "MX1", "ge-0/0/6", "10.0.0.1", ge6))
    store.save(m)

build("pop1")
build("pop2", whole_device=True, rollback=True)
print("ok", ROOT)
```

Run: `.venv/bin/python <scratchpad>/synthetic_run.py <scratchpad>/runs`
Expected: `ok …/runs`. Check `RunStore` / `record_capture` signatures against `migration_validator/runs/store.py` and `manifest.py` if the script errors; the route-test fixture is the reference usage.

- [ ] **Step 2: Start the GUI on the synthetic root**

Run in background: `.venv/bin/mig-validate gui --run-root <scratchpad>/runs --host 127.0.0.1 --gui-port 8765` (check the exact port flag name with `.venv/bin/mig-validate gui --help`; it is `gui_port` in argparse, so the flag is likely `--gui-port` or `--port`).

- [ ] **Step 3: Walk the acceptance list**

Use the `claude-in-chrome` skill (or the `run` skill) to open `http://127.0.0.1:8765`, and verify, taking a screenshot for each:

1. `pop1`: four pairings in manifest order; ge-0/0/4 and ge-0/0/5 both show `Shared destination`; ge-0/0/5 shows FAIL badge and `1 unmatched`; ge-0/0/7 is collapsed with `Awaiting post`; ge-0/0/6 evaluated and expanded.
2. Expand NORTH-VPN in ge-0/0/5; ATLAS-VPN in ge-0/0/4 stays closed. Click `Evaluate run`; expansion and filter survive.
3. Select `IPVPN`: chip counts match (ge-0/0/4 shows `1 / 3 service results`, ge-0/0/6 shows `No IPVPN services in this pairing`, unmatched card still visible). Select `Core`: ge-0/0/4 and ge-0/0/5 show the empty message and a `Hidden WARN/FAIL` pill on ge-0/0/5. Select `All types`: everything back, no refetch needed (network tab shows none).
4. `Collapse all`, then `Expand all`; Escape/Tab/Enter/Space operate the toggles and chips; focus stays on the chip after clicking it.
5. `pop2`: every evaluated pairing shows `Whole-device baseline` with Pre flag `—`; a `Rollback — original device` section lists MX1:ge-0/0/6 (rollback); `Other captures` lists MX1:all.
6. Top strip reads `Full run (current profile)` and its counts do not change while filtering.
7. `Unassigned — subject snapshot` renders once per subject file (ae0 not duplicated).
8. Narrow the window to ~900px: tables scroll inside their container, the page has no horizontal scrollbar, port names are not clipped.
9. Smoke: open a snapshot from the sidebar, open Profiles, `Edit mapping`, `New capture` form and back — unchanged behaviour. Create a `single` run via `+ New run` if one is not present and confirm the flat rendering is untouched.

Record any deviation and fix it in the relevant file (app.js / style.css), re-running the JS suite. Stop the server afterwards.

- [ ] **Step 4: Commit fixes (if any)**

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "fix(gui): port pairing view - adjustments from browser acceptance"
```

---

## Task 10: Docs and guide text

**Files:**
- Modify: `migration_validator/gui/static/app.js` (`GUIDE_TEXT.run.body` ~58-66)
- Modify: `docs/` GUI reference if one describes the run overview (`grep -rl "Results —\|Nespárováno" docs/` to find it; if none, skip).

- [ ] **Step 1: Guide copy**

In `GUIDE_TEXT.run.body`, after the sentence starting `"Tenhle screen porovnává služby…"`, insert:

```js
      "Two-device run s mapováním: každé párování starý → nový port má vlastní blok s captures, port checky, službami a Nespárováno. Filtr typu služby jen zužuje zobrazení spárovaných výsledků — nic nepřepočítává; horní souhrn je vždy za celý run.",
```

- [ ] **Step 2: Docs**

If a docs page lists the run overview sections, add one paragraph describing `Port pairings`, the type filter (display-only), `Rollback — original device`, `Other evaluations` and `Whole-device baseline`. Mirror the Czech/English pairing used elsewhere in that doc.

- [ ] **Step 3: Final verification and commit**

Run: `.venv/bin/python -m pytest -q -p no:warnings && node --test tests/js/*.test.js`
Expected: full suite green.

```bash
git add -A migration_validator/gui/static/app.js docs
git commit -m "docs(gui): port pairing view in guide and reference"
```

Then hand over with `superpowers:finishing-a-development-branch`.
