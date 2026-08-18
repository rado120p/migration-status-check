"""Aktivni ping probe.

Jediny aktivni test - proto vlastni kategorie mimo collectory. Bezi az po
bulk sberu, protoze cile se odvozuji z ARP.

Ping bezi jen v service rezimu: bez inventory neni znam cil, takze snapshot
ma probes.ping prazdne.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from lxml import etree

from migration_validator.addressing import is_link_local, link_local_is_configured
from migration_validator.models.scope import Scope

PING_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})
DEFAULT_COUNT = 5


@dataclass(frozen=True)
class PingTarget:
    scope_id: str
    target: str
    routing_instance: str | None
    resolved_from: str  # bgp | arp | nd | subnet-fallback | baseline-arp | baseline-nd
    family: int
    interface: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "target": self.target,
            "routing_instance": self.routing_instance,
            "resolved_from": self.resolved_from,
            "family": self.family,
            "interface": self.interface,
        }


# Kratsi prefix nez tohle uz neni point-to-point linka. V IPv6 nema smysl
# strilet nahodnou adresu ze /64 - je to zaruceny neuspech, ktery se v
# reportu cte jako nedostupne CPE.
IPV6_FALLBACK_MIN_PREFIX = 126


def subnet_fallback(
    addresses: list[str], family: int, owned: list[str] | None = None
) -> str | None:
    """Prvni pouzitelna adresa ze subnetu, ktera neni nase vlastni.

    `owned` jsou dalsi adresy, ktere scope vlastni a ktere se nesmi vratit
    jako cil - typicky virtual-gateway adresa IRB rozhrani a vlastni local
    adresy scope. Bez tohohle fallback vraci vlastni adresu jako cil -
    vysledkem je ping sam na sebe (overeno proti laborce).
    """
    owned_ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for own in owned or ():
        try:
            owned_ips.add(ipaddress.ip_interface(own).ip)
        except ValueError:
            continue

    for address in addresses:
        try:
            interface = ipaddress.ip_interface(address)
        except ValueError:
            continue

        network = interface.network
        if network.prefixlen >= network.max_prefixlen:
            continue
        if family == 6 and network.prefixlen < IPV6_FALLBACK_MIN_PREFIX:
            continue

        for candidate in network:
            if candidate == interface.ip:
                continue
            if candidate in owned_ips:
                continue
            # Vyloucit sit a broadcast je IPv4 uvaha - v IPv6 je adresa se
            # samymi nulami subnet-router anycast, ne broadcast.
            if family == 4 and network.prefixlen < network.max_prefixlen - 1:
                if candidate in (network.network_address, network.broadcast_address):
                    continue
            return str(candidate)
    return None


def _usable_nd(entry: dict[str, Any]) -> bool:
    """Zaznam bez MAC nebo v nedokoncenem stavu neni cil."""
    mac = (entry.get("mac") or "").strip().lower()
    state = (entry.get("state") or "").strip().lower()
    return bool(mac) and mac != "none" and state not in ("unreachable", "incomplete")


def _is_own(address: str, own: set[ipaddress.IPv4Address | ipaddress.IPv6Address]) -> bool:
    """Textove porovnani nestaci - zkraceny IPv6 zapis by proklouzl."""
    try:
        return ipaddress.ip_address(address) in own
    except ValueError:
        return False


def _networks_for(local: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Site scope, do kterych muze padnout baseline zaznam.

    Baseline je ze stareho boxu - jeho rozhrani se na novem nedaji dohledat
    jmenem, takze prislusnost ke scope se pozna jen pres IP subnet.
    """
    networks = []
    for address in local:
        try:
            networks.append(ipaddress.ip_interface(address).network)
        except ValueError:
            continue
    return networks


def _baseline_addresses(
    entries: list[dict[str, Any]],
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
    family: int,
    *,
    nd: bool,
) -> list[str]:
    """Adresy z baseline (pre snimku stareho boxu), ktere padnou do site scope.

    Link-local ND zaznamy se vylucuji vzdy - nejsou prenositelne mezi boxy
    (fe80 je per-link, ne globalni identita souseda).
    """
    if not networks:
        return []

    result = []
    for entry in entries:
        ip = entry.get("ip")
        if not ip:
            continue
        ip = str(ip)
        if is_link_local(ip):
            continue
        if nd and not _usable_nd(entry):
            continue
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if address.version != family:
            continue
        if not any(address in network for network in networks):
            continue
        result.append(ip)
    return result


