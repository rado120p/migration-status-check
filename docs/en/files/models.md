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
| `routing_instance_active` | `bool` | `False` when the routing instance is deactivated (`deactivate`) |
| `interface_active` | `bool` | `False` when the interface is deactivated (`deactivate`) |
| `protocol`, `bgp_neighbor`, `bridge_domain`, `customer_vlan` | `list[str]` | |
| `static_route` | `list[dict]` | intent from the configuration: `{rib, prefix, next_hop: list[str]}` |
| `bfd` | `list[dict]` | intent from the configuration: `{peer, minimum_interval, multiplier, source}` |
| `l2_interface` | `list[str]` | (schema 8) L2 access ports of the bridge-domains/vlans this IRB routes — singular field, matching the `bridge_domain`/`customer_vlan` convention; on `Selectors` it is the plural `l2_interfaces` (the builder translates) |
| `mvpn_site` | `list[str]` | (schema 9) the MVPN site role copied from `RoutingInstance.mvpn_site` (`{"sender", "receiver"}`), empty outside MVPN |

`static_route` and `bfd` are **configured intent, not measurement**. They are exactly what
`static_route_status` and `bfd_session_state` compare the table and the session against —
without them there would be no way to tell "configured and not working" from "there was
never anything here". `bfd[].source` is `neighbor` or `group`, depending on which level of
the BGP hierarchy the intent was found at.

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

The inventory carries a top-level `schema_version` key (`INVENTORY_SCHEMA_VERSION = 9`).
`load_inventory()` **rejects any other value outright** rather than tolerating it.

The reason is the same for every bump: a missing field would not surface as an error but as
a green service.

- **1 → 2**: the address fields were renamed by family (`ip_address` →
  `ipv4_address` / `ipv6_address`), so a tolerant read of a stale file would silently return
  a service with no addresses at all: ping would never run, yet the service would still show
  green.
- **2 → 3**: the fields `static_route` and `bfd` were added. A version 2 inventory has
  neither, so `Selectors.static_routes` and `.bfd_peers` would stay empty, the checks would
  have nothing to compare against, and a configured route missing from the table would never
  be reported.
- **3 → 4**: the single `active` field was split into `routing_instance_active` and
  `interface_active` (AR‑20/AR‑21). A tolerant read of a stale file would default both to
  `True`, so a deactivated service would look live and the checks would score FAIL/WARN
  against it instead of SKIPping with the deactivation reason.
- **6 → 7** (2026-08-26 wave): `Core` gained a `service_subtype` (`"transit"` /
  `"loopback"`) — a derived datum from `_classify` in the parsers (`lo0.*` → loopback,
  everything else with an iso/mpls family → transit). The subtype is derived, not optional:
  an old inventory without it would lose the role distinction, and the new protocol checks
  (`isis_adjacency_state` and the others, bound via `service_subtypes`) would not run on the
  old file at all.
- **7 → 8** (2026-09-02 wave): `ServiceEntry` gained `l2_interface` (an IRB routes the L2
  access ports of global or in-instance bridge-domains/vlans — see the Multicast section
  of [parsers.md](parsers.md)) and the subtypes Internet `multicast` / an IPVPN subtype
  for MVPN with an IGMP-only intent (renamed 2026-09-07). A tolerant read of an old
  inventory would silently produce an empty list and a `None` subtype — the report header
  would just lose the `L2: …` note, and subtype-bound checks would not run on the old
  file at all.
- **8 → 9** (2026-09-07 spec): the IPVPN subtype `mvpn` replaces the earlier IGMP-only
  variant (now an IGMP or PIM intent) and `ServiceEntry` gained `mvpn_site` (the site role
  from configuration). An old inventory would still carry the old subtype name, which no
  check matches any more — the `description + type + subtype` pairing rule would drop the
  MVPN service on the baseline side out of the new multicast checks.

After a version bump the inventory therefore has to be **regenerated with the parser**, not
patched by hand.

---

## `scope.py` — the filter over facts

Architecturally the most important file in the package: **a scope is the single place where it
is decided which data belongs to which service.**

### `ScopeKey`

The triple `description` + `service_type` + `service_subtype`. A frozen dataclass, so it can
serve as a dict key — which `builder.py` relies on when detecting duplicates.

### `Selectors`

Lists of strings: `interfaces`, `physical_interfaces`, `routing_instances`,
`bgp_neighbors`, `bgp_neighbors_inactive`, `local_ipv4`, `local_ipv6`, `virtual_gw_v4`,
`virtual_gw_v6`, `vlans`, `bridge_domains`, `lag_members`, `l2_interfaces`, `protocols`,
`mvpn_site`.

**`mvpn_site`** (schema 9, 2026-09-07 spec) carries the MVPN site role copied from
`RoutingInstance.mvpn_site` (`{"sender", "receiver"}`, see [parsers.md](parsers.md)) —
empty outside MVPN. It does not feed fact selection; the `pim_join` check reads it
directly (`scope.selectors.mvpn_site == ["sender"]` distinguishes a sender-only site
with no remote receiver from a broken receiver).
`l2_interfaces` (schema 8, plural — `ServiceEntry.l2_interface` is singular, the builder
translates) carries the L2 access ports of domains routed by an IRB; it **does not** feed
fact selection (an L2 port does not belong to the IRB scope), it only feeds the `L2: …`
note in the report header. Addresses and
virtual-gateway are split by family here too — same as on `ServiceEntry` above — because
ping and the report both need to pick a source/target by the target's family, not by
position in one mixed list.

