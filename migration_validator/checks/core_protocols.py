"""Checky protokolu Core transit/loopback (spec 2026-08-26).

Absence rozhrani ve vypisu je mereni, ne dira: FAIL 'chybi v outputu'.
Collector klice nesyntetizuje, takze tenhle modul je jedine misto, ktere
absenci vyklada."""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.ifaces import is_transit, qualified
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

MISSING = "chybí v outputu"
CORE = frozenset({"Core"})


def _scope_transit_interfaces(ctx: CheckContext) -> list[str]:
    """Merene jednotky bere ze selektoru (zamer), ne z faktu - rozhrani,
    ktere z vypisu zmizelo, musi dostat radek, ne ticho."""
    return sorted(
        name for name in ctx.scope.selectors.interfaces if is_transit(name)
    )


def format_uptime(seconds: int | None) -> str:
    if not seconds:
        return "0s"
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


@register
class IsisAdjacencyStateCheck(Check):
    id = "isis_adjacency_state"
    title = "Stav IS-IS adjacency"
    label = "IS-IS adjacency state"
    mode = Mode.BOTH
    requires = ("isis_adjacency",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        subject: dict[str, Any] = ctx.subject.get("isis_adjacency", {})
        baseline: dict[str, Any] = (ctx.baseline or {}).get("isis_adjacency", {})
        findings: list[Finding] = []
        for name in _scope_transit_interfaces(ctx):
            adj = subject.get(name)
            was = baseline.get(name)
            if adj is None:
                # (was or {}).get("state") muze byt None i kdyz `was`
                # existuje (baseline zaznam bez klice "state") - stringifikace
                # bez tehle stopky by tam nechala doslovny retezec "None".
                was_state = (was or {}).get("state")
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{name}: rozhrani neni v IS-IS adjacency vypisu",
                    label=qualified(self.label, name),
                    value=MISSING,
                    baseline_value=str(was_state) if was_state is not None else None,
                ))
                continue
            findings.extend(self._rows(name, adj, was, ctx.has_baseline))
        return findings

    def _rows(self, name, adj, was, has_baseline):
        rows = []
        # system/was_system muzou byt pritomne, ale None (klic "system_name"
        # bez hodnoty) - str(None) by do sloupce hodnot poslalo doslovny
        # retezec "None" misto poctiveho MISSING/absence.
        system = adj.get("system_name")
        system_value = str(system) if system is not None else MISSING
        was_system = was.get("system_name") if was else None
        was_system_value = str(was_system) if was_system is not None else None
        if has_baseline and was is not None and system != was_system:
            rows.append(Finding(
                Outcome.DEGRADED,
                f"{name}: IS-IS soused {system}, v baseline {was_system}",
                label=qualified("IS-IS neighbor name", name),
                value=system_value, baseline_value=was_system_value,
            ))
        else:
            outcome = Outcome.OK if has_baseline and was is not None else Outcome.INFO
            rows.append(Finding(
                outcome, f"{name}: IS-IS soused {system}",
                label=qualified("IS-IS neighbor name", name), value=system_value,
            ))

        state = str(adj.get("state", "unknown"))
        was_raw_state = was.get("state") if was else None
        was_state = str(was_raw_state) if was_raw_state is not None else None
        if state != "Up":
            outcome = Outcome.BROKEN
        elif has_baseline and was_state is not None and was_state != "Up":
            # Up ted, ale v baseline nebyl - zlepseni je porad zmena
            outcome = Outcome.DEGRADED
        else:
            outcome = Outcome.OK
        rows.append(Finding(
            outcome, f"{name}: adjacency {state}",
            label=qualified(self.label, name),
            value=state, baseline_value=was_state,
        ))

        for label, key in (
            ("IS-IS neighbor IPv4 address", "ip_address"),
            ("IS-IS neighbor IPv6 address", "ipv6_address"),
        ):
            value = adj.get(key)
            was_value = was.get(key) if was else None
            if has_baseline and was is not None and value != was_value:
                outcome = Outcome.DEGRADED
            else:
                outcome = Outcome.OK if value is not None else Outcome.BROKEN
            rows.append(Finding(
                outcome,
                f"{name}: {label} {value or 'chybi'}",
                label=qualified(label, name),
                value=str(value) if value else MISSING,
                baseline_value=str(was_value) if was_value else None,
            ))
        return rows
