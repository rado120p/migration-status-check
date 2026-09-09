"""E-LAN `local`: access port v globalni bridge-domain (MX) / vlan (EVO),
tedy v instanci default-switch (spec 2026-09-09). Stanzy zrcadli laborku
MX1-POP1 / PTX1-POP1 (2026-09-09)."""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

# ge-0/0/2.12 je v domene, ge-0/0/2.5 je L2 bez domeny, ge-0/0/2.77 ma
# vlan-id 12 ale v domene neni (prekryv VLAN nesmi stacit).
INTERFACES = """
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>12</name>
        <description>NGMVPN-IGMP-RECEIVER</description>
        <encapsulation>vlan-bridge</encapsulation>
        <vlan-id>12</vlan-id>
      </unit>
      <unit>
        <name>5</name>
        <description>L2-BEZ-DOMENY</description>
        <encapsulation>vlan-bridge</encapsulation>
        <vlan-id>5</vlan-id>
      </unit>
      <unit>
        <name>77</name>
        <description>TRUNK-STEJNA-VLAN</description>
        <encapsulation>vlan-bridge</encapsulation>
        <vlan-id>12</vlan-id>
      </unit>
    </interface>
    <interface>
      <name>irb</name>
      <unit>
        <name>2</name>
        <description>NGMVPN receivers POP1</description>
        <family><inet><address><name>10.12.11.2/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>NGMVPN-IGMP-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.2</name></interface>
    </instance>
  </routing-instances>
"""

MX_DOMAINS = """
  <bridge-domains{attr}>
    <domain{domain_attr}>
      <name>BD-NGMVPN-IGMP-RECEIVER</name>
      <domain-type>bridge</domain-type>
      <vlan-id>12</vlan-id>
      <interface><name>ge-0/0/2.12</name></interface>
      <routing-interface>irb.2</routing-interface>
      <protocols><igmp-snooping><version>3</version></igmp-snooping></protocols>
    </domain>
    <domain>
      <name>BD-JINA</name>
      <domain-type>bridge</domain-type>
      <vlan-id>99</vlan-id>
    </domain>
  </bridge-domains>
"""

EVO_DOMAINS = """
  <vlans{attr}>
    <vlan{domain_attr}>
      <name>VL-NGMVPN-IGMP-RECEIVER</name>
      <vlan-id>12</vlan-id>
      <interface><name>ge-0/0/2.12</name></interface>
      <l3-interface>irb.2</l3-interface>
    </vlan>
    <vlan>
      <name>VL-JINA</name>
      <vlan-id>99</vlan-id>
    </vlan>
    <vlan>
      <name>default</name>
      <vlan-id>1</vlan-id>
    </vlan>
  </vlans>
"""


def _config(domains: str, attr: str = "", domain_attr: str = "") -> etree._Element:
    body = domains.format(attr=attr, domain_attr=domain_attr)
    return etree.fromstring(f"<configuration>{INTERFACES}{body}</configuration>")


CASES = (
    pytest.param(JunosServiceParser, MX_DOMAINS, "BD-NGMVPN-IGMP-RECEIVER", id="mx"),
    pytest.param(JunosEvoAcxServiceParser, EVO_DOMAINS, "VL-NGMVPN-IGMP-RECEIVER", id="evo"),
)


def _by_iface(parser_class, config):
    return {s.interface: s for s in parser_class(config).parse()}


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_access_port_in_global_domain_is_elan_local(parser_class, domains, domain):
    port = _by_iface(parser_class, _config(domains))["ge-0/0/2.12"]
    assert (port.service_type, port.service_subtype) == ("E-LAN", "local")
    assert port.detection_confidence == "high"
    assert port.routing_instance is None
    assert port.bridge_domain == [domain]
    assert port.customer_vlan == ["12"]
    assert port.l3_interface == ["irb.2"]
    assert port.routing_instance_active is True
    assert port.interface_active is True
    assert port.detection_reason == [
        "Rozhraní je členem globální bridge-domain / vlan (default-switch), bez EVPN instance."
    ]


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_irb_side_is_unchanged(parser_class, domains, domain):
    irb = _by_iface(parser_class, _config(domains))["irb.2"]
    assert irb.service_type == "IPVPN"
    assert irb.l2_interface == ["ge-0/0/2.12"]
    assert irb.bridge_domain == [domain]


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_l2_port_outside_any_domain_stays_unknown(parser_class, domains, domain):
    port = _by_iface(parser_class, _config(domains))["ge-0/0/2.5"]
    assert (port.service_type, port.service_subtype) == ("Unknown", "layer2")
    assert port.l3_interface == []


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_vlan_overlap_alone_does_not_join_global_domain(parser_class, domains, domain):
    port = _by_iface(parser_class, _config(domains))["ge-0/0/2.77"]
    assert (port.service_type, port.service_subtype) == ("Unknown", "layer2")
    assert port.bridge_domain == []


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_deactivated_container_flags_local_service_not_irb(parser_class, domains, domain):
    by_iface = _by_iface(parser_class, _config(domains, attr=' inactive="inactive"'))
    port = by_iface["ge-0/0/2.12"]
    assert (port.service_type, port.service_subtype) == ("E-LAN", "local")
    assert port.routing_instance_active is False
    assert port.interface_active is True
    # IRB deaktivace domeny nededi - config ji nedeaktivoval.
    assert by_iface["irb.2"].routing_instance_active is True
    assert by_iface["irb.2"].interface_active is True


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_deactivated_single_domain_flags_only_its_port(parser_class, domains, domain):
    port = _by_iface(parser_class, _config(domains, domain_attr=' inactive="inactive"'))[
        "ge-0/0/2.12"
    ]
    assert port.routing_instance_active is False


@pytest.mark.parametrize(("parser_class", "domains", "domain"), CASES)
def test_active_domain_active_flag(parser_class, domains, domain):
    parser = parser_class(_config(domains))
    parser.parse()
    by_name = {d.name: d for d in parser.global_l2_domains}
    assert by_name[domain].active is True
    parser = parser_class(_config(domains, attr=' inactive="inactive"'))
    parser.parse()
    assert all(d.active is False for d in parser.global_l2_domains)
