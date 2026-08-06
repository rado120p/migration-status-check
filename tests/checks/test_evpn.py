from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.evpn import (
    EvpnEsiStatusCheck,
    EvpnInstanceStatusCheck,
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


def _vpws_subject(*, status="Up", mode="single-homed", iface_name="ge-0/0/2.213",
                  local_peers=(), remote_peers=(), remote_value=2000):
    return {"evpn_vpws": {"EVPN-VPWS-X": {"interfaces": [{
        "name": iface_name, "status": status, "mode": mode,
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


def test_vpws_baseline_matching_values_have_empty_change():
    # Zivy nalez: sparovana sluzba se shodnymi hodnotami pred a po migraci
    # nesmi tisknout "bez baseline" - check musi baseline_value == value.
    baseline = _vpws_subject(remote_peers=[PEER_OK])
    subject = _vpws_subject(remote_peers=[PEER_OK])
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline))
    for lbl in (
        "EVPN VPWS local interface status",
        "EVPN VPWS SID remote value",
        "EVPN VPWS SID remote PE",
        "EVPN VPWS SID remote status",
        "EVPN VPWS SID remote role",
        "EVPN VPWS SID local mode",
    ):
        row = _by_label(findings, lbl)
        assert row.baseline_value == row.value, lbl


def test_vpws_baseline_different_local_mode_is_plain_mode():
    # Zavorka "(multi-homing peer ve vypisu nenalezen)" popisuje subjekt,
    # ne baseline - pri rozdilnem modu ma sloupec ZMENA nest jen
    # "bylo single-homed" bez ni.
    baseline = _vpws_subject(mode="single-homed", remote_peers=[PEER_OK])
    subject = _vpws_subject(mode="all-active", remote_peers=[PEER_OK])
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline))
    row = _by_label(findings, "EVPN VPWS SID local mode")
    assert row.baseline_value == "single-homed"


def test_vpws_baseline_matches_by_position_despite_renamed_interface():
    # Jmeno rozhrani se migraci meni (ge-0/0/3.0 -> ae0.224), parovani
    # musi byt pozicni, ne podle jmena.
    baseline = _vpws_subject(remote_peers=[PEER_OK], iface_name="ge-0/0/3.0")
    subject = _vpws_subject(remote_peers=[PEER_OK], iface_name="ae0.224")
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline))
    row = _by_label(findings, "EVPN VPWS local interface status")
    assert row.baseline_value == row.value == "Up"


def test_vpws_baseline_different_status_shows_previous_value():
    baseline_peer = {**PEER_OK, "status": "Unresolved"}
    baseline = _vpws_subject(remote_peers=[baseline_peer])
    subject = _vpws_subject(remote_peers=[PEER_OK])
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline))
    row = _by_label(findings, "EVPN VPWS SID remote status")
    assert row.baseline_value == "Unresolved"


def test_vpws_baseline_peer_missing_by_ipaddr_gives_none():
    baseline_peer = {**PEER_OK, "ipaddr": "150.0.0.99"}
    baseline = _vpws_subject(remote_peers=[baseline_peer])
    subject = _vpws_subject(remote_peers=[PEER_OK])
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline))
    pe = _by_label(findings, "EVPN VPWS SID remote PE")
    status = _by_label(findings, "EVPN VPWS SID remote status")
    assert pe.baseline_value is None
    assert status.baseline_value is None


def test_vpws_without_baseline_context_stays_none():
    # Bez ctx.baseline (baseline=None) nesmi vzniknout zadna regrese.
    subject = _vpws_subject(remote_peers=[PEER_OK])
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline=None))
    for f in findings:
        assert f.baseline_value is None


def test_vpws_missing_remote_peer_borrows_baseline_from_first_peer():
    # Subjekt nema remote peer vubec, ale baseline ho mel Resolved - rozdil
    # "bylo Resolved" je presne informace, kterou operator potrebuje.
    baseline = _vpws_subject(remote_peers=[PEER_OK])
    subject = _vpws_subject(remote_peers=[])
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline))
    pe = _by_label(findings, "EVPN VPWS SID remote PE")
    status = _by_label(findings, "EVPN VPWS SID remote status")
    assert pe.baseline_value == "150.0.0.14"
    assert status.baseline_value == "Resolved"


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


