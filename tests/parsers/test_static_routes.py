"""Offline testy statickych rout - konfigurace se vklada jako XML, laborka neni potreba.

Oba parsery se meni v zamku, takze kazdy test bezi proti obema.

Tvary XML odpovidaji skutecne konfiguraci laborky z 2026-07-29
(runs/bfd-static-2026-07-29/cfg/): IPv4 lezi primo pod routing-options/static,
IPv6 pod routing-options/rib <jmeno>.inet6.0/static, a to jak globalne, tak
uvnitr routing-instance.
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers import core
from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

PARSERS = (
    pytest.param(JunosEvoAcxServiceParser, id="evo"),
    pytest.param(JunosServiceParser, id="mx"),
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


def _parse(parser_class, xml: str):
    return parser_class(etree.XML(xml.encode())).parse()


@pytest.mark.parametrize("parser_class", PARSERS)
def test_rib_names_match_what_show_route_returns(parser_class):
    """Obe konfiguracni podoby se normalizuji na jmeno tabulky z RPC.

    Naivni //static/route by nasel oboji, ale ztratil by prislusnost k RIB -
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


@pytest.mark.parametrize("parser_class", PARSERS)
def test_rib_instance_maps_global_tables_to_none(parser_class):
    """Globalni tabulky patri default instanci, kterou inventory zapisuje jako None."""
    assert core.rib_instance("inet.0") is None
    assert core.rib_instance("inet6.0") is None
    assert core.rib_instance("L3VPN-CPE13-NNI.inet.0") == "L3VPN-CPE13-NNI"
    assert core.rib_instance("L3VPN-CPE13-NNI.inet6.0") == "L3VPN-CPE13-NNI"
    assert core.rib_instance("mgmt_junos.inet6.0") == "mgmt_junos"


UNMAPPABLE = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>113</name>
        <description>CPE13-VRF</description>
        <family>
          <inet><address><name>198.11.13.1/29</name></address></inet>
        </family>
      </unit>
    </interface>
    <interface>
      <name>fxp0</name>
      <unit>
        <name>0</name>
        <family>
          <inet><address><name>10.0.0.15/24</name></address></inet>
        </family>
      </unit>
    </interface>
  </interfaces>
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
      </routing-options>
    </instance>
    <instance>
      <name>mgmt_junos</name>
      <routing-options>
        <static>
          <route>
            <name>0.0.0.0/0</name>
            <next-hop>10.0.0.2</next-hop>
          </route>
        </static>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""

WRONG_VRF = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>113</name>
        <description>CPE13-VRF</description>
        <family>
          <inet><address><name>198.11.13.1/29</name></address></inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE13-NNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>et-0/0/8.113</name></interface>
    </instance>
    <instance>
      <name>L3VPN-JINA</name>
      <instance-type>vrf</instance-type>
      <routing-options>
        <static>
          <route>
            <name>10.9.9.0/24</name>
            <next-hop>198.11.13.2</next-hop>
          </route>
        </static>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_route_lands_on_service_whose_subnet_contains_next_hop(parser_class):
    services = _parse(parser_class, BOTH_FAMILIES)
    by_interface = {service.interface: service for service in services}

    assert {
        (route["rib"], route["prefix"])
        for route in by_interface["et-0/0/8.113"].static_route
    } == {
        ("L3VPN-CPE13-NNI.inet.0", "172.26.1.0/29"),
        ("L3VPN-CPE13-NNI.inet6.0", "2001:eeee::/64"),
    }


@pytest.mark.parametrize("parser_class", PARSERS)
def test_next_hop_is_carried_as_value(parser_class):
    services = _parse(parser_class, BOTH_FAMILIES)
    by_interface = {service.interface: service for service in services}

    routes = {
        route["prefix"]: route["next_hops"]
        for route in by_interface["et-0/0/8.113"].static_route
    }
    assert routes["172.26.1.0/29"] == [
        {"to": "198.11.13.2", "interface": None, "qualified": False, "active": True}
    ]
    assert routes["2001:eeee::/64"] == [
        {"to": "2001:db8:11:13::b", "interface": None, "qualified": False, "active": True}
    ]


