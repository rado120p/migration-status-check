"""Katalog pro formular profilu - defaulty bez zadratovani v GUI.

Collectory z registru collectoru (jako /api/meta), service typy z
scoping.builder, ping z config.PING_COUNT_DEFAULT, checky z registru
sloucene s config.DEFAULTS. Typ option se odvozuje z Python typu defaultu.
"""

from __future__ import annotations

from typing import Any

from migration_validator import api
from migration_validator.config import DEFAULTS, PING_COUNT_DEFAULT, option_type
from migration_validator.scoping.builder import MIGRATED_SERVICE_TYPES

_PLATFORMS = ("junos", "junos-evo")
_NOT_OPTIONS = frozenset({"severity", "enabled"})


def _check_entry(described: dict[str, Any]) -> dict[str, Any]:
    defaults = DEFAULTS.get(described["id"], {})
    requires = described["requires"]
    return {
        "id": described["id"],
        "title": described["title"],
        "group": requires[0] if requires else "general",
        "default_severity": described["default_severity"],
        "excluded_subtypes": described.get("excluded_subtypes"),
        "default_enabled": bool(defaults.get("enabled", True)),
        "options": {
            name: {"type": option_type(value), "default": value}
            for name, value in defaults.items()
            if name not in _NOT_OPTIONS
        },
    }


def build_catalogue() -> dict[str, Any]:
    import migration_validator.collectors.all  # noqa: F401  (registrace)
    from migration_validator.collectors.registry import collectors_for

    return {
        "collectors": {
            platform: [c.name for c in collectors_for(platform)]
            for platform in _PLATFORMS
        },
        "service_types": sorted(MIGRATED_SERVICE_TYPES),
        "ping_count_default": PING_COUNT_DEFAULT,
        "checks": [_check_entry(described) for described in api.list_checks()],
    }
