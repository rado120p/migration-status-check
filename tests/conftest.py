"""Sdilene fixtures - stavba snapshotu ze skutecne inventory."""

from __future__ import annotations

import pytest

from migration_validator.models.inventory import load_inventory
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot
from migration_validator.scoping.builder import build_scopes

NOW = "2026-07-24T09:12:41Z"


def _facts_for(scopes, pps: int) -> dict:
    interfaces = {}
    arp = []
    bgp = {}
    evpn_vpws = {}
    evpn_esi = {}
    evpn_mac = {}

    for scope in scopes:
        for name in scope.selectors.interfaces + scope.selectors.physical_interfaces:
            interfaces[name] = {
                "admin_status": "up",
                "oper_status": "up",
                "input_pps": pps,
                "output_pps": pps,
                "input_errors": 0,
                "output_errors": 0,
            }
        for peer in scope.selectors.bgp_neighbors:
            arp.append({"ip": peer, "interface": scope.selectors.interfaces[0]})
            bgp[peer] = {
                "state": "Established",
                "routing_instance": (
                    scope.selectors.routing_instances[0]
                    if scope.selectors.routing_instances
                    else None
                ),
                "prefixes": {"received": 14, "accepted": 14, "advertised": 3},
            }
        service_type = scope.key.service_type
        instance = (
            scope.selectors.routing_instances[0]
            if scope.selectors.routing_instances
            else None
        )
        if service_type == "E-Line" and instance:
            evpn_vpws[instance] = {"local_sid": 213, "remote_sid": 213, "status": "Up"}
        if service_type == "E-LAN" and instance:
            evpn_esi[f"esi-{instance}"] = {
                "status": "Up",
                "df_role": "DF",
                "interface": scope.selectors.interfaces[0],
            }
            domains = scope.selectors.bridge_domains or ["-"]
            evpn_mac[instance] = {domain: 42 for domain in domains}

    return {
        "interfaces": interfaces,
        "arp": arp,
        "bgp": bgp,
        "evpn_vpws": evpn_vpws,
        "evpn_esi": evpn_esi,
        "evpn_mac": evpn_mac,
    }


@pytest.fixture
def synthetic_snapshot():
    def build(inventory_path: str, address: str, phase: str, *, pps: int = 400) -> Snapshot:
        inventory = load_inventory(inventory_path)
        scopes = build_scopes(inventory)
        pings = [
            {
                "scope_id": scope.id,
                "target": scope.selectors.bgp_neighbors[0],
                "source": scope.selectors.local_ipv4[0].split("/")[0],
                "sent": 5,
                "received": 5,
                "loss_percent": 0,
                "resolved_from": "arp",
            }
            for scope in scopes
            if scope.selectors.bgp_neighbors and scope.selectors.local_ipv4
        ]
        return Snapshot(
            device=DeviceMeta(address=address),
            capture=CaptureMeta(
                started_at=NOW,
                finished_at=NOW,
                phase=phase,
                collectors={
                    name: {"status": "ok"}
                    for name in ("interfaces", "arp", "bgp", "evpn_vpws", "evpn_esi", "evpn_mac")
                },
            ),
            facts=_facts_for(scopes, pps),
            probes={"ping": pings},
            scopes=scopes,
            inventory=inventory.entries,
        )

    return build
