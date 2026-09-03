# `collectors/` — collecting operational state

Files: `base.py`, `registry.py`, `all.py`, `interfaces.py`, `arp.py`, `nd.py`, `bgp.py`,
`evpn.py`, `routes.py`, `bfd.py`, `isis.py`, `ldp.py`, `pim.py`, `mpls.py`
and an empty `__init__.py`.

Two rules the entire layer rests on:

1. **A collector never interprets.** It returns raw structured data, never `{"bgp_ok": true}`.
   If a criterion changes, the check changes, not the collection — and old snapshots stay
   usable.
2. **MX vs EVO platform differences are handled here.** Externally both platforms return the
   **same schema**, so no check contains `if platform == "evo"`.

The data shape a collector must honour is a binding contract — see
[../architecture.md](../architecture.md), section "The fact schema is a hard interface between
collector and check".

---

## `base.py` — the abstract collector

```python
class Collector(ABC):
    name: str                     # also the key in facts
    platforms: tuple[str, ...]    # default: both

    def rpc_name(self, platform) -> str          # required
    def rpc_names(self, platform) -> tuple[str]  # default: (rpc_name(),)
    def rpc_kwargs(self, platform) -> dict       # default: {}
    def rpc_calls(self, platform) -> tuple[tuple[str, dict]]  # default: rpc_names × rpc_kwargs
    def parse(self, xml, platform) -> Any        # required
    def collect(self, device, platform) -> Any   # template: RPC + parse
```

`collect()` is a template method and **turns every kind of failure into a `CollectorError`**
whose message says which collector, which RPC and what happened:

| failure | message |
|---|---|
| unsupported platform | `collector 'X' nepodporuje platformu 'Y'` |
| RPC missing on `device.rpc` | `collector 'X': RPC 'Y' neni dostupne - ...` |
| RPC raises anything | `collector 'X': RPC 'Y' selhalo - <type>: <text>` |
| `parse()` raises anything | `collector 'X': parsovani selhalo - <type>: <text>` |

`CollectorError` is caught by `capture.py` — collection continues with the other areas.

