"""mig-validate upgrade - pregeneruje inventory a snimky runu z raw zaznamu
aktualni verzi nastroje (spec 2026-09-23, sekce 3).

Poradi: inventory soubory, pak pre/rollback capture, pak post (baseline =
pregenerovane pre podle session.json, ne dnesni mapping). Vse jde do
docasneho .upgrade-staging-<nahodne>/ v runu (kazde spusteni vlastni);
pad replaye (chyba nastroje) nic nezmeni. Capture, ktere pregenerovat
nejde, zustavaji beze zmeny a upgrade ostatnich nezastavi.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
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
RUN_CHANGED = "run se behem upgradu zmenil - spust upgrade znovu"

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


def _fingerprint(store: RunStore) -> dict[str, tuple[int, int]]:
    """mtime_ns + velikost vseho, co upgrade cte nebo nahrazuje. Zmena mezi
    ctenim a vymenou = do runu mezitim zapsal capture."""
    entries: dict[str, tuple[int, int]] = {}
    for pattern in ("run.yml", "snapshot_*.json", "inventory_*.yml", "raw/*/session.json"):
        for path in store.dir.glob(pattern):
            stat = path.stat()
            entries[path.relative_to(store.dir).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return entries


def upgrade_run(
    store: RunStore,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    before_swap: Callable[[], None] | None = None,
) -> UpgradeReport:
    """Replay (kroky 1-4) bezi bez zamku - trva sekundy, capture minuty a
    nema cekat. Vymena bezi pod RunStore.lock() a jen kdyz se run od
    zacatku nezmenil (souběh s capture zachyti fingerprint)."""
    report = UpgradeReport(run=store.name, dry_run=dry_run)
    # Fingerprint pred manifestem: capture, ktery zapise mezi nimi, se pak
    # projevi jako zmena pri vymene (jinak by vymena bezela se zastaralym
    # manifestem).
    fingerprint = _fingerprint(store)
    manifest = store.load()
    staging = Path(tempfile.mkdtemp(prefix=f"{STAGING_DIR}-", dir=store.dir))
    try:
        try:
            staged = _regenerate(store, manifest, staging, report)
        except Exception as error:  # noqa: BLE001 - chyba nastroje, run zustava beze zmeny
            message = f"replay selhal - {type(error).__name__}: {error}"
            report.error = message
            if report.items:
                report.items[-1].refuse(message)
            return report
        if dry_run or not staged:
            return report
        if before_swap is not None:
            before_swap()
        with store.lock():
            if _fingerprint(store) != fingerprint:
                report.error = RUN_CHANGED
                return report
            backup = _backup_dir(store, now)
            renamed: list[str] = []
            try:
                _swap(store, staging, staged, backup, renamed)
            except OSError as error:
                failure = f"vymena selhala - {type(error).__name__}: {error}"
                if not renamed:
                    # Nic se neprejmenovalo - run je beze zmeny; prazdna
                    # zaloha (i prazdny backup/) pryc, report.backup None.
                    for empty in (backup, backup.parent):
                        with contextlib.suppress(OSError):
                            empty.rmdir()
                    report.error = failure
                    return report
                report.backup = backup.relative_to(store.dir).as_posix()
                report.error = f"{failure} - puvodni soubory v {report.backup}"
                # Castecna vymena uz zmenila soubory na disku -
                # SummaryCache GUI je klicovana mtime run.yml, takze i tady
                # se musi posunout (utime nesmi zamaskovat chybu vymeny).
                with contextlib.suppress(OSError):
                    os.utime(store.manifest_path)
                return report
            report.backup = backup.relative_to(store.dir).as_posix()
            # SummaryCache GUI je klicovana mtime run.yml - bez posunu by
            # souhrn skupiny ukazoval stary verdikt az do restartu GUI.
            os.utime(store.manifest_path)
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
        inventory = _capture_inventory(session, raw_dir, scratch)
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
        baselines=_baselines(store, session, regenerated, item) or None,
        service_types=params.get("service_types"),
        profile_name=params.get("profile_name"),
    )


def _capture_inventory(
    session: Session, raw_dir: Path, scratch: Path
) -> Inventory | None:
    """Inventory, se kterou capture bezel: z kopie raw inventory, jinak
    z kopie YAML (jen kdyz ji aktualni verze nacte). Kdyz capture s
    inventory bezel (session.inventory neni None), ale bundle nema ani
    jedno, stav se nefabrikuje - misto tise prazdne inventory se to
    odmitne (spec 2026-09-23: stav se nikdy nefabrikuje)."""
    inventory_dir = raw_dir / INVENTORY_DIR
    if has_session(inventory_dir):
        regenerate_inventory(read_session(inventory_dir), scratch)
        return load_inventory(scratch)
    copy = inventory_dir / INVENTORY_YAML
    if copy.is_file():
        try:
            return load_inventory(copy)
        except (ValueError, yaml.YAMLError) as error:
            raise _Unreadable(str(error)) from error
    if session.inventory is not None:
        raise _Unreadable(INVENTORY_NO_RAW)
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
    session: Session,
    regenerated: dict[str, Snapshot],
    item: UpgradeItem,
) -> list[Snapshot]:
    """Baseline jmenovane v session.json. Zaznamenal-li capture jejich
    identitu (baseline_taken) a snimek ma dnes jiny started_at (pre prepsana
    po post), post s ni nemeril - pouzije se bez ni (R11/R12). Bundle bez
    baseline_taken kontrolu preskoci."""
    taken = session.baseline_taken or {}
    result: list[Snapshot] = []
    for name in session.baselines or []:
        resolved = regenerated.get(name)
        if resolved is None:
            try:
                resolved = load_snapshot(store.dir / name)
            except (OSError, ValueError, KeyError, TypeError, SnapshotVersionError):
                item.notes.append(
                    f"baseline {name} nejde pregenerovat ani nacist - ping cile bez ni"
                )
                continue
        expected = taken.get(name)
        if expected is not None and resolved.capture.started_at != expected:
            item.notes.append(f"baseline {name} se od capture zmenila - ping cile bez ni")
            continue
        result.append(resolved)
    return result


def _compare(old: Path, new: Path, load: Callable[[str], Any]) -> str:
    try:
        before = load(old.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return CHANGED
    return UNCHANGED if before == load(new.read_text(encoding="utf-8")) else CHANGED


def _backup_dir(store: RunStore, now: datetime | None) -> Path:
    """Cesta k zaloze teto vymeny (jeste nevytvorena)."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return store.dir / BACKUP_DIR / f"upgrade-{stamp}"


def _swap(
    store: RunStore, staging: Path, staged: list[str], backup: Path, renamed: list[str]
) -> None:
    """Nahrazovane soubory do backup/upgrade-<cas>/, nove na jejich misto.
    Rada prejmenovani, ne atomicka operace - pri padu uprostred jsou
    v zaloze jen originaly, ktere se stihly presunout; zbytek zustal na
    miste. `renamed` dostane jmeno souboru po kazdem dokoncenem
    prejmenovani (i noveho souboru, ktery original nemel a na misto sel bez
    zalohy) - prazdny = run se nezmenil."""
    backup.mkdir(parents=True, exist_ok=True)
    for name in staged:
        current = store.dir / name
        if current.exists():
            os.replace(current, backup / name)
            renamed.append(name)
        os.replace(staging / name, current)
        renamed.append(name)


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
        text = f"  CHYBA: {report.error}"
        if report.backup is None:
            text += " - run zustal beze zmeny"
        lines.append(text)
        if report.backup is not None:
            lines.append(f"  zaloha: {report.backup}")
    elif report.backup is not None:
        lines.append(f"  zaloha: {report.backup}")
    return lines
