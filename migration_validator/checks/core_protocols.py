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

MISSING = "chybi v outputu"
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
                baseline_value=was_system_value,
            ))

        state = str(adj.get("state", "unknown"))
        was_raw_state = was.get("state") if was else None
        was_state = str(was_raw_state) if was_raw_state is not None else None
        message = f"{name}: adjacency {state}"
        if state != "Up":
            outcome = Outcome.BROKEN
        elif has_baseline and was_state is not None and was_state != "Up":
            # Up ted, v baseline nebyl - zlepseni proti baseline, ne tiche OK
            outcome = Outcome.RECOVERED
            message = f"{name}: adjacency Up (v baseline {was_state})"
        else:
            outcome = Outcome.OK
        rows.append(Finding(
            outcome, message,
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


@register
class IsisInterfaceInfoCheck(Check):
    id = "isis_interface_info"
    title = "IS-IS konfigurace rozhrani"
    label = "IS-IS interface"
    mode = Mode.STATE
    requires = ("isis_interface",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit", "loopback"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        loopback = ctx.scope.service_subtype == "loopback"
        names = (
            sorted(ctx.scope.selectors.interfaces)
            if loopback
            else _scope_transit_interfaces(ctx)
        )
        data: dict[str, Any] = ctx.subject.get("isis_interface", {})
        findings: list[Finding] = []
        for name in names:
            entry = data.get(name)
            if entry is None:
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{name}: rozhrani neni v IS-IS interface vypisu",
                    label=qualified(self.label, name), value=MISSING,
                ))
                continue
            levels = entry.get("levels", {})
            findings.append(Finding(
                Outcome.OK if "2" in levels else Outcome.BROKEN,
                f"{name}: IS-IS level 2 {'nakonfigurovan' if '2' in levels else 'chybi'}",
                label=qualified("IS-IS level 2", name),
                value="nakonfigurovan" if "2" in levels else MISSING,
            ))
            if "1" in levels:
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{name}: IS-IS level 1 nema na Core rozhrani co delat",
                    label=qualified("IS-IS level 1", name), value="nakonfigurovan",
                ))
            if "2" not in levels:
                # Chybejici level 2 uz sam nese BROKEN - passive radek by
                # meril neco, co bez adjacency nema smysl (jeden FAIL, ne dva).
                continue
            passive = bool(levels.get("2", {}).get("passive"))
            # Loopback pasivni byt musi (nema souseda), transit nesmi
            # (pasivni port nesestavi adjacency, kterou meri
            # isis_adjacency_state).
            ok = passive if loopback else not passive
            findings.append(Finding(
                Outcome.OK if ok else Outcome.BROKEN,
                f"{name}: level 2 passive={'ano' if passive else 'ne'}",
                label=qualified("IS-IS level 2 passive", name),
                value="Passive" if passive else "bez Passive",
            ))
        return findings