**`rpc_names()` is a safeguard for fixture recording.** A collector with several RPCs must
override it, otherwise `record` and `--record-raw` would store only the first one and the
recorded fixtures would be silently incomplete. `EvpnMacCollector` on MX (two different
response shapes) and, since the 2026-08-19 QNH wave, `RoutesCollector` (the same RPC twice,
with a different `protocol`) both do — see the [`routes.py`](#routespy--static-and-aggregate-routes-from-the-routing-table)
section below. The actual authority for `record` is `rpc_calls()` (RPC name + kwargs together,
in call order); `rpc_names()`/`rpc_kwargs()` stay as the simpler interface for a collector
whose calls differ only in RPC name, not in kwargs.

## `registry.py` — the registry

The `@register` decorator stores an **instance** of the class in a module-level dict under
`cls.name`; a duplicate name raises `ValueError`. `all_collectors()` returns a sorted list and
`collectors_for(platform)` filters it down to those supporting the platform.

## `all.py` — populating the registry

Imports `arp`, `bfd`, `bgp`, `evpn`, `interfaces`, `nd`, `routes`, which triggers their
`@register`. It exists
as a separate module (not `__init__.py`) to avoid a circular import.

**A collector forgotten in this file would silently drop an entire area** — checks over it
would return `SKIP` and nothing else would show it. Guarded by
`tests/test_capture.py::test_every_area_is_registered_for_both_platforms`.

---

## `interfaces.py` — interface state and counters

RPC: `get_interface_information` with `extensive=True` (both platforms).

Output: `{name: {admin_status, oper_status, input_pps, output_pps, input_errors,
output_errors, framing_errors}}` — for **both physical interfaces and logical units**, in the
same dict.

Findings verified against recorded lab XML (24.2R1-S2.5 / 25.2R1.8-EVO):

- physical interfaces carry `traffic-statistics`, logical units carry
  `transit-traffic-statistics` — `_rates()` therefore tries both, in that order;
- **a logical unit has no error counters of its own** on either platform → they are filled
  with zeros;
- a logical unit may lack `oper-status` → it inherits it from the physical parent;
  `admin_status` is always inherited.

`input-pps` / `output-pps` are used because **Junos computes them itself — they are a rate,
not a cumulative counter.** No double sampling or waiting window is needed. Absolute byte
counters are deliberately not collected: between two boxes with different uptimes they are
incomparable.

The file also provides the `_text()` and `_int()` helpers imported by the other collectors.
`_int()` copes with a decimal notation and returns 0 for nonsense.

## `arp.py` — the ARP table

RPC: `get_arp_table_information` with `no_resolve=True`.

Output: `[{ip, mac, interface, routing_instance}]`. Entries without an IP or an interface are
dropped.

ARP is **also the data producer for the ping probe** — targets are derived from it during
`capture`.

A note on `routing_instance`: neither MX nor EVO reports it in the response
(`arp-table-entry-flags` carries only `<none/>`), so the key is usually `None`. It is in the
schema on purpose — the contract prescribes it, and the scope filters ARP by `interface`, not
by instance.

## `nd.py` — the ND table (IPv6 neighbours)

RPC: `get_ipv6_nd_information`. The twin of `arp.py` — ping targets are derived from ND the
same way they are from ARP, just for the IPv6 family.

Output: `[{ip, mac, interface, state}]`. Entries without an IP or an interface are dropped.
Unlike `probes/ping.py`, the collector **does not filter entries** — link-local neighbours
and entries with no MAC all come back; deciding what counts as a usable ping target belongs
to the probe, because it depends on the service's configuration, which the collector does
not know.

Verified against the lab: both the RPC and the element names match on vMX and on EVO. MX
wraps text in newlines, EVO does not — `_text()` (shared with `arp.py`/`interfaces.py`)
handles that by stripping.

## `bgp.py` — peer state and prefix counts

RPC: `get_bgp_neighbor_information`. Using the `neighbor` rather than the `summary` variant is
deliberate — the summary does not contain the **advertised** prefix count.

Output: `{peer_ip: {state, peer_as, routing_instance, ribs: {rib_name: {received, accepted,
advertised, active, suppressed}}}}`.

Three things verified against the lab:

- **`peer-address` carries a port** (`150.0.0.1+179` on MX, an ephemeral `150.0.0.1+57010` on
  EVO). `strip_port()` cuts it off — without that, a peer would never meet the `bgp_neighbor`
  entry from the inventory.
- **One peer may have up to 11 RIBs** (`bgp.rtarget.0`, `inet.0`, `bgp.l3vpn.0`, ...) and the
  counts are stored **per RIB, never summed**. Summing them would blend IPv4 and IPv6 into
  one number, and a drop in `inet6.0` offset by a rise in `inet.0` would pass unnoticed.
  `checks/bgp.py::peer_family()` then derives a row's family from the **peer's address**, not
  the RIB name (which need not carry a family at all, e.g. `bgp.l3vpn.0`).
- `peer-cfg-rti` with the value `master` / `default` / empty is normalised to `None`, so the
  default instance does not look like a named VRF.

`peer_as` is converted to `int` only when it really is a number, otherwise `None`.

## `evpn.py` — E-Line and E-LAN

Three collectors in one file. **The most platform-specific logic in the package.**

An implementation note: the plan listed RPC names that exist on neither platform
(`get_evpn_vpws_instance_information`, `get_mac_vrf_forwarding_mac_table`); the correct ones
are below, verified via `| display xml rpc`.

### `EvpnVpwsCollector` (`evpn_vpws`)

RPC: `get_evpn_vpws_information`. Output `{routing_instance: {status, local_sid, remote_sid}}`.

- **The key is the instance name, not the interface** — the instance name is stable across a
  migration; the port name is not.
- `status` is the **instance's interface status** (`Up`), not the remote PE's status
  (`Resolved`). Both values exist in the response and mean different things; the check compares
  against `Up`, so the comparable one is emitted.
- `local_sid` / `remote_sid` are read from the **first interface of the instance** in document
  order. An instance with several interfaces is rare in this topology and the contract types a
  SID as a single number; covering more interfaces would require extending the schema first.

### `EvpnEsiCollector` (`evpn_esi`)

RPC: `get_evpn_instance_information` with **`extensive=True`**. Without `extensive`,
`show evpn instance` returns a summary with no ESI at all and the collector would silently
return nothing.

Output `{esi: {status, df_role, interface}}`.

- `status` is `evpn-esi-local-intf-status` (`Up/Forwarding`) — `evpn-esi-status` is by contrast
  descriptive prose (`Resolved by IFL ae0.14`) that cannot be compared.
- `df_role` is the **IP address of the elected DF**, not this box's role. Deciding "am I the
  DF?" would be interpretation, and that is not a collector's job.
- `interface` is the **logical unit** (`ae0.14`, `irb.14`) — exactly what the scope holds in
  its selectors. The specification anticipated a problem here (Junos supposedly reporting the
  physical name); against the lab the concern did not materialise —
  `evpn-esi-local-intf-name` returns the logical unit directly. The safeguard is the
  conformance test `test_esi_interface_matches_a_scope`.

### `EvpnMacCollector` (`evpn_mac`)

Output `{routing_instance: {vlan_id: count}}`.

**The platform difference is in the number of RPCs:**

| platform | RPCs |
|---|---|
| `junos` | `get_bridge_mac_table` (vlan-aware) **+** `get_evpn_mac_table` (vlan-based) |
| `junos-evo` | `get_mac_vrf_mac_table` (both at once) |

On MX both are required, because each sees a different instance type. On EVO
`show evpn mac-table` does not exist at all, whereas the mac-vrf table returns both. Only the
RPCs that genuinely apply on a given platform are listed — a failure of any of them therefore
signals a real error, not a question about something unknown.

The collector overrides `collect()` to **merge** the RPC results. And it is strict about it:
**a failure of any RPC is a failure of the whole collector.** Returning partial data as `ok`
would mean the check comparing a truncated MAC count against a full baseline and calling it a
collapse. `SKIP` beats silent nonsense.

`parse()` walks **both XML shapes** (`l2ald-*` on MX, `l2ng-l2ald-*` on EVO) in one pass, so it
needs no platform branch and not even a correct `platform` argument — the XML alone suffices,
which keeps the tests simple.

**The domain key is the VLAN id, not its name** (`_normalise_domain()`):

- MX calls the same domain `BD-313` while EVO calls it `VL-313` → keying by name would mean
  the check finds no counterpart in the baseline after the migration and prints state instead
  of comparing MAC counts;
- **a vlan-based instance has no domain of its own** and the contract prescribes `"-"` for it.
  MX gives it away by reporting the VLAN as `none`, EVO by naming the domain `VL-NONE`
  (`_is_no_domain()` catches both: the `__` prefix and the `NONE` suffix). Both platforms do
  report a VLAN id, though, so keying on that alone would leave vlan-based instances
  mismatched.

## `routes.py` — static and aggregate routes from the routing table

RPC: `get_route_information`, called **twice** — once with `{"protocol": "static"}`, once
with `{"protocol": "aggregate"}` (both platforms). The pattern is `InterfacesCollector`
(`extensive` + `terse` — the same RPC name twice, just with different kwargs), not
`EvpnMacCollector` (which calls two **different** RPC names with identical kwargs) — here it
is the protocol filter that changes instead of the response shape. `rpc_calls()` — the new
authoritative method on `collectors/base.py`, shared
by every collector with more than one RPC call — returns both `(rpc_name, kwargs)` pairs;
`record` and `--record-raw` use it to store **both** responses, so the fixtures carry
`routes.xml` (protocol=static) and `routes.2.xml` (protocol=aggregate).

The protocol filter keeps the response small even on a device carrying a full internet table.
`all=True` is **not** used — it only adds `__juniper_private*` tables, which is noise.

`collect()` merges the results of both passes into one table — **strictly additive**: the
second pass (`aggregate`) only fills in prefixes the first pass (`static`) did not bring
(`target.setdefault(prefix, data)`), it never overwrites. A prefix cannot be both static and
aggregate in the same RIB at once, so a collision in identity would mean corrupted data, not a
legitimate update. Failure of **either** pass is an error for the whole collector
(`CollectorError`) — the same reasoning as `EvpnMacCollector`: partial data (statics without
aggregates) would let a check read "the aggregate disappeared", a false alarm.

The output is a two-level dictionary `{RIB: {prefix: {next_hop, via, active, protocol}}}`,
exactly as the collector produces it by merging the `tests/fixtures/rpc/junos-evo/routes.xml`
(static) and `routes.2.xml` (aggregate) recordings:

```json
{
  "inet.0": {
    "198.62.1.0/29": { "next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": true, "protocol": "static" },
    "198.62.2.0/24": { "next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": true, "protocol": "static" },
    "10.1.0.0/23": { "next_hop": [], "via": [], "active": true, "protocol": "aggregate" }
  },
  "inet6.0": {
    "2001:aaaa::/64": { "next_hop": ["2001:abcd:11:13::b"], "via": ["et-0/0/8.13"], "active": true, "protocol": "static" }
  },
  "L3VPN-CPE13-NNI.inet.0": {
    "172.26.1.0/29": { "next_hop": ["198.11.13.2"], "via": ["et-0/0/8.113"], "active": true, "protocol": "static" }
  },
  "L3VPN-CPE13-NNI.inet6.0": {
    "2001:eeee::/64": { "next_hop": ["2001:db8:11:13::b"], "via": ["et-0/0/8.113"], "active": true, "protocol": "static" }
  }
}
```

The `protocol` key carries `protocol-name` from the RPC, lower-cased (`"static"` /
`"aggregate"`) — the checks read it to split into `static_route_status` and
`aggregate_route_status`. A missing `protocol` key means a snapshot taken before schema 10,
when the collector only gathered statics; `checks/routes.py::_flatten()` defaults it to
`"static"` in that case. That default only protects the check's internals — a real old
snapshot file never gets this far, because `Snapshot.from_dict` rejects any
`schema_version != SCHEMA_VERSION` with `SnapshotVersionError`, so an old baseline still
needs a fresh capture.

Four things verified against the lab:

- **`table-name` carries the RIB name including the family** (`L3VPN-CPE13-NNI.inet6.0`). The
  asymmetry the configuration has between IPv4 and IPv6 (`routing-options` vs. `rib inet6.0`)
  does not appear in the RPC — so the collector needs no name normalisation at all.
- **`via` carries the outgoing interface**, so mapping a route onto a service needs no
  arithmetic over the next hop. `engine.py` relies on that when filling
  `unassigned.static_routes`.
- **`to` and `via` sit inside `<nh>`, not directly under `<rt-entry>`** — hence `_texts()`
  uses `iter()`, not `find()`.
- **An aggregate `rt-entry` has no `<nh>` at all** — `<nh-type>` (`Discard`/`Reject`) sits
  directly under `<rt-entry>`, so `next_hop` and `via` always come out empty (`[]`) for it.
  Its `protocol-name` carries exactly `"Aggregate"`.

Three safeguards that look redundant and are not:

- **The `protocol-name` filter in `parse()`** is a second line of defence behind the RPC
  filter. A deployment calling the RPC without `protocol` would otherwise record BGP routes
  as static or aggregate ones.
- **Empty tables are dropped.** The RPC returns over twenty tables, most of them empty;
  storing them means inflating every snapshot with rows that say nothing.
- **The `.strip()` in `_texts()`** is parity with the sibling collectors
  (`interfaces.py:32`, `bgp.py:30`) — 175 values in the interfaces recording carry whitespace,
  so for `interfaces.py` the guard is backed by observation; for `bgp.py` it is the same idiom
  rather than a measured input. **No current route recording carries whitespace**, though — not on `<to>`, `<via>`,
  `<rt-destination>` or `<table-name>` — so nothing exercises that branch. It stays for
  consistency, not because of observed input. The test that claimed to measure it
  (`test_values_are_stripped`) was deleted: it measured nothing, and its docstring lied about
  the recording on top of that.

## `bfd.py` — BFD session state

RPC: `get_bfd_session_information` with `rpc_kwargs` `{"detail": True}` (both platforms).

**Without `detail` the collector would silently gather incomplete data.** The brief listing
has neither `bfd-client` nor `remote-state` — without the client there is no way to tell a
BGP-held session from any other, and without `remote-state` no way to see that the far end
administratively shut the session down. That the flag really reaches the RPC is guarded by
the conformance test; the recorded fixtures carry it in the
`<bfd-session-information style="detail">` attribute.

The output is `{peer_ip: {state, interface, remote_state, local_diagnostic, clients,
detection_time, transmission_interval, multiplier}}`, from the
`tests/fixtures/rpc/junos-evo/bfd.xml` recording:

```json
{
  "152.11.13.2": {
    "state": "Up", "interface": "et-0/0/8.13", "remote_state": "Up",
    "local_diagnostic": "None", "clients": ["BGP"],
    "detection_time": "9.000", "transmission_interval": "3.000", "multiplier": 3
  },
  "198.11.13.2": {
    "state": "Down", "interface": "et-0/0/8.113", "remote_state": "AdminDown",
    "local_diagnostic": "None", "clients": ["BGP"],
    "detection_time": "0.000", "transmission_interval": "3.000", "multiplier": 3
  }
}
```

**The key is the neighbour address**, because that is exactly what the check uses to look up
both the intent from the inventory (`Selectors.bfd_peers`) and the BGP state
(`facts["bgp"]`).

**An empty listing is a valid state, not an error.** The vMX recording
(`tests/fixtures/rpc/junos/bfd.xml`) is `<sessions>0</sessions>` — BFD is configured there,
but no session came up. Only the check can decide whether that is a problem: it depends on
whether BFD is configured at all and whether BGP is running.

## `isis.py`, `ldp.py`, `pim.py`, `mpls.py` — Core transit and lo0.0 protocols (2026-08-26 wave)

Six new collectors for the seven new checks in `checks/core_protocols.py` (`isis_overview`
shares its module with `isis_interface_info`, but not its fact area). CLI equivalents:

| fact area | RPC (`rpc_name` + `rpc_kwargs`) | CLI equivalent |
|---|---|---|
| `isis_adjacency` | `get_isis_adjacency_information(detail=True)` | `show isis adjacency detail` |
| `isis_interface` | `get_isis_interface_information(detail=True)` | `show isis interface detail` |
| `isis_overview` | `get_isis_overview_information` | `show isis overview` |
| `ldp_neighbor` | `get_ldp_neighbor_information(detail=True)` | `show ldp neighbor detail` |
| `pim_neighbor` | `get_pim_neighbors_information` | `show pim neighbors` |
| `mpls_interface` | `get_mpls_interface_information` | `show mpls interface` |

**Namespace-agnostic iteration.** A live PyEZ response can carry elements in the
`junos-routing` namespace (an `xmlns` prefix), while the normalized recordings from Task 1
carry no prefix at all. All six collectors therefore iterate with the wildcard `{*}element`
(`xml.iter("{*}isis-adjacency")`) instead of `xml.iter("isis-adjacency")`, which would find
nothing under a namespace. Element text is read by the shared `_localname_text()` (defined in
`isis.py`, imported by `ldp.py`, `pim.py`, `mpls.py`) — it looks for a descendant by
**localname**, not an exact path, so it works regardless of how deeply the response is
nested.

`_seconds_attr()` (`isis.py`, shared with `ldp.py`/`pim.py`) reads the `junos:seconds`
attribute — the OS version rides right inside the attribute's URI, so it matches both forms
(bare `seconds` on namespace-free recordings, `{...}seconds` on a live response carrying the
`junos:` prefix), not a literal key.

### `isis.py` — `IsisAdjacencyCollector`, `IsisInterfaceCollector`, `IsisOverviewCollector`

- `isis_adjacency`: `{interface: {system_name, state, ip_address, ipv6_address}}`. `state`
  defaults to `"unknown"` when `adjacency-state` is missing — the collector never invents
  `Up`/`Down` on its own.
- `isis_interface`: `{interface: {levels: {level: {passive: bool}}}}`. `passive` is `True`
  only when the `<passive>` element's text is exactly `"Passive"` — anything else (a missing
  element, other text) is `False`.
