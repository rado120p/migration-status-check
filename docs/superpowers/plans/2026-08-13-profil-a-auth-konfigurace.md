# Profil a auth konfigurace - implementacni plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dva YAML soubory (sdileny profil + per-user auth) delaji CLI flagy volitelnymi; profil navic umi filtrovat collectory a service typy (ping cile pri capture, check smycku pri evaluate).

**Architecture:** Novy modul `auth.py` nacita per-user credentials (heslo jen pres env promennou nebo 0600 soubor). `config.py` dostava `Profile` obalujici dnesni `CheckConfig`. CLI merge implementuje precedenci flag > soubor > default (flagy prechazi na `default=None`). Capture filtruje jen resolve ping cilu a zapisuje `ping_skipped` marker; evaluate filtruje check smycku, parovani a NESPAROVANO zustavaji nefiltrovane.

**Tech Stack:** Python 3.11+, PyYAML, pytest. Zadne nove zavislosti.

**Spec:** `docs/superpowers/specs/2026-08-13-profil-a-auth-konfigurace-design.md`

## Global Constraints

- Vsechny komentare, docstringy a hlasky cesky bez diakritiky (konvence repa).
- Precedence vsude: CLI flag > hodnota ze souboru > vestavena default.
- Vestavene defaulty (dnesni argparse hodnoty, stehuji se do merge):
  username `ansible`, auth `key`, key_file `~/.ssh/id_rsa`, ssh port `22`,
  timeout `30`, ping_count `5`.
- Plaintext `password:` v auth souboru jen pri 0600 (group/world bity = ToolError, ne warning).
- `password_env` se resolvuje uz pri nacteni souboru; nenastavena promenna = okamzita chyba.
- Profil nesmi ukazovat na auth soubor (bezpecnostni rozhodnuti ze specu).
- Kazdy existujici `checks:`-only YAML se musi nacist beze zmeny.
- Stav se nikdy nefabuluje: odfiltrovany ping = SKIP s duvodem, ne ticho.
- NESPAROVANO a parovani se NIKDY nefiltruji.
- Testy: `python -m pytest <cesta> -v`; pred kazdym commitem cely balik `python -m pytest`.
- Existujicich 935 testu (1 skip) musi projit beze zmeny - dukaz zpetne kompatibility.
- Snapshot `SCHEMA_VERSION` zustava 9: `probes["ping_skipped"]` je aditivni
  klic cteny vsude pres `.get(..., [])`, stare snapshoty se nacitaji dal.
- Commit message konvence: `feat:`/`fix:`/`test:` cesky, jako dosavadni historie.

## Poradi a zavislosti

Task 1 (auth) a Task 2 (profil) jsou nezavisle. Task 3 (CLI merge) zavisi
na 1+2. Task 4 (capture filtr) a Task 6 (engine filtr) zavisi na 2;
Task 5 (SKIP marker v checku) na 4; Task 7 (report) na 6. Task 8 je
zaverecna verifikace + docs.

---

### Task 1: Auth modul

**Files:**
- Create: `migration_validator/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: nic z ostatnich tasku.
- Produces: `AuthSettings` (frozen dataclass, vsechna pole `| None`:
  `username`, `auth_type`, `key_file`, `password`, `ssh_port: int | None`,
  `timeout: int | None`) a
  `load_auth_file(path: Path, *, required: bool) -> AuthSettings`.
  Chyby se hlasi jako `ValueError` (cli.main je uz dnes prevadi na
  EXIT_TOOL_ERROR).

- [ ] **Step 1: Napis failing testy**

```python
# tests/test_auth.py
"""Auth soubor - per-user credentials, heslo jen pres env nebo 0600."""

import pytest

from migration_validator.auth import AuthSettings, load_auth_file


def write(tmp_path, content, mode=0o600):
    path = tmp_path / "auth.yml"
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_chybejici_default_soubor_je_prazdne_nastaveni(tmp_path):
    settings = load_auth_file(tmp_path / "neni.yml", required=False)
    assert settings == AuthSettings()


def test_chybejici_explicitni_soubor_je_chyba(tmp_path):
    with pytest.raises(ValueError, match="auth soubor nenalezen"):
        load_auth_file(tmp_path / "neni.yml", required=True)


def test_plna_sada_poli(tmp_path):
    path = write(
        tmp_path,
        "username: rmohyla\nauth: key\nkey_file: ~/.ssh/lab\n"
        "ssh_port: 2222\ntimeout: 60\n",
    )
    settings = load_auth_file(path, required=True)
    assert settings.username == "rmohyla"
    assert settings.auth_type == "key"
    assert settings.key_file.endswith("/.ssh/lab")
    assert not settings.key_file.startswith("~")
    assert settings.ssh_port == 2222
    assert settings.timeout == 60


def test_password_env_se_resolvuje(tmp_path, monkeypatch):
    monkeypatch.setenv("MIG_TEST_HESLO", "tajne")
    path = write(tmp_path, "auth: password\npassword_env: MIG_TEST_HESLO\n")
    assert load_auth_file(path, required=True).password == "tajne"


