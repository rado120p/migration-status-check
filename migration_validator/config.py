"""Konfigurace checku - tolerance a severity nejsou nikdy zadratovane."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from migration_validator.models.result import Severity

DEFAULTS: dict[str, dict[str, Any]] = {
    "interface_traffic": {"tolerance_percent": -60, "require_nonzero": True},
    "interface_optics_levels": {"tolerance_db": 2.0},
    "bgp_prefix_counts": {"tolerance_percent": -10},
    "evpn_mac_count": {"tolerance_percent": -60},
    "ping_reachability": {"count": 5},
    "traffic_ceased": {"enabled": False, "max_residual_pps": 1},
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


_PROFILE_KEYS = frozenset({"collectors", "service_types", "ping_count"})
_TOP_LEVEL_KEYS = frozenset({"profile", "checks"})


@dataclass
class Profile:
    """Co tenhle testovaci beh dela - sdileny, bez tajemstvi.

    `checks` je dnesni CheckConfig; sekce `profile:` nese vyber
    collectoru, service typu a ping_count. Sekce `tests:` (deklarativni
    checky) je planovane rozsireni - viz poznamka
    docs/superpowers/specs/2026-08-13-deklarativni-filter-testy-poznamka.md.
    """

    checks: CheckConfig = field(default_factory=CheckConfig)
    collectors: list[str] | None = None
    service_types: list[str] | None = None
    ping_count: int | None = None
    name: str = ""


def default_profile() -> Profile:
    return Profile()


def _validate_collectors(names: list[str], path: str | Path) -> None:
    # Pozdni import: config nesmi tahat collectory pri kazdem pouziti
    # CheckConfig (evaluate collectory vubec nepotrebuje).
    import migration_validator.collectors.all  # noqa: F401  (registrace)
    from migration_validator.collectors.registry import all_collectors

    known = sorted(collector.name for collector in all_collectors())
    for name in names:
        if name not in known:
            raise ValueError(
                f"{path}: neznamy collector '{name}' (zname: {', '.join(known)})"
            )


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping, nalezeno {type(raw).__name__}")

    unknown_top = sorted(set(raw) - _TOP_LEVEL_KEYS)
    if unknown_top:
        raise ValueError(
            f"{path}: neznamy klic {', '.join(unknown_top)} "
            f"(zname: {', '.join(sorted(_TOP_LEVEL_KEYS))})"
        )

    section = raw.get("profile") or {}
    unknown = sorted(set(section) - _PROFILE_KEYS)
    if unknown:
        # Preklep v service_types nesmi tise znamenat "vsechno".
        raise ValueError(
            f"{path}: neznamy klic {', '.join(unknown)} v sekci profile "
            f"(zname: {', '.join(sorted(_PROFILE_KEYS))})"
        )

    collectors = section.get("collectors")
    if collectors is not None:
        collectors = [str(name) for name in collectors]
        _validate_collectors(collectors, path)

    service_types = section.get("service_types")
    if service_types is not None:
        service_types = [str(item) for item in service_types]

    ping_count = section.get("ping_count")

    return Profile(
        checks=CheckConfig(raw.get("checks") or {}),
        collectors=collectors,
        service_types=service_types,
        ping_count=int(ping_count) if ping_count is not None else None,
        name=path.name,
    )
