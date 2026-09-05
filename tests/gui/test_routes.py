"""Testy GUI routes - tenke obaly nad migration_validator.api."""


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


def test_run_detail(client):
    data = client.get("/api/runs/mig01").json()
    assert data["name"] == "mig01"
    assert data["rows"][0]["old"]["port"] == "ge-0/0/1"
    assert data["snapshots"] == []


def test_run_detail_404(client):
    assert client.get("/api/runs/neni").status_code == 404


def test_meta_vraci_profile_a_auth(client):
    data = client.get("/api/meta").json()
    assert "profile" in data
    assert isinstance(data["auth"], str) and data["auth"]
    assert isinstance(data["collectors"]["junos"], list) and data["collectors"]["junos"]


def test_root_servuje_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "mig-validate" in resp.text


def test_list_runs_preskoci_teckovane_adresare(tmp_path):
    from migration_validator import api
    from migration_validator.gui.app import create_app
    from fastapi.testclient import TestClient
    OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
    NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}
    api.create_run("mig01", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    api.archive_run("mig01", run_root=tmp_path)
    api.create_run("mig02", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    client = TestClient(create_app(run_root=tmp_path))
    names = [r["name"] for r in client.get("/api/runs").json()["runs"]]
    assert names == ["mig02"]
