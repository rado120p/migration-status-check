# Migration Validator — operator guide

A tool for verifying the state of network services during a migration from an **MX router
(classic Junos)** to a router running **Junos EVO** (ACX/PTX). It validates the state before
the migration, after the migration, and compares both — despite the fact that port names
differ between the two devices (`ge-0/0/2.113` → `et-0/0/8.113`).

- Architecture and how the files relate to each other: [architecture.md](architecture.md)
- Per-file documentation: [index.md](index.md)
- Reference tables (check catalogue, `config.yml`, `mapping.yml`, JSON schemas): [reference.md](reference.md)
- Česká verze: [../cs/README.md](../cs/README.md)

> **A note on language.** The codebase is Czech-first: every docstring, CLI help string and
> result `message` is in Czech (without diacritics). This English documentation quotes those
> strings **verbatim**, so that anything you read here can be grepped in the real output.

---

## 1. Installation

Requires Python ≥ 3.11.

```bash
cd /home/rado/Desktop/scripts/migration-status-check
python3 -m venv .venv
.venv/bin/pip install -e .
```

The install creates the `mig-validate` command (`.venv/bin/mig-validate`). Dependencies are
pulled in automatically: `junos-eznc` (PyEZ), `PyYAML`, `lxml`.

Tests (no network or lab required):

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

---

## 2. The core idea: capture → evaluate

The tool has **two separate operations** and it matters not to conflate them:

| operation | touches the network | what it does |
|---|---|---|
| `capture` | **yes** | connects to the device, collects operational state, freezes it into a JSON snapshot |
| `evaluate` | **no** | a pure function over existing snapshots — evaluates them and prints the result |

The operational consequence: **the snapshot of the old device must be taken before you move
the cable.** Once migrated, that state is gone forever. Evaluation, by contrast, can be
repeated as often as you like — it puts no load on production and returns an identical
result every time.

---

## 3. The full migration procedure

| step | action | command |
|---|---|---|
| 1 | build the inventory for both devices | `mx_parser.py` / `evo_parser.py` (see below) |
| 2 | collect on the **old** device, before the migration | `mig-validate capture --phase pre-migration` |
| 3 | validate the old device (no baseline) | `mig-validate evaluate --snapshot pre.json` |
| 4 | **the migration itself** — recabling | — |
| 5 | collect on the **new** device, after the migration | `mig-validate capture --phase post-migration` |
| 6 | validate the new device and compare against the old one | `mig-validate evaluate --snapshot post.json --baseline pre.json` |

### Step 1 — inventory from the devices

The inventory is a YAML file listing interfaces and the services running on them. It is
produced by two standalone parsers **in the repository root** — one per platform:

| device | parser |
|---|---|
| MX (classic Junos) | `mx_parser.py` |
| ACX / PTX (Junos OS Evolved) | `evo_parser.py` |

```bash
.venv/bin/python mx_parser.py  172.20.20.4 -o 172.20.20.4.yml
.venv/bin/python evo_parser.py 172.20.20.5 -o 172.20.20.5.yml
```

Without `-o` the file is named `<hostname>.yml`. Authentication follows the same convention
as the validator (`--auth key|password`, `-u/--username`, `-k/--key-file`), defaulting to an
SSH key and the user `ansible`. Details: [files/parsers.md](files/parsers.md).

**The tool also works without an inventory**, but then everything lands in a single "device"
scope: instead of per-service results you get a device-wide summary, and ping does not run
at all (neither the target nor the source address is known).

### Steps 2 and 5 — collection (`capture`)

```bash
.venv/bin/mig-validate capture \
    --device 172.20.20.4 \
    --inventory 172.20.20.4.yml \
    --phase pre-migration \
    --output runs/mig01/pre/172.20.20.4.json
```

