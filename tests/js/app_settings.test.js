const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Same loading trick as app_permissions.test.js: app.js is a browser-only
// class file, not a CommonJS module, so pull App out of a vm sandbox. The
// sandbox's bare top-level `fetch` (app.js calls it unqualified, not via
// `window.fetch`) delegates to sandbox.window.fetch, which tests that need
// to mock a response can reassign per call.
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
  sandbox.fetch = (...args) => sandbox.window.fetch(...args);
  vm.createContext(sandbox);
  vm.runInContext(`${src}\nmodule.exports = App;`, sandbox, { filename: "app.js" });
  return { App: sandbox.module.exports, sandbox };
}

const { App, sandbox } = loadApp();

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

test("settingsCountLineText: not enabled wins over everything", () => {
  const fakeThis = {};
  const text = App.prototype.settingsCountLineText.call(fakeThis, {
    enabled: false, fileError: "boom", error: "boom", counts: { visible: 1, total: 2 },
  });
  assert.strictEqual(text, "Inventory not configured — set inventory.path in config/settings.yml");
});

test("settingsCountLineText: fileError alone is shown and survives a null current error", () => {
  const fakeThis = {};
  const text = App.prototype.settingsCountLineText.call(fakeThis, {
    enabled: true, fileError: "hostname_filter.yml: neplatny YAML", error: null, counts: { visible: 1, total: 2 },
  });
  assert.strictEqual(text, "hostname_filter.yml: neplatny YAML");
});

test("settingsCountLineText: fileError and a different current error both show", () => {
  const fakeThis = {};
  const text = App.prototype.settingsCountLineText.call(fakeThis, {
    enabled: true, fileError: "file broken", error: "inventory broken", counts: null,
  });
  assert.strictEqual(text, "file broken · inventory broken");
});

test("settingsCountLineText: identical fileError and current error dedupe to one line", () => {
  const fakeThis = {};
  const text = App.prototype.settingsCountLineText.call(fakeThis, {
    enabled: true, fileError: "same", error: "same", counts: null,
  });
  assert.strictEqual(text, "same");
});

test("settingsCountLineText: neither error -> counts, then blank", () => {
  const fakeThis = {};
  assert.strictEqual(
    App.prototype.settingsCountLineText.call(fakeThis, { enabled: true, fileError: null, error: null, counts: { visible: 3, total: 5 } }),
    "3 of 5 nodes visible"
  );
  assert.strictEqual(
    App.prototype.settingsCountLineText.call(fakeThis, { enabled: true, fileError: null, error: null, counts: null }),
    ""
  );
});

test("loadFilter: a broken filter file sets fileError, which a dry-run-shaped response cannot clear on its own", async () => {
  // finding #6: reproduces the bug via applyFilterResponse itself (dry-run
  // response has no file error) - fileError must stay untouched by it.
  const settings = { text: "", saved: [], counts: null, warnings: [], enabled: true, error: null, fileError: null, dirty: false };
  const fakeThis = {
    state: { settings },
    applyFilterResponse: App.prototype.applyFilterResponse,
  };
  sandbox.window.fetch = async () => ({ ok: true, json: async () => ({ allow: [], visible: 0, total: 0, warnings: [], enabled: true, error: "hostname_filter.yml: neplatny YAML" }) });
  await App.prototype.loadFilter.call(fakeThis);
  assert.strictEqual(settings.fileError, "hostname_filter.yml: neplatny YAML");

  // A dry-run response never carries a file error - applying it must not
  // touch fileError (only loadFilter/saveFilter are allowed to).
  App.prototype.applyFilterResponse.call(fakeThis, settings, { allow: ["MX-*"], visible: 1, total: 1, warnings: [], enabled: true, error: null }, false);
  assert.strictEqual(settings.fileError, "hostname_filter.yml: neplatny YAML", "dry-run must not clear the sticky file error");
});

test("saveFilter: a successful save clears fileError", async () => {
  const settings = { text: "MX-*", saved: [], counts: null, warnings: [], enabled: true, error: null, fileError: "was broken", saving: false, saveError: null, dirty: true };
  const fakeThis = {
    state: { settings },
    render() {},
    parseFilterLines: App.prototype.parseFilterLines,
    applyFilterResponse: App.prototype.applyFilterResponse,
  };
  sandbox.window.fetch = async () => ({ ok: true, json: async () => ({ allow: ["MX-*"], visible: 1, total: 1, warnings: [], enabled: true, error: null }) });
  await App.prototype.saveFilter.call(fakeThis);
  assert.strictEqual(settings.fileError, null);
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
