"""Sber stavu a counteru rozhrani.

transit-traffic-statistics/input-pps a output-pps uz jsou rate, ne kumulativni
counter - Junos je pocita sam. Neni proto potreba dvojite vzorkovani ani
cekaci okno, staci jeden RPC pruchod.

Absolutni byte countery se zamerne nesbiraji: mezi dvema boxy jsou
nesrovnatelne, protoze bezi od jineho uptime.

Overeno proti nahranemu XML z laborky (24.2R1-S2.5 / 25.2R1.8-EVO):
fyzicka rozhrani nesou 'traffic-statistics', logicke jednotky
'transit-traffic-statistics'. Logicka jednotka nema vlastni 'oper-status'
ani chybove countery na zadne z obou platforem.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.registry import register


def _text(node: etree._Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _int(node: etree._Element | None, path: str) -> int:
    value = _text(node, path)
    if value is None:
        return 0
    try:
        return int(float(value))
    except ValueError:
        return 0


def _rates(node: etree._Element) -> dict[str, int]:
    """input-pps / output-pps. Logicka jednotka ma transit-, fyzicke rozhrani ne."""
    stats = node.find("transit-traffic-statistics")
    if stats is None:
        stats = node.find("traffic-statistics")
    return {
        "input_pps": _int(stats, "input-pps"),
        "output_pps": _int(stats, "output-pps"),
    }


@register
class InterfacesCollector(Collector):
    name = "interfaces"

    def rpc_name(self, platform: str) -> str:
        return "get_interface_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"extensive": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for physical in xml.iter("physical-interface"):
            name = _text(physical, "name")
            if not name:
                continue

            errors = physical.find("input-error-list")
            output_errors = physical.find("output-error-list")

            result[name] = {
                "admin_status": (_text(physical, "admin-status") or "unknown").lower(),
                "oper_status": (_text(physical, "oper-status") or "unknown").lower(),
                "input_errors": _int(errors, "input-errors"),
                "output_errors": _int(output_errors, "output-errors"),
                "framing_errors": _int(errors, "framing-errors"),
                **_rates(physical),
            }

            for logical in physical.iter("logical-interface"):
                unit_name = _text(logical, "name")
                if not unit_name:
                    continue
                result[unit_name] = {
                    "admin_status": result[name]["admin_status"],
                    "oper_status": (
                        _text(logical, "oper-status") or result[name]["oper_status"]
                    ).lower(),
                    "input_errors": 0,
                    "output_errors": 0,
                    "framing_errors": 0,
                    **_rates(logical),
                }

        return result
