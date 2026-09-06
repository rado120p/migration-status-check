import threading
import time

import pytest

import migration_validator.auth as auth
from migration_validator.gui.captures import CaptureManager, DeviceBusy


def _ok_fn(on_progress):
    on_progress("interfaces", "start", None)
    on_progress("interfaces", "ok", None)
    return object()


def _fail_fn(on_progress):
    raise ValueError("autentizace selhala")


def _wait_done(manager, task_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = manager.get(task_id)
        if task.state in ("done", "failed"):
            return task
        time.sleep(0.01)
    raise AssertionError("capture nedobehl")


def _wait_done_route(client, task_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/captures/{task_id}").json()
        if task["state"] in ("done", "failed"):
            return task
        time.sleep(0.01)
    raise AssertionError("capture nedobehl")


def _wait_state(manager, task_id, state, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager.get(task_id).state == state:
            return
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} nedosel do stavu {state}")


def test_uspesny_capture_ma_kroky_a_stav_done():
    manager = CaptureManager()
    task = manager.start(_ok_fn, run="mig01", device="MX1", port="ge-0/0/1", phase="pre")
    task = _wait_done(manager, task.id)
    assert task.state == "done"
    assert task.steps == [
        {"collector": "interfaces", "status": "ok", "message": None}
    ]


def test_castecne_selhane_collectory_se_ulozi_do_task():
    manager = CaptureManager()

    class FakeOutcome:
        failed_collectors = {"bgp": "timeout"}
        warnings = ["inventory pregenerovana"]

    def partial_fail_fn(on_progress):
        on_progress("interfaces", "start", None)
        on_progress("interfaces", "ok", None)
        return FakeOutcome()

    task = manager.start(
        partial_fail_fn, run="mig01", device="MX1", port=None, phase="pre"
    )
    task = _wait_done(manager, task.id)
    assert task.state == "done"
    assert task.to_dict()["failed_collectors"] == {"bgp": "timeout"}
    assert task.to_dict()["warnings"] == ["inventory pregenerovana"]


def test_selhani_nastavi_failed_a_error():
    manager = CaptureManager()
    task = manager.start(_fail_fn, run="mig01", device="MX1", port=None, phase="pre")
    task = _wait_done(manager, task.id)
    assert task.state == "failed"
    assert "autentizace" in task.error


def test_soubezny_capture_na_stejnem_zarizeni_je_busy():
    manager = CaptureManager()

    def slow_fn(on_progress):
        time.sleep(0.2)
        return object()

    manager.start(slow_fn, run="mig01", device="MX1", port=None, phase="pre")
    with pytest.raises(DeviceBusy):
        manager.start(slow_fn, run="mig01", device="MX1", port=None, phase="pre")


def test_jine_zarizeni_soubezne_muze():
    manager = CaptureManager()

    def slow_fn(on_progress):
        time.sleep(0.2)
        return object()

    manager.start(slow_fn, run="mig01", device="MX1", port=None, phase="pre")
    manager.start(slow_fn, run="mig01", device="PTX1", port=None, phase="post")


def test_route_capture_selhani_docasne_failed(client):
    response = client.post(
        "/api/captures",
        json={
            "run": "mig01",
            "device": "MX1",
            "port": "ge-0/0/1",
            "phase": "neznama-faze",
            "parse_services": False,
        },
    )
    assert response.status_code == 202
    task_id = response.json()["id"]

    task = _wait_done_route(client, task_id)
    assert task["state"] == "failed"


def test_route_409_kdyz_zarizeni_obsazeno(client):
    def slow_fn(on_progress):
        time.sleep(0.5)

    client.app.state.captures.start(
        slow_fn, run="mig01", device="MX1", port=None, phase="pre"
    )
    response = client.post(
        "/api/captures",
        json={"run": "mig01", "device": "MX1", "port": None, "phase": "pre"},
    )
    assert response.status_code == 409


def test_route_get_neznamy_capture_je_404(client):
    response = client.get("/api/captures/neexistuje")
    assert response.status_code == 404


def test_route_capture_neznamy_device_je_404(client):
    response = client.post(
        "/api/captures",
        json={"run": "mig01", "device": "NEEXISTUJE", "port": None, "phase": "pre"},
    )
    assert response.status_code == 404
    assert "neni v runu" in response.json()["detail"]


def test_route_capture_nevalidni_settings_je_503(client, tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.yml"
    settings_path.write_text(
        "connection:\n  password_env: MIG_TEST_NENASTAVENA\n", encoding="utf-8"
    )
    monkeypatch.delenv("MIG_TEST_NENASTAVENA", raising=False)
    monkeypatch.setattr(auth, "DEFAULT_SETTINGS_PATH", settings_path)

    response = client.post(
        "/api/captures",
        json={"run": "mig01", "device": "MX1", "port": None, "phase": "pre"},
    )
    assert response.status_code == 503
    assert "MIG_TEST_NENASTAVENA" in response.json()["detail"]


def test_route_capture_chybejici_profil_je_422(client, tmp_path):
    from migration_validator.runs.store import RunStore

    store = RunStore(tmp_path, "mig01")
    manifest = store.load()
    manifest.profile = "neni"
    store.save(manifest)

    response = client.post(
        "/api/captures",
        json={"run": "mig01", "device": "MX1", "port": None, "phase": "pre"},
    )
    assert response.status_code == 422
    assert response.json()["detail"].startswith("profil 'neni' neexistuje")


def test_busy_run_vidi_jen_bezici_task_daneho_runu():
    manager = CaptureManager()
    gate = threading.Event()

    def blocking(on_progress):
        gate.wait(5)
        return object()

    task = manager.start(blocking, run="mig01", device="MX1", port=None, phase="pre")
    assert manager.busy_run("mig01") is True
    assert manager.busy_run("mig02") is False
    gate.set()
    _wait_done(manager, task.id)
    assert manager.busy_run("mig01") is False


def test_last_finished_vraci_posledni_dokonceny():
    manager = CaptureManager()
    task = manager.start(_fail_fn, run="g-a", device="MX1", port=None, phase="pre")
    _wait_done(manager, task.id)
    last = manager.last_finished("g-a")
    assert last.state == "failed"
    assert last.error == "autentizace selhala"
    assert manager.last_finished("g-b") is None

    task2 = manager.start(_ok_fn, run="g-a", device="MX1", port=None, phase="post")
    _wait_done(manager, task2.id)
    last = manager.last_finished("g-a")
    assert last.state == "done"


def test_pool_drzi_treti_capture_ve_fronte():
    manager = CaptureManager(pool=2)
    gate = threading.Event()

    def blocking(on_progress):
        gate.wait(5)
        return object()

    a = manager.start(blocking, run="g-a", device="A", port=None, phase="pre")
    b = manager.start(blocking, run="g-b", device="B", port=None, phase="pre")
    c = manager.start(blocking, run="g-c", device="C", port=None, phase="pre")
    _wait_state(manager, a.id, "running")
    _wait_state(manager, b.id, "running")
    time.sleep(0.05)
    assert manager.get(c.id).state == "queued"
    gate.set()
    for task in (a, b, c):
        assert _wait_done(manager, task.id).state == "done"


def test_queued_zarizeni_je_busy():
    manager = CaptureManager(pool=1)
    gate = threading.Event()

    def blocking(on_progress):
        gate.wait(5)
        return object()

    manager.start(blocking, run="g-a", device="A", port=None, phase="pre")
    queued = manager.start(blocking, run="g-b", device="B", port=None, phase="pre")
    assert manager.get(queued.id).state == "queued"
    with pytest.raises(DeviceBusy):
        manager.start(blocking, run="g-b", device="B", port=None, phase="post")
    assert manager.busy_run("g-b") is True
    gate.set()


def test_active_task_vraci_queued_nebo_running():
    manager = CaptureManager(pool=1)
    gate = threading.Event()

    def blocking(on_progress):
        gate.wait(5)
        return object()

    running = manager.start(blocking, run="g-a", device="A", port=None, phase="pre")
    queued = manager.start(blocking, run="g-b", device="B", port=None, phase="pre")
    _wait_state(manager, running.id, "running")
    assert manager.active_task("g-a").id == running.id
    assert manager.active_task("g-b").id == queued.id
    assert manager.active_task("g-c") is None
    gate.set()
    _wait_done(manager, queued.id)
    assert manager.active_task("g-a") is None
