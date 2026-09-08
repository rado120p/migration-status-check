import pytest

from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.reachability import (
    ArpPresentCheck,
    NdPresentCheck,
    PingReachabilityCheck,
)
from migration_validator.config import default_config
from migration_validator.models.result import Outcome, Severity, Status, UNCHANGED_SINCE_BASELINE
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope


def _ctx(subject, service_type="IPVPN", scope=None, baseline=None):
    scope = scope or Scope(
        id="svc:X:" + service_type,
        kind="service",
        key=ScopeKey("X", service_type, None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"], local_ipv4=["198.11.13.1/30"]
        ),
    )
    baseline_collectors = (
        {area: {"status": "ok"} for area in baseline} if baseline is not None else {}
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=baseline,
        config=default_config(),
        failed_collectors={},
        baseline_collectors=baseline_collectors,
    )


def test_arp_present_passes_with_entries():
    ctx = _ctx({"arp": [{"ip": "198.11.13.2", "interface": "ge-0/0/2.113"}]})
    result = run_check(ArpPresentCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert "198.11.13.2" in result.message


def test_arp_value_shows_learned_via():
    ctx = _ctx({"arp": [{"ip": "152.11.14.4", "mac": "0c:00:ca:ea:58:03",
                          "interface": "irb.14", "learned_via": "ae0.14"}]})
    result = run_check(ArpPresentCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert "[via ae0.14]" in result.value


def test_arp_value_without_learned_via_unchanged():
    ctx = _ctx({"arp": [{"ip": "1.2.3.4", "mac": "aa:bb", "interface": "ge-0/0/4.0",
                          "learned_via": None}]})
    result = run_check(ArpPresentCheck(), ctx)[0]
    assert "[via" not in result.value


def test_arp_empty_warns():
    result = run_check(ArpPresentCheck(), _ctx({"arp": []}))[0]
    assert result.status is Status.FAIL


def test_arp_not_run_on_core_scope():
    assert run_check(ArpPresentCheck(), _ctx({"arp": []}, service_type="Core")) == []


def test_arp_skips_on_device_scope():
    ctx = _ctx({"arp": []}, scope=device_scope())
    result = run_check(ArpPresentCheck(), ctx)[0]
    assert result.status is Status.SKIP
    assert "inventory" in result.message


def test_arp_says_nothing_when_no_ipv4_configured():
    """Sluzba bez IPv4 adresy nema co s ARP overovat - a nema o tom ani
    mlcet nahlas.

    Rozhodnuti R-1 (varianta c): rodina, kterou sluzba nema
    nakonfigurovanou, se v bloku neobjevi vubec - ani sekci, ani radkem.
    Drive tu byl SKIP se znackou family=4, ktery si tu sekci vynutil, a
    prazdna sekce rodiny byla presne to, co spec zakazuje.

    Cena rozhodnuti: chybejici check je v reportu k nerozeznani od checku,
    ktery prosel. Vedome prijato.
    """
    scope = Scope(
        id="svc:X:IPVPN",
        kind="service",
        key=ScopeKey("X", "IPVPN", None),
        selectors=Selectors(interfaces=["ge-0/0/2.113"]),
    )
    ctx = _ctx({"arp": []}, scope=scope)

    assert ArpPresentCheck().run(ctx) == []


def test_arp_reports_broken_when_ipv4_configured_but_no_entries():
    """Rozliseni od predchoziho testu: adresa je, zaznam neni - to uz je BROKEN."""
    ctx = _ctx({"arp": []})

    findings = ArpPresentCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN


def test_ping_all_targets_reachable_passes():
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "198.11.13.2",
                    "family": 4,
                    "sent": 5,
                    "received": 5,
                    "loss_percent": 0,
                },
                {
                    "target": "198.11.13.3",
                    "family": 4,
                    "sent": 5,
                    "received": 5,
                    "loss_percent": 0,
                },
            ]
        }
    )
    results = run_check(PingReachabilityCheck(), ctx)
    assert len(results) == 2
    assert all(result.status is Status.PASS for result in results)
    assert all(result.family == 4 for result in results)