@pytest.mark.parametrize("parser_class", PARSERS)
def test_management_route_lands_on_no_service(parser_class):
    """Statika v mgmt_junos padne na fxp0.0, ze ktere se scope nikdy nestane.

    Do inventory se nedostane. Kdyz je nainstalovana, chyti ji
    RunResult.unassigned z routovaci tabulky - viz Task 9.
    """
    services = _parse(parser_class, UNMAPPABLE)

    assert all(
        route["prefix"] != "0.0.0.0/0"
        for service in services
        for route in service.static_route
    )


@pytest.mark.parametrize("parser_class", PARSERS)
def test_next_hop_in_foreign_vrf_does_not_match(parser_class):
    """Shoda subnetu sama nestaci - musi sedet i routing-instance.

    Next-hop 198.11.13.2 padne do subnetu et-0/0/8.113, ale routa lezi
    v L3VPN-JINA. Bez podminky na instanci by sedla na spatnou sluzbu.
    """
    services = _parse(parser_class, WRONG_VRF)

    assert all(not service.static_route for service in services)


# ----------------------------------------------------------------------
# Deaktivovane kontejnery
#
# Tyhle tvary v laborce nejsou: ve vsech ctyrech captureech z 2026-07-29
# nese inactive="inactive" jen <interface>, nikdy static, rib ani
# routing-options. Skladaji se proto rucne - je to presne ten tvar, ktery
# v konfiguraci necha junosi `deactivate`, tedy standardni idiom pro
# vyrazeni konfigurace pri migraci.
# ----------------------------------------------------------------------

INTERFACES = """
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
        </family>
      </unit>
    </interface>
  </interfaces>
"""

# Deaktivovany je `static` i `rib`; routa v instanci zustava ziva a slouzi
# jako kontrola, ze guard nevyradil vic, nez mel.
DEACTIVATED_CONTAINERS = f"""
<configuration>
{INTERFACES}
  <routing-options>
    <static inactive="inactive">
      <route>
        <name>198.62.1.0/29</name>
        <next-hop>152.11.13.2</next-hop>
      </route>
    </static>
    <rib inactive="inactive">
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
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""

DEACTIVATED_ROUTING_OPTIONS = f"""
<configuration>
{INTERFACES}
  <routing-options inactive="inactive">
    <static>
      <route>
        <name>198.62.1.0/29</name>
        <next-hop>152.11.13.2</next-hop>
      </route>
    </static>
  </routing-options>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE13-NNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>et-0/0/8.113</name></interface>
      <routing-options inactive="inactive">
        <static>
          <route>
            <name>172.26.1.0/29</name>
            <next-hop>198.11.13.2</next-hop>
          </route>
        </static>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""


def _configured(parser_class, xml: str) -> set[tuple[str, str]]:
    parser = parser_class(etree.XML(xml.encode()))
    parser.parse()
    return {(route.rib, route.prefix) for route in parser.static_routes}


def _configured_by_identity(parser_class, xml: str) -> dict[tuple[str, str], bool]:
    """Zamer klicovany (rib, prefix) s hodnotou 'je aktivni'.

    Oproti _configured neztrati priznak, takze test pozna rozdil mezi
    'routa v zameru neni' a 'routa v zameru je a je deaktivovana'.
    """
    parser = parser_class(etree.XML(xml.encode()))
    parser.parse()
    return {(route.rib, route.prefix): route.active for route in parser.static_routes}


@pytest.mark.parametrize("parser_class", PARSERS)
def test_deactivated_static_stanza_keeps_route_as_inactive(parser_class):
    """Deaktivovany `static` necha routu v zameru, ale oznaci ji.

    Vypustit ji beze stopy je vada: check by pak nemel co preskocit a
    operator by nevedel, ze routa v konfiguraci vubec je. Zaroven nesmi
    zustat aktivni - to by dalo 'FAIL ... neni v tabulce' za routu, kterou
    operator vedome vyradil.
    """
    found = _configured_by_identity(parser_class, DEACTIVATED_CONTAINERS)

    assert found[("inet.0", "198.62.1.0/29")] is False
    # Kontrola, ze priznak nesebral i to, co ma zustat zive.
    assert found[("L3VPN-CPE13-NNI.inet.0", "172.26.1.0/29")] is True