def _neighbor_findings(
    ctx: CheckContext, *, area: str, status_label: str, address_label: str,
    names: list[str],
) -> list[Finding]:
    """Sdilena kostra pro LDP/PIM soused - checky se lisi jen gate na zamer
    a labely, ne logikou."""
    subject: dict[str, Any] = ctx.subject.get(area, {})
    baseline: dict[str, Any] = (ctx.baseline or {}).get(area, {})
    findings: list[Finding] = []
    for name in names:
        entry = subject.get(name)
        was = baseline.get(name)
        if entry is None:
            # baseline_value se odvozuje ze stejneho pravidla jako sam check
            # (uptime_seconds > 0), ne z pouhe pritomnosti zaznamu - baseline
            # se seconds=0 by jinak tvrdil "Up", coz stav nemeril (stav se
            # nikdy nefabuluje).
            baseline_value = None
            if was is not None:
                baseline_value = "Up" if was.get("uptime_seconds") else "Down"
            findings.append(Finding(
                Outcome.BROKEN, f"{name}: soused ve vypisu neni",
                label=qualified(status_label, name), value="Down",
                baseline_value=baseline_value,
            ))
            continue
        seconds = entry.get("uptime_seconds")
        up = bool(seconds and seconds > 0)
        findings.append(Finding(
            Outcome.OK if up else Outcome.BROKEN,
            f"{name}: session {'bezi' if up else 'nebezi'}",
            label=qualified(status_label, name),
            value=f"Up for {format_uptime(seconds)}" if up else "Down",
        ))
        address = entry.get("neighbor_address")
        was_address = was.get("neighbor_address") if was else None
        changed = ctx.has_baseline and was is not None and address != was_address
        # address muze byt None (klic pritomny, hodnota chybi) - str(None)
        # by do sloupce hodnot poslalo doslovny retezec "None". Chybejici
        # adresa bez zmeny proti baseline je BROKEN (mereni je poctive
        # "adresa chybi", ne pouhe INFO); zmena proti baseline zustava
        # DEGRADED - je to porovnani, ne absence.
        if address is None and not changed:
            outcome = Outcome.BROKEN
        elif changed:
            outcome = Outcome.DEGRADED
        else:
            outcome = Outcome.INFO
        message = (
            f"{name}: adresa souseda chybi"
            if address is None
            else f"{name}: adresa souseda {address}"
        )
        findings.append(Finding(
            outcome, message,
            label=qualified(address_label, name),
            value=str(address) if address is not None else MISSING,
            baseline_value=str(was_address) if was_address is not None else None,
        ))
    return findings


@register
class LdpNeighborStateCheck(Check):
    id = "ldp_neighbor_state"
    title = "Stav LDP souseda"
    label = "LDP neighbor status"
    mode = Mode.BOTH
    requires = ("ldp_neighbor",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        # LDP na tranzitnim Core rozhrani je ocekavany vzdy
        # (rozhodnuti 2026-08-26) - zadny gate na zamer.
        return _neighbor_findings(
            ctx, area="ldp_neighbor",
            status_label="LDP neighbor status",
            address_label="LDP neighbor address",
            names=_scope_transit_interfaces(ctx),
        )


@register
class PimNeighborStateCheck(Check):
    id = "pim_neighbor_state"
    title = "Stav PIM souseda"
    label = "PIM neighbor status"
    mode = Mode.BOTH
    requires = ("pim_neighbor",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        if "pim" not in ctx.scope.selectors.protocols:
            # Bez zameru ticho, ne SKIP - sluzba bez PIM neni mene zdrava
            # (rozhodnuti 2026-08-26).
            return []
        return _neighbor_findings(
            ctx, area="pim_neighbor",
            status_label="PIM neighbor status",
            address_label="PIM neighbor address",
            names=_scope_transit_interfaces(ctx),
        )


@register
class MplsInterfaceStateCheck(Check):
    id = "mpls_interface_state"
    title = "Stav MPLS rozhrani"
    label = "MPLS interface status"
    mode = Mode.BOTH
    requires = ("mpls_interface",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        subject: dict[str, Any] = ctx.subject.get("mpls_interface", {})
        baseline: dict[str, Any] = (ctx.baseline or {}).get("mpls_interface", {})
        findings: list[Finding] = []
        for name in _scope_transit_interfaces(ctx):
            entry = subject.get(name)
            was = baseline.get(name)
            # was.get("state") muze byt pritomny, ale None - str(None) by do
            # sloupce baseline poslal doslovny retezec "None" (stejna stopka
            # jako u isis_adjacency_state._rows).
            was_raw_state = (was or {}).get("state")
            was_state = str(was_raw_state) if was_raw_state is not None else None
            if entry is None:
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{name}: rozhrani neni pod protocols mpls",
                    label=qualified(self.label, name),
                    value=MISSING, baseline_value=was_state,
                ))
                continue
            state = str(entry.get("state", "unknown"))
            up = state == "Up"
            message = f"{name}: MPLS {state}"
            if up and was_state not in (None, "Up"):
                outcome = Outcome.RECOVERED
                message = f"{name}: MPLS Up (v baseline {was_state})"
            else:
                outcome = Outcome.OK if up else Outcome.BROKEN
            findings.append(Finding(
                outcome, message,
                label=qualified(self.label, name),
                value="Up" if up else "Down",
                baseline_value=was_state,
            ))
        return findings


@register
class IsisOverviewCheck(Check):
    id = "isis_overview"
    title = "IS-IS overview routeru"
    label = "IS-IS overload bit"
    mode = Mode.STATE
    requires = ("isis_overview",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"loopback"})
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        overview: dict[str, Any] = ctx.subject.get("isis_overview", {})
        if not overview:
            return [Finding(
                Outcome.DEGRADED, "chybi data z collectoru isis_overview",
                value="bez dat",
            )]
        overload = bool(overview.get("overload_enabled"))
        return [Finding(
            Outcome.DEGRADED if overload else Outcome.OK,
            "overload bit je nastaveny - router se vyhyba tranzitnimu provozu"
            if overload else "overload bit neni nastaveny",
            value="nastaven" if overload else "nenastaven",
        )]


