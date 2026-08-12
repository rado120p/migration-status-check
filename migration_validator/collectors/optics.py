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

Teplota: 'laser-temperature' na kazde lane NENI absentni - existuje, ale
je to formatovany text s atributem, napr.
<laser-temperature celsius="0">0 degrees C / 32 degrees F</laser-temperature>
(overeno na nahravce z labu, 16x). Cte se z atributu 'celsius' (cislo
primo, bez parsovani textu); text 'N degrees C / M degrees F' je jen
fallback pro pripad, ze by atribut na jine platforme/verzi chybel.
Modulova teplota (<module-temperature>, stejny tvar) se pouzije jen tehdy,
kdyz per-lane tag v XML vubec neni (bez-lane MX modul, kde se cte primo
z optics-diagnostics uzlu).
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
        # Zamerne tichy fallback: tag existuje, ale text neni cisty float
        # (napr. '0 degrees C / 32 degrees F' na temperature tazich - ty se
        # ctou zvlast pres _temperature_c, nikdy skrz _float). Format
        # nesouhlasi neni chyba parsovani, jen znak, ze cesta je spatna.
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


def _temperature_c(node: etree._Element, tag: str) -> float | None:
    """Cte '<tag celsius="N">N degrees C / M degrees F</tag>'.

    Atribut 'celsius' se preferuje (primo cislo, zadne parsovani textu).
    Regex na text je fallback pro pripad, ze by atribut na jine platforme
    nebo verzi Junosu chybel a v XML zustal jen formatovany text.
    """
    found = node.find(tag)
    if found is None:
        return None
    celsius = found.attrib.get("celsius")
    if celsius is not None:
        try:
            return float(celsius)
        except ValueError:
            pass
    text = found.text.strip() if found.text else None
    if text is None:
        return None
    match = _TEMPERATURE_RE.search(text)
    return float(match.group(1)) if match else None


def _module_temperature_c(diagnostics: etree._Element) -> float | None:
    return _temperature_c(diagnostics, "module-temperature")


def _lane(
    node: etree._Element, lane_index: str | None, module_temperature_c: float | None
) -> dict[str, Any]:
    rx = _float(node, "laser-rx-optical-power-dbm")
    if rx is None:
        rx = _float(node, "rx-signal-avg-optical-power-dbm")
    # Per-lane 'laser-temperature' ma prednost - realny fixture ho ma na
    # kazde lane (viz docstring modulu). Modulova teplota je fallback jen
    # pro bez-lane moduly / pripady, kdy per-lane tag v XML chybi.
    temperature = _temperature_c(node, "laser-temperature")
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
