"""capture_into_run - spolecna orchestrace pro CLI a GUI."""

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from lxml import etree

from migration_validator import api as mig_api
from migration_validator.config import default_profile
from migration_validator.connection.junos import ConnectionOptions
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot
from migration_validator.raw.bundle import has_session, read_session
from migration_validator.raw.calls import RecordedCall
from migration_validator.runs.manifest import CaptureRecord, RunDevice, RunManifest, load_manifest
from migration_validator.runs.orchestrate import CaptureOutcome, capture_into_run
from migration_validator.runs.store import RunStore

NOW = "2026-07-24T11:40:02Z"


def _snapshot(*, host, platform="junos", phase=None):
    scope = Scope(
        id="svc:L3VPN:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN", "IPVPN", None),
        selectors=Selectors(interfaces=[]),
    )
    return Snapshot(
        device=DeviceMeta(address=host, platform=platform),
        capture=CaptureMeta(
            started_at=NOW, finished_at=NOW, phase=phase, collectors={}
        ),
        facts={},
        probes={"ping": []},
        scopes=[scope],
        inventory=[],
    )


def _fake_capture(monkeypatch, platform="junos"):
    calls = {}

    def fake(host, **kwargs):
        calls["host"] = host
        calls.update(kwargs)
        return _snapshot(host=host, platform=platform, phase=kwargs.get("phase"))

    monkeypatch.setattr("migration_validator.runs.orchestrate.api.capture", fake)
    return calls


def test_capture_do_runu_zapise_snapshot_a_manifest(tmp_path, monkeypatch):
    _fake_capture(monkeypatch)

    store = RunStore(root=tmp_path, name="mig01")
    options = ConnectionOptions(host="172.20.20.4")
    profile = default_profile()

    outcome = capture_into_run(
        store,
        host="172.20.20.4",
        phase="pre",
        port=None,
        options=options,
        profile=profile,
        inventory="tests/fixtures/172.20.20.4.yml",
    )

    assert isinstance(outcome, CaptureOutcome)
    assert outcome.snapshot_path.exists()
    assert outcome.failed_collectors == {}

    manifest = load_manifest(tmp_path / "mig01" / "run.yml")
    assert len(manifest.captures) == 1
    record = manifest.captures[0]
    assert record.phase == "pre"
    assert record.device == "172.20.20.4"
    assert record.snapshot == outcome.snapshot_path.name

    with pytest.raises(ValueError):
        capture_into_run(
            store,
            host="172.20.20.4",
            phase="pre",
            port=None,
            options=options,
            profile=profile,
            inventory="tests/fixtures/172.20.20.4.yml",
        )


def test_capture_single_run_odmitne_neznamy_host_pred_capture(tmp_path, monkeypatch):
    calls = _fake_capture(monkeypatch)

    store = RunStore(root=tmp_path, name="single01")
    store.save(
        RunManifest(
            devices={
                "PTX1-POP1": RunDevice(
                    host="172.20.20.5", platform="junos-evo", role="single"
                )
            },
            kind="single",
        )
    )
    options = ConnectionOptions(host="172.20.20.9")
    profile = default_profile()

    with pytest.raises(ValueError, match="run typu single"):
        capture_into_run(
            store,
            host="172.20.20.9",
            phase="pre",
            port=None,
            options=options,
            profile=profile,
            inventory="tests/fixtures/172.20.20.4.yml",
        )

    assert calls == {}


INVENTORY = "tests/fixtures/172.20.20.4.yml"


def _fake_capture_recording(monkeypatch, rpc="get_arp_table_information", during=None):
    """api.capture, ktery naplni recorder jako zivy capture. `during` se
    zavola uprostred capture (simulace souběžného zapisu do runu)."""

    def fake(host, **kwargs):
        recorder = kwargs.get("recorder")
        if recorder is not None:
            recorder.facts = {"hostname": "R1", "model": "MX204", "version": "21.4R3"}
            recorder.hostname = host
            recorder.calls.append(
                RecordedCall(seq=1, rpc=rpc, kwargs={}, reply_xml=b"<x/>")
            )
        if during is not None:
            during()
        return _snapshot(host=host, phase=kwargs.get("phase"))

    monkeypatch.setattr("migration_validator.runs.orchestrate.api.capture", fake)


