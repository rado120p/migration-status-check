"use strict";

/* mig-validate GUI - vanilla JS, fetch only, no framework, no build step. */

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

/* New run type cards. Bulk is a placeholder: rendered, never selectable. */
const RUN_TYPES = [
  {
    kind: "single",
    title: "Single device",
    desc: "pre/post snapshots of one box: upgrade, reconfiguration, maintenance",
  },
  {
    kind: "migration",
    title: "Two devices",
    desc: "old → new migration with port pairing",
  },
  {
    kind: "bulk",
    title: "Bulk",
    desc: "many single-device runs from a device list",
    disabled: true,
    note: "bulk · pripravuje se",
  },
];

/* Per-screen copy for the guide rail, keyed by this.state.view values. */
const GUIDE_TEXT = {
  empty: {
    title: "Zatím žádný run",
    body: [
      "Run je vstupní bod nástroje: adresář runs/<název>/ s run.yml, do kterého se ukládají snímky zařízení a párování portů.",
      "Založ první run tlačítkem + New run — pak můžeš sbírat snapshoty (New capture) a vyhodnocovat.",
    ],
  },
  run: {
    title: "Run overview",
    body: [
      "Tenhle screen porovnává služby mezi pre a post snímky namapovaných portů.",
      "Single device run porovnává post (nebo rollback) snímek boxu s jeho vlastním pre snímkem — tabulka má jeden sloupec Device a žádné mapování portů.",
      "Kliknutím na řádek v Results rozbalíš detail checků včetně změn proti baseline.",
      "Nespárováno = služba, která po migraci chybí. Vždy zkontroluj, než run uzavřeš.",
      "Nezařazeno = objekt (BGP peer, routa, BFD session), který si nenárokovala žádná služba — typicky mezera v parsování.",
      "Archive run přesune adresář runu do runs/.archive/ — ze seznamu zmizí, snímky zůstanou. Archiv se čistí přes mig-validate run purge.",
    ],
  },
  snapshot: {
    title: "Snapshot",
    body: [
      "Samostatné vyhodnocení jednoho snímku — běží jen stavové checky (stav rozhraní, ARP/ND, ping). Srovnání s baseline najdeš v run overview.",
      "Nezařazeno = objekt bez služby — zkontroluj, jestli nechybí v inventáři.",
    ],
  },
  profiles: {
    title: "Profiles",
    body: [
      "Profil říká, co run měří: které collectory se sbírají, které typy služeb se hodnotí, kolik pingů se posílá a jak jsou nastavené checky. Ukládá se do profiles/<název>.yml, run si ho vybírá při založení.",
      "(default) je serverový profil z --profile (nebo vestavěný prázdný) a v GUI se needituje — Duplicate z něj udělá pojmenovanou kopii.",
      "Prázdný výběr collectorů nebo typů služeb znamená všechny. Profil, který používá nějaký run, nejde smazat.",
    ],
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
    body: [
      "Vyber typ runu: Single device = pre/post snímky jednoho boxu (upgrade, rekonfigurace), Two devices = migrace old → new s párováním portů. Bulk se připravuje.",
      "Pojmenuj run a vyplň zařízení. U Two devices můžeš mapování portů doplnit i později přes Edit mapping.",
      "Profil vyber ze seznamu profiles/ — (default) je serverový profil. Profily spravuješ tlačítkem Profiles v horní liště.",
    ],
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
      openResults: {},
      activeCaptureId: null,
      captureForm: null,
      captureSubmitError: null,
      captureSubmitting: false,
      newRunForm: null,
      editMappingForm: null,
      combo: { open: false, query: "", index: 0 },
      archiveModal: null,
      profileEditor: null,
    };
    this.cache = {
      runs: [],
      runsError: null,
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
      profiles: null,
      profilesError: null,
      catalogue: null,
      catalogueError: null,
    };
    this.capturePollTimer = null;

    this.profileNameEl = document.getElementById("profile-name");
    this.comboEl = document.getElementById("run-combo");
    this.comboToggleEl = document.getElementById("run-combo-toggle");
    this.comboCurrentEl = document.getElementById("run-combo-current");
    this.comboPanelEl = document.getElementById("run-combo-panel");
    this.comboFilterEl = document.getElementById("run-combo-filter");
    this.comboListEl = document.getElementById("run-combo-list");
    this.btnNewRunEl = document.getElementById("btn-new-run");
    this.sidebarSnapshotsEl = document.getElementById("sidebar-snapshots");
    this.sidebarFooterEl = document.getElementById("sidebar-footer");
    this.mainEl = document.getElementById("main");
    this.guideEl = document.getElementById("guide");
    this.btnProfilesEl = document.getElementById("btn-profiles");

    this.btnProfilesEl.addEventListener("click", () => this.goToProfiles(null));
    document.getElementById("btn-new-capture").addEventListener("click", () => {
      this.openCaptureForm();
    });
    this.btnNewRunEl.addEventListener("click", () => this.openNewRunForm());
    document.getElementById("run-combo-new").addEventListener("click", (e) => {
      e.preventDefault();
      this.closeRunCombo();
      this.openNewRunForm();
    });
    this.comboToggleEl.addEventListener("click", () => {
      if (this.state.combo.open) this.closeRunCombo();
      else this.openRunCombo();
    });
    this.comboFilterEl.addEventListener("input", (e) => {
      this.state.combo.query = e.target.value;
      this.state.combo.index = 0;
      this.renderRunCombo();
    });
    this.comboFilterEl.addEventListener("keydown", (e) => this.onComboKey(e));
    document.addEventListener("mousedown", (e) => {
      if (this.state.combo.open && !this.comboEl.contains(e.target)) this.closeRunCombo();
    });
    window.addEventListener("beforeunload", () => this.stopCapturePolling());
    window.addEventListener("beforeunload", (e) => {
      if (this.editorDirty()) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
  }

  async boot() {
    try {
      const res = await fetch("/api/runs");
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `runs se nepodarilo nacist (${res.status})`);
      }
      const { runs } = await res.json();
      this.cache.runs = Array.isArray(runs) ? runs : [];
      this.cache.runsError = null;
    } catch (err) {
      this.cache.runs = [];
      this.cache.runsError = String(err.message || err);
    }
    if (this.cache.runs.length === 0) {
      this.state.view = "empty";
    } else {
      this.state.run = this.cache.runs[0].name;
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

  // -- run combobox (topbar) ----------------------------------------------

  openRunCombo() {
    this.state.combo = { open: true, query: "", index: 0 };
    this.comboFilterEl.value = "";
    this.renderRunCombo();
    this.comboFilterEl.focus();
  }

  closeRunCombo() {
    if (!this.state.combo.open) return;
    this.state.combo.open = false;
    this.renderRunCombo();
  }

  comboMatches() {
    return MigView.filterRuns(this.cache.runs, this.state.combo.query);
  }

  onComboKey(e) {
    const matches = this.comboMatches();
    const combo = this.state.combo;
    if (e.key === "Escape") {
      e.preventDefault();
      this.closeRunCombo();
      this.comboToggleEl.focus();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      combo.index = Math.min(combo.index + 1, Math.max(matches.length - 1, 0));
      this.renderRunCombo();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      combo.index = Math.max(combo.index - 1, 0);
      this.renderRunCombo();
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = matches[combo.index];
      if (pick) {
        this.closeRunCombo();
        this.selectRun(pick.name);
      }
    }
  }

  renderRunCombo() {
    const combo = this.state.combo;
    this.comboCurrentEl.textContent = this.state.run || (this.cache.runsError ? "(nelze nacist runy)" : "—");
    this.comboToggleEl.setAttribute("aria-expanded", combo.open ? "true" : "false");
    this.comboPanelEl.hidden = !combo.open;
    if (!combo.open) return;

    clear(this.comboListEl);
    const matches = this.comboMatches();
    if (matches.length === 0) {
      const text = this.cache.runsError
        ? this.cache.runsError
        : this.cache.runs.length === 0
        ? "no runs yet"
        : "no run matches";
      this.comboListEl.appendChild(el("div", { className: "run-combo-empty", text }));
      return;
    }
    matches.forEach((run, i) => {
      const active = run.name === this.state.run;
      const focused = i === combo.index;
      this.comboListEl.appendChild(
        el("div", {
          className:
            "run-combo-row" + (active ? " active" : "") + (focused ? " focused" : ""),
          attrs: { role: "option", "aria-selected": active ? "true" : "false" },
          onClick: () => {
            this.closeRunCombo();
            this.selectRun(run.name);
          },
          children: [
            el("span", { className: "mono run-combo-name", text: run.name }),
            el("span", {
              className: "run-combo-sub",
              text: `${run.snapshots} snapshot${run.snapshots === 1 ? "" : "s"}`,
            }),
          ],
        })
      );
    });
  }

  // -- archive run modal ------------------------------------------------------

  openArchiveModal() {
    if (!this.leaveGuard()) return;
    this.state.archiveModal = { submitting: false, error: null };
    this.render();
  }

  closeArchiveModal() {
    this.state.archiveModal = null;
    this.render();
  }

  async confirmArchive() {
    const modal = this.state.archiveModal;
    const run = this.state.run;
    if (!modal || !run || modal.submitting) return;
    modal.submitting = true;
    modal.error = null;
    this.render();
    try {
      const res = await fetch(`/api/runs/${run}/archive`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        modal.error = body.detail || `archivace selhala (${res.status})`;
        modal.submitting = false;
        this.render();
        return;
      }
      this.cache.runs = (await (await fetch("/api/runs")).json()).runs;
      this.state.archiveModal = null;
      this.state.run = null;
      this.cache.detail = null;
      this.cache.evaluation = null;
      this.state.selectedSnapshot = null;
      this.state.openResults = {};
      if (this.cache.runs.length === 0) {
        this.state.view = "empty";
        this.render();
      } else {
        await this.selectRun(this.cache.runs[0].name);
      }
    } catch (err) {
      modal.error = String(err);
      modal.submitting = false;
      this.render();
    }
  }

  renderModal() {
    let root = document.getElementById("modal-root");
    if (!root) {
      root = el("div", { attrs: { id: "modal-root" } });
      document.body.appendChild(root);
    }
    clear(root);
    const modal = this.state.archiveModal;
    if (!modal) return;
    const detail = this.cache.detail || {};
    const count = (detail.snapshots || []).length;
    const children = [
      el("h3", { text: "Archive run" }),
      el("p", {
        children: [
          document.createTextNode("Run "),
          el("span", { className: "mono", text: this.state.run || "" }),
          document.createTextNode(
            ` (${count} snapshot${count === 1 ? "" : "s"}) se přesune do runs/.archive/ a zmizí ze seznamu. Data zůstanou na disku.`
          ),
        ],
      }),
    ];
    if (modal.error) children.push(el("div", { className: "field-error", text: modal.error }));
    children.push(
      el("div", {
        className: "footer-actions",
        children: [
          el("button", {
            className: "btn btn-secondary",
            text: "Cancel",
            onClick: modal.submitting ? null : () => this.closeArchiveModal(),
          }),
          el("button", {
            className: "btn btn-danger",
            text: modal.submitting ? "Archiving…" : "Archive",
            onClick: modal.submitting ? null : () => this.confirmArchive(),
          }),
        ],
      })
    );
    root.appendChild(
      el("div", {
        className: "modal-backdrop",
        onClick: (e) => {
          if (e.target.classList.contains("modal-backdrop") && !modal.submitting) {
            this.closeArchiveModal();
          }
        },
        children: [el("div", { className: "modal", children })],
      })
    );
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

  toggleResult(key) {
    this.state.openResults[key] = !this.state.openResults[key];
    this.render();
  }

  collectServiceEntries(evaluations, prefix = "res") {
    const entries = [];
    for (const evaluation of evaluations) {
      const result = evaluation.result || {};
      const hasBaseline = result.baseline != null;
      for (const scope of result.scopes || []) {
        entries.push({
          key: `${prefix}|${evaluation.subject}|${evaluation.baseline || ""}|${scope.scope_id}`,
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

  async selectRun(name) {
    if (!this.leaveGuard()) return;
    // Same run only means "nothing to do" when the run view is already up -
    // from the profile editor the same name still has to navigate back.
    if (this.state.run === name && this.state.view === "run") return;
    this.state.run = name;
    this.state.view = "run";
    this.state.selectedSnapshot = null;
    this.state.openResults = {};
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
    await this.loadSnapshotEvaluation(file);
    this.render();
  }

  async loadSnapshotEvaluation(file) {
    this.cache.snapshotEval = null;
    this.cache.snapshotEvalError = null;
    try {
      const res = await fetch(
        `/api/runs/${this.state.run}/snapshots/${encodeURIComponent(file)}/evaluation`
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

  async loadCatalogue() {
    if (this.cache.catalogue) return;
    try {
      const res = await fetch("/api/profiles/catalogue");
      if (res.ok) {
        this.cache.catalogue = await res.json();
        this.cache.catalogueError = null;
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.catalogueError = body.detail || `katalog se nepodarilo nacist (${res.status})`;
      }
    } catch (err) {
      this.cache.catalogueError = String(err);
    }
  }

  editorDirty() {
    // Only while the editor is on screen: state.profileEditor survives a
    // navigation away (the guard already asked), and a stale copy must not
    // keep prompting or arm beforeunload for the rest of the session.
    const editor = this.state.profileEditor;
    if (this.state.view !== "profiles") return false;
    return !!editor && editor.name !== null && MigView.profileDirty(editor.doc, editor.saved);
  }

  // Every navigation away from the editor goes through here (spec §5:
  // "navigating away with unsaved changes asks for confirmation").
  leaveGuard() {
    if (!this.editorDirty()) return true;
    return window.confirm("Profil má neuložené změny. Zahodit je?");
  }

  async goToProfiles(name) {
    if (this.state.view === "profiles" && !this.leaveGuard()) return;
    this.state.view = "profiles";
    this.state.selectedSnapshot = null;
    this.state.profileEditor = { name, doc: null, saved: null, saving: false, error: null, loadError: null };
    this.render();
    await Promise.all([this.loadProfiles(), this.loadCatalogue(), this.loadChecks()]);
    await this.loadEditorDocument();
    this.render();
  }

  async loadEditorDocument() {
    const editor = this.state.profileEditor;
    if (!editor) return;
    const store = this.cache.profiles;
    if (editor.name === null) {
      const doc = (store && store.default_document) || MigView.emptyProfileDocument();
      editor.doc = JSON.parse(JSON.stringify(doc));
      editor.saved = JSON.parse(JSON.stringify(doc));
      return;
    }
    try {
      const res = await fetch(`/api/profiles/${editor.name}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        editor.loadError = body.detail || `profil se nepodarilo nacist (${res.status})`;
        return;
      }
      const { document } = await res.json();
      editor.doc = document;
      editor.saved = JSON.parse(JSON.stringify(document));
    } catch (err) {
      editor.loadError = String(err);
    }
  }

  async createProfileFromDocument(name, doc) {
    const res = await fetch("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, document: doc }),
    });
    if (res.status === 201) return null;
    const body = await res.json().catch(() => ({}));
    return body.detail || `profil se nepodarilo vytvorit (${res.status})`;
  }

  async newProfile(fromDoc) {
    if (!this.leaveGuard()) return;
    const name = window.prompt("Název nového profilu (a-z 0-9 _ -):", "");
    if (name === null) return;
    const trimmed = name.trim();
    if (!/^[a-z0-9_-]+$/.test(trimmed)) {
      this.state.profileEditor.error = `nevalidni jmeno profilu '${trimmed}' - povolene znaky: a-z 0-9 _ -`;
      this.render();
      return;
    }
    const doc = fromDoc ? MigView.normalizeProfileDocument(fromDoc) : MigView.emptyProfileDocument();
    const error = await this.createProfileFromDocument(trimmed, doc);
    if (error) {
      this.state.profileEditor.error = error;
      this.render();
      return;
    }
    this.state.profileEditor = null; // no guard prompt on the way in
    await this.goToProfiles(trimmed);
  }

  async deleteProfile() {
    const editor = this.state.profileEditor;
    if (!editor || editor.name === null) return;
    if (!window.confirm(`Smazat profil ${editor.name}? Soubor profiles/${editor.name}.yml zmizí.`)) return;
    try {
      const res = await fetch(`/api/profiles/${editor.name}`, { method: "DELETE" });
      if (res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        editor.error = body.detail || `profil se nepodarilo smazat (${res.status})`;
        this.render();
        return;
      }
    } catch (err) {
      editor.error = String(err);
      this.render();
      return;
    }
    this.state.profileEditor = null;
    await this.goToProfiles(null);
  }

  async saveProfile() {
    const editor = this.state.profileEditor;
    if (!editor || editor.name === null || editor.saving) return;
    editor.saving = true;
    editor.error = null;
    this.render();
    try {
      const res = await fetch(`/api/profiles/${editor.name}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document: editor.doc }),
      });
      if (res.ok) {
        const { document } = await res.json();
        editor.doc = document;
        editor.saved = JSON.parse(JSON.stringify(document));
        await this.loadProfiles();
      } else {
        const body = await res.json().catch(() => ({}));
        editor.error = body.detail || `profil se nepodarilo ulozit (${res.status})`;
      }
    } catch (err) {
      editor.error = String(err);
    }
    editor.saving = false;
    this.render();
  }

  discardProfile() {
    const editor = this.state.profileEditor;
    if (!editor || !editor.saved) return;
    editor.doc = JSON.parse(JSON.stringify(editor.saved));
    editor.error = null;
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
    if (!this.leaveGuard()) return;
    this.state.view = "run";
    this.state.selectedSnapshot = null;
    this.render();
  }

  // -- new capture form (screen 4) ---------------------------------------

  async openCaptureForm() {
    if (!this.leaveGuard()) return;
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

  async loadProfiles() {
    this.cache.profiles = null;
    this.cache.profilesError = null;
    try {
      const res = await fetch("/api/profiles");
      if (res.ok) {
        this.cache.profiles = await res.json();
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.profilesError = {
          status: res.status,
          detail: body.detail || `profily se nepodarilo nacist (${res.status})`,
        };
      }
    } catch (err) {
      this.cache.profilesError = { status: 0, detail: String(err) };
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
    // Baseline owner: the old box in a migration run, the box itself in a
    // single run (its whole-box pre is the baseline for post and rollback).
    const detail = this.cache.captureDetail;
    if (!detail) return null;
    const entries = Object.entries(detail.devices || {});
    const entry =
      entries.find(([, d]) => d.role === "old") ||
      entries.find(([, d]) => d.role === "single");
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
    const isSingle = this.cache.captureDetail && this.cache.captureDetail.kind === "single";
    let port;
    if (isSingle) {
      port = null;
    } else if (isMapped) {
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
    this.renderRunCombo();
    this.renderModal();
    this.btnProfilesEl.classList.toggle("btn-toggle-active", this.state.view === "profiles");
    const noRuns = this.cache.runs.length === 0;
    this.btnNewRunEl.classList.toggle("btn-pulse", noRuns);
    document.getElementById("btn-new-capture").disabled = noRuns;
    switch (this.state.view) {
      case "empty":
        this.renderEmptyState();
        break;
      case "snapshot":
        this.renderSnapshotView();
        break;
      case "profiles":
        this.renderProfilesView();
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
    const children = [];
    if (this.cache.runsError) {
      children.push(
        el("div", {
          className: "notice notice-warn",
          text: `Nepodařilo se načíst runy: ${this.cache.runsError}`,
        })
      );
    } else {
      children.push(el("h1", { className: "empty-state-title", text: "Zatím žádný run" }));
    }
    children.push(
      el("p", {
        className: "empty-state-hint",
        text:
          "Run je vstupní bod nástroje — bez něj nejde sbírat snapshoty ani vyhodnocovat. " +
          "Založ první run: pojmenuj ho a vyplň zařízení.",
      }),
      el("button", {
        className: "btn btn-primary",
        text: "+ New run",
        onClick: () => this.openNewRunForm(),
      })
    );
    this.mainEl.appendChild(el("div", { className: "empty-state", children }));
  }

  renderSidebar() {
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

  findSnapshotRecord(file) {
    const detail = this.cache.detail;
    if (!detail || !file) return null;
    return (detail.snapshots || []).find((s) => s.file === file) || null;
  }

  buildCountsStrip(services, checks, matchedLine) {
    const order = ["pass", "recv", "warn", "fail", "skip", "info"];
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
    const single = detail.kind === "single";
    let oldDevice = null;
    let newDevice = null;
    let singleDevice = null;
    for (const [node, d] of Object.entries(devices)) {
      if (d.role === "old") oldDevice = { node, ...d };
      else if (d.role === "new") newDevice = { node, ...d };
      else if (d.role === "single") singleDevice = { node, ...d };
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
    const profileName = detail.profile || null;
    header.appendChild(
      el("button", {
        className: "profile-link mono",
        text: profileName ? `profile: ${profileName}` : "profile: (default)",
        attrs: { type: "button", title: "otevřít profil" },
        onClick: () => this.goToProfiles(profileName),
      })
    );
    if (single && singleDevice) {
      const label = PLATFORM_LABEL[singleDevice.platform] || singleDevice.platform;
      header.appendChild(el("span", { className: "kind-tag", text: "single device" }));
      header.appendChild(
        el("span", {
          className: "subtitle",
          text: `${singleDevice.node} · ${singleDevice.host} (${label})`,
        })
      );
    } else if (oldDevice && newDevice) {
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
    header.appendChild(
      el("button", {
        className: "btn btn-danger-secondary run-header-archive",
        text: "Archive run",
        onClick: () => this.openArchiveModal(),
      })
    );

    if (this.cache.evaluationError) {
      this.mainEl.appendChild(
        el("div", {
          className: "notice notice-warn",
          text: this.cache.evaluationError.detail,
        })
      );
    }

    const evaluations = this.cache.evaluation ? this.cache.evaluation.evaluations : [];
    // Single run: every evaluation (post vs own pre, rollback vs own pre) is
    // the main result set; there is no separate same-device section.
    const pairEvaluations = single ? evaluations : evaluations.filter((ev) => !ev.same_device);
    const results = pairEvaluations.map((ev) => ev.result);
    const services = MigView.countStatuses(
      results.flatMap((r) => (r.scopes || []).map((s) => s.status))
    );
    const checks = { pass: 0, recv: 0, warn: 0, fail: 0, skip: 0, info: 0 };
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

    const allRows = detail.rows || [];
    const mappingRows = allRows.filter((r) => r.old && r.new);
    const wholeRows = allRows.filter((r) => !r.old || !r.new);
    if (single) {
      if (allRows.length > 0) {
        this.mainEl.appendChild(el("div", { className: "subsection-title", text: "Captures" }));
        this.mainEl.appendChild(this.buildPairingTable(allRows, { single: true }));
      }
    } else if (mappingRows.length === 0) {
      this.mainEl.appendChild(
        el("div", {
          className: "notice notice-warn",
          text: "no port mapping — captures are per-device",
        })
      );
      if (wholeRows.length > 0) {
        this.mainEl.appendChild(el("div", { className: "subsection-title", text: "Captures" }));
        this.mainEl.appendChild(this.buildPairingTable(wholeRows));
      }
    } else {
      this.mainEl.appendChild(el("div", { className: "subsection-title", text: "Captures" }));
      this.mainEl.appendChild(this.buildPairingTable(allRows));
    }

    const sameDevice = single ? null : this.buildSameDeviceSection();
    if (sameDevice) this.mainEl.appendChild(sameDevice);

    const entries = this.collectServiceEntries(pairEvaluations);
    if (entries.length > 0) {
      this.mainEl.appendChild(
        el("div", { className: "subsection-title", text: `Results — ${entries.length} služeb` })
      );
      this.mainEl.appendChild(this.buildResultsTable(entries, { singlePort: single }));
    }

    if (this.cache.evaluation) {
      const unmatchedItems = [];
      const unassignedAgg = { bgp_peers: [], static_routes: [], bfd_sessions: [] };
      const multi = pairEvaluations.length > 1;
      const sameDeviceEvaluations = single ? [] : evaluations.filter((ev) => ev.same_device);
      const pairEvalLabel = (evaluation) => {
        if (!multi) return null;
        if (evaluation.step) {
          const { old: o, new: n } = evaluation.step;
          return `${o.node}:${o.port} -> ${n.node}:${n.port}`;
        }
        return evaluation.subject;
      };
      const aggregate = (evalList, labelFor) => {
        for (const evaluation of evalList) {
          const result = evaluation.result || {};
          const pairLabel = labelFor(evaluation);
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
      };
      aggregate(pairEvaluations, pairEvalLabel);
      aggregate(sameDeviceEvaluations, (evaluation) => `same-device ${evaluation.subject}`);
      this.mainEl.appendChild(
        el("div", { className: "subsection-title", text: `Nespárováno — ${unmatchedItems.length}` })
      );
      this.mainEl.appendChild(this.buildUnmatchedSection(unmatchedItems));
      const unassignedCount = Object.values(unassignedAgg).reduce((n, list) => n + list.length, 0);
      this.mainEl.appendChild(
        el("div", { className: "subsection-title", text: `Nezařazeno (jen subject) — ${unassignedCount}` })
      );
      this.mainEl.appendChild(this.buildUnassignedSection(unassignedAgg));
    }

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

    const footerButtons = [];
    if (!single) {
      footerButtons.push(
        el("button", {
          className: "btn btn-secondary",
          text: "Edit mapping",
          onClick: () => this.openEditMapping(),
        })
      );
    }
    footerButtons.push(
      el("button", {
        className: "btn btn-secondary",
        text: "Export JSON",
        onClick: () => this.exportJson(),
      }),
      el("button", {
        className: "btn btn-primary-green",
        text: "Evaluate run",
        onClick: () => this.evaluateRun(),
      })
    );
    const footer = el("div", { className: "footer-actions", children: footerButtons });
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

  buildPairingTable(rows, opts) {
    const single = !!(opts && opts.single);
    const cols = single ? " cols-single" : "";
    const table = el("div", { className: "pairing-table" });
    const headers = single
      ? ["Device", "Pre", "Post", "Rollback"]
      : ["Old port", "New port", "Pre", "Post", "Rollback"];
    table.appendChild(
      el("div", {
        className: "pairing-header-row" + cols,
        children: headers.map((text) => el("span", { text })),
      })
    );

    const task = this.cache.captureProgress;
    const endpointText = (ep) => (ep ? `${ep.node}:${ep.port || "all"}` : "not paired");
    for (const row of rows) {
      const rowMatches = this.rowMatchesCapture(row, task);
      const portCells = single
        ? [el("span", { className: "port-cell", text: endpointText(row.old || row.new) })]
        : [
            el("span", {
              className: "port-cell" + (row.old ? "" : " unpaired"),
              text: endpointText(row.old),
            }),
            el("span", {
              className: "port-cell" + (row.new ? "" : " unpaired"),
              text: endpointText(row.new),
            }),
          ];

      const rowEl = el("div", {
        className: "pairing-row" + cols,
        children: [
          ...portCells,
          this.buildFlagCell(row, "pre", task),
          this.buildFlagCell(row, "post", task),
          this.buildFlagCell(row, "rollback", task),
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
    }
    return table;
  }

  buildSameDeviceSection() {
    const evaluations = this.cache.evaluation
      ? this.cache.evaluation.evaluations
      : [];
    const sameDevice = evaluations.filter((ev) => ev.same_device);
    if (sameDevice.length === 0) return null;

    const meta = el("div", { className: "pairing-table" });
    meta.appendChild(
      el("div", {
        className: "pairing-header-row cols-same",
        children: [
          el("span", {}),
          el("span", { text: "Device" }),
          el("span", { text: "Pre taken" }),
          el("span", { text: "Post taken" }),
        ],
      })
    );
    for (const ev of sameDevice) {
      const subjectRecord = this.findSnapshotRecord(ev.subject);
      const baselineRecord = this.findSnapshotRecord(ev.baseline);
      const deviceLabel = subjectRecord
        ? `${subjectRecord.device}:${subjectRecord.port || "all"}`
        : ev.subject;
      meta.appendChild(
        el("div", {
          className: "pairing-row cols-same",
          children: [
            el("span", {}),
            el("span", { className: "port-cell", text: deviceLabel }),
            el("span", {
              className: "taken-cell",
              text: baselineRecord ? baselineRecord.taken || "" : "",
            }),
            el("span", {
              className: "taken-cell",
              text: subjectRecord ? subjectRecord.taken || "" : "",
            }),
          ],
        })
      );
    }

    const entries = this.collectServiceEntries(sameDevice, "same");
    const resultsTable = this.buildResultsTable(entries, { singlePort: true });

    return el("div", {
      className: "same-device-section",
      children: [
        el("div", {
          className: "subsection-title",
          text: "Same device — pre vs post",
        }),
        meta,
        resultsTable,
      ],
    });
  }

  // -- snapshot evaluation (screen 2) ------------------------------------

  phaseHeaderPillClass(phase) {
    if (phase === "pre") return "pill-phase-pre";
    if (phase === "post") return "pill-phase-post";
    if (phase === "rollback") return "pill-phase-rollback";
    return "pill-phase-pre";
  }

  renderSnapshotView() {
    clear(this.mainEl);
    this.mainEl.appendChild(this.buildBreadcrumb(this.state.selectedSnapshot, true));

    if (!this.cache.snapshotEval) {
      if (this.cache.snapshotEvalError) {
        this.mainEl.appendChild(
          el("div", {
            className: "notice notice-warn",
            text: this.cache.snapshotEvalError.detail,
          })
        );
      }
      return;
    }

    const { snapshot, result } = this.cache.snapshotEval;
    const phase = result.subject ? result.subject.phase : null;

    const headerChildren = [el("h1", { text: "Snapshot evaluation" })];
    if (phase) {
      headerChildren.push(
        el("span", {
          className: "header-pill " + this.phaseHeaderPillClass(phase),
          text: phase,
        })
      );
    }
    this.mainEl.appendChild(el("div", { className: "run-header", children: headerChildren }));

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
        ],
      })
    );

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
      el("div", {
        className: "subsection-title",
        text: `Nezařazeno (jen subject) — ${unassignedCount}`,
      })
    );
    this.mainEl.appendChild(this.buildUnassignedSection(unassigned));

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
    } else if (detail.kind === "single") {
      // run typu single je vzdy whole-box (bez port pole, port je null)
      alreadyCaptured = device ? this.captureAlreadyCaptured(device, null, form.phase) : false;
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

  async openNewRunForm() {
    if (!this.leaveGuard()) return;
    this.state.view = "newrun";
    this.state.selectedSnapshot = null;
    this.state.newRunForm = {
      name: "",
      kind: MigView.defaultRunKind(this.cache.runs),
      profile: "",
      touched: false,
      // The single-device sub-form edits `old` too, so switching kind in
      // either direction keeps what was typed (spec §4).
      old: { node: "", host: "", platform: "junos" },
      new: { node: "", host: "", platform: "junos-evo" },
      mappings: [],
      submitting: false,
      submitError: null,
    };
    this.render();
    await this.loadProfiles();
    this.render();
  }

  setNewRunKind(kind) {
    const form = this.state.newRunForm;
    if (!form || form.submitting) return;
    const type = RUN_TYPES.find((t) => t.kind === kind);
    if (!type || type.disabled) return;
    form.kind = kind;
    form.submitError = null;
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
    const single = form.kind === "single";
    const nameErr = this.newRunNameError();
    const dup = single ? new Set() : this.mappingDupErrors(form.mappings);
    const deviceOk = (d) => d.node.trim() && d.host.trim();
    const devicesOk = single ? deviceOk(form.old) : deviceOk(form.old) && deviceOk(form.new);
    const mappingsOk = single || form.mappings.every((r) => r.old.trim() && r.new.trim());
    if (nameErr || !devicesOk || dup.size || !mappingsOk) {
      this.render();
      return;
    }
    const trimDevice = (d, role) => ({
      node: d.node.trim(),
      host: d.host.trim(),
      platform: d.platform,
      role,
    });
    const devices = single
      ? [trimDevice(form.old, "single")]
      : [trimDevice(form.old, "old"), trimDevice(form.new, "new")];
    const mappings = single ? [] : form.mappings.map((r) => [r.old.trim(), r.new.trim()]);
    form.submitting = true;
    form.submitError = null;
    this.render();
    try {
      const res = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name.trim(),
          kind: form.kind,
          profile: form.profile || null,
          devices,
          mappings,
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
        this.state.openResults = {};
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

  buildRunTypeCards(form) {
    const cards = el("div", { className: "type-cards", attrs: { role: "radiogroup" } });
    for (const type of RUN_TYPES) {
      const on = form.kind === type.kind;
      const children = [
        el("span", { className: "type-card-title", text: type.title }),
        el("span", { className: "type-card-desc", text: type.desc }),
      ];
      if (type.note) children.push(el("span", { className: "type-card-note", text: type.note }));
      const attrs = { type: "button", role: "radio", "aria-checked": on ? "true" : "false" };
      if (type.disabled) attrs.disabled = "disabled";
      cards.appendChild(
        el("button", {
          className: "type-card" + (on ? " on" : "") + (type.disabled ? " disabled" : ""),
          attrs,
          children,
          onClick: type.disabled ? null : () => this.setNewRunKind(type.kind),
        })
      );
    }
    return cards;
  }

  buildProfilePicker(form) {
    const store = this.cache.profiles;
    const select = el("select", { className: "form-select mono" });
    select.appendChild(el("option", { text: "(default)", attrs: { value: "" } }));
    for (const entry of (store && store.profiles) || []) {
      select.appendChild(el("option", { text: entry.name, attrs: { value: entry.name } }));
    }
    select.value = form.profile || "";
    select.addEventListener("change", (e) => {
      form.profile = e.target.value;
    });
    const children = [select];
    if (this.cache.profilesError) {
      children.push(el("div", { className: "field-error", text: this.cache.profilesError.detail }));
    }
    return this.buildCaptureField("Profile", el("div", { children }));
  }

  renderNewRunForm() {
    clear(this.mainEl);
    const form = this.state.newRunForm;
    if (!form) return;
    const single = form.kind === "single";

    this.mainEl.appendChild(
      el("div", {
        className: "breadcrumb",
        children: [el("span", { className: "crumb-current", text: "new run" })],
      })
    );
    this.mainEl.appendChild(el("h1", { className: "capture-h1", text: "New run" }));

    this.mainEl.appendChild(
      el("div", {
        className: "form-card",
        children: [
          el("div", { className: "form-section-label", text: "Run type" }),
          this.buildRunTypeCards(form),
        ],
      })
    );

    const nameInput = el("input", {
      className: "form-input mono",
      attrs: { type: "text", placeholder: single ? "e.g. upgrade-ptx1" : "e.g. mig01" },
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
          el("div", { className: "form-section-label", text: "Run" }),
          el("div", {
            className: "name-profile-grid",
            children: [
              el("div", { className: "form-field", children: nameFieldChildren }),
              this.buildProfilePicker(form),
            ],
          }),
        ],
      })
    );

    const devicesGrid = single
      ? el("div", {
          className: "devices-grid single",
          children: [this.buildDeviceSubform("Device", form.old, form.touched)],
        })
      : el("div", {
          className: "devices-grid",
          children: [
            this.buildDeviceSubform("Old device", form.old, form.touched),
            this.buildDeviceSubform("New device", form.new, form.touched),
          ],
        });
    this.mainEl.appendChild(
      el("div", {
        className: "form-card",
        children: [
          el("div", { className: "form-section-label", text: single ? "Device" : "Devices" }),
          devicesGrid,
        ],
      })
    );

    if (!single) {
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
    }

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

  // -- profile editor (spec 3) -------------------------------------------

  // Chip picker: chosen values as chips with ×, a native <select> as the
  // "+ add" menu (no positioning code). Empty = all, shown as "(vsechny)".
  buildChipPicker(label, values, options, onChange, readonly) {
    const chips = el("div", { className: "chips" });
    const chosen = Array.isArray(values) ? values : [];
    if (chosen.length === 0) chips.appendChild(el("span", { className: "chip-empty", text: "(vsechny)" }));
    for (const value of chosen) {
      const chip = el("span", { className: "chip mono", text: value });
      if (!readonly) {
        chip.appendChild(
          el("button", {
            className: "chip-x",
            text: "×",
            attrs: { type: "button", title: `odebrat ${value}` },
            onClick: () => onChange(MigView.toggleListValue(chosen, value)),
          })
        );
      }
      chips.appendChild(chip);
    }
    if (!readonly) {
      const add = el("select", { className: "chip-add mono" });
      add.appendChild(el("option", { text: "+ add", attrs: { value: "" } }));
      for (const option of options.filter((o) => !chosen.includes(o))) {
        add.appendChild(el("option", { text: option, attrs: { value: option } }));
      }
      add.addEventListener("change", (e) => {
        if (e.target.value) onChange(MigView.toggleListValue(chosen, e.target.value));
      });
      chips.appendChild(add);
    }
    return el("div", {
      className: "form-field",
      children: [
        el("label", { className: "field-label", children: [
          document.createTextNode(label + " "),
          el("span", { className: "field-hint", text: "(empty = all)" }),
        ] }),
        chips,
      ],
    });
  }

  buildProfileSectionForm(editor, readonly) {
    const catalogue = this.cache.catalogue || { collectors: {}, service_types: [], ping_count_default: 5 };
    const collectorOptions = [...new Set(Object.values(catalogue.collectors).flat())].sort();
    const section = editor.doc.profile || (editor.doc.profile = { collectors: null, service_types: null, ping_count: null });
    const set = (key, value) => {
      section[key] = value;
      this.render();
    };

    const ping = el("input", {
      className: "form-input mono ping-input",
      attrs: { type: "number", min: "1", step: "1", placeholder: `${catalogue.ping_count_default} (default)` },
    });
    ping.value = section.ping_count === null || section.ping_count === undefined ? "" : String(section.ping_count);
    if (readonly) ping.setAttribute("disabled", "disabled");
    ping.addEventListener("change", (e) => {
      const raw = e.target.value.trim();
      set("ping_count", raw === "" ? null : Number(raw));
    });

    return el("div", {
      className: "form-card",
      children: [
        el("div", { className: "form-section-label", text: "Profile" }),
        el("div", {
          className: "profile-grid",
          children: [
            this.buildChipPicker("Collectors", section.collectors, collectorOptions,
              (v) => set("collectors", v), readonly),
            this.buildChipPicker("Service types", section.service_types, catalogue.service_types,
              (v) => set("service_types", v), readonly),
          ],
        }),
        this.buildCaptureField("Ping count", ping),
      ],
    });
  }

  buildProfileToolbar(editor) {
    const store = this.cache.profiles || { default: null, profiles: [] };
    const select = el("select", { className: "form-select mono" });
    select.appendChild(el("option", { text: "(default)", attrs: { value: "" } }));
    for (const entry of store.profiles) {
      select.appendChild(el("option", { text: entry.name, attrs: { value: entry.name } }));
    }
    select.value = editor.name || "";
    select.addEventListener("change", (e) => {
      const next = e.target.value || null;
      if (!this.leaveGuard()) {
        e.target.value = editor.name || "";
        return;
      }
      this.state.profileEditor = null;
      this.goToProfiles(next);
    });
    const current = store.profiles.find((p) => p.name === editor.name);
    const usedBy = current ? current.used_by : 0;
    const isDefault = editor.name === null;
    const deleteBtn = el("button", {
      className: "btn btn-danger-secondary",
      text: "Delete",
      attrs: { type: "button" },
      onClick: () => this.deleteProfile(),
    });
    if (isDefault || usedBy > 0) {
      deleteBtn.setAttribute("disabled", "disabled");
      deleteBtn.setAttribute("title", isDefault ? "(default) se nemaže" : `pouziva ${usedBy} runu`);
    }
    return el("div", {
      className: "profile-toolbar",
      children: [
        el("span", { className: "field-label", text: "Profile" }),
        select,
        isDefault ? el("span", { className: "kind-tag", text: "read-only" }) : null,
        el("button", { className: "btn btn-secondary", text: "+ New profile", attrs: { type: "button" },
          onClick: () => this.newProfile(null) }),
        el("button", { className: "btn btn-secondary", text: "Duplicate", attrs: { type: "button" },
          onClick: () => this.newProfile(editor.doc) }),
        deleteBtn,
        el("span", { className: "profile-usage", text: isDefault ? "" : `pouziva ${usedBy} runu` }),
      ],
    });
  }

  // Registered checks, read-only. Spec 4 replaces this block with the
  // editable checks table; until then it is the old registry listing.
  buildChecksRegistry() {
    if (!this.cache.checks) {
      if (this.cache.checksError) {
        return el("div", { className: "notice notice-warn", text: this.cache.checksError.detail });
      }
      return el("div");
    }
    const checks = this.cache.checks.checks || [];
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
    return el("div", {
      className: "form-card",
      children: [
        el("div", {
          className: "run-header",
          children: [
            el("h1", { text: "Registered checks" }),
            el("span", { className: "subtitle", text: `${checks.length} checks` }),
          ],
        }),
        table,
      ],
    });
  }

  renderProfilesView() {
    clear(this.mainEl);
    this.mainEl.appendChild(this.buildBreadcrumb("profiles", false));
    const editor = this.state.profileEditor;
    if (!editor) return;
    this.mainEl.appendChild(
      el("div", {
        className: "run-header",
        children: [
          el("h1", { text: "Profiles" }),
          el("span", { className: "subtitle", text: "profiles/<name>.yml · run picks one" }),
        ],
      })
    );
    if (this.cache.profilesError) {
      this.mainEl.appendChild(el("div", { className: "notice notice-warn", text: this.cache.profilesError.detail }));
    }
    if (this.cache.catalogueError) {
      this.mainEl.appendChild(el("div", { className: "notice notice-warn", text: this.cache.catalogueError }));
    }
    this.mainEl.appendChild(this.buildProfileToolbar(editor));
    if (editor.loadError) {
      this.mainEl.appendChild(el("div", { className: "notice notice-warn", text: editor.loadError }));
      return;
    }
    if (!editor.doc) return; // still loading

    const readonly = editor.name === null;
    this.mainEl.appendChild(this.buildProfileSectionForm(editor, readonly));
    this.mainEl.appendChild(this.buildChecksRegistry());

    const footerChildren = [];
    if (editor.error) footerChildren.push(el("div", { className: "field-error", text: editor.error }));
    if (!readonly) {
      const dirty = this.editorDirty();
      const enabled = dirty && !editor.saving;
      const discardBtn = el("button", { className: "btn btn-secondary", text: "Discard",
        attrs: { type: "button" }, onClick: enabled ? () => this.discardProfile() : null });
      const saveBtn = el("button", { className: "btn btn-primary",
        text: editor.saving ? "Saving…" : "Save profile",
        attrs: { type: "button" }, onClick: enabled ? () => this.saveProfile() : null });
      if (!enabled) {
        discardBtn.setAttribute("disabled", "disabled");
        saveBtn.setAttribute("disabled", "disabled");
      }
      footerChildren.push(el("div", { className: "footer-actions", children: [discardBtn, saveBtn] }));
    }
    this.mainEl.appendChild(el("div", { className: "profile-footer", children: footerChildren }));
  }
}

document.addEventListener("DOMContentLoaded", () => new App().boot());
