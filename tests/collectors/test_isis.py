"""Testy IS-IS collectoru proti nahranemu XML z laborky.

Adjacency a interface maji na obou platformach stejny tvar - jen jina jmena
rozhrani (ge-0/0/0.0 na junos, et-0/0/0.0 na junos-evo; lo0.0 na obou).
Overview byl drive zajimavy tim, ze se lisi: junos mel isis-overload-enabled
(True), junos-evo overload informaci vubec nenese (False). Nahravka
2026-09-02 (task 5b, idealni pre-migracni stav .4) ukazala, ze overload byl
v labu mezitim vypnuty - junos ted nese stejne "chybi" jako junos-evo, takze
True-vetev uz zadna nahravka nekryje a testuje se na synteticke XML (stejny
duvod jako EMPTY_OUTPUT u BFD: aby o pokryti nerozhodoval stav laborky).
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.collectors.isis import (
    IsisAdjacencyCollector,
    IsisInterfaceCollector,
    IsisOverviewCollector,
    _seconds_attr,
)

PLATFORMS = ("junos", "junos-evo")

# jmeno prvniho rozhrani z adjacency/interface fixture na kazde platforme
FIRST_IFACE = {"junos": "ge-0/0/0.0", "junos-evo": "et-0/0/0.0"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_isis_adjacency_parses_fixture(rpc_fixture, platform):
    data = IsisAdjacencyCollector().parse(rpc_fixture(platform, "isis_adjacency"), platform)
    entry = data[FIRST_IFACE[platform]]
    assert entry["state"] == "Up"
    assert entry["system_name"]
    assert "ip_address" in entry and "ipv6_address" in entry
    assert entry["ip_address"]
    assert entry["ipv6_address"]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_isis_adjacency_has_two_entries(rpc_fixture, platform):
    """Obe nahravky nesou dve sousedstvi (P<->PE mesh v laborce)."""
    data = IsisAdjacencyCollector().parse(rpc_fixture(platform, "isis_adjacency"), platform)
    assert len(data) == 2


@pytest.mark.parametrize("platform", PLATFORMS)
def test_isis_interface_levels(rpc_fixture, platform):
    data = IsisInterfaceCollector().parse(rpc_fixture(platform, "isis_interface"), platform)
    lo0 = data["lo0.0"]
    assert lo0["levels"]["2"]["passive"] is True  # dle lab konfigurace


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("iface", ("lo0.0", None))
def test_isis_interface_disabled_level1_is_not_a_level(rpc_fixture, platform, iface):
    """'level 1 disable' na rozhrani = level 1 v levels neni.

    Detail vypis vypnuty level porad vypise jako <interface-level-data> s
    <level>1</level>, jen s <passive>Disabled</passive> (brief vypis to same
    nese jako <isis-interface-state-one>Disabled</isis-interface-state-one>).
    Nahravka z laborky 2026-09-04 ma 'level 1 disable' na kazdem rozhrani;
    kdyby collector vypnuty level bral jako nakonfigurovany, isis_interface_info
    by na kazdem Core rozhrani hlasil falesny FAIL 'IS-IS level 1 nema na Core
    rozhrani co delat'.
    """
    data = IsisInterfaceCollector().parse(rpc_fixture(platform, "isis_interface"), platform)
    entry = data[iface or FIRST_IFACE[platform]]
    assert "1" not in entry["levels"]
    assert "2" in entry["levels"]


def test_isis_interface_disabled_level2_is_not_a_level():
    """Symetrie: vypnuty level 2 se nesmi tvarit jako nakonfigurovany (synteticke XML)."""
    xml = etree.fromstring(
        "<isis-interface-information><isis-interface>"
        "<interface-name>ge-0/0/0.0</interface-name>"
        "<interface-level-data><level>1</level><passive>Passive</passive></interface-level-data>"
        "<interface-level-data><level>2</level><passive>Disabled</passive></interface-level-data>"
        "</isis-interface></isis-interface-information>"
    )
    data = IsisInterfaceCollector().parse(xml, "junos")
    assert data["ge-0/0/0.0"]["levels"] == {"1": {"passive": True}}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_isis_interface_physical_is_not_passive(rpc_fixture, platform):
    """Fyzicke rozhrani do meshe passive neni - jinak by adjacency nevznikla."""
    data = IsisInterfaceCollector().parse(rpc_fixture(platform, "isis_interface"), platform)
    entry = data[FIRST_IFACE[platform]]
    assert entry["levels"]["2"]["passive"] is False


def test_isis_overview_overload_flag_on_junos():
    """<isis-overload-enabled/> pritomny v odpovedi znamena True.

    Synteticke XML od 2026-09-02 (task 5b): nahravka z .4 uz overload
    vypnuty nema (viz docstring modulu), takze tuhle vetev uz zadna
    nahravka nekryje.
    """
    xml = etree.fromstring(
        b"<isis-overview-information><isis-overview>"
        b"<isis-overload-enabled/>"
        b"</isis-overview></isis-overview-information>"
    )
    data = IsisOverviewCollector().parse(xml, "junos")
    assert data == {"overload_enabled": True}


def test_isis_overview_overload_flag_missing_on_junos_evo(rpc_fixture):
    """junos-evo fixture zadnou isis-overload-information vubec nenese."""
    data = IsisOverviewCollector().parse(rpc_fixture("junos-evo", "isis_overview"), "junos-evo")
    assert data == {"overload_enabled": False}


# --- Synteticke varianty (XML odvozeny z nahranych fixtures) -------------

ADJACENCY_DOWN = """
<isis-adjacency-information style="detail">
  <isis-adjacency>
    <system-name>clab-pop-migration-P1</system-name>
    <interface-name>ge-0/0/0.0</interface-name>
    <level>2</level>
    <adjacency-state>Down</adjacency-state>
    <holdtime>0</holdtime>
    <ip-address>10.1.2.0</ip-address>
    <global-ipv6-address>2001:db8:2::1</global-ipv6-address>
  </isis-adjacency>
