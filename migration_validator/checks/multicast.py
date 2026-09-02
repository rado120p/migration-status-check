"""Multicast checky (spec 2026-09-02): IGMP membership report, multicast
forwarding (IGMP-rizene pro Internet/IPVPN, inet.2-rizene pro Core lo0.0)
a MVPN c-multicast / provider tunnel.

IGMP mnozina definuje ocekavane streamy. Bez ni forwarding i MVPN radky
kaskaduji do SKIP (ne nezavisle hledani v tabulce). Upstream, downstream,
rate a uptime se proti baseline neporovnavaji nikdy - meni se migraci
z definice; porovnava se jen mnozina (S,G) a sender PE tunelu.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.collectors.multicast import route_key
from migration_validator.models.result import Finding, Outcome, Severity
from migration_validator.models.scope import Scope

MULTICAST_TYPES = frozenset({"Internet", "IPVPN"})
MULTICAST_SUBTYPES = frozenset({"multicast", "mvpn-igmp"})

NO_REPORT = "Receiver neposila zadny IGMP membership report"
NO_REPORT_SKIP = "bez IGMP reportu"
# junos-evo casto vraci <multicast-statistics-timed-out/> misto
# forwarding-rate-packets i na zive Forwarding route (overeno 2026-09-02).
# Absence hodnoty neni nula - musi se odlisit SKIP od BROKEN.
RATE_UNAVAILABLE = "statistiky nedostupne"


# --- helpery ---------------------------------------------------------------

def sg_label(source: str | None, group: str) -> str:
    return f"({source or '*'}, {group})"


def pairs_text(pairs: list[tuple[str | None, str]]) -> str:
    return ", ".join(sg_label(s, g) for s, g in pairs)


def igmp_pairs(facts: dict[str, Any] | None, scope: Scope) -> list[tuple[str | None, str]]:
    """(source, group) pro rozhrani scopu - serazene, bez duplicit.
    Baseline se cte s baseline scopem: jmena rozhrani se migraci meni."""
    groups = (facts or {}).get("igmp_group") or {}
    pairs = {
        (entry.get("source"), str(entry["group"]))
        for name in scope.selectors.interfaces
        for entry in groups.get(name, [])
        if entry.get("group")
    }
    return sorted(pairs, key=lambda p: (p[0] or "", p[1]))


def multicast_table(facts: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Scope ma nejvys jednu instanci (Scope.select), tak se tabulky slouci."""
    table: dict[str, dict[str, Any]] = {}
    for routes in ((facts or {}).get("multicast_route") or {}).values():
        table.update(routes)
    return table


def routes_for(
    table: dict[str, dict[str, Any]], source: str | None, group: str
) -> list[tuple[str, dict[str, Any]]]:
    """Routy k IGMP zaznamu. Multicast tabulka je vzdy (S,G); pro ASM
    zaznam (*, G) patri kazda routa s tou group."""
    if source is not None:
        key = route_key(source, group)
        return [(key, table[key])] if key in table else []
    return sorted(
        (key, route) for key, route in table.items() if key.split(",", 1)[1] == group
    )


def format_uptime_hms(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    clock = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{days}d {clock}" if days else clock


def stream_rows(sg: str, route: dict[str, Any], *, rate_label: str) -> list[Finding]:
    """Forwarding rate (> 0) a uptime (INFO) - spolecne pro vsechny tri
    forwarding checky. Bez porovnani proti baseline.

    forwarding_rate_pps muze byt None (junos-evo statistics-timed-out) -
    to je SKIP, ne BROKEN 0 pps: nemereno neni totez jako nula."""
    raw_pps = route.get("forwarding_rate_pps")
    if raw_pps is None:
        rate_row = Finding(
            Outcome.SKIP,
            f"{sg}: forwarding statistiky nejsou ve vypisu "
            "(multicast-statistics-timed-out)",
            label=rate_label, group=sg, value=RATE_UNAVAILABLE,
        )
    else:
        pps = int(raw_pps)
        rate_row = Finding(
            Outcome.OK if pps > 0 else Outcome.BROKEN,
            f"{sg}: forwarding rate {pps} pps",
            label=rate_label, group=sg, value=f"{pps} pps",
        )
    return [
        rate_row,
        Finding(
            Outcome.INFO,
            f"{sg}: route uptime {format_uptime_hms(route.get('uptime_seconds'))}",
            label="Route uptime", group=sg,
            value=format_uptime_hms(route.get("uptime_seconds")),
        ),
    ]


def _baseline_pairs(ctx: CheckContext) -> list[tuple[str | None, str]] | None:
    if not ctx.has_baseline:
        return None
    return igmp_pairs(ctx.baseline, ctx.baseline_scope or ctx.scope)


# --- igmp_membership_report -------------------------------------------------

@register
class IgmpMembershipReportCheck(Check):
    id = "igmp_membership_report"
    title = "IGMP membership report receiveru"
    label = "IGMP membership report"
    mode = Mode.BOTH
    requires = ("igmp_group",)
    requires_inventory = True
    service_types = MULTICAST_TYPES
    service_subtypes = MULTICAST_SUBTYPES
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        now = igmp_pairs(ctx.subject, ctx.scope)
        was = _baseline_pairs(ctx)
        # Baseline bez skupin = neni s cim porovnat (no-baseline pravidlo),
        # ne "bylo prazdno".
        was_value = pairs_text(was) if was else None
        if not now:
            return [Finding(
                Outcome.BROKEN, "receiver neposila zadny IGMP membership report",
                label=self.label, value=NO_REPORT, baseline_value=was_value,
            )]
        if was and set(was) != set(now):
            return [Finding(
                Outcome.DEGRADED,
                f"IGMP skupiny se zmenily proti baseline: {pairs_text(now)}",
                label=self.label, value=pairs_text(now), baseline_value=was_value,
            )]
        return [Finding(
            Outcome.OK, f"IGMP membership report: {pairs_text(now)}",
            label=self.label, value=pairs_text(now), baseline_value=was_value,
        )]
