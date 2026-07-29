import textwrap

import pytest

from migration_validator.models.inventory import ServiceEntry, load_inventory


def test_from_dict_fills_defaults_for_missing_keys():
    entry = ServiceEntry.from_dict({"interface": "ge-0/0/1.0", "service_type": "Internet"})
    assert entry.interface == "ge-0/0/1.0"
    assert entry.service_type == "Internet"
    assert entry.description is None
    assert entry.service_subtype is None
    assert entry.ipv4_address == []
    assert entry.bgp_neighbor == []
    assert entry.active is True


def test_from_dict_ignores_unknown_keys():
    entry = ServiceEntry.from_dict(
        {
            "interface": "ge-0/0/1.0",
            "service_type": "Internet",
            "detection_confidence": "medium",
            "detection_reason": ["cokoliv"],
        }
    )
    assert entry.interface == "ge-0/0/1.0"


@pytest.mark.parametrize(
    "interface,expected",
    [
        ("ge-0/0/2.113", "ge-0/0/2"),
        ("ge-0/0/2", "ge-0/0/2"),
        ("re0:mgmt-0.0", "re0:mgmt-0"),
        ("irb.14", "irb"),
    ],
)
def test_physical_name(interface, expected):
    entry = ServiceEntry.from_dict({"interface": interface, "service_type": "Internet"})
    assert entry.physical_name == expected


def test_load_inventory(tmp_path):
    path = tmp_path / "device.yml"
    path.write_text(
        textwrap.dedent(
            """\
            schema_version: 2
            device: 172.20.20.4
            interfaces:
            - interface: ge-0/0/2.113
              description: L3VPN-CPE13-NNI
              service_type: IPVPN
              service_subtype: null
              ipv4_address:
              - 198.11.13.1/30
              ipv6_address: []
              virtual_gw_ipv4_address: []
              virtual_gw_ipv6_address: []
              routing_instance: L3VPN-CPE13-NNI
              active: true
              protocol:
              - bgp
              bgp_neighbor:
              - 198.11.13.2
              bridge_domain: []
              customer_vlan:
              - '113'
            """
        ),
        encoding="utf-8",
    )

    inventory = load_inventory(path)

    assert inventory.device == "172.20.20.4"
    assert len(inventory.entries) == 1
    entry = inventory.entries[0]
    assert entry.description == "L3VPN-CPE13-NNI"
    assert entry.routing_instance == "L3VPN-CPE13-NNI"
    assert entry.bgp_neighbor == ["198.11.13.2"]
    assert entry.customer_vlan == ["113"]


def test_load_inventory_rejects_missing_interfaces_key(tmp_path):
    path = tmp_path / "bad.yml"
    path.write_text("schema_version: 2\ndevice: 1.2.3.4\n", encoding="utf-8")

    with pytest.raises(ValueError, match="interfaces"):
        load_inventory(path)


def test_entry_keeps_families_apart():
    entry = ServiceEntry.from_dict(
        {
            "interface": "ge-0/0/2.13",
            "service_type": "Internet",
            "ipv4_address": ["152.11.13.1/30"],
            "ipv6_address": ["2001:abcd:11:13::a/127"],
            "virtual_gw_ipv4_address": ["152.11.13.254"],
            "virtual_gw_ipv6_address": [],
        }
    )

    assert entry.ipv4_address == ["152.11.13.1/30"]
    assert entry.ipv6_address == ["2001:abcd:11:13::a/127"]
    assert entry.virtual_gw_ipv4_address == ["152.11.13.254"]
    assert entry.virtual_gw_ipv6_address == []


def test_roundtrip_through_dict():
    data = {
        "interface": "ge-0/0/2.13",
        "service_type": "Internet",
        "ipv4_address": ["152.11.13.1/30"],
        "ipv6_address": ["2001:abcd:11:13::a/127"],
        "virtual_gw_ipv4_address": [],
        "virtual_gw_ipv6_address": [],
    }
    entry = ServiceEntry.from_dict(data)

    for key, value in data.items():
        assert entry.to_dict()[key] == value


def test_old_inventory_fails_loudly(tmp_path):
    """Bez verze by stara inventory tise prisla o adresy a sluzby by svitily zelene."""
    path = tmp_path / "old.yml"
    path.write_text(
        "device: 172.20.20.4\n"
        "interfaces:\n"
        "  - interface: ge-0/0/2.13\n"
        "    service_type: Internet\n"
        "    ip_address: ['152.11.13.1/30']\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema_version"):
        load_inventory(path)


def test_current_inventory_loads(tmp_path):
    path = tmp_path / "new.yml"
    path.write_text(
        "schema_version: 2\n"
        "device: 172.20.20.4\n"
        "interfaces:\n"
        "  - interface: ge-0/0/2.13\n"
        "    service_type: Internet\n"
        "    ipv4_address: ['152.11.13.1/30']\n",
        encoding="utf-8",
    )

    inventory = load_inventory(path)

    assert inventory.device == "172.20.20.4"
    assert inventory.entries[0].ipv4_address == ["152.11.13.1/30"]
