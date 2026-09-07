"""IGMP zamer a multicast subtypy (spec 2026-09-02, rozsireno 2026-09-07).

Internet + igmp -> subtype "multicast"; IPVPN + (igmp nebo pim) zamer na
rozhrani + protocols mvpn v instanci -> "mvpn"; IPVPN s igmp bez mvpn
zustava None. Oba parsery se meni v zamku.
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

# Zrcadli lab: et-0/0/8.11 Internet receiver, irb.2 v MVPN VRF, irb.3 v
# obycejne VRF s IGMP (negativni pripad), et-0/0/8.13 Internet bez IGMP.
CONFIG = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>11</name>
        <description>MULTICAST-STREAM-A-MUX1-RECEIVER-1</description>
        <family><inet><address><name>10.111.11.1/24</name></address></inet></family>
      </unit>
      <unit>
        <name>13</name>
        <description>CPE13-NNI</description>
        <family><inet><address><name>152.11.13.1/29</name></address></inet></family>
      </unit>
    </interface>
    <interface>
      <name>irb</name>
      <unit>
        <name>2</name>
        <description>MUX1 receivers POP1</description>
        <family><inet><address><name>10.12.11.254/24</name></address></inet></family>
      </unit>
      <unit>
        <name>3</name>
        <family><inet><address><name>10.13.11.254/24</name></address></inet></family>
      </unit>
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
  <protocols>
    <igmp>
      <interface><name>et-0/0/8.11</name><version>3</version></interface>
      <interface><name>irb.2</name><version>3</version></interface>
    </igmp>
  </protocols>
  <routing-instances>
    <instance>
      <name>MULTICAST-STREAM-B-MUX1-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.2</name></interface>
      <route-distinguisher><rd-type>150.0.0.12:2</rd-type></route-distinguisher>
      <vrf-target><community>target:65000:2</community></vrf-target>
      <protocols>
        <mvpn><receiver-site/></mvpn>
        <pim><interface><name>irb.2</name></interface></pim>
      </protocols>
    </instance>
    <instance>
      <name>PLAIN-VRF</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.3</name></interface>
      <protocols>
        <igmp><interface><name>irb.3</name></interface></igmp>
      </protocols>
    </instance>
    <instance>
      <name>NGMVPN-PIM-RECEIVER</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.10</name></interface>
      <interface><name>irb.11</name></interface>
      <protocols>
        <mvpn><receiver-site/></mvpn>
        <pim><interface><name>irb.10</name><mode>sparse</mode></interface></pim>
      </protocols>
    </instance>
  </routing-instances>
</configuration>
"""


def _services(parser_class):
    return {s.interface: s for s in parser_class(etree.fromstring(CONFIG)).parse()}


@pytest.mark.parametrize("parser_class", PARSERS)
def test_internet_with_igmp_gets_multicast_subtype(parser_class):
    service = _services(parser_class)["et-0/0/8.11"]
    assert service.service_type == "Internet"
    assert service.service_subtype == "multicast"
    assert "igmp" in service.protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_internet_without_igmp_keeps_none_subtype(parser_class):
    service = _services(parser_class)["et-0/0/8.13"]
    assert service.service_type == "Internet"
    assert service.service_subtype is None
    assert "igmp" not in service.protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_ipvpn_with_igmp_and_mvpn_gets_mvpn_subtype(parser_class):
    service = _services(parser_class)["irb.2"]
    assert service.service_type == "IPVPN"
    assert service.service_subtype == "mvpn"
    assert "igmp" in service.protocol
    assert "mvpn" in service.protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_ipvpn_with_pim_only_and_mvpn_gets_mvpn_subtype(parser_class):
    service = _services(parser_class)["irb.10"]
    assert service.service_subtype == "mvpn"
    assert "pim" in service.protocol
    assert "igmp" not in service.protocol
    assert "protocols pim" in " ".join(service.detection_reason)


@pytest.mark.parametrize("parser_class", PARSERS)
def test_ipvpn_interface_without_per_interface_pim_stays_plain(parser_class):
    """irb.11 je v MVPN instanci, ale neni pod `pim interface` ani `igmp
    interface` - instance-level "pim" v protocol seznamu subtype nedava."""
    service = _services(parser_class)["irb.11"]
    assert service.service_type == "IPVPN"
    assert service.service_subtype is None


@pytest.mark.parametrize("parser_class", PARSERS)
def test_detection_reason_names_both_intents(parser_class):
    reasons = " ".join(_services(parser_class)["irb.2"].detection_reason)
    assert "protocols igmp a pim" in reasons


@pytest.mark.parametrize("parser_class", PARSERS)
def test_ipvpn_with_ri_igmp_but_no_mvpn_stays_plain(parser_class):
    """RI-scoped protocols igmp se pocita jako zamer, ale bez mvpn to neni
    MVPN sluzba (rozhodnuti 2026-09-02)."""
    service = _services(parser_class)["irb.3"]
    assert service.service_type == "IPVPN"
    assert service.service_subtype is None
    assert "igmp" in service.protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_detection_reason_mentions_igmp(parser_class):
    reasons = " ".join(_services(parser_class)["et-0/0/8.11"].detection_reason)
    assert "igmp" in reasons.lower()
