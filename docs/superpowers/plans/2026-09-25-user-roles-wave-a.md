# User roles — wave A (accounts, login, sessions) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GUI users log in with accounts created from the CLI; every API route is gated by the viewer/operator/admin matrix from the spec.

**Architecture:** A core `migration_validator/users.py` (PBKDF2 hashing + YAML `UserStore`, no FastAPI) serves both the new `mig-validate user …` CLI and the GUI. The GUI gets an in-memory `SessionStore` + `LoginThrottle` (`gui/sessions.py`), a session-based actor provider plugged into the existing `authz.py` seam, login/logout routes, a static login page, an Origin-check middleware and an audit logger. `create_app(users=None)` keeps today's anonymous admin for tests and embedding; `mig-validate gui` always passes a `UserStore` and refuses to start without users.

**Tech Stack:** Python 3.13, FastAPI 0.141 / Starlette 1.6, PyYAML, stdlib `hashlib`/`hmac`/`secrets`, vanilla JS, pytest (`.venv/bin/python -m pytest`), `node --test tests/js/`.

**Spec:** `docs/superpowers/specs/2026-09-25-user-roles-and-inventory-design.md` (sections "Permission matrix" and "Wave A").

## Global Constraints

- Hash: `pbkdf2_sha256`, 600 000 iterations, 16-byte `os.urandom` salt, format `pbkdf2_sha256$<iterations>$<b64url salt>$<b64url digest>`.
- `USERNAME_PATTERN = ^[A-Za-z0-9._-]{3,64}$`, usernames case-sensitive; `PASSWORD_MIN_LENGTH = 12`, `PASSWORD_MAX_LENGTH = 1024`; `ALLOWED_ROLES = {"viewer", "operator", "admin"}`.
- Users file default `config/users.yml`, set by `auth.users_file` in `config/settings.yml`; written atomically, mode 0600.
- `config/settings.yml`, `config/users.yml`, `config/hostname_filter.yml` are gitignored; only `config/settings-template.yml` is tracked.
- Session cookie `mig_session`: `Secure; HttpOnly; SameSite=Strict; Path=/`, no Max-Age. Idle timeout 8 h, absolute 12 h.
- Login throttle: 5 consecutive failures per `(username, client IP)` → locked 5 minutes → HTTP 429.
- Login failure body is always `{"detail": "invalid username or password"}` (HTTP 401). No session → HTTP 401 `{"detail": "login required"}`. Insufficient role → 403, existing message `nedostatecne opravneni: vyzaduje <perm>`.
- Passwords are never CLI arguments or env vars; prompted twice with `getpass`.
- CLI messages follow `cli.py` style (Czech without diacritics, errors via `ToolError`/`ValueError` → exit code 2). GUI-visible text is English.
- No new runtime dependency.

## Review Focus

1. **A CLI edit while the GUI runs** (role change, delete, passwd) must apply on the very next request — `UserStore.get` cache keyed by `(mtime_ns, size)`; pinned in Task 2 (cache test uses `os.utime` so equal-size rewrites are caught) and Task 5 (`test_session_provider_follows_users_file`: deleted user / changed role on next request).
2. **`created_by` silently dropped** by any code path that rebuilds or re-saves `run.yml` (capture append, mapping edit, upgrade) — pinned in Task 4 (round-trip through `update_mapping` and `load/save`).
3. **A route added later without a matrix entry** — the table-driven test in Task 5 fails when any `/api` route is missing from the table.
4. **Behind a reverse proxy** the `Origin` check compares with `Host`; a proxy that rewrites `Host` would block every write. Task 6 tests the equal-host pass; Task 9 documents `proxy_set_header Host $host`.
5. **Malformed `users.yml` edited by hand** (scalar instead of mapping, unknown role, broken hash) must fail loudly at GUI startup / CLI, never skip a user silently — pinned in Task 2.

---

## File structure

| File | Responsibility |
|---|---|
| `migration_validator/auth.py` (modify) | add `AuthSettings`, `load_auth_settings()` |
| `migration_validator/users.py` (create) | hashing, `User`, `UserStore` |
| `migration_validator/cli.py` (modify) | `user` subcommands; `gui` startup refusal + new flags |
| `migration_validator/runs/manifest.py`, `api.py` (modify) | `created_by` |
| `migration_validator/gui/sessions.py` (create) | `SessionStore`, `LoginThrottle`, `SESSION_COOKIE` |
| `migration_validator/gui/authz.py` (modify) | `Actor.username`, `session_actor_provider`, 401, `permissions_for` |
| `migration_validator/gui/auth_routes.py` (create) | `/api/login`, `/api/logout` |
| `migration_validator/gui/audit.py` (create) | audit logger helpers |
| `migration_validator/gui/app.py`, `group_routes.py`, `profile_routes.py` (modify) | wiring, relabels, `/api/me`, `/login`, `/` redirect, Origin middleware, audit calls |
| `migration_validator/gui/static/login.html`, `login.js` (create) | login page |
| `migration_validator/gui/static/index.html`, `app.js`, `style.css` (modify) | 401 redirect, user chip + logout, role-based hiding, created_by |
| `docs/en/*`, `docs/cs/*`, `config/settings-template.yml`, `.gitignore` | docs + config |

---

### Task 1: Settings leave git; `auth` settings block

**Files:**
- Modify: `.gitignore`, `config/settings-template.yml`, `migration_validator/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces: `migration_validator.auth.DEFAULT_USERS_FILE: Path` (= `Path("config") / "users.yml"`), `@dataclass(frozen=True) class AuthSettings: users_file: Path = DEFAULT_USERS_FILE`, `load_auth_settings(path: Path | None = None) -> AuthSettings`.

- [ ] **Step 1: Untrack settings.yml and ignore the local config files**

```bash
git rm --cached config/settings.yml
```

Append to `.gitignore`:

```
/config/settings.yml
/config/users.yml
/config/hostname_filter.yml
```

The local `config/settings.yml` must still exist on disk afterwards (`ls config/`).

- [ ] **Step 2: Document the `auth` block in the template**

Append to `config/settings-template.yml`:

```yaml
auth:
  # GUI accounts; created with `mig-validate user add NAME --role ROLE`.
  users_file: config/users.yml
```

- [ ] **Step 3: Write failing tests** (append to `tests/test_auth.py`)

```python
from migration_validator.auth import DEFAULT_USERS_FILE, AuthSettings, load_auth_settings


def test_auth_settings_default_when_file_missing(tmp_path):
    assert load_auth_settings(tmp_path / "missing.yml") == AuthSettings()
    assert AuthSettings().users_file == DEFAULT_USERS_FILE