def test_ping_ok_value_carries_target():
    """Produkce: u uspesneho radku nebylo poznat, KAM ping sel - cil
    nesla jen message a BROKEN vetev. Hodnota ma tvar '5/5  2.1 ms  IP'."""
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "198.11.13.2",
                    "family": 4,
                    "sent": 5,
                    "received": 5,
                    "rtt_avg_ms": 2.1,
                }
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.value == "5/5  2.1 ms  198.11.13.2"


def test_ping_ok_value_without_rtt_still_carries_target():
    ctx = _ctx(
        {"ping": [{"target": "198.11.13.2", "family": 4, "sent": 5, "received": 5}]}
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.value == "5/5  198.11.13.2"


def test_ping_old_snapshot_record_with_source_key_still_evaluates():
    """Schema zustava 9: stare snimky maji v ping zaznamech klic 'source',
    nove uz ne - smiseny par pre(se source) x post(bez) musi vyhodnotit
    stejne. Klic nikdo necte, tenhle test to prikovava."""
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "198.11.13.2",
                    "source": "198.11.13.1",
                    "family": 4,
                    "sent": 5,
                    "received": 5,
                    "rtt_avg_ms": 2.1,
                }
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert result.value == "5/5  2.1 ms  198.11.13.2"


def test_ping_partial_success_is_one_broken_finding_per_target():
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "198.11.13.2",
                    "family": 4,
                    "sent": 5,
                    "received": 5,
                    "loss_percent": 0,
                },
                {
                    "target": "198.11.13.3",
                    "family": 4,
                    "sent": 5,
                    "received": 0,
                    "loss_percent": 100,
                },
                {
                    "target": "198.11.13.4",
                    "family": 4,
                    "sent": 5,
                    "received": 5,
                    "loss_percent": 0,
                },
            ]
        }
    )
    results = run_check(PingReachabilityCheck(), ctx)
    assert len(results) == 3
    by_target = {result.subject["target"]: result for result in results}
    assert by_target["198.11.13.2"].status is Status.PASS
    assert by_target["198.11.13.3"].status is Status.WARN
    assert by_target["198.11.13.4"].status is Status.PASS
    assert "198.11.13.3" in by_target["198.11.13.3"].message


def test_ping_no_target_reachable_warns_as_advisory():
    ctx = _ctx({"ping": [{"target": "198.11.13.2", "family": 4, "sent": 5, "received": 0}]})
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.WARN


def test_ping_without_targets_skips():
    result = run_check(PingReachabilityCheck(), _ctx({"ping": []}))[0]
    assert result.status is Status.SKIP
    assert "cile" in result.message


def test_ping_oversized_subnet_without_target_says_why():
    """irb.4094 (lab 172.20.20.4): 10.40.94/24 ma ARP cile, 10.40.95/24 nic.

    Subnet vetsi nez /30 zadny fallback nedostane (hadani) - ale mlceni by
    se cetlo jako "zkontrolovano OK". Report musi rict, ze subnet zustal
    bez cile a proc.
    """
    scope = Scope(
        id="svc:irb.4094:IPVPN",
        kind="service",
        key=ScopeKey("irb.4094", "IPVPN", None),
        selectors=Selectors(
            interfaces=["irb.4094"],
            local_ipv4=["10.40.94.253/24", "10.40.95.253/24"],
        ),
    )
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "10.40.94.2",
                    "family": 4,
                    "sent": 5,
                    "received": 5,
                    "loss_percent": 0,
                }
            ]
        },
        scope=scope,
    )

    findings = PingReachabilityCheck().run(ctx)

    skips = [f for f in findings if f.outcome is Outcome.SKIP]
    assert len(skips) == 1
    assert "10.40.95.0/24" in skips[0].message
    assert "subnet > /30" in skips[0].value
    assert skips[0].family == 4


def test_ping_no_probes_oversized_subnet_gets_reason_not_generic_skip():
    scope = Scope(
        id="svc:irb.4094:IPVPN",
        kind="service",
        key=ScopeKey("irb.4094", "IPVPN", None),
        selectors=Selectors(
            interfaces=["irb.4094"], local_ipv4=["10.40.95.253/24"]
        ),
    )
    findings = PingReachabilityCheck().run(_ctx({"ping": []}, scope=scope))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.SKIP
    assert "subnet > /30" in findings[0].value


