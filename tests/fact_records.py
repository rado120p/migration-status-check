"""Stavitele zaznamu device faktu (schema 14) pro testy.

Device fakta bgp/bfd/evpn_esi/routes jsou od schematu 14 seznamy zaznamu,
ktere nesou celou svou identitu. Testy, ktere si fakta staveji rucne, je
maji stavet timhle modulem - tvar se pak meni na jednom miste.

`*_records(mapping)` prevadi stary tvar {klic: data} na seznam, aby prepis
existujiciho testu byl jen obaleni literalu.
"""

from __future__ import annotations

from typing import Any


def bgp_record(
    address: str,
    *,
    routing_instance: str | None = None,
    local_interface: str | None = None,
    state: str = "Established",
    peer_as: int | None = None,
    ribs: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    return {
        "address": address,
        "routing_instance": routing_instance,
        "local_interface": local_interface,
        "state": state,
        "peer_as": peer_as,
        "ribs": dict(ribs or {}),
    }


def bgp_records(mapping: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """{adresa: data} -> [zaznam]. Chybejici pole dostanou vychozi hodnotu."""
    return [
        {**bgp_record(address), **data, "address": address}
        for address, data in mapping.items()
    ]


def bfd_record(
    neighbor: str,
    *,
    interface: str | None = None,
    multihop: bool | None = None,
    state: str = "Up",
    clients: list[str] | None = None,
    multiplier: int | None = 3,
) -> dict[str, Any]:
    return {
        "neighbor": neighbor,
        "interface": interface,
        "multihop": (interface is None) if multihop is None else multihop,
        "state": state,
        "remote_state": "Up",
        "local_diagnostic": "None",
        "clients": list(clients if clients is not None else ["BGP"]),
        "detection_time": "0.900",
        "transmission_interval": "0.300",
        "multiplier": multiplier,
    }


def bfd_records(mapping: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """{soused: data} -> [zaznam]."""
    return [
        {**bfd_record(neighbor, interface=data.get("interface")), **data,
         "neighbor": neighbor,
         "multihop": data.get("multihop", data.get("interface") is None)}
        for neighbor, data in mapping.items()
    ]
