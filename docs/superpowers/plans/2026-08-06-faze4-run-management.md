# Fáze 4 — Run management + ping z baseline ARP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrace dostane jednotný run adresář `runs/<nazev>/` s manifestem
`run.yml` (zařízení, párování portů, evidence snímků), CLI naučí
`capture/evaluate/status --run`, parsery `mx_parser.py`/`evo_parser.py` se
sjednotí do `migration_validator/parsers/` (a `capture --parse-services`
umí inventory vyrobit samo) a ping při `--phase post` odvozuje cíle z ARP/ND
spárovaného pre snímku.

**Architecture:** Nový subpackage `migration_validator/runs/` — `manifest.py`
(datový model run.yml + load/save) a `store.py` (cesty a názvy souborů v run
adresáři). CLI handlery zůstávají tenké: run režim jen dohledá/vyrobí cesty
a deleguje do stávajícího `api.capture`/`api.evaluate`. Parsery: společné
jádro `parsers/core.py` (byte-identických ~2530 řádků obou skriptů) + dvě
malé platformní podtřídy (`mx.py`, `evo.py` — 3 metody rozdílu); kořenové
skripty zůstanou jako tenké wrappery. Ping: `resolve_targets` dostane
nepovinné baseline ARP/ND a vybírá z nich cíle podle příslušnosti do subnetů
scope (jména rozhraní starého boxu se na nový nedají mapovat, subnet ano).

**Tech Stack:** Python 3, dataclasses, PyYAML, PyEZ (`jnpr.junos`), pytest.
Žádné nové závislosti.

## Global Constraints

- Schema **snapshotu zůstává 7**, schema **inventory zůstává 5** — fáze 4
  žádné z nich nemění. `run.yml` je nový formát a dostane vlastní
  `schema_version: 1` (precedens repa: každý formát je verzovaný a loader
  cizí verzi tvrdě odmítá).
- Implementace podporuje **1 starý + 1 nový box**. Schéma run.yml je
  připravené na víc boxů a na `l2_switch` pod `new` — loader je čte a
  save je zachová beze změny, ale žádná logika je nepoužívá (fáze 5).
- Porty v názvech souborů normalizované: `ge-0/0/0` → `ge_0_0_0`
  (nahrazení `-` a `/` za `_`).
- Celoboxový režim (bez `--port`) zůstává plnohodnotný; per-port režim je
  doplněk. Stávající `evaluate --snapshot/--baseline` a `capture --output`
  zůstávají beze změny (zpětná kompatibilita).
- `runs/` je v `.gitignore` — kód nesmí předpokládat, že v repu něco je;
  testy pracují výhradně v `tmp_path`.
- Komentáře a hlášky česky bez diakritiky (konvence repa), komentáře jen
  tam, kde kód sám neřekne proč.
- Testy `.venv/bin/pytest` z kořene repa. Výchozí stav na `main`:
  **781 passed / 1 skipped** — číslo si ověř před začátkem (memory říká,
  že platí k 2026-08-06).
- Pracuj na větvi `faze4-run-management` založené z `main`. Necommitnuté
  změny `172.20.20.4.yml` / `172.20.20.5.yml` v pracovním stromě jsou
  přegenerovaný stav laborky — nech je být, do commitů fáze 4 nepatří.
- CLI exit kódy: `EXIT_OK=0`, `EXIT_FAILED_CHECKS=1`, `EXIT_TOOL_ERROR=2`
  (`cli.py:34-36`); chyby uživatele hlásit přes `ToolError` (`cli.py:39`).

---

### Task 1: Manifest run.yml — `migration_validator/runs/manifest.py`

**Files:**
- Create: `migration_validator/runs/__init__.py` (prázdný)
- Create: `migration_validator/runs/manifest.py`
- Test: `tests/runs/__init__.py` (prázdný), `tests/runs/test_manifest.py`

**Interfaces:**
- Consumes: nic z ostatních tasků (jen PyYAML, dataclasses).
- Produces (Task 2, 4, 6 importují z `migration_validator.runs.manifest`):
  - `RUN_SCHEMA_VERSION = 1`
  - `normalize_port(port: str) -> str` — `ge-0/0/0` → `ge_0_0_0`
  - `@dataclass RunDevice: host: str; platform: str; role: str`
    (role ∈ `old|new|l2-switch`, loader jinou hodnotu odmítne `ValueError`)
  - `@dataclass MappingEndpoint: node: str; port: str;
    l2_switch: dict[str, Any] | None = None` (l2_switch jen průchozí data)
  - `@dataclass InterfaceMapping: old: MappingEndpoint; new: MappingEndpoint`
  - `@dataclass CaptureRecord: phase: str; device: str; port: str | None;
    snapshot: str; taken: str` (`port=None` = celoboxový režim `all`)
  - `@dataclass RunManifest: devices: dict[str, RunDevice];
    interface_mapping: list[InterfaceMapping];
    captures: list[CaptureRecord]` s metodami:
    - `node_for_host(host: str) -> str | None`
    - `device_with_role(role: str) -> tuple[str, RunDevice] | None`
      (první v pořadí vložení; víc zařízení téže role → `ValueError`,
      viz omezení 1+1)
    - `paired_old(new_node: str, new_port: str) -> MappingEndpoint | None`
      (přes `interface_mapping`; víc old záznamů na tentýž new port
      (LAG přes EX) → `None`, fáze 4 pár nedohledá)
    - `paired_new(old_node: str, old_port: str) -> MappingEndpoint | None`
    - `add_mapping(old: MappingEndpoint, new: MappingEndpoint) -> None`
      (idempotentní — shodná dvojice se nepřidá podruhé; stejný old
      s jiným new → `ValueError`)
    - `record_capture(record: CaptureRecord) -> None` (záznam se shodným
      `(phase, device, port)` nahradí, jinak append)
    - `find_capture(phase: str, device: str, port: str | None)
      -> CaptureRecord | None` (přesná shoda port vs. None)
  - `load_manifest(path: Path) -> RunManifest` (`ValueError` na cizí
    `schema_version` nebo neplatnou roli; chybějící sekce = prázdné)
  - `save_manifest(manifest: RunManifest, path: Path) -> None`
    (`yaml.safe_dump`, `allow_unicode=True`, `sort_keys=False`,
    `schema_version` první klíč; vytvoří rodičovské adresáře)

