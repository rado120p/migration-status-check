"""Programove API - jediny sev, ktery bude volat GUI.

CLI je tenky obal nad timto modulem, ne alternativni implementace.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from migration_validator.capture import ProgressCallback, capture_device
from migration_validator.checks.all import load_all
from migration_validator.checks.registry import all_checks
from migration_validator.config import CheckConfig
from migration_validator.connection.junos import ConnectionOptions, connect
from migration_validator.engine import evaluate_snapshots
from migration_validator.models.inventory import Inventory, load_inventory
from migration_validator.models.result import RunResult
from migration_validator.models.snapshot import Snapshot
from migration_validator.runs.manifest import (
    RUN_KINDS,
    MappingEndpoint,
    RunDevice,
    RunManifest,
    check_kind_devices,
)
from migration_validator.runs.store import RunStore
from migration_validator.scoping.mapping import Mapping


def capture(
    host: str,
    *,
    inventory: Inventory | str | None = None,
    options: ConnectionOptions | None = None,
    collectors: list[str] | None = None,
    phase: str | None = None,
    ping_count: int = 5,
    record_raw: str | None = None,
    baselines: list[Snapshot] | None = None,
    service_types: list[str] | None = None,
    on_progress: ProgressCallback | None = None,
    profile_name: str | None = None,
) -> Snapshot:
    """Sebere stav zarizeni a vrati self-contained snapshot.

    `baselines` jsou pre snimky starych boxu mapovanych na tento port - ping
    cile pro --phase post se prednostne odvozuji ze sjednoceni jejich ARP/ND.
    """
    if isinstance(inventory, str):
        inventory = load_inventory(inventory)

    options = options or ConnectionOptions(host=host)
    with connect(options) as device:
        return capture_device(
            device,
            address=host,
            inventory=inventory,
            collector_names=collectors,
            phase=phase,
            ping_count=ping_count,
            record_raw=record_raw,
            baselines=baselines,
            service_types=service_types,
            on_progress=on_progress,
            profile_name=profile_name,
        )


def evaluate(
    snapshot: Snapshot,
    *,
    baseline: Snapshot | None = None,
    mapping: Mapping | None = None,
    config: CheckConfig | None = None,
    now: str | None = None,
    service_types: list[str] | None = None,
    profile_name: str | None = None,
    step: dict[str, Any] | None = None,
) -> RunResult:
    """Vyhodnoti snapshot, volitelne proti baseline snapshotu."""
    load_all()
    return evaluate_snapshots(
        subject=snapshot,
        baseline=baseline,
        mapping=mapping,
        config=config,
        now=now,
        service_types=service_types,
        profile_name=profile_name,
        step=step,
    )


def list_checks() -> list[dict[str, Any]]:
    """Popis vsech registrovanych checku - pro CLI i GUI."""
    load_all()
    return [check.describe() for check in all_checks()]


_RUN_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
_DEVICE_KEYS = ("node", "host", "platform", "role")


def _run_device(data: dict[str, str]) -> tuple[str, RunDevice]:
    label = data.get("node") or data.get("role") or "?"
    for key in _DEVICE_KEYS:
        if not data.get(key):
            raise ValueError(f"zarizeni '{label}': chybi '{key}'")
    try:
        device = RunDevice(host=data["host"], platform=data["platform"], role=data["role"])
    except ValueError as exc:
        raise ValueError(f"zarizeni '{data['node']}': {exc}") from exc
    return data["node"], device


def create_run(
    name: str,
    *,
    kind: str,
    devices: list[dict[str, str]],
    mappings: list[tuple[str, str]] | None = None,
    profile: str | None = None,
    run_root: str | Path = Path("runs"),
) -> RunManifest:
    """Zalozi runs/<name>/run.yml - schopnost, kterou CLI nema (run.yml
    se dosud psal rucne).

    kind "single": prave jedno zarizeni role single, zadny mapping.
    kind "migration": prave jeden old a jeden new; mapping je volitelny,
    bez nej vznika sekvencni run s volnym capture formularem.
    `profile` se overi proti profile store (spec 3); do te doby je kazda
    nenulova hodnota chyba."""
    if not _RUN_NAME_RE.match(name):
        raise ValueError(
            f"nevalidni jmeno runu '{name}' - povolene znaky: a-z 0-9 _ -"
        )
    store = RunStore(Path(run_root), name)
    if store.dir.exists():
        raise ValueError(f"run '{name}' uz existuje ({store.dir})")
    if kind not in RUN_KINDS:
        raise ValueError(f"neznamy kind '{kind}', ocekavano single nebo migration")

    loaded: dict[str, RunDevice] = {}
    for data in devices:
        node, device = _run_device(data)
        if node in loaded:
            raise ValueError(f"zarizeni '{node}' je uvedeno dvakrat")
        loaded[node] = device
    check_kind_devices(kind, loaded)

    manifest = RunManifest(devices=loaded, kind=kind, profile=profile)
    if kind == "single":
        if mappings:
            raise ValueError("run typu single nema interface mapping")
    else:
        roles = [device.role for device in loaded.values()]
        if roles.count("old") != 1 or roles.count("new") != 1:
            raise ValueError(
                "run typu migration ma prave jedno zarizeni role 'old' a jedno role 'new'"
            )
        old_node = manifest.device_with_role("old")[0]
        new_node = manifest.device_with_role("new")[0]
        for old_port, new_port in mappings or []:
            manifest.add_mapping(
                old=MappingEndpoint(node=old_node, port=old_port.strip()),
                new=MappingEndpoint(node=new_node, port=new_port.strip()),
            )

    if profile is not None:
        raise ValueError(
            f"profil '{profile}' neexistuje - profile store zatim neni k dispozici"
        )

    store.save(manifest)
    return manifest


ARCHIVE_DIR = ".archive"


def archive_run(
    name: str,
    *,
    run_root: str | Path = Path("runs"),
    now: datetime | None = None,
) -> Path:
    """Presune runs/<name>/ do runs/.archive/<name>-<UTC stamp>/.

    Rename na stejnem filesystemu je atomicky - run bud zmizi cely, nebo
    vubec. Teckovany adresar list_runs preskakuje."""
    if not _RUN_NAME_RE.match(name):
        raise ValueError(
            f"nevalidni jmeno runu '{name}' - povolene znaky: a-z 0-9 _ -"
        )
    store = RunStore(Path(run_root), name)
    if not store.manifest_path.exists():
        raise FileNotFoundError(f"run '{name}' neexistuje ({store.dir})")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    archive = Path(run_root) / ARCHIVE_DIR
    archive.mkdir(exist_ok=True)
    target = archive / f"{name}-{stamp}"
    store.dir.rename(target)
    return target


_ARCHIVE_STAMP_RE = re.compile(r"^(?P<name>[a-z0-9_-]+)-(?P<stamp>\d{8}T\d{6}Z)$")


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    archived: datetime
    snapshots: int
    path: Path


def list_archive(run_root: str | Path = Path("runs")) -> list[ArchiveEntry]:
    """Polozky runs/.archive/ serazene od nejstarsi. Adresare, ktere
    nevypadaji jako <name>-<stamp>, se preskakuji - nikdy se nemazou."""
    archive = Path(run_root) / ARCHIVE_DIR
    if not archive.is_dir():
        return []
    entries: list[ArchiveEntry] = []
    for entry in archive.iterdir():
        match = _ARCHIVE_STAMP_RE.match(entry.name)
        if not entry.is_dir() or match is None:
            continue
        archived = datetime.strptime(
            match.group("stamp"), "%Y%m%dT%H%M%SZ"
        ).replace(tzinfo=timezone.utc)
        snapshots = len(list(entry.glob("snapshot_*.json")))
        entries.append(ArchiveEntry(
            name=match.group("name"), archived=archived,
            snapshots=snapshots, path=entry,
        ))
    return sorted(entries, key=lambda e: (e.archived, e.name))


def archive_threshold(older_than_days: int, now: datetime | None = None) -> datetime:
    """Hranicni cas: polozky archivovane v tomto case nebo drive se povazuji
    za starsi nez older_than_days. Sdileno mezi purge_archive a CLI, aby
    vypis a mazani pouzivaly stejne pravidlo."""
    return (now or datetime.now(timezone.utc)) - timedelta(days=older_than_days)


def purge_archive(
    run_root: str | Path = Path("runs"),
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> list[ArchiveEntry]:
    """Smaze archivovane runy starsi nez older_than_days. Vraci smazane."""
    threshold = archive_threshold(older_than_days, now)
    removed: list[ArchiveEntry] = []
    for entry in list_archive(run_root):
        if entry.archived <= threshold:
            shutil.rmtree(entry.path)
            removed.append(entry)
    return removed


def _mapping_locked(manifest: RunManifest, mapping) -> bool:
    """Pairing je zamceny, kdyz na nekterem konci existuje snimek.
    Celozarizeni snimek (port=None) zamyka vsechny pairingy sveho boxu."""
    endpoints = {
        (mapping.old.node, mapping.old.port),
        (mapping.new.node, mapping.new.port),
    }
    for record in manifest.captures:
        # Presne endpoint (port se shoduje)
        if (record.device, record.port) in endpoints:
            return True
        # Celozarizeni snimek (port=None) zamyka vsechny pairingy jeho zarizeni
        if record.port is None and record.device in {mapping.old.node, mapping.new.node}:
            return True
    return False


def update_mapping(
    run: str,
    mappings: list[tuple[str, str]],
    *,
    run_root: str | Path = Path("runs"),
) -> RunManifest:
    """Prepise interface_mapping runu na pozadovany seznam.

    Zamek se overuje na serveru, ne v GUI: pairing se snimky na kteremkoliv
    konci nesmi z pozadovaneho seznamu zmizet."""
    store = RunStore(Path(run_root), run)
    if not store.manifest_path.exists():
        raise ValueError(f"run '{run}' neexistuje ({store.manifest_path})")
    manifest = store.load()

    if manifest.kind == "single":
        raise ValueError("run typu single nema interface mapping")

    old_role = manifest.device_with_role("old")
    new_role = manifest.device_with_role("new")
    if old_role is None or new_role is None:
        raise ValueError(f"run '{run}' nema zarizeni role old a new")
    old_node, new_node = old_role[0], new_role[0]

    desired = {(o.strip(), n.strip()) for o, n in mappings}
    for mapping in manifest.interface_mapping:
        pair = (mapping.old.port, mapping.new.port)
        if _mapping_locked(manifest, mapping) and pair not in desired:
            raise ValueError(
                f"pairing {mapping.old.node}:{mapping.old.port} -> "
                f"{mapping.new.node}:{mapping.new.port} ma snimky, nelze odebrat"
            )

    rebuilt = RunManifest(
        devices=manifest.devices,
        captures=manifest.captures,
        kind=manifest.kind,
        profile=manifest.profile,
        group=manifest.group,
    )
    for old_port, new_port in mappings:
        rebuilt.add_mapping(
            old=MappingEndpoint(node=old_node, port=old_port.strip()),
            new=MappingEndpoint(node=new_node, port=new_port.strip()),
        )

    store.save(rebuilt)
    return rebuilt
