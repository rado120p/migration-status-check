"""Sdilene fixtures - stavba snapshotu ze skutecne inventory."""

from __future__ import annotations

import ipaddress

import pytest

from migration_validator.checks.ifaces import is_transit
from migration_validator.models.inventory import load_inventory
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot
from migration_validator.scoping.builder import build_scopes


@pytest.fixture(autouse=True)
def _isolate_default_settings_path(tmp_path, monkeypatch):
    """Testy nesmi cist skutecny config/settings.yml.

    Patchuje se DEFAULT_SETTINGS_PATH v auth.py. Bez tohohle by kazdy test,
    ktery projde main(["capture"/"record", ...]), zkusil nacist verejny
    settings soubor a spadl na ValueErroru, kdyby soubor nebyl dostupny.

    POZOR: Pokud cli.py (prevedena v poznejsi uloze) bude pouzivat
    `from migration_validator.auth import DEFAULT_SETTINGS_PATH`, pouziti
    pri call-time se nebude patchovat - bude potrebna samostatna patchovani
    v cli.py. Pro patch v load_settings se tato patchovani staci.
    """
    fake_path = tmp_path / "settings-neexistuje.yml"
    monkeypatch.setattr("migration_validator.auth.DEFAULT_SETTINGS_PATH", fake_path)

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
    "optics",
    "isis_adjacency",
    "isis_interface",
    "isis_overview",
    "ldp_neighbor",
    "pim_neighbor",
    "mpls_interface",
    "igmp_group",
    "multicast_route",
    "mvpn_instance",
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

def _counters_for(peer: str) -> dict[str, int]:
    """Countery odvozene z adresy peera, aby se peery na reportu odlisily.

    Drive nesl kazdy peer 14/14/14/3, takze zamena peeru v kodu nebyla na
    reportu videt vubec. Zmereno pri psani planu vlny 10: rozruzneni
    neshodilo ani jeden ze 676 testu, tedy hodnoty counteru nehlidal nikdo -
    proto k teto zmene patri i zamykajici test v test_end_to_end.py.

    Odvozeni z ADRESY je podminka, ne styl: baseline i subject stavi tataz
    funkce z teze adresy, takze bgp_prefix_counts porovnava shodna cisla.
    Kdyby se cisla lisila mezi snimky, kazda zdrava sluzba by zacala svitit
    oranzove.

    Invarianty, ktere skutecny Junos drzi a fixtures je drzet musi taky:
    accepted <= received, active == accepted, suppressed == received - accepted.
    """
    seed = sum(ord(character) for character in peer) % 9
    received = 10 + seed
    accepted = received - seed % 3
    return {
        "received": received,
        "accepted": accepted,
        "advertised": 2 + seed % 4,
        "active": accepted,
        "suppressed": received - accepted,
    }

