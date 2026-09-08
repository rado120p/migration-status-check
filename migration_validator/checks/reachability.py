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

from migration_validator.addressing import is_link_local, link_local_is_configured
from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity
from migration_validator.models.scope import MULTICAST_SUBTYPES
from migration_validator.probes.ping import (
    IPV4_FALLBACK_MIN_PREFIX,
    IPV6_FALLBACK_MIN_PREFIX,
)

CUSTOMER_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})

ZERO_MAC = "00:00:00:00:00:00"
UNRESOLVED_ND_STATES = frozenset({"incomplete", "unreachable"})


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


def _entry_value(entry: dict[str, Any]) -> str:
    """MAC -> IP a k tomu L2 rozhrani, pokud zaznam prislo pres IRB.

    Bez toho radek u irb.14 vypada shodne se zaznamem primo na fyzickem
    rozhrani - a prave rozliseni "pres ktery L2 port" je smysl faze 3.
    """
    value = f"{entry.get('mac') or '?'} -> {entry['ip']}"
    if entry.get("learned_via"):
        value = f"{value}  [via {entry['learned_via']}]"
    return value


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
    excluded_subtypes = MULTICAST_SUBTYPES
    default_severity = Severity.CRITICAL

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

        findings: list[Finding] = []
        for entry in entries:
            ip = entry["ip"]
            if entry.get("mac") == ZERO_MAC:
                # Incomplete ARP zaznam neni "zadny zaznam" - je to konkretni,
                # ohlaseny stav, ktery report musi ukazat jako FAIL, ne mlcet.
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"ARP zaznam {ip} neni resolved (incomplete)",
                        label="ARP",
                        family=4,
                        value=f"incomplete -> {ip}",
                        subject={
                            "ip": ip,
                            "mac": entry.get("mac"),
                            "learned_via": entry.get("learned_via"),
                        },
                        details={"address": owning_prefix(str(ip), prefixes)},
                    )
                )
                continue
            findings.append(
                Finding(
                    Outcome.OK,
                    f"ARP zaznam {ip}",
                    label="ARP",
                    family=4,
                    value=_entry_value(entry),
                    subject={
                        "ip": ip,
                        "mac": entry.get("mac"),
                        "learned_via": entry.get("learned_via"),
                    },
                    details={"address": owning_prefix(str(ip), prefixes)},
                )
            )
        return findings


@register
class NdPresentCheck(Check):
    id = "nd_present"
    title = "Existence ND zaznamu"
    label = "ND"
    mode = Mode.STATE
    requires = ("nd",)
    requires_inventory = True
    service_types = CUSTOMER_SERVICE_TYPES
    excluded_subtypes = MULTICAST_SUBTYPES
    default_severity = Severity.CRITICAL

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

        findings: list[Finding] = []
        for entry in entries:
            ip = entry["ip"]
            state = (entry.get("state") or "").lower()
            if state in UNRESOLVED_ND_STATES:
                # Stejne jako u ARP: incomplete/unreachable je konkretni,
                # ohlaseny stav, ktery report musi ukazat jako FAIL, ne mlcet.
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"ND zaznam {ip} neni resolved ({state})",
                        label="ND",
                        family=6,
                        value=f"{state} -> {ip}",
                        subject={
                            "ip": ip,
                            "mac": entry.get("mac"),
                            "state": entry.get("state"),
                            "learned_via": entry.get("learned_via"),
                        },
                        details={"address": owning_prefix(str(ip), prefixes)},
                    )
                )
                continue
            findings.append(
                Finding(
                    Outcome.OK,
                    f"ND zaznam {ip}",
                    label="ND",
                    family=6,
                    value=_entry_value(entry),
                    subject={
                        "ip": ip,
                        "mac": entry.get("mac"),
                        "state": entry.get("state"),
                        "learned_via": entry.get("learned_via"),
                    },
                    details={"address": owning_prefix(str(ip), prefixes)},
                )
            )
        return findings


