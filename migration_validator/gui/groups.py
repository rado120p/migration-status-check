"""Souhrn skupiny (bulk single runy): jeden radek na clena s fazemi,
bezicim taskem a verdiktem. Verdikt = nejhorsi Status pres evaluace runu
(stejna cesta jako GET /api/runs/{run}/evaluation), cachovany podle mtime
run.yml a souboru profilu."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from migration_validator import api
from migration_validator.config import Profile
from migration_validator.gui.captures import CaptureManager
from migration_validator.gui.profiles import profile_for_run
from migration_validator.models.result import Status, count_statuses
from migration_validator.models.snapshot import load_snapshot
from migration_validator.profiles.store import ProfileStore
from migration_validator.runs.manifest import RunManifest
from migration_validator.runs.pairing import plan_evaluations
from migration_validator.runs.store import RunStore

PHASES = ("pre", "post", "rollback")

Counts = dict[str, int]
Verdict = tuple[str | None, Counts, Counts]


def _zero() -> Counts:
    return count_statuses([])


def _add(into: Counts, counts: dict[str, Any]) -> None:
    for key in into:
        into[key] += int(counts.get(key, 0))


def run_verdict(store: RunStore, manifest: RunManifest, profile: Profile) -> Verdict:
    """(verdikt, pocty sluzeb, pocty checku). Verdikt None = zadna evaluace
    (jen pre). ValueError pri chybejicich souborech snimku."""
    missing = store.missing_snapshots(manifest)
    if missing:
        raise ValueError("chybejici soubory snimku: " + ", ".join(sorted(missing)))
    services, checks = _zero(), _zero()
    worst: list[Status] = []
    for evaluation in plan_evaluations(manifest):
        subject = load_snapshot(str(store.dir / evaluation.subject.snapshot))
        baseline = None
        if evaluation.baseline is not None:
            baseline = load_snapshot(str(store.dir / evaluation.baseline.snapshot))
        result = api.evaluate(
            subject, baseline=baseline, config=profile.checks,
            service_types=profile.service_types, profile_name=profile.name or None,
        )
        statuses = [scope.status for scope in result.scopes]
        _add(services, count_statuses(statuses))
        _add(checks, result.summary)
        worst.append(Status.worst(statuses))
    if not worst:
        return None, services, checks
    return Status.worst(worst).value, services, checks


class SummaryCache:
    """run -> (klic, verdikt). Klic = (mtime run.yml, mtime profilu)."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[tuple[int, int], Verdict]] = {}

    def get(self, run: str, key: tuple[int, int]) -> Verdict | None:
        entry = self._entries.get(run)
        return entry[1] if entry and entry[0] == key else None

    def put(self, run: str, key: tuple[int, int], value: Verdict) -> None:
        self._entries[run] = (key, value)

    def retain(self, runs: set[str]) -> None:
        for name in list(self._entries):
            if name not in runs:
                del self._entries[name]


def _profile_mtime(manifest: RunManifest, profiles: ProfileStore, default_path: str | None) -> int:
    path = profiles.path(manifest.profile) if manifest.profile else (
        Path(default_path) if default_path else None
    )
    try:
        return path.stat().st_mtime_ns if path else 0
    except OSError:
        return 0


def _member_row(
    store: RunStore, *, profiles: ProfileStore, default_path: str | None,
    manager: CaptureManager, cache: SummaryCache,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "run": store.name, "node": None, "host": None, "platform": None,
        "phases": {phase: None for phase in PHASES}, "active_task": None,
        "verdict": None, "services": _zero(), "checks": _zero(), "error": None,
    }
    task = manager.active_task(store.name)
    if task is not None:
        row["active_task"] = {"id": task.id, "phase": task.phase, "state": task.state}
    try:
        manifest = store.load()
    except (ValueError, OSError) as error:
        row["error"] = str(error)
        return row
    node, device = next(iter(manifest.devices.items()), (None, None))
    if device is not None:
        row.update({"node": node, "host": device.host, "platform": device.platform})
        for phase in PHASES:
            record = manifest.find_capture(phase, node, None)
            row["phases"][phase] = record.taken if record else None
    try:
        profile = profile_for_run(manifest, store.manifest_path, store=profiles, default_path=default_path)
        key = (store.manifest_path.stat().st_mtime_ns, _profile_mtime(manifest, profiles, default_path))
        verdict = cache.get(store.name, key)
        if verdict is None:
            verdict = run_verdict(store, manifest, profile)
            cache.put(store.name, key, verdict)
    except (ValueError, OSError) as error:
        row["error"] = str(error)
        return row
    row["verdict"], row["services"], row["checks"] = verdict
    return row


def build_group_summary(
    group: str, *, run_root: Path, profiles: ProfileStore, default_path: str | None,
    manager: CaptureManager, cache: SummaryCache,
) -> dict[str, Any] | None:
    members = api.group_runs(run_root).get(group)
    if not members:
        return None
    cache.retain(set(members))
    rows = [
        _member_row(RunStore(run_root, name), profiles=profiles, default_path=default_path,
                    manager=manager, cache=cache)
        for name in members
    ]
    verdicts: dict[str, int] = {}
    for row in rows:
        key = "error" if row["error"] else (row["verdict"] or "none")
        verdicts[key] = verdicts.get(key, 0) + 1
    header = next((g for g in api.list_groups(run_root) if g["name"] == group), {})
    return {
        "name": group,
        "profile": header.get("profile"),
        "created": header.get("created"),
        "runs": rows,
        "verdicts": verdicts,
    }
