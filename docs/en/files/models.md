# `models/` — data models

Files: `inventory.py`, `scope.py`, `snapshot.py`, `result.py` and an empty `__init__.py`.

Every model is a `@dataclass` and every model has `to_dict()` (plus `from_dict()` where
needed), because **all output must be JSON-serialisable** — a precondition for the future GUI.

---

## `inventory.py` — the input from the parsers

The model of the YAML produced by `mx_parser.py` / `evo_parser.py`.

### `ServiceEntry`

One entry = one interface and the service running on it.

| field | type | note |
|---|---|---|
| `interface` | `str` | required, a logical unit or a physical port |
| `service_type` | `str` | required (`Internet`, `IPVPN`, `E-Line`, `E-LAN`, `Core`, `Layer1`, ...) |
| `description` | `str \| None` | the configuration description — carries most of the pairing weight |
| `service_subtype` | `str \| None` | `vpws`, `vlan-aware`, `vlan-based`, `physical-port`, ... |
| `ipv4_address` | `list[str]` | IPv4 addresses with prefix |
| `ipv6_address` | `list[str]` | IPv6 addresses with prefix |
| `virtual_gw_ipv4_address` | `list[str]` | IPv4 virtual-gateway-address on IRB |
| `virtual_gw_ipv6_address` | `list[str]` | IPv6 virtual-gateway-address on IRB |
| `routing_instance` | `str \| None` | |
| `active` | `bool` | |
| `protocol`, `bgp_neighbor`, `bridge_domain`, `customer_vlan` | `list[str]` | |

The `physical_name` property returns the part before the dot (`ge-0/0/2.113` → `ge-0/0/2`).

`from_dict()` is deliberately **tolerant of unknown keys** — the parser also emits
`detection_confidence` and `detection_reason`, which the validator does not need, so
extending the parser will not break loading. It fails hard only on a missing `interface` or
`service_type`.

The helpers `_as_list()` / `_as_optional_str()` normalise a scalar into a list and numbers
into strings, so a VLAN written as the number `113` does not blow up.

### `Inventory` and `load_inventory()`

`Inventory` = `device` (address) + `entries`. `load_inventory(path)` reads YAML and requires a
mapping with an `interfaces` key; otherwise it raises `ValueError` with the file path in the
message.

The inventory carries a top-level `schema_version` key (`INVENTORY_SCHEMA_VERSION = 2`).
`load_inventory()` **rejects any other value outright** rather than tolerating it — the
address fields were renamed by family (`ip_address` → `ipv4_address`/`ipv6_address`), so a
tolerant read of a stale file would silently return a service with no addresses at all: ping
would never run, yet the service would still show green.

---

## `scope.py` — the filter over facts

Architecturally the most important file in the package: **a scope is the single place where it
is decided which data belongs to which service.**

### `ScopeKey`

The triple `description` + `service_type` + `service_subtype`. A frozen dataclass, so it can
serve as a dict key — which `builder.py` relies on when detecting duplicates.

### `Selectors`

Ten lists: `interfaces`, `physical_interfaces`, `routing_instances`, `bgp_neighbors`,
`local_ipv4`, `local_ipv6`, `virtual_gw_v4`, `virtual_gw_v6`, `vlans`, `bridge_domains`.
Addresses and virtual-gateway are split by family here too — same as on `ServiceEntry` above
— because ping and the report both need to pick a source/target by the target's family, not
by position in one mixed list.

`matches_interface(name)` returns `True` when the name is among the logical **or** the
physical interfaces. That is what lets state checks run on the physical parent (`et-0/0/8`)
as well, provided the inventory contains the corresponding `Layer1` entry.

### `Scope` and `Scope.select()`

```python
scope.select(facts, probes) -> dict   # keys: interfaces, arp, nd, bgp,
                                      #       evpn_vpws, evpn_esi, evpn_mac, ping
```

Filtering per area:

| area | by what |
|---|---|
| `interfaces` | `matches_interface(name)` |
| `arp` | `matches_interface(entry["interface"])` |
| `nd` | `matches_interface(entry["interface"])` |
| `bgp` | `peer ∈ selectors.bgp_neighbors` |
| `evpn_vpws` | key (instance name) `∈ selectors.routing_instances` |
| `evpn_esi` | `matches_interface(data["interface"])` |
| `evpn_mac` | key (instance name) `∈ selectors.routing_instances` |
| `ping` | `probe["scope_id"] == scope.id` |

The **device scope** (`kind: "device"`, empty selectors) is a shortcut: it returns every area
unchanged. That is how the inventory-less mode is realised without a single extra branch in
the checks.

`select()` tolerates missing areas — `facts.get(area) or {}` returns empty instead of raising,
so a snapshot taken with some collectors disabled can still be evaluated.

