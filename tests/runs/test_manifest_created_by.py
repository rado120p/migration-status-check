"""created_by v run.yml (spec 2026-09-25 - user roles vlna A)."""

from migration_validator.runs.manifest import RunDevice, RunManifest, load_manifest, save_manifest


def _manifest(**kw):
    return RunManifest(
        devices={"MX1": RunDevice(host="10.0.0.1", platform="junos", role="single")},
        kind="single", **kw,
    )


def test_created_by_round_trip(tmp_path):
    path = tmp_path / "run.yml"
    save_manifest(_manifest(created_by="rado"), path)
    assert "created_by: rado" in path.read_text()
    assert load_manifest(path).created_by == "rado"


def test_created_by_absent_not_written(tmp_path):
    path = tmp_path / "run.yml"
    save_manifest(_manifest(), path)
    assert "created_by" not in path.read_text()
    assert load_manifest(path).created_by is None
