"""Interni BGP peeri (type internal / AS shoda) patri na Core lo0.0 sluzbu,
ne do Internet/IPVPN (spec 2026-08-26).

Oba parsery se meni v zamku, takze test bezi proti obema (viz
test_family_split.py).
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

PARSERS = (
    pytest.param(JunosEvoAcxServiceParser, id="evo"),
    pytest.param(JunosServiceParser, id="mx"),
)

LOOPBACK_INTERFACE = """
    <interface>
      <name>lo0</name>
      <unit>
        <name>0</name>
        <family>
          <iso/>
          <inet>
            <address><name>150.0.0.11/32</name></address>
          </inet>
        </family>
      </unit>
    </interface>
"""


def _config(bgp_xml: str, as_number: str = "65000") -> str:
    return f"""
<configuration>
  <interfaces>
    {LOOPBACK_INTERFACE}
    <interface>
      <name>ge-0/0/0</name>
      <unit>
        <name>0</name>
        <family>
          <iso/>
          <mpls/>
          <inet>
            <address><name>10.0.0.1/31</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-options>
    <autonomous-system><as-number>{as_number}</as-number></autonomous-system>
  </routing-options>
  <protocols>
    <bgp>
      {bgp_xml}
    </bgp>
  </protocols>
</configuration>
"""


CONFIG_TYPE_INTERNAL = _config(
    """
    <group><name>IBGP</name><type>internal</type>
      <neighbor><name>150.0.0.12</name></neighbor></group>
    <group><name>TRANSIT</name><type>external</type><peer-as>65010</peer-as>
      <neighbor><name>10.0.0.2</name></neighbor></group>
    """
)

CONFIG_AS_FALLBACK = _config(
    """
    <group><name>IBGP</name>
      <neighbor><name>150.0.0.12</name><peer-as>65000</peer-as></neighbor></group>
    <group><name>OTHER</name>
      <neighbor><name>150.0.0.13</name><peer-as>65001</peer-as></neighbor></group>
    """
)

CONFIG_OVERRIDE = _config(
    """
    <group><name>MIXED</name><type>external</type><peer-as>65010</peer-as>
      <neighbor><name>150.0.0.12</name><type>internal</type></neighbor></group>
    """
)


@pytest.mark.parametrize("parser_class", PARSERS)
def test_type_internal_group_maps_to_core_loopback(parser_class):
    services = parser_class(etree.fromstring(CONFIG_TYPE_INTERNAL)).parse()
    by_iface = {s.interface: s for s in services}

    loopback = by_iface["lo0.0"]
    assert "150.0.0.12" in loopback.bgp_neighbor
    assert "bgp" in loopback.protocol

    for service in services:
        if service.interface == "lo0.0":
            continue
        assert "150.0.0.12" not in service.bgp_neighbor


@pytest.mark.parametrize("parser_class", PARSERS)
def test_type_external_group_does_not_reach_loopback(parser_class):
    """Externi peer (chovani Internet/IPVPN) na lo0.0 skoncit nesmi."""
    services = parser_class(etree.fromstring(CONFIG_TYPE_INTERNAL)).parse()
    by_iface = {s.interface: s for s in services}

    loopback = by_iface["lo0.0"]
    assert "10.0.0.2" not in loopback.bgp_neighbor


@pytest.mark.parametrize("parser_class", PARSERS)
def test_as_fallback_without_explicit_type(parser_class):
    """Bez type: peer-as == local-as je internal (fallback), jiny AS externi."""
    services = parser_class(etree.fromstring(CONFIG_AS_FALLBACK)).parse()
    by_iface = {s.interface: s for s in services}

    loopback = by_iface["lo0.0"]
    assert "150.0.0.12" in loopback.bgp_neighbor
    assert "150.0.0.13" not in loopback.bgp_neighbor


@pytest.mark.parametrize("parser_class", PARSERS)
def test_neighbor_type_overrides_group_type(parser_class):
    """Group externi, ale neighbor ma vlastni type internal - neighbor vyhrava."""
    services = parser_class(etree.fromstring(CONFIG_OVERRIDE)).parse()
    by_iface = {s.interface: s for s in services}

    loopback = by_iface["lo0.0"]
    assert "150.0.0.12" in loopback.bgp_neighbor


@pytest.mark.parametrize("parser_class", PARSERS)
def test_internal_peers_get_no_bfd_intent(parser_class):
    """Core lo0.0 sluzba s internimi peery nema zadne bfd zamery, i kdyz
    globalni protocols bgp BFD konfiguruje (gate z _assign_bfd docstringu)."""
    config = f"""
<configuration>
  <interfaces>
    {LOOPBACK_INTERFACE}
  </interfaces>
  <routing-options>
    <autonomous-system><as-number>65000</as-number></autonomous-system>
  </routing-options>
  <protocols>
    <bgp>
      <bfd-liveness-detection>
        <minimum-interval>300</minimum-interval>
      </bfd-liveness-detection>
      <group><name>IBGP</name><type>internal</type>
        <neighbor><name>150.0.0.12</name></neighbor></group>
    </bgp>
  </protocols>
</configuration>
"""
    services = parser_class(etree.fromstring(config)).parse()
    by_iface = {s.interface: s for s in services}

    loopback = by_iface["lo0.0"]
    assert "150.0.0.12" in loopback.bgp_neighbor
    assert loopback.bfd == []
