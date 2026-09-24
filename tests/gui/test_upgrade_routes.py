"""POST /api/runs/{run}/upgrade a /api/groups/{group}/upgrade (spec 2026-09-23, sekce 4)."""

import threading

from fastapi.testclient import TestClient
from raw_run import arp_with_note, build_run

from migration_validator import api
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor


def _client(tmp_path):
    app = create_app(run_root=tmp_path)
    return app, TestClient(app)


def test_dry_run_returns_report_and_changes_nothing(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    before = (store.dir / "snapshot_pre_MX1_all.json").read_bytes()
    arp_with_note(monkeypatch)
    _, client = _client(tmp_path)

    res = client.post("/api/runs/mig01/upgrade?dry_run=true")

    assert res.status_code == 200
    body = res.json()
    assert body["dry_run"] is True
    assert body["backup"] is None
    assert {item["file"]: item["result"] for item in body["items"]}["snapshot_pre_MX1_all.json"] == "changed"
    assert (store.dir / "snapshot_pre_MX1_all.json").read_bytes() == before


def test_apply_replaces_and_reports_backup(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    arp_with_note(monkeypatch)
    _, client = _client(tmp_path)

    body = client.post("/api/runs/mig01/upgrade").json()

    assert body["dry_run"] is False
    assert body["backup"].startswith("backup/upgrade-")
    assert (store.dir / body["backup"] / "snapshot_pre_MX1_all.json").is_file()


def test_upgrade_refused_while_capture_runs(tmp_path):
    build_run(tmp_path)
    app, client = _client(tmp_path)
    release = threading.Event()
    app.state.captures.start(
        lambda on_progress: release.wait(5), run="mig01", device="MX1", port=None, phase="pre",
    )
    try:
        res = client.post("/api/runs/mig01/upgrade?dry_run=true")
    finally:
        release.set()

    assert res.status_code == 409
    assert "bezici capture" in res.json()["detail"]


def test_upgrade_unexpected_exception_is_500_with_string_detail(tmp_path, monkeypatch):
    """T9a: neocekavana vyjimka upgradu je 500 s textovym detailem (modal ho
    ukaze pres errorDetail), ne holy traceback."""
    build_run(tmp_path)

    def boom(name, **kwargs):
        raise RuntimeError("bug v nastroji")

    monkeypatch.setattr("migration_validator.gui.app.api.upgrade_run", boom)
    app, client = _client(tmp_path)

    res = client.post("/api/runs/mig01/upgrade?dry_run=true")

    assert res.status_code == 500
    assert res.json()["detail"] == "RuntimeError: bug v nastroji"
    with app.state.captures.maintenance("mig01"):
        pass  # maintenance se po chybe uvolnila


def test_upgrade_unknown_run_is_404(tmp_path):
    _, client = _client(tmp_path)
    assert client.post("/api/runs/nope/upgrade").status_code == 404


def test_upgrade_needs_admin(tmp_path):
    build_run(tmp_path)
    app, client = _client(tmp_path)
    app.state.actor_provider = lambda request: Actor(role="operator")

    assert client.post("/api/runs/mig01/upgrade?dry_run=true").status_code == 403
    assert client.post("/api/groups/pop1/upgrade?dry_run=true").status_code == 403


def test_group_upgrade_continues_after_one_run_fails(tmp_path, monkeypatch):
    build_run(tmp_path, "pop1-mx1", group="pop1")
    build_run(tmp_path, "pop1-mx2", group="pop1")
    real = api.upgrade_run

    def flaky(name, **kwargs):
        if name == "pop1-mx1":
            raise OSError("disk")
        return real(name, **kwargs)

    monkeypatch.setattr("migration_validator.gui.group_routes.api.upgrade_run", flaky)
    _, client = _client(tmp_path)

    body = client.post("/api/groups/pop1/upgrade?dry_run=true").json()

    runs = {entry["run"]: entry for entry in body["runs"]}
    assert body["group"] == "pop1"
    assert runs["pop1-mx1"]["error"] == "OSError: disk"
    assert runs["pop1-mx1"]["items"] == []
    assert runs["pop1-mx2"]["error"] is None
    assert runs["pop1-mx2"]["items"]


def test_group_upgrade_refused_while_any_capture_runs(tmp_path):
    build_run(tmp_path, "pop1-mx1", group="pop1")
    build_run(tmp_path, "pop1-mx2", group="pop1")
    app, client = _client(tmp_path)
    release = threading.Event()
    app.state.captures.start(
        lambda on_progress: release.wait(5), run="pop1-mx2", device="MX1", port=None, phase="pre",
    )
    try:
        res = client.post("/api/groups/pop1/upgrade?dry_run=true")
    finally:
        release.set()

    assert res.status_code == 409


def test_group_upgrade_unknown_group_is_404(tmp_path):
    _, client = _client(tmp_path)
    assert client.post("/api/groups/nope/upgrade").status_code == 404
