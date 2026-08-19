"""Testy collectoru statickych rout proti nahranemu XML z laborky.

Fixtures jsou skutecne odpovedi z 2026-07-29. Na junos (vMX) jsou v tabulce
jen mgmt routy - servisni statiky tam sice nakonfigurovane jsou, ale
nenainstalovaly se, protoze jejich next-hop neexistuje (rozhrani je po
migraci deaktivovane). Prave tenhle rozpor ma check chytat.
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.collectors.base import CollectorError
from migration_validator.collectors.routes import RoutesCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_mapping_of_tables(rpc_fixture, platform):
    result = RoutesCollector().parse(rpc_fixture(platform, "routes"), platform)
    assert isinstance(result, dict)
    assert result, "fixture nema zadnou statickou routu"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_entries_have_expected_keys(rpc_fixture, platform):
    result = RoutesCollector().parse(rpc_fixture(platform, "routes"), platform)
    for prefixes in result.values():
        for data in prefixes.values():
            assert set(data) == {"next_hop", "via", "active", "protocol"}


def test_table_name_carries_rib_and_family(rpc_fixture):
    """table-name nese RIB i rodinu v jednom poli - proto se na nej normalizuje parser."""
    result = RoutesCollector().parse(rpc_fixture("junos-evo", "routes"), "junos-evo")

    assert result["inet.0"]["198.62.1.0/29"]["next_hop"] == ["152.11.13.2"]
    assert result["inet6.0"]["2001:aaaa::/64"]["next_hop"] == ["2001:abcd:11:13::b"]
    assert result["L3VPN-CPE13-NNI.inet.0"]["172.26.1.0/29"]["via"] == ["et-0/0/8.113"]
    assert "L3VPN-CPE13-NNI.inet6.0" in result


def test_management_routes_are_collected_not_filtered(rpc_fixture):
    """Collector neinterpretuje. Vyrazeni mgmt rout patri do scope, ne sem."""
    result = RoutesCollector().parse(rpc_fixture("junos", "routes"), "junos")

    assert result["mgmt_junos.inet.0"]["0.0.0.0/0"]["via"] == ["fxp0.0"]
    assert result["mgmt_junos.inet6.0"]["::/0"]["next_hop"] == ["2001:db8::1"]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_empty_tables_are_dropped(rpc_fixture, platform):
    """RPC vraci pres dvacet tabulek, vetsina prazdna - ty by snimek jen nafoukly."""
    result = RoutesCollector().parse(rpc_fixture(platform, "routes"), platform)

    assert all(prefixes for prefixes in result.values())


# Druhy pruchod (protocol=aggregate): nahravka z Tasku 1 ukazuje, ze
# agregatni zaznam nema zadny <nh> - <nh-type> (Discard/Reject) sedi
# primo pod <rt-entry>. _texts(entry, "to"/"via") tak pro nej prirozene
# vrati []. protocol-name nese presne "Aggregate" (viz
# tests/fixtures/rpc/*/routes.2.xml).

STATIC_XML = b"""
<route-information>
  <route-table>
    <table-name>inet6.0</table-name>
    <rt>
      <rt-destination>2001:aaaa::/64</rt-destination>
      <rt-entry>
        <active-tag>*</active-tag>
        <protocol-name>Static</protocol-name>
        <preference>5</preference>
        <nh>
          <to>2001:abcd:11:13::b</to>
          <via>et-0/0/8.13</via>
        </nh>
      </rt-entry>
    </rt>
  </route-table>
</route-information>
"""

AGGREGATE_XML = b"""
<route-information>
  <route-table>
    <table-name>inet6.0</table-name>
    <rt>
      <rt-destination>2001:abcd::/32</rt-destination>
      <rt-entry>
        <active-tag>*</active-tag>
        <protocol-name>Aggregate</protocol-name>
        <nh-type>Discard</nh-type>
      </rt-entry>
    </rt>
  </route-table>
</route-information>
"""

AGGREGATE_XML_SE_STEJNYM_PREFIXEM = b"""
<route-information>
  <route-table>
    <table-name>inet6.0</table-name>
    <rt>
      <rt-destination>2001:aaaa::/64</rt-destination>
      <rt-entry>
        <active-tag>*</active-tag>
        <protocol-name>Aggregate</protocol-name>
        <nh-type>Discard</nh-type>
      </rt-entry>
    </rt>
  </route-table>
