"""Offline testy BFD z konfigurace - dedeni neighbor > group > protocols bgp.

Junosi `inherit` tuhle hierarchii NErozbaluje. Overeno proti laborce
2026-07-29: na 172.20.20.5 je BFD na skupine CPE14 a jeji dva sousede
zadne bfd-liveness-detection nemaji - ani v konfiguraci stazene s inherit.
`inherit` rozbaluje apply-groups, ne hierarchii protokolu. Pruchod si proto
musi udelat parser sam.

Oba parsery se meni v zamku, takze kazdy test bezi proti obema.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


evo = _load("evo_parser_bfd_test", "evo_parser.py")
mx = _load("mx_parser_bfd_test", "mx_parser.py")

PARSERS = (
    pytest.param(evo, evo.JunosEvoAcxServiceParser, id="evo"),
    pytest.param(mx, mx.JunosServiceParser, id="mx"),
)

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
        <name>114</name>
        <description>CPE14-VRF</description>
        <family>
          <inet><address><name>198.11.14.1/29</name></address></inet>
          <inet6><address><name>2001:db8:11:14::a/64</name></address></inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
"""

# Skupina CPE14 nese BFD, jeji dva sousede vlastni nemaji - presne tvar,
# ktery je od 2026-07-29 v laborce.
GROUP_LEVEL = f"""
<configuration>
{INTERFACES}
  <routing-instances>
    <instance>
      <name>L3VPN-CPE14-UNI</name>
      <instance-type>vrf</instance-type>
      <interface><name>et-0/0/8.114</name></interface>
      <protocols>
        <bgp>
          <group>
            <name>CPE14</name>
            <bfd-liveness-detection>
              <minimum-interval>3000</minimum-interval>
              <multiplier>3</multiplier>
            </bfd-liveness-detection>
            <neighbor><name>198.11.14.2</name></neighbor>
            <neighbor><name>2001:db8:11:14::b</name></neighbor>
          </group>
        </bgp>
      </protocols>
    </instance>
  </routing-instances>
</configuration>
"""

# Soused ma vlastni, JINE timery nez skupina - specifictejsi musi vyhrat.
NEIGHBOR_OVERRIDES_GROUP = f"""
<configuration>
{INTERFACES}
  <protocols>
    <bgp>
      <group>
        <name>CPE</name>
        <bfd-liveness-detection>
          <minimum-interval>3000</minimum-interval>
          <multiplier>3</multiplier>
        </bfd-liveness-detection>
        <neighbor>
          <name>152.11.13.2</name>
          <bfd-liveness-detection>
            <minimum-interval>300</minimum-interval>
            <multiplier>5</multiplier>
          </bfd-liveness-detection>
        </neighbor>
        <neighbor><name>2001:abcd:11:13::b</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""

PROTOCOL_LEVEL = f"""
<configuration>
{INTERFACES}
  <protocols>
    <bgp>
      <bfd-liveness-detection>
        <minimum-interval>1000</minimum-interval>
        <multiplier>3</multiplier>
      </bfd-liveness-detection>
      <group>
        <name>CPE</name>
        <neighbor><name>152.11.13.2</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""

NO_BFD = f"""
<configuration>
{INTERFACES}
  <protocols>
    <bgp>
      <group>
        <name>CPE</name>
        <neighbor><name>152.11.13.2</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""

# Deaktivovane stanzy v laborce nejsou - ve vsech ctyrech captureech
# z 2026-07-29 nese inactive="inactive" jen <interface>, nikdy
# bfd-liveness-detection. Tvar se proto sklada rucne; je to presne to, co
# v konfiguraci necha junosi `deactivate`.
DEACTIVATED_GROUP_BFD = f"""
<configuration>
{INTERFACES}
  <protocols>
    <bgp>
      <group>
        <name>CPE</name>
        <bfd-liveness-detection inactive="inactive">
          <minimum-interval>3000</minimum-interval>
          <multiplier>3</multiplier>
        </bfd-liveness-detection>
        <neighbor><name>152.11.13.2</name></neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""

# Soused nese JEN minimum-interval, skupina nese oboje. Prave tady se
# prepis celou hodnotou lisi od slevani po polozkach: multiplier musi
# zustat None, ne 3. NEIGHBOR_OVERRIDES_GROUP tenhle rozdil nevidi, protoze
# jeho soused nastavuje oba udaje a obe implementace daji totez.
NEIGHBOR_PARTIAL_OVERRIDE_OF_GROUP = f"""
<configuration>
{INTERFACES}
  <protocols>
    <bgp>
      <group>
        <name>CPE</name>
        <bfd-liveness-detection>
          <minimum-interval>3000</minimum-interval>
          <multiplier>3</multiplier>
        </bfd-liveness-detection>
        <neighbor>
          <name>152.11.13.2</name>
          <bfd-liveness-detection>
            <minimum-interval>300</minimum-interval>
          </bfd-liveness-detection>
        </neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""

# Tentyz castecny prepis, ale soused visi PRIMO pod protocols bgp, mimo
# jakoukoli skupinu. Jiny tvar, jiny obsah `inherited` - proto vlastni
# fixture, ne varianta te predchozi.
NEIGHBOR_PARTIAL_OVERRIDE_OF_BGP = f"""
<configuration>
{INTERFACES}
  <protocols>
    <bgp>
      <bfd-liveness-detection>
        <minimum-interval>1000</minimum-interval>
        <multiplier>3</multiplier>
      </bfd-liveness-detection>
      <neighbor>
        <name>152.11.13.2</name>
        <bfd-liveness-detection>
          <minimum-interval>300</minimum-interval>
        </bfd-liveness-detection>
      </neighbor>
    </bgp>
  </protocols>