- [ ] **Step 1: Napiš failing testy**

Vytvoř `tests/runs/test_manifest.py`:

```python
"""Model run.yml - jediny zdroj pravdy o migraci."""

import pytest

from migration_validator.runs.manifest import (
    CaptureRecord,
    InterfaceMapping,
    MappingEndpoint,
    RunDevice,
    RunManifest,
    load_manifest,
    normalize_port,
    save_manifest,
)


def _manifest():
    return RunManifest(
        devices={
            "MX1-POP1": RunDevice(host="172.20.20.4", platform="junos", role="old"),
            "PTX1-POP1": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="new"
            ),
        },
        interface_mapping=[
            InterfaceMapping(
                old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/0"),
                new=MappingEndpoint(node="PTX1-POP1", port="et-0/0/0"),
            )
        ],
        captures=[],
    )


def test_normalize_port():
    assert normalize_port("ge-0/0/0") == "ge_0_0_0"
    assert normalize_port("ae0") == "ae0"


def test_roundtrip_preserves_l2_switch(tmp_path):
    manifest = _manifest()
    manifest.interface_mapping.append(
        InterfaceMapping(
            old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/1"),
            new=MappingEndpoint(
                node="PTX1-POP1",
                port="ae0",
                l2_switch={
                    "node": "EX1-POP1",
                    "ae_port": "ae0",
                    "access_port": "ge-0/0/0",
                },
            ),
        )
    )
    path = tmp_path / "run.yml"
    save_manifest(manifest, path)
    loaded = load_manifest(path)
    assert loaded == manifest
    assert loaded.interface_mapping[1].new.l2_switch["node"] == "EX1-POP1"


def test_load_rejects_foreign_schema(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text("schema_version: 99\ndevices: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_manifest(path)


def test_load_rejects_unknown_role(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: modern}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="role"):
        load_manifest(path)


def test_node_for_host_and_roles():
    manifest = _manifest()
    assert manifest.node_for_host("172.20.20.4") == "MX1-POP1"
    assert manifest.node_for_host("1.1.1.1") is None
    assert manifest.device_with_role("old")[0] == "MX1-POP1"
    assert manifest.device_with_role("l2-switch") is None


def test_device_with_role_rejects_duplicates():
    manifest = _manifest()
    manifest.devices["MX2-POP1"] = RunDevice(
        host="172.20.20.6", platform="junos", role="old"
    )
    with pytest.raises(ValueError, match="old"):
        manifest.device_with_role("old")


def test_pairing_both_directions():
    manifest = _manifest()
    assert manifest.paired_old("PTX1-POP1", "et-0/0/0").port == "ge-0/0/0"
    assert manifest.paired_new("MX1-POP1", "ge-0/0/0").port == "et-0/0/0"
    assert manifest.paired_old("PTX1-POP1", "et-0/0/9") is None


def test_paired_old_ambiguous_lag_returns_none():
    manifest = _manifest()
    for old_port in ("ge-0/0/1", "ge-0/0/2"):
        manifest.add_mapping(
            MappingEndpoint(node="MX1-POP1", port=old_port),
            MappingEndpoint(node="PTX1-POP1", port="ae0"),
        )
    assert manifest.paired_old("PTX1-POP1", "ae0") is None


def test_add_mapping_idempotent_and_conflicting():
    manifest = _manifest()
    manifest.add_mapping(
        MappingEndpoint(node="MX1-POP1", port="ge-0/0/0"),
        MappingEndpoint(node="PTX1-POP1", port="et-0/0/0"),
    )
    assert len(manifest.interface_mapping) == 1
    with pytest.raises(ValueError, match="ge-0/0/0"):
        manifest.add_mapping(
            MappingEndpoint(node="MX1-POP1", port="ge-0/0/0"),
            MappingEndpoint(node="PTX1-POP1", port="et-0/0/5"),
        )


def test_record_capture_replaces_same_key():
    manifest = _manifest()
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "a.json", "T1")
    )
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", "ge-0/0/0", "b.json", "T2")
    )
    manifest.record_capture(CaptureRecord("pre", "MX1-POP1", None, "c.json", "T3"))
    assert len(manifest.captures) == 2
    assert manifest.find_capture("pre", "MX1-POP1", "ge-0/0/0").snapshot == "b.json"
    assert manifest.find_capture("pre", "MX1-POP1", None).snapshot == "c.json"
    assert manifest.find_capture("post", "MX1-POP1", None) is None
```

- [ ] **Step 2: Ověř, že testy padají**

Run: `.venv/bin/pytest tests/runs/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: migration_validator.runs`

- [ ] **Step 3: Implementuj `manifest.py`**

Dataclasses a funkce přesně dle Interfaces výše. Poznámky k implementaci:

```python
"""Model run.yml - jediny zdroj pravdy o migraci (faze 4 roadmapy)."""

RUN_SCHEMA_VERSION = 1
VALID_ROLES = frozenset({"old", "new", "l2-switch"})


def normalize_port(port: str) -> str:
    return port.replace("-", "_").replace("/", "_")
```

- `load_manifest`: `yaml.safe_load`; když soubor není mapping nebo
  `schema_version != RUN_SCHEMA_VERSION` → `ValueError` s cestou a
  nalezenou verzí. `devices`/`interface_mapping`/`captures` chybějící →
  prázdné. `MappingEndpoint` z dictu: `l2_switch` ulož tak, jak přišel
  (dict), nevaliduj obsah.
- `save_manifest`: serializuj do dictů v pořadí `schema_version`,
  `devices`, `interface_mapping`, `captures`; u endpointu vynech
  `l2_switch` když je `None`, u capture zapiš `port: all` když je `None`
  a při čtení `"all"` → `None` (soubor je čitelný pro člověka, port
  `all` je srozumitelnější než chybějící klíč).
- `device_with_role`: kandidáty spočítej; 0 → `None`, 1 → dvojice
  `(node, device)`, víc → `ValueError` („faze 4 podporuje jeden box role
  X“).

- [ ] **Step 4: Ověř, že testy prochází**

Run: `.venv/bin/pytest tests/runs/test_manifest.py -v`
Expected: PASS (všech 9)

