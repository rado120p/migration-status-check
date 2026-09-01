from migration_validator.gui.serializers import snapshot_list, status_rows
from migration_validator.runs.manifest import (
    CaptureRecord, InterfaceMapping, MappingEndpoint, RunDevice, RunManifest,
)


def _manifest():
    return RunManifest(
        devices={
            "MX1": RunDevice(host="10.0.0.1", platform="junos", role="old"),
            "PTX1": RunDevice(host="10.0.0.2", platform="junos-evo", role="new"),
        },
        interface_mapping=[
            InterfaceMapping(
                old=MappingEndpoint(node="MX1", port="ge-0/0/1"),
                new=MappingEndpoint(node="PTX1", port="et-0/0/1"),
            )
        ],
    )


def test_radek_z_mappingu_bez_snimku():
    rows = status_rows(_manifest())
    assert rows == [{
        "old": {"node": "MX1", "port": "ge-0/0/1"},
        "new": {"node": "PTX1", "port": "et-0/0/1"},
        "pre": False, "post": False, "rollback": False,
    }]


def test_pre_snimek_zapne_pre():
    manifest = _manifest()
    manifest.record_capture(CaptureRecord(
        phase="pre", device="MX1", port="ge-0/0/1",
        snapshot="s.json", taken="t",
    ))
    assert status_rows(manifest)[0]["pre"] is True


def test_all_capture_dostane_vlastni_radek():
    manifest = _manifest()
    manifest.record_capture(CaptureRecord(
        phase="pre", device="MX1", port=None, snapshot="s.json", taken="t",
    ))
    rows = status_rows(manifest)
    assert len(rows) == 2
    assert rows[1]["old"] == {"node": "MX1", "port": None}
    assert rows[1]["new"] is None
    assert rows[1]["pre"] is True


def test_snapshot_list_mapuje_capture_record():
    manifest = _manifest()
    manifest.record_capture(CaptureRecord(
        phase="pre", device="MX1", port="ge-0/0/1",
        snapshot="snapshot_pre_MX1_ge_0_0_1.json", taken="2026-09-01T00:00:00Z",
    ))
    assert snapshot_list(manifest) == [{
        "file": "snapshot_pre_MX1_ge_0_0_1.json",
        "phase": "pre",
        "device": "MX1",
        "port": "ge-0/0/1",
        "taken": "2026-09-01T00:00:00Z",
    }]
