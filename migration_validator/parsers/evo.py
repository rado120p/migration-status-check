"""EVO (ACX Junos OS Evolved) platformni cast parseru sluzeb."""

from __future__ import annotations

from migration_validator.parsers.core import (
    BridgeDomain,
    InterfaceConfig,
    JunosServiceParserCore,
    RoutingInstance,
)


class JunosEvoAcxServiceParser(JunosServiceParserCore):
    LOGGER_NAME = "junos-evo-acx-service-parser"

    def _detect_vpls(
        self,
        instance_type: str | None,
        protocol_set: set[str],
        instance: RoutingInstance | None,
    ) -> str | None:
        if (
            instance_type == "vpls"
            or "vpls" in protocol_set
            or (
                instance_type == "virtual-switch"
                and instance is not None
                and "vpls" in instance.protocols
            )
        ):
            return (
                "ACX Junos OS Evolved používá pro VPLS "
                "instance-type virtual-switch s protocols vpls."
            )

        return None

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
                reasons.append("ACX EVO MAC-VRF obsahuje více VLAN nebo VLAN rozsah.")
                return "vlan-aware", "high", reasons

            if len(all_domains) == 1 or instance.instance_vlan_ids:
                reasons.append("ACX EVO MAC-VRF obsahuje jednu VLAN/doménu.")
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
            reasons.append("Routing instance používá instance-type evpn.")
            return "vlan-based", "high", reasons

        reasons.append("EVPN služba byla nalezena, ale bez jednoznačného service-type.")
        return "evpn-unknown", "low", reasons

    def _is_evpn_instance(self, instance: RoutingInstance | None) -> bool:
        if instance is None:
            return False

        instance_type = (instance.instance_type or "").lower()

        if instance_type in {"evpn", "mac-vrf"}:
            return True

        if instance_type == "virtual-switch" and "evpn" in instance.protocols:
            return True

        return False
