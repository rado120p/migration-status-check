"""Sber ARP tabulky.

ARP je zaroven producent dat pro ping probe - z nej se odvozuji cile.

Poznamka k routing_instance: ani MX, ani EVO ho v odpovedi 'show arp no-resolve'
neuvadeji (arp-table-entry-flags nese jen <none/>), takze klic zpravidla zustane
None. Je v schematu zamerne - kontrakt fact-schematu ho predepisuje a scope
filtruje ARP podle 'interface', ne podle instance.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _text
from migration_validator.collectors.registry import register


def split_learned_via(name: str) -> tuple[str, str | None]:
    """Rozdeli 'irb.14[ ae0.14 ]' na ('irb.14', 'ae0.14').

    Junos u zaznamu naucenych pres IRB pripoji v hranate zavorce L2
    rozhrani. Scope filtruje pres presnou shodu jmena, takze neorezany
    tvar zaznam vyradi - v reportu pak 'zadny zaznam' u sluzby, ktera
    ARP ma (JSON ho nese, check ho nevidi).
    """
    base, bracket, rest = name.partition("[")
    if not bracket:
        return name.strip(), None
    return base.strip(), rest.rstrip("]").strip() or None


@register
class ArpCollector(Collector):
    name = "arp"

    def rpc_name(self, platform: str) -> str:
        return "get_arp_table_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"no_resolve": True}

    def parse(self, xml: etree._Element, platform: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        for node in xml.iter("arp-table-entry"):
            address = _text(node, "ip-address")
            interface = _text(node, "interface-name")
            if not address or not interface:
                continue
            interface, learned_via = split_learned_via(interface)
            entries.append(
                {
                    "ip": address,
                    "mac": _text(node, "mac-address"),
                    "interface": interface,
                    "learned_via": learned_via,
                    "routing_instance": _text(node, "arp-table-entry-flags/routing-instance")
                    or _text(node, "routing-instance"),
                }
            )

        return entries