| flag | meaning |
|---|---|
| `--device` | IP or hostname of the device (required) |
| `--output` | where to write the snapshot (required) |
| `--inventory` | YAML from the parser; without it, capture runs in device mode |
| `--phase` | free text, stored in the snapshot and printed in the report (`pre-migration`, `post-migration`) |
| `--collectors` | comma-separated subset of areas (`interfaces,bgp`) — for debugging |
| `--ping-count` | ICMP packets per target, default 5 |
| `--record-raw DIR` | additionally saves the raw RPC XML into `DIR/<platform>/` |
| `--username` | default `ansible` |
| `--auth key\|password` | default `key` |
| `--key-file` | default `~/.ssh/id_rsa` |
| `--password` | only with `--auth password` |
| `--port` / `--timeout` | default 22 / 30 s |

The platform (`junos` vs `junos-evo`) is **detected automatically** and drives the choice of
RPCs. You never specify it.

A failing collector does not abort the capture — the failure is recorded in the snapshot, a
warning goes to stderr, and checks that need that area will later return `SKIP`, never
`PASS`. A failing **connection**, by contrast, aborts the capture: no snapshot is written
and the exit code is 2.

### Step 3 — validation without a baseline

```bash
.venv/bin/mig-validate evaluate --snapshot runs/mig01/pre/172.20.20.4.json
```

Only **state** checks run (interfaces are up, BGP is Established, traffic is flowing, ...).
Comparison checks return `SKIP` with the reason `porovnavaci check bez baseline snapshotu`
("comparison check without a baseline snapshot").

### Step 6 — validation with comparison

```bash
.venv/bin/mig-validate evaluate \
    --snapshot runs/mig01/post/172.20.20.5.json \
    --baseline runs/mig01/pre/172.20.20.4.json \
    --mapping  mapping.yml \
    --format json --output runs/mig01/post.result.json
```

| flag | meaning |
|---|---|
| `--snapshot` | the snapshot being evaluated (required) |
| `--baseline` | the snapshot to compare against |
| `--mapping` | `mapping.yml` with manual pairings and ignores |
| `--config` | `config.yml` with tolerances and severities |
| `--format text\|json` | default `text` |
| `--output` | output file (with `--format text` the file receives **JSON**) |
| `--filter` | substring in the description or scope id |
| `--status` | comma-separated list: `pass,warn,fail,skip` |
| `--detail` | prints every check of every service, not just the worst finding |
| `--warn-as-error` | WARN then also yields exit code 1 |

---

## 4. Reading the output

Real output from a lab run:

```
Migrace: 172.20.20.4 (pre-migration) -> 172.20.20.5 (post-migration)

  68 PASS   15 WARN   1 FAIL   7 SKIP
  Sparovano 8 sluzeb, 2 nesparovana v baseline, 3 nesparovane v subject

SLUZBA                           TYP        STAV  DETAIL
EVPN-VPWS-CPE13-NNI              E-Line     WARN  et-0/0/8.213: provoz netece (in 0 pps, out 0 pps)
INTERNET-CPE13-NNI               Internet   WARN  152.11.13.2: stav se zmenil Idle -> Established
EVPN-VLAN-AWARE-INTERNET         Internet   FAIL  152.11.14.4: stav Active, ocekavano Established
clab-pop-migration-P2;et-0/0/0   Core       OK

NESPAROVANO
  baseline  clab-pop-migration-P1;et-0/0/0   (Core)  zadny kandidat na subject
  subject   EVPN-VLAN-AWARE-INTERNET         (E-LAN)  nova sluzba, chybi baseline
```

Column headers: `SLUZBA` = service, `TYP` = type, `STAV` = status, `DETAIL` = detail.
`Sparovano N sluzeb` = "N services paired". `NESPAROVANO` = "unpaired".

What matters here:

- **The per-service line shows only the worst finding.** The remaining checks appear with
  `--detail`.
- **`OK` does not automatically mean "everything was verified".** It can also mean that only
  some of the checks ran. That is exactly why `--detail` exists — it shows what was actually
  checked.
- **Filters (`--filter`, `--status`) narrow the service table only.** The summary counts at the
  top still describe the whole run — with `--status fail` you may see one row while the summary
  still reports all 68 PASS. That is intentional: a filter is a view, not a recomputation.
