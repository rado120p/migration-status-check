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
    assert data["schema_version"] == 5


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


def test_generate_inventory_unknown_platform(tmp_path, monkeypatch):
    _fake_retrieve_configuration(monkeypatch, TWO_PORTS)

    output_path = tmp_path / "inv.yml"
    with pytest.raises(ValueError):
        generate_inventory(object(), "cisco", output_path)