- [ ] **Step 5: Celá sada + commit**

Run: `.venv/bin/pytest`
Expected: výchozí počet + 9 passed

```bash
git add migration_validator/runs/ tests/runs/
git commit -m "feat: faze4 task1 - model run.yml (RunManifest, load/save, parovani portu)"
```

---

### Task 2: Run adresář — `migration_validator/runs/store.py`

**Files:**
- Create: `migration_validator/runs/store.py`
- Test: `tests/runs/test_store.py`

**Interfaces:**
- Consumes: `RunManifest`, `load_manifest`, `save_manifest`,
  `normalize_port` z Task 1.
- Produces (Task 4-6 importují z `migration_validator.runs.store`):
  - `@dataclass RunStore: root: Path; name: str` s:
    - `dir` (property) → `root / name`
    - `manifest_path` (property) → `dir / "run.yml"`
    - `load() -> RunManifest` — neexistující run.yml → prázdný
      `RunManifest` (run vzniká prvním capture)
    - `save(manifest: RunManifest) -> None`
    - `inventory_path(node: str, port: str | None) -> Path`
      → `dir / f"inventory_{node}_{normalize_port(port) if port else 'all'}.yml"`
    - `snapshot_path(phase: str, node: str, port: str | None) -> Path`
      → `dir / f"snapshot_{phase}_{node}_{...}.json"` (stejné pravidlo)
    - `snapshot_name(phase, node, port) -> str` (jen basename — to se
      ukládá do `CaptureRecord.snapshot`)
    - `missing_snapshots(manifest: RunManifest) -> list[str]` — basenames
      z `captures`, jejichž soubor v `dir` neexistuje (evaluate tím
      ověřuje manifest)

- [ ] **Step 1: Napiš failing testy**

Vytvoř `tests/runs/test_store.py`:

```python
"""Cesty a nazvy souboru v runs/<nazev>/."""

from pathlib import Path

from migration_validator.runs.manifest import CaptureRecord, RunManifest
from migration_validator.runs.store import RunStore


def test_paths_and_naming(tmp_path):
    store = RunStore(root=tmp_path, name="mig01")
    assert store.dir == tmp_path / "mig01"
    assert store.manifest_path == tmp_path / "mig01" / "run.yml"
    assert (
        store.snapshot_path("pre", "MX1-POP1", "ge-0/0/0").name
        == "snapshot_pre_MX1-POP1_ge_0_0_0.json"
    )
    assert (
        store.snapshot_path("post", "PTX1-POP1", None).name
        == "snapshot_post_PTX1-POP1_all.json"
    )
    assert (
        store.inventory_path("PTX1-POP1", "et-0/0/0").name
        == "inventory_PTX1-POP1_et_0_0_0.yml"
    )


def test_load_missing_manifest_returns_empty(tmp_path):
    store = RunStore(root=tmp_path, name="novy")
    manifest = store.load()
    assert manifest.devices == {}
    assert manifest.captures == []


def test_save_and_load_roundtrip(tmp_path):
    store = RunStore(root=tmp_path, name="mig01")
    manifest = store.load()
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", None, "snapshot_pre_MX1-POP1_all.json", "T")
    )
    store.save(manifest)
    assert store.manifest_path.exists()
    assert store.load().find_capture("pre", "MX1-POP1", None) is not None


def test_missing_snapshots(tmp_path):
    store = RunStore(root=tmp_path, name="mig01")
    manifest = store.load()
    manifest.record_capture(
        CaptureRecord("pre", "MX1-POP1", None, "snapshot_pre_MX1-POP1_all.json", "T")
    )
    assert store.missing_snapshots(manifest) == ["snapshot_pre_MX1-POP1_all.json"]
    store.dir.mkdir(parents=True)
    (store.dir / "snapshot_pre_MX1-POP1_all.json").write_text("{}")
    assert store.missing_snapshots(manifest) == []
```

- [ ] **Step 2: Ověř fail** — Run: `.venv/bin/pytest tests/runs/test_store.py -v`,
  Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Implementuj `store.py`** dle Interfaces (jednoduché
  skládání cest, žádná další logika).

