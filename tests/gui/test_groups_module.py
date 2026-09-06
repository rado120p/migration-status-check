"""Souhrn skupiny - verdikt runu, cache, radky."""

import yaml

from migration_validator import api
from migration_validator.gui.captures import CaptureManager
from migration_validator.gui.groups import SummaryCache, build_group_summary, run_verdict
from migration_validator.gui.profiles import server_default_profile
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot, save_snapshot
from migration_validator.profiles.store import ProfileStore
from migration_validator.runs.manifest import CaptureRecord
from migration_validator.runs.store import RunStore

NOW = "2026-09-06T08:14:02Z"
DEVICES = [
    {"node": "PTX1", "host": "10.0.0.1", "platform": "junos-evo"},
    {"node": "MX2", "host": "10.0.0.2", "platform": "junos"},
]


def _write_snapshot(store, phase, node, address, *, oper="up"):
    scope = Scope(
        id="svc:L3VPN:IPVPN", kind="service",
        key=ScopeKey("L3VPN", "IPVPN", None), selectors=Selectors(interfaces=["et-0/0/1.113"]),
    )
    snapshot = Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(started_at=NOW, finished_at=NOW, phase=phase,
                            collectors={"interfaces": {"status": "ok"}}),
        facts={"interfaces": {"et-0/0/1.113": {
            "admin_status": "up", "oper_status": oper,
            "input_pps": 400, "output_pps": 400, "input_errors": 0, "output_errors": 0,
        }}},
        probes={"ping": []}, scopes=[scope], inventory=[],
    )
    path = store.snapshot_path(phase, node, None)
    save_snapshot(snapshot, path)
    return path


def _capture(store, phase, node, address, **kw):
    manifest = store.load()
    path = _write_snapshot(store, phase, node, address, **kw)
    manifest.record_capture(CaptureRecord(phase, node, None, path.name, NOW))
    store.save(manifest)


