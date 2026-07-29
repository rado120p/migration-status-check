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


def test_vpws_differing_sids_pass():
    """local a remote SID se u EVPN-VPWS zamerne lisi.

    Laborka je nakonfigurovana 'local 1000; remote 2000' a sluzba je zdrava,
    takze rovnost SID nesmi byt podminkou pro PASS.
    """
    ctx = _vpws_ctx(
        {"evpn_vpws": {"VPWS": {"local_sid": 1000, "remote_sid": 2000, "status": "Up"}}}
    )
    result = run_check(EvpnVpwsStatusCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert "1000" in result.message and "2000" in result.message


def test_vpws_missing_remote_sid_fails():
    """Chybejici remote SID znamena, ze druha strana neinzeruje sluzbu."""
    ctx = _vpws_ctx(
        {"evpn_vpws": {"VPWS": {"local_sid": 1000, "remote_sid": 0, "status": "Up"}}}
    )
    result = run_check(EvpnVpwsStatusCheck(), ctx)[0]
    assert result.status is Status.FAIL
    assert "remote SID" in result.message


def test_vpws_row_carries_the_previous_state_when_there_is_one():
    """Sloupec ZMENA hlasil 'bez baseline' u kazdeho EVPN radku, i kdyz check
    baseline mel - dohledava si ji a vozi v poli `baseline`, jen ji nikdy
    nerozlozil na hodnotu pro sazbu. Stejna trida chyby jako F-15: sloupec
    tvrdi neco, co neplati.
    """
    ctx = _vpws_ctx(
        {"evpn_vpws": {"VPWS": {"local_sid": 1000, "remote_sid": 2000, "status": "Up"}}},
        baseline={"evpn_vpws": {"VPWS": {"local_sid": 1000, "remote_sid": 0, "status": "Up"}}},
    )

    result = run_check(EvpnVpwsStatusCheck(), ctx)[0]

    assert result.value == "Up  SID 1000 -> 2000"
    assert result.baseline_value == "Up  SID 1000 -> -"


def test_vpws_row_without_baseline_leaves_the_previous_state_empty():
    """Druha strana teze veci: bez baseline se nic vymyslet nesmi."""
    ctx = _vpws_ctx(
        {"evpn_vpws": {"VPWS": {"local_sid": 1000, "remote_sid": 2000, "status": "Up"}}}
    )

    assert run_check(EvpnVpwsStatusCheck(), ctx)[0].baseline_value is None


def test_esi_row_carries_the_previous_state_when_there_is_one():
    ctx = _ctx(
        {"evpn_esi": {"00:11": {"status": "Up", "df_role": "DF", "interface": "ae0"}}},
        baseline={"evpn_esi": {"00:11": {"status": "Down", "df_role": "-", "interface": "ae0"}}},
    )

    result = run_check(EvpnEsiStatusCheck(), ctx)[0]

    assert result.value == "Up  DF DF"
    assert result.baseline_value == "Down  DF -"


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
