"""Lokalne prepnuty EVPN-VPWS (lab 2026-09-25): obe AC jedne evpn-vpws
instance jsou dve E-Line sluzby ve stejne RI. Inventory tedy da dva scopy
a E2 filtr v Scope.select je musi rozdelit - bez subtypu local-switch
(spec 2026-09-25 vpws-local-switch-a-matcher).
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
      <name>et-0/0/8</name>
      <flexible-vlan-tagging/>
      <encapsulation>flexible-ethernet-services</encapsulation>
      <unit><name>211</name><encapsulation>vlan-ccc</encapsulation><vlan-id>211</vlan-id></unit>
      <unit><name>212</name><encapsulation>vlan-ccc</encapsulation><vlan-id>212</vlan-id></unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>EVPN-VPWS-LOCAL</name>
      <instance-type>evpn-vpws</instance-type>
      <protocols><evpn>
        <interface><name>et-0/0/8.211</name>
          <vpws-service-id><local>100</local><remote>200</remote></vpws-service-id></interface>
        <interface><name>et-0/0/8.212</name>
          <vpws-service-id><local>200</local><remote>100</remote></vpws-service-id></interface>
      </evpn></protocols>
      <interface><name>et-0/0/8.211</name></interface>
      <interface><name>et-0/0/8.212</name></interface>
      <route-distinguisher><rd-type>65000:211</rd-type></route-distinguisher>
      <vrf-target><community>target:65000:211</community></vrf-target>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_local_switched_vpws_gives_two_eline_services(parser_class):
    services = parser_class(etree.fromstring(CONFIG)).parse()
    eline = {s.interface: s for s in services if s.service_type == "E-Line"}
    assert set(eline) == {"et-0/0/8.211", "et-0/0/8.212"}
    assert {s.service_subtype for s in eline.values()} == {"vpws"}
    assert {s.routing_instance for s in eline.values()} == {"EVPN-VPWS-LOCAL"}