def _mac_subject(count=2, *, vlan="313", domain="BD-313",
                 iface="ge-0/0/2.313", iface_count=1):
    return {"evpn_mac": {"EVPN-AWARE-CPE13": {
        "vlans": {vlan: {"count": count, "domain": domain}},
        "interfaces": {iface: {"count": iface_count,
                               "name": f"{iface}:{vlan}", "domain": domain}},
    }}}


def test_mac_count_labels_carry_domain_and_interface():
    findings = EvpnMacCountCheck().run(_ctx(_mac_subject()))
    assert _by_label(findings, "BD-313 MAC count").outcome is Outcome.OK
    row = _by_label(findings, "BD-313 Interface ge-0/0/2.313:313 MAC count")
    assert row.outcome is Outcome.OK
    assert row.value == "1"


def test_mac_count_vlan_based_has_no_domain_prefix():
    subject = _mac_subject(domain=None)
    findings = EvpnMacCountCheck().run(_ctx(subject))
    assert _by_label(findings, "MAC count").outcome is Outcome.OK
    assert _by_label(findings, "Interface ge-0/0/2.313:313 MAC count")


def test_mac_count_zero_vlan_fails():
    findings = EvpnMacCountCheck().run(_ctx(_mac_subject(0)))
    assert _by_label(findings, "BD-313 MAC count").outcome is Outcome.BROKEN


def test_mac_count_compares_vlan_against_baseline_key():
    findings = EvpnMacCountCheck().run(
        _ctx(_mac_subject(1), baseline=_mac_subject(10))
    )
    row = _by_label(findings, "BD-313 MAC count")
    assert row.outcome is Outcome.BROKEN  # -90 % pod toleranci -60
    assert row.baseline_value == "10"


def test_mac_count_vlan_missing_in_subject_fails():
    # Count vypis mrtvou domenu vubec nevypise - kdyby check iteroval jen
    # subject, zmizela domena by z reportu tise vypadla.
    subject = {"evpn_mac": {"EVPN-AWARE-CPE13": {"vlans": {}, "interfaces": {}}}}
    findings = EvpnMacCountCheck().run(_ctx(subject, baseline=_mac_subject(5)))
    row = _by_label(findings, "BD-313 MAC count")
    assert row.outcome is Outcome.BROKEN
    assert row.baseline_value == "5"


def test_mac_count_interface_missing_in_subject_is_omitted():
    # EVO count vypis interface-name nekdy nevrati - per-interface radek
    # se pak vynechava a porovnava se jen per-VLAN (rozhodnuti ze specu).
    subject = _mac_subject()
    del subject["evpn_mac"]["EVPN-AWARE-CPE13"]["interfaces"]["ge-0/0/2.313"]
    findings = EvpnMacCountCheck().run(_ctx(subject, baseline=_mac_subject()))
    assert not [f for f in findings if "Interface" in (f.label or "")]


def test_mac_count_interface_compares_via_renamed_key():
    # Engine (Task 4) preklici baseline interfaces na jmena subjektu,
    # check tedy najde baseline pod svym klicem.
    baseline = _mac_subject(iface="et-0/0/8.313", iface_count=4)
    baseline["evpn_mac"]["EVPN-AWARE-CPE13"]["interfaces"] = {
        "ge-0/0/2.313": baseline["evpn_mac"]["EVPN-AWARE-CPE13"]["interfaces"].pop("et-0/0/8.313")
    }
    findings = EvpnMacCountCheck().run(_ctx(_mac_subject(), baseline=baseline))
    row = _by_label(findings, "BD-313 Interface ge-0/0/2.313:313 MAC count")
    assert row.baseline_value == "4"


def test_mac_count_missing_data_skips():
    findings = EvpnMacCountCheck().run(_ctx({"evpn_mac": {}}))
    assert findings[0].outcome is Outcome.SKIP


