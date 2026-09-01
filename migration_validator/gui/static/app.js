"use strict";

/* mig-validate GUI - vanilla JS, fetch only, no framework, no build step. */

const STATUS_RANK = { PASS: 0, SKIP: 1, WARN: 2, FAIL: 3, INFO: -1 };
const PLATFORM_LABEL = { junos: "MX", "junos-evo": "ACX/EVO" };

function el(tag, opts) {
  const node = document.createElement(tag);
  opts = opts || {};
  if (opts.className) node.className = opts.className;
  if (opts.text !== undefined) node.textContent = opts.text;
  if (opts.html !== undefined) node.innerHTML = opts.html;
  if (opts.attrs) {
    for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
  }
  if (opts.style) Object.assign(node.style, opts.style);
  if (opts.onClick) node.addEventListener("click", opts.onClick);
  if (opts.children) opts.children.forEach((c) => c && node.appendChild(c));
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function statusClass(status) {
  return (status || "").toLowerCase();
}

/* Per-screen copy for the guide rail, keyed by this.state.view values. */
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
  newrun: {
    title: "New run",
    body: ["Založí run adresář: pojmenuj run a vyplň obě zařízení. Mapování portů můžeš doplnit i později přes Edit mapping."],
  },
  editmapping: {
    title: "Edit mapping",
    body: ["Páruje starý port s novým. Řádek s existujícím capture je zamčený — mapování, podle kterého už se sbíralo, se nemění."],
  },
};

class App {
  constructor() {
    this.state = {
      view: "run",
      run: null,
      selectedSnapshot: null,
      snapshotBaseline: null,
      openRows: {},
      openScopes: {},
      activeCaptureId: null,
      captureForm: null,
      captureSubmitError: null,
      captureSubmitting: false,
      newRunForm: null,
      editMappingForm: null,
    };
    this.cache = {
      runs: [],
      detail: null,
      detailError: null,
      evaluation: null,
      evaluationError: null,
      snapshotEval: null,
      snapshotEvalError: null,
      checks: null,
      checksError: null,
      captureDetail: null,
      captureDetailError: null,
      meta: null,
      metaError: null,
      captureProgress: null,
    };
    this.capturePollTimer = null;

    this.profileNameEl = document.getElementById("profile-name");
    this.sidebarRunsEl = document.getElementById("sidebar-runs");
    this.sidebarSnapshotsEl = document.getElementById("sidebar-snapshots");
    this.sidebarFooterEl = document.getElementById("sidebar-footer");
    this.mainEl = document.getElementById("main");
    this.guideEl = document.getElementById("guide");
    this.btnChecksEl = document.getElementById("btn-checks");

    this.btnChecksEl.addEventListener("click", () => this.goToChecks());
    document.getElementById("btn-new-capture").addEventListener("click", () => {
      this.openCaptureForm();
    });
    const newRunLink = document.getElementById("btn-new-run");
    if (newRunLink) {
      newRunLink.addEventListener("click", (e) => {
        e.preventDefault();
        this.openNewRunForm();
      });
    }
    window.addEventListener("beforeunload", () => this.stopCapturePolling());
  }

  async boot() {
    const { runs } = await (await fetch("/api/runs")).json();
    this.cache.runs = runs;
    if (runs.length === 0) {
      this.state.view = "empty";
    } else {
      this.state.run = runs[0].name;
      await this.loadRun();
    }
    this.loadMeta().then(() => this.updateProfileBadge());
    this.render();
  }

  updateProfileBadge() {
    if (!this.profileNameEl) return;
    const meta = this.cache.meta;
    this.profileNameEl.textContent = (meta && meta.profile) || "(default)";
  }

  async loadRun() {
    this.cache.detail = null;
    this.cache.detailError = null;
    try {
      const res = await fetch(`/api/runs/${this.state.run}`);
      if (res.ok) {
        this.cache.detail = await res.json();
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.detailError = {
          status: res.status,
          detail: body.detail || `run se nepodarilo nacist (${res.status})`,
        };
      }
    } catch (err) {
      this.cache.detailError = { status: 0, detail: String(err) };
    }
    this.cache.evaluation = null;
    this.cache.evaluationError = null;
    try {
      const res = await fetch(`/api/runs/${this.state.run}/evaluation`);
      if (res.ok) {
        this.cache.evaluation = await res.json();
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.evaluationError = {
          status: res.status,
          detail: body.detail || `evaluation selhala (${res.status})`,
        };
      }
    } catch (err) {
      this.cache.evaluationError = { status: 0, detail: String(err) };
    }
  }

  async evaluateRun() {
    await this.loadRun();
    this.render();
  }

