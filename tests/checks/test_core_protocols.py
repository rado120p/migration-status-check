"""Testy checku isis_adjacency_state (spec 2026-08-26).

Idiom podle tests/checks/test_bfd.py: realny Scope se selektorem
interfaces, CheckContext, primy run() checku.
"""

from __future__ import annotations

from migration_validator.checks.base import CheckContext
from migration_validator.checks.bfd import BfdSessionStateCheck
from migration_validator.checks.core_protocols import (
    MISSING,
    BfdTransitStateCheck,
    IsisAdjacencyStateCheck,
    IsisInterfaceInfoCheck,
    IsisOverviewCheck,
    LdpNeighborStateCheck,
    MplsInterfaceStateCheck,
    PimNeighborStateCheck,
)
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors

IFACE = "ge-0/0/1.0"
LOOPBACK = "lo0.0"


def _scope(interfaces=(IFACE,), service_subtype="transit", protocols=()) -> Scope:
    return Scope(
        id="svc:Core:transit",
        kind="service",
        key=ScopeKey(None, "Core", service_subtype),
        selectors=Selectors(interfaces=list(interfaces), protocols=list(protocols)),
    )


def _ctx(subject_adj, baseline_adj=None, scope=None):
    return CheckContext(
        scope=scope or _scope(),
        subject={"isis_adjacency": subject_adj},
        baseline=({"isis_adjacency": baseline_adj} if baseline_adj is not None else None),
        config=__import__(
            "migration_validator.config", fromlist=["default_config"]
        ).default_config(),
    )


def _ctx_for(key: str, subject_value, scope=None):
    """CheckContext s obecnou fact oblasti - pro isis_interface/isis_overview."""
    return CheckContext(
        scope=scope or _scope(),
        subject={key: subject_value},
        baseline=None,
        config=__import__(
            "migration_validator.config", fromlist=["default_config"]
        ).default_config(),
    )


