"""Checky multicastu (spec 2026-09-02). Idiom: realny Scope, CheckContext,
primy run(). Baseline scope nese jina jmena rozhrani (ge- vs et-), proto
se IGMP mnozina baseline cte pres ctx.baseline_scope."""

from __future__ import annotations

from migration_validator.checks.base import CheckContext
from migration_validator.checks.multicast import (
    NO_REPORT,
    NO_REPORT_SKIP,
    RATE_UNAVAILABLE,
    CoreMulticastForwardingCheck,
    IgmpMembershipReportCheck,
    MulticastForwardingStatusCheck,
    MvpnCmulticastStatusCheck,
    assign_sources,
    format_uptime_hms,
    igmp_pairs,
    routes_for,
    sg_label,
    stream_rows,
)
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors

POST = "et-0/0/8.11"
PRE = "ge-0/0/2.11"
SG = ("10.11.11.1", "232.1.1.1")


def _scope(interface=POST, service_type="Internet", subtype="multicast", instances=(),
           static_routes=()):
    return Scope(
        id=f"svc:x:{service_type}", kind="service",
        key=ScopeKey("x", service_type, subtype),
        selectors=Selectors(
            interfaces=[interface], routing_instances=list(instances),
            static_routes=[dict(r) for r in static_routes],
        ),
    )


def _ctx(subject, baseline=None, scope=None, baseline_scope=None):
    return CheckContext(
        scope=scope or _scope(), subject=subject, baseline=baseline,
        config=default_config(), baseline_scope=baseline_scope,
    )


def _igmp(iface, *pairs):
    return {"igmp_group": {iface: [{"source": s, "group": g} for s, g in pairs]}}


# --- helpery --------------------------------------------------------------

def test_sg_label_and_asm():
    assert sg_label("10.11.11.1", "232.1.1.1") == "(10.11.11.1, 232.1.1.1)"
    assert sg_label(None, "239.1.1.1") == "(*, 239.1.1.1)"


def test_igmp_pairs_is_sorted_unique_and_scoped():
    facts = {"igmp_group": {
        POST: [{"source": "10.0.0.2", "group": "232.0.0.2"},
               {"source": "10.0.0.1", "group": "232.0.0.1"},
               {"source": "10.0.0.1", "group": "232.0.0.1"}],
        "irb.9": [{"source": "1.1.1.1", "group": "239.9.9.9"}],
    }}
    assert igmp_pairs(facts, _scope()) == [("10.0.0.1", "232.0.0.1"), ("10.0.0.2", "232.0.0.2")]
    assert igmp_pairs(None, _scope()) == []


def test_routes_for_exact_and_asm():
    table = {"10.0.0.1,232.0.0.1": {"a": 1}, "10.0.0.2,232.0.0.1": {"b": 2}}
    assert routes_for(table, "10.0.0.1", "232.0.0.1") == [("10.0.0.1,232.0.0.1", {"a": 1})]
    assert [k for k, _ in routes_for(table, None, "232.0.0.1")] == [
        "10.0.0.1,232.0.0.1", "10.0.0.2,232.0.0.1"]
    assert routes_for(table, "9.9.9.9", "232.0.0.1") == []


def test_format_uptime_hms():
    assert format_uptime_hms(3266) == "00:54:26"
    assert format_uptime_hms(93784) == "1d 02:03:04"
    assert format_uptime_hms(None) == "-"


def test_stream_rows_skip_when_rate_missing():
    """junos-evo: multicast-statistics-timed-out, absence neni nula.
    Mutant kill (2026-09-03, overeno spustenim): 'raw_pps is None' ->
    'raw_pps == 0' (TypeError v int(None), padne cely test)."""
    for route in ({}, {"forwarding_rate_pps": None}):
        rate_row, uptime_row = stream_rows("(*, 232.1.1.1)", route, rate_label="Upstream")
        assert rate_row.outcome is Outcome.SKIP
        assert rate_row.value == RATE_UNAVAILABLE

    rate_row, _ = stream_rows(
        "(*, 232.1.1.1)", {"forwarding_rate_pps": 0}, rate_label="Upstream",
    )
    assert rate_row.outcome is Outcome.BROKEN
    assert rate_row.value == "0 pps"


# --- igmp_membership_report -----------------------------------------------

