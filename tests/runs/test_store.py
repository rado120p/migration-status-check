"""Cesty a nazvy souboru v runs/<nazev>/."""

import threading
import time
from pathlib import Path

from migration_validator.runs.manifest import CaptureRecord, RunManifest
from migration_validator.runs.store import RunStore, raw_name


def test_paths_and_naming(tmp_path):
    store = RunStore(root=tmp_path, name="mig01")
    assert store.dir == tmp_path / "mig01"
    assert store.manifest_path == tmp_path / "mig01" / "run.yml"
    assert (
        store.snapshot_path("pre", "MX1-POP1", "ge-0/0/0").name
        == "snapshot_pre_MX1-POP1_ge_0_0_0.json"
    )
    assert (
        store.snapshot_path("post", "PTX1-POP1", None).name
        == "snapshot_post_PTX1-POP1_all.json"
    )
    assert (
        store.inventory_path("PTX1-POP1", "et-0/0/0").name
        == "inventory_PTX1-POP1_et_0_0_0.yml"
    )


def test_load_missing_manifest_returns_empty(tmp_path):
    store = RunStore(root=tmp_path, name="novy")
    manifest = store.load()
    assert manifest.devices == {}
    assert manifest.captures == []


def test_save_and_load_roundtrip(tmp_path):
    store = RunStore(root=tmp_path, name="mig01")
    manifest = store.load()
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", None, "snapshot_pre_MX1-POP1_all.json", "T")
    )
    store.save(manifest)
    assert store.manifest_path.exists()
    assert store.load().find_capture("pre", "MX1-POP1", None) is not None


def test_missing_snapshots(tmp_path):
    store = RunStore(root=tmp_path, name="mig01")
    manifest = store.load()
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", None, "snapshot_pre_MX1-POP1_all.json", "T")
    )
    assert store.missing_snapshots(manifest) == ["snapshot_pre_MX1-POP1_all.json"]
    store.dir.mkdir(parents=True)
    (store.dir / "snapshot_pre_MX1-POP1_all.json").write_text("{}")
    assert store.missing_snapshots(manifest) == []


def test_raw_name_and_dir(tmp_path):
    store = RunStore(tmp_path, "mig01")
    assert raw_name("snapshot_post_PTX1_et_0_0_8.json") == "post_PTX1_et_0_0_8"
    assert raw_name("inventory_PTX1_all.yml") == "inventory_PTX1_all"
    assert store.raw_dir("snapshot_pre_MX1_all.json") == tmp_path / "mig01" / "raw" / "pre_MX1_all"


def test_lock_excludes_second_holder_in_same_process(tmp_path):
    """GUI ma capture i upgrade v jednom procesu - zamek se musi vylucovat
    i mezi vlakny. Zabiji mutanta: fcntl.lockf misto fcntl.flock (POSIX
    record lock se uvnitr procesu nevylucuje)."""
    store = RunStore(tmp_path, "mig01")
    order = []

    def other():
        with store.lock():
            order.append("other")

    with store.lock():
        thread = threading.Thread(target=other)
        thread.start()
        time.sleep(0.2)
        order.append("main")
    thread.join(5)

    assert order == ["main", "other"]
