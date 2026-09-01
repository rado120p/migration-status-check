# Core: settings.yml Auth + GUI Seams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace auth.yml with a repo-local `config/settings.yml` (ansible + ssh keys tried in order, password fallback), add an `on_progress` callback to capture, and add the `create_run` / `update_mapping` seam functions the GUI needs.

**Architecture:** All changes live in the existing core app: `auth.py` is rewritten around a `ConnectionSettings` dataclass, `connection/junos.py` learns multi-key auth attempts, `capture.py` gains a progress callback, and `api.py` gains two new seam functions. The CLI keeps working throughout — each task leaves the full test suite green.

**Tech Stack:** Python 3.11+, PyYAML, junos-eznc (PyEZ), pytest.

**Spec:** `design_handoff_migration_gui/README.md` — section "Backend Changes Required" plus the validation rules under screens 5/6.

## Global Constraints

- Python `>=3.11` (pyproject). Run tests with the project venv: `pyats-venv/bin/python -m pytest`.
- All user-facing engine/CLI messages are **Czech without diacritics** (project convention — see existing messages in `cli.py`, `auth.py`). GUI chrome will be English, but that is plan 2.
- Test names follow the existing Czech convention (`test_flag_prebiji_auth_soubor` style) in files that already use it; English is fine in new files if you keep it consistent within the file.
- `auth.yml` support is removed outright — no migration shim, no deprecation message (spec: "still in development").
- Docstrings/comments state constraints, not narration; match the existing comment style (Czech, explains *why*).
- Port names in `run.yml` are stored **raw** (`ge-0/0/2`); `normalize_port` is only for snapshot/inventory *filenames*.
- Commit after every task; message style follows repo history (`feat:`/`fix:`/`docs:` prefixes, Czech summary).

---

### Task 1: `ConnectionSettings` + `load_settings` (rewrite `auth.py`)

**Files:**
- Modify: `migration_validator/auth.py` (full rewrite)
- Test: `tests/test_auth.py` (full rewrite)

**Interfaces:**
- Produces: `ConnectionSettings(username: str, ssh_key_paths: tuple[str, ...], netconf_port: int, timeout: int, password: str | None)` (frozen dataclass), `load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> ConnectionSettings`, `DEFAULT_SETTINGS_PATH = Path("config") / "settings.yml"`. `ssh_key_paths` in the returned settings are already `expanduser()`-ed strings.
- Consumes: nothing.

- [ ] **Step 1: Write the failing tests** — replace the whole content of `tests/test_auth.py`:

```python
"""Testy pro config/settings.yml - nahrada auth.yml."""

import pytest

from migration_validator.auth import (
    DEFAULT_NETCONF_PORT,
    DEFAULT_SETTINGS_PATH,
    ConnectionSettings,
    load_settings,
)


def test_chybejici_soubor_vraci_defaulty(tmp_path):
    settings = load_settings(tmp_path / "settings.yml")
    assert settings.username == "ansible"
    assert settings.netconf_port == 830
    assert settings.timeout == 30
    assert settings.password is None
    assert len(settings.ssh_key_paths) == 2
    assert settings.ssh_key_paths[0].endswith("/.ssh/id_ed25519")
    assert settings.ssh_key_paths[1].endswith("/.ssh/id_rsa")
    # expanduser probehl uz pri load - zadna ~ v cestach
    assert not any(p.startswith("~") for p in settings.ssh_key_paths)


def test_default_cesta_je_v_repu():
    assert str(DEFAULT_SETTINGS_PATH) == "config/settings.yml"


def test_plny_soubor(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text(
        "connection:\n"
        "  netconf_port: 22\n"
        "  timeout: 60\n"
        "  username: rmohyla\n"
        "  ssh_key_paths:\n"
        "    - ~/.ssh/moje_klic\n",
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert settings.username == "rmohyla"
    assert settings.netconf_port == 22
    assert settings.timeout == 60
    assert len(settings.ssh_key_paths) == 1
    assert settings.ssh_key_paths[0].endswith("/.ssh/moje_klic")


def test_password_env_se_cte_z_prostredi(tmp_path, monkeypatch):
    monkeypatch.setenv("MIG_TEST_PW", "tajne")
    path = tmp_path / "settings.yml"
    path.write_text(
        "connection:\n  password_env: MIG_TEST_PW\n", encoding="utf-8"
    )
    assert load_settings(path).password == "tajne"


def test_password_env_nenastavena_je_chyba(tmp_path, monkeypatch):
    monkeypatch.delenv("MIG_TEST_PW", raising=False)
    path = tmp_path / "settings.yml"
    path.write_text(
        "connection:\n  password_env: MIG_TEST_PW\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="MIG_TEST_PW"):
        load_settings(path)


def test_plaintext_password_vyzaduje_0600(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  password: tajne\n", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="chmod 600"):
        load_settings(path)
    path.chmod(0o600)
    assert load_settings(path).password == "tajne"


def test_password_a_password_env_zaroven_je_chyba(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text(
        "connection:\n  password: a\n  password_env: B\n", encoding="utf-8"
    )
    path.chmod(0o600)
    with pytest.raises(ValueError, match="vyber jedno"):
        load_settings(path)


def test_neznamy_klic_je_chyba(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  passwd: preklep\n", encoding="utf-8")
    with pytest.raises(ValueError, match="passwd"):
        load_settings(path)


def test_soubor_bez_connection_bloku_vraci_defaulty(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("{}\n", encoding="utf-8")
    settings = load_settings(path)
    assert settings.username == "ansible"


def test_nemapovy_yaml_je_chyba(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("- polozka\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_settings(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/python -m pytest tests/test_auth.py -v`
Expected: FAIL/ERROR with `ImportError: cannot import name 'ConnectionSettings'`

