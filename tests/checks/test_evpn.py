from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.evpn import (
    EvpnEsiStatusCheck,
    EvpnMacCountCheck,
    EvpnVpwsStatusCheck,
)
from migration_validator.config import default_config
from migration_validator.models.result import Status
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


def test_vpws_up_with_matching_sids_passes():
    ctx = _vpws_ctx(
        {"evpn_vpws": {"VPWS": {"local_sid": 213, "remote_sid": 213, "status": "Up"}}}
    )
    result = run_check(EvpnVpwsStatusCheck(), ctx)[0]
    assert result.status is Status.PASS


def test_vpws_down_fails():
    ctx = _vpws_ctx(
        {"evpn_vpws": {"VPWS": {"local_sid": 213, "remote_sid": 213, "status": "Down"}}}
    )
    result = run_check(EvpnVpwsStatusCheck(), ctx)[0]
    assert result.status is Status.FAIL
    assert "Down" in result.message


def test_vpws_sid_mismatch_fails():
    ctx = _vpws_ctx(
        {"evpn_vpws": {"VPWS": {"local_sid": 213, "remote_sid": 999, "status": "Up"}}}
    )
    result = run_check(EvpnVpwsStatusCheck(), ctx)[0]
    assert result.status is Status.FAIL
    assert "213" in result.message and "999" in result.message


def test_vpws_missing_data_skips():
    result = run_check(EvpnVpwsStatusCheck(), _vpws_ctx({"evpn_vpws": {}}))[0]
    assert result.status is Status.SKIP


def test_vpws_not_run_on_elan_scope():
    assert run_check(EvpnVpwsStatusCheck(), _ctx({"evpn_vpws": {}})) == []


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