</configuration>
"""

DEACTIVATED_NEIGHBOR_BFD = f"""
<configuration>
{INTERFACES}
  <protocols>
    <bgp>
      <group>
        <name>CPE</name>
        <bfd-liveness-detection>
          <minimum-interval>3000</minimum-interval>
          <multiplier>3</multiplier>
        </bfd-liveness-detection>
        <neighbor>
          <name>152.11.13.2</name>
          <bfd-liveness-detection inactive="inactive">
            <minimum-interval>300</minimum-interval>
            <multiplier>5</multiplier>
          </bfd-liveness-detection>
        </neighbor>
      </group>
    </bgp>
  </protocols>
</configuration>
"""


def _bfd_by_peer(module, parser_class, xml: str) -> dict[str, dict]:
    services = parser_class(etree.XML(xml.encode())).parse()
    return {
        intent["peer"]: intent
        for service in services
        for intent in service.bfd
    }


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_group_level_bfd_reaches_every_neighbor_in_group(module, parser_class):
    """Vcetne IPv6 souseda - skupinove pravidlo neni na rodinu vazane."""
    intents = _bfd_by_peer(module, parser_class, GROUP_LEVEL)

    assert set(intents) == {"198.11.14.2", "2001:db8:11:14::b"}
    for peer in intents:
        assert intents[peer]["minimum_interval"] == 3000
        assert intents[peer]["multiplier"] == 3
        assert intents[peer]["source"] == "group"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_neighbor_level_overrides_group_level(module, parser_class):
    """Specifictejsi uroven prepisuje obecnejsi, a to celou hodnotou."""
    intents = _bfd_by_peer(module, parser_class, NEIGHBOR_OVERRIDES_GROUP)

    assert intents["152.11.13.2"]["minimum_interval"] == 300
    assert intents["152.11.13.2"]["multiplier"] == 5
    assert intents["152.11.13.2"]["source"] == "neighbor"

    assert intents["2001:abcd:11:13::b"]["minimum_interval"] == 3000
    assert intents["2001:abcd:11:13::b"]["source"] == "group"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_partial_override_of_group_does_not_inherit_the_missing_field(
    module, parser_class
):
    """Tohle je ta veta AR-13: nese se CELA hodnota, ne polozka po polozce.

    Soused nastavuje jen minimum-interval, takze multiplier zustava None -
    nedoplni se ze skupiny. Slevani po polozkach by vyrobilo zamer
    300/3, ktery v konfiguraci takhle nestoji na zadne urovni.

    test_neighbor_level_overrides_group_level tenhle rozdil zmerit neumi:
    jeho soused nese oba udaje, takze prepis celou hodnotou i slevani po
    polozkach daji stejny vysledek.
    """
    intents = _bfd_by_peer(module, parser_class, NEIGHBOR_PARTIAL_OVERRIDE_OF_GROUP)

    assert set(intents) == {"152.11.13.2"}
    assert intents["152.11.13.2"]["minimum_interval"] == 300
    assert intents["152.11.13.2"]["multiplier"] is None
    assert intents["152.11.13.2"]["source"] == "neighbor"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_partial_override_of_bgp_level_does_not_inherit_the_missing_field(
    module, parser_class
):
    """Tentyz castecny prepis u souseda viseciho primo pod protocols bgp.

    Jiny tvar konfigurace nez predchozi test - soused neni v zadne skupine,
    takze dedi z protokolove urovne. `assert set(intents)` je tady
    podstatny: kdyby se peer na zadnou sluzbu nenamapoval, _bfd_by_peer by
    vratil prazdny slovnik a test by prosel, aniz by cokoli overil.
    """
    intents = _bfd_by_peer(module, parser_class, NEIGHBOR_PARTIAL_OVERRIDE_OF_BGP)

    assert set(intents) == {"152.11.13.2"}
    assert intents["152.11.13.2"]["minimum_interval"] == 300
    assert intents["152.11.13.2"]["multiplier"] is None
    assert intents["152.11.13.2"]["source"] == "neighbor"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_protocol_level_bfd_reaches_neighbor_through_group(module, parser_class):
    intents = _bfd_by_peer(module, parser_class, PROTOCOL_LEVEL)

    assert intents["152.11.13.2"]["minimum_interval"] == 1000
    assert intents["152.11.13.2"]["source"] == "bgp"


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_no_bfd_means_no_intent(module, parser_class):
    """Sluzba bez BFD nema v inventory prazdny zaznam, ma prazdny seznam."""
    intents = _bfd_by_peer(module, parser_class, NO_BFD)

    assert intents == {}


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_bfd_stanza_yields_no_intent(module, parser_class):
    """Deaktivovana stanza je totez, jako kdyby tam nebyla.

    `deactivate` je standardni idiom pro vyrazeni konfigurace pri migraci.
    Kdyby z nej vznikl zivy zamer, check by hlasil 'FAIL ... bez session'
    za ochranu, kterou operator vedome vypnul.
    """
    intents = _bfd_by_peer(module, parser_class, DEACTIVATED_GROUP_BFD)

    assert intents == {}


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_deactivated_neighbor_bfd_falls_back_to_group(module, parser_class):
    """Deaktivovana uroven neprepisuje, jen zmizi - dedeni pokracuje vys.

    Presne to udela i Junos: soused, kterym je bfd-liveness-detection
    vyrazene, spada pod pravidlo skupiny.
    """
    intents = _bfd_by_peer(module, parser_class, DEACTIVATED_NEIGHBOR_BFD)

    assert set(intents) == {"152.11.13.2"}
    assert intents["152.11.13.2"]["minimum_interval"] == 3000
    assert intents["152.11.13.2"]["multiplier"] == 3
    assert intents["152.11.13.2"]["source"] == "group"