@pytest.mark.parametrize("parser_class", PARSERS)
def test_deactivated_rib_marks_its_routes_inactive(parser_class):
    """Deaktivovany `rib` bere s sebou i `static` pod sebou - pres predky."""
    found = _configured_by_identity(parser_class, DEACTIVATED_CONTAINERS)

    assert found[("inet6.0", "2001:aaaa::/64")] is False


@pytest.mark.parametrize("parser_class", PARSERS)
def test_deactivated_global_routing_options_marks_routes_inactive(parser_class):
    """Deaktivovane globalni `routing-options` oznaci statiky default instance."""
    found = _configured_by_identity(parser_class, DEACTIVATED_ROUTING_OPTIONS)

    assert found[("inet.0", "198.62.1.0/29")] is False


@pytest.mark.parametrize("parser_class", PARSERS)
def test_deactivated_instance_routing_options_marks_routes_inactive(parser_class):
    """Deaktivovane `routing-options` uvnitr instance - druha, samostatna smycka.

    Jsou to dve ruzne smycky v `_parse_static_routes`, takze jeden test na
    obe by nechal jednu z nich nepokrytou.
    """
    found = _configured_by_identity(parser_class, DEACTIVATED_ROUTING_OPTIONS)

    assert found[("L3VPN-CPE13-NNI.inet.0", "172.26.1.0/29")] is False


QNH_MIX = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <unit>
        <name>13</name>
        <description>CPE13-NNI</description>
        <family>
          <inet6><address><name>2001:abcd:11:14::a/64</name></address></inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-options>
    <rib>
      <name>inet6.0</name>
      <static>
        <route>
          <name>2001:aaaa::/64</name>
          <next-hop>2001:abcd:11:14::4</next-hop>
          <qualified-next-hop inactive="inactive">
            <name>2001:db8::ffff</name>
          </qualified-next-hop>
        </route>
        <route>
          <name>2001:abcd:11:13::/64</name>
          <qualified-next-hop>
            <name>fe80::2</name>
            <interface>et-0/0/8.13</interface>
          </qualified-next-hop>
        </route>
      </static>
      <aggregate>
        <route>
          <name>2001:abcd::/32</name>
          <discard/>
        </route>
      </aggregate>
    </rib>
    <aggregate>
      <route>
        <name>198.62.0.0/16</name>
        <discard/>
      </route>
    </aggregate>
  </routing-options>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE13-NNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>et-0/0/8.13</name></interface>
      <routing-options>
        <aggregate>
          <route>
            <name>172.26.0.0/16</name>
            <discard/>
          </route>
        </aggregate>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("parser_cls", PARSERS)
def test_qualified_next_hop_ma_vlastni_zaznam_a_active(parser_cls):
    parser = parser_cls(etree.fromstring(QNH_MIX))
    parser.parse()
    routes = {r.prefix: r for r in parser.static_routes}

    mixed = routes["2001:aaaa::/64"]
    assert mixed.route_type == "static"
    assert mixed.next_hops == [
        {"to": "2001:abcd:11:14::4", "interface": None,
         "qualified": False, "active": True},
        {"to": "2001:db8::ffff", "interface": None,
         "qualified": True, "active": False},
    ]

    qnh_only = routes["2001:abcd:11:13::/64"]
    assert qnh_only.next_hops == [
        {"to": "fe80::2", "interface": "et-0/0/8.13",
         "qualified": True, "active": True},
    ]


@pytest.mark.parametrize("parser_cls", PARSERS)
def test_aggregate_routy_se_parsuji_globalne_v_rib_i_v_instanci(parser_cls):
    parser = parser_cls(etree.fromstring(QNH_MIX))
    parser.parse()
    aggregates = {
        (r.rib, r.prefix): r
        for r in parser.static_routes
        if r.route_type == "aggregate"
    }
    assert set(aggregates) == {
        ("inet.0", "198.62.0.0/16"),
        ("inet6.0", "2001:abcd::/32"),
        ("L3VPN-CPE13-NNI.inet.0", "172.26.0.0/16"),
    }
    assert all(r.next_hops == [] for r in aggregates.values())
    assert all(r.active for r in aggregates.values())
