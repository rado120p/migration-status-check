"""Stavba scopu z inventory.

Zpusobilost pro scope je jina vrstva nez klasifikace rozhrani v checcich:
tady se rozhoduje "je to vubec migrovana sluzba", tam "ma smysl na tom merit".
"""

from __future__ import annotations

from collections import Counter

from migration_validator.models.inventory import Inventory, ServiceEntry
from migration_validator.models.scope import Scope, ScopeKey, Selectors

MIGRATED_SERVICE_TYPES = frozenset({"Internet", "IPVPN", "E-Line", "E-LAN", "Core"})

MANAGEMENT_PREFIXES = ("fxp", "em", "me", "vme", "bme", "re0:mgmt-", "re1:mgmt-")


def is_management(interface: str) -> bool:
    """Management rozhrani se nikdy nestane service scopem."""
    physical = interface.split(".", 1)[0]
    return physical.startswith(MANAGEMENT_PREFIXES)


def _key(entry: ServiceEntry) -> ScopeKey:
    return ScopeKey(
        description=entry.description,
        service_type=entry.service_type,
        service_subtype=entry.service_subtype,
    )


def _label(entry: ServiceEntry) -> str:
    return entry.description or entry.interface


def _is_eligible(entry: ServiceEntry) -> bool:
    return entry.service_type in MIGRATED_SERVICE_TYPES and not is_management(entry.interface)


def build_scopes(inventory: Inventory) -> list[Scope]:
    """Vytvori jeden scope pro kazdy zaznam migrovaneho typu sluzby.

    Zaznamy Layer1 se scopem nestanou - slouzi jen jako potvrzeni, ze
    rodicovske fyzicke rozhrani v inventory existuje, a doplni se do
    selektoru logicke jednotky.
    """
    physical_names = {
        entry.interface for entry in inventory.entries if entry.service_type == "Layer1"
    }
    eligible = [entry for entry in inventory.entries if _is_eligible(entry)]

    key_counts = Counter(_key(entry) for entry in eligible)

    scopes: list[Scope] = []
    for entry in eligible:
        key = _key(entry)
        scope_id = f"svc:{_label(entry)}:{entry.service_type}"
        if key_counts[key] > 1:
            scope_id = f"{scope_id}:{entry.interface}"

        parents = [entry.physical_name] if entry.physical_name in physical_names else []

        scopes.append(
            Scope(
                id=scope_id,
                kind="service",
                key=key,
                selectors=Selectors(
                    interfaces=[entry.interface],
                    physical_interfaces=parents,
                    routing_instances=(
                        [entry.routing_instance] if entry.routing_instance else []
                    ),
                    bgp_neighbors=list(entry.bgp_neighbor),
                    local_ipv4=list(entry.ipv4_address),
                    local_ipv6=list(entry.ipv6_address),
                    virtual_gw_v4=list(entry.virtual_gw_ipv4_address),
                    virtual_gw_v6=list(entry.virtual_gw_ipv6_address),
                    vlans=list(entry.customer_vlan),
                    bridge_domains=list(entry.bridge_domain),
                ),
            )
        )
    return scopes
