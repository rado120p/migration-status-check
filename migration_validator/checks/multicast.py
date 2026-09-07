"""Multicast checky (spec 2026-09-02): IGMP membership report, multicast
forwarding (IGMP-rizene pro Internet/IPVPN, inet.2-rizene pro Core lo0.0)
a MVPN c-multicast / provider tunnel.

IGMP mnozina definuje ocekavane streamy. Bez ni forwarding i MVPN radky
kaskaduji do SKIP (ne nezavisle hledani v tabulce). Upstream, downstream,
rate a uptime se proti baseline neporovnavaji nikdy - meni se migraci
z definice; porovnava se jen mnozina (S,G) a sender PE tunelu.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.collectors.multicast import route_key
from migration_validator.models.result import Finding, Outcome, Severity
from migration_validator.models.scope import Scope

MULTICAST_TYPES = frozenset({"Internet", "IPVPN"})
MULTICAST_SUBTYPES = frozenset({"multicast", "mvpn"})
MVPN_SUBTYPE = "mvpn"

# Internet/multicast prijima stream primo z fyzickeho/agregovaneho transit
# rozhrani; IPVPN/mvpn jde bud pres MVPN core tunel (lsi.* nebo vt-*),
# nebo - kdyz je zdroj lokalne v tomtez VRF/PE - primo z fyzickeho,
# agregovaneho nebo irb rozhrani (rozhodnuti 2026-09-07).
INTERNET_UPSTREAM_PREFIXES = ("ge-", "xe-", "et-", "ae")
MVPN_UPSTREAM_PREFIXES = ("lsi.", "vt-", "ge-", "xe-", "et-", "ae", "irb")

# 224.0.0.0/24 je link-local (all-routers, PIM, IGMPv3 ...) - hlasi ho
# protokoly samotne, ne receiver, do ocekavane mnoziny streamu nepatri.
LINK_LOCAL_GROUPS = ipaddress.ip_network("224.0.0.0/24")

NO_REPORT = "Receiver neposila zadny IGMP membership report"
NO_REPORT_PIM_INFO = "bez IGMP reportu, o streamy se hlasi PIM join"
NO_REPORT_SKIP = "bez IGMP reportu"
NO_JOIN = "Zadny PIM join"
NO_JOIN_IGMP_INFO = "bez PIM join, o streamy se hlasi IGMP"
SENDER_NO_RECEIVER = "sender site bez vzdaleneho receiveru, neni co overit"
# junos-evo casto vraci <multicast-statistics-timed-out/> misto
# forwarding-rate-packets i na zive Forwarding route (overeno 2026-09-02).
# Absence hodnoty neni nula - musi se odlisit SKIP od BROKEN.
RATE_UNAVAILABLE = "statistiky nedostupne"


# --- helpery ---------------------------------------------------------------

def sg_label(source: str | None, group: str) -> str:
    return f"({source or '*'}, {group})"


def pairs_text(pairs: list[tuple[str | None, str]]) -> str:
    return ", ".join(sg_label(s, g) for s, g in pairs)


def role_pairs_text(pairs: list[Pair]) -> str:
    return ", ".join(f"{sg_label(s, g)} [{roles_label(r)}]" for s, g, r in pairs)


def _sg_set(pairs: list[Pair]) -> set[tuple[str | None, str]]:
    return {(s, g) for s, g, _ in pairs}


def _link_local(group: str) -> bool:
    try:
        return ipaddress.ip_address(group) in LINK_LOCAL_GROUPS
    except ValueError:
        return False


def igmp_pairs(facts: dict[str, Any] | None, scope: Scope) -> list[tuple[str | None, str]]:
    """(source, group) pro rozhrani scopu - serazene, bez duplicit.
    Baseline se cte s baseline scopem: jmena rozhrani se migraci meni."""
    groups = (facts or {}).get("igmp_group") or {}
    pairs = {
        (entry.get("source"), str(entry["group"]))
        for name in scope.selectors.interfaces
        for entry in groups.get(name, [])
        if entry.get("group") and not _link_local(str(entry["group"]))
    }
    return sorted(pairs, key=lambda p: (p[0] or "", p[1]))


RECEIVER = "receiver"
SENDER = "sender"

Pair = tuple[str | None, str, frozenset[str]]


def _service_interface(scope: Scope) -> str | None:
    return scope.selectors.interfaces[0] if scope.selectors.interfaces else None


def pim_pairs(facts: dict[str, Any] | None, scope: Scope) -> list[Pair]:
    """(source, group, roles) z PIM join tabulky scopu (spec 2026-09-07).
    Role z vypisu, ne z konfigurace: servisni rozhrani mezi downstream =
    receiver, servisni rozhrani == upstream = sender. Join, ktery se
    rozhrani nedotyka, patri jine sluzbe v teze instanci."""
    iface = _service_interface(scope)
    if iface is None:
        return []
    found: dict[tuple[str | None, str], set[str]] = {}
    for table in ((facts or {}).get("pim_join") or {}).values():
        for join in table.values():
            group = str(join.get("group") or "")
            if not group or _link_local(group):
                continue
            roles = set()
            if iface in (join.get("downstream_interfaces") or []):
                roles.add(RECEIVER)
            if join.get("upstream_interface") == iface:
                roles.add(SENDER)
            if roles:
                found.setdefault((join.get("source"), group), set()).update(roles)
    return sorted(
        ((s, g, frozenset(r)) for (s, g), r in found.items()),
        key=lambda p: (p[0] or "", p[1]),
    )


def expected_pairs(facts: dict[str, Any] | None, scope: Scope) -> list[Pair]:
    """Sjednoceni IGMP paru (vzdy receiver) a PIM paru; stejne (S,G) dostane
    unii roli. Tohle je mnozina ocekavanych streamu pro forwarding
    a c-multicast checky (rozhodnuti 2026-09-07)."""
    merged: dict[tuple[str | None, str], set[str]] = {}
    for source, group in igmp_pairs(facts, scope):
        merged.setdefault((source, group), set()).add(RECEIVER)
    for source, group, roles in pim_pairs(facts, scope):
        merged.setdefault((source, group), set()).update(roles)
    return sorted(
        ((s, g, frozenset(r)) for (s, g), r in merged.items()),
        key=lambda p: (p[0] or "", p[1]),
    )


def roles_label(roles: frozenset[str]) -> str:
    return "/".join(sorted(roles))


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
    order = 10
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
            if pim_pairs(ctx.subject, ctx.scope):
                # Rozhrani s obema zamery: streamy nese PIM join, IGMP mlci
                # (rozhodnuti 2026-09-07). pim_join area se cte volitelne.
                return [Finding(
                    Outcome.INFO, "receiver neposila IGMP report, o streamy se hlasi PIM join",
                    label=self.label, value=NO_REPORT_PIM_INFO, baseline_value=was_value,
                )]
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


# --- pim_join ---------------------------------------------------------------

@register
class PimJoinCheck(Check):
    id = "pim_join"
    order = 11
    title = "PIM join na servisnim rozhrani"
    label = "PIM join"
    mode = Mode.BOTH
    requires = ("pim_join",)
    requires_inventory = True
    service_types = MULTICAST_TYPES
    service_subtypes = MULTICAST_SUBTYPES
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        if "pim" not in ctx.scope.selectors.protocols:
            # Bez zameru ticho, ne SKIP - stejne jako pim_neighbor_state.
            return []
        now = pim_pairs(ctx.subject, ctx.scope)
        was = pim_pairs(ctx.baseline, ctx.baseline_scope or ctx.scope) if ctx.has_baseline else []
        was_value = role_pairs_text(was) if was else None
        if not now:
            if igmp_pairs(ctx.subject, ctx.scope):
                return [Finding(
                    Outcome.INFO,
                    "zadny PIM join na servisnim rozhrani, o streamy se hlasi IGMP",
                    label=self.label, value=NO_JOIN_IGMP_INFO, baseline_value=was_value,
                )]
            if list(ctx.scope.selectors.mvpn_site) == [SENDER]:
                # Sender-only site bez vzdaleneho receiveru nema zadny join
                # state - neni co overit, ale neni to rozbity receiver
                # (rozhodnuti 2026-09-07).
                return [Finding(
                    Outcome.DEGRADED, "sender site bez vzdaleneho receiveru",
                    label=self.label, value=SENDER_NO_RECEIVER, baseline_value=was_value,
                )]
            return [Finding(
                Outcome.BROKEN, "zadny PIM join na servisnim rozhrani",
                label=self.label, value=NO_JOIN, baseline_value=was_value,
            )]
        # Role se neporovnavaji - migraci se nemeni, pri rozdilu by slo
        # o jiny stream. Porovnava se jen mnozina (S,G).
        if was and _sg_set(was) != _sg_set(now):
            return [Finding(
                Outcome.DEGRADED, f"PIM join se zmenil proti baseline: {role_pairs_text(now)}",
                label=self.label, value=role_pairs_text(now), baseline_value=was_value,
            )]
        return [Finding(
            Outcome.OK, f"PIM join: {role_pairs_text(now)}",
            label=self.label, value=role_pairs_text(now), baseline_value=was_value,
        )]


# --- multicast_forwarding_status --------------------------------------------

def _upstream_problem(subtype: str | None, upstream: str | None) -> str | None:
    """None = upstream v poradku. Jinak text pripojeny za 'upstream <hodnota>'
    - odlisuje chybejici upstream od upstreamu ze spatne role."""
    if not upstream:
        return " - S,G je v tabulce ale nema upstream interface"
    prefixes = MVPN_UPSTREAM_PREFIXES if subtype == MVPN_SUBTYPE else INTERNET_UPSTREAM_PREFIXES
    if upstream.startswith(prefixes):
        return None
    return f" neni z ocekavane role (ocekavano {'/'.join(prefixes)})"


def _summary(label: str, total: int, failed: int) -> Finding:
    if failed:
        return Finding(
            Outcome.BROKEN, f"{failed} z {total} S,G nefunguje",
            label=label, value=f"{failed}/{total} S,G nefunguje",
        )
    return Finding(Outcome.OK, f"{total} S,G funguje", label=label, value=f"{total} S,G")


@register
class MulticastForwardingStatusCheck(Check):
    id = "multicast_forwarding_status"
    order = 12
    title = "Multicast forwarding na servisnim rozhrani"
    label = "Multicast forwarding status"
    mode = Mode.STATE
    requires = ("igmp_group", "multicast_route")
    requires_inventory = True
    service_types = MULTICAST_TYPES
    service_subtypes = MULTICAST_SUBTYPES
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        pairs = igmp_pairs(ctx.subject, ctx.scope)
        if not pairs:
            # Kaskada (rozhodnuti 2026-09-02): bez IGMP mnoziny neni co hledat.
            return [Finding(
                Outcome.SKIP, "bez IGMP reportu neni co hledat v multicast tabulce",
                value=NO_REPORT_SKIP,
            )]
        table = multicast_table(ctx.subject)
        iface = ctx.scope.selectors.interfaces[0]
        rows: list[Finding] = []
        failed = 0
        for source, group in pairs:
            matches = routes_for(table, source, group)
            if not matches:
                sg = sg_label(source, group)
                rows.append(Finding(
                    Outcome.BROKEN, f"{sg}: S,G neni v multicast tabulce",
                    label="Stream", group=sg, value="S,G neni v multicast tabulce",
                ))
                failed += 1
                continue
            pair_failed = False
            for key, route in matches:
                # Skupina nese realny zdroj z tabulky - u ASM zaznamu (*, G)
                # je to jediny zpusob, jak streamy rozlisit.
                sg = sg_label(key.split(",", 1)[0], group)
                stream = self._stream(sg, iface, ctx.scope.service_subtype, route)
                if any(f.outcome is Outcome.BROKEN for f in stream):
                    pair_failed = True
                rows.extend(stream)
            if pair_failed:
                failed += 1
        return [_summary(self.label, len(pairs), failed), *rows]

    @staticmethod
    def _stream(sg: str, iface: str, subtype: str | None, route: dict[str, Any]) -> list[Finding]:
        downstream = route.get("downstream_interfaces") or []
        on_iface = iface in downstream
        upstream = route.get("upstream_interface")
        problem = _upstream_problem(subtype, upstream)
        return [
            Finding(
                Outcome.OK if on_iface else Outcome.BROKEN,
                f"{sg}: stream se na {iface} " + ("posila" if on_iface else "neposila"),
                label="Stream", group=sg,
                value=(
                    f"Stream se na {iface} posila" if on_iface
                    else f"S,G je v tabulce ale stream se na {iface} neposila"
                ),
            ),
            Finding(
                Outcome.OK if problem is None else Outcome.BROKEN,
                f"{sg}: upstream {upstream or '-'}{problem or ''}",
                label="Upstream interface", group=sg, value=upstream or "-",
            ),
            *stream_rows(sg, route, rate_label="Forwarding-rate"),
        ]


# --- core_multicast_forwarding -----------------------------------------------

def assign_sources(
    table: dict[str, dict[str, Any]], prefixes: list[str]
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Routy k inet.2 prefixum podle zdroje - nejdelsi pokryvajici prefix
    vyhrava, kazda routa se pocita jen jednou. Ne-IPv4 prefixy a zdroje
    se preskoci (inet.2 je IPv4 tabulka)."""
    networks = []
    for prefix in prefixes:
        try:
            networks.append((ipaddress.ip_network(prefix, strict=False), prefix))
        except ValueError:
            continue
    networks.sort(key=lambda item: item[0].prefixlen, reverse=True)
    assigned: dict[str, list[tuple[str, dict[str, Any]]]] = {prefix: [] for prefix in prefixes}
    for key, route in sorted(table.items()):
        try:
            source = ipaddress.ip_address(key.split(",", 1)[0])
        except ValueError:
            continue
        for network, prefix in networks:
            if source.version == network.version and source in network:
                assigned[prefix].append((key, route))
                break
    return assigned


