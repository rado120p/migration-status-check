from pathlib import Path

import pytest

from migration_validator.models.inventory import Inventory, ServiceEntry
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


def test_layer1_does_not_become_a_scope_but_feeds_physical_selector():
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

    assert len(scopes) == 1
    scope = scopes[0]
    assert scope.id == "svc:L3VPN-CPE13-NNI:IPVPN"
    assert scope.selectors.interfaces == ["ge-0/0/2.113"]
    assert scope.selectors.physical_interfaces == ["ge-0/0/2"]
    assert scope.selectors.bgp_neighbors == ["198.11.13.2"]
    assert scope.selectors.routing_instances == ["L3VPN-CPE13-NNI"]


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


def test_real_inventory_files_produce_expected_scope_counts():
    from migration_validator.models.inventory import load_inventory

    for name in ("172.20.20.4.yml", "172.20.20.5.yml"):
        scopes = build_scopes(load_inventory(FIXTURES / name))
        types = {scope.key.service_type for scope in scopes}
        assert "Layer1" not in types
        assert not any(
            scope.selectors.interfaces[0].startswith(("fxp", "re0:mgmt"))
            for scope in scopes
        )
