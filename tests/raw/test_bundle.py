"""Raw bundle na disku (spec 2026-09-23, sekce 1)."""

import json

import pytest

from migration_validator import __version__
from migration_validator.raw.bundle import (
    RawFormatError,
    Session,
    has_session,
    read_session,
    tool_info,
    write_session,
)
from migration_validator.raw.calls import RecordedCall


def _session(**overrides):
    calls = [
        RecordedCall(
            seq=1, rpc="get_config", kwargs={"options": {"database": "committed"}},
            filter=["interfaces"], reply_xml=b"<configuration><interfaces/></configuration>",
        ),
        RecordedCall(
            seq=2, rpc="get_bgp_neighbor_information", kwargs={},
            error={"type": "RpcError", "message": "boom"},
        ),
        RecordedCall(seq=3, rpc="commit_check", kwargs={}, reply_value=True),
    ]
    fields = dict(
        kind="capture", address="172.20.20.4", hostname="172.20.20.4",
        facts={"hostname": "MX1", "model": "MX204", "version": "21.4R3"},
        calls=calls, started_at="2026-09-23T10:00:00Z",
        finished_at="2026-09-23T10:01:00Z",
        params={
            "phase": "pre", "port": None, "collectors": None, "ping_count": 5,
            "service_types": None, "profile_name": None,
        },
        inventory={"file": "inventory_MX1_all.yml", "raw": True},
        baselines=[], tool={"version": "0.1.0", "commit": None},
    )
    fields.update(overrides)
    return Session(**fields)


def test_round_trip(tmp_path):
    target = tmp_path / "raw" / "pre_MX1_all"
    write_session(_session(), target)

    assert read_session(target) == _session()
    assert (target / "0001-get_config.xml.gz").is_file()
    data = json.loads((target / "session.json").read_text())
    assert data["raw_format"] == 1
    assert data["calls"][1] == {
        "seq": 2, "rpc": "get_bgp_neighbor_information", "kwargs": {},
        "error": {"type": "RpcError", "message": "boom"},
    }
    assert data["calls"][2]["value"] is True


def test_write_replaces_old_session_completely(tmp_path):
    target = tmp_path / "raw" / "pre_MX1_all"
    write_session(_session(), target)
    (target / "stale.txt").write_text("x")

    write_session(_session(calls=[]), target)

    assert not (target / "stale.txt").exists()
    assert read_session(target).calls == []


def test_failed_write_leaves_no_old_session(tmp_path, monkeypatch):
    """Selhani zapisu nesmi nechat stary raw vedle noveho snimku - upgrade
    by jinak pres novy snapshot pregeneroval predchozi capture.
    Zabiji mutanta: write_session maze stary target az po uspesnem zapisu."""
    target = tmp_path / "raw" / "pre_MX1_all"
    write_session(_session(), target)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("migration_validator.raw.bundle.gzip.open", boom)
    with pytest.raises(OSError, match="disk full"):
        write_session(_session(), target)

    assert not target.exists()
    assert [p.name for p in target.parent.iterdir()] == []


def test_copies_inventory_raw_into_capture_bundle(tmp_path):
    inventory_raw = tmp_path / "raw" / "inventory_MX1_ge_0_0_2"
    write_session(
        _session(kind="inventory", params=None, inventory=None, baselines=None,
                 port_filter="ge-0/0/2"),
        inventory_raw,
    )
    target = tmp_path / "raw" / "pre_MX1_ge_0_0_2"
    write_session(_session(), target, inventory_raw=inventory_raw)

    assert read_session(target / "inventory").port_filter == "ge-0/0/2"


def test_copies_inventory_yaml_when_inventory_has_no_raw(tmp_path):
    yaml_path = tmp_path / "inv.yml"
    yaml_path.write_text("schema_version: 10\n")
    target = tmp_path / "raw" / "pre_MX1_all"

    write_session(
        _session(inventory={"file": "inv.yml", "raw": False}), target,
        inventory_yaml=yaml_path,
    )

    assert (target / "inventory" / "inventory.yml").read_text() == "schema_version: 10\n"


def test_unknown_raw_format_is_rejected(tmp_path):
    target = tmp_path / "raw" / "pre_MX1_all"
    write_session(_session(), target)
    data = json.loads((target / "session.json").read_text())
    data["raw_format"] = 99
    (target / "session.json").write_text(json.dumps(data))

    with pytest.raises(RawFormatError, match="raw_format 99"):
        read_session(target)


def test_corrupt_session_is_raw_format_error(tmp_path):
    target = tmp_path / "raw" / "pre_MX1_all"
    target.mkdir(parents=True)
    (target / "session.json").write_text("{")

    with pytest.raises(RawFormatError):
        read_session(target)


def test_has_session(tmp_path):
    target = tmp_path / "raw" / "pre_MX1_all"
    assert not has_session(target)
    write_session(_session(), target)
    assert has_session(target)


def test_tool_info_has_version_and_commit_key():
    info = tool_info()
    assert info["version"] == __version__
    assert "commit" in info
