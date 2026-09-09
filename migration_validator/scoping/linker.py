"""Vazba L3 casti sluzby (IRB) na jeji L2 tranzit v EVPN instanci.

Sluzba Internet/IPVPN na novem boxu ma dve casti: IRB scope (L3) a E-LAN
scope s tranzitnim rozhranim v teze mac-vrf instanci. Zdrojem vazby je
`l3_context` IRB rozhrani z faktu `evpn_instance` (faze 2.1): "master"
znamena inet.0 (Internet scope bez RI), jine jmeno je RI L3 scopu.
Prirazeni ke konkretnimu L2 scopu instance dela shoda unit-cisla IRB
s VLAN (selectors.vlans), pripadne s unitem tranzitniho rozhrani.

Vazba se pocita jen nad subject snapshotem - parovani baseline<->subject
zustava na matcheru a description. Nejednoznacnost (vic L3 kandidatu pro
jeden IRB) vazbu nevytvori: spatny odkaz je horsi nez zadny. Opacny smer
nejednoznacny neni: jeden IRB obsluhuje vsechny L2 scopy sve bridge
domain, vazba je tedy N L2 : 1 L3.

E-LAN `local` scope (access port v globalni bridge-domain/vlan, instance
`default-switch`) nema `evpn_instance` fakta - vaze se konfiguracne, pres
selektor `l2_interfaces` IRB scopu (`_link_local_scopes`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from migration_validator.models.scope import LOCAL_L2_INSTANCE, LOCAL_L2_SUBTYPE, Scope

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
    linked_l2: set[str] = set()
    for scope in scopes:
        if scope.is_device or scope.service_type != L2_SERVICE_TYPE:
            continue
        if scope.service_subtype == LOCAL_L2_SUBTYPE:
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
            # Jen L2 strana se vaze nejvys jednou - jeden L3 (IRB) legitimne
            # obsluhuje vsechny L2 scopy sve bridge domain (N L2 : 1 L3,
            # lab 172.20.20.4, BD-4094 se dvema access porty).
            if scope.id in linked_l2:
                continue
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
    links.extend(_link_local_scopes(scopes))
    return links


def _link_local_scopes(scopes: list[Scope]) -> list[ScopeLink]:
    """Vazba E-LAN `local` (globalni bridge-domain / vlan) na IRB.

    Zdrojem neni RPC vypis (default-switch zadny `l3_context` nema), ale
    inventory: IRB nese access porty svych domen v selektoru
    `l2_interfaces`. Pravidlo 'prave jeden kandidat' plati stejne jako
    u EVPN - spatny odkaz je horsi nez zadny (spec 2026-09-09).
    """
    l3_scopes = [
        scope
        for scope in scopes
        if not scope.is_device and scope.service_type in L3_SERVICE_TYPES
    ]
    links: list[ScopeLink] = []
    for scope in scopes:
        if scope.is_device or scope.service_type != L2_SERVICE_TYPE:
            continue
        if scope.service_subtype != LOCAL_L2_SUBTYPE or not scope.selectors.interfaces:
            continue
        l2_interface = scope.selectors.interfaces[0]
        candidates = [
            l3 for l3 in l3_scopes if l2_interface in l3.selectors.l2_interfaces
        ]
        if len(candidates) != 1:
            continue
        l3_scope = candidates[0]
        instances = l3_scope.selectors.routing_instances
        links.append(
            ScopeLink(
                l3_scope_id=l3_scope.id,
                l2_scope_id=scope.id,
                irb_interface=l3_scope.selectors.interfaces[0],
                l2_interface=l2_interface,
                l3_context=instances[0] if instances else "master",
                l2_instance=LOCAL_L2_INSTANCE,
            )
        )
    return links
