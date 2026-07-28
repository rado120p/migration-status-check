#!/usr/bin/env python3

"""
Junos OS Evolved ACX service parser.

Načte konfiguraci ACX zařízení s Junos OS Evolved přes Junos PyEZ a vytvoří YAML
obsahující informace o službách na zákaznických rozhraních.

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


DEFAULT_USER = "ansible"
DEFAULT_KEY = str(Path.home() / ".ssh" / "id_rsa")
DEFAULT_PORT = 22
DEFAULT_TIMEOUT = 30

LOGGER = logging.getLogger("junos-evo-acx-service-parser")


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
    ip_addresses: list[str] = field(default_factory=list)
    virtual_gw_ip_addresses: list[str] = field(default_factory=list)


@dataclass
class InterfaceService:
    interface: str
    description: str | None
    service_type: str
    service_subtype: str | None
    ip_address: list[str]
    virtual_gw_ip_address: list[str]
    routing_instance: str | None
    protocol: list[str]
    active: bool = True
    bgp_neighbor: list[str] = field(default_factory=list)

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


class JunosEvoAcxServiceParser:
    def __init__(self, config_xml: etree._Element):
        self.config_xml = config_xml

        self.routing_instances: dict[str, RoutingInstance] = {}
        self.interface_to_instances: dict[str, list[str]] = {}

        self.global_l2circuits: set[str] = set()
        self.global_ccc_interfaces: set[str] = set()

        self.global_protocols_by_interface: dict[str, set[str]] = {}
        self.default_bgp_neighbors: list[str] = []

    def parse(self) -> list[InterfaceService]:
        self._parse_routing_instances()
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

        return sorted(
            services,
            key=lambda item: interface_sort_key(item.interface),
        )
    
    def _is_inactive(
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
                bgp_neighbors=self._parse_bgp_neighbors(
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
    ) -> list[str]:
        neighbors: list[str] = []

        for bgp_node in node.xpath(bgp_xpath):
            for neighbor_node in bgp_node.xpath(
                ".//*[local-name()='neighbor']"
            ):
                if self._is_inactive(neighbor_node):
                    continue

                neighbor = first_text(
                    neighbor_node,
                    "./*[local-name()='name']/text()",
                ) or first_text(neighbor_node, "./text()")

                if neighbor:
                    neighbors.append(neighbor)

        return unique(neighbors)

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

        self.default_bgp_neighbors = self._parse_bgp_neighbors(
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

            # Fyzické rozhraní přidáme vždy.
            results.append(
                self._build_interface_config(
                    physical_name=physical_name,
                    logical_name=physical_name,
                    physical_description=physical_description,
                    physical_encapsulation=physical_encapsulation,
                    node=interface_node,
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
            ip_addresses=unique(
                ipv4_addresses + ipv6_addresses
            ),
            virtual_gw_ip_addresses=unique(
                virtual_gw_ipv4_addresses
                + virtual_gw_ipv6_addresses
            ),
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
            ip_address=interface.ip_addresses,
            virtual_gw_ip_address=interface.virtual_gw_ip_addresses,
            routing_instance=instance.name if instance else None,
            protocol=protocols,
            active=instance.active if instance else True,
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

            if service.service_type == "Internet":
                candidate_neighbors = self.default_bgp_neighbors
            elif service.routing_instance:
                instance = self.routing_instances.get(
                    service.routing_instance
                )

                if instance:
                    candidate_neighbors = instance.bgp_neighbors

            matched_neighbors = [
                neighbor
                for neighbor in candidate_neighbors
                if self._bgp_neighbor_matches_interface(
                    neighbor,
                    interface,
                )
            ]

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

        for address in interface.ip_addresses:
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

        if (
            instance_type == "vpls"
            or "vpls" in protocol_set
            or (
                instance_type == "virtual-switch"
                and instance is not None
                and "vpls" in instance.protocols
            )
        ):
            reasons.append(
                "ACX Junos OS Evolved používá pro VPLS "
                "instance-type virtual-switch s protocols vpls."
            )

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

    def _detect_evpn_elan_subtype(
        self,
        interface: InterfaceConfig,
        instance: RoutingInstance,
        bridge_domains: list[BridgeDomain],
        customer_vlans: list[str],
    ) -> tuple[str, str, list[str]]:
        """Určí EVPN E-LAN subtype pro ACX s Junos OS Evolved."""

        reasons: list[str] = []
        instance_type = (instance.instance_type or "").lower()
        service_type = (instance.evpn_service_type or "").lower()
        all_domains = instance.bridge_domains + instance.vlans

        # Na ACX EVO je explicitní service-type nejspolehlivější údaj.
        if service_type in {"vlan-aware", "vlan-based", "vlan-bundle"}:
            reasons.append(
                f"Routing instance explicitně používá service-type {service_type}."
            )
            return service_type, "high", reasons

        # MAC-VRF s více VLAN/doménami odpovídá VLAN-aware bundle.
        if instance_type == "mac-vrf":
            if len(all_domains) > 1 or self._contains_vlan_range_or_multiple(
                customer_vlans + instance.instance_vlan_ids
            ):
                reasons.append(
                    "ACX EVO MAC-VRF obsahuje více VLAN nebo VLAN rozsah."
                )
                return "vlan-aware", "high", reasons

            if len(all_domains) == 1 or instance.instance_vlan_ids:
                reasons.append(
                    "ACX EVO MAC-VRF obsahuje jednu VLAN/doménu."
                )
                return "vlan-based", "medium", reasons

        # Virtual-switch je EVPN pouze tehdy, když má protocols evpn.
        if instance_type == "virtual-switch" and "evpn" in instance.protocols:
            if len(all_domains) > 1 or self._contains_vlan_range_or_multiple(
                customer_vlans + instance.instance_vlan_ids
            ):
                reasons.append(
                    "Virtual-switch s protocols evpn obsluhuje více VLAN/domén."
                )
                return "vlan-aware", "high", reasons

            reasons.append(
                "Virtual-switch s protocols evpn obsluhuje jednu VLAN/doménu."
            )
            return "vlan-based", "medium", reasons

        if instance_type == "evpn":
            reasons.append(
                "Routing instance používá instance-type evpn."
            )
            return "vlan-based", "high", reasons

        reasons.append(
            "EVPN služba byla nalezena, ale bez jednoznačného service-type."
        )
        return "evpn-unknown", "low", reasons

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

        if interface.ip_addresses:
            return False

        if interface.virtual_gw_ip_addresses:
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
        if instance is None:
            return False

        instance_type = (
            instance.instance_type or ""
        ).lower()

        if instance_type in {"evpn", "mac-vrf"}:
            return True

        if (
            instance_type == "virtual-switch"
            and "evpn" in instance.protocols
        ):
            return True

        return False

    def _is_layer3(
        self,
        interface: InterfaceConfig,
    ) -> bool:
        return bool(
            interface.ip_addresses
            or interface.virtual_gw_ip_addresses
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
            and not interface.ip_addresses
            and not interface.virtual_gw_ip_addresses
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
        routing-instances
        protocols
        bridge-domains
        vlans
    """

    config_filter = etree.XML(
        b"""
        <configuration>
            <interfaces/>
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
        "ip_address",
        "virtual_gw_ip_address",
        "routing_instance",
        "active",
        "protocol",
        "bgp_neighbor",
        "bridge_domain",
        "customer_vlan",
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


def parse_arguments() -> argparse.Namespace:
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

    return parser.parse_args()


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


def main() -> int:
    args = parse_arguments()
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

        parser = JunosEvoAcxServiceParser(config_xml)
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
