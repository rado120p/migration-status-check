import textwrap

import pytest

from migration_validator.models.inventory import (
    INVENTORY_SCHEMA_VERSION,
    ServiceEntry,
    load_inventory,
)


def test_from_dict_fills_defaults_for_missing_keys():
    entry = ServiceEntry.from_dict({"interface": "ge-0/0/1.0", "service_type": "Internet"})
    assert entry.interface == "ge-0/0/1.0"
    assert entry.service_type == "Internet"
    assert entry.description is None
    assert entry.service_subtype is None
    assert entry.ipv4_address == []
    assert entry.bgp_neighbor == []
    assert entry.routing_instance_active is True
    assert entry.interface_active is True


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
            schema_version: 7
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
              routing_instance_active: true
              interface_active: true
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
    path.write_text("schema_version: 7\ndevice: 1.2.3.4\n", encoding="utf-8")

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
        "schema_version: 7\n"
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


def test_version_two_inventory_is_rejected(tmp_path):
    """Stara inventory nema static_route ani bfd.

    Tolerantni cteni by tise vratilo sluzby bez statik, takze by check
    'nakonfigurovana routa neni v tabulce' nemel co hlasit a sluzba by
    svitila zelene. Stejny duvod jako u rozdeleni rodin ve verzi 2.
    """
    path = tmp_path / "stara.yml"
    path.write_text(
        "schema_version: 2\ndevice: r1\ninterfaces: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema_version 2"):
        load_inventory(path)


def test_static_routes_and_bfd_survive_load(tmp_path):
    path = tmp_path / "nova.yml"
    path.write_text(
        """
schema_version: 7
device: r1
interfaces:
  - interface: et-0/0/8.113
    service_type: IPVPN
    static_route:
      - rib: L3VPN-A.inet.0
        prefix: 172.26.1.0/29
        next_hop: [198.11.13.2]
    bfd:
      - peer: 198.11.13.2
        minimum_interval: 3000
        multiplier: 3
        source: group
""",
        encoding="utf-8",
    )

    entry = load_inventory(path).entries[0]

    assert entry.static_route == [
        {
            "rib": "L3VPN-A.inet.0",
            "prefix": "172.26.1.0/29",
            "next_hop": ["198.11.13.2"],
        }
    ]
    assert entry.bfd[0]["source"] == "group"
    assert entry.bfd[0]["minimum_interval"] == 3000


def test_missing_new_fields_default_to_empty(tmp_path):
    path = tmp_path / "bez.yml"
    path.write_text(
        "schema_version: 7\ndevice: r1\n"
        "interfaces:\n  - interface: et-0/0/8.13\n    service_type: Internet\n",
        encoding="utf-8",
    )

    entry = load_inventory(path).entries[0]

    assert entry.static_route == []
    assert entry.bfd == []


def test_mapping_list_rejects_scalars(tmp_path):
    """Prvek, ktery neni mapping, je chyba - ne tichy prevod na retezec.

    _as_list by z {'rib': ...} udelal jeho str() a check by pak hledal
    klice v retezci.

    Vzor je zamerne cela hlaska vcetne jmena typu: samotne "mapping" sedi
    i na jmeno adresare z tmp_path, takze by test prosel i proti vyjimce,
    ktera s tou kontrolou nema nic spolecneho.
    """
    path = tmp_path / "spatna.yml"
    path.write_text(
        "schema_version: 7\ndevice: r1\n"
        "interfaces:\n  - interface: et-0/0/8.13\n    service_type: Internet\n"
        "    static_route: [not-a-mapping]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"ocekavan mapping v seznamu, nalezeno str"):
        load_inventory(path)


def test_service_entry_carries_both_deactivation_flags(tmp_path):
    """Dve pole misto jednoho 'active' - zdroje deaktivace jsou dva.

    Zabiji mutanta: ponechani jednoho pole 'active' a jeho namapovani na oba
    stavy. Zaznam nize ma RI zivou a rozhrani deaktivovane, takze jedno
    sdilene pole nemuze mit obe hodnoty spravne.
    """
    path = tmp_path / "inv.yml"
    path.write_text(
        "schema_version: 7\ndevice: r1\n"
        "interfaces:\n"
        "  - interface: ge-0/0/4.0\n"
        "    service_type: IPVPN\n"
        "    routing_instance: L3VPN-CPE14-UNI\n"
        "    routing_instance_active: true\n"
        "    interface_active: false\n",
        encoding="utf-8",
    )

    inventory = load_inventory(str(path))
    entry = inventory.entries[0]

    assert entry.routing_instance_active is True
    assert entry.interface_active is False
    assert not hasattr(entry, "active"), (
        "pole 'active' ma zaniknout - popisovalo stav routing-instance, "
        "ne rozhrani, a se dvema zdroji deaktivace uz nestaci"
    )


def test_entry_reads_inactive_bgp_neighbors(tmp_path):
    """Deaktivovany soused se cte do vlastniho seznamu, ne do zivych."""
    path = tmp_path / "inv.yml"
    path.write_text(
        "schema_version: 7\n"
        "device: dev\n"
        "interfaces:\n"
        "- interface: ge-0/0/2.13\n"
        "  service_type: Internet\n"
        "  bgp_neighbor: [198.11.13.2]\n"
        "  bgp_neighbor_inactive: [198.11.13.9]\n",
        encoding="utf-8",
    )
    entry = load_inventory(str(path)).entries[0]

    assert entry.bgp_neighbor == ["198.11.13.2"]
    assert entry.bgp_neighbor_inactive == ["198.11.13.9"]


def test_inventory_schema_version_is_seven():
    """Konstanta schematu je 7 - stara inventory se nemigruje, generuje se znovu.

    Nazev drive sliboval odmitnuti schematu 3, ktere tenhle test nikdy
    netestoval: telo jen asertuje hodnotu konstanty. Skutecne odmitnuti
    stare inventory pokryva test_old_inventory_fails_loudly a ten zustava.
    """
    assert INVENTORY_SCHEMA_VERSION == 7