- [ ] **Step 3: Rewrite `migration_validator/auth.py`** (replace entire file):

```python
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


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> ConnectionSettings:
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
        netconf_port=int(connection.get("netconf_port") or DEFAULT_NETCONF_PORT),
        timeout=int(connection.get("timeout") or DEFAULT_TIMEOUT),
        password=password,
    )
```

- [ ] **Step 4: Run the new tests**

Run: `pyats-venv/bin/python -m pytest tests/test_auth.py -v`
Expected: PASS (all). Note: `cli.py` still imports `AuthSettings`/`load_auth_file`/`DEFAULT_AUTH_PATH` which no longer exist — the *full* suite is expected red until Task 3. Do not run the full suite as a gate here; that is Task 3's job.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/auth.py tests/test_auth.py
git commit -m "feat: config/settings.yml nahrazuje auth.yml - ConnectionSettings + load_settings"
```

---

### Task 2: Multi-key auth in `connection/junos.py`

**Files:**
- Modify: `migration_validator/connection/junos.py`
- Test: `tests/connection/test_junos.py`

**Interfaces:**
- Produces: `ConnectionOptions(host: str, username: str = "ansible", ssh_key_paths: tuple[str, ...] = (), password: str | None = None, port: int = 830, timeout: int = 30)` with method `auth_attempts() -> list[dict[str, Any]]`; `connect(options)` context manager unchanged in signature. Fields `auth_type` and `key_file` are **removed**.
- Consumes: nothing from Task 1 (settings are wired in Task 3).

- [ ] **Step 1: Rewrite the auth-related tests** in `tests/connection/test_junos.py`. Delete `test_key_auth_kwargs`, `test_password_auth_kwargs`, `test_password_auth_requires_password`, `test_unknown_auth_type_is_rejected`, `test_default_key_file_points_to_ssh_dir` and add:

```python
from migration_validator.connection.junos import ConnectionOptions, JunosConnectionError


def test_auth_attempts_zkousi_klice_v_poradi(tmp_path):
    key1 = tmp_path / "id_ed25519"
    key2 = tmp_path / "id_rsa"
    key1.write_text("k1")
    key2.write_text("k2")
    options = ConnectionOptions(
        host="172.20.20.4", ssh_key_paths=(str(key1), str(key2))
    )
    attempts = options.auth_attempts()
    assert [a["ssh_private_key_file"] for a in attempts] == [str(key1), str(key2)]
    assert all(a["host"] == "172.20.20.4" for a in attempts)


def test_auth_attempts_preskoci_neexistujici_klic(tmp_path):
    existing = tmp_path / "id_rsa"
    existing.write_text("k")
    options = ConnectionOptions(
        host="h", ssh_key_paths=(str(tmp_path / "neni"), str(existing))
    )
    attempts = options.auth_attempts()
    assert len(attempts) == 1
    assert attempts[0]["ssh_private_key_file"] == str(existing)


