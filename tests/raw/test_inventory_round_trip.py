"""Inventory z nahrane konfigurace vyjde pri replayi stejne (spec 2026-09-23)."""

import yaml
from lxml import etree

from migration_validator.raw.bundle import Session, read_session, write_session
from migration_validator.raw.recorder import RecordingDevice, SessionRecording
from migration_validator.raw.replay import ReplayDevice
from migration_validator.runs.services import generate_inventory

CONFIG = """
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
    <interface>
      <name>ge-0/0/1</name>
      <unit>
        <name>200</name>
        <description>INTERNET-CPE200-NNI</description>
        <family><inet><address><name>152.11.200.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <firewall><family><inet><filter><name>F</name></filter></inet></family></firewall>
</configuration></data></rpc-reply>
"""


class _Rpc:
    def get_config(self, filter_xml, options):
        return etree.fromstring(CONFIG)


class _Device:
    facts = {"hostname": "MX1", "model": "MX204", "version": "21.4R3"}
    hostname = "172.20.20.4"
    rpc = _Rpc()


def test_inventory_replays_identically(tmp_path):
    recording = SessionRecording()
    live = tmp_path / "live.yml"
    generate_inventory(RecordingDevice(_Device(), recording), "junos", live, "ge-0/0/0")
    raw_dir = tmp_path / "raw" / "inventory_MX1_ge_0_0_0"
    write_session(
        Session(
            kind="inventory", address="172.20.20.4", hostname=recording.hostname,
            facts=recording.facts, calls=recording.calls, port_filter="ge-0/0/0",
        ),
        raw_dir,
    )

    session = read_session(raw_dir)
    replayed = tmp_path / "replayed.yml"
    generate_inventory(ReplayDevice(session), "junos", replayed, session.port_filter)

    assert yaml.safe_load(replayed.read_text()) == yaml.safe_load(live.read_text())
    assert "ge-0/0/1" not in replayed.read_text()
