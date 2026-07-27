# Migration Validator — Plán 2: sběrná cesta na zařízení

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Doplnit `capture` — připojení přes PyEZ, sběr operačního stavu z Junos i Junos EVO, aktivní ping probe a zápis snapshotu, který spotřebovává Plán 1.

**Architecture:** Collectory překládají RPC XML na strukturovaná data a **nikdy neinterpretují**. Platformní rozdíly MX vs EVO se řeší uvnitř collectoru, navenek je schéma jednotné. Ping je jediný aktivní test a běží až po bulk sběru, protože cíle se odvozují z ARP.

**Tech Stack:** Python 3.13, `junos-eznc` (PyEZ), `lxml`, pytest.

**Spec:** `docs/superpowers/specs/2026-07-24-migration-validator-design.md`
**Předchozí plán:** `docs/superpowers/plans/2026-07-24-migration-validator-evaluate.md` — **musí být hotový**, tenhle plán na něm staví.

## Global Constraints

Platí všechna omezení z Plánu 1, plus:

- **Collectory nikdy neinterpretují.** Vrací syrová strukturovaná data, nikdy `{"bgp_ok": true}`. Verdikt patří do checku.
- **Platformní rozdíly řeší collector**, ne check. Navenek stejné schéma pro `junos` i `junos-evo`.
- **Selhání jednoho collectoru nezruší capture.** Zapíše se do `capture.collectors[name] = {"status": "error", "message": ...}` a pokračuje se.
- **Selhání připojení capture ukončí** — snapshot nevznikne, exit kód 2, chybová hláška rozliší auth / timeout / refused.
- **Ping běží jen v service režimu.** Bez inventory má snapshot `probes.ping: []`.
- **Každý collector musí mít fixture pro obě platformy** v `tests/fixtures/rpc/{junos,junos-evo}/`.
- **XPath se nepíše naslepo.** Každý parser se ověřuje proti nahranému XML z laborky (`mig-validate record`).
- Autentizace přebírá konvenci z existujících parserů: `--auth key|password`, `--username`, `--key-file`, default `ansible` + `~/.ssh/id_rsa`.
- **Collectory MUSÍ dodržet kontrakt fact-schématu** ze specu (sekce *Kontrakt fact-schématu*).
  Klíč mimo kontrakt způsobí, že check tiše vrátí SKIP — offline testy Plánu 1 to nechytnou,
  protože jejich fixtures jsou konzistentní z konstrukce. Zvlášť pozor: `evpn_vpws` a `evpn_mac`
  se klíčují **názvem routing-instance**, ne rozhraním; `evpn_esi.interface` musí být název, který
  scope matchne (logická jednotka, nebo doplnit `routing_instance` do schématu). K tomu patří
  **conformance test**: pro každou oblast test „collector emituje X → check konzumuje X" nad
  nahraným XML, aby byly obě poloviny švu (AR-6) připnuté proti sobě, ne každá proti své vlastní
  představě. Zvaž sdílený `SCHEMA` modul, který importuje collector i conformance test.

## Laboratorní prostředí

Containerlab topologie `pop-migration` běží lokálně. Ověřeno 2026-07-24:

| adresa | container | model | verze | platforma |
|---|---|---|---|---|
| `172.20.20.4` | `clab-pop-migration-MX1-POP1` | VMX | `24.2R1-S2.5` | `junos` |
| `172.20.20.5` | `clab-pop-migration-PTX1-POP1` | PTX10002-36QDD | `25.2R1.8-EVO` | `junos-evo` |

**Detekce platformy podle `detect_platform()` na těchto verzích funguje** — EVO má `EVO` v řetězci
verze, MX ne. Fallback na model prefix se neuplatní.

Přístup je přes heslo, ne SSH klíč. Heslo **není v repozitáři** — nastav si ho do prostředí:

```bash
export MIG_LAB_PASSWORD='...'      # laboratorni credentials, uzivatel admin
```

Všechny ověřovací kroky v tomto plánu pak používají:

```bash
--auth password --username admin --password "$MIG_LAB_PASSWORD"
```

`clab-pop-migration-PTX1-POP1` se neresolvuje jménem — používej IP adresu.

Ověřovací Python snippety v tomto plánu píšou pro stručnost `ConnectionOptions(host=address)`,
což je výchozí autentizace klíčem. **V této laborce místo toho použij:**

```python
import os
from migration_validator.connection.junos import ConnectionOptions

def lab(address: str) -> ConnectionOptions:
    return ConnectionOptions(
        host=address,
        username="admin",
        auth_type="password",
        password=os.environ["MIG_LAB_PASSWORD"],
    )
```

### Postup nahrávání fixtures: dvoufázově

Fixtures nemají být jen „nějaké XML" — mají odpovídat **skutečnému stavu před a po migraci**,
jinak testy nikdy neuvidí realistický rozdíl (spárované služby, provoz, který se přesunul,
ARP a MAC, které zmizely na jedné straně a objevily se na druhé).

Postup:

1. **Fáze „pre"** — služby aktivní na MX, na EVO vypnuté. Nahraj fixtures z `172.20.20.4`
   do `tests/fixtures/rpc/junos/`.
2. **Migrace** — služby na MX vypni (`deactivate`), na EVO zapni.
3. **Fáze „post"** — nechej chvíli běžet provoz, ať se naučí ARP a MAC. Nahraj fixtures
   z `172.20.20.5` do `tests/fixtures/rpc/junos-evo/`.

Tím dostaneš dvojici fixtures, ze které jde v Tasku 7 postavit **realistický end-to-end test**
celé migrace, ne jen kontrola schématu.

Pokud fáze 2 není v daném okamžiku možná (laborka se používá k něčemu jinému), nahraj obě strany
v aktuálním stavu — testy collectorů kontrolují schéma, ne konkrétní hodnoty, takže projdou.
Realistickou dvojici pak doplň později.

## Struktura souborů

| soubor | zodpovědnost |
|---|---|
| `migration_validator/connection/__init__.py` | prázdný |
| `migration_validator/connection/junos.py` | `ConnectionOptions`, `connect()`, `detect_platform()`, mapování chyb |
| `migration_validator/collectors/__init__.py` | prázdný |
| `migration_validator/collectors/base.py` | `Collector` ABC, `CollectorError` |
| `migration_validator/collectors/registry.py` | registrace, výběr podle platformy |
| `migration_validator/collectors/interfaces.py` | `get-interface-information` → oper state, pps, errors |
| `migration_validator/collectors/arp.py` | `get-arp-table-information` |
| `migration_validator/collectors/bgp.py` | `get-bgp-summary-information` |
| `migration_validator/collectors/evpn.py` | vpws-sid-pe-status, esi-status, MAC counts (platformní varianty) |
| `migration_validator/collectors/all.py` | import všech collectorů kvůli registraci |
| `migration_validator/probes/__init__.py` | prázdný |
| `migration_validator/probes/ping.py` | `resolve_targets()`, `run_ping()` |
| `migration_validator/capture.py` | orchestrace fází capture |
| `migration_validator/api.py` | doplnit `capture()` |
| `migration_validator/cli.py` | doplnit `capture` a `record` |

---

### Task 1: Připojení k zařízení a detekce platformy

**Files:**
- Create: `migration_validator/connection/__init__.py`
- Create: `migration_validator/connection/junos.py`
- Test: `tests/connection/test_junos.py`

**Interfaces:**
- Consumes: nic z předchozích tasků
- Produces:
  - `ConnectionOptions(host, username="ansible", auth_type="key", key_file=<~/.ssh/id_rsa>, password=None, port=22, timeout=30)`
  - `ConnectionOptions.device_kwargs() -> dict` — argumenty pro `jnpr.junos.Device`
  - `JunosConnectionError(Exception)`
  - `connect(options) -> ContextManager[Device]`
  - `detect_platform(device) -> str` — `"junos"` nebo `"junos-evo"`
  - `device_meta(device) -> DeviceMeta`

**Poznámka:** `detect_platform` se opírá o `dev.facts["version"]` — Junos EVO má v řetězci verze `EVO` (např. `22.4R3-S2-EVO`). Fallback na `dev.facts["model"]` pro případ, že verze chybí.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/connection/__init__.py` (prázdný) a `tests/connection/test_junos.py`:

```python
from pathlib import Path

import pytest

from migration_validator.connection.junos import (
    ConnectionOptions,
    detect_platform,
    device_meta,
)


class FakeDevice:
    def __init__(self, facts):
        self.facts = facts


@pytest.mark.parametrize(
    "facts,expected",
    [
        ({"version": "21.4R3-S4"}, "junos"),
        ({"version": "22.4R3-S2-EVO"}, "junos-evo"),
        ({"version": "23.2R1-EVO"}, "junos-evo"),
        ({"version": None, "model": "PTX10001-36MR"}, "junos-evo"),
        ({"version": None, "model": "MX204"}, "junos"),
        ({}, "junos"),
    ],
)
def test_detect_platform(facts, expected):
    assert detect_platform(FakeDevice(facts)) == expected


def test_device_meta_reads_facts():
    device = FakeDevice(
        {
            "hostname": "MX1-POP1",
            "model": "MX204",
            "version": "21.4R3-S4",
            "RE0": {"up_time": "95 days, 3 hours"},
        }
    )
    meta = device_meta(device, address="172.20.20.4")

    assert meta.address == "172.20.20.4"
    assert meta.hostname == "MX1-POP1"
    assert meta.model == "MX204"
    assert meta.platform == "junos"


def test_key_auth_kwargs():
    options = ConnectionOptions(host="172.20.20.4", key_file="/home/u/.ssh/id_rsa")
    kwargs = options.device_kwargs()

    assert kwargs["host"] == "172.20.20.4"
    assert kwargs["user"] == "ansible"
    assert kwargs["ssh_private_key_file"] == "/home/u/.ssh/id_rsa"
    assert "passwd" not in kwargs


def test_password_auth_kwargs():
    options = ConnectionOptions(
        host="172.20.20.4", auth_type="password", username="admin", password="secret"
    )
    kwargs = options.device_kwargs()

    assert kwargs["passwd"] == "secret"
    assert "ssh_private_key_file" not in kwargs


