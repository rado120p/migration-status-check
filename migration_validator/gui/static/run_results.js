// Pure view-model for the mapped-migration run overview: groups the
// /api/runs/{run} rows and /api/runs/{run}/evaluation payload by old -> new
// port pairing. No DOM, no fetch; app.js renders what comes out of here.
// Payloads are treated as immutable - never assign into them.
"use strict";

const SERVICE_TYPE_ORDER = ["Internet", "IPVPN", "E-Line", "E-LAN", "Core"];
const ALL_TYPES = "All";
const UNKNOWN_TYPE = "Unknown";
const LAYER1_TYPE = "Layer1";
const DEVICE_SCOPE_ID = "device";
const STATUS_KEYS = ["pass", "recv", "warn", "fail", "skip", "info"];

function classifyScope(scope) {
  if (scope.scope_id === DEVICE_SCOPE_ID) return "device";
  const identity = scope.identity || {};
  const key = scope.key || {};
  if (identity.service_type === LAYER1_TYPE || key.service_type === LAYER1_TYPE) return "layer1";
  return "service";
}

function rawServiceType(scope) {
  const identity = scope.identity || {};
  const key = scope.key || {};
  return identity.service_type || key.service_type || UNKNOWN_TYPE;
}

function pairingKey(runName, step) {
  return JSON.stringify([runName, step.old.node, step.old.port, step.new.node, step.new.port]);
}

function sameEndpoint(a, b) {
  return !!a && !!b && a.node === b.node && (a.port ?? null) === (b.port ?? null);
}

function findRecord(snapshots, file) {
  if (!file) return null;
  return (snapshots || []).find((s) => s.file === file) || null;
}

function evaluationLabel(evaluation) {
  if (evaluation.step) {
    const { old: o, new: n } = evaluation.step;
    return `${o.node}:${o.port} -> ${n.node}:${n.port}`;
  }
  return evaluation.subject;
}

function buildEvaluationModel(evaluation, index, snapshots, sectionKey) {
  const result = evaluation.result || {};
  const subjectRecord = findRecord(snapshots, evaluation.subject);
  const baselineRecord = findRecord(snapshots, evaluation.baseline);
  const key = `${sectionKey}|${index}|${evaluation.subject}|${evaluation.baseline || ""}`;
  const serviceEntries = [];
  const infrastructureEntries = [];
  (result.scopes || []).forEach((scope) => {
    const kind = classifyScope(scope);
    const entry = {
      key: `${key}|${scope.scope_id}`,
      scope,
      kind,
      serviceType: kind === "service" ? rawServiceType(scope) : null,
    };
    (kind === "service" ? serviceEntries : infrastructureEntries).push(entry);
  });
  const unmatched = result.unmatched || {};
  return {
    key,
    label: evaluationLabel(evaluation),
    evaluation,
    subjectRecord,
    baselineRecord,
    warning: evaluation.warning || null,
    hasBaseline: result.baseline != null,
    wholeDeviceBaseline: !!(evaluation.baseline && baselineRecord && baselineRecord.port === null),
    baselineMetaMissing: !!(evaluation.baseline && !baselineRecord),
    subjectMetaMissing: !subjectRecord,
    serviceEntries,
    infrastructureEntries,
    unmatchedBaseline: unmatched.baseline || [],
    unmatchedSubject: unmatched.subject || [],
    excludedCount: (result.excluded_services || []).length,
    filtered: result.filtered || null,
  };
}

function buildPairingGroups({ runName, rows, evaluations, snapshots }) {
  const groups = [];
  const byKey = new Map();
  for (const row of rows || []) {
    if (!row.old || !row.new) continue;
    const key = pairingKey(runName, row);
    if (byKey.has(key)) continue;
    const group = {
      key,
      old: row.old,
      new: row.new,
      captureRow: row,
      sharedDestination: false,
      metadataMismatch: false,
      pending: true,
      evaluations: [],
    };
    groups.push(group);
    byKey.set(key, group);
  }
  const rollback = [];
  const other = [];
  const sameDevice = [];
  (evaluations || []).forEach((evaluation, index) => {
    if (evaluation.same_device === true) {
      sameDevice.push(buildEvaluationModel(evaluation, index, snapshots, "same"));
      return;
    }
    if (evaluation.step) {
      const key = pairingKey(runName, evaluation.step);
      let group = byKey.get(key);
      if (!group) {
        group = {
          key,
          old: evaluation.step.old,
          new: evaluation.step.new,
          captureRow: null,
          sharedDestination: false,
          metadataMismatch: true,
          pending: true,
          evaluations: [],
        };
        groups.push(group);
        byKey.set(key, group);
      }
      group.evaluations.push(buildEvaluationModel(evaluation, index, snapshots, "pair"));
      return;
    }
    const model = buildEvaluationModel(evaluation, index, snapshots, "aux");
    if (model.subjectRecord && model.subjectRecord.phase === "rollback") rollback.push(model);
    else other.push(model);
  });
  for (const group of groups) {
    group.sharedDestination = groups.some((g) => g !== group && sameEndpoint(g.new, group.new));
    group.pending = group.evaluations.length === 0;
  }
  return { groups, rollback, other, sameDevice };
}

function countStatuses(statuses) {
  const counts = { pass: 0, recv: 0, warn: 0, fail: 0, skip: 0, info: 0 };
  for (const status of statuses) {
    const key = String(status || "").toLowerCase();
    if (key in counts) counts[key] += 1;
  }
  return counts;
}

