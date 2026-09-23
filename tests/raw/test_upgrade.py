"""mig-validate upgrade - pregenerovani runu z raw zaznamu (spec 2026-09-23, sekce 3)."""

import json
from datetime import datetime, timezone

from raw_run import PRE_AT, arp_with_note, build_run

from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot, save_snapshot
from migration_validator.runs.manifest import CaptureRecord
from migration_validator.raw.upgrade import (
    CHANGED,
    NO_RAW,
    NOT_REGENERABLE,
    RAW_MISMATCH,
    UNCHANGED,
    UpgradeItem,
    UpgradeReport,
    render_report,
    upgrade_run,
)

NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)


def _results(report):
    return {item.file: (item.result, item.reason) for item in report.items}


def _bytes(store):
    return {
        path.name: path.read_bytes()
        for path in sorted(store.dir.iterdir())
        if path.is_file() and path.name != ".lock"
    }


def test_same_version_upgrade_is_unchanged(tmp_path):
    store = build_run(tmp_path)

    report = upgrade_run(store, dry_run=True)

    assert _results(report) == {
        "inventory_MX1_all.yml": (UNCHANGED, None),
        "snapshot_pre_MX1_all.json": (UNCHANGED, None),
        "snapshot_post_PTX1_all.json": (UNCHANGED, None),
    }
    assert report.exit_code == 0
    assert report.error is None


def test_capture_without_raw_is_reported_and_untouched(tmp_path):
    store = build_run(tmp_path)
    old = Snapshot(device=DeviceMeta(address="10.0.0.9"), capture=CaptureMeta(started_at=PRE_AT, phase="pre"))
    save_snapshot(old, store.dir / "snapshot_pre_OLD_all.json")
    manifest = store.load()
    manifest.record_capture(CaptureRecord(
        phase="pre", device="OLD", port=None,
        snapshot="snapshot_pre_OLD_all.json", taken=PRE_AT,
    ))
    store.save(manifest)
    before = (store.dir / "snapshot_pre_OLD_all.json").read_bytes()

    report = upgrade_run(store, now=NOW)

    assert _results(report)["snapshot_pre_OLD_all.json"] == (NOT_REGENERABLE, NO_RAW)
    assert report.exit_code == 1
    assert (store.dir / "snapshot_pre_OLD_all.json").read_bytes() == before


def test_raw_that_does_not_belong_to_snapshot_is_refused(tmp_path):
    store = build_run(tmp_path)
    manifest = store.load()
    manifest.captures[0].taken = "2026-09-23T07:59:59Z"
    store.save(manifest)

    report = upgrade_run(store, dry_run=True)

    assert _results(report)["snapshot_pre_MX1_all.json"] == (NOT_REGENERABLE, RAW_MISMATCH)