- [ ] **Step 4: Ověř pass** — Run: `.venv/bin/pytest tests/runs/ -v`, Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add migration_validator/runs/store.py tests/runs/test_store.py
git commit -m "feat: faze4 task2 - RunStore (cesty a nazvy souboru v runs/)"
```

---

### Task 3: Sjednocení parserů do `migration_validator/parsers/`

Podklad (změřeno diffem, ne odhad): `mx_parser.py` (2644 ř.) a
`evo_parser.py` (2645 ř.) jsou **byte-identické až na 9 hunků / 109
změněných řádků**. Skutečný platformní rozdíl jsou 3 metody:
`_detect_service` (VPLS větev, mx:1762-1780 vs evo:1762-1785),
`_detect_evpn_elan_subtype` (mx:1878-1944 vs evo:1883-1948, odlišný
algoritmus) a `_is_evpn_instance` (mx:1978-2001 vs evo:1982-2000);
zbytek jsou jen jméno třídy, docstring modulu a jméno loggeru.
`INVENTORY_SCHEMA_VERSION = 5` je dnes ve 3 kopiích (oba parsery +
`models/inventory.py:137`).

**Files:**
- Create: `migration_validator/parsers/__init__.py`
- Create: `migration_validator/parsers/core.py` (společné jádro)
- Create: `migration_validator/parsers/mx.py`, `migration_validator/parsers/evo.py`
- Modify: `mx_parser.py`, `evo_parser.py` (kořen — z 2644 řádků na tenké wrappery)
- Modify: `tests/parsers/test_bfd_config.py`, `test_family_split.py`,
  `test_inactive.py`, `test_static_routes.py` (importlib → normální importy)
- Test: `tests/parsers/test_package.py` (nový)

**Interfaces:**
- Consumes: `INVENTORY_SCHEMA_VERSION` z `migration_validator/models/inventory.py`.
- Produces (Task 5 importuje z `migration_validator.parsers`):
  - `JunosServiceParser` (MX), `JunosEvoAcxServiceParser` (EVO) —
    beze změny chování, `__init__(config_xml)`, `parse() -> list[InterfaceService]`
  - `parser_for_platform(platform: str) -> type` —
    `"junos"` → `JunosServiceParser`, `"junos-evo"` → `JunosEvoAcxServiceParser`,
    jiné → `ValueError`
  - `retrieve_configuration(device) -> etree._Element`,
    `create_yaml_data(hostname, services)`, `write_yaml(data, output_path)`
    (reexport z `core`)

- [ ] **Step 1: Vytvoř `parsers/core.py` z `mx_parser.py`**

`git mv` nepoužívej (kořenový soubor zůstává). Postup:

1. Zkopíruj `mx_parser.py` do `migration_validator/parsers/core.py`.
2. Přejmenuj třídu na `JunosServiceParserCore` a udělej z ní bázi:
   - `LOGGER` nahraď třídním atributem: `LOGGER_NAME = "junos-service-parser"`
     a v `__init__` `self._logger = logging.getLogger(self.LOGGER_NAME)`;
     všechna použití modulového `LOGGER` uvnitř třídy přepiš na
     `self._logger`. Modulový `LOGGER` ponech pro funkce mimo třídu
     (`retrieve_configuration`, `main`, ...) pod jménem
     `junos-service-parser`.
   - Tři platformní metody nahraď `raise NotImplementedError`:
     `_detect_vpls(self, instance_type, protocol_set, instance)`
     (nová úzká metoda — viz Step 2), `_detect_evpn_elan_subtype(...)`,
     `_is_evpn_instance(...)`. Signatury zachovej dle původních metod.
   - V `_detect_service` nahraď původní VPLS podmínku voláním
     `self._detect_vpls(instance_type, protocol_set, instance)`
     (vrací `bool`); reason string ať staví podtřída — viz Step 2.
3. Smaž `INVENTORY_SCHEMA_VERSION = 5` a nahraď importem
   `from migration_validator.models.inventory import INVENTORY_SCHEMA_VERSION`.
4. `parse_arguments`/`main` parametrizuj třídou: `main(argv=None, *,
   parser_cls=None)`; `parser_cls=None` → chyba s pokynem použít
   platformní wrapper. Zbytek `main` beze změny.

- [ ] **Step 2: Vytvoř podtřídy `mx.py` a `evo.py`**

`migration_validator/parsers/mx.py` — přenes tělo metod **beze změny**
z `mx_parser.py` (řádky viz podklad výše):

```python
"""MX (Junos) platformni cast parseru sluzeb."""

from migration_validator.parsers.core import JunosServiceParserCore


class JunosServiceParser(JunosServiceParserCore):
    LOGGER_NAME = "junos-service-parser"

    # tela nasledujicich metod prenes beze zmeny z mx_parser.py:
    # _detect_vpls        <- VPLS vetev _detect_service (mx_parser.py:1762-1780)
    # _detect_evpn_elan_subtype  <- mx_parser.py:1878-1944
    # _is_evpn_instance   <- mx_parser.py:1978-2001
```

`migration_validator/parsers/evo.py` totéž s
`class JunosEvoAcxServiceParser(JunosServiceParserCore)`,
`LOGGER_NAME = "junos-evo-acx-service-parser"` a těly z
`evo_parser.py:1762-1785`, `1883-1948`, `1982-2000`.

Pozor: EVO `_detect_evpn_elan_subtype` volá `_contains_vlan_range_or_multiple`
a čte `RoutingInstance.evpn_service_type` — obojí je v core (společné),
jen MX to nepoužívá. Nic nemazat.

`migration_validator/parsers/__init__.py`:

```python
"""Sjednocene parsery konfigurace (MX + EVO/ACX), spolecne jadro v core."""

from migration_validator.parsers.core import (
    create_yaml_data,
    retrieve_configuration,
    write_yaml,
)
from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

_PLATFORM_PARSERS = {
    "junos": JunosServiceParser,
    "junos-evo": JunosEvoAcxServiceParser,
}


def parser_for_platform(platform: str):
    try:
        return _PLATFORM_PARSERS[platform]
    except KeyError:
        raise ValueError(f"nepodporovana platforma parseru: {platform}") from None
```

- [ ] **Step 3: Kořenové skripty jako wrappery**

`mx_parser.py` (celý obsah nahraď):

```python
#!/usr/bin/env python3
"""Tenky wrapper - implementace zije v migration_validator/parsers/."""

import sys

from migration_validator.parsers.core import main
from migration_validator.parsers.mx import JunosServiceParser

if __name__ == "__main__":
    sys.exit(main(parser_cls=JunosServiceParser))
```

`evo_parser.py` obdobně s `JunosEvoAcxServiceParser`. Wrapper musí dál
fungovat spuštěním `python mx_parser.py <host> -o out.yml` z kořene repa
(pythonpath `.` — `pyproject.toml` už nastavuje pro pytest; pro přímé
spuštění z kořene funguje díky cwd).

- [ ] **Step 4: Přepni testy z importlib na importy**

Ve všech čtyřech modulech `tests/parsers/` nahraď `importlib.util` blok:

```python
from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

PARSERS = (
    pytest.param(JunosEvoAcxServiceParser, id="evo"),
    pytest.param(JunosServiceParser, id="mx"),
)
```

a parametry testů uprav z `(module, parser_cls)` na samotné `parser_cls`
(moduly už nejsou potřeba — helpery jako `normalize_vlan_values` importuj
z `migration_validator.parsers.core`, pokud je testy používaly).

- [ ] **Step 5: Nový test balíčku**

Vytvoř `tests/parsers/test_package.py`:

```python
"""Sjednoceni parseru - registry a jedna verze schematu."""

import pytest

from migration_validator.models.inventory import INVENTORY_SCHEMA_VERSION
from migration_validator.parsers import (
    JunosEvoAcxServiceParser,
    JunosServiceParser,
    parser_for_platform,
)
from migration_validator.parsers import core


def test_parser_for_platform():
    assert parser_for_platform("junos") is JunosServiceParser
    assert parser_for_platform("junos-evo") is JunosEvoAcxServiceParser
    with pytest.raises(ValueError, match="platform"):
        parser_for_platform("ios")


def test_single_schema_version_source():
    assert core.INVENTORY_SCHEMA_VERSION is INVENTORY_SCHEMA_VERSION
