import json

import pytest

from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import (
    SCHEMA_VERSION,
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
    assert written["schema_version"] == SCHEMA_VERSION
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


def test_snapshot_rejects_previous_schema_version():
    """Stary snimek se musi odmitnout hlasite, ne precist s prazdnymi klici.

    Vlna 8 pridala do Selectors klic bgp_neighbors_inactive. Snapshot scopy
    vnoruje, takze stary snimek by ho precetl jako prazdny a deaktivovany
    peer by se tise stal aktivnim - prave ta vada, kterou vlna opravuje.
    """
    with pytest.raises(SnapshotVersionError):
        Snapshot.from_dict({"schema_version": 4})


def test_snapshot_without_inventory_has_empty_ping():
    snapshot = Snapshot(
        device=DeviceMeta(address="1.2.3.4"),
        capture=CaptureMeta(started_at="2026-07-24T09:00:00Z"),
        facts={},
        probes={"ping": []},
    )
    assert snapshot.inventory is None
    assert snapshot.probes["ping"] == []


def test_snapshot_version_is_eight():
    assert SCHEMA_VERSION == 8


def test_old_snapshot_fails_loudly():
    with pytest.raises(SnapshotVersionError, match="schema_version"):
        Snapshot.from_dict({"schema_version": 1, "device": {"address": "x"}})


def test_version_two_snapshot_is_rejected():
    """Stary snimek nema oblasti routes a bfd.

    Kontrolou verze by prosel, ale failed_collectors by je nevypsalo -
    collector neselhal, on vubec nebezel. bfd_session_state by pak videl
    zamer z inventare, nula session, BGP Established a dal FAIL na kazde
    sluzbe. Falesny poplach, ne ticha zelen.
    """
    with pytest.raises(SnapshotVersionError, match="schema_version 2"):
        Snapshot.from_dict({"schema_version": 2, "device": {}, "capture": {}})