def test_igmp_report_pass_lists_pairs():
    (row,) = IgmpMembershipReportCheck().run(_ctx(_igmp(POST, SG)))
    assert row.outcome is Outcome.OK
    assert row.label == "IGMP membership report"
    assert row.value == "(10.11.11.1, 232.1.1.1)"


def test_igmp_report_missing_is_fail():
    (row,) = IgmpMembershipReportCheck().run(_ctx({"igmp_group": {}}))
    assert row.outcome is Outcome.BROKEN
    assert row.value == NO_REPORT


def test_igmp_report_same_set_against_baseline_is_pass():
    (row,) = IgmpMembershipReportCheck().run(_ctx(
        _igmp(POST, SG), baseline=_igmp(PRE, SG),
        baseline_scope=_scope(PRE),
    ))
    assert row.outcome is Outcome.OK
    assert row.baseline_value == "(10.11.11.1, 232.1.1.1)"


def test_igmp_report_changed_set_is_warn():
    """Mutant kill (2026-09-03, overeno spustenim): nahrazeni DEGRADED -> OK
    ve vetvi 'mnozina se lisi' tenhle test polozi."""
    (row,) = IgmpMembershipReportCheck().run(_ctx(
        _igmp(POST, SG), baseline=_igmp(PRE, SG, ("10.11.11.2", "232.1.1.2")),
        baseline_scope=_scope(PRE),
    ))
    assert row.outcome is Outcome.DEGRADED
    assert row.baseline_value == "(10.11.11.1, 232.1.1.1), (10.11.11.2, 232.1.1.2)"


def test_igmp_report_baseline_without_groups_is_not_compared():
    (row,) = IgmpMembershipReportCheck().run(_ctx(
        _igmp(POST, SG), baseline={"igmp_group": {}}, baseline_scope=_scope(PRE),
    ))
    assert row.outcome is Outcome.OK
    assert row.baseline_value is None


def test_igmp_check_applies_only_to_multicast_subtypes():
    check = IgmpMembershipReportCheck()
    assert check.applies_to(_scope())
    assert check.applies_to(_scope("irb.2", "IPVPN", "mvpn-igmp", ["RI"]))
    assert not check.applies_to(_scope("et-0/0/8.13", "Internet", None))
    assert not check.applies_to(_scope("irb.3", "IPVPN", None, ["RI"]))
    assert not check.applies_to(_scope("lo0.0", "Core", "loopback"))


# --- multicast_forwarding_status -------------------------------------------

def _route(upstream="et-0/0/0.0", downstream=(POST,), pps=6, uptime=3266):
    return {
        "upstream_interface": upstream, "downstream_interfaces": list(downstream),
        "forwarding_rate_pps": pps, "uptime_seconds": uptime,
        "state": "Active", "forwarding_state": "Forwarding",
    }


def _facts(iface=POST, instance="master", pairs=(SG,), routes=None):
    facts = _igmp(iface, *pairs)
    facts["multicast_route"] = {instance: routes if routes is not None else {
        f"{s},{g}": _route() for s, g in pairs}}
    return facts


def _by_label(findings, group):
    return {f.label: f for f in findings if f.group == group}


def test_forwarding_pass_block_shape():
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts()))
    summary = findings[0]
    assert summary.outcome is Outcome.OK
    assert summary.label == "Multicast forwarding status"
    assert summary.value == "1 S,G"
    assert summary.group is None
    sg = sg_label(*SG)
    rows = _by_label(findings, sg)
    assert [f.label for f in findings[1:]] == ["Stream", "Upstream interface", "Forwarding-rate", "Route uptime"]
    assert rows["Stream"].outcome is Outcome.OK
    assert rows["Stream"].value == f"Stream se na {POST} posila"
    assert rows["Upstream interface"].value == "et-0/0/0.0"
    assert rows["Forwarding-rate"].value == "6 pps"
    assert rows["Route uptime"].outcome is Outcome.INFO
    assert rows["Route uptime"].value == "00:54:26"


def test_forwarding_without_igmp_is_single_skip():
    findings = MulticastForwardingStatusCheck().run(_ctx(
        {"igmp_group": {}, "multicast_route": {"master": {f"{SG[0]},{SG[1]}": _route()}}}))
    assert [(f.outcome, f.value) for f in findings] == [(Outcome.SKIP, NO_REPORT_SKIP)]


def test_forwarding_sg_missing_from_table():
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes={})))
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "1/1 S,G nefunguje"
    assert len(findings) == 2
    assert findings[1].label == "Stream"
    assert findings[1].value == "S,G neni v multicast tabulce"


