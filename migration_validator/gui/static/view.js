// Port of migration_validator/reporting/view.py - keep the two in lockstep.
// Strings are byte-identical to the CLI (ASCII) so outputs stay comparable.
"use strict";

const FAMILY_ORDER = [null, 4, 6];
const NO_BASELINE = "bez baseline";
const MERGED_LABEL = "Ostatni checky";

function changeText(row, hasBaseline) {
  if (!hasBaseline) return "";
  if (row.mode === "state") return "";
  if (row.baseline_value == null) {
    return row.status === "SKIP" ? "" : NO_BASELINE;
  }
  if (row.baseline_value === row.value) return "";
  if (row.delta) return `bylo ${row.baseline_value}   ${row.delta}`;
  return `bylo ${row.baseline_value}`;
}

function mergeDeactivationSkips(checks) {
  const marked = new Set(
    checks.filter((c) => (c.details || {}).skipped_because === "service_deactivated")
  );
  if (marked.size === 0) return checks;
  const kept = checks.filter((c) => !marked.has(c));
  return [
    ...kept,
    {
      id: "deactivation_skips",
      mode: "state",
      status: "SKIP",
      severity: "advisory",
      message: `${marked.size} dalsich checku preskoceno, sluzba je deaktivovana`,
      label: MERGED_LABEL,
      value: `${marked.size} preskoceno`,
    },
  ];
}

function checkRow(check, qualify) {
  let label = check.label || check.id;
  const address = (check.details || {}).address;
  if (qualify && address) label = `${label} (${address})`;
  return {
    status: check.status,
    label,
    value: check.value != null ? check.value : "-",
    baseline_value: check.baseline_value != null ? check.baseline_value : null,
    delta: check.delta != null ? check.delta : null,
    mode: check.mode,
  };
}

function worstMessage(scope) {
  if (scope.status === "PASS") return "";
  for (const status of ["FAIL", "WARN", "SKIP", "RECV"]) {
    for (const check of scope.checks || []) {
      if (check.status === status) return check.message;
    }
  }
  return "";
}

function buildView(scope, opts) {
  const detail = !!(opts && opts.detail);
  const identity = scope.identity || {};
  const addresses = { 4: identity.ipv4 || [], 6: identity.ipv6 || [] };
  const gateways = { 4: identity.virtual_gw_v4 || [], 6: identity.virtual_gw_v6 || [] };

  const sections = [];
  for (const family of FAMILY_ORDER) {
    let checks = (scope.checks || []).filter(
      (c) => (c.family != null ? c.family : null) === family
    );
    if (checks.length === 0) continue;
    if (!detail) checks = mergeDeactivationSkips(checks);
    const own = addresses[family] || [];
    const qualify = own.length > 1;
    const rows = [];
    const groups = [];
    const byTitle = new Map();
    for (const check of checks) {
      const row = checkRow(check, qualify);
      if (check.group == null) {
        rows.push(row);
        continue;
      }
      let group = byTitle.get(check.group);
      if (!group) {
        group = { title: check.group, rows: [] };
        byTitle.set(check.group, group);
        groups.push(group);
      }
      group.rows.push(row);
    }
    sections.push({
      family,
      addresses: own,
      virtual_gw: gateways[family] || [],
      rows,
      groups,
    });
  }

  const link = scope.link || null;
  const linkRole = link ? link.role : null;
  let linkNote = null;
  if (link) {
    if (linkRole === "l3") {
      const peers = link.peers || [];
      const parts = peers.map((p) => `${p.interface} v ${p.instance}`).join(", ");
      const blocks = peers.length > 1 ? "bloky nize" : "blok nize";
      linkNote = parts ? `L2 cast: ${parts} (${blocks})` : null;
    } else if (linkRole === "l2") {
      linkNote = `L3 cast: ${link.peer_interface} v ${link.peer_instance} (blok vyse)`;
    }
  }

  let serviceType = identity.service_type || "-";
  if (linkRole === "l2") serviceType = `${serviceType} (L2 cast)`;

  const match = scope.match || null;
  return {
    status: scope.status,
    description: identity.description || scope.scope_id,
    service_type: serviceType,
    routing_instance: identity.routing_instance || null,
    baseline_interfaces: match ? match.baseline_interfaces || [] : [],
    subject_interfaces: match
      ? match.subject_interfaces || []
      : identity.interfaces || [],
    worst_message: worstMessage(scope),
    sections,
    link_role: linkRole,
    link_note: linkNote,
  };
}

function countStatuses(statuses) {
  const counts = { pass: 0, recv: 0, warn: 0, fail: 0, skip: 0, info: 0 };
  for (const status of statuses) {
    const key = status.toLowerCase();
    if (Object.prototype.hasOwnProperty.call(counts, key)) counts[key] += 1;
  }
  return counts;
}

const UNASSIGNED_TITLES = [
  ["bgp_peers", "BGP peer"],
  ["static_routes", "Staticka routa"],
  ["bfd_sessions", "BFD session"],
];

function unassignedRow(kind, item) {
  if (kind === "bgp_peers") {
    return { identity: item.peer, detail: `RI ${item.routing_instance || "-"}` };
  }
  if (kind === "static_routes") {
    const hops = item.next_hop || [];
    const detail = hops.length
      ? `-> ${hops.join(", ")}`
      : `via ${(item.via && item.via.length ? item.via : ["-"]).join(", ")}`;
    const suffix = item.protocol === "aggregate" ? " (aggregate)" : "";
    return { identity: `${item.rib} ${item.prefix}${suffix}`, detail };
  }
  return {
    identity: item.peer,
    detail: `${item.interface || "-"}   ${item.state || "-"}`,
  };
}

/* Run combobox filter: case-insensitive substring on run name and device
   node names. Empty query returns the input array unchanged. */
