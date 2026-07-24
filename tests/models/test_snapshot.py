import json

import pytest

from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import (
    CaptureMeta,
    DeviceMeta,
    Snapshot,
    SnapshotVersionError,
    load_snapshot,
    save_snapshot,
)


def _snapshot() -> Snapshot:
    return Snapshot(
        device=DeviceMeta(address="172.20.20.4", hostname="MX1-POP1", platform="junos"),
        capture=CaptureMeta(
            started_at="2026-07-24T09:12:03Z",
            finished_at="2026-07-24T09:12:41Z",
            phase="pre-migration",
            collectors={
                "interfaces": {"status": "ok"},
                "evpn_esi": {"status": "error", "message": "RpcError: syntax error"},
            },
        ),
        facts={"interfaces": {"ge-0/0/2.113": {"oper_status": "up"}}},
        probes={"ping": []},
        scopes=[
            Scope(
                id="svc:L3VPN-CPE13-NNI:IPVPN",
                kind="service",
                key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
                selectors=Selectors(interfaces=["ge-0/0/2.113"]),
            )
        ],
    )


def test_round_trip_through_dict():
    snapshot = _snapshot()
    assert Snapshot.from_dict(snapshot.to_dict()) == snapshot


def test_save_and_load(tmp_path):
    path = tmp_path / "snap.json"
    save_snapshot(_snapshot(), path)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["schema_version"] == 1
    assert written["device"]["platform"] == "junos"

    assert load_snapshot(path) == _snapshot()


def test_failed_collectors_lists_only_errors():
    failed = _snapshot().capture.failed_collectors()
    assert failed == {"evpn_esi": "RpcError: syntax error"}


def test_load_rejects_other_schema_version(tmp_path):
    path = tmp_path / "future.json"
    payload = _snapshot().to_dict()
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SnapshotVersionError, match="99"):
        load_snapshot(path)


def test_snapshot_without_inventory_has_empty_ping():
    snapshot = Snapshot(
        device=DeviceMeta(address="1.2.3.4"),
        capture=CaptureMeta(started_at="2026-07-24T09:00:00Z"),
        facts={},
        probes={"ping": []},
    )
    assert snapshot.inventory is None
    assert snapshot.probes["ping"] == []
