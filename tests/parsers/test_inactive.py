"""Offline testy deaktivované konfigurace - laborka není potřeba.

Oba parsery se mění v zámku, takže každý test běží proti oběma.

Tvary XML odpovídají skutečné konfiguraci laborky z 2026-07-29
(runs/bfd-static-2026-07-29/cfg/): na 172.20.20.4 jsou deaktivovaná rozhraní
ge-0/0/2, ge-0/0/4 a ge-0/0/5, na 172.20.20.5 pak et-0/0/10.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, filename: str):
    """Parsery jsou skripty v kořeni repozitáře, ne balíček - načteme je podle cesty."""
    spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


evo = _load("evo_parser_inactive_test", "evo_parser.py")
mx = _load("mx_parser_inactive_test", "mx_parser.py")

PARSERS = (
    pytest.param(evo, evo.JunosEvoAcxServiceParser, id="evo"),
    pytest.param(mx, mx.JunosServiceParser, id="mx"),
)

DEACTIVATED_INTERFACE = """
<configuration>
  <interfaces>
    <interface inactive="inactive">
      <name>ge-0/0/4</name>
      <description>L3VPN-CPE14-UNI</description>
      <unit>
        <name>0</name>
        <description>L3VPN-CPE14-UNI</description>
        <family>
          <inet>
            <address><name>198.11.14.1/30</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE14-UNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>ge-0/0/4.0</name></interface>
    </instance>
  </routing-instances>
</configuration>
"""


def _services(module, parser_class, xml_text):
    return parser_class(etree.fromstring(xml_text.encode())).parse()


def _by_name(services, name):
    found = [service for service in services if service.interface == name]
    assert found, f"služba {name} v inventory není - deaktivace ji nesmí vypustit"
    return found[0]


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_interface_stays_in_inventory(module, parser_class):
    """Deaktivované rozhraní se z inventory nevypouští - jen se označí.

    Zabíjí mutanta: `continue` na deaktivovaném rozhraní v _parse_interfaces.
    Dočasně deaktivovaná služba se pořád musí zmigrovat, takže vypustit ji
    z inventory je chyba, ne oprava.
    """
    services = _services(module, parser_class, DEACTIVATED_INTERFACE)

    assert _by_name(services, "ge-0/0/4.0")


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_interface_is_flagged(module, parser_class):
    """interface_active je False u fyzického rozhraní i u jeho jednotky.

    Zabíjí mutanta: `interface_active=True` natvrdo v _classify_interface.
    Jednotka atribut inactive sama nemá - dědí ho po fyzickém rodiči.
    """
    services = _services(module, parser_class, DEACTIVATED_INTERFACE)

    assert _by_name(services, "ge-0/0/4").interface_active is False
    assert _by_name(services, "ge-0/0/4.0").interface_active is False


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_interface_does_not_touch_routing_instance_flag(module, parser_class):
    """Zdroje deaktivace se nemíchají - RI je tu živá.

    Zabíjí mutanta: naplnění obou polí z téhož zdroje.
    """
    services = _services(module, parser_class, DEACTIVATED_INTERFACE)

    assert _by_name(services, "ge-0/0/4.0").routing_instance_active is True


DEACTIVATED_CONTAINERS = """
<configuration>
  <interfaces inactive="inactive">
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>113</name>
        <description>L3VPN-CPE13-NNI</description>
        <family>
          <inet>
            <address><name>198.11.13.1/30</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances inactive="inactive">
    <instance>
      <name>L3VPN-TEST</name>
      <instance-type>vrf</instance-type>
      <interface><name>ge-0/0/2.113</name></interface>
      <routing-options>
        <static>
          <route>
            <name>10.9.9.0/24</name>
            <next-hop>198.11.13.2</next-hop>
          </route>
        </static>
      </routing-options>
      <protocols>
        <bgp>
          <group>
            <name>CPE13</name>
            <neighbor><name>198.11.13.2</name></neighbor>
          </group>
        </bgp>
      </protocols>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_interfaces_container_flags_every_interface(module, parser_class):
    """<interfaces inactive> označí všechna rozhraní pod sebou.

    Zabíjí mutanta: _is_inactive, které se dívá jen na uzel a ne na předky.
    Bez dědění vrátí interface_active=True, protože samo <interface>
    atribut nemá.
    """
    services = _services(module, parser_class, DEACTIVATED_CONTAINERS)

    assert _by_name(services, "ge-0/0/2.113").interface_active is False


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_routing_instances_container_flags_the_service(module, parser_class):
    """<routing-instances inactive> označí služby všech VRF pod sebou."""
    services = _services(module, parser_class, DEACTIVATED_CONTAINERS)

    assert _by_name(services, "ge-0/0/2.113").routing_instance_active is False


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_routing_instances_container_drops_static_routes(module, parser_class):
    """Statiky pod deaktivovaným kontejnerem se vypustí ze záměru.

    Roadmapa to ověřila na `<routing-instances inactive="inactive">`: statika
    ('L3VPN-TEST.inet.0', '10.9.9.0/24') se dnes vrací jako živá. Deaktivovaná
    VRF žádnou routu do tabulky nedá, takže záměr z ní vzniknout nesmí -
    jinak check hlásí FAIL za routu, kterou nikdo nechce.

    Zabíjí mutanta: dědění zavedené jen pro rozhraní a ne pro statiky.
    """
    services = _services(module, parser_class, DEACTIVATED_CONTAINERS)

    routes = [route for service in services for route in service.static_route]

    assert routes == [], f"deaktivovaný kontejner vyrobil záměr: {routes}"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_bgp_container_drops_neighbors_and_bfd(module, parser_class):
    """<protocols>/<bgp> pod deaktivovanou VRF nedá souseda ani BFD záměr.

    Zabíjí mutanta: dědění zavedené jen pro statiky a ne pro BGP.
    """
    services = _services(module, parser_class, DEACTIVATED_CONTAINERS)

    peers = [peer for service in services for peer in service.bgp_neighbor]
    bfd = [intent for service in services for intent in service.bfd]

    assert peers == [], f"deaktivovaný kontejner vyrobil BGP záměr: {peers}"
    assert bfd == [], f"deaktivovaný kontejner vyrobil BFD záměr: {bfd}"


