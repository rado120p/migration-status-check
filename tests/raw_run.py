"""Testovaci run se skutecnymi raw bundly pro testy upgradu (spec 2026-09-23).

Jde skutecnou cestou recorder -> bundle: capture_device nad RecordingDevice,
write_session, save_snapshot, zaznam v run.yml. Importuje se jako
`from raw_run import ...` (tests/ je na sys.path).
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from migration_validator.capture import capture_device
from migration_validator.models.inventory import load_inventory
from migration_validator.models.snapshot import load_snapshot, save_snapshot
from migration_validator.raw.bundle import Session, tool_info, write_session
from migration_validator.raw.recorder import RecordingDevice, SessionRecording
from migration_validator.runs.manifest import CaptureRecord, RunDevice
from migration_validator.runs.services import generate_inventory
from migration_validator.runs.store import RunStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INVENTORY_4 = FIXTURES / "172.20.20.4.yml"
PRE_AT = "2026-09-23T08:00:00Z"
POST_AT = "2026-09-23T09:00:00Z"
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
</ping-results>
"""

CONFIG_XML = """
<rpc-reply><data><configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/0</name>
      <unit>
        <name>100</name>
        <description>INTERNET-CPE100-NNI</description>
        <family><inet><address><name>152.11.100.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <firewall><family><inet><filter><name>F</name></filter></inet></family></firewall>
</configuration></data></rpc-reply>
"""

RESPONSES = {
    "get_interface_information": INTERFACES_XML,
    "get_arp_table_information": ARP_XML,
    "get_bgp_neighbor_information": "<bgp-information/>",
    "get_evpn_vpws_information": "<evpn-vpws-information/>",
    "get_evpn_instance_information": "<evpn-instance-information/>",
    "get_bridge_mac_table": "<l2ald-rtb-macdb/>",
    "get_evpn_mac_table": "<l2ald-rtb-macdb/>",
    "ping": PING_XML,
    "get_config": CONFIG_XML,
}


class FakeRpc:
    def __getattr__(self, name):
        if name.startswith("_") or name not in RESPONSES:
            raise AttributeError(name)
        return lambda **kwargs: etree.fromstring(RESPONSES[name])


class FakeDevice:
    def __init__(self):
        self.facts = {"hostname": "MX1-POP1", "model": "MX204", "version": "21.4R3-S4"}
        self.hostname = "172.20.20.4"
        self.rpc = FakeRpc()


def record_inventory(store: RunStore, node: str, port: str | None = None) -> Path:
    recording = SessionRecording()
    path = store.inventory_path(node, port)
    generate_inventory(RecordingDevice(FakeDevice(), recording), "junos", path, port)
    write_session(
        Session(
            kind="inventory", address="172.20.20.4", hostname=recording.hostname,
            facts=recording.facts, calls=list(recording.calls), started_at=PRE_AT,
            port_filter=port, tool=tool_info(),
        ),
        store.raw_dir(path.name),
    )
    return path


def record_capture(
    store: RunStore, *, phase: str, node: str, started_at: str,
    port: str | None = None, baselines=(), collectors=COLLECTORS, role: str | None = None,
) -> str:
    recording = SessionRecording()
    snapshot = capture_device(
        RecordingDevice(FakeDevice(), recording), "172.20.20.4",
        inventory=load_inventory(INVENTORY_4), collector_names=list(collectors),
        phase=phase, now=started_at,
        baselines=[load_snapshot(store.dir / name) for name in baselines] or None,
    )
    name = store.snapshot_name(phase, node, port)
    save_snapshot(snapshot, store.dir / name)
    write_session(
        Session(
            kind="capture", address="172.20.20.4", hostname=recording.hostname,
            facts=recording.facts, calls=list(recording.calls),
            started_at=snapshot.capture.started_at,
            finished_at=snapshot.capture.finished_at,
            params={
                "phase": phase, "port": port, "collectors": list(collectors),
                "ping_count": 5, "service_types": None, "profile_name": None,
            },
            inventory={"file": INVENTORY_4.name, "raw": False},
            baselines=list(baselines), tool=tool_info(),
        ),
        store.raw_dir(name),
        inventory_yaml=INVENTORY_4,
    )
    manifest = store.load()
    manifest.devices.setdefault(node, RunDevice(
        host=f"10.0.0.{len(manifest.devices) + 1}", platform="junos",
        role=role or ("new" if phase == "post" else "old"),
    ))
    manifest.record_capture(CaptureRecord(
        phase=phase, device=node, port=port, snapshot=name,
        taken=snapshot.capture.started_at,
    ))
    store.save(manifest)
    return name


def build_run(root, name: str = "mig01", *, group: str | None = None) -> RunStore:
    """Run: pre MX1 (celobox), post PTX1 s baseline pre, inventory MX1 s raw."""
    store = RunStore(Path(root), name)
    pre = record_capture(store, phase="pre", node="MX1", started_at=PRE_AT)
    record_capture(store, phase="post", node="PTX1", started_at=POST_AT, baselines=[pre])
    record_inventory(store, "MX1")
    if group is not None:
        manifest = store.load()
        manifest.group = group
        store.save(manifest)
    return store


def arp_with_note(monkeypatch) -> None:
    """Simulace opravy parsovani: ArpCollector pridava pole -> fakta se
    zmeni, snapshot pregenerovany novou verzi se lisi."""
    from migration_validator.collectors.arp import ArpCollector

    original = ArpCollector.parse

    def parse(self, xml, platform):
        return [dict(entry, note="v2") for entry in original(self, xml, platform)]

    monkeypatch.setattr(ArpCollector, "parse", parse)
