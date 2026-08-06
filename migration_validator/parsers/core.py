#!/usr/bin/env python3

"""
Spolecne jadro parseru sluzeb (MX Junos + ACX Junos OS Evolved).

Nacte konfiguraci zarizeni pres Junos PyEZ a vytvori YAML obsahujici
informace o sluzbach na zakaznickych rozhranich. Platformni rozdily
(VPLS detekce, EVPN E-LAN subtype, detekce EVPN instance) jsou v
podtridach `migration_validator.parsers.mx` a `migration_validator.parsers.evo`
- tady zije jen spolecne jadro.

Podporované typy služeb:

    L3:
        IPVPN
        Internet
        Core

    E-Line:
        ccc
        vpws

    E-LAN:
        vpls
        vlan-based
        vlan-bundle
        vlan-aware
        transparent
        evpn-unknown

Přihlášení:

    Výchozí:
        ansible + ~/.ssh/id_rsa

    Heslo:
        --auth password --username USER

    Vlastní SSH klíč:
        --auth key --username USER --key-file PATH
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml
from jnpr.junos import Device
from jnpr.junos.exception import (
    ConnectAuthError,
    ConnectError,
    ConnectRefusedError,
    ConnectTimeoutError,
    RpcError,
)
from lxml import etree

from migration_validator.models.inventory import INVENTORY_SCHEMA_VERSION


DEFAULT_USER = "ansible"
DEFAULT_KEY = str(Path.home() / ".ssh" / "id_rsa")
DEFAULT_PORT = 22
DEFAULT_TIMEOUT = 30

LOGGER = logging.getLogger("junos-service-parser")


# ---------------------------------------------------------------------------
# Datové modely
# ---------------------------------------------------------------------------


@dataclass
class ConnectionOptions:
    hostname: str
    username: str
    auth_type: str
    port: int = DEFAULT_PORT
    timeout: int = DEFAULT_TIMEOUT
    key_file: str | None = None
    password: str | None = None


@dataclass
class BridgeDomain:
    name: str
    vlan_ids: list[str] = field(default_factory=list)
    vlan_id_list: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    routing_interface: str | None = None

    @property
    def all_vlan_ids(self) -> list[str]:
        return unique(self.vlan_ids + self.vlan_id_list)


@dataclass
class RoutingInstance:
    name: str
    instance_type: str | None = None
    active: bool = True
    interfaces: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    bridge_domains: list[BridgeDomain] = field(default_factory=list)
    vlans: list[BridgeDomain] = field(default_factory=list)
    route_distinguisher: str | None = None
    vrf_targets: list[str] = field(default_factory=list)
    evpn_service_type: str | None = None
    instance_vlan_ids: list[str] = field(default_factory=list)
    bgp_neighbors: list[str] = field(default_factory=list)
    # Deaktivovaný soused se ze záměru nevypouští, jen se drží zvlášť.
    # Paralelní seznam, ne mapa: `bgp_neighbors` slouží jako **selektor**
    # (podle něj se k službě párují naměřené session), a přepis na mapu by
    # rozbil čtyři testy členství v enginu a ve scopu bez užitku.
    bgp_neighbors_inactive: list[str] = field(default_factory=list)
    bfd: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class InterfaceConfig:
    name: str
    physical_name: str
    description: str | None = None
    families: list[str] = field(default_factory=list)
    encapsulation: str | None = None
    vlan_ids: list[str] = field(default_factory=list)
    vlan_id_list: list[str] = field(default_factory=list)
    input_vlan_map: str | None = None
    output_vlan_map: str | None = None
    ipv4_addresses: list[str] = field(default_factory=list)
    ipv6_addresses: list[str] = field(default_factory=list)
    virtual_gw_ipv4_addresses: list[str] = field(default_factory=list)
    virtual_gw_ipv6_addresses: list[str] = field(default_factory=list)
    active: bool = True


@dataclass
class StaticRoute:
    """Jedna statická routa z konfigurace — záměr, ne stav routovací tabulky."""

    rib: str
    prefix: str
    next_hop: list[str] = field(default_factory=list)
    # Deaktivovaná routa se ze záměru **nevypouští**. Kdyby zmizela, check
    # by neměl co přeskočit a operátor by z reportu nepoznal, že v
    # konfiguraci vůbec je. Příznak se čte na listu, protože `_is_inactive`
    # chodí po předcích — pokryje tím deaktivaci na libovolné úrovni nad
    # routou, včetně celého `routing-options`.
    active: bool = True


@dataclass
class InterfaceService:
    interface: str
    description: str | None
    service_type: str
    service_subtype: str | None
    ipv4_address: list[str]
    ipv6_address: list[str]
    virtual_gw_ipv4_address: list[str]
    virtual_gw_ipv6_address: list[str]
    routing_instance: str | None
    protocol: list[str]
    routing_instance_active: bool = True
    interface_active: bool = True
    bgp_neighbor: list[str] = field(default_factory=list)
    bgp_neighbor_inactive: list[str] = field(default_factory=list)
    static_route: list[dict[str, Any]] = field(default_factory=list)
    bfd: list[dict[str, Any]] = field(default_factory=list)

    # Doplňující údaje pro další skripty.
    bridge_domain: list[str] = field(default_factory=list)
    customer_vlan: list[str] = field(default_factory=list)
    detection_confidence: str = "medium"
    detection_reason: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# XML pomocné funkce
# ---------------------------------------------------------------------------


def local_name(element: etree._Element) -> str:
    return etree.QName(element).localname


def first_text(
    node: etree._Element | None,
    xpath: str,
) -> str | None:
    if node is None:
        return None

    results = node.xpath(xpath)

    if not results:
        return None

    value = results[0]

    if isinstance(value, etree._Element):
        value = value.text

    if value is None:
        return None

    value = str(value).strip()

    return value or None


def all_texts(
    node: etree._Element | None,
    xpath: str,
) -> list[str]:
    if node is None:
        return []

    values: list[str] = []

    for result in node.xpath(xpath):
        if isinstance(result, etree._Element):
            value = result.text
        else:
            value = str(result)

        if value is None:
            continue

        value = value.strip()

        if value:
            values.append(value)

    return unique(values)


def child_names(
    node: etree._Element | None,
    xpath: str,
) -> list[str]:
    if node is None:
        return []

    names: list[str] = []

    for result in node.xpath(xpath):
        if isinstance(result, etree._Element):
            names.append(local_name(result))

    return unique(names)


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def rib_instance(rib: str) -> str | None:
    """Routing-instance ze jména RIB: 'L3VPN-A.inet6.0' -> 'L3VPN-A', 'inet.0' -> None.

    Globální tabulky patří default instanci, kterou inventory zapisuje jako
    None — stejně jako `routing_instance` služby. Díky tomu jde porovnávat
    přímo, bez zvláštní větve pro globální tabulku.
    """
    head = rib.rsplit(".", 2)[0] if rib.count(".") >= 2 else ""
    return head or None


def _bfd_values(
    node: etree._Element | None,
    source: str,
) -> dict[str, Any] | None:
    """Hodnoty jedné úrovně BFD. None znamená, že na této úrovni nic není."""
    if node is None:
        return None

    return {
        "minimum_interval": _optional_int(
            first_text(
                node,
                "./*[local-name()='minimum-interval']/text()",
            )
        ),
        "multiplier": _optional_int(
            first_text(
                node,
                "./*[local-name()='multiplier']/text()",
            )
        ),
        "source": source,
    }


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None

    return int(value)


def xpath_exists(
    node: etree._Element | None,
    xpath: str,
) -> bool:
    if node is None:
        return False

    return bool(node.xpath(xpath))


def normalize_vlan_values(values: Iterable[str]) -> list[str]:
    """
    Normalizuje hodnoty VLAN.

    Zachovává například:
        100
        100-200
        none
        all
    """

    normalized: list[str] = []

    for value in values:
        value = str(value).strip().lower()

        if not value:
            continue

        normalized.append(value)

    return unique(normalized)


def find_configuration_root(
    response: etree._Element,
) -> etree._Element:
    if local_name(response) == "configuration":
        return response

    nodes = response.xpath(
        ".//*[local-name()='configuration']"
    )

    if not nodes:
        raise RuntimeError(
            "V NETCONF odpovědi nebyl nalezen element configuration."
        )

    return nodes[0]


# ---------------------------------------------------------------------------
# Parser Junos konfigurace
# ---------------------------------------------------------------------------


class JunosServiceParserCore:
    """Spolecne jadro parseru - platformni metody viz podtridy mx/evo."""

    LOGGER_NAME = "junos-service-parser"

    def __init__(self, config_xml: etree._Element):
        self._logger = logging.getLogger(self.LOGGER_NAME)

        self.config_xml = config_xml

        self.routing_instances: dict[str, RoutingInstance] = {}
        self.interface_to_instances: dict[str, list[str]] = {}

        self.global_l2circuits: set[str] = set()
        self.global_ccc_interfaces: set[str] = set()

        self.global_protocols_by_interface: dict[str, set[str]] = {}
        self.default_bgp_neighbors: list[str] = []
        self.default_bgp_neighbors_inactive: list[str] = []
        self.default_bfd: dict[str, dict[str, Any]] = {}
        self.static_routes: list[StaticRoute] = []

    def parse(self) -> list[InterfaceService]:
        self._parse_routing_instances()
        self.static_routes = self._parse_static_routes()
        self._parse_default_bgp_neighbors()
        self._parse_global_l2circuits()
        self._parse_global_connections()

        interface_configs = self._parse_interfaces()
        interface_configs_by_name = {
            interface.name: interface
            for interface in interface_configs
        }

        services = [
            self._classify_interface(interface)
            for interface in interface_configs
        ]

        services = [
            service
            for service in services
            if service is not None
        ]

        self._assign_bgp_neighbors(
            services,
            interface_configs_by_name,
        )

        self._assign_static_routes(
            services,
            interface_configs_by_name,
        )

        self._assign_bfd(services)

        return sorted(
            services,
            key=lambda item: interface_sort_key(item.interface),
        )
    
    def _is_inactive(
        self,
        node: etree._Element,
    ) -> bool:
        """Deaktivace se dědí z kontejneru dolů.

        Junos označí atributem `inactive` jen ten uzel, na kterém se příkaz
        `deactivate` vykonal. `deactivate routing-instances` proto označí
        kontejner a jednotlivé `instance` pod ním zůstanou bez atributu —
        ale neplatí ani jedna z nich.

        Bez chození po předcích si obě úrovně odporují: deaktivovat jednu VRF
        statiky vypustí, deaktivovat všechny je nechá naživu. To je chyba za
        jakékoli politiky.
        """
        current: etree._Element | None = node

        while current is not None:
            if self._node_is_inactive(current):
                return True
            current = current.getparent()

        return False

    def _node_is_inactive(
        self,
        node: etree._Element,
    ) -> bool:
        # Klasický Junos XML formát:
        # <instance inactive="inactive">
        if (node.get("inactive") or "").lower() == "inactive":
            return True

        # Některé odpovědi mohou použít active="false".
        active_value = (node.get("active") or "").lower()

        if active_value in {"false", "inactive"}:
            return True

        # YANG metadata mohou mít namespaced atribut active="false".
        for attribute_name, attribute_value in node.attrib.items():
            if etree.QName(attribute_name).localname != "active":
                continue

            if str(attribute_value).lower() in {"false", "inactive"}:
                return True

        return False
    
    # ------------------------------------------------------------------
    # Routing instances
    # ------------------------------------------------------------------

    def _parse_routing_instances(self) -> None:
        nodes = self.config_xml.xpath(
            "./*[local-name()='routing-instances']"
            "/*[local-name()='instance']"
        )

        for node in nodes:
            instance_name = first_text(
                node,
                "./*[local-name()='name']/text()",
            )

            if not instance_name:
                continue

            instance_neighbors, instance_neighbors_inactive = (
                self._parse_bgp_neighbors(
                    node,
                    "./*[local-name()='protocols']"
                    "/*[local-name()='bgp']",
                )
            )

            instance = RoutingInstance(
                name=instance_name,
                active=not self._is_inactive(node),
                instance_type=(
                    first_text(
                        node,
                        "./*[local-name()='instance-type']/text()",
                    )
                    or ""
                ).strip().lower() or None,
                interfaces=unique(
                    all_texts(
                        node,
                        ".//*[local-name()='interface']"
                        "/*[local-name()='name']/text()"
                        " | .//*[local-name()='interface']/text()",
                    )
                ),
                protocols=child_names(
                    node,
                    "./*[local-name()='protocols']/*",
                ),
                bridge_domains=self._parse_bridge_domains(node),
                vlans=self._parse_vlans(node),
                route_distinguisher=self._parse_route_distinguisher(node),
                vrf_targets=self._parse_vrf_targets(node),
                evpn_service_type=self._parse_evpn_service_type(node),
                instance_vlan_ids=normalize_vlan_values(
                    all_texts(
                        node,
                        "./*[local-name()='vlan-id']/text()"
                        " | ./*[local-name()='vlan-id-list']/text()"
                        " | ./*[local-name()='vlan-id-list']"
                        "/*[local-name()='name']/text()",
                    )
                ),
                bgp_neighbors=instance_neighbors,
                bgp_neighbors_inactive=instance_neighbors_inactive,
                bfd=self._parse_bfd(
                    node,
                    "./*[local-name()='protocols']"
                    "/*[local-name()='bgp']",
                ),
            )

            self.routing_instances[instance_name] = instance

            for interface_name in instance.interfaces:
                self.interface_to_instances.setdefault(
                    interface_name,
                    [],
                ).append(instance_name)

            for bridge_domain in (
                instance.bridge_domains + instance.vlans
            ):
                for interface_name in bridge_domain.interfaces:
                    self.interface_to_instances.setdefault(
                        interface_name,
                        [],
                    ).append(instance_name)

    def _parse_route_distinguisher(
        self,
        node: etree._Element,
    ) -> str | None:
        return first_text(
            node,
            "./*[local-name()='route-distinguisher']"
            "/*[local-name()='rd-type']/text()"
            " | ./*[local-name()='route-distinguisher']/text()",
        )

    def _parse_vrf_targets(
        self,
        node: etree._Element,
    ) -> list[str]:
        return all_texts(
            node,
            "./*[local-name()='vrf-target']"
            "//*[local-name()='community']/text()"
            " | ./*[local-name()='vrf-target']/text()",
        )

    def _parse_evpn_service_type(
        self,
        node: etree._Element,
    ) -> str | None:
        """
        Na mac-vrf platformách může být explicitně nakonfigurováno:

            protocols {
                evpn {
                    encapsulation ...
                }
            }

        nebo například:

            service-type vlan-aware;
            service-type vlan-based;
        """

        value = first_text(
            node,
            ".//*[local-name()='evpn']"
            "/*[local-name()='service-type']/text()"
            " | ./*[local-name()='service-type']/text()",
        )

        if value:
            return value.lower()

        return None

    def _parse_bgp_neighbors(
        self,
        node: etree._Element,
        bgp_xpath: str,
    ) -> tuple[list[str], list[str]]:
        neighbors: list[str] = []
        inactive: list[str] = []

        for bgp_node in node.xpath(bgp_xpath):
            for neighbor_node in bgp_node.xpath(
                ".//*[local-name()='neighbor']"
            ):
                neighbor = first_text(
                    neighbor_node,
                    "./*[local-name()='name']/text()",
                ) or first_text(neighbor_node, "./text()")

                if not neighbor:
                    continue

                if self._is_inactive(neighbor_node):
                    inactive.append(neighbor)
                else:
                    neighbors.append(neighbor)

        return unique(neighbors), unique(inactive)

    def _parse_static_routes(self) -> list[StaticRoute]:
        """Statiky z globálních routing-options i ze všech routing-instances.

        Jméno RIB se normalizuje na tvar, jaký vrací `show route` v poli
        table-name. Konfigurace není mezi rodinami symetrická — IPv4 leží
        přímo pod routing-options/static, IPv6 pod
        routing-options/rib <jméno>.inet6.0/static — ale RPC ten rozdíl nezná.
        Parser ho proto zahladí tady a dál se nešíří.

        Deaktivovaný kontejner (`routing-options`, `rib` i `static`) route
        nepustí dál, stejně jako se už přeskakuje jednotlivá `route`
        a `instance` — `_is_inactive` chodí po předcích, takže i deaktivovaný
        `rib` chytí `_is_inactive(static_node)` o úroveň níž, ne skip na
        samotném `rib` uzlu. Bez toho by `deactivate` — standardní idiom pro
        vyřazení konfigurace při migraci — vyrobil živý záměr a check by
        hlásil `FAIL … neni v tabulce` za routu, kterou nikdo nechce.
        """

        routes: list[StaticRoute] = []

        for options_node in self.config_xml.xpath(
            "./*[local-name()='routing-options']"
        ):

            routes.extend(
                self._static_routes_under(options_node, None)
            )

        for instance_node in self.config_xml.xpath(
            "./*[local-name()='routing-instances']"
            "/*[local-name()='instance']"
        ):

            instance_name = first_text(
                instance_node,
                "./*[local-name()='name']/text()",
            )

            if not instance_name:
                continue

            for options_node in instance_node.xpath(
                "./*[local-name()='routing-options']"
            ):

                routes.extend(
                    self._static_routes_under(
                        options_node,
                        instance_name,
                    )
                )

        return routes

    def _static_routes_under(
        self,
        options_node: etree._Element,
        instance_name: str | None,
    ) -> list[StaticRoute]:
        """Statiky pod jedním routing-options, s odvozeným jménem RIB."""

        default_rib = (
            f"{instance_name}.inet.0"
            if instance_name
            else "inet.0"
        )

        containers: list[tuple[str, etree._Element]] = [
            (default_rib, static_node)
            for static_node in options_node.xpath(
                "./*[local-name()='static']"
            )
        ]

        for rib_node in options_node.xpath(
            "./*[local-name()='rib']"
        ):
            rib_name = first_text(
                rib_node,
                "./*[local-name()='name']/text()",
            )

            if not rib_name:
                continue

            containers.extend(
                (rib_name, static_node)
                for static_node in rib_node.xpath(
                    "./*[local-name()='static']"
                )
            )

        routes: list[StaticRoute] = []

        for rib_name, static_node in containers:

            for route_node in static_node.xpath(
                "./*[local-name()='route']"
            ):

                prefix = first_text(
                    route_node,
                    "./*[local-name()='name']/text()",
                )

                if not prefix:
                    continue

                routes.append(
                    StaticRoute(
                        rib=rib_name,
                        prefix=prefix,
                        # Jen holý next-hop. discard, reject a next-table
                        # adresu k porovnání se subnetem rozhraní nemají,
                        # takže se na službu nenamapují.
                        #
                        # qualified-next-hop ji naopak má — nese buď adresu,
                        # nebo interface-name — a do výčtu výš nepatří.
                        # Vynechává se vědomě a odloženě, ne proto, že by
                        # adresu neměl: routa směrovaná výhradně přes něj
                        # dostane prázdný next_hop, na službu se nenamapuje
                        # a nenainstalovaná zmizí beze stopy. Zapsáno jako
                        # otevřený bod roadmapy vlny 10.
                        next_hop=all_texts(
                            route_node,
                            "./*[local-name()='next-hop']/text()",
                        ),
                        active=not self._is_inactive(route_node),
                    )
                )

        return routes

    def _bfd_node(
        self,
        node: etree._Element | None,
    ) -> etree._Element | None:
        """Element bfd-liveness-detection přímo pod daným uzlem, bez sestupu.

        Deaktivovaná stanza se chová, jako by tam nebyla. `deactivate` je
        standardní junosí idiom pro vyřazení konfigurace při migraci, takže
        záměr z ní vzniknout nesmí — jinak by nástroj hlásil `FAIL … bez
        session` za ochranu, kterou operátor vědomě vypnul, a to je právě
        ten jeden výstup, který podrývá celou pointu porovnávání
        konfigurace se skutečností.

        Návrat None navíc pustí dědění o úroveň výš: soused
        s deaktivovaným BFD zdědí pravidlo skupiny, což je právě to, co
        udělá i Junos.
        """
        if node is None:
            return None

        found = node.xpath("./*[local-name()='bfd-liveness-detection']")

        if not found or self._is_inactive(found[0]):
            return None

        return found[0]

    def _parse_bfd(
        self,
        node: etree._Element,
        bgp_xpath: str,
    ) -> dict[str, dict[str, Any]]:
        """BFD podle peeru, s děděním neighbor > group > protocols bgp.

        Specifičtější úroveň přepisuje obecnější, a to **celou hodnotou**,
        ne položku po položce: soused s vlastním minimum-interval si
        nedědí multiplier ze skupiny.

        Junosí `inherit` tuhle hierarchii nerozbaluje — rozbaluje
        apply-groups, ne hierarchii protokolu. Ověřeno proti laborce
        2026-07-29, kdy skupina CPE14 nesla BFD a její sousedé ho neměli
        ani v konfiguraci stažené s `inherit`.
        """

        intents: dict[str, dict[str, Any]] = {}

        for bgp_node in node.xpath(bgp_xpath):
            protocol_level = _bfd_values(
                self._bfd_node(bgp_node),
                "bgp",
            )

            # Soused může viset přímo pod bgp i pod skupinou. Kontejnery
            # se procházejí zvlášť a jen o úroveň níž, aby se soused
            # ve skupině nezapočítal dvakrát.
            containers: list[
                tuple[etree._Element, dict[str, Any] | None]
            ] = [(bgp_node, protocol_level)]

            for group_node in bgp_node.xpath(
                "./*[local-name()='group']"
            ):
                containers.append(
                    (
                        group_node,
                        _bfd_values(
                            self._bfd_node(group_node),
                            "group",
                        )
                        or protocol_level,
                    )
                )

            for container, inherited in containers:
                for neighbor_node in container.xpath(
                    "./*[local-name()='neighbor']"
                ):
                    if self._is_inactive(neighbor_node):
                        continue

                    peer = first_text(
                        neighbor_node,
                        "./*[local-name()='name']/text()",
                    ) or first_text(neighbor_node, "./text()")

                    if not peer:
                        continue

                    values = _bfd_values(
                        self._bfd_node(neighbor_node),
                        "neighbor",
                    ) or inherited

                    if values is not None:
                        intents[peer] = {"peer": peer, **values}

        return intents

    def _parse_bridge_domains(
        self,
        instance_node: etree._Element,
    ) -> list[BridgeDomain]:
        container_nodes = instance_node.xpath(
            "./*[local-name()='bridge-domains']"
        )

        if not container_nodes:
            return []

        return self._parse_l2_domain_container(
            container_nodes[0]
        )

    def _parse_vlans(
        self,
        instance_node: etree._Element,
    ) -> list[BridgeDomain]:
        container_nodes = instance_node.xpath(
            "./*[local-name()='vlans']"
        )

        if not container_nodes:
            return []

        return self._parse_l2_domain_container(
            container_nodes[0]
        )

    def _parse_l2_domain_container(
        self,
        container: etree._Element,
    ) -> list[BridgeDomain]:
        domains: list[BridgeDomain] = []

        for domain_node in container:
            if not isinstance(domain_node.tag, str):
                continue

            domain_name = first_text(
                domain_node,
                "./*[local-name()='name']/text()",
            )

            if not domain_name:
                domain_name = local_name(domain_node)

            vlan_ids = normalize_vlan_values(
                all_texts(
                    domain_node,
                    "./*[local-name()='vlan-id']/text()"
                    " | ./*[local-name()='vlan-tags']"
                    "/*[local-name()='outer']/text()"
                    " | ./*[local-name()='vlan-tags']"
                    "/*[local-name()='inner']/text()",
                )
            )

            vlan_id_list = normalize_vlan_values(
                all_texts(
                    domain_node,
                    "./*[local-name()='vlan-id-list']/text()"
                    " | ./*[local-name()='vlan-id-list']"
                    "/*[local-name()='name']/text()"
                    " | ./*[local-name()='vlan-id-range']/text()",
                )
            )

            interfaces = all_texts(
                domain_node,
                "./*[local-name()='interface']"
                "/*[local-name()='name']/text()",
            )

            routing_interface = first_text(
                domain_node,
                "./*[local-name()='routing-interface']/text()",
            )

            domains.append(
                BridgeDomain(
                    name=domain_name,
                    vlan_ids=vlan_ids,
                    vlan_id_list=vlan_id_list,
                    interfaces=interfaces,
                    routing_interface=routing_interface,
                )
            )

        return domains

    # ------------------------------------------------------------------
    # Globální E-Line konfigurace
    # ------------------------------------------------------------------

    def _parse_default_bgp_neighbors(self) -> None:
        """
        Top-level BGP patří do default routing instance.

        Tyto neighbory později párujeme jen se službami
        klasifikovanými jako Internet.
        """

        (
            self.default_bgp_neighbors,
            self.default_bgp_neighbors_inactive,
        ) = self._parse_bgp_neighbors(
            self.config_xml,
            "./*[local-name()='protocols']"
            "/*[local-name()='bgp']",
        )
        self.default_bfd = self._parse_bfd(
            self.config_xml,
            "./*[local-name()='protocols']"
            "/*[local-name()='bgp']",
        )

    def _parse_global_l2circuits(self) -> None:
        """
        Typická konfigurace:

            protocols {
                l2circuit {
                    neighbor 192.0.2.1 {
                        interface ge-0/0/0.100 {
                            virtual-circuit-id 100;
                        }
                    }
                }
            }
        """

        interface_names = all_texts(
            self.config_xml,
            "./*[local-name()='protocols']"
            "/*[local-name()='l2circuit']"
            "//*[local-name()='interface']"
            "/*[local-name()='name']/text()",
        )

        self.global_l2circuits.update(interface_names)

        for interface_name in interface_names:
            self.global_protocols_by_interface.setdefault(
                interface_name,
                set(),
            ).add("l2circuit")

    def _parse_global_connections(self) -> None:
        """
        Typická CCC konfigurace:

            protocols {
                connections {
                    interface-switch CUSTOMER {
                        interface ge-0/0/0.100;
                        interface ge-0/0/1.100;
                    }
                }
            }
        """

        interface_names = all_texts(
            self.config_xml,
            "./*[local-name()='protocols']"
            "/*[local-name()='connections']"
            "//*[local-name()='interface']"
            "/*[local-name()='name']/text()",
        )

        self.global_ccc_interfaces.update(interface_names)

        for interface_name in interface_names:
            self.global_protocols_by_interface.setdefault(
                interface_name,
                set(),
            ).add("connections")

    # ------------------------------------------------------------------
    # Rozhraní
    # ------------------------------------------------------------------

    def _parse_interfaces(self) -> list[InterfaceConfig]:
        results: list[InterfaceConfig] = []

        nodes = self.config_xml.xpath(
            "./*[local-name()='interfaces']"
            "/*[local-name()='interface']"
        )

        for interface_node in nodes:
            physical_name = first_text(
                interface_node,
                "./*[local-name()='name']/text()",
            )

            if not physical_name:
                continue

            physical_description = first_text(
                interface_node,
                "./*[local-name()='description']/text()",
            )

            physical_encapsulation = first_text(
                interface_node,
                "./*[local-name()='encapsulation']/text()",
            )

            physical_inactive = self._is_inactive(interface_node)

            # Fyzické rozhraní přidáme vždy.
            results.append(
                self._build_interface_config(
                    physical_name=physical_name,
                    logical_name=physical_name,
                    physical_description=physical_description,
                    physical_encapsulation=physical_encapsulation,
                    node=interface_node,
                    active=not physical_inactive,
                )
            )

            # Potom přidáme jednotlivé logical units.
            unit_nodes = interface_node.xpath(
                "./*[local-name()='unit']"
            )

            for unit_node in unit_nodes:
                unit_number = first_text(
                    unit_node,
                    "./*[local-name()='name']/text()",
                )

                if unit_number is None:
                    continue

                logical_name = f"{physical_name}.{unit_number}"

                results.append(
                    self._build_interface_config(
                        physical_name=physical_name,
                        logical_name=logical_name,
                        physical_description=physical_description,
                        physical_encapsulation=physical_encapsulation,
                        node=unit_node,
                        # Jednotka pod deaktivovaným rodičem je deaktivovaná
                        # taky, i když sama atribut nemá - Junos to tak i
                        # vyhodnocuje.
                        active=not self._is_inactive(unit_node),
                    )
                )

        return results

    def _build_interface_config(
        self,
        physical_name: str,
        logical_name: str,
        physical_description: str | None,
        physical_encapsulation: str | None,
        node: etree._Element,
        active: bool = True,
    ) -> InterfaceConfig:
        unit_description = first_text(
            node,
            "./*[local-name()='description']/text()",
        )

        unit_encapsulation = first_text(
            node,
            "./*[local-name()='encapsulation']/text()",
        )

        families = child_names(
            node,
            "./*[local-name()='family']/*",
        )

        vlan_ids = normalize_vlan_values(
            all_texts(
                node,
                "./*[local-name()='vlan-id']/text()"
                " | ./*[local-name()='inner-vlan-id']/text()"
                " | ./*[local-name()='vlan-tags']"
                "/*[local-name()='outer']/text()"
                " | ./*[local-name()='vlan-tags']"
                "/*[local-name()='inner']/text()"
                " | ./*[local-name()='family']"
                "/*[local-name()='bridge']"
                "/*[local-name()='vlan-id']/text()"
                " | ./*[local-name()='family']"
                "/*[local-name()='ethernet-switching']"
                "/*[local-name()='vlan']"
                "/*[local-name()='members']/text()",
            )
        )

        vlan_id_list = normalize_vlan_values(
            all_texts(
                node,
                "./*[local-name()='vlan-id-list']/text()"
                " | ./*[local-name()='vlan-id-list']"
                "/*[local-name()='name']/text()"
                " | ./*[local-name()='family']"
                "/*[local-name()='bridge']"
                "/*[local-name()='vlan-id-list']/text()",
            )
        )

        ipv4_addresses = all_texts(
            node,
            "./*[local-name()='family']"
            "/*[local-name()='inet']"
            "/*[local-name()='address']"
            "/*[local-name()='name']/text()",
        )

        ipv6_addresses = all_texts(
            node,
            "./*[local-name()='family']"
            "/*[local-name()='inet6']"
            "/*[local-name()='address']"
            "/*[local-name()='name']/text()",
        )

        virtual_gw_ipv4_addresses = all_texts(
            node,
            "./*[local-name()='family']"
            "/*[local-name()='inet']"
            "/*[local-name()='address']"
            "/*[local-name()='virtual-gateway-address']/text()",
        )

        virtual_gw_ipv6_addresses = all_texts(
            node,
            "./*[local-name()='family']"
            "/*[local-name()='inet6']"
            "/*[local-name()='address']"
            "/*[local-name()='virtual-gateway-address']/text()",
        )

        input_vlan_map = first_text(
            node,
            "./*[local-name()='input-vlan-map']"
            "/*[local-name()='map-type']/text()",
        )

        output_vlan_map = first_text(
            node,
            "./*[local-name()='output-vlan-map']"
            "/*[local-name()='map-type']/text()",
        )

        return InterfaceConfig(
            name=logical_name,
            physical_name=physical_name,
            description=unit_description or physical_description,
            families=families,
            encapsulation=unit_encapsulation or physical_encapsulation,
            vlan_ids=vlan_ids,
            vlan_id_list=vlan_id_list,
            input_vlan_map=input_vlan_map,
            output_vlan_map=output_vlan_map,
            ipv4_addresses=unique(ipv4_addresses),
            ipv6_addresses=unique(ipv6_addresses),
            virtual_gw_ipv4_addresses=unique(virtual_gw_ipv4_addresses),
            virtual_gw_ipv6_addresses=unique(virtual_gw_ipv6_addresses),
            active=active,
        )

    # ------------------------------------------------------------------
    # Klasifikace
    # ------------------------------------------------------------------

    def _classify_interface(
        self,
        interface: InterfaceConfig,
    ) -> InterfaceService | None:
        instance = self._find_evpn_vpws_instance(interface)

        if instance is None:
            instance = self._find_best_instance(interface)
        protocols = self._collect_protocols(
            interface,
            instance,
        )

        bridge_domains = self._find_interface_bridge_domains(
            interface,
            instance,
        )

        customer_vlans = self._collect_customer_vlans(
            interface,
            bridge_domains,
        )

        service_type, service_subtype, confidence, reasons = (
            self._detect_service(
                interface=interface,
                instance=instance,
                protocols=protocols,
                bridge_domains=bridge_domains,
                customer_vlans=customer_vlans,
            )
        )

        if self._should_ignore_interface(
            interface,
            instance,
            service_type,
        ):
            return None

        return InterfaceService(
            interface=interface.name,
            description=interface.description,
            service_type=service_type,
            service_subtype=service_subtype,
            ipv4_address=interface.ipv4_addresses,
            ipv6_address=interface.ipv6_addresses,
            virtual_gw_ipv4_address=interface.virtual_gw_ipv4_addresses,
            virtual_gw_ipv6_address=interface.virtual_gw_ipv6_addresses,
            routing_instance=instance.name if instance else None,
            protocol=protocols,
            routing_instance_active=instance.active if instance else True,
            interface_active=interface.active,
            bridge_domain=[
                domain.name
                for domain in bridge_domains
            ],
            customer_vlan=customer_vlans,
            detection_confidence=confidence,
            detection_reason=reasons,
        )

    def _find_best_instance(
        self,
        interface: InterfaceConfig,
    ) -> RoutingInstance | None:
        names = unique(
            self.interface_to_instances.get(interface.name, [])
            + self.interface_to_instances.get(
                interface.physical_name,
                [],
            )
        )

        instances = [
            self.routing_instances[name]
            for name in names
            if name in self.routing_instances
        ]

        # Přímé hledání podle XML obsahu routing instance.
        if not instances:
            for instance_name, instance in self.routing_instances.items():
                if (
                    interface.name in instance.interfaces
                    or interface.physical_name in instance.interfaces
                ):
                    instances.append(instance)

        if not instances:
            return None

        priority = {
            "vrf": 100,
            "evpn-vpws": 95,
            "l2vpn": 90,
            "vpls": 80,
            "mac-vrf": 70,
            "virtual-switch": 60,
            "evpn": 50,
            "forwarding": 20,
        }

        return max(
            instances,
            key=lambda item: priority.get(
                (item.instance_type or "").strip().lower(),
                0,
            ),
        )

    def _find_evpn_vpws_instance(
        self,
        interface: InterfaceConfig,
    ) -> RoutingInstance | None:
        candidate_names = {
            interface.name,
            interface.physical_name,
        }

        instance_nodes = self.config_xml.xpath(
            "./*[local-name()='routing-instances']"
            "/*[local-name()='instance']"
        )

        for node in instance_nodes:
            instance_type = (
                first_text(
                    node,
                    "./*[local-name()='instance-type']/text()",
                )
                or ""
            ).strip().lower()

            if instance_type != "evpn-vpws":
                continue

            configured_interfaces = set(
                all_texts(
                    node,
                    ".//*[local-name()='interface']"
                    "/*[local-name()='name']/text()"
                    " | .//*[local-name()='interface']/text()",
                )
            )

            if candidate_names & configured_interfaces:
                instance_name = first_text(
                    node,
                    "./*[local-name()='name']/text()",
                )

                if instance_name in self.routing_instances:
                    return self.routing_instances[instance_name]

                return RoutingInstance(
                    name=instance_name or "unknown-evpn-vpws",
                    instance_type="evpn-vpws",
                    interfaces=list(configured_interfaces),
                )

        return None

    def _collect_protocols(
        self,
        interface: InterfaceConfig,
        instance: RoutingInstance | None,
    ) -> list[str]:
        protocols: list[str] = []

        protocols.extend(interface.families)

        protocols.extend(
            self.global_protocols_by_interface.get(
                interface.name,
                set(),
            )
        )

        protocols.extend(
            self.global_protocols_by_interface.get(
                interface.physical_name,
                set(),
            )
        )

        if instance:
            protocols.extend(instance.protocols)

            if instance.instance_type:
                protocols.append(instance.instance_type)

        return unique(protocols)

    def _assign_bgp_neighbors(
        self,
        services: list[InterfaceService],
        interface_configs_by_name: dict[str, InterfaceConfig],
    ) -> None:
        for service in services:
            if service.service_type not in {"Internet", "IPVPN"}:
                continue

            interface = interface_configs_by_name.get(service.interface)

            if interface is None:
                continue

            candidate_neighbors: list[str] = []
            candidate_inactive: list[str] = []

            if service.service_type == "Internet":
                candidate_neighbors = self.default_bgp_neighbors
                candidate_inactive = self.default_bgp_neighbors_inactive
            elif service.routing_instance:
                instance = self.routing_instances.get(
                    service.routing_instance
                )

                if instance:
                    candidate_neighbors = instance.bgp_neighbors
                    candidate_inactive = instance.bgp_neighbors_inactive

            matched_neighbors = [
                neighbor
                for neighbor in candidate_neighbors
                if self._bgp_neighbor_matches_interface(
                    neighbor,
                    interface,
                )
            ]

            matched_inactive = [
                neighbor
                for neighbor in candidate_inactive
                if self._bgp_neighbor_matches_interface(neighbor, interface)
            ]

            if matched_inactive:
                service.bgp_neighbor_inactive = unique(
                    service.bgp_neighbor_inactive + matched_inactive
                )

            if not matched_neighbors:
                continue

            service.bgp_neighbor = unique(
                service.bgp_neighbor + matched_neighbors
            )
            service.protocol = unique(service.protocol + ["bgp"])
            service.detection_reason.append(
                "BGP neighbor odpovídá subnetu rozhraní: "
                + ", ".join(service.bgp_neighbor)
            )

    def _bgp_neighbor_matches_interface(
        self,
        neighbor: str,
        interface: InterfaceConfig,
    ) -> bool:
        try:
            neighbor_ip = ipaddress.ip_address(neighbor)
        except ValueError:
            return False

        for address in interface.ipv4_addresses + interface.ipv6_addresses:
            try:
                interface_address = ipaddress.ip_interface(address)
            except ValueError:
                continue

            if (
                neighbor_ip.version == interface_address.version
                and neighbor_ip in interface_address.network
            ):
                return True

        return False

    def _assign_static_routes(
        self,
        services: list[InterfaceService],
        interface_configs_by_name: dict[str, InterfaceConfig],
    ) -> None:
        """Routa patří službě, která má next-hop ve svém subnetu a leží v téže RIB.

        Obě podmínky musí platit současně. Bez shody routing-instance by
        next-hop, který náhodou padne do subnetu rozhraní v jiné VRF, sedl
        na špatnou službu.

        Na rozdíl od `_assign_bgp_neighbors` tu není filtr na service_type:
        L2 rozhraní nemá IP adresu, takže se namatchovat nemůže, a filtr by
        byl duplikát podmínky, kterou už dělá shoda adres.
        """

        for service in services:
            interface = interface_configs_by_name.get(service.interface)

            if interface is None:
                continue

            matched = [
                route
                for route in self.static_routes
                if rib_instance(route.rib) == service.routing_instance
                and any(
                    self._bgp_neighbor_matches_interface(
                        next_hop,
                        interface,
                    )
                    for next_hop in route.next_hop
                )
            ]

            if not matched:
                continue

            service.static_route = [
                asdict(route)
                for route in matched
            ]
            service.detection_reason.append(
                "Statická routa odpovídá subnetu rozhraní: "
                + ", ".join(route.prefix for route in matched)
            )

    def _assign_bfd(
        self,
        services: list[InterfaceService],
    ) -> None:
        """BFD se připíná jen k peerům, které služba už má v bgp_neighbor.

        Musí běžet **až po** `_assign_bgp_neighbors` — dřív je seznam
        peerů prázdný a nebylo by co spárovat.

        Na rozdíl od `_assign_bgp_neighbors` tu není filtr na service_type.
        Služba bez routing-instance sahá do `self.default_bfd`, což je
        záměr z globálního `protocols bgp` — a to i tehdy, jde-li o Core
        nebo E-LAN. Nevadí to, protože `_assign_bgp_neighbors` plní
        `bgp_neighbor` jen u Internet a IPVPN, takže ostatním službám
        prázdný seznam ukončí iteraci hned na začátku. Je to podmínka,
        na které tahle metoda stojí, ne shoda náhod — kdyby se filtr
        v `_assign_bgp_neighbors` rozšířil, patří sem gate.
        """

        for service in services:
            if not service.bgp_neighbor:
                continue

            if service.routing_instance:
                instance = self.routing_instances.get(
                    service.routing_instance
                )
                intents = instance.bfd if instance else {}
            else:
                intents = self.default_bfd

            service.bfd = [
                intents[peer]
                for peer in service.bgp_neighbor
                if peer in intents
            ]

    def _find_interface_bridge_domains(
        self,
        interface: InterfaceConfig,
        instance: RoutingInstance | None,
    ) -> list[BridgeDomain]:
        if instance is None:
            return []

        all_domains = (
            instance.bridge_domains
            + instance.vlans
        )

        direct_matches = [
            domain
            for domain in all_domains
            if interface.name in domain.interfaces
            or interface.physical_name in domain.interfaces
        ]

        if direct_matches:
            return direct_matches

        # Některé konfigurace připojí rozhraní přímo pod instanci.
        # Pokud má instance právě jednu doménu, lze ji bezpečně přiřadit.
        if (
            interface.name in instance.interfaces
            or interface.physical_name in instance.interfaces
        ) and len(all_domains) == 1:
            return all_domains

        # U trunku lze domény porovnat podle VLAN.
        interface_vlans = set(
            interface.vlan_ids
            + interface.vlan_id_list
        )

        if interface_vlans:
            vlan_matches: list[BridgeDomain] = []

            for domain in all_domains:
                domain_vlans = set(domain.all_vlan_ids)

                if interface_vlans & domain_vlans:
                    vlan_matches.append(domain)

            return vlan_matches

        return []

    def _collect_customer_vlans(
        self,
        interface: InterfaceConfig,
        bridge_domains: list[BridgeDomain],
    ) -> list[str]:
        values: list[str] = []

        values.extend(interface.vlan_ids)
        values.extend(interface.vlan_id_list)

        for bridge_domain in bridge_domains:
            values.extend(bridge_domain.all_vlan_ids)

        return normalize_vlan_values(values)

    def _detect_service(
        self,
        interface: InterfaceConfig,
        instance: RoutingInstance | None,
        protocols: list[str],
        bridge_domains: list[BridgeDomain],
        customer_vlans: list[str],
    ) -> tuple[str, str | None, str, list[str]]:
        protocol_set = set(protocols)
        family_set = set(interface.families)

        reasons: list[str] = []

        instance_type = (
            instance.instance_type
            if instance
            else None
        )

        # --------------------------------------------------------------
        # IPVPN
        # --------------------------------------------------------------

        if instance_type == "vrf":
            reasons.append(
                "Rozhraní je přiřazeno do routing instance typu vrf."
            )

            return (
                "IPVPN",
                None,
                "high",
                reasons,
            )

        if instance and (
            instance.route_distinguisher
            or instance.vrf_targets
        ) and self._is_layer3(interface):
            reasons.append(
                "L3 rozhraní je v instanci s route distinguisherem "
                "nebo VRF targetem."
            )

            return (
                "IPVPN",
                None,
                "high",
                reasons,
            )

        # --------------------------------------------------------------
        # E-Line VPWS
        # --------------------------------------------------------------

        if (
            instance is not None
            and (instance.instance_type or "").lower() == "evpn-vpws"
        ):
            return (
                "E-Line",
                "vpws",
                "high",
                [
                    "Routing instance používá "
                    "instance-type evpn-vpws."
                ],
            )

        # --------------------------------------------------------------
        # E-Line CCC
        # --------------------------------------------------------------

        if (
            interface.name in self.global_ccc_interfaces
            or interface.physical_name in self.global_ccc_interfaces
            or "ccc" in family_set
            or "connections" in protocol_set
        ):
            reasons.append(
                "Rozhraní používá family ccc nebo protocols connections."
            )

            return (
                "E-Line",
                "ccc",
                "high",
                reasons,
            )

        # --------------------------------------------------------------
        # E-LAN VPLS
        # --------------------------------------------------------------

        vpls_reason = self._detect_vpls(instance_type, protocol_set, instance)

        if vpls_reason:
            reasons.append(vpls_reason)

            return (
                "E-LAN",
                "vpls",
                "high",
                reasons,
            )

        # --------------------------------------------------------------
        # E-LAN EVPN
        # --------------------------------------------------------------

        if self._is_evpn_instance(instance):
            subtype, subtype_confidence, subtype_reasons = (
                self._detect_evpn_elan_subtype(
                    interface=interface,
                    instance=instance,
                    bridge_domains=bridge_domains,
                    customer_vlans=customer_vlans,
                )
            )

            reasons.extend(subtype_reasons)

            return (
                "E-LAN",
                subtype,
                subtype_confidence,
                reasons,
            )

        # --------------------------------------------------------------
        # Core / uplink
        # --------------------------------------------------------------

        if {"iso", "mpls"} & family_set:
            reasons.append(
                "Rozhraní používá family iso nebo family mpls."
            )

            return (
                "Core",
                None,
                "high",
                reasons,
            )

        # --------------------------------------------------------------
        # Internet
        # --------------------------------------------------------------

        if self._is_layer3(interface):
            reasons.append(
                "Rozhraní má family inet/inet6 nebo IP adresu "
                "a není přiřazeno do zákaznické VRF."
            )

            return (
                "Internet",
                None,
                "medium",
                reasons,
            )

        # --------------------------------------------------------------
        # Layer 1
        # --------------------------------------------------------------

        if self._is_physical_layer1_port(interface, instance):
            reasons.append(
                "Jde o fyzické rozhraní bez logické servisní konfigurace."
            )

            return (
                "Layer1",
                "physical-port",
                "high",
                reasons,
            )
        
        # --------------------------------------------------------------
        # Nerozpoznané L2
        # --------------------------------------------------------------

        if self._is_layer2(interface):
            reasons.append(
                "Rozhraní je L2, ale konfigurace neobsahuje dostatek "
                "údajů pro určení konkrétní služby."
            )

            return (
                "Unknown",
                "layer2",
                "low",
                reasons,
            )

        return (
            "Unknown",
            None,
            "low",
            ["Nebyl nalezen jednoznačný servisní model."],
        )

    def _detect_vpls(
        self,
        instance_type: str | None,
        protocol_set: set[str],
        instance: RoutingInstance | None,
    ) -> str | None:
        """Platformni detekce VPLS - viz podtridy mx/evo.

        Vraci duvod (reason string) pokud jde o VPLS, jinak None.
        """

        raise NotImplementedError

    def _detect_evpn_elan_subtype(
        self,
        interface: InterfaceConfig,
        instance: RoutingInstance,
        bridge_domains: list[BridgeDomain],
        customer_vlans: list[str],
    ) -> tuple[str, str, list[str]]:
        """Platformni urceni EVPN E-LAN subtype - viz podtridy mx/evo."""

        raise NotImplementedError

    # ------------------------------------------------------------------
    # Pomocná klasifikační logika
    # ------------------------------------------------------------------

    def _is_physical_layer1_port(
        self,
        interface: InterfaceConfig,
        instance: RoutingInstance | None,
    ) -> bool:
        """
        Fyzický port bez logické servisní konfigurace.
        """

        if "." in interface.name:
            return False

        if instance is not None:
            return False

        if interface.ipv4_addresses or interface.ipv6_addresses:
            return False

        if interface.virtual_gw_ipv4_addresses or interface.virtual_gw_ipv6_addresses:
            return False

        if interface.families:
            return False

        if interface.name in self.global_l2circuits:
            return False

        if interface.name in self.global_ccc_interfaces:
            return False

        return True
    
    def _is_evpn_instance(
        self,
        instance: RoutingInstance | None,
    ) -> bool:
        """Platformni rozpoznani EVPN instance - viz podtridy mx/evo."""

        raise NotImplementedError

    def _is_layer3(
        self,
        interface: InterfaceConfig,
    ) -> bool:
        return bool(
            interface.ipv4_addresses
            or interface.ipv6_addresses
            or interface.virtual_gw_ipv4_addresses
            or interface.virtual_gw_ipv6_addresses
            or "inet" in interface.families
            or "inet6" in interface.families
        )

    def _is_layer2(
        self,
        interface: InterfaceConfig,
    ) -> bool:
        return bool(
            {
                "ccc",
                "bridge",
                "ethernet-switching",
                "vpls",
            }
            & set(interface.families)
        ) or bool(
            interface.encapsulation
            and any(
                marker in interface.encapsulation.lower()
                for marker in (
                    "ccc",
                    "vpls",
                    "bridge",
                    "ethernet",
                )
            )
        )

    def _contains_vlan_range_or_multiple(
        self,
        vlan_values: Iterable[str],
    ) -> bool:
        values = [
            value
            for value in normalize_vlan_values(vlan_values)
            if value not in {"none", "all"}
        ]

        if len(values) > 1:
            return True

        return any(
            "-" in value
            or "," in value
            or value.startswith("[")
            for value in values
        )

    def _has_transparent_evidence(
        self,
        interface: InterfaceConfig,
        bridge_domains: list[BridgeDomain],
        customer_vlans: list[str],
    ) -> bool:
        """
        Transparent není v každé síti definován stejně.

        Proto se používají pouze konkrétní konfigurační indicie:

            vlan-id none
            vlan-id-list all
            flexible-ethernet-services bez normalizační VLAN
            ethernet-ccc bez VLAN překladu

        Výsledek má záměrně confidence medium.
        """

        normalized_vlans = set(customer_vlans)

        if "none" in normalized_vlans:
            return True

        if "all" in normalized_vlans:
            return True

        for domain in bridge_domains:
            if "none" in domain.all_vlan_ids:
                return True

            if "all" in domain.all_vlan_ids:
                return True

        encapsulation = (
            interface.encapsulation or ""
        ).lower()

        no_vlan_translation = (
            interface.input_vlan_map is None
            and interface.output_vlan_map is None
        )

        no_normalization_vlan = not any(
            vlan not in {"none", "all"}
            for vlan in normalized_vlans
        )

        if (
            encapsulation
            in {
                "ethernet-bridge",
                "ethernet-vpls",
                "ethernet-ccc",
            }
            and no_vlan_translation
            and no_normalization_vlan
        ):
            return True

        return False

    def _should_ignore_interface(
        self,
        interface: InterfaceConfig,
        instance: RoutingInstance | None,
        service_type: str,
    ) -> bool:
        """
        Odstraní z výsledku skutečně prázdná rozhraní.

        Neodstraňuje rozhraní jen proto, že je služba Unknown.
        """

        return bool(
            interface.description is None
            and not interface.families
            and interface.encapsulation is None
            and not interface.vlan_ids
            and not interface.vlan_id_list
            and not interface.ipv4_addresses
            and not interface.ipv6_addresses
            and not interface.virtual_gw_ipv4_addresses
            and not interface.virtual_gw_ipv6_addresses
            and instance is None
            and service_type == "Unknown"
        )


# ---------------------------------------------------------------------------
# Připojení
# ---------------------------------------------------------------------------


def build_device(
    options: ConnectionOptions,
) -> Device:
    arguments: dict[str, Any] = {
        "host": options.hostname,
        "user": options.username,
        "port": options.port,
        "gather_facts": False,
        "auto_probe": options.timeout,
    }

    if options.auth_type == "key":
        if not options.key_file:
            raise ValueError(
                "Nebyla zadána cesta k privátnímu SSH klíči."
            )

        key_path = Path(
            options.key_file
        ).expanduser()

        if not key_path.is_file():
            raise FileNotFoundError(
                f"SSH klíč neexistuje: {key_path}"
            )

        arguments["ssh_private_key_file"] = str(key_path)

        # U SSH klíče je passwd použito jako passphrase.
        if options.password:
            arguments["passwd"] = options.password

    elif options.auth_type == "password":
        if not options.password:
            raise ValueError(
                "Pro přihlášení heslem nebylo zadáno heslo."
            )

        arguments["passwd"] = options.password

    else:
        raise ValueError(
            f"Nepodporovaný typ autentizace: {options.auth_type}"
        )

    return Device(**arguments)


def retrieve_configuration(
    device: Device,
) -> etree._Element:
    """
    Načte konfiguraci potřebnou pro klasifikaci služeb.

    Použit je jeden filtr, aby parser viděl vazby mezi:
        interfaces
        routing-options
        routing-instances
        protocols
        bridge-domains
        vlans
    """

    config_filter = etree.XML(
        b"""
        <configuration>
            <interfaces/>
            <routing-options/>
            <routing-instances/>
            <protocols/>
            <bridge-domains/>
            <vlans/>
            <switch-options/>
        </configuration>
        """
    )

    response = device.rpc.get_config(
        filter_xml=config_filter,
        options={
            "database": "committed",
            "inherit": "",
        },
    )

    return find_configuration_root(response)


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------


def create_yaml_data(
    hostname: str,
    services: list[InterfaceService],
) -> dict[str, Any]:
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "device": hostname,
        "interfaces": [
            clean_service_dict(asdict(service))
            for service in services
        ],
    }


def clean_service_dict(
    data: dict[str, Any],
) -> dict[str, Any]:
    """
    service_subtype zůstane v YAML i s null hodnotou,
    aby měly další skripty stabilní datovou strukturu.
    """

    ordered_keys = (
        "interface",
        "description",
        "service_type",
        "service_subtype",
        "ipv4_address",
        "ipv6_address",
        "virtual_gw_ipv4_address",
        "virtual_gw_ipv6_address",
        "routing_instance",
        "routing_instance_active",
        "interface_active",
        "protocol",
        "bgp_neighbor",
        "bgp_neighbor_inactive",
        "bridge_domain",
        "customer_vlan",
        "static_route",
        "bfd",
        "detection_confidence",
        "detection_reason",
    )

    return {
        key: data.get(key)
        for key in ordered_keys
    }


def write_yaml(
    data: dict[str, Any],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        yaml.safe_dump(
            data,
            file,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=120,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Načte Junos konfiguraci přes PyEZ "
            "a vytvoří YAML inventář služeb."
        ),
    )

    parser.add_argument(
        "hostname",
        help="Hostname nebo IP adresa Junos zařízení.",
    )

    parser.add_argument(
        "--auth",
        choices=("key", "password"),
        default="key",
        help=(
            "Způsob autentizace. Výchozí je SSH klíč."
        ),
    )

    parser.add_argument(
        "-u",
        "--username",
        default=None,
        help=(
            "SSH uživatel. Výchozí pro key režim je "
            f"{DEFAULT_USER!r}."
        ),
    )

    parser.add_argument(
        "-k",
        "--key-file",
        default=DEFAULT_KEY,
        help=(
            "Privátní SSH klíč. Výchozí: "
            f"{DEFAULT_KEY}"
        ),
    )

    parser.add_argument(
        "--ask-key-passphrase",
        action="store_true",
        help="Vyžádá passphrase privátního SSH klíče.",
    )

    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"NETCONF SSH port. Výchozí: {DEFAULT_PORT}.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=(
            "Timeout připojení v sekundách. "
            f"Výchozí: {DEFAULT_TIMEOUT}."
        ),
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Výstupní YAML soubor. Výchozí: "
            "<hostname>.yml"
        ),
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Zapne podrobné logování.",
    )

    return parser.parse_args(argv)


def resolve_connection_options(
    args: argparse.Namespace,
) -> ConnectionOptions:
    if args.auth == "password":
        username = args.username

        if not username:
            username = input(
                "SSH username: "
            ).strip()

        if not username:
            raise ValueError(
                "SSH username nesmí být prázdný."
            )

        password = getpass.getpass(
            f"SSH password pro "
            f"{username}@{args.hostname}: "
        )

        if not password:
            raise ValueError(
                "SSH password nesmí být prázdný."
            )

        return ConnectionOptions(
            hostname=args.hostname,
            username=username,
            auth_type="password",
            port=args.port,
            timeout=args.timeout,
            password=password,
        )

    username = args.username or DEFAULT_USER

    key_passphrase: str | None = None

    if args.ask_key_passphrase:
        key_passphrase = getpass.getpass(
            f"Passphrase SSH klíče pro "
            f"{username}@{args.hostname}: "
        )

    return ConnectionOptions(
        hostname=args.hostname,
        username=username,
        auth_type="key",
        port=args.port,
        timeout=args.timeout,
        key_file=str(
            Path(args.key_file).expanduser()
        ),
        password=key_passphrase,
    )


def configure_logging(
    debug: bool,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | %(message)s"
        ),
    )


def interface_sort_key(
    interface_name: str,
) -> tuple[Any, ...]:
    parts = re.split(
        r"([0-9]+)",
        interface_name,
    )

    return tuple(
        int(part) if part.isdigit() else part
        for part in parts
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None, *, parser_cls: type | None = None) -> int:
    if parser_cls is None:
        raise ValueError(
            "parser_cls neni zadana - pouzij platformni wrapper "
            "(mx_parser.py / evo_parser.py) nebo predej "
            "parser_cls=JunosServiceParser / JunosEvoAcxServiceParser."
        )

    args = parse_arguments(argv)
    configure_logging(args.debug)

    device: Device | None = None

    try:
        options = resolve_connection_options(args)

        device = build_device(options)

        LOGGER.info(
            "Připojuji se k %s jako %s.",
            options.hostname,
            options.username,
        )

        device.open()

        LOGGER.info(
            "Spojení navázáno, načítám konfiguraci."
        )

        config_xml = retrieve_configuration(device)

        parser = parser_cls(config_xml)
        services = parser.parse()

        output_path = args.output

        if output_path is None:
            safe_hostname = re.sub(
                r"[^a-zA-Z0-9_.-]",
                "_",
                args.hostname,
            )

            output_path = Path(
                f"{safe_hostname}.yml"
            )

        yaml_data = create_yaml_data(
            hostname=args.hostname,
            services=services,
        )

        write_yaml(
            data=yaml_data,
            output_path=output_path,
        )

        LOGGER.info(
            "Nalezeno rozhraní: %d.",
            len(services),
        )

        LOGGER.info(
            "Výstup uložen do %s.",
            output_path.resolve(),
        )

        return 0

    except KeyboardInterrupt:
        LOGGER.error(
            "Operace byla přerušena uživatelem."
        )

        return 130

    except ConnectAuthError:
        LOGGER.error(
            "Přihlášení selhalo. Zkontroluj uživatele, "
            "heslo nebo SSH klíč."
        )

        return 2

    except ConnectTimeoutError:
        LOGGER.error(
            "Vypršel časový limit připojení."
        )

        return 3

    except ConnectRefusedError:
        LOGGER.error(
            "NETCONF spojení bylo odmítnuto. "
            "Zkontroluj konfiguraci system services netconf ssh."
        )

        return 4

    except ConnectError as error:
        LOGGER.error(
            "Chyba připojení: %s",
            error,
        )

        return 5

    except RpcError as error:
        LOGGER.error(
            "Chyba Junos RPC: %s",
            error,
        )

        return 6

    except (
        ValueError,
        FileNotFoundError,
        RuntimeError,
        OSError,
        etree.XMLSyntaxError,
    ) as error:
        LOGGER.error(
            "%s",
            error,
        )

        return 7

    except Exception:
        LOGGER.exception(
            "Neočekávaná chyba."
        )

        return 99

    finally:
        if device is not None and device.connected:
            device.close()

            LOGGER.info(
                "Spojení bylo ukončeno."
            )


if __name__ == "__main__":
    sys.exit(main())