DEACTIVATED_UNIT_ONLY = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit inactive="inactive">
        <name>113</name>
        <description>L3VPN-CPE13-NNI</description>
        <family><inet><address><name>198.11.13.1/30</name></address></inet></family>
      </unit>
      <unit>
        <name>13</name>
        <description>INTERNET-CPE13-NNI</description>
        <family><inet><address><name>152.11.13.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_unit_under_active_interface_is_flagged(module, parser_class):
    """Deaktivace jednotky nesmí spadnout ani nahoru, ani na sousední jednotku.

    Zabíjí mutanta: `active=not physical_inactive` v _parse_interfaces, tedy
    "úroveň jednotky se ignoruje". Ten dnes přežije celou sadu - všechny
    ostatní fixtures deaktivují až fyzické rozhraní, takže se zděděná
    a vlastní deaktivace nedají rozlišit.
    """
    services = _services(module, parser_class, DEACTIVATED_UNIT_ONLY)

    assert _by_name(services, "ge-0/0/2").interface_active is True
    assert _by_name(services, "ge-0/0/2.113").interface_active is False
    assert _by_name(services, "ge-0/0/2.13").interface_active is True


INACTIVE_RIB = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/4</name>
      <unit>
        <name>0</name>
        <description>L3VPN-CPE14-UNI</description>
        <family><inet><address><name>198.11.14.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>L3VPN-CPE14-UNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>ge-0/0/4.0</name></interface>
      <routing-options>
        <rib inactive="inactive">
          <name>L3VPN-CPE14-UNI.inet.0</name>
          <static>
            <route><name>10.8.8.0/24</name><next-hop>198.11.14.2</next-hop></route>
          </static>
        </rib>
        <static>
          <route><name>10.9.9.0/24</name><next-hop>198.11.14.2</next-hop></route>
        </static>
      </routing-options>
    </instance>
  </routing-instances>
</configuration>
"""

INACTIVE_GROUP = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>13</name>
        <description>INTERNET-CPE13-NNI</description>
        <family><inet><address><name>152.11.13.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <protocols>
    <bgp>
      <group inactive="inactive">
        <name>CPE13</name>
        <neighbor><name>152.11.13.2</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_inactive_rib_drops_only_its_own_routes(module, parser_class):
    """Deaktivovaný `rib` vypustí své statiky a sousední `static` nechá být.

    Zabíjí mutanta (až po smazání guardu na rib_node): `_is_inactive` bez
    chůze po předcích. Routa uvnitř `rib` sama atribut nemá, takže bez dědění
    unikne do záměru.

    Next-hop uvnitř deaktivovaného `rib` musí ležet v subnetu rozhraní -
    jinak ji `_assign_static_routes` ke službě nepřipne a test by prošel
    i pod mutantem, protože by únik neviděl.
    """
    services = _services(module, parser_class, INACTIVE_RIB)
    routes = [route for service in services for route in service.static_route]

    assert [route["prefix"] for route in routes] == ["10.9.9.0/24"], routes


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_inactive_bgp_group_drops_its_neighbors(module, parser_class):
    """Deaktivovaná `group` nedá souseda.

    Zabíjí mutanta (až po smazání guardu na group_node): `_is_inactive` bez
    chůze po předcích. `neighbor` sám atribut nemá.
    """
    services = _services(module, parser_class, INACTIVE_GROUP)
    peers = [peer for service in services for peer in service.bgp_neighbor]

    assert peers == [], peers
