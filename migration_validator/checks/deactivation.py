"""Check deaktivace sluzby.

Deaktivovana sluzba z inventory nemizi - docasne deaktivovana sluzba se porad
musi zmigrovat, takze vypustit ji je chyba, ne oprava. Tenhle check je misto,
kde se to rozhodnuti promitne do vysledku: ostatni checky nad deaktivovanou
sluzbou SKIPnou (checks/base.py), tenhle jediny ne, a rekne, co se zmenilo
proti baseline.

Zdrava sluzba tu radek nedostane. R-1 rika, ze co se nekontroluje, se
v bloku neobjevi; radek "sluzba je aktivni" by u kazdeho zdraveho bloku
pribyl a nerekl nic.
"""

from __future__ import annotations

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

ACTIVE = "aktivni"


@register
class DeactivationStateCheck(Check):
    id = "deactivation_state"
    title = "Stav deaktivace sluzby"
    label = "Deaktivace"
    mode = Mode.BOTH
    requires_inventory = True
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        subject_off = ctx.scope.is_deactivated
        baseline_off = (
            ctx.baseline_scope.is_deactivated
            if ctx.baseline_scope is not None
            else None
        )
        reason = ctx.scope.deactivation_reason

        if not subject_off and baseline_off is not True:
            return []

        if subject_off and baseline_off is None:
            return [
                Finding(
                    Outcome.SKIP,
                    f"sluzba je v konfiguraci deaktivovana ({reason}), "
                    "baseline neni k porovnani",
                    label=self.label,
                    value=reason,
                )
            ]

        if subject_off and baseline_off:
            return [
                Finding(
                    Outcome.OK,
                    f"sluzba je deaktivovana ({reason}) stejne jako v baseline",
                    label=self.label,
                    value=reason,
                    baseline_value=ctx.baseline_scope.deactivation_reason,
                )
            ]

        if subject_off:
            return [
                Finding(
                    Outcome.BROKEN,
                    f"sluzba v baseline bezela, ted je deaktivovana ({reason}) "
                    "- migrace nedokoncena",
                    label=self.label,
                    value=reason,
                    baseline_value=ACTIVE,
                )
            ]

        return [
            Finding(
                Outcome.DEGRADED,
                "sluzba byla v baseline deaktivovana "
                f"({ctx.baseline_scope.deactivation_reason}), ted je aktivni",
                label=self.label,
                value=ACTIVE,
                baseline_value=ctx.baseline_scope.deactivation_reason,
            )
        ]