def test_auth_attempts_heslo_je_posledni(tmp_path):
    key = tmp_path / "id_rsa"
    key.write_text("k")
    options = ConnectionOptions(
        host="h", ssh_key_paths=(str(key),), password="tajne"
    )
    attempts = options.auth_attempts()
    assert attempts[-1]["passwd"] == "tajne"
    assert "ssh_private_key_file" not in attempts[-1]


def test_auth_attempts_bez_moznosti_je_chyba(tmp_path):
    options = ConnectionOptions(host="h", ssh_key_paths=(str(tmp_path / "neni"),))
    with pytest.raises(JunosConnectionError, match="zadna pouzitelna autentizace"):
        options.auth_attempts()


def test_default_port_je_netconf():
    assert ConnectionOptions(host="h").port == 830
```

Keep the existing `pytest`/`Path` imports at the top of the file; the surviving tests (`detect_platform`, `device_meta`, connect-error tests) stay. If a surviving `connect()` test constructs `ConnectionOptions(..., auth_type=...)` or `key_file=`, update it to `ssh_key_paths=(...)` with a `tmp_path` key file.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `pyats-venv/bin/python -m pytest tests/connection/test_junos.py -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'ssh_key_paths'`

- [ ] **Step 3: Implement in `junos.py`.** Replace `DEFAULT_PORT = 22` with `DEFAULT_PORT = 830` and replace the `ConnectionOptions` dataclass and `connect` with:

```python
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
```

- [ ] **Step 4: Run the connection tests**

Run: `pyats-venv/bin/python -m pytest tests/connection/ -v`
Expected: PASS. (Full suite still red until Task 3 — `cli.py` builds `ConnectionOptions` with removed fields.)

- [ ] **Step 5: Commit**

```bash
git add migration_validator/connection/junos.py tests/connection/test_junos.py
git commit -m "feat: connect zkousi ssh klice v poradi, heslo jako fallback"
```

---

### Task 3: CLI rewire — settings replace auth flags, suite green

**Files:**
- Modify: `migration_validator/cli.py` (imports, `_add_auth_arguments`, `_auth_settings` → `_connection_settings`, `_connection_options`)
- Modify: `tests/test_cli.py` (the auth block, roughly lines 1280–1415)
- Create: `config/settings.yml` (lab config, gitignored password stays in env)

**Interfaces:**
- Consumes: `load_settings`, `ConnectionSettings`, `DEFAULT_SETTINGS_PATH` (Task 1); new `ConnectionOptions` (Task 2).
- Produces: CLI flags `--settings` (path), `--username`, `--password`, `--ssh-port`/`--port`, `--timeout`. Flags `--auth`, `--key-file`, `--auth-file` are **removed**. `_connection_options(args, settings: ConnectionSettings) -> ConnectionOptions`.

- [ ] **Step 1: Rewrite the auth-flag tests in `tests/test_cli.py`.** Delete the tests in the block commented `# --- profil, auth soubor, service-types (task 3) ---` that exercise `--auth-file`/`AuthSettings`/`DEFAULT_AUTH_PATH` (`test_capture_ma_auth_file_a_service_types`, `test_flag_prebiji_auth_soubor`, `test_auth_settings_necte_skutecny_domovsky_auth_soubor`, and the two nearby tests importing `AuthSettings` at lines ~1363/1372 — read them first; if they test flag precedence, port them to `ConnectionSettings`). Keep `test_capture_sitovy_port_se_nepropise_do_ssh_portu` but port it. Add:

