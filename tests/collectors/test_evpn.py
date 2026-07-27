import pytest
from lxml import etree

from migration_validator.collectors.evpn import (
    NO_DOMAIN,
    EvpnEsiCollector,
    EvpnMacCollector,
    EvpnVpwsCollector,
)

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_vpws_schema(rpc_fixture, platform):
    result = EvpnVpwsCollector().parse(rpc_fixture(platform, "evpn_vpws"), platform)
    assert isinstance(result, dict)
    for instance, data in result.items():
        assert set(data) == {"local_sid", "remote_sid", "status"}
        assert isinstance(data["status"], str)
        assert isinstance(data["local_sid"], int)
        assert isinstance(data["remote_sid"], int)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_esi_schema(rpc_fixture, platform):
    result = EvpnEsiCollector().parse(rpc_fixture(platform, "evpn_esi"), platform)
    assert isinstance(result, dict)
    for esi, data in result.items():
        assert set(data) == {"status", "df_role", "interface"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_mac_schema(rpc_fixture, platform):
    result = EvpnMacCollector().parse(rpc_fixture(platform, "evpn_mac"), platform)
    assert isinstance(result, dict)
    for instance, domains in result.items():
        assert isinstance(domains, dict)
        assert all(isinstance(count, int) for count in domains.values())


def test_esi_requires_extensive():
    """Bez 'extensive' Junos zadny ESI blok nevrati a collector by tise mlcel."""
    assert EvpnEsiCollector().rpc_kwargs("junos") == {"extensive": True}


def test_vpws_reads_interface_status_not_pe_status():
    """Stav rozhrani je 'Up', stav vzdaleneho PE 'Resolved'.

    Check evpn_vpws_status porovnava proti 'Up', takze collector musi emitovat
    stav rozhrani. Kdyby vratil 'Resolved', zdrava sluzba by hlasila BROKEN.
    """
    xml = etree.fromstring(
        """
        <evpn-vpws-information>
          <evpn-vpws-instance>
            <evpn-vpws-instance-name>VPWS-A</evpn-vpws-instance-name>
            <evpn-vpws-interface-status-table>
              <evpn-vpws-interface>
                <evpn-vpws-interface-name>ge-0/0/2.213</evpn-vpws-interface-name>
                <evpn-vpws-interface-status>Up</evpn-vpws-interface-status>
                <evpn-vpws-service-id-local-status-table>
                  <evpn-vpws-sid-local>
                    <evpn-vpws-sid-local-value>1000</evpn-vpws-sid-local-value>
                  </evpn-vpws-sid-local>
                </evpn-vpws-service-id-local-status-table>
                <evpn-vpws-service-id-remote-status-table>
                  <evpn-vpws-sid-remote>
                    <evpn-vpws-sid-remote-value>2000</evpn-vpws-sid-remote-value>
                    <evpn-vpws-sid-pe-status-table>
                      <evpn-vpws-sid-pe-info>
                        <evpn-vpws-sid-pe-status>Resolved</evpn-vpws-sid-pe-status>
                      </evpn-vpws-sid-pe-info>
                    </evpn-vpws-sid-pe-status-table>
                  </evpn-vpws-sid-remote>
                </evpn-vpws-service-id-remote-status-table>
              </evpn-vpws-interface>
            </evpn-vpws-interface-status-table>
          </evpn-vpws-instance>
        </evpn-vpws-information>
        """
    )
    result = EvpnVpwsCollector().parse(xml, "junos")
    assert result == {"VPWS-A": {"local_sid": 1000, "remote_sid": 2000, "status": "Up"}}


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


def test_mac_counts_are_grouped_by_instance_and_vlan_on_mx():
    xml = etree.fromstring(
        """
        <l2ald-rtb-macdb>
          <l2ald-mac-entry>
            <l2-mac-routing-instance>EVPN-AWARE</l2-mac-routing-instance>
            <l2-mac-bridging-domain>BD-313</l2-mac-bridging-domain>
            <l2-bridge-vlan>313</l2-bridge-vlan>
            <l2-mac-entry><l2-mac-address>00:11:22:33:44:01</l2-mac-address></l2-mac-entry>
            <l2-mac-entry><l2-mac-address>00:11:22:33:44:02</l2-mac-address></l2-mac-entry>
          </l2ald-mac-entry>
          <l2ald-mac-entry>
            <l2-mac-routing-instance>EVPN-BASED</l2-mac-routing-instance>
            <l2-mac-bridging-domain>__EVPN-BASED__</l2-mac-bridging-domain>
            <l2-bridge-vlan>none</l2-bridge-vlan>
            <l2-mac-entry><l2-mac-address>00:11:22:33:44:03</l2-mac-address></l2-mac-entry>
          </l2ald-mac-entry>
        </l2ald-rtb-macdb>
        """
    )

    assert EvpnMacCollector().parse(xml, "junos") == {
        "EVPN-AWARE": {"313": 2},
        "EVPN-BASED": {NO_DOMAIN: 1},
    }


def test_mac_counts_use_same_keys_on_evo():
    """Stejna sluzba musi mit stejne klice jako na MX, jinak nejde porovnat.

    EVO pouziva jina jmena elementu (l2ng-*) a domene rika 'VL-313' misto
    'BD-313'; vlan-based instanci navic uvadi VLAN id, i kdyz zadnou vlastni
    domenu nema - proto se pozna podle 'VL-NONE'.
    """
    xml = etree.fromstring(
        """
        <l2ng-l2ald-rtb-macdb>
          <l2ng-l2ald-mac-entry-vlan>
            <l2ng-l2-mac-routing-instance>EVPN-AWARE</l2ng-l2-mac-routing-instance>
            <l2ng-l2-vlan-id>313</l2ng-l2-vlan-id>
            <l2ng-mac-entry>
              <l2ng-l2-mac-vlan-name>VL-313</l2ng-l2-mac-vlan-name>
              <l2ng-l2-mac-address>00:11:22:33:44:01</l2ng-l2-mac-address>
            </l2ng-mac-entry>
            <l2ng-mac-entry>
              <l2ng-l2-mac-vlan-name>VL-313</l2ng-l2-mac-vlan-name>
              <l2ng-l2-mac-address>00:11:22:33:44:02</l2ng-l2-mac-address>
            </l2ng-mac-entry>
          </l2ng-l2ald-mac-entry-vlan>
          <l2ng-l2ald-mac-entry-vlan>
            <l2ng-l2-mac-routing-instance>EVPN-BASED</l2ng-l2-mac-routing-instance>
            <l2ng-l2-vlan-id>413</l2ng-l2-vlan-id>
            <l2ng-mac-entry>
              <l2ng-l2-mac-vlan-name>VL-NONE</l2ng-l2-mac-vlan-name>
              <l2ng-l2-mac-address>00:11:22:33:44:03</l2ng-l2-mac-address>
            </l2ng-mac-entry>
          </l2ng-l2ald-mac-entry-vlan>
        </l2ng-l2ald-rtb-macdb>
        """
    )

    assert EvpnMacCollector().parse(xml, "junos-evo") == {
        "EVPN-AWARE": {"313": 2},
        "EVPN-BASED": {NO_DOMAIN: 1},
    }


def test_mac_merges_every_rpc_for_platform():
    """MX vidi vlan-aware jen pres bridge mac-table, vlan-based jen pres evpn."""

    class FakeRpc:
        def get_bridge_mac_table(self, **kwargs):
            return etree.fromstring(
                """
                <l2ald-rtb-macdb><l2ald-mac-entry>
                  <l2-mac-routing-instance>AWARE</l2-mac-routing-instance>
                  <l2-bridge-vlan>313</l2-bridge-vlan>
                  <l2-mac-entry><a/></l2-mac-entry>
                </l2ald-mac-entry></l2ald-rtb-macdb>
                """
            )

        def get_evpn_mac_table(self, **kwargs):
            return etree.fromstring(
                """
                <l2ald-rtb-macdb><l2ald-mac-entry>
                  <l2-mac-routing-instance>BASED</l2-mac-routing-instance>
                  <l2-bridge-vlan>none</l2-bridge-vlan>
                  <l2-mac-entry><a/></l2-mac-entry>
                </l2ald-mac-entry></l2ald-rtb-macdb>
                """
            )

    class FakeDevice:
        rpc = FakeRpc()

    result = EvpnMacCollector().collect(FakeDevice(), "junos")
    assert result == {"AWARE": {"313": 1}, "BASED": {NO_DOMAIN: 1}}


def test_mac_partial_rpc_failure_is_an_error():
    """Castecna data by check porovnal proti plnemu baseline jako propad."""
    from migration_validator.collectors.base import CollectorError

    class FakeRpc:
        def get_bridge_mac_table(self, **kwargs):
            return etree.fromstring("<l2ald-rtb-macdb/>")

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