def test_ping_oversized_ipv6_subnet_reason_uses_ipv6_threshold():
    scope = Scope(
        id="svc:irb.15:IPVPN",
        kind="service",
        key=ScopeKey("irb.15", "IPVPN", None),
        selectors=Selectors(interfaces=["irb.15"], local_ipv6=["2001:db8::1/64"]),
    )
    findings = PingReachabilityCheck().run(_ctx({"ping": []}, scope=scope))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.SKIP
    assert "subnet > /126" in findings[0].value
    assert findings[0].family == 6


def test_ping_mimo_profil_je_skip_s_duvodem():
    """Prazdne pingy s markerem ping_skipped jsou vedomy vynechani profilem,
    ne chybejici cil - zprava musi rozlisit proc."""
    ctx = _ctx({"ping": [], "ping_skipped": [{"scope_id": "svc:x", "reason": "mimo profil"}]})
    findings = PingReachabilityCheck().run(ctx)
    assert len(findings) == 1
    assert findings[0].outcome is Outcome.SKIP
    assert "mimo profil" in findings[0].message


def test_ping_out_of_profile_names_profile():
    """Kdyz capture zaznamenala jmeno profilu u ping_skipped markeru, report
    ho musi ukazat - "mimo profil" samo o sobe nerika, ktereho."""
    ctx = _ctx(
        {
            "ping": [],
            "ping_skipped": [
                {"scope_id": "svc:x", "reason": "mimo profil", "profile": "core-only"}
            ],
        }
    )
    findings = PingReachabilityCheck().run(ctx)
    assert len(findings) == 1
    assert findings[0].outcome is Outcome.SKIP
    assert findings[0].message == "ping neproveden - mimo profil (core-only)"
    assert findings[0].value == "mimo profil (core-only)"


def test_ping_out_of_profile_without_name_falls_back():
    """Bez jmena profilu (napr. default profil s prazdnym nazvem) drzime
    puvodni text - nefabrikujeme jmeno, ktere capture neposlala."""
    ctx = _ctx(
        {
            "ping": [],
            "ping_skipped": [{"scope_id": "svc:x", "reason": "mimo profil"}],
        }
    )
    findings = PingReachabilityCheck().run(ctx)
    assert len(findings) == 1
    assert findings[0].message == "ping neproveden - mimo profil"
    assert findings[0].value == "mimo profil"


def test_ping_zero_sent_is_skip():
    """Nic neodeslano neni totez jako "odeslano a bez odpovedi" - ping proste
    nebehl."""
    ctx = _ctx(
        {"ping": [{"target": "10.0.0.2", "family": 4, "sent": 0, "received": 0}]}
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.SKIP
    assert result.message == "10.0.0.2: ping neodeslan"
    assert result.value == "10.0.0.2 neodeslan"


def test_ping_probe_without_family_skips_instead_of_vanishing():
    """Probe bez rodiny nesmi tise zmizet - check musi zustat v poli checku.

    ping.py zatim nenastavuje "family" (dalsi task) - do te doby to musi
    check hlasit jako SKIP, ne ho proste vynechat z vysledku.
    """
    ctx = _ctx({"ping": [{"target": "198.11.13.2", "sent": 5, "received": 5}]})

    results = run_check(PingReachabilityCheck(), ctx)

    assert any(result.id == "ping_reachability" for result in results)
    assert results[0].status is Status.SKIP
    assert "rodin" in results[0].message


def test_ping_records_fallback_resolution():
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "198.11.13.2",
                    "family": 4,
                    "sent": 5,
                    "received": 5,
                    "resolved_from": "subnet-fallback",
                }
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.details["resolved_from"] == "subnet-fallback"


def test_ping_ipv6_target_gets_family_6():
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "2001:abcd:11:13::b",
                    "family": 6,
                    "sent": 5,
                    "received": 5,
                }
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert result.family == 6


# --- Nove testy z Tasku 7: rodiny, MAC a hodnoty (ARP/ND), link-local pravidlo ---


def _service_scope() -> Scope:
    return Scope(
        id="svc:INTERNET-CPE13-NNI:Internet",
        kind="service",
        key=ScopeKey(description="INTERNET-CPE13-NNI", service_type="Internet"),
        selectors=Selectors(
            interfaces=["et-0/0/8.13"],
            local_ipv4=["152.11.13.1/30"],
            local_ipv6=["2001:abcd:11:13::a/127"],
        ),
    )


