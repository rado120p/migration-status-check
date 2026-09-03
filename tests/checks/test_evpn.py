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
                  local_peers=(), remote_peers=(), remote_value=2000,
                  instance="EVPN-VPWS-X"):
    return {"evpn_vpws": {instance: {"interfaces": [{
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


def test_vpws_unresolved_peer_message_carries_reason():
    peer = {**PEER_OK, "status": "Unresolved"}
    findings = run_findings(_vpws_subject(instance="ELINE-1", remote_peers=[peer]))
    row = _by_label(findings, "EVPN VPWS SID remote PE")
    assert row.outcome is Outcome.BROKEN
    assert row.message == "ELINE-1: remote peer 150.0.0.14 neni Resolved (Unresolved)"


def test_vpws_missing_peer_status_message_says_chybi():
    peer = {**PEER_OK}
    del peer["status"]
    findings = run_findings(_vpws_subject(instance="ELINE-1", remote_peers=[peer]))
    row = _by_label(findings, "EVPN VPWS SID remote PE")
    assert row.outcome is Outcome.BROKEN
    assert row.message.endswith("neni Resolved (chybi)")


def test_vpws_esi_info_row_uses_uppercase():
    findings = run_findings(_vpws_subject(instance="ELINE-1", remote_peers=[PEER_OK]))
    row = _by_label(findings, "EVPN VPWS SID remote ESI")
    assert row.message == "ELINE-1: remote peer ESI 00:00:00:00:00:00:00:00:00:00"


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


# Rozpad ESI bloku na samostatne radky (lab 2026-08-13): jeden slepeny
# radek "Up/Forwarding  DF 150.0.0.12" s ESI v labelu se spatne cetl a
# u nezvoleneho DF vypsal "DF DF not elected yet". Novy tvar: INFO
# hlavicka s ESI, pak radky ESI Status / ESI Local interface status /
# ESI DF, kazdy s vlastnim verdiktem.
def _esi_entry(status="Up/Forwarding", df="150.0.0.12", interface="ae0.14",
               resolved="Resolved by IFL ae0.14"):
    return {"status": status, "df_role": df, "interface": interface,
            "resolved_status": resolved}


def test_esi_block_splits_into_header_and_detail_rows():
    ctx = _ctx({"evpn_esi": {"00:11": _esi_entry()}})
    results = run_check(EvpnEsiStatusCheck(), ctx)
    labels = [r.label for r in results]
    assert labels == ["ESI", "ESI Status",
                      "ESI Local interface status", "ESI DF"]

    header, status, local, df = results
    # Hodnota nesmi byt prazdna (invariant kazdy-radek-ma-hodnotu,
    # test_every_row_has_a_label_and_a_value_on_real_data) - ESI je
    # hodnotou hlavicky.
    assert header.status is Status.INFO and header.value == "00:11"
    assert status.status is Status.PASS
    assert status.value == "Resolved by IFL ae0.14"
    assert local.status is Status.PASS
    assert local.value == "ae0.14 Up/Forwarding"
    assert df.status is Status.PASS and df.value == "150.0.0.12"


def test_esi_rows_carry_the_previous_state_when_there_is_one():
    ctx = _ctx(
        {"evpn_esi": {"00:11": _esi_entry()}},
        baseline={"evpn_esi": {"00:11": _esi_entry(
            status="Down", df="-", interface="ae0.14",
            resolved="Resolved by IFL ae0.14")}},
    )
    header, status, local, df = run_check(EvpnEsiStatusCheck(), ctx)
    # ESI v baseline existuje -> hlavicka nesmi hlasit "bez baseline";
    # shodna baseline hodnota necha sloupec ZMENA prazdny.
    assert header.baseline_value == "00:11"
    assert status.baseline_value == "Resolved by IFL ae0.14"
    assert local.baseline_value == "ae0.14 Down"
    assert df.baseline_value == "-"


def test_esi_down_interface_fails():
    ctx = _ctx({"evpn_esi": {"00:11": _esi_entry(status="Down")}})
    results = run_check(EvpnEsiStatusCheck(), ctx)
    local = next(r for r in results if r.label == "ESI Local interface status")
    assert local.status is Status.FAIL


def test_esi_df_not_elected_fails_without_double_df():
    ctx = _ctx({"evpn_esi": {"00:11": _esi_entry(df="DF not elected yet")}})
    results = run_check(EvpnEsiStatusCheck(), ctx)
    df = next(r for r in results if r.label == "ESI DF")
    assert df.status is Status.FAIL
    assert df.value == "DF not elected yet"


def test_esi_df_not_elected_no_double_df():
    ctx = _ctx({"evpn_esi": {"00:11": _esi_entry(df="DF not elected yet")}})
    df = _by_label(EvpnEsiStatusCheck().run(ctx), "ESI DF")
    assert df.outcome is Outcome.BROKEN
    assert df.message == "00:11: DF not elected yet"


def test_esi_df_role_empty_string_is_info_bez_zaznamu():
    ctx = _ctx({"evpn_esi": {"00:11": _esi_entry(df="")}})
    df = _by_label(EvpnEsiStatusCheck().run(ctx), "ESI DF")
    assert df.outcome is Outcome.INFO
    assert df.value == "-"
    assert df.message == "00:11: DF bez zaznamu"


def test_esi_without_resolved_status_omits_the_status_row():
    # Stary snapshot pole resolved_status nema - radek se vynechava,
    # nefabuluje se ([[stav-se-nikdy-nefabuluje]]).
    entry = {"status": "Up/Forwarding", "df_role": "150.0.0.12",
             "interface": "ae0.14"}
    ctx = _ctx({"evpn_esi": {"00:11": entry}})
    labels = [r.label for r in run_check(EvpnEsiStatusCheck(), ctx)]
    assert "ESI Status" not in labels
    assert "ESI Local interface status" in labels


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


def test_mac_count_interface_missing_in_subject_is_broken():
    # EVO count vypis interface-name nekdy nevrati, ale kdyz baseline
    # interface s MAC adresami znala a subjekt o ni mlci, jde o ztracenou
    # viditelnost, ne o legitimni zmizeni - radek je BROKEN, ne ticho.
    subject = _mac_subject()
    del subject["evpn_mac"]["EVPN-AWARE-CPE13"]["interfaces"]["ge-0/0/2.313"]
    findings = EvpnMacCountCheck().run(_ctx(subject, baseline=_mac_subject()))
    row = _by_label(findings, "BD-313 Interface ge-0/0/2.313:313 MAC count")
    assert row.outcome is Outcome.BROKEN
    assert row.message == (
        "BD-313 Interface ge-0/0/2.313:313 MAC count: "
        "v baseline 1 MAC, v subjektu chybi"
    )
    assert row.value == "chybi"
    assert row.baseline_value == "1"


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


def _mac_ctx(subject):
    return _vlan_aware_ctx(subject)  # stejny scope ae0.14 / vlan 14


def _mac_subject_aware():
    return {"evpn_mac": {"EVPN-VLAN-AWARE-POP1": {
        "vlans": {
            "14": {"domain": "VL-14", "count": 8},
            "15": {"domain": "VL-15", "count": 8},
        },
        "interfaces": {
            "ae0.14": {"name": "ae0.14:14", "domain": "VL-14", "count": 6},
            "ae0.15": {"name": "ae0.15:15", "domain": "VL-15", "count": 6},
        },
    }}}


def test_mac_count_jen_vlastni_vlan_a_unit():
    findings = EvpnMacCountCheck().run(_mac_ctx(_mac_subject_aware()))
    labels = [f.label for f in findings]
    assert "VL-14 MAC count" in labels
    assert "VL-15 MAC count" not in labels
    assert "VL-14 Interface ae0.14:14 MAC count" in labels
    assert "VL-15 Interface ae0.15:15 MAC count" not in labels


def test_mac_count_fallback_bez_selektoru():
    ctx = _mac_ctx(_mac_subject_aware())
    ctx.scope.selectors.interfaces = []
    ctx.scope.selectors.vlans = []
    findings = EvpnMacCountCheck().run(ctx)
    assert len([f for f in findings if f.label.endswith("MAC count")]) == 4


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
    # Agregatni radky (Task 2) zmizely - "zdravi" ted nese jen count a
    # posouzeny radek vlastniho unitu.
    findings = _instance_findings(_instance_subject())
    assert _by_label(findings, "EVPN neighbors").outcome is Outcome.OK
    assert _by_label(findings, "EVPN interface").outcome is Outcome.OK


def test_instance_zero_neighbors_fails():
    findings = _instance_findings(_instance_subject(neighbors=0))
    assert _by_label(findings, "EVPN neighbors").outcome is Outcome.BROKEN


def _instance_findings_bez_filtru(subject, baseline=None):
    # Tyhle testy overuji vypocet OK/BROKEN per-ESI, ne relevance filtr
    # (Task 3) - vypnuty selektor drzi puvodni chovani (viz
    # test_esi_fallback_bez_selektoru_tiskne_vse).
    ctx = _ctx(subject, baseline)
    ctx.scope.selectors.interfaces = []
    return EvpnInstanceStatusCheck().run(ctx)


def test_instance_esi_resolved_passes_unresolved_fails():
    ok = _instance_findings_bez_filtru(_instance_subject())
    assert _by_label(ok, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.OK
    bad = _instance_findings_bez_filtru(
        _instance_subject(esis={"00:11:12:13:14:00:00:00:00:00": ""})
    )
    assert _by_label(bad, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.BROKEN


def test_instance_esi_unresolved_status_fails_not_substring_match():
    # "Unresolved" obsahuje "resolved" jako podretezec - substring test by
    # tenhle stav omylem oznacil za OK, presne obracene, nez rika status.
    bad = _instance_findings_bez_filtru(
        _instance_subject(esis={"00:11:12:13:14:00:00:00:00:00": "Unresolved"})
    )
    assert _by_label(bad, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.BROKEN


def test_instance_no_esi_gives_skip_row_only_without_filter():
    # SKIP "zadne ESI ve vypisu" patri jen fallbacku bez selektoru -
    # u sluzby s aktivnim filtrem nese ESI zdravi blok checku
    # evpn_esi_status a instancni radek by ho jen dubloval.
    findings = _instance_findings_bez_filtru(_instance_subject(esis={}))
    row = _by_label(findings, "ESI status")
    assert row.outcome is Outcome.SKIP
    assert row.value == "bez dat"

    filtered = _instance_findings(_instance_subject(esis={}))
    assert not [f for f in filtered if f.label == "ESI status"]


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
    # "Chybi vs baseline" radek je bez aktivniho filtru - pri aktivnim
    # filtru tahle vetev vypada spolu s ostatnimi (Task 3, viz
    # _esi_is_relevant docstring).
    findings = _instance_findings_bez_filtru(
        _instance_subject(esis={}),
        baseline=_instance_subject(),
    )
    row = _by_label(findings, "ESI 00:11:12:13:14:00:00:00:00:00")
    assert row.outcome is Outcome.BROKEN


def test_instance_two_instances_qualify_labels():
    # Dve instance ve scope musi mit odlisitelne radky - stejny princip
    # jako test_two_interfaces_qualify_labels u VPWS checku. Agregatni
    # radek "EVPN local interfaces" zmizel (Task 2) - qualifikace se
    # overuje na posuzovanem "EVPN interface" radku.
    subject = _instance_subject()
    subject["evpn_instance"]["EVPN-B"] = subject["evpn_instance"].pop("EVPN-AWARE-CPE13")
    subject["evpn_instance"]["EVPN-A"] = _instance_subject()["evpn_instance"]["EVPN-AWARE-CPE13"]
    findings = _instance_findings(subject)
    assert any(f.label == "EVPN interface (EVPN-A)" for f in findings)
    assert any(f.label == "EVPN interface (EVPN-B)" for f in findings)


def test_instance_esi_status_text_not_compared_to_baseline():
    # Text statusu nese jmeno IFL ('Resolved by IFL ae0.14'), ktere se
    # migraci meni - rovnost textu by FAILovala kazdou migraci.
    findings = _instance_findings_bez_filtru(
        _instance_subject(),
        baseline=_instance_subject(
            esis={"00:11:12:13:14:00:00:00:00:00": "Resolved by IFL ge-0/0/2.313"}
        ),
    )
    assert _by_label(findings, "ESI 00:11:12:13:14:00:00:00:00:00").outcome is Outcome.OK


def test_instance_info_rows_list_names():
    # IRB interface radek vyzaduje link (Task 2) - bez nej se netiskne,
    # viz test_bez_linku_zadny_irb_radek. Tady se overuje jen EVPN
    # interface a neighbor, ktere link nepotrebuji.
    findings = _instance_findings(_instance_subject())
    values = [f.value for f in findings if f.label == "EVPN interface"]
    assert "ge-0/0/2.313 Up" in values
    neighbor_values = [f.value for f in findings if f.label == "EVPN neighbor"]
    assert "150.0.0.13" in neighbor_values


def test_instance_status_rows_carry_baseline_value():
    subject = _instance_subject()
    baseline = _instance_subject()
    findings = _instance_findings(subject, baseline=baseline)
    assert _by_label(findings, "EVPN interface").baseline_value == "ge-0/0/2.313 Up"
    neighbor_row = _by_label(findings, "EVPN neighbor")
    assert neighbor_row.baseline_value == "150.0.0.13"


def test_irb_instance_status_row_carries_baseline_value():
    ctx = _vlan_aware_ctx(_aware_subject(), link=LINK_L2)
    ctx.baseline = _aware_subject()
    findings = EvpnInstanceStatusCheck().run(ctx)
    row = _by_label(findings, "IRB interface")
    assert row.baseline_value == "irb.14 Up (master)"


def test_instance_missing_data_skips():
    findings = _instance_findings({"evpn_instance": {}})
    assert findings[0].outcome is Outcome.SKIP


def test_instance_not_run_on_eline_scope():
    result = run_check(
        EvpnInstanceStatusCheck(),
        _ctx(_instance_subject(), service_type="E-Line", subtype="vpws"),
    )
    assert result == []


def _instance_subject_with_addresses(**overrides):
    data = {
        "local_interfaces": {"total": 1, "up": 1, "entries": [
            {"name": "ge-0/0/2.313", "status": "Up"},
        ]},
        "irb_interfaces": {"total": 0, "up": 0, "entries": []},
        "neighbors": {"total": 2, "addresses": ["150.0.0.2", "150.0.0.3"]},
        "esis": {},
    }
    data.update(overrides)
    return {"evpn_instance": {"EVPN-AWARE-CPE13": data}}


def test_neighbor_adresy_stoji_hned_pod_countem():
    findings = EvpnInstanceStatusCheck().run(_ctx(_instance_subject_with_addresses()))
    labels = [f.label for f in findings]
    count_idx = labels.index("EVPN neighbors")
    assert labels[count_idx + 1] == "EVPN neighbor"
    assert labels[count_idx + 2] == "EVPN neighbor"


def _vlan_aware_ctx(subject, link=None):
    """Scope sluzby ae0.14 v RI se tremi sluzbami - vlan-aware vzor z labu."""
    scope = Scope(
        id="svc:SVC:E-LAN",
        kind="service",
        key=ScopeKey("SVC", "E-LAN", "vlan-aware"),
        selectors=Selectors(
            interfaces=["ae0.14"],
            routing_instances=["EVPN-VLAN-AWARE-POP1"],
            vlans=["14"],
        ),
    )
    return CheckContext(
        scope=scope, subject=subject, baseline=None,
        config=default_config(), failed_collectors={}, link=link,
    )


def _aware_subject():
    return {"evpn_instance": {"EVPN-VLAN-AWARE-POP1": {
        "local_interfaces": {"total": 4, "up": 4, "entries": [
            {"name": ".local..64", "status": "Up"},
            {"name": "ae0.14", "status": "Up"},
            {"name": "ae0.15", "status": "Up"},
            {"name": "ae0.4094", "status": "Down"},
        ]},
        "irb_interfaces": {"total": 3, "up": 3, "entries": [
            {"name": "irb.14", "status": "Up", "l3_context": "master"},
            {"name": "irb.15", "status": "Up", "l3_context": "L3VPN-CPE14"},
            {"name": "irb.4094", "status": "Up", "l3_context": "MGMT"},
        ]},
        "neighbors": {"total": 1, "addresses": ["150.0.0.2"]},
        "esis": {
            "00:11:12:13:14:00:14:00:00:00": "Resolved by IFL ae0.14",
            "00:11:12:13:14:00:15:00:00:00": "Resolved by IFL ae0.15",
        },
    }}}


LINK_L2 = {"role": "l2", "peer_scope_id": "svc:X:Internet",
           "peer_interface": "irb.14", "peer_instance": "inet.0"}


def test_agregatni_radky_se_netisknou():
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(_aware_subject()))
    labels = {f.label for f in findings}
    assert "EVPN local interfaces" not in labels
    assert "EVPN local interfaces up" not in labels
    assert "EVPN IRB interfaces" not in labels
    assert "EVPN IRB interfaces up" not in labels
    assert "EVPN neighbors" in labels  # count zustava


def test_evpn_interface_jen_vlastni_unit_a_je_posuzovany():
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(_aware_subject()))
    rows = [f for f in findings if f.label == "EVPN interface"]
    assert [r.value for r in rows] == ["ae0.14 Up"]
    assert rows[0].outcome is Outcome.OK


def test_evpn_interface_down_je_broken():
    subject = _aware_subject()
    subject["evpn_instance"]["EVPN-VLAN-AWARE-POP1"]["local_interfaces"]["entries"][1][
        "status"] = "Down"
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(subject))
    rows = [f for f in findings if f.label == "EVPN interface"]
    assert rows[0].outcome is Outcome.BROKEN


def test_irb_radek_jen_linkovany_unit():
    findings = EvpnInstanceStatusCheck().run(
        _vlan_aware_ctx(_aware_subject(), link=LINK_L2))
    rows = [f for f in findings if f.label == "IRB interface"]
    assert [r.value for r in rows] == ["irb.14 Up (master)"]
    assert rows[0].outcome is Outcome.OK


def test_irb_down_je_broken():
    """IRB je v instanci, ale Down - operacne overeno 2026-08-13 v labu
    (disablovany irb.4094 hlasi v mac-vrf vypisu Down). Mutant OK<->BROKEN
    na IRB vetvi tenhle test zabiji, zadny jiny ji nedrzel."""
    subject = _aware_subject()
    subject["evpn_instance"]["EVPN-VLAN-AWARE-POP1"]["irb_interfaces"]["entries"][0][
        "status"] = "Down"
    findings = EvpnInstanceStatusCheck().run(
        _vlan_aware_ctx(subject, link=LINK_L2))
    rows = [f for f in findings if f.label == "IRB interface"]
    assert [r.value for r in rows] == ["irb.14 Down (master)"]
    assert rows[0].outcome is Outcome.BROKEN
    assert "ocekavano Up" in rows[0].message


def test_bez_linku_zadny_irb_radek():
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(_aware_subject()))
    assert not [f for f in findings if f.label == "IRB interface"]


def test_fallback_bez_selektoru_tiskne_vse():
    ctx = _vlan_aware_ctx(_aware_subject())
    ctx.scope.selectors.interfaces = []
    findings = EvpnInstanceStatusCheck().run(ctx)
    rows = [f for f in findings if f.label == "EVPN interface"]
    assert len(rows) == 4  # filtr vypnuty, vsechno jako drive


def _aware_subject_bez_vlastni_unit():
    """IFL se do mac-vrf vubec nedostal - realny selhany stav migrace."""
    subject = _aware_subject()
    entries = subject["evpn_instance"]["EVPN-VLAN-AWARE-POP1"]["local_interfaces"]["entries"]
    subject["evpn_instance"]["EVPN-VLAN-AWARE-POP1"]["local_interfaces"]["entries"] = [
        e for e in entries if e["name"] != "ae0.14"
    ]
    return subject


def test_chybejici_vlastni_unit_je_broken():
    findings = EvpnInstanceStatusCheck().run(
        _vlan_aware_ctx(_aware_subject_bez_vlastni_unit()))
    rows = [f for f in findings if f.label == "EVPN interface"]
    assert [r.value for r in rows] == ["ae0.14 chybi v instanci"]
    assert rows[0].outcome is Outcome.BROKEN


def test_chybejici_linkovany_irb_je_broken():
    subject = _aware_subject()
    entries = subject["evpn_instance"]["EVPN-VLAN-AWARE-POP1"]["irb_interfaces"]["entries"]
    subject["evpn_instance"]["EVPN-VLAN-AWARE-POP1"]["irb_interfaces"]["entries"] = [
        e for e in entries if e["name"] != "irb.14"
    ]
    findings = EvpnInstanceStatusCheck().run(
        _vlan_aware_ctx(subject, link=LINK_L2))
    rows = [f for f in findings if f.label == "IRB interface"]
    assert [r.value for r in rows] == ["irb.14 chybi v instanci"]
    assert rows[0].outcome is Outcome.BROKEN


def test_fallback_bez_selektoru_zadny_chybejici_radek():
    ctx = _vlan_aware_ctx(_aware_subject_bez_vlastni_unit())
    ctx.scope.selectors.interfaces = []
    findings = EvpnInstanceStatusCheck().run(ctx)
    assert not [f for f in findings if "chybi v instanci" in (f.value or "")]


def test_vice_chybejicich_unitu_jeden_radek_kazdy_serazene():
    # Ruling explicitne pozaduje: jeden BROKEN radek per chybejici vlastni
    # unit (sluzba muze mit v principu vic selektorovych interfacu).
    # ae0.14 v subjektu je, ae0.16 ne - poradi overuje, ze sorted() neni
    # nahodne.
    ctx = _vlan_aware_ctx(_aware_subject())
    ctx.scope.selectors.interfaces = ["ae0.16", "ae0.14"]
    findings = EvpnInstanceStatusCheck().run(ctx)
    rows = [f for f in findings if f.label == "EVPN interface"]
    assert [(r.value, r.outcome) for r in rows] == [
        ("ae0.14 Up", Outcome.OK),
        ("ae0.16 chybi v instanci", Outcome.BROKEN),
    ]


def test_vice_instanci_negeneruje_falesny_broken():
    # Unit patri tomu RI, ktere ho jmenuje - u vice instanci ve scope by
    # naivni per-instance kontrola oznacila unit za chybejici v kazde
    # instanci, kde neni, i kdyz je spravne v jine. Jednoducha pojistka:
    # pri vice instancich (qualify=True) se chybejici-unit radek vubec
    # neemituje.
    subject = _aware_subject_bez_vlastni_unit()
    subject["evpn_instance"]["EVPN-OTHER"] = (
        _instance_subject()["evpn_instance"]["EVPN-AWARE-CPE13"]
    )
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(subject))
    assert not [f for f in findings if "chybi v instanci" in (f.value or "")]


def test_esi_listing_se_pri_aktivnim_filtru_netiskne():
    # Revize 2026-08-13: relevance filtr (drzel jen vlastni IFL) nechaval
    # jediny radek, ktery po rozpadu evpn_esi_status na blok ESI Status /
    # Local interface status / DF rikal doslova totez. Pri aktivnim
    # filtru se instancni ESI listing uz netiskne vubec; vetev "v
    # baseline bylo, ted chybi" tam byla stejne mrtva (chybejici ESI nema
    # status text, ktery by jmenoval IFL).
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(_aware_subject()))
    assert not [f for f in findings if f.label.startswith("ESI ")]


def test_esi_fallback_bez_selektoru_tiskne_vse():
    ctx = _vlan_aware_ctx(_aware_subject())
    ctx.scope.selectors.interfaces = []
    findings = EvpnInstanceStatusCheck().run(ctx)
    esi_rows = [f for f in findings if f.label.startswith("ESI ")]
    assert len(esi_rows) == 2
