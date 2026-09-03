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

test("worstMessage: RECV scope with no worse status -> its message", () => {
  const scope = { status: "RECV", checks: [
    { status: "PASS", message: "fine" },
    { status: "RECV", message: "opet up/up" },
  ]};
  assert.strictEqual(MigView.worstMessage(scope), "opet up/up");
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
    { pass: 2, recv: 0, warn: 1, fail: 0, skip: 0, info: 1 });
});

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

test("countStatuses: unknown status ignored, known ones still counted, no stray key", () => {
  const counts = MigView.countStatuses(["PASS", "BOGUS", "WARN", "PASS"]);
  assert.deepStrictEqual(counts, { pass: 2, recv: 0, warn: 1, fail: 0, skip: 0, info: 0 });
  assert.strictEqual(Object.keys(counts).length, 6);
});

test("unassignedRow: bfd session -> interface + state detail", () => {
  assert.deepStrictEqual(
    MigView.unassignedRow("bfd_sessions",
      { peer: "10.1.2.2", interface: "et-0/0/8.13", state: "Up" }),
    { identity: "10.1.2.2", detail: "et-0/0/8.13   Up" });
});
