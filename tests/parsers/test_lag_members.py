"""Test cteni 802.3ad clenstvi do lag_members zaznamu bundlu (Layer1)."""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

PARSERS = (
    pytest.param(JunosEvoAcxServiceParser, id="evo"),
    pytest.param(JunosServiceParser, id="mx"),
)

# Realna konfigurace z labu pouziva tecku (ieee-802.3ad), presne podle CLI
# stanzy "gigether-options 802.3ad ae0" - overeno na labu 2026-08-12.
CONFIG_S_LAGEM = """
<configuration>
  <interfaces>
    <interface>
      <name>ae0</name>
    </interface>
    <interface>
      <name>et-0/0/5</name>
      <gigether-options>
        <ieee-802.3ad>
          <bundle>ae0</bundle>
        </ieee-802.3ad>
      </gigether-options>
    </interface>
    <interface>
      <name>et-0/0/6</name>
      <gigether-options>
        <ieee-802.3ad>
          <bundle>ae0</bundle>
        </ieee-802.3ad>
      </gigether-options>
    </interface>
  </interfaces>
</configuration>
"""

# Nekdy se objevi i varianta se spojovnikem (ieee-802-3ad) - tolerance pro
# starsi/jine renderovani configu.
CONFIG_S_LAGEM_SPOJOVNIK = """
<configuration>
  <interfaces>
    <interface>
      <name>ae0</name>
    </interface>
    <interface>
      <name>et-0/0/5</name>
      <gigether-options>
        <ieee-802-3ad>
          <bundle>ae0</bundle>
        </ieee-802-3ad>
      </gigether-options>
    </interface>
    <interface>
      <name>et-0/0/6</name>
      <gigether-options>
        <ieee-802-3ad>
          <bundle>ae0</bundle>
        </ieee-802-3ad>
      </gigether-options>
    </interface>
  </interfaces>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_lag_clenstvi_se_pripne_na_bundle(parser_class):
    services = parser_class(etree.fromstring(CONFIG_S_LAGEM)).parse()

    bundle = next(s for s in services if s.interface == "ae0")
    assert bundle.service_type == "Layer1"
    assert bundle.lag_members == ["et-0/0/5", "et-0/0/6"]

    members = [s for s in services if s.interface.startswith("et-0/0/")]
    assert all(s.lag_members == [] for s in members)


@pytest.mark.parametrize("parser_class", PARSERS)
def test_lag_clenstvi_se_pripne_na_bundle_spojovnikova_varianta(parser_class):
    services = parser_class(etree.fromstring(CONFIG_S_LAGEM_SPOJOVNIK)).parse()

    bundle = next(s for s in services if s.interface == "ae0")
    assert bundle.lag_members == ["et-0/0/5", "et-0/0/6"]