**`protocols`** (2026-08-26 wave) carries configured intent (`ServiceEntry.protocol`) — which
IGP/signaling protocols the interface should be running per the configuration (in practice
so far just `"pim"`, from `protocols pim interface <name>`). Unlike the other selectors it
**does not filter which facts get selected** — the per-interface areas (`isis_adjacency`,
`ldp_neighbor`, `pim_neighbor`, …) are still selected in `Scope.select()` by interface, not
by this field. It is purely a **gate** read directly by `pim_neighbor_state`
(`"pim" not in ctx.scope.selectors.protocols` → the check stays silent, no rows, not SKIP) —
see [checks.md](checks.md#core_protocolspy--core-transit-and-lo00-protocols-2026-08-26-wave).

Plus two lists of dictionaries carrying **configured intent**:

| selector | item shape | role |
|---|---|---|
| `static_routes` | `{rib, prefix, next_hop}` | both a **filter** (a route belongs to the scope when the `(rib, prefix)` pair matches) and the **set** the check uses to notice that a configured route is missing from the table |
| `bfd_peers` | `{peer, minimum_interval, multiplier, source}` | **intent only** — sessions are selected via `bgp_neighbors` |

That `bfd_peers` is not used as a filter is deliberate: these are two different things and
merging them into one list would mean keeping them in sync. A session for a peer the parser
failed to record as intent would never reach the scope, and a gap in the parser would
disappear without a trace.

`matches_interface(name)` returns `True` when the name is among the logical **or** the
physical interfaces. That is what lets state checks run on the physical parent (`et-0/0/8`)
as well, provided the inventory contains the corresponding `Layer1` entry.

### `Scope` and `Scope.select()`

```python
scope.select(facts, probes) -> dict   # keys: interfaces, arp, nd, bgp,
                                      #       evpn_vpws, evpn_esi, evpn_mac,
                                      #       routes, bfd, ping, optics,
                                      #       isis_adjacency, isis_interface,
                                      #       isis_overview, ldp_neighbor,
                                      #       pim_neighbor, mpls_interface
```

The module constant **`FACT_AREAS`** enumerates the areas allowed in facts. The device scope
returns every area on that list unchanged, so **an area forgotten in it would vanish in
inventory-less mode**.

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
| `routes` | the `(RIB, prefix)` pair `∈ selectors.static_routes` |
| `bfd` | `peer ∈ selectors.bgp_neighbors`, **plus** (2026-08-26 wave) `matches_interface(data["interface"])` on a Core scope with `service_subtype == "transit"` — a second path alongside the existing peer-address one, since transit Core has neither BFD intent nor peers in the service configuration; there a session belongs by interface |
| `optics` | `matches_interface(name)` or `name ∈ selectors.lag_members` |
| `isis_adjacency`, `isis_interface`, `ldp_neighbor`, `pim_neighbor`, `mpls_interface` | `matches_interface(name)` — same as `interfaces`/`optics` (2026-08-26 wave) |
| `isis_overview` | a **device-global fact**, not per-interface — only a Core scope with `service_subtype == "loopback"` gets it (empty dict otherwise); the device scope passes everything through unchanged (2026-08-26 wave) |
| `multicast_route`, `pim_join` | **belong to the instance, not the interface** (`_instance_table()`): a scope with no RI gets `master`, a scope with an RI gets its own table — only for the roles that measure multicast (Internet, IPVPN, Core/loopback); an empty dict otherwise. Filtering per (S,G) / per interface (upstream as well as downstream) is left to the check (`pim_join` scoped by instance the same way as `multicast_route`, 2026-09-07 spec) |
| `ping` | `probe["scope_id"] == scope.id` |

Two points worth stressing:

- **`routes` are filtered on the pair, not on the prefix alone.** The same prefix can exist
  in several RIBs (typically `0.0.0.0/0`), and selecting by prefix alone would hand a service
  someone else's route. Empty tables are not returned — they would say nothing in the report
  and the check would have to skip them.
- **`bfd` is filtered by `bgp_neighbors`, not by `bfd_peers`.** Selecting by intent would
  mean a session for a peer the parser failed to record never reaches the scope — and a gap
  in the parser would disappear without a trace.

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

Current `SCHEMA_VERSION = 13` (`models/snapshot.py`). The bump from 5 to 6 carried the
normalization of ARP/ND records learned over an IRB (`interface` + `learned_via` instead of
the untrimmed `irb.14[ ae0.14 ]`, see `collectors.md`) and a new `evpn_vpws` schema
(`interfaces`/`local_sid`/`remote_sid`/`peers` instead of a flat
`status`/`local_sid`/`remote_sid`). The bump from 10 to 11 (2026-08-26 wave) added six new
fact areas to `FACT_AREAS` (`isis_adjacency`, `isis_interface`, `isis_overview`,
`ldp_neighbor`, `pim_neighbor`, `mpls_interface`) — see [collectors.md](collectors.md) for
the shape of each area. The bump from 11 to 12 (2026-09-02 wave) added three multicast
fact areas (`igmp_group`, `multicast_route`, `mvpn_instance`) — see the `multicast.py`
section of [collectors.md](collectors.md). The bump from 12 to 13 (2026-09-07 spec) added
a fourth multicast fact area, `pim_join` (the PIM join table, keyed the same way as
`multicast_route` — instance → `"source,group"` → payload), to `FACT_AREAS`. Old snapshot
data therefore has to be recaptured, not patched by hand.

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
