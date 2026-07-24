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
    derive_status,
)
from migration_validator.models.scope import Scope


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

    @property
    def has_baseline(self) -> bool:
        return self.baseline is not None

    def options(self, check_id: str) -> dict[str, Any]:
        return self.config.options(check_id)


class Check(ABC):
    id: ClassVar[str]
    title: ClassVar[str]
    mode: ClassVar[Mode] = Mode.STATE
    requires: ClassVar[tuple[str, ...]] = ()
    requires_inventory: ClassVar[bool] = False
    service_types: ClassVar[frozenset[str] | None] = None
    default_severity: ClassVar[Severity] = Severity.ADVISORY

    def applies_to(self, scope: Scope) -> bool:
        """Device scope dostane vsechny checky - filtrovat nema podle ceho."""
        if scope.is_device:
            return True
        if self.service_types is None:
            return True
        return scope.service_type in self.service_types

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
            "default_severity": self.default_severity.value,
        }


def _skip(check: Check, severity: Severity, message: str) -> list[CheckResult]:
    return [
        CheckResult(
            id=check.id,
            mode=check.mode.value,
            status=derive_status(Outcome.SKIP, severity),
            severity=severity,
            message=message,
        )
    ]


def run_check(check: Check, ctx: CheckContext) -> list[CheckResult]:
    """Spusti check a prevede jeho Findings na CheckResults."""
    if not ctx.config.enabled(check.id):
        return []
    if not check.applies_to(ctx.scope):
        return []

    severity = ctx.config.severity(check.id, check.default_severity)

    if check.requires_inventory and ctx.scope.is_device:
        return _skip(check, severity, "check vyzaduje inventory, snapshot ji neobsahuje")

    if check.mode is Mode.COMPARE and not ctx.has_baseline:
        return _skip(check, severity, "porovnavaci check bez baseline snapshotu")

    for area in check.requires:
        if area in ctx.failed_collectors:
            return _skip(
                check,
                severity,
                f"chybi data z collectoru '{area}': {ctx.failed_collectors[area]}",
            )

    try:
        findings = check.run(ctx)
    except Exception as error:  # noqa: BLE001 - jeden rozbity check nesmi zabit cely beh
        return _skip(check, severity, f"check selhal: {error}")

    return [
        CheckResult(
            id=check.id,
            mode=check.mode.value,
            status=derive_status(finding.outcome, severity),
            severity=severity,
            message=finding.message,
            label=finding.label,
            baseline=finding.baseline,
            subject=finding.subject,
            details=finding.details,
        )
        for finding in findings
    ]
