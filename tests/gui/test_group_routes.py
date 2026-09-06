"""Routes /api/groups - bulk single runy."""

import threading
import time

import yaml
from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor
from migration_validator.gui.captures import CaptureManager

DEVICES = [
    {"node": "PTX1", "host": "10.0.0.1", "platform": "junos-evo"},
    {"node": "MX2", "host": "10.0.0.2", "platform": "junos"},
]


def _client(tmp_path, role=None, pool=10):
    app = create_app(run_root=tmp_path, profiles_root=tmp_path / "p", capture_pool=pool)
    if role is not None:
        app.state.actor_provider = lambda request: Actor(role=role)
    return TestClient(app)


def _body(**over):
    body = {"group": "pop1", "profile": None, "devices": DEVICES, "capture_pre": False}
    body.update(over)
    return body


def test_post_groups_zalozi_skupinu_a_vrati_souhrn(tmp_path):
    resp = _client(tmp_path).post("/api/groups", json=_body())
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "pop1"
    assert [row["run"] for row in data["runs"]] == ["pop1-mx2", "pop1-ptx1"]
    assert data["batch"] is None
    assert (tmp_path / "pop1-ptx1" / "run.yml").exists()


def test_post_groups_radkove_chyby_409(tmp_path):
    devices = [DEVICES[0], {"node": "ptx1", "host": "1.1.1.1", "platform": "junos"}]
    resp = _client(tmp_path).post("/api/groups", json=_body(devices=devices))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["rows"] == [
        {"index": 1, "message": "zarizeni 'ptx1' je uvedeno dvakrat (radek 1)"}
    ]
    assert "dvakrat" in detail["message"]
    assert not (tmp_path / "pop1-ptx1").exists()


def test_post_groups_chyba_skupiny_ma_index_null(tmp_path):
    resp = _client(tmp_path).post("/api/groups", json=_body(group="Pop 1"))
    assert resp.status_code == 409
    assert resp.json()["detail"]["rows"][0]["index"] is None


def test_post_groups_nevalidni_profil_je_409(tmp_path):
    resp = _client(tmp_path).post("/api/groups", json=_body(profile="Bad Name"))
    assert resp.status_code == 409
    assert isinstance(resp.json()["detail"], str)
    assert not (tmp_path / "pop1-ptx1").exists()


def test_post_groups_vyzaduje_operate(tmp_path):
    resp = _client(tmp_path, role="viewer").post("/api/groups", json=_body())
    assert resp.status_code == 403


def test_get_groups_seznam(tmp_path):
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    data = client.get("/api/groups").json()
    assert data["groups"][0]["name"] == "pop1"
    assert data["groups"][0]["runs"] == 2


def test_get_group_404(tmp_path):
    assert _client(tmp_path).get("/api/groups/neni").status_code == 404


def test_get_runs_nese_group(tmp_path):
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    runs = {r["name"]: r for r in client.get("/api/runs").json()["runs"]}
    assert runs["pop1-ptx1"]["group"] == "pop1"
    assert client.get("/api/runs/pop1-ptx1").json()["group"] == "pop1"


def test_post_devices_prida_cleny(tmp_path):
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    resp = client.post("/api/groups/pop1/devices", json={
        "devices": [{"node": "EX1", "host": "10.0.0.9", "platform": "junos"}],
    })
    assert resp.status_code == 201
    assert [row["run"] for row in resp.json()["runs"]] == ["pop1-ex1", "pop1-mx2", "pop1-ptx1"]


def test_post_devices_neznama_skupina_404(tmp_path):
    resp = _client(tmp_path).post("/api/groups/neni/devices", json={"devices": DEVICES})
    assert resp.status_code == 404




def _blocking_capture(monkeypatch, gate, calls=None):
    """Nahradi capture_into_run necim, co ceka na gate, nic nezapisuje a
    zaznamena kwargs (parse_services) do `calls`."""
    import migration_validator.gui.capture_launch as launch

    def fake(store, **kwargs):
        if calls is not None:
            calls.append((store.name, kwargs))
        gate.wait(5)
        return object()

    monkeypatch.setattr(launch, "capture_into_run", fake)


def test_post_captures_spusti_task_pro_kazdeho_clena(tmp_path, monkeypatch):
    gate = threading.Event()
    _blocking_capture(monkeypatch, gate)
    client = _client(tmp_path, pool=1)
    client.post("/api/groups", json=_body())
    resp = client.post("/api/groups/pop1/captures", json={"phase": "pre"})
    assert resp.status_code == 202
    data = resp.json()
    assert len(data["batch"]) == 12
    assert [t["run"] for t in data["tasks"]] == ["pop1-mx2", "pop1-ptx1"]
    assert all(t["task_id"] and t["error"] is None for t in data["tasks"])
    states = {client.get(f"/api/captures/{t['task_id']}").json()["state"] for t in data["tasks"]}
    assert states <= {"queued", "running"}
    summary = client.get("/api/groups/pop1").json()
    assert all(row["active_task"] is not None for row in summary["runs"])
    gate.set()