@register
class BfdTransitStateCheck(Check):
    id = "bfd_transit_state"
    title = "Stav BFD na tranzitnim rozhrani"
    label = "BFD"
    mode = Mode.BOTH
    requires = ("bfd",)
    requires_inventory = True
    service_types = CORE
    service_subtypes = frozenset({"transit"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        sessions: dict[str, Any] = ctx.subject.get("bfd", {})
        by_interface: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for peer, data in sessions.items():
            by_interface.setdefault(str(data.get("interface", "")), []).append((peer, data))

        baseline_sessions: dict[str, Any] = (ctx.baseline or {}).get("bfd", {})
        baseline_by_interface: dict[str, dict[str, Any]] = {}
        for peer, data in baseline_sessions.items():
            baseline_by_interface.setdefault(
                str(data.get("interface", "")), {}
            )[peer] = data

        findings: list[Finding] = []
        for name in _scope_transit_interfaces(ctx):
            entries = by_interface.get(name)
            baseline_entries = baseline_by_interface.get(name, {})
            if not entries:
                # BFD je na tranzitu ocekavane vzdy (rozhodnuti
                # 2026-08-26) - zadny zamer se neparsuje. baseline_value
                # se odvozuje ze stavu prvni (podle peer serazene) baseline
                # session pro tenhle interface, ne z pevneho "Up" - stav se
                # nikdy nefabuluje.
                baseline_value = None
                if baseline_entries:
                    first_peer = sorted(baseline_entries)[0]
                    baseline_value = str(
                        baseline_entries[first_peer].get("state", "unknown")
                    )
                findings.append(Finding(
                    Outcome.BROKEN,
                    f"{name}: zadna BFD session",
                    label=qualified(self.label, name), value="Down",
                    baseline_value=baseline_value,
                ))
                continue
            for peer, data in sorted(entries):
                state = str(data.get("state", "unknown"))
                was_raw_state = baseline_entries.get(peer, {}).get("state")
                was_state = str(was_raw_state) if was_raw_state is not None else None
                message = f"{name}: BFD session s {peer} {state}"
                if state == "Up" and was_state is not None and was_state != "Up":
                    outcome = Outcome.RECOVERED
                    message = f"{name}: BFD session s {peer} Up (v baseline {was_state})"
                else:
                    outcome = Outcome.OK if state == "Up" else Outcome.BROKEN
                findings.append(Finding(
                    outcome, message,
                    label=qualified(self.label, name),
                    # Syrovy stav, ne .capitalize() - to by z "AdminDown"
                    # udelalo "Admindown" (nalez finalniho review). Sesterky
                    # check bfd.py:113 vypisuje stav taky syrovy - stejny slovnik.
                    value=state, baseline_value=was_state, subject=data,
                ))
        return findings
