"""Offline testy parseru - konfigurace se vklada jako XML, laborka neni potreba.

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
    """Parsery jsou skripty v korenu repozitare, ne balicek - nacteme je podle cesty."""
    spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    # dataclasses vyhledavaji svuj modul v sys.modules (kvuli resolvingu
    # typovych anotaci) - bez registrace pred exec_module to padne na
    # AttributeError: 'NoneType' object has no attribute '__dict__'.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


evo = _load("evo_parser_under_test", "evo_parser.py")
mx = _load("mx_parser_under_test", "mx_parser.py")

PARSERS = (
    pytest.param(evo, evo.JunosEvoAcxServiceParser, id="evo"),
    pytest.param(mx, mx.JunosServiceParser, id="mx"),
)

DUAL_STACK = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>13</name>
        <description>INTERNET-CPE13-NNI</description>
        <family>
          <inet>
            <address><name>152.11.13.1/30</name></address>
          </inet>
          <inet6>
            <address><name>2001:abcd:11:13::a/127</name></address>
          </inet6>
        </family>
      </unit>
    </interface>
  </interfaces>
</configuration>
"""


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_families_are_separate_fields(module, parser_class):
    services = parser_class(etree.fromstring(DUAL_STACK)).parse()
    unit = next(s for s in services if s.interface == "ge-0/0/2.13")

    assert unit.ipv4_address == ["152.11.13.1/30"]
    assert unit.ipv6_address == ["2001:abcd:11:13::a/127"]


@pytest.mark.parametrize("module,parser_class", PARSERS)
def test_merged_field_is_gone(module, parser_class):
    """Slite pole nesmi prezit - jinak by se na nej necekane navazalo."""
    services = parser_class(etree.fromstring(DUAL_STACK)).parse()
    unit = next(s for s in services if s.interface == "ge-0/0/2.13")

    assert not hasattr(unit, "ip_address")