def test_post_captures_obsazene_zarizeni_je_radkova_chyba(tmp_path, monkeypatch):
    gate = threading.Event()
    _blocking_capture(monkeypatch, gate)
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    client.post("/api/captures", json={"run": "pop1-ptx1", "device": "PTX1", "phase": "pre"})
    resp = client.post("/api/groups/pop1/captures", json={"phase": "pre"})
    assert resp.status_code == 202
    tasks = {t["run"]: t for t in resp.json()["tasks"]}
    assert tasks["pop1-ptx1"]["task_id"] is None
    assert "uz bezi capture" in tasks["pop1-ptx1"]["error"]
    assert tasks["pop1-mx2"]["task_id"] is not None
    gate.set()


def test_post_captures_bez_inventory_parsuje_sluzby(tmp_path, monkeypatch):
    gate = threading.Event()
    calls = []
    _blocking_capture(monkeypatch, gate, calls)
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    client.post("/api/groups/pop1/captures", json={"phase": "pre"})
    gate.set()
    assert sorted((name, kw["parse_services"]) for name, kw in calls) == [
        ("pop1-mx2", True), ("pop1-ptx1", True),
    ]


def test_post_captures_s_inventory_ji_znovu_pouzije(tmp_path, monkeypatch):
    gate = threading.Event()
    calls = []
    _blocking_capture(monkeypatch, gate, calls)
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    (tmp_path / "pop1-ptx1" / "inventory_PTX1_all.yml").write_text("[]\n", encoding="utf-8")
    client.post("/api/groups/pop1/captures", json={"phase": "post"})
    gate.set()
    assert dict((name, kw["parse_services"]) for name, kw in calls) == {
        "pop1-mx2": True, "pop1-ptx1": False,
    }


def test_post_captures_rozbity_run_yml_clena_je_radkova_chyba(tmp_path, monkeypatch):
    """store.load() muze shodit yaml.YAMLError, kdyz se run.yml clena rozbije
    mezi tim, co api.group_runs() precetl clenstvi, a tim, co _start_batch
    nacita manifest jednotlivych clenu (zavodni okno pri soubeznem zapisu).
    api.group_runs() sam malformovany top-level YAML uz filtruje pryc z
    clenstvi (viz _raw_manifest), takze staticky rozbity soubor tuto vetev
    nezasahne - simulujeme rozbiti primo v RunStore.load()."""
    gate = threading.Event()
    _blocking_capture(monkeypatch, gate)
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())

    import migration_validator.runs.store as store_module
    original_load = store_module.RunStore.load

    def broken_load(self):
        if self.name == "pop1-mx2":
            raise yaml.YAMLError("rozbity yaml")
        return original_load(self)

    monkeypatch.setattr(store_module.RunStore, "load", broken_load)
    resp = client.post("/api/groups/pop1/captures", json={"phase": "pre"})
    assert resp.status_code == 202
    tasks = {t["run"]: t for t in resp.json()["tasks"]}
    assert tasks["pop1-mx2"]["task_id"] is None
    assert tasks["pop1-mx2"]["error"]
    assert tasks["pop1-ptx1"]["task_id"] is not None
    gate.set()


def test_post_captures_neznama_faze_422(tmp_path):
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    resp = client.post("/api/groups/pop1/captures", json={"phase": "during"})
    assert resp.status_code == 422
    assert "neznama faze" in resp.json()["detail"]


def test_post_groups_capture_pre_vrati_batch(tmp_path, monkeypatch):
    gate = threading.Event()
    _blocking_capture(monkeypatch, gate)
    resp = _client(tmp_path).post("/api/groups", json=_body(capture_pre=True))
    assert resp.status_code == 201
    batch = resp.json()["batch"]
    assert len(batch["tasks"]) == 2
    gate.set()


def test_archive_group_odmitne_beh_a_pak_archivuje(tmp_path, monkeypatch):
    gate = threading.Event()
    _blocking_capture(monkeypatch, gate)
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    batch = client.post("/api/groups/pop1/captures", json={"phase": "pre"}).json()
    resp = client.post("/api/groups/pop1/archive")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "skupina 'pop1' ma bezici capture"
    gate.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        states = {client.get(f"/api/captures/{t['task_id']}").json()["state"] for t in batch["tasks"]}
        if states <= {"done", "failed"}:
            break
        time.sleep(0.01)
    resp = client.post("/api/groups/pop1/archive")
    assert resp.status_code == 200
    assert len(resp.json()["archived"]) == 2
    assert client.get("/api/groups/pop1").status_code == 404


def test_archive_group_vyzaduje_admin(tmp_path):
    client = _client(tmp_path, role="operator")
    assert client.post("/api/groups/pop1/archive").status_code == 403
