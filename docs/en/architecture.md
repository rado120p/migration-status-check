# Architecture and file interactions

This document describes **how the parts talk to each other** and the rules that hold it
together. Per-file descriptions live in [files/](index.md#file-coverage); the operating
manual is [README.md](README.md).

---

## 1. Layers and dependency direction

```
                     ┌── touches the net ──┐
  connection/junos ──►  collectors/*     ──┐
        │            │  probes/ping      ──┤
        └────────────┘                     ├──►  Snapshot (JSON on disk)
                                           │
  models/inventory ──► scoping/builder ────┘

  ───────── the network boundary: above it, nothing ever connects again ─────────

  Snapshot ──► scoping/matcher ──┐
  Snapshot ──► models/scope ─────┼──►  engine  ──►  checks/*  ──►  models/result
                                 │                                     │
                                 └── config ──────────────────────────┐ │
                                                                      ▼ ▼
                                                                 reporting/*
```

| layer | may | may not |
|---|---|---|
| `connection` | connect, send RPCs, return XML | interpret the content |
| `collectors` | XML → structured dict | decide PASS/FAIL, know about services |
| `probes` | active tests (ping) | anything else |
| `scoping` | inventory → scopes, pair old ↔ new | read collected data |
| `checks` | pure function `(facts, scope) → Finding` | touch the network |
| `reporting` | results → text / JSON | evaluate anything |

The rule is also enforced by imports: the docstring of `checks/base.py` explicitly forbids
importing anything from `migration_validator.connection`, and no module under `checks/`
violates it.

---

## 2. Two paths through the code

### Path A — `capture` (touches the network)

```
cli._cmd_capture
  └─ api.capture
       ├─ connection.junos.connect ....... opens a PyEZ Device, translates errors
       └─ capture.capture_device
            ├─ connection.junos.detect_platform ..... junos | junos-evo
            ├─ collectors.registry.collectors_for ... picks collectors for the platform
            ├─ collector.collect(device, platform) .. per area → facts[area]
            ├─ scoping.builder.build_scopes ......... inventory → [Scope]
            ├─ probes.ping.resolve_targets .......... scopes + facts["arp"] → targets
            ├─ probes.ping.run_ping ................. active ICMP, per target
            └─ models.snapshot.Snapshot ............. + save_snapshot() to disk
```

The order inside `capture_device` is **not arbitrary**: ping needs targets derived from ARP,
so the bulk collection must happen first. That entire dependency plays out **here**. The
finished ping result is stored in the snapshot as ordinary data — which is why, at evaluation
time, checks are mutually independent and can run in any order.

### Path B — `evaluate` (never touches the network)

```
cli._cmd_evaluate
  ├─ models.snapshot.load_snapshot ....... subject (and optionally baseline)
  ├─ scoping.mapping.load_mapping ........ mapping.yml
  ├─ config.load_config .................. config.yml
  └─ api.evaluate
       ├─ checks.all.load_all ............ import = check registration
       └─ engine.evaluate_snapshots
            ├─ scoping.matcher.match_scopes ...... baseline scopes ↔ subject scopes
            ├─ Scope.select(facts, probes) ....... filters data down to the scope
            ├─ engine._aligned_baseline_data ..... renames baseline interface keys
            ├─ checks.base.run_check ............. Finding → CheckResult (status)
            └─ models.result.RunResult
  └─ reporting.text_report.render / json_report.to_json
```

---

## 3. The contracts it all rests on

This is the heart of the matter. These are not imports but **agreements between layers** —
break one and the tool usually will not crash; it will just quietly stop measuring.

### 3.1 The fact schema is a hard interface between collector and check

A collector writes data under a key, `Scope.select()` filters by that same key, and the check
looks for it there. If a collector renames a key, the check receives nothing and returns
`SKIP`. **And because `SKIP` is not `FAIL`, nothing else shows it** — the tool silently stops
checking an entire area.

| area | shape | filtered by |
|---|---|---|
| `interfaces` | `{ifname: {admin_status, oper_status, input_pps, output_pps, input_errors, output_errors, framing_errors}}` | interface name (logical unit as well as physical parent) |
| `arp` | `[{ip, mac, interface, routing_instance}]` | `interface` |
| `bgp` | `{peer_ip: {state, peer_as, routing_instance, prefixes: {received, accepted, advertised}}}` | `peer_ip` ∈ `bgp_neighbors` |
| `evpn_vpws` | `{routing_instance: {status, local_sid, remote_sid}}` | **key = routing instance** |
| `evpn_esi` | `{esi: {status, df_role, interface}}` | `interface` |
| `evpn_mac` | `{routing_instance: {vlan_id: count}}` | **key = routing instance**; the inner key is the **VLAN id** as a string (`"313"`), or `"-"` for vlan-based |

Two places where the keying is deliberately non-obvious:

- **`evpn_vpws` and `evpn_mac` are keyed by routing-instance name, not by interface.** The
  instance name is stable across a migration; the port name is not.
- **`evpn_mac` uses the VLAN id as its inner key, not the domain name.** MX calls the same
  domain `BD-313` while EVO calls it `VL-313`; keying by name would mean that after the
  migration the check finds no counterpart in the baseline and prints state instead of
  comparing MAC counts.

The safeguard is `tests/collectors/test_conformance.py`: it builds facts with the **real
collectors from recorded lab XML**, runs them through the **real checks**, and asserts that
the result is not all `SKIP`. It is the only test that pins both halves of the seam against
each other.

### 3.2 `Scope.select()` is the only filter

A check never sees whole-device data — it receives it pre-filtered. As a result it cannot
accidentally reach into another service, and it has **no branch at all for "with inventory /
without inventory"**:

| mode | scope | what the check receives |
|---|---|---|
| no inventory | one `device` scope with empty selectors | all data, unfiltered |
| with inventory | one scope per service | only that service's interfaces / RIs / peers / VLANs |

Without an inventory, `bgp_session_state` says "11 of 12 peers are Established"; with one,
the same broken down per service. Same code, same JSON shape, different granularity.

### 3.3 Facts are inventory-independent, probes are not

Bulk collection runs a fixed set of RPCs per platform, so the `facts` block looks the same
with or without an inventory. Ping, however, needs a target and a source address — both of
which come from a scope. **In device mode it therefore does not run at all** and the snapshot
carries `probes.ping: []`.

### 3.4 Interface-name alignment when comparing (AR‑6b)

`interface_traffic` compares baseline against subject **by interface name**. But a migration
renames the port (`ge-0/0/2.113` → `et-0/0/8.113`), so the baseline data sits under a
different key from the one the check looks up on the subject.

`engine._aligned_baseline_data()` therefore **renames the baseline interface keys to the
subject's names** for a matched pair before handing data to the checks. The mapping is
positional (`zip(baseline.selectors.interfaces, subject.selectors.interfaces)`) and is
unambiguous because a service scope carries exactly one logical interface in its selectors
(`scoping/builder.py`).

Without it, the comparison would quietly fall back to state mode and the signal about a
traffic drop would be lost precisely for the services whose interface was renamed.

> The spec (AR‑6b) recommends guarding the "one interface per scope" invariant with an
> assert so it would fail loudly if it ever broke. No such assert exists in the code — `zip`
> would simply align the first pair and drop the rest. This is an observation about the
> current state, not a call to change it.

Alignment applies to interfaces only. BGP is keyed by peer IP and EVPN by instance name, and
both stay stable across a migration.

### 3.5 Status derivation lives in one place

A check **does not return a status**. It returns a `Finding` carrying the measurement
(`Outcome`), and the framework derives the status in `models/result.py::derive_status()`:

| check returns | severity `critical` | severity `advisory` |
|---|---|---|
| `ok` | PASS | PASS |
| `degraded` (partial success) | **WARN** | **WARN** |
| `broken` | FAIL | WARN |
| `skip(reason)` | SKIP | SKIP |

The rule *"partial success = WARN"* is thereby written once instead of in every check. Ping
reaching 2 of 3 addresses is a WARN even when ping is configured as `critical`. The author of
a new check cannot accidentally break this convention.

One related rule lives in `engine.py`: **a scope's status is the worst status of its checks,
but `SKIP` only wins when there is nothing better to report.** Without that condition, a
healthy IPVPN service in baseline-less mode would show `SKIP` merely because
`bgp_prefix_counts` is compare-only — and the operator would lose the green signal exactly
for the services with the most checks.

### 3.6 Missing data never yields a PASS

The overriding rule of the whole tool. Its implementation is spread across layers, and it is
worth seeing together:

| situation | where | behaviour |
|---|---|---|
| connection failed | `connection/junos.py` | `JunosConnectionError` → no snapshot is written, exit code 2, the message distinguishes auth / timeout / refused |
| one collector's RPC failed | `capture.py` | the area stays empty with the correct type, the error is recorded in `capture.collectors` |
| a check needs an area that failed | `checks/base.py::run_check` | `SKIP` carrying the **original error message** |
| comparison check without a baseline | `checks/base.py::run_check` | `SKIP` with a reason |
| check requires an inventory the snapshot lacks | `checks/base.py::run_check` | `SKIP` with a reason |
| the check itself raises | `checks/base.py::run_check` | `SKIP` — one broken check must not kill the run |
| snapshot has a different `schema_version` | `models/snapshot.py` | `SnapshotVersionError` → exit code 2, both versions printed |
| ping does not get through | `probes/ping.py` | **not an error** — it is a normal measurement result |

### 3.7 Registries are populated by import

Collectors and checks register themselves via the `@register` decorator **when their module
is imported**. Somebody has to import them:

- `collectors/all.py` imports `arp`, `bgp`, `evpn`, `interfaces`; `capture.py` imports it at
  module level.
- `checks/all.py` imports `bgp`, `evpn`, `ifaces`, `reachability`; `api.evaluate()` and
  `api.list_checks()` call `load_all()` explicitly, so the registry is populated even when a
  GUI or a test calls the API directly without going through the CLI.

`checks/all.py` is a **separate module rather than `__init__.py`, to dodge a circular
import**: `checks/ifaces.py` imports `checks/base.py`, so `checks/__init__.py` must not
import `ifaces`. The same reasoning applies to `collectors/all.py`.

The practical consequence: **a collector forgotten in `all.py` would silently drop an entire
area.** `tests/test_capture.py::test_every_area_is_registered_for_both_platforms` guards
against that.

### 3.8 It never guesses

On ambiguity, the matcher **does not pair**. If a rule yields more than one candidate, the
scope goes to `unmatched` with the reason `ambiguous: N kandidatu (...)`. A silently wrong
match would, during a migration, mean a green light on a broken service.

Symmetrically, nothing is discarded quietly:

- `unmatched` (services with no counterpart) is a first-class output and is **always** printed
  in the text report, even when everything else is green and even when a filter removed every
  scope;
- `unassigned.bgp_peers` holds peers that could not be attributed to any service.

---

## 4. Platform differences are the collector's job

MX and EVO differ in RPCs and in response shape. **The entire difference is absorbed inside
the collector**; externally both platforms return the same schema — which is why no check
contains `if platform == "evo"`. That is the premise the cross-device comparison rests on.

| area | Junos (MX) | Junos EVO (ACX/PTX) |
|---|---|---|
| MAC table | two RPCs: `get_bridge_mac_table` (vlan-aware) + `get_evpn_mac_table` (vlan-based) | one RPC: `get_mac_vrf_mac_table` (both at once) |
| MAC entry shape | `l2ald-*` | `l2ng-l2ald-*` |
| domain name | `BD-313`, vlan-based `__EVPN-VLAN-BASED-...__` | `VL-313`, vlan-based `VL-NONE` |
| BGP `peer-address` | `150.0.0.1+179` | `150.0.0.1+57010` (ephemeral port) |

Platform detection (`connection/junos.py::detect_platform`) looks for `EVO` in the version
string, then falls back to a model prefix (`PTX10`, `ACX7`, `QFX5700`, `MX304`).

`Collector.rpc_names()` exists because of the first row of that table: a collector with more
than one RPC must override it, otherwise `record` and `--record-raw` would store only the
first one and the recorded fixtures would be silently incomplete.

---

## 5. The seam for a future GUI

`api.py` is **the only thing a GUI will call**. `cli.py` is a thin wrapper over it, not an
alternative implementation — whatever the CLI can do, a GUI can do, because they go the same
way.

Several concrete properties follow:

- every result is JSON-serialisable (`to_dict()` on each model),
- `list_checks()` exists so a GUI does not need a hard-coded list of tests: add a check to the
  registry and it appears in both interfaces,
- `render()` and `to_json()` only format — **they compute nothing**. The summary and each
  scope's status are computed by the engine, so the terminal and a GUI cannot drift apart.

---

## 6. Verified against the lab

The collectors were not written blind; the XPaths were verified against recorded XML from the
containerlab topology `pop-migration`:

| address | model | version | platform |
|---|---|---|---|
| `172.20.20.4` | VMX | `24.2R1-S2.5` | `junos` |
| `172.20.20.5` | PTX10002-36QDD | `25.2R1.8-EVO` | `junos-evo` |

Recordings from both platforms live in `tests/fixtures/rpc/{junos,junos-evo}/` and serve as
regression fixtures. Lab credentials are not in the repository — the environment is described
in `docs/superpowers/plans/2026-07-24-migration-validator-capture.md`.

---

## 7. Architecture decisions

The full reasoning is in the specification
(`docs/superpowers/specs/2026-07-24-migration-validator-design.md`, in Czech); this list is
here so you can trace why something is the way it is:

| # | decision |
|---|---|
| AR‑1 | Plain PyEZ and a bespoke framework — not pyATS, not JSNAPy |
| AR‑2 | Snapshot-centric model: `capture` → `evaluate` |
| AR‑3 | `evaluate` with an optional baseline (state vs. comparison checks) |
| AR‑4 | The inventory is an optional layer, not a mandatory input — realised via the `Scope` abstraction |
| AR‑5 | Collection is device-scoped, correlation is service-scoped |
| AR‑6 | Hard separation of collector and check |
| AR‑6b | Interface-name alignment when comparing |
| AR‑7 | Platform differences are handled by the collector, not the check |
| AR‑8 | The existing parsers stay unchanged; the validator consumes their YAML |
