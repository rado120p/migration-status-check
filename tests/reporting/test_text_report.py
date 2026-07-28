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


def _legacy_check(check_id, status, message):
    return CheckResult(
        id=check_id,
        mode="both",
        status=status,
        severity=Severity.ADVISORY,
        message=message,
    )


def _legacy_result() -> RunResult:
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
                checks=[_legacy_check("interface_state", Status.PASS, "up/up")],
            ),
            ScopeResult(
                scope_id="svc:L3VPN-CPE13-NNI:IPVPN",
                key={"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN"},
                status=Status.WARN,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[
                    _legacy_check(
                        "interface_traffic", Status.WARN, "provoz -72 % (410 -> 115 pps)"
                    ),
                    _legacy_check("interface_state", Status.PASS, "up/up"),
                ],
            ),
            ScopeResult(
                scope_id="svc:EVPN-VPWS-CPE13-NNI:E-Line",
                key={"description": "EVPN-VPWS-CPE13-NNI", "service_type": "E-Line"},
                status=Status.FAIL,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[_legacy_check("evpn_vpws_status", Status.FAIL, "vpws-sid-pe-status: Down")],
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


def test_filter_by_text_matches_description():
    filtered = filter_result(_legacy_result(), text="L3VPN")
    assert [scope.scope_id for scope in filtered.scopes] == ["svc:L3VPN-CPE13-NNI:IPVPN"]


def test_filter_by_status_keeps_only_requested():
    filtered = filter_result(_legacy_result(), statuses={Status.FAIL, Status.WARN})
    assert {scope.status for scope in filtered.scopes} == {Status.WARN, Status.FAIL}


def test_filter_does_not_touch_unmatched():
    filtered = filter_result(_legacy_result(), text="NEEXISTUJE")
    assert filtered.scopes == []
    assert len(filtered.unmatched["baseline"]) == 1
    assert len(filtered.unmatched["subject"]) == 1


def test_to_json_is_valid_and_keeps_czech_characters():
    result = _legacy_result()
    result.scopes[0].checks[0].message = "rozhrani je v poradku"
    payload = json.loads(to_json(result))
    assert payload["schema_version"] == 1
    assert payload["scopes"][0]["checks"][0]["message"] == "rozhrani je v poradku"


def _check(check_id, status, message, *, label, value, family=None,
           mode="state", baseline_value=None, delta=None):
    return CheckResult(
        id=check_id, mode=mode, status=status, severity=Severity.ADVISORY,
        message=message, label=label, family=family, value=value,
        baseline_value=baseline_value, delta=delta,
    )


def _dual_stack_scope(status=Status.WARN) -> ScopeResult:
    return ScopeResult(
        scope_id="svc:INTERNET-CPE13-NNI:Internet",
        key={"description": "INTERNET-CPE13-NNI", "service_type": "Internet"},
        status=status,
        match=MatchInfo(
            status="matched",
            baseline_interfaces=["ge-0/0/2.13"],
            subject_interfaces=["et-0/0/8.13"],
        ),
        checks=[
            _check("interface_state", Status.PASS, "up", label="Interface admin status", value="Up"),
            _check("arp_present", Status.PASS, "arp ok", label="ARP", family=4,
                   value="0c:00:ef:5e:df:01 -> 152.11.13.2"),
            _check("nd_present", Status.PASS, "nd ok", label="ND", family=6,
                   value="0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b"),
            _check("interface_traffic", Status.WARN, "pokles", label="Interface traffic in",
                   value="460 pps", mode="both", baseline_value="520 pps", delta="-12 %"),
        ],
        identity={
            "description": "INTERNET-CPE13-NNI",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "ipv4": ["152.11.13.1/30"],
            "ipv6": ["2001:abcd:11:13::a/127"],
            "virtual_gw_v4": [],
            "virtual_gw_v6": [],
        },
    )


def _result(scopes, *, baseline=True) -> RunResult:
    return RunResult(
        evaluated_at="2026-07-28T11:40:02Z",
        subject={"address": "172.20.20.5", "phase": "post-migration", "captured_at": "x"},
        baseline=(
            {"address": "172.20.20.4", "phase": "pre-migration", "captured_at": "y"}
            if baseline
            else None
        ),
        summary={"pass": 1, "warn": 1, "fail": 0, "skip": 0,
                 "scopes_matched": 1, "unmatched_baseline": 0, "unmatched_subject": 0},
        scopes=scopes,
    )


def test_ipv4_section_comes_before_ipv6():
    output = render(_result([_dual_stack_scope()]))

    assert output.index("-- IPv4") < output.index("-- IPv6")


def test_section_header_carries_the_address():
    output = render(_result([_dual_stack_scope()]))

    assert "-- IPv4  152.11.13.1/30" in output
    assert "-- IPv6  2001:abcd:11:13::a/127" in output


def test_long_ipv6_value_is_not_truncated():
    """Orezana IPv6 adresa je horsi nez zadna."""
    output = render(_result([_dual_stack_scope()]))

    assert "0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b" in output


def test_columns_hold_the_line_across_sections():
    """Sirky se pocitaji z celeho bloku, ne z kazde sekce zvlast."""
    output = render(_result([_dual_stack_scope()]))
    data_lines = [
        line for line in output.splitlines()
        if line.startswith((" PASS |", " WARN |", " FAIL |", " SKIP |"))
    ]
    positions = {line.index(" : ") for line in data_lines}

    assert len(positions) == 1, f"sloupec s hodnotou se lame: {positions}"


def test_passing_service_is_collapsed_by_default():
    output = render(_result([_dual_stack_scope(status=Status.PASS)]))

    assert "-- IPv4" not in output


def test_failing_service_is_expanded_by_default():
    output = render(_result([_dual_stack_scope(status=Status.FAIL)]))

    assert "-- IPv4" in output


def test_detail_expands_passing_service():
    output = render(_result([_dual_stack_scope(status=Status.PASS)]), detail=True)

    assert "-- IPv4" in output


def test_change_column_is_absent_without_baseline():
    output = render(_result([_dual_stack_scope()], baseline=False))

    assert "ZMENA" not in output


def test_change_column_shows_previous_value():
    output = render(_result([_dual_stack_scope()]))

    assert "bylo 520 pps" in output


def test_ports_are_in_the_summary_row():
    output = render(_result([_dual_stack_scope()]))

    assert "ge-0/0/2.13" in output
    assert "et-0/0/8.13" in output


def _vgw_scope() -> ScopeResult:
    """EVPN-VLAN-AWARE-INTERNET z laborky: irb.14 ma virtual gateway."""
    return ScopeResult(
        scope_id="svc:EVPN-VLAN-AWARE-INTERNET:Internet",
        key={"description": "EVPN-VLAN-AWARE-INTERNET", "service_type": "Internet"},
        status=Status.FAIL,
        match=MatchInfo(
            status="matched",
            baseline_interfaces=["ge-0/0/5.0"],
            subject_interfaces=["irb.14"],
        ),
        checks=[
            _check("arp_present", Status.FAIL, "zadny zaznam", label="ARP", family=4,
                   value="zadny zaznam"),
        ],
        identity={
            "description": "EVPN-VLAN-AWARE-INTERNET",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "ipv4": ["152.11.14.2/29"],
            "ipv6": [],
            "virtual_gw_v4": ["152.11.14.1"],
            "virtual_gw_v6": [],
        },
    )


def test_virtual_gateway_is_shown_in_the_section_header():
    output = render(_result([_vgw_scope()]))

    assert "-- IPv4  152.11.14.2/29   VGW 152.11.14.1" in output


def test_service_without_ipv6_has_no_ipv6_section():
    output = render(_result([_vgw_scope()]))

    assert "-- IPv6" not in output


def test_block_frame_agrees_with_its_widest_line():
    """Presne to selhani, ktere AR-5 resi: ramec kratsi nez hlavicka."""
    output = render(_result([_dual_stack_scope()]))
    lines = output.splitlines()

    frame = [line for line in lines if line and set(line) == {"="}]
    assert frame, "blok nema ramec"

    body = [
        line for line in lines
        if line.startswith((" STAV |", " PASS |", " WARN |", " FAIL |", " SKIP |", " -----+"))
    ]
    assert body, "blok nema zadny radek"

    assert max(len(line) for line in body) <= len(frame[0])
