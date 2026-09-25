import logging

import pytest
from fastapi.testclient import TestClient

from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor
from migration_validator.hostname_filter import FilterStore
from migration_validator.inventory import InventorySource

HOSTS = "\n".join(
    [f"MX-POP{i} ansible_host=10.0.1.{i}" for i in range(1, 61)]
    + ["PTX-POP1 ansible_host=10.0.2.1", "CORE1 ansible_host=10.0.3.1", "BROKEN"]
) + "\n"


def _client(tmp_path, role="admin", hosts=HOSTS, inventory=True):
    path = tmp_path / "hosts"
    path.write_text(hosts)
    app = create_app(
        run_root=tmp_path / "runs", profiles_root=tmp_path / "profiles",
        inventory=InventorySource(path) if inventory else None,
        hostname_filter=FilterStore(tmp_path / "hostname_filter.yml"),
    )
    app.state.actor_provider = lambda request: Actor(role=role, username="u")
    return TestClient(app), tmp_path


def test_search_limit_total_and_sort(tmp_path):
    client, _ = _client(tmp_path)
    body = client.get("/api/inventory").json()
    assert body["enabled"] is True and body["error"] is None
    assert body["total"] == 62
    assert len(body["items"]) == 50
    assert body["items"][0] == {"node": "CORE1", "host": "10.0.3.1"}


def test_search_substring_case_insensitive(tmp_path):
    client, _ = _client(tmp_path)
    body = client.get("/api/inventory", params={"q": "ptx"}).json()
    assert body["items"] == [{"node": "PTX-POP1", "host": "10.0.2.1"}]
    assert body["total"] == 1


def test_filter_narrows_search(tmp_path):
    client, root = _client(tmp_path)
    assert client.put("/api/inventory/filter", json={"allow": ["PTX-*", "CORE*"]}).status_code == 200
    body = client.get("/api/inventory").json()
    assert [i["node"] for i in body["items"]] == ["CORE1", "PTX-POP1"]


def test_filter_get_shape_and_warnings(tmp_path):
    client, _ = _client(tmp_path)
    body = client.get("/api/inventory/filter").json()
    assert body == {"allow": [], "visible": 62, "total": 62,
                    "warnings": ["line 63: BROKEN has no ansible_host"],
                    "enabled": True, "error": None}


def test_filter_put_normalizes_and_counts(tmp_path, caplog):
    client, root = _client(tmp_path)
    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        body = client.put("/api/inventory/filter", json={"allow": [" MX-* ", "MX-*"]}).json()
    assert body["allow"] == ["MX-*"] and body["visible"] == 60 and body["total"] == 62
    assert FilterStore(root / "hostname_filter.yml").load() == ["MX-*"]
    assert "action=filter-edit" in caplog.text


def test_filter_put_dry_run_does_not_save_or_audit(tmp_path, caplog):
    client, root = _client(tmp_path)
    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        body = client.put("/api/inventory/filter", params={"dry_run": "true"}, json={"allow": ["CORE*"]}).json()
    assert body["visible"] == 1 and body["allow"] == ["CORE*"]
    assert not (root / "hostname_filter.yml").exists()
    assert "filter-edit" not in caplog.text


@pytest.mark.parametrize("allow", [[""], ["MX-*", "  "]])
def test_filter_put_rejects_empty_entry(tmp_path, allow):
    client, _ = _client(tmp_path)
    assert client.put("/api/inventory/filter", json={"allow": allow}).status_code == 422


@pytest.mark.parametrize("role", ["viewer", "operator"])
@pytest.mark.parametrize("dry_run", ["false", "true"])
def test_filter_put_is_admin_only(tmp_path, role, dry_run):
    client, _ = _client(tmp_path, role=role)
    resp = client.put("/api/inventory/filter", params={"dry_run": dry_run}, json={"allow": ["MX-*"]})
    assert resp.status_code == 403


def test_inventory_disabled(tmp_path):
    client, _ = _client(tmp_path, inventory=False)
    assert client.get("/api/inventory").json() == {"items": [], "total": 0, "enabled": False, "error": None}
    body = client.get("/api/inventory/filter").json()
    assert body["enabled"] is False and body["total"] == 0 and body["visible"] == 0


def test_inventory_unreadable_is_error_state(tmp_path):
    client, root = _client(tmp_path)
    (root / "hosts").unlink()
    body = client.get("/api/inventory").json()
    assert body["enabled"] is True and body["items"] == [] and "hosts" in body["error"]


def test_malformed_filter_file_is_error_state(tmp_path):
    client, root = _client(tmp_path)
    (root / "hostname_filter.yml").write_text("allow: MX-*\n")
    search = client.get("/api/inventory").json()
    assert search["items"] == [] and "hostname_filter.yml" in search["error"]
    state = client.get("/api/inventory/filter").json()
    assert "hostname_filter.yml" in state["error"]
    # saving a valid filter repairs it
    assert client.put("/api/inventory/filter", json={"allow": ["MX-*"]}).status_code == 200
    assert client.get("/api/inventory").json()["error"] is None