def test_password_auth_requires_password():
    with pytest.raises(ValueError, match="heslo"):
        ConnectionOptions(host="1.2.3.4", auth_type="password").device_kwargs()


def test_unknown_auth_type_is_rejected():
    with pytest.raises(ValueError, match="auth"):
        ConnectionOptions(host="1.2.3.4", auth_type="magic").device_kwargs()


def test_default_key_file_points_to_ssh_dir():
    options = ConnectionOptions(host="1.2.3.4")
    assert options.key_file.endswith(str(Path(".ssh") / "id_rsa"))
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/connection/test_junos.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.connection'`

- [ ] **Step 3: Implementuj připojení**

Vytvoř `migration_validator/connection/__init__.py` (prázdný) a `migration_validator/connection/junos.py`:

```python
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
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/connection/test_junos.py -v`
Expected: PASS, 12 testů

- [ ] **Step 5: Ověř proti laborce**

```bash
.venv/bin/python -c "
import os
from migration_validator.connection.junos import ConnectionOptions, connect, device_meta

for address in ('172.20.20.4', '172.20.20.5'):
    options = ConnectionOptions(
        host=address, username='admin', auth_type='password',
        password=os.environ['MIG_LAB_PASSWORD'],
    )
    with connect(options) as dev:
        print(device_meta(dev, address))
"
```

Expected (ověřeno 2026-07-24 proti běžící laborce):

```
DeviceMeta(address='172.20.20.4', hostname='clab-pop-migration-MX1-POP1',
           platform='junos', model='VMX', version='24.2R1-S2.5', ...)
DeviceMeta(address='172.20.20.5', hostname='clab-pop-migration-PTX1-POP1',
           platform='junos-evo', model='PTX10002-36QDD', version='25.2R1.8-EVO', ...)
```

- [ ] **Step 6: Commit**

```bash
git add migration_validator/connection tests/connection
git commit -m "feat: add PyEZ connection wrapper with platform detection"
```

---

### Task 2: Základ collectorů a nahrávání syrového XML

**Files:**
- Create: `migration_validator/collectors/__init__.py`
- Create: `migration_validator/collectors/base.py`
- Create: `migration_validator/collectors/registry.py`
- Modify: `migration_validator/cli.py` — přidat podpříkaz `record`
- Test: `tests/collectors/test_base.py`

**Interfaces:**
- Consumes: `connect`, `ConnectionOptions`, `detect_platform` (Task 1)
- Produces:
  - `CollectorError(Exception)`
  - `Collector` ABC: `name: str`, `platforms: tuple[str, ...]`, `rpc_name(platform) -> str`, `rpc_kwargs(platform) -> dict`, `parse(xml, platform) -> Any`, `collect(device, platform) -> Any`, `supports(platform) -> bool`
  - `registry.register(cls)`, `registry.all_collectors()`, `registry.collectors_for(platform)`
  - CLI podpříkaz `record --device X --output-dir DIR [--auth ...]` — uloží syrové RPC XML pro každý registrovaný collector

**Proč `record` hned teď:** XPath se nesmí psát naslepo. Tenhle podpříkaz vyrobí fixtures z laborky, proti kterým se v Tascích 3–5 píšou parsery. Bez něj by kolektory byly hádání.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/collectors/__init__.py` (prázdný) a `tests/collectors/test_base.py`:

```python
import pytest
from lxml import etree

from migration_validator.collectors.base import Collector, CollectorError
from migration_validator.collectors.registry import (
    all_collectors,
    collectors_for,
    register,
)


class FakeRpcMeta:
    def __init__(self, responses):
        self._responses = responses

    def __getattr__(self, name):
        if name not in self._responses:
            raise AttributeError(name)

        def call(**kwargs):
            return self._responses[name]

        return call


class FakeDevice:
    def __init__(self, responses):
        self.rpc = FakeRpcMeta(responses)


class DemoCollector(Collector):
    name = "demo"

    def rpc_name(self, platform):
        return "get_demo_information"

    def parse(self, xml, platform):
        return {node.get("id"): node.text for node in xml.findall("item")}


def test_collect_runs_rpc_and_parses():
    xml = etree.fromstring('<demo><item id="a">1</item><item id="b">2</item></demo>')
    device = FakeDevice({"get_demo_information": xml})

    assert DemoCollector().collect(device, "junos") == {"a": "1", "b": "2"}


def test_unsupported_platform_raises():
    class EvoOnly(DemoCollector):
        name = "evo_only"
        platforms = ("junos-evo",)

    collector = EvoOnly()
    assert collector.supports("junos") is False

    with pytest.raises(CollectorError, match="junos"):
        collector.collect(FakeDevice({}), "junos")


def test_missing_rpc_raises_collector_error():
    with pytest.raises(CollectorError, match="get_demo_information"):
        DemoCollector().collect(FakeDevice({}), "junos")


def test_registry_filters_by_platform():
    @register
    class OnlyEvo(DemoCollector):
        name = "only_evo"
        platforms = ("junos-evo",)

    @register
    class Both(DemoCollector):
        name = "both_platforms"

    names = {collector.name for collector in collectors_for("junos")}
    assert "both_platforms" in names
    assert "only_evo" not in names

    assert {collector.name for collector in all_collectors()} >= {
        "only_evo",
        "both_platforms",
    }


def test_duplicate_registration_is_rejected():
    @register
    class Unique(DemoCollector):
        name = "unique_collector"

    with pytest.raises(ValueError, match="unique_collector"):

        @register
        class Duplicate(DemoCollector):
            name = "unique_collector"
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/collectors/test_base.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.collectors'`

- [ ] **Step 3: Implementuj základ**

Vytvoř `migration_validator/collectors/__init__.py` (prázdný) a `migration_validator/collectors/base.py`:

```python
"""Zaklad collectoru.

Collector nikdy neinterpretuje - vraci syrova strukturovana data. Kdyz se
zmeni kriterium, meni se check, ne sber, a stare snapshoty zustanou pouzitelne.

Platformni rozdily MX vs EVO se resi tady. Navenek vraci obe platformy
stejne schema.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from lxml import etree

PLATFORMS = ("junos", "junos-evo")


class CollectorError(Exception):
    """Sber jedne oblasti selhal. Ostatni collectory pokracuji."""


class Collector(ABC):
    name: ClassVar[str]
    platforms: ClassVar[tuple[str, ...]] = PLATFORMS

    def supports(self, platform: str) -> bool:
        return platform in self.platforms

    @abstractmethod
    def rpc_name(self, platform: str) -> str:
        """Nazev RPC metody na PyEZ Device.rpc (podtrzitkova varianta)."""

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {}

    @abstractmethod
    def parse(self, xml: etree._Element, platform: str) -> Any:
        """Prevede RPC odpoved na strukturovana data. Zadne verdikty."""

    def collect(self, device: Any, platform: str) -> Any:
        if not self.supports(platform):
            raise CollectorError(
                f"collector '{self.name}' nepodporuje platformu '{platform}'"
            )

        rpc_name = self.rpc_name(platform)
        try:
            rpc = getattr(device.rpc, rpc_name)
        except AttributeError as error:
            raise CollectorError(
                f"collector '{self.name}': RPC '{rpc_name}' neni dostupne - {error}"
            ) from error

        try:
            xml = rpc(**self.rpc_kwargs(platform))
        except Exception as error:  # noqa: BLE001 - RpcError i sitove chyby
            raise CollectorError(
                f"collector '{self.name}': RPC '{rpc_name}' selhalo - "
                f"{type(error).__name__}: {error}"
            ) from error

        try:
            return self.parse(xml, platform)
        except Exception as error:  # noqa: BLE001
            raise CollectorError(
                f"collector '{self.name}': parsovani selhalo - "
                f"{type(error).__name__}: {error}"
            ) from error
```

- [ ] **Step 4: Implementuj registry**

Vytvoř `migration_validator/collectors/registry.py`:

```python
"""Registr collectoru."""

from __future__ import annotations

from migration_validator.collectors.base import Collector

_REGISTRY: dict[str, Collector] = {}


def register(cls: type[Collector]) -> type[Collector]:
    if cls.name in _REGISTRY:
        raise ValueError(f"collector '{cls.name}' je uz registrovany")
    _REGISTRY[cls.name] = cls()
    return cls


def all_collectors() -> list[Collector]:
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def collectors_for(platform: str) -> list[Collector]:
    return [collector for collector in all_collectors() if collector.supports(platform)]
```

- [ ] **Step 5: Spusť test**

Run: `.venv/bin/pytest tests/collectors/test_base.py -v`
Expected: PASS, 5 testů

- [ ] **Step 6: Přidej podpříkaz `record` do CLI**

V `migration_validator/cli.py` přidej import:

```python
from migration_validator.collectors.registry import collectors_for
from migration_validator.connection.junos import (
    ConnectionOptions,
    JunosConnectionError,
    connect,
    detect_platform,
)
```

Přidej funkci pro sdílené auth argumenty a handler:

```python
def _add_auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--username", default="ansible")
    parser.add_argument("--auth", choices=("key", "password"), default="key")
    parser.add_argument("--key-file", default=str(Path.home() / ".ssh" / "id_rsa"))
    parser.add_argument("--password")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--timeout", type=int, default=30)


def _connection_options(args: argparse.Namespace) -> ConnectionOptions:
    return ConnectionOptions(
        host=args.device,
        username=args.username,
        auth_type=args.auth,
        key_file=args.key_file,
        password=args.password,
        port=args.port,
        timeout=args.timeout,
    )


def _cmd_record(args: argparse.Namespace) -> int:
    from lxml import etree

    import migration_validator.collectors.all  # noqa: F401  (registrace)

    target_root = Path(args.output_dir)
    try:
        with connect(_connection_options(args)) as device:
            platform = detect_platform(device)
            target = target_root / platform
            target.mkdir(parents=True, exist_ok=True)

            for collector in collectors_for(platform):
                rpc_name = collector.rpc_name(platform)
                try:
                    xml = getattr(device.rpc, rpc_name)(**collector.rpc_kwargs(platform))
                except Exception as error:  # noqa: BLE001
                    print(f"  {collector.name}: SELHALO - {error}", file=sys.stderr)
                    continue

                path = target / f"{collector.name}.xml"
                path.write_bytes(etree.tostring(xml, pretty_print=True))
                print(f"  {collector.name}: {path}")
    except JunosConnectionError as error:
        raise ToolError(str(error)) from error

    return EXIT_OK
```

