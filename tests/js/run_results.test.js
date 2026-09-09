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
  assert.strictEqual(R.groupNeedsAttention(infra, R.ALL_TYPES), false);
  const infraWarn = mk([{ ...L1("ae0"), status: "WARN" }, scope("A", "Internet")]);
  assert.strictEqual(R.groupNeedsAttention(infraWarn, R.ALL_TYPES), true);
  const unmatched = mk([scope("A", "Internet")], { baseline: [{ scope_id: "B", description: "B", service_type: "IPVPN", reason: "no match" }], subject: [] });
  assert.strictEqual(R.groupNeedsAttention(unmatched, R.ALL_TYPES), true);
  const pending = R.buildPairingGroups({ runName: "r", rows: [row("ge-0/0/4", "ae0")], evaluations: [], snapshots: [] }).groups[0];
  assert.strictEqual(R.groupNeedsAttention(pending, R.ALL_TYPES), false);
});

test("hiddenBadEntries: returns hidden WARN/FAIL entries, empty when unfiltered or pending", () => {
  const mk = (scopes) => {
    const ev = evaluation("post-ae0.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), scopes);
    return R.buildPairingGroups({ runName: "r", rows: [row("ge-0/0/4", "ae0")], evaluations: [ev], snapshots: SNAPSHOTS }).groups[0];
  };
  const group = mk([scope("A", "Internet"), scope("B", "IPVPN", { status: "FAIL" })]);
  const hidden = R.hiddenBadEntries(group, "Internet");
  assert.strictEqual(hidden.length, 1);
  assert.strictEqual(hidden[0].scope.scope_id, "B");
  assert.deepStrictEqual(R.hiddenBadEntries(group, R.ALL_TYPES), []);
  const pending = R.buildPairingGroups({ runName: "r", rows: [row("ge-0/0/4", "ae0")], evaluations: [], snapshots: [] }).groups[0];
  assert.deepStrictEqual(R.hiddenBadEntries(pending, R.ALL_TYPES), []);
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

test("filterAcrossModels: per-evaluation filtering, no cross-evaluation dedupe by scope id", () => {
  const modelA = { serviceEntries: entriesOf([scope("A", "Internet"), scope("B", "IPVPN")]) };
  const modelB = { serviceEntries: entriesOf([scope("A", "Internet"), scope("B", "IPVPN")]) };
  const summed = R.filterAcrossModels([modelA, modelB], "Internet");
  assert.strictEqual(summed.matchedCount, 2);
  assert.strictEqual(summed.visibleCount, 2);
  assert.strictEqual(summed.linkedContextCount, 0);
  assert.strictEqual(summed.statuses.length, 2);

  const [l3, l2] = linked("A", "B", "Internet", "IPVPN");
  const linkedModelA = { serviceEntries: entriesOf([l3, l2]) };
  const plainModelB = { serviceEntries: entriesOf([scope("A", "Internet"), scope("B", "IPVPN")]) };
  const withLink = R.filterAcrossModels([linkedModelA, plainModelB], "Internet");
  assert.strictEqual(withLink.linkedContextCount, 1);
});
