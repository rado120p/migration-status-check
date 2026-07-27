import pytest

from migration_validator.collectors.interfaces import InterfacesCollector

PLATFORMS = ("junos", "junos-evo")

COUNTER_KEYS = (
    "input_pps",
    "output_pps",
    "input_errors",
    "output_errors",
    "framing_errors",
)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_parses_something(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    assert result, "parser nevratil zadne rozhrani"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_entry_has_full_schema(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    required = {"admin_status", "oper_status", *COUNTER_KEYS}
    for name, data in result.items():
        assert required <= set(data), f"{name} nema plne schema: {sorted(data)}"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_counters_are_integers(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    for name, data in result.items():
        for key in COUNTER_KEYS:
            assert isinstance(data[key], int), f"{name}.{key} neni int"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_names_are_stripped(rpc_fixture, platform):
    """Junos vraci name obalene bilymi znaky - nesmi prosaknout do klice."""
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    for name in result:
        assert name == name.strip(), f"nazev nese bile znaky: {name!r}"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_includes_logical_units(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    assert any("." in name for name in result), "chybi logicke jednotky"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_includes_physical_interfaces(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    assert any("." not in name for name in result), "chybi fyzicka rozhrani"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_statuses_are_lowercase(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    for name, data in result.items():
        for key in ("admin_status", "oper_status"):
            assert data[key] == data[key].lower(), f"{name}.{key} neni lowercase"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_framing_errors_are_collected(rpc_fixture, platform):
    """framing_errors je v kontraktu fact-schematu a check ifaces ho cte."""
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    physical = [name for name in result if "." not in name]
    assert physical
    assert all("framing_errors" in result[name] for name in physical)


def test_collector_supports_both_platforms():
    collector = InterfacesCollector()
    assert collector.supports("junos")
    assert collector.supports("junos-evo")
    assert collector.name == "interfaces"