Zaregistruj podpříkaz v `build_parser()`:

```python
    record = sub.add_parser(
        "record", help="ulozi syrove RPC XML jako fixtures pro testy"
    )
    record.add_argument("--device", required=True)
    record.add_argument("--output-dir", required=True)
    _add_auth_arguments(record)
    record.set_defaults(func=_cmd_record)
```

Doplň chybějící importy na začátek `cli.py`:

```python
from pathlib import Path
```

- [ ] **Step 7: Vytvoř zatím prázdný modul `all`**

Vytvoř `migration_validator/collectors/all.py`:

```python
"""Import vsech collectoru kvuli registraci.

Naplni se v Tascich 3-5.
"""

from __future__ import annotations


def load_all() -> None:
    return None
```

- [ ] **Step 8: Ověř, že CLI nespadlo**

Run: `.venv/bin/pytest -q`
Expected: všechny testy PASS

Run: `.venv/bin/mig-validate record --help`
Expected: nápověda s `--device` a `--output-dir`

- [ ] **Step 9: Commit**

```bash
git add migration_validator/collectors migration_validator/cli.py tests/collectors
git commit -m "feat: add collector base, registry and raw RPC recorder"
```

---

### Task 3: Collector rozhraní a ARP

**Files:**
- Create: `migration_validator/collectors/interfaces.py`
- Create: `migration_validator/collectors/arp.py`
- Modify: `migration_validator/collectors/all.py`
- Create: `tests/collectors/conftest.py`
- Test: `tests/collectors/test_interfaces.py`, `tests/collectors/test_arp.py`
- Create: `tests/fixtures/rpc/junos/interfaces.xml`, `tests/fixtures/rpc/junos-evo/interfaces.xml`, `tests/fixtures/rpc/junos/arp.xml`, `tests/fixtures/rpc/junos-evo/arp.xml`

**Interfaces:**
- Consumes: `Collector`, `register` (Task 2)
- Produces:
  - `InterfacesCollector` (`name="interfaces"`) → `dict[str, dict]` s klíči `admin_status`, `oper_status`, `input_pps`, `output_pps`, `input_errors`, `output_errors`
  - `ArpCollector` (`name="arp"`) → `list[dict]` s klíči `ip`, `mac`, `interface`, `routing_instance`
  - `tests/collectors/conftest.py`: fixture `rpc_fixture(platform, name) -> etree._Element`

**Tvar dat musí odpovídat tomu, co konzumují checky z Plánu 1** — viz `facts` schéma ve specu.

- [ ] **Step 1: Nahraj syrové XML z laborky**

Podpříkaz `record` iteruje přes **registrované** collectory, a ty zatím žádné nejsou — proto se
první sada fixtures nahrává ručně. Od Tasku 4 dál už `mig-validate record` funguje normálně.

```bash
.venv/bin/python -c "
from lxml import etree
from pathlib import Path
from migration_validator.connection.junos import ConnectionOptions, connect, detect_platform

RPCS = {
    'interfaces': ('get_interface_information', {'extensive': True}),
    'arp': ('get_arp_table_information', {'no_resolve': True}),
}

for address in ('172.20.20.4', '172.20.20.5'):
    with connect(ConnectionOptions(host=address)) as dev:
        platform = detect_platform(dev)
        target = Path('tests/fixtures/rpc') / platform
        target.mkdir(parents=True, exist_ok=True)
        for name, (rpc, kwargs) in RPCS.items():
            xml = getattr(dev.rpc, rpc)(**kwargs)
            (target / f'{name}.xml').write_bytes(etree.tostring(xml, pretty_print=True))
            print(platform, name, 'ulozeno')
"
```

- [ ] **Step 2: Prohlédni si strukturu XML**

```bash
.venv/bin/python -c "
from lxml import etree
tree = etree.parse('tests/fixtures/rpc/junos/interfaces.xml')
node = tree.find('.//logical-interface')
print(etree.tostring(node, pretty_print=True).decode()[:3000])
"
```

**Zapiš si skutečné názvy elementů.** Implementace níže vychází z běžné struktury Junos
(`physical-interface/name`, `admin-status`, `oper-status`, `logical-interface/name`,
`transit-traffic-statistics/input-pps`), ale **musíš ji ověřit** — zvlášť na EVO, kde se
umístění `transit-traffic-statistics` může lišit. Pokud se liší, uprav XPath v `parse()`
a doplň testovací případ.

- [ ] **Step 3: Vytvoř fixture harness**

Vytvoř `tests/collectors/conftest.py`:

```python
"""Nacitani nahraneho RPC XML pro testy collectoru."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "rpc"


@pytest.fixture
def rpc_fixture():
    def load(platform: str, name: str) -> etree._Element:
        path = FIXTURE_ROOT / platform / f"{name}.xml"
        if not path.exists():
            pytest.skip(f"chybi fixture {path} - nahraj ji pres 'mig-validate record'")
        return etree.parse(str(path)).getroot()

    return load
```

- [ ] **Step 4: Napiš failing testy**

Vytvoř `tests/collectors/test_interfaces.py`:

```python
import pytest

from migration_validator.collectors.interfaces import InterfacesCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_parses_something(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    assert result, "parser nevratil zadne rozhrani"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_entry_has_full_schema(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    required = {
        "admin_status",
        "oper_status",
        "input_pps",
        "output_pps",
        "input_errors",
        "output_errors",
    }
    for name, data in result.items():
        assert required <= set(data), f"{name} nema plne schema: {sorted(data)}"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_counters_are_integers(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    for name, data in result.items():
        for key in ("input_pps", "output_pps", "input_errors", "output_errors"):
            assert isinstance(data[key], int), f"{name}.{key} neni int"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_includes_logical_units(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    assert any("." in name for name in result), "chybi logicke jednotky"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_includes_physical_interfaces(rpc_fixture, platform):
    result = InterfacesCollector().parse(rpc_fixture(platform, "interfaces"), platform)
    assert any("." not in name for name in result), "chybi fyzicka rozhrani"


def test_collector_supports_both_platforms():
    collector = InterfacesCollector()
    assert collector.supports("junos")
    assert collector.supports("junos-evo")
    assert collector.name == "interfaces"
```

Vytvoř `tests/collectors/test_arp.py`:

```python
import pytest

from migration_validator.collectors.arp import ArpCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_list_of_entries(rpc_fixture, platform):
    result = ArpCollector().parse(rpc_fixture(platform, "arp"), platform)
    assert isinstance(result, list)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_entries_have_expected_keys(rpc_fixture, platform):
    result = ArpCollector().parse(rpc_fixture(platform, "arp"), platform)
    for entry in result:
        assert set(entry) == {"ip", "mac", "interface", "routing_instance"}
        assert entry["ip"]
        assert entry["interface"]


def test_collector_metadata():
    assert ArpCollector().name == "arp"
```

- [ ] **Step 5: Spusť testy a ověř, že selžou**

Run: `.venv/bin/pytest tests/collectors -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.collectors.interfaces'`

- [ ] **Step 6: Implementuj collector rozhraní**

Vytvoř `migration_validator/collectors/interfaces.py`:

```python
"""Sber stavu a counteru rozhrani.

transit-traffic-statistics/input-pps a output-pps uz jsou rate, ne kumulativni
counter - Junos je pocita sam. Neni proto potreba dvojite vzorkovani ani
cekaci okno, staci jeden RPC pruchod.

Absolutni byte countery se zamerne nesbiraji: mezi dvema boxy jsou
nesrovnatelne, protoze bezi od jineho uptime.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.registry import register


def _text(node: etree._Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _int(node: etree._Element | None, path: str) -> int:
    value = _text(node, path)
    if value is None:
        return 0
    try:
        return int(float(value))
    except ValueError:
        return 0


def _rates(node: etree._Element) -> dict[str, int]:
    """input-pps / output-pps z transit-traffic-statistics."""
    stats = node.find("transit-traffic-statistics")
    if stats is None:
        stats = node.find("traffic-statistics")
    return {
        "input_pps": _int(stats, "input-pps"),
        "output_pps": _int(stats, "output-pps"),
    }


@register
class InterfacesCollector(Collector):
    name = "interfaces"

    def rpc_name(self, platform: str) -> str:
        return "get_interface_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"extensive": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for physical in xml.iter("physical-interface"):
            name = _text(physical, "name")
            if not name:
                continue

            errors = physical.find("input-error-list")
            output_errors = physical.find("output-error-list")

            result[name] = {
                "admin_status": (_text(physical, "admin-status") or "unknown").lower(),
                "oper_status": (_text(physical, "oper-status") or "unknown").lower(),
                "input_errors": _int(errors, "input-errors"),
                "output_errors": _int(output_errors, "output-errors"),
                **_rates(physical),
            }

            for logical in physical.iter("logical-interface"):
                unit_name = _text(logical, "name")
                if not unit_name:
                    continue
                result[unit_name] = {
                    "admin_status": result[name]["admin_status"],
                    "oper_status": (
                        _text(logical, "oper-status") or result[name]["oper_status"]
                    ).lower(),
                    "input_errors": 0,
                    "output_errors": 0,
                    **_rates(logical),
                }

        return result
```

- [ ] **Step 7: Implementuj ARP collector**

Vytvoř `migration_validator/collectors/arp.py`:

```python
"""Sber ARP tabulky.

ARP je zaroven producent dat pro ping probe - z nej se odvozuji cile.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _text
from migration_validator.collectors.registry import register


@register
class ArpCollector(Collector):
    name = "arp"

    def rpc_name(self, platform: str) -> str:
        return "get_arp_table_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"no_resolve": True}

    def parse(self, xml: etree._Element, platform: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        for node in xml.iter("arp-table-entry"):
            address = _text(node, "ip-address")
            interface = _text(node, "interface-name")
            if not address or not interface:
                continue
            entries.append(
                {
                    "ip": address,
                    "mac": _text(node, "mac-address"),
                    "interface": interface,
                    "routing_instance": _text(node, "arp-table-entry-flags/routing-instance")
                    or _text(node, "routing-instance"),
                }
            )

        return entries
```

