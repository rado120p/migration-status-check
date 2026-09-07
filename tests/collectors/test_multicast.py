"""Testy multicast collectoru proti nahranemu XML z laborky (spec 2026-09-02).

igmp_group: pseudo-rozhrani `local` neni sluzba a zahazuje se pri parsovani;
zdroj 0.0.0.0 (ASM/Exclude) se uklada jako None, aby check mohl vypsat (*, G).
multicast_route: klic instance (master | RI), uvnitr "S,G"; jen INET rodina.
MX nezna `instance all` (zmereno 2026-09-02, Task 1) - master jde bez
argumentu, RI se dotazuji per-VRF pres record_calls/collect.
mvpn_instance: c-multicast per instance + sender_pe z provider-tunnel-id.
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.collectors.base import CollectorError
from migration_validator.collectors.multicast import (
    IgmpGroupCollector,
    MulticastRouteCollector,
    MvpnInstanceCollector,
    PimJoinCollector,
    _vrf_instances,
    parse_sender_pe,
    route_key,
)

PLATFORMS = ("junos", "junos-evo")

# Rozhrani receiveru MULTICAST-STREAM-A (Internet/multicast) na obou boxech.
IGMP_IFACE = {"junos": "ge-0/0/2.11", "junos-evo": "et-0/0/8.11"}
MVPN_RI = "MULTICAST-STREAM-B-MUX1-RECEIVER"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_igmp_group_parses_fixture(rpc_fixture, platform):
    data = IgmpGroupCollector().parse(rpc_fixture(platform, "igmp_group"), platform)
    entries = data[IGMP_IFACE[platform]]
    assert {"source": "10.11.11.1", "group": "232.1.1.1"} in entries


@pytest.mark.parametrize("platform", PLATFORMS)
def test_igmp_group_drops_local_pseudo_interface(rpc_fixture, platform):
    """Mutant kill (2026-09-03, overeno spustenim, obe platformy): odstraneni
    'interface == "local"' filtru necha 'local' pseudo-rozhrani projit."""
    data = IgmpGroupCollector().parse(rpc_fixture(platform, "igmp_group"), platform)
    assert "local" not in data


def test_multicast_route_parses_master_on_junos(rpc_fixture):
    # MX: master bez argumentu instance - viz test_rpc_names_and_kwargs.
    data = MulticastRouteCollector().parse(rpc_fixture("junos", "multicast_route"), "junos")
    master = data["master"][route_key("10.11.11.1", "232.1.1.1")]
    assert master["upstream_interface"]
    assert IGMP_IFACE["junos"] in master["downstream_interfaces"]
    assert isinstance(master["forwarding_rate_pps"], int)
    assert isinstance(master["uptime_seconds"], int)


def test_multicast_route_parses_ri_on_junos(rpc_fixture):
    # MX: RI se nahravala zvlast (Task 1, record_calls per-VRF), takze RI je
    # v jednom z pripojenych multicast_route.<N>.xml souboru - poradi dane
    # poradim VRF ze zarizeni (get-instance-information), ne obsahem. Task 1
    # mel na .4 jen jednu VRF (multicast_route.2.xml). Nahravka 2026-09-02
    # (task 5b, idealni pre-migracni stav) uz ma na .4 ctyri VRF - tri bez
    # bezicich multicast routes ("instance is not running", .2-.4) a
    # MULTICAST-STREAM-B-MUX1-RECEIVER jako ctvrtou (multicast_route.5.xml).
    data = MulticastRouteCollector().parse(rpc_fixture("junos", "multicast_route.5"), "junos")
    ri = data[MVPN_RI][route_key("10.12.12.1", "239.1.1.1")]
    assert ri["upstream_interface"].startswith(("lsi.", "vt-"))


def test_multicast_route_parses_master_and_ri_on_evo(rpc_fixture):
    # EVO: `instance all` funguje, obe instance jsou v jedne odpovedi.
    data = MulticastRouteCollector().parse(rpc_fixture("junos-evo", "multicast_route"), "junos-evo")
    master = data["master"][route_key("10.11.11.1", "232.1.1.1")]
    assert master["upstream_interface"]
    assert IGMP_IFACE["junos-evo"] in master["downstream_interfaces"]
    # R2: nahravka 2026-09-03 (task 5c, post-migration z .5) uz nese
    # <forwarding-rate-packets> misto <multicast-statistics-timed-out/> -
    # stream bezi dost dlouho, ze box stihl statistiky spocitat. Vetev
    # "chybejici element je None" ma vlastni pokryti na syntetickem XML
    # (test_route_without_rate_element_is_none_not_zero nize), takze tady
    # se overuje jen aktualni realny obsah.
    assert master["forwarding_rate_pps"] == 6
    assert isinstance(master["uptime_seconds"], int)
    ri = data[MVPN_RI][route_key("10.12.12.1", "239.1.1.1")]
    assert ri["upstream_interface"].startswith(("lsi.", "vt-"))


@pytest.mark.parametrize("platform", PLATFORMS)
def test_mvpn_instance_parses_fixture(rpc_fixture, platform):
    data = MvpnInstanceCollector().parse(rpc_fixture(platform, "mvpn_instance"), platform)
    entries = data[MVPN_RI]["c_multicast"]
    assert entries[0]["source_prefix"] == "10.12.12.1/32"
    assert entries[0]["group_prefix"] == "239.1.1.1/32"
    assert entries[0]["provider_tunnel_id"].startswith("RSVP-TE P2MP:")
    assert entries[0]["sender_pe"] == "150.0.0.13"


def test_rpc_names_and_kwargs():
    assert IgmpGroupCollector().rpc_name("junos") == "get_igmp_group_information"
    assert IgmpGroupCollector().rpc_kwargs("junos") == {}
    assert MulticastRouteCollector().rpc_name("junos") == "get_multicast_route_information"
    # R1: MX nezna `instance all` (zmereno 2026-09-02) - master bez argumentu.
    assert MulticastRouteCollector().rpc_kwargs("junos") == {"extensive": True}
    assert MulticastRouteCollector().rpc_kwargs("junos-evo") == {
        "extensive": True,
        "instance": "all",
    }
    assert MvpnInstanceCollector().rpc_name("junos") == "get_mvpn_instance_information"
    assert MvpnInstanceCollector().rpc_kwargs("junos") == {"inet": True}


# --- Synteticke varianty (XML odvozeny z nahranych fixtures) -------------

EMPTY_IGMP = "<igmp-group-information/>"
EMPTY_MROUTE = "<multicast-route-information/>"
EMPTY_MVPN = "<mvpn-instance-information/>"


def test_empty_outputs_are_empty_dicts():
    assert IgmpGroupCollector().parse(etree.fromstring(EMPTY_IGMP), "junos") == {}
    assert MulticastRouteCollector().parse(etree.fromstring(EMPTY_MROUTE), "junos") == {}
    assert MvpnInstanceCollector().parse(etree.fromstring(EMPTY_MVPN), "junos") == {}


ASM_GROUP = """
<igmp-group-information>
  <mgm-interface-groups>
    <interface-name>irb.2</interface-name>
    <mgm-group-count>1</mgm-group-count>
    <mgm-group>
      <multicast-group-address>239.5.5.5</multicast-group-address>
      <mgm-group-mode-type>Exclude</mgm-group-mode-type>
      <multicast-source-address>0.0.0.0</multicast-source-address>
    </mgm-group>
  </mgm-interface-groups>
  <mgm-interface-groups>
    <interface-name>irb.3</interface-name>
    <mgm-group-count>0</mgm-group-count>
  </mgm-interface-groups>
