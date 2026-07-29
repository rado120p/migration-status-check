# Dokumentace — rozcestník

Dokumentace k nástroji **Migration Validator** (validace síťových služeb při migraci
Junos → Junos EVO).

| dokument | obsah |
|---|---|
| [README.md](README.md) | **Provozní příručka** — instalace, migrační postup, všechny příkazy, čtení výstupu, řešení potíží |
| [architecture.md](architecture.md) | Vrstvy, směr závislostí, datový tok a **kontrakty**, na kterých nástroj stojí |
| [reference.md](reference.md) | Katalog checků, `config.yml`, `mapping.yml`, JSON schéma snapshotu a výsledku, návratové kódy |
| [files/](#pokrytí-souborů) | Popis každého souboru zvlášť |

Anglická verze téhož: [../en/index.md](../en/index.md).

Zdrojová specifikace a implementační plány (historický záznam rozhodnutí, ne příručka)
zůstávají v `docs/superpowers/`:

- `specs/2026-07-24-migration-validator-design.md` — návrh a architektonická rozhodnutí AR‑1 až AR‑8
- `plans/2026-07-24-migration-validator-evaluate.md` — plán 1: vyhodnocovací cesta
- `plans/2026-07-24-migration-validator-capture.md` — plán 2: sběrná cesta a laboratorní prostředí

> **Kde se spec a kód rozcházejí, platí kód.** Tahle dokumentace popisuje kód. Konkrétní
> odchylky jsou vyznačené na místech, kde vznikly.

---

## Pokrytí souborů

Každý zdrojový soubor repozitáře je popsaný v některém z dokumentů níže. Prázdné
`__init__.py` (pouhé značky balíčku, bez kódu) jsou uvedené hromadně u svého balíčku.

| soubor | dokument |
|---|---|
| `pyproject.toml` | [files/top-level.md](files/top-level.md) |
| `.gitignore` | [files/top-level.md](files/top-level.md) |
| `migration_validator/__init__.py` | [files/top-level.md](files/top-level.md) |
| `migration_validator/api.py` | [files/top-level.md](files/top-level.md) |
| `migration_validator/cli.py` | [files/top-level.md](files/top-level.md) |
| `migration_validator/capture.py` | [files/top-level.md](files/top-level.md) |
| `migration_validator/engine.py` | [files/top-level.md](files/top-level.md) |
| `migration_validator/config.py` | [files/top-level.md](files/top-level.md) |
| `migration_validator/models/inventory.py` | [files/models.md](files/models.md) |
| `migration_validator/models/scope.py` | [files/models.md](files/models.md) |
| `migration_validator/models/snapshot.py` | [files/models.md](files/models.md) |
| `migration_validator/models/result.py` | [files/models.md](files/models.md) |
| `migration_validator/connection/junos.py` | [files/connection.md](files/connection.md) |
| `migration_validator/collectors/base.py` | [files/collectors.md](files/collectors.md) |
| `migration_validator/collectors/registry.py` | [files/collectors.md](files/collectors.md) |
| `migration_validator/collectors/all.py` | [files/collectors.md](files/collectors.md) |
| `migration_validator/collectors/interfaces.py` | [files/collectors.md](files/collectors.md) |
| `migration_validator/collectors/arp.py` | [files/collectors.md](files/collectors.md) |
| `migration_validator/collectors/nd.py` | [files/collectors.md](files/collectors.md) |
| `migration_validator/collectors/bgp.py` | [files/collectors.md](files/collectors.md) |
| `migration_validator/collectors/evpn.py` | [files/collectors.md](files/collectors.md) |
| `migration_validator/probes/ping.py` | [files/probes.md](files/probes.md) |
| `migration_validator/scoping/builder.py` | [files/scoping.md](files/scoping.md) |
| `migration_validator/scoping/matcher.py` | [files/scoping.md](files/scoping.md) |
| `migration_validator/scoping/mapping.py` | [files/scoping.md](files/scoping.md) |
| `migration_validator/checks/base.py` | [files/checks.md](files/checks.md) |
| `migration_validator/checks/registry.py` | [files/checks.md](files/checks.md) |
| `migration_validator/checks/all.py` | [files/checks.md](files/checks.md) |
| `migration_validator/checks/ifaces.py` | [files/checks.md](files/checks.md) |
| `migration_validator/checks/bgp.py` | [files/checks.md](files/checks.md) |
| `migration_validator/checks/evpn.py` | [files/checks.md](files/checks.md) |
| `migration_validator/checks/reachability.py` | [files/checks.md](files/checks.md) |
| `migration_validator/reporting/json_report.py` | [files/reporting.md](files/reporting.md) |
| `migration_validator/reporting/view.py` | [files/reporting.md](files/reporting.md) |
| `migration_validator/reporting/text_report.py` | [files/reporting.md](files/reporting.md) |
| `mx_parser.py` | [files/parsers.md](files/parsers.md) |
| `evo_parser.py` | [files/parsers.md](files/parsers.md) |
| `172.20.20.4.yml`, `172.20.20.5.yml` | [files/parsers.md](files/parsers.md) |
| `tests/**` (včetně `fixtures/`) | [files/tests.md](files/tests.md) |
| prázdné `__init__.py` v `models/`, `connection/`, `collectors/`, `probes/`, `scoping/`, `checks/`, `reporting/` | u příslušného balíčku |

---

## Mapa balíčku

```
migration-status-check/
├── mx_parser.py               # inventory z MX  (klasický Junos)      → files/parsers.md
├── evo_parser.py              # inventory z ACX/PTX (Junos EVO)       → files/parsers.md
├── pyproject.toml             # balíček, závislosti, entry point      → files/top-level.md
│
├── migration_validator/
│   ├── cli.py                 # tenký obal nad api.py                 → files/top-level.md
│   ├── api.py                 # capture() / evaluate() / list_checks()
│   ├── capture.py             # orchestrace sběru
│   ├── engine.py              # orchestrace vyhodnocení
│   ├── config.py              # tolerance a severity
│   │
│   ├── models/                # Inventory, Scope, Snapshot, Result    → files/models.md
│   ├── connection/            # PyEZ, jediná vrstva sahající na síť   → files/connection.md
│   ├── collectors/            # RPC XML → strukturovaná data (vč. nd) → files/collectors.md
│   ├── probes/                # aktivní ping                          → files/probes.md
│   ├── scoping/               # inventory → scopy, párování           → files/scoping.md
│   ├── checks/                # čisté funkce (fakta, scope) → verdikt  → files/checks.md
│   └── reporting/             # výsledek → text / JSON                → files/reporting.md
│
├── tests/                     # žádný test nepotřebuje síť            → files/tests.md
└── docs/                      # tato dokumentace + spec a plány
```
