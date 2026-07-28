"""Datove modely vysledku validace."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class Status(str, Enum):
    """Vysledny stav checku nebo scope."""

    PASS = "PASS"
    SKIP = "SKIP"
    WARN = "WARN"
    FAIL = "FAIL"

    @property
    def rank(self) -> int:
        return _STATUS_RANK[self]

    @classmethod
    def worst(cls, statuses: Iterable["Status"]) -> "Status":
        """Nejhorsi stav ze sady. Prazdna sada = SKIP (nic se nezmerilo)."""
        collected = list(statuses)
        if not collected:
            return cls.SKIP
        return max(collected, key=lambda status: status.rank)


_STATUS_RANK: dict[Status, int] = {
    Status.PASS: 0,
    Status.SKIP: 1,
    Status.WARN: 2,
    Status.FAIL: 3,
}


class Severity(str, Enum):
    CRITICAL = "critical"
    ADVISORY = "advisory"


class Outcome(str, Enum):
    """Co check nameri - status z toho odvodi framework."""

    OK = "ok"
    DEGRADED = "degraded"
    BROKEN = "broken"
    SKIP = "skip"


def derive_status(outcome: Outcome, severity: Severity) -> Status:
    """Prevede vysledek mereni na status podle severity.

    DEGRADED je vzdy WARN - castecny uspech nesmi byt tvrdy FAIL ani pri
    severity critical.
    """
    if outcome is Outcome.OK:
        return Status.PASS
    if outcome is Outcome.SKIP:
        return Status.SKIP
    if outcome is Outcome.DEGRADED:
        return Status.WARN
    return Status.FAIL if severity is Severity.CRITICAL else Status.WARN


@dataclass
class Finding:
    """Namereny vysledek jednoho checku pred odvozenim statusu.

    `message` je duvod pro sbaleny radek reportu. `value`, `baseline_value`
    a `delta` jsou to, co se tiskne ve sloupcich rozbaleneho bloku - rozklad
    na popisek a hodnotu musi udelat check, protoze jen on vi, co je u dane
    veliciny hodnota a co vysvetleni.
    """

    outcome: Outcome
    message: str
    label: str | None = None
    family: int | None = None
    value: str | None = None
    baseline_value: str | None = None
    delta: str | None = None
    baseline: dict[str, Any] | None = None
    subject: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    id: str
    mode: str
    status: Status
    severity: Severity
    message: str
    label: str | None = None
    family: int | None = None
    value: str | None = None
    baseline_value: str | None = None
    delta: str | None = None
    baseline: dict[str, Any] | None = None
    subject: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "mode": self.mode,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
        }
        if self.label is not None:
            payload["label"] = self.label
        for name in ("family", "value", "baseline_value", "delta"):
            attribute = getattr(self, name)
            if attribute is not None:
                payload[name] = attribute
        if self.baseline is not None:
            payload["baseline"] = self.baseline
        if self.subject is not None:
            payload["subject"] = self.subject
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass
class MatchInfo:
    status: str  # matched | unmatched
    method: str | None = None
    confidence: str | None = None
    baseline_interfaces: list[str] = field(default_factory=list)
    subject_interfaces: list[str] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status}
        for name in ("method", "confidence", "reason"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        payload["baseline_interfaces"] = list(self.baseline_interfaces)
        payload["subject_interfaces"] = list(self.subject_interfaces)
        return payload


@dataclass
class ScopeResult:
    scope_id: str
    key: dict[str, Any]
    status: Status
    match: MatchInfo | None
    checks: list[CheckResult] = field(default_factory=list)
    identity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "key": self.key,
            "identity": self.identity,
            "status": self.status.value,
            "match": self.match.to_dict() if self.match else None,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass
class RunResult:
    evaluated_at: str
    subject: dict[str, Any]
    baseline: dict[str, Any] | None
    summary: dict[str, Any]
    scopes: list[ScopeResult] = field(default_factory=list)
    unmatched: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"baseline": [], "subject": []}
    )
    unassigned: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"bgp_peers": []}
    )
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluated_at": self.evaluated_at,
            "subject": self.subject,
            "baseline": self.baseline,
            "summary": self.summary,
            "scopes": [scope.to_dict() for scope in self.scopes],
            "unmatched": self.unmatched,
            "unassigned": self.unassigned,
        }