`device_scope()` produces that single scope for the inventory-less mode.

---

## `snapshot.py` — the frozen device state

### `DeviceMeta`

`address`, `hostname`, `platform` (`junos` | `junos-evo`), `model`, `version`,
`uptime_seconds`. The last one is always `None` so far —
`connection/junos.py::device_meta()` does not populate it.

### `CaptureMeta`

`started_at`, `finished_at`, `phase`, `collectors`. The **`failed_collectors()`** method
returns `{name: error message}` for those that did not finish `ok` — and that is exactly the
channel through which a failed collection turns into a `SKIP` in the checks
(`CheckContext.failed_collectors`).

### `Snapshot`

`device`, `capture`, `facts`, `probes`, `scopes`, `inventory`, `schema_version`.

`from_dict()` **rejects a different `schema_version` outright** (`SnapshotVersionError`
printing both versions). No attempt is made to migrate old data: loud failure beats a silent
misinterpretation.

`save_snapshot()` / `load_snapshot()` write and read UTF‑8 JSON with `ensure_ascii=False` and
create the target directory. The disk round-trip is asserted by
`tests/test_capture.py::test_snapshot_round_trips_to_disk`.

---

## `result.py` — the result models

### `Status` and `Severity`

`Status`: `PASS` / `SKIP` / `WARN` / `FAIL`, with an ordering used by `worst()`:

```
PASS (0)  <  SKIP (1)  <  WARN (2)  <  FAIL (3)
```

`SKIP` is therefore "worse" than `PASS` — not measured is not fine. `Status.worst()` of an
empty set returns `SKIP`.

`Severity`: `critical` | `advisory`.

### `Outcome` and `derive_status()`

`Outcome` is what **the check measures**: `ok` / `degraded` / `broken` / `skip`.
`derive_status(outcome, severity)` turns it into a `Status`:

| outcome | critical | advisory |
|---|---|---|
| `ok` | PASS | PASS |
| `degraded` | **WARN** | **WARN** |
| `broken` | FAIL | WARN |
| `skip` | SKIP | SKIP |

`degraded` is **always** WARN, even at severity `critical`. The rule "partial success = WARN"
is thus written once, in one place.

### `Finding` → `CheckResult`

`Finding` is a check's raw output: `outcome`, `message`, `label`, `family` (4 / 6 / `None`),
`value`, `baseline_value`, `delta`, `baseline`, `subject`, `details`. `CheckResult` is the
same thing **after status derivation**, plus `id`, `mode` and `severity`. The conversion is
done by `checks/base.py::run_check()`, not by the check itself.

`label`/`value`/`baseline_value`/`delta` exist for the report: splitting a measured value
into a label and a value for the report's columns has to be done by the check, because only
the check knows what counts as the value for a given quantity and what counts as
explanation — the renderer never parses numbers back out of `message`. `family` places a
finding into the `IPv4`/`IPv6` section in the text report (`reporting/view.py`); `None`
marks a row bound to the interface itself, not to an address (e.g. interface state).

`CheckResult.to_dict()` omits empty optional keys, so the JSON is not flooded with `null`.

### `ScopeResult` and `RunResult`

`ScopeResult`: `scope_id`, `key`, `status`, `match` (`MatchInfo | None`), `checks`, `identity`.

`identity` (filled in by `engine.py::_identity()`) carries everything the report needs about
a service that would otherwise stay inside the `Scope`: `description`, `service_type`,
`service_subtype`, `routing_instance`, `ipv4`, `ipv6`, `virtual_gw_v4`, `virtual_gw_v6`. The
renderer never sees scopes, only the `RunResult`, so without this the address and
virtual-gateway columns would have nowhere to read from.

`MatchInfo`: `status` (`matched` | `unmatched`), `method`, `confidence`,
`baseline_interfaces`, `subject_interfaces`, `reason`.

`RunResult`: `evaluated_at`, `subject`, `baseline`, `summary`, `scopes`, `unmatched`,
`unassigned`, `filtered`, `schema_version`. It is the only thing reporting receives —
**everything is computed beforehand**, so the text output and a GUI cannot disagree on the
numbers.

`filtered` is `None` for a whole run and a dict (`scopes_shown`, `scopes_total`, optionally
`text` and `statuses`) for a result that went through `filter_result()`. In the first case
`to_dict()` omits the key entirely, so unfiltered output keeps exactly the shape it had.

`count_statuses(statuses)` breaks any statuses down into the four counters. It takes statuses
rather than checks precisely so that the per-check summary (engine, filter) and the
per-service summary (renderer) can share it — and the difference between those two units was
what went unlabelled in the report.