def _service_ctx(subject: dict) -> CheckContext:
    return CheckContext(
        scope=_service_scope(),
        subject=subject,
        baseline=None,
        config=default_config(),
    )


def test_arp_finding_shows_mac_and_family():
    ctx = _service_ctx(
        {
            "arp": [
                {
                    "ip": "152.11.13.2",
                    "mac": "0c:00:ef:5e:df:01",
                    "interface": "et-0/0/8.13",
                    "routing_instance": None,
                }
            ]
        }
    )

    findings = ArpPresentCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].family == 4
    assert findings[0].label == "ARP"
    assert findings[0].value == "0c:00:ef:5e:df:01 -> 152.11.13.2"
    assert findings[0].outcome is Outcome.OK


def test_nd_finding_shows_mac_and_family():
    ctx = _service_ctx(
        {
            "nd": [
                {
                    "ip": "2001:abcd:11:13::b",
                    "mac": "0c:00:ef:5e:df:01",
                    "interface": "et-0/0/8.13",
                    "state": "reachable",
                }
            ]
        }
    )

    findings = NdPresentCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].family == 6
    assert findings[0].label == "ND"
    assert findings[0].value == "0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b"
    assert findings[0].outcome is Outcome.OK


def test_nd_says_nothing_when_no_ipv6_configured():
    """Zrcadli test_arp_says_nothing_when_no_ipv4_configured.

    Tenhle check byl mistem srazky, na ktere R-1 vzniklo: znackoval svou
    rodinu i u sluzby, ktera tu rodinu nakonfigurovanou nema, a vynutil si
    tak prazdnou IPv6 sekci u kazde ciste IPv4 sluzby - tedy u vetsiny.
    """
    scope = Scope(
        id="svc:X:IPVPN",
        kind="service",
        key=ScopeKey("X", "IPVPN", None),
        selectors=Selectors(interfaces=["et-0/0/8.13"]),
    )
    ctx = CheckContext(
        scope=scope,
        subject={"nd": []},
        baseline=None,
        config=default_config(),
    )

    assert NdPresentCheck().run(ctx) == []


def test_nd_ignores_link_local_when_not_configured():
    ctx = _service_ctx(
        {
            "nd": [
                {
                    "ip": "fe80::c66b:b8ff:fe48:0",
                    "mac": "c4:6b:b8:48:00:00",
                    "interface": "et-0/0/8.13",
                    "state": "stale",
                }
            ]
        }
    )

    findings = NdPresentCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN


def test_nd_keeps_link_local_when_configured():
    scope = _service_scope()
    scope.selectors.local_ipv6 = ["fe80::1/64"]
    ctx = CheckContext(
        scope=scope,
        subject={
            "nd": [
                {
                    "ip": "fe80::c66b:b8ff:fe48:0",
                    "mac": "c4:6b:b8:48:00:00",
                    "interface": "et-0/0/8.13",
                    "state": "stale",
                }
            ]
        },
        baseline=None,
        config=default_config(),
    )

    findings = NdPresentCheck().run(ctx)

    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "c4:6b:b8:48:00:00 -> fe80::c66b:b8ff:fe48:0"


def test_arp_without_entries_is_broken():
    findings = ArpPresentCheck().run(_service_ctx({"arp": []}))

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].family == 4


def test_arp_zero_mac_is_broken():
    """Incomplete ARP zaznam (nulovy MAC) neni "zadny zaznam" - je to
    konkretni, ohlaseny stav a report ho musi ukazat jako FAIL."""
    ctx = _service_ctx(
        {
            "arp": [
                {
                    "ip": "152.11.13.2",
                    "mac": "00:00:00:00:00:00",
                    "interface": "et-0/0/8.13",
                }
            ]
        }
    )

    findings = ArpPresentCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].message == "ARP zaznam 152.11.13.2 neni resolved (incomplete)"
    assert findings[0].value == "incomplete -> 152.11.13.2"