```

- [ ] **Step 6: Ověř celou sadu**

Run: `.venv/bin/pytest tests/parsers/ -v` a poté `.venv/bin/pytest`
Expected: všechny parser testy PASS v obou parametrizacích (`evo`, `mx`),
celá sada zelená. Regresní pojistka: `python - <<'EOF'` snippet, který
naparsuje `tests/fixtures/rpc/junos/…` není potřeba — pokrývají to
existující testy parametrizované přes obě třídy.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/parsers/ mx_parser.py evo_parser.py tests/parsers/
git commit -m "refactor: faze4 task3 - parsery sjednoceny do migration_validator/parsers (core + mx/evo podtridy)"
```

---

### Task 4: `capture --run` (bez --parse-services)

**Files:**
- Modify: `migration_validator/cli.py` (`_cmd_capture` u `cli.py:149`,
  parser u `cli.py:247-256`)
- Test: `tests/test_cli.py` (nové testy), využij `synthetic_snapshot`
  vzor ne — capture jde přes síť, mockni `api.capture` (viz Step 1)

**Interfaces:**
- Consumes: `RunStore` (Task 2), `RunManifest`, `RunDevice`,
  `CaptureRecord`, `MappingEndpoint` (Task 1); stávající `api.capture`
  a `save_snapshot` (`models/snapshot.py:136`).
- Produces: CLI kontrakt pro Task 5-7:
  - `mig-validate capture --run <nazev> --device <IP> --phase pre|post|rollback
    [--port ge-0/0/0] [--maps-to NODE:PORT] [--run-root runs]`
  - pravidla: s `--run` je `--output` zakázán (`ToolError`), `--phase`
    povinná a jen z {pre, post, rollback}; bez `--run` vše jako dnes
    (`--output` povinný, `--phase` volný text).
  - jméno uzlu: `manifest.node_for_host(device)`; není-li v manifestu,
    node = hodnota `--device` a do `devices` se přidá `RunDevice(host,
    platform z detekce, role: pre/rollback → old, post → new)`.
  - inventory: `--inventory` explicitně, jinak run soubor
    `inventory_<node>_<port|all>.yml`, jinak `ToolError` s hláškou
    `inventory nenalezena - spust s --parse-services` (flag přidá Task 5).
  - snapshot se uloží do `store.snapshot_path(phase, node, port)` a do
    manifestu přibude `CaptureRecord(..., taken=<capture ISO cas>)`;
    `--maps-to NODE:PORT` zapíše `add_mapping` (old/new strana podle
    role zařízení: post capture je `new`, maps-to ukazuje na `old`
    a naopak).

- [ ] **Step 1: Napiš failing testy**

Do `tests/test_cli.py` přidej sekci run režimu. Síť mockni monkeypatchem
`migration_validator.cli.api.capture` tak, aby vracel minimální
`Snapshot` (použij stejný stavební vzor jako `_write` helper v témže
souboru — snapshot se schema 7, prázdné facts, `capture.phase` dle
argumentu). Testy:

```python
def _fake_capture(monkeypatch, platform="junos"):
    calls = {}

    def fake(host, **kwargs):
        calls["host"] = host
        calls.update(kwargs)
        return _snapshot(host=host, platform=platform, phase=kwargs.get("phase"))

    monkeypatch.setattr("migration_validator.cli.api.capture", fake)
    return calls
```

(`_snapshot` = pomocná funkce vracející validní `Snapshot` objekt;
odvoď od stávajícího `_write` helperu, jen vrací objekt místo zápisu.)

1. `test_capture_run_rejects_output` — `--run X --output y.json` →
   exit 2, hláška o neslučitelnosti.
2. `test_capture_run_requires_known_phase` — `--run X --phase fialova` →
   exit 2.
3. `test_capture_run_creates_manifest_and_snapshot` — tmp run root
   (`--run-root`), `--run mig01 --device 172.20.20.4 --phase pre
   --inventory tests/fixtures/172.20.20.4.yml` → exit 0; existuje
   `runs_root/mig01/run.yml` s `devices["172.20.20.4"].role == "old"`
   a jedním `CaptureRecord(phase="pre", port=None,
   snapshot="snapshot_pre_172.20.20.4_all.json")`; soubor snapshotu
   existuje a jde načíst `load_snapshot`.
4. `test_capture_run_port_mode_and_maps_to` — pre capture s `--port
   ge-0/0/0`, pak post capture druhého zařízení s `--port et-0/0/0
   --maps-to 172.20.20.4:ge-0/0/0` → manifest má `interface_mapping`
   se správnou old/new orientací (old=172.20.20.4:ge-0/0/0,
   new=172.20.20.5:et-0/0/0) a post CaptureRecord.
5. `test_capture_run_missing_inventory_hint` — bez `--inventory` a bez
   souboru v run dir → exit 2, stderr obsahuje `--parse-services`.
6. `test_capture_run_second_capture_replaces_record` — dvakrát pre
   téhož zařízení/portu → v manifestu jeden záznam.

- [ ] **Step 2: Ověř fail** — Run:
  `.venv/bin/pytest tests/test_cli.py -k "capture_run" -v` — FAIL
  (neznámé argumenty).

- [ ] **Step 3: Implementace v `cli.py`**

- Parser capture: přidej `--run`, `--run-root` (default `"runs"`,
  `type=Path`, pomáhá testům a nestandardním umístěním), `--port`,
  `--maps-to`; `--output` změň na nepovinný a povinnost vynucuj ručně:
  bez `--run` chybějící `--output` → `parser.error(...)` ekvivalent
  přes `ToolError`; s `--run` zadaný `--output` → `ToolError`.
