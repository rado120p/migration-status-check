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
