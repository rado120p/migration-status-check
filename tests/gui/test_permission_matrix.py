"""Pins the spec's permission matrix: every /api route x every role."""

import pytest
from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}

# (method, path, minimum role). An allowed role may get 404/409/422 from the
# thin fixture; only 401/403 are asserted.
MATRIX = [
    ("GET", "/api/checks", "viewer"),
    ("GET", "/api/meta", "viewer"),
    ("GET", "/api/runs", "viewer"),
    ("GET", "/api/runs/mig01", "viewer"),
    ("GET", "/api/runs/mig01/evaluation", "viewer"),
    ("GET", "/api/runs/mig01/snapshots/x.json/evaluation", "viewer"),
    ("GET", "/api/captures/nope", "viewer"),
    ("GET", "/api/profiles", "viewer"),
    ("GET", "/api/profiles/catalogue", "viewer"),
    ("GET", "/api/profiles/p1", "viewer"),
    ("GET", "/api/groups", "viewer"),
    ("GET", "/api/groups/g1", "viewer"),
    ("POST", "/api/runs", "operator"),
    ("PUT", "/api/runs/mig01/mapping", "operator"),
    ("POST", "/api/runs/mig01/archive", "operator"),
    ("POST", "/api/runs/mig01/upgrade", "operator"),
    ("POST", "/api/captures", "operator"),
    ("POST", "/api/groups", "operator"),
    ("POST", "/api/groups/g1/devices", "operator"),
    ("POST", "/api/groups/g1/captures", "operator"),
    ("POST", "/api/groups/g1/archive", "operator"),
    ("POST", "/api/groups/g1/upgrade", "operator"),
    ("POST", "/api/profiles/preview", "operator"),
    ("POST", "/api/profiles", "operator"),
    ("PUT", "/api/profiles/p1", "operator"),
    ("DELETE", "/api/profiles/p1", "operator"),
]

BODIES = {
    ("POST", "/api/runs"): {"name": "zz", "kind": "migration", "devices": [], "mappings": []},
    ("PUT", "/api/runs/mig01/mapping"): {"mappings": []},
    ("POST", "/api/captures"): {"run": "nope", "device": "MX1", "phase": "pre"},
    ("POST", "/api/groups"): {"group": "zz", "devices": []},
    ("POST", "/api/groups/g1/devices"): {"devices": []},
    ("POST", "/api/groups/g1/captures"): {"phase": "nope"},
    ("POST", "/api/profiles/preview"): {"document": {}},
    ("POST", "/api/profiles"): {"name": "zz", "document": {}},
    ("PUT", "/api/profiles/p1"): {"document": {}},
}

RANK = {"viewer": 0, "operator": 1, "admin": 2}
PUBLIC = {("POST", "/api/login"), ("POST", "/api/logout")}


def _client(tmp_path, actor):
    api.create_run("mig01", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    app = create_app(run_root=tmp_path, profiles_root=tmp_path / "profiles")
    app.state.actor_provider = lambda request: actor
    # An allowed role may hit a route that fails on the thin fixture (e.g.
    # upgrade without raw records); only 401/403 matter here.
    return TestClient(app, raise_server_exceptions=False), app


def _call(client, method, path):
    return client.request(method, path, json=BODIES.get((method, path)))


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
@pytest.mark.parametrize("method, path, minimum", MATRIX)
def test_matrix(tmp_path, role, method, path, minimum):
    client, _ = _client(tmp_path, Actor(role=role, username="u"))
    status = _call(client, method, path).status_code
    if RANK[role] >= RANK[minimum]:
        assert status not in (401, 403), f"{role} {method} {path} -> {status}"
    else:
        assert status == 403, f"{role} {method} {path} -> {status}"


@pytest.mark.parametrize("method, path, minimum", MATRIX)
def test_no_session_is_401(tmp_path, method, path, minimum):
    client, _ = _client(tmp_path, None)
    assert _call(client, method, path).status_code == 401


def test_matrix_covers_every_api_route(tmp_path):
    _, app = _client(tmp_path, Actor(role="admin"))
    covered = {(m, p) for m, p, _ in MATRIX} | PUBLIC
    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        for method in getattr(route, "methods", None) or []:
            if method == "HEAD":
                continue
            if not any(m == method and _matches(route.path, p) for m, p in covered):
                missing.append(f"{method} {path}")
    assert not missing, f"routes missing from MATRIX: {missing}"


def _matches(template: str, concrete: str) -> bool:
    t, c = template.strip("/").split("/"), concrete.strip("/").split("/")
    return len(t) == len(c) and all(a == b or a.startswith("{") for a, b in zip(t, c))
