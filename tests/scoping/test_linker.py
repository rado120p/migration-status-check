"""Vazba L3 (IRB) scope na L2 (E-LAN) scope teze EVPN instance."""

from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.scoping.linker import ScopeLink, link_scopes


def _l2_scope(
    scope_id="svc:EVPN-VLAN-AWARE-CPE14:E-LAN",
    interface="ae0.15",
    instance="EVPN-VLAN-AWARE-POP1",
    vlans=("15",),
):
    return Scope(
        id=scope_id,
        kind="service",
        key=ScopeKey("EVPN-VLAN-AWARE-CPE14", "E-LAN", "vlan-aware"),
        selectors=Selectors(
            interfaces=[interface],
            routing_instances=[instance],
            vlans=list(vlans),
        ),
    )


def _l3_scope(
    scope_id="svc:L3VPN-CPE14-UNI:IPVPN",
    interface="irb.15",
    service_type="IPVPN",
    instances=("L3VPN-CPE14-UNI",),
):
    return Scope(
        id=scope_id,
        kind="service",
        key=ScopeKey("L3VPN-CPE14-UNI", service_type, None),
        selectors=Selectors(
            interfaces=[interface],
            routing_instances=list(instances),
        ),
    )


def _facts(instance="EVPN-VLAN-AWARE-POP1", irb="irb.15", context="L3VPN-CPE14-UNI"):
    return {
        instance: {
            "local_interfaces": {"total": 2, "up": 2, "entries": []},
            "irb_interfaces": {
                "total": 1,
                "up": 1,
                "entries": [{"name": irb, "status": "Up", "l3_context": context}],
            },
            "neighbors": {"total": 1, "addresses": ["10.0.0.1"]},
            "esis": {},
        }
    }


def test_links_ipvpn_scope_via_context_and_unit():
    l2, l3 = _l2_scope(), _l3_scope()
    links = link_scopes([l3, l2], _facts())
    assert links == [
        ScopeLink(
            l3_scope_id=l3.id,
            l2_scope_id=l2.id,
            irb_interface="irb.15",
            l2_interface="ae0.15",
            l3_context="L3VPN-CPE14-UNI",
            l2_instance="EVPN-VLAN-AWARE-POP1",
        )
    ]


def test_master_context_links_internet_scope_without_instance():
    l2 = _l2_scope(interface="ae0.14", vlans=("14",))
    l3 = _l3_scope(
        scope_id="svc:EVPN-VLAN-AWARE-INTERNET:Internet",
        interface="irb.14",
        service_type="Internet",
        instances=(),
    )
    links = link_scopes([l3, l2], _facts(irb="irb.14", context="master"))
    assert len(links) == 1
    assert links[0].l3_context == "master"
    assert links[0].irb_interface == "irb.14"


def test_master_context_does_not_link_scope_with_instance():
    # IPVPN scope ma RI - "master" na nej nesmi ukazat
    l2 = _l2_scope(interface="ae0.14", vlans=("14",))
    l3 = _l3_scope(interface="irb.14")
    assert link_scopes([l3, l2], _facts(irb="irb.14", context="master")) == []


def test_unit_mismatch_creates_no_link():
    # irb.15 patri k VLAN 15; L2 scope s VLAN 14 neni jeho tranzit
    l2 = _l2_scope(interface="ae0.14", vlans=("14",))
    l3 = _l3_scope()
    assert link_scopes([l3, l2], _facts()) == []


def test_vlan_match_without_interface_unit_is_enough():
    # tranzit muze byt trunk bez unit-cisla shodneho s VLAN - staci selectors.vlans
    l2 = _l2_scope(interface="ae0.100", vlans=("15",))
    l3 = _l3_scope()
    links = link_scopes([l3, l2], _facts())
    assert len(links) == 1
    assert links[0].l2_interface == "ae0.100"


def test_missing_instance_facts_creates_no_link():
    assert link_scopes([_l3_scope(), _l2_scope()], {}) == []


def test_irb_without_context_creates_no_link():
    facts = _facts()
    del facts["EVPN-VLAN-AWARE-POP1"]["irb_interfaces"]["entries"][0]["l3_context"]
    assert link_scopes([_l3_scope(), _l2_scope()], facts) == []


def test_ambiguous_l3_candidates_create_no_link():
    # dva L3 scopy se stejnym irb.15 i stejnou RI = nejednoznacne, vazba se nevytvori
    l3a = _l3_scope(scope_id="svc:A:IPVPN")
    l3b = _l3_scope(scope_id="svc:B:IPVPN")
    assert link_scopes([l3a, l3b, _l2_scope()], _facts()) == []


def test_context_mismatch_creates_no_link():
    l3 = _l3_scope(instances=("JINA-RI",))
    assert link_scopes([l3, _l2_scope()], _facts()) == []


def test_device_scope_is_ignored():
    from migration_validator.models.scope import device_scope

    links = link_scopes([device_scope(), _l3_scope(), _l2_scope()], _facts())
    assert len(links) == 1


def test_each_scope_links_at_most_once():
    # jedna instance se dvema IRB, ale jen jeden L2 scope - druha vazba nevznikne
    facts = _facts()
    facts["EVPN-VLAN-AWARE-POP1"]["irb_interfaces"]["entries"].append(
        {"name": "irb.16", "status": "Up", "l3_context": "L3VPN-CPE14-UNI"}
    )
    l2 = _l2_scope(vlans=("15", "16"))
    l3 = _l3_scope()
    links = link_scopes([l3, l2], facts)
    assert len(links) == 1