- `isis_overview`: **device-global**, not per-interface — a single dict `{overload_enabled:
  bool}`. The presence of the `<isis-overload-enabled/>` element (regardless of its text)
  means `True`; its absence means `False`. Only the Core loopback scope lets this fact
  through (see `models.md`).

### `ldp.py` — `LdpNeighborCollector`

`{interface: {neighbor_address, uptime_seconds}}`. **`lo0.*` records are dropped already at
parse time** — an LDP neighbor on the loopback is a *targeted session* between device
loopbacks across the mesh, not the state of a transit link, and this collector only measures
physical/aggregated interfaces. Interfaces with no name (`interface-name` missing) are
skipped, same as elsewhere.

### `pim.py` — `PimNeighborCollector`

`{interface: {neighbor_address, uptime_seconds}}`. The response nests as
`pim-neighbors-information > pim-interface > pim-neighbor` — a `pim-interface` **without** a
nested `pim-neighbor` (PIM enabled on the interface, but no neighbor) gets no synthetic
record, the key is simply missing (absence is absence, not an invented Down). The contract
keys on a single neighbor per interface; with more than one `pim-neighbor` under one
`pim-interface` (a multi-access segment) the collector takes the first and silently drops the
rest — the lab recordings never hit this case (1:1).

### `mpls.py` — `MplsInterfaceCollector`

