"""Model inventory sluzeb - vystup parseru konfigurace."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _as_mapping_list(value: Any) -> list[dict[str, Any]]:
    """Pole, jehoz prvky jsou mappingy - staticke routy a BFD zamer.

    Na rozdil od _as_list se prvky neprevadeji na retezec: ztratila by se
    struktura, ze ktere check bere identitu routy (rib, prefix) i hodnotu
    (next_hop). Nevalidni tvar je chyba, ne tichy prevod.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"ocekavan seznam mappingu, nalezeno {type(value).__name__}")

    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(
                f"ocekavan mapping v seznamu, nalezeno {type(item).__name__}"
            )
        items.append(dict(item))
    return items


@dataclass
class ServiceEntry:
    """Jeden zaznam z inventory - rozhrani a sluzba, ktera na nem bezi."""

    interface: str
    service_type: str
    description: str | None = None
    service_subtype: str | None = None
    ipv4_address: list[str] = field(default_factory=list)
    ipv6_address: list[str] = field(default_factory=list)
    virtual_gw_ipv4_address: list[str] = field(default_factory=list)
    virtual_gw_ipv6_address: list[str] = field(default_factory=list)
    routing_instance: str | None = None
    routing_instance_active: bool = True
    interface_active: bool = True
    protocol: list[str] = field(default_factory=list)
    bgp_neighbor: list[str] = field(default_factory=list)
    bridge_domain: list[str] = field(default_factory=list)
    customer_vlan: list[str] = field(default_factory=list)
    static_route: list[dict[str, Any]] = field(default_factory=list)
    bfd: list[dict[str, Any]] = field(default_factory=list)

    @property
    def physical_name(self) -> str:
        """Nazev fyzickeho rodice - cast pred teckou."""
        return self.interface.split(".", 1)[0]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServiceEntry":
        if "interface" not in data:
            raise ValueError("zaznam inventory nema klic 'interface'")
        if "service_type" not in data:
            raise ValueError(
                f"zaznam inventory pro {data['interface']} nema klic 'service_type'"
            )
        return cls(
            interface=str(data["interface"]),
            service_type=str(data["service_type"]),
            description=_as_optional_str(data.get("description")),
            service_subtype=_as_optional_str(data.get("service_subtype")),
            ipv4_address=_as_list(data.get("ipv4_address")),
            ipv6_address=_as_list(data.get("ipv6_address")),
            virtual_gw_ipv4_address=_as_list(data.get("virtual_gw_ipv4_address")),
            virtual_gw_ipv6_address=_as_list(data.get("virtual_gw_ipv6_address")),
            routing_instance=_as_optional_str(data.get("routing_instance")),
            routing_instance_active=bool(data.get("routing_instance_active", True)),
            interface_active=bool(data.get("interface_active", True)),
            protocol=_as_list(data.get("protocol")),
            bgp_neighbor=_as_list(data.get("bgp_neighbor")),
            bridge_domain=_as_list(data.get("bridge_domain")),
            customer_vlan=_as_list(data.get("customer_vlan")),
            static_route=_as_mapping_list(data.get("static_route")),
            bfd=_as_mapping_list(data.get("bfd")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": self.interface,
            "description": self.description,
            "service_type": self.service_type,
            "service_subtype": self.service_subtype,
            "ipv4_address": list(self.ipv4_address),
            "ipv6_address": list(self.ipv6_address),
            "virtual_gw_ipv4_address": list(self.virtual_gw_ipv4_address),
            "virtual_gw_ipv6_address": list(self.virtual_gw_ipv6_address),
            "routing_instance": self.routing_instance,
            "routing_instance_active": self.routing_instance_active,
            "interface_active": self.interface_active,
            "protocol": list(self.protocol),
            "bgp_neighbor": list(self.bgp_neighbor),
            "bridge_domain": list(self.bridge_domain),
            "customer_vlan": list(self.customer_vlan),
            "static_route": [dict(route) for route in self.static_route],
            "bfd": [dict(intent) for intent in self.bfd],
        }


@dataclass
class Inventory:
    device: str
    entries: list[ServiceEntry] = field(default_factory=list)


INVENTORY_SCHEMA_VERSION = 5


def load_inventory(path: str | Path) -> Inventory:
    """Nacte YAML vystup parseru konfigurace.

    Stara inventory se odmita, ne dopocitava. Pole adres se prejmenovala na
    rodiny; tolerantni cteni by u starsiho souboru tise vratilo sluzby bez
    adres, takze by neprobehl ping a sluzba by presto svitila zelene.

    Verze 3 pridala static_route a bfd. Tolerantni cteni ma tady stejnou
    cenu: sluzba by prisla bez zameru, takze by check nemel co porovnat
    s routovaci tabulkou a rozpor mezi konfiguraci a stavem by zmizel.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping, nalezeno {type(raw).__name__}")

    version = raw.get("schema_version")
    if version != INVENTORY_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: inventory ma schema_version {version}, nastroj umi "
            f"{INVENTORY_SCHEMA_VERSION} - vygeneruj ji znovu parserem"
        )

    if "interfaces" not in raw:
        raise ValueError(f"{path}: chybi klic 'interfaces'")
    entries = [ServiceEntry.from_dict(item) for item in raw["interfaces"] or []]
    return Inventory(device=str(raw.get("device", "")), entries=entries)
