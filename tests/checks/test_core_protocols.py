"""Testy checku isis_adjacency_state (spec 2026-08-26).

Idiom podle tests/checks/test_bfd.py: realny Scope se selektorem
interfaces, CheckContext, primy run() checku.
"""

from __future__ import annotations

from migration_validator.checks.base import CheckContext
from migration_validator.checks.core_protocols import (
    MISSING,
    IsisAdjacencyStateCheck,
)
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors

IFACE = "ge-0/0/1.0"


def _scope(interfaces=(IFACE,), service_subtype="transit") -> Scope:
    return Scope(
        id="svc:Core:transit",
        kind="service",
        key=ScopeKey(None, "Core", service_subtype),
        selectors=Selectors(interfaces=list(interfaces)),
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


def test_loopback_scope_does_not_apply():
    check = IsisAdjacencyStateCheck()
    scope = _scope(service_subtype="loopback")

    assert check.applies_to(scope) is False
