"""Testy checku isis_adjacency_state (spec 2026-08-26).

Idiom podle tests/checks/test_bfd.py: realny Scope se selektorem
interfaces, CheckContext, primy run() checku.
"""

from __future__ import annotations

from migration_validator.checks.base import CheckContext
from migration_validator.checks.core_protocols import (
    MISSING,
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


def test_baseline_state_down_before_up_now_is_warn():
    """Zlepseni je porad zmena - hlasi se jako DEGRADED, ne jako tiche OK."""
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
    assert state_row.outcome is Outcome.DEGRADED
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
    assert level1_row.value == "nakonfigurován"


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
    assert level1_row.value == "nakonfigurován"


def test_transit_non_passive_level2_is_pass():
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
    """Pasivni tranzit nesestavi adjacency, kterou meri isis_adjacency_state."""
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


def test_ldp_neighbor_address_none_is_missing_not_literal_none():
    findings = LdpNeighborStateCheck().run(
        _ctx_area(
            "ldp_neighbor",
            {IFACE: {"neighbor_address": None, "uptime_seconds": 60}},
        )
    )

    address_row = findings[1]
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
