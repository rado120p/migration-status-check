"""Ktery profil run pouziva.

run.yml `profile: <name>` ukazuje do profile store; None = serverovy
default (`--profile` flag, nebo vestaveny prazdny profil). Chybejici profil
je chyba - zadny tichy fallback na default, run by se vyhodnotil jinak,
nez si operator myslel.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from migration_validator.config import Profile, default_profile, load_profile
from migration_validator.profiles.store import ProfileStore
from migration_validator.runs.manifest import RunManifest


def server_default_profile(default_path: str | None) -> Profile:
    return load_profile(default_path) if default_path else default_profile()


def profile_for_run(
    manifest: RunManifest,
    manifest_path: Path,
    *,
    store: ProfileStore,
    default_path: str | None,
) -> Profile:
    if manifest.profile is None:
        return server_default_profile(default_path)
    try:
        return store.load(manifest.profile)
    except FileNotFoundError as error:
        raise ValueError(
            f"profil '{manifest.profile}' neexistuje ({manifest_path})"
        ) from error


def profile_usage(run_root: Path) -> dict[str, int]:
    """Kolik run.yml pod run_root odkazuje na ktery profil. Cte jen klic
    `profile` primo z YAMLu, aby rozbity manifest nezastavil vypis;
    teckovane adresare (archiv) se preskakuji jako v GET /api/runs."""
    counts: dict[str, int] = {}
    if not run_root.is_dir():
        return counts
    for entry in sorted(run_root.iterdir()):
        if entry.name.startswith("."):
            continue
        manifest_path = entry / "run.yml"
        if not manifest_path.is_file():
            continue
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        name = raw.get("profile") if isinstance(raw, dict) else None
        if name:
            counts[str(name)] = counts.get(str(name), 0) + 1
    return counts
