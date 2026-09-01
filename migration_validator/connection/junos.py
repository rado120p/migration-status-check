"""Pripojeni k Junos zarizeni pres PyEZ.

Jedina vrstva, ktera saha na sit. Checky ji nikdy neimportuji.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from jnpr.junos import Device
from jnpr.junos.exception import (
    ConnectAuthError,
    ConnectError,
    ConnectRefusedError,
    ConnectTimeoutError,
)

from migration_validator.models.snapshot import DeviceMeta

DEFAULT_USER = "ansible"
DEFAULT_PORT = 830
DEFAULT_TIMEOUT = 30

EVO_MODEL_PREFIXES = ("PTX10", "ACX7", "QFX5700", "MX304")


class JunosConnectionError(Exception):
    """Pripojeni selhalo - nastroj nemuze pokracovat."""


@dataclass
class ConnectionOptions:
    host: str
    username: str = DEFAULT_USER
    ssh_key_paths: tuple[str, ...] = ()
    password: str | None = None
    port: int = DEFAULT_PORT
    timeout: int = DEFAULT_TIMEOUT

    def auth_attempts(self) -> list[dict[str, Any]]:
        """Kwargs pro Device() v poradi zkouseni: klice, pak heslo."""
        base: dict[str, Any] = {
            "host": self.host,
            "user": self.username,
            "port": self.port,
        }
        attempts: list[dict[str, Any]] = []
        for key_path in self.ssh_key_paths:
            if Path(key_path).exists():
                attempts.append({**base, "ssh_private_key_file": key_path})
        if self.password:
            attempts.append({**base, "passwd": self.password})
        if not attempts:
            raise JunosConnectionError(
                f"{self.host}: zadna pouzitelna autentizace - zadny ssh klic "
                f"neexistuje a heslo neni nastavene"
            )
        return attempts


@contextmanager
def connect(options: ConnectionOptions) -> Iterator[Device]:
    """Otevre spojeni. Zkousi auth moznosti v poradi; jina chyba nez
    autentizace (timeout, refused) konci hned - dalsi klic by ji nespravil."""
    device: Device | None = None
    tried: list[str] = []
    last_error: Exception | None = None

    for kwargs in options.auth_attempts():
        label = kwargs.get("ssh_private_key_file", "heslo")
        candidate = Device(**kwargs)
        try:
            candidate.open()
            device = candidate
            break
        except ConnectAuthError as error:
            tried.append(label)
            last_error = error
        except ConnectTimeoutError as error:
            raise JunosConnectionError(
                f"{options.host}: timeout po {options.timeout} s - {error}"
            ) from error
        except ConnectRefusedError as error:
            raise JunosConnectionError(
                f"{options.host}: spojeni odmitnuto - {error}"
            ) from error
        except ConnectError as error:
            raise JunosConnectionError(
                f"{options.host}: pripojeni selhalo - {error}"
            ) from error

    if device is None:
        raise JunosConnectionError(
            f"{options.host}: autentizace selhala (uzivatel {options.username}, "
            f"zkuseno: {', '.join(tried)}) - {last_error}"
        ) from last_error

    device.timeout = options.timeout
    try:
        yield device
    finally:
        device.close()


def detect_platform(device: Any) -> str:
    """Junos vs Junos EVO. EVO ma v retezci verze 'EVO'."""
    facts = getattr(device, "facts", {}) or {}
    version = facts.get("version") or ""
    if "EVO" in str(version).upper():
        return "junos-evo"

    model = str(facts.get("model") or "")
    if model.upper().startswith(EVO_MODEL_PREFIXES):
        return "junos-evo"

    return "junos"


def device_meta(device: Any, address: str) -> DeviceMeta:
    facts = getattr(device, "facts", {}) or {}
    return DeviceMeta(
        address=address,
        hostname=facts.get("hostname"),
        platform=detect_platform(device),
        model=facts.get("model"),
        version=facts.get("version"),
        uptime_seconds=None,
    )