def test_nenastavena_promenna_je_chyba_hned(tmp_path, monkeypatch):
    monkeypatch.delenv("MIG_TEST_HESLO", raising=False)
    path = write(tmp_path, "password_env: MIG_TEST_HESLO\n")
    with pytest.raises(ValueError, match="MIG_TEST_HESLO neni nastavena"):
        load_auth_file(path, required=True)


def test_plaintext_heslo_pri_0600_projde(tmp_path):
    path = write(tmp_path, "password: tajne\n", mode=0o600)
    assert load_auth_file(path, required=True).password == "tajne"


def test_plaintext_heslo_pri_sirsich_pravech_je_chyba(tmp_path):
    path = write(tmp_path, "password: tajne\n", mode=0o640)
    with pytest.raises(ValueError, match="chmod 600"):
        load_auth_file(path, required=True)


def test_heslo_a_env_zaroven_je_chyba(tmp_path, monkeypatch):
    monkeypatch.setenv("MIG_TEST_HESLO", "x")
    path = write(tmp_path, "password: a\npassword_env: MIG_TEST_HESLO\n")
    with pytest.raises(ValueError, match="password i password_env"):
        load_auth_file(path, required=True)


def test_neznamy_klic_je_chyba(tmp_path):
    path = write(tmp_path, "usrename: preklep\n")
    with pytest.raises(ValueError, match="neznamy klic"):
        load_auth_file(path, required=True)
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/test_auth.py -v`
Expected: FAIL/ERROR - `ModuleNotFoundError: migration_validator.auth`

- [ ] **Step 3: Implementuj auth.py**

```python
# migration_validator/auth.py
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
```

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/test_auth.py -v`
Expected: 9 PASS

- [ ] **Step 5: Cely balik a commit**

Run: `python -m pytest`
Expected: vse zelene (935 + 9 novych)

```bash
git add migration_validator/auth.py tests/test_auth.py
git commit -m "feat: per-user auth soubor s password_env a 0600 pravidlem"
```

---

### Task 2: Profile v config.py

**Files:**
- Modify: `migration_validator/config.py`
- Test: `tests/test_profile.py`

**Interfaces:**
- Consumes: `CheckConfig`, `DEFAULTS` (existujici v config.py);
  `all_collectors()` z `collectors/registry.py` (pozdni import).
- Produces: `Profile` dataclass (`checks: CheckConfig`,
  `collectors: list[str] | None`, `service_types: list[str] | None`,
  `ping_count: int | None`, `name: str`),
  `load_profile(path: str | Path) -> Profile`,
  `default_profile() -> Profile`. Stavajici `load_config`/`default_config`
  zustavaji nedotcene (pouzivaji je testy i api).

- [ ] **Step 1: Napis failing testy**

```python
# tests/test_profile.py
"""Profil - nadmnozina dnesniho --config YAML."""

import pytest

from migration_validator.config import Profile, default_profile, load_profile


def write(tmp_path, content, name="profil.yml"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_stary_checks_only_yaml_se_nacita_beze_zmeny(tmp_path):
    path = write(tmp_path, "checks:\n  bgp_prefix_counts:\n    tolerance_percent: -5\n")
    profile = load_profile(path)
    assert profile.collectors is None
    assert profile.service_types is None
    assert profile.ping_count is None
    assert profile.checks.options("bgp_prefix_counts")["tolerance_percent"] == -5


def test_plny_profil(tmp_path):
    path = write(
        tmp_path,
        "profile:\n"
        "  collectors: [interfaces, bgp]\n"
        "  service_types: [Internet, IPVPN]\n"
        "  ping_count: 3\n"
        "checks:\n"
        "  interface_optics_levels:\n"
        "    enabled: false\n",
        name="core-only.yml",
    )
    profile = load_profile(path)
    assert profile.collectors == ["interfaces", "bgp"]
    assert profile.service_types == ["Internet", "IPVPN"]
    assert profile.ping_count == 3
    assert profile.name == "core-only.yml"
    assert not profile.checks.enabled("interface_optics_levels")


def test_neznamy_klic_v_profile_je_chyba(tmp_path):
    path = write(tmp_path, "profile:\n  servicetypes: [Internet]\n")
    with pytest.raises(ValueError, match="neznamy klic"):
        load_profile(path)


def test_neznamy_collector_je_chyba_s_vypisem(tmp_path):
    path = write(tmp_path, "profile:\n  collectors: [iface]\n")
    with pytest.raises(ValueError, match="neznamy collector 'iface'"):
        load_profile(path)


def test_neznamy_top_level_klic_je_chyba(tmp_path):
    path = write(tmp_path, "cheks:\n  bgp_prefix_counts: {}\n")
    with pytest.raises(ValueError, match="neznamy klic"):
        load_profile(path)


def test_default_profile_je_prazdny():
    profile = default_profile()
    assert profile == Profile(checks=profile.checks)
    assert profile.checks.enabled("bgp_prefix_counts")
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/test_profile.py -v`
Expected: FAIL - `ImportError: cannot import name 'Profile'`

