"""Sber stavu BFD session.

Collector nerozhoduje, jestli je chybejici session problem - to zavisi na
tom, jestli je BFD vubec nakonfigurovane a jestli bezi BGP, a oboji vi az
check.

Pouziva detail variantu: strucny vypis nema ani bfd-client, ani
remote-state, a bez klienta nejde odlisit session drzenou BGP od jine.

Prazdny vypis je platny stav. Stav laborky k 2026-07-31: obe nahravky
(junos i junos-evo) nesou po dvou session, takze prazdny vypis uz neni
pokryty nahravkou - testuje se na syntetickem XML v tests/collectors/test_bfd.py.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _int, _text
from migration_validator.collectors.registry import register


@register
class BfdCollector(Collector):
    name = "bfd"

    def rpc_name(self, platform: str) -> str:
        return "get_bfd_session_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"detail": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        sessions: dict[str, dict[str, Any]] = {}

        for node in xml.iter("bfd-session"):
            neighbor = _text(node, "session-neighbor")
            if not neighbor:
                continue

            clients = []
            for client in node.iter("bfd-client"):
                name = _text(client, "client-name")
                if name:
                    clients.append(name)

            sessions[neighbor] = {
                "state": _text(node, "session-state") or "unknown",
                "interface": _text(node, "session-interface"),
                "remote_state": _text(node, "remote-state"),
                "local_diagnostic": _text(node, "local-diagnostic"),
                "clients": clients,
                "detection_time": _text(node, "session-detection-time"),
                "transmission_interval": _text(node, "session-transmission-interval"),
                "multiplier": _int(node, "session-adaptive-multiplier"),
            }

        return sessions
