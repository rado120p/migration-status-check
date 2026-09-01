"""Nastaveni pripojeni - config/settings.yml v repu.

Nahrazuje auth.yml (per-user soubor v ~/.config). Heslo se do souboru
nepise: bud jmeno env promenne (password_env), nebo plaintext jen pri
opravneni 0600. Chyby jsou ValueError - cli.main je prevadi na
EXIT_TOOL_ERROR stejne jako ostatni vstupni chyby.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_SETTINGS_PATH = Path("config") / "settings.yml"

DEFAULT_USERNAME = "ansible"
DEFAULT_NETCONF_PORT = 830
DEFAULT_TIMEOUT = 30
DEFAULT_SSH_KEY_PATHS = ("~/.ssh/id_ed25519", "~/.ssh/id_rsa")

_KNOWN_KEYS = frozenset(
    {"netconf_port", "timeout", "username", "ssh_key_paths", "password", "password_env"}
)


@dataclass(frozen=True)
class ConnectionSettings:
    username: str = DEFAULT_USERNAME
    ssh_key_paths: tuple[str, ...] = ()
    netconf_port: int = DEFAULT_NETCONF_PORT
    timeout: int = DEFAULT_TIMEOUT
    password: str | None = None


def _expand(paths) -> tuple[str, ...]:
    return tuple(str(Path(p).expanduser()) for p in paths)


def load_settings(path: Path | None = None) -> ConnectionSettings:
    if path is None:
        path = DEFAULT_SETTINGS_PATH
    if not path.exists():
        return ConnectionSettings(ssh_key_paths=_expand(DEFAULT_SSH_KEY_PATHS))

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping")

    connection = raw.get("connection") or {}
    if not isinstance(connection, dict):
        raise ValueError(f"{path}: 'connection' musi byt mapping")

    unknown = sorted(set(connection) - _KNOWN_KEYS)
    if unknown:
        raise ValueError(
            f"{path}: neznamy klic {', '.join(unknown)} "
            f"(zname: {', '.join(sorted(_KNOWN_KEYS))})"
        )

    password = connection.get("password")
    password_env = connection.get("password_env")
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
            raise ValueError(f"{path}: promenna {password_env} neni nastavena")
        password = value

    key_paths = connection.get("ssh_key_paths")
    if key_paths is None:
        key_paths = DEFAULT_SSH_KEY_PATHS

    return ConnectionSettings(
        username=connection.get("username") or DEFAULT_USERNAME,
        ssh_key_paths=_expand(key_paths),
        netconf_port=int(connection["netconf_port"]) if connection.get("netconf_port") is not None else DEFAULT_NETCONF_PORT,
        timeout=int(connection["timeout"]) if connection.get("timeout") is not None else DEFAULT_TIMEOUT,
        password=password,
    )