def test_forwarding_downstream_without_service_interface_fails_stream_row():
    """Mutant kill (2026-09-03, overeno spustenim): 'iface in downstream'
    -> 'bool(downstream)' by proslo s libovolnym neprazdnym downstream."""
    routes = {f"{SG[0]},{SG[1]}": _route(downstream=["et-0/0/8.99"])}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes=routes)))
    rows = _by_label(findings, sg_label(*SG))
    assert rows["Stream"].outcome is Outcome.BROKEN
    assert rows["Stream"].value == f"S,G je v tabulce ale stream se na {POST} neposila"
    assert findings[0].outcome is Outcome.BROKEN


def test_forwarding_internet_upstream_must_be_transit():
    """Mutant kill (2026-09-03, overeno spustenim, spolu s
    test_forwarding_mvpn_upstream_must_be_lsi_or_vt): '_upstream_ok' vraci
    True vzdy."""
    routes = {f"{SG[0]},{SG[1]}": _route(upstream="lsi.1048576")}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes=routes)))
    row = _by_label(findings, sg_label(*SG))["Upstream interface"]
    assert row.outcome is Outcome.BROKEN
    assert row.value == "lsi.1048576"
    assert row.message == (
        "(10.11.11.1, 232.1.1.1): upstream lsi.1048576 neni z ocekavane role "
        "(ocekavano ge-/xe-/et-/ae)"
    )


def test_upstream_wrong_role_uses_mvpn_prefixes():
    """Mvpn-igmp scope hlasi jinou ocekavanou roli nez Internet/multicast."""
    scope = _scope("irb.2", "IPVPN", "mvpn-igmp", ["RI"])
    routes = {"10.12.12.1,239.1.1.1": _route(upstream="et-0/0/0.0", downstream=["irb.2"])}
    pair = (("10.12.12.1", "239.1.1.1"),)
    findings = MulticastForwardingStatusCheck().run(
        _ctx(_facts("irb.2", "RI", pair, routes), scope=scope))
    row = _by_label(findings, "(10.12.12.1, 239.1.1.1)")["Upstream interface"]
    assert row.outcome is Outcome.BROKEN
    assert row.message == (
        "(10.12.12.1, 239.1.1.1): upstream et-0/0/0.0 neni z ocekavane role "
        "(ocekavano lsi./vt-)"
    )


def test_upstream_missing_message_unchanged():
    routes = {f"{SG[0]},{SG[1]}": _route(upstream=None)}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes=routes)))
    row = _by_label(findings, sg_label(*SG))["Upstream interface"]
    assert row.outcome is Outcome.BROKEN
    assert row.message == (
        "(10.11.11.1, 232.1.1.1): upstream - - S,G je v tabulce ale nema "
        "upstream interface"
    )


def test_forwarding_mvpn_upstream_must_be_lsi_or_vt():
    """Mutant kill (2026-09-03, overeno spustenim, spolu s
    test_forwarding_internet_upstream_must_be_transit): '_upstream_ok'
    vraci True vzdy."""
    scope = _scope("irb.2", "IPVPN", "mvpn-igmp", ["RI"])
    ok = {"10.12.12.1,239.1.1.1": _route(upstream="lsi.1048576", downstream=["irb.2"])}
    bad = {"10.12.12.1,239.1.1.1": _route(upstream="et-0/0/0.0", downstream=["irb.2"])}
    pair = (("10.12.12.1", "239.1.1.1"),)
    good = MulticastForwardingStatusCheck().run(_ctx(_facts("irb.2", "RI", pair, ok), scope=scope))
    wrong = MulticastForwardingStatusCheck().run(_ctx(_facts("irb.2", "RI", pair, bad), scope=scope))
    assert _by_label(good, "(10.12.12.1, 239.1.1.1)")["Upstream interface"].outcome is Outcome.OK
    assert _by_label(wrong, "(10.12.12.1, 239.1.1.1)")["Upstream interface"].outcome is Outcome.BROKEN


def test_forwarding_missing_upstream_renders_dash():
    routes = {f"{SG[0]},{SG[1]}": _route(upstream=None)}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes=routes)))
    row = _by_label(findings, sg_label(*SG))["Upstream interface"]
    assert row.outcome is Outcome.BROKEN and row.value == "-"


