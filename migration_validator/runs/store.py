"""Run adresar - sprava manifest a snapshot souboru."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from migration_validator.runs.manifest import RunManifest, load_manifest, normalize_port, save_manifest

RAW_DIR = "raw"
LOCK_FILE = ".lock"


def raw_name(file_name: str) -> str:
    """Jmeno raw adresare k souboru runu (spec 2026-09-23):
    snapshot_<X>.json -> <X>, inventory_<Y>.yml -> inventory_<Y>."""
    return Path(file_name).stem.removeprefix("snapshot_")


@dataclass
class RunStore:
    """Sprava adresare pro jednu fazi migrace (pre, post, atd.)."""

    root: Path
    name: str

    @property
    def dir(self) -> Path:
        """Cesta k adresari run."""
        return self.root / self.name

    @property
    def manifest_path(self) -> Path:
        """Cesta k run.yml souboru."""
        return self.dir / "run.yml"

    def load(self) -> RunManifest:
        """Nacti manifest z run.yml, nebo vrat prazdny manifest."""
        try:
            return load_manifest(self.manifest_path)
        except FileNotFoundError:
            return RunManifest(devices={}, interface_mapping=[], captures=[])

    def save(self, manifest: RunManifest) -> None:
        """Ulozi manifest do run.yml."""
        save_manifest(manifest, self.manifest_path)

    def inventory_path(self, node: str, port: str | None) -> Path:
        """Cesta k inventory souboru pro dany node a port."""
        port_str = normalize_port(port) if port else "all"
        return self.dir / f"inventory_{node}_{port_str}.yml"

    def snapshot_path(self, phase: str, node: str, port: str | None) -> Path:
        """Cesta k snapshot souboru."""
        port_str = normalize_port(port) if port else "all"
        return self.dir / f"snapshot_{phase}_{node}_{port_str}.json"

    def snapshot_name(self, phase: str, node: str, port: str | None) -> str:
        """Nazev (basename) snapshot souboru."""
        return self.snapshot_path(phase, node, port).name

    def raw_dir(self, file_name: str) -> Path:
        """Raw adresar snapshotu nebo inventory (stejny kmen jako soubor)."""
        return self.dir / RAW_DIR / raw_name(file_name)

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Exkluzivni zamek runu - fcntl.flock na runs/<run>/.lock.

        flock, ne lockf: GUI bezi capture i upgrade v jednom procesu a jen
        flock na samostatnych open() se vylucuje i uvnitr procesu. Pri padu
        procesu se uvolni sam. Drzi se jen kolem zapisu na disk, nikdy kolem
        NETCONF session.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / LOCK_FILE, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def missing_snapshots(self, manifest: RunManifest) -> list[str]:
        """Seznam basenames z captures, jejichz soubor v dir nema."""
        missing = []
        for capture in manifest.captures:
            snapshot_file = self.dir / capture.snapshot
            if not snapshot_file.exists():
                missing.append(capture.snapshot)
        return missing