- `_cmd_capture` run větev (napiš jako privátní funkci
  `_capture_into_run(args) -> int`, ať `_cmd_capture` zůstane čitelný):
  1. `store = RunStore(args.run_root, args.run)`; `manifest = store.load()`.
  2. fáze mimo `{"pre", "post", "rollback"}` → `ToolError`.
  3. inventory dle pravidel v Interfaces (pro `--inventory` použij
     cestu přímo; run soubor hledej `store.inventory_path(node, port)`
     a fallback `store.inventory_path(node, None)`).
  4. zavolej `api.capture` jako dnešní větev (stejné auth/collector
     argumenty).
  5. node/role: host už v manifestu → node z něj; jinak přidej
     `RunDevice(host=args.device, platform=snapshot.device.platform,
     role={"pre": "old", "rollback": "old", "post": "new"}[phase])`
     pod klíčem `args.device`.
  6. `save_snapshot(snapshot, store.snapshot_path(phase, node, args.port))`,
     `manifest.record_capture(CaptureRecord(phase, node, args.port,
     store.snapshot_name(...), taken=snapshot.capture.taken))` —
     přesné jméno pole času ověř v `CaptureMeta` (`models/snapshot.py`).
  7. `--maps-to`: rozparsuj `NODE:PORT` (rsplit `:`; chybný tvar →
     `ToolError`); post → `add_mapping(old=MappingEndpoint(node_z_maps_to,
     port), new=MappingEndpoint(node, args.port))`; pre/rollback →
     zrcadlově. `--maps-to` bez `--port` → `ToolError` (pár je vždy
     per-port).
  8. `store.save(manifest)`; return `EXIT_OK`.

- [ ] **Step 4: Ověř pass** — Run:
  `.venv/bin/pytest tests/test_cli.py -k "capture_run" -v` — PASS;
  poté celá sada.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/cli.py tests/test_cli.py
git commit -m "feat: faze4 task4 - capture --run (run.yml, snapshoty do runs/<nazev>/)"
```

---

### Task 5: `capture --parse-services` (+ per-port inventory)

**Files:**
- Modify: `migration_validator/cli.py` (capture parser + run větev)
- Create: `migration_validator/runs/services.py` (generace inventory pro run)
- Test: `tests/runs/test_services.py`, rozšíření `tests/test_cli.py`

**Interfaces:**
- Consumes: `parser_for_platform`, `retrieve_configuration`,
  `create_yaml_data`, `write_yaml` (Task 3); `RunStore` (Task 2);
  `detect_platform` (`connection/junos.py:89`).
- Produces:
  - `generate_inventory(device, platform: str, output_path: Path,
    port: str | None = None) -> None` v `runs/services.py` — stáhne
    konfiguraci přes otevřené PyEZ `device`, vybere parser podle
    platformy, `parse()`, při `port` vyfiltruje služby, jejichž
    fyzické jméno rozhraní (část před tečkou) == `port`, a zapíše
    inventory YAML (`create_yaml_data` + `write_yaml`).
  - CLI flag `--parse-services` na capture: platí jen s `--run`
    (`ToolError` jinak); přeskočí se, když inventory soubor pro
    (node, port) už existuje (existující soubor má přednost, žádné
    tiché přepsání — vypiš info na stderr).

- [ ] **Step 1: Failing test na filtr portu**

`tests/runs/test_services.py` — parsni malý config XML (vezmi vstupní
vzor z `tests/parsers/test_family_split.py`, který už staví XML pro oba
parsery) a ověř:

```python
def test_generate_inventory_port_filter(tmp_path, monkeypatch):
    # monkeypatch retrieve_configuration, at vraci pripravene XML
    # se dvema sluzbami na ruznych fyzickych portech
    ...
    generate_inventory(object(), "junos", tmp_path / "inv.yml", port="ge-0/0/0")
    data = yaml.safe_load((tmp_path / "inv.yml").read_text())
    assert [s["interface"] for s in data["interfaces"]] == ["ge-0/0/0.100"]
    assert data["schema_version"] == 5
```

plus `test_generate_inventory_all_mode` (bez portu → obě služby) a
`test_generate_inventory_unknown_platform` (`ValueError`).
Monkeypatchuj `migration_validator.runs.services.retrieve_configuration`.

- [ ] **Step 2: Ověř fail** — `ModuleNotFoundError: ...runs.services`.

- [ ] **Step 3: Implementuj `runs/services.py`**

```python
"""Vyroba inventory do run adresare (capture --parse-services)."""

from pathlib import Path

from migration_validator.parsers import (
    create_yaml_data,
    parser_for_platform,
    retrieve_configuration,
    write_yaml,
)


def generate_inventory(device, platform, output_path: Path, port=None) -> None:
    config = retrieve_configuration(device)
    services = parser_for_platform(platform)(config).parse()
    if port is not None:
        services = [
            s for s in services if s.interface.split(".", 1)[0] == port
        ]
    write_yaml(create_yaml_data(_hostname(device), services), output_path)
```

(`_hostname`: vezmi `device.hostname` s fallbackem na prázdný řetězec —
ověř, co PyEZ `Device` nabízí; `create_yaml_data` signaturu viz
`parsers/core.py`. Pole `InterfaceService.interface` ověř v core —
pokud fyzický port drží jiné pole, použij ho.)

- [ ] **Step 4: CLI napojení + testy**

V `_capture_into_run`: když inventory soubor neexistuje a je
`--parse-services`, otevři spojení (stejný `connect()` kontext, který
používá capture — uspořádej tak, aby se konfigurace stáhla **v témže
spojení** jako snapshot, ne druhým loginem: `api.capture` přijímá
`options`; nejjednodušší je stáhnout inventory v samostatném krátkém
spojení před capture — rozhodnutí: **samostatné spojení**, `api.capture`
se nemění) a zavolej `generate_inventory(device,
detect_platform(device), store.inventory_path(node, port), port)`.
CLI testy: `test_capture_run_parse_services_generates_inventory`
(monkeypatch `generate_inventory` + `api.capture`; ověř, že se soubor
vyrobil / funkce dostala správnou cestu a port) a
`test_parse_services_requires_run`.

- [ ] **Step 5: Ověř pass + celá sada** — `.venv/bin/pytest` zelené.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/runs/services.py migration_validator/cli.py tests/
git commit -m "feat: faze4 task5 - capture --parse-services vyrabi inventory do runs/"
```

---

### Task 6: `evaluate --run` + `status --run`

**Files:**
- Modify: `migration_validator/cli.py` (`_cmd_evaluate` u `cli.py:62`,
  parser u `:220-235`; nový subcommand `status`)
