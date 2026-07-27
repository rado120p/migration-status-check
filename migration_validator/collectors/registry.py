"""Registr collectoru."""

from __future__ import annotations

from migration_validator.collectors.base import Collector

_REGISTRY: dict[str, Collector] = {}


def register(cls: type[Collector]) -> type[Collector]:
    if cls.name in _REGISTRY:
        raise ValueError(f"collector '{cls.name}' je uz registrovany")
    _REGISTRY[cls.name] = cls()
    return cls


def all_collectors() -> list[Collector]:
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def collectors_for(platform: str) -> list[Collector]:
    return [collector for collector in all_collectors() if collector.supports(platform)]
