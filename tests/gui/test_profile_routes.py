"""/api/profiles - store CRUD, katalog, used_by, opravneni."""

from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.config import DEFAULTS, PING_COUNT_DEFAULT
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor
from migration_validator.profiles.catalogue import build_catalogue
from migration_validator.profiles.store import ProfileStore, empty_document

SINGLE = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "single"}


def _client(tmp_path, role=None, profile_path=None):
    app = create_app(
        run_root=tmp_path / "runs", profile_path=profile_path,
        profiles_root=tmp_path / "profiles",
    )
    if role is not None:
        app.state.actor_provider = lambda request: Actor(role=role)
    return TestClient(app)


# -- katalog ----------------------------------------------------------------

def test_katalog_ma_kazdy_check_jednou_s_default_severity():
    catalogue = build_catalogue()
    ids = [c["id"] for c in catalogue["checks"]]
    assert len(ids) == len(set(ids))
    assert ids == [c["id"] for c in api.list_checks()]  # poradi registru
    by_id = {c["id"]: c for c in catalogue["checks"]}
    assert set(by_id) == {c["id"] for c in api.list_checks()}
    for described in api.list_checks():
        entry = by_id[described["id"]]
        assert entry["default_severity"] == described["default_severity"]
        assert entry["title"] == described["title"]
        assert entry["group"] == (described["requires"][0] if described["requires"] else "general")


def test_katalog_options_z_defaults_bez_severity_a_enabled():
    by_id = {c["id"]: c for c in build_catalogue()["checks"]}
    assert by_id["interface_traffic"]["options"] == {
        "tolerance_percent": {"type": "number", "default": -60},
        "require_nonzero": {"type": "boolean", "default": True},
    }
    assert by_id["interface_optics_levels"]["options"]["tolerance_db"] == {
        "type": "number", "default": 2.0,
    }
    assert by_id["traffic_ceased"]["options"] == {
        "max_residual_pps": {"type": "number", "default": 1},
    }
    assert by_id["traffic_ceased"]["default_enabled"] is False
    assert by_id["interface_state"]["default_enabled"] is True
    assert by_id["interface_state"]["options"] == {}
    assert by_id["deactivation_state"]["group"] == "general"


def test_kazdy_klic_v_defaults_je_registrovany_check():
    ids = {c["id"] for c in api.list_checks()}
    assert set(DEFAULTS) <= ids


def test_katalog_collectors_service_types_ping():
    catalogue = build_catalogue()
    assert "interfaces" in catalogue["collectors"]["junos"]
    assert "interfaces" in catalogue["collectors"]["junos-evo"]
    assert catalogue["service_types"] == ["Core", "E-LAN", "E-Line", "IPVPN", "Internet"]
    assert catalogue["ping_count_default"] == PING_COUNT_DEFAULT == 5


def test_get_catalogue_route(tmp_path):
    client = _client(tmp_path, role="viewer")
    resp = client.get("/api/profiles/catalogue")
    assert resp.status_code == 200
    assert resp.json()["checks"][0]["id"] == api.list_checks()[0]["id"]


# -- CRUD -------------------------------------------------------------------

def _doc(**profile):
    doc = empty_document()
    doc["profile"].update(profile)
    return doc


def test_list_prazdny_store_a_default(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/profiles").json() == {
        "default": None, "default_document": empty_document(), "profiles": [],
    }


def test_list_default_z_profile_flagu(tmp_path):
    default = tmp_path / "core.yml"
    default.write_text("profile:\n  ping_count: 3\n", encoding="utf-8")
    client = _client(tmp_path, profile_path=str(default))
    body = client.get("/api/profiles").json()
    assert body["default"] == "core.yml"
    assert body["default_document"]["profile"]["ping_count"] == 3


def test_post_get_put_delete(tmp_path):
    client = _client(tmp_path)
    doc = _doc(collectors=["interfaces", "bgp"], ping_count=3)
    resp = client.post("/api/profiles", json={"name": "core-only", "document": doc})
    assert resp.status_code == 201
    assert resp.json() == {"name": "core-only", "document": doc}
    assert (tmp_path / "profiles" / "core-only.yml").is_file()

    assert client.get("/api/profiles/core-only").json() == {"name": "core-only", "document": doc}
    assert client.get("/api/profiles").json()["profiles"] == [{"name": "core-only", "used_by": 0}]

    doc2 = _doc(service_types=["IPVPN"])
    doc2["checks"] = {"interface_optics_levels": {"enabled": False}}
    resp = client.put("/api/profiles/core-only", json={"document": doc2})
    assert resp.status_code == 200
    assert resp.json()["document"] == doc2
    assert client.get("/api/profiles/core-only").json()["document"] == doc2

    assert client.delete("/api/profiles/core-only").status_code == 204
    assert client.get("/api/profiles/core-only").status_code == 404
    assert client.get("/api/profiles").json()["profiles"] == []


