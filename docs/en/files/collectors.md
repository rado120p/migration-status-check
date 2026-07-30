# `collectors/` — collecting operational state

Files: `base.py`, `registry.py`, `all.py`, `interfaces.py`, `arp.py`, `nd.py`, `bgp.py`,
`evpn.py`, `routes.py`, `bfd.py` and an empty `__init__.py`.

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
recorded fixtures would be silently incomplete. So far the only such collector is
`EvpnMacCollector` on MX.

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

## `routes.py` — static routes from the routing table

RPC: `get_route_information` with `rpc_kwargs` `{"protocol": "static"}` (both platforms).

The protocol filter keeps the response small even on a device carrying a full internet table.
`all=True` is **not** used — it only adds `__juniper_private*` tables, which is noise.

The output is a two-level dictionary `{RIB: {prefix: {next_hop, via, active}}}`, exactly as
the collector produces it from the `tests/fixtures/rpc/junos-evo/routes.xml` recording:

```json
{
  "inet.0": {
    "198.62.1.0/29": { "next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": true },
    "198.62.2.0/24": { "next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": true }
  },
  "inet6.0": {
    "2001:aaaa::/64": { "next_hop": ["2001:abcd:11:13::b"], "via": ["et-0/0/8.13"], "active": true }
  },
  "L3VPN-CPE13-NNI.inet.0": {
    "172.26.1.0/29": { "next_hop": ["198.11.13.2"], "via": ["et-0/0/8.113"], "active": true }
  },
  "L3VPN-CPE13-NNI.inet6.0": {
    "2001:eeee::/64": { "next_hop": ["2001:db8:11:13::b"], "via": ["et-0/0/8.113"], "active": true }
  }
}
```

Three things verified against the lab:

- **`table-name` carries the RIB name including the family** (`L3VPN-CPE13-NNI.inet6.0`). The
  asymmetry the configuration has between IPv4 and IPv6 (`routing-options` vs. `rib inet6.0`)
  does not appear in the RPC — so the collector needs no name normalisation at all.
- **`via` carries the outgoing interface**, so mapping a route onto a service needs no
  arithmetic over the next hop. `engine.py` relies on that when filling
  `unassigned.static_routes`.
- **`to` and `via` sit inside `<nh>`, not directly under `<rt-entry>`** — hence `_texts()`
  uses `iter()`, not `find()`.

Three safeguards that look redundant and are not:

- **The `protocol-name` filter in `parse()`** is a second line of defence behind the RPC
  filter. A deployment calling the RPC without `protocol` would otherwise record BGP routes
  as static ones.
- **Empty tables are dropped.** The RPC returns over twenty tables, most of them empty;
  storing them means inflating every snapshot with rows that say nothing.
- **The `.strip()` in `_texts()`** is parity with the sibling collectors
  (`interfaces.py:32`, `bgp.py:30`), where MX really does return texts wrapped in newlines.
  **No current route recording carries whitespace**, though — not on `<to>`, `<via>`,
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
