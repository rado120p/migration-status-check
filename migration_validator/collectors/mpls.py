"""Sber stavu MPLS na rozhranich."""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.isis import _localname_text
from migration_validator.collectors.registry import register


@register
class MplsInterfaceCollector(Collector):
    name = "mpls_interface"

    def rpc_name(self, platform: str) -> str:
        return "get_mpls_interface_information"

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        interfaces: dict[str, dict[str, Any]] = {}
        for node in xml.iter("{*}mpls-interface"):
            interface = _localname_text(node, "interface-name")
            if not interface:
                continue
            interfaces[interface] = {
                "state": _localname_text(node, "mpls-interface-state") or "unknown",
            }
        return interfaces
