"""Snapshot - zmrazeny stav zarizeni v jednom okamziku.

Snapshot je self-contained: evaluate k nemu nepotrebuje ani inventory,
ani pristup na sit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from migration_validator.models.inventory import ServiceEntry
from migration_validator.models.scope import Scope

SCHEMA_VERSION = 4


class SnapshotVersionError(Exception):
    """Snapshot ma jinou schema_version, nez nastroj umi zpracovat."""


@dataclass
class DeviceMeta:
    address: str
    hostname: str | None = None
    platform: str = "junos"  # junos | junos-evo
    model: str | None = None
    version: str | None = None
    uptime_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "hostname": self.hostname,
            "platform": self.platform,
            "model": self.model,
            "version": self.version,
            "uptime_seconds": self.uptime_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceMeta":
        return cls(
            address=data["address"],
            hostname=data.get("hostname"),
            platform=data.get("platform", "junos"),
            model=data.get("model"),
            version=data.get("version"),
            uptime_seconds=data.get("uptime_seconds"),
        )


@dataclass
class CaptureMeta:
    started_at: str
    finished_at: str | None = None
    phase: str | None = None
    collectors: dict[str, dict[str, Any]] = field(default_factory=dict)

    def failed_collectors(self) -> dict[str, str]:
        """Jmeno collectoru -> chybova hlaska, jen pro ty, ktere selhaly."""
        return {
            name: str(info.get("message", "neznama chyba"))
            for name, info in self.collectors.items()
            if info.get("status") != "ok"
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "phase": self.phase,
            "collectors": self.collectors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaptureMeta":
        return cls(
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            phase=data.get("phase"),
            collectors=data.get("collectors", {}),
        )


@dataclass
class Snapshot:
    device: DeviceMeta
    capture: CaptureMeta
    facts: dict[str, Any] = field(default_factory=dict)
    probes: dict[str, Any] = field(default_factory=lambda: {"ping": []})
    scopes: list[Scope] = field(default_factory=list)
    inventory: list[ServiceEntry] | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "device": self.device.to_dict(),
            "capture": self.capture.to_dict(),
            "inventory": (
                [entry.to_dict() for entry in self.inventory]
                if self.inventory is not None
                else None
            ),
            "scopes": [scope.to_dict() for scope in self.scopes],
            "facts": self.facts,
            "probes": self.probes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Snapshot":
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise SnapshotVersionError(
                f"snapshot ma schema_version {version}, nastroj umi {SCHEMA_VERSION}"
            )
        inventory = data.get("inventory")
        return cls(
            device=DeviceMeta.from_dict(data["device"]),
            capture=CaptureMeta.from_dict(data["capture"]),
            facts=data.get("facts", {}),
            probes=data.get("probes", {"ping": []}),
            scopes=[Scope.from_dict(item) for item in data.get("scopes", [])],
            inventory=(
                [ServiceEntry.from_dict(item) for item in inventory]
                if inventory is not None
                else None
            ),
            schema_version=version,
        )


def save_snapshot(snapshot: Snapshot, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_snapshot(path: str | Path) -> Snapshot:
    return Snapshot.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
