from fact_records import (
    bfd_record,
    bfd_records,
    bgp_record,
    bgp_records,
    esi_record,
    route_record,
    route_records,
)

from migration_validator.models.scope import (
    FACT_AREAS,
    Scope,
    ScopeKey,
    Selectors,
)

FACTS = {
    "interfaces": {
        "ge-0/0/2": {"oper_status": "up"},
        "ge-0/0/2.113": {"oper_status": "up", "input_pps": 10},
        "ge-0/0/9.0": {"oper_status": "down"},
    },
    "arp": [
        {"ip": "198.11.13.2", "interface": "ge-0/0/2.113"},
        {"ip": "10.9.9.2", "interface": "ge-0/0/9.0"},
    ],
    "nd": [
        {"ip": "fe80::1", "interface": "ge-0/0/2.113"},
        {"ip": "2001:db8::9", "interface": "ge-0/0/9.0"},
    ],
    "bgp": bgp_records({
        "198.11.13.2": {"state": "Established", "routing_instance": "L3VPN-CPE13-NNI"},
        "10.9.9.2": {"state": "Active", "routing_instance": None},
    }),
    "evpn_vpws": {"EVPN-VPWS-CPE13-NNI": {"status": "Up"}},
    "evpn_esi": [
        esi_record("L3VPN-CPE13-NNI", "00:11", {"ge-0/0/2.113": "Up"}),
    ],
    "evpn_mac": {"L3VPN-CPE13-NNI": {"BD-313": 42}},
}

PROBES = {
    "ping": [
        {"scope_id": "svc:L3VPN-CPE13-NNI:IPVPN", "target": "198.11.13.2", "received": 5},
        {"scope_id": "svc:jiny:Internet", "target": "10.9.9.2", "received": 0},
    ]
}


def _service_scope() -> Scope:
    return Scope(
        id="svc:L3VPN-CPE13-NNI:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"],
            physical_interfaces=["ge-0/0/2"],
            routing_instances=["L3VPN-CPE13-NNI"],
            bgp_neighbors=["198.11.13.2"],
            local_ipv4=["198.11.13.1/30"],
            local_ipv6=[],
            virtual_gw_v4=[],
            virtual_gw_v6=[],
            vlans=["113"],
            bridge_domains=[],
        ),
    )


def test_select_filters_interfaces_including_physical_parent():
    selected = _service_scope().select(FACTS, PROBES)
    assert set(selected["interfaces"]) == {"ge-0/0/2", "ge-0/0/2.113"}


def test_select_filters_arp_by_interface():
    selected = _service_scope().select(FACTS, PROBES)
    assert [entry["ip"] for entry in selected["arp"]] == ["198.11.13.2"]


def test_select_filters_nd_by_interface():
    """Servisni vetev ma vlastni filtr - link-local adresa v nem musi projit,
    filtruje se podle rozhrani, ne podle rodiny adres."""
    selected = _service_scope().select(FACTS, PROBES)
    assert [entry["ip"] for entry in selected["nd"]] == ["fe80::1"]


def test_select_filters_bgp_by_neighbor():
    selected = _service_scope().select(FACTS, PROBES)
    assert list(selected["bgp"]) == ["198.11.13.2"]


def test_select_filters_evpn_by_routing_instance_and_interface():
    selected = _service_scope().select(FACTS, PROBES)
    assert list(selected["evpn_mac"]) == ["L3VPN-CPE13-NNI"]
    assert list(selected["evpn_esi"]) == ["00:11"]
    assert selected["evpn_vpws"] == {}


def test_select_evpn_instance_by_routing_instance():
    scope = Scope(
        id="svc:X:E-LAN", kind="service",
        key=ScopeKey("X", "E-LAN", "vlan-aware"),
        selectors=Selectors(routing_instances=["EVPN-A"]),
    )
    facts = {"evpn_instance": {"EVPN-A": {"neighbors": {}}, "EVPN-B": {}}}
    assert set(scope.select(facts)["evpn_instance"]) == {"EVPN-A"}


ESI = "00:11:12:13:14:00:00:00:00:00"


