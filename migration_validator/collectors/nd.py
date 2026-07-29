"""Sber ND tabulky (IPv6 sousede).

Dvojce arp.py - z ND se odvozuji cile pingu pro IPv6, stejne jako se z ARP
odvozuji pro IPv4.

Collector zaznamy nefiltruje. Link-local sousede i zaznamy bez MAC se vraci
vsechny; rozhodnuti, co je pouzitelny cil pingu, patri do probes/ping.py,
protoze zavisi na konfiguraci sluzby, kterou collector nezna.

Overeno proti laborce: RPC i jmena prvku jsou shodna na vMX i na EVO. MX
obaluje texty novymi radky, EVO ne - _text() to resi strippingem.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _text
from migration_validator.collectors.registry import register


@register
class NdCollector(Collector):
    name = "nd"

    def rpc_name(self, platform: str) -> str:
        return "get_ipv6_nd_information"

    def parse(self, xml: etree._Element, platform: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        for node in xml.iter("ipv6-nd-entry"):
            address = _text(node, "ipv6-nd-neighbor-address")
            interface = _text(node, "ipv6-nd-interface-name")
            if not address or not interface:
                continue
            entries.append(
                {
                    "ip": address,
                    "mac": _text(node, "ipv6-nd-neighbor-l2-address"),
                    "interface": interface,
                    "state": _text(node, "ipv6-nd-state"),
                }
            )

        return entries
