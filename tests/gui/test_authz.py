"""Permission seam - dnes je kazdy anonymni admin, testy prepinaji roli
pres app.state.actor_provider."""

from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"}


def _client(tmp_path, role=None):
    api.create_run("mig01", old_device=OLD, new_device=NEW, run_root=tmp_path)
    app = create_app(run_root=tmp_path)
    if role is not None:
        app.state.actor_provider = lambda request: Actor(role=role)
    return TestClient(app)


def test_anonymni_je_admin_a_muze_zapisovat(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": [],
    })
    assert resp.status_code == 201


def test_viewer_cte_ale_nezapisuje(tmp_path):
    client = _client(tmp_path, role="viewer")
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/runs/mig01").status_code == 200
    resp = client.post("/api/runs", json={
        "name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": [],
    })
    assert resp.status_code == 403
    assert resp.json()["detail"] == "nedostatecne opravneni: vyzaduje operate"


def test_operator_zaklada_run(tmp_path):
    client = _client(tmp_path, role="operator")
    resp = client.post("/api/runs", json={
        "name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": [],
    })
    assert resp.status_code == 201


def test_neznama_role_nema_nic(tmp_path):
    client = _client(tmp_path, role="guest")
    assert client.get("/api/runs").status_code == 403
