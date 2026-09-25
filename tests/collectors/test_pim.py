"""Testy PIM collectoru proti nahranemu XML z laborky.

Vnoreni je pim-neighbors-information > pim-interface > pim-neighbor, pricemz
element pim-interface-name lezi uvnitr pim-neighbor (ne primo pod
pim-interface) - collector proto hleda jmeno rozhrani jako libovolneho
potomka pim-interface, aby fungoval na obou tvarech. junos-evo navic nese
<banner-fifth/> v neighbor-option-banner, ktery junos nema - dekorativni,
nesmi ovlivnit parsovani.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from migration_validator.collectors.pim import PimNeighborCollector

CASES = Path(__file__).resolve().parents[1] / "fixtures" / "cases"

PLATFORMS = ("junos", "junos-evo")

FIRST_IFACE = {"junos": "ge-0/0/0.0", "junos-evo": "et-0/0/0.0"}
SECOND_IFACE = {"junos": "ge-0/0/1.0", "junos-evo": "et-0/0/1.0"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_pim_neighbor_parses_fixture(rpc_fixture, platform):
    data = PimNeighborCollector().parse(rpc_fixture(platform, "pim_neighbor"), platform)
    entry = data[FIRST_IFACE[platform]]
    assert entry["neighbor_address"]
    assert isinstance(entry["uptime_seconds"], int)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_pim_neighbor_has_two_entries(rpc_fixture, platform):
    data = PimNeighborCollector().parse(rpc_fixture(platform, "pim_neighbor"), platform)
    assert set(data) == {FIRST_IFACE[platform], SECOND_IFACE[platform]}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_pim_neighbor_rpc_name_and_kwargs(platform):
    collector = PimNeighborCollector()
    assert collector.rpc_name(platform) == "get_pim_neighbors_information"
    assert collector.rpc_kwargs(platform) == {}


# --- Synteticke varianty (XML odvozeny z nahranych fixtures) -------------

EMPTY_PIM = "<pim-neighbors-information/>"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_pim_neighbor_empty_output_is_empty_dict(platform):
    """Prazdny vypis (PIM bez sousedu) - absence je absence klice."""
    data = PimNeighborCollector().parse(etree.fromstring(EMPTY_PIM.encode()), platform)
    assert data == {}


PIM_INTERFACE_WITHOUT_NEIGHBOR = """
<pim-neighbors-information style="basic">
  <pim-interface>
    <pim-neighbor>
      <pim-interface-name>ge-0/0/2.0</pim-interface-name>
      <pim-neighbor-uptime seconds="10">00:00:10</pim-neighbor-uptime>
      <pim-neighbor-address>10.1.9.1</pim-neighbor-address>
    </pim-neighbor>
  </pim-interface>
  <pim-interface>
    <pim-interface-name>ge-0/0/3.0</pim-interface-name>
  </pim-interface>
</pim-neighbors-information>
"""


@pytest.mark.parametrize("platform", PLATFORMS)
def test_pim_interface_without_neighbor_gets_no_key(platform):
    """pim-interface bez vnoreneho pim-neighbor neni synteticky Down zaznam -
    proste chybi (absence = absence klice)."""
    data = PimNeighborCollector().parse(
        etree.fromstring(PIM_INTERFACE_WITHOUT_NEIGHBOR.encode()), platform
    )
    assert set(data) == {"ge-0/0/2.0"}
    assert "ge-0/0/3.0" not in data


# --- Dual-stack testy (IPv6 neprepise IPv4) --------------------------------


def test_dual_stack_v6_neighbor_does_not_overwrite_v4():
    xml = etree.parse(str(CASES / "pim_neighbors_dual_stack.xml")).getroot()
    result = PimNeighborCollector().parse(xml, "junos-evo")
    assert result["et-0/0/1.0"] == {"neighbor_address": "10.1.0.4", "uptime_seconds": 87773}
    assert result["et-0/0/0.0"]["neighbor_address"] == "10.1.1.2"


def test_neighbor_without_ip_protocol_version_counts_as_v4():
    xml = etree.fromstring(
        "<pim-neighbors-information><pim-interface><pim-neighbor>"
        "<pim-interface-name>et-0/0/2.0</pim-interface-name>"
        "<pim-neighbor-address>10.9.0.2</pim-neighbor-address>"
        "</pim-neighbor></pim-interface></pim-neighbors-information>"
    )
    assert PimNeighborCollector().parse(xml, "junos")["et-0/0/2.0"]["neighbor_address"] == "10.9.0.2"


def test_only_v6_neighbor_gives_no_key():
    xml = etree.fromstring(
        "<pim-neighbors-information><pim-interface><pim-neighbor>"
        "<pim-interface-name>et-0/0/2.0</pim-interface-name>"
        "<ip-protocol-version>6</ip-protocol-version>"
        "<pim-neighbor-address>fe80::1</pim-neighbor-address>"
        "</pim-neighbor></pim-interface></pim-neighbors-information>"
    )
    assert PimNeighborCollector().parse(xml, "junos") == {}