def _capture(store, phase="pre", host="172.20.20.4", port=None, **kwargs):
    kwargs.setdefault("inventory", INVENTORY)
    return capture_into_run(
        store, host=host, phase=phase, port=port,
        options=ConnectionOptions(host=host), profile=default_profile(),
        overwrite=True, **kwargs,
    )


def test_capture_writes_raw_bundle_next_to_snapshot(tmp_path, monkeypatch):
    _fake_capture_recording(monkeypatch)
    store = RunStore(root=tmp_path, name="mig01")

    outcome = _capture(store)

    record = load_manifest(store.manifest_path).captures[0]
    raw_dir = store.raw_dir(outcome.snapshot_path.name)
    session = read_session(raw_dir)
    assert session.kind == "capture"
    assert session.started_at == record.taken
    assert session.params == {
        "phase": "pre", "port": None, "collectors": None, "ping_count": 5,
        "service_types": None, "profile_name": None,
    }
    assert session.inventory == {"file": "172.20.20.4.yml", "raw": False}
    assert (raw_dir / "inventory" / "inventory.yml").is_file()
    assert [call.rpc for call in session.calls] == ["get_arp_table_information"]


def test_second_capture_replaces_raw(tmp_path, monkeypatch):
    store = RunStore(root=tmp_path, name="mig01")
    _fake_capture_recording(monkeypatch, rpc="get_arp_table_information")
    _capture(store)
    _fake_capture_recording(monkeypatch, rpc="get_nd_information")
    outcome = _capture(store)

    session = read_session(store.raw_dir(outcome.snapshot_path.name))
    assert [call.rpc for call in session.calls] == ["get_nd_information"]


def test_raw_write_failure_is_warning_and_leaves_no_stale_raw(tmp_path, monkeypatch):
    """Zabiji mutanta: capture_into_run vubec nezavola remove_session(raw_dir)
    (po selhanem zapisu by vedle noveho snimku zustal raw predchoziho)."""
    store = RunStore(root=tmp_path, name="mig01")
    _fake_capture_recording(monkeypatch)
    first = _capture(store)
    raw_dir = store.raw_dir(first.snapshot_path.name)
    assert has_session(raw_dir)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("migration_validator.runs.orchestrate.write_session", boom)
    outcome = _capture(store)

    assert any("nepujde pregenerovat" in warning for warning in outcome.warnings)
    assert not raw_dir.exists()
    assert outcome.snapshot_path.exists()
    assert len(load_manifest(store.manifest_path).captures) == 1


def test_old_raw_removal_failure_does_not_fail_capture(tmp_path, monkeypatch):
    """R13: selhani smazani stareho raw neshodi dokonceny capture - mereni
    se ulozi, zaznam v run.yml vznikne, varovani rekne proc. Zbyly stary
    bundle upgrade odmitne kontrolou started_at/taken (RAW_MISMATCH)."""
    from migration_validator.raw.bundle import remove_session as real_remove

    store = RunStore(root=tmp_path, name="mig01")
    _fake_capture_recording(monkeypatch)
    first = _capture(store)
    raw_dir = store.raw_dir(first.snapshot_path.name)
    first.snapshot_path.unlink()
    manifest = load_manifest(store.manifest_path)
    manifest.captures.clear()
    store.save(manifest)

    def flaky(target):
        if Path(target) == raw_dir:
            raise OSError("permission denied")
        return real_remove(target)

    monkeypatch.setattr("migration_validator.runs.orchestrate.remove_session", flaky)
    outcome = _capture(store)

    assert any(
        warning.startswith(f"stary raw zaznam {raw_dir.name} se nepodarilo smazat (permission denied)")
        and warning.endswith("upgrade ho odmitne jako cizi")
        for warning in outcome.warnings
    )
    assert outcome.snapshot_path.exists()
    records = load_manifest(store.manifest_path).captures
    assert [record.snapshot for record in records] == [outcome.snapshot_path.name]