def resolve_targets(
    scopes: list[Scope],
    arp_entries: list[dict[str, Any]],
    nd_entries: list[dict[str, Any]] | None = None,
    *,
    baseline_arp: list[dict[str, Any]] | None = None,
    baseline_nd: list[dict[str, Any]] | None = None,
) -> list[PingTarget]:
    """Odvodi cile pingu ze scopu, ARP tabulky (IPv4) a ND tabulky (IPv6).

    V ramci jednoho scope prijdou cile IPv4 pred IPv6 - napric vice scopy uz
    poradi neplati (je to scopeA-v4, scopeA-v6, scopeB-v4, ...). Na poradi
    stejne nic nezavisi, checky rodinu ctou z pole `family`, ne z pozice
    v seznamu.

    Priorita tieru: bgp > baseline > arp/nd > subnet-fallback. Prvni tier,
    ktery pro danou rodinu neco vrati, vyhrava.

    `baseline_arp`/`baseline_nd` jsou ARP/ND z pre snimku stareho boxu -
    pouziva se pri --phase post, kdy novy box jeste nema vlastni ARP/ND
    napliene (cutover cerstvy). Maji prednost pred vlastnimi cili.
    """
    nd_entries = nd_entries or []
    baseline_arp = baseline_arp or []
    baseline_nd = baseline_nd or []
    targets: list[PingTarget] = []

    for scope in scopes:
        if scope.is_device or scope.service_type not in PING_SERVICE_TYPES:
            continue

        instance = (
            scope.selectors.routing_instances[0]
            if scope.service_type == "IPVPN" and scope.selectors.routing_instances
            else None
        )
        keep_link_local = link_local_is_configured(scope)

        for family in (4, 6):
            if family == 4:
                local = scope.selectors.local_ipv4
                owned = scope.selectors.virtual_gw_v4
                baseline_entries = baseline_arp
                baseline_is_nd = False
            else:
                local = scope.selectors.local_ipv6
                owned = scope.selectors.virtual_gw_v6
                baseline_entries = baseline_nd
                baseline_is_nd = True

            # Vlastni adresy, ktere se nikdy nesmi vratit jako cil - i kdyz
            # je baseline (stary box) videl v ARP/ND, na novem boxu je to
            # nase vlastni IP. Parsuje se na ipaddress objekty, aby textova
            # varianta zapisu (napr. zkracene IPv6) nerozbila porovnani.
            own_addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
            for addr in (*local, *owned):
                try:
                    own_addresses.add(ipaddress.ip_interface(addr).ip)
                except ValueError:
                    continue

            # Nakonfigurovany BGP soused je nejpresnejsi cil - je to adresa
            # CPE primo z konfigurace sluzby, ne odhad z tabulek. Deaktivovani
            # sousedi (bgp_neighbors_inactive) se zamerne nepouzivaji: vypnuty
            # zamer nema generovat FAIL, ktery se cte jako nedostupne CPE.
            # Link-local soused se preskakuje - ping na fe80 potrebuje
            # rozhrani a selektor bgp_neighbors zadne nenese.
            bgp_addresses = []
            for neighbor in scope.selectors.bgp_neighbors:
                try:
                    neighbor_ip = ipaddress.ip_address(neighbor)
                except ValueError:
                    continue
                if (
                    neighbor_ip.version == family
                    and not neighbor_ip.is_link_local
                    and neighbor_ip not in own_addresses
                ):
                    bgp_addresses.append(str(neighbor_ip))
            if bgp_addresses:
                targets.extend(
                    PingTarget(scope.id, address, instance, "bgp", family)
                    for address in bgp_addresses
                )
                continue

            baseline_addresses = [
                address
                for address in _baseline_addresses(
                    baseline_entries, _networks_for(local), family, nd=baseline_is_nd
                )
                if ipaddress.ip_address(address) not in own_addresses
            ]
            if baseline_addresses:
                origin = "baseline-nd" if baseline_is_nd else "baseline-arp"
                targets.extend(
                    PingTarget(scope.id, address, instance, origin, family)
                    for address in baseline_addresses
                )
                continue

            if family == 4:
                addresses = [
                    (str(entry["ip"]), None)
                    for entry in arp_entries
                    if scope.selectors.matches_interface(str(entry.get("interface", "")))
                    and entry.get("ip")
                ]
                origin = "arp"
            else:
                addresses = [
                    (
                        str(entry["ip"]),
                        # Link-local cil bez interface Junos odmitne (overeno).
                        str(entry["interface"]) if is_link_local(str(entry["ip"])) else None,
                    )
                    for entry in nd_entries
                    if scope.selectors.matches_interface(str(entry.get("interface", "")))
                    and entry.get("ip")
                    and _usable_nd(entry)
                    and (keep_link_local or not is_link_local(str(entry["ip"])))
                ]
                origin = "nd"

            if addresses:
                targets.extend(
                    PingTarget(scope.id, address, instance, origin, family, interface)
                    for address, interface in addresses
                    # Neplati typicky, ale kdyby ARP/ND vratila nasi vlastni
                    # adresu (gratuitous ARP / duplicitni adresa muze vlastni
                    # IP dostat do tabulky na kterekoliv pozici), ping sam na
                    # sebe je nesmyslny vysledek - radsi zadny cil nez lhavy.
                    if not _is_own(address, own_addresses)
                )
                continue

            fallback = subnet_fallback(local, family, owned=[*local, *owned])
            if fallback:
                targets.append(
                    PingTarget(scope.id, fallback, instance, "subnet-fallback", family)
                )

    return targets


