"""Uzivatele GUI - hash hesel a YAML soubor s ucty.

Bez FastAPI: `mig-validate user ...` musi bezet i bez [gui] extra.
Hash je PBKDF2-HMAC-SHA256; pocet iteraci se cte z ulozeneho retezce,
takze zvyseni PASSWORD_ITERATIONS nerozbije existujici ucty.
"""

from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import hmac
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
ALLOWED_ROLES = {"viewer", "operator", "admin"}

log = logging.getLogger(__name__)


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class User:
    username: str
    role: str
    password_hash: str


def validate_username(name: str) -> None:
    if not isinstance(name, str) or not USERNAME_PATTERN.match(name):
        raise AuthenticationError(
            f"nevalidni jmeno uzivatele '{name}' - povoleno A-Z a-z 0-9 . _ -, 3-64 znaku"
        )


def validate_role(role: str) -> None:
    if role not in ALLOWED_ROLES:
        raise AuthenticationError(
            f"neznama role '{role}' (zname: {', '.join(sorted(ALLOWED_ROLES))})"
        )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def hash_password(password: str, *, iterations: int | None = None) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise AuthenticationError(
            f"Password must contain at least {PASSWORD_MIN_LENGTH} characters."
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise AuthenticationError(
            f"Password must contain at most {PASSWORD_MAX_LENGTH} characters."
        )
    rounds = PASSWORD_ITERATIONS if iterations is None else iterations
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return "$".join((PASSWORD_SCHEME, str(rounds), _b64(salt), _b64(digest)))


def _parse_hash(stored: str) -> tuple[int, bytes, bytes] | None:
    if not isinstance(stored, str):
        return None
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != PASSWORD_SCHEME:
        return None
    try:
        iterations = int(parts[1])
        salt = base64.urlsafe_b64decode(parts[2].encode("ascii"))
        digest = base64.urlsafe_b64decode(parts[3].encode("ascii"))
    except (ValueError, binascii.Error):
        return None
    if iterations < 1 or not salt or not digest:
        return None
    return iterations, salt, digest


def verify_password(password: str, stored: str) -> bool:
    parsed = _parse_hash(stored)
    if parsed is None:
        log.warning("neplatny format password_hash - prihlaseni odmitnuto")
        return False
    iterations, salt, digest = parsed
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, digest)


def needs_rehash(stored: str) -> bool:
    parsed = _parse_hash(stored)
    return parsed is not None and parsed[0] < PASSWORD_ITERATIONS


@functools.cache
def dummy_hash() -> str:
    """Hash pro neexistujiciho uzivatele - login trva stejne dlouho."""
    return hash_password("dummy-password-for-timing-only")


def _load_entry(path: Path, username, entry) -> User:
    try:
        validate_username(username)
    except AuthenticationError as error:
        raise ValueError(f"{path}: {error}") from error
    if not isinstance(entry, dict):
        raise ValueError(f"{path}: uzivatel '{username}' musi byt mapping (role, password_hash)")
    role = entry.get("role")
    try:
        validate_role(role)
    except AuthenticationError as error:
        raise ValueError(f"{path}: uzivatel '{username}': {error}") from error
    password_hash = entry.get("password_hash")
    if _parse_hash(password_hash) is None:
        raise ValueError(f"{path}: uzivatel '{username}': neplatny password_hash")
    return User(username=username, role=role, password_hash=password_hash)


class UserStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._cache_key: tuple[int, int] | None = None
        self._cache: dict[str, User] = {}

    def load(self) -> dict[str, User]:
        if not self.path.exists():
            return {}
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{self.path}: ocekavan YAML mapping s klicem 'users'")
        entries = raw.get("users") or {}
        if not isinstance(entries, dict):
            raise ValueError(f"{self.path}: 'users' musi byt mapping jmeno -> ucet")
        return {name: _load_entry(self.path, name, entry) for name, entry in entries.items()}

    def get(self, username: str) -> User | None:
        try:
            st = self.path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            key = None
        if key != self._cache_key or key is None:
            self._cache = self.load()
            self._cache_key = key
        return self._cache.get(username)

    def save(self, users: dict[str, User]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "users": {
                name: {"role": user.role, "password_hash": user.password_hash}
                for name, user in sorted(users.items())
            }
        }
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".users-", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump(data, handle, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