def _elan(ri, iface):
    return Scope(
        id=f"svc:{iface}", kind="service",
        key=ScopeKey(description=iface, service_type="E-LAN"),
        selectors=Selectors(interfaces=[iface], physical_interfaces=["ae0"],
                            routing_instances=[ri]),
    )


def test_esi_shared_by_two_instances_each_scope_gets_its_ifl():
    facts = {"evpn_esi": [
        esi_record("EVI-A", ESI, {"ae0.4093": "Down"}),
        esi_record("EVI-B", ESI, {"ae0.4094": "Up"}),
    ]}
    a = _elan("EVI-A", "ae0.4093").select(facts)["evpn_esi"]
    b = _elan("EVI-B", "ae0.4094").select(facts)["evpn_esi"]
    assert a[ESI]["status"] == "Down" and a[ESI]["interface"] == "ae0.4093"
    assert b[ESI]["status"] == "Up" and b[ESI]["interface"] == "ae0.4094"


def test_esi_two_ifls_one_instance_scope_sees_only_own_ifl():
    facts = {"evpn_esi": [esi_record("EVI", ESI, {"ae0.4093": "Up", "ae0.4094": "Down"})]}
    view = _elan("EVI", "ae0.4093").select(facts)["evpn_esi"]
    assert view == {ESI: {
        "resolved_status": "Resolved by IFL ae0.4093", "df_role": "10.0.0.1",
        "interface": "ae0.4093", "status": "Up", "mode": "all-active",
    }}


def test_esi_of_other_instance_is_not_selected():
    facts = {"evpn_esi": [esi_record("EVI-B", ESI, {"ae0.4093": "Up"})]}
    assert _elan("EVI-A", "ae0.4093").select(facts)["evpn_esi"] == {}


def test_select_filters_ping_by_scope_id():
    selected = _service_scope().select(FACTS, PROBES)
    assert [probe["target"] for probe in selected["ping"]] == ["198.11.13.2"]


def test_select_tolerates_missing_fact_areas():
    selected = _service_scope().select({}, None)
    assert selected["interfaces"] == {}
    assert selected["arp"] == []
    assert selected["ping"] == []


def test_select_prenasi_ping_skipped_marker():
    scope = _service_scope()
    subject = scope.select(
        {}, {"ping": [], "ping_skipped": [{"scope_id": scope.id, "reason": "mimo profil"}]}
    )
    assert subject["ping_skipped"] is True


def test_select_bez_markeru_je_false():
    subject = _service_scope().select({}, {"ping": []})
    assert subject["ping_skipped"] is False


def test_scope_round_trip():
    scope = _service_scope()
    assert Scope.from_dict(scope.to_dict()) == scope


def test_nd_area_is_a_list_when_missing():
    """Spatny prazdny typ by check videl jako prazdny slovnik a tise prosel."""
    selected = _service_scope().select({})

    assert selected["nd"] == []
    assert "nd" in FACT_AREAS


def test_device_scope_no_longer_exists():
    # Spec 2026-09-24: jmeno `device` se pro nic noveho nepouzije.
    import migration_validator.models.scope as scope_module

    assert not hasattr(scope_module, "device_scope")
    assert not hasattr(scope_module, "DEVICE_SCOPE_ID")
    assert not hasattr(Scope, "is_device")


ROUTE_FACTS = route_records({
    "inet.0": {
        "198.62.1.0/29": {"next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": True},
        "10.9.9.0/24": {"next_hop": ["10.9.9.1"], "via": ["et-0/0/9.0"], "active": True},
    },
    "L3VPN-A.inet.0": {
        "172.26.1.0/29": {"next_hop": ["198.11.13.2"], "via": ["et-0/0/8.113"], "active": True},
    },
})

BFD_FACTS = bfd_records({
    "152.11.13.2": {"state": "Up", "interface": "et-0/0/8.13"},
    "198.11.14.2": {"state": "Down", "interface": "et-0/0/8.114"},
})


def _scope_with(**selector_kwargs) -> Scope:
    return Scope(
        id="svc:test:Internet",
        kind="service",
        key=ScopeKey(description="test", service_type="Internet"),
        selectors=Selectors(**selector_kwargs),
    )


