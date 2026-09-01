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


def test_root_servuje_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "mig-validate" in resp.text