</isis-adjacency-information>
"""


@pytest.mark.parametrize("platform", PLATFORMS)
def test_isis_adjacency_down_state_is_recorded_verbatim(platform):
    """Down neni chyba collectoru - to rozhoduje az check."""
    data = IsisAdjacencyCollector().parse(
        etree.fromstring(ADJACENCY_DOWN.encode()), platform
    )
    assert data["ge-0/0/0.0"]["state"] == "Down"


ADJACENCY_NO_IPV6 = """
<isis-adjacency-information style="detail">
  <isis-adjacency>
    <system-name>clab-pop-migration-P1</system-name>
    <interface-name>ge-0/0/0.0</interface-name>
    <level>2</level>
    <adjacency-state>Up</adjacency-state>
    <ip-address>10.1.2.0</ip-address>
  </isis-adjacency>
</isis-adjacency-information>
"""


@pytest.mark.parametrize("platform", PLATFORMS)
def test_isis_adjacency_missing_ipv6_is_none(platform):
    """IPv4-only sousedstvi (adjacency-flag 'Speaks: IP' bez IPv6) - klic
    zustava, hodnota je None, ne chybejici klic."""
    data = IsisAdjacencyCollector().parse(
        etree.fromstring(ADJACENCY_NO_IPV6.encode()), platform
    )
    entry = data["ge-0/0/0.0"]
    assert "ipv6_address" in entry
    assert entry["ipv6_address"] is None


OVERVIEW_NO_OVERLOAD_INFORMATION = """
<isis-overview-information>
  <isis-overview>
    <instance-name>master</instance-name>
    <isis-router-id>150.0.0.11</isis-router-id>
    <isis-routing>
      <isis-routing-ipv4/>
      <isis-routing-ipv6/>
    </isis-routing>
  </isis-overview>
</isis-overview-information>
"""


@pytest.mark.parametrize("platform", PLATFORMS)
def test_isis_overview_without_overload_information_is_false(platform):
    data = IsisOverviewCollector().parse(
        etree.fromstring(OVERVIEW_NO_OVERLOAD_INFORMATION.encode()), platform
    )
    assert data == {"overload_enabled": False}


def test_isis_adjacency_passes_detail_flag():
    assert IsisAdjacencyCollector().rpc_kwargs("junos") == {"detail": True}


def test_isis_interface_passes_detail_flag():
    assert IsisInterfaceCollector().rpc_kwargs("junos") == {"detail": True}


def test_isis_overview_has_no_extra_kwargs():
    assert IsisOverviewCollector().rpc_kwargs("junos") == {}


def test_isis_rpc_names():
    assert IsisAdjacencyCollector().rpc_name("junos") == "get_isis_adjacency_information"
    assert IsisInterfaceCollector().rpc_name("junos") == "get_isis_interface_information"
    assert IsisOverviewCollector().rpc_name("junos") == "get_isis_overview_information"


# --- _seconds_attr: pomocnik, ktery Task 5 sam nepouziva, ale Task 6 ho
# importuje (viz docstring modulu) - musi byt overen tady, jinak se poprve
# zkusi az v Task 6. ------------------------------------------------------


@pytest.mark.parametrize("platform", PLATFORMS)
def test_seconds_attr_reads_unprefixed_attribute_from_fixture(rpc_fixture, platform):
    """Nahravky nemaji zadny prefix - kryje vetev `key == 'seconds'`."""
    root = rpc_fixture(platform, "isis_adjacency")
    node = next(root.iter("{*}last-transition-time"))
    # junos hodnota aktualizovana na nahravku 2026-09-02 (task 5b), junos-evo
    # na nahravku 2026-09-03 (task 5c) - adjacency bezi dal, seconds jen
    # rostou s casem od posledniho capture.
    expected = 27915 if platform == "junos" else 8594
    assert _seconds_attr(node) == expected


def test_seconds_attr_reads_namespaced_attribute():
    """Ziva PyEZ odpoved muze nest junos: prefix - kryje vetev
    `key.endswith('}seconds')`, kterou zadna nahravka nedosahne."""
    xml = (
        '<last-transition-time xmlns:junos="http://xml.juniper.net/junos/x/junos" '
        'junos:seconds="42">00:00:42</last-transition-time>'
    )
    node = etree.fromstring(xml.encode())
    assert _seconds_attr(node) == 42


def test_seconds_attr_missing_attribute_falls_back_to_text():
    """Bez junos:seconds se cte text (MX verze, ktere atribut neposilaji)."""
    node = etree.fromstring(b"<last-transition-time>00:00:42</last-transition-time>")
    assert _seconds_attr(node) == 42


def test_seconds_attr_unparseable_text_is_none():
    node = etree.fromstring(b"<last-transition-time>Never</last-transition-time>")
    assert _seconds_attr(node) is None


def test_seconds_attr_none_node_is_none():
    assert _seconds_attr(None) is None