def test_post_existujici_je_409(tmp_path):
    client = _client(tmp_path)
    client.post("/api/profiles", json={"name": "a", "document": empty_document()})
    resp = client.post("/api/profiles", json={"name": "a", "document": empty_document()})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "profil 'a' uz existuje"


def test_post_nevalidni_jmeno_je_422(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/profiles", json={"name": "Core Only", "document": empty_document()})
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("nevalidni jmeno profilu 'Core Only'")


def test_post_neznamy_collector_je_422_s_hlaskou_loaderu(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/profiles", json={"name": "x", "document": _doc(collectors=["iface"])})
    assert resp.status_code == 422
    assert "neznamy collector 'iface'" in resp.json()["detail"]
    assert not (tmp_path / "profiles" / "x.yml").exists()


def test_put_neexistujici_je_404(tmp_path):
    client = _client(tmp_path)
    resp = client.put("/api/profiles/neni", json={"document": empty_document()})
    assert resp.status_code == 404
    assert resp.json()["detail"].startswith("profil 'neni' neexistuje")
    assert client.delete("/api/profiles/neni").status_code == 404


def test_used_by_a_delete_odmitnut_dokud_je_pouzity(tmp_path):
    client = _client(tmp_path)
    client.post("/api/profiles", json={"name": "core-only", "document": empty_document()})
    for run in ("a", "b"):
        api.create_run(
            run, kind="single", devices=[{**SINGLE, "node": run}], profile="core-only",
            run_root=tmp_path / "runs", profiles_root=tmp_path / "profiles",
        )
    assert client.get("/api/profiles").json()["profiles"] == [{"name": "core-only", "used_by": 2}]
    resp = client.delete("/api/profiles/core-only")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "profil pouziva 2 runu"
    assert (tmp_path / "profiles" / "core-only.yml").is_file()


def test_preview_je_shodny_se_souborem(tmp_path):
    client = _client(tmp_path)
    doc = _doc(collectors=["interfaces"], ping_count=None)
    doc["checks"] = {"interface_traffic": {"tolerance_percent": -40}}
    preview = client.post("/api/profiles/preview", json={"document": doc}).json()["yaml"]
    client.post("/api/profiles", json={"name": "p", "document": doc})
    assert preview == (tmp_path / "profiles" / "p.yml").read_text(encoding="utf-8")
    assert preview == "profile:\n  collectors:\n  - interfaces\nchecks:\n  interface_traffic:\n    tolerance_percent: -40\n"


def test_get_rozbity_soubor_je_422(tmp_path):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "bad.yml").write_text("cheks: {}\n", encoding="utf-8")
    client = _client(tmp_path)
    resp = client.get("/api/profiles/bad")
    assert resp.status_code == 422
    assert "neznamy klic cheks" in resp.json()["detail"]


def test_viewer_cte_ale_nezapisuje(tmp_path):
    admin = _client(tmp_path)
    admin.post("/api/profiles", json={"name": "a", "document": empty_document()})
    viewer = _client(tmp_path, role="viewer")
    assert viewer.get("/api/profiles").status_code == 200
    assert viewer.get("/api/profiles/a").status_code == 200
    assert viewer.get("/api/profiles/catalogue").status_code == 200
    for resp in (
        viewer.post("/api/profiles", json={"name": "b", "document": empty_document()}),
        viewer.put("/api/profiles/a", json={"document": empty_document()}),
        viewer.delete("/api/profiles/a"),
        viewer.post("/api/profiles/preview", json={"document": empty_document()}),
    ):
        assert resp.status_code == 403
        assert resp.json()["detail"] == "nedostatecne opravneni: vyzaduje admin"


def test_operator_take_nezapisuje(tmp_path):
    operator = _client(tmp_path, role="operator")
    resp = operator.post("/api/profiles", json={"name": "b", "document": empty_document()})
    assert resp.status_code == 403
