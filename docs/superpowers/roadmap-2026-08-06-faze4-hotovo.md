# Fáze 4 hotová — run management + ping z baseline ARP, stav k 2026-08-06

**Výchozí bod:** větev `worktree-faze4-run-management` založená z `main` na
commitu `e8235ac`; **785 testů zelených, 1 přeskočený** (změřeno ve worktree
před začátkem). Po fázi 4: **851 testů zelených, 1 přeskočený** (změřeno na
`main` po merge, `.venv/bin/pytest`) — fáze přidala 66 testů.
Schema **snapshotu zůstává 7**, schema **inventory zůstává 5**; nový formát
`run.yml` má vlastní `schema_version: 1` (loader cizí verzi odmítá).

Zadáním byla fáze 4 roadmapy
[`specs/2026-08-05-evpn-a-run-management-roadmapa-design.md`](specs/2026-08-05-evpn-a-run-management-roadmapa-design.md)
(sekce „Fáze 4 — Run management (bod 4) + ping z baseline ARP (1c)").
Provedení [`plans/2026-08-06-faze4-run-management.md`](plans/2026-08-06-faze4-run-management.md).

---

## Co fáze 4 přinesla

**Nový balíček `migration_validator/runs/` (Task 1+2, commity `e8ab2a1`,
`2108c2d`, `f65c0f6`).** `manifest.py` je model run.yml — `RunManifest`
(devices / interface_mapping / captures), párování portů
(`paired_old`/`paired_new`, LAG víc old→jeden new vrací `None`), idempotentní
`add_mapping` a `record_capture` (náhrada záznamu se shodným phase+device+port).
`l2_switch` pod `new` je jen průchozí data pro fázi 5. `store.py` drží
konvenci souborů: `snapshot_<pre|post|rollback>_<node>_<port|all>.json`,
`inventory_<node>_<port|all>.yml`, porty normalizované `ge-0/0/0` →
`ge_0_0_0`. Pravidlo `.gitignore` `runs/` ukotveno na kořen repa
(`02d952c`), aby nepolykalo nový balíček.

**Parsery sjednocené do `migration_validator/parsers/` (Task 3, commit
`df33ebf`).** Diff obou skriptů změřil, že jsou byte-identické až na 9 hunků;
skutečný platformní rozdíl jsou 3 metody. `core.py` nese společné jádro
(~2530 řádků beze změny), `mx.py`/`evo.py` jen `_detect_vpls`,
`_detect_evpn_elan_subtype`, `_is_evpn_instance` + jméno loggeru,
`parser_for_platform()` mapuje `junos`/`junos-evo`. Kořenové `mx_parser.py`
a `evo_parser.py` zůstaly jako 10řádkové wrappery se stejným CLI.
`INVENTORY_SCHEMA_VERSION` má jediný zdroj v `models/inventory.py`
(dřív 3 kopie). `tests/parsers/` přešly z importlib-by-path na normální
importy.

**`capture --run` (Task 4+5, commity `18c7624`, `f834b34`, `297e09f`).**
Run režim ukládá snapshoty do `runs/<název>/`, vede run.yml (node podle
hosta z manifestu, jinak `--device`; role pre/rollback→old, post→new),
`--maps-to NODE:PORT` zapisuje pár se správnou orientací. Inventory se
hledá `--inventory` → per-port soubor → celoboxový soubor → chyba radící
`--parse-services`; flag `--parse-services` stáhne konfiguraci zvláštním
krátkým spojením a vyrobí inventory přes sjednocené parsery (existující
soubor se nikdy tiše nepřepisuje). Pozor na změnu CLI: SSH port capture je
teď `--ssh-port` (`--port` znamená port rozhraní; `record` má dál `--port`).
Per-port inventory obsahuje i Layer1 záznam fyzického portu — správné
chování parseru, checky na něm závisejí.

**`evaluate --run` + `status --run` (Task 6, commit `7336fa9`).**
`runs/pairing.py::plan_evaluations` je čistá funkce: post se páruje proti
pre spárovaného old portu, fallback celoboxový pre starého boxu, jinak
vyhodnocení bez baseline s důvodem na stderr; rollback proti pre **téhož**
zařízení a portu; `--ports` filtruje subjekty. Evaluate nejdřív ověří, že
soubory z manifestu existují (`missing_snapshots` → chyba se seznamem),
každé vyhodnocení uvozuje hlavička `=== X vs Y ===`, exit kód je nejhorší
z vyhodnocení. `status --run` tiskne přehled párů a nasnímaných fází.
Explicitní `evaluate --snapshot/--baseline` i `capture --output` fungují
beze změny.

**Ping z baseline ARP (Task 7, commit `973ef14`, schválená varianta 2).**
Při `--phase post` se cíle pingu berou z ARP/ND pre snímku spárovaného
portu (dohledán přes run.yml). Záznamy starého boxu nesou jména rozhraní,
která na novém boxu neexistují — mapují se proto na scope **příslušností
IP do subnetů** z `local_ipv4/6`. `resolved_from` je `baseline-arp`/
`baseline-nd`; vlastní adresy, VGW a link-local se vylučují, `_usable_nd`
platí i pro baseline. Bez pre snímku platí dnešní odvození z vlastní
ARP + subnet fallback (info na stderr).

**Dokumentace (Task 8, commity `e9f3a04`, `530879f`).** Nová kapitola
„3a. Run management" v `docs/cs/README.md`, sekce 8 v `docs/cs/reference.md`,
aktualizované `docs/cs/files/parsers.md` a `top-level.md` (cs i en) včetně
opravy driftu `--port`→`--ssh-port`.

**Finální review celé větve (commit `892481b`).** Opraveno: `evaluate/status
--run <překlep>` dřív tiše skončil exit 0 (prázdný manifest fallback je
správný jen pro capture) — teď ToolError; guardy `--maps-to` mimo run režim
a `evaluate --run --baseline`; sdílený `find_pre_baseline` v pairing.py
používá CLI i plánovač (dřív duplikát logiky).

## Co zbývá / odloženo

- **Fáze 5 (EX podpora)** — vlastní brainstorm/spec, staví na run.yml
  (`l2_switch` záznamy) a sjednocených parserech (třetí podtřída).
- Staré body 20–21 z předchozí roadmapy (viz memory).
- Drobnosti odložené z review (vše OK to defer, viz finální review):
  `--status` enum v docs bez „info" (pre-existing), `_status_rows` řadí
  l2-switch do OLD sloupce (fáze 5), dedup baseline ping kandidátů,
  hláška „neznama faze 'None'" při chybějící `--phase` v run režimu.
- `runs/mig01` snímky jsou schema 6 — pro práci s novým CLI je nutné
  přesnímat (platilo už před fází 4).