`{interface: {state}}`. `state` defaults to `"unknown"` when `mpls-interface-state` is
missing.

## `multicast.py` — IGMP, multicast forwarding, MVPN c-multicast (2026-09-02 wave)

Three collectors for four new checks in `checks/multicast.py`: `igmp_group`,
`multicast_route`, `mvpn_instance`. CLI equivalents:

| fact area | RPC (`rpc_name` + `rpc_kwargs`) | CLI equivalent |
|---|---|---|
| `igmp_group` | `get_igmp_group_information` | `show igmp group` |
| `multicast_route` | `get_multicast_route_information(extensive=True[, instance=...])` | `show multicast route instance all extensive` |
| `mvpn_instance` | `get_mvpn_instance_information(inet=True)` | `show mvpn instance inet` |

None of the collectors interprets a verdict — `local` under IGMP is dropped only because it
is not an interface (it can never be a service interface), not because of PASS/FAIL. A
missing interface/instance in the reply means an absent key, not an empty list.

### `IgmpGroupCollector` (`igmp_group`)

`{interface: [{source, group}]}`. `multicast-source-address` `"0.0.0.0"` (ASM `(*, G)`)
maps to `source: None`, not to the literal text `"0.0.0.0"` — checks then test `is None`,
not a magic string. The `local` pseudo-interface (groups the router joined itself, not a
receiver) is dropped while parsing. An interface with no groups gets no key with an empty
list — absence is absence.

