import pytest

from migration_validator.collectors.nd import NdCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_list_of_entries(rpc_fixture, platform):
    result = NdCollector().parse(rpc_fixture(platform, "nd"), platform)
    assert isinstance(result, list)
    assert result, "fixture nema zadny ND zaznam"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_entries_have_expected_keys(rpc_fixture, platform):
    result = NdCollector().parse(rpc_fixture(platform, "nd"), platform)
    for entry in result:
        assert set(entry) == {"ip", "mac", "interface", "state"}
        assert entry["ip"]
        assert entry["interface"]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_values_are_stripped(rpc_fixture, platform):
    """MX obaluje hodnoty novymi radky, EVO ne - ping by dostal nevalidni cil."""
    result = NdCollector().parse(rpc_fixture(platform, "nd"), platform)
    for entry in result:
        for key in ("ip", "mac", "interface", "state"):
            value = entry[key]
            if value is not None:
                assert value == value.strip(), f"{key} nese bile znaky: {value!r}"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_link_local_entries_are_kept_by_collector(rpc_fixture, platform):
    """Collector neinterpretuje - filtr link-local patri do resolvovani cilu."""
    result = NdCollector().parse(rpc_fixture(platform, "nd"), platform)
    assert any(entry["ip"].lower().startswith("fe80:") for entry in result)


def test_entry_without_usable_mac_survives_collection(rpc_fixture):
    """Rozhodnuti, co je pouzitelny cil pingu, nepatri collectoru.

    Na vMX ma zaznam pro 2001:db8::1 (fxp0.0) mac 'none' jako obycejny text,
    ne prazdnou hodnotu - budouci "uklid" v collectoru by ho nesmel zahodit.
    """
    result = NdCollector().parse(rpc_fixture("junos", "nd"), "junos")
    matching = [entry for entry in result if entry["ip"] == "2001:db8::1"]
    assert matching, "fixture uz neobsahuje zaznam bez pouzitelne MAC"
    assert matching[0]["mac"] == "none"


def test_collector_metadata():
    collector = NdCollector()
    assert collector.name == "nd"
    assert collector.rpc_name("junos") == "get_ipv6_nd_information"
    assert collector.rpc_name("junos-evo") == "get_ipv6_nd_information"
