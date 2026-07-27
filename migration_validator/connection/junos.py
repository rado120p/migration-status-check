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
DEFAULT_PORT = 22
DEFAULT_TIMEOUT = 30

EVO_MODEL_PREFIXES = ("PTX10", "ACX7", "QFX5700", "MX304")


class JunosConnectionError(Exception):
    """Pripojeni selhalo - nastroj nemuze pokracovat."""


@dataclass
class ConnectionOptions:
    host: str
    username: str = DEFAULT_USER
    auth_type: str = "key"  # key | password
    key_file: str = str(Path.home() / ".ssh" / "id_rsa")
    password: str | None = None
    port: int = DEFAULT_PORT
    timeout: int = DEFAULT_TIMEOUT

    def device_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "host": self.host,
            "user": self.username,
            "port": self.port,
        }
        if self.auth_type == "key":
            kwargs["ssh_private_key_file"] = self.key_file
        elif self.auth_type == "password":
            if not self.password:
                raise ValueError("auth_type 'password' vyzaduje heslo")
            kwargs["passwd"] = self.password
        else:
            raise ValueError(
                f"neznamy auth_type '{self.auth_type}', ocekavano 'key' nebo 'password'"
            )
        return kwargs


@contextmanager
def connect(options: ConnectionOptions) -> Iterator[Device]:
    """Otevre spojeni. Chyby prelozi na JunosConnectionError s jasnym duvodem."""
    device = Device(**options.device_kwargs())
    try:
        device.open()
    except ConnectAuthError as error:
        raise JunosConnectionError(
            f"{options.host}: autentizace selhala (uzivatel {options.username}) - {error}"
        ) from error
    except ConnectTimeoutError as error:
        raise JunosConnectionError(
            f"{options.host}: timeout po {options.timeout} s - {error}"
        ) from error
    except ConnectRefusedError as error:
        raise JunosConnectionError(f"{options.host}: spojeni odmitnuto - {error}") from error
    except ConnectError as error:
        raise JunosConnectionError(f"{options.host}: pripojeni selhalo - {error}") from error

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
