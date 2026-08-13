"""Programove API - jediny sev, ktery bude volat GUI.

CLI je tenky obal nad timto modulem, ne alternativni implementace.
"""

from __future__ import annotations

from typing import Any

from migration_validator.capture import capture_device
from migration_validator.checks.all import load_all
from migration_validator.checks.registry import all_checks
from migration_validator.config import CheckConfig
from migration_validator.connection.junos import ConnectionOptions, connect
from migration_validator.engine import evaluate_snapshots
from migration_validator.models.inventory import Inventory, load_inventory
from migration_validator.models.result import RunResult
from migration_validator.models.snapshot import Snapshot
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
    baseline: Snapshot | None = None,
    service_types: list[str] | None = None,
) -> Snapshot:
    """Sebere stav zarizeni a vrati self-contained snapshot.

    `baseline` je pre snimek stareho boxu - kdyz je dany, ping cile pro
    --phase post se prednostne odvozuji z jeho ARP/ND (novy box po cutoveru
    jeste nema vlastni ARP napliene).
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
            baseline=baseline,
            service_types=service_types,
        )


def evaluate(
    snapshot: Snapshot,
    *,
    baseline: Snapshot | None = None,
    mapping: Mapping | None = None,
    config: CheckConfig | None = None,
    now: str | None = None,
) -> RunResult:
    """Vyhodnoti snapshot, volitelne proti baseline snapshotu."""
    load_all()
    return evaluate_snapshots(
        subject=snapshot, baseline=baseline, mapping=mapping, config=config, now=now
    )


def list_checks() -> list[dict[str, Any]]:
    """Popis vsech registrovanych checku - pro CLI i GUI."""
    load_all()
    return [check.describe() for check in all_checks()]
