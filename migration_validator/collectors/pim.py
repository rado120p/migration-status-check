"""Sber PIM sousedstvi.

Vnoreni odpovedi je pim-neighbors-information > pim-interface > pim-neighbor.
Element pim-interface-name v nahranych fixtures lezi uvnitr pim-neighbor
(ne primo pod pim-interface) - `_localname_text` ale hleda mezi vsemi
potomky, takze funguje na oba tvary, i kdyby zivá odpoved nesla jmeno
rozhrani primo na urovni pim-interface.

pim-interface bez vnoreneho pim-neighbor (rozhrani s PIM zapnutym, ale bez
souseda) nedostava synteticky zaznam - klic proste chybi, absence je
absence, ne vymyslene Down.

Kontrakt klicuje jednim sousedem na rozhrani (viz Ukol 6), takze pri vic
nez jednom pim-neighbor pod jednim pim-interface (multi-access segment)
collector bere prvni IPv4 souseda a dalsi tise zahazuje - v capturech z
laborky k tomu nedochazi (1:1). Na dual-stack rozhrani laborka PTX vypise
IPv4 pim-interface blokem, pak IPv6 - bez filtru by IPv6 prepisalo IPv4.
Filtrujem ip-protocol-version=6; chybejici verze = IPv4.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.isis import _localname_text, _seconds_attr
from migration_validator.collectors.registry import register


@register
class PimNeighborCollector(Collector):
    name = "pim_neighbor"

    def rpc_name(self, platform: str) -> str:
        return "get_pim_neighbors_information"

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        neighbors: dict[str, dict[str, Any]] = {}
        for interface_node in xml.iter("{*}pim-interface"):
            interface = _localname_text(interface_node, "pim-interface-name")
            if not interface:
                continue
            for neighbor_node in interface_node.iter("{*}pim-neighbor"):
                # Kazda rodina ma vlastni pim-interface se stejnym jmenem,
                # v6 az za v4 (PTX laborka 2026-09-24) - bez filtru v6 soused
                # prepsal v4. IPv6 PIM se nevyhodnocuje; chybejici verze = v4.
                if _localname_text(neighbor_node, "ip-protocol-version") == "6":
                    continue
                if interface in neighbors:
                    break
                uptime = next(neighbor_node.iter("{*}pim-neighbor-uptime"), None)
                neighbors[interface] = {
                    "neighbor_address": _localname_text(neighbor_node, "pim-neighbor-address"),
                    "uptime_seconds": _seconds_attr(uptime),
                }
                break
        return neighbors