def _group(tmp_path):
    api.create_group("g", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    return RunStore(tmp_path, "g-ptx1"), RunStore(tmp_path, "g-mx2")


def test_run_verdict_bez_post_je_none(tmp_path):
    ptx, _ = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    verdict, services, checks = run_verdict(ptx, ptx.load(), server_default_profile(None))
    assert verdict is None
    assert services["pass"] == 0 and checks["pass"] == 0


def test_run_verdict_pre_a_post_je_pass(tmp_path):
    ptx, _ = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1")
    verdict, services, checks = run_verdict(ptx, ptx.load(), server_default_profile(None))
    assert verdict == "PASS"
    assert services["pass"] == 1
    assert checks["pass"] >= 1


def test_run_verdict_down_rozhrani_je_fail(tmp_path):
    ptx, _ = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1", oper="down")
    verdict, services, _ = run_verdict(ptx, ptx.load(), server_default_profile(None))
    assert verdict == "FAIL"
    assert services["fail"] == 1


def _summary(tmp_path, cache=None, manager=None):
    return build_group_summary(
        "g", run_root=tmp_path, profiles=ProfileStore(tmp_path / "p"), default_path=None,
        manager=manager or CaptureManager(), cache=cache or SummaryCache(),
    )


def test_summary_radky_a_verdikty(tmp_path):
    ptx, mx = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1", oper="down")
    _capture(mx, "pre", "MX2", "10.0.0.2")
    summary = _summary(tmp_path)
    assert summary["name"] == "g"
    assert summary["profile"] is None
    rows = {row["run"]: row for row in summary["runs"]}
    assert rows["g-ptx1"]["node"] == "PTX1"
    assert rows["g-ptx1"]["phases"] == {"pre": NOW, "post": NOW, "rollback": None}
    assert rows["g-ptx1"]["verdict"] == "FAIL"
    assert rows["g-ptx1"]["active_task"] is None
    assert rows["g-ptx1"]["error"] is None
    assert rows["g-mx2"]["verdict"] is None
    assert rows["g-mx2"]["phases"]["post"] is None
    assert summary["verdicts"] == {"FAIL": 1, "none": 1}


def test_summary_neznama_skupina_je_none(tmp_path):
    assert _summary(tmp_path) is None


def test_summary_rozbity_manifest_je_error_radek(tmp_path):
    ptx, mx = _group(tmp_path)
    mx.manifest_path.write_text(
        "schema_version: 1\nkind: bulk\ngroup: g\ndevices: {}\n", encoding="utf-8"
    )
    summary = _summary(tmp_path)
    rows = {row["run"]: row for row in summary["runs"]}
    assert "neznamy kind 'bulk'" in rows["g-mx2"]["error"]
    assert rows["g-mx2"]["verdict"] is None
    assert rows["g-ptx1"]["error"] is None
    assert summary["verdicts"] == {"none": 1, "error": 1}


def test_summary_rozbity_yaml_je_error_radek(tmp_path, monkeypatch):
    """store.load() muze shodit yaml.YAMLError (napr. run.yml se rozbije
    soubezne se čtenim clenstvi skupiny). api.group_runs() sam malformovany
    top-level YAML uz filtruje pryc z clenstvi, takze staticky rozbity
    soubor tuto vetev nezasahne - simulujeme rozbiti primo v RunStore.load()."""
    ptx, mx = _group(tmp_path)

    original_load = RunStore.load

    def broken_load(self):
        if self.name == "g-mx2":
            raise yaml.YAMLError("rozbity yaml")
        return original_load(self)

    monkeypatch.setattr(RunStore, "load", broken_load)
    summary = _summary(tmp_path)
    rows = {row["run"]: row for row in summary["runs"]}
    assert rows["g-mx2"]["error"]
    assert rows["g-mx2"]["verdict"] is None
    assert rows["g-ptx1"]["error"] is None


def test_summary_chybejici_snimek_je_error_radek(tmp_path):
    ptx, _ = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1")
    next(ptx.dir.glob("snapshot_pre_*.json")).unlink()
    rows = {row["run"]: row for row in _summary(tmp_path)["runs"]}
    assert rows["g-ptx1"]["error"].startswith("chybejici soubory snimku")


def test_summary_active_task_z_manageru(tmp_path):
    import threading
    _group(tmp_path)
    manager = CaptureManager(pool=1)
    gate = threading.Event()
    task = manager.start(lambda on_progress: gate.wait(5), run="g-ptx1", device="PTX1",
                         port=None, phase="post")
    rows = {row["run"]: row for row in _summary(tmp_path, manager=manager)["runs"]}
    assert rows["g-ptx1"]["active_task"]["id"] == task.id
    assert rows["g-ptx1"]["active_task"]["phase"] == "post"
    assert rows["g-ptx1"]["active_task"]["state"] in ("queued", "running")
    gate.set()


def test_summary_cache_nevyhodnocuje_dvakrat(tmp_path, monkeypatch):
    ptx, _ = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1")
    calls = []
    real = api.evaluate

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(api, "evaluate", spy)
    cache = SummaryCache()
    _summary(tmp_path, cache=cache)
    _summary(tmp_path, cache=cache)
    assert len(calls) == 1
    # nova capture posune mtime run.yml -> prepocet
    import os, time
    stamp = time.time() + 5
    os.utime(ptx.manifest_path, (stamp, stamp))
    _summary(tmp_path, cache=cache)
    assert len(calls) == 2


def test_summary_cache_sdilena_pres_skupiny_se_neprorezava(tmp_path, monkeypatch):
    ptx, _ = _group(tmp_path)
    api.create_group(
        "h", [{"node": "SW1", "host": "10.0.0.9", "platform": "junos"}],
        run_root=tmp_path, profiles_root=tmp_path / "p",
    )
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1")
    calls = []
    real = api.evaluate

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(api, "evaluate", spy)
    cache = SummaryCache()
    _summary(tmp_path, cache=cache)
    build_group_summary(
        "h", run_root=tmp_path, profiles=ProfileStore(tmp_path / "p"), default_path=None,
        manager=CaptureManager(), cache=cache,
    )
    _summary(tmp_path, cache=cache)
    assert len(calls) == 1
