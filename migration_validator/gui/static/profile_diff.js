/* Checks table diff (spec 4). Form state holds the effective value of every
   catalogue check; the document holds only what differs from the catalogue
   defaults. checksDocument is the single place that decides what is written -
   the server mirrors it in profiles/store.py strip_check_defaults. */

const SEVERITIES = ["critical", "advisory"];

/* Helper text under option fields. The registry has no description field,
   so the sign convention lives here (see docs/cs/reference.md, config). */
const OPTION_HINTS = {
  tolerance_percent: "záporné = povolený pokles v % proti baseline",
  tolerance_db: "povolený posun RX/TX v dB proti baseline",
  max_residual_pps: "zbytkový provoz v pps, který ještě znamená 'utichlo'",
  count: "počet pingů na jednu adresu",
  require_nonzero: "nulový provoz je nález i bez baseline",
};

function defaultRow(entry) {
  const options = {};
  for (const [key, spec] of Object.entries(entry.options || {})) options[key] = spec.default;
  return { enabled: !!entry.default_enabled, severity: entry.default_severity, options, extra: {} };
}

function checksForm(catalogue, overrides) {
  const form = { checks: {}, unknown: {} };
  const known = new Set();
  for (const entry of catalogue.checks || []) {
    known.add(entry.id);
    const row = defaultRow(entry);
    for (const [key, value] of Object.entries((overrides && overrides[entry.id]) || {})) {
      if (value === null || value === undefined) continue;
      if (key === "enabled") row.enabled = !!value;
      else if (key === "severity") row.severity = value;
      else if (key in row.options) row.options[key] = value;
      else row.extra[key] = value;
    }
    form.checks[entry.id] = row;
  }
  for (const [id, value] of Object.entries(overrides || {})) {
    if (!known.has(id) && value && Object.keys(value).length) form.unknown[id] = { ...value };
  }
  return form;
}

/* "" in a numeric field means "back to default". Numbers compare by value
   so 2 and 2.0 are equal. */
function optionDiffers(spec, value) {
  if (value === "" || value === null || value === undefined) return false;
  if (spec.type === "number") return Number(value) !== Number(spec.default);
  return value !== spec.default;
}

function rowOverrides(entry, row) {
  const out = {};
  if (row.enabled !== !!entry.default_enabled) out.enabled = row.enabled;
  if (row.severity !== entry.default_severity) out.severity = row.severity;
  for (const [key, spec] of Object.entries(entry.options || {})) {
    const value = row.options[key];
    if (optionDiffers(spec, value)) out[key] = spec.type === "number" ? Number(value) : value;
  }
  for (const [key, value] of Object.entries(row.extra || {})) out[key] = value;
  return out;
}

function isOverride(entry, row) {
  return Object.keys(rowOverrides(entry, row)).length > 0;
}

function checksDocument(catalogue, form) {
  const out = {};
  for (const entry of catalogue.checks || []) {
    const row = form.checks[entry.id];
    if (!row) continue;
    const overrides = rowOverrides(entry, row);
    if (Object.keys(overrides).length) out[entry.id] = overrides;
  }
  for (const [id, value] of Object.entries(form.unknown || {})) {
    if (value && Object.keys(value).length) out[id] = { ...value };
  }
  return out;
}

function overrideCount(catalogue, form) {
  return Object.keys(checksDocument(catalogue, form)).length;
}

function groupChecks(catalogue) {
  const groups = [];
  const byName = new Map();
  for (const entry of catalogue.checks || []) {
    let group = byName.get(entry.group);
    if (!group) {
      group = { group: entry.group, checks: [] };
      byName.set(entry.group, group);
      groups.push(group);
    }
    group.checks.push(entry);
  }
  return groups;
}

/* Loader messages quote the check id and the option key in single quotes;
   the longest catalogue id contained in the message wins (interface_state
   vs interface_state_x). */
function errorTarget(message, catalogue) {
  if (!message) return null;
  let entry = null;
  for (const candidate of catalogue.checks || []) {
    if (message.includes(`'${candidate.id}'`) && (!entry || candidate.id.length > entry.id.length)) entry = candidate;
  }
  if (!entry) return null;
  let key = null;
  for (const candidate of Object.keys(entry.options || {})) {
    if (message.includes(`'${candidate}'`)) key = candidate;
  }
  return { checkId: entry.id, key };
}

const MigDiff = {
  SEVERITIES,
  OPTION_HINTS,
  defaultRow,
  checksForm,
  isOverride,
  checksDocument,
  overrideCount,
  groupChecks,
  errorTarget,
};

if (typeof module !== "undefined" && module.exports) module.exports = MigDiff;
