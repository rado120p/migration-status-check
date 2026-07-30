"""Testy checku deaktivace.

Matice je cela pointa: sluzba deaktivovana na obou stranach je PASS (stav se
nezmenil), ne SKIP. Sluzba, ktera na starem zarizeni bezela a na novem je
deaktivovana, je FAIL - migrace nedokoncena.
"""

from __future__ import annotations

import pytest

from migration_validator.checks.base import CheckContext
from migration_validator.checks.deactivation import DeactivationStateCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _scope(deactivated: bool) -> Scope:
    return Scope(
        id="svc:CPE14:IPVPN",
        kind="service",
        key=ScopeKey(description="CPE14", service_type="IPVPN"),
        selectors=Selectors(interfaces=["ge-0/0/4.0"]),
        interface_active=not deactivated,
    )


def _ctx(subject_off: bool, baseline_off: bool | None) -> CheckContext:
    return CheckContext(
        scope=_scope(subject_off),
        subject={},
        baseline={} if baseline_off is not None else None,
        baseline_scope=_scope(baseline_off) if baseline_off is not None else None,
        config=default_config(),
    )


@pytest.mark.parametrize(
    "subject_off,baseline_off,expected",
    [
        (True, True, Outcome.OK),
        (True, False, Outcome.BROKEN),
        (False, True, Outcome.DEGRADED),
        (True, None, Outcome.SKIP),
    ],
)
def test_matrix(subject_off, baseline_off, expected):
    """Kazda bunka matice zvlast - aby selhani ukazalo, ktera se rozpojila.

    Zabiji mutanta: kterakoli zamena dvojice vetvi. Radek (True, False) je
    ten, ktery odlisuje "deaktivovano i drive" od "deaktivovano az ted" -
    bez nej by slo obe hlasit jako PASS.
    """
    findings = DeactivationStateCheck().run(_ctx(subject_off, baseline_off))

    assert len(findings) == 1
    assert findings[0].outcome is expected


@pytest.mark.parametrize("baseline_off", [False, None])
def test_healthy_service_gets_no_row(baseline_off):
    """Zdrava sluzba nema v bloku pribyt radek, ktery nic nerika.

    Zabiji mutanta: navrat Outcome.OK misto prazdneho seznamu. S nim by
    kazdy zdravy blok narostl o radek "sluzba je aktivni".
    """
    findings = DeactivationStateCheck().run(_ctx(False, baseline_off))

    assert findings == []


def test_reason_names_both_sources():
    """Duvod jmenuje, co odaktivovat - RI, rozhrani, nebo oboji."""
    scope = Scope(
        id="svc:CPE14:IPVPN",
        kind="service",
        key=None,
        selectors=Selectors(),
        routing_instance_active=False,
        interface_active=False,
    )
    ctx = CheckContext(
        scope=scope, subject={}, baseline=None, baseline_scope=None,
        config=default_config(),
    )

    findings = DeactivationStateCheck().run(ctx)

    assert findings[0].value == "RI + interface deactivated"
