"""Zivy capture pres RecordingDevice a jeho replay dají stejny snapshot
(spec 2026-09-23, sekce 6 - hlavni pojistka, ze replay neodboci)."""

from pathlib import Path

from lxml import etree

from migration_validator import api
from migration_validator.capture import capture_device
from migration_validator.models.inventory import load_inventory
from migration_validator.models.result import Status
from migration_validator.raw.bundle import Session, read_session, write_session
from migration_validator.raw.recorder import RecordingDevice, SessionRecording
from migration_validator.raw.replay import ReplayDevice

NOW = "2026-09-23T10:00:00Z"
DONE = "2026-09-23T10:02:00Z"
INVENTORY_4 = Path(__file__).resolve().parents[1] / "fixtures" / "172.20.20.4.yml"
COLLECTORS = ["interfaces", "arp", "bgp", "evpn_vpws", "evpn_instance", "evpn_mac"]

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
}


class _Rpc:
    def __init__(self, failing=()):
        self.failing = set(failing)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.failing:
            def boom(**kwargs):
                raise RuntimeError(f"RPC {name} selhalo")
            return boom
        if name == "ping":
            return lambda **kwargs: etree.fromstring(PING_XML)
        if name not in RESPONSES:
            raise AttributeError(name)
        return lambda **kwargs: etree.fromstring(RESPONSES[name])


class _Device:
    def __init__(self, failing=()):
        self.facts = {"hostname": "MX1-POP1", "model": "MX204", "version": "21.4R3-S4"}
        self.hostname = "172.20.20.4"
        self.rpc = _Rpc(failing)


def _live(tmp_path, failing=()):
    recording = SessionRecording()
    inventory = load_inventory(INVENTORY_4)
    snapshot = capture_device(
        RecordingDevice(_Device(failing), recording), "172.20.20.4",
        inventory=inventory, collector_names=COLLECTORS, phase="pre",
        now=NOW, finished_at=DONE,
    )
    write_session(
        Session(
            kind="capture", address="172.20.20.4", hostname=recording.hostname,
            facts=recording.facts, calls=recording.calls,
            started_at=snapshot.capture.started_at,
            finished_at=snapshot.capture.finished_at,
        ),
        tmp_path / "raw",
    )
    return snapshot, read_session(tmp_path / "raw"), inventory


def _replay(session, inventory, collectors=COLLECTORS, **kwargs):
    return capture_device(
        ReplayDevice(session), session.address, inventory=inventory,
        collector_names=collectors, phase="pre", now=session.started_at,
        finished_at=session.finished_at, **kwargs,
    )


def test_finished_at_is_kept_separately():
    snapshot = capture_device(_Device(), "172.20.20.4", collector_names=["arp"], now=NOW, finished_at=DONE)
    assert (snapshot.capture.started_at, snapshot.capture.finished_at) == (NOW, DONE)


def test_replay_reproduces_live_snapshot(tmp_path):
    live, session, inventory = _live(tmp_path, failing=("get_bgp_neighbor_information",))

    assert live.capture.collectors["bgp"]["status"] == "error"
    assert live.probes["ping"]
    assert _replay(session, inventory).to_dict() == live.to_dict()


def test_replay_new_collector_is_error_not_silent(tmp_path):
    """Collector, ktery v nahravce neni, je status error 'neni v raw
    zaznamu' - jeho checky pak SKIP, ne tichy PASS."""
    _, session, inventory = _live(tmp_path)

    replayed = _replay(session, inventory, collectors=COLLECTORS + ["bfd"])

    assert replayed.capture.collectors["bfd"]["status"] == "error"
    assert "neni v raw zaznamu" in replayed.capture.collectors["bfd"]["message"]


def test_replay_unrecorded_ping_target_is_skip_not_broken(tmp_path):
    """Jine argumenty pingu nez pri nahravani (tady ping_count) = kazdy cil
    je miss. Vysledek je sent=0 a check SKIP, nikdy BROKEN."""
    _, session, inventory = _live(tmp_path)

    replayed = _replay(session, inventory, ping_count=3)

    assert replayed.probes["ping"]
    assert all(
        probe["sent"] == 0 and probe["error"] == "neni v raw zaznamu"
        for probe in replayed.probes["ping"]
    )
    result = api.evaluate(replayed, now=NOW)
    ping_checks = [
        check for scope in result.scopes for check in scope.checks
        if check.id == "ping_reachability"
    ]
    assert ping_checks
    assert all(check.status is Status.SKIP for check in ping_checks)
