from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.evpn import (
    EvpnEsiStatusCheck,
    EvpnMacCountCheck,
    EvpnVpwsStatusCheck,
)
from migration_validator.config import default_config
from migration_validator.models.result import Outcome, Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _ctx(subject, baseline=None, service_type="E-LAN", subtype="vlan-aware"):
    scope = Scope(
        id=f"svc:SVC:{service_type}",
        kind="service",
        key=ScopeKey("SVC", service_type, subtype),
        selectors=Selectors(
            interfaces=["ge-0/0/2.313"], routing_instances=["EVPN-AWARE-CPE13"]
        ),
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=baseline,
        config=default_config(),
        failed_collectors={},
    )


def _vpws_ctx(subject, baseline=None):
    return _ctx(subject, baseline, service_type="E-Line", subtype="vpws")


def _vpws_subject(*, status="Up", mode="single-homed",
                  local_peers=(), remote_peers=(), remote_value=2000):
    return {"evpn_vpws": {"EVPN-VPWS-X": {"interfaces": [{
        "name": "ge-0/0/2.213", "status": status, "mode": mode,
        "local_sid": {"value": 1000, "peers": list(local_peers)},
        "remote_sid": {"value": remote_value, "peers": list(remote_peers)},
    }]}}}


PEER_OK = {"esi": "00:00:00:00:00:00:00:00:00:00", "ipaddr": "150.0.0.14",
           "mode": "single-homed", "role": "Primary", "status": "Resolved"}


def run_findings(subject):
    return EvpnVpwsStatusCheck().run(_vpws_ctx(subject))


def _by_label(findings, label):
    hits = [f for f in findings if f.label == label]
    assert len(hits) == 1, f"label {label!r}: {len(hits)} radku"
    return hits[0]


def test_local_interface_status_row_renamed():
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK]))
    row = _by_label(findings, "EVPN VPWS local interface status")
    assert row.outcome is Outcome.OK and row.value == "Up"


def test_remote_peer_resolved_passes_per_peer():
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK]))
    assert _by_label(findings, "EVPN VPWS SID remote PE").value == "150.0.0.14"
    assert _by_label(findings, "EVPN VPWS SID remote status").outcome is Outcome.OK


def test_remote_peer_unresolved_fails():
    peer = {**PEER_OK, "status": "Unresolved"}
    findings = run_findings(_vpws_subject(remote_peers=[peer]))
    assert _by_label(findings, "EVPN VPWS SID remote status").outcome is Outcome.BROKEN


def test_missing_remote_peer_fails_with_unknown_peer():
    findings = run_findings(_vpws_subject(remote_peers=[]))
    pe = _by_label(findings, "EVPN VPWS SID remote PE")
    assert pe.outcome is Outcome.BROKEN and pe.value == "Neznamy peer"
    status = _by_label(findings, "EVPN VPWS SID remote status")
    assert status.outcome is Outcome.BROKEN and status.value == "Unresolved / Chybi"


def test_informative_rows_are_info():
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK]))
    for label in ("EVPN VPWS SID local value", "EVPN VPWS SID remote value",
                  "EVPN VPWS SID remote mode", "EVPN VPWS SID remote role"):
        assert _by_label(findings, label).outcome is Outcome.INFO


def test_two_remote_peers_two_row_sets():
    peer2 = {**PEER_OK, "ipaddr": "150.0.0.2", "mode": "all-active",
             "esi": "00:11:12:13:14:00:00:00:00:00"}
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK, peer2]))
    pe_rows = [f for f in findings if f.label == "EVPN VPWS SID remote PE"]
    assert [f.value for f in pe_rows] == ["150.0.0.14", "150.0.0.2"]


def test_local_multihoming_peer_rows():
    peer = {**PEER_OK, "ipaddr": "150.0.0.2", "mode": "all-active",
            "esi": "00:11:12:13:14:00:00:00:00:00"}
    findings = run_findings(_vpws_subject(mode="all-active", local_peers=[peer]))
    assert _by_label(findings, "EVPN VPWS SID local peer PE").value == "150.0.0.2"
    assert _by_label(findings, "EVPN VPWS SID local status").outcome is Outcome.OK


