"""capture_into_run - sdilena orchestrace pro CLI i GUI.

Presunuto z cli.py (Task 5, GUI plan 2026-09-01): tento modul nezna
argparse.Namespace, jen typovane parametry - CLI i GUI ho volaji stejne.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from migration_validator import api
from migration_validator.capture import ProgressCallback
from migration_validator.config import PING_COUNT_DEFAULT, Profile
from migration_validator.connection.junos import (
    ConnectionOptions,
    connect,
    detect_platform,
)
from migration_validator.models.snapshot import (
    Snapshot,
    SnapshotVersionError,
    load_snapshot,
    save_snapshot,
)
from migration_validator.runs.manifest import CaptureRecord, RunDevice
from migration_validator.runs.pairing import find_pre_baseline
from migration_validator.runs.services import generate_inventory
from migration_validator.runs.store import RunStore

_PHASE_TO_ROLE = {"pre": "old", "rollback": "old", "post": "new"}


def _load_baseline_snapshot(path: str) -> Snapshot:
    try:
        return load_snapshot(path)
    except FileNotFoundError as error:
        raise ValueError(f"snapshot nenalezen: {path}") from error
    except SnapshotVersionError as error:
        raise ValueError(str(error)) from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: nevalidni JSON ({error})") from error
    except (KeyError, TypeError) as error:
        raise ValueError(f"{path}: poskozeny snapshot ({error})") from error


@dataclass
class CaptureOutcome:
    """Vysledek capture_into_run - co CLI/GUI vypise uzivateli."""

    snapshot_path: Path
    failed_collectors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _inventory_interfaces(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return set()
    return {
        entry.get("interface")
        for entry in raw.get("interfaces") or []
        if entry.get("interface")
    }


def _parse_services(
    options: ConnectionOptions,
    port: str | None,
    inventory_path: Path,
    warnings: list[str],
) -> None:
    """Vyrobi inventory pro parse_services v samostatnem kratkem spojeni.

    Existujici soubor se pregeneruje - konfigurace noveho boxu se meni
    kazdou vlnou a flag je explicitni umysl (revize faze 4, viz spec
    2026-08-17). api.capture se nemeni - konfigurace pro inventory se
    stahne pred snapshotem.
    """
    previous = _inventory_interfaces(inventory_path)

    with connect(options) as device:
        platform = detect_platform(device)
        generate_inventory(device, platform, inventory_path, port)

    current = _inventory_interfaces(inventory_path) or set()
    if previous is None:
        warnings.append(f"inventory vyrobena: {inventory_path} ({len(current)} sluzeb)")
    else:
        added = len(current - previous)
        removed = len(previous - current)
        warnings.append(
            f"inventory pregenerovana: {inventory_path} "
            f"({len(current)} sluzeb, +{added} nove, -{removed} odebrane)"
        )


def capture_into_run(
    store: RunStore,
    *,
    host: str,
    phase: str,
    port: str | None,
    options: ConnectionOptions,
    profile: Profile,
    parse_services: bool = False,
    inventory: str | None = None,
    overwrite: bool = False,
    collectors: list[str] | None = None,
    ping_count: int | None = None,
    service_types: list[str] | None = None,
    record_raw: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> CaptureOutcome:
    """Sebere snapshot a zapise ho do run adresare (manifest + soubor).

    Raises ValueError pro vstupni problemy (neznama faze, pre bez
    --overwrite, chybejici inventory); JunosConnectionError propaguje.
    """
    warnings: list[str] = []

    if phase not in _PHASE_TO_ROLE:
        raise ValueError(
            f"neznama faze '{phase}', ocekavano jedno z {sorted(_PHASE_TO_ROLE)}"
        )

    manifest = store.load()
    node = manifest.node_for_host(host) or host

    if manifest.kind == "single" and node not in manifest.devices:
        raise ValueError(
            f"run typu single ma jedine zarizeni {sorted(manifest.devices)}, "
            f"host '{host}' v nem neni"
        )

    if phase == "pre" and not overwrite:
        existing = manifest.find_capture("pre", node, port)
        if existing is not None:
            raise ValueError(
                f"pre snimek uz existuje: {existing.snapshot}; "
                "prepis povol s --overwrite"
            )

    if inventory:
        inventory_path = Path(inventory)
    elif parse_services:
        inventory_path = store.inventory_path(node, port)
        _parse_services(options, port, inventory_path, warnings)
    else:
        inventory_path = store.inventory_path(node, port)
        if not inventory_path.exists():
            inventory_path = store.inventory_path(node, None)
        if not inventory_path.exists():
            raise ValueError("inventory nenalezena - spust s --parse-services")

    baselines: list[Snapshot] = []
    if phase == "post":
        seen_snapshots: set[str] = set()
        records = []
        for old in manifest.mapped_olds(node, port) if port else []:
            record = manifest.find_capture("pre", old.node, old.port) or manifest.find_capture(
                "pre", old.node, None
            )
            if record is not None and record.snapshot not in seen_snapshots:
                seen_snapshots.add(record.snapshot)
                records.append(record)
        if not records:
            fallback = find_pre_baseline(manifest, node, port)
            if fallback is not None:
                records.append(fallback)
        for record in records:
            baselines.append(_load_baseline_snapshot(str(store.dir / record.snapshot)))
        if not baselines:
            print("pre snimek nenalezen, ping cile z vlastni ARP", file=sys.stderr)

    collectors = collectors if collectors is not None else profile.collectors
    ping_count = ping_count if ping_count is not None else (profile.ping_count or PING_COUNT_DEFAULT)
    service_types = service_types if service_types is not None else profile.service_types

    snapshot = api.capture(
        host,
        inventory=str(inventory_path),
        options=options,
        collectors=collectors,
        phase=phase,
        ping_count=ping_count,
        record_raw=record_raw,
        baselines=baselines or None,
        service_types=service_types,
        on_progress=on_progress,
        profile_name=profile.name or None,
    )

    if node not in manifest.devices:
        manifest.devices[node] = RunDevice(
            host=host,
            platform=snapshot.device.platform,
            role=_PHASE_TO_ROLE[phase],
        )

    snapshot_path = store.snapshot_path(phase, node, port)
    save_snapshot(snapshot, snapshot_path)
    manifest.record_capture(
        CaptureRecord(
            phase=phase,
            device=node,
            port=port,
            snapshot=store.snapshot_name(phase, node, port),
            taken=snapshot.capture.started_at,
        )
    )

    store.save(manifest)

    failed = snapshot.capture.failed_collectors()

    return CaptureOutcome(
        snapshot_path=snapshot_path,
        failed_collectors=failed,
        warnings=warnings,
    )