```python
def test_flag_prebiji_settings():
    from migration_validator.auth import ConnectionSettings
    from migration_validator.cli import _connection_options, build_parser

    settings = ConnectionSettings(username="rmohyla", netconf_port=2222)
    args = build_parser().parse_args(
        ["capture", "--device", "r1", "--username", "jiny", "--output", "o.json"]
    )
    options = _connection_options(args, settings)
    assert options.username == "jiny"          # flag vyhrava
    assert options.port == 2222                # settings, flag nezadany


def test_settings_flag_urcuje_cestu(tmp_path):
    from migration_validator.cli import _connection_settings, build_parser

    path = tmp_path / "s.yml"
    path.write_text("connection:\n  username: laborant\n", encoding="utf-8")
    args = build_parser().parse_args(
        ["capture", "--device", "r1", "--settings", str(path), "--output", "o.json"]
    )
    assert _connection_settings(args).username == "laborant"


def test_chybejici_default_settings_neni_chyba(tmp_path, monkeypatch):
    from migration_validator.cli import _connection_settings, build_parser

    monkeypatch.chdir(tmp_path)  # zadny config/settings.yml v cwd
    args = build_parser().parse_args(
        ["capture", "--device", "r1", "--output", "o.json"]
    )
    assert _connection_settings(args).username == "ansible"


def test_capture_sitovy_port_se_nepropise_do_ssh_portu():
    # capture --port je sitovy port (ge-0/0/0); SSH/NETCONF port jde
    # ze settings/defaultu, nikdy z args.port.
    from migration_validator.auth import ConnectionSettings
    from migration_validator.cli import _connection_options, build_parser

    args = build_parser().parse_args(
        ["capture", "--device", "r1", "--run", "mig", "--port", "ge-0/0/0"]
    )
    options = _connection_options(args, ConnectionSettings(netconf_port=830))
    assert options.port == 830
```

- [ ] **Step 2: Run to verify failures**

Run: `pyats-venv/bin/python -m pytest tests/test_cli.py -v -k "settings or prebiji or sitovy_port"`
Expected: FAIL/ERROR (`_connection_settings` not defined; old imports broken).

- [ ] **Step 3: Rewire `cli.py`:**

Replace the import line `from migration_validator.auth import AuthSettings, DEFAULT_AUTH_PATH, load_auth_file` with:

```python
from migration_validator.auth import (
    DEFAULT_SETTINGS_PATH,
    ConnectionSettings,
    load_settings,
)
```

Replace `_add_auth_arguments`, `_auth_settings`, `_connection_options` with:

```python
def _add_auth_arguments(
    parser: argparse.ArgumentParser, *, port_flag: str = "--port", port_dest: str = "port"
) -> None:
    # default=None vsude: merge flag > settings > default se deje az
    # v _connection_options, argparse default by settings tise prebil.
    parser.add_argument("--username", default=None)
    parser.add_argument("--password")
    parser.add_argument(port_flag, dest=port_dest, type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument(
        "--settings",
        help="cesta k settings YAML (default config/settings.yml)",
    )


def _connection_settings(args: argparse.Namespace) -> ConnectionSettings:
    if args.settings:
        path = Path(args.settings)
        if not path.exists():
            raise ToolError(f"settings soubor nenalezen: {path}")
        return load_settings(path)
    return load_settings(DEFAULT_SETTINGS_PATH)


def _connection_options(
    args: argparse.Namespace, settings: ConnectionSettings
) -> ConnectionOptions:
    # capture ma --ssh-port (dest "ssh_port"), protoze --port u nej znamena
    # cislo/jmeno sitoveho portu v run rezimu; record pouziva puvodni --port
    # jako SSH port. Rozlisuje se pritomnosti atributu, ne hodnotou None.
    if hasattr(args, "ssh_port"):
        flag_port = args.ssh_port
    else:
        flag_port = getattr(args, "port", None)
    return ConnectionOptions(
        host=args.device,
        username=_pick(args.username, settings.username),
        ssh_key_paths=settings.ssh_key_paths,
        password=_pick(args.password, settings.password),
        port=_pick(flag_port, settings.netconf_port),
        timeout=_pick(args.timeout, settings.timeout),
    )
```

Then replace every call site `_auth_settings(args)` with `_connection_settings(args)` (three sites: `_cmd_capture`, `_parse_services_into`, `_cmd_record`). `load_settings` raising `ValueError` is already converted to `EXIT_TOOL_ERROR` by `main()`.

- [ ] **Step 4: Create `config/settings.yml`** for the lab:

```yaml
connection:
  netconf_port: 830
  timeout: 30
  username: rmohyla
  ssh_key_paths: []
  password_env: MIG_LAB_PASSWORD
```

Heads-up in the commit message: the lab previously connected on port 22 with defaults; if the containerlab boxes don't listen on 830, set `netconf_port: 22` here — verify against the lab before assuming (memory: `MIG_LAB_PASSWORD` loads via explicit eval from `~/.bashrc`).

- [ ] **Step 5: Run the FULL suite and fix stragglers**

