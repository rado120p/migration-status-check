"""Evaluation routes - stejna cesta jako cli._evaluate_run, bez renderu."""

import pytest
from fastapi.testclient import TestClient

from migration_validator.gui.app import create_app
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot, save_snapshot
from migration_validator.runs.manifest import (
    CaptureRecord,
    InterfaceMapping,
    MappingEndpoint,
    RunDevice,
    RunManifest,
)
from migration_validator.runs.store import RunStore

NOW = "2026-07-24T11:40:02Z"


def _write_run_snapshot(store, phase, node, port, address, interface, *, oper="up"):
    """Ulozi snapshot na misto, kde ho ceka RunStore, a vrati jeho cestu."""
    scope = Scope(
        id="svc:L3VPN:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN", "IPVPN", None),
        selectors=Selectors(interfaces=[interface]),
    )
    snapshot = Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(
            started_at=NOW, finished_at=NOW, phase=phase,
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts={
            "interfaces": {
                interface: {
                    "admin_status": "up", "oper_status": oper,
                    "input_pps": 400, "output_pps": 400,
                    "input_errors": 0, "output_errors": 0,
                }
            }
        },
        probes={"ping": []},
        scopes=[scope],
        inventory=[],
    )
    path = store.snapshot_path(phase, node, port)
    save_snapshot(snapshot, path)
    return path


@pytest.fixture
def run_se_snimky_client(tmp_path):
    store = RunStore(tmp_path, "mig01")
    manifest = RunManifest(
        devices={
            "MX1": RunDevice(host="10.0.0.1", platform="junos", role="old"),
            "PTX1": RunDevice(host="10.0.0.2", platform="junos-evo", role="new"),
        },
        interface_mapping=[
            InterfaceMapping(
                old=MappingEndpoint(node="MX1", port="ge-0/0/1"),
                new=MappingEndpoint(node="PTX1", port="et-0/0/1"),
            )
        ],
    )
    pre_path = _write_run_snapshot(
        store, "pre", "MX1", "ge-0/0/1", "10.0.0.1", "ge-0/0/1.113"
    )
    post_path = _write_run_snapshot(
        store, "post", "PTX1", "et-0/0/1", "10.0.0.2", "et-0/0/1.113"
    )
    manifest.record_capture(
        CaptureRecord("pre", "MX1", "ge-0/0/1", pre_path.name, NOW)
    )
    manifest.record_capture(
        CaptureRecord("post", "PTX1", "et-0/0/1", post_path.name, NOW)
    )
    store.save(manifest)

    app = create_app(run_root=tmp_path)
    return TestClient(app)


def test_run_evaluation_vraci_result_dict(run_se_snimky_client):
    data = run_se_snimky_client.get("/api/runs/mig01/evaluation").json()
    assert len(data["evaluations"]) == 1
    ev = data["evaluations"][0]
    assert "summary" in ev["result"]
    assert set(ev["result"]["summary"]) >= {"pass", "warn", "fail", "skip"}
    # subject/baseline se skutecne sparovaly pres plan_evaluations
    assert ev["subject"].startswith("snapshot_post_")
    assert ev["baseline"] is not None
    assert ev["baseline"].startswith("snapshot_pre_")
    assert ev["step"]["old"]["port"] == "ge-0/0/1"
    assert ev["step"]["new"]["port"] == "et-0/0/1"


def test_run_evaluation_404(client):
    assert client.get("/api/runs/neni/evaluation").status_code == 404


def test_run_evaluation_422_chybejici_soubor(run_se_snimky_client, tmp_path):
    next((tmp_path / "mig01").glob("snapshot_pre_*.json")).unlink()
    resp = run_se_snimky_client.get("/api/runs/mig01/evaluation")
    assert resp.status_code == 422
    assert "chybejici soubory snimku" in resp.json()["detail"]


