"""Testy checku deaktivace.

Matice je cela pointa: deaktivovana sluzba neni nikdy PASS, ani kdyz byla
deaktivovana i v baselinu - konfigurace by deaktivovane prvky bezne
obsahovat nemela (rozhodnuti uzivatele z 2026-08-04). Sluzba, ktera na
starem zarizeni bezela a na novem je deaktivovana, je FAIL - migrace
nedokoncena.
"""

from __future__ import annotations

import pytest

from migration_validator.checks.base import CheckContext
from migration_validator.checks.deactivation import (
    DeactivationStateCheck,
    deactivation_outcome,
)
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
        (True, False, Outcome.BROKEN),
        (True, True, Outcome.DEGRADED),
        (True, None, Outcome.DEGRADED),
        (False, True, Outcome.DEGRADED),
        (False, False, None),
        (False, None, None),
    ],
)
def test_deactivation_outcome_table(subject_off, baseline_off, expected):
    """Vsech sest kombinaci zvlast - aby selhani ukazalo, ktera se rozpojila.

    Rozdil mezi baseline_off=False a baseline_off=None je nosny: prvni
    znamena "v baselinu bezel", druhy "baseline neni k porovnani". Kdyby je
    funkce splacala dohromady, radek (True, None) by dal BROKEN a bezny beh
    bez baseline by kazdou deaktivovanou sluzbu hlasil jako nedokoncenou
    migraci.
    """
    assert deactivation_outcome(subject_off, baseline_off) is expected


@pytest.mark.parametrize(
    "subject_off,baseline_off,expected",
    [
        (True, True, Outcome.DEGRADED),
        (True, False, Outcome.BROKEN),
        (False, True, Outcome.RECOVERED),
        (True, None, Outcome.DEGRADED),
    ],
)
def test_matrix(subject_off, baseline_off, expected):
    """Kazda bunka matice zvlast - aby selhani ukazalo, ktera se rozpojila.

    Zabiji mutanta: kterakoli zamena dvojice vetvi. Radek (True, False) je
    ten, ktery odlisuje "deaktivovano i drive" od "deaktivovano az ted" -
    bez nej by slo obe hlasit jako PASS. Radek (False, True) je reaktivace:
    check ho hlasi jako Outcome.RECOVERED, i kdyz sdilena
    `deactivation_outcome` pro nej porad pocita DEGRADED (viz
    test_deactivation_outcome_table) - RECOVERED dosazuje az
    DeactivationStateCheck.run.
    """
    findings = DeactivationStateCheck().run(_ctx(subject_off, baseline_off))

    assert len(findings) == 1
    assert findings[0].outcome is expected


def test_reactivated_service_is_recovered():
    """Zlepseni je RECOVERED, ne varovani (R-2) - hlaska se nemeni."""
    findings = DeactivationStateCheck().run(_ctx(False, True))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.RECOVERED
    assert findings[0].message == (
        "sluzba byla v baseline deaktivovana (interface deactivated), "
        "ted je aktivni"
    )


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
