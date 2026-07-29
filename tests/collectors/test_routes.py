"""Testy collectoru statickych rout proti nahranemu XML z laborky.

Fixtures jsou skutecne odpovedi z 2026-07-29. Na junos (vMX) jsou v tabulce
jen mgmt routy - servisni statiky tam sice nakonfigurovane jsou, ale
nenainstalovaly se, protoze jejich next-hop neexistuje (rozhrani je po
migraci deaktivovane). Prave tenhle rozpor ma check chytat.
"""

from __future__ import annotations

import pytest

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
            assert set(data) == {"next_hop", "via", "active"}


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


@pytest.mark.parametrize("platform", PLATFORMS)
def test_values_are_stripped(rpc_fixture, platform):
    """MX obaluje hodnoty novymi radky, EVO ne."""
    result = RoutesCollector().parse(rpc_fixture(platform, "routes"), platform)

    for prefixes in result.values():
        for prefix, data in prefixes.items():
            assert prefix == prefix.strip()
            for value in data["next_hop"] + data["via"]:
                assert value == value.strip(), f"nese bile znaky: {value!r}"
