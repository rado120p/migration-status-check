"""Offline testy parseru - konfigurace se vklada jako XML, laborka neni potreba.

Oba parsery se meni v zamku, takze kazdy test bezi proti obema.
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

DUAL_STACK = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>13</name>
        <description>INTERNET-CPE13-NNI</description>
        <family>
          <inet>
            <address><name>152.11.13.1/30</name></address>
          </inet>
          <inet6>
            <address><name>2001:abcd:11:13::a/127</name></address>
          </inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_families_are_separate_fields(parser_class):
    services = parser_class(etree.fromstring(DUAL_STACK)).parse()
    unit = next(s for s in services if s.interface == "ge-0/0/2.13")

    assert unit.ipv4_address == ["152.11.13.1/30"]
    assert unit.ipv6_address == ["2001:abcd:11:13::a/127"]


@pytest.mark.parametrize("parser_class", PARSERS)
def test_merged_field_is_gone(parser_class):
    """Slite pole nesmi prezit - jinak by se na nej necekane navazalo."""
    services = parser_class(etree.fromstring(DUAL_STACK)).parse()
    unit = next(s for s in services if s.interface == "ge-0/0/2.13")

    assert not hasattr(unit, "ip_address")


DUAL_STACK_BGP = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>13</name>
        <description>INTERNET-CPE13-NNI</description>
        <family>
          <inet>
            <address><name>152.11.13.1/30</name></address>
          </inet>
          <inet6>
            <address><name>2001:abcd:11:13::a/127</name></address>
          </inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
  <protocols>
    <bgp>
      <group>
        <name>CPE</name>
        <type>external</type>
        <neighbor><name>152.11.13.2</name></neighbor>
        <neighbor><name>2001:abcd:11:13::b</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_bgp_neighbors_of_both_families_map_to_service(parser_class):
    services = parser_class(etree.fromstring(DUAL_STACK_BGP)).parse()
    unit = next(s for s in services if s.interface == "ge-0/0/2.13")

    assert set(unit.bgp_neighbor) == {"152.11.13.2", "2001:abcd:11:13::b"}


@pytest.mark.parametrize("parser_class", PARSERS)
def test_neighbor_outside_subnet_does_not_map(parser_class):
    """Cizi soused se nesmi prilepit ke sluzbe jen proto, ze je stejne rodiny."""
    config = DUAL_STACK_BGP.replace(
        "<neighbor><name>152.11.13.2</name></neighbor>",
        "<neighbor><name>10.99.99.2</name></neighbor>",
    )
    services = parser_class(etree.fromstring(config)).parse()
    unit = next(s for s in services if s.interface == "ge-0/0/2.13")

    assert "10.99.99.2" not in unit.bgp_neighbor
