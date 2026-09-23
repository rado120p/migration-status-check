"""mig-validate upgrade - pregeneruje inventory a snimky runu z raw zaznamu
aktualni verzi nastroje (spec 2026-09-23, sekce 3).

Poradi: inventory soubory, pak pre/rollback capture, pak post (baseline =
pregenerovane pre podle session.json, ne dnesni mapping). Vse jde do
.upgrade-staging/; pad replaye (chyba nastroje) nic nezmeni. Capture, ktere
pregenerovat nejde, zustavaji beze zmeny a upgrade ostatnich nezastavi.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

import migration_validator.collectors.all  # noqa: F401  (registrace)
from migration_validator.capture import capture_device
from migration_validator.collectors.registry import all_collectors
from migration_validator.connection.junos import detect_platform
from migration_validator.models.inventory import Inventory, load_inventory
from migration_validator.models.snapshot import (
    Snapshot,
    SnapshotVersionError,
    load_snapshot,
    save_snapshot,
)
from migration_validator.probes.ping import DEFAULT_COUNT
from migration_validator.raw.bundle import (
    INVENTORY_DIR,
    INVENTORY_YAML,
    RawFormatError,
    Session,
    has_session,
    read_session,
)
from migration_validator.raw.calls import NotRecorded
from migration_validator.raw.replay import ReplayDevice
from migration_validator.runs.manifest import CaptureRecord, RunManifest
from migration_validator.runs.services import generate_inventory
from migration_validator.runs.store import RunStore

STAGING_DIR = ".upgrade-staging"
BACKUP_DIR = "backup"

UNCHANGED = "unchanged"
CHANGED = "changed"
NOT_REGENERABLE = "not_regenerable"

NO_RAW = "bez raw zaznamu (zachyceno pred zavedenim)"
RAW_MISMATCH = "raw nepatri k tomuto snimku"
INVENTORY_NO_RAW = "inventory bez raw zaznamu a nejde nacist"

_RESULT_TEXT = {
    UNCHANGED: "pregenerovano - beze zmeny",
    CHANGED: "pregenerovano - zmeneno",
    NOT_REGENERABLE: "nelze",
}


@dataclass
class UpgradeItem:
    kind: str  # capture | inventory
    file: str
    phase: str | None = None
    device: str | None = None
    port: str | None = None
    result: str = UNCHANGED
    reason: str | None = None
    notes: list[str] = field(default_factory=list)

    def refuse(self, reason: str) -> None:
        self.result = NOT_REGENERABLE
        self.reason = reason


@dataclass
class UpgradeReport:
    run: str
    dry_run: bool
    items: list[UpgradeItem] = field(default_factory=list)
    backup: str | None = None
    error: str | None = None

    @property
    def exit_code(self) -> int:
        """0 vse pregenerovano, 1 nektere capture nejdou, 2 upgrade runu spadl."""
        if self.error is not None:
            return 2
        if any(item.result == NOT_REGENERABLE for item in self.items):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "dry_run": self.dry_run,
            "backup": self.backup,
            "items": [asdict(item) for item in self.items],
            "error": self.error,
        }


class _Unreadable(Exception):
    """Inventory bez raw, kterou aktualni verze nenacte."""


def upgrade_run(
    store: RunStore,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> UpgradeReport:
    report = UpgradeReport(run=store.name, dry_run=dry_run)
    manifest = store.load()
    staging = store.dir / STAGING_DIR
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        try:
            staged = _regenerate(store, manifest, staging, report)
        except Exception as error:  # noqa: BLE001 - chyba nastroje, run zustava beze zmeny
            report.error = f"replay selhal - {type(error).__name__}: {error}"
            return report
        if dry_run or not staged:
            return report
        report.backup = _swap(store, staging, staged, now)
        return report
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def regenerate_inventory(session: Session, target: Path) -> None:
    """Inventory ze session --parse-services aktualnim parserem, se stejnym
    port filtrem jako puvodne."""
    device = ReplayDevice(session)
    generate_inventory(device, detect_platform(device), target, session.port_filter)


def _regenerate(
    store: RunStore, manifest: RunManifest, staging: Path, report: UpgradeReport
) -> list[str]:
    """Pregeneruje vse do stagingu. Vraci jmena souboru, ktere se zmenily."""
    staged: list[str] = []

    for path in sorted(store.dir.glob("inventory_*.yml")):
        item = UpgradeItem(kind="inventory", file=path.name)
        report.items.append(item)
        raw_dir = store.raw_dir(path.name)
        if not has_session(raw_dir):
            item.refuse(NO_RAW)
            continue
        target = staging / path.name
        try:
            regenerate_inventory(read_session(raw_dir), target)
        except (NotRecorded, RawFormatError) as error:
            item.refuse(str(error))
            continue
        item.result = _compare(path, target, yaml.safe_load)
        if item.result == CHANGED:
            staged.append(path.name)

    regenerated: dict[str, Snapshot] = {}
    ordered = [r for r in manifest.captures if r.phase != "post"] + [
        r for r in manifest.captures if r.phase == "post"
    ]
    for record in ordered:
        item = UpgradeItem(
            kind="capture", file=record.snapshot, phase=record.phase,
            device=record.device, port=record.port,
        )
        report.items.append(item)
        snapshot = _replay_capture(store, record, staging, regenerated, item)
        if snapshot is None:
            continue
        regenerated[record.snapshot] = snapshot
        target = staging / record.snapshot
        save_snapshot(snapshot, target)
        item.result = _compare(store.dir / record.snapshot, target, json.loads)
        if item.result == CHANGED:
            staged.append(record.snapshot)
    return staged


def _replay_capture(
    store: RunStore,
    record: CaptureRecord,
    staging: Path,
    regenerated: dict[str, Snapshot],
    item: UpgradeItem,
) -> Snapshot | None:
    raw_dir = store.raw_dir(record.snapshot)
    if not has_session(raw_dir):
        item.refuse(NO_RAW)
        return None
    try:
        session = read_session(raw_dir)
    except RawFormatError as error:
        item.refuse(str(error))
        return None
    if session.started_at != record.taken:
        item.refuse(RAW_MISMATCH)
        return None
    scratch = staging / ".inventory" / f"{Path(record.snapshot).stem}.yml"
    try:
        inventory = _capture_inventory(raw_dir, scratch)
    except (NotRecorded, RawFormatError) as error:
        item.refuse(str(error))
        return None
    except _Unreadable:
        item.refuse(INVENTORY_NO_RAW)
        return None
    params = session.params or {}
    return capture_device(
        ReplayDevice(session),
        session.address or record.device,
        inventory=inventory,
        collector_names=_known_collectors(params.get("collectors"), item),
        phase=params.get("phase", record.phase),
        ping_count=params.get("ping_count", DEFAULT_COUNT),
        now=session.started_at,
        finished_at=session.finished_at,
        baselines=_baselines(store, session.baselines or [], regenerated, item) or None,
        service_types=params.get("service_types"),
        profile_name=params.get("profile_name"),
    )


def _capture_inventory(raw_dir: Path, scratch: Path) -> Inventory | None:
    """Inventory, se kterou capture bezel: z kopie raw inventory, jinak
    z kopie YAML (jen kdyz ji aktualni verze nacte)."""
    inventory_dir = raw_dir / INVENTORY_DIR
    if has_session(inventory_dir):
        regenerate_inventory(read_session(inventory_dir), scratch)
        return load_inventory(scratch)
    copy = inventory_dir / INVENTORY_YAML
    if copy.is_file():
        try:
            return load_inventory(copy)
        except ValueError as error:
            raise _Unreadable(str(error)) from error
    return None


def _known_collectors(names: list[str] | None, item: UpgradeItem) -> list[str] | None:
    """None = vsechny collectory aktualni verze. Jmeno, ktere tahle verze
    nezna (prejmenovany/odstraneny collector), se vypusti s poznamkou -
    _select_collectors by na nem jinak spadl."""
    if names is None:
        return None
    known = {collector.name for collector in all_collectors()}
    for name in names:
        if name not in known:
            item.notes.append(f"collector {name} v teto verzi neexistuje")
    return [name for name in names if name in known]


def _baselines(
    store: RunStore,
    names: list[str],
    regenerated: dict[str, Snapshot],
    item: UpgradeItem,
) -> list[Snapshot]:
    result: list[Snapshot] = []
    for name in names:
        if name in regenerated:
            result.append(regenerated[name])
            continue
        try:
            result.append(load_snapshot(store.dir / name))
        except (OSError, ValueError, KeyError, TypeError, SnapshotVersionError):
            item.notes.append(
                f"baseline {name} nejde pregenerovat ani nacist - ping cile bez ni"
            )
    return result


def _compare(old: Path, new: Path, load: Callable[[str], Any]) -> str:
    try:
        before = load(old.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return CHANGED
    return UNCHANGED if before == load(new.read_text(encoding="utf-8")) else CHANGED


def _swap(store: RunStore, staging: Path, staged: list[str], now: datetime | None) -> str:
    """Nahrazovane soubory do backup/upgrade-<cas>/, nove na jejich misto.
    Rada prejmenovani, ne atomicka operace - pri padu uprostred je vse
    puvodni v zaloze."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup = store.dir / BACKUP_DIR / f"upgrade-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for name in staged:
        current = store.dir / name
        if current.exists():
            os.replace(current, backup / name)
        os.replace(staging / name, current)
    return backup.relative_to(store.dir).as_posix()


def render_report(report: UpgradeReport) -> list[str]:
    lines = [f"run {report.run}" + (" (dry-run)" if report.dry_run else "")]
    for item in report.items:
        if item.kind == "capture":
            label = f"{item.phase or '?':<8} {item.device} {item.port or 'all'}"
        else:
            label = f"inventory {item.file}"
        text = _RESULT_TEXT[item.result]
        if item.reason:
            text = f"{text} - {item.reason}"
        lines.append(f"  {label:<44} {text}")
        lines.extend(f"    ! {note}" for note in item.notes)
    if report.error is not None:
        lines.append(f"  CHYBA: {report.error} - run zustal beze zmeny")
    elif report.backup is not None:
        lines.append(f"  zaloha: {report.backup}")
    return lines
