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
from migration_validator.reporting.text_report import filter_result, render


def _check(check_id, status, message):
    return CheckResult(
        id=check_id,
        mode="both",
        status=status,
        severity=Severity.ADVISORY,
        message=message,
    )


def _result() -> RunResult:
    return RunResult(
        evaluated_at="2026-07-24T11:40:02Z",
        subject={"address": "172.20.20.5", "phase": "post-migration", "captured_at": "x"},
        baseline={"address": "172.20.20.4", "phase": "pre-migration", "captured_at": "y"},
        summary={
            "pass": 3, "warn": 1, "fail": 1, "skip": 0,
            "scopes_matched": 2, "unmatched_baseline": 1, "unmatched_subject": 1,
        },
        scopes=[
            ScopeResult(
                scope_id="svc:INTERNET-CPE13-NNI:Internet",
                key={"description": "INTERNET-CPE13-NNI", "service_type": "Internet"},
                status=Status.PASS,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[_check("interface_state", Status.PASS, "up/up")],
            ),
            ScopeResult(
                scope_id="svc:L3VPN-CPE13-NNI:IPVPN",
                key={"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN"},
                status=Status.WARN,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[
                    _check("interface_traffic", Status.WARN, "provoz -72 % (410 -> 115 pps)"),
                    _check("interface_state", Status.PASS, "up/up"),
                ],
            ),
            ScopeResult(
                scope_id="svc:EVPN-VPWS-CPE13-NNI:E-Line",
                key={"description": "EVPN-VPWS-CPE13-NNI", "service_type": "E-Line"},
                status=Status.FAIL,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[_check("evpn_vpws_status", Status.FAIL, "vpws-sid-pe-status: Down")],
            ),
        ],
        unmatched={
            "baseline": [
                {
                    "scope_id": "svc:L3VPN-CPE99-NNI:IPVPN",
                    "description": "L3VPN-CPE99-NNI",
                    "service_type": "IPVPN",
                    "reason": "zadny kandidat na subject",
                }
            ],
            "subject": [
                {
                    "scope_id": "svc:EVPN-VLAN-AWARE-INTERNET:E-LAN",
                    "description": "EVPN-VLAN-AWARE-INTERNET",
                    "service_type": "E-LAN",
                    "reason": "nova sluzba, chybi baseline",
                }
            ],
        },
        unassigned={"bgp_peers": []},
    )


def test_render_contains_header_with_both_devices():
    output = render(_result())
    assert "172.20.20.4" in output and "172.20.20.5" in output
    assert "pre-migration" in output and "post-migration" in output


def test_render_contains_summary_counts():
    output = render(_result())
    assert "3 PASS" in output
    assert "1 WARN" in output
    assert "1 FAIL" in output
    assert "Sparovano 2" in output


def test_render_lists_services_with_worst_check_message():
    output = render(_result())
    assert "L3VPN-CPE13-NNI" in output
    assert "provoz -72 %" in output
    assert "vpws-sid-pe-status: Down" in output


def test_render_always_shows_unmatched_section():
    output = render(_result())
    assert "NESPAROVANO" in output
    assert "L3VPN-CPE99-NNI" in output
    assert "EVPN-VLAN-AWARE-INTERNET" in output


def test_unmatched_section_present_even_when_all_green():
    result = _result()
    for scope in result.scopes:
        scope.status = Status.PASS
    assert "NESPAROVANO" in render(result)


def test_filter_by_text_matches_description():
    filtered = filter_result(_result(), text="L3VPN")
    assert [scope.scope_id for scope in filtered.scopes] == ["svc:L3VPN-CPE13-NNI:IPVPN"]


def test_filter_by_status_keeps_only_requested():
    filtered = filter_result(_result(), statuses={Status.FAIL, Status.WARN})
    assert {scope.status for scope in filtered.scopes} == {Status.WARN, Status.FAIL}


def test_filter_does_not_touch_unmatched():
    filtered = filter_result(_result(), text="NEEXISTUJE")
    assert filtered.scopes == []
    assert len(filtered.unmatched["baseline"]) == 1
    assert len(filtered.unmatched["subject"]) == 1


def test_to_json_is_valid_and_keeps_czech_characters():
    result = _result()
    result.scopes[0].checks[0].message = "rozhrani je v poradku"
    payload = json.loads(to_json(result))
    assert payload["schema_version"] == 1
    assert payload["scopes"][0]["checks"][0]["message"] == "rozhrani je v poradku"
