from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.reachability import (
    ArpPresentCheck,
    NdPresentCheck,
    PingReachabilityCheck,
)
from migration_validator.config import default_config
from migration_validator.models.result import Outcome, Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope


def _ctx(subject, service_type="IPVPN", scope=None):
    scope = scope or Scope(
        id="svc:X:" + service_type,
        kind="service",
        key=ScopeKey("X", service_type, None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"], local_ipv4=["198.11.13.1/30"]
        ),
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=None,
        config=default_config(),
        failed_collectors={},
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
    assert result.status is Status.WARN


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
    ctx = _ctx({"ping": [], "ping_skipped": True})
    findings = PingReachabilityCheck().run(ctx)
    assert len(findings) == 1
    assert findings[0].outcome is Outcome.SKIP
    assert "mimo profil" in findings[0].message


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
