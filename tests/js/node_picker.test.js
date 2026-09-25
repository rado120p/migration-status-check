const test = require("node:test");
const assert = require("node:assert");
const P = require("../../migration_validator/gui/static/node_picker.js");

test("pickerMode: manual when inventory unavailable or device.manual", () => {
  assert.strictEqual(P.pickerMode({}, false), "manual");
  assert.strictEqual(P.pickerMode({ manual: true }, true), "manual");
  assert.strictEqual(P.pickerMode({}, true), "pick");
});

test("applyPick / clearPick", () => {
  const d = { node: "", host: "", platform: "junos" };
  P.applyPick(d, { node: "MX-POP1", host: "10.0.0.1" });
  assert.deepStrictEqual(d, { node: "MX-POP1", host: "10.0.0.1", platform: "junos", picked: true, manual: false });
  P.clearPick(d);
  assert.strictEqual(d.node, ""); assert.strictEqual(d.host, ""); assert.strictEqual(d.picked, false);
  assert.strictEqual(d.platform, "junos");
});

test("setManual clears both ways", () => {
  const d = { node: "MX-POP1", host: "10.0.0.1", picked: true };
  P.setManual(d, true);
  assert.deepStrictEqual([d.node, d.host, d.picked, d.manual], ["", "", false, true]);
  d.node = "typed"; d.host = "1.1.1.1";
  P.setManual(d, false);
  assert.deepStrictEqual([d.node, d.host, d.manual], ["", "", false]);
});

test("moveHighlight wraps and handles empty", () => {
  assert.strictEqual(P.moveHighlight(-1, 1, 3), 0);
  assert.strictEqual(P.moveHighlight(2, 1, 3), 0);
  assert.strictEqual(P.moveHighlight(0, -1, 3), 2);
  assert.strictEqual(P.moveHighlight(1, 1, 0), -1);
});

test("labels", () => {
  assert.strictEqual(P.moreLabel(120, 50), "70 more — refine the search");
  assert.strictEqual(P.moreLabel(50, 50), null);
  assert.strictEqual(P.itemLabel({ node: "MX-POP1", host: "172.20.20.4" }), "MX-POP1 · 172.20.20.4");
});