def test_changed_parse_regenerates_and_backs_up(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    before = (store.dir / "snapshot_pre_MX1_all.json").read_bytes()
    arp_with_note(monkeypatch)

    report = upgrade_run(store, now=NOW)

    assert _results(report)["snapshot_pre_MX1_all.json"] == (CHANGED, None)
    assert report.backup == "backup/upgrade-20260923T120000Z"
    backup = store.dir / report.backup
    assert (backup / "snapshot_pre_MX1_all.json").read_bytes() == before
    data = json.loads((store.dir / "snapshot_pre_MX1_all.json").read_text())
    assert all(entry["note"] == "v2" for entry in data["facts"]["arp"])
    assert not (store.dir / ".upgrade-staging").exists()


def test_post_uses_regenerated_pre_as_baseline(tmp_path, monkeypatch):
    """Zabiji mutanta: post dostane baseline nactenou z disku (stara verze)
    misto pregenerovane pre ze stejneho behu."""
    store = build_run(tmp_path)
    arp_with_note(monkeypatch)
    seen = {}

    from migration_validator.raw import upgrade as upgrade_module

    real = upgrade_module.capture_device

    def spy(*args, **kwargs):
        if kwargs.get("phase") == "post":
            seen["baselines"] = kwargs.get("baselines")
        return real(*args, **kwargs)

    monkeypatch.setattr(upgrade_module, "capture_device", spy)
    upgrade_run(store, dry_run=True)

    assert seen["baselines"]
    assert all(entry.get("note") == "v2" for entry in seen["baselines"][0].facts["arp"])


def test_missing_baseline_is_noted_and_post_still_regenerates(tmp_path):
    store = build_run(tmp_path)
    (store.dir / "snapshot_pre_MX1_all.json").write_text("{")
    import shutil

    shutil.rmtree(store.raw_dir("snapshot_pre_MX1_all.json"))

    report = upgrade_run(store, dry_run=True)

    post = next(item for item in report.items if item.file == "snapshot_post_PTX1_all.json")
    assert post.result != NOT_REGENERABLE
    assert any("nejde pregenerovat ani nacist" in note for note in post.notes)


def test_unknown_recorded_collector_is_dropped_with_note(tmp_path):
    store = build_run(tmp_path)
    raw = store.raw_dir("snapshot_pre_MX1_all.json") / "session.json"
    data = json.loads(raw.read_text())
    data["params"]["collectors"].append("gone_collector")
    raw.write_text(json.dumps(data))

    report = upgrade_run(store, dry_run=True)

    pre = next(item for item in report.items if item.file == "snapshot_pre_MX1_all.json")
    assert pre.result == UNCHANGED
    assert pre.notes == ["collector gone_collector v teto verzi neexistuje"]


def test_inventory_without_raw_is_listed(tmp_path):
    store = build_run(tmp_path)
    (store.dir / "inventory_OLD_all.yml").write_text("schema_version: 9\n")

    report = upgrade_run(store, dry_run=True)

    assert _results(report)["inventory_OLD_all.yml"] == (NOT_REGENERABLE, NO_RAW)


def test_config_without_needed_hierarchy_is_not_regenerable(tmp_path):
    store = build_run(tmp_path)
    raw = store.raw_dir("inventory_MX1_all.yml") / "session.json"
    data = json.loads(raw.read_text())
    data["calls"][0]["filter"].remove("protocols")
    raw.write_text(json.dumps(data))

    report = upgrade_run(store, dry_run=True)

    assert _results(report)["inventory_MX1_all.yml"] == (
        NOT_REGENERABLE, "konfigurace nema hierarchii protocols",
    )


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    before = _bytes(store)
    arp_with_note(monkeypatch)

    report = upgrade_run(store, dry_run=True, now=NOW)

    assert CHANGED in {item.result for item in report.items}
    assert report.backup is None
    assert _bytes(store) == before
    assert not (store.dir / "backup").exists()
    assert not (store.dir / ".upgrade-staging").exists()


def test_crash_during_replay_leaves_run_untouched(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    before = _bytes(store)
    arp_with_note(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("bug v nastroji")

    monkeypatch.setattr("migration_validator.raw.upgrade.capture_device", boom)
    report = upgrade_run(store, now=NOW)

    assert report.error == "replay selhal - RuntimeError: bug v nastroji"
    assert report.exit_code == 2
    assert _bytes(store) == before
    assert not (store.dir / "backup").exists()
    assert not (store.dir / ".upgrade-staging").exists()


def test_render_report_lines():
    report = UpgradeReport(run="mig01", dry_run=True, items=[
        UpgradeItem(kind="capture", file="snapshot_post_PTX1_all.json", phase="post",
                    device="PTX1", port=None, result=CHANGED),
        UpgradeItem(kind="capture", file="snapshot_pre_MX1_ge_0_0_2.json", phase="pre",
                    device="MX1", port="ge-0/0/2", result=NOT_REGENERABLE, reason=NO_RAW,
                    notes=["collector x v teto verzi neexistuje"]),
        UpgradeItem(kind="inventory", file="inventory_MX1_all.yml"),
    ])

    lines = render_report(report)

    assert lines[0] == "run mig01 (dry-run)"
    assert "post     PTX1 all" in lines[1]
    assert lines[1].endswith("pregenerovano - zmeneno")
    assert lines[2].endswith("nelze - bez raw zaznamu (zachyceno pred zavedenim)")
    assert lines[3] == "    ! collector x v teto verzi neexistuje"
    assert "inventory inventory_MX1_all.yml" in lines[4]
    assert lines[4].endswith("pregenerovano - beze zmeny")


def test_report_to_dict_shape():
    report = UpgradeReport(run="mig01", dry_run=False, backup="backup/x", items=[
        UpgradeItem(kind="inventory", file="inventory_MX1_all.yml"),
    ])
    assert report.to_dict() == {
        "run": "mig01", "dry_run": False, "backup": "backup/x", "error": None,
        "items": [{
            "kind": "inventory", "file": "inventory_MX1_all.yml", "phase": None,
            "device": None, "port": None, "result": "unchanged", "reason": None,
            "notes": [],
        }],
    }
