const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// app.js is a browser-only class file (window.fetch patch at module scope,
// `new App().boot()` wired to DOMContentLoaded) - it is not written as a
// CommonJS module like view.js/profile_diff.js/run_results.js. canOperate()
// is pure (reads only `this.me`), so we load the source into a minimal vm
// sandbox and pull the App class out via a synthetic module.exports, rather
// than restructure the production file just to make one method importable.
function loadApp() {
  const src = fs.readFileSync(
    path.join(__dirname, "../../migration_validator/gui/static/app.js"),
    "utf8"
  );
  const sandbox = {
    window: { fetch: async () => ({ status: 200 }) },
    document: { addEventListener: () => {} },
    module: { exports: {} },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${src}\nmodule.exports = App;`, sandbox, { filename: "app.js" });
  return sandbox.module.exports;
}

const App = loadApp();

test("canOperate: least privilege (false) before /api/me answers or when it fails", () => {
  // finding #9: loadMe() leaves this.me unset both before boot() finishes
  // and when /api/me returns non-OK; canOperate() must not treat that as
  // "allowed" - that contradicts the data-role=\"viewer\" CSS default.
  const fakeThis = { me: undefined };
  assert.strictEqual(App.prototype.canOperate.call(fakeThis), false);
});

test("canOperate: true once /api/me answers with the operate permission", () => {
  const fakeThis = { me: { permissions: ["view", "operate"] } };
  assert.strictEqual(App.prototype.canOperate.call(fakeThis), true);
});

test("canOperate: false once /api/me answers without operate (viewer role)", () => {
  const fakeThis = { me: { permissions: ["view"] } };
  assert.strictEqual(App.prototype.canOperate.call(fakeThis), false);
});
