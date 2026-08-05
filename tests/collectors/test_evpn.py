import pytest
from lxml import etree

from migration_validator.collectors.evpn import (
    EvpnEsiCollector,
    EvpnInstanceCollector,
    EvpnMacCollector,
    EvpnVpwsCollector,
)

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_vpws_instances_carry_interface_list(rpc_fixture, platform):
    result = EvpnVpwsCollector().parse(rpc_fixture(platform, "evpn_vpws"), platform)
    assert result, "fixture nema zadnou vpws instanci"
    for data in result.values():
        assert set(data) == {"interfaces"}
        for iface in data["interfaces"]:
            assert set(iface) == {"name", "status", "mode", "local_sid", "remote_sid"}
            for sid in (iface["local_sid"], iface["remote_sid"]):
                assert set(sid) == {"value", "peers"}
                for peer in sid["peers"]:
                    assert set(peer) == {"esi", "ipaddr", "mode", "role", "status"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_vpws_remote_peer_resolved_from_fixture(rpc_fixture, platform):
    result = EvpnVpwsCollector().parse(rpc_fixture(platform, "evpn_vpws"), platform)
    iface = next(iter(result.values()))["interfaces"][0]
    assert iface["remote_sid"]["value"] is not None
    assert iface["remote_sid"]["peers"], "fixture nese sid-pe-info, parser ho nevraci"
    assert iface["remote_sid"]["peers"][0]["status"] == "Resolved"


def test_vpws_empty_pe_table_gives_empty_peers():
    xml = etree.fromstring(
        """<evpn-vpws-information><evpn-vpws-instance>
        <evpn-vpws-instance-name>X</evpn-vpws-instance-name>
        <evpn-vpws-interface-status-table><evpn-vpws-interface>
          <evpn-vpws-interface-name>ge-0/0/2.213</evpn-vpws-interface-name>
          <evpn-vpws-interface-mode>single-homed</evpn-vpws-interface-mode>
          <evpn-vpws-interface-status>Up</evpn-vpws-interface-status>
          <evpn-vpws-service-id-local-status-table><evpn-vpws-sid-local>
            <evpn-vpws-sid-local-value>1000</evpn-vpws-sid-local-value>
            <evpn-vpws-sid-pe-status-table/>
          </evpn-vpws-sid-local></evpn-vpws-service-id-local-status-table>
          <evpn-vpws-service-id-remote-status-table><evpn-vpws-sid-remote>
            <evpn-vpws-sid-remote-value>2000</evpn-vpws-sid-remote-value>
            <evpn-vpws-sid-pe-status-table/>
          </evpn-vpws-sid-remote></evpn-vpws-service-id-remote-status-table>
        </evpn-vpws-interface></evpn-vpws-interface-status-table>
        </evpn-vpws-instance></evpn-vpws-information>"""
    )
    result = EvpnVpwsCollector().parse(xml, "junos")
    iface = result["X"]["interfaces"][0]
    assert iface["local_sid"] == {"value": 1000, "peers": []}
    assert iface["remote_sid"] == {"value": 2000, "peers": []}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_esi_schema(rpc_fixture, platform):
    result = EvpnEsiCollector().parse(rpc_fixture(platform, "evpn_esi"), platform)
    assert isinstance(result, dict)
    for esi, data in result.items():
        assert set(data) == {"status", "df_role", "interface"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_mac_count_schema(rpc_fixture, platform):
    result = EvpnMacCollector().parse(rpc_fixture(platform, "evpn_mac"), platform)
    assert result, "fixture nema zadnou instanci"
    for data in result.values():
        assert set(data) == {"vlans", "interfaces"}
        for vlan, entry in data["vlans"].items():
            assert vlan.isdigit()
            assert set(entry) == {"count", "domain"}
        for key, entry in data["interfaces"].items():
            assert ":" not in key
            assert set(entry) == {"count", "name", "domain"}


def test_mac_count_vlan_key_is_learn_vlan_even_for_vlan_based(rpc_fixture):
    # Placeholder nazev domeny (__X__/VL-NONE) drive znamenal klic '-'.
    # Count vypis ale nese skutecne learn-vlan na obou platformach,
    # takze vlan-based instance se pre/post paruje pres VLAN id.
    result = EvpnMacCollector().parse(rpc_fixture("junos", "evpn_mac.2"), "junos")
    based = result["EVPN-VLAN-BASED-CPE13-NNI"]
    assert based["vlans"] == {"413": {"count": 2, "domain": None}}


def test_mac_count_interface_key_strips_vlan_suffix(rpc_fixture):
    result = EvpnMacCollector().parse(rpc_fixture("junos", "evpn_mac"), "junos")
    aware = result["EVPN-VLAN-AWARE-CPE13-NNI"]
    assert aware["interfaces"] == {
        "ge-0/0/2.313": {"count": 1, "name": "ge-0/0/2.313:313", "domain": "BD-313"}
    }


def test_mac_count_skips_system_instance_and_empty_entries(rpc_fixture):
    result = EvpnMacCollector().parse(rpc_fixture("junos-evo", "evpn_mac"), "junos-evo")
    assert "default-switch" not in result
    # prazdne <...-if-mac-count-entry/> bloky nesmi vyrobit zaznam
    aware = result["EVPN-VLAN-AWARE-CPE13-NNI"]
    assert set(aware["interfaces"]) == {"et-0/0/8.313"}


def test_mac_count_merges_domains_of_one_instance(rpc_fixture):
    result = EvpnMacCollector().parse(rpc_fixture("junos-evo", "evpn_mac"), "junos-evo")
    pop1 = result["EVPN-VLAN-AWARE-POP1"]
    assert set(pop1["vlans"]) == {"14", "15", "4094"}
    assert set(pop1["interfaces"]) == {"ae0.14", "ae0.15", "ae0.4094"}


def test_mac_count_uses_count_kwarg():
    # Bez count=True by RPC stahlo celou tabulku - a parser count tvaru
    # by z ni nic neprecetl.
    assert EvpnMacCollector().rpc_kwargs("junos") == {"count": True}
    assert EvpnMacCollector().rpc_kwargs("junos-evo") == {"count": True}


def test_esi_requires_extensive():
    """Bez 'extensive' Junos zadny ESI blok nevrati a collector by tise mlcel."""
    assert EvpnEsiCollector().rpc_kwargs("junos") == {"extensive": True}


def test_esi_emits_logical_unit_as_interface():
    """Scope drzi logickou jednotku, takze ESI musi hlasit 'ae0.14', ne 'ae0'."""
    xml = etree.fromstring(
        """
        <evpn-instance-information>
          <evpn-instance>
            <evpn-esi>
              <evpn-esi-value>00:11:12:13:14:00:00:00:00:00</evpn-esi-value>
              <evpn-esi-status>Resolved by IFL ae0.14</evpn-esi-status>
              <evpn-esi-local-intf-information>
                <evpn-esi-local-intf-name>ae0.14</evpn-esi-local-intf-name>
                <evpn-esi-local-intf-status>Up/Forwarding</evpn-esi-local-intf-status>
              </evpn-esi-local-intf-information>
              <evpn-esi-df-information>
                <esi-designated-forwarder>150.0.0.2</esi-designated-forwarder>
              </evpn-esi-df-information>
            </evpn-esi>
          </evpn-instance>
        </evpn-instance-information>
        """
    )
    result = EvpnEsiCollector().parse(xml, "junos-evo")

    assert result == {
        "00:11:12:13:14:00:00:00:00:00": {
            "status": "Up/Forwarding",
            "df_role": "150.0.0.2",
            "interface": "ae0.14",
        }
    }


def test_mac_merges_every_rpc_for_platform():
    """MX vidi vlan-aware jen pres bridge mac-table, vlan-based jen pres evpn."""

    class FakeRpc:
        def get_bridge_mac_table(self, **kwargs):
            return etree.fromstring(
                """
                <l2ald-rtb-mac-count><l2ald-rtb-mac-count-entry>
                  <rtb-name>AWARE</rtb-name>
                  <bd-name>BD-313</bd-name>
                  <l2ald-rtb-if-mac-count>
                    <l2ald-rtb-if-mac-count-entry>
                      <interface-name>ge-0/0/2.313:313</interface-name>
                      <mac-count>1</mac-count>
                    </l2ald-rtb-if-mac-count-entry>
                  </l2ald-rtb-if-mac-count>
                  <l2ald-rtb-learn-vlan-mac-count>
                    <l2ald-rtb-learn-vlan-mac-count-entry>
                      <learn-vlan>313</learn-vlan>
                      <mac-count>1</mac-count>
                    </l2ald-rtb-learn-vlan-mac-count-entry>
                  </l2ald-rtb-learn-vlan-mac-count>
                </l2ald-rtb-mac-count-entry></l2ald-rtb-mac-count>
                """
            )

        def get_evpn_mac_table(self, **kwargs):
            return etree.fromstring(
                """
                <l2ald-rtb-mac-count><l2ald-rtb-mac-count-entry>
                  <rtb-name>BASED</rtb-name>
                  <bd-name>__BASED__</bd-name>
                  <l2ald-rtb-if-mac-count>
                    <l2ald-rtb-if-mac-count-entry>
                      <interface-name>ge-0/0/3.413:413</interface-name>
                      <mac-count>1</mac-count>
                    </l2ald-rtb-if-mac-count-entry>
                  </l2ald-rtb-if-mac-count>
                  <l2ald-rtb-learn-vlan-mac-count>
                    <l2ald-rtb-learn-vlan-mac-count-entry>
                      <learn-vlan>413</learn-vlan>
                      <mac-count>1</mac-count>
                    </l2ald-rtb-learn-vlan-mac-count-entry>
                  </l2ald-rtb-learn-vlan-mac-count>
                </l2ald-rtb-mac-count-entry></l2ald-rtb-mac-count>
                """
            )

    class FakeDevice:
        rpc = FakeRpc()

    result = EvpnMacCollector().collect(FakeDevice(), "junos")
    assert result == {
        "AWARE": {
            "vlans": {"313": {"count": 1, "domain": "BD-313"}},
            "interfaces": {
                "ge-0/0/2.313": {
                    "count": 1,
                    "name": "ge-0/0/2.313:313",
                    "domain": "BD-313",
                }
            },
        },
        "BASED": {
            "vlans": {"413": {"count": 1, "domain": None}},
            "interfaces": {
                "ge-0/0/3.413": {
                    "count": 1,
                    "name": "ge-0/0/3.413:413",
                    "domain": None,
                }
            },
        },
    }


def test_mac_partial_rpc_failure_is_an_error():
    """Castecna data by check porovnal proti plnemu baseline jako propad."""
    from migration_validator.collectors.base import CollectorError

    class FakeRpc:
        def get_bridge_mac_table(self, **kwargs):
            return etree.fromstring("<l2ald-rtb-mac-count/>")

        def get_evpn_mac_table(self, **kwargs):
            raise RuntimeError("l2-learning neni dostupne")

    class FakeDevice:
        rpc = FakeRpc()

    with pytest.raises(CollectorError, match="get_evpn_mac_table"):
        EvpnMacCollector().collect(FakeDevice(), "junos")


def test_collector_names():
    assert EvpnVpwsCollector().name == "evpn_vpws"
    assert EvpnEsiCollector().name == "evpn_esi"
    assert EvpnMacCollector().name == "evpn_mac"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_auto_generated_esi_are_ignored(rpc_fixture, platform):
    result = EvpnEsiCollector().parse(rpc_fixture(platform, "evpn_esi"), platform)
    # ESI zacinajici 05: si box sam generuje (per-IRB) a nemaji status.
    # Nesmi se objevit v reportu.
    assert all(not esi.startswith("05:") for esi in result)


def test_auto_generated_esi_does_not_hide_real_esi(rpc_fixture):
    # Junos fixture obsahuje pouze auto-generovany 05: ESI (po filtru prazdno).
    # Junos-EVO obsahuje realne ESI (00:11:...) i auto-generovane (05:...).
    # Test musi overit, ze skrz filtr stale prochazi skutecne ESI.
    result = EvpnEsiCollector().parse(rpc_fixture("junos-evo", "evpn_esi"), "junos-evo")
    assert "00:11:12:13:14:00:00:00:00:00" in result


@pytest.mark.parametrize("platform", PLATFORMS)
def test_instance_schema(rpc_fixture, platform):
    result = EvpnInstanceCollector().parse(
        rpc_fixture(platform, "evpn_instance"), platform
    )
    assert result, "fixture nema zadnou instanci"
    for data in result.values():
        assert set(data) == {"local_interfaces", "irb_interfaces", "neighbors", "esis"}
        for area in ("local_interfaces", "irb_interfaces"):
            assert set(data[area]) == {"total", "up", "entries"}
        assert set(data["neighbors"]) == {"total", "addresses"}


def test_instance_skips_default_evpn(rpc_fixture):
    result = EvpnInstanceCollector().parse(
        rpc_fixture("junos-evo", "evpn_instance"), "junos-evo"
    )
    assert "__default_evpn__" not in result


def test_instance_irb_carries_l3_context(rpc_fixture):
    result = EvpnInstanceCollector().parse(
        rpc_fixture("junos-evo", "evpn_instance"), "junos-evo"
    )
    pop1 = result["EVPN-VLAN-AWARE-POP1"]
    assert pop1["irb_interfaces"]["total"] == 2
    assert {"name": "irb.14", "status": "Up", "l3_context": "master"} in (
        pop1["irb_interfaces"]["entries"]
    )
    assert {"name": "irb.15", "status": "Up", "l3_context": "L3VPN-CPE14-UNI"} in (
        pop1["irb_interfaces"]["entries"]
    )


def test_instance_esis_exclude_autogenerated(rpc_fixture):
    # 05: ESI si box generuje sam (per-IRB) a nenesou status - stejne
    # pravidlo jako u EvpnEsiCollector.
    result = EvpnInstanceCollector().parse(
        rpc_fixture("junos-evo", "evpn_instance"), "junos-evo"
    )
    esis = result["EVPN-VLAN-AWARE-POP1"]["esis"]
    assert esis == {"00:11:12:13:14:00:00:00:00:00": "Resolved by IFL ae0.15"}


def test_instance_neighbors(rpc_fixture):
    result = EvpnInstanceCollector().parse(
        rpc_fixture("junos-evo", "evpn_instance"), "junos-evo"
    )
    aware = result["EVPN-VLAN-AWARE-CPE13-NNI"]
    assert aware["neighbors"] == {"total": 1, "addresses": ["150.0.0.13"]}


def test_instance_platform_rpc_names():
    # Na EVO je kanonicky prikaz 'show mac-vrf routing instance extensive';
    # jmeno RPC overene v laborce pres '| display xml rpc' (odhadnuta
    # jmena v minulosti dvakrat nesedela).
    collector = EvpnInstanceCollector()
    assert collector.rpc_name("junos") == "get_evpn_instance_information"
    assert collector.rpc_name("junos-evo") == "get_mac_vrf_instance_information"
    assert collector.rpc_kwargs("junos") == {"extensive": True}