# Druha RIB ma vlastni cisla, jinak by se dva bloky tehoz peera lisily jen
# hlavickou. Nic nemeri; podstatne je, ze se lisi od primarni RIB stejneho
# peera (ta je odvozena z adresy pres _counters_for) a ze accepted < received.
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
    ribs = {primary: _counters_for(peer)}
    if peer == DUAL_RIB_PEER:
        secondary = "inet.0" if primary == "inet6.0" else "inet6.0"
        ribs.setdefault(secondary, dict(_SECOND_RIB_COUNTERS))
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
    optics = {}
    isis_adjacency = {}
    isis_interface = {}
    isis_overview = {}
    ldp_neighbor = {}
    pim_neighbor = {}
    mpls_interface = {}
    # igmp_group/multicast_route/mvpn_instance se NEsyntetizuji - stejny
    # duvod jako pim_neighbor vyse (sdilena inventory nema multicast sluzbu).
    # Zdravou syntezu doplni Task 6, az bude znamy tvar checku.
    igmp_group = {}
    multicast_route = {}
    mvpn_instance = {}

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
        # Zdrava optika pro kazdy fyzicky tranzitni port - bez ni by scope
        # s optickym rozhranim dostal prazdnou oblast a check z Tasku 12
        # by ji nemel co overit (fixture by lhala o zdravem stavu sluzby).
        for port in scope.selectors.physical_interfaces:
            optics[port] = {
                "lanes": [
                    {
                        "lane": 0,
                        "rx_power_dbm": -5.0,
                        "tx_power_dbm": -2.0,
                        "temperature_c": 30.0,
                        "alarms": {},
                        "warnings": {},
                    }
                ]
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
                "next_hop": [
                    hop["to"] for hop in route.get("next_hops") or [] if hop["active"]
                ],
                "via": list(scope.selectors.interfaces[:1]),
                "active": True,
                "protocol": str(route.get("route_type", "static")),
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

        # Zdrava IS-IS adjacency pro kazde tranzitni rozhrani Core sluzby -
        # bez ni by isis_adjacency_state hlasil FAIL 'chybi v outputu' na
        # kazde zdrave migraci a rozbil by test_full_migration_run_has_no_
        # unexplained_fail_or_warn (AR-29).
        if scope.key.service_type == "Core" and scope.service_subtype == "transit":
            v4 = scope.selectors.local_ipv4[0] if scope.selectors.local_ipv4 else None
            v6 = scope.selectors.local_ipv6[0] if scope.selectors.local_ipv6 else None
            # system_name NESMI vychazet z mistniho jmena rozhrani (ge- vs
            # et-) - pre/post fixtures prejmenovavaji fyzicke porty pri
            # vymene zarizeni, ale link (a tedy site sousedni /31 nebo /127)
            # zustava stejny. Klic ze site je proto jediny stabilni identifikator
            # sdileny mezi baseline a subjektem - jmenem rozhrani by AR-29
            # test spadl, jakmile by se scope matching zacal spolehat na
            # shodu system_name mezi pre a post snapshotem.
            network_key = str(ipaddress.ip_interface(v4).network) if v4 else "bez-adresy"
            for name in scope.selectors.interfaces:
                if not is_transit(name):
                    continue
                isis_adjacency[name] = {
                    "system_name": f"P-{network_key}",
                    "state": "Up",
                    "ip_address": _neighbour_for(v4) if v4 else None,
                    "ipv6_address": _neighbour_for(v6) if v6 else None,
                }
                # Nepasivni level 2 na tranzitu - jinak by isis_interface_info
                # (Task 9) hlasil FAIL "level 2 passive=ano" na kazdem
                # zdravem tranzitnim rozhrani a rozbil AR-29.
                isis_interface[name] = {"levels": {"2": {"passive": False}}}
                # LDP je na tranzitu ocekavany vzdy (Task 10, zadny gate na
                # zamer) - bez zaznamu by ldp_neighbor_state hlasil FAIL
                # "Down" na kazdem zdravem tranzitnim rozhrani a rozbil AR-29.
                # Soused se odvozuje stejne jako u IS-IS - ze site, ne ze
                # jmena rozhrani, aby zustal stabilni mezi pre/post fixturami.
                ldp_neighbor[name] = {
                    "neighbor_address": _neighbour_for(v4) if v4 else None,
                    "uptime_seconds": 25210,
                }
                # MPLS na tranzitu musi byt Up ze stejneho duvodu jako LDP -
                # jinak by mpls_interface_state hlasil FAIL a rozbil AR-29.
                mpls_interface[name] = {"state": "Up"}
                # PIM na tranzitu (spec 2026-09-02, multicast lab): global
                # "protocols pim interface all" ted zapina pim i na tranzitnich
                # portech, ktere pak nesou "pim" v zameru. pim_neighbor_state
                # ma gate na zamer (na rozdil od LDP/MPLS), ale kdyz uz bezi,
                # bez zdrave synteze by hlasil FAIL "soused ve vypisu neni"
                # na kazde zdrave migraci a rozbil AR-29.
                if "pim" in scope.selectors.protocols:
                    pim_neighbor[name] = {
                        "neighbor_address": _neighbour_for(v4) if v4 else None,
                        "uptime_seconds": 25210,
                    }
                # BFD na tranzitu je ocekavany vzdy (Task 11, zadny gate na
                # zamer) - bez zaznamu by bfd_transit_state hlasil FAIL
                # "zadna BFD session" na kazdem zdravem tranzitnim rozhrani
                # a rozbil AR-29. Peer se odvozuje ze site stejne jako u
                # IS-IS/LDP (_neighbour_for), aby zustal stabilni mezi
                # pre/post fixturami - klic je peer adresa, ne jmeno
                # rozhrani, protoze bfd dict je sdileny napric scopy.
                # POZOR: v4 (a tedy bfd_peer) je odvozeny z prvniho
                # local_ipv4 scopu, ne z jednotliveho rozhrani - se dvema
                # tranzitnimi rozhranimi ve stejnem scopu by druha iterace
                # prepsala zaznam prvni pod stejnym klicem. Sdilena inventory
                # ma vzdy jedno tranzitni rozhrani na Core transit scope
                # (overeno), takze se to dnes neprojevi; druhe rozhrani ve
                # scopu by potrebovalo vlastni v4/peer, ne tento kod beze zmeny.
                bfd_peer = _neighbour_for(v4) if v4 else None
                if bfd_peer:
                    bfd[bfd_peer] = {
                        "state": "Up",
                        "interface": name,
                        "remote_state": "Up",
                        "local_diagnostic": "None",
                        "clients": ["ISIS"],
                        "detection_time": "9.000",
                        "transmission_interval": "3.000",
                        "multiplier": 3,
                    }
        # lo0.* nese IS-IS jako pasivni level 2 - loopback nema souseda,
        # takze nepasivni level 2 by isis_interface_info hlasil FAIL.
        # overload_enabled: False, jinak by isis_overview hlasil WARN na
        # kazde zdrave migraci a rozbil AR-29.
        if scope.key.service_type == "Core" and scope.service_subtype == "loopback":
            for name in scope.selectors.interfaces:
                isis_interface[name] = {"levels": {"2": {"passive": True}}}
            isis_overview = {"overload_enabled": False}

        service_type = scope.key.service_type
        instance = (
            scope.selectors.routing_instances[0]
            if scope.selectors.routing_instances
            else None
        )
        if service_type == "E-Line" and instance:
            # Nove schema (Task 5): interfaces list, kazdy SID nese peers.
            # Remote peer musi byt 'Resolved', jinak by check hlasil BROKEN
            # a rozbil test_full_migration_run_has_no_unexplained_fail_or_warn.
            iface_name = (
                scope.selectors.interfaces[0] if scope.selectors.interfaces else instance
            )
            evpn_vpws[instance] = {
                "interfaces": [
                    {
                        "name": iface_name,
                        "status": "Up",
                        "mode": "single-homed",
                        "local_sid": {"value": 1000, "peers": []},
                        "remote_sid": {
                            "value": 2000,
                            "peers": [
                                {
                                    "esi": "00:00:00:00:00:00:00:00:00:00",
                                    "ipaddr": "150.0.0.14",
                                    "mode": "single-homed",
                                    "role": "Primary",
                                    "status": "Resolved",
                                }
                            ],
                        },
                    }
                ]
            }
        if service_type == "E-LAN" and instance:
            evpn_esi[f"esi-{instance}"] = {
                "status": "Up",
                "df_role": "DF",
                "interface": scope.selectors.interfaces[0],
            }
            # Nove schema (Task 2): klic je VLAN id, domena je jen popisek k
            # rendrovani (None u vlan-based - collector taky nevraci domenu).
            #
            # Instance sdili vic E-LAN scopu (napr. MGMT-DEVICE4 a MGMT-VLAN
            # obe v EVPN-VLAN-AWARE-POP1) - drivejsi kod tady zapisoval cely
            # slovnik najednou a druhy scope ten prvni tise prepsal. Fungovalo
            # to, dokud baseline (MX) a subjekt (EVO) nahodou sdileli stejny
            # vlan-key format; realna laborka po regeneraci (2026-09-02) uz
            # pro tutez domenu pouziva jiny format na kazde platforme
            # (MX vlan-tags jako "0x8100.4094", EVO plain "4093"/"4094"), a
            # scope taky muze mit vic nez jeden customer_vlan (multi-tag
            # unit) - jen prvni by tise vypadl. Merge pres vsechny scopy a
            # vsechny jejich vlany drzi synteticka fakta konzistentni s tim,
            # co inventory skutecne desklaruje.
            iface = scope.selectors.interfaces[0] if scope.selectors.interfaces else None
            vlans = scope.selectors.vlans or ["1"]
            domain = scope.selectors.bridge_domains[0] if scope.selectors.bridge_domains else None
            mac_entry = evpn_mac.setdefault(instance, {"vlans": {}, "interfaces": {}})
            for vlan in vlans:
                mac_entry["vlans"][vlan] = {"count": 42, "domain": domain}
            if iface:
                mac_entry["interfaces"][iface] = {
                    "count": 42, "name": f"{iface}:{vlans[0]}", "domain": domain
                }

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
        "optics": optics,
        "isis_adjacency": isis_adjacency,
        "isis_interface": isis_interface,
        "isis_overview": isis_overview,
        "ldp_neighbor": ldp_neighbor,
        "pim_neighbor": pim_neighbor,
        "mpls_interface": mpls_interface,
        "igmp_group": igmp_group,
        "multicast_route": multicast_route,
        "mvpn_instance": mvpn_instance,
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
