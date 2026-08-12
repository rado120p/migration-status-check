"""Check deaktivace sluzby.

Deaktivovana sluzba z inventory nemizi - docasne deaktivovana sluzba se porad
musi zmigrovat, takze vypustit ji je chyba, ne oprava. Tenhle check je misto,
kde se to rozhodnuti promitne do vysledku: ostatni checky nad deaktivovanou
sluzbou SKIPnou (checks/base.py), tenhle jediny ne. Sam SKIP nikdy nevydava:
deaktivovana sluzba neni PASS ani SKIP, je to nalez.

Zdrava sluzba tu radek nedostane. R-1 rika, ze co se nekontroluje, se
v bloku neobjevi; radek "sluzba je aktivni" by u kazdeho zdraveho bloku
pribyl a nerekl nic.
"""

from __future__ import annotations

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

ACTIVE = "aktivni"


def deactivation_outcome(subject_off: bool, baseline_off: bool | None) -> Outcome | None:
    """Sdilena semantika deaktivace pro sluzbu, statickou routu i BGP peera.

    Rozhodnuti uzivatele z 2026-08-04: deaktivovany prvek konfigurace je sam
    o sobe nalez. Konfigurace by deaktivovane prvky bezne obsahovat nemela,
    takze sluzba, ktera nejaky nese, nesmi byt PASS. Baseline neurcuje
    JESTLI se to hlasi, jen JAK NAHLAS.

    `baseline_off is None` znamena "baseline neni k porovnani" a je to neco
    jineho nez `False` ("v baselinu bezel"). Splacnuti obou dohromady je
    duvod, proc je tahle funkce psana pres `is False` a ne pres `not`.

    Navrat `None` znamena "zadny radek nevznika" - zdravy prvek nema v bloku
    dostat radek, ktery nic nerika (R-1).

    Radek 4 tabulky (`subject_off=False`, `baseline_off=True` - aktivni ted,
    vypnuty v baselinu) patri jen sluzbe samotne. checks/routes.py a
    checks/bgp.py volaji tuhle funkci pro podprvky vzdy s `subject_off=True`
    - znovuzapnuta routa nebo peer zadny deaktivovany prvek nenesou a
    zlepseni neni varovani (R-2), takze pro ne se `deactivation_outcome`
    s `subject_off=False` vubec nevola.
    """
    if not subject_off and not baseline_off:
        return None
    if subject_off and baseline_off is False:
        return Outcome.BROKEN
    return Outcome.DEGRADED


@register
class DeactivationStateCheck(Check):
    id = "deactivation_state"
    title = "Stav deaktivace sluzby"
    label = "Deaktivace"
    mode = Mode.BOTH
    requires_inventory = True
    default_severity = Severity.CRITICAL
    layer1 = True

    def run(self, ctx: CheckContext) -> list[Finding]:
        subject_off = ctx.scope.is_deactivated
        baseline_off = (
            ctx.baseline_scope.is_deactivated
            if ctx.baseline_scope is not None
            else None
        )
        reason = ctx.scope.deactivation_reason

        outcome = deactivation_outcome(subject_off, baseline_off)
        if outcome is None:
            return []

        if subject_off and baseline_off is None:
            return [
                Finding(
                    outcome,
                    f"sluzba je v konfiguraci deaktivovana ({reason}), "
                    "baseline neni k porovnani",
                    label=self.label,
                    value=reason,
                )
            ]

        if subject_off and baseline_off:
            return [
                Finding(
                    outcome,
                    f"sluzba je deaktivovana ({reason}) stejne jako v baseline",
                    label=self.label,
                    value=reason,
                    baseline_value=ctx.baseline_scope.deactivation_reason,
                )
            ]

        if subject_off:
            return [
                Finding(
                    outcome,
                    f"sluzba v baseline bezela, ted je deaktivovana ({reason}) "
                    "- migrace nedokoncena",
                    label=self.label,
                    value=reason,
                    baseline_value=ACTIVE,
                )
            ]

        return [
            Finding(
                outcome,
                "sluzba byla v baseline deaktivovana "
                f"({ctx.baseline_scope.deactivation_reason}), ted je aktivni",
                label=self.label,
                value=ACTIVE,
                baseline_value=ctx.baseline_scope.deactivation_reason,
            )
        ]
