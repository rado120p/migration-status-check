"""Core se deli na subtype transit/loopback - lo0 a tranzitni port uz nesmi
sdilet jeden profil checku (spec 2026-08-26).

Oba parsery se meni v zamku, takze test bezi proti obema (viz
test_family_split.py).
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
      <name>lo0</name>
      <unit>
        <name>0</name>
        <family>
          <iso/>
          <inet>
            <address><name>150.0.0.11/32</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_core_subtype_transit_vs_loopback(parser_class):
    services = parser_class(etree.fromstring(CONFIG)).parse()
    by_iface = {s.interface: s for s in services if s.service_type == "Core"}

    assert by_iface["ge-0/0/0.0"].service_subtype == "transit"
    assert by_iface["lo0.0"].service_subtype == "loopback"