def test_arp_mixed_list_gives_one_ok_and_one_broken_row():
    """Mix resolved + incomplete zaznamu ma dat po jednom radku za kazdy,
    ne 'zadny zaznam' (ten je jen pro prazdny seznam)."""
    ctx = _service_ctx(
        {
            "arp": [
                {"ip": "152.11.13.1", "mac": "0c:00:ca:ea:58:03", "interface": "et-0/0/8.13"},
                {"ip": "152.11.13.2", "mac": "00:00:00:00:00:00", "interface": "et-0/0/8.13"},
            ]
        }
    )

    findings = ArpPresentCheck().run(ctx)

    assert len(findings) == 2
    by_outcome = {f.outcome: f for f in findings}
    assert set(by_outcome) == {Outcome.OK, Outcome.BROKEN}
    assert by_outcome[Outcome.BROKEN].message == "ARP zaznam 152.11.13.2 neni resolved (incomplete)"
    assert all(f.value != "zadny zaznam" for f in findings)


def test_nd_mixed_list_gives_one_ok_and_one_broken_row():
    """Mix reachable + incomplete zaznamu ma dat po jednom radku za kazdy,
    ne 'zadny pouzitelny ND zaznam'."""
    ctx = _service_ctx(
        {
            "nd": [
                {"ip": "2001:abcd:11:13::a", "mac": "0c:00:ca:ea:58:03",
                 "state": "reachable", "interface": "et-0/0/8.13"},
                {"ip": "2001:abcd:11:13::b", "mac": None,
                 "state": "incomplete", "interface": "et-0/0/8.13"},
            ]
        }
    )

    findings = NdPresentCheck().run(ctx)

    assert len(findings) == 2
    by_outcome = {f.outcome: f for f in findings}
    assert set(by_outcome) == {Outcome.OK, Outcome.BROKEN}
    assert by_outcome[Outcome.BROKEN].message == "ND zaznam 2001:abcd:11:13::b neni resolved (incomplete)"
    assert all(f.value != "zadny zaznam" for f in findings)


@pytest.mark.parametrize("state", ["incomplete", "unreachable"])
def test_nd_unresolved_states_are_broken(state):
    ctx = _service_ctx(
        {
            "nd": [
                {
                    "ip": "2001:abcd:11:13::b",
                    "mac": None,
                    "state": state,
                    "interface": "et-0/0/8.13",
                }
            ]
        }
    )

    findings = NdPresentCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].message == f"ND zaznam 2001:abcd:11:13::b neni resolved ({state})"
    assert findings[0].value == f"{state} -> 2001:abcd:11:13::b"


def test_finding_records_which_configured_range_it_belongs_to():
    """Report tim popisuje radky u sluzby s vic rozsahy jedne rodiny."""
    ctx = _service_ctx(
        {
            "arp": [
                {
                    "ip": "152.11.13.2",
                    "mac": "0c:00:ef:5e:df:01",
                    "interface": "et-0/0/8.13",
                    "routing_instance": None,
                }
            ]
        }
    )

    findings = ArpPresentCheck().run(ctx)

    assert findings[0].details["address"] == "152.11.13.1/30"


def test_owning_prefix_picks_the_containing_range():
    from migration_validator.checks.reachability import owning_prefix

    prefixes = ["152.11.13.1/30", "152.11.20.1/29"]

    assert owning_prefix("152.11.20.2", prefixes) == "152.11.20.1/29"
    assert owning_prefix("10.9.9.9", prefixes) is None
    assert owning_prefix("2001:db8::1", prefixes) is None


def test_arp_unresolved_row_is_fail():
    """Nerozreseny zaznam je konkretni porucha; check je critical, takze FAIL."""
    ctx = _service_ctx(
        {"arp": [{"ip": "152.11.13.2", "mac": "00:00:00:00:00:00", "interface": "et-0/0/8.13"}]}
    )

    result = run_check(ArpPresentCheck(), ctx)[0]

    assert result.status is Status.FAIL
    assert result.severity is Severity.CRITICAL


def test_nd_unresolved_row_is_fail():
    ctx = _service_ctx(
        {"nd": [{"ip": "2001:abcd:11:13::b", "mac": None, "state": "incomplete", "interface": "et-0/0/8.13"}]}
    )

    result = run_check(NdPresentCheck(), ctx)[0]

    assert result.status is Status.FAIL
    assert result.severity is Severity.CRITICAL


