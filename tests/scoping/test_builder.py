from pathlib import Path

import pytest

from migration_validator.models.inventory import Inventory, ServiceEntry
from migration_validator.models.scope import Selectors
from migration_validator.scoping.builder import build_scopes, is_management

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _entry(**kwargs) -> ServiceEntry:
    return ServiceEntry.from_dict(kwargs)


@pytest.mark.parametrize(
    "interface,expected",
    [
        ("fxp0.0", True),
        ("em0", True),
        ("me0.0", True),
        ("vme.0", True),
        ("bme0", True),
        ("re0:mgmt-0.0", True),
        ("re1:mgmt-0.0", True),
        ("ge-0/0/2.113", False),
        ("et-0/0/8.13", False),
        ("ae0.14", False),
        ("irb.14", False),
        ("lo0.0", False),
    ],
)
def test_is_management(interface, expected):
    assert is_management(interface) is expected


def test_layer1_feeds_physical_selector_and_becomes_its_own_scope():
    """Layer1 zaznam s aspon jednou eligible sluzbou na portu je ted zaroven

    zdrojem physical_interfaces selektoru sluzby (jako drive) i vlastnim
    scopem kind=layer1 (Task 7) - deti nema, protoze v tomto fixture zadny
    dalsi zaznam neni na portu ge-0/0/2 zavisly krome L3VPN-CPE13-NNI.
    """
    inventory = Inventory(
        device="172.20.20.4",
        entries=[
            _entry(interface="ge-0/0/2", description="NNI1-TO-CPE1", service_type="Layer1"),
            _entry(
                interface="ge-0/0/2.113",
                description="L3VPN-CPE13-NNI",
                service_type="IPVPN",
                routing_instance="L3VPN-CPE13-NNI",
                bgp_neighbor=["198.11.13.2"],
                ipv4_address=["198.11.13.1/30"],
                customer_vlan=["113"],
            ),
        ],
    )

    scopes = build_scopes(inventory)

    assert len(scopes) == 2
    service = next(s for s in scopes if s.kind == "service")
    assert service.id == "svc:L3VPN-CPE13-NNI:IPVPN"
    assert service.selectors.interfaces == ["ge-0/0/2.113"]
    assert service.selectors.physical_interfaces == ["ge-0/0/2"]
    assert service.selectors.bgp_neighbors == ["198.11.13.2"]
    assert service.selectors.routing_instances == ["L3VPN-CPE13-NNI"]

    l1 = next(s for s in scopes if s.kind == "layer1")
    assert l1.id == "l1:ge-0/0/2"
    assert l1.selectors.interfaces == ["ge-0/0/2"]


def test_management_interfaces_are_excluded_even_when_typed_internet():
    inventory = Inventory(
        device="172.20.20.5",
        entries=[
            _entry(interface="fxp0.0", service_type="Internet", ipv4_address=["10.0.0.15/24"]),
            _entry(
                interface="re0:mgmt-0.0",
                service_type="Internet",
                ipv4_address=["172.20.20.5/24"],
            ),
        ],
    )

    assert build_scopes(inventory) == []


def test_core_and_loopback_produce_scopes():
    inventory = Inventory(
        device="172.20.20.4",
        entries=[
            _entry(interface="lo0.0", service_type="Core", ipv4_address=["150.0.0.11/32"]),
            _entry(
                interface="ge-0/0/0.0",
                description="clab-pop-migration-P1;et-0/0/0",
                service_type="Core",
                protocol=["inet", "iso", "mpls"],
            ),
        ],
    )

    scopes = build_scopes(inventory)

    assert [scope.key.service_type for scope in scopes] == ["Core", "Core"]
    assert scopes[0].id == "svc:lo0.0:Core"  # bez description se pouzije nazev rozhrani


def test_duplicate_key_gets_interface_suffix():
    inventory = Inventory(
        device="172.20.20.4",
        entries=[
            _entry(interface="ge-0/0/2.13", description="SAME", service_type="Internet"),
            _entry(interface="ge-0/0/3.13", description="SAME", service_type="Internet"),
        ],
    )

    scopes = build_scopes(inventory)

    assert [scope.id for scope in scopes] == [
        "svc:SAME:Internet:ge-0/0/2.13",
        "svc:SAME:Internet:ge-0/0/3.13",
    ]


def test_irb_virtual_gw_lands_in_selectors():
    inventory = Inventory(
        device="172.20.20.5",
        entries=[
            _entry(
                interface="irb.14",
                description="EVPN-VLAN-AWARE-INTERNET",
                service_type="Internet",
                ipv4_address=["152.11.14.2/29"],
                virtual_gw_ipv4_address=["152.11.14.1"],
                bgp_neighbor=["152.11.14.4"],
            )
        ],
    )

    scope = build_scopes(inventory)[0]

    assert scope.selectors.virtual_gw_v4 == ["152.11.14.1"]
    assert scope.selectors.local_ipv4 == ["152.11.14.2/29"]


def test_selectors_keep_families_apart():
    inventory = Inventory(
        device="172.20.20.5",
        entries=[
            ServiceEntry(
                interface="et-0/0/8.13",
                service_type="Internet",
                description="INTERNET-CPE13-NNI",
                ipv4_address=["152.11.13.1/30"],
                ipv6_address=["2001:abcd:11:13::a/127"],
                virtual_gw_ipv4_address=["152.11.13.254"],
                virtual_gw_ipv6_address=[],
            )
        ],
    )

    scope = build_scopes(inventory)[0]

    assert scope.selectors.local_ipv4 == ["152.11.13.1/30"]
    assert scope.selectors.local_ipv6 == ["2001:abcd:11:13::a/127"]
    assert scope.selectors.virtual_gw_v4 == ["152.11.13.254"]
    assert scope.selectors.virtual_gw_v6 == []