def test_forwarding_zero_rate_fails_rate_row_and_summary():
    routes = {f"{SG[0]},{SG[1]}": _route(pps=0)}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes=routes)))
    assert _by_label(findings, sg_label(*SG))["Forwarding-rate"].outcome is Outcome.BROKEN
    assert findings[0].value == "1/1 S,G nefunguje"


def test_forwarding_asm_group_matches_every_source():
    routes = {"10.0.0.1,239.5.5.5": _route(), "10.0.0.2,239.5.5.5": _route()}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(pairs=((None, "239.5.5.5"),), routes=routes)))
    assert findings[0].value == "1 S,G"
    assert {f.group for f in findings[1:]} == {"(10.0.0.1, 239.5.5.5)", "(10.0.0.2, 239.5.5.5)"}


def test_forwarding_two_streams_one_broken():
    second = ("10.11.11.2", "232.1.1.2")
    routes = {f"{SG[0]},{SG[1]}": _route(), f"{second[0]},{second[1]}": _route(downstream=[])}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(pairs=(SG, second), routes=routes)))
    assert findings[0].value == "1/2 S,G nefunguje"


def test_forwarding_asm_failed_pair_counts_once():
    """Bug fix 2026-09-03 (finding 1): ASM (*, G) IGMP zaznam odpovidajici
    dvema rozbitym routam ma souhrn 1/1, ne 2/1 - par se pocita jednou."""
    routes = {
        "10.0.0.1,239.5.5.5": _route(downstream=[]),
        "10.0.0.2,239.5.5.5": _route(downstream=[]),
    }
    findings = MulticastForwardingStatusCheck().run(
        _ctx(_facts(pairs=((None, "239.5.5.5"),), routes=routes)))
    assert findings[0].value == "1/1 S,G nefunguje"


def test_forwarding_missing_rate_is_skip_not_failure():
    """Radici rozhodnuti Tasku 1: chybejici rate (EVO bez statistik) je SKIP,
    ne selhani streamu - stream ma jinak vsechno v poradku."""
    routes = {f"{SG[0]},{SG[1]}": _route(pps=None)}
    findings = MulticastForwardingStatusCheck().run(_ctx(_facts(routes=routes)))
    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "1 S,G"
    rate_row = _by_label(findings, sg_label(*SG))["Forwarding-rate"]
    assert rate_row.outcome is Outcome.SKIP
    assert rate_row.value == RATE_UNAVAILABLE


# --- core_multicast_forwarding ----------------------------------------------

PREFIX = "10.11.11.1/32"
INET2 = ({"rib": "inet.2", "prefix": PREFIX, "route_type": "static",
          "next_hops": [{"to": "10.1.1.2", "interface": None, "qualified": False, "active": True}],
          "active": True},)


def _core_scope(static_routes=INET2):
    return _scope("lo0.0", "Core", "loopback", static_routes=static_routes)


def _core_facts(routes=None, via=("et-0/0/0.0",), inet2_present=True):
    facts = {"multicast_route": {"master": routes if routes is not None else {
        f"{SG[0]},{SG[1]}": _route(downstream=["et-0/0/8.11", "irb.2"])}}}
    facts["routes"] = {"inet.2": {PREFIX: {"next_hop": ["10.1.1.2"], "via": list(via),
                                           "active": True, "protocol": "static"}}} if inet2_present else {}
    return facts


def test_assign_sources_longest_prefix_wins_and_each_route_once():
    """Mutant kill (2026-09-03, overeno spustenim): sort podle prefixlen
    vzestupne misto sestupne."""
    table = {"10.11.11.1,232.1.1.1": {}, "10.11.12.1,232.1.1.2": {}, "192.0.2.1,232.9.9.9": {}}
    assigned = assign_sources(table, ["10.11.0.0/16", "10.11.11.0/24"])
    assert [k for k, _ in assigned["10.11.11.0/24"]] == ["10.11.11.1,232.1.1.1"]
    assert [k for k, _ in assigned["10.11.0.0/16"]] == ["10.11.12.1,232.1.1.2"]


def test_core_no_inet2_statics_is_silent():
    assert CoreMulticastForwardingCheck().run(_ctx(_core_facts(), scope=_core_scope(()))) == []