- **The `NESPAROVANO` section is always printed**, even when everything else is green, and
  **filters do not apply to it.** It is the main safeguard against an overlooked service:
  - `baseline` = the service existed on the old box and is missing on the new one → suspect a
    forgotten migration,
  - `subject` = the new box has something extra → a new or restructured service.
- Statuses are `OK` / `WARN` / `FAIL` / `SKIP`. **`SKIP` means "not measured"**, not "fine" —
  missing data never yields a PASS.

Common message strings, translated:

| Czech string in the output | meaning |
|---|---|
| `provoz netece (in 0 pps, out 0 pps)` | no traffic flowing |
| `stav se zmenil Idle -> Established` | state changed from Idle to Established |
| `stav Active, ocekavano Established` | state is Active, expected Established |
| `zadny kandidat na subject` | no candidate on the subject device |
| `nova sluzba, chybi baseline` | new service, no baseline |
| `ambiguous: N kandidatu (...)` | ambiguous: N candidates, so no pairing was made |
| `pocty prefixu v toleranci -10 %` | prefix counts within the −10 % tolerance |
| `vsech N cilu odpovedelo` | all N targets responded |

### Exit codes

| code | meaning |
|---|---|
| `0` | no FAIL |
| `1` | at least one FAIL (or a WARN with `--warn-as-error`) |
| `2` | **tool error** — could not connect, snapshot missing, broken JSON, different `schema_version` |

The distinction between 1 and 2 is deliberate: *a test failed* and *the tool failed* are two
different things.

---

## 5. Debugging service pairing

Pairing the old and new device is the most delicate part of the tool, because it rests on
the quality of the `description` fields on the devices. The tool **never guesses**: if a rule
yields more than one candidate, the service is left unpaired and goes to `NESPAROVANO` with
the reason `ambiguous: N kandidatu (...)`.

You can debug this without running a full validation and without touching the network:

```bash
.venv/bin/mig-validate match \
    --baseline runs/mig01/pre/172.20.20.4.json \
    --subject  runs/mig01/post/172.20.20.5.json \
    --mapping  mapping.yml
```

```
SPAROVANO (8)
  high     description+service_type+service_subtype      svc:EVPN-VPWS-CPE13-NNI:E-Line
           -> svc:EVPN-VPWS-CPE13-NNI:E-Line
  medium   routing_instance+service_type                 svc:L3VPN-CPE14-UNI:IPVPN
           -> svc:et-0/0/10.0:IPVPN
...
```

(`SPAROVANO` = paired, `NESPAROVANO baseline` / `NESPAROVANO subject` = unpaired on either
side.)

The loop is: run `match` → add the missing pair to `mapping.yml` → run again. It takes
seconds. The rules and the `mapping.yml` format are described in [reference.md](reference.md).

---

## 6. Configuration

Both files are optional; without them the defaults from `migration_validator/config.py`
apply.

**`config.yml`** — check tolerances and severities, passed via `--config`:

```yaml
checks:
  interface_traffic:
    tolerance_percent: -60      # traffic drop that still passes
    require_nonzero: true
  bgp_prefix_counts:
    tolerance_percent: -10
    severity: advisory
  ping_reachability:
    count: 5
  traffic_ceased:
    enabled: false              # default; requires a third capture of the old box
```

**`mapping.yml`** — manual pairing and ignores, passed via `--mapping`:

```yaml
mappings:
  - baseline: {description: EVPN-VLAN-AWARE-INTERNET, service_type: Internet}
    subject:  {description: EVPN-VLAN-AWARE-INTERNET, service_type: E-LAN}
    note: "sluzba restrukturalizovana na EVPN vlan-aware s IRB gateway"
  - baseline: {interface: "ge-0/0/4.0"}
    subject:  {interface: "et-0/0/10.0"}

ignore:
  - {description: "EVPN-VLAN-AWARE-L3VPN"}
  - {interface: "ge-0/0/7.0"}
```