- Create: `migration_validator/runs/pairing.py`
- Test: `tests/runs/test_pairing.py`, rozšíření `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1+2; `api.evaluate`, `load_snapshot`, `filter_result`,
  `render`, `to_json`.
- Produces:
  - `@dataclass Evaluation: subject: CaptureRecord;
    baseline: CaptureRecord | None; reason: str | None` (reason vysvětlí
    chybějící baseline)
  - `plan_evaluations(manifest: RunManifest, ports: list[str] | None)
    -> list[Evaluation]` v `runs/pairing.py` s pravidly:
    - subject = každý capture s `phase == "post"`; baseline hledej
      v pořadí: (1) `find_capture("pre", old_node, old_port)` kde
      `old = paired_old(subject.device, subject.port)` (jen port režim),
      (2) `find_capture("pre", old_node, None)` — celoboxový pre starého
      boxu (`old_node` z `device_with_role("old")`), (3) žádný →
      `baseline=None, reason="chybi pre snimek stareho boxu"`.
    - subject = každý capture s `phase == "rollback"`; baseline =
      `find_capture("pre", subject.device, subject.port)` — původní pre
      **téhož** zařízení a portu (roadmapa: rollback se měří proti
      původnímu pre).
    - `ports` (nový-box porty; pro rollback porty starého boxu) filtruje
      subjecty; port `None` záznam projde jen bez filtru.
    - pre captury samy o sobě evaluaci netvoří.
  - CLI: `mig-validate evaluate --run <nazev> [--run-root runs]
    [--ports et-0/0/0,...]` — vzájemně výlučné se `--snapshot`;
    před vyhodnocením `store.missing_snapshots(manifest)` → neprázdné →
    `ToolError` se seznamem. Každá evaluace: načti snapshoty, spusť
    `api.evaluate(subject, baseline=baseline)`, vyrenderuj přes stávající
    `--format/--filter/--status/--detail` volby; před blok vypiš
    hlavičku `=== <subject.snapshot> vs <baseline.snapshot|bez baseline> ===`.
    Exit kód = nejhorší z evaluací (stávající pravidlo `EXIT_FAILED_CHECKS`).
  - CLI: `mig-validate status --run <nazev> [--run-root runs]` — tabulka:
    řádek na mapping pár + na celoboxové capturey; sloupce old node:port,
    new node:port, a která fáze má snímek (pre/post/rollback ✔/–).
    Prostý `print`, žádný nový reporting modul.

- [ ] **Step 1: Failing testy `plan_evaluations`**

`tests/runs/test_pairing.py` — postav manifest v paměti (Task 1 API) a
ověř: post+pre port pár → baseline per-port; post bez port páru, ale
s celoboxovým pre → baseline all; post bez pre → `baseline is None`
s reason; rollback → pre téhož zařízení; `ports=["et-0/0/0"]` filtruje;
pre-only manifest → prázdný plán.

- [ ] **Step 2: Ověř fail** — `ModuleNotFoundError`.

- [ ] **Step 3: Implementuj `runs/pairing.py`** dle pravidel výše
  (čistá funkce nad manifestem, žádné IO).

- [ ] **Step 4: Ověř pass** — `.venv/bin/pytest tests/runs/test_pairing.py -v`.

- [ ] **Step 5: CLI evaluate/status + testy**

`tests/test_cli.py`: použij `_write` helper na výrobu snapshotů ve
struktuře run adresáře + ručně zapsaný `run.yml` (přes Task 1 API).
Testy: `test_evaluate_run_pairs_and_exit_code` (post vs pre, ověř
hlavičku a exit kód), `test_evaluate_run_missing_file_lists_names`
(záznam v manifestu bez souboru → exit 2 a jméno souboru v hlášce),
`test_evaluate_run_ports_filter`, `test_evaluate_run_rejects_snapshot_combo`,
`test_status_run_overview` (výstup obsahuje pár a fajfky fází).
Implementuj `_cmd_status` + rozšíření `_cmd_evaluate` (run větev jako
`_evaluate_run(args) -> int`).

- [ ] **Step 6: Ověř pass + celá sada.**

- [ ] **Step 7: Commit**

```bash
git add migration_validator/runs/pairing.py migration_validator/cli.py tests/
git commit -m "feat: faze4 task6 - evaluate --run (parovani pre/post/rollback) a status --run"
```

---

### Task 7: Ping z baseline ARP (4.5)

**Files:**
- Modify: `migration_validator/probes/ping.py` (`resolve_targets`, `ping.py:120-196`)
- Modify: `migration_validator/capture.py` (`capture_device`, `capture.py:73-123`)
- Modify: `migration_validator/api.py` (`capture`, `api.py:22-46`)
- Modify: `migration_validator/cli.py` (run větev capture)
- Test: `tests/probes/test_ping.py` (rozšíření), `tests/test_capture.py`
  (rozšíření), `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 6 `plan_evaluations` nepoužívá — pár dohledá
  `manifest.paired_old` / `find_capture` přímo (Task 1).
- Produces:
  - `resolve_targets(scopes, arp_entries, nd_entries=None, *,
    baseline_arp=None, baseline_nd=None) -> list[PingTarget]` —
    baseline seznamy mají tvar `facts["arp"]`/`facts["nd"]` pre snímku
    starého boxu. Pravidla:
    - Jména rozhraní starého boxu na scope nového boxu nejde mapovat —
      baseline záznam se ke scope přiřadí **příslušností IP do subnetů
      scope** (sítě z `selectors.local_ipv4`/`local_ipv6` přes
      `ipaddress.ip_interface(addr).network`; záznamy bez zásahu do
      žádné sítě se ignorují). Link-local ND záznamy z baseline se
      nepoužijí nikdy (nejsou přenositelné mezi boxy).
    - Priorita per scope+family: baseline cíle → vlastní ARP/ND
      (dnešní logika) → subnet fallback. `resolved_from` nových cílů:
      `"baseline-arp"` / `"baseline-nd"`; `PingTarget.interface`
      zůstává `None`.
    - Vyloučení vlastních adres a VGW jako dnes (`address != source`,
      `owned` = VGW seznam; navíc vylouč adresy, které jsou přímo
      v `selectors.local_ipv4/6` scope — na novém boxu je to naše IP,
      i když na starém byla v ARP).
    - `_usable_nd` platí i pro baseline ND.
  - `capture_device(..., baseline=None)` — nepovinný `Snapshot`;
    předá `resolve_targets(..., baseline_arp=baseline.facts["arp"],
    baseline_nd=baseline.facts["nd"])` když není `None`.
  - `api.capture(..., baseline=None)` — průchozí parametr.
  - CLI: v `_capture_into_run` při `phase == "post"` dohledej pre
    snímek: `paired_old(node, port)` → `find_capture("pre", old.node,
    old.port)`; fallback `find_capture("pre", old_node, None)`
    (celoboxový). Nalezený → `load_snapshot` a předej jako `baseline`.
    Nenalezený → pokračuj bez baseline (dnešní chování) a vypiš info
    na stderr `pre snimek nenalezen, ping cile z vlastni ARP`.

