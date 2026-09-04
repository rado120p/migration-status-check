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
};

if (typeof module !== "undefined" && module.exports) module.exports = MigView;
