"""FastAPI aplikace - routes jsou tenke obaly nad migration_validator.api."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from migration_validator import api
from migration_validator.auth import load_settings
from migration_validator.collectors.registry import collectors_for
from migration_validator.config import default_profile, load_profile
from migration_validator.connection.junos import ConnectionOptions
from migration_validator.gui.authz import Actor, Permission, anonymous_admin, require
from migration_validator.gui.captures import CaptureManager, DeviceBusy
from migration_validator.gui.serializers import snapshot_list, status_rows
from migration_validator.models.snapshot import load_snapshot
from migration_validator.profiles.store import ProfileStore
from migration_validator.runs.manifest import RunManifest
from migration_validator.runs.orchestrate import capture_into_run
from migration_validator.runs.pairing import plan_evaluations
from migration_validator.runs.store import RunStore


class DeviceBody(BaseModel):
    node: str
    host: str
    platform: str
    role: str


class CreateRunBody(BaseModel):
    name: str
    kind: str
    profile: str | None = None
    devices: list[DeviceBody]
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


# "created" je mtime souboru run.yml (posune se pri kazdem capture nebo
# uprave mappingu) - GUI ho pouziva jen k predvyberu typu noveho runu.
def _created_iso(path: Path) -> str:
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_summary(store: RunStore) -> dict:
    manifest = store.load()
    return {
        "name": store.name,
        "kind": manifest.kind,
        "profile": manifest.profile,
        "created": _created_iso(store.manifest_path),
        "devices": _devices_dict(manifest),
        "snapshots": len(manifest.captures),
        "mapped_ports": len(manifest.interface_mapping),
    }


STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    run_root: Path = Path("runs"),
    profile_path: str | None = None,
    profiles_root: Path = Path("profiles"),
) -> FastAPI:
    app = FastAPI(title="mig-validate")
    app.state.run_root = run_root
    app.state.profile_path = profile_path
    profiles = ProfileStore(Path(profiles_root))
    app.state.profiles = profiles
    manager = CaptureManager()
    app.state.captures = manager
    app.state.actor_provider = anonymous_admin

    @app.get("/api/checks")
    def list_checks(actor: Actor = require(Permission.VIEW)) -> dict:
        return {"checks": api.list_checks()}

    @app.get("/api/meta")
    def meta(actor: Actor = require(Permission.VIEW)) -> dict:
        try:
            settings = load_settings()
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        profile = load_profile(profile_path) if profile_path else default_profile()
        parts = [settings.username]
        if settings.ssh_key_paths:
            parts.append(f"ssh keys ({len(settings.ssh_key_paths)})")
        if settings.password is not None:
            parts.append("password fallback" if settings.ssh_key_paths else "password via env")
        auth = " · ".join(parts)
        collectors = {}
        for platform in ("junos", "junos-evo"):
            names = [c.name for c in collectors_for(platform)]
            if profile.collectors is not None:
                names = [n for n in names if n in profile.collectors]
            collectors[platform] = names
        return {"profile": profile.name or None, "auth": auth, "collectors": collectors}

    @app.get("/api/runs")
    def list_runs(actor: Actor = require(Permission.VIEW)) -> dict:
        runs = []
        if run_root.exists():
            for entry in sorted(run_root.iterdir()):
                if entry.name.startswith("."):
                    continue
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
            "kind": manifest.kind,
            "profile": manifest.profile,
            "devices": _devices_dict(manifest),
            "rows": status_rows(manifest),
            "snapshots": snapshot_list(manifest),
        }

    @app.get("/api/runs/{run}")
    def run_detail(run: str, actor: Actor = require(Permission.VIEW)) -> dict:
        return _detail(run)

    @app.post("/api/runs", status_code=201)
    def create_run(body: CreateRunBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        try:
            api.create_run(
                body.name,
                kind=body.kind,
                devices=[d.model_dump() for d in body.devices],
                mappings=body.mappings,
                profile=body.profile,
                run_root=run_root,
                profiles_root=profiles.root,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _detail(body.name)

    @app.put("/api/runs/{run}/mapping")
    def update_mapping(run: str, body: MappingBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        try:
            api.update_mapping(run, body.mappings, run_root=run_root)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _detail(run)

    @app.post("/api/runs/{run}/archive")
    def archive_run(run: str, actor: Actor = require(Permission.ADMIN)) -> dict:
        """Archivuje run - presune runs/<run>/ do runs/.archive/.

        Kontrola busy_run() a nasledny rename nejsou atomicke. Mezi nimi muze
        soubezny zapis (start capture, nebo PUT /api/runs/{run}/mapping, ktery
        nema zadnou obdobnou ochranu) znovu vytvorit runs/<run>/run.yml a po
        rename tak zustane "duch" runu (soubory existuji, ale run zmizel ze
        seznamu/API). V teto vlne je toto okno akceptovano - GUI ma dnes
        jednoho operatora. Radne reseni (napr. zamek na urovni runu) patri do
        vlny s rolovym modelem.
        """
        if manager.busy_run(run):
            raise HTTPException(status_code=409, detail="run ma bezici capture")
        try:
            target = api.archive_run(run, run_root=run_root)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"archived_to": target.name}

    @app.get("/api/runs/{run}/evaluation")
    def run_evaluation(run: str, ports: str | None = None, actor: Actor = require(Permission.VIEW)) -> dict:
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
                "same_device": evaluation.same_device,
                "result": result.to_dict(),
            })
        return {"evaluations": evaluations}

    def _require_snapshot_path(store: RunStore, file: str) -> Path:
        path = store.dir / file
        if not path.exists() or "/" in file or file == "..":
            raise HTTPException(status_code=404, detail=f"snapshot nenalezen: {file}")
        return path

    def _snapshot_meta(snapshot) -> dict:
        return {
            "device": snapshot.device.hostname or snapshot.device.address,
            "platform": snapshot.device.platform,
            "taken": snapshot.capture.started_at,
            "collectors": snapshot.capture.collectors,
        }

    @app.get("/api/runs/{run}/snapshots/{file}/evaluation")
    def snapshot_evaluation(run: str, file: str, baseline: str | None = None, actor: Actor = require(Permission.VIEW)) -> dict:
        store = _require_store(run)
        path = _require_snapshot_path(store, file)
        baseline_snapshot = None
        if baseline is not None:
            manifest = store.load()
            records = {c.snapshot: c for c in manifest.captures}
            subject_record = records.get(file)
            baseline_record = records.get(baseline)
            if subject_record is None or baseline_record is None:
                missing = file if subject_record is None else baseline
                raise HTTPException(
                    status_code=404, detail=f"snimek neni v run.yml: {missing}"
                )
            if subject_record.device != baseline_record.device:
                raise HTTPException(
                    status_code=422,
                    detail="baseline musi byt snimek stejneho zarizeni",
                )
            baseline_path = _require_snapshot_path(store, baseline)
            baseline_snapshot = load_snapshot(str(baseline_path))
        snapshot = load_snapshot(str(path))
        profile = load_profile(profile_path) if profile_path else default_profile()
        result = api.evaluate(
            snapshot,
            baseline=baseline_snapshot,
            config=profile.checks,
            service_types=profile.service_types,
            profile_name=profile.name or None,
        )
        return {
            "snapshot": _snapshot_meta(snapshot),
            "baseline": _snapshot_meta(baseline_snapshot)
            if baseline_snapshot else None,
            "result": result.to_dict(),
        }

    @app.post("/api/captures", status_code=202)
    def start_capture(body: CaptureBody, actor: Actor = require(Permission.OPERATE)) -> dict:
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
    def capture_status(task_id: str, actor: Actor = require(Permission.VIEW)) -> dict:
        task = manager.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="capture nenalezen")
        return task.to_dict()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
