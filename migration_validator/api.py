"""Programove API - jediny sev, ktery bude volat GUI.

CLI je tenky obal nad timto modulem, ne alternativni implementace.
"""

from __future__ import annotations

import re
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
    MappingEndpoint,
    RunDevice,
    RunManifest,
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
_DEVICE_KEYS = ("node", "host", "platform")


def _run_device(data: dict[str, str], role: str) -> tuple[str, RunDevice]:
    for key in _DEVICE_KEYS:
        if not data.get(key):
            raise ValueError(f"zarizeni role '{role}': chybi '{key}'")
    return data["node"], RunDevice(
        host=data["host"], platform=data["platform"], role=role
    )


def create_run(
    name: str,
    *,
    old_device: dict[str, str],
    new_device: dict[str, str],
    mappings: list[tuple[str, str]] | None = None,
    run_root: str | Path = Path("runs"),
) -> RunManifest:
    """Zalozi runs/<name>/run.yml - schopnost, kterou CLI nema (run.yml
    se dosud psal rucne). Mapping je volitelny: bez nej vznika
    sekvencni run s volnym capture formularem."""
    if not _RUN_NAME_RE.match(name):
        raise ValueError(
            f"nevalidni jmeno runu '{name}' - povolene znaky: a-z 0-9 _ -"
        )
    store = RunStore(Path(run_root), name)
    if store.dir.exists():
        raise ValueError(f"run '{name}' uz existuje ({store.dir})")

    old_node, old = _run_device(old_device, "old")
    new_node, new = _run_device(new_device, "new")

    manifest = RunManifest(devices={old_node: old, new_node: new})
    for old_port, new_port in mappings or []:
        manifest.add_mapping(
            old=MappingEndpoint(node=old_node, port=old_port.strip()),
            new=MappingEndpoint(node=new_node, port=new_port.strip()),
        )

    store.save(manifest)
    return manifest
