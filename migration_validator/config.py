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

# Vychozi pocet pingu, kdyz ho nezada ani CLI ani profil. Sdileny s
# runs/orchestrate a s katalogem profilu (GUI formular ukazuje defaulty).
PING_COUNT_DEFAULT = 5


def option_type(value: Any) -> str:
    """Typ volby checku odvozeny z Python typu defaultu - sdileny katalogem
    (GUI formular) a validaci profilu."""
    # bool je podtrida int - musi byt prvni.
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def option_matches_type(value: Any, expected: str) -> bool:
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, str)


_SEVERITY_VALUES = sorted(severity.value for severity in Severity)


def _validate_checks(raw: Any, path: str | Path) -> dict[str, dict[str, Any]]:
    """Sekce checks: volby z DEFAULTS musi mit typ defaultu, severity je
    z enumu, enabled je bool. Neznamy check ani neznama volba chybou
    nejsou - GUI je ukaze jako 'neznamy check' / read-only k odebrani a
    soubor tak jde vycistit; loader ho nesmi odmitnout."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path}: sekce checks: ocekavan mapping, nalezeno {type(raw).__name__}"
        )
    checks: dict[str, dict[str, Any]] = {}
    for check_id, overrides in raw.items():
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, dict):
            raise ValueError(
                f"{path}: check '{check_id}': ocekavan mapping, "
                f"nalezeno {type(overrides).__name__}"
            )
        defaults = DEFAULTS.get(check_id, {})
        for key, value in overrides.items():
            if key == "severity":
                if not isinstance(value, str) or value not in _SEVERITY_VALUES:
                    raise ValueError(
                        f"{path}: check '{check_id}': severity '{value}' neni platna "
                        f"(zname: {', '.join(_SEVERITY_VALUES)})"
                    )
                continue
            expected = "boolean" if key == "enabled" else (
                option_type(defaults[key]) if key in defaults else None
            )
            if expected is not None and not option_matches_type(value, expected):
                raise ValueError(
                    f"{path}: check '{check_id}': volba '{key}' ocekava {expected}, "
                    f"nalezeno {type(value).__name__}"
                )
        checks[check_id] = dict(overrides)
    return checks


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
        checks=CheckConfig(_validate_checks(raw.get("checks"), path)),
        collectors=collectors,
        service_types=service_types,
        ping_count=int(ping_count) if ping_count is not None else None,
        name=path.name,
    )