- [ ] **Step 8: Zaregistruj collectory**

Přepiš `migration_validator/collectors/all.py`:

```python
"""Import vsech collectoru kvuli registraci."""

from __future__ import annotations

from migration_validator.collectors import arp, interfaces  # noqa: F401


def load_all() -> None:
    return None
```

- [ ] **Step 9: Spusť testy**

Run: `.venv/bin/pytest tests/collectors -v`
Expected: PASS. **Pokud některý test selže, znamená to, že XPath neodpovídá skutečnému XML** — uprav `parse()` podle toho, co jsi viděl v Kroku 2, ne naopak test.

- [ ] **Step 10: Ověř proti živému zařízení**

```bash
.venv/bin/python -c "
from migration_validator.collectors.registry import collectors_for
import migration_validator.collectors.all
from migration_validator.connection.junos import ConnectionOptions, connect, detect_platform

with connect(ConnectionOptions(host='172.20.20.4')) as dev:
    platform = detect_platform(dev)
    for collector in collectors_for(platform):
        data = collector.collect(dev, platform)
        print(collector.name, '->', len(data), 'polozek')
"
```

Expected: `interfaces` a `arp` vrátí nenulový počet položek.

- [ ] **Step 11: Commit**

```bash
git add migration_validator/collectors tests/collectors tests/fixtures
git commit -m "feat: add interface and ARP collectors with recorded fixtures"
```

---

### Task 4: BGP collector

**Files:**
- Create: `migration_validator/collectors/bgp.py`
- Modify: `migration_validator/collectors/all.py`
- Test: `tests/collectors/test_bgp.py`
- Create: `tests/fixtures/rpc/junos/bgp.xml`, `tests/fixtures/rpc/junos-evo/bgp.xml`

**Interfaces:**
- Consumes: `Collector`, `register` (Task 2), `_text`, `_int` (Task 3)
- Produces: `BgpCollector` (`name="bgp"`) → `dict[str, dict]` klíčovaný adresou peera, hodnota má `state`, `peer_as`, `routing_instance`, `prefixes: {received, accepted, advertised}`

**Proč `get_bgp_neighbor_information` a ne summary:** `show bgp summary` neobsahuje počet **advertised** prefixů. Neighbor varianta má v `bgp-rib` všechny tři počty najednou.

- [ ] **Step 1: Nahraj XML**

```bash
.venv/bin/python -c "
from lxml import etree
from pathlib import Path
from migration_validator.connection.junos import ConnectionOptions, connect, detect_platform

for address in ('172.20.20.4', '172.20.20.5'):
    with connect(ConnectionOptions(host=address)) as dev:
        platform = detect_platform(dev)
        target = Path('tests/fixtures/rpc') / platform
        target.mkdir(parents=True, exist_ok=True)
        xml = dev.rpc.get_bgp_neighbor_information()
        (target / 'bgp.xml').write_bytes(etree.tostring(xml, pretty_print=True))
        print(platform, 'bgp ulozeno')
"
```

- [ ] **Step 2: Prohlédni si strukturu**

```bash
.venv/bin/python -c "
from lxml import etree
tree = etree.parse('tests/fixtures/rpc/junos/bgp.xml')
peer = tree.find('.//bgp-peer')
print(etree.tostring(peer, pretty_print=True).decode()[:4000])
"
```

Ověř zejména:
- `peer-address` často obsahuje port (`198.11.13.2+179`) — implementace ho ořezává
- `peer-cfg-rti` nese routing instanci; u default instance může chybět nebo být `master`
- `bgp-rib` může být víc (inet.0, inet6.0, VRF) — počty se sčítají přes všechny RIB

- [ ] **Step 3: Napiš failing test**

Vytvoř `tests/collectors/test_bgp.py`:

```python
import pytest
from lxml import etree

from migration_validator.collectors.bgp import BgpCollector, strip_port

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("198.11.13.2+179", "198.11.13.2"),
        ("198.11.13.2", "198.11.13.2"),
        ("2001:db8:11:13::b+51234", "2001:db8:11:13::b"),
        ("  10.0.0.1  ", "10.0.0.1"),
    ],
)
def test_strip_port(raw, expected):
    assert strip_port(raw) == expected


@pytest.mark.parametrize("platform", PLATFORMS)
def test_parses_peers(rpc_fixture, platform):
    result = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    assert result, "parser nenasel zadneho peera"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_peer_schema(rpc_fixture, platform):
    result = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    for peer, data in result.items():
        assert "+" not in peer, f"adresa peera nese port: {peer}"
        assert set(data) == {"state", "peer_as", "routing_instance", "prefixes"}
        assert set(data["prefixes"]) == {"received", "accepted", "advertised"}
        assert all(isinstance(value, int) for value in data["prefixes"].values())


def test_prefix_counts_are_summed_across_ribs():
    xml = etree.fromstring(
        """
        <bgp-information>
          <bgp-peer>
            <peer-address>10.0.0.1+179</peer-address>
            <peer-state>Established</peer-state>
            <peer-as>65001</peer-as>
            <peer-cfg-rti>L3VPN-A</peer-cfg-rti>
            <bgp-rib>
              <received-prefix-count>10</received-prefix-count>
              <accepted-prefix-count>9</accepted-prefix-count>
              <advertised-prefix-count>2</advertised-prefix-count>
            </bgp-rib>
            <bgp-rib>
              <received-prefix-count>4</received-prefix-count>
              <accepted-prefix-count>4</accepted-prefix-count>
              <advertised-prefix-count>1</advertised-prefix-count>
            </bgp-rib>
          </bgp-peer>
        </bgp-information>
        """
    )

    result = BgpCollector().parse(xml, "junos")

    assert result["10.0.0.1"]["prefixes"] == {
        "received": 14,
        "accepted": 13,
        "advertised": 3,
    }
    assert result["10.0.0.1"]["state"] == "Established"
    assert result["10.0.0.1"]["peer_as"] == 65001
    assert result["10.0.0.1"]["routing_instance"] == "L3VPN-A"


def test_missing_routing_instance_becomes_none():
    xml = etree.fromstring(
        """
        <bgp-information>
          <bgp-peer>
            <peer-address>10.0.0.2</peer-address>
            <peer-state>Active</peer-state>
          </bgp-peer>
        </bgp-information>
        """
    )
    result = BgpCollector().parse(xml, "junos")
    assert result["10.0.0.2"]["routing_instance"] is None
    assert result["10.0.0.2"]["prefixes"] == {
        "received": 0,
        "accepted": 0,
        "advertised": 0,
    }


def test_collector_metadata():
    assert BgpCollector().name == "bgp"
```

- [ ] **Step 4: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/collectors/test_bgp.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.collectors.bgp'`

- [ ] **Step 5: Implementuj collector**

Vytvoř `migration_validator/collectors/bgp.py`:

```python
"""Sber stavu BGP session a poctu prefixu.

Pouziva get_bgp_neighbor_information, protoze summary varianta neobsahuje
pocet advertised prefixu.

Collector nerozhoduje, jestli je stav v poradku - jen ho zapise.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _int, _text
from migration_validator.collectors.registry import register

DEFAULT_INSTANCES = {"master", "default", ""}


def strip_port(value: str) -> str:
    """peer-address casto nese port: '198.11.13.2+179' -> '198.11.13.2'."""
    return value.strip().split("+", 1)[0]


@register
class BgpCollector(Collector):
    name = "bgp"

    def rpc_name(self, platform: str) -> str:
        return "get_bgp_neighbor_information"

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        peers: dict[str, dict[str, Any]] = {}

        for node in xml.iter("bgp-peer"):
            raw_address = _text(node, "peer-address")
            if not raw_address:
                continue
            address = strip_port(raw_address)

            prefixes = {"received": 0, "accepted": 0, "advertised": 0}
            for rib in node.iter("bgp-rib"):
                prefixes["received"] += _int(rib, "received-prefix-count")
                prefixes["accepted"] += _int(rib, "accepted-prefix-count")
                prefixes["advertised"] += _int(rib, "advertised-prefix-count")

            instance = _text(node, "peer-cfg-rti")
            if instance is not None and instance.lower() in DEFAULT_INSTANCES:
                instance = None

            peer_as = _text(node, "peer-as")

            peers[address] = {
                "state": _text(node, "peer-state") or "unknown",
                "peer_as": int(peer_as) if peer_as and peer_as.isdigit() else None,
                "routing_instance": instance,
                "prefixes": prefixes,
            }

        return peers
```

- [ ] **Step 6: Zaregistruj collector**

V `migration_validator/collectors/all.py` uprav import:

```python
from migration_validator.collectors import arp, bgp, interfaces  # noqa: F401
```

- [ ] **Step 7: Spusť test**

Run: `.venv/bin/pytest tests/collectors/test_bgp.py -v`
Expected: PASS. Pokud fixture testy selžou, uprav XPath podle skutečného XML z Kroku 2.

- [ ] **Step 8: Commit**

```bash
git add migration_validator/collectors/bgp.py migration_validator/collectors/all.py \
        tests/collectors/test_bgp.py tests/fixtures
git commit -m "feat: add BGP collector with per-peer prefix counts"
```

---

### Task 5: EVPN collectory

**Files:**
- Create: `migration_validator/collectors/evpn.py`
- Modify: `migration_validator/collectors/all.py`
- Test: `tests/collectors/test_evpn.py`
- Create: `tests/fixtures/rpc/{junos,junos-evo}/evpn_vpws.xml`, `evpn_esi.xml`, `evpn_mac.xml`

**Interfaces:**
- Consumes: `Collector`, `register` (Task 2), `_text`, `_int` (Task 3)
- Produces:
  - `EvpnVpwsCollector` (`name="evpn_vpws"`) → `{instance: {local_sid: int, remote_sid: int, status: str}}`
  - `EvpnEsiCollector` (`name="evpn_esi"`) → `{esi: {status: str, df_role: str, interface: str}}`
  - `EvpnMacCollector` (`name="evpn_mac"`) → `{instance: {bridge_domain: int}}`; u `vlan-based` instance bez vlastní bridge domény se použije klíč `"-"`

