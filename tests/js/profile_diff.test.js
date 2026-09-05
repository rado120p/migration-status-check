const test = require("node:test");
const assert = require("node:assert");
const MigDiff = require("../../migration_validator/gui/static/profile_diff.js");

const CATALOGUE = {
  checks: [
    { id: "interface_state", title: "Stav rozhrani", group: "interfaces",
      default_severity: "critical", default_enabled: true, options: {} },
    { id: "interface_traffic", title: "Datovost rozhrani", group: "interfaces",
      default_severity: "advisory", default_enabled: true,
      options: { tolerance_percent: { type: "number", default: -60 },
                 require_nonzero: { type: "boolean", default: true } } },
    { id: "bgp_prefix_counts", title: "Pocty BGP prefixu", group: "bgp",
      default_severity: "advisory", default_enabled: true,
      options: { tolerance_percent: { type: "number", default: -10 } } },
    { id: "traffic_ceased", title: "Utichnuti", group: "interfaces",
      default_severity: "advisory", default_enabled: false,
      options: { max_residual_pps: { type: "number", default: 1 } } },
  ],
};

test("defaultRow: enabled/severity/options from the catalogue entry", () => {
  assert.deepStrictEqual(MigDiff.defaultRow(CATALOGUE.checks[1]), {
    enabled: true, severity: "advisory",
    options: { tolerance_percent: -60, require_nonzero: true }, extra: {},
  });
  assert.strictEqual(MigDiff.defaultRow(CATALOGUE.checks[3]).enabled, false);
});

test("checksForm: no overrides -> every check at defaults, nothing unknown", () => {
  const form = MigDiff.checksForm(CATALOGUE, {});
  assert.deepStrictEqual(Object.keys(form.checks), CATALOGUE.checks.map((c) => c.id));
  assert.deepStrictEqual(form.checks.interface_traffic, MigDiff.defaultRow(CATALOGUE.checks[1]));
  assert.deepStrictEqual(form.unknown, {});
});

test("checksForm: overrides land on the row; unknown check and unknown key are preserved", () => {
  const form = MigDiff.checksForm(CATALOGUE, {
    interface_traffic: { tolerance_percent: -40, severity: "critical", foo: "x" },
    interface_state: { enabled: false },
    old_check: { enabled: false },
  });
  assert.deepStrictEqual(form.checks.interface_traffic, {
    enabled: true, severity: "critical",
    options: { tolerance_percent: -40, require_nonzero: true }, extra: { foo: "x" },
  });
  assert.strictEqual(form.checks.interface_state.enabled, false);
  assert.deepStrictEqual(form.unknown, { old_check: { enabled: false } });
});

test("checksDocument: nothing differs -> {}", () => {
  const form = MigDiff.checksForm(CATALOGUE, {});
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {});
  assert.strictEqual(MigDiff.overrideCount(CATALOGUE, form), 0);
});

test("checksDocument: enabled written only when it differs from default_enabled", () => {
  const form = MigDiff.checksForm(CATALOGUE, {});
  form.checks.interface_state.enabled = false;
  form.checks.traffic_ceased.enabled = true;
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {
    interface_state: { enabled: false },
    traffic_ceased: { enabled: true },
  });
  form.checks.traffic_ceased.enabled = false;
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), { interface_state: { enabled: false } });
});

test("checksDocument: severity equal to default omitted, different one written", () => {
  const form = MigDiff.checksForm(CATALOGUE, {});
  form.checks.bgp_prefix_counts.severity = "advisory";
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {});
  form.checks.bgp_prefix_counts.severity = "critical";
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), { bgp_prefix_counts: { severity: "critical" } });
});

test("checksDocument: option equal to default omitted; emptied numeric field drops the key", () => {
  const form = MigDiff.checksForm(CATALOGUE, { interface_traffic: { tolerance_percent: -40 } });
  form.checks.interface_traffic.options.require_nonzero = true;
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), { interface_traffic: { tolerance_percent: -40 } });
  form.checks.interface_traffic.options.tolerance_percent = "";
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {});
  form.checks.interface_traffic.options.tolerance_percent = -60;
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {});
});

test("checksDocument: unknown check preserved until removed; unknown key on known check preserved", () => {
  const form = MigDiff.checksForm(CATALOGUE, {
    old_check: { enabled: false }, interface_traffic: { foo: "x" },
  });
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {
    old_check: { enabled: false }, interface_traffic: { foo: "x" },
  });
  assert.strictEqual(MigDiff.overrideCount(CATALOGUE, form), 2);
  delete form.unknown.old_check;
  delete form.checks.interface_traffic.extra.foo;
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {});
});

test("isOverride: enable, severity or any option differing marks the row", () => {
  const entry = CATALOGUE.checks[1];
  assert.strictEqual(MigDiff.isOverride(entry, MigDiff.defaultRow(entry)), false);
  assert.strictEqual(MigDiff.isOverride(entry, { ...MigDiff.defaultRow(entry), enabled: false }), true);
  assert.strictEqual(MigDiff.isOverride(entry, { ...MigDiff.defaultRow(entry), severity: "critical" }), true);
  const row = MigDiff.defaultRow(entry);
  row.options.tolerance_percent = -40;
  assert.strictEqual(MigDiff.isOverride(entry, row), true);
  row.options.tolerance_percent = "";
  assert.strictEqual(MigDiff.isOverride(entry, row), false);
  const extra = MigDiff.defaultRow(entry);
  extra.extra.foo = 1;
  assert.strictEqual(MigDiff.isOverride(entry, extra), true);
});

test("groupChecks: groups in order of first appearance, rows in catalogue order", () => {
  assert.deepStrictEqual(
    MigDiff.groupChecks(CATALOGUE).map((g) => [g.group, g.checks.map((c) => c.id)]),
    [
      ["interfaces", ["interface_state", "interface_traffic", "traffic_ceased"]],
      ["bgp", ["bgp_prefix_counts"]],
    ]
  );
  assert.deepStrictEqual(MigDiff.groupChecks({ checks: [] }), []);
});

test("errorTarget: check id and option key found in a loader message; longest id wins", () => {
  const msg = "profiles/p.yml: check 'interface_traffic': volba 'tolerance_percent' ocekava number, nalezeno str";
  assert.deepStrictEqual(MigDiff.errorTarget(msg, CATALOGUE), { checkId: "interface_traffic", key: "tolerance_percent" });
  assert.deepStrictEqual(
    MigDiff.errorTarget("x: check 'interface_state': volba 'enabled' ocekava boolean, nalezeno str", CATALOGUE),
    { checkId: "interface_state", key: null }
  );
  assert.strictEqual(MigDiff.errorTarget("profiles/p.yml: neznamy collector 'iface'", CATALOGUE), null);
  assert.strictEqual(MigDiff.errorTarget(null, CATALOGUE), null);
});

test("OPTION_HINTS covers the catalogue's numeric tolerances", () => {
  for (const key of ["tolerance_percent", "tolerance_db", "max_residual_pps", "count", "require_nonzero"]) {
    assert.strictEqual(typeof MigDiff.OPTION_HINTS[key], "string");
  }
  assert.deepStrictEqual(MigDiff.SEVERITIES, ["critical", "advisory"]);
});
