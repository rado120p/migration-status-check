"""Vazba L3 casti sluzby (IRB) na jeji L2 tranzit v EVPN instanci.

Sluzba Internet/IPVPN na novem boxu ma dve casti: IRB scope (L3) a E-LAN
scope s tranzitnim rozhranim v teze mac-vrf instanci. Zdrojem vazby je
`l3_context` IRB rozhrani z faktu `evpn_instance` (faze 2.1): "master"
znamena inet.0 (Internet scope bez RI), jine jmeno je RI L3 scopu.
Prirazeni ke konkretnimu L2 scopu instance dela shoda unit-cisla IRB
s VLAN (selectors.vlans), pripadne s unitem tranzitniho rozhrani.

Vazba se pocita jen nad subject snapshotem - parovani baseline<->subject
zustava na matcheru a description. Nejednoznacnost (vic kandidatu) vazbu
nevytvori: spatny odkaz je horsi nez zadny.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from migration_validator.models.scope import Scope

L3_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})
L2_SERVICE_TYPE = "E-LAN"


@dataclass(frozen=True)
class ScopeLink:
    l3_scope_id: str
    l2_scope_id: str
    irb_interface: str  # irb.15
    l2_interface: str  # ae0.15
    l3_context: str  # jmeno RI, nebo "master" (= inet.0)
    l2_instance: str  # jmeno EVPN instance


def _unit(name: str) -> str | None:
    if "." not in name:
        return None
    return name.split(".", 1)[1]


def _matching_interface(scope: Scope, unit: str) -> str | None:
    """Rozhrani L2 scopu, jehoz unit-cislo sedi na IRB. None, kdyz shoda
    prisla jen pres selectors.vlans (trunk bez odpovidajiciho unitu)."""
    return next(
        (iface for iface in scope.selectors.interfaces if _unit(iface) == unit), None
    )


def _carries_unit(scope: Scope, unit: str) -> bool:
    if unit in scope.selectors.vlans:
        return True
    return _matching_interface(scope, unit) is not None


def _context_matches(scope: Scope, context: str) -> bool:
    instances = scope.selectors.routing_instances
    if context == "master":
        return not instances
    return instances == [context]


def link_scopes(
    scopes: list[Scope], evpn_instance: dict[str, Any]
) -> list[ScopeLink]:
    l3_by_interface: dict[str, list[Scope]] = {}
    for scope in scopes:
        if scope.is_device or scope.service_type not in L3_SERVICE_TYPES:
            continue
        for iface in scope.selectors.interfaces:
            l3_by_interface.setdefault(iface, []).append(scope)

    links: list[ScopeLink] = []
    linked_l3: set[str] = set()
    linked_l2: set[str] = set()
    for scope in scopes:
        if scope.is_device or scope.service_type != L2_SERVICE_TYPE:
            continue
        if not scope.selectors.routing_instances or not scope.selectors.interfaces:
            continue
        # [0]: builder emituje nejvyse jednu RI na scope, takze tenhle index
        # nikdy neztrati kandidaty - neni to vyber "prvni z vice moznych".
        instance = scope.selectors.routing_instances[0]
        data = evpn_instance.get(instance)
        if not data:
            continue
        for entry in data.get("irb_interfaces", {}).get("entries", []):
            irb_name = entry.get("name") or ""
            context = entry.get("l3_context")
            unit = _unit(irb_name)
            if not context or unit is None or not _carries_unit(scope, unit):
                continue
            candidates = [
                candidate
                for candidate in l3_by_interface.get(irb_name, ())
                if _context_matches(candidate, context)
            ]
            if len(candidates) != 1:
                continue
            l3_scope = candidates[0]
            if l3_scope.id in linked_l3 or scope.id in linked_l2:
                continue
            linked_l3.add(l3_scope.id)
            linked_l2.add(scope.id)
            l2_interface = (
                _matching_interface(scope, unit) or scope.selectors.interfaces[0]
            )
            links.append(
                ScopeLink(
                    l3_scope_id=l3_scope.id,
                    l2_scope_id=scope.id,
                    irb_interface=irb_name,
                    l2_interface=l2_interface,
                    l3_context=context,
                    l2_instance=instance,
                )
            )
    return links
