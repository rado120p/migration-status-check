"""Sdilene fixtures - stavba snapshotu ze skutecne inventory."""

from __future__ import annotations

import ipaddress

import pytest

from migration_validator.models.inventory import load_inventory
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot
from migration_validator.scoping.builder import build_scopes

NOW = "2026-07-24T09:12:41Z"

MAC = "0c:00:ef:5e:df:01"

# Oblasti, ktere musi snapshot nest. Drzi se zamerne u FACT_AREAS: kdyby
# tady oblast chybela, check nad ni nedostane SKIP (chybejici collector
# neni totez co selhany), ale spusti se nad prazdnymi fakty a vrati FAIL.
COLLECTOR_NAMES = (
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


def _neighbour_for(prefix: str) -> str | None:
    """Protejsek na stejnem linku - prvni adresa v subnetu, ktera neni nase.

    Sluzba muze mit adresu a zadneho BGP souseda (napr. rozhrani, ktere po
    migraci zbylo bez peeru). `arp_present` / `nd_present` na ni presto bezi,
    takze bez odvozeneho souseda by synteticka fixture hlasila WARN za
    prazdnou tabulku - a to je vlastnost fixture, ne zdravi sluzby.
    """
    try:
        interface = ipaddress.ip_interface(prefix)
    except ValueError:
        return None

    for candidate in interface.network.hosts():
        if candidate != interface.ip:
            return str(candidate)

    # /32 a /128 zadneho souseda nemaji - na loopbacku se ARP nedela.
    return None


# Peer, ktery jako jediny nese dve RIB. Bez nej by motivujici scenar AR-36
# nebyl na sdilenych fixtures k videni - kazdy peer by mel prave jednu RIB
# a blok, kvuli kteremu vlna 5 report prepsala, by se nikdy nevyrenderoval.
# Vybrany je zamerne: 152.11.13.2 je na obou fixtures (.4 i .5), takze
# baseline i subject nesou tutez strukturu a bgp_prefix_counts je porovna
# misto aby hlasil "RIB neni v baseline". Je to IPv4 peer nesouci navic
# inet6.0, tedy multiprotokolova session, ne fabulace.
DUAL_RIB_PEER = "152.11.13.2"

_RIB_COUNTERS = {
    "received": 14,
    "accepted": 14,
    "advertised": 3,
    "active": 14,
    "suppressed": 0,
}

# Druha RIB ma vlastni cisla, jinak by se dva bloky teho peera lisily jen
# hlavickou. Nic nemeri; podstatne je, ze se lisi od 14/14/14/3 a ze
# accepted < received.
_SECOND_RIB_COUNTERS = {
    "received": 6,
    "accepted": 5,
    "advertised": 2,
    "active": 5,
    "suppressed": 1,
}


def _ribs_for(peer: str, family: int) -> dict:
    """RIB peera podle rodiny; DUAL_RIB_PEER dostane navic druhou.

    Sdilene fixtures drive davaly kazdemu peerovi inet.0 bez ohledu na
    rodinu - u IPv6 peera to bylo v rozporu se sousedni skupinou statickych
    rout ve stejne sekci, ktera inet6.0 pouzivala spravne.
    """
    primary = "inet6.0" if family == 6 else "inet.0"
    ribs = {primary: dict(_RIB_COUNTERS)}
    if peer == DUAL_RIB_PEER:
        ribs["inet6.0"] = dict(_SECOND_RIB_COUNTERS)
    return ribs


def _facts_for(scopes, pps: int) -> dict:
    interfaces = {}
    arp = []
    nd = []
    bgp = {}
    evpn_vpws = {}
    evpn_esi = {}
    evpn_mac = {}
    routes: dict[str, dict[str, dict]] = {}
    bfd = {}

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
        seen_families = set()
        for peer in scope.selectors.bgp_neighbors:
            # bgp_neighbors mixa v4 a v6 sousedy - bez rozliseni rodiny by
            # v6 peer skoncil v ARP tabulce jako falesny family-4 zaznam.
            interface = scope.selectors.interfaces[0]
            family = ipaddress.ip_address(peer).version
            seen_families.add(family)
            if family == 6:
                nd.append(
                    {
                        "ip": peer,
                        "mac": MAC,
                        "interface": interface,
                        "state": "reachable",
                    }
                )
            else:
                arp.append({"ip": peer, "interface": interface})
            bgp[peer] = {
                "state": "Established",
                "routing_instance": (
                    scope.selectors.routing_instances[0]
                    if scope.selectors.routing_instances
                    else None
                ),
                "ribs": _ribs_for(peer, family),
            }
        # Sluzba s adresou, ale bez peeru te rodiny, by jinak zustala s
        # prazdnou ARP/ND tabulkou. Fabrikuje se proto soused odvozeny ze
        # subnetu - stejne, jako se fabrikuje uplne vsechno ostatni.
        if scope.selectors.interfaces:
            interface = scope.selectors.interfaces[0]
            for family, prefixes in (
                (4, scope.selectors.local_ipv4),
                (6, scope.selectors.local_ipv6),
            ):
                if family in seen_families or not prefixes:
                    continue
                neighbour = _neighbour_for(prefixes[0])
                if neighbour is None:
                    continue
                if family == 6:
                    nd.append(
                        {
                            "ip": neighbour,
                            "mac": MAC,
                            "interface": interface,
                            "state": "reachable",
                        }
                    )
                else:
                    arp.append({"ip": neighbour, "interface": interface})

        # Zamer z inventory se zrcadli do namerenych faktu jako zdravy stav:
        # routa je v tabulce se stejnym next-hopem, session je Up.
        for route in scope.selectors.static_routes:
            routes.setdefault(str(route["rib"]), {})[str(route["prefix"])] = {
                "next_hop": list(route.get("next_hop") or []),
                "via": list(scope.selectors.interfaces[:1]),
                "active": True,
            }

        for intent in scope.selectors.bfd_peers:
            peer = str(intent["peer"])
            bfd[peer] = {
                "state": "Up",
                "interface": (
                    scope.selectors.interfaces[0]
                    if scope.selectors.interfaces
                    else None
                ),
                "remote_state": "Up",
                "local_diagnostic": "None",
                "clients": ["BGP"],
                "detection_time": "9.000",
                "transmission_interval": "3.000",
                "multiplier": intent.get("multiplier"),
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
        "nd": nd,
        "bgp": bgp,
        "evpn_vpws": evpn_vpws,
        "evpn_esi": evpn_esi,
        "evpn_mac": evpn_mac,
        "routes": routes,
        "bfd": bfd,
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
                "family": 4,
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
                collectors={name: {"status": "ok"} for name in COLLECTOR_NAMES},
            ),
            facts=_facts_for(scopes, pps),
            probes={"ping": pings},
            scopes=scopes,
            inventory=inventory.entries,
        )

    return build