</igmp-group-information>
"""


def test_asm_source_is_none_and_interface_without_groups_has_no_key():
    data = IgmpGroupCollector().parse(etree.fromstring(ASM_GROUP), "junos")
    assert data == {"irb.2": [{"source": None, "group": "239.5.5.5"}]}


INET6_ONLY = """
<multicast-route-information>
  <route-family>
    <multicast-instance>master</multicast-instance>
    <address-family>INET6</address-family>
    <multicast-route>
      <multicast-group-address>ff3e::1</multicast-group-address>
      <multicast-source-address>2001:db8::1</multicast-source-address>
      <upstream-interface-name>et-0/0/0.0</upstream-interface-name>
    </multicast-route>
  </route-family>
  <route-family>
    <multicast-instance>master</multicast-instance>
    <address-family>INET</address-family>
  </route-family>
</multicast-route-information>
"""


def test_inet6_family_is_ignored_and_empty_instance_has_no_key():
    assert MulticastRouteCollector().parse(etree.fromstring(INET6_ONLY), "junos") == {}


ROUTE_WITHOUT_DOWNSTREAM = """
<multicast-route-information>
  <route-family>
    <multicast-instance>master</multicast-instance>
    <address-family>INET</address-family>
    <multicast-route>
      <multicast-group-address>232.1.1.1</multicast-group-address>
      <multicast-source-address>10.11.11.1</multicast-source-address>
      <upstream-interface-name>et-0/0/0.0</upstream-interface-name>
      <outgoing-interface-count>0</outgoing-interface-count>
      <forwarding-rate-packets>0</forwarding-rate-packets>
      <multicast-route-state>Active</multicast-route-state>
      <multicast-route-forwarding-state>Pruned</multicast-route-forwarding-state>
      <multicast-route-uptime seconds="12">00:00:12</multicast-route-uptime>
    </multicast-route>
  </route-family>
