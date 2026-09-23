"""Spolecne tvary raw zaznamu - list modul bez tezkych importu.

Pouziva ho recorder, bundle, replay i probes/ping (NotRecorded). Nesmi
importovat parsery ani capture - ping by jinak tahal cely parser a hrozil
by cyklicky import.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from lxml import etree

NOT_RECORDED = "neni v raw zaznamu"

# Produkcni `interfaces extensive` muze mit obri textove uzly.
_PARSER = etree.XMLParser(huge_tree=True)


class NotRecorded(Exception):
    """Volani neni v raw zaznamu. Nikdy se nevydava za vysledek: collector
    z nej udela status error (checky SKIP), ping sent=0 (SKIP)."""


def canonical_kwargs(rpc: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """JSON-bezpecne argumenty pro parovani nahravky s replayem.

    `filter_xml` u get_config se vynecha - nese ho config_filter() jako
    seznam hierarchii. Ostatni lxml elementy se serializuji na text.
    """
    out: dict[str, Any] = {}
    for key in sorted(kwargs):
        value = kwargs[key]
        if rpc == "get_config" and key == "filter_xml":
            continue
        if isinstance(value, etree._Element):
            value = etree.tostring(value, encoding="unicode")
        out[key] = value
    return out


def config_filter(rpc: str, kwargs: dict[str, Any]) -> list[str] | None:
    """Top-level hierarchie filtru get_config v poradi, jinak None.

    Junos prazdnou hierarchii v odpovedi vynecha - rozlisit "zadano
    a prazdne" od "nikdy nezadano" jde jen podle filtru.
    """
    if rpc != "get_config":
        return None
    element = kwargs.get("filter_xml")
    if element is None:
        return None
    return [
        etree.QName(child).localname
        for child in element
        if isinstance(child.tag, str)
    ]


def call_key(rpc: str, kwargs: dict[str, Any]) -> str:
    """Klic pro parovani volani; kwargs uz musi byt kanonicke."""
    return f"{rpc} {json.dumps(kwargs, sort_keys=True, default=str)}"


@dataclass
class RecordedCall:
    seq: int
    rpc: str
    kwargs: dict[str, Any]
    filter: list[str] | None = None
    reply_xml: bytes | None = None
    reply_value: Any = None
    error: dict[str, str] | None = None

    def reply(self) -> Any:
        """Cerstve naparsovana odpoved - kazde podani je nezavisla kopie."""
        if self.reply_xml is not None:
            return etree.fromstring(self.reply_xml, _PARSER)
        return self.reply_value