**Tohle je task s největší nejistotou.** RPC názvy i struktura se mezi MX a EVO liší nejvíc a bez nahraného XML se to napsat nedá. Postup je proto: nejdřív zjistit dostupná RPC, pak nahrát, pak psát parser.

- [ ] **Step 1: Zjisti skutečné RPC názvy na obou platformách**

```bash
.venv/bin/python -c "
from migration_validator.connection.junos import ConnectionOptions, connect, detect_platform

CANDIDATES = [
    'get_evpn_vpws_instance_information',
    'get_evpn_instance_information',
    'get_evpn_ethernet_segment_information',
    'get_evpn_mac_table',
    'get_mac_vrf_forwarding_mac_table',
    'get_bridge_mac_table',
]

for address in ('172.20.20.4', '172.20.20.5'):
    with connect(ConnectionOptions(host=address)) as dev:
        print('---', address, detect_platform(dev))
        for name in CANDIDATES:
            print(f'  {name}: {\"ANO\" if hasattr(dev.rpc, name) else \"ne\"}')
"
```

Pokud kandidát chybí, najdi správný název přes CLI na zařízení:

```
show evpn vpws-instance | display xml rpc
show evpn instance extensive | display xml rpc
show bridge mac-table | display xml rpc          # MX
show mac-vrf forwarding mac-table | display xml rpc   # EVO
```

**Zapiš si skutečné názvy** — v implementaci níže je nahradíš v metodě `rpc_name()`.

- [ ] **Step 2: Nahraj XML pro všechny tři oblasti**

```bash
.venv/bin/python -c "
from lxml import etree
from pathlib import Path
from migration_validator.connection.junos import ConnectionOptions, connect, detect_platform

# Uprav podle vysledku Kroku 1
RPCS = {
    'junos': {
        'evpn_vpws': 'get_evpn_vpws_instance_information',
        'evpn_esi':  'get_evpn_instance_information',
        'evpn_mac':  'get_bridge_mac_table',
    },
    'junos-evo': {
        'evpn_vpws': 'get_evpn_vpws_instance_information',
        'evpn_esi':  'get_evpn_instance_information',
        'evpn_mac':  'get_mac_vrf_forwarding_mac_table',
    },
}

for address in ('172.20.20.4', '172.20.20.5'):
    with connect(ConnectionOptions(host=address)) as dev:
        platform = detect_platform(dev)
        target = Path('tests/fixtures/rpc') / platform
        target.mkdir(parents=True, exist_ok=True)
        for name, rpc in RPCS[platform].items():
            try:
                xml = getattr(dev.rpc, rpc)()
            except Exception as error:
                print(f'  {platform} {name}: SELHALO - {error}')
                continue
            (target / f'{name}.xml').write_bytes(etree.tostring(xml, pretty_print=True))
            print(f'  {platform} {name}: ulozeno')
"
```

- [ ] **Step 3: Napiš failing test**

Vytvoř `tests/collectors/test_evpn.py`:

```python
import pytest
from lxml import etree

from migration_validator.collectors.evpn import (
    EvpnEsiCollector,
    EvpnMacCollector,
    EvpnVpwsCollector,
    NO_DOMAIN,
)

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_vpws_schema(rpc_fixture, platform):
    result = EvpnVpwsCollector().parse(rpc_fixture(platform, "evpn_vpws"), platform)
    assert isinstance(result, dict)
    for instance, data in result.items():
        assert set(data) == {"local_sid", "remote_sid", "status"}
        assert isinstance(data["status"], str)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_esi_schema(rpc_fixture, platform):
    result = EvpnEsiCollector().parse(rpc_fixture(platform, "evpn_esi"), platform)
    assert isinstance(result, dict)
    for esi, data in result.items():
        assert set(data) == {"status", "df_role", "interface"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_mac_schema(rpc_fixture, platform):
    result = EvpnMacCollector().parse(rpc_fixture(platform, "evpn_mac"), platform)
    assert isinstance(result, dict)
    for instance, domains in result.items():
        assert isinstance(domains, dict)
        assert all(isinstance(count, int) for count in domains.values())


def test_mac_counts_are_grouped_by_instance_and_domain():
    xml = etree.fromstring(
        """
        <l2ald-mac-table>
          <mac-table-entry>
            <mac-routing-instance>EVPN-AWARE</mac-routing-instance>
            <mac-bridging-domain>BD-313</mac-bridging-domain>
            <mac-address>00:11:22:33:44:01</mac-address>
          </mac-table-entry>
          <mac-table-entry>
            <mac-routing-instance>EVPN-AWARE</mac-routing-instance>
            <mac-bridging-domain>BD-313</mac-bridging-domain>
            <mac-address>00:11:22:33:44:02</mac-address>
          </mac-table-entry>
          <mac-table-entry>
            <mac-routing-instance>EVPN-BASED</mac-routing-instance>
            <mac-address>00:11:22:33:44:03</mac-address>
          </mac-table-entry>
        </l2ald-mac-table>
        """
    )

    result = EvpnMacCollector().parse(xml, "junos")

    assert result == {
        "EVPN-AWARE": {"BD-313": 2},
        "EVPN-BASED": {NO_DOMAIN: 1},
    }


def test_collector_names():
    assert EvpnVpwsCollector().name == "evpn_vpws"
    assert EvpnEsiCollector().name == "evpn_esi"
    assert EvpnMacCollector().name == "evpn_mac"
```

- [ ] **Step 4: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/collectors/test_evpn.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.collectors.evpn'`

- [ ] **Step 5: Implementuj collectory**

Vytvoř `migration_validator/collectors/evpn.py`. **RPC názvy v `rpc_name()` a názvy elementů v `parse()` uprav podle toho, co jsi zjistil v Krocích 1 a 2** — níže je výchozí varianta pro běžnou strukturu Junos:

```python
"""Sber EVPN stavu pro E-Line (vpws) a E-LAN (vlan-aware, vlan-based).

Platformni rozdil se resi tady: MX pouziva virtual-switch/bridge-domain,
EVO mac-vrf/VLAN. Navenek oba vraci stejne schema, takze check
evpn_mac_count nikde neobsahuje vetev na platformu.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.interfaces import _int, _text
from migration_validator.collectors.registry import register

NO_DOMAIN = "-"


@register
class EvpnVpwsCollector(Collector):
    name = "evpn_vpws"

    def rpc_name(self, platform: str) -> str:
        return "get_evpn_vpws_instance_information"

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        instances: dict[str, dict[str, Any]] = {}

        for node in xml.iter("evpn-vpws-instance"):
            name = _text(node, "instance-name") or _text(node, "evpn-instance-name")
            if not name:
                continue
            service = node.find("vpws-service-id")
            instances[name] = {
                "local_sid": _int(service, "local-sid") or _int(node, "local-sid"),
                "remote_sid": _int(service, "remote-sid") or _int(node, "remote-sid"),
                "status": (
                    _text(node, "evpn-vpws-sid-pe-status")
                    or _text(node, "vpws-sid-pe-status")
                    or "unknown"
                ),
            }

        return instances


@register
class EvpnEsiCollector(Collector):
    name = "evpn_esi"

    def rpc_name(self, platform: str) -> str:
        return "get_evpn_instance_information"

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        segments: dict[str, dict[str, Any]] = {}

        for node in xml.iter("evpn-segment-information"):
            esi = _text(node, "esi") or _text(node, "esi-identifier")
            if not esi:
                continue
            segments[esi] = {
                "status": _text(node, "esi-state") or _text(node, "status") or "unknown",
                "df_role": _text(node, "designated-forwarder-role")
                or _text(node, "df-role"),
                "interface": _text(node, "interface-name") or _text(node, "esi-interface"),
            }

        return segments


@register
class EvpnMacCollector(Collector):
    name = "evpn_mac"

    def rpc_name(self, platform: str) -> str:
        if platform == "junos-evo":
            return "get_mac_vrf_forwarding_mac_table"
        return "get_bridge_mac_table"

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for node in xml.iter("mac-table-entry"):
            instance = _text(node, "mac-routing-instance")
            if not instance:
                continue
            domain = _text(node, "mac-bridging-domain") or NO_DOMAIN
            counts[instance][domain] += 1

        return {instance: dict(domains) for instance, domains in counts.items()}
```

- [ ] **Step 6: Zaregistruj collectory**

V `migration_validator/collectors/all.py`:

```python
from migration_validator.collectors import arp, bgp, evpn, interfaces  # noqa: F401
```

- [ ] **Step 7: Spusť test**

Run: `.venv/bin/pytest tests/collectors/test_evpn.py -v`
Expected: PASS. Fixture testy jsou schválně volné (kontrolují jen schéma), aby prošly i na prázdné tabulce; syntetický test `test_mac_counts_are_grouped_by_instance_and_domain` je ten přísný.

**Pokud parser vrátí prázdný dict a v XML data jsou**, XPath neodpovídá — uprav ho.

- [ ] **Step 8: Ověř proti laborce, že data nejsou prázdná**

```bash
.venv/bin/python -c "
import migration_validator.collectors.all
from migration_validator.collectors.registry import collectors_for
from migration_validator.connection.junos import ConnectionOptions, connect, detect_platform

for address in ('172.20.20.4', '172.20.20.5'):
    with connect(ConnectionOptions(host=address)) as dev:
        platform = detect_platform(dev)
        print('---', address, platform)
        for collector in collectors_for(platform):
            if not collector.name.startswith('evpn'):
                continue
            print(' ', collector.name, collector.collect(dev, platform))
"
```

Expected: `evpn_vpws` obsahuje `EVPN-VPWS-CPE13-NNI`, `evpn_mac` obsahuje instance `EVPN-VLAN-AWARE-CPE13-NNI` a `EVPN-VLAN-BASED-CPE13-NNI`.

- [ ] **Step 9: Commit**

```bash
git add migration_validator/collectors/evpn.py migration_validator/collectors/all.py \
        tests/collectors/test_evpn.py tests/fixtures
git commit -m "feat: add EVPN VPWS, ESI and MAC collectors with platform variants"
```