### `MulticastRouteCollector` (`multicast_route`)

`{instance: {"S,G": {upstream_interface, downstream_interfaces, forwarding_rate_pps,
uptime_seconds, state, forwarding_state}}}`. Only `address-family INET` (IPv6 multicast is
not collected). The `S,G` key is the string `f"{source},{group}"` (`route_key()`), not a
tuple — the snapshot must stay JSON-safe.

**`forwarding_rate_pps` is `int | None`, never an invented zero.** junos-evo often returns
`<multicast-statistics-timed-out/>` instead of `forwarding-rate-packets`, even on a live
Forwarding route (measured 2026-09-02) — the element is then absent from the reply and
`_int()` returns `None`. Checks render `SKIP : statistics unavailable` on `None`, not
`BROKEN` with 0 pps.

**MX does not know `instance="all"`.** `get-multicast-route-information(instance="all")` on
MX returns `<output>instance is not running</output>` (probed 2026-09-02) — MX has no
all-instances form at all. The collector therefore has two different paths per platform:

- **junos-evo**: one call, `rpc_kwargs()` → `{"extensive": True, "instance": "all"}`.
- **junos (MX)**: `rpc_kwargs()` returns just `{"extensive": True}` (master instance,
  no argument); `record_calls(device, platform)` additionally discovers the list of VRFs
  live (`get-instance-information(brief=True)`, filtered on `instance-type == "vrf"`) and
  adds one call with `instance=<name>` per RI. RI names are **never** taken from
  inventory — the collector has no inventory, and hardcoding them is forbidden.

`record_calls()` is a hook in `collect()`/`record` (default = `rpc_calls()`) that makes
`mig-validate record` save MX replies as `multicast_route.xml` (master) plus
`multicast_route.2.xml`, `.3.xml`… (one per RI), while junos-evo still writes a single
`multicast_route.xml`. `_fixture_paths` in the conformance tests globs `name.xml` +
`name.N.xml` instead of counting entries in `rpc_names`.

### `MvpnInstanceCollector` (`mvpn_instance`)

`{instance: {"c_multicast": [{source_prefix, group_prefix, provider_tunnel_id,
sender_pe}]}}`. `c-multicast-address` has the shape `"S/32:G/32"` — split on the first
colon into `source_prefix`/`group_prefix`. `sender_pe` is the PE address extracted from
`provider_tunnel_id` (`parse_sender_pe()`, regex `P2MP:(\d+\.\d+\.\d+\.\d+)`) — `None`
when the tunnel id is missing or contains `"invalid"` (`I-P-tnl:invalid`). An instance
present in the reply but with no c-multicast entries keeps its key with an empty list — this
is how the check distinguishes "instance not in the mvpn listing at all" from "instance is
there, but no c-multicast".
