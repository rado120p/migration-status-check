# Migration Validator — operator guide

A tool for verifying the state of network services during a migration from an **MX router
(classic Junos)** to a router running **Junos EVO** (ACX/PTX). It validates the state before
the migration, after the migration, and compares both — despite the fact that port names
differ between the two devices (`ge-0/0/2.113` → `et-0/0/8.113`).

It verifies interface state, BGP, EVPN (both E-Line and E-LAN), reachability (ARP/ND/ping),
**static routes** and **BFD sessions**. The last two are the only ones where the measured
state is also compared against the **configured intent** — a configured static route that
never made it into the routing table is otherwise invisible.

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
| `--ssh-port` / `--timeout` | default 22 / 30 s |

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
| `--detail` | expands the full block for services with status PASS too (WARN/FAIL always expand) |
| `--warn-as-error` | WARN then also yields exit code 1 |

---

## 3a. Run management (`--run`)

Since phase 4 there is a second path alongside manual `--output`/`--snapshot`/`--baseline`: a
named **run directory** that remembers by itself which devices belong to the migration, how
their ports pair up, and which snapshots have already been captured. It suits port-by-port
migration (LAG by LAG, customer by customer), multi-phase runs (`pre` → `post`, and
`rollback` when a migration is reverted), and anywhere manual tracking of `pre.json`/
`post.json` files would stop being manageable.

Files under `runs/<name>/` normalize the port by replacing `-`/`/` with `_`
(`ge-0/0/0` → `ge_0_0_0`): `inventory_<node>_<port|all>.yml`,
`snapshot_<pre|post|rollback>_<node>_<port|all>.json`. A capture without `--port`
(whole-box mode) uses `all` instead of a port name.

`run.yml` is the **single source of truth** for the migration; it can be written by hand as a
migration plan (`mig-validate` then only reads it and fills in `captures`), or it can grow
incrementally from `capture --run ...` calls. **Several `interface_mapping` entries may share
the same `new` port** — N:1 (LAG) mapping, several old ports migrating onto one new LAG port:

```yaml
schema_version: 1
kind: migration

devices:
  MX1-POP1:  {host: 172.20.20.4, platform: junos,     role: old}
  PTX1-POP1: {host: 172.20.20.5, platform: junos-evo, role: new}

interface_mapping:
  - old: {node: MX1-POP1, port: ge-0/0/0}
    new: {node: PTX1-POP1, port: ae0}
  # a second old port migrating onto the same new LAG port ae0
  - old: {node: MX1-POP1, port: ge-0/0/1}
    new: {node: PTX1-POP1, port: ae0}

captures:                          # the application maintains this section
  - phase: pre
    device: MX1-POP1
    port: ge-0/0/0
    snapshot: snapshot_pre_MX1-POP1_ge_0_0_0.json
    taken: "2026-08-06T09:12:03Z"
```

`kind` is `migration` (the default when missing) or `single`. A migration run has one box of
role `old` and one of role `new`. A `single` run has exactly one device of role `single` and no
`interface_mapping`; its post (and rollback) snapshot is compared against the box's own pre
snapshot (upgrade, in-place reconfiguration). Optional `profile: <name>` records which profile
from `profiles/` the run uses (missing = server default); `group: <string>` is reserved for bulk
creation of single runs and is not written by anything yet.

