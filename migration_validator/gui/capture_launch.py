"""Spusteni capture do runu pres CaptureManager - spolecne pro
POST /api/captures a davkovy capture skupiny."""

from __future__ import annotations

from migration_validator.auth import ConnectionSettings
from migration_validator.config import Profile
from migration_validator.connection.junos import ConnectionOptions
from migration_validator.gui.captures import CaptureManager, CaptureTask
from migration_validator.runs.manifest import RunManifest
from migration_validator.runs.orchestrate import capture_into_run
from migration_validator.runs.store import RunStore


def launch_capture(
    manager: CaptureManager,
    *,
    store: RunStore,
    manifest: RunManifest,
    node: str,
    phase: str,
    port: str | None,
    parse_services: bool,
    profile: Profile,
    settings: ConnectionSettings,
) -> CaptureTask:
    """Zaradi capture zarizeni `node` do manageru. KeyError, kdyz node
    v runu neni; DeviceBusy propada z manageru."""
    device = manifest.devices[node]
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
            phase=phase,
            port=port,
            options=options,
            profile=profile,
            parse_services=parse_services,
            overwrite=True,  # GUI resi prepis potvrzenim ve formulari
            on_progress=on_progress,
        )

    return manager.start(fn, run=store.name, device=node, port=port, phase=phase)
