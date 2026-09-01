"""FastAPI aplikace - routes jsou tenke obaly nad migration_validator.api."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from migration_validator import api
from migration_validator.gui.serializers import snapshot_list, status_rows
from migration_validator.runs.store import RunStore


def _run_summary(store: RunStore) -> dict:
    manifest = store.load()
    return {
        "name": store.name,
        "devices": {
            node: {"host": d.host, "platform": d.platform, "role": d.role}
            for node, d in manifest.devices.items()
        },
        "snapshots": len(manifest.captures),
        "mapped_ports": len(manifest.interface_mapping),
    }


def create_app(
    run_root: Path = Path("runs"), profile_path: str | None = None
) -> FastAPI:
    app = FastAPI(title="mig-validate")
    app.state.run_root = run_root
    app.state.profile_path = profile_path

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

    @app.get("/api/runs/{run}")
    def run_detail(run: str) -> dict:
        store = _require_store(run)
        manifest = store.load()
        return {
            "name": run,
            "devices": _run_summary(store)["devices"],
            "rows": status_rows(manifest),
            "snapshots": snapshot_list(manifest),
        }

    return app
