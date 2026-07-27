import pytest

from migration_validator.collectors.arp import ArpCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_list_of_entries(rpc_fixture, platform):
    result = ArpCollector().parse(rpc_fixture(platform, "arp"), platform)
    assert isinstance(result, list)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_entries_have_expected_keys(rpc_fixture, platform):
    result = ArpCollector().parse(rpc_fixture(platform, "arp"), platform)
    for entry in result:
        assert set(entry) == {"ip", "mac", "interface", "routing_instance"}
        assert entry["ip"]
        assert entry["interface"]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_values_are_stripped(rpc_fixture, platform):
    """Junos obaluje hodnoty bilymi znaky - ping by pak dostal nevalidni cil."""
    result = ArpCollector().parse(rpc_fixture(platform, "arp"), platform)
    assert result, "fixture nema zadny ARP zaznam"
    for entry in result:
        for key in ("ip", "mac", "interface"):
            value = entry[key]
            if value is not None:
                assert value == value.strip(), f"{key} nese bile znaky: {value!r}"


def test_collector_metadata():
    assert ArpCollector().name == "arp"