def _inet2_prefixes(scope: Scope) -> list[str]:
    return sorted(
        str(route["prefix"])
        for route in scope.selectors.static_routes
        if str(route.get("rib")) == "inet.2"
        and str(route.get("route_type", "static")) == "static"
    )


def _labels_of(streams: list[tuple[str, dict[str, Any]]]) -> str:
    return ", ".join(sg_label(*key.split(",", 1)) for key, _ in streams)


@register
class CoreMulticastForwardingCheck(Check):
    id = "core_multicast_forwarding"
    order = 13
    title = "Multicast forwarding pro inet.2 statiky"
    label = "Multicast forwarding status"
    mode = Mode.BOTH
    requires = ("multicast_route", "routes")
    requires_inventory = True
    service_types = frozenset({"Core"})
    service_subtypes = frozenset({"loopback"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        prefixes = _inet2_prefixes(ctx.scope)
        if not prefixes:
            # Bez inet.2 zameru ticho, ne SKIP (rozhodnuti 2026-09-02).
            return []
        # Baseline i subject tabulka se rozdeluji podle prefixu ze subject
        # scope zamerne - prefixy jsou stabilni identifikator napric migraci,
        # na rozdil od nazvu rozhrani (proto IGMP checky pouzivaji baseline_scope).
        assigned = assign_sources(multicast_table(ctx.subject), prefixes)
        baseline_assigned = (
            assign_sources(multicast_table(ctx.baseline), prefixes)
            if ctx.has_baseline else None
        )
        inet2 = (ctx.subject.get("routes") or {}).get("inet.2") or {}
        findings: list[Finding] = []
        for prefix in prefixes:
            streams = assigned.get(prefix, [])
            was = baseline_assigned.get(prefix, []) if baseline_assigned else []
            was_value = _labels_of(was) if was else None
            if not streams:
                findings.append(Finding(
                    Outcome.BROKEN, f"neexistuje S,G se zdrojem v {prefix}",
                    label=self.label, value=f"Neexistuje S,G pro {prefix}", baseline_value=was_value,
                ))
                continue
            changed = bool(was) and {k for k, _ in was} != {k for k, _ in streams}
            findings.append(Finding(
                Outcome.DEGRADED if changed else Outcome.OK,
                f"existuje S,G se zdrojem v {prefix}: {_labels_of(streams)}"
                + (" (mnozina se lisi od baseline)" if changed else ""),
                label=self.label, value=f"Existuje S,G pro {prefix}", baseline_value=was_value,
            ))
            measured = inet2.get(prefix)
            vias = list(measured.get("via") or []) if measured else None
            for key, route in streams:
                sg = sg_label(*key.split(",", 1))
                findings.extend(self._stream(sg, route, vias))
        missing = [p for p in prefixes if not assigned.get(p)]
        findings.insert(0, Finding(
            Outcome.BROKEN if missing else Outcome.OK,
            f"{len(missing)} z {len(prefixes)} inet.2 prefixu bez streamu" if missing
            else f"{len(prefixes)} inet.2 prefixu se streamem",
            label=self.label,
            value=f"{len(missing)}/{len(prefixes)} bez streamu" if missing
            else f"{len(prefixes)} inet.2 prefixu",
        ))
        return findings

    @staticmethod
    def _stream(sg: str, route: dict[str, Any], vias: list[str] | None) -> list[Finding]:
        upstream = route.get("upstream_interface")
        if vias is None:
            # FAIL za chybejici inet.2 routu nese static_route_status -
            # tady by byl druhy FAIL za tutez pricinu.
            upstream_row = Finding(
                Outcome.SKIP, f"{sg}: inet.2 routa neni v tabulce, upstream nelze overit",
                label="Upstream interface", group=sg, value="routa neni v tabulce",
            )
        else:
            ok = bool(upstream) and upstream in vias
            upstream_row = Finding(
                Outcome.OK if ok else Outcome.BROKEN,
                f"{sg}: upstream {upstream or '-'}"
                + ("" if ok else f" neni mezi via inet.2 routy ({', '.join(vias) or '-'})"),
                label="Upstream interface", group=sg, value=upstream or "-",
            )
        downstream = route.get("downstream_interfaces") or []
        downstream_row = Finding(
            Outcome.OK if downstream else Outcome.BROKEN,
            f"{sg}: downstream {', '.join(downstream) or 'zadne'}",
            label="Downstream interfaces", group=sg,
            value=", ".join(downstream) if downstream else "Zadne downstream interfacy",
        )
        return [upstream_row, downstream_row, *stream_rows(sg, route, rate_label="Forwarding rate packets")]


# --- mvpn_cmulticast_status --------------------------------------------------

def _cmulticast_entry(
    entries: list[dict[str, Any]], source: str | None, group: str
) -> dict[str, Any] | None:
    """Zaznam, jehoz S/32:G/32 pokryva IGMP (S,G); pro (*, G) staci group."""
    try:
        group_ip = ipaddress.ip_address(group)
        source_ip = ipaddress.ip_address(source) if source else None
    except ValueError:
        return None
    for entry in entries:
        try:
            if group_ip not in ipaddress.ip_network(entry["group_prefix"], strict=False):
                continue
            if source_ip is not None and source_ip not in ipaddress.ip_network(
                entry["source_prefix"], strict=False
            ):
                continue
        except (KeyError, ValueError):
            continue
        return entry
    return None


@register
class MvpnCmulticastStatusCheck(Check):
    id = "mvpn_cmulticast_status"
    order = 14
    title = "MVPN c-multicast a provider tunnel"
    label = "C-Multicast status"
    mode = Mode.BOTH
    requires = ("igmp_group", "mvpn_instance")
    requires_inventory = True
    service_types = frozenset({"IPVPN"})
    service_subtypes = frozenset({MVPN_SUBTYPE})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        pairs = igmp_pairs(ctx.subject, ctx.scope)
        if not pairs:
            # Kaskada (rozhodnuti 2026-09-02): bez IGMP mnoziny neni co hledat v MVPN.
            return [Finding(
                Outcome.SKIP, "bez IGMP reportu neni co hledat v MVPN",
                label=self.label, value=NO_REPORT_SKIP,
            )]
        instance = (
            ctx.scope.selectors.routing_instances[0]
            if ctx.scope.selectors.routing_instances else None
        )
        data = (ctx.subject.get("mvpn_instance") or {}).get(instance)
        if data is None:
            return [Finding(
                Outcome.BROKEN, f"instance {instance} neni v mvpn vypisu",
                label=self.label, value="instance neni v mvpn vypisu",
            )]
        entries = data.get("c_multicast") or []
        baseline_entries = (
            ((ctx.baseline or {}).get("mvpn_instance") or {}).get(instance) or {}
        ).get("c_multicast") or []
        findings: list[Finding] = []
        for source, group in pairs:
            sg = sg_label(source, group)
            entry = _cmulticast_entry(entries, source, group)
            if entry is None:
                findings.append(Finding(
                    Outcome.BROKEN, f"{sg}: chybi c-multicast zaznam",
                    label=self.label, group=sg, value="chybi c-multicast zaznam",
                ))
                continue
            findings.append(Finding(
                Outcome.OK, f"{sg}: c-multicast {entry['source_prefix']}:{entry['group_prefix']}",
                label=self.label, group=sg,
                value=f"{entry['source_prefix']}:{entry['group_prefix']}",
            ))
            was = _cmulticast_entry(baseline_entries, source, group) if ctx.has_baseline else None
            findings.append(self._tunnel_row(sg, entry, was))
        return findings

    @staticmethod
    def _tunnel_row(sg: str, entry: dict[str, Any], was: dict[str, Any] | None) -> Finding:
        tunnel = entry.get("provider_tunnel_id") or "-"
        pe = entry.get("sender_pe")
        was_tunnel = was.get("provider_tunnel_id") if was else None
        was_pe = was.get("sender_pe") if was else None
        if not pe:
            outcome, note = Outcome.BROKEN, " - bez provider tunelu"
        elif was_pe and was_pe != pe:
            # Jen sender PE, ne cely retezec: tunnel id se pri re-signalizaci
            # LSP zmeni bez zmeny sluzby (rozhodnuti 2026-09-02).
            outcome, note = Outcome.DEGRADED, f" - sender PE se zmenil {was_pe} -> {pe}"
        else:
            outcome, note = Outcome.OK, ""
        return Finding(
            outcome, f"{sg}: provider tunnel {tunnel}{note}",
            label="Provider tunnel", group=sg, value=tunnel, baseline_value=was_tunnel,
        )
