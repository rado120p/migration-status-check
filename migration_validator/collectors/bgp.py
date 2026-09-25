"""Sber stavu BGP session a poctu prefixu.

Pouziva get_bgp_neighbor_information, protoze summary varianta neobsahuje
pocet advertised prefixu.

Collector nerozhoduje, jestli je stav v poradku - jen ho zapise.

Overeno proti laborce: peer-address nese port ('150.0.0.1+179' na MX,
efemerni port '150.0.0.1+57010' na EVO) a jeden peer muze mit az 11 RIB
(bgp.rtarget.0, inet.0, bgp.l3vpn.0, ...). Countery se ukladaji za kazdou
RIB zvlast - souctem by se IPv4 a IPv6 slily do jednoho cisla a pokles v
inet6.0 kompenzovany narustem v inet.0 by prosel bez povsimnuti.

Od schematu 14 (kolize klicu, spec 2026-09-25) je `parse` seznam zaznamu,
ne slovnik podle adresy: dve VRF se stejnou p2p podsiti maji peera na
stejne adrese (ostry beh MX -> ACX 2026-09-23) a klic adresou jednu
session tise zahodil. Identitu nese cely zaznam.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _int, _text
from migration_validator.collectors.registry import register

DEFAULT_INSTANCES = {"master", "default", ""}


def strip_port(value: str) -> str:
    """peer-address casto nese port: '198.11.13.2+179' -> '198.11.13.2'."""
    return value.strip().split("+", 1)[0]


@register
class BgpCollector(Collector):
    name = "bgp"

    def rpc_name(self, platform: str) -> str:
        return "get_bgp_neighbor_information"

    def parse(self, xml: etree._Element, platform: str) -> list[dict[str, Any]]:
        # Seznam, ne slovnik podle adresy: dve VRF se stejnou p2p podsiti
        # maji peera na stejne adrese (ostry beh MX -> ACX 2026-09-23) a
        # klic adresou jednu session tise zahodil. Identitu nese zaznam:
        # (routing_instance, address, local_interface) - local_interface
        # rozlisi i dva link-local sousedy v jedne RI (schema 14).
        peers: list[dict[str, Any]] = []

        for node in xml.iter("bgp-peer"):
            raw_address = _text(node, "peer-address")
            if not raw_address:
                continue
            address = strip_port(raw_address)

            ribs: dict[str, dict[str, int]] = {}
            for rib in node.iter("bgp-rib"):
                rib_name = _text(rib, "name")
                if not rib_name:
                    continue
                ribs[rib_name] = {
                    "received": _int(rib, "received-prefix-count"),
                    "accepted": _int(rib, "accepted-prefix-count"),
                    "advertised": _int(rib, "advertised-prefix-count"),
                    "active": _int(rib, "active-prefix-count"),
                    "suppressed": _int(rib, "suppressed-prefix-count"),
                }

            instance = _text(node, "peer-cfg-rti")
            if instance is not None and instance.lower() in DEFAULT_INSTANCES:
                instance = None

            peer_as = _text(node, "peer-as")

            peers.append({
                "address": address,
                "routing_instance": instance,
                "local_interface": _text(node, "local-interface-name"),
                "state": _text(node, "peer-state") or "unknown",
                "peer_as": int(peer_as) if peer_as and peer_as.isdigit() else None,
                "ribs": ribs,
            })

        return peers
