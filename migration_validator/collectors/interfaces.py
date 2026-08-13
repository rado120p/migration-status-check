"""Sber stavu a counteru rozhrani.

Dve varianty tehoz RPC, kazda nese neco jineho:

- extensive: chybove countery, pps rate a admin/oper stav fyzickych
  rozhrani. Logicka jednotka tu NEMA vlastni admin-status ani oper-status
  na zadne z obou platforem - drive se dedily z rodice, coz byla fabulace
  (disablovany irb.4094 je admin down / link up, rodic irb up/up).
- terse: skutecny per-unit admin-status i oper-status (sloupce Admin/Link
  z 'show interfaces terse'). Countery nenese.

collect() vola obe a slucuje per rozhrani; stav se cte jen tam, kde ho box
opravdu vydava, nic se nededi ani neodvozuje z iff- flagu.

transit-traffic-statistics/input-pps a output-pps uz jsou rate, ne kumulativni
counter - Junos je pocita sam. Neni proto potreba dvojite vzorkovani ani
cekaci okno, staci jeden RPC pruchod.

Absolutni byte countery se zamerne nesbiraji: mezi dvema boxy jsou
nesrovnatelne, protoze bezi od jineho uptime.

Overeno proti nahranemu XML z laborky (24.2R1-S2.5 / 25.2R1.8-EVO):
fyzicka rozhrani nesou 'traffic-statistics', logicke jednotky
'transit-traffic-statistics'.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector, CollectorError
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


def _status(node: etree._Element, path: str) -> str:
    return (_text(node, path) or "unknown").lower()


@register
class InterfacesCollector(Collector):
    name = "interfaces"

    def rpc_name(self, platform: str) -> str:
        return "get_interface_information"

    def rpc_names(self, platform: str) -> tuple[str, ...]:
        # Dvakrat totez RPC (extensive + terse) - record tak ulozi obe
        # nahravky (interfaces.xml, interfaces.2.xml).
        return ("get_interface_information", "get_interface_information")

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"extensive": True}

    def rpc_calls(self, platform: str) -> tuple[tuple[str, dict[str, Any]], ...]:
        return (
            ("get_interface_information", {"extensive": True}),
            ("get_interface_information", {"terse": True}),
        )

    def collect(self, device: Any, platform: str) -> dict[str, dict[str, Any]]:
        # Selhani kterekoliv varianty je chyba celeho collectoru (stejne
        # jako u evpn_mac): bez terse by unity nemely stav, bez extensive
        # countery - castecna data by check tise vydaval za zmerena.
        if not self.supports(platform):
            raise CollectorError(
                f"collector '{self.name}' nepodporuje platformu '{platform}'"
            )

        merged: dict[str, dict[str, Any]] = {}
        failures: list[str] = []

        for rpc_name, rpc_kwargs in self.rpc_calls(platform):
            variant = f"{rpc_name}({', '.join(sorted(rpc_kwargs))})"
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

            for name, data in parsed.items():
                merged.setdefault(name, {}).update(data)

        if failures:
            raise CollectorError(
                f"collector '{self.name}': RPC selhalo - " + "; ".join(failures)
            )

        return merged

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        if xml.get("style") == "terse":
            return self._parse_terse(xml)
        return self._parse_extensive(xml)

    def _parse_terse(self, xml: etree._Element) -> dict[str, dict[str, Any]]:
        """Jen admin/oper stavy - jediny vypis, kde je ma i logicka jednotka.

        Zadne countery ani nuly za ne: slouceni v collect() by jimi
        prepsalo skutecne hodnoty z extensive.
        """
        result: dict[str, dict[str, Any]] = {}

        for physical in xml.iter("physical-interface"):
            name = _text(physical, "name")
            if not name:
                continue
            result[name] = {
                "admin_status": _status(physical, "admin-status"),
                "oper_status": _status(physical, "oper-status"),
            }
            for logical in physical.iter("logical-interface"):
                unit_name = _text(logical, "name")
                if not unit_name:
                    continue
                result[unit_name] = {
                    "admin_status": _status(logical, "admin-status"),
                    "oper_status": _status(logical, "oper-status"),
                }

        return result

    def _parse_extensive(self, xml: etree._Element) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for physical in xml.iter("physical-interface"):
            name = _text(physical, "name")
            if not name:
                continue

            errors = physical.find("input-error-list")
            output_errors = physical.find("output-error-list")

            result[name] = {
                "admin_status": _status(physical, "admin-status"),
                "oper_status": _status(physical, "oper-status"),
                "input_errors": _int(errors, "input-errors"),
                "output_errors": _int(output_errors, "output-errors"),
                "framing_errors": _int(errors, "framing-errors"),
                **_rates(physical),
            }

            for logical in physical.iter("logical-interface"):
                unit_name = _text(logical, "name")
                if not unit_name:
                    continue
                # Zadny admin/oper: extensive je pro unit nenese a dedit je
                # z rodice by byla fabulace - dodava je terse vetev.
                result[unit_name] = {
                    "input_errors": 0,
                    "output_errors": 0,
                    "framing_errors": 0,
                    **_rates(logical),
                }

        return result
