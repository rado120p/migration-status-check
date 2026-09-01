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

class App {
  constructor() {
    this.state = {
      view: "run",
      run: null,
      selectedSnapshot: null,
      openRows: {},
      openScopes: {},
      activeCaptureId: null,
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
    };

    this.sidebarRunsEl = document.getElementById("sidebar-runs");
    this.sidebarSnapshotsEl = document.getElementById("sidebar-snapshots");
    this.sidebarFooterEl = document.getElementById("sidebar-footer");
    this.mainEl = document.getElementById("main");
    this.btnChecksEl = document.getElementById("btn-checks");

    this.btnChecksEl.addEventListener("click", () => this.goToChecks());
    document.getElementById("btn-new-capture").addEventListener("click", () => {
      // wired in a later task - inert stub for now.
    });
    const newRunLink = document.getElementById("btn-new-run");
    if (newRunLink) {
      newRunLink.addEventListener("click", (e) => {
        e.preventDefault();
        // wired in a later task - inert stub for now.
      });
    }
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
    this.render();
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
    await this.loadRun();
    this.render();
  }

  async selectSnapshot(file) {
    this.state.view = "snapshot";
    this.state.selectedSnapshot = file;
    this.state.openScopes = {};
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
    this.renderSidebar();
    this.btnChecksEl.classList.toggle("btn-toggle-active", this.state.view === "checks");
    if (this.state.view === "empty") {
      this.renderEmptyState();
      return;
    }
    if (this.state.view === "snapshot") {
      this.renderSnapshotView();
      return;
    }
    if (this.state.view === "checks") {
      this.renderChecksView();
      return;
    }
    this.renderRunOverview();
  }

  renderEmptyState() {
    clear(this.mainEl);
    this.mainEl.appendChild(
      el("div", {
        className: "empty-state",
        children: [
          el("span", { text: "no runs yet" }),
          el("button", { className: "btn btn-primary", text: "Create your first run" }),
        ],
      })
    );
  }

  renderSidebar() {
    clear(this.sidebarRunsEl);
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

  findEvaluation(row) {
    const evaluations = this.cache.evaluation ? this.cache.evaluation.evaluations : [];
    return evaluations.find((ev) => {
      if (!ev.step) return false;
      return (
        this.portsEqual(ev.step.old, row.old) && this.portsEqual(ev.step.new, row.new)
      );
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

    this.mainEl.appendChild(this.buildPairingTable(detail));

    const footer = el("div", {
      className: "footer-actions",
      children: [
        el("button", {
          className: "btn btn-secondary",
          text: "Edit mapping",
          onClick: () => {
            // wired in a later task
          },
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

  buildPairingTable(detail) {
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

    for (const row of detail.rows) {
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
          el("span", {
            className: "flag-cell " + (row.pre ? "on" : "off"),
            text: row.pre ? "✓" : "—",
          }),
          el("span", {
            className: "flag-cell " + (row.post ? "on" : "off"),
            text: row.post ? "✓" : "—",
          }),
          el("span", {
            className: "flag-cell " + (row.rollback ? "on" : "off"),
            text: row.rollback ? "✓" : "—",
          }),
          el("span", {
            className: "col-result",
            children: [el("span", { className: "status-pill " + pillClass, text: pillLabel })],
          }),
        ],
      });
      table.appendChild(rowEl);

      if (open) {
        table.appendChild(this.buildScopesPanel(presentation.scopes, key));
      }
    }
    return table;
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
    headerChildren.push(
      el("span", { className: "header-pill pill-no-baseline", text: "no baseline" })
    );
    this.mainEl.appendChild(el("div", { className: "run-header", children: headerChildren }));

    this.mainEl.appendChild(
      el("div", {
        className: "meta-bar",
        children: [
          el("span", {
            children: [
              document.createTextNode("Device "),
              el("span", { className: "value", text: snapshot.device }),
            ],
          }),
          el("span", {
            children: [
              document.createTextNode("Platform "),
              el("span", { className: "value", text: snapshot.platform }),
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
            text: "standalone evaluation — comparison checks skipped",
          }),
        ],
      })
    );

    this.mainEl.appendChild(
      this.buildScopesPanel(result.scopes || [], "snapshot", {
        noBaseline: true,
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
