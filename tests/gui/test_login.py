import pytest
from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator import users as users_mod
from migration_validator.gui.app import create_app
from migration_validator.gui.sessions import SESSION_COOKIE, LoginThrottle, SessionStore
from migration_validator.users import User, UserStore, hash_password

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}
PW = "correct horse battery"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(users_mod, "PASSWORD_ITERATIONS", 1_000)
    users_mod.dummy_hash.cache_clear()
    store = UserStore(tmp_path / "users.yml")
    store.save({
        "rado": User("rado", "admin", hash_password(PW)),
        "eva": User("eva", "viewer", hash_password(PW)),
    })
    api.create_run("mig01", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    clock = Clock()
    app = create_app(
        run_root=tmp_path, profiles_root=tmp_path / "profiles", users=store,
        sessions=SessionStore(clock=clock), throttle=LoginThrottle(clock=clock),
    )
    # base_url https so the Secure cookie is sent back by the test client
    return TestClient(app, base_url="https://testserver"), store, clock


def _login(client, username="rado", password=PW):
    return client.post("/api/login", json={"username": username, "password": password})


def test_login_sets_secure_cookie_and_me(env):
    client, _, _ = env
    resp = _login(client)
    assert resp.status_code == 200
    assert resp.json() == {"username": "rado", "role": "admin"}
    cookie = resp.headers["set-cookie"]
    for flag in ("mig_session=", "HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert flag.lower() in cookie.lower()
    assert "max-age" not in cookie.lower()
    me = client.get("/api/me").json()
    assert me == {"username": "rado", "role": "admin",
                  "permissions": ["view", "operate", "admin"], "auth": True}


def test_wrong_password_and_unknown_user_look_identical(env):
    client, _, _ = env
    a = _login(client, password="wrong password!!")
    b = _login(client, username="nobody")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": "invalid username or password"}
    assert "set-cookie" not in a.headers
    assert client.get("/api/runs").status_code == 401


def test_throttle_429_then_recovers(env):
    client, _, clock = env
    for _ in range(5):
        assert _login(client, password="wrong password!!").status_code == 401
    resp = _login(client)  # even the right password is refused while locked
    assert resp.status_code == 429
    assert "try again in 300 s" in resp.json()["detail"]
    clock.now += 301
    assert _login(client).status_code == 200


def test_logout_invalidates_session(env):
    client, _, _ = env
    _login(client)
    token = client.cookies.get(SESSION_COOKIE)
    assert client.post("/api/logout").status_code == 204
    client.cookies.set(SESSION_COOKIE, token)  # replaying the old token
    assert client.get("/api/runs").status_code == 401


def test_logout_without_session_is_204(env):
    client, _, _ = env
    assert client.post("/api/logout").status_code == 204


def test_session_expires_after_idle(env):
    client, _, clock = env
    _login(client)
    clock.now += 8 * 3600 + 1
    assert client.get("/api/runs").status_code == 401


def test_viewer_role_enforced_after_login(env):
    client, _, _ = env
    _login(client, "eva")
    assert client.get("/api/runs").status_code == 200
    assert client.post("/api/runs/mig01/archive").status_code == 403


def test_index_redirects_to_login_without_session(env):
    client, _, _ = env
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"
    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert "<form" in login_page.text


def test_login_page_redirects_when_logged_in(env):
    client, _, _ = env
    _login(client)
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/"
    assert client.get("/", follow_redirects=False).status_code == 200


def test_rehash_on_login(env, monkeypatch):
    client, store, _ = env
    monkeypatch.setattr(users_mod, "PASSWORD_ITERATIONS", 2_000)
    assert _login(client).status_code == 200
    assert store.load()["rado"].password_hash.split("$")[1] == "2000"


def test_rehash_skipped_when_user_deleted_mid_request(env, monkeypatch):
    """verify_password is slow; if the account is deleted while it runs, the
    rehash (which loads a FRESH copy of the store) must not resurrect the
    stale pre-verify record."""
    client, store, _ = env
    monkeypatch.setattr(users_mod, "PASSWORD_ITERATIONS", 2_000)
    from migration_validator.gui import auth_routes

    real_verify = auth_routes.verify_password

    def racing_verify(password, stored):
        ok = real_verify(password, stored)
        if ok:
            store.save({"eva": store.load()["eva"]})  # 'rado' deleted mid-request
        return ok

    monkeypatch.setattr(auth_routes, "verify_password", racing_verify)
    resp = _login(client)
    assert resp.status_code == 200  # in-memory login still succeeds
    assert "rado" not in store.load()  # deletion is not undone by the rehash


def test_rehash_preserves_role_changed_mid_request(env, monkeypatch):
    """If the role changes between the initial lookup and the rehash write,
    the newer role must win - not the role captured before verify_password."""
    client, store, _ = env
    monkeypatch.setattr(users_mod, "PASSWORD_ITERATIONS", 2_000)
    from migration_validator.gui import auth_routes

    real_verify = auth_routes.verify_password

    def racing_verify(password, stored):
        ok = real_verify(password, stored)
        if ok:
            current = store.load()
            current["rado"] = User("rado", "viewer", current["rado"].password_hash)
            store.save(current)
        return ok

    monkeypatch.setattr(auth_routes, "verify_password", racing_verify)
    resp = _login(client)
    assert resp.status_code == 200
    rado = store.load()["rado"]
    assert rado.role == "viewer"  # race-changed role wins over the stale one
    assert rado.password_hash.split("$")[1] == "2000"  # rehash still applied


def test_origin_mismatch_rejected(env):
    client, _, _ = env
    _login(client)
    resp = client.post("/api/runs/mig01/archive", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "cross-origin request refused"}
    ok = client.post("/api/runs/mig01/archive", headers={"Origin": "https://testserver"})
    assert ok.status_code == 200


def test_origin_check_applies_to_login(env):
    client, _, _ = env
    resp = client.post("/api/login", json={"username": "rado", "password": PW},
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_anonymous_mode_me(tmp_path):
    client = TestClient(create_app(run_root=tmp_path))
    assert client.get("/api/me").json() == {
        "username": "anonymous", "role": "admin",
        "permissions": ["view", "operate", "admin"], "auth": False,
    }
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"
    assert client.get("/", follow_redirects=False).status_code == 200
