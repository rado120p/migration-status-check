"""Testy GUI routes - tenke obaly nad migration_validator.api."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.gui.app import create_app

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"}


@pytest.fixture
def client(tmp_path):
    api.create_run(
        "mig01", old_device=OLD, new_device=NEW,
        mappings=[("ge-0/0/1", "et-0/0/1")], run_root=tmp_path,
    )
    app = create_app(run_root=tmp_path)
    return TestClient(app)


def test_checks_vraci_registrovane_checky(client):
    data = client.get("/api/checks").json()
    assert isinstance(data["checks"], list)
    assert {"id", "mode", "default_severity", "service_types"} <= set(
        data["checks"][0]
    )


def test_runs_vypise_run(client):
    data = client.get("/api/runs").json()
    assert [r["name"] for r in data["runs"]] == ["mig01"]
    run = data["runs"][0]
    assert run["devices"]["MX1"]["role"] == "old"
    assert run["mapped_ports"] == 1
    assert run["snapshots"] == 0


def test_runs_ignoruje_adresar_bez_manifestu(client, tmp_path):
    (tmp_path / "smeti").mkdir()
    data = client.get("/api/runs").json()
    assert [r["name"] for r in data["runs"]] == ["mig01"]
