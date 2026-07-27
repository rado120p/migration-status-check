"""Terminalovy vystup.

Sekce NESPAROVANO se vypisuje vzdy, i kdyz je vsechno ostatni zelene, a
filtrovani se na ni nevztahuje - je to hlavni pojistka proti prehlednuti
nezmigrovane sluzby.
"""

from __future__ import annotations

from dataclasses import replace

from migration_validator.models.result import RunResult, ScopeResult, Status

SYMBOL = {
    Status.PASS: "OK ",
    Status.WARN: "WARN",
    Status.FAIL: "FAIL",
    Status.SKIP: "SKIP",
}

_STATUS_ORDER = (Status.FAIL, Status.WARN, Status.SKIP, Status.PASS)


def filter_result(
    result: RunResult,
    *,
    text: str | None = None,
    statuses: set[Status] | None = None,
) -> RunResult:
    """Vrati kopii vysledku s profiltrovanymi scopy. Unmatched zustava cely."""
    scopes = list(result.scopes)

    if text:
        needle = text.lower()
        scopes = [
            scope
            for scope in scopes
            if needle in scope.scope_id.lower()
            or needle in str(scope.key.get("description", "")).lower()
        ]

    if statuses:
        scopes = [scope for scope in scopes if scope.status in statuses]

    return replace(result, scopes=scopes)


def _worst_message(scope: ScopeResult) -> str:
    if scope.status is Status.PASS:
        return ""
    for status in _STATUS_ORDER:
        for check in scope.checks:
            if check.status is status and status is not Status.PASS:
                return check.message
    return ""


def _detail_lines(scope: ScopeResult) -> list[str]:
    """Vsechny checky sluzby, nejhorsi nahore.

    Souhrnny radek ukazuje jen nejhorsi nalez, takze bez tohohle nejde poznat,
    co dalsiho se kontrolovalo - a hlavne jestli PASS znamena "overeno", nebo
    "check se vubec nespustil".
    """
    ordered = sorted(
        scope.checks,
        key=lambda check: (_STATUS_ORDER.index(check.status), check.id, check.label or ""),
    )
    return [
        f"    {SYMBOL[check.status]:<5} {check.id:<22.22} "
        f"{str(check.severity.value):<9.9} {check.message}"
        for check in ordered
    ]


def render(result: RunResult, *, detail: bool = False) -> str:
    lines: list[str] = []

    subject = result.subject
    baseline = result.baseline
    if baseline:
        lines.append(
            f"Migrace: {baseline['address']} ({baseline['phase']}) -> "
            f"{subject['address']} ({subject['phase']})"
        )
    else:
        lines.append(f"Validace: {subject['address']} ({subject['phase']})")
    lines.append("")

    summary = result.summary
    lines.append(
        f"  {summary['pass']} PASS   {summary['warn']} WARN   "
        f"{summary['fail']} FAIL   {summary['skip']} SKIP"
    )
    lines.append(
        f"  Sparovano {summary['scopes_matched']} sluzeb, "
        f"{summary['unmatched_baseline']} nesparovana v baseline, "
        f"{summary['unmatched_subject']} nesparovane v subject"
    )
    lines.append("")

    lines.append(f"{'SLUZBA':<32} {'TYP':<10} {'STAV':<5} DETAIL")
    for scope in result.scopes:
        description = scope.key.get("description") or scope.scope_id
        service_type = scope.key.get("service_type") or "-"
        lines.append(
            f"{description:<32.32} {service_type:<10.10} "
            f"{SYMBOL[scope.status]:<5} {_worst_message(scope)}"
        )
        if detail:
            lines.extend(_detail_lines(scope))
            lines.append("")

    lines.append("")
    lines.append("NESPAROVANO")
    if not result.unmatched["baseline"] and not result.unmatched["subject"]:
        lines.append("  (nic)")
    for side in ("baseline", "subject"):
        for item in result.unmatched[side]:
            label = item.get("description") or item["scope_id"]
            service_type = item.get("service_type") or "-"
            lines.append(f"  {side:<9} {label:<32.32} ({service_type})  {item['reason']}")

    return "\n".join(lines) + "\n"
