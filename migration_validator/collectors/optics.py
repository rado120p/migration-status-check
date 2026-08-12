"""Sber optickych urovni a alarmu - show interfaces diagnostics optics.

RPC jmeno 'get_interface_optics_diagnostics_information' overeno na labu
(PTX10002-36QDD, Junos 25.2R1.8-EVO) pres '| display xml rpc' - NEHADAT,
viz collectors/evpn.py. vMX vraci prazdny <interface-information/> (RPC
existuje, zadne physical-interface deti) - parse() pro nej vrati {}.

Dve podoby vypisu: moduly s lanes (optics-diagnostics-lane-values, ACX/EVO
vzdy, MX nekdy) a bez lanes (jen optics-diagnostics, MX). Bez-lane modul se
uklada jako jedna lane s lane=None. RX vykon ma na MX dve jmena
(laser-rx-optical-power-dbm vs rx-signal-avg-optical-power-dbm u
koherentnich modulu) - slevaji se tady, downstream vidi jen rx_power_dbm.
Flagy se porovnavaji case-insensitive ('off' MX vs 'Off' ACX/EVO).

Nepripojene porty na realnem zarizeni hlasi laser-rx/output-power-dbm jako
'-Inf' a prislusne alarmy jako 'On' - float("-Inf") se parsuje bez chyby a
zustava v datech tak, jak je (collector neinterpretuje, jen prenasi; co s
nekonecnem na reportu udelat resi az check v navaznem tasku).

Teplota nema na EVO per-lane cislo - 'laser-temperature' tam nese jen
prahove hodnoty (*-alarm-threshold), samotna teplota je jedna spolecna
hodnota za cely modul v <module-temperature> ve tvaru '0 degrees C /
32 degrees F' (overeno na nahravce z labu). _module_temperature_c z ni
vytahne prvni cislo pred 'degrees C' a sdili se pres vsechny lany stejneho
portu - to je fyzikalne spravne, teplomer v modulu je jeden.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.registry import register

ALARM_TAGS = (
    "laser-rx-power-high-alarm",
    "laser-rx-power-low-alarm",
    "rx-loss-of-signal-alarm",
    "laser-tx-power-high-alarm",
    "laser-tx-power-low-alarm",
    "tx-loss-of-signal-functionality-alarm",
    "tx-laser-disabled-alarm",
    "laser-temp-high-alarm",
    "laser-temp-low-alarm",
)

WARN_TAGS = (
    "laser-rx-power-high-warn",
    "laser-rx-power-low-warn",
    "laser-tx-power-high-warn",
    "laser-tx-power-low-warn",
    "laser-temp-high-warn",
    "laser-temp-low-warn",
)


def _text(node: etree._Element, path: str) -> str | None:
    found = node.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _float(node: etree._Element, path: str) -> float | None:
    value = _text(node, path)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _flags(node: etree._Element, tags: tuple[str, ...]) -> dict[str, bool]:
    """True = flag zvednuty. Tag, ktery v XML neni, se neuklada - absence
    neni totez co 'off' a check ji nema co posuzovat."""
    result: dict[str, bool] = {}
    for tag in tags:
        value = _text(node, tag)
        if value is not None:
            result[tag] = value.lower() != "off"
    return result


_TEMPERATURE_RE = re.compile(r"(-?[\d.]+)\s*degrees C")


def _module_temperature_c(diagnostics: etree._Element) -> float | None:
    """'<module-temperature>0 degrees C / 32 degrees F</module-temperature>'
    -> 0.0. Realny fixture nema per-lane teplotu (viz docstring modulu)."""
    text = _text(diagnostics, "module-temperature")
    if text is None:
        return None
    match = _TEMPERATURE_RE.search(text)
    return float(match.group(1)) if match else None


def _lane(
    node: etree._Element, lane_index: str | None, module_temperature_c: float | None
) -> dict[str, Any]:
    rx = _float(node, "laser-rx-optical-power-dbm")
    if rx is None:
        rx = _float(node, "rx-signal-avg-optical-power-dbm")
    # Bare 'laser-temperature' je hypoteticka per-lane hodnota (nekryta
    # zadnou nahravkou) - kdyz existuje, ma prednost pred module-temperature.
    temperature = _float(node, "laser-temperature")
    if temperature is None:
        temperature = module_temperature_c
    return {
        "lane": int(lane_index) if lane_index is not None else None,
        "rx_power_dbm": rx,
        "tx_power_dbm": _float(node, "laser-output-power-dbm"),
        "temperature_c": temperature,
        "alarms": _flags(node, ALARM_TAGS),
        "warnings": _flags(node, WARN_TAGS),
    }


@register
class OpticsCollector(Collector):
    name = "optics"

    def rpc_name(self, platform: str) -> str:
        return "get_interface_optics_diagnostics_information"

    def parse(
        self, xml: etree._Element, platform: str
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for physical in xml.iter("physical-interface"):
            name = _text(physical, "name")
            diagnostics = physical.find("optics-diagnostics")
            if not name or diagnostics is None:
                continue
            module_temperature_c = _module_temperature_c(diagnostics)
            lane_nodes = diagnostics.findall("optics-diagnostics-lane-values")
            if lane_nodes:
                lanes = [
                    _lane(node, _text(node, "lane-index"), module_temperature_c)
                    for node in lane_nodes
                ]
            else:
                lanes = [_lane(diagnostics, None, module_temperature_c)]
            result[name] = {"lanes": lanes}
        return result
