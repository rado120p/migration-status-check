import textwrap

import pytest

from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.scoping.mapping import (
    Selector,
    empty_mapping,
    load_mapping,
)


def _scope(description, service_type, interface) -> Scope:
    return Scope(
        id=f"svc:{description}:{service_type}",
        kind="service",
        key=ScopeKey(description, service_type, None),
        selectors=Selectors(interfaces=[interface]),
    )


def test_selector_matches_on_description_and_type():
    selector = Selector(description="EVPN-VLAN-AWARE-INTERNET", service_type="Internet")
    assert selector.matches(_scope("EVPN-VLAN-AWARE-INTERNET", "Internet", "ge-0/0/5.0"))
    assert not selector.matches(_scope("EVPN-VLAN-AWARE-INTERNET", "E-LAN", "ae0.14"))


def test_selector_matches_on_interface_alone():
    selector = Selector(interface="ge-0/0/4.0")
    assert selector.matches(_scope(None, "IPVPN", "ge-0/0/4.0"))
    assert not selector.matches(_scope(None, "IPVPN", "ge-0/0/5.0"))


def test_empty_selector_is_rejected():
    with pytest.raises(ValueError, match="prazdny selektor"):
        Selector.from_dict({})


def test_load_mapping(tmp_path):
    path = tmp_path / "mapping.yml"
    path.write_text(
        textwrap.dedent(
            """\
            mappings:
              - baseline: {description: EVPN-VLAN-AWARE-INTERNET, service_type: Internet}
                subject:  {description: EVPN-VLAN-AWARE-INTERNET, service_type: E-LAN}
                note: "sluzba restrukturalizovana"
              - baseline: {interface: "ge-0/0/4.0"}
                subject:  {interface: "et-0/0/10.0"}
            ignore:
              - {interface: "ge-0/0/7.0"}
            """
        ),
        encoding="utf-8",
    )

    mapping = load_mapping(path)

    assert len(mapping.mappings) == 2
    assert mapping.mappings[0].note == "sluzba restrukturalizovana"
    assert mapping.mappings[1].subject.interface == "et-0/0/10.0"
    assert mapping.is_ignored(_scope(None, "Internet", "ge-0/0/7.0")) is True
    assert mapping.is_ignored(_scope(None, "Internet", "ge-0/0/8.0")) is False


def test_empty_mapping_ignores_nothing():
    mapping = empty_mapping()
    assert mapping.mappings == []
    assert mapping.is_ignored(_scope("X", "Internet", "ge-0/0/1.0")) is False
