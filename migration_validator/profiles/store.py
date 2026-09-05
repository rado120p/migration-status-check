"""Profile store - profiles/<name>.yml, jeden soubor na profil.

Ulozeni validuje pres existujici load_profile (jediny zdroj pravdy pro
format profilu): dokument se vypise do docasneho souboru ve stejnem
adresari, nacte se, a teprve pak se prejmenuje pres cil. Nevalidni
dokument tedy nikdy neprepise platny soubor a nenecha za sebou temp.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from migration_validator.config import (
    DEFAULTS,
    Profile,
    load_profile,
    option_matches_type,
    option_type,
)

PROFILE_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


def check_profile_name(name: str) -> None:
    if not PROFILE_NAME_RE.match(name):
        raise ValueError(
            f"nevalidni jmeno profilu '{name}' - povolene znaky: a-z 0-9 _ -"
        )


def empty_document() -> dict[str, Any]:
    """Plny tvar dokumentu s null = "nenastaveno"."""
    return {
        "profile": {"collectors": None, "service_types": None, "ping_count": None},
        "checks": {},
    }


def _default_severities() -> dict[str, str]:
    # Pozdni import: registr checku se nacita az pri save/preview, ne pri
    # importu store (config nesmi tahat checky).
    from migration_validator.checks.all import load_all
    from migration_validator.checks.registry import all_checks

    load_all()
    return {check.id: check.default_severity.value for check in all_checks()}


def strip_check_defaults(checks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Z overrides vyhodi to, co se rovna defaultu (enabled, severity,
    volby z DEFAULTS); check bez rozdilu zmizi. Neznamy check (neni v
    registru) zustava beze zmeny - loader ho pusti (GUI ho ukaze k
    odebrani). Neni-li checks (nebo jednotlivy check) mapping, vraci se
    beze zmeny - loader pak odmitne s hlaskou misto AttributeError.
    Zrcadlo checksDocument v gui/static/profile_diff.js."""
    if not isinstance(checks, dict):
        return checks
    severities = _default_severities()
    result: dict[str, dict[str, Any]] = {}
    for check_id, overrides in checks.items():
        if not isinstance(overrides, dict):
            result[check_id] = overrides
            continue
        if check_id not in severities:
            result[check_id] = dict(overrides)
            continue
        defaults = DEFAULTS.get(check_id, {})
        default_enabled = bool(defaults.get("enabled", True))
        kept: dict[str, Any] = {}
        for key, value in overrides.items():
            if value is None:
                continue
            if key == "enabled":
                if isinstance(value, bool) and value == default_enabled:
                    continue
            elif key == "severity":
                if value == severities.get(check_id):
                    continue
            elif key in defaults:
                expected = option_type(defaults[key])
                if option_matches_type(value, expected) and value == defaults[key]:
                    continue
            kept[key] = value
        if kept:
            result[check_id] = kept
    return result


def document_from_profile(profile: Profile) -> dict[str, Any]:
    return {
        "profile": {
            "collectors": list(profile.collectors) if profile.collectors is not None else None,
            "service_types": list(profile.service_types)
            if profile.service_types is not None else None,
            "ping_count": profile.ping_count,
        },
        "checks": {
            check_id: dict(overrides)
            for check_id, overrides in profile.checks.raw.items()
        },
    }


def document_to_yaml(document: dict[str, Any]) -> str:
    """Jediny serializer - save i preview. Null klice, prazdne seznamy
    v sekci profile a prazdna sekce checks se vynechaji, soubor tak nese
    jen to, co je nastavene. Prazdny seznam se zahodi stejne jako null:
    jinak by se do capture dostal jako "neber nic" (a GUI ho v
    normalizeProfileDocument take zahazuje).
    Nezname klice zustavaji (save je nechava spadnout v load_profile).
    Overrides rovne defaultu se vynechaji (strip_check_defaults)."""
    section = {
        key: value
        for key, value in (document.get("profile") or {}).items()
        if value is not None and value != []
    }
    checks = strip_check_defaults(document.get("checks") or {})
    data: dict[str, Any] = {}
    if section:
        data["profile"] = section
    if checks:
        data["checks"] = checks
    if not data:
        return ""
    return yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, default_flow_style=False
    )


@dataclass(frozen=True)
class ProfileStore:
    root: Path

    def path(self, name: str) -> Path:
        check_profile_name(name)
        return self.root / f"{name}.yml"

    def list(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            entry.stem
            for entry in self.root.glob("*.yml")
            if entry.is_file() and PROFILE_NAME_RE.match(entry.stem)
        )

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def load(self, name: str) -> Profile:
        path = self.path(name)
        if not path.is_file():
            raise FileNotFoundError(f"profil '{name}' neexistuje ({path})")
        return load_profile(path)

    def document(self, name: str) -> dict[str, Any]:
        return document_from_profile(self.load(name))

    def save(self, name: str, document: dict[str, Any]) -> Path:
        path = self.path(name)
        self.root.mkdir(parents=True, exist_ok=True)
        text = document_to_yaml(document)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.root, prefix=f".{name}.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            try:
                load_profile(tmp)
            except ValueError as error:
                # Hlaska loaderu beze zmeny, jen s cilovou cestou miste temp.
                raise ValueError(str(error).replace(str(tmp), str(path))) from error
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return path

    def delete(self, name: str) -> None:
        path = self.path(name)
        if not path.is_file():
            raise FileNotFoundError(f"profil '{name}' neexistuje ({path})")
        path.unlink()
