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
};

if (typeof module !== "undefined" && module.exports) module.exports = MigRunResults;
