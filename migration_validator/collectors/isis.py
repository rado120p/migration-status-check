"""Sber IS-IS stavu. Tri collectory v jednom modulu - sdileji namespace
helpery.

Zivá PyEZ odpoved muze nest xmlns junos-routing, ale normalizovane nahravky
z Tasku 1 zadny prefix nemaji - proto se iteruje pres '{*}' wildcard, aby
collector fungoval v obou pripadech, a texty se ctou pres localname misto
primeho `node.iter('isis-adjacency')`, ktery by na jmennem prostoru nic
nenasel.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.registry import register


def _localname_text(node: etree._Element, name: str) -> str | None:
    for child in node.iter(f"{{*}}{name}"):
        text = (child.text or "").strip()
        return text or None
    return None


def _seconds_attr(node: etree._Element | None) -> int | None:
    """junos:seconds - atribut nese verzi OS v URI, matchuje se proto na obe
    varianty: `seconds` (nahravky bez namespace) i `{...}seconds` (ziva
    odpoved s prefixem junos:)."""
    if node is None:
        return None
    for key, value in node.attrib.items():
        if key.endswith("}seconds") or key == "seconds":
            try:
                return int(value)
            except ValueError:
                return None
    return None


@register
class IsisAdjacencyCollector(Collector):
    name = "isis_adjacency"

    def rpc_name(self, platform: str) -> str:
        return "get_isis_adjacency_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"detail": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        adjacencies: dict[str, dict[str, Any]] = {}
        for node in xml.iter("{*}isis-adjacency"):
            interface = _localname_text(node, "interface-name")
            if not interface:
                continue
            adjacencies[interface] = {
                "system_name": _localname_text(node, "system-name"),
                "state": _localname_text(node, "adjacency-state") or "unknown",
                "ip_address": _localname_text(node, "ip-address"),
                "ipv6_address": _localname_text(node, "global-ipv6-address"),
            }
        return adjacencies


@register
class IsisInterfaceCollector(Collector):
    name = "isis_interface"

    def rpc_name(self, platform: str) -> str:
        return "get_isis_interface_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"detail": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        interfaces: dict[str, dict[str, Any]] = {}
        for node in xml.iter("{*}isis-interface"):
            interface = _localname_text(node, "interface-name")
            if not interface:
                continue
            levels: dict[str, dict[str, Any]] = {}
            for level_node in node.iter("{*}interface-level-data"):
                level = _localname_text(level_node, "level")
                if not level:
                    continue
                levels[level] = {
                    "passive": _localname_text(level_node, "passive") == "Passive",
                }
            interfaces[interface] = {"levels": levels}
        return interfaces


@register
class IsisOverviewCollector(Collector):
    name = "isis_overview"

    def rpc_name(self, platform: str) -> str:
        return "get_isis_overview_information"

    def parse(self, xml: etree._Element, platform: str) -> dict[str, Any]:
        overload = next(xml.iter("{*}isis-overload-enabled"), None)
        return {"overload_enabled": overload is not None}
