"""MX (Junos) platformni cast parseru sluzeb."""

from __future__ import annotations

from migration_validator.parsers.core import (
    BridgeDomain,
    InterfaceConfig,
    JunosServiceParserCore,
    RoutingInstance,
)


class JunosServiceParser(JunosServiceParserCore):
    LOGGER_NAME = "junos-service-parser"

    def _detect_vpls(
        self,
        instance_type: str | None,
        protocol_set: set[str],
        instance: RoutingInstance | None,
    ) -> str | None:
        if (
            instance_type == "vpls"
            or "vpls" in protocol_set
        ):
            return (
                "Routing instance používá instance-type vpls "
                "nebo protocols vpls."
            )

        return None

    def _detect_evpn_elan_subtype(
        self,
        interface: InterfaceConfig,
        instance: RoutingInstance,
        bridge_domains: list[BridgeDomain],
        customer_vlans: list[str],
    ) -> tuple[str, str, list[str]]:
        """
        Určení EVPN E-LAN subtype podle lokálního
        konfiguračního standardu.

        Pravidla:
            virtual-switch + bridge domain -> vlan-aware
            evpn                           -> vlan-based
        """

        reasons: list[str] = []

        instance_type = (
            instance.instance_type or ""
        ).lower()

        all_bridge_domains = (
            instance.bridge_domains
            + instance.vlans
        )

        if (
            instance_type == "virtual-switch"
            and all_bridge_domains
        ):
            reasons.append(
                "Routing instance má instance-type virtual-switch "
                "a obsahuje bridge domain."
            )

            return (
                "vlan-aware",
                "high",
                reasons,
            )

        if instance_type == "evpn":
            reasons.append(
                "Routing instance má instance-type evpn."
            )

            return (
                "vlan-based",
                "high",
                reasons,
            )

        reasons.append(
            "EVPN služba byla nalezena, ale instance-type "
            "neodpovídá pravidlům pro vlan-aware nebo vlan-based."
        )

        return (
            "evpn-unknown",
            "low",
            reasons,
        )

    def _is_evpn_instance(
        self,
        instance: RoutingInstance | None,
    ) -> bool:
        if instance is None:
            return False

        instance_type = (
            instance.instance_type or ""
        ).lower()

        if instance_type == "evpn":
            return True

        if (
            instance_type == "virtual-switch"
            and (
                instance.bridge_domains
                or instance.vlans
            )
        ):
            return True

        return False
