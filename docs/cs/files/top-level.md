# Kořen balíčku — vstupní body a orchestrace

Soubory: `pyproject.toml`, `.gitignore`, `migration_validator/__init__.py`,
`api.py`, `cli.py`, `capture.py`, `engine.py`, `config.py`.

Tyhle soubory nic neměří ani neparsují — **řídí pořadí, ve kterém se volají ostatní vrstvy**.

---

## `pyproject.toml`

Definice balíčku. Podstatné body:

| položka | hodnota |
|---|---|
| název / verze | `migration-validator` 0.1.0 |
| Python | ≥ 3.11 |
| závislosti | `junos-eznc>=2.7` (PyEZ), `PyYAML>=6.0`, `lxml>=5.0` |
| extra `dev` | `pytest>=8.0` |
| entry point | `mig-validate = migration_validator.cli:main` |
| pytest | `testpaths = ["tests"]`, `addopts = "-q"` |

Entry point je důvod, proč po `pip install -e .` existuje příkaz `mig-validate`. Parsery
v kořeni (`mx_parser.py`, `evo_parser.py`) **do balíčku nepatří** a spouští se přímo jako
skripty.

## `.gitignore`

Ignoruje `__pycache__/`, `*.py[cod]`, `.venv/`, `pyats-venv/`, `*.egg-info/`,
`.pytest_cache/` a **`runs/`** — výstupy běhů (snapshoty a výsledky) se needitují ani
neverzují.

## `migration_validator/__init__.py`

Jen docstring a `__version__ = "0.1.0"`. Verzi ověřuje `tests/test_package.py`.

---

## `api.py` — programové rozhraní

Jediný šev, který bude volat budoucí GUI. Tři funkce:

```python
capture(host, *, inventory=None, options=None, collectors=None,
        phase=None, ping_count=5, record_raw=None) -> Snapshot
evaluate(snapshot, *, baseline=None, mapping=None, config=None, now=None) -> RunResult
list_checks() -> list[dict]
```

- **`capture()`** přijme `inventory` buď jako hotový objekt `Inventory`, nebo jako cestu
  ke stringu (pak si ji sám načte). Otevře spojení přes `connect()` jako context manager
  a uvnitř zavolá `capture_device()`. Spojení se zavře vždy, i při výjimce.
- **`evaluate()`** nejdřív zavolá `checks.all.load_all()` — bez toho by byl registr checků
  prázdný, kdyby API volalo GUI nebo test přímo. Pak deleguje na `engine.evaluate_snapshots()`.
  Parametr `now` umožňuje deterministický timestamp v testech.
- **`list_checks()`** vrátí `describe()` každého registrovaného checku. Existuje proto, aby
  GUI nemuselo mít hardcoded seznam testů.

Zásadní vlastnost: **`evaluate` nemá jak sáhnout na síť.** Nedostává `Device`, hostname ani
credentials — jen hotové snapshoty.

## `cli.py` — terminálové rozhraní

Tenký obal nad `api.py`, ne alternativní implementace. Definuje pět podpříkazů:

| podpříkaz | funkce | co dělá |
|---|---|---|
| `capture` | `_cmd_capture` | `api.capture()` + `save_snapshot()`, varování o selhaných collectorech na stderr |
| `evaluate` | `_cmd_evaluate` | načte snapshoty, mapping a config, `api.evaluate()`, filtruje a vykreslí |
| `match` | `_cmd_match` | jen `match_scopes()` — ladění `mapping.yml` bez celé validace |
| `checks` | `_cmd_checks` | výpis registru checků, text nebo JSON |
| `record` | `_cmd_record` | uloží syrové RPC XML všech collectorů jako fixtures |

Další podstatné části souboru:

- **`ToolError`** — vlastní výjimka pro „nástroj selhal". `main()` ji chytá a vrací kód 2;
  chytá i `OSError` a `ValueError` ze stejného důvodu. Selhaný *test* je proti tomu kód 1
  a řeší se návratovou hodnotou, ne výjimkou.
- **`_load_snapshot()`** převádí čtyři různé druhy selhání (`FileNotFoundError`,
  `SnapshotVersionError`, `JSONDecodeError`, `KeyError`/`TypeError`) na `ToolError` s českou
  hláškou. Rozbitý snapshot tak nikdy neskončí traceback, ale čitelnou chybou.
- **`_add_auth_arguments()`** — sdílené přihlašovací přepínače pro `capture` i `record`,
  konvence převzatá z parserů: `--username` (default `ansible`), `--auth key|password`,
  `--key-file` (default `~/.ssh/id_rsa`), `--password`, `--port`, `--timeout`.
- **`_parse_statuses()`** rozloží `--status pass,warn` na množinu `Status`.

Dvě věci, které stojí za pozor:

1. **Při `--format text` a zadaném `--output`** jde na terminál text, ale do souboru se
   zapíše **JSON** — a to nefiltrovaný `result`, ne odfiltrovaný `shown`. U `--format json`
   se naopak zapíše filtrovaný výstup.