- [ ] **Step 1: Failing testy `resolve_targets`**

Do `tests/probes/test_ping.py` (stavební vzory scope viz stávající
testy v souboru) přidej:

1. `test_baseline_arp_wins_over_own_arp` — scope s `local_ipv4
   ["192.0.2.1/24"]`, vlastní ARP prázdná, `baseline_arp=[{"ip":
   "192.0.2.50", "interface": "ge-0/0/0.100"}]` → jeden cíl
   `192.0.2.50`, `resolved_from == "baseline-arp"`; a varianta, kdy
   vlastní ARP něco má, ale baseline má přednost.
2. `test_baseline_filters_by_scope_subnet` — baseline záznam
   `10.9.9.9` mimo subnet scope → nepoužije se, spadne to na vlastní
   ARP/fallback.
3. `test_baseline_excludes_own_and_vgw` — baseline obsahuje source IP,
   VGW adresu a adresu z `local_ipv4` → žádná z nich není cíl.
4. `test_baseline_nd_respects_usable_and_link_local` — ND záznam
   `unreachable` a link-local záznam se ignorují; validní globální ND
   adresa v subnetu → `resolved_from == "baseline-nd"`.
5. `test_no_baseline_keeps_today_behavior` — bez keyword argumentů
   výsledek identický s dnešním (regresní).

- [ ] **Step 2: Ověř fail** — nové testy FAIL (`TypeError: unexpected
  keyword argument`).

- [ ] **Step 3: Implementuj v `ping.py`**

Do smyčky rodin v `resolve_targets` vlož před dnešní ARP/ND blok výběr
z baseline (pomocná modulová funkce `_baseline_addresses(entries,
networks, family, *, nd)` vrací seznam adres; sítě spočti jednou per
scope+family). Dnešní bloky se nemění — jen se přeskočí, když baseline
něco dala (`continue` po appendu, stejný vzor jako u `addresses`).

- [ ] **Step 4: Ověř pass** — `.venv/bin/pytest tests/probes/ -v`.

- [ ] **Step 5: Protáhni `baseline` přes capture + api + cli, s testy**

`tests/test_capture.py`: test, že `capture_device(..., baseline=snap)`
předá baseline facts do `resolve_targets` (monkeypatch
`migration_validator.capture.resolve_targets`, zachyť kwargs).
`tests/test_cli.py`: post capture v runu s existujícím pre snímkem →
`api.capture` dostal `baseline` (rozšiř `_fake_capture` calls dict);
bez pre snímku → `baseline is None` a stderr obsahuje `pre snimek
nenalezen`.

- [ ] **Step 6: Celá sada + commit**

```bash
git add migration_validator/probes/ping.py migration_validator/capture.py \
    migration_validator/api.py migration_validator/cli.py tests/
git commit -m "feat: faze4 task7 - ping cile z ARP/ND baseline pre snimku (varianta 2)"
```

---

### Task 8: Dokumentace

**Files:**
- Modify: `README.md` (sekce workflow — runs/ struktura, nové CLI)
- Modify: `docs/cs/README.md` (postup migrace přes `--run`, hybridní
  run.yml — ruční sepsání předem i plnění flagy, příklad z roadmapy)
- Modify: `docs/cs/files/parsers.md` (parsery žijí v
  `migration_validator/parsers/`, kořenové skripty jsou wrappery,
  `--parse-services`)
- Modify: `docs/cs/reference.md` (nové flagy capture/evaluate, subcommand
  `status`, pravidla párování pre/post/rollback, ping z baseline)

**Interfaces:** žádné — jen text; příklady příkazů zkopíruj z CLI
kontraktů Task 4-7 (ať se rozcházet nemohou, po dopsání je spusť
s `--help` a zkontroluj shodu).

- [ ] **Step 1: Uprav všechny čtyři soubory.** Do `docs/cs/README.md`
  patří i poznámka, že staré workflow (`capture --output`, `evaluate
  --snapshot/--baseline`) zůstává plnohodnotné.
- [ ] **Step 2: Ověř příklady** — `mig-validate capture --help`,
  `mig-validate evaluate --help`, `mig-validate status --help` a
  porovnej s textem.
- [ ] **Step 3: Commit**

```bash
git add README.md docs/cs/
git commit -m "docs: faze4 - run management, sjednocene parsery, ping z baseline"
```

---

## Self-review (provedeno při psaní plánu)

- **Pokrytí spec:** 4.1 struktura runs/ → Task 2; 4.2 hybridní run.yml
  → Task 1 (ruční sepsání = `load_manifest` čte plný formát včetně
  `l2_switch`; plnění flagy = Task 4); 4.3 CLI → Task 4+6 (vč. zpětné
  kompatibility `--snapshot/--baseline` a plnohodnotného celoboxového
  režimu); 4.4 parsery + `--parse-services` → Task 3+5; 4.5 ping
  z baseline → Task 7; „Evaluate ověřuje, že soubory z manifestu
  existují“ → Task 6 (`missing_snapshots`).
- **Vědomé odchylky od spec:** (a) capture záznam v run.yml má `port:
  all` místo vynechaného klíče — čitelnost; (b) `--run-root` flag navíc
  kvůli testovatelnosti a runům mimo cwd; (c) baseline ARP se na scope
  mapuje přes subnet membership, ne přes jméno portu — jména rozhraní
  starého boxu na novém neexistují a subnet je jediný přenositelný
  klíč (spec mechanismus nepředepisuje, jen zdroj dat).
- **Typová konzistence:** `CaptureRecord.port: str | None` napříč Task
  1/2/4/6/7; `MappingEndpoint` sdílený Task 1/4/6; `parser_for_platform`
  vrací třídu (ne instanci) — Task 5 ji volá `(config).parse()`.