@register
class PingReachabilityCheck(Check):
    id = "ping_reachability"
    title = "Dosazitelnost CPE pingem"
    label = "Ping"
    mode = Mode.STATE
    requires = ("ping",)
    requires_inventory = True
    service_types = CUSTOMER_SERVICE_TYPES
    excluded_subtypes = MULTICAST_SUBTYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        probes: list[dict[str, Any]] = ctx.subject.get("ping", [])
        skipped = ctx.subject.get("ping_skipped")
        if not probes and skipped:
            # Odfiltrovano profilem pri capture - vedome nesbirano,
            # ne chybejici cil. Stav se nefabuluje: rekneme proc - a pokud
            # capture zaznamenala jmeno profilu, rekneme rovnou ktereho.
            profile = next(
                (s.get("profile") for s in skipped if s.get("profile")), None
            )
            if profile:
                message = f"ping neproveden - mimo profil ({profile})"
                value = f"mimo profil ({profile})"
            else:
                message = "ping neproveden - mimo profil"
                value = "mimo profil"
            return [
                Finding(
                    Outcome.SKIP,
                    message,
                    label="Ping",
                    value=value,
                )
            ]

        # Subnet vetsi nez P2P prah bez jedineho cile: resolver tam fallback
        # vedome nepousti (hadani cile z /24 vyrabi cerveny radek o nicem) -
        # ale mlceni by se cetlo jako "zkontrolovano". Report rekne proc.
        oversized = _oversized_subnets_without_targets(ctx.scope, probes)
        if not probes and not oversized:
            return [
                Finding(
                    Outcome.SKIP,
                    "pro tento scope nejsou ve snapshotu zadne cile pingu",
                    label="Ping",
                    value="bez cile",
                )
            ]

        findings: list[Finding] = [
            Finding(
                Outcome.SKIP,
                f"{network}: zadny cil - subnet vetsi nez /{threshold}, "
                "fallback by cil jen hadal",
                label="Ping",
                family=family,
                value=f"{network}  bez cile (subnet > /{threshold})",
            )
            for network, family, threshold in oversized
        ]
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


def _oversized_subnets_without_targets(
    scope: Any, probes: list[dict[str, Any]]
) -> list[tuple[str, int, int]]:
    """Subnety scope nad P2P prahem, do kterych nepadl zadny cil pingu.

    Vraci (sit, rodina, prah) - prah jde do textu nalezu, aby IPv4 (/30)
    a IPv6 (/126) mluvily kazdy svym cislem. Poradi drzi poradi selektoru.
    """
    result: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for family, prefixes, threshold in (
        (4, scope.selectors.local_ipv4, IPV4_FALLBACK_MIN_PREFIX),
        (6, scope.selectors.local_ipv6, IPV6_FALLBACK_MIN_PREFIX),
    ):
        targets = []
        for probe in probes:
            if probe.get("family") != family:
                continue
            try:
                targets.append(ipaddress.ip_address(str(probe.get("target"))))
            except ValueError:
                continue
        for prefix in prefixes:
            try:
                network = ipaddress.ip_interface(prefix).network
            except ValueError:
                continue
            if network.prefixlen >= threshold:
                continue
            if any(target in network for target in targets):
                continue
            key = str(network)
            if key in seen:
                continue
            seen.add(key)
            result.append((key, family, threshold))
    return result


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

        if sent == 0:
            # Nic neodeslano neni totez jako "odeslano a bez odpovedi" -
            # ping proste nebehl (napr. resolver cil vyhodil az pozdeji).
            findings.append(
                Finding(
                    Outcome.SKIP,
                    f"{target}: ping neodeslan",
                    label="Ping",
                    family=family,
                    value=f"{target} neodeslan",
                    subject={"target": target, "sent": 0, "received": received},
                    details=details,
                )
            )
        elif received:
            findings.append(
                Finding(
                    Outcome.OK,
                    f"{target}: odpovedelo {received} z {sent}",
                    label="Ping",
                    family=family,
                    value=f"{value}  {target}",
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