2. **Podpříkaz `record` existuje navíc oproti specifikaci**, která popisuje jen
   `capture --record-raw`. Oba jsou funkční a liší se chováním: `record` u každého RPC
   vypíše, zda uspělo, a při selhání pokračuje s hlášením; `capture --record-raw` je tichý
   best-effort (`capture._record`).

## `capture.py` — orchestrace sběru

Jediná veřejná funkce `capture_device(device, address, ...)`. Pořadí fází je záměrné:

```
1. detect_platform(device)          junos | junos-evo
2. _select_collectors(...)          filtr podle platformy a --collectors
3. for collector in selected:       collector.collect() → facts[jméno]
4. build_scopes(inventory)          jen když inventory je
5. resolve_targets(scopy, ARP)      cíle pingu ze scopů a ARP tabulky
6. run_ping(...) per cíl            aktivní měření
7. Snapshot(...)                    zmrazení
```

Klíčová místa:

- **Selhání collectoru nezruší capture.** `CollectorError` se odchytí, oblast se naplní
  prázdnou hodnotou **správného typu** (`_empty_for()` — `[]` pro `arp`, jinak `{}`) a chyba
  se zapíše do `capture.collectors`. Checky nad tou oblastí pak dostanou `SKIP`, nikdy `PASS`.
- **Neznámý název v `--collectors` je tvrdá chyba** (`ValueError`), ne tiché vynechání —
  jinak by překlep vypadal jako úspěšný sběr bez té oblasti.
- **Ping běží jen když existují scopy**, tedy jen s inventory. Bez ní má snapshot
  `probes.ping: []`.
- **`_record()`** ukládá syrové XML pro `--record-raw`. Prochází `collector.rpc_names()`,
  ne jen `rpc_name()`, takže collector s více RPC (na MX `evpn_mac`) uloží obě —
  první jako `evpn_mac.xml`, druhé jako `evpn_mac.2.xml`. Je to best-effort: selhané RPC
  se tiše přeskočí, protože nahrávání fixtures nesmí shodit sběr.
- Import `migration_validator.collectors.all` na úrovni modulu je to, co **naplní registr
  collectorů**.

## `engine.py` — orchestrace vyhodnocení

`evaluate_snapshots(subject, baseline=None, mapping=None, config=None, now=None)`.
Nesahá na síť; všechna data pocházejí ze snapshotů.

Průběh:

1. `_scopes_of()` — vezme scopy ze snapshotu, a **když žádné nejsou, vyrobí jediný device
   scope**. Tím se realizuje režim bez inventory bez jediné větve v checcích.
2. Bez baseline: pro každý scope se spustí `_run_scope()` bez baseline dat.
3. S baseline: `match_scopes()` spáruje scopy a pak
   - spárované dvojice se vyhodnotí **proti baseline**,
   - nespárované subject scopy se vyhodnotí **stavově** a zároveň jdou do `unmatched.subject`
     (nová služba se pořád zkontroluje, jen se nemá s čím porovnat),
   - nespárované baseline scopy jdou **jen** do `unmatched.baseline` — v subjektu nejsou,
     není co měřit.
4. Sečte se `summary` přes všechny checky všech scopů.
5. `_unassigned_bgp_peers()` dohledá peery ze subjektu, které nespadly do žádného scope.
   V device režimu vrací prázdný seznam (device scope „vlastní" všechno).

Dvě netriviální funkce:

- **`_aligned_baseline_data()`** přejmenuje klíče baseline rozhraní na názvy subjektu
  (`ge-0/0/2.113` → `et-0/0/8.113`), aby `interface_traffic` našel s čím porovnávat.
  Podrobně v [../architecture.md](../architecture.md), sekce „Zarovnání názvů rozhraní při
  porovnání".
- **Status scope** = `Status.worst()` přes checky, ale **jen přes ty, které nevrátily
  `SKIP`**. Když nezůstane nic, výsledek je `SKIP`. Bez téhle podmínky by zdravá IPVPN
  služba bez baseline svítila `SKIP` jen kvůli compare-only checku.

## `config.py` — tolerance a severity

Malý soubor s velkým dopadem: **žádná tolerance ani severity není zadrátovaná v checku.**

- `DEFAULTS` — slovník výchozích hodnot per check id.
- `CheckConfig.options(check_id)` — sloučí defaulty s uživatelským YAML (uživatel vyhrává).
- `CheckConfig.severity(check_id, default)` — přepíše severity z configu, jinak vrátí
  výchozí z třídy checku.
- `CheckConfig.enabled(check_id)` — `false` znamená, že check vůbec neběží (jediný, kdo to
  má defaultně, je `traffic_ceased`).
- `load_config(path)` čte klíč `checks:` z YAML; `default_config()` vrátí prázdnou konfiguraci.

Seznam výchozích hodnot je v [../reference.md](../reference.md#2-configyml).
