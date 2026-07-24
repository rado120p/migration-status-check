"""Rucni mapovani a ignorovani sluzeb - mapping.yml.

Management rozhrani se sem psat nemusi, ta jsou vyloucena uz ve builderu.
Tenhle soubor je pro pripady specificke pro danou migraci.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from migration_validator.models.scope import Scope


@dataclass(frozen=True)
class Selector:
    description: str | None = None
    service_type: str | None = None
    interface: str | None = None

    def matches(self, scope: Scope) -> bool:
        if self.description is not None:
            if scope.key is None or scope.key.description != self.description:
                return False
        if self.service_type is not None:
            if scope.key is None or scope.key.service_type != self.service_type:
                return False
        if self.interface is not None:
            if self.interface not in scope.selectors.interfaces:
                return False
        return True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Selector":
        selector = cls(
            description=data.get("description"),
            service_type=data.get("service_type"),
            interface=data.get("interface"),
        )
        if (
            selector.description is None
            and selector.service_type is None
            and selector.interface is None
        ):
            raise ValueError(
                "prazdny selektor v mapping.yml - uved description, service_type nebo interface"
            )
        return selector


@dataclass
class MappingRule:
    baseline: Selector
    subject: Selector
    note: str | None = None


@dataclass
class Mapping:
    mappings: list[MappingRule] = field(default_factory=list)
    ignore: list[Selector] = field(default_factory=list)

    def is_ignored(self, scope: Scope) -> bool:
        return any(selector.matches(scope) for selector in self.ignore)


def empty_mapping() -> Mapping:
    return Mapping()


def load_mapping(path: str | Path) -> Mapping:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping, nalezeno {type(raw).__name__}")

    rules = []
    for item in raw.get("mappings") or []:
        if "baseline" not in item or "subject" not in item:
            raise ValueError(f"{path}: pravidlo mapovani musi mit 'baseline' i 'subject'")
        rules.append(
            MappingRule(
                baseline=Selector.from_dict(item["baseline"]),
                subject=Selector.from_dict(item["subject"]),
                note=item.get("note"),
            )
        )

    ignore = [Selector.from_dict(item) for item in raw.get("ignore") or []]
    return Mapping(mappings=rules, ignore=ignore)