def test_run_evaluation_ports_filter(run_se_snimky_client):
    matched = run_se_snimky_client.get(
        "/api/runs/mig01/evaluation", params={"ports": "ge-0/0/1"}
    ).json()
    assert len(matched["evaluations"]) == 1

    unmatched = run_se_snimky_client.get(
        "/api/runs/mig01/evaluation", params={"ports": "neni"}
    ).json()
    assert unmatched["evaluations"] == []


def test_run_evaluation_same_device_flag(run_se_snimky_client, tmp_path):
    # novy box dostane vlastni pre -> navic same_device evaluace
    store = RunStore(tmp_path, "mig01")
    manifest = store.load()
    pre_new = _write_run_snapshot(
        store, "pre", "PTX1", "et-0/0/1", "10.0.0.2", "et-0/0/1.113"
    )
    manifest.record_capture(
        CaptureRecord("pre", "PTX1", "et-0/0/1", pre_new.name, NOW)
    )
    store.save(manifest)

    data = run_se_snimky_client.get("/api/runs/mig01/evaluation").json()
    assert len(data["evaluations"]) == 2
    assert all(ev["subject"].startswith("snapshot_post_") for ev in data["evaluations"])
    same = [ev for ev in data["evaluations"] if ev["same_device"]]
    assert len(same) == 1
    assert same[0]["baseline"] == pre_new.name


def test_snapshot_evaluation_bez_baseline(run_se_snimky_client):
    detail = run_se_snimky_client.get("/api/runs/mig01").json()
    file = detail["snapshots"][0]["file"]
    data = run_se_snimky_client.get(
        f"/api/runs/mig01/snapshots/{file}/evaluation"
    ).json()
    assert data["snapshot"]["device"]
    assert "summary" in data["result"]


def test_snapshot_evaluation_neznamy_soubor(run_se_snimky_client):
    resp = run_se_snimky_client.get("/api/runs/mig01/snapshots/neni.json/evaluation")
    assert resp.status_code == 404


@pytest.fixture
def run_s_rollbackem_client(tmp_path):
    """Run se dvema snimky stejneho zarizeni (MX1 pre + rollback) a post PTX1."""
    store = RunStore(tmp_path, "mig01")
    manifest = RunManifest(
        devices={
            "MX1": RunDevice(host="10.0.0.1", platform="junos", role="old"),
            "PTX1": RunDevice(host="10.0.0.2", platform="junos-evo", role="new"),
        },
        interface_mapping=[],
    )
    pre_path = _write_run_snapshot(
        store, "pre", "MX1", "ge-0/0/1", "10.0.0.1", "ge-0/0/1.113"
    )
    rollback_path = _write_run_snapshot(
        store, "rollback", "MX1", "ge-0/0/1", "10.0.0.1", "ge-0/0/1.113",
        oper="down",
    )
    post_path = _write_run_snapshot(
        store, "post", "PTX1", "et-0/0/1", "10.0.0.2", "et-0/0/1.113"
    )
    manifest.record_capture(
        CaptureRecord("pre", "MX1", "ge-0/0/1", pre_path.name, NOW)
    )
    manifest.record_capture(
        CaptureRecord("rollback", "MX1", "ge-0/0/1", rollback_path.name, NOW)
    )
    manifest.record_capture(
        CaptureRecord("post", "PTX1", "et-0/0/1", post_path.name, NOW)
    )
    store.save(manifest)

    app = create_app(run_root=tmp_path)
    return TestClient(app)


def _files_by_phase(client):
    detail = client.get("/api/runs/mig01").json()
    return {snap["phase"]: snap["file"] for snap in detail["snapshots"]}


def test_snapshot_evaluation_s_baseline(run_s_rollbackem_client):
    files = _files_by_phase(run_s_rollbackem_client)
    data = run_s_rollbackem_client.get(
        f"/api/runs/mig01/snapshots/{files['rollback']}/evaluation",
        params={"baseline": files["pre"]},
    ).json()
    assert data["baseline"]["device"] == "10.0.0.1"
    assert data["snapshot"]["device"] == "10.0.0.1"
    assert "summary" in data["result"]
    # baseline se skutecne pouzil - scope subjektu se sparoval se scope baseline
    assert data["result"]["summary"]["scopes_matched"] == 1