The `{interface: ...}` selector targets a **logical unit** (`ge-0/0/2.113`), not a physical
port. Management interfaces (`fxp`, `em`, `me`, `vme`, `bme`, `re0:mgmt-*`, `re1:mgmt-*`) do
not need to be listed — they are excluded already at scope-building time and are never
pinged.

Full overview: [reference.md](reference.md).

---

## 7. Other commands

**List the checks** — the single source of truth, no hand-maintained list:

```bash
.venv/bin/mig-validate checks              # table
.venv/bin/mig-validate checks --format json
```

**Record raw RPC XML** for test fixtures:

```bash
.venv/bin/mig-validate record --device 172.20.20.4 --output-dir tests/fixtures/rpc
```

This stores every collector's response into `tests/fixtures/rpc/<platform>/<area>.xml`. A
collector with multiple RPCs (`evpn_mac` on MX) stores each one separately — the second as
`evpn_mac.2.xml`. It is the only sustainable way to keep collectors from rotting: unfamiliar
output from production gets copied into the fixtures and the regression test is done.

The same can be done as a side effect of a regular capture: `capture --record-raw DIR`. The
difference is that `record` reports success or failure per RPC, while `--record-raw` is a
silent best-effort.

---

## 8. Where results are stored

Recommended convention (`runs/` is in `.gitignore`):

```
runs/2026-07-24_MX1-POP1_migration/
├── pre/172.20.20.4.snapshot.json
├── post/172.20.20.5.snapshot.json
├── pre-validation.result.json
└── post-validation.result.json
```

No path is hard-wired anywhere — `--output` is always explicit.

---

## 9. Troubleshooting

| symptom | cause and remedy |
|---|---|
| `chyba: ...: autentizace selhala` + code 2 | authentication failed. Check `--username`, `--key-file`, or use `--auth password --password ...` |
| `chyba: ...: timeout po 30 s` + code 2 | device unreachable, or NETCONF not enabled on port 22 |
| `varovani: collector 'X' selhal` on stderr | an RPC failed, capture continued. The snapshot exists, but checks over area `X` will be `SKIP` |
| `chyba: snapshot ma schema_version N` | snapshot from a different tool version; re-capture it |
| Everything is `SKIP` | typically the collectors failed — inspect `capture.collectors` in the snapshot |
| A service is missing from the listing | it is not a migrated service type (`Internet`, `IPVPN`, `E-Line`, `E-LAN`, `Core`), it is a management interface, or it is in `ignore` in `mapping.yml` |
| Lots of `ambiguous` entries in `NESPAROVANO` | duplicate `description` values on the device — pair them manually via `mapping.yml` |
| `interface_traffic` reports `provoz netece` on a freshly migrated service | expected if nothing is flowing through the port yet; the check is `advisory`, so WARN, not FAIL |

One unusual detail to watch: with `--format text` **and** `--output`, the terminal gets text
but the **file receives JSON** — and unfiltered, the complete result.

---

## 10. Known limitations

- **The inventory is an input, not an output.** Pairing quality is bounded by the quality of
  the descriptions on the devices; `mapping.yml` is an escape hatch, not a substitute for
  discipline.
- **Traffic comparison is best-effort.** Real time passes between the pre- and post-snapshot
  (recabling) and traffic may legitimately differ. Hence `advisory` and a −60 % tolerance.
- **Ping is best-effort.** The CPE may be powered off or block ICMP. Hence `advisory`.
- **The tolerances are initial estimates** and are meant to be tuned against real traffic —
  which is exactly why they are configurable.
- **A capture is a single point in time.** The tool does not do continuous monitoring.
- **At runtime the tool speaks Czech without diacritics** — CLI help, logs and result messages
  alike (`provoz netece`, `zadny kandidat na subject`), because output goes to terminals where
  diacritics cannot be relied upon. That constraint applies to the tool, not to this
  documentation; every Czech string quoted here is verbatim so it can be grepped.
