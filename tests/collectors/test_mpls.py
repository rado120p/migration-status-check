"""Testy MPLS interface collectoru proti nahranemu XML z laborky."""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.collectors.mpls import MplsInterfaceCollector

PLATFORMS = ("junos", "junos-evo")

FIRST_IFACE = {"junos": "ge-0/0/0.0", "junos-evo": "et-0/0/0.0"}
SECOND_IFACE = {"junos": "ge-0/0/1.0", "junos-evo": "et-0/0/1.0"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_mpls_interface_parses_fixture(rpc_fixture, platform):
    data = MplsInterfaceCollector().parse(rpc_fixture(platform, "mpls_interface"), platform)
    assert data[FIRST_IFACE[platform]] == {"state": "Up"}
    assert data[SECOND_IFACE[platform]] == {"state": "Up"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_mpls_interface_rpc_name_and_kwargs(platform):
    collector = MplsInterfaceCollector()
    assert collector.rpc_name(platform) == "get_mpls_interface_information"
    assert collector.rpc_kwargs(platform) == {}


# --- Synteticke varianty (XML odvozeny z nahranych fixtres) --------------

EMPTY_MPLS = "<mpls-interface-information/>"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_mpls_interface_empty_output_is_empty_dict(platform):
    data = MplsInterfaceCollector().parse(etree.fromstring(EMPTY_MPLS.encode()), platform)
    assert data == {}


MPLS_MISSING_STATE = """
<mpls-interface-information>
  <mpls-interface>
    <interface-name>ge-0/0/2.0</interface-name>
  </mpls-interface>
</mpls-interface-information>
"""


@pytest.mark.parametrize("platform", PLATFORMS)
def test_mpls_interface_missing_state_falls_back_to_unknown(platform):
    data = MplsInterfaceCollector().parse(
        etree.fromstring(MPLS_MISSING_STATE.encode()), platform
    )
    assert data["ge-0/0/2.0"] == {"state": "unknown"}