def test_unexpected_raw_write_error_is_warning(tmp_path, monkeypatch):
    """R13: raw je best effort - ani jina nez OSError vyjimka pri zapisu
    raw neshodi capture a nenecha stary raw vedle noveho snimku."""
    store = RunStore(root=tmp_path, name="mig01")
    _fake_capture_recording(monkeypatch)
    first = _capture(store)
    raw_dir = store.raw_dir(first.snapshot_path.name)
    assert has_session(raw_dir)

    def boom(*args, **kwargs):
        raise TypeError("Object of type bytes is not JSON serializable")

    monkeypatch.setattr("migration_validator.runs.orchestrate.write_session", boom)
    outcome = _capture(store)

    assert any(
        "nepujde pregenerovat (Object of type bytes is not JSON serializable)" in warning
        for warning in outcome.warnings
    )
    assert not raw_dir.exists()
    assert outcome.snapshot_path.exists()
    assert [record.snapshot for record in load_manifest(store.manifest_path).captures] == [
        outcome.snapshot_path.name
    ]


def test_capture_removes_old_raw_before_save_snapshot(tmp_path, monkeypatch):
    """Mutant A: capture_into_run prohodi poradi na save_snapshot(...) pred
    remove_session(raw_dir) (orchestrate.py ~294-295) - pri padu po ulozeni
    snimku, ale pred smazanim raw, by vedle noveho snimku zustal stary raw.
    Zachyti se spy na save_snapshot: v okamziku volani uz musi byt stary raw
    smazany."""
    from migration_validator.models.snapshot import save_snapshot as real_save_snapshot

    store = RunStore(root=tmp_path, name="mig01")
    _fake_capture_recording(monkeypatch)
    first = _capture(store)
    raw_dir = store.raw_dir(first.snapshot_path.name)
    assert has_session(raw_dir)

    seen = {}

    def spy(snapshot, path):
        seen["raw_dir_existed_at_save"] = raw_dir.exists()
        return real_save_snapshot(snapshot, path)

    monkeypatch.setattr("migration_validator.runs.orchestrate.save_snapshot", spy)
    _capture(store)

    assert seen["raw_dir_existed_at_save"] is False


