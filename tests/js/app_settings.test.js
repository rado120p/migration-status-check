const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Same loading trick as app_permissions.test.js: app.js is a browser-only
// class file, not a CommonJS module, so pull App out of a vm sandbox.
function loadApp() {
  const src = fs.readFileSync(
    path.join(__dirname, "../../migration_validator/gui/static/app.js"),
    "utf8"
  );
  const sandbox = {
    window: { fetch: async () => ({ status: 200 }), confirm: () => true },
    document: { addEventListener: () => {} },
    module: { exports: {} },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${src}\nmodule.exports = App;`, sandbox, { filename: "app.js" });
  return sandbox.module.exports;
}

const App = loadApp();

test("canAdmin: false for operator (view+operate only)", () => {
  const fakeThis = { me: { permissions: ["view", "operate"] } };
  assert.strictEqual(App.prototype.canAdmin.call(fakeThis), false);
});

test("canAdmin: true for admin (view+operate+admin)", () => {
  const fakeThis = { me: { permissions: ["view", "operate", "admin"] } };
  assert.strictEqual(App.prototype.canAdmin.call(fakeThis), true);
});

test("parseFilterLines: trims, drops blank/whitespace-only lines", () => {
  const fakeThis = {};
  // Cross-realm result from the vm sandbox: spread into a plain array of
  // this realm before deepStrictEqual, which also compares prototypes.
  const result = [...App.prototype.parseFilterLines.call(fakeThis, " MX-*\n\n  \nPTX-*\n")];
  assert.deepStrictEqual(result, ["MX-*", "PTX-*"]);
});

test("leaveGuard: a dirty settings filter is confirmed like a dirty profile (not skipped)", () => {
  // leaveGuard() resolves `window` from app.js's own module scope (the vm
  // sandbox's window, fixed to confirm() => true by loadApp), not
  // Node's global - so this checks the return value, not that Node's
  // window.confirm was invoked.
  const fakeThis = {
    state: { view: "settings", settings: { dirty: true }, profileEditor: null },
    editorDirty: () => false,
    settingsDirty: App.prototype.settingsDirty,
  };
  assert.strictEqual(App.prototype.leaveGuard.call(fakeThis), true);
});

test("leaveGuard: clean settings (not dirty) never prompts", () => {
  const fakeThis = {
    state: { view: "settings", settings: { dirty: false } },
    editorDirty: () => false,
    settingsDirty: App.prototype.settingsDirty,
  };
  assert.strictEqual(App.prototype.leaveGuard.call(fakeThis), true);
});

test("goToSettings: leaving a dirty profile editor is guarded, not just leaving Settings itself", async () => {
  const fakeThis = {
    state: { view: "profiles", settings: null },
    leaveGuard: () => false, // simulates "Discard changes?" -> Cancel
    render() {},
    loadFilter: async () => {},
  };
  await App.prototype.goToSettings.call(fakeThis);
  assert.strictEqual(fakeThis.state.view, "profiles", "navigation must be blocked when leaveGuard() refuses");
});

test("goToProfiles: leaving a dirty settings filter is guarded, not just leaving Profiles itself", async () => {
  const fakeThis = {
    state: { view: "settings", profileEditor: null },
    leaveGuard: () => false, // simulates "Discard changes?" -> Cancel
    render() {},
    loadProfiles: async () => {},
    loadCatalogue: async () => {},
    loadEditorDocument: async () => {},
    resetChecksForm() {},
    schedulePreview() {},
  };
  await App.prototype.goToProfiles.call(fakeThis, null);
  assert.strictEqual(fakeThis.state.view, "settings", "navigation must be blocked when leaveGuard() refuses");
});