def test_arp_empty_table_is_fail():
    """Sluzba s IPv4 bez jedineho ARP zaznamu nefunguje - FAIL, ne WARN (2026-09-03)."""
    result = run_check(ArpPresentCheck(), _service_ctx({"arp": []}))[0]

    assert result.status is Status.FAIL
    assert result.severity is Severity.CRITICAL


@pytest.mark.parametrize("check_class", [ArpPresentCheck, NdPresentCheck, PingReachabilityCheck])
@pytest.mark.parametrize("service_type,subtype", [("Internet", "multicast"), ("IPVPN", "mvpn")])
def test_reachability_checks_do_not_apply_to_multicast_services(check_class, service_type, subtype):
    scope = Scope(id="svc:M:" + service_type, kind="service",
                  key=ScopeKey("M", service_type, subtype),
                  selectors=Selectors(interfaces=["ge-0/0/2.11"], local_ipv4=["10.1.1.1/30"]))
    assert check_class().applies_to(scope) is False
    assert run_check(check_class(), _ctx({"arp": [], "nd": [], "ping": []}, scope=scope)) == []


# --- Task 6: BOTH mod, baseline porovnani (UNCHANGED) ---


def test_arp_empty_in_both_is_unchanged_pass():
    [row] = run_check(ArpPresentCheck(), _ctx({"arp": []}, baseline={"arp": []}))
    assert row.status is Status.PASS and row.details[UNCHANGED_SINCE_BASELINE] is True
    assert row.value == "zadny zaznam" == row.baseline_value


def test_arp_empty_now_present_before_is_fail():
    before = {"arp": [{"ip": "198.11.13.2", "mac": "0c:00:00:00:00:01", "interface": "ge-0/0/2.113"}]}
    [row] = run_check(ArpPresentCheck(), _ctx({"arp": []}, baseline=before))
    assert row.status is Status.FAIL and row.baseline_value == "1 zaznam"


def test_arp_incomplete_in_both_is_unchanged():
    entry = {"ip": "198.11.13.2", "mac": "00:00:00:00:00:00", "interface": "ge-0/0/2.113"}
    [row] = run_check(ArpPresentCheck(), _ctx({"arp": [entry]}, baseline={"arp": [entry]}))
    assert row.status is Status.PASS and row.value == row.baseline_value


def test_arp_ok_row_baseline_value_is_same_entry_text():
    entry = {"ip": "198.11.13.2", "mac": "0c:00:00:00:00:01", "interface": "ge-0/0/2.113"}
    [row] = run_check(ArpPresentCheck(), _ctx({"arp": [entry]}, baseline={"arp": [entry]}))
    assert row.baseline_value == row.value


def test_nd_unreachable_in_both_is_unchanged():
    entry = {"ip": "2001:db8:11:13::2", "mac": "none", "interface": "ge-0/0/2.113", "state": "unreachable"}
    scope = Scope(id="svc:X:IPVPN", kind="service", key=ScopeKey("X", "IPVPN", None),
                  selectors=Selectors(interfaces=["ge-0/0/2.113"], local_ipv6=["2001:db8:11:13::1/64"]))
    [row] = run_check(NdPresentCheck(), _ctx({"nd": [entry]}, baseline={"nd": [entry]}, scope=scope))
    assert row.status is Status.PASS and row.value == "unreachable -> 2001:db8:11:13::2" == row.baseline_value


def test_ping_failed_in_both_is_unchanged_warn_becomes_pass():
    probe = {"scope_id": "svc:X:IPVPN", "target": "198.11.13.2", "family": 4, "sent": 5, "received": 0}
    [row] = run_check(PingReachabilityCheck(), _ctx({"ping": [probe]}, baseline={"ping": [probe]}))
    assert row.status is Status.PASS and row.details[UNCHANGED_SINCE_BASELINE] is True
    assert row.baseline_value == "0/5  198.11.13.2 neodpovedel"


def test_ping_ok_row_baseline_value_same_shape():
    probe = {"scope_id": "svc:X:IPVPN", "target": "198.11.13.2", "family": 4, "sent": 5, "received": 5, "rtt_avg_ms": 1.0}
    [row] = run_check(PingReachabilityCheck(), _ctx({"ping": [probe]}, baseline={"ping": [probe]}))
    assert row.baseline_value == row.value
