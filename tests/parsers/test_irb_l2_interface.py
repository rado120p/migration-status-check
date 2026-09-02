"""IRB nese sve access porty z globalnich bridge-domains (MX) / vlans (EVO)
(spec 2026-09-02). Vazba je routing-interface / l3-interface == jmeno IRB.
Je to inventory, ne multicast logika - plati pro kazdy IRB."""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

INTERFACES = """
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>12</name>
        <encapsulation>vlan-bridge</encapsulation>
        <vlan-id>12</vlan-id>
      </unit>
    </interface>
    <interface>
      <name>irb</name>
      <unit>
        <name>2</name>
        <description>MUX1 receivers POP1</description>
        <family><inet><address><name>10.12.11.254/24</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>MULTICAST-STREAM-B-MUX1-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.2</name></interface>
      <protocols><mvpn><receiver-site/></mvpn></protocols>
    </instance>
  </routing-instances>
"""

CONFIG_MX = f"""
<configuration>
  {INTERFACES}
  <bridge-domains>
    <domain>
      <name>BD-MUX1-B</name>
      <domain-type>bridge</domain-type>
      <vlan-id>12</vlan-id>
      <interface><name>ge-0/0/2.12</name></interface>
      <routing-interface>irb.2</routing-interface>
    </domain>
  </bridge-domains>
</configuration>
"""

CONFIG_EVO = f"""
<configuration>
  {INTERFACES}
  <vlans>
    <vlan>
      <name>VL-MUX1-B</name>
      <vlan-id>2</vlan-id>
      <interface><name>ge-0/0/2.12</name></interface>
      <l3-interface>irb.2</l3-interface>
    </vlan>
  </vlans>
</configuration>
"""

CASES = (
    pytest.param(JunosServiceParser, CONFIG_MX, "BD-MUX1-B", "12", id="mx"),
    pytest.param(JunosEvoAcxServiceParser, CONFIG_EVO, "VL-MUX1-B", "2", id="evo"),
)


@pytest.mark.parametrize(("parser_class", "config", "domain", "vlan"), CASES)
def test_irb_carries_its_access_port_and_domain(parser_class, config, domain, vlan):
    by_iface = {s.interface: s for s in parser_class(etree.fromstring(config)).parse()}
    irb = by_iface["irb.2"]
    assert irb.l2_interface == ["ge-0/0/2.12"]
    assert irb.bridge_domain == [domain]
    assert irb.customer_vlan == [vlan]


@pytest.mark.parametrize(("parser_class", "config", "domain", "vlan"), CASES)
def test_irb_without_domain_has_empty_l2_interface(parser_class, config, domain, vlan):
    stripped = config.replace("irb.2</routing-interface>", "irb.7</routing-interface>").replace(
        "irb.2</l3-interface>", "irb.7</l3-interface>"
    )
    by_iface = {s.interface: s for s in parser_class(etree.fromstring(stripped)).parse()}
    assert by_iface["irb.2"].l2_interface == []