def test_snapshot_evaluation_bez_baseline_nema_baseline_blok(run_s_rollbackem_client):
    files = _files_by_phase(run_s_rollbackem_client)
    data = run_s_rollbackem_client.get(
        f"/api/runs/mig01/snapshots/{files['rollback']}/evaluation"
    ).json()
    assert data["baseline"] is None


def test_snapshot_evaluation_baseline_jine_zarizeni(run_s_rollbackem_client):
    files = _files_by_phase(run_s_rollbackem_client)
    resp = run_s_rollbackem_client.get(
        f"/api/runs/mig01/snapshots/{files['post']}/evaluation",
        params={"baseline": files["pre"]},
    )
    assert resp.status_code == 422
    assert "stejneho zarizeni" in resp.json()["detail"]


def test_snapshot_evaluation_baseline_neznamy_soubor(run_s_rollbackem_client):
    files = _files_by_phase(run_s_rollbackem_client)
    resp = run_s_rollbackem_client.get(
        f"/api/runs/mig01/snapshots/{files['rollback']}/evaluation",
        params={"baseline": "neni.json"},
    )
    assert resp.status_code == 404


def test_snapshot_evaluation_baseline_soubor_chybi_na_disku(
    run_s_rollbackem_client, tmp_path
):
    files = _files_by_phase(run_s_rollbackem_client)
    (tmp_path / "mig01" / files["pre"]).unlink()
    resp = run_s_rollbackem_client.get(
        f"/api/runs/mig01/snapshots/{files['rollback']}/evaluation",
        params={"baseline": files["pre"]},
    )
    assert resp.status_code == 404


# -- profil per run (spec 3) -------------------------------------------------

def _set_run_profile(tmp_path, name):
    store = RunStore(tmp_path, "mig01")
    manifest = store.load()
    manifest.profile = name
    store.save(manifest)


def _check_ids(payload):
    return {
        check["id"]
        for ev in payload["evaluations"]
        for scope in ev["result"]["scopes"]
        for check in scope["checks"]
    }


def test_run_s_chybejicim_profilem_je_422(run_se_snimky_client, tmp_path):
    _set_run_profile(tmp_path, "neni")
    resp = run_se_snimky_client.get("/api/runs/mig01/evaluation")
    assert resp.status_code == 422
    assert resp.json()["detail"] == f"profil 'neni' neexistuje ({tmp_path / 'mig01' / 'run.yml'})"
    file = next((tmp_path / "mig01").glob("snapshot_post_*.json")).name
    resp = run_se_snimky_client.get(f"/api/runs/mig01/snapshots/{file}/evaluation")
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("profil 'neni' neexistuje")


def test_run_s_profilem_pouzije_jeho_checky(run_se_snimky_client, tmp_path):
    from migration_validator.profiles.store import ProfileStore, empty_document
    assert "interface_state" in _check_ids(
        run_se_snimky_client.get("/api/runs/mig01/evaluation").json()
    )
    doc = empty_document()
    doc["checks"] = {"interface_state": {"enabled": False}}
    # create_app(run_root=tmp_path) -> profiles_root je Path("profiles") relativni
    # k cwd; fixture nema store, proto se app staví znovu s explicitnim rootem.
    ProfileStore(tmp_path / "profiles").save("bez-state", doc)
    _set_run_profile(tmp_path, "bez-state")
    client = TestClient(create_app(run_root=tmp_path, profiles_root=tmp_path / "profiles"))
    payload = client.get("/api/runs/mig01/evaluation").json()
    ids = _check_ids(payload)
    assert "interface_state" not in ids
    assert "interface_traffic" in ids
    assert payload["evaluations"][0]["result"]["profile"] == "bez-state.yml"


