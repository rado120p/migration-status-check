"""FastAPI aplikace - routes jsou tenke obaly nad migration_validator.api."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from migration_validator import api
from migration_validator.auth import load_settings
from migration_validator.config import default_profile, load_profile
from migration_validator.connection.junos import ConnectionOptions
from migration_validator.gui.captures import CaptureManager, DeviceBusy
from migration_validator.gui.serializers import snapshot_list, status_rows
from migration_validator.models.snapshot import load_snapshot
from migration_validator.runs.manifest import RunManifest
from migration_validator.runs.orchestrate import capture_into_run
from migration_validator.runs.pairing import plan_evaluations
from migration_validator.runs.store import RunStore


class DeviceBody(BaseModel):
    node: str
    host: str
    platform: str


class CreateRunBody(BaseModel):
    name: str
    old_device: DeviceBody
    new_device: DeviceBody
    mappings: list[tuple[str, str]] = []


class MappingBody(BaseModel):
    mappings: list[tuple[str, str]]


class CaptureBody(BaseModel):
    run: str
    device: str  # node name z run.yml
    port: str | None = None
    phase: str
    parse_services: bool = False


def _devices_dict(manifest: RunManifest) -> dict:
    return {
        node: {"host": d.host, "platform": d.platform, "role": d.role}
        for node, d in manifest.devices.items()
    }


def _run_summary(store: RunStore) -> dict:
    manifest = store.load()
    return {
        "name": store.name,
        "devices": _devices_dict(manifest),
        "snapshots": len(manifest.captures),
        "mapped_ports": len(manifest.interface_mapping),
    }


def create_app(
    run_root: Path = Path("runs"), profile_path: str | None = None
) -> FastAPI:
    app = FastAPI(title="mig-validate")
    app.state.run_root = run_root
    app.state.profile_path = profile_path
    manager = CaptureManager()
    app.state.captures = manager

    @app.get("/api/checks")
    def list_checks() -> dict:
        return {"checks": api.list_checks()}

    @app.get("/api/runs")
    def list_runs() -> dict:
        runs = []
        if run_root.exists():
            for entry in sorted(run_root.iterdir()):
                store = RunStore(run_root, entry.name)
                if store.manifest_path.exists():
                    runs.append(_run_summary(store))
        return {"runs": runs}

    def _require_store(run: str) -> RunStore:
        store = RunStore(run_root, run)
        if not store.manifest_path.exists():
            raise HTTPException(status_code=404, detail=f"run '{run}' neexistuje")
        return store

    def _detail(run: str) -> dict:
        store = _require_store(run)
        manifest = store.load()
        return {
            "name": run,
            "devices": _devices_dict(manifest),
            "rows": status_rows(manifest),
            "snapshots": snapshot_list(manifest),
        }

    @app.get("/api/runs/{run}")
    def run_detail(run: str) -> dict:
        return _detail(run)

    @app.post("/api/runs", status_code=201)
    def create_run(body: CreateRunBody) -> dict:
        try:
            api.create_run(
                body.name,
                old_device=body.old_device.model_dump(),
                new_device=body.new_device.model_dump(),
                mappings=body.mappings,
                run_root=run_root,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _detail(body.name)

    @app.put("/api/runs/{run}/mapping")
    def update_mapping(run: str, body: MappingBody) -> dict:
        try:
            api.update_mapping(run, body.mappings, run_root=run_root)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _detail(run)

    @app.get("/api/runs/{run}/evaluation")
    def run_evaluation(run: str, ports: str | None = None) -> dict:
        store = _require_store(run)
        manifest = store.load()
        missing = store.missing_snapshots(manifest)
        if missing:
            raise HTTPException(
                status_code=422,
                detail="chybejici soubory snimku: " + ", ".join(sorted(missing)),
            )
        port_filter = (
            [p.strip() for p in ports.split(",") if p.strip()] if ports else None
        )
        profile = load_profile(profile_path) if profile_path else default_profile()
        evaluations = []
        for evaluation in plan_evaluations(manifest, port_filter):
            subject = load_snapshot(str(store.dir / evaluation.subject.snapshot))
            baseline = None
            if evaluation.baseline is not None:
                baseline = load_snapshot(
                    str(store.dir / evaluation.baseline.snapshot)
                )
            step_payload = None
            if evaluation.step is not None:
                step_payload = {
                    "old": {"node": evaluation.step.old.node,
                            "port": evaluation.step.old.port},
                    "new": {"node": evaluation.step.new.node,
                            "port": evaluation.step.new.port},
                }
            result = api.evaluate(
                subject,
                baseline=baseline,
                config=profile.checks,
                service_types=profile.service_types,
                profile_name=profile.name or None,
                step=step_payload,
            )
            evaluations.append({
                "subject": evaluation.subject.snapshot,
                "baseline": evaluation.baseline.snapshot
                if evaluation.baseline else None,
                "warning": evaluation.reason or None,
                "step": step_payload,
                "result": result.to_dict(),
            })
        return {"evaluations": evaluations}

    @app.get("/api/runs/{run}/snapshots/{file}/evaluation")
    def snapshot_evaluation(run: str, file: str) -> dict:
        store = _require_store(run)
        path = store.dir / file
        if not path.exists() or "/" in file or file == "..":
            raise HTTPException(status_code=404, detail=f"snapshot nenalezen: {file}")
        snapshot = load_snapshot(str(path))
        profile = load_profile(profile_path) if profile_path else default_profile()
        result = api.evaluate(
            snapshot,
            config=profile.checks,
            service_types=profile.service_types,
            profile_name=profile.name or None,
        )
        return {
            "snapshot": {
                "device": snapshot.device.hostname or snapshot.device.address,
                "platform": snapshot.device.platform,
                "taken": snapshot.capture.started_at,
                "collectors": snapshot.capture.collectors,
            },
            "result": result.to_dict(),
        }

    @app.post("/api/captures", status_code=202)
    def start_capture(body: CaptureBody) -> dict:
        store = _require_store(body.run)
        manifest = store.load()
        device = manifest.devices.get(body.device)
        if device is None:
            raise HTTPException(
                status_code=404, detail=f"zarizeni '{body.device}' neni v runu"
            )
        try:
            settings = load_settings()
            profile = (
                load_profile(profile_path) if profile_path else default_profile()
            )
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        options = ConnectionOptions(
            host=device.host,
            username=settings.username,
            ssh_key_paths=settings.ssh_key_paths,
            password=settings.password,
            port=settings.netconf_port,
            timeout=settings.timeout,
        )

        def fn(on_progress):
            return capture_into_run(
                store,
                host=device.host,
                phase=body.phase,
                port=body.port,
                options=options,
                profile=profile,
                parse_services=body.parse_services,
                overwrite=True,  # GUI resi prepis potvrzenim ve formulari
                on_progress=on_progress,
            )

        try:
            task = manager.start(
                fn, run=body.run, device=body.device,
                port=body.port, phase=body.phase,
            )
        except DeviceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"id": task.id}

    @app.get("/api/captures/{task_id}")
    def capture_status(task_id: str) -> dict:
        task = manager.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="capture nenalezen")
        return task.to_dict()

    return app