- [ ] **Step 3: Implementuj Profile v config.py**

Pridej na konec `config.py` (existujici kod se nemeni):

```python
_PROFILE_KEYS = frozenset({"collectors", "service_types", "ping_count"})
_TOP_LEVEL_KEYS = frozenset({"profile", "checks"})


@dataclass
class Profile:
    """Co tenhle testovaci beh dela - sdileny, bez tajemstvi.

    `checks` je dnesni CheckConfig; sekce `profile:` nese vyber
    collectoru, service typu a ping_count. Sekce `tests:` (deklarativni
    checky) je planovane rozsireni - viz poznamka
    docs/superpowers/specs/2026-08-13-deklarativni-filter-testy-poznamka.md.
    """

    checks: CheckConfig = field(default_factory=CheckConfig)
    collectors: list[str] | None = None
    service_types: list[str] | None = None
    ping_count: int | None = None
    name: str = ""


def default_profile() -> Profile:
    return Profile()


def _validate_collectors(names: list[str], path: str | Path) -> None:
    # Pozdni import: config nesmi tahat collectory pri kazdem pouziti
    # CheckConfig (evaluate collectory vubec nepotrebuje).
    import migration_validator.collectors.all  # noqa: F401  (registrace)
    from migration_validator.collectors.registry import all_collectors

    known = sorted(collector.name for collector in all_collectors())
    for name in names:
        if name not in known:
            raise ValueError(
                f"{path}: neznamy collector '{name}' (zname: {', '.join(known)})"
            )


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping, nalezeno {type(raw).__name__}")

    unknown_top = sorted(set(raw) - _TOP_LEVEL_KEYS)
    if unknown_top:
        raise ValueError(
            f"{path}: neznamy klic {', '.join(unknown_top)} "
            f"(zname: {', '.join(sorted(_TOP_LEVEL_KEYS))})"
        )

    section = raw.get("profile") or {}
    unknown = sorted(set(section) - _PROFILE_KEYS)
    if unknown:
        # Preklep v service_types nesmi tise znamenat "vsechno".
        raise ValueError(
            f"{path}: neznamy klic {', '.join(unknown)} v sekci profile "
            f"(zname: {', '.join(sorted(_PROFILE_KEYS))})"
        )

    collectors = section.get("collectors")
    if collectors is not None:
        collectors = [str(name) for name in collectors]
        _validate_collectors(collectors, path)

    service_types = section.get("service_types")
    if service_types is not None:
        service_types = [str(item) for item in service_types]

    ping_count = section.get("ping_count")

    return Profile(
        checks=CheckConfig(raw.get("checks") or {}),
        collectors=collectors,
        service_types=service_types,
        ping_count=int(ping_count) if ping_count is not None else None,
        name=path.name,
    )
```

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/test_profile.py tests/test_auth.py -v`
Expected: vse PASS

- [ ] **Step 5: Cely balik a commit**

Run: `python -m pytest`

```bash
git add migration_validator/config.py tests/test_profile.py
git commit -m "feat: Profile - profil YAML jako nadmnozina checks configu"
```

---

### Task 3: CLI merge - --profile, --auth-file, --service-types

**Files:**
- Modify: `migration_validator/cli.py`
- Test: `tests/test_cli.py` (pridat testy, existujici nechat)

**Interfaces:**
- Consumes: `load_auth_file`, `AuthSettings`, `DEFAULT_AUTH_PATH` (Task 1);
  `load_profile`, `default_profile`, `Profile` (Task 2).
- Produces: helper `_pick(*values)` (prvni ne-None, jinak None);
  `_load_profile_arg(args) -> Profile` (z `args.profile`, jinak
  `default_profile()`); `_auth_settings(args) -> AuthSettings`;
  upraveny `_connection_options(args, auth: AuthSettings)`.
  Task 4 a 6 spolehaji na to, ze CLI preda `service_types` a
  `profile.name` do api vrstvy.

- [ ] **Step 1: Napis failing testy**

Pridej do `tests/test_cli.py`:

```python
def test_evaluate_prijima_profile_i_config_alias():
    parser = build_parser()
    args = parser.parse_args(["evaluate", "--snapshot", "s.json", "--profile", "p.yml"])
    assert args.profile == "p.yml"
    args = parser.parse_args(["evaluate", "--snapshot", "s.json", "--config", "p.yml"])
    assert args.profile == "p.yml"


def test_capture_ma_auth_file_a_service_types():
    parser = build_parser()
    args = parser.parse_args(
        ["capture", "--device", "r1", "--auth-file", "a.yml",
         "--service-types", "Internet,IPVPN"]
    )
    assert args.auth_file == "a.yml"
    assert args.service_types == "Internet,IPVPN"