---

### Task 6: Ping probe a odvození cílů

**Files:**
- Create: `migration_validator/probes/__init__.py`
- Create: `migration_validator/probes/ping.py`
- Test: `tests/probes/test_ping.py`

**Interfaces:**
- Consumes: `Scope` (Plán 1, Task 4)
- Produces:
  - `PingTarget(scope_id, target, source, routing_instance, resolved_from)`
  - `PING_SERVICE_TYPES: frozenset[str]` = `{"Internet", "IPVPN"}`
  - `source_address(scope) -> str | None` — u IRB se použije `virtual_gw`
  - `subnet_fallback(scope) -> str | None`
  - `resolve_targets(scopes, arp_entries) -> list[PingTarget]`
  - `run_ping(device, target, count=5) -> dict`
  - `parse_ping_result(xml) -> dict`

**Pravidla:**
- Ping jen pro `Internet` a `IPVPN`.
- Primárně **všechny** adresy z ARP na rozhraních scope.
- Prázdný ARP → fallback na první použitelnou adresu ze subnetu (`resolved_from: "subnet-fallback"`).
- Source = adresa rozhraní; **u IRB `virtual_gw_ip_address`**.
- IPVPN → `routing_instance`; Internet → bez ní.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/probes/__init__.py` (prázdný) a `tests/probes/test_ping.py`:

```python
import pytest
from lxml import etree

from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.probes.ping import (
    parse_ping_result,
    resolve_targets,
    source_address,
    subnet_fallback,
)


def _scope(
    scope_id="svc:X:IPVPN",
    service_type="IPVPN",
    interfaces=("ge-0/0/2.113",),
    addresses=("198.11.13.1/30",),
    virtual_gw=(),
    routing_instance="L3VPN-CPE13-NNI",
):
    return Scope(
        id=scope_id,
        kind="service",
        key=ScopeKey("X", service_type, None),
        selectors=Selectors(
            interfaces=list(interfaces),
            local_addresses=list(addresses),
            virtual_gw=list(virtual_gw),
            routing_instances=[routing_instance] if routing_instance else [],
        ),
    )


def test_source_is_interface_address():
    assert source_address(_scope()) == "198.11.13.1"


def test_source_prefers_virtual_gw_on_irb():
    scope = _scope(
        interfaces=("irb.14",),
        addresses=("152.11.14.2/29",),
        virtual_gw=("152.11.14.1",),
    )
    assert source_address(scope) == "152.11.14.1"


def test_source_none_when_no_address():
    assert source_address(_scope(addresses=())) is None


@pytest.mark.parametrize(
    "address,expected",
    [
        ("198.11.13.1/30", "198.11.13.2"),
        ("198.11.13.2/30", "198.11.13.1"),
        ("10.1.2.1/31", "10.1.2.0"),
        ("10.1.2.0/31", "10.1.2.1"),
        ("152.11.14.1/29", "152.11.14.2"),
        ("150.0.0.11/32", None),
    ],
)
def test_subnet_fallback(address, expected):
    assert subnet_fallback(_scope(addresses=(address,))) == expected


def test_targets_come_from_arp():
    arp = [
        {"ip": "198.11.13.2", "interface": "ge-0/0/2.113"},
        {"ip": "198.11.13.3", "interface": "ge-0/0/2.113"},
        {"ip": "10.9.9.9", "interface": "ge-0/0/9.0"},
    ]
    targets = resolve_targets([_scope()], arp)

    assert [target.target for target in targets] == ["198.11.13.2", "198.11.13.3"]
    assert all(target.resolved_from == "arp" for target in targets)
    assert all(target.source == "198.11.13.1" for target in targets)
    assert all(target.routing_instance == "L3VPN-CPE13-NNI" for target in targets)


def test_internet_service_has_no_routing_instance():
    targets = resolve_targets(
        [_scope(service_type="Internet", routing_instance=None)],
        [{"ip": "152.11.13.2", "interface": "ge-0/0/2.113"}],
    )
    assert targets[0].routing_instance is None


def test_empty_arp_falls_back_to_subnet():
    targets = resolve_targets([_scope()], [])
    assert len(targets) == 1
    assert targets[0].target == "198.11.13.2"
    assert targets[0].resolved_from == "subnet-fallback"


def test_no_targets_for_core_or_elan_scopes():
    scopes = [
        _scope(scope_id="svc:C:Core", service_type="Core"),
        _scope(scope_id="svc:E:E-LAN", service_type="E-LAN"),
    ]
    assert resolve_targets(scopes, []) == []


def test_device_scope_produces_no_targets():
    from migration_validator.models.scope import device_scope

    assert resolve_targets([device_scope()], [{"ip": "1.2.3.4", "interface": "ge-0/0/0"}]) == []


def test_parse_ping_result():
    xml = etree.fromstring(
        """
        <ping-results>
          <probe-results-summary>
            <probes-sent>5</probes-sent>
            <responses-received>4</responses-received>
            <packet-loss>20</packet-loss>
            <rtt-average>1240</rtt-average>
          </probe-results-summary>
        </ping-results>
        """
    )
    result = parse_ping_result(xml)

    assert result["sent"] == 5
    assert result["received"] == 4
    assert result["loss_percent"] == 20
    assert result["rtt_avg_ms"] == pytest.approx(1.24)


def test_parse_ping_result_handles_total_loss():
    xml = etree.fromstring(
        """
        <ping-results>
          <probe-results-summary>
            <probes-sent>5</probes-sent>
            <responses-received>0</responses-received>
            <packet-loss>100</packet-loss>
          </probe-results-summary>
        </ping-results>
        """
    )
    result = parse_ping_result(xml)
    assert result["received"] == 0
    assert result["rtt_avg_ms"] is None
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/probes/test_ping.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.probes'`

- [ ] **Step 3: Implementuj probe**

Vytvoř `migration_validator/probes/__init__.py` (prázdný) a `migration_validator/probes/ping.py`:

```python
"""Aktivni ping probe.

Jediny aktivni test - proto vlastni kategorie mimo collectory. Bezi az po
bulk sberu, protoze cile se odvozuji z ARP.

Ping bezi jen v service rezimu: bez inventory neni znam cil ani source
adresa, takze snapshot ma probes.ping prazdne.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

from lxml import etree

from migration_validator.models.scope import Scope

PING_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})
DEFAULT_COUNT = 5


@dataclass(frozen=True)
class PingTarget:
    scope_id: str
    target: str
    source: str | None
    routing_instance: str | None
    resolved_from: str  # arp | subnet-fallback

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "target": self.target,
            "source": self.source,
            "routing_instance": self.routing_instance,
            "resolved_from": self.resolved_from,
        }


def source_address(scope: Scope) -> str | None:
    """Adresa rozhrani. U IRB se pouziva virtual-gw."""
    if scope.selectors.virtual_gw:
        return scope.selectors.virtual_gw[0].split("/")[0]
    if scope.selectors.local_addresses:
        return scope.selectors.local_addresses[0].split("/")[0]
    return None


def subnet_fallback(scope: Scope) -> str | None:
    """Prvni pouzitelna adresa ze subnetu, ktera neni nase vlastni."""
    for address in scope.selectors.local_addresses:
        try:
            interface = ipaddress.ip_interface(address)
        except ValueError:
            continue

        network = interface.network
        if network.prefixlen >= network.max_prefixlen:
            continue

        for candidate in network:
            if candidate == interface.ip:
                continue
            if network.prefixlen < network.max_prefixlen - 1:
                if candidate in (network.network_address, network.broadcast_address):
                    continue
            return str(candidate)
    return None


def resolve_targets(
    scopes: list[Scope], arp_entries: list[dict[str, Any]]
) -> list[PingTarget]:
    """Odvodi cile pingu ze scopu a ARP tabulky."""
    targets: list[PingTarget] = []

    for scope in scopes:
        if scope.is_device or scope.service_type not in PING_SERVICE_TYPES:
            continue

        source = source_address(scope)
        instance = (
            scope.selectors.routing_instances[0]
            if scope.service_type == "IPVPN" and scope.selectors.routing_instances
            else None
        )

        addresses = [
            str(entry["ip"])
            for entry in arp_entries
            if scope.selectors.matches_interface(str(entry.get("interface", "")))
            and entry.get("ip")
        ]

        if addresses:
            targets.extend(
                PingTarget(scope.id, address, source, instance, "arp")
                for address in addresses
            )
            continue

        fallback = subnet_fallback(scope)
        if fallback:
            targets.append(
                PingTarget(scope.id, fallback, source, instance, "subnet-fallback")
            )

    return targets


def parse_ping_result(xml: etree._Element) -> dict[str, Any]:
    summary = xml.find(".//probe-results-summary")

    def value(path: str) -> str | None:
        if summary is None:
            return None
        node = summary.find(path)
        return node.text.strip() if node is not None and node.text else None

    sent = int(value("probes-sent") or 0)
    received = int(value("responses-received") or 0)
    loss = value("packet-loss")
    rtt_us = value("rtt-average")

    return {
        "sent": sent,
        "received": received,
        "loss_percent": int(loss) if loss is not None else None,
        "rtt_avg_ms": round(int(rtt_us) / 1000.0, 3) if rtt_us and received else None,
    }


def run_ping(device: Any, target: PingTarget, count: int = DEFAULT_COUNT) -> dict[str, Any]:
    """Spusti ping z zarizeni. Neuspech neni chyba nastroje, ale vysledek."""
    kwargs: dict[str, Any] = {"host": target.target, "count": str(count)}
    if target.source:
        kwargs["source"] = target.source
    if target.routing_instance:
        kwargs["routing_instance"] = target.routing_instance

    record = target.to_dict()
    try:
        xml = device.rpc.ping(**kwargs)
    except Exception as error:  # noqa: BLE001
        record.update(
            {
                "sent": count,
                "received": 0,
                "loss_percent": 100,
                "rtt_avg_ms": None,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        return record

    record.update(parse_ping_result(xml))
    return record
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/probes/test_ping.py -v`
Expected: PASS, 18 testů

- [ ] **Step 5: Ověř ping proti laborce**

