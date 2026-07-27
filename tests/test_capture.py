from pathlib import Path

import pytest
from lxml import etree

from migration_validator.capture import capture_device
from migration_validator.models.inventory import load_inventory

NOW = "2026-07-24T09:12:41Z"

# Inventory fixtures zijou v tests/fixtures, ne v korenu repozitare - viz
# commity 26d8461 a 671615b, ktere je tam presunuly kvuli reprodukovatelnosti.
FIXTURES = Path(__file__).resolve().parent / "fixtures"
INVENTORY_4 = str(FIXTURES / "172.20.20.4.yml")

INTERFACES_XML = """
<interface-information>
  <physical-interface>
    <name>ge-0/0/2</name>
    <admin-status>up</admin-status>
    <oper-status>up</oper-status>
    <logical-interface>
      <name>ge-0/0/2.113</name>
      <oper-status>up</oper-status>
      <transit-traffic-statistics>
        <input-pps>412</input-pps>
        <output-pps>388</output-pps>
      </transit-traffic-statistics>
    </logical-interface>
  </physical-interface>
</interface-information>
"""

ARP_XML = """
<arp-table-information>
  <arp-table-entry>
    <ip-address>198.11.13.2</ip-address>
    <mac-address>00:11:22:33:44:55</mac-address>
    <interface-name>ge-0/0/2.113</interface-name>
  </arp-table-entry>
</arp-table-information>
"""

PING_XML = """
<ping-results>
  <probe-results-summary>
    <probes-sent>5</probes-sent>
    <responses-received>5</responses-received>
    <packet-loss>0</packet-loss>
    <rtt-average>1240</rtt-average>
  </probe-results-summary>
  <ping-success/>
</ping-results>
"""

RESPONSES = {
    "get_interface_information": INTERFACES_XML,
    "get_arp_table_information": ARP_XML,
    "get_bgp_neighbor_information": "<bgp-information/>",
    "get_evpn_vpws_information": "<evpn-vpws-information/>",
    "get_evpn_instance_information": "<evpn-instance-information/>",
    "get_bridge_mac_table": "<l2ald-rtb-macdb/>",
    "get_evpn_mac_table": "<l2ald-rtb-macdb/>",
    "get_mac_vrf_mac_table": "<l2ng-l2ald-rtb-macdb/>",
}


class FakeRpc:
    def __init__(self, failing=()):
        self.failing = set(failing)
        self.ping_calls = []

    def __getattr__(self, name):
        if name in self.failing:

            def boom(**kwargs):
                raise RuntimeError(f"RPC {name} selhalo")

            return boom

        if name == "ping":

            def do_ping(**kwargs):
                self.ping_calls.append(kwargs)
                return etree.fromstring(PING_XML)

            return do_ping

        if name not in RESPONSES:
            raise AttributeError(name)

        def call(**kwargs):
            return etree.fromstring(RESPONSES[name])

        return call


class FakeDevice:
    def __init__(self, failing=()):
        self.facts = {"hostname": "MX1-POP1", "model": "MX204", "version": "21.4R3-S4"}
        self.rpc = FakeRpc(failing)


def test_capture_without_inventory_has_device_scope_and_no_ping():
    snapshot = capture_device(FakeDevice(), "172.20.20.4", now=NOW)

    assert snapshot.inventory is None
    assert snapshot.scopes == []
    assert snapshot.probes["ping"] == []
    assert snapshot.facts["interfaces"]["ge-0/0/2.113"]["input_pps"] == 412


def test_capture_with_inventory_builds_scopes_and_pings():
    inventory = load_inventory(INVENTORY_4)
    device = FakeDevice()

    snapshot = capture_device(device, "172.20.20.4", inventory=inventory, now=NOW)

    assert snapshot.scopes
    assert snapshot.inventory is not None
    assert any(probe["target"] == "198.11.13.2" for probe in snapshot.probes["ping"])
    assert device.rpc.ping_calls


def test_failed_collector_is_recorded_and_capture_continues():
    snapshot = capture_device(
        FakeDevice(failing=("get_bgp_neighbor_information",)), "172.20.20.4", now=NOW
    )

    assert snapshot.capture.collectors["interfaces"]["status"] == "ok"
    assert snapshot.capture.collectors["bgp"]["status"] == "error"
    assert "selhalo" in snapshot.capture.collectors["bgp"]["message"]
    assert snapshot.facts["bgp"] == {}


def test_failed_collector_shows_up_as_skip_in_evaluate():
    from migration_validator import api
    from migration_validator.models.result import Status

    inventory = load_inventory(INVENTORY_4)
    snapshot = capture_device(
        FakeDevice(failing=("get_arp_table_information",)),
        "172.20.20.4",
        inventory=inventory,
        now=NOW,
    )

    result = api.evaluate(snapshot, now=NOW)
    arp_checks = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "arp_present"
    ]
    assert arp_checks
    assert all(check.status is Status.SKIP for check in arp_checks)


def test_failed_arp_collector_leaves_ping_empty():
    """Cile pingu se odvozuji z ARP - bez nej nesmi vzniknout falesny probe."""
    inventory = load_inventory(INVENTORY_4)
    device = FakeDevice(failing=("get_arp_table_information",))

    snapshot = capture_device(device, "172.20.20.4", inventory=inventory, now=NOW)

    assert snapshot.facts["arp"] == []
    assert all(
        probe["resolved_from"] == "subnet-fallback" for probe in snapshot.probes["ping"]
    )


def test_collector_subset_can_be_selected():
    snapshot = capture_device(
        FakeDevice(), "172.20.20.4", collector_names=["interfaces"], now=NOW
    )

    assert set(snapshot.capture.collectors) == {"interfaces"}
    assert "bgp" not in snapshot.facts


def test_record_raw_writes_xml(tmp_path):
    capture_device(FakeDevice(), "172.20.20.4", now=NOW, record_raw=tmp_path)

    assert (tmp_path / "junos" / "interfaces.xml").exists()


def test_snapshot_round_trips_to_disk(tmp_path):
    from migration_validator.models.snapshot import load_snapshot, save_snapshot

    snapshot = capture_device(FakeDevice(), "172.20.20.4", now=NOW)
    path = tmp_path / "snap.json"
    save_snapshot(snapshot, path)

    assert load_snapshot(path) == snapshot


def test_unknown_collector_name_is_rejected():
    with pytest.raises(ValueError, match="neznamy collector"):
        capture_device(FakeDevice(), "172.20.20.4", collector_names=["nope"], now=NOW)


def test_phase_is_recorded():
    snapshot = capture_device(
        FakeDevice(), "172.20.20.4", phase="pre-migration", now=NOW
    )
    assert snapshot.capture.phase == "pre-migration"


def test_evo_platform_selects_evo_rpcs():
    """Detekce platformy musi vybrat mac-vrf variantu, ne bridge table."""

    class EvoDevice(FakeDevice):
        def __init__(self):
            super().__init__()
            self.facts = {
                "hostname": "PTX1-POP1",
                "model": "PTX10002-36QDD",
                "version": "25.2R1.8-EVO",
            }

    snapshot = capture_device(EvoDevice(), "172.20.20.5", now=NOW)

    assert snapshot.device.platform == "junos-evo"
    assert snapshot.capture.collectors["evpn_mac"]["status"] == "ok"