</multicast-route-information>
"""


def test_route_without_downstream_has_empty_list_and_present_zero_rate():
    # R2: <forwarding-rate-packets>0</forwarding-rate-packets> je PRITOMNY
    # element - 0 zustava 0, ne None (None patri jen chybejicimu elementu).
    data = MulticastRouteCollector().parse(etree.fromstring(ROUTE_WITHOUT_DOWNSTREAM), "junos")
    route = data["master"]["10.11.11.1,232.1.1.1"]
    assert route["downstream_interfaces"] == []
    assert route["forwarding_rate_pps"] == 0
    assert route["uptime_seconds"] == 12
    assert route["forwarding_state"] == "Pruned"


ROUTE_WITHOUT_RATE_ELEMENT = """
<multicast-route-information>
  <route-family>
    <multicast-instance>master</multicast-instance>
    <address-family>INET</address-family>
    <multicast-route>
      <multicast-group-address>232.1.1.1</multicast-group-address>
      <multicast-source-address>10.11.11.1</multicast-source-address>
      <upstream-interface-name>et-0/0/0.0</upstream-interface-name>
      <multicast-statistics-timed-out/>
      <multicast-route-state>Active</multicast-route-state>
      <multicast-route-forwarding-state>Forwarding</multicast-route-forwarding-state>
    </multicast-route>
  </route-family>
</multicast-route-information>
"""


def test_route_without_rate_element_is_none_not_zero():
    # R2: absence elementu (EVO, statistiky timeout) != namerena nula.
    data = MulticastRouteCollector().parse(etree.fromstring(ROUTE_WITHOUT_RATE_ELEMENT), "junos")
    route = data["master"]["10.11.11.1,232.1.1.1"]
    assert route["forwarding_rate_pps"] is None


MVPN_WITHOUT_CMULTICAST = """
<mvpn-instance-information>
  <mvpn-instance>
    <instance-family>
      <address-family>INET</address-family>
      <instance-entry>
        <instance-name>EMPTY-RI</instance-name>
        <provider-tunnel>
          <provider-tunnel-id>I-P-tnl:invalid</provider-tunnel-id>
        </provider-tunnel>
      </instance-entry>
    </instance-family>
  </mvpn-instance>
</mvpn-instance-information>
"""


def test_instance_without_cmulticast_keeps_key_with_empty_list():
    """Instance ve vypisu bez c-multicast != instance mimo vypis - check
    je hlasi jinou vetou, takze klic musi zustat."""
    data = MvpnInstanceCollector().parse(etree.fromstring(MVPN_WITHOUT_CMULTICAST), "junos")
    assert data == {"EMPTY-RI": {"c_multicast": []}}


@pytest.mark.parametrize(
    ("tunnel", "expected"),
    [
        ("RSVP-TE P2MP:150.0.0.13, 24209,150.0.0.13", "150.0.0.13"),
        ("I-P-tnl:invalid", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_sender_pe(tunnel, expected):
    assert parse_sender_pe(tunnel) == expected


# --- R1: MX nezna instance=all, VRF se enumeruji ze zarizeni --------------

INSTANCE_INFO_BRIEF = """
<instance-information>
  <instance-core>
    <instance-name>MULTICAST-STREAM-B-MUX1-RECEIVER</instance-name>
    <instance-type>vrf</instance-type>
  </instance-core>
  <instance-core>
    <instance-name>__master.anon__</instance-name>
    <instance-type>forwarding</instance-type>
  </instance-core>
