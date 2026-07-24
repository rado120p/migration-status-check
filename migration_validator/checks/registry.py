"""Registr checku - jediny zdroj pravdy pro CLI i GUI."""

from __future__ import annotations

from migration_validator.checks.base import Check
from migration_validator.config import CheckConfig
from migration_validator.models.scope import Scope

_REGISTRY: dict[str, Check] = {}


def register(cls: type[Check]) -> type[Check]:
    """Dekorator pro registraci checku."""
    if cls.id in _REGISTRY:
        raise ValueError(f"check '{cls.id}' je uz registrovany")
    _REGISTRY[cls.id] = cls()
    return cls


def all_checks() -> list[Check]:
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def get_check(check_id: str) -> Check:
    if check_id not in _REGISTRY:
        raise KeyError(f"neznamy check '{check_id}'")
    return _REGISTRY[check_id]


def checks_for(scope: Scope, config: CheckConfig) -> list[Check]:
    return [
        check
        for check in all_checks()
        if config.enabled(check.id) and check.applies_to(scope)
    ]