def _write_multi_snapshot(store, phase, node, port, address, services):
    """Snapshot s vice sluzbami: services = [(description, service_type, interface)]."""
    scopes = [
        Scope(
            id=f"svc:{desc}:{stype}",
            kind="service",
            key=ScopeKey(desc, stype, None),
            selectors=Selectors(interfaces=[iface]),
        )
        for desc, stype, iface in services
    ]
    facts = {
        "interfaces": {
            iface: {
                "admin_status": "up", "oper_status": "up",
                "input_pps": 400, "output_pps": 400,
                "input_errors": 0, "output_errors": 0,
            }
            for _, _, iface in services
        }
    }
    snapshot = Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(
            started_at=NOW, finished_at=NOW, phase=phase,
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts=facts, probes={"ping": []}, scopes=scopes, inventory=[],
    )
    path = store.snapshot_path(phase, node, port)
    save_snapshot(snapshot, path)
    return path


def test_run_evaluation_sdileny_cil_dve_baseline(tmp_path):
    """Dva stare porty na jeden LAG: kazdy krok dostane jen sve sluzby,
    cizi sluzba na sdilenem portu jde do excluded_services."""
    store = RunStore(tmp_path, "mig02")
    manifest = RunManifest(
        devices={
            "MX1": RunDevice(host="10.0.0.1", platform="junos", role="old"),
            "PTX1": RunDevice(host="10.0.0.2", platform="junos-evo", role="new"),
        },
        interface_mapping=[
            InterfaceMapping(
                old=MappingEndpoint(node="MX1", port="ge-0/0/4"),
                new=MappingEndpoint(node="PTX1", port="ae0"),
            ),
            InterfaceMapping(
                old=MappingEndpoint(node="MX1", port="ge-0/0/5"),
                new=MappingEndpoint(node="PTX1", port="ae0"),
            ),
        ],
    )
    pre4 = _write_multi_snapshot(store, "pre", "MX1", "ge-0/0/4", "10.0.0.1",
                                 [("A", "Internet", "ge-0/0/4.100")])
    pre5 = _write_multi_snapshot(store, "pre", "MX1", "ge-0/0/5", "10.0.0.1",
                                 [("B", "IPVPN", "ge-0/0/5.200")])
    post = _write_multi_snapshot(store, "post", "PTX1", "ae0", "10.0.0.2",
                                 [("A", "Internet", "ae0.100"), ("B", "IPVPN", "ae0.200")])
    manifest.record_capture(CaptureRecord("pre", "MX1", "ge-0/0/4", pre4.name, NOW))
    manifest.record_capture(CaptureRecord("pre", "MX1", "ge-0/0/5", pre5.name, NOW))
    manifest.record_capture(CaptureRecord("post", "PTX1", "ae0", post.name, NOW))
    store.save(manifest)

    client = TestClient(create_app(run_root=tmp_path))
    data = client.get("/api/runs/mig02/evaluation").json()
    steps = [ev for ev in data["evaluations"] if ev["step"] is not None]
    assert [ev["step"]["old"]["port"] for ev in steps] == ["ge-0/0/4", "ge-0/0/5"]
    assert all(ev["step"]["new"]["port"] == "ae0" for ev in steps)
    assert all(ev["subject"] == post.name for ev in steps)
    assert [ev["baseline"] for ev in steps] == [pre4.name, pre5.name]

    def services(ev):
        return sorted(
            s["identity"]["description"] for s in ev["result"]["scopes"]
            if s["scope_id"] != "device" and s["identity"].get("service_type") != "Layer1"
        )

    assert services(steps[0]) == ["A"]
    assert services(steps[1]) == ["B"]
    assert [x["description"] for x in steps[0]["result"]["excluded_services"]] == ["B"]
    assert [x["description"] for x in steps[1]["result"]["excluded_services"]] == ["A"]
    assert steps[0]["result"]["unmatched"]["subject"] == []
    assert not any(ev["same_device"] for ev in data["evaluations"])
