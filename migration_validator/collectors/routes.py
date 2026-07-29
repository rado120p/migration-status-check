"""Sber statickych rout z routovaci tabulky.

Collector nerozhoduje, jestli routa chybi nebo prebyva - jen zapise, co
v tabulce je. Porovnani se zamerem z konfigurace patri do checku.

Overeno proti laborce: `table-name` nese jmeno RIB vcetne rodiny
(`L3VPN-CPE13-NNI.inet6.0`), takze asymetrie, kterou ma konfigurace mezi
IPv4 a IPv6, se v RPC nevyskytuje. `via` nese vystupni rozhrani, takze
mapovani na sluzbu nepotrebuje aritmetiku nad next-hopem.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _text
from migration_validator.collectors.registry import register

STATIC = "static"
ACTIVE_TAG = "*"


def _texts(node: etree._Element, tag: str) -> list[str]:
    """Vsechny neprazdne texty daneho tagu pod uzlem.

    `to` i `via` sedi uvnitr <nh>, ne primo pod <rt-entry>, proto iter().
    """
    values = []
    for element in node.iter(tag):
        value = (element.text or "").strip()
        if value:
            values.append(value)
    return values


@register
class RoutesCollector(Collector):
    name = "routes"

    def rpc_name(self, platform: str) -> str:
        return "get_route_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        # Bez `all=True`: ta varianta pridava jen __juniper_private*
        # tabulky, coz je sum. Filtr na protokol drzi odpoved malou i na
        # zarizeni s plnou internetovou tabulkou.
        return {"protocol": STATIC}

    def parse(
        self, xml: etree._Element, platform: str
    ) -> dict[str, dict[str, dict[str, Any]]]:
        tables: dict[str, dict[str, dict[str, Any]]] = {}

        for table in xml.iter("route-table"):
            name = _text(table, "table-name")
            if not name:
                continue

            prefixes: dict[str, dict[str, Any]] = {}
            for route in table.iter("rt"):
                prefix = _text(route, "rt-destination")
                if not prefix:
                    continue

                for entry in route.iter("rt-entry"):
                    # Filtr na protokol uz je v RPC, tohle je pojistka:
                    # nasazeni s jinym filtrem by jinak zapsalo BGP routy
                    # jako staticke.
                    if (_text(entry, "protocol-name") or "").lower() != STATIC:
                        continue

                    prefixes[prefix] = {
                        "next_hop": _texts(entry, "to"),
                        "via": _texts(entry, "via"),
                        "active": _text(entry, "active-tag") == ACTIVE_TAG,
                    }

            # RPC vraci pres dvacet tabulek, vetsina prazdna. Ukladat je
            # znamena nafouknout kazdy snimek o rady, ktere nic nerikaji.
            if prefixes:
                tables[name] = prefixes

        return tables
