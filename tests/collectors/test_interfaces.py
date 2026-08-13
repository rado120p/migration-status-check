import pytest
from lxml import etree

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
    """Countery nese kazdy zaznam z extensive; admin/oper jen fyzicka
    rozhrani - stavy unitu dodava terse RPC (viz testy nize)."""
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    for name, data in result.items():
        assert set(COUNTER_KEYS) <= set(data), f"{name} nema countery: {sorted(data)}"
        if "." not in name:
            assert {"admin_status", "oper_status"} <= set(data), name


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
@pytest.mark.parametrize("fixture", ("interfaces", "interfaces.2"))
def test_statuses_are_lowercase(rpc_fixture, platform, fixture):
    result = InterfacesCollector().parse(rpc_fixture(platform, fixture), platform)
    for name, data in result.items():
        for key in ("admin_status", "oper_status"):
            if key in data:
                assert data[key] == data[key].lower(), f"{name}.{key} neni lowercase"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_framing_errors_are_collected(rpc_fixture, platform):
    """framing_errors je v kontraktu fact-schematu a check ifaces ho cte."""
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    physical = [name for name in result if "." not in name]
    assert physical
    assert all("framing_errors" in result[name] for name in physical)


# Stavy unitu: extensive vypis zadny per-unit admin/oper nenese, skutecne
# hodnoty ma jen terse vypis (fixture interfaces.2.xml, nahrana z labu se
# zamerne disablovanymi unity irb.4094 a ae0.4094). Zadne dedeni z rodice
# ani odvozovani z iff- flagu - stav se cte jen tam, kde ho box vydava.


def test_terse_parse_cte_realne_stavy_unitu(rpc_fixture):
    """Lab dukaz: irb.4094 je admin down / link UP, ae0.4094 down / down.
    Dedeni z rodice ani 'disabled => down' by tenhle rozdil nikdy nedalo."""
    result = InterfacesCollector().parse(
        rpc_fixture("junos-evo", "interfaces.2"), "junos-evo"
    )
    assert result["irb.4094"]["admin_status"] == "down"
    assert result["irb.4094"]["oper_status"] == "up"
    assert result["ae0.4094"]["admin_status"] == "down"
    assert result["ae0.4094"]["oper_status"] == "down"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_terse_parse_emituje_jen_stavy(rpc_fixture, platform):
    """Terse vypis countery nenese - kdyby je parse emitoval (jako nuly),
    slouceni v collect by jimi prepsalo skutecne hodnoty z extensive."""
    result = InterfacesCollector().parse(
        rpc_fixture(platform, "interfaces.2"), platform
    )
    assert result
    for name, data in result.items():
        assert set(data) == {"admin_status", "oper_status"}, name


@pytest.mark.parametrize("platform", PLATFORMS)
def test_extensive_parse_nefabuluje_stavy_unitu(rpc_fixture, platform):
    """Extensive vypis per-unit admin/oper proste nema - parse je nesmi
    vymyslet dedenim z rodice. Nesou je jen fyzicka rozhrani."""
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    # ".local." na vMX je FYZICKE rozhrani s teckami ve jmene - heuristika
    # "obsahuje tecku" na nej nesmi sahnout, stav nese pravem.
    units = [name for name in result if "." in name and not name.startswith(".")]
    assert units
    for name in units:
        assert "admin_status" not in result[name], name
        assert "oper_status" not in result[name], name


class _FakeRpc:
    """Vraci nahranou fixture podle kwargs - stejne RPC, jina varianta."""

    def __init__(self, by_kwargs):
        self._by_kwargs = by_kwargs

    def get_interface_information(self, **kwargs):
        return self._by_kwargs[tuple(sorted(kwargs))]


class _FakeDevice:
    def __init__(self, by_kwargs):
        self.rpc = _FakeRpc(by_kwargs)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_collect_slouci_extensive_a_terse(rpc_fixture, platform):
    """collect vola obe varianty a slucuje: countery z extensive, stavy
    z terse. Kazdy unit z terse ma po slouceni oba stavy."""
    device = _FakeDevice({
        ("extensive",): rpc_fixture(platform, "interfaces"),
        ("terse",): rpc_fixture(platform, "interfaces.2"),
    })
    result = InterfacesCollector().collect(device, platform)

    units = [name for name in result if "." in name]
    assert units
    with_status = [
        name for name in units
        if {"admin_status", "oper_status"} <= set(result[name])
    ]
    assert with_status, "zadny unit nedostal stavy z terse"

    physical = [name for name in result if "." not in name]
    assert all("input_errors" in result[name] for name in physical)


def test_collector_supports_both_platforms():
    collector = InterfacesCollector()
    assert collector.supports("junos")
    assert collector.supports("junos-evo")
    assert collector.name == "interfaces"