def test_flag_prebiji_auth_soubor(tmp_path, monkeypatch):
    from migration_validator.auth import AuthSettings
    from migration_validator.cli import _connection_options, build_parser

    auth = AuthSettings(username="rmohyla", ssh_port=2222)
    args = build_parser().parse_args(
        ["capture", "--device", "r1", "--username", "ansible"]
    )
    options = _connection_options(args, auth)
    assert options.username == "ansible"   # flag vyhrava
    assert options.port == 2222            # soubor vyhrava nad defaultem


def test_soubor_prebiji_default(tmp_path):
    from migration_validator.auth import AuthSettings
    from migration_validator.cli import _connection_options, build_parser

    args = build_parser().parse_args(["capture", "--device", "r1"])
    options = _connection_options(args, AuthSettings(username="rmohyla"))
    assert options.username == "rmohyla"


def test_default_kdyz_neni_flag_ani_soubor():
    from migration_validator.auth import AuthSettings
    from migration_validator.cli import _connection_options, build_parser

    args = build_parser().parse_args(["capture", "--device", "r1"])
    options = _connection_options(args, AuthSettings())
    assert options.username == "ansible"
    assert options.port == 22
    assert options.timeout == 30
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/test_cli.py -v`
Expected: nove testy FAIL (`--profile` neexistuje, `_connection_options`
bere jen jeden argument)

- [ ] **Step 3: Implementuj merge v cli.py**

Zmeny v `cli.py`:

1. `_add_auth_arguments` - vsechny defaulty na `None` (jinak by argparse
   default vzdy "vyhral" nad souborem):

```python
def _add_auth_arguments(
    parser: argparse.ArgumentParser, *, port_flag: str = "--port", port_dest: str = "port"
) -> None:
    # default=None vsude: merge flag > soubor > default se deje az
    # v _connection_options, argparse default by soubor tise prebil.
    parser.add_argument("--username", default=None)
    parser.add_argument("--auth", choices=("key", "password"), default=None)
    parser.add_argument("--key-file", default=None)
    parser.add_argument("--password")
    parser.add_argument(port_flag, dest=port_dest, type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument(
        "--auth-file",
        help="cesta k auth YAML (default ~/.config/mig-validate/auth.yml)",
    )
```

2. Nove helpery + prepsany `_connection_options`:

```python
def _pick(*values):
    """Prvni hodnota, ktera neni None - precedence flag > soubor > default."""
    for value in values:
        if value is not None:
            return value
    return None


def _auth_settings(args: argparse.Namespace) -> AuthSettings:
    if args.auth_file:
        return load_auth_file(Path(args.auth_file), required=True)
    return load_auth_file(DEFAULT_AUTH_PATH, required=False)


def _connection_options(
    args: argparse.Namespace, auth: AuthSettings
) -> ConnectionOptions:
    # capture ma --ssh-port (dest "ssh_port"), protoze --port u nej znamena
    # cislo/jmeno sitoveho portu v run rezimu; record pouziva puvodni --port.
    flag_port = getattr(args, "ssh_port", None)
    if flag_port is None:
        flag_port = getattr(args, "port", None)
    return ConnectionOptions(
        host=args.device,
        username=_pick(args.username, auth.username, "ansible"),
        auth_type=_pick(args.auth, auth.auth_type, "key"),
        key_file=_pick(
            args.key_file, auth.key_file, str(Path.home() / ".ssh" / "id_rsa")
        ),
        password=_pick(args.password, auth.password),
        port=_pick(flag_port, auth.ssh_port, 22),
        timeout=_pick(args.timeout, auth.timeout, 30),
    )
```

3. Importy nahore: `from migration_validator.auth import AuthSettings, DEFAULT_AUTH_PATH, load_auth_file`
   a `from migration_validator.config import default_config, default_profile, load_config, load_profile`.

4. Flagy: u `evaluate` nahrad `evaluate.add_argument("--config")` za
   `evaluate.add_argument("--profile", "--config", dest="profile", help="profil YAML (--config je alias)")`.
   U `capture` pridej stejny `--profile` flag a
   `capture.add_argument("--service-types", help="carkou oddeleny seznam typu sluzeb")`;
   u `evaluate` taky `--service-types`.

5. Volajici mista - vsude kde bylo `_connection_options(args)` je ted
   `_connection_options(args, _auth_settings(args))` (`_cmd_capture`,
   `_capture_into_run`, `_parse_services_into`, `_cmd_record`).
   `record` dostava `--auth-file` automaticky pres `_add_auth_arguments`.

6. `_cmd_evaluate` a `_evaluate_run`: misto
   `config = load_config(args.config) if args.config else default_config()`
   pouzij
   `profile = load_profile(args.profile) if args.profile else default_profile()`
   a dal predavej `config=profile.checks`. (`service_types` a `profile.name`
   se do api zapoji v Tasku 6 - tady jen priprav promennou
   `service_types = _parse_service_types(args, profile)`.)

```python
def _parse_service_types(
    args: argparse.Namespace, profile: Profile
) -> list[str] | None:
    if getattr(args, "service_types", None):
        return [s.strip() for s in args.service_types.split(",") if s.strip()]
    return profile.service_types
```

7. `_cmd_capture` a `_capture_into_run`: collectors a ping_count pres merge:

```python
    collectors = (
        args.collectors.split(",") if args.collectors else profile.collectors
    )
    ping_count = _pick(args.ping_count, profile.ping_count, 5)
```

   a `capture.add_argument("--ping-count", type=int, default=None)` (default
   se stehuje do merge). `service_types=_parse_service_types(args, profile)`
   se do `api.capture` preda az v Tasku 4 - tady zatim jen lokalni promenna
   (nepouzita promenna neni chyba testu, ale klidne ji zaved az v Tasku 4).

- [ ] **Step 4: Over testy**

Run: `python -m pytest tests/test_cli.py tests/test_auth.py -v`
Expected: vse PASS

- [ ] **Step 5: Cely balik a commit**

Run: `python -m pytest`
Expected: vse zelene - zadny existujici test se nesmel rozbit
(zvlast tests/test_cli.py a tests/test_end_to_end.py)

```bash
git add migration_validator/cli.py tests/test_cli.py
git commit -m "feat: --profile/--auth-file/--service-types a merge flag > soubor > default"
```

---

### Task 4: Capture - service_types filtr ping cilu + ping_skipped marker

**Files:**
- Modify: `migration_validator/capture.py`, `migration_validator/api.py`,
  `migration_validator/cli.py` (predani parametru)
- Test: `tests/test_capture.py` (pridat testy)

**Interfaces:**
- Consumes: `Profile.service_types` (Task 2), CLI promenna z Tasku 3.
- Produces: `capture_device(..., service_types: list[str] | None = None)`
  a `api.capture(..., service_types: list[str] | None = None)`.
  Snapshot dostava `probes["ping_skipped"]: list[dict]` s prvky
  `{"scope_id": str, "reason": "mimo profil"}` - Task 5 z nich cte.

- [ ] **Step 1: Napis failing test**

Pridej do `tests/test_capture.py` (pouzij existujici stub device
z toho souboru - podivej se na okolni testy a zrcadli jejich setup):

```python
def test_service_types_filtruje_ping_a_zapisuje_marker(...):
    # capture_device(..., service_types=["IPVPN"]) na inventory se
    # sluzbami Internet i IPVPN:
    snapshot = capture_device(device, address="10.0.0.1",
                              inventory=inventory, service_types=["IPVPN"])
    pinged_scopes = {p["scope_id"] for p in snapshot.probes["ping"]}
    skipped = {p["scope_id"] for p in snapshot.probes["ping_skipped"]}
    # zadny Internet scope nema ping, kazdy ma marker
    assert all(sid in skipped for sid in internet_scope_ids)
    assert not (pinged_scopes & set(internet_scope_ids))
    # vsechny scopy jsou porad ve snapshotu (inventory se nefiltruje)
    assert {s.id for s in snapshot.scopes} == all_scope_ids


def test_bez_filtru_zadny_marker(...):
    snapshot = capture_device(device, address="10.0.0.1", inventory=inventory)
    assert snapshot.probes.get("ping_skipped", []) == []
```

Konkretni tvar (fixtures, stub device) prizpusob existujicim testum
v `tests/test_capture.py` - je tam hotovy vzor pro capture_device
s falesnym devicem; assert cast zustava jak je vyse.

- [ ] **Step 2: Over, ze test pada**

Run: `python -m pytest tests/test_capture.py -v`
Expected: FAIL - `capture_device() got an unexpected keyword argument 'service_types'`

- [ ] **Step 3: Implementace**

V `capture.py` pridej parametr a filtr (scopy se stavi cele, filtruje
se JEN resolve ping cilu - rozhodnuti ze specu):

```python
def capture_device(
    device: Any,
    address: str,
    *,
    inventory: Inventory | None = None,
    collector_names: list[str] | None = None,
    phase: str | None = None,
    ping_count: int = DEFAULT_COUNT,
    now: str | None = None,
    record_raw: str | Path | None = None,
    baseline: Snapshot | None = None,
    service_types: list[str] | None = None,
) -> Snapshot:
```

a misto dnesniho `for target in resolve_targets(scopes, ...)`:

```python
    pings: list[dict[str, Any]] = []
    ping_skipped: list[dict[str, Any]] = []
    if scopes:
        if service_types is None:
            ping_scopes = scopes
        else:
            allowed = set(service_types)
            ping_scopes = [s for s in scopes if s.service_type in allowed]
            # Marker misto ticha: evaluate z nej udela SKIP s duvodem,
            # jinak by odfiltrovany ping vypadal jako "bez cile".
            ping_skipped = [
                {"scope_id": s.id, "reason": "mimo profil"}
                for s in scopes
                if s.kind == "service" and s.service_type not in allowed
            ]
        baseline_arp = baseline.facts.get("arp", []) if baseline is not None else None
        baseline_nd = baseline.facts.get("nd", []) if baseline is not None else None
        for target in resolve_targets(
            ping_scopes,
            facts.get("arp", []),
            facts.get("nd", []),
            baseline_arp=baseline_arp,
            baseline_nd=baseline_nd,
        ):
            pings.append(run_ping(device, target, count=ping_count))
```

a v `Snapshot(...)`: `probes={"ping": pings, "ping_skipped": ping_skipped},`

V `api.py` pridej `service_types: list[str] | None = None` do `capture()`
a preposli do `capture_device`. V `cli.py` (`_cmd_capture`,
`_capture_into_run`) preda `service_types=_parse_service_types(args, profile)`
do `api.capture`.

- [ ] **Step 4: Over testy**

Run: `python -m pytest tests/test_capture.py -v`
Expected: PASS

- [ ] **Step 5: Cely balik a commit**

Run: `python -m pytest`

```bash
git add migration_validator/capture.py migration_validator/api.py migration_validator/cli.py tests/test_capture.py
git commit -m "feat: service_types filtr ping cilu pri capture + ping_skipped marker"
```

---

### Task 5: SKIP "mimo profil" v ping checku

**Files:**
- Modify: `migration_validator/models/scope.py` (`Scope.select`),
  `migration_validator/checks/reachability.py` (`PingReachabilityCheck.run`)
- Test: `tests/checks/` (soubor s ping testy - najdi ho pres
  `grep -rl ping_reachability tests/`)

**Interfaces:**
- Consumes: `probes["ping_skipped"]` (Task 4).
- Produces: `Scope.select` navic vraci klic `"ping_skipped": bool`
  (True kdyz je scope_id v probes["ping_skipped"]); check z nej dela
  `Finding(Outcome.SKIP, "ping neproveden - mimo profil", ...)`.

- [ ] **Step 1: Napis failing test**

Do souboru s testy ping checku (vzor okolnich testu tamtez - staveji
CheckContext; zrcadli jejich setup):

```python
def test_ping_mimo_profil_je_skip_s_duvodem(...):
    # ctx.subject bez ping probu, ale s ping_skipped=True
    findings = PingReachabilityCheck().run(ctx)
    assert len(findings) == 1
    assert findings[0].outcome is Outcome.SKIP
    assert "mimo profil" in findings[0].message
```

A do testu `Scope.select` (tests/models/ nebo kde se select testuje -
`grep -rn "def select\|\.select(" tests/ | head`):

```python
def test_select_prenasi_ping_skipped_marker():
    scope = ...  # service scope s id "svc:X:Internet"
    subject = scope.select(
        {}, {"ping": [], "ping_skipped": [{"scope_id": scope.id, "reason": "mimo profil"}]}
    )
    assert subject["ping_skipped"] is True


def test_select_bez_markeru_je_false():
    subject = scope.select({}, {"ping": []})
    assert subject["ping_skipped"] is False
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/ -k "mimo_profil or ping_skipped" -v`
Expected: FAIL (klic neexistuje)

- [ ] **Step 3: Implementace**

V `Scope.select` (models/scope.py) - v OBOU vetvich (device i service)
pridej pred `return`:

```python
        selected["ping_skipped"] = any(
            entry.get("scope_id") == self.id
            for entry in probes.get("ping_skipped", [])
        )
```

(V device vetvi vyjde vzdy False - device scope zadne sluzebni pingy
nema; klic tam ale byt musi, aby check necetl neexistujici klic.)

V `PingReachabilityCheck.run` (checks/reachability.py) uprav vetev
prazdnych probu:

```python
        probes: list[dict[str, Any]] = ctx.subject.get("ping", [])
        if not probes:
            if ctx.subject.get("ping_skipped"):
                # Odfiltrovano profilem pri capture - vedome nesbirano,
                # ne chybejici cil. Stav se nefabuluje: rekneme proc.
                return [
                    Finding(
                        Outcome.SKIP,
                        "ping neproveden - mimo profil",
                        label="Ping",
                        value="mimo profil",
                    )
                ]
            return [
                Finding(
                    Outcome.SKIP,
                    "pro tento scope nejsou ve snapshotu zadne cile pingu",
                    label="Ping",
                    value="bez cile",
                )
            ]
```

- [ ] **Step 4: Over testy**

Run: `python -m pytest tests/ -k "mimo_profil or ping_skipped" -v`
Expected: PASS

- [ ] **Step 5: Cely balik a commit**

Run: `python -m pytest`

```bash
git add migration_validator/models/scope.py migration_validator/checks/reachability.py tests/
git commit -m "feat: ping mimo profil je SKIP s duvodem, ne 'bez cile'"
```

---

### Task 6: Engine - service_types filtr check smycky + filtered marker

**Files:**
- Modify: `migration_validator/engine.py`, `migration_validator/api.py`,
  `migration_validator/cli.py`, `migration_validator/reporting/text_report.py`
  (jen `filter_result` - merge `filtered` klicu)
- Test: `tests/test_engine.py` (pridat testy)

**Interfaces:**
- Consumes: `_parse_service_types` z Tasku 3, `Profile.name` z Tasku 2.
- Produces: `evaluate_snapshots(..., service_types: list[str] | None = None,
  profile_name: str | None = None)` a stejne parametry na `api.evaluate`.
  `RunResult.filtered` dostava klice `"service_types"`, `"profile"`,
  `"scopes_shown"`, `"scopes_total"`. Task 7 je renderuje.

- [ ] **Step 1: Napis failing testy**

Pridej do `tests/test_engine.py` (pouzij existujici zpusob stavby
snapshotu z toho souboru - `synthetic_snapshot` fixture z conftest.py
nebo lokalni helper, podle vzoru okolnich testu):

```python
def test_service_types_filtruje_check_smycku(...):
    # snapshot se sluzbami Internet i IPVPN
    result = evaluate_snapshots(subject, service_types=["IPVPN"],
                                profile_name="core-only.yml")
    typy = {s.key.get("service_type") for s in result.scopes
            if s.key and s.key.get("service_type")}
    assert "Internet" not in typy
    assert result.filtered["service_types"] == ["IPVPN"]
    assert result.filtered["profile"] == "core-only.yml"
    assert result.filtered["scopes_total"] > result.filtered["scopes_shown"]


def test_device_a_layer1_scopy_filtru_nepodlehaji(...):
    result = evaluate_snapshots(subject, service_types=["IPVPN"])
    kinds = {s.scope_id for s in result.scopes}
    # device scope a l1: bloky zustavaji - filtr je na service typy,
    # ne na infrastrukturu
    assert any(sid.startswith("l1:") for sid in kinds) or "device" in str(kinds)


def test_nesparovane_se_nefiltruji(...):
    # baseline ma Internet sluzbu, ktera v subject chybi;
    # filtr na IPVPN ji NESMI schovat
    result = evaluate_snapshots(subject, baseline=baseline,
                                service_types=["IPVPN"])
    assert any("Internet" in str(item) for item in result.unmatched["baseline"])


def test_bez_filtru_zadny_marker(...):
    result = evaluate_snapshots(subject)
    assert result.filtered is None
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL - `unexpected keyword argument 'service_types'`

- [ ] **Step 3: Implementace**

V `engine.py` `evaluate_snapshots`:

```python
def evaluate_snapshots(
    subject: Snapshot,
    baseline: Snapshot | None = None,
    mapping: Mapping | None = None,
    config: CheckConfig | None = None,
    now: str | None = None,
    service_types: list[str] | None = None,
    profile_name: str | None = None,
) -> RunResult:
```

Filtr aplikuj na SERVICE scopy tesne pred check smyckou - parovani
bezi na plnych mnozinach (NESPAROVANO se nesmi filtrovat):

```python
    def _in_profile(scope: Scope) -> bool:
        # Filtr je jen na service typy: device a layer1 scopy jsou
        # infrastruktura, ne sluzba, a v reportu zustavaji vzdy.
        if service_types is None or scope.kind != "service":
            return True
        return scope.service_type in set(service_types)
```

- Ve vetvi `baseline is None`: `for scope in subject_scopes:` -> preskoc
  scopy, kde `not _in_profile(scope)` (pocitej `skipped_total`).
- Ve vetvi s baseline: parovani (`match_scopes`) bezi na PLNYCH
  seznamech; filtr aplikuj az na `matches.pairs` (preskoc pary, kde
  `not _in_profile(pair.subject)`) a na smycku nesparovanych subject
  scopu, POKUD tam engine dela _run_scope - `unmatched` seznamy samotne
  zustavaji nedotcene.
- Pred `return` vyrob marker (jen kdyz filtr aktivni):

```python
    filtered = None
    if service_types is not None:
        filtered = {
            "service_types": list(service_types),
            "scopes_shown": len(scope_results),
            "scopes_total": len(scope_results) + skipped_total,
        }
        if profile_name:
            filtered["profile"] = profile_name
```

a preda `filtered=filtered` do `RunResult(...)`.

V `api.evaluate` pridej oba parametry a preposli je.

V `cli.py` `_cmd_evaluate` a `_evaluate_run`: preda
`service_types=service_types` a
`profile_name=profile.name or None` do `api.evaluate`.

V `reporting/text_report.py` `filter_result`: `applied` dict stav na
zaklade existujiciho markeru, aby CLI filtr neprepsal engine filtr:

```python
    applied = dict(result.filtered or {})
    applied.update({... dosavadni klice text/statuses/scopes_shown/scopes_total ...})
```

Pozor: `scopes_shown`/`scopes_total` od CLI filtru prepisuji ty od
engine - to je spravne (CLI filtr je uzsi vyber z uz profiltrovaneho),
ale `service_types`/`profile` klice zustavaji.

- [ ] **Step 4: Over testy**

Run: `python -m pytest tests/test_engine.py tests/reporting/ -v`
Expected: PASS

- [ ] **Step 5: Cely balik a commit**

Run: `python -m pytest`

```bash
git add migration_validator/engine.py migration_validator/api.py migration_validator/cli.py migration_validator/reporting/text_report.py tests/test_engine.py
git commit -m "feat: service_types filtr v evaluate, NESPAROVANO zustava nefiltrovane"
```

---

### Task 7: Report - filtr hlavicka s profilem a service typy

**Files:**
- Modify: `migration_validator/reporting/text_report.py` (`_filter_note`)
- Test: `tests/reporting/` (soubor kde se testuje `_filter_note` /
  `render` - najdi pres `grep -rln "filter_note\|filtr:" tests/`)

**Interfaces:**
- Consumes: `RunResult.filtered` s klici z Tasku 6.
- Produces: radek hlavicky obsahuje `profil=<jmeno>` a
  `typy=<seznam>`, kdyz jsou v markeru.

- [ ] **Step 1: Napis failing test**

```python
def test_filtr_hlavicka_nese_profil_a_typy(...):
    result = ...  # RunResult s filtered={"service_types": ["IPVPN"],
                  #   "profile": "core-only.yml",
                  #   "scopes_shown": 2, "scopes_total": 5}
    lines = render(result).splitlines()
    note = next(line for line in lines if "filtr:" in line)
    assert "profil=core-only.yml" in note
    assert "typy=IPVPN" in note
    assert "2 z 5 sluzeb" in note
```

- [ ] **Step 2: Over, ze test pada**

Run: `python -m pytest tests/reporting/ -v`
Expected: FAIL

- [ ] **Step 3: Implementace**

V `_filter_note` pridej k dosavadnim kriteriim:

```python
    criteria = []
    if applied.get("profile"):
        criteria.append(f"profil={applied['profile']}")
    if applied.get("service_types"):
        criteria.append(f"typy={','.join(applied['service_types'])}")
    if applied.get("text"):
        criteria.append(f"text={applied['text']}")
    if applied.get("statuses"):
        criteria.append(f"status={','.join(applied['statuses'])}")
```

Zbytek funkce beze zmeny - druhy radek poznamky uz dnes rika, ze se
NESPAROVANO neprepocitava.

- [ ] **Step 4: Over testy**

Run: `python -m pytest tests/reporting/ -v`
Expected: PASS

- [ ] **Step 5: Cely balik a commit**

Run: `python -m pytest`

```bash
git add migration_validator/reporting/text_report.py tests/reporting/
git commit -m "feat: filtr hlavicka reportu nese profil a service typy"
```

---

### Task 8: Zaverecna verifikace + dokumentace

**Files:**
- Modify: `README.md` (sekce o profilu a auth souboru)

**Interfaces:**
- Consumes: vse predchozi.
- Produces: hotova vetev.

- [ ] **Step 1: Cely balik testu**

Run: `python -m pytest`
Expected: vse zelene (935 puvodnich + nove), 1 puvodni skip

- [ ] **Step 2: Rucni smoke test CLI**

```bash
python -m migration_validator.cli checks
python -m migration_validator.cli evaluate --snapshot neexistuje.json --profile /dev/null; echo "exit: $?"
# ocekavano: exit 2 a srozumitelna chyba (mapping /dev/null = prazdny profil? ne -
# /dev/null neni YAML mapping -> "ocekavan YAML mapping" - obe hlasky OK)
```

A precedence end-to-end (bez zarizeni, jen parsovani):

```bash
mkdir -p /tmp/mig-smoke && printf 'username: rmohyla\n' > /tmp/mig-smoke/auth.yml
python - <<'EOF'
from migration_validator.auth import load_auth_file
from migration_validator.cli import _connection_options, build_parser
from pathlib import Path
auth = load_auth_file(Path("/tmp/mig-smoke/auth.yml"), required=True)
args = build_parser().parse_args(["capture", "--device", "r1"])
print(_connection_options(args, auth).username)  # ocekavano: rmohyla
EOF
```

- [ ] **Step 3: README sekce**

Do `README.md` pridej sekci (cesky, po vzoru existujicich sekci):

```markdown
## Profil a auth soubor

Profil (sdileny, klidne v gitu) rika, CO beh testuje:

    profile:
      collectors: [interfaces, bgp, evpn_instance]
      service_types: [Internet, IPVPN]
      ping_count: 3
    checks:
      interface_optics_levels:
        enabled: false

    mig-validate evaluate --run mig01 --profile profiles/core-only.yml

Auth soubor (per-user, default ~/.config/mig-validate/auth.yml) rika,
KDO se pripojuje - heslo pres env promennou, plaintext jen pri 0600:

    username: rmohyla
    auth: password
    password_env: MIG_PROD_PASSWORD

Sdileny ansible ucet: --auth-file /cesta/k/ansible-auth.yml.
Precedence vsude: CLI flag > soubor > vestavena default.
Heslo do env bez ~/.bash_history: `read -s MIG_PROD_PASSWORD && export MIG_PROD_PASSWORD`.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README sekce o profilu a auth souboru"
```

- [ ] **Step 5: Uklid a predani**

Zkontroluj `git status` (cisty strom), `git log --oneline -8` (vsechny
tasky commitnute). Vetev je hotova - nasleduje review podle
superpowers:requesting-code-review / finishing-a-development-branch.