Run: `pyats-venv/bin/python -m pytest`
Expected: PASS (~1223 tests). If anything still references `AuthSettings`, `load_auth_file`, `--auth-file`, `--key-file`, or `--auth`, port it the same way as Step 1. Also `grep -rn "auth_file\|AuthSettings\|load_auth_file\|key_file" migration_validator/ tests/` must come back empty (except `ssh_private_key_file` in junos.py and its tests).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/cli.py tests/test_cli.py config/settings.yml
git commit -m "feat: CLI cte config/settings.yml, flagy --auth/--key-file/--auth-file odstraneny"
```

---

### Task 4: `on_progress` callback in capture

**Files:**
- Modify: `migration_validator/capture.py:92-169` (`capture_device`)
- Modify: `migration_validator/api.py:22-54` (`capture`)
- Test: `tests/test_capture.py` (append)

**Interfaces:**
- Produces: `ProgressCallback = Callable[[str, str, str | None], None]` — arguments `(step, status, message)`; `status` is `"start" | "ok" | "error"`; `step` is a collector name or `"ping"`. New keyword-only parameter `on_progress: ProgressCallback | None = None` on both `capture_device` and `api.capture`.
- Consumes: nothing from prior tasks.

- [ ] **Step 1: Write the failing test** — append to `tests/test_capture.py` (reuse that file's existing fake-device fixtures/conftest; read its top 50 lines first to match how a device is faked there):

```python
def test_on_progress_hlasi_kolektory_a_ping(fake_device_zdroj):
    # fake_device_zdroj: pouzij stejny zpusob vyroby fake device jako
    # okolni testy v tomto souboru (conftest hardcoduje active: True atd.)
    events: list[tuple[str, str]] = []

    def on_progress(step: str, status: str, message: str | None) -> None:
        events.append((step, status))

    capture_device(
        fake_device_zdroj,
        address="1.2.3.4",
        on_progress=on_progress,
    )

    # kazdy kolektor hlasi start i vysledek
    starts = [s for s, st in events if st == "start"]
    finishes = [(s, st) for s, st in events if st in ("ok", "error")]
    assert len(starts) == len(finishes)
    # kdyz fake device nese scopes, posledni krok je hruby "ping"
    if "ping" in starts:
        assert starts[-1] == "ping"
```

Adapt the fixture name to the file's actual fake-device pattern (read `tests/test_capture.py`'s existing tests first and construct the device the same way they do) — if the shared fake has no inventory/scopes, ping events won't fire and the test asserts only collector events.

- [ ] **Step 2: Run to verify it fails**

Run: `pyats-venv/bin/python -m pytest tests/test_capture.py -v -k on_progress`
Expected: FAIL with `TypeError: capture_device() got an unexpected keyword argument 'on_progress'`

- [ ] **Step 3: Implement.** In `capture.py` add near the imports:

```python
from typing import Callable

ProgressCallback = Callable[[str, str, str | None], None]
```

Add `on_progress: ProgressCallback | None = None` as the last keyword parameter of `capture_device`. Wrap the collector loop:

```python
    for collector in selected:
        if on_progress is not None:
            on_progress(collector.name, "start", None)
        if record_raw is not None:
            _record(Path(record_raw), platform, collector.name, device, collector)
        try:
            facts[collector.name] = collector.collect(device, platform)
            status[collector.name] = {"status": "ok"}
            if on_progress is not None:
                on_progress(collector.name, "ok", None)
        except CollectorError as error:
            # Oblast zustane prazdna se spravnym typem - check ji uvidi jako
            # chybejici a diky failed_collectors() vrati SKIP, nikdy PASS.
            facts[collector.name] = _empty_for(collector.name)
            status[collector.name] = {"status": "error", "message": str(error)}
            if on_progress is not None:
                on_progress(collector.name, "error", str(error))
```

For ping, materialize the targets so the count is reportable, then emit one coarse step around the whole batch:

```python
        targets = list(
            resolve_targets(
                ping_scopes,
                facts.get("arp", []),
                facts.get("nd", []),
                baseline_arp=baseline_arp,
                baseline_nd=baseline_nd,
            )
        )
        if on_progress is not None:
            on_progress("ping", "start", f"{len(targets)} cilu")
        for target in targets:
            pings.append(run_ping(device, target, count=ping_count))
        if on_progress is not None:
            on_progress("ping", "ok", None)