def parse_ping_result(xml: etree._Element) -> dict[str, Any]:
    """Prevede odpoved ping RPC na strukturovany vysledek.

    Junos vraci dva druhy neuspechu a je potreba je rozlisit:

    - 'no response' - ping odesel, nic se nevratilo. Summary existuje
      a hlasi 100% loss. To je platny vysledek merani.
    - 'internal error' + ping-error-message (napr. 'bind: Can't assign
      requested address') - ping vubec neodesel a summary chybi. Bez
      zaznamu duvodu by to vypadalo jako uspesne merenych nula paketu.
    """
    summary = xml.find(".//probe-results-summary")

    def value(path: str) -> str | None:
        if summary is None:
            return None
        node = summary.find(path)
        return node.text.strip() if node is not None and node.text else None

    sent = int(value("probes-sent") or 0)
    received = int(value("responses-received") or 0)
    loss = value("packet-loss")
    rtt_us = value("rtt-average")

    result: dict[str, Any] = {
        "sent": sent,
        "received": received,
        # Kdyz summary chybi, neprosel ani jeden paket - 100 je pravdivejsi
        # nez None, ktere by se v reportu cetlo jako "nemereno".
        "loss_percent": int(loss) if loss is not None else (None if summary is not None else 100),
        "rtt_avg_ms": round(int(rtt_us) / 1000.0, 3) if rtt_us and received else None,
    }

    reason = _failure_reason(xml)
    if reason and not received:
        result["error"] = reason

    return result


def _failure_reason(xml: etree._Element) -> str | None:
    """Text z ping-failure / ping-error-message, kdyz ping neuspel."""
    parts = [
        node.text.strip()
        for tag in ("ping-failure", "ping-error-message")
        for node in xml.iter(tag)
        if node.text and node.text.strip()
    ]
    return "; ".join(dict.fromkeys(parts)) or None


def run_ping(device: Any, target: PingTarget, count: int = DEFAULT_COUNT) -> dict[str, Any]:
    """Spusti ping z zarizeni. Neuspech neni chyba nastroje, ale vysledek.

    Odchytava se zamerne cokoliv. Krome ocekavanych RPC chyb vMX obcas vrati
    poskozene XML (`<ping-results>` bez uzaviraciho tagu), na kterem PyEZ
    spadne na XMLSyntaxError - overeno jako prechodne, pri opakovani projde.
    Argument `routing_instance` s tim nesouvisi, ten RPC prijima bez problemu.
    Protoze je ping advisory, staci duvod zapsat a pokracovat; shodit celou
    capture kvuli jednomu probu by bylo horsi.
    """
    kwargs: dict[str, Any] = {
        "host": target.target,
        "count": str(count),
        # Bez rapid trva 5 paketu ~5 s, s nim ~0,3 s. Overeno proti laborce,
        # ze tvar odpovedi zustava stejny - probe-results-summary se stejnymi
        # poli - takze parse_ping_result se nemeni.
        "rapid": True,
    }
    if target.routing_instance:
        kwargs["routing_instance"] = target.routing_instance
    if target.interface:
        kwargs["interface"] = target.interface

    record = target.to_dict()
    try:
        xml = device.rpc.ping(**kwargs)
    except Exception as error:  # noqa: BLE001
        record.update(
            {
                "sent": count,
                "received": 0,
                "loss_percent": 100,
                "rtt_avg_ms": None,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        return record

    record.update(parse_ping_result(xml))
    return record
