"""Scope - filtr nad device-scoped fakty.

Scope neobsahuje zadna namerena data. Bez inventory existuje jediny device
scope s prazdnymi selektory, ktery propousti vse - diky tomu nemaji checky
zadnou vetev pro rezim bez inventory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEVICE_SCOPE_ID = "device"

FACT_AREAS = ("interfaces", "arp", "nd", "bgp", "evpn_vpws", "evpn_esi", "evpn_mac")


@dataclass(frozen=True)
class ScopeKey:
    description: str | None
    service_type: str
    service_subtype: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "service_type": self.service_type,
            "service_subtype": self.service_subtype,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScopeKey":
        return cls(
            description=data.get("description"),
            service_type=data["service_type"],
            service_subtype=data.get("service_subtype"),
        )


@dataclass
class Selectors:
    interfaces: list[str] = field(default_factory=list)
    physical_interfaces: list[str] = field(default_factory=list)
    routing_instances: list[str] = field(default_factory=list)
    bgp_neighbors: list[str] = field(default_factory=list)
    local_ipv4: list[str] = field(default_factory=list)
    local_ipv6: list[str] = field(default_factory=list)
    virtual_gw_v4: list[str] = field(default_factory=list)
    virtual_gw_v6: list[str] = field(default_factory=list)
    vlans: list[str] = field(default_factory=list)
    bridge_domains: list[str] = field(default_factory=list)

    def matches_interface(self, name: str) -> bool:
        return name in self.interfaces or name in self.physical_interfaces

    def to_dict(self) -> dict[str, Any]:
        return {
            "interfaces": list(self.interfaces),
            "physical_interfaces": list(self.physical_interfaces),
            "routing_instances": list(self.routing_instances),
            "bgp_neighbors": list(self.bgp_neighbors),
            "local_ipv4": list(self.local_ipv4),
            "local_ipv6": list(self.local_ipv6),
            "virtual_gw_v4": list(self.virtual_gw_v4),
            "virtual_gw_v6": list(self.virtual_gw_v6),
            "vlans": list(self.vlans),
            "bridge_domains": list(self.bridge_domains),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Selectors":
        return cls(**{name: list(data.get(name, [])) for name in cls().to_dict()})


@dataclass
class Scope:
    id: str
    kind: str  # service | device
    key: ScopeKey | None
    selectors: Selectors

    @property
    def is_device(self) -> bool:
        return self.kind == "device"

    @property
    def service_type(self) -> str | None:
        return self.key.service_type if self.key else None

    def select(
        self, facts: dict[str, Any], probes: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Vybere z device-scoped faktu jen to, co patri tomuto scope."""
        probes = probes or {}
        pings = list(probes.get("ping", []))

        if self.is_device:
            selected: dict[str, Any] = {area: facts.get(area, _empty(area)) for area in FACT_AREAS}
            selected["ping"] = pings
            return selected

        interfaces = {
            name: data
            for name, data in (facts.get("interfaces") or {}).items()
            if self.selectors.matches_interface(name)
        }
        arp = [
            entry
            for entry in (facts.get("arp") or [])
            if self.selectors.matches_interface(str(entry.get("interface", "")))
        ]
        nd = [
            entry
            for entry in (facts.get("nd") or [])
            if self.selectors.matches_interface(str(entry.get("interface", "")))
        ]
        bgp = {
            peer: data
            for peer, data in (facts.get("bgp") or {}).items()
            if peer in self.selectors.bgp_neighbors
        }
        evpn_vpws = {
            name: data
            for name, data in (facts.get("evpn_vpws") or {}).items()
            if name in self.selectors.routing_instances
        }
        evpn_esi = {
            esi: data
            for esi, data in (facts.get("evpn_esi") or {}).items()
            if self.selectors.matches_interface(str(data.get("interface", "")))
        }
        evpn_mac = {
            name: data
            for name, data in (facts.get("evpn_mac") or {}).items()
            if name in self.selectors.routing_instances
        }
        ping = [probe for probe in pings if probe.get("scope_id") == self.id]

        return {
            "interfaces": interfaces,
            "arp": arp,
            "nd": nd,
            "bgp": bgp,
            "evpn_vpws": evpn_vpws,
            "evpn_esi": evpn_esi,
            "evpn_mac": evpn_mac,
            "ping": ping,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "key": self.key.to_dict() if self.key else None,
            "selectors": self.selectors.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scope":
        key = data.get("key")
        return cls(
            id=data["id"],
            kind=data["kind"],
            key=ScopeKey.from_dict(key) if key else None,
            selectors=Selectors.from_dict(data.get("selectors", {})),
        )


def _empty(area: str) -> Any:
    return [] if area in ("arp", "nd") else {}


def device_scope() -> Scope:
    """Jediny scope pro rezim bez inventory - propousti vsechna data."""
    return Scope(id=DEVICE_SCOPE_ID, kind="device", key=None, selectors=Selectors())