def test_post_records_baselines_used(tmp_path, monkeypatch):
    _fake_capture_recording(monkeypatch)
    mig_api.create_run(
        "mig01", kind="migration",
        devices=[
            {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"},
            {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"},
        ],
        mappings=[("ge-0/0/1", "et-0/0/1")], run_root=tmp_path,
    )
    store = RunStore(root=tmp_path, name="mig01")
    pre = _capture(store, host="10.0.0.1", port="ge-0/0/1")

    post = _capture(store, phase="post", host="10.0.0.2", port="et-0/0/1")

    session = read_session(store.raw_dir(post.snapshot_path.name))
    assert session.baselines == [pre.snapshot_path.name]
    pre_taken = load_manifest(store.manifest_path).find_capture("pre", "MX1", "ge-0/0/1").taken
    assert session.baseline_taken == {pre.snapshot_path.name: pre_taken}


def test_pre_records_empty_baseline_taken(tmp_path, monkeypatch):
    _fake_capture_recording(monkeypatch)
    store = RunStore(root=tmp_path, name="mig01")

    outcome = _capture(store)

    session = read_session(store.raw_dir(outcome.snapshot_path.name))
    assert session.baselines == []
    assert session.baseline_taken == {}


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
  </interfaces>
</configuration></data></rpc-reply>
"""


class _ConfigRpc:
    def get_config(self, filter_xml, options):
        return etree.fromstring(CONFIG)


class _ConfigDevice:
    facts = {"hostname": "R1", "model": "MX204", "version": "21.4R3"}
    hostname = "172.20.20.4"
    rpc = _ConfigRpc()


def _fake_connect(monkeypatch):
    @contextmanager
    def fake(options):
        yield _ConfigDevice()

    monkeypatch.setattr("migration_validator.runs.orchestrate.connect", fake)


def test_parse_services_pins_inventory_raw_into_capture(tmp_path, monkeypatch):
    _fake_connect(monkeypatch)
    _fake_capture_recording(monkeypatch)
    store = RunStore(root=tmp_path, name="mig01")

    outcome = _capture(store, port="ge-0/0/0", inventory=None, parse_services=True)

    inventory_name = store.inventory_path("172.20.20.4", "ge-0/0/0").name
    inventory_session = read_session(store.raw_dir(inventory_name))
    assert inventory_session.kind == "inventory"
    assert inventory_session.port_filter == "ge-0/0/0"
    assert inventory_session.calls[0].rpc == "get_config"
    capture_session = read_session(store.raw_dir(outcome.snapshot_path.name))
    assert capture_session.inventory == {"file": inventory_name, "raw": True}
    assert read_session(store.raw_dir(outcome.snapshot_path.name) / "inventory").port_filter == "ge-0/0/0"
    assert not list(store.dir.glob(".tmp-*"))


def test_inventory_raw_failure_leaves_no_stale_raw(tmp_path, monkeypatch):
    _fake_connect(monkeypatch)
    _fake_capture_recording(monkeypatch)
    store = RunStore(root=tmp_path, name="mig01")
    _capture(store, port="ge-0/0/0", inventory=None, parse_services=True)
    inventory_raw = store.raw_dir(store.inventory_path("172.20.20.4", "ge-0/0/0").name)
    assert has_session(inventory_raw)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("migration_validator.runs.orchestrate.write_session", boom)
    outcome = _capture(store, port="ge-0/0/0", inventory=None, parse_services=True)

    assert not inventory_raw.exists()
    assert any("nepujde pregenerovat" in warning for warning in outcome.warnings)


def test_parse_services_removes_old_raw_before_os_replace(tmp_path, monkeypatch):
    """Mutant B: _parse_services prohodi poradi na os.replace(scratch,
    inventory_path) pred remove_session(raw_dir) (orchestrate.py ~140-141) -
    pri padu po prejmenovani docasneho souboru, ale pred smazanim raw, by
    vedle nove inventory zustal stary raw. Spy jde na globalni os.replace
    (write_session ho pouziva taky), deleguje na skutecny os.replace a
    zaznamena has_session(inventory raw) jen pro cil == inventory YAML."""
    _fake_connect(monkeypatch)
    _fake_capture_recording(monkeypatch)
    store = RunStore(root=tmp_path, name="mig01")
    _capture(store, port="ge-0/0/0", inventory=None, parse_services=True)
    inventory_path = store.inventory_path("172.20.20.4", "ge-0/0/0")
    inventory_raw = store.raw_dir(inventory_path.name)
    assert has_session(inventory_raw)

    real_replace = os.replace
    seen = {}

    def spy(src, dst):
        if Path(dst) == inventory_path:
            seen["raw_existed_at_replace"] = has_session(inventory_raw)
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", spy)
    _capture(store, port="ge-0/0/0", inventory=None, parse_services=True)

    assert seen["raw_existed_at_replace"] is False


def test_capture_write_waits_for_run_lock(tmp_path, monkeypatch):
    """Zapis capture (snapshot + raw + run.yml) jde pod zamkem runu - upgrade
    ho pri vymene nesmi prerusit ani prepsat."""
    _fake_capture_recording(monkeypatch)
    store = RunStore(root=tmp_path, name="mig01")
    snapshot_path = store.snapshot_path("pre", "172.20.20.4", None)

    with store.lock():
        thread = threading.Thread(target=lambda: _capture(store))
        thread.start()
        time.sleep(0.3)
        assert not snapshot_path.exists()
    thread.join(5)

    assert snapshot_path.exists()


def test_manifest_is_reloaded_under_lock(tmp_path, monkeypatch):
    """Capture trva minuty; druhe zarizeni runu mezitim zapise svuj zaznam.
    Zabiji mutanta: capture_into_run uklada manifest nacteny na zacatku."""
    store = RunStore(root=tmp_path, name="mig01")

    def other_capture():
        manifest = store.load()
        manifest.record_capture(CaptureRecord(
            phase="pre", device="OTHER", port=None,
            snapshot="snapshot_pre_OTHER_all.json", taken=NOW,
        ))
        store.save(manifest)

    _fake_capture_recording(monkeypatch, during=other_capture)
    _capture(store)

    devices = {record.device for record in load_manifest(store.manifest_path).captures}
    assert devices == {"OTHER", "172.20.20.4"}