```bash
.venv/bin/python -c "
from migration_validator.connection.junos import ConnectionOptions, connect
from migration_validator.probes.ping import PingTarget, run_ping

target = PingTarget(
    scope_id='svc:L3VPN-CPE13-NNI:IPVPN',
    target='198.11.13.2',
    source='198.11.13.1',
    routing_instance='L3VPN-CPE13-NNI',
    resolved_from='arp',
)
with connect(ConnectionOptions(host='172.20.20.4')) as dev:
    print(run_ping(dev, target))
"
```

Expected: dict s `sent`, `received`, `loss_percent`. Pokud RPC `ping` odmítne argument `routing_instance`, ověř skutečný název přes `show ... | display xml rpc` a uprav `run_ping`.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/probes tests/probes
git commit -m "feat: add ping probe with ARP-based target resolution"
```

---

### Task 7: Orchestrace capture, API a CLI

**Files:**
- Create: `migration_validator/capture.py`
- Modify: `migration_validator/api.py` — přidat `capture()`
- Modify: `migration_validator/cli.py` — přidat podpříkaz `capture`
- Test: `tests/test_capture.py`

**Interfaces:**
- Consumes: vše z Tasků 1–6 + `build_scopes`, `load_inventory`, `Snapshot`, `save_snapshot` (Plán 1)
- Produces:
  - `capture.capture_device(device, address, *, inventory=None, collector_names=None, ping_count=5, now=None, record_raw=None) -> Snapshot`
  - `api.capture(host, *, inventory=None, options=None, collectors=None, phase=None, ping_count=5) -> Snapshot`
  - CLI podpříkaz `capture --device X [--inventory Y] [--phase Z] --output O [--collectors a,b] [--record-raw DIR]`

**Pořadí fází** (podle specu): connect → bulk collectory → build scopes → resolve ping targets → ping probe → freeze.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/test_capture.py`:

```python
import pytest
from lxml import etree

from migration_validator.capture import capture_device
from migration_validator.models.inventory import load_inventory

NOW = "2026-07-24T09:12:41Z"

INTERFACES_XML = """
<interface-information>
  <physical-interface>
    <name>ge-0/0/2</name>
    <admin-status>up</admin-status>
    <oper-status>up</oper-status>
    <logical-interface>
      <name>ge-0/0/2.113</name>
      <oper-status>up</oper-status>
      <transit-traffic-statistics>
        <input-pps>412</input-pps>
        <output-pps>388</output-pps>
      </transit-traffic-statistics>
    </logical-interface>
  </physical-interface>
</interface-information>
"""

ARP_XML = """
<arp-table-information>
  <arp-table-entry>
    <ip-address>198.11.13.2</ip-address>
    <mac-address>00:11:22:33:44:55</mac-address>
    <interface-name>ge-0/0/2.113</interface-name>
  </arp-table-entry>
</arp-table-information>
"""

PING_XML = """
<ping-results>
  <probe-results-summary>
    <probes-sent>5</probes-sent>
    <responses-received>5</responses-received>
    <packet-loss>0</packet-loss>
    <rtt-average>1240</rtt-average>
  </probe-results-summary>
</ping-results>
"""


class FakeRpc:
    def __init__(self, failing=()):
        self.failing = set(failing)
        self.ping_calls = []

    def __getattr__(self, name):
        if name in self.failing:
            def boom(**kwargs):
                raise RuntimeError(f"RPC {name} selhalo")
            return boom

        responses = {
            "get_interface_information": INTERFACES_XML,
            "get_arp_table_information": ARP_XML,
            "get_bgp_neighbor_information": "<bgp-information/>",
            "get_evpn_vpws_instance_information": "<evpn-vpws-information/>",
            "get_evpn_instance_information": "<evpn-instance-information/>",
            "get_bridge_mac_table": "<l2ald-mac-table/>",
            "get_mac_vrf_forwarding_mac_table": "<l2ald-mac-table/>",
        }
        if name == "ping":
            def do_ping(**kwargs):
                self.ping_calls.append(kwargs)
                return etree.fromstring(PING_XML)
            return do_ping

        if name not in responses:
            raise AttributeError(name)

        def call(**kwargs):
            return etree.fromstring(responses[name])

        return call


class FakeDevice:
    def __init__(self, failing=()):
        self.facts = {"hostname": "MX1-POP1", "model": "MX204", "version": "21.4R3-S4"}
        self.rpc = FakeRpc(failing)


def test_capture_without_inventory_has_device_scope_and_no_ping():
    snapshot = capture_device(FakeDevice(), "172.20.20.4", now=NOW)

    assert snapshot.inventory is None
    assert snapshot.scopes == []
    assert snapshot.probes["ping"] == []
    assert snapshot.facts["interfaces"]["ge-0/0/2.113"]["input_pps"] == 412


def test_capture_with_inventory_builds_scopes_and_pings():
    inventory = load_inventory("172.20.20.4.yml")
    device = FakeDevice()

    snapshot = capture_device(device, "172.20.20.4", inventory=inventory, now=NOW)

    assert snapshot.scopes
    assert snapshot.inventory is not None
    assert any(probe["target"] == "198.11.13.2" for probe in snapshot.probes["ping"])
    assert device.rpc.ping_calls


def test_failed_collector_is_recorded_and_capture_continues():
    snapshot = capture_device(
        FakeDevice(failing=("get_bgp_neighbor_information",)), "172.20.20.4", now=NOW
    )

    assert snapshot.capture.collectors["interfaces"]["status"] == "ok"
    assert snapshot.capture.collectors["bgp"]["status"] == "error"
    assert "selhalo" in snapshot.capture.collectors["bgp"]["message"]
    assert snapshot.facts["bgp"] == {}


def test_failed_collector_shows_up_as_skip_in_evaluate():
    from migration_validator import api
    from migration_validator.models.result import Status

    inventory = load_inventory("172.20.20.4.yml")
    snapshot = capture_device(
        FakeDevice(failing=("get_arp_table_information",)),
        "172.20.20.4",
        inventory=inventory,
        now=NOW,
    )

    result = api.evaluate(snapshot, now=NOW)
    arp_checks = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "arp_present"
    ]
    assert arp_checks
    assert all(check.status is Status.SKIP for check in arp_checks)


def test_collector_subset_can_be_selected():
    snapshot = capture_device(
        FakeDevice(), "172.20.20.4", collector_names=["interfaces"], now=NOW
    )

    assert set(snapshot.capture.collectors) == {"interfaces"}
    assert "bgp" not in snapshot.facts


def test_record_raw_writes_xml(tmp_path):
    capture_device(FakeDevice(), "172.20.20.4", now=NOW, record_raw=tmp_path)

    assert (tmp_path / "junos" / "interfaces.xml").exists()


def test_snapshot_round_trips_to_disk(tmp_path):
    from migration_validator.models.snapshot import load_snapshot, save_snapshot

    snapshot = capture_device(FakeDevice(), "172.20.20.4", now=NOW)
    path = tmp_path / "snap.json"
    save_snapshot(snapshot, path)

    assert load_snapshot(path) == snapshot


def test_unknown_collector_name_is_rejected():
    with pytest.raises(ValueError, match="neznamy collector"):
        capture_device(FakeDevice(), "172.20.20.4", collector_names=["nope"], now=NOW)
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/test_capture.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.capture'`

- [ ] **Step 3: Implementuj orchestraci**

Vytvoř `migration_validator/capture.py`:

```python
"""Orchestrace fazi capture.

Poradi: bulk collectory -> scopy -> resolve ping cilu -> ping probe -> freeze.
Zavislost ARP -> ping se odehrava cela tady, takze checky uz jsou navzajem
nezavisle.

Selhani jednoho collectoru nezrusi capture - zapise se do capture.collectors
a checky, ktere tu oblast potrebuji, dostanou SKIP.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import etree

import migration_validator.collectors.all  # noqa: F401  (registrace)
from migration_validator.collectors.base import CollectorError
from migration_validator.collectors.registry import all_collectors, collectors_for
from migration_validator.connection.junos import detect_platform, device_meta
from migration_validator.models.inventory import Inventory
from migration_validator.models.snapshot import CaptureMeta, Snapshot
from migration_validator.probes.ping import DEFAULT_COUNT, resolve_targets, run_ping
from migration_validator.scoping.builder import build_scopes

LIST_AREAS = frozenset({"arp"})


def _empty_for(area: str) -> Any:
    """Prazdna hodnota spravneho typu pro oblast, jejiz collector selhal."""
    return [] if area in LIST_AREAS else {}


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _select_collectors(platform: str, names: list[str] | None):
    available = collectors_for(platform)
    if names is None:
        return available

    known = {collector.name for collector in all_collectors()}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ValueError(f"neznamy collector: {', '.join(unknown)}")

    return [collector for collector in available if collector.name in names]


def _record(xml_root: Path, platform: str, name: str, device: Any, collector) -> None:
    """Ulozi syrove RPC XML pro pozdejsi pouziti jako fixture."""
    target = Path(xml_root) / platform
    target.mkdir(parents=True, exist_ok=True)
    try:
        xml = getattr(device.rpc, collector.rpc_name(platform))(
            **collector.rpc_kwargs(platform)
        )
    except Exception:  # noqa: BLE001 - nahravani je best effort
        return
    (target / f"{name}.xml").write_bytes(etree.tostring(xml, pretty_print=True))


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
) -> Snapshot:
    started_at = now or _timestamp()
    platform = detect_platform(device)

    selected = _select_collectors(platform, collector_names)

    facts: dict[str, Any] = {}
    status: dict[str, dict[str, Any]] = {}

    for collector in selected:
        if record_raw is not None:
            _record(Path(record_raw), platform, collector.name, device, collector)
        try:
            facts[collector.name] = collector.collect(device, platform)
            status[collector.name] = {"status": "ok"}
        except CollectorError as error:
            # Oblast zustane prazdna se spravnym typem - check ji uvidi jako
            # chybejici a diky failed_collectors() vrati SKIP, nikdy PASS.
            facts[collector.name] = _empty_for(collector.name)
            status[collector.name] = {"status": "error", "message": str(error)}

    scopes = build_scopes(inventory) if inventory is not None else []

    pings: list[dict[str, Any]] = []
    if scopes:
        for target in resolve_targets(scopes, facts.get("arp", [])):
            pings.append(run_ping(device, target, count=ping_count))

    return Snapshot(
        device=device_meta(device, address),
        capture=CaptureMeta(
            started_at=started_at,
            finished_at=now or _timestamp(),
            phase=phase,
            collectors=status,
        ),
        facts=facts,
        probes={"ping": pings},
        scopes=scopes,
        inventory=inventory.entries if inventory is not None else None,
    )
```

