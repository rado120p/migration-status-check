"""Testy LDP collectoru proti nahranemu XML z laborky.

Nahravka nese ctyri sousedstvi na kazde platforme, ale dve z nich jsou na
lo0.0 (targeted session mezi loopbacky, ne stav tranzitniho linku) - collector
je musi zahodit. Zbyvaji dve fyzicka rozhrani: ge-0/0/0.0 + ge-0/0/1.0 na
junos, et-0/0/0.0 + et-0/0/1.0 na junos-evo.
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.collectors.ldp import LdpNeighborCollector

PLATFORMS = ("junos", "junos-evo")

FIRST_IFACE = {"junos": "ge-0/0/0.0", "junos-evo": "et-0/0/0.0"}
SECOND_IFACE = {"junos": "ge-0/0/1.0", "junos-evo": "et-0/0/1.0"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ldp_neighbor_parses_fixture(rpc_fixture, platform):
    data = LdpNeighborCollector().parse(rpc_fixture(platform, "ldp_neighbor"), platform)
    entry = data[FIRST_IFACE[platform]]
    assert entry["neighbor_address"]
    assert isinstance(entry["uptime_seconds"], int)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ldp_neighbor_has_two_transit_entries(rpc_fixture, platform):
    """Ctyri zaznamy v nahravce, dva na lo0.0 - po zahozeni zbyvaji dva."""
    data = LdpNeighborCollector().parse(rpc_fixture(platform, "ldp_neighbor"), platform)
    assert set(data) == {FIRST_IFACE[platform], SECOND_IFACE[platform]}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ldp_neighbor_discards_lo0_entries(rpc_fixture, platform):
    """lo0.0 nese targeted session, ne stav tranzitniho linku (spec)."""
    data = LdpNeighborCollector().parse(rpc_fixture(platform, "ldp_neighbor"), platform)
    assert "lo0.0" not in data


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ldp_neighbor_rpc_name_and_kwargs(platform):
    collector = LdpNeighborCollector()
    assert collector.rpc_name(platform) == "get_ldp_neighbor_information"
    assert collector.rpc_kwargs(platform) == {"detail": True}


# --- Synteticke varianty (XML odvozeny z nahranych fixtures) -------------

EMPTY_LDP = "<ldp-neighbor-information/>"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ldp_neighbor_empty_output_is_empty_dict(platform):
    """Prazdny vypis (LDP bez sousedu) - absence je absence klice, ne
    syntetizovany Down."""
    data = LdpNeighborCollector().parse(etree.fromstring(EMPTY_LDP.encode()), platform)
    assert data == {}


LDP_ONLY_LO0 = """
<ldp-neighbor-information>
  <ldp-neighbor>
    <ldp-neighbor-address>150.0.0.1</ldp-neighbor-address>
    <interface-name>lo0.0</interface-name>
    <ldp-up-time seconds="4336">01:12:16</ldp-up-time>
  </ldp-neighbor>
</ldp-neighbor-information>
"""


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ldp_neighbor_only_lo0_entry_yields_empty_dict(platform):
    data = LdpNeighborCollector().parse(etree.fromstring(LDP_ONLY_LO0.encode()), platform)
    assert data == {}


LDP_TEXT_UPTIME = """
<ldp-neighbor-information>
  <ldp-neighbor>
    <ldp-neighbor-address>10.1.2.0</ldp-neighbor-address>
    <interface-name>xe-0/0/0.0</interface-name>
    <ldp-up-time>112w2d 18:30:36</ldp-up-time>
  </ldp-neighbor>
  <ldp-neighbor>
    <ldp-neighbor-address>10.1.3.0</ldp-neighbor-address>
    <interface-name>xe-0/0/1.0</interface-name>
    <ldp-up-time>3d 00:00:01</ldp-up-time>
  </ldp-neighbor>
  <ldp-neighbor>
    <ldp-neighbor-address>10.1.4.0</ldp-neighbor-address>
    <interface-name>xe-0/0/2.0</interface-name>
    <ldp-up-time>01:02:03</ldp-up-time>
  </ldp-neighbor>
  <ldp-neighbor>
    <ldp-neighbor-address>10.1.5.0</ldp-neighbor-address>
    <interface-name>xe-0/0/3.0</interface-name>
    <ldp-up-time>nesmysl</ldp-up-time>
  </ldp-neighbor>
</ldp-neighbor-information>
"""


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ldp_neighbor_parses_text_uptime_without_seconds_attr(platform):
    """Nektere MX verze neposilaji junos:seconds, jen text '112w2d 18:30:36'."""
    data = LdpNeighborCollector().parse(etree.fromstring(LDP_TEXT_UPTIME.encode()), platform)
    assert data["xe-0/0/0.0"]["uptime_seconds"] == 112 * 7 * 86400 + 2 * 86400 + 18 * 3600 + 30 * 60 + 36
    assert data["xe-0/0/1.0"]["uptime_seconds"] == 3 * 86400 + 1
    assert data["xe-0/0/2.0"]["uptime_seconds"] == 3723
    assert data["xe-0/0/3.0"]["uptime_seconds"] is None
