import json

from migration_validator.models.result import (
    CheckResult,
    MatchInfo,
    RunResult,
    ScopeResult,
    Severity,
    Status,
)
from migration_validator.reporting.json_report import to_json


def _result_with_nonfinite_subject() -> RunResult:
    check = CheckResult(
        id="interface_optics_levels",
        mode="both",
        status=Status.FAIL,
        severity=Severity.CRITICAL,
        message="et-0/0/5: RX bez signalu",
        label="Interface optical levels (et-0/0/5 lane 0)",
        value="RX -Inf dBm / TX -Inf dBm",
        subject={"rx_power_dbm": float("-inf"), "tx_power_dbm": float("-inf")},
    )
    scope = ScopeResult(
        scope_id="l1:et-0/0/5",
        key={"name": "L1;et-0/0/5"},
        status=Status.FAIL,
        match=MatchInfo(status="matched"),
        checks=[check],
    )
    return RunResult(
        evaluated_at="2026-08-13T00:00:00Z",
        subject={"address": "172.20.20.4"},
        baseline=None,
        summary={},
        scopes=[scope],
    )


def test_to_json_emits_strict_json_without_infinity_token():
    """json.dumps s vychozim allow_nan vypise '-Infinity' - neplatny JSON,
    ktery jq i striktni parsery odmitnou. Nekonecno se serializuje jako
    string token '-Inf'."""
    text = to_json(_result_with_nonfinite_subject())
    assert "-Infinity" not in text

    def _reject(token):
        raise AssertionError(f"nestriktni JSON token: {token}")

    payload = json.loads(text, parse_constant=_reject)
    subject = payload["scopes"][0]["checks"][0]["subject"]
    assert subject["rx_power_dbm"] == "-Inf"
