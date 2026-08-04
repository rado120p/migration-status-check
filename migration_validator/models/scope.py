"""Scope - filtr nad device-scoped fakty.

Scope neobsahuje zadna namerena data. Bez inventory existuje jediny device
scope s prazdnymi selektory, ktery propousti vse - diky tomu nemaji checky
zadnou vetev pro rezim bez inventory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEVICE_SCOPE_ID = "device"

FACT_AREAS = (
    "interfaces",
    "arp",
    "nd",
    "bgp",
    "evpn_vpws",
    "evpn_esi",
    "evpn_mac",
    "routes",
    "bfd",
)


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
    bgp_neighbors_inactive: list[str] = field(default_factory=list)
    local_ipv4: list[str] = field(default_factory=list)
    local_ipv6: list[str] = field(default_factory=list)
    virtual_gw_v4: list[str] = field(default_factory=list)
    virtual_gw_v6: list[str] = field(default_factory=list)
    vlans: list[str] = field(default_factory=list)
    bridge_domains: list[str] = field(default_factory=list)
    # Zamer z konfigurace. Slouzi zaroven jako filtr (vyber podle
    # (rib, prefix)) i jako mnozina, proti ktere check pozna, ze
    # nakonfigurovana routa v tabulce chybi.
    static_routes: list[dict[str, Any]] = field(default_factory=list)
    # Jen zamer. Session se vybiraji pres bgp_neighbors - jsou to dve
    # ruzne veci a slevat je do jednoho seznamu by znamenalo drzet je
    # v synchronu.
    bfd_peers: list[dict[str, Any]] = field(default_factory=list)

    def matches_interface(self, name: str) -> bool:
        return name in self.interfaces or name in self.physical_interfaces

    def to_dict(self) -> dict[str, Any]:
        return {
            "interfaces": list(self.interfaces),
            "physical_interfaces": list(self.physical_interfaces),
            "routing_instances": list(self.routing_instances),
            "bgp_neighbors": list(self.bgp_neighbors),
            "bgp_neighbors_inactive": list(self.bgp_neighbors_inactive),
            "local_ipv4": list(self.local_ipv4),
            "local_ipv6": list(self.local_ipv6),
            "virtual_gw_v4": list(self.virtual_gw_v4),
            "virtual_gw_v6": list(self.virtual_gw_v6),
            "vlans": list(self.vlans),
            "bridge_domains": list(self.bridge_domains),
            "static_routes": [dict(route) for route in self.static_routes],
            "bfd_peers": [dict(intent) for intent in self.bfd_peers],
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
    # Deaktivace neni selektor, je to vlastnost sluzby - proto tady, ne
    # v Selectors. Sluzba se pri deaktivaci z inventory nevypousti (docasne
    # deaktivovana sluzba se porad musi zmigrovat), takze priznak je jediny
    # zpusob, jak se to da poznat.
    routing_instance_active: bool = True
    interface_active: bool = True

    @property
    def is_device(self) -> bool:
        return self.kind == "device"

    @property
    def is_deactivated(self) -> bool:
        return not (self.routing_instance_active and self.interface_active)

    @property
    def deactivation_reason(self) -> str | None:
        """Kratky duvod do sloupce hodnot. None, kdyz je sluzba ziva."""
        reasons = []
        if not self.routing_instance_active:
            reasons.append("RI")
        if not self.interface_active:
            reasons.append("interface")
        return f"{' + '.join(reasons)} deactivated" if reasons else None

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
        # Deaktivovany peer patri scopu stejne jako aktivni. Kdyby se sem
        # jeho session nedostala, check by ho videl jako bezsessioveho,
        # dal by mu SKIP 'deaktivovan' - a rozpor 'konfigurace vypnuto,
        # zarizeni bezi' by z reportu zmizel.
        bgp = {
            peer: data
            for peer, data in (facts.get("bgp") or {}).items()
            if peer in self.selectors.bgp_neighbors
            or peer in self.selectors.bgp_neighbors_inactive
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
        wanted_routes = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in self.selectors.static_routes
        }
        routes = {}
        for table, prefixes in (facts.get("routes") or {}).items():
            selected_prefixes = {
                prefix: data
                for prefix, data in prefixes.items()
                if (table, prefix) in wanted_routes
            }
            # Prazdna tabulka se nevraci - v reportu by nic nerekla a
            # check by ji musel preskakovat.
            if selected_prefixes:
                routes[table] = selected_prefixes

        # Session patri scopu podle peeru, ne podle zameru: kdyby se
        # vybiralo podle bfd_peers, session peeru, ktereho parser do
        # zameru nedoplnil, by se sem nedostala a chyba v pruchodu
        # hierarchii by se schovala pred vystupem nastroje (AR-14).
        #
        # Na rozdil od bgp vys se tady bgp_neighbors_inactive zamerne
        # nepricita. checks/bfd.py o deaktivovanych peerech nevi - zamer si
        # bere z bfd_peers, ktery pro deaktivovaneho peera prazdny je -
        # takze vybranou session by vypsal jako WARN 'session existuje,
        # v konfiguraci sluzby neni'. To je nepravda: v konfiguraci sluzby
        # peer je, jen deaktivovany. BFD se na teto vlne zamerne nemenilo,
        # takze session zustava nezarazena a videt je v NEZARAZENO - viz
        # _unassigned_bfd_sessions v engine.py.
        bfd = {
            peer: data
            for peer, data in (facts.get("bfd") or {}).items()
            if peer in self.selectors.bgp_neighbors
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
            "routes": routes,
            "bfd": bfd,
            "ping": ping,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "key": self.key.to_dict() if self.key else None,
            "selectors": self.selectors.to_dict(),
            "routing_instance_active": self.routing_instance_active,
            "interface_active": self.interface_active,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scope":
        key = data.get("key")
        return cls(
            id=data["id"],
            kind=data["kind"],
            key=ScopeKey.from_dict(key) if key else None,
            selectors=Selectors.from_dict(data.get("selectors", {})),
            routing_instance_active=data.get("routing_instance_active", True),
            interface_active=data.get("interface_active", True),
        )


def _empty(area: str) -> Any:
    return [] if area in ("arp", "nd") else {}


def device_scope() -> Scope:
    """Jediny scope pro rezim bez inventory - propousti vsechna data."""
    return Scope(id=DEVICE_SCOPE_ID, kind="device", key=None, selectors=Selectors())
