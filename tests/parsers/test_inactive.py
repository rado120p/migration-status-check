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