def test_routes_are_selected_by_rib_and_prefix():
    """Identita je (RIB, prefix) - stejny prefix v jine RIB je jina routa."""
    scope = _scope_with(
        static_routes=[
            {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": ["152.11.13.2"]}
        ]
    )

    selected = scope.select({"routes": ROUTE_FACTS})

    assert selected["routes"] == {
        "static": {
            "inet.0": {
                "198.62.1.0/29": route_record(
                    "inet.0", "198.62.1.0/29",
                    next_hop=["152.11.13.2"], via=["et-0/0/8.13"], active=True,
                )
            }
        }
    }


def test_table_without_matching_prefix_is_dropped_entirely():
    """Prazdna tabulka by v reportu nic nerekla a check by ji musel preskakovat."""
    scope = _scope_with(
        static_routes=[
            {"rib": "L3VPN-A.inet.0", "prefix": "172.26.1.0/29", "next_hop": []}
        ]
    )

    selected = scope.select({"routes": ROUTE_FACTS})

    assert set(selected["routes"]["static"]) == {"L3VPN-A.inet.0"}


def test_routes_view_is_keyed_by_protocol_and_selector_route_type():
    scope = Scope(
        id="svc:x", kind="service", key=ScopeKey(description="x", service_type="IPVPN"),
        selectors=Selectors(interfaces=["ae0.100"], routing_instances=["CUST"], static_routes=[
            {"rib": "CUST.inet.0", "prefix": "10.1.0.0/24", "route_type": "static", "next_hops": []},
        ]),
    )
    facts = {"routes": [
        route_record("CUST.inet.0", "10.1.0.0/24", protocol="static", via=["ae0.100"]),
        route_record("CUST.inet.0", "10.1.0.0/24", protocol="aggregate", active=False),
    ]}
    view = scope.select(facts)["routes"]
    assert set(view) == {"static"}
    assert view["static"]["CUST.inet.0"]["10.1.0.0/24"]["via"] == ["ae0.100"]


def test_routes_view_keeps_static_and_aggregate_selectors_separate():
    scope = Scope(
        id="svc:x", kind="service", key=ScopeKey(description="x", service_type="IPVPN"),
        selectors=Selectors(interfaces=["ae0.100"], routing_instances=["CUST"], static_routes=[
            {"rib": "CUST.inet.0", "prefix": "10.1.0.0/24", "route_type": "static", "next_hops": []},
            {"rib": "CUST.inet.0", "prefix": "10.1.0.0/24", "route_type": "aggregate", "next_hops": []},
        ]),
    )
    facts = {"routes": [
        route_record("CUST.inet.0", "10.1.0.0/24", protocol="static", via=["ae0.100"]),
        route_record("CUST.inet.0", "10.1.0.0/24", protocol="aggregate", active=False),
    ]}
    view = scope.select(facts)["routes"]
    assert set(view) == {"static", "aggregate"}


def test_routes_view_protocol_less_record_matches_default_static_selector():
    """route_key() musi pouzit stejny default 'static' na obou stranach
    (review Minor 3): zaznam bez klice 'protocol' (parser ho nekdy
    nevyplni) se ma spárovat se selektorem bez 'route_type' (taky default
    static). Predtim scope.py cetlo record stranu bez defaultu
    (str(record.get('protocol')) -> literal 'None'), takze se nikdy
    nesparovalo s wanted_routes defaultem 'static' - routa nebyla videt
    nikde."""
    scope = Scope(
        id="svc:x", kind="service", key=ScopeKey(description="x", service_type="Internet"),
        selectors=Selectors(static_routes=[
            {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hops": []},
        ]),
    )
    record = {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": ["152.11.13.2"]}

    view = scope.select({"routes": [record]})["routes"]

    assert view == {"static": {"inet.0": {"198.62.1.0/29": record}}}


def test_bfd_sessions_are_selected_by_bgp_neighbors():
    """Session patri scopu podle peeru, ne podle zameru - viz AR-14.

    Kdyby se vybiralo podle bfd_peers, session peeru, ktereho parser do
    zameru nedoplnil, by se do scope nedostala a chyba v pruchodu hierarchii
    by se schovala pred vystupem nastroje.
    """
    scope = _scope_with(
        interfaces=["et-0/0/8.13"], bgp_neighbors=["152.11.13.2"], bfd_peers=[]
    )

    selected = scope.select({"bfd": BFD_FACTS})

    assert set(selected["bfd"]) == {"152.11.13.2"}


def test_bgp_session_of_deactivated_peer_is_selected():
    """Deaktivovany peer se zivou session patri scopu stejne jako aktivni.

    Kdyby se session nevybrala, checks/bgp.py by peera videl jako
    bezsessioveho, dal by mu WARN 'deaktivovan' a rozpor 'konfigurace
    vypnuto, zarizeni bezi' by z reportu zmizel.

    Zabiji mutanta: vyber `bgp` filtrovany jen pres `bgp_neighbors`
    (bez `bgp_neighbors_inactive`).
    """
    scope = _scope_with(bgp_neighbors=[], bgp_neighbors_inactive=["152.11.13.2"])

    selected = scope.select({"bgp": bgp_records({"152.11.13.2": {"state": "Established"}})})

    assert set(selected["bgp"]) == {"152.11.13.2"}


def test_bfd_session_of_deactivated_peer_is_not_selected():
    """Protejsek predchoziho testu - u BFD se inactive zamerne nepricita.

    checks/bfd.py o deaktivaci nevi: zamer si bere z bfd_peers, ktery pro
    deaktivovaneho peera prazdny je, takze vybranou session by vypsal jako
    WARN 'session existuje, v konfiguraci sluzby neni' - nepravda, peer
    v konfiguraci je, jen deaktivovany. Musi zustat nezarazena, aby ji
    engine ukazal v NEZARAZENO.

    Zabiji mutanta: vyber `bfd` rozsireny o `bgp_neighbors_inactive`
    (symetricky s vyberem `bgp`).
    """
    scope = _scope_with(bgp_neighbors=[], bgp_neighbors_inactive=["152.11.13.2"])

    selected = scope.select({"bfd": BFD_FACTS})

    assert selected["bfd"] == {}


def test_missing_areas_come_back_as_empty_mappings():
    scope = _scope_with()

    selected = scope.select({})

    assert selected["routes"] == {}
    assert selected["bfd"] == {}


OPTICS_FACTS = {
    "et-0/0/8": {"lanes": [{"lane": 0, "rx_power_dbm": -3.0}]},
    "et-0/0/9": {"lanes": [{"lane": 0, "rx_power_dbm": -1.0}]},
    "et-0/0/10": {"lanes": [{"lane": 0, "rx_power_dbm": -9.0}]},
}


def test_optics_selected_by_physical_interface():
    """Optika je klicovana fyzickym portem, ne logickym rozhranim scopu."""
    scope = _scope_with(physical_interfaces=["et-0/0/8"])

    selected = scope.select({"optics": OPTICS_FACTS})

    assert set(selected["optics"]) == {"et-0/0/8"}


def test_optics_selected_by_lag_member():
    """LAG-clen nese vlastni optiku, i kdyz neni v physical_interfaces (to
    je jmeno agregovaneho ae rozhrani). Zabiji mutanta: vypusteni
    `or name in self.selectors.lag_members` ze Scope.select.
    """
    scope = _scope_with(physical_interfaces=["ae0"], lag_members=["et-0/0/9"])

    selected = scope.select({"optics": OPTICS_FACTS})

    assert set(selected["optics"]) == {"et-0/0/9"}


def test_optics_of_foreign_port_is_not_selected():
    scope = _scope_with(physical_interfaces=["et-0/0/8"])

    selected = scope.select({"optics": OPTICS_FACTS})

    assert "et-0/0/10" not in selected["optics"]


def test_scope_reports_why_it_is_deactivated():
    """Duvod jmenuje oba zdroje, aby operator vedel, co odaktivovat.

    Zabiji mutanta: is_deactivated postavene jen na routing_instance_active.
    """
    live = Scope(id="s", kind="service", key=None, selectors=Selectors())
    off_ri = Scope(
        id="s", kind="service", key=None, selectors=Selectors(),
        routing_instance_active=False,
    )
    off_if = Scope(
        id="s", kind="service", key=None, selectors=Selectors(),
        interface_active=False,
    )
    off_both = Scope(
        id="s", kind="service", key=None, selectors=Selectors(),
        routing_instance_active=False, interface_active=False,
    )

    assert live.is_deactivated is False
    assert live.deactivation_reason is None
    assert off_ri.is_deactivated is True
    assert off_ri.deactivation_reason == "RI deactivated"
    assert off_if.is_deactivated is True
    assert off_if.deactivation_reason == "interface deactivated"
    assert off_both.is_deactivated is True
    assert off_both.deactivation_reason == "RI + interface deactivated"


def test_scope_round_trips_deactivation_flags():
    """Bez serializace by baseline svuj stav nenesla a AR-23 by nemel co porovnat.

    Zabiji mutanta: vynechani obou klicu z to_dict.
    """
    scope = Scope(
        id="svc:CPE14:IPVPN", kind="service", key=None, selectors=Selectors(),
        routing_instance_active=True, interface_active=False,
    )

    serialized = scope.to_dict()
    assert "routing_instance_active" in serialized
    assert "interface_active" in serialized

    restored = Scope.from_dict(serialized)

    assert restored.routing_instance_active is True
    assert restored.interface_active is False


def test_selectors_survive_roundtrip():
    selectors = Selectors(
        interfaces=["et-0/0/8.13"],
        local_ipv4=["152.11.13.1/30"],
        local_ipv6=["2001:abcd:11:13::a/127"],
        virtual_gw_v4=["152.11.13.254"],
        virtual_gw_v6=["2001:abcd:11:13::1"],
        static_routes=[{"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": []}],
        bfd_peers=[{"peer": "152.11.13.2", "minimum_interval": 300, "multiplier": 3}],
    )

    restored = Selectors.from_dict(selectors.to_dict())

    assert restored.local_ipv4 == ["152.11.13.1/30"]
    assert restored.local_ipv6 == ["2001:abcd:11:13::a/127"]
    assert restored.virtual_gw_v4 == ["152.11.13.254"]
    assert restored.virtual_gw_v6 == ["2001:abcd:11:13::1"]
    assert restored.static_routes == [
        {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": []}
    ]
    assert restored.bfd_peers == [
        {"peer": "152.11.13.2", "minimum_interval": 300, "multiplier": 3}
    ]


def _local_scope(active=True) -> Scope:
    return Scope(
        id="svc:NGMVPN-IGMP-RECEIVER:E-LAN",
        kind="service",
        key=ScopeKey("NGMVPN-IGMP-RECEIVER", "E-LAN", "local"),
        selectors=Selectors(
            interfaces=["ge-0/0/2.12"], vlans=["12"],
            bridge_domains=["BD-NGMVPN-IGMP-RECEIVER"],
        ),
        routing_instance_active=active,
    )


def test_local_scope_selects_default_switch_mac_table():
    facts = {
        "evpn_mac": {
            "default-switch": {"vlans": {"12": {"count": 1, "domain": "BD-X"}}, "interfaces": {}},
            "EVPN-VLAN-AWARE-POP1": {"vlans": {}, "interfaces": {}},
        }
    }
    selected = _local_scope().select(facts, {})
    assert list(selected["evpn_mac"]) == ["default-switch"]


def test_vlan_aware_scope_does_not_get_default_switch():
    scope = Scope(
        id="s", kind="service", key=ScopeKey("X", "E-LAN", "vlan-aware"),
        selectors=Selectors(interfaces=["ge-0/0/2.313"], routing_instances=["EVPN-A"]),
    )
    facts = {"evpn_mac": {"default-switch": {}, "EVPN-A": {}}}
    assert list(scope.select(facts, {})["evpn_mac"]) == ["EVPN-A"]


def test_local_scope_deactivation_reason_names_bridge_domain():
    assert _local_scope(active=False).deactivation_reason == "bridge-domain deactivated"
    off_both = _local_scope(active=False)
    off_both.interface_active = False
    assert off_both.deactivation_reason == "bridge-domain + interface deactivated"


# Dve VRF se stejnou p2p podsiti (ostry beh MX -> ACX 2026-09-23): collector
# klicuje BGP i BFD jen adresou peera, takze zarizeni nese jedinou polozku
# 192.168.1.2 - tu, kterou collector precetl posledni. Scope ji smi vzit jen
# tehdy, kdyz je opravdu jeho (instance u BGP, rozhrani u single-hop BFD).
SHARED_PEER = "192.168.1.2"


def _vrf_scope(name: str, interface: str) -> Scope:
    return Scope(
        id=f"svc:{name}:IPVPN",
        kind="service",
        key=ScopeKey(name, "IPVPN", None),
        selectors=Selectors(
            interfaces=[interface],
            routing_instances=[name],
            bgp_neighbors=[SHARED_PEER],
        ),
    )


def _bgp_entry(instance: str | None) -> dict:
    return {"state": "Established", "routing_instance": instance, "ribs": {}}


def test_ipvpn_scope_does_not_take_peer_of_another_vrf_with_same_address():
    """Zabiji mutanta: vyber `bgp` jen podle adresy (bez porovnani instance).

    Cizi zaznam je proste nepritomny - zadna kolize, zadna nejednoznacnost
    (kolize klicu, spec 2026-09-25): kazda VRF ma sve vlastni zaznamy."""
    facts = {"bgp": bgp_records({SHARED_PEER: _bgp_entry("customer-a")})}

    b = _vrf_scope("customer-b", "ge-0/0/2.200").select(facts)
    a = _vrf_scope("customer-a", "ge-0/0/1.100").select(facts)
    assert b["bgp"] == {} and b["bgp_ambiguous"] == {}
    assert set(a["bgp"]) == {SHARED_PEER} and a["bgp_ambiguous"] == {}


def test_ipvpn_and_internet_on_same_subnet_do_not_take_each_others_peer():
    """Instance se nebere jako 'kterakoli z mych nebo master' - IPVPN by pak
    pohltila master peera Internet sluzby na stejne /30."""
    internet = Scope(
        id="svc:inet:Internet",
        kind="service",
        key=ScopeKey("inet", "Internet", None),
        selectors=Selectors(interfaces=["ge-0/0/3.0"], bgp_neighbors=[SHARED_PEER]),
    )
    master_facts = {"bgp": bgp_records({SHARED_PEER: _bgp_entry(None)})}
    vrf_facts = {"bgp": bgp_records({SHARED_PEER: _bgp_entry("customer-b")})}

    assert _vrf_scope("customer-b", "ge-0/0/2.200").select(master_facts)["bgp"] == {}
    assert internet.select(vrf_facts)["bgp"] == {}
    assert set(internet.select(master_facts)["bgp"]) == {SHARED_PEER}


def test_internet_scope_in_virtual_router_takes_master_peer():
    """Zrcadli parsers/core.py:_assign_bgp_neighbors - Internet sluzba bere
    peery z globalniho `protocols bgp`, i kdyz je rozhrani v instanci."""
    scope = Scope(
        id="svc:inet:Internet",
        kind="service",
        key=ScopeKey("inet", "Internet", None),
        selectors=Selectors(
            interfaces=["ge-0/0/3.0"],
            routing_instances=["VR-INET"],
            bgp_neighbors=[SHARED_PEER],
        ),
    )

    selected = scope.select({"bgp": bgp_records({SHARED_PEER: _bgp_entry(None)})})

    assert set(selected["bgp"]) == {SHARED_PEER}


def test_core_loopback_takes_master_ibgp_peer():
    scope = Scope(
        id="svc:lo0:Core",
        kind="service",
        key=ScopeKey("lo0", "Core", "loopback"),
        selectors=Selectors(interfaces=["lo0.0"], bgp_neighbors=["150.0.0.1"]),
    )

    selected = scope.select({"bgp": bgp_records({"150.0.0.1": _bgp_entry(None)})})

    assert set(selected["bgp"]) == {"150.0.0.1"}


def test_deactivated_peer_with_live_foreign_vrf_record_is_not_selected():
    """Deaktivovany peer: zaznam cizi VRF na jeho adrese proste neni jeho -
    zadna kolize, zadna nejednoznacnost."""
    scope = Scope(
        id="svc:customer-b:IPVPN",
        kind="service",
        key=ScopeKey("customer-b", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.200"],
            routing_instances=["customer-b"],
            bgp_neighbors_inactive=[SHARED_PEER],
        ),
    )

    selected = scope.select({"bgp": bgp_records({SHARED_PEER: _bgp_entry("customer-a")})})

    assert selected["bgp"] == {}
    assert selected["bgp_ambiguous"] == {}


def test_bgp_same_address_two_vrfs_each_scope_gets_its_own():
    facts = {"bgp": [
        bgp_record("192.168.1.2", routing_instance="customer-a", state="Idle"),
        bgp_record("192.168.1.2", routing_instance="customer-b"),
    ]}
    a = _vrf_scope("customer-a", "ge-0/0/1.100").select(facts)
    b = _vrf_scope("customer-b", "ge-0/0/2.200").select(facts)
    assert a["bgp"]["192.168.1.2"]["state"] == "Idle"
    assert b["bgp"]["192.168.1.2"]["state"] == "Established"
    assert a["bgp_ambiguous"] == {} and b["bgp_ambiguous"] == {}
    assert "bgp_collisions" not in a


def test_bgp_two_link_local_records_in_one_ri_are_ambiguous():
    facts = {"bgp": [
        bgp_record("fe80::2", routing_instance="customer-a", local_interface="ge-0/0/1.100"),
        bgp_record("fe80::2", routing_instance="customer-a", local_interface="ge-0/0/1.200"),
    ]}
    scope = Scope(
        id="svc:customer-a:IPVPN",
        kind="service",
        key=ScopeKey("customer-a", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/1.100"],
            routing_instances=["customer-a"],
            bgp_neighbors=["fe80::2"],
        ),
    )

    view = scope.select(facts)

    assert view["bgp"] == {}
    assert view["bgp_ambiguous"] == {"fe80::2": 2}


def test_bfd_session_on_foreign_interface_is_not_selected():
    """Zabiji mutanta: vyber `bfd` jen podle adresy (bez porovnani rozhrani).

    Od schematu 14 zaznam cizi VRF na nasi adrese proste neni nas - zadna
    kolize, zadna nejednoznacnost (kazda VRF ma svuj zaznam s vlastnim
    rozhranim)."""
    facts = {"bfd": bfd_records({SHARED_PEER: {"state": "Up", "interface": "ge-0/0/1.100"}})}

    selected = _vrf_scope("customer-b", "ge-0/0/2.200").select(facts)

    assert selected["bfd"] == {}
    assert selected["bfd_ambiguous"] == {}


def test_bfd_session_on_own_interface_is_selected():
    facts = {"bfd": bfd_records({SHARED_PEER: {"state": "Up", "interface": "ge-0/0/1.100"}})}

    selected = _vrf_scope("customer-a", "ge-0/0/1.100").select(facts)

    assert set(selected["bfd"]) == {SHARED_PEER}
    assert selected["bfd_ambiguous"] == {}


def test_multihop_bfd_session_without_interface_is_selected_by_peer():
    """Multihop session rozhrani nenese (laborka: 198.11.14.4 v L3VPN-CPE14-UNI),
    takze zbyva jen adresa."""
    facts = {"bfd": bfd_records({SHARED_PEER: {"state": "Up", "interface": None}})}

    selected = _vrf_scope("customer-b", "ge-0/0/2.200").select(facts)

    assert set(selected["bfd"]) == {SHARED_PEER}
    assert selected["bfd_ambiguous"] == {}


def _ipvpn(ri: str, peer: str = SHARED_PEER, iface: str = "ae0.100") -> Scope:
    return Scope(
        id=f"svc:{ri}:IPVPN",
        kind="service",
        key=ScopeKey(description=ri, service_type="IPVPN"),
        selectors=Selectors(interfaces=[iface], routing_instances=[ri], bgp_neighbors=[peer]),
    )


def test_bfd_same_neighbor_two_interfaces_each_scope_gets_its_own():
    facts = {"bfd": [
        bfd_record("192.168.1.2", interface="ae0.100", state="Down"),
        bfd_record("192.168.1.2", interface="ae0.200"),
    ]}
    a = _ipvpn("customer-a").select(facts)
    b = _ipvpn("customer-b", iface="ae0.200").select(facts)
    assert a["bfd"]["192.168.1.2"]["state"] == "Down"
    assert b["bfd"]["192.168.1.2"]["state"] == "Up"
    assert a["bfd_ambiguous"] == {} and "bfd_collisions" not in a


def test_bfd_two_multihop_same_neighbor_is_ambiguous():
    facts = {"bfd": [bfd_record("198.11.14.4"), bfd_record("198.11.14.4", state="Down")]}
    view = _ipvpn("CUST", peer="198.11.14.4").select(facts)
    assert view["bfd"] == {}
    assert view["bfd_ambiguous"] == {"198.11.14.4": 2}


def test_bfd_single_hop_on_own_interface_wins_over_multihop():
    facts = {"bfd": [
        bfd_record("192.168.1.2", interface="ae0.100"),
        bfd_record("192.168.1.2"),  # multihop jine VRF
    ]}
    view = _ipvpn("customer-a").select(facts)
    assert view["bfd"]["192.168.1.2"]["interface"] == "ae0.100"
    assert view["bfd_ambiguous"] == {}


def test_bfd_single_multihop_is_owned_by_address():
    facts = {"bfd": [bfd_record("198.11.14.4")]}
    view = _ipvpn("CUST", peer="198.11.14.4").select(facts)
    assert view["bfd"]["198.11.14.4"]["multihop"] is True


def _eline(iface, ri="EVPN-VPWS-LOCAL"):
    return Scope(
        id=f"svc:{iface}", kind="service",
        key=ScopeKey(description=iface, service_type="E-Line", service_subtype="vpws"),
        selectors=Selectors(interfaces=[iface], physical_interfaces=["et-0/0/8"],
                            routing_instances=[ri]),
    )


def _ac(name, status="Up"):
    return {
        "name": name, "status": status, "mode": "single-homed", "pseudowire_status": None,
        "local_sid": {"value": 100, "peers": [], "local_interface": None},
        "remote_sid": {"value": 200, "peers": [], "local_interface": None},
    }


def _vpws_facts():
    return {"evpn_vpws": {"EVPN-VPWS-LOCAL": {"interfaces": [
        _ac("et-0/0/8.211"), _ac("et-0/0/8.212", status="Down"),
    ]}}}


def test_select_vpws_keeps_only_own_ac():
    """Review E2: scope .211 nesmi videt AC .212 tehoz VPWS instance -
    Down .212 by jinak rozsvitil FAIL na zdrave sluzbe."""
    facts = _vpws_facts()
    for iface in ("et-0/0/8.211", "et-0/0/8.212"):
        acs = _eline(iface).select(facts)["evpn_vpws"]["EVPN-VPWS-LOCAL"]["interfaces"]
        assert [ac["name"] for ac in acs] == [iface]
    # physical_interfaces ["et-0/0/8"] nesmi vybrat unity
    assert len(facts["evpn_vpws"]["EVPN-VPWS-LOCAL"]["interfaces"]) == 2, "select zmenil fakta"


def test_select_vpws_scope_whose_ac_is_missing_gets_empty_list():
    selected = _eline("et-0/0/8.213").select(_vpws_facts())
    assert selected["evpn_vpws"] == {"EVPN-VPWS-LOCAL": {"interfaces": []}}


def test_select_vpws_real_remote_pw_names_survive_filter():
    """ae0.224 / ge-0/0/3.0 z labu: filtr podle jmena IFL musi sedet."""
    for iface in ("ae0.224", "ge-0/0/3.0"):
        scope = Scope(
            id=f"svc:{iface}", kind="service",
            key=ScopeKey(description=iface, service_type="E-Line", service_subtype="vpws"),
            selectors=Selectors(interfaces=[iface], physical_interfaces=[iface.split(".")[0]],
                                routing_instances=["EVPN-VPWS-CPE23-UNI"]),
        )
        facts = {"evpn_vpws": {"EVPN-VPWS-CPE23-UNI": {"interfaces": [_ac(iface)]}}}
        acs = scope.select(facts)["evpn_vpws"]["EVPN-VPWS-CPE23-UNI"]["interfaces"]
        assert [ac["name"] for ac in acs] == [iface]


def test_select_vpws_other_instance_not_selected():
    assert _eline("et-0/0/8.211", ri="OTHER").select(_vpws_facts())["evpn_vpws"] == {}
