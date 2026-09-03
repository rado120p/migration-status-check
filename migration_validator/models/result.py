"""Datove modely vysledku validace."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class Status(str, Enum):
    """Vysledny stav checku nebo scope."""

    PASS = "PASS"
    RECV = "RECV"
    SKIP = "SKIP"
    WARN = "WARN"
    FAIL = "FAIL"
    INFO = "INFO"

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


def count_statuses(statuses: Iterable[Status]) -> dict[str, int]:
    """Rozpad stavu na sest counteru.

    Bere libovolne stavy, aby stejnou funkci mohl pouzit souhrn za checky
    i souhrn za sluzby - prave rozdil mezi temi dvema jednotkami byl v
    reportu neoznaceny a operator si odnasel cislo, na ktere se nedival.
    """
    counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0, "info": 0, "recv": 0}
    for status in statuses:
        counts[status.value.lower()] += 1
    return counts


_STATUS_RANK: dict[Status, int] = {
    Status.INFO: -1,
    Status.PASS: 0,
    Status.RECV: 1,
    Status.SKIP: 2,
    Status.WARN: 3,
    Status.FAIL: 4,
}


class Severity(str, Enum):
    CRITICAL = "critical"
    ADVISORY = "advisory"


class Outcome(str, Enum):
    """Co check nameri - status z toho odvodi framework."""

    OK = "ok"
    INFO = "info"
    RECOVERED = "recovered"
    DEGRADED = "degraded"
    BROKEN = "broken"
    SKIP = "skip"


def derive_status(outcome: Outcome, severity: Severity) -> Status:
    """Prevede vysledek mereni na status podle severity.

    DEGRADED je vzdy WARN - castecny uspech nesmi byt tvrdy FAIL ani pri
    severity critical. RECOVERED je vzdy RECV - zlepseni proti baseline
    neni varovani, ale ma byt videt.
    """
    if outcome is Outcome.OK:
        return Status.PASS
    if outcome is Outcome.INFO:
        return Status.INFO
    if outcome is Outcome.RECOVERED:
        return Status.RECV
    if outcome is Outcome.SKIP:
        return Status.SKIP
    if outcome is Outcome.DEGRADED:
        return Status.WARN
    return Status.FAIL if severity is Severity.CRITICAL else Status.WARN


# Klic v CheckResult.details, kterym check rekne, PROC byl preskocen.
# Zije v models, ne v checks/ ani v reporting/: pisou ho checky a cte ho
# renderer, a ani jeden z tech baliku nema na druhy videt.
SKIPPED_BECAUSE = "skipped_because"

# Jedina hodnota, kterou renderer sleva. Ostatni SKIPy (chybejici
# inventory, compare bez baseline, selhany collector, vyjimka v checku)
# znacku nedostavaji, protoze kazdy z nich nese vlastni informaci.
SKIP_DEACTIVATED = "service_deactivated"


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
    group: str | None = None
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
    group: str | None = None
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
        if self.group is not None:
            payload["group"] = self.group
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
    link: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "scope_id": self.scope_id,
            "key": self.key,
            "identity": self.identity,
            "status": self.status.value,
            "match": self.match.to_dict() if self.match else None,
            "checks": [check.to_dict() for check in self.checks],
        }
        # Aditivni klic: bez vazby zustava tvar presne ten, ktery uz cte okoli.
        if self.link is not None:
            payload["link"] = self.link
        return payload


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
        default_factory=lambda: {
            "bgp_peers": [],
            "static_routes": [],
            "bfd_sessions": [],
        }
    )
    # Zaznam o tom, ze tohle uz neni cely beh. Vyplneny je jen u vysledku,
    # ktery prosel filtrem - a protoze `evaluate --format json --status fail`
    # zapisuje prave ten profiltrovany, bez nej by strojovy vystup hlasil
    # prepoctena cisla a nic by neprozradilo, ze nejde o cely beh.
    filtered: dict[str, Any] | None = None
    # Jmeno profilu (basename souboru), pod kterym beh vznikl - nezavisle na
    # tom, jestli profil nesl service_types filtr. Na rozdil od `filtered`,
    # ktery se plni jen pri aktivnim filtrovani sluzeb, tohle ma byt v
    # kazdem behu s profilem, aby report vzdy rekl, pod cim vznikl.
    profile: str | None = None
    # Migracni krok (old/new endpoint), pod kterym vysledek vznikl -
    # aditivni klic, stejne pravidlo jako `profile`.
    step: dict[str, Any] | None = None
    # Sluzby subjektu potlacene filtrem pres baseline (cizi vlny na LAGu).
    # Plni se jen se `step` - i prazdny seznam rika "filtr bezel".
    excluded_services: list[dict[str, Any]] | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "evaluated_at": self.evaluated_at,
            "subject": self.subject,
            "baseline": self.baseline,
            "summary": self.summary,
            "scopes": [scope.to_dict() for scope in self.scopes],
            "unmatched": self.unmatched,
            "unassigned": self.unassigned,
        }
        # Klic se nepridava prazdny: nefiltrovany beh ma zustat presne tim
        # tvarem, ktery uz cte okoli.
        if self.filtered is not None:
            payload["filtered"] = self.filtered
        # Stejne pravidlo jako u `filtered`: beh bez profilu ma zustat
        # presne tim tvarem, ktery uz cte okoli.
        if self.profile is not None:
            payload["profile"] = self.profile
        if self.step is not None:
            payload["step"] = self.step
        if self.excluded_services is not None:
            payload["excluded_services"] = self.excluded_services
        return payload
