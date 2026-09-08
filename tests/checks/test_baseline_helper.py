"""Jedina brana pro Outcome.UNCHANGED (R-3, spec 2026-09-08)."""
from migration_validator.checks.base import CheckContext
from migration_validator.checks.baseline import suffix, unchanged_or
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _ctx(baseline, collectors=None):
    scope = Scope(id="svc:x:Core", kind="service", key=ScopeKey("x", "Core", "transit"),
                  selectors=Selectors(interfaces=["ge-0/0/1.0"]))
    if collectors is None and baseline is not None:
        collectors = {area: {"status": "ok"} for area in baseline}
    return CheckContext(scope=scope, subject={}, baseline=baseline, config=default_config(),
                        baseline_collectors=collectors or {})


def test_broken_with_same_measured_baseline_is_unchanged():
    assert unchanged_or(Outcome.BROKEN, _ctx({"ldp_neighbor": {}}), "ldp_neighbor", True) is Outcome.UNCHANGED
    assert unchanged_or(Outcome.DEGRADED, _ctx({"ldp_neighbor": {}}), "ldp_neighbor", True) is Outcome.UNCHANGED


def test_not_same_or_no_baseline_or_failed_collector_keeps_outcome():
    assert unchanged_or(Outcome.BROKEN, _ctx({"ldp_neighbor": {}}), "ldp_neighbor", False) is Outcome.BROKEN
    assert unchanged_or(Outcome.BROKEN, _ctx(None), "ldp_neighbor", True) is Outcome.BROKEN
    failed = _ctx({"ldp_neighbor": {}}, collectors={"ldp_neighbor": {"status": "error", "message": "RpcError"}})
    assert unchanged_or(Outcome.BROKEN, failed, "ldp_neighbor", True) is Outcome.BROKEN
    unrecorded = _ctx({"ldp_neighbor": {}}, collectors={})
    assert unchanged_or(Outcome.BROKEN, unrecorded, "ldp_neighbor", True) is Outcome.BROKEN


def test_ok_and_skip_pass_through():
    assert unchanged_or(Outcome.OK, _ctx({"x": {}}), "x", True) is Outcome.OK
    assert unchanged_or(Outcome.SKIP, _ctx({"x": {}}), "x", True) is Outcome.SKIP


def test_suffix():
    assert suffix(Outcome.UNCHANGED) == ", stejne jako v baseline"
    assert suffix(Outcome.BROKEN) == ""
