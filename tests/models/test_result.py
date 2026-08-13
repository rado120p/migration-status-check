import pytest

from migration_validator.models.result import (
    CheckResult,
    Finding,
    Outcome,
    RunResult,
    ScopeResult,
    Severity,
    Status,
    count_statuses,
    derive_status,
)


@pytest.mark.parametrize(
    "outcome,severity,expected",
    [
        (Outcome.OK, Severity.CRITICAL, Status.PASS),
        (Outcome.OK, Severity.ADVISORY, Status.PASS),
        (Outcome.DEGRADED, Severity.CRITICAL, Status.WARN),
        (Outcome.DEGRADED, Severity.ADVISORY, Status.WARN),
        (Outcome.BROKEN, Severity.CRITICAL, Status.FAIL),
        (Outcome.BROKEN, Severity.ADVISORY, Status.WARN),
        (Outcome.SKIP, Severity.CRITICAL, Status.SKIP),
        (Outcome.SKIP, Severity.ADVISORY, Status.SKIP),
    ],
)
def test_derive_status(outcome, severity, expected):
    assert derive_status(outcome, severity) is expected


def test_degraded_is_warn_even_when_critical():
    """Castecny uspech je vzdy WARN - pravidlo je v jednom miste, ne v checcich."""
    assert derive_status(Outcome.DEGRADED, Severity.CRITICAL) is Status.WARN


def test_status_worst_ranks_skip_above_pass():
    assert Status.worst([Status.PASS, Status.SKIP]) is Status.SKIP
    assert Status.worst([Status.PASS, Status.WARN, Status.SKIP]) is Status.WARN
    assert Status.worst([Status.WARN, Status.FAIL]) is Status.FAIL
    assert Status.worst([]) is Status.SKIP


def test_finding_defaults():
    finding = Finding(outcome=Outcome.OK, message="vse ok")
    assert finding.label is None
    assert finding.details == {}


def test_finding_carries_presentation_fields():
    finding = Finding(
        Outcome.OK,
        "rozhrani je up/up",
        label="Interface admin status",
        family=4,
        value="Up",
        baseline_value="Up",
        delta=None,
    )

    assert finding.family == 4
    assert finding.value == "Up"
    assert finding.baseline_value == "Up"
    assert finding.delta is None


def test_check_result_omits_empty_presentation_fields():
    result = CheckResult(
        id="interface_state",
        mode="state",
        status=Status.PASS,
        severity=Severity.CRITICAL,
        message="up/up",
    )

    payload = result.to_dict()

    assert "family" not in payload
    assert "value" not in payload
    assert "baseline_value" not in payload
    assert "delta" not in payload


def test_check_result_serialises_presentation_fields():
    result = CheckResult(
        id="interface_traffic",
        mode="both",
        status=Status.PASS,
        severity=Severity.ADVISORY,
        message="v toleranci",
        family=6,
        value="460 pps",
        baseline_value="520 pps",
        delta="-12 %",
    )

    payload = result.to_dict()

    assert payload["family"] == 6
    assert payload["value"] == "460 pps"
    assert payload["baseline_value"] == "520 pps"
    assert payload["delta"] == "-12 %"


def test_scope_result_serialises_identity():
    scope = ScopeResult(
        scope_id="svc:X:Internet",
        key={"description": "X", "service_type": "Internet"},
        status=Status.PASS,
        match=None,
        identity={
            "description": "X",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "ipv4": ["152.11.13.1/30"],
            "ipv6": [],
            "virtual_gw_v4": [],
            "virtual_gw_v6": [],
        },
    )

    assert scope.to_dict()["identity"]["ipv4"] == ["152.11.13.1/30"]


def test_info_outcome_derives_info_status_for_both_severities():
    assert derive_status(Outcome.INFO, Severity.CRITICAL) is Status.INFO
    assert derive_status(Outcome.INFO, Severity.ADVISORY) is Status.INFO


def test_info_never_wins_worst():
    # INFO radek nesmi zhorsit (ani "vylepsit") stav sluzby.
    assert Status.worst([Status.PASS, Status.INFO]) is Status.PASS
    assert Status.worst([Status.INFO, Status.FAIL]) is Status.FAIL


def test_count_statuses_counts_info_separately():
    counts = count_statuses([Status.PASS, Status.INFO, Status.INFO])
    assert counts["pass"] == 1
    assert counts["info"] == 2


def _run_result(**kwargs) -> RunResult:
    defaults = dict(
        evaluated_at="2026-08-13T00:00:00Z",
        subject={"address": "172.20.20.4"},
        baseline=None,
        summary={},
    )
    defaults.update(kwargs)
    return RunResult(**defaults)


def test_run_result_to_dict_omits_profile_when_none():
    payload = _run_result().to_dict()
    assert "profile" not in payload


def test_run_result_to_dict_carries_profile_name():
    payload = _run_result(profile="core-only.yml").to_dict()
    assert payload["profile"] == "core-only.yml"