def _instance_subject(*, total=2, up=2, irb_total=1, irb_up=1,
                      neighbors=1, esis=None):
    return {"evpn_instance": {"EVPN-AWARE-CPE13": {
        "local_interfaces": {"total": total, "up": up, "entries": [
            {"name": "ge-0/0/2.313", "status": "Up"}]},
        "irb_interfaces": {"total": irb_total, "up": irb_up, "entries": [
            {"name": "irb.14", "status": "Up", "l3_context": "master"}]},
        "neighbors": {"total": neighbors, "addresses": ["150.0.0.13"]},
        "esis": {"00:11:12:13:14:00:00:00:00:00": "Resolved by IFL ae0.14"}
        if esis is None else esis,
    }}}


def _instance_findings(subject, baseline=None):
    return EvpnInstanceStatusCheck().run(_ctx(subject, baseline))


def test_instance_healthy_rows_pass():
    findings = _instance_findings(_instance_subject())
    for label in ("EVPN local interfaces", "EVPN local interfaces up",
                  "EVPN IRB interfaces up", "EVPN neighbors"):
        assert _by_label(findings, label).outcome in (Outcome.OK, Outcome.INFO), label
    assert _by_label(findings, "EVPN IRB interfaces").outcome is Outcome.INFO


def test_instance_zero_local_interfaces_fails():
    findings = _instance_findings(_instance_subject(total=0, up=0))
    assert _by_label(findings, "EVPN local interfaces").outcome is Outcome.BROKEN


def test_instance_interface_down_fails_up_row():
    findings = _instance_findings(_instance_subject(total=2, up=1))
    row = _by_label(findings, "EVPN local interfaces up")
    assert row.outcome is Outcome.BROKEN
    assert row.value == "1/2"


def test_instance_without_irb_skips_irb_up_row():
    findings = _instance_findings(_instance_subject(irb_total=0, irb_up=0))
    assert not [f for f in findings if f.label == "EVPN IRB interfaces up"]


def test_instance_irb_down_fails():
    findings = _instance_findings(_instance_subject(irb_total=2, irb_up=1))
    assert _by_label(findings, "EVPN IRB interfaces up").outcome is Outcome.BROKEN


def test_instance_zero_neighbors_fails():
    findings = _instance_findings(_instance_subject(neighbors=0))
    assert _by_label(findings, "EVPN neighbors").outcome is Outcome.BROKEN