```

In `api.py`, add `on_progress: "ProgressCallback | None" = None` to `capture(...)`'s keyword parameters (import `ProgressCallback` from `migration_validator.capture`) and pass it through to `capture_device`.

- [ ] **Step 4: Run tests**

Run: `pyats-venv/bin/python -m pytest tests/test_capture.py -v`
Expected: PASS (new and pre-existing).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/capture.py migration_validator/api.py tests/test_capture.py
git commit -m "feat: capture_device hlasi prubeh pres on_progress callback"
```

---

### Task 5: `api.create_run`

**Files:**
- Modify: `migration_validator/api.py` (append)
- Test: `tests/test_api_runs.py` (create)

**Interfaces:**
- Produces: `api.create_run(name: str, *, old_device: dict[str, str], new_device: dict[str, str], mappings: list[tuple[str, str]] | None = None, run_root: str | Path = Path("runs")) -> RunManifest`. Device dicts have keys `node`, `host`, `platform`. Mapping tuples are `(old_port, new_port)`, **raw** port strings. Raises `ValueError` on: invalid name, existing directory, missing device keys, duplicate old port mapped to two different new ports (via `RunManifest.add_mapping`).
- Consumes: `RunManifest`, `RunDevice`, `MappingEndpoint`, `save_manifest` from `migration_validator.runs.manifest`; `RunStore` from `migration_validator.runs.store`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_api_runs.py`:

```python
"""Testy pro api.create_run a api.update_mapping - seam pro GUI."""

import pytest
import yaml

from migration_validator import api

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"}


def test_create_run_zapise_run_yml(tmp_path):
    manifest = api.create_run(
        "mig02",
        old_device=OLD,
        new_device=NEW,
        mappings=[("ge-0/0/1", "et-0/0/1")],
        run_root=tmp_path,
    )
    raw = yaml.safe_load((tmp_path / "mig02" / "run.yml").read_text())
    assert raw["devices"]["MX1"]["role"] == "old"
    assert raw["devices"]["PTX1"]["role"] == "new"
    assert raw["interface_mapping"] == [
        {"old": {"node": "MX1", "port": "ge-0/0/1"},
         "new": {"node": "PTX1", "port": "et-0/0/1"}}
    ]
    assert raw["captures"] == []
    assert manifest.devices["MX1"].host == "10.0.0.1"


def test_create_run_bez_mappingu_je_validni(tmp_path):
    api.create_run("seq", old_device=OLD, new_device=NEW, run_root=tmp_path)
    raw = yaml.safe_load((tmp_path / "seq" / "run.yml").read_text())
    assert raw["interface_mapping"] == []


def test_create_run_porty_zustavaji_syrove(tmp_path):
    # normalize_port je jen pro nazvy souboru, run.yml nese ge-0/0/2
    api.create_run(
        "raw", old_device=OLD, new_device=NEW,
        mappings=[("ge-0/0/2", "ae0")], run_root=tmp_path,
    )
    raw = yaml.safe_load((tmp_path / "raw" / "run.yml").read_text())
    assert raw["interface_mapping"][0]["old"]["port"] == "ge-0/0/2"


def test_create_run_nevalidni_jmeno(tmp_path):
    with pytest.raises(ValueError, match="jmeno"):
        api.create_run("Mig 02!", old_device=OLD, new_device=NEW, run_root=tmp_path)


def test_create_run_existujici_adresar(tmp_path):
    (tmp_path / "mig02").mkdir()
    with pytest.raises(ValueError, match="existuje"):
        api.create_run("mig02", old_device=OLD, new_device=NEW, run_root=tmp_path)


def test_create_run_duplicitni_old_port(tmp_path):
    with pytest.raises(ValueError, match="sparovan"):
        api.create_run(
            "dup", old_device=OLD, new_device=NEW,
            mappings=[("ge-0/0/1", "et-0/0/1"), ("ge-0/0/1", "et-0/0/2")],
            run_root=tmp_path,
        )


def test_create_run_n_na_1_lag_je_povoleny(tmp_path):
    api.create_run(
        "lag", old_device=OLD, new_device=NEW,
        mappings=[("ge-0/0/1", "ae0"), ("ge-0/0/2", "ae0")],
        run_root=tmp_path,
    )
    raw = yaml.safe_load((tmp_path / "lag" / "run.yml").read_text())
    assert len(raw["interface_mapping"]) == 2


