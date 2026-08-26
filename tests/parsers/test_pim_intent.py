"""PIM zamer: 'pim' v protocol znamena 'neighbor je ocekavany' (spec 2026-08-26).
Bez zaznamu check mlci - parsovani je jediny zdroj toho ocekavani.

Oba parsery se meni v zamku, takze test bezi proti obema (viz
test_family_split.py).

RI-PIM variantu (routing-instances X protocols pim interface ...) test
nema - zachycene lab configy s touto konfiguraci nejsou v repu (jen
offline XML idiomy pro testy), takze tvar nejde overit. RoutingInstance
uz ale sesbira protocols pres child_names(node, "./protocols/*")
(existujici mechanismus, RoutingInstance.protocols -> _collect_protocols),
takze az se RI-PIM tvar objevi, mel by fungovat bez dalsi zmeny parseru.
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
      <name>ge-0/0/0</name>
      <unit>
        <name>0</name>
        <family>
          <iso/>
          <mpls/>
          <inet>
            <address><name>10.1.2.1/31</name></address>
          </inet>
        </family>
      </unit>
    </interface>
    <interface>
      <name>ge-0/0/1</name>
      <unit>
        <name>0</name>
        <family>
          <iso/>
          <mpls/>
          <inet>
            <address><name>10.1.2.5/31</name></address>
          </inet>
        </family>
      </unit>
      <unit>
        <name>1</name>
        <family>
          <inet>
            <address><name>10.1.2.9/31</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <protocols>
    <pim>
      <interface>
        <name>ge-0/0/1.0</name>
      </interface>
    </pim>
  </protocols>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_pim_interface_gets_pim_protocol(parser_class):
    services = parser_class(etree.fromstring(CONFIG)).parse()
    by_iface = {s.interface: s for s in services}

    assert "pim" in by_iface["ge-0/0/1.0"].protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_interface_without_pim_stays_clean(parser_class):
    """Druhe Core rozhrani bez PIM zaznamu si 'pim' neveze."""
    services = parser_class(etree.fromstring(CONFIG)).parse()
    by_iface = {s.interface: s for s in services}

    assert "pim" not in by_iface["ge-0/0/0.0"].protocol


@pytest.mark.parametrize("parser_class", PARSERS)
def test_sibling_unit_does_not_inherit_pim(parser_class):
    """PIM je na logicke jednotce - sesterska unit stejneho fyzickeho
    rozhrani (ge-0/0/1.1) nesmi 'pim' dostat pres merge podle
    physical_name, kdyby global_protocols_by_interface omylem plnilo
    fyzicky nazev misto logickeho."""
    services = parser_class(etree.fromstring(CONFIG)).parse()
    by_iface = {s.interface: s for s in services}

    assert "pim" not in by_iface["ge-0/0/1.1"].protocol
