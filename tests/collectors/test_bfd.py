"""Testy collectoru BFD proti nahranemu XML z laborky.

Fixture pro junos je zamerne PRAZDNY vypis: na vMX je BFD nakonfigurovane
u dvou sousedu, ale BGP je u obou Idle, takze zadna session nevznikla.
Prazdny vypis je platny stav, ne chyba sberu.
"""

from __future__ import annotations

import pytest

from migration_validator.collectors.bfd import BfdCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_mapping_keyed_by_neighbor(rpc_fixture, platform):
    result = BfdCollector().parse(rpc_fixture(platform, "bfd"), platform)
    assert isinstance(result, dict)
    # Jmeno testu slibuje klicovani adresou peeru, tak to i asertujme.
    # Fixture pro junos je zamerne prazdna, tam neni co overit.
    if platform == "junos-evo":
        assert "152.11.13.2" in result


def test_empty_output_is_a_valid_state(rpc_fixture):
    """Nula session neni selhani collectoru - collector nema co interpretovat."""
    result = BfdCollector().parse(rpc_fixture("junos", "bfd"), "junos")

    assert result == {}


def test_up_and_down_sessions_are_recorded_verbatim(rpc_fixture):
    result = BfdCollector().parse(rpc_fixture("junos-evo", "bfd"), "junos-evo")

    assert result["152.11.13.2"]["state"] == "Up"
    assert result["152.11.13.2"]["interface"] == "et-0/0/8.13"
    assert result["198.11.13.2"]["state"] == "Down"
    assert result["198.11.13.2"]["remote_state"] == "AdminDown"


def test_client_names_are_collected(rpc_fixture):
    """Klient rozlisi BGP session od te, kterou drzi jiny protokol."""
    result = BfdCollector().parse(rpc_fixture("junos-evo", "bfd"), "junos-evo")

    assert result["152.11.13.2"]["clients"] == ["BGP"]


def test_entries_have_expected_keys(rpc_fixture):
    result = BfdCollector().parse(rpc_fixture("junos-evo", "bfd"), "junos-evo")

    for data in result.values():
        assert set(data) == {
            "state",
            "interface",
            "remote_state",
            "local_diagnostic",
            "clients",
            "detection_time",
            "transmission_interval",
            "multiplier",
        }


def test_collector_passes_detail_flag():
    """Strucna varianta nema bfd-client ani remote-state."""
    assert BfdCollector().rpc_kwargs("junos") == {"detail": True}
