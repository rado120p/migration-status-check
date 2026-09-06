"""Routes /api/groups - bulk single runy (spec 2026-09-06).

Cteni view, zakladani a capture operate, archivace admin. Chyby validace
(GroupError) jdou jako 409 s detail {"message", "rows": [{"index",
"message"}]}, aby je formular ukazal u radku; neznama skupina 404; zapis,
ktery selhal uprostred, 500 s jiz zapsanymi runy."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from migration_validator import api
from migration_validator.auth import load_settings
from migration_validator.gui.authz import Actor, Permission, require
from migration_validator.gui.capture_launch import launch_capture
from migration_validator.gui.captures import CaptureManager, DeviceBusy
from migration_validator.gui.groups import PHASES, SummaryCache, build_group_summary
from migration_validator.gui.profiles import profile_for_run
from migration_validator.profiles.store import ProfileStore
from migration_validator.runs.store import RunStore


class GroupDeviceBody(BaseModel):
    node: str
    host: str
    platform: str


class CreateGroupBody(BaseModel):
    group: str
    profile: str | None = None
    devices: list[GroupDeviceBody]
    capture_pre: bool = False


class AddDevicesBody(BaseModel):
    devices: list[GroupDeviceBody]


class GroupCaptureBody(BaseModel):
    phase: str


def _group_error(error: api.GroupError) -> HTTPException:
    return HTTPException(status_code=409, detail={
        "message": str(error),
        "rows": [{"index": index, "message": message} for index, message in error.rows],
    })


def _write_error(error: api.GroupWriteError) -> HTTPException:
    return HTTPException(status_code=500, detail={
        "message": str(error), "written": error.written,
    })


def build_groups_router(
    *, run_root: Path, profiles: ProfileStore, profile_path: str | None,
    manager: CaptureManager, cache: SummaryCache,
) -> APIRouter:
    router = APIRouter(prefix="/api/groups")

    def _summary_or_404(group: str) -> dict[str, Any]:
        summary = build_group_summary(
            group, run_root=run_root, profiles=profiles, default_path=profile_path,
            manager=manager, cache=cache,
        )
        if summary is None:
            raise HTTPException(status_code=404, detail=f"skupina '{group}' neexistuje")
        return summary

    def _start_batch(group: str, phase: str) -> dict[str, Any]:
        members = api.group_runs(run_root).get(group)
        if not members:
            raise HTTPException(status_code=404, detail=f"skupina '{group}' neexistuje")
        try:
            settings = load_settings()
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        tasks = []
        for name in members:
            store = RunStore(run_root, name)
            entry: dict[str, Any] = {"run": name, "device": None, "task_id": None, "error": None}
            try:
                manifest = store.load()
                node = next(iter(manifest.devices))
                entry["device"] = node
                profile = profile_for_run(
                    manifest, store.manifest_path, store=profiles, default_path=profile_path,
                )
                task = launch_capture(
                    manager, store=store, manifest=manifest, node=node, phase=phase,
                    port=None, parse_services=False, profile=profile, settings=settings,
                )
                entry["task_id"] = task.id
            except (ValueError, OSError, StopIteration, DeviceBusy, yaml.YAMLError) as error:
                entry["error"] = str(error) or "run bez zarizeni"
            tasks.append(entry)
        return {"batch": uuid.uuid4().hex[:12], "tasks": tasks}

    @router.get("")
    def list_groups(actor: Actor = require(Permission.VIEW)) -> dict:
        return {"groups": api.list_groups(run_root)}

    @router.post("", status_code=201)
    def create_group(body: CreateGroupBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        try:
            api.create_group(
                body.group, [d.model_dump() for d in body.devices],
                profile=body.profile, run_root=run_root, profiles_root=profiles.root,
            )
        except api.GroupError as error:
            raise _group_error(error) from error
        except api.GroupWriteError as error:
            raise _write_error(error) from error
        batch = _start_batch(body.group, "pre") if body.capture_pre else None
        return {**_summary_or_404(body.group), "batch": batch}

    @router.get("/{group}")
    def group_detail(group: str, actor: Actor = require(Permission.VIEW)) -> dict:
        return _summary_or_404(group)

    @router.post("/{group}/devices", status_code=201)
    def add_devices(group: str, body: AddDevicesBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        try:
            api.add_group_devices(group, [d.model_dump() for d in body.devices], run_root=run_root)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except api.GroupError as error:
            raise _group_error(error) from error
        except api.GroupWriteError as error:
            raise _write_error(error) from error
        return _summary_or_404(group)

    @router.post("/{group}/captures", status_code=202)
    def group_capture(group: str, body: GroupCaptureBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        if body.phase not in PHASES:
            raise HTTPException(
                status_code=422,
                detail=f"neznama faze '{body.phase}', ocekavano pre, post nebo rollback",
            )
        return _start_batch(group, body.phase)

    @router.post("/{group}/archive")
    def archive_group(group: str, actor: Actor = require(Permission.ADMIN)) -> dict:
        members = api.group_runs(run_root).get(group)
        if not members:
            raise HTTPException(status_code=404, detail=f"skupina '{group}' neexistuje")
        if any(manager.busy_run(name) for name in members):
            raise HTTPException(status_code=409, detail=f"skupina '{group}' ma bezici capture")
        try:
            archived = api.archive_group(group, run_root=run_root)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except api.GroupWriteError as error:
            raise _write_error(error) from error
        return {"archived": archived}

    return router
