"""Zaklad checku.

Check nevraci status primo - vraci Finding a status z nej odvodi framework.
Diky tomu je pravidlo "castecny uspech = WARN" na jednom miste a autor
noveho checku ho nemuze omylem porusit.

Checky nikdy nesahaji na sit. Tento modul nesmi importovat nic z
migration_validator.connection.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from migration_validator.config import CheckConfig
from migration_validator.models.result import (
    CheckResult,
    Finding,
    Outcome,
    Severity,
    SKIPPED_BECAUSE,
    SKIP_DEACTIVATED,
    derive_status,
)
from migration_validator.models.scope import LAYER1_SERVICE_TYPE, Scope


class Mode(str, Enum):
    STATE = "state"
    COMPARE = "compare"
    BOTH = "both"


@dataclass
class CheckContext:
    scope: Scope
    subject: dict[str, Any]
    baseline: dict[str, Any] | None
    config: CheckConfig
    failed_collectors: dict[str, str] = field(default_factory=dict)
    # Priznak deaktivace lezi na scopu, ne ve faktech, takze bez baseline
    # scopu nejde porovnat "deaktivovano i drive" proti "deaktivovano az ted".
    baseline_scope: Scope | None = None
    link: dict[str, Any] | None = None

    @property
    def has_baseline(self) -> bool:
        return self.baseline is not None

    def options(self, check_id: str) -> dict[str, Any]:
        return self.config.options(check_id)


class Check(ABC):
    id: ClassVar[str]
    title: ClassVar[str]
    # Popisek do sloupce CHECK pro radky, ktere nevznikly uvnitr checku
    # (skip od frameworku) nebo ktere si vlastni popisek nenesou. Bez nej
    # spadl radek na id checku a mezi hezkymi popisky sedelo
    # `SKIP | evpn_esi_status`. `title` se na to nehodi - je to veta
    # o checku ("Stav EVPN ESI"), ne popisek sloupce.
    label: ClassVar[str]
    mode: ClassVar[Mode] = Mode.STATE
    requires: ClassVar[tuple[str, ...]] = ()
    requires_inventory: ClassVar[bool] = False
    service_types: ClassVar[frozenset[str] | None] = None
    # AND ke service_types - kdyz je nastaveny, musi sedet i subtype
    # (napr. Core transit vs. Core loopback). Vychozi None nic nefiltruje.
    service_subtypes: ClassVar[frozenset[str] | None] = None
    default_severity: ClassVar[Severity] = Severity.ADVISORY
    # Bezi check i na Layer1 scopu (fyzicky port)? Vychozi ne - vetsina
    # checku meri sluzbu, ne port, a SKIP radky by L1 blok jen zaplevelily.
    layer1: ClassVar[bool] = False

    def applies_to(self, scope: Scope) -> bool:
        """Device scope dostane vsechny checky - filtrovat nema podle ceho."""
        if scope.is_device:
            return True
        if scope.kind == "layer1":
            return self.layer1
        if self.service_types is not None:
            if scope.service_type not in self.service_types:
                return False
        if self.service_subtypes is not None:
            if scope.service_subtype not in self.service_subtypes:
                return False
        return True

    @abstractmethod
    def run(self, ctx: CheckContext) -> list[Finding]:
        """Vrati namerene vysledky. Status odvodi run_check()."""

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "mode": self.mode.value,
            "requires": list(self.requires),
            "requires_inventory": self.requires_inventory,
            "service_types": (
                sorted(self.service_types) if self.service_types else None
            ),
            "service_subtypes": (
                sorted(self.service_subtypes) if self.service_subtypes else None
            ),
            "default_severity": self.default_severity.value,
        }


def _skip(
    check: Check,
    severity: Severity,
    message: str,
    value: str,
    *,
    skipped_because: str | None = None,
) -> list[CheckResult]:
    """Skip, ktery vznikl mimo check - a presto je to plnohodnotny radek.

    `value` je kratky duvod do sloupce hodnot, `message` zustava celou
    vetou pro sloupec NALEZ a strojovy vystup. Drive tu obe pole chybela,
    takze renderer sahl po id checku a po cele vete; u selhaneho collectoru
    to byla veta o RPC chybe, ktera roztahla blok na 270 znaku sirky.

    `skipped_because` je STRUKTURALNI znacka pro renderer. Vyplnuje ji
    jedina vetva (deaktivovana sluzba), protoze jedine ta vyrabi N radku,
    ktere rikaji doslova totez. Renderer podle ni sleva - ne podle
    Status.SKIP a ne podle textu zpravy: v jednom bloku sedi vedle sebe
    devet deaktivacnich SKIPu a jeden 'bez baseline', a ten druhy nese
    informaci, kterou nic jineho nenese.
    """
    return [
        CheckResult(
            id=check.id,
            mode=check.mode.value,
            status=derive_status(Outcome.SKIP, severity),
            severity=severity,
            message=message,
            label=check.label,
            value=value,
            details={SKIPPED_BECAUSE: skipped_because} if skipped_because else {},
        )
    ]


# Check, ktery deaktivaci hlasi, se sam preskocit nesmi - jinak by nebylo co
# porovnat a sluzba by v reportu zmizela do SKIPu bez duvodu.
DEACTIVATION_CHECK_ID = "deactivation_state"


def run_check(check: Check, ctx: CheckContext) -> list[CheckResult]:
    """Spusti check a prevede jeho Findings na CheckResults."""
    if not ctx.config.enabled(check.id):
        return []
    if not check.applies_to(ctx.scope):
        return []

    severity = ctx.config.severity(check.id, check.default_severity)

    if check.requires_inventory and ctx.scope.is_device:
        return _skip(
            check,
            severity,
            "check vyzaduje inventory, snapshot ji neobsahuje",
            "bez inventory",
        )

    if check.mode is Mode.COMPARE and not ctx.has_baseline:
        return _skip(
            check, severity, "porovnavaci check bez baseline snapshotu", "bez baseline"
        )

    # Poradi je soucast pozadavku: zkratka jde az za requires_inventory, takze
    # v device scope preskoci uz ten - device scope inventory nema a nema tedy
    # ani z ceho priznak vzit.
    if check.id != DEACTIVATION_CHECK_ID and ctx.scope.is_deactivated:
        reason = ctx.scope.deactivation_reason
        return _skip(
            check,
            severity,
            f"sluzba je v konfiguraci deaktivovana ({reason})",
            reason,
            skipped_because=SKIP_DEACTIVATED,
        )

    for area in check.requires:
        if area in ctx.failed_collectors:
            return _skip(
                check,
                severity,
                f"chybi data z collectoru '{area}': {ctx.failed_collectors[area]}",
                "collector selhal",
            )

    try:
        findings = check.run(ctx)
    except Exception as error:  # noqa: BLE001 - jeden rozbity check nesmi zabit cely beh
        return _skip(check, severity, f"check selhal: {error}", "check selhal")

    return [
        CheckResult(
            id=check.id,
            mode=check.mode.value,
            status=derive_status(finding.outcome, severity),
            severity=severity,
            message=finding.message,
            # Popisek doplnuje framework, ne renderer: renderer vidi jen
            # CheckResult, takze by nemel odkud vzit nic lepsiho nez id
            # checku - a to je presne ten radek, ktery se do reportu nemel
            # nikdy dostat.
            label=finding.label or check.label,
            group=finding.group,
            family=finding.family,
            value=finding.value,
            baseline_value=finding.baseline_value,
            delta=finding.delta,
            baseline=finding.baseline,
            subject=finding.subject,
            details=finding.details,
        )
        for finding in findings
    ]