def test_create_run_chybejici_klic_zarizeni(tmp_path):
    with pytest.raises(ValueError, match="host"):
        api.create_run(
            "bad", old_device={"node": "MX1", "platform": "junos"},
            new_device=NEW, run_root=tmp_path,
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `pyats-venv/bin/python -m pytest tests/test_api_runs.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'create_run'`

- [ ] **Step 3: Implement in `api.py`** — add imports and the function:

```python
import re
from pathlib import Path

from migration_validator.runs.manifest import (
    MappingEndpoint,
    RunDevice,
    RunManifest,
)
from migration_validator.runs.store import RunStore

_RUN_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
_DEVICE_KEYS = ("node", "host", "platform")


def _run_device(data: dict[str, str], role: str) -> tuple[str, RunDevice]:
    for key in _DEVICE_KEYS:
        if not data.get(key):
            raise ValueError(f"zarizeni role '{role}': chybi '{key}'")
    return data["node"], RunDevice(
        host=data["host"], platform=data["platform"], role=role
    )


def create_run(
    name: str,
    *,
    old_device: dict[str, str],
    new_device: dict[str, str],
    mappings: list[tuple[str, str]] | None = None,
    run_root: str | Path = Path("runs"),
) -> RunManifest:
    """Zalozi runs/<name>/run.yml - schopnost, kterou CLI nema (run.yml
    se dosud psal rucne). Mapping je volitelny: bez nej vznika
    sekvencni run s volnym capture formularem."""
    if not _RUN_NAME_RE.match(name):
        raise ValueError(
            f"nevalidni jmeno runu '{name}' - povolene znaky: a-z 0-9 _ -"
        )
    store = RunStore(Path(run_root), name)
    if store.dir.exists():
        raise ValueError(f"run '{name}' uz existuje ({store.dir})")

    old_node, old = _run_device(old_device, "old")
    new_node, new = _run_device(new_device, "new")

    manifest = RunManifest(devices={old_node: old, new_node: new})
    for old_port, new_port in mappings or []:
        manifest.add_mapping(
            old=MappingEndpoint(node=old_node, port=old_port.strip()),
            new=MappingEndpoint(node=new_node, port=new_port.strip()),
        )

    store.save(manifest)
    return manifest
```

- [ ] **Step 4: Run tests**

Run: `pyats-venv/bin/python -m pytest tests/test_api_runs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/api.py tests/test_api_runs.py
git commit -m "feat: api.create_run zaklada run adresar s run.yml"
```

---

### Task 6: `api.update_mapping` with the lock rule

**Files:**
- Modify: `migration_validator/api.py` (append)
- Test: `tests/test_api_runs.py` (append)

**Interfaces:**
- Produces: `api.update_mapping(run: str, mappings: list[tuple[str, str]], *, run_root: str | Path = Path("runs")) -> RunManifest`. Replaces the run's whole `interface_mapping` with the desired list. Raises `ValueError` when the run doesn't exist, when a **locked** pairing (one referenced by any capture record on either endpoint) is missing from the desired list, or on duplicate-old conflicts.
- Consumes: `create_run` (tests use it as the fixture factory), `load_manifest`, `CaptureRecord`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api_runs.py`:

```python
from migration_validator.runs.manifest import CaptureRecord, load_manifest


def _run_se_snimkem(tmp_path):
    """Run se dvema pairingy, prvni ma pre snimek -> je zamceny."""
    api.create_run(
        "edit", old_device=OLD, new_device=NEW,
        mappings=[("ge-0/0/1", "et-0/0/1"), ("ge-0/0/2", "et-0/0/2")],
        run_root=tmp_path,
    )
    manifest_path = tmp_path / "edit" / "run.yml"
    manifest = load_manifest(manifest_path)
    manifest.record_capture(CaptureRecord(
        phase="pre", device="MX1", port="ge-0/0/1",
        snapshot="snapshot_pre_MX1_ge_0_0_1.json", taken="2026-09-01T00:00:00Z",
    ))
    from migration_validator.runs.manifest import save_manifest
    save_manifest(manifest, manifest_path)
    return manifest_path


def test_update_mapping_prida_a_odebere_volny_pairing(tmp_path):
    _run_se_snimkem(tmp_path)
    result = api.update_mapping(
        "edit",
        [("ge-0/0/1", "et-0/0/1"), ("ge-0/0/3", "et-0/0/3")],
        run_root=tmp_path,
    )
    ports = [(m.old.port, m.new.port) for m in result.interface_mapping]
    assert ports == [("ge-0/0/1", "et-0/0/1"), ("ge-0/0/3", "et-0/0/3")]
    # zapsano na disk
    on_disk = load_manifest(tmp_path / "edit" / "run.yml")
    assert len(on_disk.interface_mapping) == 2


def test_update_mapping_zamceny_pairing_nelze_odebrat(tmp_path):
    _run_se_snimkem(tmp_path)
    with pytest.raises(ValueError, match="ma snimky"):
        api.update_mapping("edit", [("ge-0/0/2", "et-0/0/2")], run_root=tmp_path)


def test_update_mapping_zamek_plati_i_pro_novy_endpoint(tmp_path):
    path = _run_se_snimkem(tmp_path)
    manifest = load_manifest(path)
    manifest.record_capture(CaptureRecord(
        phase="post", device="PTX1", port="et-0/0/2",
        snapshot="snapshot_post_PTX1_et_0_0_2.json", taken="2026-09-01T00:00:00Z",
    ))
    from migration_validator.runs.manifest import save_manifest
    save_manifest(manifest, path)
    with pytest.raises(ValueError, match="ma snimky"):
        api.update_mapping("edit", [("ge-0/0/1", "et-0/0/1")], run_root=tmp_path)


def test_update_mapping_neexistujici_run(tmp_path):
    with pytest.raises(ValueError, match="neexistuje"):
        api.update_mapping("neni", [], run_root=tmp_path)


def test_update_mapping_zachova_captures(tmp_path):
    _run_se_snimkem(tmp_path)
    result = api.update_mapping(
        "edit",
        [("ge-0/0/1", "et-0/0/1"), ("ge-0/0/2", "et-0/0/2")],
        run_root=tmp_path,
    )
    assert len(result.captures) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pyats-venv/bin/python -m pytest tests/test_api_runs.py -v -k update_mapping`
Expected: FAIL with `AttributeError: ... 'update_mapping'`

- [ ] **Step 3: Implement in `api.py`:**

```python
def _mapping_locked(manifest: RunManifest, mapping) -> bool:
    """Pairing je zamceny, kdyz na nekterem konci existuje snimek."""
    endpoints = {
        (mapping.old.node, mapping.old.port),
        (mapping.new.node, mapping.new.port),
    }
    return any(
        (record.device, record.port) in endpoints for record in manifest.captures
    )


def update_mapping(
    run: str,
    mappings: list[tuple[str, str]],
    *,
    run_root: str | Path = Path("runs"),
) -> RunManifest:
    """Prepise interface_mapping runu na pozadovany seznam.

    Zamek se overuje na serveru, ne v GUI: pairing se snimky na kteremkoliv
    konci nesmi z pozadovaneho seznamu zmizet."""
    store = RunStore(Path(run_root), run)
    if not store.manifest_path.exists():
        raise ValueError(f"run '{run}' neexistuje ({store.manifest_path})")
    manifest = store.load()

    old_role = manifest.device_with_role("old")
    new_role = manifest.device_with_role("new")
    if old_role is None or new_role is None:
        raise ValueError(f"run '{run}' nema zarizeni role old a new")
    old_node, new_node = old_role[0], new_role[0]

    desired = {(o.strip(), n.strip()) for o, n in mappings}
    for mapping in manifest.interface_mapping:
        pair = (mapping.old.port, mapping.new.port)
        if _mapping_locked(manifest, mapping) and pair not in desired:
            raise ValueError(
                f"pairing {mapping.old.node}:{mapping.old.port} -> "
                f"{mapping.new.node}:{mapping.new.port} ma snimky, nelze odebrat"
            )

    rebuilt = RunManifest(devices=manifest.devices, captures=manifest.captures)
    for old_port, new_port in mappings:
        rebuilt.add_mapping(
            old=MappingEndpoint(node=old_node, port=old_port.strip()),
            new=MappingEndpoint(node=new_node, port=new_port.strip()),
        )

    store.save(rebuilt)
    return rebuilt
```

- [ ] **Step 4: Run the full suite**

Run: `pyats-venv/bin/python -m pytest`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/api.py tests/test_api_runs.py
git commit -m "feat: api.update_mapping s server-side zamkem pairingu se snimky"
```
