# Documentation — table of contents

Documentation for **Migration Validator** (validation of network services during a
Junos → Junos EVO migration).

| document | contents |
|---|---|
| [README.md](README.md) | **Operator guide** — installation, the migration procedure, every command, reading the output, troubleshooting |
| [architecture.md](architecture.md) | Layers, dependency direction, data flow and the **contracts** the tool rests on |
| [reference.md](reference.md) | Check catalogue, `config.yml`, `mapping.yml`, snapshot and result JSON schemas, exit codes |
| [files/](#file-coverage) | Per-file documentation |

Czech version of the same set: [../cs/index.md](../cs/index.md).

The source specification and implementation plans (a historical record of decisions, not a
manual) remain under `docs/superpowers/` — they are written in Czech:

- `specs/2026-07-24-migration-validator-design.md` — design and architecture decisions AR‑1 to AR‑8
- `plans/2026-07-24-migration-validator-evaluate.md` — plan 1: the evaluation path
- `plans/2026-07-24-migration-validator-capture.md` — plan 2: the collection path and the lab environment

> **Where the spec and the code disagree, the code wins.** This documentation describes the
> code. Specific divergences are flagged where they occur.

---

## File coverage

Every source file in the repository is covered by one of the documents below. Empty
`__init__.py` files (package markers with no code) are listed collectively under their
package.

| file | document |
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
| `tests/**` (including `fixtures/`) | [files/tests.md](files/tests.md) |
| empty `__init__.py` in `models/`, `connection/`, `collectors/`, `probes/`, `scoping/`, `checks/`, `reporting/` | with the respective package |

---

## Package map

```
migration-status-check/
├── mx_parser.py               # inventory from MX  (classic Junos)     → files/parsers.md
├── evo_parser.py              # inventory from ACX/PTX (Junos EVO)     → files/parsers.md
├── pyproject.toml             # package, dependencies, entry point     → files/top-level.md
│
├── migration_validator/
│   ├── cli.py                 # thin wrapper over api.py               → files/top-level.md
│   ├── api.py                 # capture() / evaluate() / list_checks()
│   ├── capture.py             # collection orchestration
│   ├── engine.py              # evaluation orchestration
│   ├── config.py              # tolerances and severities
│   │
│   ├── models/                # Inventory, Scope, Snapshot, Result     → files/models.md
│   ├── connection/            # PyEZ, the only layer touching the net  → files/connection.md
│   ├── collectors/            # RPC XML → structured data (incl. nd)   → files/collectors.md
│   ├── probes/                # active ping                            → files/probes.md
│   ├── scoping/               # inventory → scopes, pairing            → files/scoping.md
│   ├── checks/                # pure functions (facts, scope) → verdict → files/checks.md
│   └── reporting/             # result → text / JSON                   → files/reporting.md
│
├── tests/                     # 306 tests, none of them need a network → files/tests.md
└── docs/                      # this documentation + spec and plans
```
