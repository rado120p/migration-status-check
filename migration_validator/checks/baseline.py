"""Jedina brana pro Outcome.UNCHANGED (R-3, spec 2026-09-08).

Shodny spatny stav v baseline i subjektu je PASS se znackou - ale jen
kdyz baseline tu oblast zmerila. Selhany collector stare krabice nesmi
z chyby migrace udelat "stejne jako v baseline" (stav se nefabuluje).
Checky helper volaji misto primeho Outcome.UNCHANGED, aby podminka
zila na jednom miste.
"""

from __future__ import annotations

from migration_validator.checks.base import CheckContext
from migration_validator.models.result import Outcome

UNCHANGED_SUFFIX = ", stejne jako v baseline"

# Collector placeholder pro chybejici XML element (napr. "state" v
# zaznamu, ktery XML vubec nemel). "unknown" v obou snapshotech neni
# dukaz shodneho stavu, jen dukaz, ze ani jeden snapshot stav nezmeril -
# stav se nefabuluje, takze "unknown" nesmi projit do same= podminky
# UNCHANGED. Konstanta byla drive duplikovana v checks/evpn.py a
# checks/bfd.py (Task 7/8); Task 10 ji konsoliduje sem.
UNKNOWN = "unknown"


def unchanged_or(outcome: Outcome, ctx: CheckContext, area: str, same: bool) -> Outcome:
    if outcome not in (Outcome.BROKEN, Outcome.DEGRADED):
        return outcome
    if same and ctx.baseline_measured(area):
        return Outcome.UNCHANGED
    return outcome


def suffix(outcome: Outcome) -> str:
    return UNCHANGED_SUFFIX if outcome is Outcome.UNCHANGED else ""