def test_core_pass_block_shape():
    findings = CoreMulticastForwardingCheck().run(_ctx(_core_facts(), scope=_core_scope()))
    assert findings[0].outcome is Outcome.OK
    assert findings[0].label == "Multicast forwarding status"
    assert findings[0].message == "1 inet.2 prefixu se streamem"
    assert findings[0].value == "1 inet.2 prefixu"
    assert findings[1].outcome is Outcome.OK
    assert findings[1].value == f"Existuje S,G pro {PREFIX}"
    rows = _by_label(findings, sg_label(*SG))
    assert [f.label for f in findings[2:]] == [
        "Upstream interface", "Downstream interfaces", "Forwarding rate packets", "Route uptime"]
    assert rows["Upstream interface"].outcome is Outcome.OK
    assert rows["Upstream interface"].value == "et-0/0/0.0"
    assert rows["Downstream interfaces"].value == "et-0/0/8.11, irb.2"
    assert rows["Forwarding rate packets"].value == "6 pps"


def test_core_no_stream_for_prefix_fails_without_filler_skips():
    """Prazdne SKIP radky (S,G / Forwarding rate / Upstream / Downstream)
    zmizely - zustava jen souhrn a per-prefix BROKEN radek."""
    findings = CoreMulticastForwardingCheck().run(_ctx(_core_facts(routes={}), scope=_core_scope()))
    assert not any(f.outcome is Outcome.SKIP and f.value == "" for f in findings)
    assert findings[0].label == "Multicast forwarding status"
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].message == "1 z 1 inet.2 prefixu bez streamu"
    assert findings[0].value == "1/1 bez streamu"
    assert findings[1].outcome is Outcome.BROKEN
    assert findings[1].value == f"Neexistuje S,G pro {PREFIX}"
    assert len(findings) == 2


def test_core_no_stream_has_no_filler_skip_rows_and_summary():
    """Jeden prefix se streamem, druhy bez nej - souhrn pocita oba, i kdyz
    jen jeden chybi."""
    prefix2 = "10.11.11.2/32"
    static_routes = INET2 + ({
        "rib": "inet.2", "prefix": prefix2, "route_type": "static",
        "next_hops": [{"to": "10.1.1.3", "interface": None, "qualified": False, "active": True}],
        "active": True,
    },)
    routes = {f"{SG[0]},{SG[1]}": _route(downstream=["et-0/0/8.11", "irb.2"])}
    findings = CoreMulticastForwardingCheck().run(
        _ctx(_core_facts(routes=routes), scope=_core_scope(static_routes=static_routes)))
    assert not any(f.outcome is Outcome.SKIP and f.value == "" for f in findings)
    assert findings[0].label == "Multicast forwarding status"
    assert findings[0].message == "1 z 2 inet.2 prefixu bez streamu"
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "1/2 bez streamu"


def test_core_all_streams_summary_ok():
    prefix2 = "10.11.11.2/32"
    static_routes = INET2 + ({
        "rib": "inet.2", "prefix": prefix2, "route_type": "static",
        "next_hops": [{"to": "10.1.1.3", "interface": None, "qualified": False, "active": True}],
        "active": True,
    },)
    routes = {
        f"{SG[0]},{SG[1]}": _route(downstream=["et-0/0/8.11", "irb.2"]),
        "10.11.11.2,232.1.1.1": _route(downstream=["et-0/0/8.11", "irb.2"]),
    }
    findings = CoreMulticastForwardingCheck().run(
        _ctx(_core_facts(routes=routes), scope=_core_scope(static_routes=static_routes)))
    assert findings[0].outcome is Outcome.OK
    assert findings[0].message == "2 inet.2 prefixu se streamem"
    assert findings[0].value == "2 inet.2 prefixu"


def test_core_upstream_must_be_one_of_via():
    """ECMP / qualified-next-hop: inet.2 routa ma vic via, upstream staci
    jeden z nich. Mutant kill (2026-09-03, overeno spustenim): 'upstream in
    vias' -> 'upstream.startswith((\"ge-\",\"xe-\",\"et-\",\"ae\"))'."""
    findings = CoreMulticastForwardingCheck().run(_ctx(
        _core_facts(via=("et-0/0/1.0", "et-0/0/0.0")), scope=_core_scope()))
    assert _by_label(findings, sg_label(*SG))["Upstream interface"].outcome is Outcome.OK
    wrong = CoreMulticastForwardingCheck().run(_ctx(_core_facts(via=("et-0/0/1.0",)), scope=_core_scope()))
    row = _by_label(wrong, sg_label(*SG))["Upstream interface"]
    assert row.outcome is Outcome.BROKEN and row.value == "et-0/0/0.0"


