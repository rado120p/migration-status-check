"""FastAPI aplikace - routes jsou tenke obaly nad migration_validator.api."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from migration_validator import api
from migration_validator.auth import DEFAULT_CAPTURE_POOL, load_settings
from migration_validator.collectors.registry import collectors_for
from migration_validator.gui import audit
from migration_validator.gui.auth_routes import build_auth_router
from migration_validator.gui.authz import (
    Actor,
    Permission,
    anonymous_admin,
    current_actor,
    permissions_for,
    require,
    session_actor_provider,
)
from migration_validator.gui.capture_launch import launch_capture
from migration_validator.gui.captures import CaptureManager, DeviceBusy, RunBusy
from migration_validator.gui.group_routes import build_groups_router
from migration_validator.gui.groups import SummaryCache
from migration_validator.gui.profile_routes import build_profiles_router
from migration_validator.gui.profiles import profile_for_run, server_default_profile
from migration_validator.gui.serializers import snapshot_list, status_rows
from migration_validator.gui.sessions import LoginThrottle, SessionStore
from migration_validator.models.snapshot import SnapshotVersionError, load_snapshot
from migration_validator.profiles.store import ProfileStore
from migration_validator.runs.manifest import RunManifest
from migration_validator.runs.pairing import plan_evaluations
from migration_validator.runs.store import RunStore
from migration_validator.users import UserStore


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


def _run_summary(store: RunStore) -> dict:
    manifest = store.load()
    return {
        "name": store.name,
        "kind": manifest.kind,
        "profile": manifest.profile,
        "group": manifest.group,
        "created": api.iso_mtime(store.manifest_path),
        "created_by": manifest.created_by,
        "devices": _devices_dict(manifest),
        "snapshots": len(manifest.captures),
        "mapped_ports": len(manifest.interface_mapping),
    }


STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    run_root: Path = Path("runs"),
    profile_path: str | None = None,
    profiles_root: Path = Path("profiles"),
    capture_pool: int = DEFAULT_CAPTURE_POOL,
    users: UserStore | None = None,
    sessions: SessionStore | None = None,
    throttle: LoginThrottle | None = None,
    settings_path: Path | None = None,
) -> FastAPI:
    # S auth zapnutym by /docs a /openapi.json byly nechrenenou mapou celeho
    # API bez require() seamu - vypnout, kdyz users soubor existuje.
    docs_kwargs = (
        {"docs_url": None, "redoc_url": None, "openapi_url": None}
        if users is not None else {}
    )
    app = FastAPI(title="mig-validate", **docs_kwargs)

    @app.exception_handler(SnapshotVersionError)
    async def schema_outdated(request: Request, error: SnapshotVersionError) -> JSONResponse:
        # detail zustava retezec - app.js vsude cte body.detail jako text;
        # jen notice s tlacitkem Upgrade run se ridi podle code.
        return JSONResponse(
            status_code=422,
            content={"detail": str(error), "code": "schema_outdated"},
        )

    app.state.run_root = run_root
    app.state.profile_path = profile_path
    app.state.settings_path = settings_path
    profiles = ProfileStore(Path(profiles_root))
    app.state.profiles = profiles
    manager = CaptureManager(pool=capture_pool)
    app.state.captures = manager
    app.state.auth_enabled = users is not None
    if users is not None:
        sessions = sessions or SessionStore()
        app.state.actor_provider = session_actor_provider(sessions, users)
        app.include_router(build_auth_router(users, sessions, throttle or LoginThrottle()))
    else:
        app.state.actor_provider = anonymous_admin
    app.include_router(build_profiles_router(profiles, run_root, profile_path))
    cache = SummaryCache()
    app.state.group_cache = cache
    app.include_router(build_groups_router(
        run_root=run_root, profiles=profiles, profile_path=profile_path,
        manager=manager, cache=cache, settings_path=settings_path,
    ))

    @app.get("/api/me")
    def me(request: Request, actor: Actor = require(Permission.VIEW)) -> dict:
        return {
            "username": actor.username,
            "role": actor.role,
            "permissions": permissions_for(actor.role),
            "auth": request.app.state.auth_enabled,
        }

    @app.get("/api/checks")
    def list_checks(actor: Actor = require(Permission.VIEW)) -> dict:
        return {"checks": api.list_checks()}

    @app.get("/api/meta")
    def meta(request: Request, actor: Actor = require(Permission.VIEW)) -> dict:
        try:
            settings = load_settings(request.app.state.settings_path)
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        profile = server_default_profile(profile_path)
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

    def _profile_for(store: RunStore, manifest: RunManifest):
        try:
            return profile_for_run(
                manifest, store.manifest_path,
                store=profiles, default_path=profile_path,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def _detail(run: str) -> dict:
        store = _require_store(run)
        manifest = store.load()
        return {
            "name": run,
            "kind": manifest.kind,
            "profile": manifest.profile,
            "group": manifest.group,
            "created_by": manifest.created_by,
            "devices": _devices_dict(manifest),
            "rows": status_rows(manifest),
            "snapshots": snapshot_list(manifest, store),
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
                created_by=actor.username,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit.record(actor.username, "run-create", body.name)
        return _detail(body.name)

    @app.put("/api/runs/{run}/mapping")
    def update_mapping(run: str, body: MappingBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        try:
            api.update_mapping(run, body.mappings, run_root=run_root)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        audit.record(actor.username, "mapping-edit", run)
        return _detail(run)

    @app.post("/api/runs/{run}/archive")
    def archive_run(run: str, actor: Actor = require(Permission.OPERATE)) -> dict:
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
        audit.record(actor.username, "run-archive", run)
        return {"archived_to": target.name}

    @app.post("/api/runs/{run}/upgrade")
    def upgrade_run(run: str, dry_run: bool = False, actor: Actor = require(Permission.OPERATE)) -> dict:
        """Pregeneruje run z raw zaznamu (spec 2026-09-23). Synchronni -
        replay je offline a kratky, fronta captures se nepouziva."""
        _require_store(run)
        try:
            with manager.maintenance(run):
                report = api.upgrade_run(run, run_root=run_root, dry_run=dry_run)
        except RunBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:  # noqa: BLE001 - modal ukaze text, ne holy 500
            raise HTTPException(
                status_code=500, detail=f"{type(error).__name__}: {error}"
            ) from error
        if not dry_run:
            audit.record(actor.username, "run-upgrade", run)
        return report.to_dict()

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
        profile = _profile_for(store, manifest)
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
                port=evaluation.subject.port,
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
        manifest = store.load()
        baseline_snapshot = None
        if baseline is not None:
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
        profile = _profile_for(store, manifest)
        # Port per-port snimku z manifestu: zuzuje NEZARAZENO stejne jako
        # run evaluation. Celoboxovy zaznam (port None) i snimek mimo
        # run.yml se vyhodnoti bez zuzeni.
        record = next((c for c in manifest.captures if c.snapshot == file), None)
        result = api.evaluate(
            snapshot,
            baseline=baseline_snapshot,
            config=profile.checks,
            service_types=profile.service_types,
            profile_name=profile.name or None,
            port=record.port if record is not None else None,
        )
        return {
            "snapshot": _snapshot_meta(snapshot),
            "baseline": _snapshot_meta(baseline_snapshot)
            if baseline_snapshot else None,
            "result": result.to_dict(),
        }

    @app.post("/api/captures", status_code=202)
    def start_capture(
        body: CaptureBody, request: Request, actor: Actor = require(Permission.OPERATE)
    ) -> dict:
        store = _require_store(body.run)
        manifest = store.load()
        if body.device not in manifest.devices:
            raise HTTPException(
                status_code=404, detail=f"zarizeni '{body.device}' neni v runu"
            )
        try:
            settings = load_settings(request.app.state.settings_path)
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        profile = _profile_for(store, manifest)
        try:
            task = launch_capture(
                manager, store=store, manifest=manifest, node=body.device,
                phase=body.phase, port=body.port, parse_services=body.parse_services,
                profile=profile, settings=settings,
            )
        except DeviceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        target = f"{body.run}/{body.device}/{body.phase}"
        if body.port:
            target += f"/{body.port}"
        audit.record(actor.username, "capture-start", target)
        return {"id": task.id}

    @app.get("/api/captures/{task_id}")
    def capture_status(task_id: str, actor: Actor = require(Permission.VIEW)) -> dict:
        task = manager.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="capture nenalezen")
        return task.to_dict()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def static_no_cache(request, call_next):
        # Bez build stepu se app.js/style.css meni na miste; prohlizec musi
        # pri kazdem reloadu revalidovat (ETag drzi prenos levny).
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.middleware("http")
    async def same_origin_writes(request, call_next):
        # SameSite=Strict + tato kontrola = CSRF ochrana. Chybejici Origin
        # (curl, skripty) projde - prohlizec ho u techto metod posila vzdy.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse(status_code=403, content={"detail": "cross-origin request refused"})
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        if current_actor(request) is None:
            return RedirectResponse("/login", status_code=302)
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if not request.app.state.auth_enabled or current_actor(request) is not None:
            return RedirectResponse("/", status_code=302)
        return FileResponse(STATIC_DIR / "login.html")

    return app
