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


def test_runs_nesou_kind_profile_a_created(client):
    run = client.get("/api/runs").json()["runs"][0]
    assert run["kind"] == "migration"
    assert run["profile"] is None
    assert run["created"].endswith("Z")
    assert run["created"].startswith("20")


def test_run_detail_nese_kind_a_profile(client):
    data = client.get("/api/runs/mig01").json()
    assert data["kind"] == "migration"
    assert data["profile"] is None


def test_runs_single_run_ma_kind_single(client, tmp_path):
    from migration_validator import api
    api.create_run(
        "upg01", kind="single",
        devices=[{"node": "PTX9", "host": "10.0.0.9", "platform": "junos-evo", "role": "single"}],
        run_root=tmp_path,
    )
    runs = {r["name"]: r for r in client.get("/api/runs").json()["runs"]}
    assert runs["upg01"]["kind"] == "single"
    assert runs["mig01"]["kind"] == "migration"


def test_runs_bez_skupiny_maji_group_null(client):
    assert client.get("/api/runs").json()["runs"][0]["group"] is None
    assert client.get("/api/runs/mig01").json()["group"] is None
