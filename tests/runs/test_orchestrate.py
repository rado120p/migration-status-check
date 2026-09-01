"""capture_into_run - spolecna orchestrace pro CLI a GUI."""

import pytest

from migration_validator.config import default_profile
from migration_validator.connection.junos import ConnectionOptions
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot
from migration_validator.runs.manifest import load_manifest
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
