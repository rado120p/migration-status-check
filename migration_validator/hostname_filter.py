"""Filtr jmen uzlu pro vyhledavani v inventari (spec 2026-09-25, vlna B).

Glob (fnmatch) na cele jmeno, bez ohledu na velikost pismen. Filtr zuzuje
jen nabidku ve vyhledavani - existujici runy ani rucni zadani neomezuje.
"""

from __future__ import annotations

import fnmatch
import os
import tempfile
from pathlib import Path

import yaml

DEFAULT_FILTER_FILE = Path("config") / "hostname_filter.yml"


def normalize_patterns(raw: list) -> list[str]:
    patterns: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"prazdny nebo neplatny vzor: {entry!r}")
        pattern = entry.strip()
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def is_visible(node: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    name = node.lower()
    return any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in patterns)


class FilterStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as error:
            raise ValueError(f"{self.path}: neplatny YAML: {error}") from error
        if not isinstance(raw, dict):
            raise ValueError(f"{self.path}: ocekavan mapping s klicem 'allow'")
        allow = raw.get("allow") or []
        if not isinstance(allow, list):
            raise ValueError(f"{self.path}: 'allow' musi byt seznam vzoru")
        try:
            return normalize_patterns(allow)
        except ValueError as error:
            raise ValueError(f"{self.path}: {error}") from error

    def save(self, patterns: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".hostname_filter-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump({"allow": list(patterns)}, handle, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