def test_scope_inherits_deactivation_from_the_inventory_entry():
    """Prevod je 1:1 - build_scopes dela jeden scope na jeden zaznam.

    Zabiji mutanta: build_scopes, ktere priznaky neopise a necha default True.
    """
    inventory = Inventory(
        device="r1",
        entries=[
            _entry(
                interface="ge-0/0/4.0",
                description="L3VPN-CPE14-UNI",
                service_type="IPVPN",
                routing_instance="L3VPN-CPE14-UNI",
                routing_instance_active=True,
                interface_active=False,
            )
        ],
    )

    scopes = build_scopes(inventory)

    assert len(scopes) == 1
    assert scopes[0].routing_instance_active is True
    assert scopes[0].interface_active is False


def test_real_inventory_files_produce_expected_scope_counts():
    from migration_validator.models.inventory import load_inventory

    for name in ("172.20.20.4.yml", "172.20.20.5.yml"):
        scopes = build_scopes(load_inventory(FIXTURES / name))
        assert not any(
            scope.selectors.interfaces[0].startswith(("fxp", "re0:mgmt"))
            for scope in scopes
        )


def test_layer1_port_se_sluzbou_dostane_scope():
    inventory = Inventory(device="dev", entries=[
        ServiceEntry(interface="ae0", service_type="Layer1",
                     description="EX1;ae0", service_subtype="physical-port",
                     lag_members=["et-0/0/5"]),
        ServiceEntry(interface="ae0.14", service_type="Internet",
                     description="INET"),
    ])
    scopes = build_scopes(inventory)
    l1 = [s for s in scopes if s.kind == "layer1"]
    assert len(l1) == 1
    assert l1[0].id == "l1:ae0"
    assert l1[0].key.service_type == "Layer1"
    assert l1[0].selectors.interfaces == ["ae0"]
    assert l1[0].selectors.lag_members == ["et-0/0/5"]


def test_layer1_port_bez_sluzeb_scope_nedostane():
    inventory = Inventory(device="dev", entries=[
        ServiceEntry(interface="ge-0/0/9", service_type="Layer1"),
    ])
    assert not [s for s in build_scopes(inventory) if s.kind == "layer1"]


def test_management_layer1_scope_nedostane():
    inventory = Inventory(device="dev", entries=[
        ServiceEntry(interface="fxp0", service_type="Layer1"),
        ServiceEntry(interface="fxp0.0", service_type="Internet"),
    ])
    assert not [s for s in build_scopes(inventory) if s.kind == "layer1"]


def test_interni_layer1_rozhrani_scope_nedostane():
    """irb a lo0 nejsou tranzitni porty - nemaji optiku ani chyby k mereni,

    L1 blok pro ne by byl jen sum a pozdeji by pod ne padaly sluzby nesmyslne.
    """
    inventory = Inventory(device="dev", entries=[
        ServiceEntry(interface="irb", service_type="Layer1"),
        ServiceEntry(interface="irb.4094", service_type="Internet", description="MGMT-GW"),
        ServiceEntry(interface="lo0", service_type="Layer1"),
        ServiceEntry(interface="lo0.0", service_type="Core", description="LO"),
    ])
    assert not [s for s in build_scopes(inventory) if s.kind == "layer1"]


def test_tranzitni_porty_si_scope_zachovaji():
    inventory = Inventory(device="dev", entries=[
        ServiceEntry(interface="ae0", service_type="Layer1",
                     description="EX1;ae0", service_subtype="physical-port",
                     lag_members=["et-0/0/5"]),
        ServiceEntry(interface="ae0.14", service_type="Internet", description="INET"),
        ServiceEntry(interface="ge-0/0/2", service_type="Layer1", description="NNI"),
        ServiceEntry(interface="ge-0/0/2.113", service_type="IPVPN",
                     description="L3VPN-CPE13-NNI", routing_instance="L3VPN-CPE13-NNI"),
    ])
    l1_ids = {s.id for s in build_scopes(inventory) if s.kind == "layer1"}
    assert l1_ids == {"l1:ae0", "l1:ge-0/0/2"}


def test_mvpn_site_protece_do_selektoru():
    entry = ServiceEntry(
        interface="irb.10", service_type="IPVPN", service_subtype="mvpn",
        routing_instance="NGMVPN-PIM-SOURCE", mvpn_site=["sender"],
    )
    (scope,) = build_scopes(Inventory(device="x", entries=[entry]))
    assert scope.selectors.mvpn_site == ["sender"]
    assert Selectors.from_dict(scope.selectors.to_dict()).mvpn_site == ["sender"]


def test_l2_interface_protece_do_selektoru():
    entry = ServiceEntry(
        interface="irb.2", service_type="IPVPN", service_subtype="mvpn-igmp",
        routing_instance="MULTICAST-STREAM-B-MUX1-RECEIVER", l2_interface=["ge-0/0/2.12"],
    )
    (scope,) = build_scopes(Inventory(device="x", entries=[entry]))
    assert scope.selectors.l2_interfaces == ["ge-0/0/2.12"]
    assert scope.id == "svc:irb.2:IPVPN"