def test_core_inet2_route_missing_from_table_skips_upstream_row():
    findings = CoreMulticastForwardingCheck().run(_ctx(_core_facts(inet2_present=False), scope=_core_scope()))
    row = _by_label(findings, sg_label(*SG))["Upstream interface"]
    assert row.outcome is Outcome.SKIP and row.value == "routa neni v tabulce"


def test_core_no_downstream_fails():
    routes = {f"{SG[0]},{SG[1]}": _route(downstream=[])}
    findings = CoreMulticastForwardingCheck().run(_ctx(_core_facts(routes=routes), scope=_core_scope()))
    row = _by_label(findings, sg_label(*SG))["Downstream interfaces"]
    assert row.outcome is Outcome.BROKEN and row.value == "Zadne downstream interfacy"


def test_core_sg_set_compared_against_baseline():
    """Mutant: smazani vetve DEGRADED pri rozdilu mnozin polozi tento test."""
    baseline = _core_facts(routes={
        f"{SG[0]},{SG[1]}": _route(), "10.11.11.1,232.1.1.9": _route()})
    findings = CoreMulticastForwardingCheck().run(_ctx(
        _core_facts(), baseline=baseline, scope=_core_scope(), baseline_scope=_core_scope()))
    assert findings[1].outcome is Outcome.DEGRADED
    assert findings[1].baseline_value == "(10.11.11.1, 232.1.1.1), (10.11.11.1, 232.1.1.9)"
    same = CoreMulticastForwardingCheck().run(_ctx(
        _core_facts(), baseline=_core_facts(), scope=_core_scope(), baseline_scope=_core_scope()))
    assert same[1].outcome is Outcome.OK
    assert same[1].baseline_value == "(10.11.11.1, 232.1.1.1)"


def test_core_check_applies_only_to_loopback():
    check = CoreMulticastForwardingCheck()
    assert check.applies_to(_core_scope())
    assert not check.applies_to(_scope("et-0/0/0.0", "Core", "transit"))
    assert not check.applies_to(_scope())


def test_core_missing_rate_is_skip():
    """Radici rozhodnuti Tasku 1 prenesene do core checku: chybejici rate
    (EVO bez statistik) je SKIP, ne selhani - a souhrn zustava OK, protoze
    ho rozhoduje jen existence streamu a mnozina proti baseline."""
    routes = {f"{SG[0]},{SG[1]}": _route(pps=None)}
    findings = CoreMulticastForwardingCheck().run(_ctx(_core_facts(routes=routes), scope=_core_scope()))
    assert findings[0].outcome is Outcome.OK
    rate_row = _by_label(findings, sg_label(*SG))["Forwarding rate packets"]
    assert rate_row.outcome is Outcome.SKIP
    assert rate_row.value == RATE_UNAVAILABLE


# --- mvpn_cmulticast_status -------------------------------------------------

RI = "MULTICAST-STREAM-B-MUX1-RECEIVER"
MSG = ("10.12.12.1", "239.1.1.1")
TUNNEL = "RSVP-TE P2MP:150.0.0.13, 24209,150.0.0.13"


def _mvpn_scope(iface="irb.2"):
    return _scope(iface, "IPVPN", "mvpn-igmp", [RI])


def _entry(tunnel=TUNNEL, pe="150.0.0.13", source=f"{MSG[0]}/32", group=f"{MSG[1]}/32"):
    return {"source_prefix": source, "group_prefix": group, "provider_tunnel_id": tunnel, "sender_pe": pe}


def _mvpn_facts(entries=None, iface="irb.2", pairs=(MSG,), instance_present=True):
    facts = _igmp(iface, *pairs)
    facts["mvpn_instance"] = {RI: {"c_multicast": entries if entries is not None else [_entry()]}} if instance_present else {}
    return facts


def test_mvpn_pass_rows():
    findings = MvpnCmulticastStatusCheck().run(_ctx(_mvpn_facts(), scope=_mvpn_scope()))
    rows = _by_label(findings, sg_label(*MSG))
    assert [f.label for f in findings] == ["C-Multicast status", "Provider tunnel"]
    assert rows["C-Multicast status"].outcome is Outcome.OK
    assert rows["C-Multicast status"].value == "10.12.12.1/32:239.1.1.1/32"
    assert rows["Provider tunnel"].outcome is Outcome.OK
    assert rows["Provider tunnel"].value == TUNNEL