function linkedIds(scope) {
  const link = scope.link || null;
  if (!link) return [];
  const ids = [];
  if (link.peer_scope_id) ids.push(link.peer_scope_id);
  for (const peer of link.peers || []) if (peer && peer.scope_id) ids.push(peer.scope_id);
  return ids;
}

function filterServiceEntries(entries, selectedType) {
  if (selectedType === ALL_TYPES) {
    return {
      visible: entries.map((entry) => ({ entry, linkedContext: false })),
      matchedCount: entries.length,
      linkedContextCount: 0,
    };
  }
  const ids = new Set(entries.map((e) => e.scope.scope_id));
  const matched = new Set(
    entries.filter((e) => e.serviceType === selectedType).map((e) => e.scope.scope_id)
  );
  const context = new Set();
  for (const entry of entries) {
    const id = entry.scope.scope_id;
    if (matched.has(id)) {
      for (const pid of linkedIds(entry.scope)) if (ids.has(pid) && !matched.has(pid)) context.add(pid);
    } else if (linkedIds(entry.scope).some((pid) => matched.has(pid))) {
      context.add(id);
    }
  }
  const visible = entries
    .filter((e) => matched.has(e.scope.scope_id) || context.has(e.scope.scope_id))
    .map((entry) => ({ entry, linkedContext: context.has(entry.scope.scope_id) }));
  return { visible, matchedCount: matched.size, linkedContextCount: context.size };
}

function filterAcrossModels(models, selectedType) {
  let matchedCount = 0;
  let linkedContextCount = 0;
  let visibleCount = 0;
  const statuses = [];
  for (const model of models) {
    const shown = filterServiceEntries(model.serviceEntries, selectedType);
    matchedCount += shown.matchedCount;
    linkedContextCount += shown.linkedContextCount;
    visibleCount += shown.visible.length;
    for (const v of shown.visible) if (!v.linkedContext) statuses.push(v.entry.scope.status);
  }
  return { matchedCount, linkedContextCount, visibleCount, statuses };
}

function missingPartners(entry, entries) {
  const ids = new Set(entries.map((e) => e.scope.scope_id));
  return linkedIds(entry.scope).filter((id) => !ids.has(id));
}

function countByType(entries) {
  const counts = {};
  for (const entry of entries) {
    const type = entry.serviceType || UNKNOWN_TYPE;
    counts[type] = (counts[type] || 0) + 1;
  }
  return counts;
}

function serviceTypeChoices(catalogueTypes, entries) {
  const counts = countByType(entries);
  const ordered = [];
  const seen = new Set();
  const push = (type) => {
    if (seen.has(type)) return;
    seen.add(type);
    ordered.push({ type, count: counts[type] || 0 });
  };
  SERVICE_TYPE_ORDER.forEach(push);
  (catalogueTypes || []).slice().sort().forEach(push);
  Object.keys(counts).filter((t) => t !== UNKNOWN_TYPE).sort().forEach(push);
  if (counts[UNKNOWN_TYPE]) push(UNKNOWN_TYPE);
  return ordered;
}

function hiddenBadEntries(group, selectedType) {
  const bad = (status) => status === "WARN" || status === "FAIL";
  const hidden = [];
  for (const model of group.evaluations) {
    const shown = new Set(filterServiceEntries(model.serviceEntries, selectedType).visible.map((v) => v.entry));
    for (const entry of model.serviceEntries) if (!shown.has(entry) && bad(entry.scope.status)) hidden.push(entry);
  }
  return hidden;
}

function groupNeedsAttention(group, selectedType) {
  const bad = (status) => status === "WARN" || status === "FAIL";
  for (const model of group.evaluations) {
    if (model.infrastructureEntries.some((e) => bad(e.scope.status))) return true;
    if (model.unmatchedBaseline.length || model.unmatchedSubject.length) return true;
  }
  if (hiddenBadEntries(group, selectedType).length) return true;
  return false;
}

function mainEvaluationModels(model) {
  return [...model.groups.flatMap((g) => g.evaluations), ...model.rollback, ...model.other];
}

function unassignedIsEmpty(payload) {
  return !Object.values(payload || {}).some((list) => Array.isArray(list) && list.length > 0);
}

function collectUnassigned(models) {
  const bySubject = new Map();
  for (const model of models) {
    const payload = (model.evaluation.result || {}).unassigned || {};
    if (unassignedIsEmpty(payload)) continue;
    const serialized = JSON.stringify(payload);
    let entry = bySubject.get(model.evaluation.subject);
    if (!entry) {
      entry = { subject: model.evaluation.subject, record: model.subjectRecord, variants: [] };
      bySubject.set(model.evaluation.subject, entry);
    }
    let variant = entry.variants.find((v) => v.serialized === serialized);
    if (!variant) {
      variant = { serialized, payload, labels: [] };
      entry.variants.push(variant);
    }
    variant.labels.push(model.label);
  }
  return [...bySubject.values()];
}

const MigRunResults = {
  SERVICE_TYPE_ORDER,
  ALL_TYPES,
  UNKNOWN_TYPE,
  STATUS_KEYS,
  classifyScope,
  rawServiceType,
  pairingKey,
  evaluationLabel,
  buildEvaluationModel,
  buildPairingGroups,
  countStatuses,
  linkedIds,
  filterServiceEntries,
  filterAcrossModels,
  missingPartners,
  countByType,
  serviceTypeChoices,
  groupNeedsAttention,
  hiddenBadEntries,
  mainEvaluationModels,
  collectUnassigned,
};

if (typeof module !== "undefined" && module.exports) module.exports = MigRunResults;
