from migration_validator.gui.app import create_app
from fastapi.testclient import TestClient

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}
SINGLE = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "single"}


def _migration(name="mig02", mappings=None):
    return {"name": name, "kind": "migration", "profile": None,
            "devices": [OLD, NEW], "mappings": mappings or []}


def _client(tmp_path):
    return TestClient(create_app(run_root=tmp_path))


def test_post_runs_zalozi_run(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json=_migration(mappings=[["ge-0/0/1", "et-0/0/1"]]))
    assert resp.status_code == 201
    assert (tmp_path / "mig02" / "run.yml").exists()
    assert resp.json()["rows"][0]["old"]["port"] == "ge-0/0/1"


def test_post_runs_duplicitni_jmeno_je_409(tmp_path):
    client = _client(tmp_path)
    body = _migration()
    assert client.post("/api/runs", json=body).status_code == 201
    resp = client.post("/api/runs", json=body)
    assert resp.status_code == 409
    assert "existuje" in resp.json()["detail"]


def test_put_mapping_prepise_pairingy(tmp_path):
    client = _client(tmp_path)
    client.post("/api/runs", json=_migration(mappings=[["ge-0/0/1", "et-0/0/1"]]))
    resp = client.put("/api/runs/mig02/mapping", json={
        "mappings": [["ge-0/0/1", "et-0/0/1"], ["ge-0/0/2", "ae0"]],
    })
    assert resp.status_code == 200
    assert len(resp.json()["rows"]) == 2


def test_put_mapping_zamek_je_409(tmp_path):
    # zamceny pairing (se snimkem) nejde odebrat - server vraci 409
    from migration_validator.runs.manifest import (
        CaptureRecord, load_manifest, save_manifest,
    )
    client = _client(tmp_path)
    client.post("/api/runs", json=_migration(mappings=[["ge-0/0/1", "et-0/0/1"]]))
    path = tmp_path / "mig02" / "run.yml"
    manifest = load_manifest(path)
    manifest.record_capture(CaptureRecord(
        phase="pre", device="MX1", port="ge-0/0/1", snapshot="s.json", taken="t",
    ))
    save_manifest(manifest, path)
    resp = client.put("/api/runs/mig02/mapping", json={"mappings": []})
    assert resp.status_code == 409
    assert "ma snimky" in resp.json()["detail"]


def test_post_archive_presune_run_a_zmizi_ze_seznamu(tmp_path):
    client = _client(tmp_path)
    client.post("/api/runs", json=_migration())
    resp = client.post("/api/runs/mig02/archive")
    assert resp.status_code == 200
    archived = resp.json()["archived_to"]
    assert archived.startswith("mig02-")
    assert (tmp_path / ".archive" / archived / "run.yml").exists()
    assert not (tmp_path / "mig02").exists()
    assert client.get("/api/runs").json()["runs"] == []


def test_post_archive_podruhe_je_404(tmp_path):
    client = _client(tmp_path)
    client.post("/api/runs", json=_migration())
    assert client.post("/api/runs/mig02/archive").status_code == 200
    resp = client.post("/api/runs/mig02/archive")
    assert resp.status_code == 404
    assert "neexistuje" in resp.json()["detail"]


def test_post_archive_nevalidni_jmeno_je_404(tmp_path):
    client = _client(tmp_path)
    assert client.post("/api/runs/Mig.02/archive").status_code == 404


def test_post_archive_s_bezicim_capture_je_409(tmp_path):
    from migration_validator.gui.captures import CaptureTask
    app = create_app(run_root=tmp_path)
    client = TestClient(app)
    client.post("/api/runs", json=_migration())
    manager = app.state.captures
    manager._tasks["fake"] = CaptureTask(
        id="fake", run="mig02", device="MX1", port=None, phase="pre",
    )
    resp = client.post("/api/runs/mig02/archive")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "run ma bezici capture"
    assert (tmp_path / "mig02" / "run.yml").exists()


def test_post_archive_vyzaduje_admina(tmp_path):
    from migration_validator.gui.authz import Actor
    app = create_app(run_root=tmp_path)
    client = TestClient(app)
    client.post("/api/runs", json=_migration())
    app.state.actor_provider = lambda request: Actor(role="operator")
    resp = client.post("/api/runs/mig02/archive")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "nedostatecne opravneni: vyzaduje admin"


def test_post_runs_single(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "upg01", "kind": "single", "profile": None,
        "devices": [SINGLE], "mappings": [],
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "single"
    assert body["profile"] is None
    assert body["devices"]["PTX1"]["role"] == "single"
    assert body["rows"] == []


def test_post_runs_vraci_kind_migration(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json=_migration())
    assert resp.status_code == 201
    assert resp.json()["kind"] == "migration"


def test_post_runs_single_se_dvema_zarizenimi_je_409(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "upg01", "kind": "single", "profile": None,
        "devices": [SINGLE, {**OLD, "role": "single"}], "mappings": [],
    })
    assert resp.status_code == 409
    assert "run typu single" in resp.json()["detail"]


def test_post_runs_migration_s_jednim_zarizenim_je_409(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "mig02", "kind": "migration", "profile": None,
        "devices": [OLD], "mappings": [],
    })
    assert resp.status_code == 409
    assert "role 'old' a jedno role 'new'" in resp.json()["detail"]


def test_post_runs_neznamy_kind_je_409(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "b", "kind": "bulk", "profile": None, "devices": [SINGLE], "mappings": [],
    })
    assert resp.status_code == 409
    assert "neznamy kind 'bulk'" in resp.json()["detail"]


def test_post_runs_profil_je_zatim_409(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "p", "kind": "single", "profile": "core-only",
        "devices": [SINGLE], "mappings": [],
    })
    assert resp.status_code == 409
    assert "profil 'core-only' neexistuje" in resp.json()["detail"]


def test_put_mapping_na_single_je_409(tmp_path):
    client = _client(tmp_path)
    client.post("/api/runs", json={
        "name": "upg01", "kind": "single", "profile": None,
        "devices": [SINGLE], "mappings": [],
    })
    resp = client.put("/api/runs/upg01/mapping", json={"mappings": [["ge-0/0/1", "et-0/0/1"]]})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "run typu single nema interface mapping"


def test_post_runs_vyzaduje_operate(tmp_path):
    from migration_validator.gui.authz import Actor
    app = create_app(run_root=tmp_path)
    client = TestClient(app)
    app.state.actor_provider = lambda request: Actor(role="viewer")
    resp = client.post("/api/runs", json=_migration())
    assert resp.status_code == 403
    assert resp.json()["detail"] == "nedostatecne opravneni: vyzaduje operate"
    assert not (tmp_path / "mig02").exists()
