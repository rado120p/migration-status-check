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