</instance-information>
"""


class _FakeRpc:
    def __init__(self, replies):
        self._replies = replies

    def get_instance_information(self, **kwargs):
        return etree.fromstring(INSTANCE_INFO_BRIEF)

    def get_multicast_route_information(self, **kwargs):
        key = kwargs.get("instance")
        if key not in self._replies:
            raise RuntimeError(f"neocekavane volani instance={key!r}")
        return etree.fromstring(self._replies[key])


class _FakeDevice:
    def __init__(self, replies):
        self.rpc = _FakeRpc(replies)


def test_vrf_instances_filters_only_vrf_type():
    device = _FakeDevice({})
    assert _vrf_instances(device) == ["MULTICAST-STREAM-B-MUX1-RECEIVER"]


_MASTER_XML = """
<multicast-route-information>
  <route-family>
    <multicast-instance>master</multicast-instance>
    <address-family>INET</address-family>
    <multicast-route>
      <multicast-group-address>232.1.1.1</multicast-group-address>
      <multicast-source-address>10.11.11.1</multicast-source-address>
      <upstream-interface-name>et-0/0/0.0</upstream-interface-name>
    </multicast-route>
  </route-family>
</multicast-route-information>
"""

_RI_XML = """
<multicast-route-information>
  <route-family>
    <multicast-instance>MULTICAST-STREAM-B-MUX1-RECEIVER</multicast-instance>
    <address-family>INET</address-family>
    <multicast-route>
      <multicast-group-address>239.1.1.1</multicast-group-address>
      <multicast-source-address>10.12.12.1</multicast-source-address>
      <upstream-interface-name>lsi.3</upstream-interface-name>
    </multicast-route>
  </route-family>
