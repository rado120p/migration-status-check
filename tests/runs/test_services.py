"""Testy pro generaci inventory do run adresare (capture --parse-services)."""

from __future__ import annotations

import pytest
import yaml
from lxml import etree

from migration_validator.runs.services import generate_inventory

TWO_PORTS = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/0</name>
      <unit>
        <name>100</name>
        <description>INTERNET-CPE100-NNI</description>
        <family>
          <inet>
            <address><name>152.11.100.1/30</name></address>
          </inet>
        </family>
      </unit>
    </interface>
    <interface>
      <name>ge-0/0/1</name>
      <unit>
        <name>200</name>
        <description>INTERNET-CPE200-NNI</description>
        <family>
          <inet>
            <address><name>152.11.200.1/30</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
</configuration>
"""


def _fake_retrieve_configuration(monkeypatch, xml_text):
    def fake(device, hierarchies):
        return etree.fromstring(xml_text)

    monkeypatch.setattr(
        "migration_validator.runs.services.retrieve_configuration", fake
    )


def test_generate_inventory_port_filter(tmp_path, monkeypatch):
    _fake_retrieve_configuration(monkeypatch, TWO_PORTS)

    output_path = tmp_path / "inv.yml"
    generate_inventory(object(), "junos", output_path, port="ge-0/0/0")

    data = yaml.safe_load(output_path.read_text())
    # Parser vedle logicke jednotky vraci i "Layer1" zaznam za holy fyzicky
    # port (ge-0/0/0) - patri sem, potvrzuje existenci fyzickeho rozhrani a
    # dalsi kod (napr. evpn_esi collector) s jeho pritomnosti pocita.
    assert sorted(s["interface"] for s in data["interfaces"]) == [
        "ge-0/0/0",
        "ge-0/0/0.100",
    ]
    assert data["schema_version"] == 8


def test_generate_inventory_all_mode(tmp_path, monkeypatch):
    _fake_retrieve_configuration(monkeypatch, TWO_PORTS)

    output_path = tmp_path / "inv.yml"
    generate_inventory(object(), "junos", output_path)

    data = yaml.safe_load(output_path.read_text())
    assert sorted(s["interface"] for s in data["interfaces"]) == [
        "ge-0/0/0",
        "ge-0/0/0.100",
        "ge-0/0/1",
        "ge-0/0/1.200",
    ]


LAG_WITH_IRB = """
<configuration>
  <interfaces>
    <interface>
      <name>ae0</name>
      <unit>
        <name>15</name>
        <description>L3VPN-CPE14-UNI</description>
      </unit>
    </interface>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>4094</name>
        <description>MGMT-VLAN</description>
      </unit>
    </interface>
    <interface>
      <name>irb</name>
      <unit>
        <name>15</name>
        <description>L3VPN-CPE14-UNI</description>
        <family>
          <inet>
            <address><name>198.11.14.1/29</name></address>
          </inet>
        </family>
      </unit>
      <unit>
        <name>4094</name>
        <description>MGMT-VLAN</description>
        <family>
          <inet>
            <address><name>10.0.94.1/24</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE14-UNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.15</name></interface>
    </instance>
    <instance>
      <name>EVPN-VLAN-AWARE-POP1</name>
      <instance-type>mac-vrf</instance-type>
      <protocols><evpn/></protocols>
      <service-type>vlan-aware</service-type>
      <vlans>
        <vlan>
          <name>VL-15</name>
          <vlan-id>15</vlan-id>
          <interface><name>ae0.15</name></interface>
          <l3-interface>irb.15</l3-interface>
        </vlan>
        <vlan>
          <name>VL-4094</name>
          <vlan-id>4094</vlan-id>
          <interface><name>ae0.4094</name></interface>
          <interface><name>et-0/0/8.4094</name></interface>
          <l3-interface>irb.4094</l3-interface>
        </vlan>
      </vlans>
    </instance>
  </routing-instances>
</configuration>
"""


def test_port_filter_pulls_linked_irb(tmp_path, monkeypatch):
    """Lab 2026-08-17: L3 polovina sluzby bydli na irb, ne na LAGu.

    Port filtr na ae0 musi pritahnout irb.15 (l3-interface VL-15), jinak
    IPVPN scope chybi ve snimku a baseline sluzba se nikdy nesparuje.
    """
    _fake_retrieve_configuration(monkeypatch, LAG_WITH_IRB)

    output_path = tmp_path / "inv.yml"
    generate_inventory(object(), "junos-evo", output_path, port="ae0")

    names = sorted(
        s["interface"] for s in yaml.safe_load(output_path.read_text())["interfaces"]
    )
    assert "irb.15" in names
    # irb.4094 patri k VL-4094, jejiz jediny pritomny L2 unit je na
    # et-0/0/8 - filtr na ae0 ji pritahnout nesmi (ae0.4094 v configu
    # rozhrani neexistuje, jen v instanci).
    assert "irb.4094" not in names
    assert "et-0/0/8.4094" not in names


def test_port_filter_pulls_irb_of_shared_vlan(tmp_path, monkeypatch):
    """VL-4094 ma dva L2 porty a jednu irb - filtr na et-0/0/8 ji pritahne."""
    _fake_retrieve_configuration(monkeypatch, LAG_WITH_IRB)

    output_path = tmp_path / "inv.yml"
    generate_inventory(object(), "junos-evo", output_path, port="et-0/0/8")

    names = sorted(
        s["interface"] for s in yaml.safe_load(output_path.read_text())["interfaces"]
    )
    assert "irb.4094" in names
    assert "irb.15" not in names
    assert "ae0.15" not in names


def test_generate_inventory_unknown_platform(tmp_path, monkeypatch):
    _fake_retrieve_configuration(monkeypatch, TWO_PORTS)

    output_path = tmp_path / "inv.yml"
    with pytest.raises(ValueError):
        generate_inventory(object(), "cisco", output_path)
