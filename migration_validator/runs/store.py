"""Run adresar - sprava manifest a snapshot souboru."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from migration_validator.runs.manifest import RunManifest, load_manifest, normalize_port, save_manifest


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

    def missing_snapshots(self, manifest: RunManifest) -> list[str]:
        """Seznam basenames z captures, jejichz soubor v dir nema."""
        missing = []
        for capture in manifest.captures:
            snapshot_file = self.dir / capture.snapshot
            if not snapshot_file.exists():
                missing.append(capture.snapshot)
        return missing
