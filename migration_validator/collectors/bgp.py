"""Sber stavu BGP session a poctu prefixu.

Pouziva get_bgp_neighbor_information, protoze summary varianta neobsahuje
pocet advertised prefixu.

Collector nerozhoduje, jestli je stav v poradku - jen ho zapise.

Overeno proti laborce: peer-address nese port ('150.0.0.1+179' na MX,
efemerni port '150.0.0.1+57010' na EVO), jeden peer muze mit az 11 RIB
(bgp.rtarget.0, inet.0, bgp.l3vpn.0, ...) a pocty se scitaji pres vsechny.
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

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        peers: dict[str, dict[str, Any]] = {}

        for node in xml.iter("bgp-peer"):
            raw_address = _text(node, "peer-address")
            if not raw_address:
                continue
            address = strip_port(raw_address)

            prefixes = {"received": 0, "accepted": 0, "advertised": 0}
            for rib in node.iter("bgp-rib"):
                prefixes["received"] += _int(rib, "received-prefix-count")
                prefixes["accepted"] += _int(rib, "accepted-prefix-count")
                prefixes["advertised"] += _int(rib, "advertised-prefix-count")

            instance = _text(node, "peer-cfg-rti")
            if instance is not None and instance.lower() in DEFAULT_INSTANCES:
                instance = None

            peer_as = _text(node, "peer-as")

            peers[address] = {
                "state": _text(node, "peer-state") or "unknown",
                "peer_as": int(peer_as) if peer_as and peer_as.isdigit() else None,
                "routing_instance": instance,
                "prefixes": prefixes,
            }

        return peers
