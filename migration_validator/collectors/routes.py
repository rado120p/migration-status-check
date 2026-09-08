"""Sber statickych a agregatnich rout z routovaci tabulky.

Collector nerozhoduje, jestli routa chybi nebo prebyva - jen zapise, co
v tabulce je. Porovnani se zamerem z konfigurace patri do checku.

Overeno proti laborce: `table-name` nese jmeno RIB vcetne rodiny
(`L3VPN-CPE13-NNI.inet6.0`), takze asymetrie, kterou ma konfigurace mezi
IPv4 a IPv6, se v RPC nevyskytuje. `via` nese vystupni rozhrani, takze
mapovani na sluzbu nepotrebuje aritmetiku nad next-hopem.

Sber jede dvema pruchody stejneho RPC (protocol=static, protocol=aggregate)
- vzor je InterfacesCollector (extensive + terse). Merge v collect() druhym
pruchodem jen priresava, nikdy neprepisuje: prefix nemuze byt v jedne RIB
soucasne static i aggregate, takze kolize by znamenala poskozena data, ne
legitimni update.

Overeno proti laborce (routes.2.xml): agregatni zaznam nema zadny <nh> -
<nh-type> (Discard/Reject) sedi primo pod <rt-entry>, takze next_hop i via
jsou pro nej vzdy prazdne. protocol-name nese presne "Aggregate".

Prefix muze mit vic rt-entry (next-hop + qualified-next-hop s jinou
preferenci) - slucuji se, active = aspon jeden aktivni.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector, CollectorError
from migration_validator.collectors.interfaces import _text
from migration_validator.collectors.registry import register

STATIC = "static"
AGGREGATE = "aggregate"
PROTOCOLS = (STATIC, AGGREGATE)
ACTIVE_TAG = "*"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if not (v in seen or seen.add(v))]


def _merge_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """next-hop + qualified-next-hop s jinou preferenci = dva <rt-entry>
    pod jednim prefixem (MX1-POP1 2026-09-08, routes_qnh.xml). Drive
    posledni zaznam prepsal prvni, takze aktivni routa vysla jako
    neaktivni a upstream check videl jen hopy neaktivniho zaznamu.
    Aktivni zaznam jde prvni, aby poradi hopu odpovidalo forwardingu."""
    ordered = sorted(entries, key=lambda e: not e["active"])
    return {
        "next_hop": _unique([hop for e in ordered for hop in e["next_hop"]]),
        "via": _unique([via for e in ordered for via in e["via"]]),
        "active": any(e["active"] for e in ordered),
        "protocol": ordered[0]["protocol"],
    }


def _texts(node: etree._Element, tag: str) -> list[str]:
    """Vsechny neprazdne texty daneho tagu pod uzlem.

    `to` i `via` sedi uvnitr <nh>, ne primo pod <rt-entry>, proto iter().

    .strip() je parita se sousednimi collectory (collectors/interfaces.py:32,
    collectors/bgp.py:30), kde Junos hodnoty obalene novymi radky opravdu
    vraci. Zadna soucasna nahravka rout bile znaky na <to>, <via>,
    <rt-destination> ani <table-name> nema, takze tuhle vetev nic netestuje -
    drzi se kvuli konzistenci, ne kvuli pozorovanemu vstupu.
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

    def rpc_names(self, platform: str) -> tuple[str, ...]:
        # Dvakrat totez RPC (static + aggregate) - record tak ulozi obe
        # nahravky (routes.xml, routes.2.xml).
        return ("get_route_information", "get_route_information")

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        # Bez `all=True`: ta varianta pridava jen __juniper_private*
        # tabulky, coz je sum. Filtr na protokol drzi odpoved malou i na
        # zarizeni s plnou internetovou tabulkou.
        return {"protocol": STATIC}

    def rpc_calls(self, platform: str) -> tuple[tuple[str, dict[str, Any]], ...]:
        return tuple(
            ("get_route_information", {"protocol": protocol})
            for protocol in PROTOCOLS
        )

    def collect(self, device: Any, platform: str) -> Any:
        # Selhani ktereholiv pruchodu je chyba celeho collectoru (stejny
        # duvod jako u interfaces): bez aggregate pruchodu by check cetl
        # chybejici agregat jako zmizely, castecna data nesmi vypadat
        # jako zmerena.
        if not self.supports(platform):
            raise CollectorError(
                f"collector '{self.name}' nepodporuje platformu '{platform}'"
            )

        tables: dict[str, dict[str, dict[str, Any]]] = {}
        failures: list[str] = []

        for rpc_name, rpc_kwargs in self.rpc_calls(platform):
            variant = f"{rpc_name}(protocol={rpc_kwargs['protocol']})"
            try:
                xml = getattr(device.rpc, rpc_name)(**rpc_kwargs)
            except Exception as error:  # noqa: BLE001 - RpcError i sitove chyby
                failures.append(f"{variant}: {type(error).__name__}: {error}")
                continue

            try:
                parsed = self.parse(xml, platform)
            except Exception as error:  # noqa: BLE001
                failures.append(f"{variant}: parsovani selhalo - {error}")
                continue

            for table, prefixes in parsed.items():
                target = tables.setdefault(table, {})
                for prefix, data in prefixes.items():
                    # Druhy pruchod jen pridava: prefix nemuze byt v jedne
                    # RIB zaroven static a aggregate, a last-write-wins by
                    # jeden z nich tise schoval.
                    target.setdefault(prefix, data)

        if failures:
            raise CollectorError(
                f"collector '{self.name}': RPC selhalo - " + "; ".join(failures)
            )

        return tables

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

                entries: list[dict[str, Any]] = []
                for entry in route.iter("rt-entry"):
                    # Filtr na protokol uz je v RPC, tohle je pojistka:
                    # nasazeni s jinym filtrem (nebo neocekavana odpoved)
                    # by jinak zapsalo BGP routy jako staticke/agregatni.
                    protocol = (_text(entry, "protocol-name") or "").lower()
                    if protocol not in PROTOCOLS:
                        continue
                    entries.append({
                        "next_hop": _texts(entry, "to"),
                        "via": _texts(entry, "via"),
                        "active": _text(entry, "active-tag") == ACTIVE_TAG,
                        "protocol": protocol,
                    })
                if entries:
                    prefixes[prefix] = _merge_entries(entries)

            # RPC vraci pres dvacet tabulek, vetsina prazdna. Ukladat je
            # znamena nafouknout kazdy snimek o rady, ktere nic nerikaji.
            if prefixes:
                tables[name] = prefixes

        return tables
