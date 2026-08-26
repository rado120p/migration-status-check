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
collector bere prvni a dalsi tise zahazuje - v capturech z laborky k tomu
nedochazi (1:1).
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
            neighbor_node = next(interface_node.iter("{*}pim-neighbor"), None)
            if not interface or neighbor_node is None:
                continue
            uptime = next(neighbor_node.iter("{*}pim-neighbor-uptime"), None)
            neighbors[interface] = {
                "neighbor_address": _localname_text(neighbor_node, "pim-neighbor-address"),
                "uptime_seconds": _seconds_attr(uptime),
            }
        return neighbors
