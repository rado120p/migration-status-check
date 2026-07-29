"""Offline testy statickych rout - konfigurace se vklada jako XML, laborka neni potreba.

Oba parsery se meni v zamku, takze kazdy test bezi proti obema.

Tvary XML odpovidaji skutecne konfiguraci laborky z 2026-07-29
(runs/bfd-static-2026-07-29/cfg/): IPv4 lezi primo pod routing-options/static,
IPv6 pod routing-options/rib <jmeno>.inet6.0/static, a to jak globalne, tak
uvnitr routing-instance.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, filename: str):
    """Parsery jsou skripty v korenu repozitare, ne balicek - nacteme je podle cesty."""
    spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


evo = _load("evo_parser_static_test", "evo_parser.py")
mx = _load("mx_parser_static_test", "mx_parser.py")

PARSERS = (
    pytest.param(evo, evo.JunosEvoAcxServiceParser, id="evo"),
    pytest.param(mx, mx.JunosServiceParser, id="mx"),
)

BOTH_FAMILIES = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>13</name>
        <description>CPE13-NNI</description>
        <family>
          <inet><address><name>152.11.13.1/29</name></address></inet>
          <inet6><address><name>2001:abcd:11:13::a/64</name></address></inet6>
        </family>
      </unit>
      <unit>
        <name>113</name>
        <description>CPE13-VRF</description>
        <family>
          <inet><address><name>198.11.13.1/29</name></address></inet>
          <inet6><address><name>2001:db8:11:13::a/64</name></address></inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-options>
    <static>
      <route>
        <name>198.62.1.0/29</name>
        <next-hop>152.11.13.2</next-hop>
      </route>
    </static>
    <rib>
      <name>inet6.0</name>
      <static>
        <route>
          <name>2001:aaaa::/64</name>
          <next-hop>2001:abcd:11:13::b</next-hop>
        </route>
      </static>
    </rib>
  </routing-options>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE13-NNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>et-0/0/8.113</name></interface>
      <routing-options>
        <static>
          <route>
            <name>172.26.1.0/29</name>
            <next-hop>198.11.13.2</next-hop>
          </route>
        </static>
        <rib>
          <name>L3VPN-CPE13-NNI.inet6.0</name>
          <static>
            <route>
              <name>2001:eeee::/64</name>
              <next-hop>2001:db8:11:13::b</next-hop>
            </route>
          </static>
        </rib>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""


def _parse(module, parser_class, xml: str):
    return parser_class(etree.XML(xml.encode())).parse()


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_rib_names_match_what_show_route_returns(module, parser_class):
    """Obe konfiguracni podoby se normalizuji na jmeno tabulky z RPC.

    Naivni //static/route by nasel obojí, ale ztratil by prislusnost k RIB -
    a prave ta odlisuje ::/0 v mgmt_junos.inet6.0 od ::/0 v inet6.0.
    """
    parser = parser_class(etree.XML(BOTH_FAMILIES.encode()))
    parser.parse()

    found = {(route.rib, route.prefix) for route in parser.static_routes}
    assert found == {
        ("inet.0", "198.62.1.0/29"),
        ("inet6.0", "2001:aaaa::/64"),
        ("L3VPN-CPE13-NNI.inet.0", "172.26.1.0/29"),
        ("L3VPN-CPE13-NNI.inet6.0", "2001:eeee::/64"),
    }


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_rib_instance_maps_global_tables_to_none(module, parser_class):
    """Globalni tabulky patri default instanci, kterou inventory zapisuje jako None."""
    assert module.rib_instance("inet.0") is None
    assert module.rib_instance("inet6.0") is None
    assert module.rib_instance("L3VPN-CPE13-NNI.inet.0") == "L3VPN-CPE13-NNI"
    assert module.rib_instance("L3VPN-CPE13-NNI.inet6.0") == "L3VPN-CPE13-NNI"
    assert module.rib_instance("mgmt_junos.inet6.0") == "mgmt_junos"