Full flag tables, pairing rules and the `status` subcommand are in
[reference.md, section 8](reference.md#8-run-management---run). Highlights of the
LAG-migration-steps behaviour added on top of run management:

- **N:1 mapping.** `evaluate --run` produces **one report per migration step** (one per
  `interface_mapping` entry), not one per `post` capture on the shared LAG port. Each report's
  header, and the `===` separator above it, carry
  `[krok OLD_NODE:OLD_PORT -> NEW_NODE:NEW_PORT]`. `evaluate --run`'s `--ports` filter matches
  the step's **old** port on any mapped step (1:1 included, not just N:1/LAG), not the new
  port; unmapped/whole-box evaluations are filtered by the snapshot's own port.
- **Filter-through-baseline.** Services on the LAG port that did not pair with a given step's
  baseline are excluded from that step's checks; the report adds a summary line
  `Dalsi sluzby na <port> mimo tento krok: N (nesparovano s baseline <old port>)`. The JSON
  result carries the additive `step` key whenever a step exists, and the additive
  `excluded_services` key only when a step exists **and** a baseline was found for it.
- **Whole-box `pre` as a shared baseline.** When a step's own old-port `pre` snapshot is
  missing and the whole-box `pre` snapshot of the old device is used instead (see pairing
  rules above), that same whole-box snapshot becomes the baseline for **every** step sharing
  the LAG port — so services belonging to other waves can pair up in more than one step's
  report at once. Nothing is being hidden; the filter-through-baseline just cannot narrow the
  step, because a whole-box baseline does not distinguish ports.
- **`--parse-services`** always regenerates the inventory now and prints a delta
  (`inventory pregenerovana: ...`/`inventory vyrobena: ...`) — the old "generation skipped"
  behaviour is gone.
- **`capture --run --phase pre`** on an already-captured node/port now fails with
  `pre snimek uz existuje: ...; prepis povol s --overwrite`; the new `--overwrite` flag allows
  the replacement.
- **Ping targets for a post capture on a mapped LAG port** are the union of ARP/ND across all
  of that port's mapped `pre` snapshots, deduplicated by IP.

*Coverage note:* this section is a compact companion to `docs/cs/README.md` section 3a, not a
full translation (e.g. it omits `run.yml`'s device-discovery and inventory-source details).
Closing that residual EN/CS gap is tracked as follow-up debt, not part of the
LAG-migration-steps feature.

### Archiving and purging runs

The GUI can archive a run (*Archive run* on the run overview): the directory
moves to `runs/.archive/<name>-<UTC time>/`, disappears from the list and keeps
its data. The archive is cleaned from the shell:

```bash
mig-validate run purge                       # list the archive only
mig-validate run purge --older-than 30       # delete entries older than 30 days, asks first
mig-validate run purge --older-than 30 --yes # no prompt
```

Profiles are managed in the GUI under *Profiles*: named files `profiles/<name>.yml` are
created, duplicated and deleted in the editor, and the `profile:` section (collectors,
service types, ping count) has a form that shows the defaults. A run picks its profile in
*New run*; `(default)` is the server profile from `--profile` and is read-only in the GUI.

Restore = move the directory back into `runs/` and rename it to the original name.

---

## 4. Reading the output

Real output from a lab run (trimmed — the summary table actually has 11 rows, only a
selection is shown here; 10 of those 11 services are not `PASS` and therefore auto-expand
into a full block — only one is shown, the other nine are omitted):

```
Migrace: 172.20.20.4 (pre-migration) -> 172.20.20.5 (post-migration)

  Sluzby:  1 PASS   6 WARN  4 FAIL  0 SKIP
  Checky: 83 PASS  32 WARN  8 FAIL  9 SKIP
  Sparovano 8 sluzeb, 2 nesparovana v baseline, 3 nesparovane v subject

STAV  SLUZBA                               TYP      STARY PORT   NOVY PORT    RI                        NALEZ
WARN  EVPN-VPWS-CPE13-NNI                  E-Line   ge-0/0/2.213 et-0/0/8.213 EVPN-VPWS-CPE13-NNI       et-0/0/8: input_pps kleslo o 100 % (9 -> 0), prah je -60 %
FAIL  INTERNET-CPE13-NNI                   Internet ge-0/0/2.13  et-0/0/8.13  -                         152.11.13.2: stav Connect, ocekavano Established
FAIL  L3VPN-CPE13-NNI                      IPVPN    ge-0/0/2.113 et-0/0/8.113 L3VPN-CPE13-NNI           198.11.13.2: stav Connect, ocekavano Established
PASS  svc:lo0.0:Core                       Core     -            lo0.0        -

======================================================================================================================
 FAIL  INTERNET-CPE13-NNI   Internet   ge-0/0/2.13 -> et-0/0/8.13   RI: -
======================================================================================================================
 STAV | CHECK                                      : POST (et-0/0/8.13)                      | ZMENA PROTI ge-0/0/2.13
 -----+--------------------------------------------+-----------------------------------------+------------------------
 PASS | Interface errors (et-0/0/8)                : bez chyb                                |
 PASS | Interface errors (et-0/0/8.13)             : bez chyb                                |
 PASS | Interface admin status (et-0/0/8)          : Up                                      |
 PASS | Interface operational status (et-0/0/8)    : Up                                      |
 PASS | Interface admin status (et-0/0/8.13)       : Up                                      |
 PASS | Interface operational status (et-0/0/8.13) : Up                                      |
 WARN | Interface traffic in (et-0/0/8)            : 0 pps                                   | bylo 9 pps   -100 %
 WARN | Interface traffic out (et-0/0/8)           : 0 pps                                   | bylo 995 pps   -100 %
 PASS | Interface traffic in (et-0/0/8.13)         : 0 pps                                   |
 PASS | Interface traffic out (et-0/0/8.13)        : 0 pps                                   |

 -- IPv4  152.11.13.1/30 ---------------------------------------------------------------------------------------------
 PASS | ARP                                        : 0c:00:ef:5e:df:01 -> 152.11.13.2        |
 FAIL | BGP status                                 : Connect                                 | bylo Established
 WARN | Ping                                       : 0/5  152.11.13.2 neodpovedel            |

 -- IPv6  2001:abcd:11:13::a/127 -------------------------------------------------------------------------------------
 FAIL | BGP status                                 : Connect                                 | bylo Idle
 PASS | ND                                         : 0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b |
 WARN | Ping                                       : 0/5  2001:abcd:11:13::b neodpovedel     |

NESPAROVANO
  baseline  clab-pop-migration-P1;et-0/0/0 (Core)  zadny kandidat na subject
  baseline  svc:lo0.0:Core                 (Core)  zadny kandidat na subject
  subject   EVPN-VLAN-AWARE-INTERNET       (E-LAN)  nova sluzba, chybi baseline
  subject   clab-pop-migration-P2;et-0/0/0 (Core)  nova sluzba, chybi baseline
  subject   svc:lo0.0:Core                 (Core)  nova sluzba, chybi baseline
```

Column headers: `STAV` = status, `SLUZBA` = service, `TYP` = type, `STARY PORT` /
`NOVY PORT` = old/new port (the logical unit from the scope, not the physical parent),
`RI` = routing instance, `NALEZ` = finding. Inside a block: `CHECK` = check, `ZMENA PROTI
<port>` = change against `<port>`. `Sparovano N sluzeb` = "N services paired". `NESPAROVANO`
= "unpaired".

What matters here:

- **The summary table has one row per service and shows only the worst finding.**
- **Every service that is not `PASS` gets its full block printed automatically — no
  `--detail` needed.** `--detail` additionally expands the blocks of `PASS` services too.
- **A block runs top to bottom: interface-bound rows first (state, error counters, traffic),
  then an `IPv4` section, then `IPv6`.** The section header carries the configured addresses
  and, on an IRB, the `VGW` address.
- **A family the service has not configured does not appear at all** — no section and no row.
  An IPv4-only service therefore has no mention of IPv6 anywhere in the report. The price of
  that decision: such a check is indistinguishable from one that passed.
- **Every interface-bound row names its interface in parentheses** (`Interface admin status
  (et-0/0/8.13)`). A scope holds both the physical and the logical interface, so without the
  name a block would carry pairs of rows with identical labels, different values and
  contradictory `ZMENA` columns.
- **The `ZMENA` (change) column** shows `bylo <value>` ("was `<value>`") plus a delta when
  there is one (`-100 %`, `+3`); for checks that have no baseline by definition (`arp_present`,
  `ping_reachability`, `interface_state`, ...) it stays empty. **With no baseline loaded, the
  `ZMENA` column is dropped entirely**, not just left blank.
- Every column's width **is computed from its content** — a long service name, routing
  instance, or IPv6 address is never truncated. That holds for the `NESPAROVANO` section's
  label too.
- **The summary has two named lines because it counts two different units.** `Sluzby:`
  (services) matches the row count of the table below it; `Checky:` (checks) is the total
  across every measurement. The gap is large — 11 services, 132 checks — and while it went
  unlabelled the operator walked away with the number they were not looking at.
- **Filters (`--filter`, `--status`) recompute the summary for the selection shown.** A line
  above the counts names the filter and how many of how many services survived it
  (`filtr: status=FAIL -- 2 z 11 sluzeb`). The `Sparovano` line and the `NESPAROVANO` section
  are **not** recomputed — and the output says so right below the filter line. The machine
  output carries the same record under the `filtered` key.
- **The `NESPAROVANO` section is always printed**, even when everything else is green, and
  **filters do not apply to it.** It is the main safeguard against an overlooked service:
  - `baseline` = the service existed on the old box and is missing on the new one → suspect a
    forgotten migration,
  - `subject` = the new box has something extra → a new or restructured service.
- Statuses are `PASS` / `WARN` / `FAIL` / `SKIP`. **`SKIP` means "not measured"**, not "fine" —
  missing data never yields a `PASS`.

Common message strings, translated:

| Czech string in the output | meaning |
|---|---|
| `et-0/0/8: input_pps 0 pps` | interface traffic-in counter reads 0 pps |
| `stav Connect, ocekavano Established` | state is Connect, expected Established |
| `stav se zmenil Connect -> Established` | state changed from Connect to Established |
| `0/5  <ip> neodpovedel` | 0 of 5 pings answered, `<ip>` did not respond |
| `zadny kandidat na subject` | no candidate on the subject device |
| `nova sluzba, chybi baseline` | new service, no baseline |
| `ambiguous: N kandidatu (...)` | ambiguous: N candidates, so no pairing was made |
| `198.11.13.2/inet.0: pokles advertised 14 -> 3, prah je -10 %` | advertised prefixes on that RIB dropped from 14 to 3, past the −10 % tolerance |
| `bez chyb` | no errors (interface error counters) |
| `bez baseline` | no baseline value for this row, and the baseline demonstrably did not measure the area |
| `beze zmeny (chyba uz v baseline)` | unchanged: the same failure was already in the baseline (row is PASS with the `unchanged_since_baseline` marker, R-3) |
| `novy zaznam (v baseline nebyl)` | new entry: an ARP/ND/ping address or target the measured baseline did not have |

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
| `chyba: snapshot ma schema_version N` | snapshot from a different tool version; **re-capture it**. The data cannot be derived from the old snapshot — a version 2 snapshot contains neither the `routes` nor the `bfd` area, so the new checks would stay silent. This applies to the stored runs `runs/ipv6/` and `runs/ipv6-live-2026-07-29/` as well |
| `ValueError: ... schema_version 2` during `capture` | the inventory YAML comes from an older parser version; **regenerate it** (`mx_parser.py` / `evo_parser.py`). A version 2 inventory has no `static_route` or `bfd` fields, so the service would look as if it carried no intent |
| A service shows `FAIL … neni v tabulce` for a static route | the route **is** in the configuration but missing from the routing table — typically because its next hop became unreachable (a deactivated interface). This is a finding, not a tool error |
| `SKIP … BGP neni Established` on BFD | BFD is configured but the BGP peer has not come up yet. BFD cannot come up without BGP, so the state is not reported as a failure |
| Everything is `SKIP` | typically the collectors failed — inspect `capture.collectors` in the snapshot |
| A service is missing from the listing | it is not a migrated service type (`Internet`, `IPVPN`, `E-Line`, `E-LAN`, `Core`), it is a management interface, or it is in `ignore` in `mapping.yml` |
| Lots of `ambiguous` entries in `NESPAROVANO` | duplicate `description` values on the device — pair them manually via `mapping.yml` |
| `interface_traffic` reports `input_pps 0 pps` / `output_pps 0 pps` on a freshly migrated service | expected if nothing is flowing through the port yet; the check is `advisory`, so WARN, not FAIL |

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
  alike (`input_pps 0 pps`, `zadny kandidat na subject`), because output goes to terminals where
  diacritics cannot be relied upon. That constraint applies to the tool, not to this
  documentation; every Czech string quoted here is verbatim so it can be grepped.
