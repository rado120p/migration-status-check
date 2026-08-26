"""Sber LDP sousedstvi.

Nahravka nese i sousedstvi na lo0.0 - to je targeted session mezi loopbacky
zarizeni v meshi, ne stav tranzitniho linku. Collector je proto zahazuje
(spec Ukolu 6); zajima nas jen LDP na fyzickych/agregovanych rozhranich.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.isis import _localname_text, _seconds_attr
from migration_validator.collectors.registry import register


@register
class LdpNeighborCollector(Collector):
    name = "ldp_neighbor"

    def rpc_name(self, platform: str) -> str:
        return "get_ldp_neighbor_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"detail": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        neighbors: dict[str, dict[str, Any]] = {}
        for node in xml.iter("{*}ldp-neighbor"):
            interface = _localname_text(node, "interface-name")
            if not interface or interface.startswith("lo0"):
                # lo0 vypisy se ignoruji (spec) - targeted session, ne
                # stav tranzitniho linku
                continue
            uptime = next(node.iter("{*}ldp-up-time"), None)
            neighbors[interface] = {
                "neighbor_address": _localname_text(node, "ldp-neighbor-address"),
                "uptime_seconds": _seconds_attr(uptime),
            }
        return neighbors
