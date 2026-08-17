"""Vazba L2 unitu na jeho l3-interface (irb) z bridge-domain/vlan konfigurace.

Lab 2026-08-17: sluzba L3VPN-CPE14-UNI je na EVO dvojice ae0.15 (E-LAN
v mac-vrf) + irb.15 (IPVPN). Port filtr inventory musi umet irb pritahnout
strukturalne - description muze chybet nebo se lisit (VL-4094 ma dva L2
porty a jednu irb). Parser proto na L2 sluzbe nese `l3_interface`.
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

# EVO syntaxe: routing-instance mac-vrf s "vlans", vazba je <l3-interface>.
# Presne zrcadli lab config (show routing-instances EVPN-VLAN-AWARE-POP1).
CONFIG_EVO_VLANS = """
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

# MX syntaxe: virtual-switch s "bridge-domains", vazba je <routing-interface>.
CONFIG_MX_BRIDGE_DOMAINS = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/4</name>
      <unit>
        <name>15</name>
        <description>L3VPN-CPE14-UNI</description>
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
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>EVPN-VS-POP1</name>
      <instance-type>virtual-switch</instance-type>
      <protocols><evpn/></protocols>
      <bridge-domains>
        <domain>
          <name>BD-15</name>
          <vlan-id>15</vlan-id>
          <interface><name>ge-0/0/4.15</name></interface>
          <routing-interface>irb.15</routing-interface>
        </domain>
      </bridge-domains>
    </instance>
  </routing-instances>
</configuration>
"""


def _services_by_interface(parser_cls, xml_text):
    services = parser_cls(etree.fromstring(xml_text)).parse()
    return {service.interface: service for service in services}


def test_evo_vlan_l3_interface_lands_on_l2_service():
    by_iface = _services_by_interface(JunosEvoAcxServiceParser, CONFIG_EVO_VLANS)

    assert by_iface["ae0.15"].l3_interface == ["irb.15"]


def test_evo_shared_vlan_links_every_l2_member_to_same_irb():
    by_iface = _services_by_interface(JunosEvoAcxServiceParser, CONFIG_EVO_VLANS)

    # VL-4094: dva L2 porty sdileji jednu irb - oba ji musi nest.
    assert by_iface["et-0/0/8.4094"].l3_interface == ["irb.4094"]


def test_evo_service_without_domain_has_no_l3_interface():
    by_iface = _services_by_interface(JunosEvoAcxServiceParser, CONFIG_EVO_VLANS)

    # irb sama zadnou vazbu nenese - je cilem vazby, ne zdrojem.
    assert by_iface["irb.15"].l3_interface == []


def test_mx_bridge_domain_routing_interface_lands_on_l2_service():
    by_iface = _services_by_interface(JunosServiceParser, CONFIG_MX_BRIDGE_DOMAINS)

    assert by_iface["ge-0/0/4.15"].l3_interface == ["irb.15"]
