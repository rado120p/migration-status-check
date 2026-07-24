from migration_validator.models.scope import (
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
            local_addresses=["198.11.13.1/30"],
            virtual_gw=[],
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
