from migration_validator.models.scope import (
    FACT_AREAS,
    Scope,
    ScopeKey,
    Selectors,
    device_scope,
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
    "bgp": {
        "198.11.13.2": {"state": "Established"},
        "10.9.9.2": {"state": "Active"},
    },
    "evpn_vpws": {"EVPN-VPWS-CPE13-NNI": {"status": "Up"}},
    "evpn_esi": {"00:11": {"status": "Up", "interface": "ge-0/0/2.113"}},
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


def test_select_filters_ping_by_scope_id():
    selected = _service_scope().select(FACTS, PROBES)
    assert [probe["target"] for probe in selected["ping"]] == ["198.11.13.2"]


def test_device_scope_selects_everything():
    selected = device_scope().select(FACTS, PROBES)
    assert selected["interfaces"] == FACTS["interfaces"]
    assert selected["arp"] == FACTS["arp"]
    assert selected["bgp"] == FACTS["bgp"]
    assert len(selected["ping"]) == 2


def test_device_scope_is_flagged():
    assert device_scope().is_device is True
    assert _service_scope().is_device is False


def test_select_tolerates_missing_fact_areas():
    selected = _service_scope().select({}, None)
    assert selected["interfaces"] == {}
    assert selected["arp"] == []
    assert selected["ping"] == []


def test_scope_round_trip():
    scope = _service_scope()
    assert Scope.from_dict(scope.to_dict()) == scope


def test_nd_area_is_a_list_when_missing():
    """Spatny prazdny typ by check videl jako prazdny slovnik a tise prosel."""
    selected = device_scope().select({})

    assert selected["nd"] == []
    assert "nd" in FACT_AREAS


ROUTE_FACTS = {
    "inet.0": {
        "198.62.1.0/29": {"next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": True},
        "10.9.9.0/24": {"next_hop": ["10.9.9.1"], "via": ["et-0/0/9.0"], "active": True},
    },
    "L3VPN-A.inet.0": {
        "172.26.1.0/29": {"next_hop": ["198.11.13.2"], "via": ["et-0/0/8.113"], "active": True},
    },
}

BFD_FACTS = {
    "152.11.13.2": {"state": "Up", "interface": "et-0/0/8.13"},
    "198.11.14.2": {"state": "Down", "interface": "et-0/0/8.114"},
}


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
        "inet.0": {
            "198.62.1.0/29": {
                "next_hop": ["152.11.13.2"],
                "via": ["et-0/0/8.13"],
                "active": True,
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

    assert set(selected["routes"]) == {"L3VPN-A.inet.0"}


def test_bfd_sessions_are_selected_by_bgp_neighbors():
    """Session patri scopu podle peeru, ne podle zameru - viz AR-14.

    Kdyby se vybiralo podle bfd_peers, session peeru, ktereho parser do
    zameru nedoplnil, by se do scope nedostala a chyba v pruchodu hierarchii
    by se schovala pred vystupem nastroje.
    """
    scope = _scope_with(bgp_neighbors=["152.11.13.2"], bfd_peers=[])

    selected = scope.select({"bfd": BFD_FACTS})

    assert set(selected["bfd"]) == {"152.11.13.2"}


def test_bgp_session_of_deactivated_peer_is_selected():
    """Deaktivovany peer se zivou session patri scopu stejne jako aktivni.

    Kdyby se session nevybrala, checks/bgp.py by peera videl jako
    bezsessioveho, dal by mu SKIP 'deaktivovan' a rozpor 'konfigurace
    vypnuto, zarizeni bezi' by z reportu zmizel.

    Zabiji mutanta: vyber `bgp` filtrovany jen pres `bgp_neighbors`
    (bez `bgp_neighbors_inactive`).
    """
    scope = _scope_with(bgp_neighbors=[], bgp_neighbors_inactive=["152.11.13.2"])

    selected = scope.select({"bgp": {"152.11.13.2": {"state": "Established"}}})

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


def test_device_scope_sees_all_routes_and_sessions():
    """Rezim bez inventory je podle AR-10 doporuceny zpusob prohlidky zarizeni."""
    selected = device_scope().select({"routes": ROUTE_FACTS, "bfd": BFD_FACTS})

    assert selected["routes"] == ROUTE_FACTS
    assert selected["bfd"] == BFD_FACTS


def test_missing_areas_come_back_as_empty_mappings():
    scope = _scope_with()

    selected = scope.select({})

    assert selected["routes"] == {}
    assert selected["bfd"] == {}


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
