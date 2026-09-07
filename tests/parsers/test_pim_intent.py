"""PIM zamer: 'pim' v protocol znamena 'neighbor je ocekavany' (spec 2026-08-26).
Bez zaznamu check mlci - parsovani je jediny zdroj toho ocekavani.

Oba parsery se meni v zamku, takze test bezi proti obema (viz
test_family_split.py).

RI-PIM varianta je pokryta test_ri_pim_interface_is_in_pim_interfaces_set (spec 2026-09-07).
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

CONFIG = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/0</name>
      <unit>
        <name>0</name>
        <family>
          <iso/>
          <mpls/>
          <inet>
            <address><name>10.1.2.1/31</name></address>
          </inet>
        </family>
      </unit>
    </interface>
    <interface>
      <name>ge-0/0/1</name>
      <unit>
        <name>0</name>
        <family>
          <iso/>
          <mpls/>
          <inet>
            <address><name>10.1.2.5/31</name></address>
          </inet>
        </family>
      </unit>
      <unit>
        <name>1</name>
        <family>
          <inet>
            <address><name>10.1.2.9/31</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <protocols>
    <pim>
      <interface>
        <name>ge-0/0/1.0</name>
      </interface>
    </pim>
  </protocols>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_pim_interface_gets_pim_protocol(parser_class):
    services = parser_class(etree.fromstring(CONFIG)).parse()
    by_iface = {s.interface: s for s in services}

    assert "pim" in by_iface["ge-0/0/1.0"].protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_interface_without_pim_stays_clean(parser_class):
    """Druhe Core rozhrani bez PIM zaznamu si 'pim' neveze."""
    services = parser_class(etree.fromstring(CONFIG)).parse()
    by_iface = {s.interface: s for s in services}

    assert "pim" not in by_iface["ge-0/0/0.0"].protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_sibling_unit_does_not_inherit_pim(parser_class):
    """PIM je na logicke jednotce - sesterska unit stejneho fyzickeho
    rozhrani (ge-0/0/1.1) nesmi 'pim' dostat pres merge podle
    physical_name, kdyby global_protocols_by_interface omylem plnilo
    fyzicky nazev misto logickeho."""
    services = parser_class(etree.fromstring(CONFIG)).parse()
    by_iface = {s.interface: s for s in services}

    assert "pim" not in by_iface["ge-0/0/1.1"].protocol

RI_CONFIG = """
<configuration>
  <interfaces>
    <interface>
      <name>irb</name>
      <unit>
        <name>10</name>
        <family><inet><address><name>10.100.11.1/30</name></address></inet></family>
      </unit>
      <unit>
        <name>11</name>
        <family><inet><address><name>10.100.12.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>NGMVPN-PIM-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.10</name></interface>
      <interface><name>irb.11</name></interface>
      <route-distinguisher><rd-type>150.0.0.11:10</rd-type></route-distinguisher>
      <vrf-target><community>target:65000:10</community></vrf-target>
      <protocols>
        <mvpn><receiver-site/></mvpn>
        <pim>
          <interface><name>irb.10</name><mode>sparse</mode></interface>
        </pim>
      </protocols>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_ri_pim_interface_is_in_pim_interfaces_set(parser_class):
    """RI-scoped `protocols pim interface X` je zamer per rozhrani (spec
    2026-09-07). RoutingInstance.protocols dava "pim" vsem rozhranim instance,
    takze `protocol` seznam to nerozlisi - rozlisuje to jen pim_interfaces."""
    parser = parser_class(etree.fromstring(RI_CONFIG))
    parser.parse()
    assert parser.pim_interfaces == {"irb.10"}
    assert "pim" in parser.global_protocols_by_interface["irb.10"]
    assert "irb.11" not in parser.global_protocols_by_interface


@pytest.mark.parametrize("parser_class", PARSERS)
def test_global_pim_interface_is_in_pim_interfaces_set(parser_class):
    parser = parser_class(etree.fromstring(CONFIG))
    parser.parse()
    assert parser.pim_interfaces == {"ge-0/0/1.0"}