def test_instance_esi_resolved_passes_unresolved_fails():
    ok = _instance_findings(_instance_subject())
    assert _by_label(ok, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.OK
    bad = _instance_findings(
        _instance_subject(esis={"00:11:12:13:14:00:00:00:00:00": ""})
    )
    assert _by_label(bad, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.BROKEN


def test_instance_esi_unresolved_status_fails_not_substring_match():
    # "Unresolved" obsahuje "resolved" jako podretezec - substring test by
    # tenhle stav omylem oznacil za OK, presne obracene, nez rika status.
    bad = _instance_findings(
        _instance_subject(esis={"00:11:12:13:14:00:00:00:00:00": "Unresolved"})
    )
    assert _by_label(bad, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.BROKEN


def test_instance_no_esi_gives_skip_row():
    findings = _instance_findings(_instance_subject(esis={}))
    row = _by_label(findings, "ESI status")
    assert row.outcome is Outcome.SKIP
    assert row.value == "bez dat"


def test_instance_local_count_differs_from_baseline_is_not_a_finding():
    # Revize spec 2.4 (overeno v laborce 2026-08-06): migrace konsoliduje
    # sluzby do jedne mac-vrf instance, takze pocty local/IRB interfacu se
    # meni pri kazde migraci. Rozdil nese sloupec ZMENA (baseline_value),
    # stav zustava podle stavoveho pravidla.
    findings = _instance_findings(
        _instance_subject(total=2, up=2),
        baseline=_instance_subject(total=3, up=3),
    )
    row = _by_label(findings, "EVPN local interfaces")
    assert row.outcome is Outcome.OK
    assert row.baseline_value == "3"
    up_row = _by_label(findings, "EVPN local interfaces up")
    assert up_row.outcome is Outcome.OK
    assert up_row.baseline_value == "3/3"


def test_instance_irb_up_count_differs_from_baseline_is_not_a_finding():
    findings = _instance_findings(
        _instance_subject(irb_total=3, irb_up=3),
        baseline=_instance_subject(irb_total=1, irb_up=1),
    )
    row = _by_label(findings, "EVPN IRB interfaces up")
    assert row.outcome is Outcome.OK
    assert row.baseline_value == "1/1"


def test_instance_neighbors_below_baseline_degrades():
    # Ubytek EVPN sousedu proti baseline je podezrely (ztraceny peer),
    # ale ne tvrdy FAIL - u ciste L2 vlan-aware sluzby po migraci
    # legitimne ubyde puvodni MX.
    findings = _instance_findings(
        _instance_subject(neighbors=1),
        baseline=_instance_subject(neighbors=2),
    )
    row = _by_label(findings, "EVPN neighbors")
    assert row.outcome is Outcome.DEGRADED
    assert row.baseline_value == "2"
    assert "baseline 2" in row.message


def test_instance_neighbors_at_or_above_baseline_ok():
    same = _instance_findings(
        _instance_subject(neighbors=2), baseline=_instance_subject(neighbors=2)
    )
    assert _by_label(same, "EVPN neighbors").outcome is Outcome.OK
    more = _instance_findings(
        _instance_subject(neighbors=3), baseline=_instance_subject(neighbors=2)
    )
    assert _by_label(more, "EVPN neighbors").outcome is Outcome.OK


def test_instance_zero_neighbors_fails_even_with_baseline():
    # Stavove pravidlo (> 0) ma prednost pred poklesem: nula sousedu je
    # FAIL, ne WARN.
    findings = _instance_findings(
        _instance_subject(neighbors=0), baseline=_instance_subject(neighbors=2)
    )
    assert _by_label(findings, "EVPN neighbors").outcome is Outcome.BROKEN


def test_instance_esi_missing_against_baseline_fails():
    findings = _instance_findings(
        _instance_subject(esis={}),
        baseline=_instance_subject(),
    )
    row = _by_label(findings, "ESI 00:11:12:13:14:00:00:00:00:00")
    assert row.outcome is Outcome.BROKEN


def test_instance_two_instances_qualify_labels():
    # Dve instance ve scope musi mit odlisitelne radky - stejny princip
    # jako test_two_interfaces_qualify_labels u VPWS checku.
    subject = _instance_subject()
    subject["evpn_instance"]["EVPN-B"] = subject["evpn_instance"].pop("EVPN-AWARE-CPE13")
    subject["evpn_instance"]["EVPN-A"] = _instance_subject()["evpn_instance"]["EVPN-AWARE-CPE13"]
    findings = _instance_findings(subject)
    assert any(f.label == "EVPN local interfaces (EVPN-A)" for f in findings)
    assert any(f.label == "EVPN local interfaces (EVPN-B)" for f in findings)


def test_instance_esi_status_text_not_compared_to_baseline():
    # Text statusu nese jmeno IFL ('Resolved by IFL ae0.14'), ktere se
    # migraci meni - rovnost textu by FAILovala kazdou migraci.
    findings = _instance_findings(
        _instance_subject(),
        baseline=_instance_subject(
            esis={"00:11:12:13:14:00:00:00:00:00": "Resolved by IFL ge-0/0/2.313"}
        ),
    )
    assert _by_label(findings, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.OK


def test_instance_info_rows_list_names():
    findings = _instance_findings(_instance_subject())
    values = [f.value for f in findings if f.label == "EVPN interface"]
    assert "ge-0/0/2.313 Up" in values
    irb_values = [f.value for f in findings if f.label == "IRB interface"]
    assert "irb.14 Up (master)" in irb_values
    neighbor_values = [f.value for f in findings if f.label == "EVPN neighbor"]
    assert "150.0.0.13" in neighbor_values


def test_instance_missing_data_skips():
    findings = _instance_findings({"evpn_instance": {}})
    assert findings[0].outcome is Outcome.SKIP


def test_instance_not_run_on_eline_scope():
    result = run_check(
        EvpnInstanceStatusCheck(),
        _ctx(_instance_subject(), service_type="E-Line", subtype="vpws"),
    )
    assert result == []
