import pytest

from migration_validator.collectors.arp import ArpCollector, split_learned_via

PLATFORMS = ("junos", "junos-evo")


def test_split_learned_via_plain_name():
    assert split_learned_via("ge-0/0/4.0") == ("ge-0/0/4.0", None)


def test_split_learned_via_irb_bracket():
    assert split_learned_via("irb.14[ ae0.14 ]") == ("irb.14", "ae0.14")


def test_split_learned_via_strips_whitespace():
    assert split_learned_via("irb.14 [ae0.14]") == ("irb.14", "ae0.14")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_list_of_entries(rpc_fixture, platform):
    result = ArpCollector().parse(rpc_fixture(platform, "arp"), platform)
    assert isinstance(result, list)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_entries_have_expected_keys(rpc_fixture, platform):
    result = ArpCollector().parse(rpc_fixture(platform, "arp"), platform)
    for entry in result:
        assert set(entry) == {"ip", "mac", "interface", "learned_via", "routing_instance"}
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


@pytest.mark.parametrize("platform", ("junos-evo",))
def test_irb_entry_is_normalised(rpc_fixture, platform):
    result = ArpCollector().parse(rpc_fixture(platform, "arp"), platform)
    irb = [entry for entry in result if entry["interface"] == "irb.14"]
    assert irb, "fixture nema ARP zaznam na irb.14"
    assert irb[0]["learned_via"] == "ae0.14"
    assert all("[" not in entry["interface"] for entry in result)
