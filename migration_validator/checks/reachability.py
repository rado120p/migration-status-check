"""ARP, ND a ping checky.

Vsechny tri jsou vedome best-effort - CPE muze byt vypnute nebo blokovat
ICMP - proto default severity advisory. Cile pingu se resolvuji uz pri
capture (ARP/ND -> ping), tady se ctou hotove vysledky ze snapshotu.

Kazdy check vraci jeden Finding na zaznam (ARP/ND) resp. na cil (ping) -
report tiskne radky jednotlive, ne jako souhrnnou vetu.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity
from migration_validator.models.scope import Scope

CUSTOMER_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})


def link_local_is_configured(scope: Scope) -> bool:
    """Ma sluzba link-local adresu primo pod rozhranim?

    Link-local sousede se objevi u kazdeho IPv6 rozhrani a o zakaznicke
    sluzbe nerikaji nic. Existuji ale nasazeni, kde sluzba pouziva link-local
    - staci, aby mela mezi nakonfigurovanymi adresami jednu link-local, klidne
    i vedle bezne routovatelne - pak je link-local soused legitimni cil.
    Rozhoduje konfigurace (pritomnost, ne vylucnost), ne heuristika.
    """
    for address in scope.selectors.local_ipv6:
        try:
            if ipaddress.ip_interface(address).ip.is_link_local:
                return True
        except ValueError:
            continue
    return False


def is_link_local(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_link_local
    except ValueError:
        return False


def owning_prefix(address: str, prefixes: list[str]) -> str | None:
    """Ktery nakonfigurovany rozsah tuhle adresu obsahuje.

    Report tim popisuje radky u sluzby, ktera ma vic rozsahu jedne rodiny -
    bez toho by u dvou ARP zaznamu nebylo poznat, ke kteremu rozsahu patri.
    U jedineho rozsahu se to v reportu zahodi, protoze uz je v hlavicce.
    """
    try:
        target = ipaddress.ip_address(address)
    except ValueError:
        return None

    for prefix in prefixes:
        try:
            network = ipaddress.ip_interface(prefix).network
        except ValueError:
            continue
        if target.version == network.version and target in network:
            return prefix
    return None


def _family_not_configured() -> list[Finding]:
    """Rodina, kterou sluzba nema nakonfigurovanou, se nehlasi nijak.

    Rozhodnuti R-1 (varianta c). Drive tu byl SKIP oznackovany svou
    rodinou - a prave ta znacka si v bloku vynutila sekci rodiny, kterou
    ma podle spec renderer vynechat. Sekci potlacit a radek nechat neslo:
    sekce vznika prave z toho, ze do ni nejaky radek patri.

    Cena: chybejici check je v reportu k nerozeznani od checku, ktery
    prosel, a ve strojovem vystupu po nem taky nezustane stopa. Vedome
    prijato - alternativou byla prazdna sekce u vetsiny sluzeb.
    """
    return []


@register
class ArpPresentCheck(Check):
    id = "arp_present"
    title = "Existence ARP zaznamu"
    label = "ARP"
    mode = Mode.STATE
    requires = ("arp",)
    requires_inventory = True
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        prefixes = ctx.scope.selectors.local_ipv4
        if not prefixes:
            return _family_not_configured()

        entries: list[dict[str, Any]] = ctx.subject.get("arp", [])
        entries = [entry for entry in entries if entry.get("ip")]

        if not entries:
            return [
                Finding(
                    Outcome.BROKEN,
                    "na rozhranich sluzby neni zadny ARP zaznam",
                    label="ARP",
                    family=4,
                    value="zadny zaznam",
                    subject={"count": 0, "addresses": []},
                )
            ]

        return [
            Finding(
                Outcome.OK,
                f"ARP zaznam {entry['ip']}",
                label="ARP",
                family=4,
                value=f"{entry.get('mac') or '?'} -> {entry['ip']}",
                subject={"ip": entry["ip"], "mac": entry.get("mac")},
                details={"address": owning_prefix(str(entry["ip"]), prefixes)},
            )
            for entry in entries
        ]


@register
class NdPresentCheck(Check):
    id = "nd_present"
    title = "Existence ND zaznamu"
    label = "ND"
    mode = Mode.STATE
    requires = ("nd",)
    requires_inventory = True
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        prefixes = ctx.scope.selectors.local_ipv6
        if not prefixes:
            return _family_not_configured()

        keep_link_local = link_local_is_configured(ctx.scope)
        entries = [
            entry
            for entry in ctx.subject.get("nd", [])
            if entry.get("ip")
            and (keep_link_local or not is_link_local(str(entry["ip"])))
        ]

        if not entries:
            return [
                Finding(
                    Outcome.BROKEN,
                    "na rozhranich sluzby neni zadny pouzitelny ND zaznam",
                    label="ND",
                    family=6,
                    value="zadny zaznam",
                    subject={"count": 0, "addresses": []},
                )
            ]

        return [
            Finding(
                Outcome.OK,
                f"ND zaznam {entry['ip']}",
                label="ND",
                family=6,
                value=f"{entry.get('mac') or '?'} -> {entry['ip']}",
                subject={
                    "ip": entry["ip"],
                    "mac": entry.get("mac"),
                    "state": entry.get("state"),
                },
                details={"address": owning_prefix(str(entry["ip"]), prefixes)},
            )
            for entry in entries
        ]


@register
class PingReachabilityCheck(Check):
    id = "ping_reachability"
    title = "Dosazitelnost CPE pingem"
    label = "Ping"
    mode = Mode.STATE
    requires = ("ping",)
    requires_inventory = True
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        probes: list[dict[str, Any]] = ctx.subject.get("ping", [])
        if not probes:
            return [
                Finding(
                    Outcome.SKIP,
                    "pro tento scope nejsou ve snapshotu zadne cile pingu",
                    label="Ping",
                    value="bez cile",
                )
            ]

        findings: list[Finding] = []
        for family in (4, 6):
            batch = [probe for probe in probes if probe.get("family") == family]
            if not batch:
                continue
            prefixes = (
                ctx.scope.selectors.local_ipv6
                if family == 6
                else ctx.scope.selectors.local_ipv4
            )
            findings.extend(_ping_findings(batch, family, prefixes))

        # Probe bez rodiny by jinak proste zmizel z vysledku - check by
        # tise nevratil nic misto toho, aby rekl, ze neco nevyhodnotil.
        unclassified = [probe for probe in probes if probe.get("family") not in (4, 6)]
        if unclassified:
            targets = ", ".join(str(probe.get("target")) for probe in unclassified)
            findings.append(
                Finding(
                    Outcome.SKIP,
                    f"probe bez rodiny nelze vyhodnotit: {targets}",
                    label="Ping",
                    value="bez rodiny",
                )
            )

        return findings


def _ping_findings(
    probes: list[dict[str, Any]], family: int, prefixes: list[str]
) -> list[Finding]:
    """Jeden radek na cil - report je vypisuje jednotlive, ne jako souhrn."""
    findings = []
    for probe in probes:
        target = str(probe.get("target"))
        sent = int(probe.get("sent", 0))
        received = int(probe.get("received", 0))
        rtt = probe.get("rtt_avg_ms")
        value = f"{received}/{sent}"
        if rtt is not None:
            value = f"{value}  {rtt} ms"

        details = {
            "resolved_from": probe.get("resolved_from"),
            "address": owning_prefix(target, prefixes),
        }

        if received:
            findings.append(
                Finding(
                    Outcome.OK,
                    f"{target}: odpovedelo {received} z {sent}",
                    label="Ping",
                    family=family,
                    value=value,
                    subject={"target": target, "sent": sent, "received": received},
                    details=details,
                )
            )
        else:
            findings.append(
                Finding(
                    Outcome.BROKEN,
                    f"{target}: neodpovedel ({sent} paketu)",
                    label="Ping",
                    family=family,
                    value=f"{value}  {target} neodpovedel",
                    subject={"target": target, "sent": sent, "received": 0},
                    details=details,
                )
            )
    return findings