</multicast-route-information>
"""


def test_collect_merges_master_and_per_vrf_on_junos():
    device = _FakeDevice({None: _MASTER_XML, "MULTICAST-STREAM-B-MUX1-RECEIVER": _RI_XML})
    data = MulticastRouteCollector().collect(device, "junos")
    assert route_key("10.11.11.1", "232.1.1.1") in data["master"]
    assert (
        route_key("10.12.12.1", "239.1.1.1")
        in data["MULTICAST-STREAM-B-MUX1-RECEIVER"]
    )


def test_collect_raises_when_a_per_vrf_call_fails_on_junos():
    # Jen master ma odpoved - RI volani spadne, castecna data nesmi vypadat
    # jako zmerena.
    device = _FakeDevice({None: _MASTER_XML})
    with pytest.raises(CollectorError):
        MulticastRouteCollector().collect(device, "junos")


def test_record_calls_on_junos_includes_master_and_per_vrf():
    device = _FakeDevice({})
    calls = MulticastRouteCollector().record_calls(device, "junos")
    names = [kwargs.get("instance") for _, kwargs in calls]
    assert None in names
    assert "MULTICAST-STREAM-B-MUX1-RECEIVER" in names
    assert ("get_multicast_route_information", {"extensive": True, "instance": "MULTICAST-STREAM-B-MUX1-RECEIVER"}) in calls


def test_collect_raises_when_instance_lookup_fails_on_junos():
    """_vrf_instances (get-instance-information) sedi mimo per-RPC try/except
    smycku v collect() - bez vlastniho osetreni by AttributeError/RuntimeError
    proplulo mimo CollectorError a capture_device (ktery chyta jen
    CollectorError) by spadl misto aby oblast oznacil za selhanou."""

    class _BoomRpc:
        def get_instance_information(self, **kwargs):
            raise RuntimeError("RPC get_instance_information selhalo")

    class _BoomDevice:
        rpc = _BoomRpc()

    with pytest.raises(CollectorError):
        MulticastRouteCollector().collect(_BoomDevice(), "junos")


def test_record_calls_on_evo_is_just_the_static_call():
    device = _FakeDevice({})
    calls = MulticastRouteCollector().record_calls(device, "junos-evo")
    assert calls == (("get_multicast_route_information", {"extensive": True, "instance": "all"}),)


# --- pim_join (spec 2026-09-07) --------------------------------------------

PIM_RI_RECEIVER = "NGMVPN-PIM-RECEIVER"
PIM_RI_SENDER = "NGMVPN-PIM-SOURCE"
PIM_SG = route_key("10.10.10.1", "232.10.10.1")


def test_pim_join_parses_receiver_on_junos(rpc_fixture):
    data = PimJoinCollector().parse(rpc_fixture("junos", "pim_join"), "junos")
    join = data[PIM_RI_RECEIVER][PIM_SG]
    assert join["source"] == "10.10.10.1"
    assert join["group"] == "232.10.10.1"
    assert join["upstream_interface"] == "Through BGP"
    assert join["upstream_neighbor"] == "Through MVPN"
    assert join["downstream_interfaces"] == ["irb.10"]
    assert isinstance(join["uptime_seconds"], int)


def test_pim_join_parses_sender_on_junos(rpc_fixture):
    data = PimJoinCollector().parse(rpc_fixture("junos", "pim_join.2"), "junos")
    join = data[PIM_RI_SENDER][PIM_SG]
    assert join["upstream_interface"] == "irb.10"
    assert join["upstream_neighbor"] == "10.10.13.1"
    assert join["downstream_interfaces"] == ["Pseudo-MVPN"]


def test_pim_join_parses_all_instances_on_evo(rpc_fixture):
    data = PimJoinCollector().parse(rpc_fixture("junos-evo", "pim_join"), "junos-evo")
    assert set(data) == {"master", "NGMVPN-IGMP-RECEIVER", PIM_RI_RECEIVER}
    # Internet/multicast join: downstream Pseudo-GMP + skutecne jmeno v
    # pim-pseudo-downstream-interface-name - obe se sbiraji.
    master = data["master"][route_key("10.11.11.1", "232.1.1.1")]
    assert master["upstream_interface"] == "et-0/0/0.0"
    assert master["downstream_interfaces"] == ["Pseudo-GMP", "et-0/0/8.11"]
    assert data["NGMVPN-IGMP-RECEIVER"][route_key("10.12.12.1", "239.1.1.1")]["downstream_interfaces"] == ["irb.2"]


def test_pim_join_inet6_family_and_empty_instance_have_no_key(rpc_fixture):
    data = PimJoinCollector().parse(rpc_fixture("junos", "pim_join.2"), "junos")
    assert set(data) == {PIM_RI_SENDER}


_PIM_ASM_XML = """
<pim-join-information>
  <join-family>
    <pim-instance>PIM.master</pim-instance>
    <address-family>INET</address-family>
    <join-group>
      <multicast-group-address>239.5.5.5</multicast-group-address>
      <upstream-interface-name>et-0/0/0.0</upstream-interface-name>
      <upstream-neighbor>10.1.1.2</upstream-neighbor>
      <uptime seconds="7">00:00:07</uptime>
      <downstream-interfaces>
        <downstream-interface>
          <pim-interface-name>irb.7</pim-interface-name>
        </downstream-interface>
        <downstream-interface>
          <pim-interface-name>irb.7</pim-interface-name>
        </downstream-interface>
      </downstream-interfaces>
    </join-group>
  </join-family>
</pim-join-information>
"""


def test_pim_join_asm_has_none_source_and_star_key_and_unique_downstream():
    data = PimJoinCollector().parse(etree.fromstring(_PIM_ASM_XML), "junos")
    join = data["master"]["*,239.5.5.5"]
    assert join["source"] is None
    assert join["group"] == "239.5.5.5"
    assert join["downstream_interfaces"] == ["irb.7"]
    assert join["uptime_seconds"] == 7


def test_pim_join_rpc_names_and_kwargs():
    collector = PimJoinCollector()
    assert collector.rpc_name("junos") == "get_pim_join_information"
    assert collector.rpc_kwargs("junos") == {"extensive": True}
    assert collector.rpc_kwargs("junos-evo") == {"extensive": True, "instance": "all"}


def test_pim_join_record_calls_on_junos_includes_master_and_per_vrf():
    calls = PimJoinCollector().record_calls(_FakeDevice({}), "junos")
    assert ("get_pim_join_information", {"extensive": True}) in calls
    assert ("get_pim_join_information", {"extensive": True, "instance": "MULTICAST-STREAM-B-MUX1-RECEIVER"}) in calls