def test_no_baseline_adjacency_up_full_fields():
    findings = IsisAdjacencyStateCheck().run(
        _ctx(
            {
                IFACE: {
                    "system_name": "P1",
                    "state": "Up",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            }
        )
    )

    assert len(findings) == 4
    name_row, state_row, v4_row, v6_row = findings
    assert name_row.outcome is Outcome.INFO
    assert name_row.label == f"IS-IS neighbor name ({IFACE})"
    assert name_row.value == "P1"

    assert state_row.outcome is Outcome.OK
    assert state_row.label == f"IS-IS adjacency state ({IFACE})"
    assert state_row.value == "Up"

    assert v4_row.outcome is Outcome.OK
    assert v4_row.label == f"IS-IS neighbor IPv4 address ({IFACE})"
    assert v4_row.value == "10.0.0.1"

    assert v6_row.outcome is Outcome.OK
    assert v6_row.label == f"IS-IS neighbor IPv6 address ({IFACE})"
    assert v6_row.value == "2001:db8::1"


def test_no_baseline_state_down_is_broken():
    findings = IsisAdjacencyStateCheck().run(
        _ctx(
            {
                IFACE: {
                    "system_name": "P1",
                    "state": "Down",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            }
        )
    )

    state_row = findings[1]
    assert state_row.outcome is Outcome.BROKEN
    assert state_row.value == "Down"


def test_no_baseline_missing_ipv6_is_broken_on_ipv6_row():
    findings = IsisAdjacencyStateCheck().run(
        _ctx(
            {
                IFACE: {
                    "system_name": "P1",
                    "state": "Up",
                    "ip_address": "10.0.0.1",
                }
            }
        )
    )

    v6_row = findings[3]
    assert v6_row.outcome is Outcome.BROKEN
    assert v6_row.value == MISSING


def test_interface_missing_from_output_is_single_fail_row():
    findings = IsisAdjacencyStateCheck().run(_ctx({}))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == MISSING
    assert findings[0].label == f"IS-IS adjacency state ({IFACE})"


def test_interface_missing_from_output_carries_baseline_state():
    findings = IsisAdjacencyStateCheck().run(
        _ctx({}, baseline_adj={IFACE: {"state": "Up"}})
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].baseline_value == "Up"


def test_baseline_different_system_name_is_warn():
    findings = IsisAdjacencyStateCheck().run(
        _ctx(
            {
                IFACE: {
                    "system_name": "P1",
                    "state": "Up",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            },
            baseline_adj={
                IFACE: {
                    "system_name": "P2",
                    "state": "Up",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            },
        )
    )

    name_row = findings[0]
    assert name_row.outcome is Outcome.DEGRADED
    assert name_row.value == "P1"
    assert name_row.baseline_value == "P2"


def test_baseline_state_up_now_and_up_before_is_pass():
    findings = IsisAdjacencyStateCheck().run(
        _ctx(
            {
                IFACE: {
                    "system_name": "P1",
                    "state": "Up",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            },
            baseline_adj={
                IFACE: {
                    "system_name": "P1",
                    "state": "Up",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            },
        )
    )

    state_row = findings[1]
    assert state_row.outcome is Outcome.OK
    assert state_row.value == "Up"
    assert state_row.baseline_value == "Up"


def test_baseline_state_down_before_up_now_is_recovered():
    """Zlepseni proti baseline je RECOVERED, ne tiche OK ani DEGRADED.

    Mutant kill (2026-09-03, overeno spustenim): flip RECOVERED->OK (nebo
    ->DEGRADED) ve vetvi "Up ted / ne-Up v baseline" v
    IsisAdjacencyStateCheck._rows -> tenhle test padne."""
    findings = IsisAdjacencyStateCheck().run(
        _ctx(
            {
                IFACE: {
                    "system_name": "P1",
                    "state": "Up",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            },
            baseline_adj={
                IFACE: {
                    "system_name": "P1",
                    "state": "Down",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            },
        )
    )

    state_row = findings[1]
    assert state_row.outcome is Outcome.RECOVERED
    assert state_row.message == f"{IFACE}: adjacency Up (v baseline Down)"
    assert state_row.value == "Up"
    assert state_row.baseline_value == "Down"


def test_baseline_different_ip_address_is_warn():
    findings = IsisAdjacencyStateCheck().run(
        _ctx(
            {
                IFACE: {
                    "system_name": "P1",
                    "state": "Up",
                    "ip_address": "10.0.0.2",
                    "ipv6_address": "2001:db8::1",
                }
            },
            baseline_adj={
                IFACE: {
                    "system_name": "P1",
                    "state": "Up",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            },
        )
    )

    v4_row = findings[2]
    assert v4_row.outcome is Outcome.DEGRADED
    assert v4_row.value == "10.0.0.2"
    assert v4_row.baseline_value == "10.0.0.1"


def test_missing_system_name_does_not_leak_literal_none_into_value():
    """Pritomny adjacency zaznam bez klice system_name nesmi vyrobit radek
    s hodnotou "None" - str(None) by to jinak udelal potichu."""
    findings = IsisAdjacencyStateCheck().run(
        _ctx(
            {
                IFACE: {
                    "state": "Up",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            }
        )
    )

    name_row = findings[0]
    assert name_row.value != "None"
    assert name_row.value == MISSING


def test_baseline_present_without_state_key_does_not_leak_literal_none():
    """Baseline zaznam existuje, ale nenese klic 'state' - baseline_value
    ma zustat None, ne se stringifikovat na doslovny text "None"."""
    findings = IsisAdjacencyStateCheck().run(
        _ctx(
            {
                IFACE: {
                    "system_name": "P1",
                    "state": "Up",
                    "ip_address": "10.0.0.1",
                    "ipv6_address": "2001:db8::1",
                }
            },
            baseline_adj={IFACE: {"system_name": "P1"}},
        )
    )

    state_row = findings[1]
    assert state_row.baseline_value != "None"
    assert state_row.baseline_value is None


def test_interface_missing_baseline_without_state_key_does_not_leak_none():
    findings = IsisAdjacencyStateCheck().run(
        _ctx({}, baseline_adj={IFACE: {"system_name": "P1"}})
    )

    assert findings[0].baseline_value != "None"
    assert findings[0].baseline_value is None


def test_missing_marker_has_no_diacritics():
    assert MISSING == "chybi v outputu"


def test_name_row_match_carries_baseline_value():
    """Soused se shoduje s baselinem - name_row nese baseline_value, ne jen
    value (brief 2026-09-03, bod 26-ISIS)."""
    findings = IsisAdjacencyStateCheck().run(
        _ctx(
            {IFACE: {"system_name": "P1", "state": "Up"}},
            baseline_adj={IFACE: {"system_name": "P1", "state": "Up"}},
        )
    )

    name_row = findings[0]
    assert name_row.baseline_value == "P1"


def test_name_row_no_baseline_does_not_leak_literal_none():
    findings = IsisAdjacencyStateCheck().run(
        _ctx({IFACE: {"system_name": "P1", "state": "Up"}})
    )

    name_row = findings[0]
    assert name_row.baseline_value is None


def test_loopback_scope_does_not_apply():
    check = IsisAdjacencyStateCheck()
    scope = _scope(service_subtype="loopback")

    assert check.applies_to(scope) is False


# --- IsisInterfaceInfoCheck -------------------------------------------------


def _loopback_scope(interfaces=(LOOPBACK,)) -> Scope:
    return _scope(interfaces=interfaces, service_subtype="loopback")


def test_loopback_passive_level2_is_pass():
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for(
            "isis_interface",
            {LOOPBACK: {"levels": {"2": {"passive": True}}}},
            scope=_loopback_scope(),
        )
    )

    level2_row = next(f for f in findings if f.label == f"IS-IS level 2 ({LOOPBACK})")
    assert level2_row.outcome is Outcome.OK

    passive_row = next(
        f for f in findings if f.label == f"IS-IS level 2 passive ({LOOPBACK})"
    )
    assert passive_row.outcome is Outcome.OK
    assert passive_row.value == "Passive"


def test_loopback_non_passive_level2_is_fail_passive_row():
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for(
            "isis_interface",
            {LOOPBACK: {"levels": {"2": {"passive": False}}}},
            scope=_loopback_scope(),
        )
    )

    passive_row = next(
        f for f in findings if f.label == f"IS-IS level 2 passive ({LOOPBACK})"
    )
    assert passive_row.outcome is Outcome.BROKEN
    assert passive_row.value == "bez Passive"


def test_level1_present_is_fail_row_on_loopback():
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for(
            "isis_interface",
            {
                LOOPBACK: {
                    "levels": {"1": {"passive": True}, "2": {"passive": True}}
                }
            },
            scope=_loopback_scope(),
        )
    )

    level1_row = next(f for f in findings if f.label == f"IS-IS level 1 ({LOOPBACK})")
    assert level1_row.outcome is Outcome.BROKEN
    assert level1_row.value == "nakonfigurovan"


def test_level1_present_is_fail_row_on_transit():
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for(
            "isis_interface",
            {IFACE: {"levels": {"1": {"passive": False}, "2": {"passive": False}}}},
            scope=_scope(),
        )
    )

    level1_row = next(f for f in findings if f.label == f"IS-IS level 1 ({IFACE})")
    assert level1_row.outcome is Outcome.BROKEN
    assert level1_row.value == "nakonfigurovan"


def test_transit_non_passive_level2_is_pass():
    """Mutant kill (2026-08-26, overeno spustenim), spolu s
    test_transit_passive_level2_is_fail: `ok = passive if loopback else not
    passive` -> `ok = passive` v IsisInterfaceInfoCheck.run necha tenhle test
    padnout."""
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for(
            "isis_interface",
            {IFACE: {"levels": {"2": {"passive": False}}}},
            scope=_scope(),
        )
    )

    passive_row = next(
        f for f in findings if f.label == f"IS-IS level 2 passive ({IFACE})"
    )
    assert passive_row.outcome is Outcome.OK
    assert passive_row.value == "bez Passive"


def test_transit_passive_level2_is_fail():
    """Pasivni tranzit nesestavi adjacency, kterou meri isis_adjacency_state.

    Mutant kill (2026-08-26, overeno spustenim): `ok = passive if loopback
    else not passive` -> `ok = passive` v IsisInterfaceInfoCheck.run necha
    tenhle test padnout."""
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for(
            "isis_interface",
            {IFACE: {"levels": {"2": {"passive": True}}}},
            scope=_scope(),
        )
    )

    passive_row = next(
        f for f in findings if f.label == f"IS-IS level 2 passive ({IFACE})"
    )
    assert passive_row.outcome is Outcome.BROKEN
    assert passive_row.value == "Passive"


def test_level2_missing_emits_single_broken_row_no_passive_row():
    """Chybejici level 2 znamena, ze IS-IS na rozhrani nesestavi adjacency -
    passive radek uz nema smysl merit a nesmi se vypsat (brief 2026-09-03)."""
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for(
            "isis_interface",
            {LOOPBACK: {"levels": {}}},
            scope=_loopback_scope(),
        )
    )

    labels = [f.label for f in findings if f.outcome is Outcome.BROKEN]
    assert labels == [f"IS-IS level 2 ({LOOPBACK})"]
    assert not any(
        f.label.startswith("IS-IS level 2 passive") for f in findings
    )
    assert findings[0].value == MISSING


def test_level2_present_still_emits_passive_row():
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for(
            "isis_interface",
            {LOOPBACK: {"levels": {"2": {"passive": True}}}},
            scope=_loopback_scope(),
        )
    )

    assert any(
        f.label == f"IS-IS level 2 passive ({LOOPBACK})" for f in findings
    )


def test_level1_and_missing_level2_still_emits_level1_row():
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for(
            "isis_interface",
            {LOOPBACK: {"levels": {"1": {"passive": True}}}},
            scope=_loopback_scope(),
        )
    )

    labels = [f.label for f in findings]
    assert f"IS-IS level 2 ({LOOPBACK})" in labels
    assert f"IS-IS level 1 ({LOOPBACK})" in labels
    assert not any(label.startswith("IS-IS level 2 passive") for label in labels)


def test_interface_missing_from_isis_interface_output_is_fail():
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for("isis_interface", {}, scope=_scope())
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == MISSING
    assert findings[0].label == f"IS-IS interface ({IFACE})"


def test_loopback_measures_selector_interfaces_not_transit_filter():
    """Loopback scope selectors.interfaces = ["lo0.0"] - lo0 neni transit,
    ale na loopback scopu se meri presto (ne pres _scope_transit_interfaces)."""
    findings = IsisInterfaceInfoCheck().run(
        _ctx_for(
            "isis_interface",
            {LOOPBACK: {"levels": {"2": {"passive": True}}}},
            scope=_loopback_scope(),
        )
    )

    assert any(f.label == f"IS-IS level 2 ({LOOPBACK})" for f in findings)


# --- IsisOverviewCheck -------------------------------------------------


def test_overload_enabled_is_warn():
    findings = IsisOverviewCheck().run(
        _ctx_for(
            "isis_overview", {"overload_enabled": True}, scope=_loopback_scope()
        )
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "nastaven"


def test_overload_disabled_is_pass():
    findings = IsisOverviewCheck().run(
        _ctx_for(
            "isis_overview", {"overload_enabled": False}, scope=_loopback_scope()
        )
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "nenastaven"


def test_isis_overview_empty_is_degraded_bez_dat():
    findings = IsisOverviewCheck().run(
        _ctx_for("isis_overview", {}, scope=_loopback_scope())
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].message == "chybi data z collectoru isis_overview"
    assert findings[0].value == "bez dat"


def test_isis_overview_disabled_flag_is_not_treated_as_empty():
    """Dict s overload_enabled False zustava OK 'nenastaven', ne DEGRADED
    'bez dat' - prazdny je jen chybejici/{} subject."""
    findings = IsisOverviewCheck().run(
        _ctx_for(
            "isis_overview", {"overload_enabled": False}, scope=_loopback_scope()
        )
    )

    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "nenastaven"


def test_isis_overview_does_not_apply_to_transit_scope():
    check = IsisOverviewCheck()
    scope = _scope(service_subtype="transit")

    assert check.applies_to(scope) is False


# --- LdpNeighborStateCheck / PimNeighborStateCheck / MplsInterfaceStateCheck --


def _ctx_area(area, subject_value, baseline_value=None, scope=None):
    """CheckContext s obecnou fact oblasti a volitelnym baselinem."""
    return CheckContext(
        scope=scope or _scope(),
        subject={area: subject_value},
        baseline=({area: baseline_value} if baseline_value is not None else None),
        config=__import__(
            "migration_validator.config", fromlist=["default_config"]
        ).default_config(),
    )


def test_ldp_neighbor_up_is_pass_with_address_info():
    findings = LdpNeighborStateCheck().run(
        _ctx_area(
            "ldp_neighbor",
            {IFACE: {"neighbor_address": "10.0.0.2", "uptime_seconds": 25210}},
        )
    )

    assert len(findings) == 2
    status_row, address_row = findings
    assert status_row.outcome is Outcome.OK
    assert status_row.label == f"LDP neighbor status ({IFACE})"
    assert status_row.value == "Up for 7h 0m"

    assert address_row.outcome is Outcome.INFO
    assert address_row.label == f"LDP neighbor address ({IFACE})"
    assert address_row.value == "10.0.0.2"


def test_ldp_neighbor_zero_uptime_is_fail():
    findings = LdpNeighborStateCheck().run(
        _ctx_area(
            "ldp_neighbor",
            {IFACE: {"neighbor_address": "10.0.0.2", "uptime_seconds": 0}},
        )
    )

    status_row = findings[0]
    assert status_row.outcome is Outcome.BROKEN
    assert status_row.value == "Down"


def test_ldp_neighbor_missing_row_is_fail_down():
    findings = LdpNeighborStateCheck().run(_ctx_area("ldp_neighbor", {}))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "Down"
    assert findings[0].label == f"LDP neighbor status ({IFACE})"


def test_ldp_neighbor_baseline_address_changed_is_warn():
    findings = LdpNeighborStateCheck().run(
        _ctx_area(
            "ldp_neighbor",
            {IFACE: {"neighbor_address": "10.0.0.2", "uptime_seconds": 25210}},
            baseline_value={IFACE: {"neighbor_address": "10.0.0.9", "uptime_seconds": 100}},
        )
    )

    address_row = findings[1]
    assert address_row.outcome is Outcome.DEGRADED
    assert address_row.value == "10.0.0.2"
    assert address_row.baseline_value == "10.0.0.9"


def test_pim_neighbor_with_intent_up_is_pass():
    scope = _scope(protocols=("pim",))
    findings = PimNeighborStateCheck().run(
        _ctx_area(
            "pim_neighbor",
            {IFACE: {"neighbor_address": "10.0.0.3", "uptime_seconds": 60}},
            scope=scope,
        )
    )

    assert len(findings) == 2
    status_row, address_row = findings
    assert status_row.outcome is Outcome.OK
    assert address_row.outcome is Outcome.INFO
    assert address_row.value == "10.0.0.3"


def test_pim_neighbor_with_intent_missing_is_fail():
    scope = _scope(protocols=("pim",))
    findings = PimNeighborStateCheck().run(_ctx_area("pim_neighbor", {}, scope=scope))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "Down"


def test_pim_neighbor_without_intent_is_silent_not_skip():
    """Mutant kill (2026-08-26, overeno spustenim): smazani gate `if "pim"
    not in ctx.scope.selectors.protocols` v PimNeighborStateCheck.run necha
    tenhle test padnout (findings uz neni prazdny seznam)."""
    scope = _scope(protocols=())
    findings = PimNeighborStateCheck().run(_ctx_area("pim_neighbor", {}, scope=scope))

    assert findings == []


def test_pim_neighbor_baseline_address_changed_is_warn():
    scope = _scope(protocols=("pim",))
    findings = PimNeighborStateCheck().run(
        _ctx_area(
            "pim_neighbor",
            {IFACE: {"neighbor_address": "10.0.0.3", "uptime_seconds": 60}},
            baseline_value={IFACE: {"neighbor_address": "10.0.0.4", "uptime_seconds": 60}},
            scope=scope,
        )
    )

    address_row = findings[1]
    assert address_row.outcome is Outcome.DEGRADED
    assert address_row.value == "10.0.0.3"
    assert address_row.baseline_value == "10.0.0.4"


def test_mpls_interface_up_is_pass():
    findings = MplsInterfaceStateCheck().run(
        _ctx_area("mpls_interface", {IFACE: {"state": "Up"}})
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "Up"
    assert findings[0].label == f"MPLS interface status ({IFACE})"


def test_mpls_interface_down_is_fail():
    findings = MplsInterfaceStateCheck().run(
        _ctx_area("mpls_interface", {IFACE: {"state": "Dn"}})
    )

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "Down"


def test_mpls_interface_missing_is_fail_chybi_v_outputu():
    findings = MplsInterfaceStateCheck().run(_ctx_area("mpls_interface", {}))

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == MISSING


def test_mpls_interface_baseline_state_present_but_none_is_not_literal_none():
    """was.get('state') muze byt pritomny, ale None - baseline_value nesmi
    byt doslovny retezec 'None'."""
    findings = MplsInterfaceStateCheck().run(
        _ctx_area(
            "mpls_interface",
            {IFACE: {"state": "Up"}},
            baseline_value={IFACE: {"state": None}},
        )
    )

    assert findings[0].baseline_value is None


def test_mpls_up_after_down_baseline_is_recovered():
    findings = MplsInterfaceStateCheck().run(
        _ctx_area(
            "mpls_interface",
            {IFACE: {"state": "Up"}},
            baseline_value={IFACE: {"state": "Down"}},
        )
    )

    assert findings[0].outcome is Outcome.RECOVERED
    assert findings[0].message == f"{IFACE}: MPLS Up (v baseline Down)"
    assert findings[0].value == "Up"
    assert findings[0].baseline_value == "Down"


def test_mpls_up_after_up_baseline_stays_ok():
    findings = MplsInterfaceStateCheck().run(
        _ctx_area(
            "mpls_interface",
            {IFACE: {"state": "Up"}},
            baseline_value={IFACE: {"state": "Up"}},
        )
    )

    assert findings[0].outcome is Outcome.OK


def test_ldp_neighbor_address_none_is_missing_not_literal_none():
    findings = LdpNeighborStateCheck().run(
        _ctx_area(
            "ldp_neighbor",
            {IFACE: {"neighbor_address": None, "uptime_seconds": 60}},
        )
    )

    address_row = findings[1]
    assert address_row.value == MISSING


def test_ldp_neighbor_address_missing_no_baseline_change_is_broken():
    findings = LdpNeighborStateCheck().run(
        _ctx_area(
            "ldp_neighbor",
            {IFACE: {"neighbor_address": None, "uptime_seconds": 60}},
        )
    )

    address_row = next(
        f for f in findings if f.label == f"LDP neighbor address ({IFACE})"
    )
    assert address_row.outcome is Outcome.BROKEN
    assert address_row.message == f"{IFACE}: adresa souseda chybi"
    assert address_row.value == MISSING


def test_ldp_neighbor_address_missing_with_baseline_change_stays_degraded():
    findings = LdpNeighborStateCheck().run(
        _ctx_area(
            "ldp_neighbor",
            {IFACE: {"neighbor_address": None, "uptime_seconds": 60}},
            baseline_value={IFACE: {"neighbor_address": "10.0.0.9", "uptime_seconds": 60}},
        )
    )

    address_row = next(
        f for f in findings if f.label == f"LDP neighbor address ({IFACE})"
    )
    assert address_row.outcome is Outcome.DEGRADED
    assert address_row.value == MISSING
    assert address_row.baseline_value == "10.0.0.9"


def test_pim_neighbor_address_missing_no_baseline_change_is_broken():
    scope = _scope(protocols=("pim",))
    findings = PimNeighborStateCheck().run(
        _ctx_area(
            "pim_neighbor",
            {IFACE: {"neighbor_address": None, "uptime_seconds": 60}},
            scope=scope,
        )
    )

    address_row = next(
        f for f in findings if f.label == f"PIM neighbor address ({IFACE})"
    )
    assert address_row.outcome is Outcome.BROKEN
    assert address_row.message == f"{IFACE}: adresa souseda chybi"
    assert address_row.value == MISSING


def test_ldp_neighbor_missing_row_baseline_derived_from_uptime_not_presence():
    """Baseline zaznam s uptime_seconds=0 znamenal Down, ne Up - baseline_value
    se nesmi odvozovat jen z pritomnosti zaznamu (stav se nikdy nefabuluje)."""
    findings = LdpNeighborStateCheck().run(
        _ctx_area(
            "ldp_neighbor",
            {},
            baseline_value={IFACE: {"neighbor_address": "10.0.0.2", "uptime_seconds": 0}},
        )
    )

    assert findings[0].baseline_value == "Down"


# --- BfdTransitStateCheck + gate na stary BfdSessionStateCheck ---


def test_bfd_transit_up_is_pass_with_peer_in_message():
    findings = BfdTransitStateCheck().run(
        _ctx_area("bfd", {"10.0.0.9": {"state": "Up", "interface": IFACE}})
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "Up"
    assert findings[0].label == f"BFD ({IFACE})"
    assert "10.0.0.9" in findings[0].message


def test_bfd_transit_down_is_fail():
    findings = BfdTransitStateCheck().run(
        _ctx_area("bfd", {"10.0.0.9": {"state": "Down", "interface": IFACE}})
    )

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "Down"


def test_bfd_transit_admindown_state_is_rendered_raw_not_capitalized():
    """AdminDown je platny stav BFD (tests/fixtures/rpc/junos/bfd.xml). `.capitalize()`
    by z nej udelal "Admindown" - sesterky check v bfd.py:113 vypisuje stav syrovy,
    takze `bfd_transit_state` ma delat totez (nalez finalniho review)."""
    findings = BfdTransitStateCheck().run(
        _ctx_area("bfd", {"10.0.0.9": {"state": "AdminDown", "interface": IFACE}})
    )

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "AdminDown"


def test_bfd_transit_missing_session_is_fail_down():
    """Mutant kill (2026-08-26, overeno spustenim) v BfdTransitStateCheck.run:
    spustena varianta `entries = by_interface.get(name) or []` (literalni
    smazani vetve `if not entries` by spadlo na TypeError v `sorted(None)`,
    viz roadmap) necha tenhle test padnout (0 findings misto 1x BROKEN)."""
    findings = BfdTransitStateCheck().run(_ctx_area("bfd", {}))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "Down"
    assert findings[0].label == f"BFD ({IFACE})"


def test_bfd_transit_two_sessions_on_one_interface_two_rows_no_phantom_fail():
    findings = BfdTransitStateCheck().run(
        _ctx_area(
            "bfd",
            {
                "10.0.0.9": {"state": "Up", "interface": IFACE},
                "fe80::9": {"state": "Up", "interface": IFACE},
            },
        )
    )

    assert len(findings) == 2
    assert all(f.outcome is Outcome.OK for f in findings)
    assert all(f.value == "Up" for f in findings)


def test_bfd_transit_up_after_down_baseline_is_recovered():
    findings = BfdTransitStateCheck().run(
        _ctx_area(
            "bfd",
            {"10.0.0.9": {"state": "Up", "interface": IFACE}},
            baseline_value={"10.0.0.9": {"state": "Down", "interface": IFACE}},
        )
    )

    assert findings[0].outcome is Outcome.RECOVERED
    assert findings[0].message == f"{IFACE}: BFD session s 10.0.0.9 Up (v baseline Down)"
    assert findings[0].value == "Up"
    assert findings[0].baseline_value == "Down"


def test_bfd_transit_up_after_up_baseline_stays_ok():
    findings = BfdTransitStateCheck().run(
        _ctx_area(
            "bfd",
            {"10.0.0.9": {"state": "Up", "interface": IFACE}},
            baseline_value={"10.0.0.9": {"state": "Up", "interface": IFACE}},
        )
    )

    assert findings[0].outcome is Outcome.OK
    assert findings[0].baseline_value == "Up"


def test_bfd_transit_up_after_down_baseline_on_renamed_interface_is_recovered():
    """Tranzitni port se pri migraci bezne prejmenuje (ge-0/0/1.0 ->
    et-0/0/1.0) - baseline session se ma dohledat podle peera, ne podle
    rozhrani, jinak RECOVERED nikdy nenaskoci (nalez finalniho review)."""
    findings = BfdTransitStateCheck().run(
        _ctx_area(
            "bfd",
            {"10.0.0.9": {"state": "Up", "interface": "et-0/0/1.0"}},
            baseline_value={"10.0.0.9": {"state": "Down", "interface": "ge-0/0/1.0"}},
            scope=_scope(interfaces=("et-0/0/1.0",)),
        )
    )

    assert findings[0].outcome is Outcome.RECOVERED
    assert findings[0].baseline_value == "Down"


def test_bfd_transit_missing_session_baseline_value_from_first_sorted_peer():
    """Zadna session ted, ale v baseline byla vic nez jedna - baseline_value
    se odvozuje ze stavu prvni (podle peer serazene), ne z pevneho 'Up'."""
    findings = BfdTransitStateCheck().run(
        _ctx_area(
            "bfd",
            {},
            baseline_value={
                "10.0.0.9": {"state": "Down", "interface": IFACE},
                "10.0.0.1": {"state": "AdminDown", "interface": IFACE},
            },
        )
    )

    assert len(findings) == 1
    assert findings[0].baseline_value == "AdminDown"


def test_bfd_transit_missing_session_no_baseline_session_is_none():
    findings = BfdTransitStateCheck().run(_ctx_area("bfd", {}))

    assert findings[0].baseline_value is None


def test_bfd_transit_does_not_apply_to_customer_scope():
    check = BfdTransitStateCheck()
    scope = Scope(
        id="svc:CPE13:IPVPN",
        kind="service",
        key=ScopeKey(description="CPE13", service_type="IPVPN"),
        selectors=Selectors(),
    )

    assert check.applies_to(scope) is False


def test_old_bfd_check_does_not_apply_to_core_transit_scope():
    old_check = BfdSessionStateCheck()
    transit_scope = _scope(service_subtype="transit")

    assert old_check.applies_to(transit_scope) is False


def test_old_bfd_check_does_not_apply_to_core_loopback_scope():
    """iBGP BFD na loopbacku je vedome odlozene rozhodnuti (2026-08-26,
    nalez finalniho review) - Scope.select ted tahne interni peery i do
    lo0.0 scope, ale zadny check jejich session nemeri. Kdyby tu
    `bfd_session_state` bezel dal, kazda takova session by dostala
    nepravdive WARN "bez konfigurace"."""
    old_check = BfdSessionStateCheck()
    loopback_scope = _scope(service_subtype="loopback")

    assert old_check.applies_to(loopback_scope) is False


def test_old_bfd_check_still_applies_to_internet_and_device_scope():
    from migration_validator.models.scope import device_scope

    old_check = BfdSessionStateCheck()
    internet_scope = Scope(
        id="svc:internet",
        kind="service",
        key=ScopeKey(description=None, service_type="Internet"),
        selectors=Selectors(),
    )

    assert old_check.applies_to(internet_scope) is True
    assert old_check.applies_to(device_scope()) is True


def test_ldp_neighbor_present_without_uptime_is_not_down():
    """Soused ve vypisu je, jen uptime se nepodarilo precist - 'Down' by byl
    vymysleny stav (stav se nikdy nefabuluje)."""
    findings = LdpNeighborStateCheck().run(
        _ctx_area(
            "ldp_neighbor",
            {IFACE: {"neighbor_address": "10.0.0.2", "uptime_seconds": None}},
        )
    )
    status_row = findings[0]
    assert status_row.outcome is Outcome.DEGRADED
    assert status_row.value == "uptime nezmereno"
    assert "nebezi" not in status_row.message
    assert "Down" not in status_row.message
