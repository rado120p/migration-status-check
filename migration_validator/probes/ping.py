"""Aktivni ping probe.

Jediny aktivni test - proto vlastni kategorie mimo collectory. Bezi az po
bulk sberu, protoze cile se odvozuji z ARP.

Ping bezi jen v service rezimu: bez inventory neni znam cil ani source
adresa, takze snapshot ma probes.ping prazdne.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from lxml import etree

from migration_validator.models.scope import Scope

PING_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})
DEFAULT_COUNT = 5


@dataclass(frozen=True)
class PingTarget:
    scope_id: str
    target: str
    source: str | None
    routing_instance: str | None
    resolved_from: str  # arp | subnet-fallback

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "target": self.target,
            "source": self.source,
            "routing_instance": self.routing_instance,
            "resolved_from": self.resolved_from,
        }


def source_address(scope: Scope) -> str | None:
    """Adresa rozhrani. U IRB se pouziva virtual-gw.

    Rodiny se tu jen provizorne spojuji zpet dohromady - vyber spravne
    rodiny podle cile resi az pozdejsi task, tady se jen zachovava dnesni
    chovani nad rozdelenymi selektory.
    """
    virtual_gw = scope.selectors.virtual_gw_v4 + scope.selectors.virtual_gw_v6
    if virtual_gw:
        return virtual_gw[0].split("/")[0]
    local = scope.selectors.local_ipv4 + scope.selectors.local_ipv6
    if local:
        return local[0].split("/")[0]
    return None


def subnet_fallback(scope: Scope) -> str | None:
    """Prvni pouzitelna adresa ze subnetu, ktera neni nase vlastni."""
    for address in scope.selectors.local_ipv4 + scope.selectors.local_ipv6:
        try:
            interface = ipaddress.ip_interface(address)
        except ValueError:
            continue

        network = interface.network
        if network.prefixlen >= network.max_prefixlen:
            continue

        for candidate in network:
            if candidate == interface.ip:
                continue
            if network.prefixlen < network.max_prefixlen - 1:
                if candidate in (network.network_address, network.broadcast_address):
                    continue
            return str(candidate)
    return None


def resolve_targets(
    scopes: list[Scope], arp_entries: list[dict[str, Any]]
) -> list[PingTarget]:
    """Odvodi cile pingu ze scopu a ARP tabulky."""
    targets: list[PingTarget] = []

    for scope in scopes:
        if scope.is_device or scope.service_type not in PING_SERVICE_TYPES:
            continue

        source = source_address(scope)
        instance = (
            scope.selectors.routing_instances[0]
            if scope.service_type == "IPVPN" and scope.selectors.routing_instances
            else None
        )

        addresses = [
            str(entry["ip"])
            for entry in arp_entries
            if scope.selectors.matches_interface(str(entry.get("interface", "")))
            and entry.get("ip")
        ]

        if addresses:
            targets.extend(
                PingTarget(scope.id, address, source, instance, "arp")
                for address in addresses
            )
            continue

        fallback = subnet_fallback(scope)
        if fallback:
            targets.append(
                PingTarget(scope.id, fallback, source, instance, "subnet-fallback")
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
    kwargs: dict[str, Any] = {"host": target.target, "count": str(count)}
    if target.source:
        kwargs["source"] = target.source
    if target.routing_instance:
        kwargs["routing_instance"] = target.routing_instance

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
