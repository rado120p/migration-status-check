"""Konfigurace checku - tolerance a severity nejsou nikdy zadratovane."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from migration_validator.models.result import Severity

DEFAULTS: dict[str, dict[str, Any]] = {
    "interface_traffic": {"tolerance_percent": -60, "require_nonzero": True},
    "bgp_prefix_counts": {"tolerance_percent": -10},
    "evpn_mac_count": {"tolerance_percent": -60},
    "ping_reachability": {"count": 5},
    "traffic_ceased": {"enabled": False},
}


@dataclass
class CheckConfig:
    raw: dict[str, dict[str, Any]] = field(default_factory=dict)

    def options(self, check_id: str) -> dict[str, Any]:
        merged = dict(DEFAULTS.get(check_id, {}))
        merged.update(self.raw.get(check_id, {}))
        return merged

    def severity(self, check_id: str, default: Severity) -> Severity:
        value = self.options(check_id).get("severity")
        return Severity(value) if value else default

    def enabled(self, check_id: str) -> bool:
        return bool(self.options(check_id).get("enabled", True))


def default_config() -> CheckConfig:
    return CheckConfig()


def load_config(path: str | Path) -> CheckConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping, nalezeno {type(raw).__name__}")
    return CheckConfig(raw.get("checks") or {})