  exportJson() {
    if (!this.cache.evaluation) return;
    const blob = new Blob([JSON.stringify(this.cache.evaluation, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${this.state.run}-evaluation.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // -- live capture progress polling --------------------------------------

  syncCapturePolling() {
    const shouldPoll = this.state.view === "run" && !!this.state.activeCaptureId;
    if (shouldPoll && !this.capturePollTimer) {
      this.capturePollTimer = setInterval(() => this.pollCapture(), 2000);
      this.pollCapture();
    } else if (!shouldPoll && this.capturePollTimer) {
      this.stopCapturePolling();
    }
  }

  stopCapturePolling() {
    if (this.capturePollTimer) {
      clearInterval(this.capturePollTimer);
      this.capturePollTimer = null;
    }
  }

  async pollCapture() {
    const id = this.state.activeCaptureId;
    if (!id) {
      this.stopCapturePolling();
      return;
    }
    let task;
    try {
      const res = await fetch(`/api/captures/${id}`);
      if (!res.ok) {
        this.state.activeCaptureId = null;
        this.stopCapturePolling();
        this.render();
        return;
      }
      task = await res.json();
    } catch (err) {
      // network hiccup - keep the interval running and retry next tick.
      return;
    }
    this.cache.captureProgress = task;
    if (task.state === "done") {
      this.state.activeCaptureId = null;
      this.stopCapturePolling();
      // Keep the task around (with its failed_collectors/warnings) so a
      // WARN note can render on the matching row/notice - cleared on the
      // next view change (new capture form, new capture started, etc).
      if (!this.hasCaptureIssues(task)) {
        this.cache.captureProgress = null;
      }
      await this.loadRun();
      this.render();
      return;
    }
    if (task.state === "failed") {
      this.state.activeCaptureId = null;
      this.stopCapturePolling();
      this.render();
      return;
    }
    this.render();
  }

  rowMatchesCapture(row, task) {
    if (!task) return false;
    if (task.run !== this.state.run) return false;
    for (const endpoint of [row.old, row.new]) {
      if (!endpoint) continue;
      if (endpoint.node === task.device && (endpoint.port ?? null) === (task.port ?? null)) {
        return true;
      }
    }
    return false;
  }

  // Single-capture guard: the GUI tracks exactly one in-flight capture id at
  // a time (deliberately - no multi-id tracker). While it is still running,
  // starting a second one would silently orphan the first one's polling, so
  // the capture form disables submission and explains why.
  activeCaptureGuardTask() {
    const task = this.cache.captureProgress;
    if (this.state.activeCaptureId && task && task.state === "running") {
      return task;
    }
    return null;
  }

  buildCaptureStepsLine(task) {
    const parts = [];
    const steps = task.steps || [];
    steps.forEach((s, i) => {
      if (i > 0) parts.push(el("span", { text: " · " }));
      let cls = "step-ok";
      let label = `${s.collector} ✓`;
      if (s.status === "running") {
        cls = "step-running";
        label = `${s.collector}…`;
      } else if (s.status === "error") {
        cls = "step-error";
        label = `${s.collector} ✗`;
      }
      parts.push(el("span", { className: cls, text: label }));
    });
    return parts;
  }

  hasCaptureIssues(task) {
    if (!task) return false;
    const failed = task.failed_collectors || {};
    const warnings = task.warnings || [];
    return Object.keys(failed).length > 0 || warnings.length > 0;
  }

  buildCaptureIssueLines(task) {
    const lines = [];
    const failed = task.failed_collectors || {};
    for (const [name, message] of Object.entries(failed)) {
      lines.push(`collector ${name} failed: ${message}`);
    }
    for (const warning of task.warnings || []) lines.push(warning);
    const parts = [];
    lines.forEach((line, i) => {
      if (i > 0) parts.push(el("span", { text: " · " }));
      parts.push(el("span", { text: line }));
    });
    return parts;
  }

  toggleRow(key) {
    this.state.openRows[key] = !this.state.openRows[key];
    this.render();
  }

  toggleScope(key) {
    this.state.openScopes[key] = !this.state.openScopes[key];
    this.render();
  }

  async selectRun(name) {
    if (this.state.run === name) return;
    this.state.run = name;
    this.state.view = "run";
    this.state.selectedSnapshot = null;
    this.state.openRows = {};
    this.state.openScopes = {};
    // Capture tracking is global state (controller ruling: one in-flight
    // capture at a time, tracked across the whole app) - a run switch must
    // not orphan it. activeCaptureId/captureProgress/polling survive; the
    // progress UI itself gates on task.run === this.state.run so it only
    // renders while viewing the run the capture belongs to.
    await this.loadRun();
    this.render();
  }

  async selectSnapshot(file) {
    this.state.view = "snapshot";
    this.state.selectedSnapshot = file;
    this.state.snapshotBaseline = null;
    this.state.openScopes = {};
    await this.loadSnapshotEvaluation(file);
    this.render();
  }

  async selectSnapshotBaseline(file) {
    this.state.snapshotBaseline = file || null;
    await this.loadSnapshotEvaluation(this.state.selectedSnapshot);
    this.render();
  }

  async loadSnapshotEvaluation(file) {
    this.cache.snapshotEval = null;
    this.cache.snapshotEvalError = null;
    const baseline = this.state.snapshotBaseline;
    const query = baseline
      ? `?baseline=${encodeURIComponent(baseline)}`
      : "";
    try {
      const res = await fetch(
        `/api/runs/${this.state.run}/snapshots/${encodeURIComponent(file)}/evaluation${query}`
      );
      if (res.ok) {
        this.cache.snapshotEval = await res.json();
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.snapshotEvalError = {
          status: res.status,
          detail: body.detail || `snapshot evaluation selhala (${res.status})`,
        };
      }
    } catch (err) {
      this.cache.snapshotEvalError = { status: 0, detail: String(err) };
    }
  }

  async goToChecks() {
    this.state.view = "checks";
    this.state.selectedSnapshot = null;
    await this.loadChecks();
    this.render();
  }

  async loadChecks() {
    this.cache.checks = null;
    this.cache.checksError = null;
    try {
      const res = await fetch("/api/checks");
      if (res.ok) {
        this.cache.checks = await res.json();
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.checksError = {
          status: res.status,
          detail: body.detail || `checks se nepodarilo nacist (${res.status})`,
        };
      }
    } catch (err) {
      this.cache.checksError = { status: 0, detail: String(err) };
    }
  }

  backToRun() {
    this.state.view = "run";
    this.state.selectedSnapshot = null;
    this.render();
  }

  // -- new capture form (screen 4) ---------------------------------------

  async openCaptureForm() {
    const run = this.state.run || (this.cache.runs[0] && this.cache.runs[0].name) || null;
    this.state.view = "capture";
    this.state.selectedSnapshot = null;
    this.state.captureSubmitError = null;
    this.cache.captureProgress = null;
    this.state.captureForm = {
      run,
      device: null,
      phase: "pre",
      port: null,
      portText: "",
      parseServices: false,
    };
    await this.loadCaptureFormData(run);
    this.presetCaptureFormDevice();
    if (!this.cache.meta && !this.cache.metaError) await this.loadMeta();
    this.updateProfileBadge();
    this.render();
  }

  async loadCaptureFormData(run) {
    this.cache.captureDetail = null;
    this.cache.captureDetailError = null;
    if (!run) return;
    try {
      const res = await fetch(`/api/runs/${run}`);
      if (res.ok) {
        this.cache.captureDetail = await res.json();
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.captureDetailError = {
          status: res.status,
          detail: body.detail || `run se nepodarilo nacist (${res.status})`,
        };
      }
    } catch (err) {
      this.cache.captureDetailError = { status: 0, detail: String(err) };
    }
  }

  async loadMeta() {
    this.cache.meta = null;
    this.cache.metaError = null;
    try {
      const res = await fetch("/api/meta");
      if (res.ok) {
        this.cache.meta = await res.json();
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.metaError = {
          status: res.status,
          detail: body.detail || `meta se nepodarilo nacist (${res.status})`,
        };
      }
    } catch (err) {
      this.cache.metaError = { status: 0, detail: String(err) };
    }
  }

  presetCaptureFormDevice() {
    const detail = this.cache.captureDetail;
    if (!detail) return;
    const devices = detail.devices || {};
    const oldEntry = Object.entries(devices).find(([, d]) => d.role === "old");
    const firstEntry = oldEntry || Object.entries(devices)[0];
    if (!firstEntry) return;
    const [node, d] = firstEntry;
    this.state.captureForm.device = node;
    this.state.captureForm.phase = d.role === "new" ? "post" : "pre";
    this.state.captureForm.port = null;
    this.state.captureForm.portText = "";
  }

  async selectCaptureFormRun(run) {
    this.state.captureForm.run = run;
    this.state.captureForm.device = null;
    this.state.captureSubmitError = null;
    await this.loadCaptureFormData(run);
    this.presetCaptureFormDevice();
    this.render();
  }

  selectCaptureFormDevice(node) {
    const detail = this.cache.captureDetail;
    const device = detail && detail.devices[node];
    this.state.captureForm.device = node;
    this.state.captureForm.phase = device && device.role === "new" ? "post" : "pre";
    this.state.captureForm.port = null;
    this.state.captureForm.portText = "";
    this.state.captureSubmitError = null;
    this.render();
  }

  selectCaptureFormPhase(phase) {
    this.state.captureForm.phase = phase;
    this.state.captureSubmitError = null;
    this.render();
  }

  selectCaptureFormPort(value) {
    this.state.captureForm.port = value === "" ? null : value;
    this.state.captureSubmitError = null;
    this.render();
  }

  setCaptureFormPortText(value) {
    this.state.captureForm.portText = value;
  }

  commitCaptureFormPortText() {
    this.state.captureSubmitError = null;
    this.render();
  }

  toggleCaptureFormParseServices() {
    this.state.captureForm.parseServices = !this.state.captureForm.parseServices;
    this.render();
  }

  captureMappedRows() {
    const detail = this.cache.captureDetail;
    if (!detail) return [];
    return (detail.rows || []).filter((r) => r.old && r.new);
  }

  captureFindOldDeviceNode() {
    const detail = this.cache.captureDetail;
    if (!detail) return null;
    const entry = Object.entries(detail.devices || {}).find(([, d]) => d.role === "old");
    return entry ? entry[0] : null;
  }

  capturePortOptions(device) {
    const detail = this.cache.captureDetail;
    if (!detail || !device) return [];
    const role = detail.devices[device] ? detail.devices[device].role : null;
    const mapped = this.captureMappedRows();
    const ports = [];
    for (const row of mapped) {
      if (role === "new") {
        if (row.new.node === device && !ports.includes(row.new.port)) ports.push(row.new.port);
      } else if (row.old.node === device && !ports.includes(row.old.port)) {
        ports.push(row.old.port);
      }
    }
    return ports;
  }

  captureAlreadyCaptured(device, port, phase) {
    const detail = this.cache.captureDetail;
    if (!detail) return false;
    const rows = detail.rows || [];
    if (phase === "post") {
      const row = rows.find((r) => r.new && r.new.node === device && r.new.port === port);
      return row ? !!row.post : false;
    }
    const row = rows.find((r) => r.old && r.old.node === device && r.old.port === port);
    return row ? !!row[phase] : false;
  }

  captureMapsToText(device, port) {
    if (port === null) return null;
    const mapped = this.captureMappedRows();
    const matches = mapped.filter(
      (r) =>
        (r.old.node === device && r.old.port === port) ||
        (r.new.node === device && r.new.port === port)
    );
    if (!matches.length) return null;
    const newNode = matches[0].new.node;
    const newPort = matches[0].new.port;
    const siblings = mapped.filter((r) => r.new.node === newNode && r.new.port === newPort);
    const oldPorts = siblings.map((r) => r.old.port);
    return `${oldPorts.join(", ")} → ${newPort}`;
  }

  captureFindSnapshotFile(node, port) {
    const detail = this.cache.captureDetail;
    if (!detail) return null;
    const rec = (detail.snapshots || []).find(
      (s) => s.phase === "pre" && s.device === node && s.port === port
    );
    return rec ? rec.file : null;
  }

  captureBaselineNotice() {
    const form = this.state.captureForm;
    if (form.phase !== "post") return null;
    const detail = this.cache.captureDetail;
    if (!detail) return null;
    const oldNode = this.captureFindOldDeviceNode();
    if (!oldNode) return null;
    const rows = detail.rows || [];
    const mapped = this.captureMappedRows();

    if (form.port !== null) {
      const matches = mapped.filter(
        (r) =>
          (r.old.node === form.device && r.old.port === form.port) ||
          (r.new.node === form.device && r.new.port === form.port)
      );
      if (matches.length) {
        const withPre = matches.find((m) => m.pre) || matches[0];
        const oldPort = withPre.old.port;
        if (withPre.pre) {
          const file = this.captureFindSnapshotFile(oldNode, oldPort);
          return {
            kind: "indigo",
            text: file
              ? `Baseline ${file} found for this pairing.`
              : `Baseline found for this pairing.`,
          };
        }
        return {
          kind: "warn",
          text: `no pre baseline recorded for ${oldPort} — comparison checks will SKIP`,
        };
      }
    }

    // whole-device fallback: no port match (mapping-less run, or "all" selected)
    const wholeRow = rows.find((r) => r.old && r.old.node === oldNode && r.old.port === null);
    if (wholeRow && wholeRow.pre) {
      const file = this.captureFindSnapshotFile(oldNode, null);
      return {
        kind: "indigo",
        text: file
          ? `Baseline ${file} found for this pairing.`
          : `Baseline found for this pairing.`,
      };
    }
    return {
      kind: "warn",
      text: `no pre baseline recorded for ${oldNode}:all — comparison checks will SKIP`,
    };
  }

  async backToRunFromCapture(targetRun) {
    if (targetRun && targetRun !== this.state.run) {
      this.state.run = targetRun;
      await this.loadRun();
    }
    this.state.view = "run";
    this.state.selectedSnapshot = null;
    this.render();
  }

  async startCapture() {
    const form = this.state.captureForm;
    if (!form.run || !form.device) return;
    if (this.activeCaptureGuardTask()) return;
    const isMapped = this.captureMappedRows().length > 0;
    let port;
    if (isMapped) {
      port = form.port;
    } else {
      const raw = (form.portText || "").trim();
      port = !raw || raw.toLowerCase() === "all" ? null : raw;
    }
    this.state.captureSubmitError = null;
    this.state.captureSubmitting = true;
    this.render();
    try {
      const res = await fetch("/api/captures", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run: form.run,
          device: form.device,
          port,
          phase: form.phase,
          parse_services: !!form.parseServices,
        }),
      });
      if (res.ok) {
        const body = await res.json();
        this.state.activeCaptureId = body.id;
        this.cache.captureProgress = null;
        this.state.captureSubmitting = false;
        await this.backToRunFromCapture(form.run);
        return;
      }
      const body = await res.json().catch(() => ({}));
      this.state.captureSubmitError = body.detail || `capture se nepodarilo spustit (${res.status})`;
    } catch (err) {
      this.state.captureSubmitError = String(err);
    }
    this.state.captureSubmitting = false;
    this.render();
  }

  exportSnapshotJson() {
    if (!this.cache.snapshotEval) return;
    const blob = new Blob([JSON.stringify(this.cache.snapshotEval, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const snapBase = this.state.selectedSnapshot.replace(/\.json$/, "");
    a.download = `${this.state.run}-${snapBase}-evaluation.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  buildBreadcrumb(currentLabel, currentMono) {
    return el("div", {
      className: "breadcrumb",
      children: [
        el("span", {
          className: "crumb-link",
          text: `run ${this.state.run}`,
          onClick: () => this.backToRun(),
        }),
        el("span", { className: "crumb-sep", text: "/" }),
        el("span", {
          className: "crumb-current" + (currentMono ? " mono" : ""),
          text: currentLabel,
        }),
      ],
    });
  }

  render() {
    this.renderGuide();
    this.renderSidebar();
    this.btnChecksEl.classList.toggle("btn-toggle-active", this.state.view === "checks");
    switch (this.state.view) {
      case "empty":
        this.renderEmptyState();
        break;
      case "snapshot":
        this.renderSnapshotView();
        break;
      case "checks":
        this.renderChecksView();
        break;
      case "capture":
        this.renderCaptureForm();
        break;
      case "newrun":
        this.renderNewRunForm();
        break;
      case "editmapping":
        this.renderEditMapping();
        break;
      default:
        this.renderRunOverview();
        break;
    }
    this.syncCapturePolling();
  }

  renderGuide() {
    clear(this.guideEl);
    const entry = GUIDE_TEXT[this.state.view] || GUIDE_TEXT.run;
    this.guideEl.appendChild(el("h4", { text: "Guide — " + entry.title }));
    for (const paragraph of entry.body) {
      this.guideEl.appendChild(el("p", { text: paragraph }));
    }
  }

  renderEmptyState() {
    clear(this.mainEl);
    this.mainEl.appendChild(
      el("div", {
        className: "empty-state",
        children: [
          el("span", { text: "No runs yet — create one to get started." }),
          el("button", {
            className: "btn btn-primary",
            text: "Create your first run",
            onClick: () => this.openNewRunForm(),
          }),
        ],
      })
    );
  }

  renderSidebar() {
    clear(this.sidebarRunsEl);
    if (this.cache.runs.length === 0) {
      this.sidebarRunsEl.appendChild(el("div", { className: "sidebar-empty", text: "no runs yet" }));
    }
    for (const run of this.cache.runs) {
      const active = run.name === this.state.run;
      this.sidebarRunsEl.appendChild(
        el("div", {
          className: "run-card" + (active ? " active" : ""),
          onClick: () => this.selectRun(run.name),
          children: [
            el("div", {
              className: "run-card-top",
              children: [
                el("span", {
                  className: "run-card-name" + (active ? "" : " inactive"),
                  text: run.name,
                }),
              ],
            }),
            el("span", {
              className: "run-card-sub" + (active ? "" : " inactive"),
              text: `${run.snapshots} snapshots · ${run.mapped_ports} mapped ports`,
            }),
          ],
        })
      );
    }

    clear(this.sidebarSnapshotsEl);
    const snapshots = this.cache.detail ? this.cache.detail.snapshots : [];
    for (const snap of snapshots) {
      const active = snap.file === this.state.selectedSnapshot;
      const label = `${snap.phase} ${snap.device}:${snap.port || "all"}`;
      this.sidebarSnapshotsEl.appendChild(
        el("div", {
          className: "snap-row" + (active ? " active" : ""),
          onClick: () => this.selectSnapshot(snap.file),
          children: [
            el("div", {
              className: "snap-row-top",
              children: [
                el("span", { className: "snap-row-label mono", text: label }),
                el("span", {
                  className: "phase-badge phase-" + snap.phase,
                  text: snap.phase,
                }),
              ],
            }),
            el("span", { className: "snap-row-taken", text: snap.taken || "" }),
          ],
        })
      );
    }

    clear(this.sidebarFooterEl);
    if (this.cache.detail) {
      const deviceCount = Object.keys(this.cache.detail.devices).length;
      const snapCount = this.cache.detail.snapshots.length;
      this.sidebarFooterEl.appendChild(
        el("div", {
          className: "sidebar-footer-row",
          children: [
            el("span", { text: "Devices" }),
            el("span", { className: "value", text: String(deviceCount) }),
          ],
        })
      );
      this.sidebarFooterEl.appendChild(
        el("div", {
          className: "sidebar-footer-row",
          children: [
            el("span", { text: "Snapshots" }),
            el("span", { className: "value", text: String(snapCount) }),
          ],
        })
      );
    }
  }

  // -- run overview (screen 1) ------------------------------------------

  portsEqual(a, b) {
    if (!a && !b) return true;
    if (!a || !b) return false;
    return a.node === b.node && a.port === b.port;
  }

  findSnapshotRecord(file) {
    const detail = this.cache.detail;
    if (!detail || !file) return null;
    return (detail.snapshots || []).find((s) => s.file === file) || null;
  }

  findEvaluation(row) {
    const evaluations = this.cache.evaluation ? this.cache.evaluation.evaluations : [];
    // Step matches take priority - they are the exact mapped-post pairing.
    // Only fall back to subject-snapshot matching (whole-device,
    // mapping-less, rollback evaluations - all step=null) when no step
    // eval claimed this row, so a rollback eval can never displace the
    // post eval a mapped row already had.
    const stepMatch = evaluations.find(
      (ev) =>
        ev.step && this.portsEqual(ev.step.old, row.old) && this.portsEqual(ev.step.new, row.new)
    );
    if (stepMatch) return stepMatch;
    return evaluations.find((ev) => {
      // same-device evaluace maji vlastni sekci - nesmi obsadit radek tabulky
      if (ev.step || ev.same_device) return false;
      const record = this.findSnapshotRecord(ev.subject);
      if (!record) return false;
      const endpoint = { node: record.device, port: record.port };
      return this.portsEqual(endpoint, row.old) || this.portsEqual(endpoint, row.new);
    });
  }

  rowKey(row) {
    const old = row.old ? `${row.old.node}:${row.old.port}` : "x";
    const nw = row.new ? `${row.new.node}:${row.new.port}` : "x";
    return `${old}->${nw}`;
  }

  worstFromSummary(summary) {
    if (summary.fail > 0) return "FAIL";
    if (summary.warn > 0) return "WARN";
    if (summary.skip > 0 && summary.pass === 0) return "SKIP";
    return "PASS";
  }

  rowPresentation(evaluation) {
    const result = evaluation.result;
    const scopes = result.scopes || [];
    if (scopes.length === 0) {
      const worst = this.worstFromSummary(result.summary);
      return { worst, count: null, scopes: [], clickable: false };
    }
    let worst = "PASS";
    for (const scope of scopes) {
      if (STATUS_RANK[scope.status] > STATUS_RANK[worst]) worst = scope.status;
    }
    const count = scopes.filter((s) => s.status === worst).length;
    return { worst, count, scopes, clickable: true };
  }

  pillText(worst, count) {
    if (worst === "PASS" || worst === "SKIP" || count === null) return worst;
    return `${worst} ${count}`;
  }

  rowTintClass(worst) {
    if (worst === "WARN") return "tint-warn";
    if (worst === "FAIL") return "tint-fail";
    return "";
  }

  scopeNote(scope) {
    const checks = scope.checks || [];
    if (scope.status === "PASS") {
      return `${checks.length} checks passed`;
    }
    const bad = checks.find((c) => c.status === scope.status) || checks[0];
    return bad ? bad.message : "";
  }

  computeSummaryTotals() {
    const totals = { pass: 0, warn: 0, fail: 0, skip: 0, info: 0 };
    const evaluations = this.cache.evaluation ? this.cache.evaluation.evaluations : [];
    for (const ev of evaluations) {
      const s = ev.result.summary || {};
      for (const key of Object.keys(totals)) totals[key] += s[key] || 0;
    }
    return totals;
  }

  renderRunOverview() {
    clear(this.mainEl);
    const detail = this.cache.detail;
    if (!detail) {
      if (this.cache.detailError) {
        this.mainEl.appendChild(
          el("div", {
            className: "notice notice-warn",
            text: this.cache.detailError.detail,
          })
        );
      }
      return;
    }

    const devices = detail.devices || {};
    let oldDevice = null;
    let newDevice = null;
    for (const [node, d] of Object.entries(devices)) {
      if (d.role === "old") oldDevice = { node, ...d };
      else if (d.role === "new") newDevice = { node, ...d };
    }

    const header = el("div", {
      className: "run-header",
      children: [
        el("h1", {
          children: [
            document.createTextNode("Run "),
            el("span", { className: "mono", text: detail.name }),
          ],
        }),
      ],
    });
    if (oldDevice && newDevice) {
      const oldLabel = PLATFORM_LABEL[oldDevice.platform] || oldDevice.platform;
      const newLabel = PLATFORM_LABEL[newDevice.platform] || newDevice.platform;
      header.appendChild(
        el("span", {
          className: "subtitle",
          text: `${oldDevice.host} (${oldLabel}) → ${newDevice.host} (${newLabel})`,
        })
      );
    }
    this.mainEl.appendChild(header);

    if (this.cache.evaluationError) {
      this.mainEl.appendChild(
        el("div", {
          className: "notice notice-warn",
          text: this.cache.evaluationError.detail,
        })
      );
    }

    const totals = this.computeSummaryTotals();
    const cardsDef = [
      ["pass", "PASS", "#059669"],
      ["warn", "WARN", "#d97706"],
      ["fail", "FAIL", "#dc2626"],
      ["skip", "SKIP", "#6b7280"],
      ["info", "INFO", "#2563eb"],
    ];
    const cardsRow = el("div", { className: "summary-cards" });
    for (const [key, label, color] of cardsDef) {
      const count = totals[key];
      const card = el("div", {
        className: "summary-card" + (key === "fail" && count > 0 ? " fail-nonzero" : ""),
        children: [
          el("div", { className: "summary-count", text: String(count), style: { color } }),
          el("div", { className: "summary-label", text: label }),
        ],
      });
      cardsRow.appendChild(card);
    }
    this.mainEl.appendChild(cardsRow);

    const allRows = detail.rows || [];
    const mappingRows = allRows.filter((r) => r.old && r.new);
    const wholeRows = allRows.filter((r) => !r.old || !r.new);
    if (mappingRows.length === 0) {
      this.mainEl.appendChild(
        el("div", {
          className: "notice notice-warn",
          text: "no port mapping — captures are per-device",
        })
      );
      if (wholeRows.length > 0) {
        this.mainEl.appendChild(this.buildPairingTable(wholeRows));
      }
    } else {
      this.mainEl.appendChild(this.buildPairingTable(allRows));
    }

    const sameDevice = this.buildSameDeviceSection();
    if (sameDevice) this.mainEl.appendChild(sameDevice);

    // A capture on a not-yet-existing row (e.g. "all" on a mapped run, or the
    // first-ever capture on a mapping-less run) has nothing to attach to
    // above - surface its live progress standalone so it isn't silent.
    // Gated on task.run === this.state.run: a capture tracked while viewing
    // another run must not bleed its progress into this one.
    const task = this.cache.captureProgress;
    if (task && task.run === this.state.run && !allRows.some((r) => this.rowMatchesCapture(r, task))) {
      if (task.state === "running") {
        const parts = this.buildCaptureStepsLine(task);
        this.mainEl.appendChild(
          el("div", {
            className: "notice notice-indigo",
            children: [
              el("span", { className: "capturing-pill", text: "capturing…" }),
              document.createTextNode(` ${task.device}:${task.port || "all"} (${task.phase}) `),
              ...(parts.length ? parts : [el("span", { text: "starting…" })]),
            ],
          })
        );
      } else if (task.state === "failed") {
        this.mainEl.appendChild(
          el("div", {
            className: "notice notice-fail",
            text: `${task.device}:${task.port || "all"} (${task.phase}) failed — ${task.error || ""}`,
          })
        );
      } else if (task.state === "done" && this.hasCaptureIssues(task)) {
        this.mainEl.appendChild(
          el("div", {
            className: "notice notice-warn",
            children: [
              document.createTextNode(`${task.device}:${task.port || "all"} (${task.phase}) done with issues — `),
              ...this.buildCaptureIssueLines(task),
            ],
          })
        );
      }
    }

    const footer = el("div", {
      className: "footer-actions",
      children: [
        el("button", {
          className: "btn btn-secondary",
          text: "Edit mapping",
          onClick: () => this.openEditMapping(),
        }),
        el("button", {
          className: "btn btn-secondary",
          text: "Export JSON",
          onClick: () => this.exportJson(),
        }),
        el("button", {
          className: "btn btn-primary-green",
          text: "Evaluate run",
          onClick: () => this.evaluateRun(),
        }),
      ],
    });
    this.mainEl.appendChild(footer);
  }

  buildFlagCell(row, phaseKey, task) {
    const matches = task && this.rowMatchesCapture(row, task) && task.phase === phaseKey;
    if (matches && task.state === "running") {
      return el("span", {
        className: "flag-cell",
        children: [el("span", { className: "capturing-pill", text: "capturing…" })],
      });
    }
    if (matches && task.state === "failed") {
      return el("span", { className: "flag-cell off", text: "—" });
    }
    const on = !!row[phaseKey];
    return el("span", { className: "flag-cell " + (on ? "on" : "off"), text: on ? "✓" : "—" });
  }

  buildPairingTable(rows) {
    const table = el("div", { className: "pairing-table" });
    table.appendChild(
      el("div", {
        className: "pairing-header-row",
        children: [
          el("span", {}),
          el("span", { text: "Old port" }),
          el("span", { text: "New port" }),
          el("span", { text: "Pre" }),
          el("span", { text: "Post" }),
          el("span", { text: "Rollback" }),
          el("span", { className: "col-result", text: "Result" }),
        ],
      })
    );

    const task = this.cache.captureProgress;
    for (const row of rows) {
      const evaluation = this.findEvaluation(row);
      const key = this.rowKey(row);
      let worst = "waiting";
      let presentation = null;
      if (evaluation) {
        presentation = this.rowPresentation(evaluation);
        worst = presentation.worst;
      }
      const pillClass = evaluation ? "pill-" + statusClass(worst) : "pill-waiting";
      const pillLabel = evaluation ? this.pillText(worst, presentation.count) : "waiting";
      const clickable = !!(evaluation && presentation.clickable);
      const open = clickable && !!this.state.openRows[key];
      const rowMatches = this.rowMatchesCapture(row, task);

      const rowEl = el("div", {
        className:
          "pairing-row" +
          (clickable ? " clickable" : "") +
          (evaluation ? " " + this.rowTintClass(worst) : ""),
        onClick: clickable ? () => this.toggleRow(key) : null,
        children: [
          el("span", { className: "chevron" + (open ? " open" : ""), html: "&#9654;" }),
          el("span", {
            className: "port-cell" + (row.old ? "" : " unpaired"),
            text: row.old ? `${row.old.node}:${row.old.port || "all"}` : "not paired",
          }),
          el("span", {
            className: "port-cell" + (row.new ? "" : " unpaired"),
            text: row.new ? `${row.new.node}:${row.new.port || "all"}` : "not paired",
          }),
          this.buildFlagCell(row, "pre", task),
          this.buildFlagCell(row, "post", task),
          this.buildFlagCell(row, "rollback", task),
          el("span", {
            className: "col-result",
            children: [el("span", { className: "status-pill " + pillClass, text: pillLabel })],
          }),
        ],
      });
      table.appendChild(rowEl);

      if (rowMatches && task.state === "running") {
        const parts = this.buildCaptureStepsLine(task);
        table.appendChild(
          el("div", {
            className: "row-note",
            children: parts.length ? parts : [el("span", { text: "starting…" })],
          })
        );
      } else if (rowMatches && task.state === "failed") {
        table.appendChild(
          el("div", {
            className: "row-note row-note-fail",
            text: task.error || "capture selhal",
          })
        );
      } else if (rowMatches && task.state === "done" && this.hasCaptureIssues(task)) {
        table.appendChild(
          el("div", {
            className: "row-note row-note-warn",
            children: this.buildCaptureIssueLines(task),
          })
        );
      }

      if (open) {
        table.appendChild(this.buildScopesPanel(presentation.scopes, key));
      }
    }
    return table;
  }

  buildSameDeviceSection() {
    const evaluations = this.cache.evaluation
      ? this.cache.evaluation.evaluations
      : [];
    const sameDevice = evaluations.filter((ev) => ev.same_device);
    if (sameDevice.length === 0) return null;

    const table = el("div", { className: "pairing-table" });
    table.appendChild(
      el("div", {
        className: "pairing-header-row cols-same",
        children: [
          el("span", {}),
          el("span", { text: "Device" }),
          el("span", { text: "Pre taken" }),
          el("span", { text: "Post taken" }),
          el("span", { className: "col-result", text: "Result" }),
        ],
      })
    );

    for (const ev of sameDevice) {
      const subjectRecord = this.findSnapshotRecord(ev.subject);
      const baselineRecord = this.findSnapshotRecord(ev.baseline);
      const key = `same:${ev.subject}`;
      const presentation = this.rowPresentation(ev);
      const worst = presentation.worst;
      const clickable = presentation.clickable;
      const open = clickable && !!this.state.openRows[key];
      const deviceLabel = subjectRecord
        ? `${subjectRecord.device}:${subjectRecord.port || "all"}`
        : ev.subject;

      table.appendChild(
        el("div", {
          className:
            "pairing-row cols-same" +
            (clickable ? " clickable" : "") +
            " " + this.rowTintClass(worst),
          onClick: clickable ? () => this.toggleRow(key) : null,
          children: [
            el("span", { className: "chevron" + (open ? " open" : ""), html: "&#9654;" }),
            el("span", { className: "port-cell", text: deviceLabel }),
            el("span", {
              className: "taken-cell",
              text: baselineRecord ? baselineRecord.taken || "" : "",
            }),
            el("span", {
              className: "taken-cell",
              text: subjectRecord ? subjectRecord.taken || "" : "",
            }),
            el("span", {
              className: "col-result",
              children: [
                el("span", {
                  className: "status-pill pill-" + statusClass(worst),
                  text: this.pillText(worst, presentation.count),
                }),
              ],
            }),
          ],
        })
      );
      if (open) {
        table.appendChild(this.buildScopesPanel(presentation.scopes, key));
      }
    }

    return el("div", {
      className: "same-device-section",
      children: [
        el("div", {
          className: "subsection-title",
          text: "Same device — pre vs post",
        }),
        table,
      ],
    });
  }

  buildScopesPanel(scopes, rowKey, opts) {
    const noBaseline = !!(opts && opts.noBaseline);
    const flat = !!(opts && opts.flat);
    const panel = el("div", {
      className: "scopes-panel" + (flat ? " scopes-panel-flat" : ""),
    });
    for (const scope of scopes) {
      const scopeKey = `${rowKey}|${scope.scope_id}`;
      const scopeOpen = !!this.state.openScopes[scopeKey];
      const sClass = statusClass(scope.status);
      const card = el("div", {
        className: "scope-card status-" + sClass,
      });
      const headerRow = el("div", {
        className: "scope-card-header",
        onClick: () => this.toggleScope(scopeKey),
        children: [
          el("span", { className: "scope-pill " + sClass, text: scope.status }),
          el("span", { className: "scope-id", text: scope.scope_id }),
          el("span", { className: "scope-note", text: this.scopeNote(scope) }),
          el("span", { className: "scope-header-spacer" }),
          el("span", {
            className: "scope-chevron" + (scopeOpen ? " open" : ""),
            html: "&#9660;",
          }),
        ],
      });
      card.appendChild(headerRow);
      if (scopeOpen) {
        card.appendChild(this.buildChecksTable(scope.checks || [], noBaseline));
      }
      panel.appendChild(card);
    }
    return panel;
  }

  buildChecksTable(checks, noBaseline) {
    const wrap = el("div", { className: "checks-panel" });
    const grid = el("div", {
      className: "checks-grid" + (noBaseline ? " no-baseline" : ""),
    });
    const labels = noBaseline
      ? ["Check", "Message", "Value", "Status"]
      : ["Check", "Message", "Value", "Baseline", "Status"];
    for (const label of labels) {
      grid.appendChild(el("span", { className: "col-head", text: label }));
    }
    for (const check of checks) {
      const valueClass =
        check.status === "FAIL" ? " fail" : check.status === "WARN" ? " warn" : "";
      grid.appendChild(el("span", { className: "chk-id", text: check.id }));
      grid.appendChild(el("span", { className: "chk-msg", text: check.message }));
      grid.appendChild(
        el("span", { className: "chk-value" + valueClass, text: check.value ?? "—" })
      );
      if (!noBaseline) {
        grid.appendChild(
          el("span", { className: "chk-baseline", text: check.baseline_value ?? "—" })
        );
      }
      grid.appendChild(
        el("span", {
          className: "chk-status " + statusClass(check.status),
          text: check.status,
        })
      );
    }
    wrap.appendChild(grid);
    return wrap;
  }

  // -- snapshot evaluation (screen 2) ------------------------------------

  phaseHeaderPillClass(phase) {
    if (phase === "pre") return "pill-phase-pre";
    if (phase === "post") return "pill-phase-post";
    if (phase === "rollback") return "pill-phase-rollback";
    return "pill-phase-pre";
  }

  snapshotBaselineCandidates() {
    const record = this.findSnapshotRecord(this.state.selectedSnapshot);
    if (!record || !this.cache.detail) return [];
    return (this.cache.detail.snapshots || []).filter(
      (s) => s.device === record.device && s.file !== record.file
    );
  }

  buildSnapshotCompareBar() {
    const candidates = this.snapshotBaselineCandidates();
    if (candidates.length === 0) return null;
    const select = el("select", {
      className: "form-select",
      children: [
        el("option", { text: "— no baseline —", attrs: { value: "" } }),
        ...candidates.map((s) => {
          const label = `${s.phase} · ${s.port || "all"} · ${s.taken || ""}`;
          const attrs =
            s.file === this.state.snapshotBaseline
              ? { value: s.file, selected: "selected" }
              : { value: s.file };
          return el("option", { text: label, attrs });
        }),
      ],
    });
    select.addEventListener("change", (e) =>
      this.selectSnapshotBaseline(e.target.value)
    );
    return el("div", {
      className: "compare-bar",
      children: [el("span", { className: "compare-bar-label", text: "Compare with" }), select],
    });
  }

  renderSnapshotView() {
    clear(this.mainEl);
    this.mainEl.appendChild(this.buildBreadcrumb(this.state.selectedSnapshot, true));

    if (!this.cache.snapshotEval) {
      if (this.cache.snapshotEvalError) {
        const bar = this.buildSnapshotCompareBar();
        if (bar) this.mainEl.appendChild(bar);
        this.mainEl.appendChild(
          el("div", {
            className: "notice notice-warn",
            text: this.cache.snapshotEvalError.detail,
          })
        );
      }
      return;
    }

    const { snapshot, baseline, result } = this.cache.snapshotEval;
    const phase = result.subject ? result.subject.phase : null;
    const baselineRecord = this.findSnapshotRecord(this.state.snapshotBaseline);

    const headerChildren = [el("h1", { text: "Snapshot evaluation" })];
    if (phase) {
      headerChildren.push(
        el("span", {
          className: "header-pill " + this.phaseHeaderPillClass(phase),
          text: phase,
        })
      );
    }
    if (baseline && baselineRecord) {
      headerChildren.push(
        el("span", {
          className: "header-pill " + this.phaseHeaderPillClass(baselineRecord.phase),
          text: `vs ${baselineRecord.phase}`,
        })
      );
    } else {
      headerChildren.push(
        el("span", { className: "header-pill pill-no-baseline", text: "no baseline" })
      );
    }
    this.mainEl.appendChild(el("div", { className: "run-header", children: headerChildren }));

    const compareBar = this.buildSnapshotCompareBar();
    if (compareBar) this.mainEl.appendChild(compareBar);

    this.mainEl.appendChild(
      el("div", {
        className: "meta-bar",
        children: [
          el("span", {
            children: [
              document.createTextNode("Device "),
              el("span", { className: "value", text: snapshot.device || "" }),
            ],
          }),
          el("span", {
            children: [
              document.createTextNode("Platform "),
              el("span", { className: "value", text: snapshot.platform || "" }),
            ],
          }),
          el("span", {
            children: [
              document.createTextNode("Taken "),
              el("span", { className: "value", text: snapshot.taken || "" }),
            ],
          }),
          el("span", {
            children: [
              document.createTextNode("Collectors "),
              el("span", {
                className: "value",
                text: Object.keys(snapshot.collectors || {}).join(", "),
              }),
            ],
          }),
          baseline
            ? el("span", {
                children: [
                  document.createTextNode("Baseline taken "),
                  el("span", { className: "value", text: baseline.taken || "" }),
                ],
              })
            : null,
        ],
      })
    );

    const summary = result.summary || {};
    this.mainEl.appendChild(
      el("div", {
        className: "chip-row",
        children: [
          el("span", {
            className: "chip chip-pass",
            text: `${summary.pass || 0} PASS`,
          }),
          el("span", {
            className: "chip chip-warn",
            text: `${summary.warn || 0} WARN`,
          }),
          el("span", {
            className: "chip chip-fail",
            text: `${summary.fail || 0} FAIL`,
          }),
          el("span", {
            className: "chip chip-skip",
            text: `${summary.skip || 0} SKIP`,
          }),
          el("span", { className: "chip-row-spacer" }),
          el("span", {
            className: "chip-row-note",
            text: baseline
              ? `compared against ${this.state.snapshotBaseline}`
              : "standalone evaluation — comparison checks skipped",
          }),
        ],
      })
    );

    this.mainEl.appendChild(
      this.buildScopesPanel(result.scopes || [], "snapshot", {
        noBaseline: !baseline,
        flat: true,
      })
    );

    this.mainEl.appendChild(
      el("div", {
        className: "footer-actions",
        children: [
          el("button", {
            className: "btn btn-secondary",
            text: "Export JSON",
            onClick: () => this.exportSnapshotJson(),
          }),
        ],
      })
    );
  }

  // -- new capture form (screen 4) ----------------------------------------

  renderCaptureForm() {
    clear(this.mainEl);
    const form = this.state.captureForm;
    if (!form) return;

    this.mainEl.appendChild(
      el("div", {
        className: "breadcrumb",
        children: [
          el("span", {
            className: "crumb-link",
            text: `run ${form.run || ""}`,
            onClick: () => this.backToRunFromCapture(form.run),
          }),
          el("span", { className: "crumb-sep", text: "/" }),
          el("span", { className: "crumb-current", text: "new capture" }),
        ],
      })
    );
    this.mainEl.appendChild(el("h1", { className: "capture-h1", text: "New capture" }));

    if (this.cache.captureDetailError) {
      this.mainEl.appendChild(
        el("div", {
          className: "notice notice-warn",
          text: this.cache.captureDetailError.detail,
        })
      );
      return;
    }
    const detail = this.cache.captureDetail;
    if (!detail) return;

    const devices = detail.devices || {};
    const mapped = this.captureMappedRows();
    const isMapped = mapped.length > 0;
    const device = form.device;

    const grid = el("div", { className: "capture-grid" });

    // -- Target card --
    const runSelect = el("select", {
      className: "form-select",
      children: this.cache.runs.map((r) =>
        el("option", {
          text: r.name,
          attrs: r.name === form.run ? { value: r.name, selected: "selected" } : { value: r.name },
        })
      ),
    });
    runSelect.addEventListener("change", (e) => this.selectCaptureFormRun(e.target.value));
    const targetChildren = [
      el("div", { className: "form-section-label", text: "Target" }),
      this.buildCaptureField("Run", runSelect),
    ];

    const deviceSelect = el("select", {
      className: "form-select mono",
      children: Object.entries(devices).map(([node, d]) => {
        const label = `${node} — ${d.host} (${d.role})`;
        const attrs = node === device ? { value: node, selected: "selected" } : { value: node };
        return el("option", { text: label, attrs });
      }),
    });
    deviceSelect.addEventListener("change", (e) => this.selectCaptureFormDevice(e.target.value));
    targetChildren.push(this.buildCaptureField("Device", deviceSelect));

    const phaseToggle = el("div", { className: "phase-toggle" });
    for (const phase of ["pre", "post", "rollback"]) {
      phaseToggle.appendChild(
        el("span", {
          className: "phase-toggle-item" + (form.phase === phase ? " active" : ""),
          text: phase,
          onClick: () => this.selectCaptureFormPhase(phase),
        })
      );
    }
    targetChildren.push(this.buildCaptureField("Phase", phaseToggle));

    let selectedPort = form.port;
    let alreadyCaptured = false;
    if (isMapped) {
      const ports = device ? this.capturePortOptions(device) : [];
      const allCaptured = device ? this.captureAlreadyCaptured(device, null, form.phase) : false;
      const portSelect = el("select", {
        className: "form-select mono",
        children: [
          ...ports.map((p) => {
            const captured = device ? this.captureAlreadyCaptured(device, p, form.phase) : false;
            const label = p + (captured ? " ✓ captured" : "");
            const attrs = p === selectedPort ? { value: p, selected: "selected" } : { value: p };
            return el("option", { text: label, attrs });
          }),
          el("option", {
            text: "all (whole device)" + (allCaptured ? " ✓ captured" : ""),
            attrs: selectedPort === null ? { value: "", selected: "selected" } : { value: "" },
          }),
        ],
      });
      portSelect.addEventListener("change", (e) => this.selectCaptureFormPort(e.target.value));
      targetChildren.push(this.buildCaptureField("Port", portSelect));
      alreadyCaptured = selectedPort === null ? allCaptured : this.captureAlreadyCaptured(device, selectedPort, form.phase);

      const mapsTo = device ? this.captureMapsToText(device, selectedPort) : null;
      if (mapsTo) {
        targetChildren.push(
          this.buildCaptureField(
            "Maps to",
            el("div", { className: "readonly-row mono", text: mapsTo })
          )
        );
      }
    } else {
      const portInput = el("input", {
        className: "form-input mono",
        attrs: { type: "text", placeholder: "all" },
      });
      portInput.value = form.portText || "";
      portInput.addEventListener("input", (e) => this.setCaptureFormPortText(e.target.value));
      portInput.addEventListener("change", () => this.commitCaptureFormPortText());
      targetChildren.push(this.buildCaptureField("Port", portInput));
      const raw = (form.portText || "").trim();
      const effectivePort = !raw || raw.toLowerCase() === "all" ? null : raw;
      alreadyCaptured = device ? this.captureAlreadyCaptured(device, effectivePort, form.phase) : false;
    }

    const parseServicesLabel = el("label", {
      className: "checkbox-row",
      children: [
        (() => {
          const cb = el("input", { attrs: { type: "checkbox" } });
          cb.checked = !!form.parseServices;
          cb.addEventListener("change", () => this.toggleCaptureFormParseServices());
          return cb;
        })(),
        document.createTextNode("Regenerate inventory from config "),
        el("span", { className: "mono checkbox-flag", text: "(--parse-services)" }),
      ],
    });
    targetChildren.push(parseServicesLabel);

    const targetCard = el("div", { className: "form-card", children: targetChildren });

    // -- Profile & auth card --
    const meta = this.cache.meta;
    const profileChildren = [el("div", { className: "form-section-label", text: "Profile & auth" })];
    const profileSelect = el("select", {
      className: "form-select mono",
      children: [
        el("option", {
          text: (meta && meta.profile) || "(default)",
          attrs: { value: "", selected: "selected" },
        }),
      ],
      attrs: { disabled: "disabled" },
    });
    profileChildren.push(this.buildCaptureField("Profile", profileSelect));
    profileChildren.push(
      el("div", {
        className: "readonly-row",
        children: [
          el("span", { className: "readonly-label", text: "Auth" }),
          el("span", {
            className: "readonly-value mono",
            text: meta ? meta.auth : this.cache.metaError ? "unavailable" : "…",
          }),
        ],
      })
    );
    const deviceForCollectors = device ? devices[device] : null;
    const platformCollectors =
      meta && meta.collectors && deviceForCollectors
        ? meta.collectors[deviceForCollectors.platform]
        : null;
    const collectorsValue = el("span", {
      className: "readonly-value mono",
      text: platformCollectors ? `${platformCollectors.length} collectors` : "—",
    });
    if (platformCollectors) {
      collectorsValue.setAttribute("title", platformCollectors.join(", "));
    }
    profileChildren.push(
      el("div", {
        className: "readonly-row",
        children: [el("span", { className: "readonly-label", text: "Collectors" }), collectorsValue],
      })
    );
    if (this.cache.metaError) {
      profileChildren.push(
        el("div", { className: "notice notice-warn", text: this.cache.metaError.detail })
      );
    }
    const profileCard = el("div", { className: "form-card", children: profileChildren });

    const sideChildren = [profileCard];
    const baseline = this.captureBaselineNotice();
    if (baseline) {
      sideChildren.push(
        el("div", {
          className: "notice " + (baseline.kind === "indigo" ? "notice-indigo" : "notice-warn"),
          text: baseline.text,
        })
      );
    }
    if (this.state.captureSubmitError) {
      sideChildren.push(
        el("div", { className: "notice notice-fail", text: this.state.captureSubmitError })
      );
    }

    const guardTask = this.activeCaptureGuardTask();
    if (guardTask) {
      sideChildren.push(
        el("div", {
          className: "notice notice-indigo",
          text: `a capture is already running (${guardTask.device}:${guardTask.port || "all"} ${guardTask.phase}) — wait for it to finish`,
        })
      );
    }

    const submitDisabled = this.state.captureSubmitting || !!guardTask;
    const actions = el("div", {
      className: "footer-actions",
      children: [
        el("button", {
          className: "btn btn-secondary",
          text: "Cancel",
          onClick: () => this.backToRunFromCapture(form.run),
        }),
        el("button", {
          className: "btn btn-primary",
          text: this.state.captureSubmitting
            ? "Starting…"
            : alreadyCaptured
              ? "Re-capture"
              : "Start capture",
          onClick: submitDisabled ? null : () => this.startCapture(),
          attrs: submitDisabled ? { disabled: "disabled" } : {},
        }),
      ],
    });
    sideChildren.push(actions);
    const sideCol = el("div", { className: "capture-side", children: sideChildren });

    grid.appendChild(targetCard);
    grid.appendChild(sideCol);
    this.mainEl.appendChild(grid);
  }

  buildCaptureField(label, control) {
    return el("div", {
      className: "form-field",
      children: [el("label", { className: "field-label", text: label }), control],
    });
  }

  // -- shared mapping-card builder (screens 5 & 6) -------------------------

  mappingDupErrors(rows) {
    const seen = new Set();
    const dup = new Set();
    for (const r of rows) {
      const old = (r.old || "").trim();
      if (!old) continue;
      if (seen.has(old)) dup.add(old);
      seen.add(old);
    }
    return dup;
  }

  buildMappingCard(rows, opts) {
    const card = el("div", { className: "form-card" });
    card.appendChild(el("div", { className: "form-section-label", text: "Port mapping" }));
    const list = el("div", { className: "mapping-rows" });
    const dupOld = opts.dupOld || new Set();
    rows.forEach((row, i) => {
      const locked = !!row.locked;
      const editableRow = opts.editable && !locked;

      const oldInput = el("input", {
        className: "form-input mono mapping-port",
        attrs: { type: "text", placeholder: "old port" },
      });
      oldInput.value = row.old || "";
      oldInput.disabled = !editableRow;
      oldInput.addEventListener("input", (e) => opts.onChangeOld(i, e.target.value));
      oldInput.addEventListener("blur", () => opts.onBlur && opts.onBlur());

      const newInput = el("input", {
        className: "form-input mono mapping-port",
        attrs: { type: "text", placeholder: "new port" },
      });
      newInput.value = row.new || "";
      newInput.disabled = !editableRow;
      newInput.addEventListener("input", (e) => opts.onChangeNew(i, e.target.value));
      newInput.addEventListener("blur", () => opts.onBlur && opts.onBlur());

      const rowChildren = [oldInput, el("span", { className: "mapping-arrow", text: "→" }), newInput];
      if (locked) {
        rowChildren.push(
          el("span", {
            className: "locked-hint",
            text: "locked",
            attrs: { title: "has snapshots" },
          })
        );
      } else if (opts.editable) {
        rowChildren.push(
          el("span", {
            className: "mapping-remove",
            text: "✕",
            onClick: () => opts.onRemove(i),
          })
        );
      } else {
        rowChildren.push(el("span", {}));
      }
      list.appendChild(el("div", { className: "mapping-row", children: rowChildren }));
      if (dupOld.has((row.old || "").trim())) {
        list.appendChild(
          el("div", { className: "field-error", text: "duplicate old port" })
        );
      } else if (opts.touched && (!row.old.trim() || !row.new.trim())) {
        list.appendChild(
          el("div", { className: "field-error", text: "both ports are required" })
        );
      }
    });
    if (rows.length === 0) {
      list.appendChild(
        el("div", { className: "mapping-empty-hint", text: "no pairings yet" })
      );
    }
    card.appendChild(list);
    if (opts.editable) {
      card.appendChild(
        el("button", {
          className: "btn btn-secondary mapping-add-btn",
          text: "+ add pairing",
          onClick: opts.onAdd,
        })
      );
    }
    return card;
  }

  buildDeviceSubform(title, device, touched) {
    const wrap = el("div", { className: "device-subform" });
    wrap.appendChild(el("div", { className: "device-subform-title", text: title }));

    const nodeInput = el("input", { className: "form-input mono", attrs: { type: "text" } });
    nodeInput.value = device.node;
    nodeInput.addEventListener("input", (e) => {
      device.node = e.target.value;
    });
    nodeInput.addEventListener("blur", () => this.render());
    const nodeField = [el("label", { className: "field-label", text: "Node" }), nodeInput];
    if (touched && !device.node.trim()) {
      nodeField.push(el("div", { className: "field-error", text: "node is required" }));
    }
    wrap.appendChild(el("div", { className: "form-field", children: nodeField }));

    const hostInput = el("input", { className: "form-input", attrs: { type: "text" } });
    hostInput.value = device.host;
    hostInput.addEventListener("input", (e) => {
      device.host = e.target.value;
    });
    hostInput.addEventListener("blur", () => this.render());
    const hostField = [el("label", { className: "field-label", text: "Host" }), hostInput];
    if (touched && !device.host.trim()) {
      hostField.push(el("div", { className: "field-error", text: "host is required" }));
    }
    wrap.appendChild(el("div", { className: "form-field", children: hostField }));

    const platformSelect = el("select", {
      className: "form-select",
      children: ["junos", "junos-evo"].map((p) =>
        el("option", {
          text: p,
          attrs: p === device.platform ? { value: p, selected: "selected" } : { value: p },
        })
      ),
    });
    platformSelect.addEventListener("change", (e) => {
      device.platform = e.target.value;
      this.render();
    });
    wrap.appendChild(this.buildCaptureField("Platform", platformSelect));
    return wrap;
  }

  buildReadonlyDeviceSubform(title, device) {
    const wrap = el("div", { className: "device-subform" });
    wrap.appendChild(el("div", { className: "device-subform-title", text: title }));
    if (!device) return wrap;
    const rows = [
      ["Node", device.node],
      ["Host", device.host],
      ["Platform", device.platform],
    ];
    for (const [label, value] of rows) {
      wrap.appendChild(
        el("div", {
          className: "readonly-row mono",
          children: [
            el("span", { className: "readonly-label", text: label }),
            el("span", { className: "readonly-value", text: value || "" }),
          ],
        })
      );
    }
    return wrap;
  }

  // -- new run (screen 5) --------------------------------------------------

  openNewRunForm() {
    this.state.view = "newrun";
    this.state.selectedSnapshot = null;
    this.state.newRunForm = {
      name: "",
      touched: false,
      old: { node: "", host: "", platform: "junos" },
      new: { node: "", host: "", platform: "junos-evo" },
      mappings: [],
      submitting: false,
      submitError: null,
    };
    this.render();
  }

  cancelNewRunForm() {
    this.state.newRunForm = null;
    this.state.view = this.cache.runs.length === 0 ? "empty" : "run";
    this.render();
  }

  newRunNameError() {
    const name = (this.state.newRunForm.name || "").trim();
    if (!name) return "run name is required";
    if (!/^[a-z0-9_-]+$/.test(name)) return "only a-z 0-9 _ - allowed";
    return null;
  }

  async submitNewRun() {
    const form = this.state.newRunForm;
    form.touched = true;
    const nameErr = this.newRunNameError();
    const dup = this.mappingDupErrors(form.mappings);
    const devicesOk =
      form.old.node.trim() &&
      form.old.host.trim() &&
      form.new.node.trim() &&
      form.new.host.trim();
    const mappingsOk = form.mappings.every((r) => r.old.trim() && r.new.trim());
    if (nameErr || !devicesOk || dup.size || !mappingsOk) {
      this.render();
      return;
    }
    form.submitting = true;
    form.submitError = null;
    this.render();
    try {
      const res = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name.trim(),
          old_device: {
            node: form.old.node.trim(),
            host: form.old.host.trim(),
            platform: form.old.platform,
          },
          new_device: {
            node: form.new.node.trim(),
            host: form.new.host.trim(),
            platform: form.new.platform,
          },
          mappings: form.mappings.map((r) => [r.old.trim(), r.new.trim()]),
        }),
      });
      if (res.status === 201) {
        const detail = await res.json();
        const runName = form.name.trim();
        this.cache.detail = detail;
        this.cache.runs = (await (await fetch("/api/runs")).json()).runs;
        this.state.newRunForm = null;
        this.state.run = runName;
        this.state.view = "run";
        this.state.selectedSnapshot = null;
        this.state.openRows = {};
        this.state.openScopes = {};
        await this.loadRun();
        this.render();
        return;
      }
      const body = await res.json().catch(() => ({}));
      form.submitError = body.detail || `run se nepodarilo vytvorit (${res.status})`;
    } catch (err) {
      form.submitError = String(err);
    }
    form.submitting = false;
    this.render();
  }

  renderNewRunForm() {
    clear(this.mainEl);
    const form = this.state.newRunForm;
    if (!form) return;

    this.mainEl.appendChild(
      el("div", {
        className: "breadcrumb",
        children: [el("span", { className: "crumb-current", text: "new run" })],
      })
    );
    this.mainEl.appendChild(el("h1", { className: "capture-h1", text: "New run" }));

    const nameInput = el("input", {
      className: "form-input mono",
      attrs: { type: "text", placeholder: "e.g. mig01" },
    });
    nameInput.value = form.name;
    nameInput.addEventListener("input", (e) => {
      form.name = e.target.value;
    });
    nameInput.addEventListener("blur", () => {
      form.touched = true;
      this.render();
    });
    const nameFieldChildren = [el("label", { className: "field-label", text: "Run name" }), nameInput];
    const nameErr = form.touched ? this.newRunNameError() : null;
    if (nameErr) nameFieldChildren.push(el("div", { className: "field-error", text: nameErr }));
    if (form.submitError) {
      nameFieldChildren.push(el("div", { className: "field-error", text: form.submitError }));
    }
    this.mainEl.appendChild(
      el("div", {
        className: "form-card",
        children: [
          el("div", { className: "form-section-label", text: "Run name" }),
          el("div", { className: "form-field", children: nameFieldChildren }),
        ],
      })
    );

    const devicesGrid = el("div", {
      className: "devices-grid",
      children: [
        this.buildDeviceSubform("Old device", form.old, form.touched),
        this.buildDeviceSubform("New device", form.new, form.touched),
      ],
    });
    this.mainEl.appendChild(
      el("div", {
        className: "form-card",
        children: [el("div", { className: "form-section-label", text: "Devices" }), devicesGrid],
      })
    );

    this.mainEl.appendChild(
      this.buildMappingCard(form.mappings, {
        editable: true,
        touched: form.touched,
        dupOld: this.mappingDupErrors(form.mappings),
        onAdd: () => {
          form.mappings.push({ old: "", new: "" });
          this.render();
        },
        onRemove: (i) => {
          form.mappings.splice(i, 1);
          this.render();
        },
        onChangeOld: (i, v) => {
          form.mappings[i].old = v;
        },
        onChangeNew: (i, v) => {
          form.mappings[i].new = v;
        },
        onBlur: () => {
          form.touched = true;
          this.render();
        },
      })
    );

    this.mainEl.appendChild(
      el("div", {
        className: "footer-actions",
        children: [
          el("button", {
            className: "btn btn-secondary",
            text: "Cancel",
            onClick: () => this.cancelNewRunForm(),
          }),
          el("button", {
            className: "btn btn-primary",
            text: form.submitting ? "Creating…" : "Create run",
            onClick: form.submitting ? null : () => this.submitNewRun(),
          }),
        ],
      })
    );
  }

  // -- edit mapping (screen 6) ---------------------------------------------

  openEditMapping() {
    const detail = this.cache.detail;
    if (!detail) return;
    const rows = (detail.rows || [])
      .filter((r) => r.old && r.new)
      .map((r) => ({
        old: r.old.port,
        new: r.new.port,
        locked: !!(r.pre || r.post || r.rollback),
      }));
    this.state.editMappingForm = { rows, touched: false, submitting: false, submitError: null };
    this.state.view = "editmapping";
    this.render();
  }

  async saveMapping() {
    const form = this.state.editMappingForm;
    form.touched = true;
    const dup = this.mappingDupErrors(form.rows);
    const rowsOk = form.rows.every((r) => r.old.trim() && r.new.trim());
    if (dup.size || !rowsOk) {
      this.render();
      return;
    }
    form.submitting = true;
    form.submitError = null;
    this.render();
    try {
      const res = await fetch(`/api/runs/${this.state.run}/mapping`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mappings: form.rows.map((r) => [r.old.trim(), r.new.trim()]),
        }),
      });
      if (res.ok) {
        this.cache.detail = await res.json();
        this.state.editMappingForm = null;
        this.state.view = "run";
        await this.loadRun();
        this.render();
        return;
      }
      const body = await res.json().catch(() => ({}));
      form.submitError = body.detail || `mapping se nepodarilo ulozit (${res.status})`;
    } catch (err) {
      form.submitError = String(err);
    }
    form.submitting = false;
    this.render();
  }

  renderEditMapping() {
    clear(this.mainEl);
    const form = this.state.editMappingForm;
    const detail = this.cache.detail;
    if (!form || !detail) return;

    this.mainEl.appendChild(this.buildBreadcrumb("edit mapping", false));
    this.mainEl.appendChild(el("h1", { className: "capture-h1", text: "Edit mapping" }));

    const devices = detail.devices || {};
    let oldDevice = null;
    let newDevice = null;
    for (const [node, d] of Object.entries(devices)) {
      if (d.role === "old") oldDevice = { node, ...d };
      else if (d.role === "new") newDevice = { node, ...d };
    }
    const devicesGrid = el("div", {
      className: "devices-grid",
      children: [
        this.buildReadonlyDeviceSubform("Old device", oldDevice),
        this.buildReadonlyDeviceSubform("New device", newDevice),
      ],
    });
    this.mainEl.appendChild(
      el("div", {
        className: "form-card",
        children: [el("div", { className: "form-section-label", text: "Devices" }), devicesGrid],
      })
    );

    this.mainEl.appendChild(
      this.buildMappingCard(form.rows, {
        editable: true,
        touched: form.touched,
        dupOld: this.mappingDupErrors(form.rows),
        onAdd: () => {
          form.rows.push({ old: "", new: "", locked: false });
          this.render();
        },
        onRemove: (i) => {
          form.rows.splice(i, 1);
          this.render();
        },
        onChangeOld: (i, v) => {
          form.rows[i].old = v;
        },
        onChangeNew: (i, v) => {
          form.rows[i].new = v;
        },
        onBlur: () => {
          form.touched = true;
          this.render();
        },
      })
    );

    if (form.submitError) {
      this.mainEl.appendChild(el("div", { className: "notice notice-fail", text: form.submitError }));
    }

    this.mainEl.appendChild(
      el("div", {
        className: "footer-actions",
        children: [
          el("button", {
            className: "btn btn-secondary",
            text: "Cancel",
            onClick: () => this.backToRun(),
          }),
          el("button", {
            className: "btn btn-primary",
            text: form.submitting ? "Saving…" : "Save",
            onClick: form.submitting ? null : () => this.saveMapping(),
          }),
        ],
      })
    );
  }

  // -- registered checks (screen 3) --------------------------------------

  renderChecksView() {
    clear(this.mainEl);
    this.mainEl.appendChild(this.buildBreadcrumb("registered checks", false));

    if (!this.cache.checks) {
      if (this.cache.checksError) {
        this.mainEl.appendChild(
          el("div", {
            className: "notice notice-warn",
            text: this.cache.checksError.detail,
          })
        );
      }
      return;
    }

    const checks = this.cache.checks.checks || [];
    this.mainEl.appendChild(
      el("div", {
        className: "run-header",
        children: [
          el("h1", { text: "Registered checks" }),
          el("span", {
            className: "subtitle",
            text: `${checks.length} checks`,
          }),
        ],
      })
    );

    const table = el("div", { className: "checks-registry-table" });
    table.appendChild(
      el("div", {
        className: "checks-registry-header-row",
        children: [
          el("span", { text: "ID" }),
          el("span", { text: "Mode" }),
          el("span", { text: "Severity" }),
          el("span", { text: "Service types" }),
          el("span", { className: "col-enabled", text: "Enabled" }),
        ],
      })
    );
    for (const check of checks) {
      const severity = (check.default_severity || "").toLowerCase();
      const enabled = check.enabled === undefined ? true : !!check.enabled;
      const types = check.service_types ? check.service_types.join(", ") : "all";
      table.appendChild(
        el("div", {
          className: "checks-registry-row" + (enabled ? "" : " disabled"),
          children: [
            el("span", { className: "reg-id", text: check.id }),
            el("span", { className: "reg-mode", text: check.mode }),
            el("span", {
              className: "reg-severity " + severity,
              text: (check.default_severity || "").toUpperCase(),
            }),
            el("span", { className: "reg-types", text: types }),
            el("span", {
              className: "reg-enabled " + (enabled ? "yes" : "off"),
              text: enabled ? "yes" : "off",
            }),
          ],
        })
      );
    }
    this.mainEl.appendChild(table);
  }
}

document.addEventListener("DOMContentLoaded", () => new App().boot());