def test_mvpn_without_igmp_is_skip():
    findings = MvpnCmulticastStatusCheck().run(_ctx(_mvpn_facts(pairs=()), scope=_mvpn_scope()))
    assert [(f.outcome, f.label, f.value) for f in findings] == [
        (Outcome.SKIP, "C-Multicast status", NO_REPORT_SKIP)]


def test_mvpn_instance_missing_is_fail():
    findings = MvpnCmulticastStatusCheck().run(_ctx(_mvpn_facts(instance_present=False), scope=_mvpn_scope()))
    assert [(f.outcome, f.value) for f in findings] == [(Outcome.BROKEN, "instance neni v mvpn vypisu")]


def test_mvpn_missing_cmulticast_entry_is_fail():
    findings = MvpnCmulticastStatusCheck().run(_ctx(_mvpn_facts(entries=[]), scope=_mvpn_scope()))
    assert [(f.outcome, f.label, f.value, f.group) for f in findings] == [
        (Outcome.BROKEN, "C-Multicast status", "chybi c-multicast zaznam", sg_label(*MSG))]


def test_mvpn_invalid_tunnel_is_fail():
    findings = MvpnCmulticastStatusCheck().run(_ctx(
        _mvpn_facts(entries=[_entry(tunnel="I-P-tnl:invalid", pe=None)]), scope=_mvpn_scope()))
    row = _by_label(findings, sg_label(*MSG))["Provider tunnel"]
    assert row.outcome is Outcome.BROKEN
    assert row.value == "I-P-tnl:invalid"
    assert "bez provider tunelu" in row.message


def test_mvpn_sender_pe_change_is_warn_but_tunnel_id_change_is_not():
    """Mutant kill (2026-09-03, overeno spustenim): porovnani celeho retezce
    (was_tunnel != tunnel) misto sender_pe polozi druhou polovinu testu."""
    resignaled = "RSVP-TE P2MP:150.0.0.13, 99999,150.0.0.13"
    same_pe = MvpnCmulticastStatusCheck().run(_ctx(
        _mvpn_facts(), baseline=_mvpn_facts(entries=[_entry(tunnel=resignaled)]),
        scope=_mvpn_scope(), baseline_scope=_mvpn_scope()))
    assert _by_label(same_pe, sg_label(*MSG))["Provider tunnel"].outcome is Outcome.OK
    assert _by_label(same_pe, sg_label(*MSG))["Provider tunnel"].baseline_value == resignaled

    other_pe = "RSVP-TE P2MP:150.0.0.11, 24209,150.0.0.11"
    moved = MvpnCmulticastStatusCheck().run(_ctx(
        _mvpn_facts(), baseline=_mvpn_facts(entries=[_entry(tunnel=other_pe, pe="150.0.0.11")]),
        scope=_mvpn_scope(), baseline_scope=_mvpn_scope()))
    row = _by_label(moved, sg_label(*MSG))["Provider tunnel"]
    assert row.outcome is Outcome.DEGRADED


def test_mvpn_sender_pe_change_names_both():
    """Hlaska musi jmenovat obe PE - stare i nove - ne jen tu starou."""
    other_pe = "RSVP-TE P2MP:150.0.0.11, 24209,150.0.0.11"
    findings = MvpnCmulticastStatusCheck().run(_ctx(
        _mvpn_facts(), baseline=_mvpn_facts(entries=[_entry(tunnel=other_pe, pe="150.0.0.11")]),
        scope=_mvpn_scope(), baseline_scope=_mvpn_scope()))
    row = _by_label(findings, sg_label(*MSG))["Provider tunnel"]
    assert row.message == (
        f"{sg_label(*MSG)}: provider tunnel {TUNNEL} - "
        "sender PE se zmenil 150.0.0.11 -> 150.0.0.13"
    )


def test_mvpn_asm_report_matches_by_group_only():
    findings = MvpnCmulticastStatusCheck().run(_ctx(
        _mvpn_facts(pairs=((None, MSG[1]),)), scope=_mvpn_scope()))
    assert _by_label(findings, sg_label(None, MSG[1]))["C-Multicast status"].outcome is Outcome.OK


def test_mvpn_check_applies_only_to_mvpn_igmp():
    check = MvpnCmulticastStatusCheck()
    assert check.applies_to(_mvpn_scope())
    assert not check.applies_to(_scope())
    assert not check.applies_to(_scope("irb.3", "IPVPN", None, [RI]))
