"""Prevod manifestu na JSON tvary pro GUI - zrcadli cli._status_rows."""

from __future__ import annotations

from migration_validator.models.snapshot import SCHEMA_VERSION, peek_schema_version
from migration_validator.raw.bundle import has_session
from migration_validator.runs.manifest import RunManifest
from migration_validator.runs.store import RunStore


def status_rows(manifest: RunManifest) -> list[dict]:
    rows: list[dict] = []
    for mapping in manifest.interface_mapping:
        old, new = mapping.old, mapping.new
        rows.append({
            "old": {"node": old.node, "port": old.port},
            "new": {"node": new.node, "port": new.port},
            "pre": manifest.find_capture("pre", old.node, old.port) is not None,
            "post": manifest.find_capture("post", new.node, new.port) is not None,
            "rollback": manifest.find_capture("rollback", old.node, old.port)
            is not None,
        })

    seen: set[str] = set()
    for capture in manifest.captures:
        if capture.port is not None or capture.device in seen:
            continue
        seen.add(capture.device)
        device = manifest.devices.get(capture.device)
        endpoint = {"node": capture.device, "port": None}
        flags = {
            "pre": manifest.find_capture("pre", capture.device, None) is not None,
            "post": manifest.find_capture("post", capture.device, None) is not None,
            "rollback": manifest.find_capture("rollback", capture.device, None)
            is not None,
        }
        if device is not None and device.role == "new":
            rows.append({"old": None, "new": endpoint, **flags})
        else:
            rows.append({"old": endpoint, "new": None, **flags})
    return rows


def snapshot_list(manifest: RunManifest, store: RunStore | None = None) -> list[dict]:
    rows = []
    for record in manifest.captures:
        row = {
            "file": record.snapshot,
            "phase": record.phase,
            "device": record.device,
            "port": record.port,
            "taken": record.taken,
        }
        if store is not None:
            version = peek_schema_version(store.dir / record.snapshot)
            row["schema_version"] = version
            row["outdated"] = version != SCHEMA_VERSION
            row["has_raw"] = has_session(store.raw_dir(record.snapshot))
        rows.append(row)
    return rows
