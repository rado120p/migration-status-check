"""Selekce multicast areas (spec 2026-09-02). multicast_route se vybira podle
instance (master | RI), ne podle rozhrani - filtr per (S,G) dela check."""

from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope

FACTS = {
    "igmp_group": {
        "et-0/0/8.11": [{"source": "10.11.11.1", "group": "232.1.1.1"}],
        "irb.2": [{"source": "10.12.12.1", "group": "239.1.1.1"}],
    },
    "multicast_route": {
        "master": {"10.11.11.1,232.1.1.1": {"upstream_interface": "et-0/0/0.0"}},
        "MVPN-RI": {"10.12.12.1,239.1.1.1": {"upstream_interface": "lsi.1048576"}},
    },
    "mvpn_instance": {"MVPN-RI": {"c_multicast": []}},
}


def _scope(service_type, subtype, interfaces, instances=()):
    return Scope(
        id=f"svc:x:{service_type}", kind="service",
        key=ScopeKey("x", service_type, subtype),
        selectors=Selectors(interfaces=list(interfaces), routing_instances=list(instances)),
    )


def test_internet_multicast_scope_gets_its_igmp_and_master_table():
    selected = _scope("Internet", "multicast", ["et-0/0/8.11"]).select(FACTS)
    assert set(selected["igmp_group"]) == {"et-0/0/8.11"}
    assert set(selected["multicast_route"]) == {"master"}
    assert selected["mvpn_instance"] == {}


def test_mvpn_scope_gets_ri_table_and_mvpn_instance():
    selected = _scope("IPVPN", "mvpn", ["irb.2"], ["MVPN-RI"]).select(FACTS)
    assert set(selected["igmp_group"]) == {"irb.2"}
    assert set(selected["multicast_route"]) == {"MVPN-RI"}
    assert set(selected["mvpn_instance"]) == {"MVPN-RI"}


def test_core_loopback_gets_master_table_but_transit_does_not():
    """Mutant kill (2026-09-03, overeno spustenim): smazani
    'measures_multicast' gate (transit by dostal tabulku)."""
    loopback = _scope("Core", "loopback", ["lo0.0"]).select(FACTS)
    transit = _scope("Core", "transit", ["et-0/0/0.0"]).select(FACTS)
    assert set(loopback["multicast_route"]) == {"master"}
    assert transit["multicast_route"] == {}


def test_elan_scope_sees_no_multicast_area():
    selected = _scope("E-LAN", "vlan-aware", ["ae0.15"], ["EVPN-RI"]).select(FACTS)
    assert selected["igmp_group"] == {}
    assert selected["multicast_route"] == {}
    assert selected["mvpn_instance"] == {}


def test_missing_instance_table_yields_empty_dict_not_key_error():
    selected = _scope("IPVPN", "mvpn", ["irb.5"], ["OTHER-RI"]).select(FACTS)
    assert selected["multicast_route"] == {}


def test_device_scope_passes_everything():
    selected = device_scope().select(FACTS)
    assert selected["multicast_route"] == FACTS["multicast_route"]
    assert selected["mvpn_instance"] == FACTS["mvpn_instance"]