def test_auth_settings_default_when_block_missing(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  timeout: 10\n")
    assert load_auth_settings(path).users_file == DEFAULT_USERS_FILE


def test_auth_settings_users_file(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("auth:\n  users_file: /srv/mig/users.yml\n")
    assert load_auth_settings(path).users_file == Path("/srv/mig/users.yml")


def test_auth_settings_expands_user(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("auth:\n  users_file: ~/users.yml\n")
    assert load_auth_settings(path).users_file == Path("~/users.yml").expanduser()


def test_auth_settings_unknown_key_rejected(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("auth:\n  userfile: x.yml\n")
    with pytest.raises(ValueError, match="userfile"):
        load_auth_settings(path)


def test_auth_settings_block_must_be_mapping(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("auth: config/users.yml\n")
    with pytest.raises(ValueError, match="'auth' musi byt mapping"):
        load_auth_settings(path)


def test_auth_settings_does_not_need_password_env(tmp_path, monkeypatch):
    # `mig-validate user add` must work without MIG_LAB_PASSWORD in the env.
    monkeypatch.delenv("NOPE_UNSET", raising=False)
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  password_env: NOPE_UNSET\nauth:\n  users_file: u.yml\n")
    assert load_auth_settings(path).users_file == Path("u.yml")
```

(Ensure `from pathlib import Path` and `import pytest` are imported at the top of the test file.)

- [ ] **Step 4: Run and see them fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py -q`
Expected: ImportError for `load_auth_settings`.

- [ ] **Step 5: Implement**

In `migration_validator/auth.py`: extract the "read file → dict, check it is a mapping" part of `load_settings` into a helper and add the auth loader.

```python
DEFAULT_USERS_FILE = Path("config") / "users.yml"

_KNOWN_AUTH_KEYS = frozenset({"users_file"})


@dataclass(frozen=True)
class AuthSettings:
    users_file: Path = DEFAULT_USERS_FILE


def _read_settings(path: Path) -> dict | None:
    """Obsah settings.yml jako dict; None kdyz soubor neexistuje."""
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping")
    return raw


def load_auth_settings(path: Path | None = None) -> AuthSettings:
    """Blok `auth:` - zvlast od connection, aby `user add` nepotreboval
    password_env v prostredi."""
    if path is None:
        path = DEFAULT_SETTINGS_PATH
    raw = _read_settings(path)
    if raw is None:
        return AuthSettings()
    block = raw.get("auth") or {}
    if not isinstance(block, dict):
        raise ValueError(f"{path}: 'auth' musi byt mapping")
    unknown = sorted(set(block) - _KNOWN_AUTH_KEYS)
    if unknown:
        raise ValueError(
            f"{path}: neznamy klic auth.{', auth.'.join(unknown)} "
            f"(zname: {', '.join(sorted(_KNOWN_AUTH_KEYS))})"
        )
    users_file = block.get("users_file")
    if users_file is None:
        return AuthSettings()
    return AuthSettings(users_file=Path(str(users_file)).expanduser())
```

Make `load_settings` use `_read_settings` (behaviour unchanged: missing file → defaults).

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_auth.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add .gitignore config/settings-template.yml migration_validator/auth.py tests/test_auth.py
git commit -m "feat(auth): auth.users_file setting; config/settings.yml leaves git"
```

(`git rm --cached` from Step 1 is already staged.)

---

### Task 2: Core users module

**Files:**
- Create: `migration_validator/users.py`
- Test: `tests/test_users.py`

**Interfaces:**
- Produces:
  - constants `USERNAME_PATTERN`, `PASSWORD_SCHEME`, `PASSWORD_ITERATIONS`, `PASSWORD_MIN_LENGTH`, `PASSWORD_MAX_LENGTH`, `ALLOWED_ROLES`
  - `class AuthenticationError(ValueError)`
  - `@dataclass(frozen=True) class User: username: str; role: str; password_hash: str`
  - `validate_username(name: str) -> None`, `validate_role(role: str) -> None` (raise `AuthenticationError`)
  - `hash_password(password: str, *, iterations: int | None = None) -> str` (None → module `PASSWORD_ITERATIONS` read at call time, so tests can monkeypatch it)
  - `verify_password(password: str, stored: str) -> bool`
  - `needs_rehash(stored: str) -> bool`
  - `dummy_hash() -> str` (cached; used for unknown-user timing)
  - `class UserStore(path: Path)`: `.path`, `.load() -> dict[str, User]`, `.get(username: str) -> User | None`, `.save(users: dict[str, User]) -> None`

- [ ] **Step 1: Write failing tests** — `tests/test_users.py`

```python
import os
import stat

import pytest
import yaml

from migration_validator import users as users_mod
from migration_validator.users import (
    AuthenticationError,
    User,
    UserStore,
    hash_password,
    needs_rehash,
    verify_password,
)

FAST = 1_000  # iterations for tests; production value is exercised once below


def test_hash_format_and_round_trip():
    stored = hash_password("correct horse battery", iterations=FAST)
    scheme, iterations, salt, digest = stored.split("$")
    assert scheme == "pbkdf2_sha256"
    assert iterations == "1000"
    assert salt and digest
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterx", stored)


def test_default_iterations_are_600k():
    stored = hash_password("correct horse battery")
    assert stored.split("$")[1] == "600000"
    assert verify_password("correct horse battery", stored)
    assert not needs_rehash(stored)


def test_salt_is_random():
    assert hash_password("same password!!", iterations=FAST) != hash_password("same password!!", iterations=FAST)


def test_iterations_read_from_stored_hash():
    stored = hash_password("correct horse battery", iterations=FAST)
    assert verify_password("correct horse battery", stored)  # verified with 1000, not 600000
    assert needs_rehash(stored)


@pytest.mark.parametrize("stored", [
    "", "garbage", "md5$1$a$b", "pbkdf2_sha256$notanumber$YQ==$YQ==",
    "pbkdf2_sha256$1000$!!!$YQ==", "pbkdf2_sha256$1000$YQ==",
    "pbkdf2_sha256$0$YQ==$YQ==",
])
def test_malformed_hash_verifies_false(stored):
    assert verify_password("whatever password", stored) is False


def test_password_length_limits():
    with pytest.raises(AuthenticationError, match="12"):
        hash_password("short", iterations=FAST)
    with pytest.raises(AuthenticationError, match="1024"):
        hash_password("x" * 1025, iterations=FAST)


@pytest.mark.parametrize("name", ["ab", "a" * 65, "rado mohyla", "rado/..", "ráda"])
def test_bad_usernames(name):
    with pytest.raises(AuthenticationError):
        users_mod.validate_username(name)


def test_bad_role():
    with pytest.raises(AuthenticationError, match="guest"):
        users_mod.validate_role("guest")


def _user(name="rado", role="admin"):
    return User(name, role, hash_password("correct horse battery", iterations=FAST))


def test_store_missing_file_is_empty(tmp_path):
    store = UserStore(tmp_path / "users.yml")
    assert store.load() == {}
    assert store.get("rado") is None


def test_store_save_load_round_trip_and_mode(tmp_path):
    path = tmp_path / "sub" / "users.yml"
    store = UserStore(path)
    store.save({"rado": _user()})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = store.load()
    assert loaded["rado"].role == "admin"
    assert verify_password("correct horse battery", loaded["rado"].password_hash)
    raw = yaml.safe_load(path.read_text())
    assert set(raw["users"]["rado"]) == {"role", "password_hash"}
    assert [p.name for p in path.parent.iterdir()] == ["users.yml"]  # no temp leftovers


def test_store_get_sees_external_edit(tmp_path):
    path = tmp_path / "users.yml"
    store = UserStore(path)
    store.save({"rado": _user(role="viewer")})
    assert store.get("rado").role == "viewer"
    other = UserStore(path)  # e.g. the CLI process
    other.save({"rado": _user(role="admin")})
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert store.get("rado").role == "admin"
    other.save({})
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))
    assert store.get("rado") is None


@pytest.mark.parametrize("content, match", [
    ("- rado\n", "mapping"),
    ("users: [rado]\n", "users"),
    ("users:\n  rado: admin\n", "rado"),
    ("users:\n  rado: {role: guest, password_hash: 'pbkdf2_sha256$1000$YQ==$YQ=='}\n", "guest"),
    ("users:\n  rado: {role: admin, password_hash: 'plain'}\n", "password_hash"),
    ("users:\n  'x y': {role: admin, password_hash: 'pbkdf2_sha256$1000$YQ==$YQ=='}\n", "x y"),
])
def test_store_malformed_file_raises(tmp_path, content, match):
    path = tmp_path / "users.yml"
    path.write_text(content)
    with pytest.raises(ValueError, match=match):
        UserStore(path).load()


def test_store_empty_file_and_empty_users(tmp_path):
    path = tmp_path / "users.yml"
    path.write_text("")
    assert UserStore(path).load() == {}
    path.write_text("users:\n")
    assert UserStore(path).load() == {}
```

- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/python -m pytest tests/test_users.py -q`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `migration_validator/users.py`**

```python
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
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_users.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/users.py tests/test_users.py
git commit -m "feat(users): PBKDF2 password hashing and YAML user store"
```

---

### Task 3: `mig-validate user` CLI

**Files:**
- Modify: `migration_validator/cli.py`
- Test: `tests/test_cli_users.py`

**Interfaces:**
- Consumes: `load_auth_settings` (Task 1); `UserStore`, `User`, `hash_password`, `validate_username`, `validate_role`, `ALLOWED_ROLES` (Task 2).
- Produces: subcommands `user add NAME --role ROLE`, `user passwd NAME`, `user role NAME ROLE`, `user delete NAME`, `user list`, each with `--settings PATH`.

- [ ] **Step 1: Write failing tests** — `tests/test_cli_users.py`

```python
import pytest

from migration_validator import users as users_mod
from migration_validator.cli import EXIT_OK, EXIT_TOOL_ERROR, main
from migration_validator.users import UserStore, verify_password


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(users_mod, "PASSWORD_ITERATIONS", 1_000)
    path = tmp_path / "settings.yml"
    path.write_text(f"auth:\n  users_file: {tmp_path / 'users.yml'}\n")
    return path


@pytest.fixture
def store(tmp_path):
    return UserStore(tmp_path / "users.yml")


def _prompts(monkeypatch, *answers):
    it = iter(answers)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(it))


def test_add_user(settings, store, monkeypatch, capsys):
    _prompts(monkeypatch, "correct horse battery", "correct horse battery")
    assert main(["user", "add", "rado", "--role", "admin", "--settings", str(settings)]) == EXIT_OK
    user = store.load()["rado"]
    assert user.role == "admin"
    assert verify_password("correct horse battery", user.password_hash)
    assert "rado" in capsys.readouterr().out


def test_add_refuses_mismatch(settings, store, monkeypatch, capsys):
    _prompts(monkeypatch, "correct horse battery", "correct horse batterx")
    assert main(["user", "add", "rado", "--role", "admin", "--settings", str(settings)]) == EXIT_TOOL_ERROR
    assert store.load() == {}
    assert "neshoduji" in capsys.readouterr().err


def test_add_refuses_short_password(settings, store, monkeypatch):
    _prompts(monkeypatch, "short", "short")
    assert main(["user", "add", "rado", "--role", "admin", "--settings", str(settings)]) == EXIT_TOOL_ERROR
    assert store.load() == {}


def test_add_refuses_existing(settings, store, monkeypatch, capsys):
    _prompts(monkeypatch, *["correct horse battery"] * 4)
    main(["user", "add", "rado", "--role", "admin", "--settings", str(settings)])
    assert main(["user", "add", "rado", "--role", "viewer", "--settings", str(settings)]) == EXIT_TOOL_ERROR
    assert store.load()["rado"].role == "admin"
    assert "existuje" in capsys.readouterr().err


def test_add_refuses_bad_name_before_prompting(settings, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": pytest.fail("prompted"))
    assert main(["user", "add", "a b", "--role", "admin", "--settings", str(settings)]) == EXIT_TOOL_ERROR


def test_role_must_be_known(settings):
    with pytest.raises(SystemExit):
        main(["user", "add", "rado", "--role", "guest", "--settings", str(settings)])


def test_passwd_role_delete_list(settings, store, monkeypatch, capsys):
    _prompts(monkeypatch, "correct horse battery", "correct horse battery",
             "another long password", "another long password")
    main(["user", "add", "rado", "--role", "viewer", "--settings", str(settings)])
    assert main(["user", "passwd", "rado", "--settings", str(settings)]) == EXIT_OK
    assert verify_password("another long password", store.load()["rado"].password_hash)
    assert main(["user", "role", "rado", "operator", "--settings", str(settings)]) == EXIT_OK
    assert store.load()["rado"].role == "operator"
    capsys.readouterr()
    assert main(["user", "list", "--settings", str(settings)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "rado" in out and "operator" in out and "pbkdf2" not in out
    assert main(["user", "delete", "rado", "--settings", str(settings)]) == EXIT_OK
    assert store.load() == {}


@pytest.mark.parametrize("argv", [["passwd", "nobody"], ["role", "nobody", "admin"], ["delete", "nobody"]])
def test_unknown_user(settings, argv, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": pytest.fail("prompted"))
    assert main(["user", *argv, "--settings", str(settings)]) == EXIT_TOOL_ERROR


def test_list_empty(settings, capsys):
    assert main(["user", "list", "--settings", str(settings)]) == EXIT_OK
    assert "zadni uzivatele" in capsys.readouterr().out
```

- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/python -m pytest tests/test_cli_users.py -q`
Expected: argparse error (invalid choice 'user').

- [ ] **Step 3: Implement in `cli.py`**

Add `import getpass` at the top (module import — tests patch `getpass.getpass`). Command functions:

```python
def _user_store(args: argparse.Namespace):
    from migration_validator.auth import load_auth_settings
    from migration_validator.users import UserStore

    return UserStore(load_auth_settings(args.settings).users_file)


def _prompt_password() -> str:
    first = getpass.getpass("Heslo: ")
    second = getpass.getpass("Heslo znovu: ")
    if first != second:
        raise ToolError("hesla se neshoduji")
    return first


def _existing_user(users: dict, name: str):
    if name not in users:
        raise ToolError(f"uzivatel '{name}' neexistuje")
    return users[name]


def _cmd_user_add(args: argparse.Namespace) -> int:
    from migration_validator.users import User, hash_password, validate_username

    validate_username(args.name)
    store = _user_store(args)
    users = store.load()
    if args.name in users:
        raise ToolError(f"uzivatel '{args.name}' uz existuje ({store.path})")
    users[args.name] = User(args.name, args.role, hash_password(_prompt_password()))
    store.save(users)
    print(f"uzivatel {args.name} ({args.role}) zalozen v {store.path}")
    return EXIT_OK


def _cmd_user_passwd(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from migration_validator.users import hash_password

    store = _user_store(args)
    users = store.load()
    user = _existing_user(users, args.name)
    users[args.name] = replace(user, password_hash=hash_password(_prompt_password()))
    store.save(users)
    print(f"heslo uzivatele {args.name} zmeneno")
    return EXIT_OK


def _cmd_user_role(args: argparse.Namespace) -> int:
    from dataclasses import replace

    store = _user_store(args)
    users = store.load()
    user = _existing_user(users, args.name)
    users[args.name] = replace(user, role=args.role)
    store.save(users)
    print(f"uzivatel {args.name}: role {user.role} -> {args.role}")
    return EXIT_OK


def _cmd_user_delete(args: argparse.Namespace) -> int:
    store = _user_store(args)
    users = store.load()
    _existing_user(users, args.name)
    del users[args.name]
    store.save(users)
    print(f"uzivatel {args.name} smazan")
    return EXIT_OK


def _cmd_user_list(args: argparse.Namespace) -> int:
    users = _user_store(args).load()
    if not users:
        print("(zadni uzivatele)")
    for name, user in sorted(users.items()):
        print(f"{name}\t{user.role}")
    return EXIT_OK
```

Parser, in `build_parser()` before `return parser`:

```python
    from migration_validator.users import ALLOWED_ROLES

    roles = sorted(ALLOWED_ROLES)
    user_common = argparse.ArgumentParser(add_help=False)
    user_common.add_argument(
        "--settings", type=Path, default=None,
        help="settings.yml s auth.users_file (default config/settings.yml)",
    )
    user = sub.add_parser("user", help="ucty GUI (heslo se vzdy zadava interaktivne)")
    user_sub = user.add_subparsers(dest="user_command", required=True)
    add = user_sub.add_parser("add", parents=[user_common], help="zalozi uzivatele")
    add.add_argument("name")
    add.add_argument("--role", required=True, choices=roles)
    add.set_defaults(func=_cmd_user_add)
    passwd = user_sub.add_parser("passwd", parents=[user_common], help="zmeni heslo")
    passwd.add_argument("name")
    passwd.set_defaults(func=_cmd_user_passwd)
    role = user_sub.add_parser("role", parents=[user_common], help="zmeni roli")
    role.add_argument("name")
    role.add_argument("role", choices=roles)
    role.set_defaults(func=_cmd_user_role)
    delete = user_sub.add_parser("delete", parents=[user_common], help="smaze uzivatele")
    delete.add_argument("name")
    delete.set_defaults(func=_cmd_user_delete)
    listing = user_sub.add_parser("list", parents=[user_common], help="vypise uzivatele a role")
    listing.set_defaults(func=_cmd_user_list)
```

(`users.py` imports only stdlib + yaml, so a top-level-of-function import is cheap; keep it inside `build_parser` to mirror the lazy imports elsewhere in `cli.py`, or move to module top if `cli.py` already imports core modules at top — follow what the file does.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_cli_users.py tests/test_cli.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/cli.py tests/test_cli_users.py
git commit -m "feat(cli): mig-validate user add/passwd/role/delete/list"
```

---

### Task 4: `created_by` in run.yml

**Files:**
- Modify: `migration_validator/runs/manifest.py`, `migration_validator/api.py` (`create_run`, `_write_group_members`, `create_group`, `add_group_devices`, `update_mapping`), `migration_validator/gui/app.py` (`_detail`, `_run_summary`)
- Test: `tests/runs/test_manifest_created_by.py` (or append to the existing manifest test module in `tests/runs/`), `tests/test_api_runs.py`, `tests/test_api_groups.py`

**Interfaces:**
- Produces: `RunManifest.created_by: str | None = None`; keyword `created_by: str | None = None` on `api.create_run`, `api.create_group`, `api.add_group_devices`; `"created_by"` key in `GET /api/runs/{run}` and each `GET /api/runs` item.

- [ ] **Step 1: Write failing tests**

```python
# tests/runs/test_manifest_created_by.py
from migration_validator.runs.manifest import RunDevice, RunManifest, load_manifest, save_manifest


def _manifest(**kw):
    return RunManifest(
        devices={"MX1": RunDevice(host="10.0.0.1", platform="junos", role="single")},
        kind="single", **kw,
    )


def test_created_by_round_trip(tmp_path):
    path = tmp_path / "run.yml"
    save_manifest(_manifest(created_by="rado"), path)
    assert "created_by: rado" in path.read_text()
    assert load_manifest(path).created_by == "rado"


def test_created_by_absent_not_written(tmp_path):
    path = tmp_path / "run.yml"
    save_manifest(_manifest(), path)
    assert "created_by" not in path.read_text()
    assert load_manifest(path).created_by is None
```

(Check `RunDevice`'s constructor and `load_manifest`/`save_manifest` signatures in `manifest.py` and adapt the helper if they differ.)

Append to `tests/test_api_runs.py`:

```python
def test_create_run_records_created_by_and_mapping_edit_keeps_it(tmp_path):
    api.create_run("mig09", kind="migration", devices=[OLD, NEW], run_root=tmp_path, created_by="rado")
    api.update_mapping("mig09", [("ge-0/0/1", "et-0/0/1")], run_root=tmp_path)
    assert RunStore(tmp_path, "mig09").load().created_by == "rado"
```

(Use the module's existing `OLD`/`NEW` device dicts and imports; check `update_mapping`'s real signature at `api.py:520` and call it accordingly.)

Append to `tests/test_api_groups.py`:

```python
def test_group_members_record_created_by(tmp_path):
    api.create_group("g1", [{"node": "MX1", "host": "10.0.0.1", "platform": "junos"}],
                     run_root=tmp_path, created_by="rado")
    api.add_group_devices("g1", [{"node": "MX2", "host": "10.0.0.2", "platform": "junos"}],
                          run_root=tmp_path, created_by="eva")
    assert RunStore(tmp_path, "g1-MX1").load().created_by == "rado"
    assert RunStore(tmp_path, "g1-MX2").load().created_by == "eva"
```

(Adapt device dict keys to what `_validate_group_devices` expects; check existing tests in the file.)

- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/python -m pytest tests/runs/test_manifest_created_by.py tests/test_api_runs.py tests/test_api_groups.py -q`
Expected: TypeError on unexpected keyword `created_by`.

- [ ] **Step 3: Implement**

- `RunManifest`: add field after `group`:
  ```python
      # Kdo run zalozil v GUI (spec 2026-09-25); None = CLI / starsi run.yml.
      created_by: str | None = None
  ```
- `load_manifest`: `created_by=raw.get("created_by"),`
- `save_manifest`: after the `group` block: `if manifest.created_by is not None: data["created_by"] = manifest.created_by`
- `api.create_run(..., created_by: str | None = None)` → `RunManifest(devices=loaded, kind=kind, profile=profile, created_by=created_by)`
- `_write_group_members(..., created_by: str | None = None)` → pass into `RunManifest(...)`; `create_group(..., created_by=None)` and `add_group_devices(..., created_by=None)` pass it through.
- `update_mapping`'s `rebuilt = RunManifest(...)` adds `created_by=manifest.created_by`.
- `gui/app.py` `_detail` and `_run_summary`: add `"created_by": manifest.created_by`.
- Grep for any other `RunManifest(` construction (`grep -rn "RunManifest(" migration_validator`) and carry `created_by` wherever an existing manifest is rebuilt.

- [ ] **Step 4: Run tests + whole suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/runs/manifest.py migration_validator/api.py migration_validator/gui/app.py tests/
git commit -m "feat(runs): record created_by in run.yml"
```

---

### Task 5: Sessions, login throttle, actor provider, 401 and permission relabel

**Files:**
- Create: `migration_validator/gui/sessions.py`
- Modify: `migration_validator/gui/authz.py`, `migration_validator/gui/app.py`, `migration_validator/gui/group_routes.py`, `migration_validator/gui/profile_routes.py`
- Test: `tests/gui/test_sessions.py`, `tests/gui/test_permission_matrix.py`, modify `tests/gui/test_authz.py`

**Interfaces:**
- Consumes: `UserStore`, `User` (Task 2).
- Produces:
  - `sessions.SESSION_COOKIE = "mig_session"`, `IDLE_TIMEOUT_S = 8 * 3600`, `ABSOLUTE_TIMEOUT_S = 12 * 3600`, `LOCKOUT_THRESHOLD = 5`, `LOCKOUT_S = 300`
  - `class SessionStore(clock: Callable[[], float] = time.monotonic, idle: float = IDLE_TIMEOUT_S, absolute: float = ABSOLUTE_TIMEOUT_S)`: `create(username) -> str`, `lookup(token) -> str | None`, `drop(token) -> None`
  - `class LoginThrottle(clock=time.monotonic, threshold=LOCKOUT_THRESHOLD, lockout=LOCKOUT_S)`: `retry_after(username, ip) -> int`, `failure(username, ip) -> None`, `success(username, ip) -> None`
  - `authz.Actor(role: str, username: str = "anonymous")`; `ActorProvider = Callable[[Request], Actor | None]`; `session_actor_provider(sessions, users) -> ActorProvider`; `permissions_for(role: str) -> list[str]`; `require()` → 401 `{"detail": "login required"}` when the provider returns `None`.

- [ ] **Step 1: Write failing session/throttle tests** — `tests/gui/test_sessions.py`

```python
from migration_validator.gui.sessions import LoginThrottle, SessionStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_session_create_lookup_drop():
    store = SessionStore(clock=Clock())
    token = store.create("rado")
    assert len(token) >= 40
    assert store.lookup(token) == "rado"
    store.drop(token)
    assert store.lookup(token) is None
    assert store.lookup("nonsense") is None


def test_session_idle_timeout_slides():
    clock = Clock()
    store = SessionStore(clock=clock, idle=100, absolute=1000)
    token = store.create("rado")
    clock.now += 90
    assert store.lookup(token) == "rado"   # refreshes last_seen
    clock.now += 90
    assert store.lookup(token) == "rado"
    clock.now += 101
    assert store.lookup(token) is None


def test_session_absolute_timeout():
    clock = Clock()
    store = SessionStore(clock=clock, idle=100, absolute=250)
    token = store.create("rado")
    for _ in range(3):
        clock.now += 80
        assert store.lookup(token) == "rado"
    clock.now += 20  # 260 s since creation, only 20 s idle
    assert store.lookup(token) is None


def test_throttle_locks_after_five_failures_and_expires():
    clock = Clock()
    throttle = LoginThrottle(clock=clock, threshold=5, lockout=300)
    for _ in range(4):
        throttle.failure("rado", "1.2.3.4")
        assert throttle.retry_after("rado", "1.2.3.4") == 0
    throttle.failure("rado", "1.2.3.4")
    assert throttle.retry_after("rado", "1.2.3.4") == 300
    assert throttle.retry_after("rado", "5.6.7.8") == 0     # other IP unaffected
    assert throttle.retry_after("eva", "1.2.3.4") == 0      # other user unaffected
    clock.now += 299.5
    assert throttle.retry_after("rado", "1.2.3.4") == 1
    clock.now += 1
    assert throttle.retry_after("rado", "1.2.3.4") == 0
    throttle.failure("rado", "1.2.3.4")                     # counter restarted
    assert throttle.retry_after("rado", "1.2.3.4") == 0


def test_throttle_success_resets():
    throttle = LoginThrottle(clock=Clock(), threshold=5, lockout=300)
    for _ in range(4):
        throttle.failure("rado", "ip")
    throttle.success("rado", "ip")
    for _ in range(4):
        throttle.failure("rado", "ip")
    assert throttle.retry_after("rado", "ip") == 0
```

- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/python -m pytest tests/gui/test_sessions.py -q` → ModuleNotFoundError.

- [ ] **Step 3: Implement `migration_validator/gui/sessions.py`**

```python
"""Prihlasene session GUI - v pameti procesu (restart = vsichni se
prihlasi znovu, rozhodnuto ve specu 2026-09-25)."""

from __future__ import annotations

import math
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

SESSION_COOKIE = "mig_session"
IDLE_TIMEOUT_S = 8 * 3600
ABSOLUTE_TIMEOUT_S = 12 * 3600
LOCKOUT_THRESHOLD = 5
LOCKOUT_S = 300


@dataclass
class _Session:
    username: str
    created_at: float
    last_seen: float


class SessionStore:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        idle: float = IDLE_TIMEOUT_S,
        absolute: float = ABSOLUTE_TIMEOUT_S,
    ):
        self._clock = clock
        self._idle = idle
        self._absolute = absolute
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def _expired(self, session: _Session, now: float) -> bool:
        return now - session.last_seen > self._idle or now - session.created_at > self._absolute

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            for key in [k for k, s in self._sessions.items() if self._expired(s, now)]:
                del self._sessions[key]
            self._sessions[token] = _Session(username, now, now)
        return token

    def lookup(self, token: str) -> str | None:
        now = self._clock()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if self._expired(session, now):
                del self._sessions[token]
                return None
            session.last_seen = now
            return session.username

    def drop(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)


@dataclass
class _Failures:
    count: int
    last_failure: float
    locked_until: float = 0.0


class LoginThrottle:
    """5 neuspechu za sebou pro (jmeno, IP) -> zamek na 5 minut."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        threshold: int = LOCKOUT_THRESHOLD,
        lockout: float = LOCKOUT_S,
    ):
        self._clock = clock
        self._threshold = threshold
        self._lockout = lockout
        self._entries: dict[tuple[str, str], _Failures] = {}
        self._lock = threading.Lock()

    def retry_after(self, username: str, ip: str) -> int:
        now = self._clock()
        with self._lock:
            entry = self._entries.get((username, ip))
            if entry is None or entry.locked_until <= now:
                return 0
            return math.ceil(entry.locked_until - now)

    def failure(self, username: str, ip: str) -> None:
        now = self._clock()
        with self._lock:
            if len(self._entries) > 10_000:
                # Ochrana pameti proti zaplave ruznych jmen.
                stale = [k for k, e in self._entries.items()
                         if e.locked_until <= now and now - e.last_failure > self._lockout]
                for key in stale:
                    del self._entries[key]
            entry = self._entries.get((username, ip))
            if entry is None or (entry.locked_until and entry.locked_until <= now) \
                    or now - entry.last_failure > self._lockout:
                entry = _Failures(count=0, last_failure=now)
                self._entries[(username, ip)] = entry
            entry.count += 1
            entry.last_failure = now
            if entry.count >= self._threshold:
                entry.locked_until = now + self._lockout
                entry.count = 0

    def success(self, username: str, ip: str) -> None:
        with self._lock:
            self._entries.pop((username, ip), None)
```

Run: `.venv/bin/python -m pytest tests/gui/test_sessions.py -q` → PASS.

- [ ] **Step 4: Write failing authz + matrix tests**

In `tests/gui/test_authz.py`: add

```python
def test_no_actor_is_401(tmp_path):
    api.create_run("mig01", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    app = create_app(run_root=tmp_path)
    app.state.actor_provider = lambda request: None
    client = TestClient(app)
    resp = client.get("/api/runs")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "login required"}


def test_session_provider_follows_users_file(tmp_path, monkeypatch):
    from migration_validator import users as users_mod
    from migration_validator.gui.authz import session_actor_provider
    from migration_validator.gui.sessions import SESSION_COOKIE, SessionStore
    from migration_validator.users import User, UserStore, hash_password

    store = UserStore(tmp_path / "users.yml")
    store.save({"eva": User("eva", "viewer", hash_password("correct horse battery", iterations=1000))})
    sessions = SessionStore()
    token = sessions.create("eva")
    provider = session_actor_provider(sessions, store)

    class Req:
        cookies = {SESSION_COOKIE: token}

    assert provider(Req()) == Actor(role="viewer", username="eva")
    store.save({"eva": User("eva", "admin", store.load()["eva"].password_hash)})
    import os
    st = store.path.stat()
    os.utime(store.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert provider(Req()).role == "admin"
    store.save({})
    st = store.path.stat()
    os.utime(store.path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))
    assert provider(Req()) is None
    assert sessions.lookup(token) is None  # deleted user's session is dropped

    class NoCookie:
        cookies = {}

    assert provider(NoCookie()) is None


def test_permissions_for():
    from migration_validator.gui.authz import permissions_for
    assert permissions_for("viewer") == ["view"]
    assert permissions_for("operator") == ["view", "operate"]
    assert permissions_for("admin") == ["view", "operate", "admin"]
    assert permissions_for("guest") == []
```

Update the existing guard test `test_kazda_api_routa_ma_authz_zavislost` to accept an explicit public allowlist (used from Task 6 on):

```python
PUBLIC_API = {"POST /api/login", "POST /api/logout"}
...
        if not _dependant_uses_authz(dependant):
            methods = sorted(getattr(route, "methods", []) or [])
            label = f"{','.join(methods)} {path}"
            if label not in PUBLIC_API:
                unguarded.append(label)
```

Create `tests/gui/test_permission_matrix.py`:

```python
"""Pins the spec's permission matrix: every /api route x every role."""

import pytest
from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}

# (method, path, minimum role). Bodies are irrelevant: an allowed role may get
# 404/409/422, a disallowed one must get 403 before validation... except that
# FastAPI validates the body before dependencies run, so every write sends a
# body that passes pydantic validation.
MATRIX = [
    ("GET", "/api/checks", "viewer"),
    ("GET", "/api/meta", "viewer"),
    ("GET", "/api/runs", "viewer"),
    ("GET", "/api/runs/mig01", "viewer"),
    ("GET", "/api/runs/mig01/evaluation", "viewer"),
    ("GET", "/api/runs/mig01/snapshots/x.json/evaluation", "viewer"),
    ("GET", "/api/captures/nope", "viewer"),
    ("GET", "/api/profiles", "viewer"),
    ("GET", "/api/profiles/catalogue", "viewer"),
    ("GET", "/api/profiles/p1", "viewer"),
    ("GET", "/api/groups", "viewer"),
    ("GET", "/api/groups/g1", "viewer"),
    ("POST", "/api/runs", "operator"),
    ("PUT", "/api/runs/mig01/mapping", "operator"),
    ("POST", "/api/runs/mig01/archive", "operator"),
    ("POST", "/api/runs/mig01/upgrade", "operator"),
    ("POST", "/api/captures", "operator"),
    ("POST", "/api/groups", "operator"),
    ("POST", "/api/groups/g1/devices", "operator"),
    ("POST", "/api/groups/g1/captures", "operator"),
    ("POST", "/api/groups/g1/archive", "operator"),
    ("POST", "/api/groups/g1/upgrade", "operator"),
    ("POST", "/api/profiles/preview", "operator"),
    ("POST", "/api/profiles", "operator"),
    ("PUT", "/api/profiles/p1", "operator"),
    ("DELETE", "/api/profiles/p1", "operator"),
]

BODIES = {
    ("POST", "/api/runs"): {"name": "zz", "kind": "migration", "devices": [], "mappings": []},
    ("PUT", "/api/runs/mig01/mapping"): {"mappings": []},
    ("POST", "/api/captures"): {"run": "nope", "device": "MX1", "phase": "pre"},
    ("POST", "/api/groups"): {"group": "zz", "devices": []},
    ("POST", "/api/groups/g1/devices"): {"devices": []},
    ("POST", "/api/groups/g1/captures"): {"phase": "nope"},
    ("POST", "/api/profiles/preview"): {"document": {}},
    ("POST", "/api/profiles"): {"name": "zz", "document": {}},
    ("PUT", "/api/profiles/p1"): {"document": {}},
}

RANK = {"viewer": 0, "operator": 1, "admin": 2}
PUBLIC = {("POST", "/api/login"), ("POST", "/api/logout")}


def _client(tmp_path, actor):
    api.create_run("mig01", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    app = create_app(run_root=tmp_path, profiles_root=tmp_path / "profiles")
    app.state.actor_provider = lambda request: actor
    # An allowed role may hit a route that fails on the thin fixture (e.g.
    # upgrade without raw records); only 401/403 matter here.
    return TestClient(app, raise_server_exceptions=False), app


def _call(client, method, path):
    return client.request(method, path, json=BODIES.get((method, path)))


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
@pytest.mark.parametrize("method, path, minimum", MATRIX)
def test_matrix(tmp_path, role, method, path, minimum):
    client, _ = _client(tmp_path, Actor(role=role, username="u"))
    status = _call(client, method, path).status_code
    if RANK[role] >= RANK[minimum]:
        assert status not in (401, 403), f"{role} {method} {path} -> {status}"
    else:
        assert status == 403, f"{role} {method} {path} -> {status}"


@pytest.mark.parametrize("method, path, minimum", MATRIX)
def test_no_session_is_401(tmp_path, method, path, minimum):
    client, _ = _client(tmp_path, None)
    assert _call(client, method, path).status_code == 401


def test_matrix_covers_every_api_route(tmp_path):
    _, app = _client(tmp_path, Actor(role="admin"))
    covered = {(m, p) for m, p, _ in MATRIX} | PUBLIC
    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        for method in getattr(route, "methods", None) or []:
            if method == "HEAD":
                continue
            if not any(m == method and _matches(route.path, p) for m, p in covered):
                missing.append(f"{method} {path}")
    assert not missing, f"routes missing from MATRIX: {missing}"


def _matches(template: str, concrete: str) -> bool:
    t, c = template.strip("/").split("/"), concrete.strip("/").split("/")
    return len(t) == len(c) and all(a == b or a.startswith("{") for a, b in zip(t, c))
```

If a write route's body shape above fails pydantic validation and the 403/401 assertion fails because of it, fix the body in `BODIES` from the route's `BaseModel`, not the assertion. `/api/me` does not exist yet — Task 6 adds its MATRIX row `("GET", "/api/me", "viewer")`.

- [ ] **Step 5: Run and see them fail**

Run: `.venv/bin/python -m pytest tests/gui/test_authz.py tests/gui/test_permission_matrix.py -q`
Expected: failures for 401 (currently 403/200), relabelled routes (operator gets 403), `session_actor_provider` import.

- [ ] **Step 6: Implement authz + relabels**

`authz.py`:

```python
@dataclass(frozen=True)
class Actor:
    role: str
    username: str = "anonymous"


ActorProvider = Callable[[Request], "Actor | None"]


def anonymous_admin(request: Request) -> Actor:
    """Bez users souboru (testy, vlozene pouziti). `mig-validate gui` ho
    nikdy nepouzije - bez uzivatelu odmitne start."""
    return Actor(role="admin")


def session_actor_provider(sessions: SessionStore, users: UserStore) -> ActorProvider:
    """Role se cte z users souboru pri kazdem requestu - CLI zmena role
    nebo smazani uctu plati hned."""

    def provider(request: Request) -> Actor | None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        username = sessions.lookup(token)
        if username is None:
            return None
        user = users.get(username)
        if user is None:
            sessions.drop(token)
            return None
        return Actor(role=user.role, username=user.username)

    return provider


def permissions_for(role: str) -> list[str]:
    actor = Actor(role=role)
    return [p.value for p in Permission if allows(actor, p)]
```

`current_actor` return type becomes `Actor | None`. In `require`'s dependency:

```python
    def dependency(actor: Actor | None = Depends(current_actor)) -> Actor:
        if actor is None:
            raise HTTPException(status_code=401, detail="login required")
        if not allows(actor, permission):
            ...
```

Update the module docstring (the future-roles note is now implemented). Relabel ADMIN → OPERATE on: `app.py` archive_run, upgrade_run; `group_routes.py` archive_group, upgrade_group; `profile_routes.py` preview, create_profile, update_profile, delete_profile. Update any existing tests that asserted operator/viewer 403 on those routes (grep `tests/gui` for `"admin"` role fixtures and `vyzaduje admin`).

- [ ] **Step 7: Run tests + whole suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add migration_validator/gui tests/gui
git commit -m "feat(gui): session store, login throttle, session actor, 401 and operator relabels"
```

---

### Task 6: Login/logout, `/api/me`, `/login` page, `/` redirect, Origin check, `create_app(users=…)`

**Files:**
- Create: `migration_validator/gui/auth_routes.py`, `migration_validator/gui/static/login.html`, `migration_validator/gui/static/login.js`
- Modify: `migration_validator/gui/app.py`, `migration_validator/gui/static/style.css` (login styles)
- Test: `tests/gui/test_login.py`; add `("GET", "/api/me", "viewer")` to `MATRIX` in `tests/gui/test_permission_matrix.py`

**Interfaces:**
- Consumes: Task 2 `UserStore`, `verify_password`, `needs_rehash`, `hash_password`, `dummy_hash`; Task 5 `SessionStore`, `LoginThrottle`, `SESSION_COOKIE`, `session_actor_provider`, `permissions_for`, `current_actor`.
- Produces: `create_app(..., users: UserStore | None = None, sessions: SessionStore | None = None, throttle: LoginThrottle | None = None)`; `app.state.auth_enabled: bool`; `build_auth_router(users, sessions, throttle) -> APIRouter`; `GET /api/me` → `{"username", "role", "permissions", "auth"}`.

- [ ] **Step 1: Write failing tests** — `tests/gui/test_login.py`

```python
import pytest
from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator import users as users_mod
from migration_validator.gui.app import create_app
from migration_validator.gui.sessions import SESSION_COOKIE, LoginThrottle, SessionStore
from migration_validator.users import User, UserStore, hash_password

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}
PW = "correct horse battery"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(users_mod, "PASSWORD_ITERATIONS", 1_000)
    users_mod.dummy_hash.cache_clear()
    store = UserStore(tmp_path / "users.yml")
    store.save({
        "rado": User("rado", "admin", hash_password(PW)),
        "eva": User("eva", "viewer", hash_password(PW)),
    })
    api.create_run("mig01", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    clock = Clock()
    app = create_app(
        run_root=tmp_path, profiles_root=tmp_path / "profiles", users=store,
        sessions=SessionStore(clock=clock), throttle=LoginThrottle(clock=clock),
    )
    # base_url https so the Secure cookie is sent back by the test client
    return TestClient(app, base_url="https://testserver"), store, clock


def _login(client, username="rado", password=PW):
    return client.post("/api/login", json={"username": username, "password": password})


def test_login_sets_secure_cookie_and_me(env):
    client, _, _ = env
    resp = _login(client)
    assert resp.status_code == 200
    assert resp.json() == {"username": "rado", "role": "admin"}
    cookie = resp.headers["set-cookie"]
    for flag in ("mig_session=", "HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert flag.lower() in cookie.lower()
    assert "max-age" not in cookie.lower()
    me = client.get("/api/me").json()
    assert me == {"username": "rado", "role": "admin",
                  "permissions": ["view", "operate", "admin"], "auth": True}


def test_wrong_password_and_unknown_user_look_identical(env):
    client, _, _ = env
    a = _login(client, password="wrong password!!")
    b = _login(client, username="nobody")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": "invalid username or password"}
    assert "set-cookie" not in a.headers
    assert client.get("/api/runs").status_code == 401


def test_throttle_429_then_recovers(env):
    client, _, clock = env
    for _ in range(5):
        assert _login(client, password="wrong password!!").status_code == 401
    resp = _login(client)  # even the right password is refused while locked
    assert resp.status_code == 429
    assert "try again in 300 s" in resp.json()["detail"]
    clock.now += 301
    assert _login(client).status_code == 200


def test_logout_invalidates_session(env):
    client, _, _ = env
    _login(client)
    token = client.cookies.get(SESSION_COOKIE)
    assert client.post("/api/logout").status_code == 204
    client.cookies.set(SESSION_COOKIE, token)  # replaying the old token
    assert client.get("/api/runs").status_code == 401


def test_logout_without_session_is_204(env):
    client, _, _ = env
    assert client.post("/api/logout").status_code == 204


def test_session_expires_after_idle(env):
    client, _, clock = env
    _login(client)
    clock.now += 8 * 3600 + 1
    assert client.get("/api/runs").status_code == 401


def test_viewer_role_enforced_after_login(env):
    client, _, _ = env
    _login(client, "eva")
    assert client.get("/api/runs").status_code == 200
    assert client.post("/api/runs/mig01/archive").status_code == 403


def test_index_redirects_to_login_without_session(env):
    client, _, _ = env
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"
    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert "<form" in login_page.text


def test_login_page_redirects_when_logged_in(env):
    client, _, _ = env
    _login(client)
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 302 and resp.headers["location"] == "/"
    assert client.get("/", follow_redirects=False).status_code == 200


def test_rehash_on_login(env, monkeypatch):
    client, store, _ = env
    monkeypatch.setattr(users_mod, "PASSWORD_ITERATIONS", 2_000)
    assert _login(client).status_code == 200
    assert store.load()["rado"].password_hash.split("$")[1] == "2000"


def test_origin_mismatch_rejected(env):
    client, _, _ = env
    _login(client)
    resp = client.post("/api/runs/mig01/archive", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "cross-origin request refused"}
    ok = client.post("/api/runs/mig01/archive", headers={"Origin": "https://testserver"})
    assert ok.status_code == 200


def test_origin_check_applies_to_login(env):
    client, _, _ = env
    resp = client.post("/api/login", json={"username": "rado", "password": PW},
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_anonymous_mode_me(tmp_path):
    client = TestClient(create_app(run_root=tmp_path))
    assert client.get("/api/me").json() == {
        "username": "anonymous", "role": "admin",
        "permissions": ["view", "operate", "admin"], "auth": False,
    }
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"
    assert client.get("/", follow_redirects=False).status_code == 200
```


- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/python -m pytest tests/gui/test_login.py -q` → TypeError `users` kwarg / 404s.

- [ ] **Step 3: Implement `gui/auth_routes.py`**

```python
"""/api/login a /api/logout - jedine /api routy bez require()."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from migration_validator.gui.sessions import SESSION_COOKIE, LoginThrottle, SessionStore
from migration_validator.users import (
    AuthenticationError,
    UserStore,
    dummy_hash,
    hash_password,
    needs_rehash,
    verify_password,
)

INVALID_LOGIN = "invalid username or password"


class LoginBody(BaseModel):
    username: str
    password: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _cookie_kwargs() -> dict:
    return {"httponly": True, "secure": True, "samesite": "strict", "path": "/"}


def build_auth_router(users: UserStore, sessions: SessionStore, throttle: LoginThrottle) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/login")
    def login(body: LoginBody, request: Request) -> JSONResponse:
        ip = _client_ip(request)
        wait = throttle.retry_after(body.username, ip)
        if wait:
            raise HTTPException(status_code=429, detail=f"too many failed logins, try again in {wait} s")
        user = users.get(body.username)
        stored = user.password_hash if user else dummy_hash()
        password_ok = verify_password(body.password[:2048], stored)
        if user is None or not password_ok:
            throttle.failure(body.username, ip)
            raise HTTPException(status_code=401, detail=INVALID_LOGIN)
        throttle.success(body.username, ip)
        if needs_rehash(user.password_hash):
            try:
                current = users.load()
                current[user.username] = replace(user, password_hash=hash_password(body.password))
                users.save(current)
            except (AuthenticationError, OSError):
                pass  # login still succeeds; rehash retried next time
        token = sessions.create(user.username)
        response = JSONResponse({"username": user.username, "role": user.role})
        response.set_cookie(SESSION_COOKIE, token, **_cookie_kwargs())
        return response

    @router.post("/logout", status_code=204)
    def logout(request: Request) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            sessions.drop(token)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, **_cookie_kwargs())
        return response

    return router
```

(Task 7 adds audit lines to both handlers.)

- [ ] **Step 4: Wire `create_app`**

In `app.py`:

```python
def create_app(
    run_root: Path = Path("runs"),
    profile_path: str | None = None,
    profiles_root: Path = Path("profiles"),
    capture_pool: int = DEFAULT_CAPTURE_POOL,
    users: UserStore | None = None,
    sessions: SessionStore | None = None,
    throttle: LoginThrottle | None = None,
) -> FastAPI:
    ...
    app.state.auth_enabled = users is not None
    if users is not None:
        sessions = sessions or SessionStore()
        app.state.actor_provider = session_actor_provider(sessions, users)
        app.include_router(build_auth_router(users, sessions, throttle or LoginThrottle()))
    else:
        app.state.actor_provider = anonymous_admin
```

`/api/me`:

```python
    @app.get("/api/me")
    def me(request: Request, actor: Actor = require(Permission.VIEW)) -> dict:
        return {
            "username": actor.username,
            "role": actor.role,
            "permissions": permissions_for(actor.role),
            "auth": request.app.state.auth_enabled,
        }
```

Origin middleware (register next to `static_no_cache`):

```python
    @app.middleware("http")
    async def same_origin_writes(request, call_next):
        # SameSite=Strict + tato kontrola = CSRF ochrana. Chybejici Origin
        # (curl, skripty) projde - prohlizec ho u techto metod posila vzdy.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse(status_code=403, content={"detail": "cross-origin request refused"})
        return await call_next(request)
```

`/` and `/login`:

```python
    @app.get("/", include_in_schema=False)
    def index(request: Request):
        if current_actor(request) is None:
            return RedirectResponse("/login", status_code=302)
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if not request.app.state.auth_enabled or current_actor(request) is not None:
            return RedirectResponse("/", status_code=302)
        return FileResponse(STATIC_DIR / "login.html")
```

Imports: `from urllib.parse import urlsplit`, `RedirectResponse`, `UserStore`, `SessionStore`, `LoginThrottle`, `session_actor_provider`, `permissions_for`, `current_actor`, `build_auth_router`.

- [ ] **Step 5: Login page**

`static/login.html`:

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mig-validate — sign in</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/style.css">
</head>
<body class="login-body">
<main class="login-card">
  <div class="brand">
    <div class="brand-mark">mv</div>
    <span class="brand-name">mig-validate</span>
  </div>
  <form id="login-form" novalidate>
    <label class="form-label" for="login-username">Username</label>
    <input class="form-input" id="login-username" name="username" autocomplete="username" required autofocus>
    <label class="form-label" for="login-password">Password</label>
    <input class="form-input" id="login-password" name="password" type="password" autocomplete="current-password" required>
    <div class="login-error" id="login-error" role="alert" hidden></div>
    <button class="btn btn-primary login-submit" type="submit">Sign in</button>
  </form>
</main>
<script src="/static/login.js"></script>
</body>
</html>
```

`static/login.js`:

```js
"use strict";

(function () {
  const form = document.getElementById("login-form");
  const errorEl = document.getElementById("login-error");
  const submit = form.querySelector("button[type=submit]");

  function showError(text) {
    errorEl.textContent = text;
    errorEl.hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorEl.hidden = true;
    const username = form.username.value.trim();
    const password = form.password.value;
    if (!username || !password) {
      showError("Enter username and password.");
      return;
    }
    submit.disabled = true;
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) {
        window.location.replace("/");
        return;
      }
      const body = await res.json().catch(() => ({}));
      showError(typeof body.detail === "string" ? body.detail : `Sign-in failed (${res.status}).`);
      form.password.value = "";
      form.password.focus();
    } catch (err) {
      showError("Server unreachable.");
    } finally {
      submit.disabled = false;
    }
  });
})();
```

`style.css` — append, using existing tokens (check the `:root` variables at the top of `style.css` and reuse their names instead of these fallbacks):

```css
.login-body { min-height: 100vh; display: flex; align-items: center; justify-content: center; }
.login-card { width: min(360px, calc(100vw - 32px)); padding: 28px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
.login-card .brand { margin-bottom: 20px; }
.login-card form { display: flex; flex-direction: column; gap: 8px; }
.login-error { color: var(--fail); font-size: 13px; }
.login-submit { margin-top: 12px; }
```

- [ ] **Step 6: Add the `/api/me` MATRIX row; run tests**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/gui tests/gui
git commit -m "feat(gui): login/logout, /api/me, login page, same-origin write check"
```

---

### Task 7: Audit log and `created_by` from the session

**Files:**
- Create: `migration_validator/gui/audit.py`
- Modify: `migration_validator/gui/app.py`, `group_routes.py`, `profile_routes.py`, `auth_routes.py`
- Test: `tests/gui/test_audit.py`

**Interfaces:**
- Consumes: `Actor.username` (Task 5); `created_by` kwargs (Task 4).
- Produces: `audit.AUDIT_LOGGER = "migration_validator.gui.audit"`; `audit.record(username: str, action: str, target: str = "", **extra: str) -> None`; `audit.configure_audit_logging(stream=None) -> None` (idempotent; own handler, `propagate=False`, level INFO, format `%(asctime)s audit %(message)s`).

- [ ] **Step 1: Write failing tests** — `tests/gui/test_audit.py`

```python
import logging

import pytest
from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor
from migration_validator.runs.store import RunStore

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}


@pytest.fixture
def client(tmp_path):
    app = create_app(run_root=tmp_path, profiles_root=tmp_path / "profiles")
    app.state.actor_provider = lambda request: Actor(role="operator", username="eva")
    return TestClient(app), tmp_path


def test_create_run_records_creator_and_audits(client, caplog):
    c, root = client
    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        resp = c.post("/api/runs", json={"name": "mig02", "kind": "migration", "devices": [OLD, NEW], "mappings": []})
    assert resp.status_code == 201
    assert resp.json()["created_by"] == "eva"
    assert RunStore(root, "mig02").load().created_by == "eva"
    assert "user=eva action=run-create target=mig02" in caplog.text


def test_archive_is_audited(client, caplog):
    c, root = client
    api.create_run("mig03", kind="migration", devices=[OLD, NEW], run_root=root)
    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        assert c.post("/api/runs/mig03/archive").status_code == 200
    assert "user=eva action=run-archive target=mig03" in caplog.text


def test_group_create_records_creator(client):
    c, root = client
    resp = c.post("/api/groups", json={"group": "g1", "devices": [{"node": "MX1", "host": "10.0.0.1", "platform": "junos"}]})
    assert resp.status_code == 201
    assert RunStore(root, "g1-MX1").load().created_by == "eva"


def test_upgrade_dry_run_not_audited(client, caplog):
    c, root = client
    api.create_run("mig04", kind="migration", devices=[OLD, NEW], run_root=root)
    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        c.post("/api/runs/mig04/upgrade?dry_run=true")
    assert "run-upgrade" not in caplog.text


def test_configure_audit_logging_writes_to_stream():
    import io
    from migration_validator.gui import audit

    stream = io.StringIO()
    audit.configure_audit_logging(stream)
    audit.configure_audit_logging(stream)  # idempotent: one line, not two
    audit.record("rado", "login", ip="1.2.3.4")
    lines = [l for l in stream.getvalue().splitlines() if "action=login" in l]
    assert len(lines) == 1 and "user=rado action=login ip=1.2.3.4" in lines[0]
```

`configure_audit_logging` sets `propagate=False`, which would hide audit lines from `caplog` in every later test. Add this autouse fixture at the top of `tests/gui/test_audit.py`:

```python
@pytest.fixture(autouse=True)
def restore_audit_logger():
    logger = logging.getLogger("migration_validator.gui.audit")
    handlers, propagate, level = list(logger.handlers), logger.propagate, logger.level
    yield
    logger.handlers[:] = handlers
    logger.propagate, logger.level = propagate, level
```

- [ ] **Step 2: Run and see them fail.** `.venv/bin/python -m pytest tests/gui/test_audit.py -q`

- [ ] **Step 3: Implement `gui/audit.py`**

```python
"""Audit stopa GUI: kdo co zmenil. Jen log, zadny soubor navic."""

from __future__ import annotations

import logging
import sys

AUDIT_LOGGER = "migration_validator.gui.audit"
_log = logging.getLogger(AUDIT_LOGGER)
_HANDLER_NAME = "mig-audit"


def record(username: str, action: str, target: str = "", **extra: str) -> None:
    parts = [f"user={username}", f"action={action}"]
    if target:
        parts.append(f"target={target}")
    parts.extend(f"{key}={value}" for key, value in extra.items())
    _log.info(" ".join(parts))


def configure_audit_logging(stream=None) -> None:
    """Vlastni handler - uvicorn root logger nekonfiguruje a INFO by se
    jinak ztratilo; propagate=False aby se ncclient INFO nepridal."""
    for handler in list(_log.handlers):
        if handler.get_name() == _HANDLER_NAME:
            _log.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(logging.Formatter("%(asctime)s audit %(message)s"))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)
    _log.propagate = False
```

- [ ] **Step 4: Add `record(...)` calls and `created_by`**

Action names (fixed vocabulary):

| Route | action | target |
|---|---|---|
| login ok / fail | `login` / `login-failed` | — (`ip=`) |
| logout (session known) | `logout` | — |
| `POST /api/runs` | `run-create` | run name; pass `created_by=actor.username` |
| `PUT /api/runs/{run}/mapping` | `mapping-edit` | run |
| `POST /api/runs/{run}/archive` | `run-archive` | run |
| `POST /api/runs/{run}/upgrade` (not dry_run) | `run-upgrade` | run |
| `POST /api/captures` | `capture-start` | `run/device/phase` (+ `/port` when set) |
| `POST /api/groups` | `group-create` | group; `created_by=actor.username` (+ `group-capture` when `capture_pre`) |
| `POST /api/groups/{g}/devices` | `group-add-devices` | group; `created_by=actor.username` |
| `POST /api/groups/{g}/captures` | `group-capture` | `group/phase` |
| `POST /api/groups/{g}/archive` | `group-archive` | group |
| `POST /api/groups/{g}/upgrade` (not dry_run) | `group-upgrade` | group |
| profile create / update / delete | `profile-create` / `profile-edit` / `profile-delete` | name |

Record only after the operation succeeded (after the `try` block). Login: `record(body.username, "login-failed", ip=ip)` on failure, `record(user.username, "login", ip=ip)` on success; logout: look up `sessions.lookup(token)` before dropping and record when non-None.

- [ ] **Step 5: Run whole suite.** `.venv/bin/python -m pytest -q -p no:warnings` → PASS.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui tests/gui
git commit -m "feat(gui): audit log lines and created_by from the session user"
```

---

### Task 8: `mig-validate gui` requires users; TLS/proxy flags

**Files:**
- Modify: `migration_validator/cli.py` (`_cmd_gui`, `gui` parser)
- Test: `tests/test_cli_gui.py`

**Interfaces:**
- Consumes: `load_auth_settings`, `load_settings`, `UserStore`, `create_app(users=…)`, `configure_audit_logging`.
- Produces: `gui` flags `--settings PATH`, `--ssl-certfile`, `--ssl-keyfile`, `--forwarded-allow-ips`.

- [ ] **Step 1: Write failing tests** — `tests/test_cli_gui.py`

```python
import pytest

pytest.importorskip("uvicorn")

from migration_validator import users as users_mod
from migration_validator.cli import EXIT_OK, EXIT_TOOL_ERROR, main
from migration_validator.users import User, UserStore, hash_password


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text(f"auth:\n  users_file: {tmp_path / 'users.yml'}\n")
    return path


@pytest.fixture
def captured(monkeypatch):
    calls = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))
    # Real configure would set propagate=False on the audit logger for the
    # rest of the session and hide it from caplog in other tests.
    monkeypatch.setattr("migration_validator.gui.audit.configure_audit_logging", lambda stream=None: None)
    return calls


def _add_admin(tmp_path):
    UserStore(tmp_path / "users.yml").save(
        {"rado": User("rado", "admin", hash_password("correct horse battery", iterations=1000))}
    )


def test_gui_refuses_without_users(settings, captured, capsys, tmp_path):
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path)]) == EXIT_TOOL_ERROR
    assert "mig-validate user add" in capsys.readouterr().err
    assert not captured


def test_gui_starts_with_auth(settings, captured, tmp_path):
    _add_admin(tmp_path)
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path)]) == EXIT_OK
    assert captured["app"].state.auth_enabled is True
    assert "ssl_certfile" not in captured and "forwarded_allow_ips" not in captured


def test_gui_passes_tls_and_proxy_flags(settings, captured, tmp_path):
    _add_admin(tmp_path)
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path),
                 "--ssl-certfile", "c.pem", "--ssl-keyfile", "k.pem",
                 "--forwarded-allow-ips", "10.0.0.5"]) == EXIT_OK
    assert captured["ssl_certfile"] == "c.pem"
    assert captured["ssl_keyfile"] == "k.pem"
    assert captured["forwarded_allow_ips"] == "10.0.0.5"


def test_gui_needs_both_tls_files(settings, captured, tmp_path, capsys):
    _add_admin(tmp_path)
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path),
                 "--ssl-certfile", "c.pem"]) == EXIT_TOOL_ERROR
    assert not captured
```

- [ ] **Step 2: Run and see them fail.** `.venv/bin/python -m pytest tests/test_cli_gui.py -q`

- [ ] **Step 3: Implement**

```python
def _cmd_gui(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError as error:
        raise ToolError("GUI vyzaduje 'pip install migration-validator[gui]'") from error
    from migration_validator.auth import load_auth_settings, load_settings
    from migration_validator.gui.app import create_app
    from migration_validator.gui.audit import configure_audit_logging
    from migration_validator.users import UserStore

    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        raise ToolError("--ssl-certfile a --ssl-keyfile se zadavaji spolu")
    users = UserStore(load_auth_settings(args.settings).users_file)
    if not users.load():
        raise ToolError(
            f"zadni uzivatele v {users.path} - zaloz admina: "
            "mig-validate user add <jmeno> --role admin"
        )
    app = create_app(
        run_root=args.run_root, profile_path=args.profile,
        profiles_root=args.profiles_root,
        capture_pool=load_settings(args.settings).capture_pool,
        users=users,
    )
    configure_audit_logging()
    options = {"host": args.host, "port": args.gui_port}
    if args.ssl_certfile:
        options.update(ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile)
    if args.forwarded_allow_ips:
        options["forwarded_allow_ips"] = args.forwarded_allow_ips
    uvicorn.run(app, **options)
    return EXIT_OK
```

Note `load_settings(args.settings)` with `args.settings=None` keeps today's default path. Parser additions on `gui`:

```python
    gui.add_argument("--settings", type=Path, default=None,
                     help="settings.yml (default config/settings.yml)")
    gui.add_argument("--ssl-certfile", default=None, help="TLS certifikat (HTTPS bez reverse proxy)")
    gui.add_argument("--ssl-keyfile", default=None, help="TLS privatni klic")
    gui.add_argument("--forwarded-allow-ips", default=None,
                     help="IP reverse proxy, ktere veri X-Forwarded-For (pro login throttle)")
```

- [ ] **Step 4: Run whole suite.** `.venv/bin/python -m pytest -q -p no:warnings` → PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/cli.py tests/test_cli_gui.py
git commit -m "feat(cli): gui requires users; --settings, TLS and proxy flags"
```

---

### Task 9: Frontend — 401 redirect, user chip + logout, role-based hiding, created_by; docs

**Files:**
- Modify: `migration_validator/gui/static/index.html`, `app.js`, `style.css`
- Modify docs: `docs/en/README.md`, `docs/cs/README.md`, `docs/en/reference.md`, `docs/cs/reference.md` (and `docs/*/files/top-level.md` if it lists modules — add `users.py`, `gui/sessions.py`, `gui/auth_routes.py`, `gui/audit.py`)

**Interfaces:**
- Consumes: `GET /api/me` `{username, role, permissions, auth}`, `POST /api/logout`, `created_by` in run detail.

- [ ] **Step 1: 401 → login, globally**

At the top of `app.js` (after `"use strict"` if present), wrap `window.fetch` once so none of the ~32 call sites change:

```js
// Session expired or logged out elsewhere: every API call lands on the login page.
const rawFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const res = await rawFetch(...args);
  if (res.status === 401) window.location.assign("/login");
  return res;
};
```

- [ ] **Step 2: User chip + logout in the topbar**

`index.html`: set `<body data-role="viewer">` (least privilege until `/api/me` answers) and add, right after `<div class="topbar-spacer"></div>`:

```html
      <div class="user-chip" id="user-chip" hidden>
        <span class="mono" id="user-name"></span>
        <span class="user-role" id="user-role"></span>
        <button class="btn btn-secondary btn-small" id="btn-logout" type="button">Log out</button>
      </div>
```

In `app.js` app start-up (find the init function that first loads `/api/runs` / `/api/meta`), load `/api/me` before the first render:

```js
  async loadMe() {
    const res = await fetch("/api/me");
    if (!res.ok) return;
    const me = await res.json();
    this.me = me;
    document.body.dataset.role = me.role;
    if (me.auth) {
      document.getElementById("user-name").textContent = me.username;
      document.getElementById("user-role").textContent = me.role;
      document.getElementById("user-chip").hidden = false;
      document.getElementById("btn-logout").addEventListener("click", async () => {
        await fetch("/api/logout", { method: "POST" });
        window.location.assign("/login");
      });
    }
  }
```

- [ ] **Step 3: Role-based hiding (UI hint only)**

`style.css`:

```css
body[data-role="viewer"] [data-perm="operate"],
body:not([data-role="admin"]) [data-perm="admin"] { display: none !important; }
.user-chip { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.user-role { color: var(--muted); }
```

Add `data-perm="operate"` to every control a viewer cannot use:

- `index.html`: `#btn-new-run`, `#btn-new-capture`, `#run-combo-new`.
- `app.js` (via `attrs: { "data-perm": "operate" }` in the `el(...)` call; merge with existing `attrs`): the "+ New run" button (~line 2328), "Upgrade run" / "Archive run" buttons (~2514, 2521, 2533), "Edit mapping" (~2597), "Upgrade group" / "Archive group" (~1360, 1365), "+ Add devices" (~1387), group capture buttons, per-run capture start buttons, profile editor Save / Delete / "+ New profile" (~4482, 4508 and the Save button nearby), and the "New group"/bulk entry point. Find each with `grep -n 'text: "' app.js` and read around it; do not rely on the line numbers.

The Profiles button stays visible (viewers may view profiles). Nothing is `data-perm="admin"` in wave A.

- [ ] **Step 4: created_by in the run detail header**

Find where the run overview renders kind/profile (search `detail.profile` or `"profile"` in the run overview render). When `detail.created_by` is set, append a muted `created by <name>` item in the same meta line. When absent, render nothing.

- [ ] **Step 5: Run JS tests and Python suite**

Run: `node --test tests/js/ && .venv/bin/python -m pytest -q -p no:warnings`
Expected: all PASS.

- [ ] **Step 6: Docs**

`docs/en/README.md` and `docs/cs/README.md` (GUI section) — add "Accounts and login":

- `mig-validate user add NAME --role viewer|operator|admin` (password prompted), `passwd`, `role`, `delete`, `list`; `--settings`.
- Permission matrix (copy the table from the spec).
- `auth.users_file` in `config/settings.yml` (default `config/users.yml`, mode 0600, gitignored).
- `mig-validate gui` refuses to start without users; flags `--settings`, `--ssl-certfile/--ssl-keyfile`, `--forwarded-allow-ips`.
- HTTPS is required for the session cookie except on 127.0.0.1/localhost; behind a reverse proxy keep the `Host` header (`proxy_set_header Host $host;`) or every write is refused as cross-origin.
- Sessions live in memory: a GUI restart logs everyone out; idle 8 h, absolute 12 h; 5 failed logins lock that username+IP for 5 minutes.
- Upgrade note: `config/settings.yml` is no longer tracked — copy it aside before `git pull`, restore afterwards.

`docs/*/reference.md` §8 (`run.yml`): document the optional `created_by` key. Czech docs in Czech, matching the existing tone.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/gui/static docs
git commit -m "feat(gui): login redirect, user chip, role-based controls; docs for accounts"
```

---

## Manual verification (after Task 9, controller)

```bash
cp config/settings.yml /tmp/settings.yml.bak   # untracked now, but keep a copy
.venv/bin/mig-validate user add rado --role admin
.venv/bin/mig-validate gui --port 8399
```

Open `http://127.0.0.1:8399/` → redirected to `/login`; sign in; header shows `rado admin Log out`; create a viewer, sign in as viewer in a private window → no New run / Archive / Upgrade / Edit mapping buttons; archive via curl as viewer → 403.