- [ ] **Step 4: Doplň `capture()` do API**

V `migration_validator/api.py` přidej importy a funkci:

```python
from migration_validator.capture import capture_device
from migration_validator.connection.junos import ConnectionOptions, connect
from migration_validator.models.inventory import Inventory, load_inventory


def capture(
    host: str,
    *,
    inventory: Inventory | str | None = None,
    options: ConnectionOptions | None = None,
    collectors: list[str] | None = None,
    phase: str | None = None,
    ping_count: int = 5,
    record_raw: str | None = None,
) -> Snapshot:
    """Sebere stav zarizeni a vrati self-contained snapshot."""
    if isinstance(inventory, str):
        inventory = load_inventory(inventory)

    options = options or ConnectionOptions(host=host)
    with connect(options) as device:
        return capture_device(
            device,
            address=host,
            inventory=inventory,
            collector_names=collectors,
            phase=phase,
            ping_count=ping_count,
            record_raw=record_raw,
        )
```

> **Poznámka k `record_raw`:** nahrávání spustí RPC podruhé (jednou pro uložení XML, jednou pro
> `collect()`). Je to záměrná jednoduchost — `--record-raw` se používá jen při ladění parserů,
> ne v běžném provozu.

- [ ] **Step 5: Doplň podpříkaz `capture` do CLI**

V `migration_validator/cli.py` přidej handler:

```python
def _cmd_capture(args: argparse.Namespace) -> int:
    from migration_validator.models.snapshot import save_snapshot

    try:
        snapshot = api.capture(
            args.device,
            inventory=args.inventory,
            options=_connection_options(args),
            collectors=args.collectors.split(",") if args.collectors else None,
            phase=args.phase,
            ping_count=args.ping_count,
            record_raw=args.record_raw,
        )
    except JunosConnectionError as error:
        raise ToolError(str(error)) from error

    save_snapshot(snapshot, args.output)
    print(f"snapshot ulozen: {args.output}")

    failed = snapshot.capture.failed_collectors()
    for name, message in failed.items():
        print(f"  varovani: collector '{name}' selhal - {message}", file=sys.stderr)

    return EXIT_OK
```

A registraci v `build_parser()`:

```python
    capture = sub.add_parser("capture", help="sebere stav zarizeni do snapshotu")
    capture.add_argument("--device", required=True)
    capture.add_argument("--inventory")
    capture.add_argument("--phase")
    capture.add_argument("--output", required=True)
    capture.add_argument("--collectors", help="carkou oddeleny seznam")
    capture.add_argument("--ping-count", type=int, default=5)
    capture.add_argument("--record-raw")
    _add_auth_arguments(capture)
    capture.set_defaults(func=_cmd_capture)
```

- [ ] **Step 6: Spusť test**

Run: `.venv/bin/pytest tests/test_capture.py -v`
Expected: PASS, 8 testů

- [ ] **Step 7: Spusť celou sadu**

Run: `.venv/bin/pytest -q`
Expected: všechny testy PASS

- [ ] **Step 8: Ověř celý řetěz proti laborce**

```bash
.venv/bin/mig-validate capture --device 172.20.20.4 \
    --inventory 172.20.20.4.yml --phase pre-migration \
    --output runs/lab/pre/172.20.20.4.json

.venv/bin/mig-validate capture --device 172.20.20.5 \
    --inventory 172.20.20.5.yml --phase post-migration \
    --output runs/lab/post/172.20.20.5.json

.venv/bin/mig-validate evaluate \
    --snapshot runs/lab/post/172.20.20.5.json \
    --baseline runs/lab/pre/172.20.20.4.json
```

Expected: oba snapshoty vzniknou, `evaluate` vypíše tabulku služeb i sekci `NESPAROVANO`.

- [ ] **Step 9: Commit**

```bash
git add migration_validator/capture.py migration_validator/api.py \
        migration_validator/cli.py tests/test_capture.py
git commit -m "feat: add capture orchestration, API and CLI subcommand"
```

---

## Hotovo po Plánu 2

Kompletní workflow ze specu:

```bash
# krok 2 a 3 - stary box pred migraci
mig-validate capture  --device 172.20.20.4 --inventory 172.20.20.4.yml \
                      --phase pre-migration --output runs/mig01/pre/172.20.20.4.json
mig-validate evaluate --snapshot runs/mig01/pre/172.20.20.4.json --format text

# krok 5 a 6 - novy box po migraci
mig-validate capture  --device 172.20.20.5 --inventory 172.20.20.5.yml \
                      --phase post-migration --output runs/mig01/post/172.20.20.5.json
mig-validate evaluate --snapshot  runs/mig01/post/172.20.20.5.json \
                      --baseline  runs/mig01/pre/172.20.20.4.json \
                      --mapping   mapping.yml \
                      --format json --output runs/mig01/post.result.json

# krok 8 - filtrovani
mig-validate evaluate --snapshot post.json --baseline pre.json --filter CPE13 --status fail,warn

# ladeni parovani
mig-validate match --baseline pre.json --subject post.json
```

## Co se při implementaci ukázalo jinak (2026-07-27)

Plán byl proveden celý. Tyhle věci se ale proti laborce ukázaly jinak, než jak byly napsané —
zapsané tady, aby se na ně nemuselo přicházet znovu:

1. **RPC názvy v Tasku 5 neexistovaly na žádné z platforem.** Správně
   `get_evpn_vpws_information` (ne `get_evpn_vpws_instance_information`) a
   `get_mac_vrf_mac_table` (ne `get_mac_vrf_forwarding_mac_table`).
   Ověřovací snippet v Tasku 5 Kroku 1 navíc nefunguje: `hasattr(dev.rpc, name)` vrací `True`
   pro libovolné jméno, protože PyEZ RPC metody generuje dynamicky. RPC se musí zkusit zavolat.
2. **ESI bloky jsou v odpovědi jen s `extensive`.** Bez něj collector tiše vrací prázdno.
3. **MAC tabulka potřebuje na MX dvě RPC.** `show bridge mac-table` vidí jen vlan-aware instance,
   `show evpn mac-table` jen vlan-based. Na EVO stačí jedno (`mac-vrf`), a `show evpn mac-table`
   tam vůbec neexistuje. Řeší se přepsáním `collect()` v `EvpnMacCollector`, ne zásahem do base.
4. **Vnitřním klíčem `evpn_mac` je VLAN id, ne název domény** — viz upřesnění kontraktu ve specu.
5. **Dva checky z Plánu 1 byly proti realitě špatně** a implementace je opravila:
   `evpn_vpws_status` bral `local_sid != remote_sid` jako BROKEN, ačkoliv `local 1000; remote 2000`
   je správná konfigurace EVPN-VPWS; a stav se porovnával na přesnou rovnost s `"Up"`, zatímco
   ESI hlásí `Up/Forwarding`.
6. **`InterfacesCollector` musí emitovat `framing_errors`.** Kontrakt ve specu ho předepisuje a
   `checks/ifaces.py` ho čte, ale v Tasku 3 chyběl — a protože ho check bere jako nepovinný,
   žádný test by to nechytil.
7. **`parse_ping_result` musí rozlišit dva druhy neúspěchu.** `no response` má platné summary
   (výsledek měření), zatímco `internal error` + `bind: Can't assign requested address` summary
   nemá vůbec a původní parser ho vracel jako čistou nulu.
8. **vMX občas vrátí poškozené XML z ping RPC** (`<ping-results>` bez uzavíracího tagu). Je to
   přechodné a `run_ping` to odchytí; argument `routing_instance` s tím nesouvisí, ten RPC bere.
9. **Conformance testy nebyly v žádném kroku**, i když je Global Constraints vyžadují. Doplněny
   jako `tests/collectors/test_conformance.py` a ověřeny mutací (překlíčování `evpn_mac` na
   rozhraní shodí tři testy místo tichého SKIP).
10. **Collector s více RPC potřebuje `rpc_names()`.** `record` i `--record-raw` původně ukládaly
    jen `rpc_name()`, takže fixture `evpn_mac.xml` na MX obsahovala pouze vlan-aware instance —
    a kdokoliv by fixtures regeneroval dokumentovaným postupem, tu mezeru zreprodukuje, aniž by
    si toho všiml. Base má proto `rpc_names()` (default jednoprvková) a nahrávání ukládá druhé
    a další RPC jako `evpn_mac.2.xml`. Conformance test je při skládání faktů slučuje, aby
    testoval to, co capture opravdu sbírá.
11. **Fixtures se nahrávají po celých sadách, ne po oblastech.** Sada pro `junos` byla chvíli
    míchaná ze dvou okamžiků (rozhraní a ARP z doby před migrací, EVPN po ní). Na tvrzení o
    schématu a klíčování to nevadí, ale hodnotové porovnání se na takové sadě postavit nedá.
    Po doběhnutí Tasku 7 byla proto celá `junos` sada nahrána znovu jedním průchodem
    `mig-validate record`.

## Otevřené body pro pozdější iterace

Nejsou to nedodělky tohoto plánu — jsou to vědomě odložená rozhodnutí ze specu:

1. **Sada checků pro `Core`** je zatím jen `interface_state` + countery. Kandidáti na doplnění: ISIS adjacency, stav MPLS/LDP. Nebyly v původním zadání.
2. **Tolerance se doladí podle provozu** — `-60 %` u datovosti, `-10 %` u BGP prefixů jsou úvodní odhady.
3. **Refaktoring `mx_parser.py` / `evo_parser.py`** do sdíleného balíku — samostatné rozhodnutí, viz AR-8 ve specu.
4. **Převod existujících JSNAPy testů** do collector + check modelu.

