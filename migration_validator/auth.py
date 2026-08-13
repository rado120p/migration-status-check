"""Per-user auth soubor - kdo jsem, ne co testuju.

Heslo se do souboru nepise: bud jmeno env promenne (password_env),
nebo plaintext jen pri opravneni 0600. Sdileny adresar tak nikdy
nenese tajemstvi. Chyby jsou ValueError - cli.main je prevadi na
EXIT_TOOL_ERROR stejne jako ostatni vstupni chyby.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_AUTH_PATH = Path.home() / ".config" / "mig-validate" / "auth.yml"

_KNOWN_KEYS = frozenset(
    {"username", "auth", "key_file", "password", "password_env", "ssh_port", "timeout"}
)


@dataclass(frozen=True)
class AuthSettings:
    username: str | None = None
    auth_type: str | None = None
    key_file: str | None = None
    password: str | None = None
    ssh_port: int | None = None
    timeout: int | None = None


def load_auth_file(path: Path, *, required: bool) -> AuthSettings:
    if not path.exists():
        if required:
            raise ValueError(f"auth soubor nenalezen: {path}")
        return AuthSettings()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping")

    unknown = sorted(set(raw) - _KNOWN_KEYS)
    if unknown:
        raise ValueError(
            f"{path}: neznamy klic {', '.join(unknown)} "
            f"(zname: {', '.join(sorted(_KNOWN_KEYS))})"
        )

    password = raw.get("password")
    password_env = raw.get("password_env")
    if password and password_env:
        raise ValueError(f"{path}: password i password_env zaroven - vyber jedno")

    if password:
        # Kontrola prav jen kdyz soubor nese tajemstvi - soubor bez hesla
        # smi byt klidne world-readable (nic tajneho v nem neni).
        mode = path.stat().st_mode & 0o077
        if mode:
            raise ValueError(
                f"{path}: obsahuje heslo, ale je citelny pro group/others - "
                f"sprav pravy: chmod 600 {path}"
            )

    if password_env:
        value = os.environ.get(password_env)
        if not value:
            raise ValueError(
                f"{path}: promenna {password_env} neni nastavena"
            )
        password = value

    key_file = raw.get("key_file")
    if key_file:
        key_file = str(Path(key_file).expanduser())

    return AuthSettings(
        username=raw.get("username"),
        auth_type=raw.get("auth"),
        key_file=key_file,
        password=password,
        ssh_port=int(raw["ssh_port"]) if raw.get("ssh_port") is not None else None,
        timeout=int(raw["timeout"]) if raw.get("timeout") is not None else None,
    )