</route-information>
"""


def _static_xml() -> etree._Element:
    return etree.fromstring(STATIC_XML)


def test_parse_tagne_aggregate_zaznam_protokolem():
    parsed = RoutesCollector().parse(etree.fromstring(AGGREGATE_XML), "junos-evo")
    entry = parsed["inet6.0"]["2001:abcd::/32"]
    assert entry["protocol"] == "aggregate"
    assert entry["next_hop"] == []
    assert entry["via"] == []
    assert entry["active"] is True


def test_parse_tagne_static_zaznam_protokolem():
    # stavajici STATIC_XML konstanta / fixture routes.xml
    parsed = RoutesCollector().parse(_static_xml(), "junos-evo")
    entry = next(iter(next(iter(parsed.values())).values()))
    assert entry["protocol"] == "static"


FOREIGN_PROTOCOL_XML = b"""
<route-information>
  <route-table>
    <table-name>inet6.0</table-name>
    <rt>
      <rt-destination>2001:aaaa::/64</rt-destination>
      <rt-entry>
        <active-tag>*</active-tag>
        <protocol-name>Static</protocol-name>
        <preference>5</preference>
        <nh>
          <to>2001:abcd:11:13::b</to>
          <via>et-0/0/8.13</via>
        </nh>
      </rt-entry>
    </rt>
    <rt>
      <rt-destination>2001:bbbb::/64</rt-destination>
      <rt-entry>
        <active-tag>*</active-tag>
        <protocol-name>BGP</protocol-name>
        <preference>170</preference>
        <nh>
          <to>2001:abcd:11:13::c</to>
          <via>et-0/0/8.14</via>
        </nh>
      </rt-entry>
    </rt>
  </route-table>
</route-information>
"""


def test_parse_filtruje_cizi_protokol_ale_neztraci_static():
    """Pojistka v parse() (`if protocol not in PROTOCOLS: continue`) musi
    zahodit zaznam s protokolem mimo static/aggregate (napr. BGP), zatimco
    sousedni static zaznam ve stejne tabulce zustane netknuty - filtr je
    selektivni, ne rozbity."""
    parsed = RoutesCollector().parse(etree.fromstring(FOREIGN_PROTOCOL_XML), "junos-evo")

    assert "2001:bbbb::/64" not in parsed["inet6.0"]
    assert parsed["inet6.0"]["2001:aaaa::/64"]["protocol"] == "static"


class _FakeRoutesRpc:
    """Vraci nahrane XML podle hodnoty kwargu `protocol` - stejne RPC,
    jina varianta stejne jako u InterfacesCollector.

    Hodnota v `by_protocol` muze byt i vyjimka - simuluje selhani jednoho
    z pruchodu (RpcError, sitova chyba)."""

    def __init__(self, by_protocol):
        self._by_protocol = by_protocol

    def get_route_information(self, **kwargs):
        outcome = self._by_protocol[kwargs["protocol"]]
        if isinstance(outcome, Exception):
            raise outcome
        return etree.fromstring(outcome)


class _FakeDevice:
    def __init__(self, by_protocol):
        self.rpc = _FakeRoutesRpc(by_protocol)


def test_collect_merguje_oba_pruchody_a_druhy_jen_pridava():
    device = _FakeDevice({
        "static": STATIC_XML,
        "aggregate": AGGREGATE_XML_SE_STEJNYM_PREFIXEM,
    })
    facts = RoutesCollector().collect(device, "junos-evo")
    # prefix z prvniho pruchodu nesmi byt prepsan druhym
    assert facts["inet6.0"]["2001:aaaa::/64"]["protocol"] == "static"


def test_rpc_calls_stril_static_a_aggregate():
    assert RoutesCollector().rpc_calls("junos-evo") == (
        ("get_route_information", {"protocol": "static"}),
        ("get_route_information", {"protocol": "aggregate"}),
    )


def test_collect_selhani_druheho_pruchodu_je_chyba_celeho_collectoru():
    """Bez agregatniho pruchodu by check cetl chybejici agregat jako
    zmizely - castecna data nesmi vypadat jako zmerena. Selhani
    kterehokoliv pruchodu proto musi shodit CollectorError, ne tise vratit
    jen to, co se stihlo nasbirat."""
    device = _FakeDevice({
        "static": STATIC_XML,
        "aggregate": RuntimeError("timeout"),
    })
    with pytest.raises(CollectorError, match=r"protocol=aggregate.*timeout"):
        RoutesCollector().collect(device, "junos-evo")