function filterRuns(runs, query) {
  const needle = (query || "").trim().toLowerCase();
  if (!needle) return runs;
  return runs.filter((run) => {
    if ((run.name || "").toLowerCase().includes(needle)) return true;
    const nodes = Object.keys(run.devices || {});
    return nodes.some((node) => node.toLowerCase().includes(needle));
  });
}

/* Kind preselected on the New run form: the kind of the most recently
   created run (ISO UTC strings compare lexicographically), else migration. */
function defaultRunKind(runs) {
  let newest = null;
  for (const run of runs || []) {
    if (!run || typeof run.created !== "string") continue;
    if (newest === null || run.created > newest.created) newest = run;
  }
  if (newest === null) return "migration";
  return newest.kind === "single" ? "single" : "migration";
}

/* Profile document helpers (spec 3). The normalisation mirrors
   profiles/store.py document_to_yaml: null = "not set" and is dropped, an
   override dict with nothing left is dropped, an empty section is dropped. */
function emptyProfileDocument() {
  return { profile: { collectors: null, service_types: null, ping_count: null }, checks: {} };
}

function normalizeProfileDocument(doc) {
  const out = {};
  const section = {};
  for (const [key, value] of Object.entries((doc && doc.profile) || {})) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    section[key] = value;
  }
  if (Object.keys(section).length) out.profile = section;
  const checks = {};
  for (const [id, overrides] of Object.entries((doc && doc.checks) || {})) {
    const kept = {};
    for (const [key, value] of Object.entries(overrides || {})) {
      if (value !== null && value !== undefined) kept[key] = value;
    }
    if (Object.keys(kept).length) checks[id] = kept;
  }
  if (Object.keys(checks).length) out.checks = checks;
  return out;
}

function profileDirty(doc, savedDoc) {
  return JSON.stringify(normalizeProfileDocument(doc)) !== JSON.stringify(normalizeProfileDocument(savedDoc));
}

function toggleListValue(list, value) {
  const current = Array.isArray(list) ? list : [];
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
  return next.length ? next : null;
}

/* Run combobox entries: group headers first (sorted by name) with their
   members indented, then ungrouped runs. A query matching a group name keeps
   the whole group; otherwise filterRuns decides per run and a matching
   member keeps its header. */
function comboEntries(runs, query) {
  const needle = (query || "").trim().toLowerCase();
  const groups = new Map();
  const loose = [];
  for (const run of runs || []) {
    if (run.group) {
      if (!groups.has(run.group)) groups.set(run.group, []);
      groups.get(run.group).push(run);
    } else {
      loose.push(run);
    }
  }
  const out = [];
  for (const name of [...groups.keys()].sort()) {
    const members = groups.get(name).slice().sort((a, b) => a.name.localeCompare(b.name));
    const kept = needle && !name.toLowerCase().includes(needle) ? filterRuns(members, needle) : members;
    if (!kept.length) continue;
    out.push({ type: "group", name, count: members.length });
    for (const run of kept) out.push({ type: "run", run, grouped: true });
  }
  for (const run of filterRuns(loose.slice().sort((a, b) => a.name.localeCompare(b.name)), needle)) {
    out.push({ type: "run", run, grouped: false });
  }
  return out;
}

const VERDICT_ORDER = ["FAIL", "WARN", "RECV", "PASS", "SKIP", "INFO"];

function verdictRank(row) {
  if (row.error) return VERDICT_ORDER.length + 1;
  if (!row.verdict) return VERDICT_ORDER.length;
  const index = VERDICT_ORDER.indexOf(row.verdict);
  return index === -1 ? VERDICT_ORDER.length : index;
}

function groupRowOrder(rows) {
  return rows.slice().sort((a, b) => verdictRank(a) - verdictRank(b) || (a.node || "").localeCompare(b.node || ""));
}

function sortGroupRows(rows, key, dir) {
  if (!key) return groupRowOrder(rows);
  const sign = dir === "desc" ? -1 : 1;
  const value = (row) => {
    if (key === "verdict") return verdictRank(row);
    if (key === "pre" || key === "post" || key === "rollback") return (row.phases && row.phases[key]) || "";
    return row[key] || "";
  };
  return rows.slice().sort((a, b) => {
    const va = value(a), vb = value(b);
    const cmp = typeof va === "number" ? va - vb : String(va).localeCompare(String(vb));
    return sign * cmp || (a.node || "").localeCompare(b.node || "");
  });
}

/* Phase cell of the group table. taskState = {phase, state, error} from the
   client's polling map for that run, or null. */
function phaseCell(row, phase, taskState) {
  if (taskState && taskState.phase === phase) {
    if (taskState.state === "queued") return { kind: "queued", text: "queued", title: "" };
    if (taskState.state === "running") return { kind: "running", text: "running…", title: "" };
    if (taskState.state === "failed") return { kind: "error", text: "error", title: taskState.error || "" };
  }
  const taken = row.phases && row.phases[phase];
  if (!taken) return { kind: "none", text: "—", title: "" };
  const match = /T(\d{2}:\d{2})/.exec(taken);
  return { kind: "time", text: match ? match[1] : taken, title: taken };
}

const MigView = {
  FAMILY_ORDER,
  changeText,
  mergeDeactivationSkips,
  checkRow,
  worstMessage,
  buildView,
  countStatuses,
  UNASSIGNED_TITLES,
  unassignedRow,
  filterRuns,
  defaultRunKind,
  emptyProfileDocument,
  normalizeProfileDocument,
  profileDirty,
  toggleListValue,
  comboEntries,
  VERDICT_ORDER,
  groupRowOrder,
  sortGroupRows,
  phaseCell,
};

if (typeof module !== "undefined" && module.exports) module.exports = MigView;
