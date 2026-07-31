"""Testy collectoru BFD proti nahranemu XML z laborky.

Obe nahravky maji session: na 172.20.20.4 (junos) jsou dve na ge-0/0/2,
na 172.20.20.5 (junos-evo) dve na et-0/0/8. Tvar odpovedi se mezi rodinami
nelisi - same elementy, jen jina jmena rozhrani.

Prazdny vypis je platny stav a testuje se na syntetickem XML (EMPTY_OUTPUT),
ne na nahravce, aby o pokryti nerozhodoval stav laborky.
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.collectors.bfd import BfdCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_mapping_keyed_by_neighbor(rpc_fixture, platform):
    result = BfdCollector().parse(rpc_fixture(platform, "bfd"), platform)
    assert isinstance(result, dict)
    # Jmeno testu slibuje klicovani adresou peeru, tak to i asertujme.
    if platform == "junos-evo":
        assert "152.11.13.2" in result


EMPTY_OUTPUT = """
<bfd-session-information style="detail">
  <sessions>0</sessions>
  <clients>0</clients>
  <cumulative-transmission-rate>0.0</cumulative-transmission-rate>
  <cumulative-reception-rate>0.0</cumulative-reception-rate>
</bfd-session-information>
"""


@pytest.mark.parametrize("platform", PLATFORMS)
def test_empty_output_is_a_valid_state(platform):
    """Nula session neni selhani collectoru - collector nema co interpretovat.

    XML je synteticke, ne nahravka. Drive se tenhle stav bral z `junos`
    fixture, ktera zadnou session nemela - jenze to znamenalo, ze stav
    laborky rozhodoval o tom, jestli je tenhle pripad vubec testovany.
    """
    result = BfdCollector().parse(etree.fromstring(EMPTY_OUTPUT.encode()), platform)

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
