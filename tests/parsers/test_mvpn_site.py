"""Role MVPN instance z konfigurace (spec 2026-09-07): sender <=> sender-site
nebo provider-tunnel; receiver <=> receiver-site nebo zadne site klicove slovo
(Junos default je obojí). Slouzi jen k interpretaci prazdne PIM join tabulky."""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

PARSERS = (
    pytest.param(JunosEvoAcxServiceParser, id="evo"),
    pytest.param(JunosServiceParser, id="mx"),
)

TUNNEL = """
<provider-tunnel><family><inet><rsvp-te>
  <label-switched-path-template><template-name>T</template-name></label-switched-path-template>
</rsvp-te></inet></family></provider-tunnel>
"""


def _config(mvpn: str | None, tunnel: bool) -> str:
    mvpn_xml = f"<mvpn>{mvpn or ''}</mvpn>" if mvpn is not None else ""
    return f"""
<configuration>
  <interfaces>
    <interface><name>irb</name><unit><name>10</name>
      <family><inet><address><name>10.100.11.1/30</name></address></inet></family>
    </unit></interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>RI</name>
      <instance-type>vrf</instance-type>
      <interface><name>irb.10</name></interface>
      {TUNNEL if tunnel else ''}
      <protocols>
        {mvpn_xml}
        <pim><interface><name>irb.10</name></interface></pim>
      </protocols>
    </instance>
  </routing-instances>
</configuration>
"""


CASES = [
    pytest.param(None, False, [], id="no-mvpn"),
    pytest.param("<sender-site/>", False, ["sender"], id="sender-site"),
    pytest.param("<receiver-site/>", False, ["receiver"], id="receiver-site"),
    pytest.param("<sender-site/>", True, ["sender"], id="tunnel+sender-site"),
    pytest.param("<receiver-site/>", True, ["receiver", "sender"], id="tunnel+receiver-site"),
    pytest.param("", True, ["receiver", "sender"], id="tunnel-no-site-keyword"),
    pytest.param("", False, ["receiver"], id="mvpn-no-site-no-tunnel"),
]


@pytest.mark.parametrize("parser_class", PARSERS)
@pytest.mark.parametrize(("mvpn", "tunnel", "expected"), CASES)
def test_mvpn_site_derivation(parser_class, mvpn, tunnel, expected):
    parser = parser_class(etree.fromstring(_config(mvpn, tunnel)))
    services = {s.interface: s for s in parser.parse()}
    assert parser.routing_instances["RI"].mvpn_site == expected
    assert services["irb.10"].mvpn_site == expected