def test_local_single_homed_info_note():
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK]))
    row = _by_label(findings, "EVPN VPWS SID local mode")
    assert row.outcome is Outcome.INFO
    assert "multi-homing peer ve vypisu nenalezen" in row.value


def test_interface_down_still_broken():
    findings = run_findings(_vpws_subject(status="Down", remote_peers=[PEER_OK]))
    row = _by_label(findings, "EVPN VPWS local interface status")
    assert row.outcome is Outcome.BROKEN


def test_two_interfaces_qualify_labels():
    subject = _vpws_subject(remote_peers=[PEER_OK])
    ifaces = subject["evpn_vpws"]["EVPN-VPWS-X"]["interfaces"]
    ifaces.append({**ifaces[0], "name": "ae0.224"})
    findings = run_findings(subject)
    assert any(f.label == "EVPN VPWS local interface status (ge-0/0/2.213)"
               for f in findings)


def test_vpws_missing_data_skips():
    result = run_check(EvpnVpwsStatusCheck(), _vpws_ctx({"evpn_vpws": {}}))[0]
    assert result.status is Status.SKIP


def test_vpws_not_run_on_elan_scope():
    assert run_check(EvpnVpwsStatusCheck(), _ctx({"evpn_vpws": {}})) == []


def test_esi_row_carries_the_previous_state_when_there_is_one():
    ctx = _ctx(
        {"evpn_esi": {"00:11": {"status": "Up", "df_role": "DF", "interface": "ae0"}}},
        baseline={"evpn_esi": {"00:11": {"status": "Down", "df_role": "-", "interface": "ae0"}}},
    )

    result = run_check(EvpnEsiStatusCheck(), ctx)[0]

    assert result.value == "Up  DF DF"
    assert result.baseline_value == "Down  DF -"


def test_esi_up_passes_and_reports_df_role():
    ctx = _ctx({"evpn_esi": {"00:11": {"status": "Up", "df_role": "DF", "interface": "ae0"}}})
    result = run_check(EvpnEsiStatusCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert result.subject["df_role"] == "DF"


def test_esi_down_fails():
    ctx = _ctx({"evpn_esi": {"00:11": {"status": "Down", "df_role": "-", "interface": "ae0"}}})
    assert run_check(EvpnEsiStatusCheck(), ctx)[0].status is Status.FAIL


def test_esi_missing_data_skips():
    assert run_check(EvpnEsiStatusCheck(), _ctx({"evpn_esi": {}}))[0].status is Status.SKIP


def test_mac_count_nonzero_passes_per_bridge_domain():
    ctx = _ctx({"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 42, "BD-314": 7}}})
    results = run_check(EvpnMacCountCheck(), ctx)
    assert {r.label for r in results} == {"EVPN-AWARE-CPE13/BD-313", "EVPN-AWARE-CPE13/BD-314"}
    assert all(r.status is Status.PASS for r in results)


def test_mac_count_zero_warns():
    ctx = _ctx({"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 0}}})
    result = run_check(EvpnMacCountCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert "0" in result.message


def test_mac_count_drop_beyond_tolerance_warns():
    ctx = _ctx(
        subject={"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 11}}},
        baseline={"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 42}}},
    )
    result = run_check(EvpnMacCountCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert "42" in result.message and "11" in result.message


def test_mac_count_within_tolerance_passes():
    ctx = _ctx(
        subject={"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 40}}},
        baseline={"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 42}}},
    )
    assert run_check(EvpnMacCountCheck(), ctx)[0].status is Status.PASS


def test_mac_count_vlan_based_uses_single_placeholder_domain():
    ctx = _ctx(
        {"evpn_mac": {"EVPN-BASED-CPE13": {"-": 12}}},
        service_type="E-LAN",
        subtype="vlan-based",
    )
    result = run_check(EvpnMacCountCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert result.label == "EVPN-BASED-CPE13"
