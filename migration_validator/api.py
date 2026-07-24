"""Programove API - jediny sev, ktery bude volat GUI.

CLI je tenky obal nad timto modulem, ne alternativni implementace.
capture() doplni Plan 2.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.all import load_all
from migration_validator.checks.registry import all_checks
from migration_validator.config import CheckConfig
from migration_validator.engine import evaluate_snapshots
from migration_validator.models.result import RunResult
from migration_validator.models.snapshot import Snapshot
from migration_validator.scoping.mapping import Mapping


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
