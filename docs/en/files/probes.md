# `probes/` — active tests

Files: `ping.py` and an empty `__init__.py`.

Ping is the **only active test** in the tool — hence its own category outside the collectors:

| | collectors | probes |
|---|---|---|
| nature | passive state reading | active traffic generation |
| scope | device-scoped (one RPC for the whole device) | per target |
| inventory dependency | none | **yes** — without a scope, neither target nor source address is known |
| when it runs | in the first phase of `capture` | after bulk collection (targets come from ARP/ND) |

At `evaluate` time the ping result is ordinary data in the snapshot. That is what makes the
checks mutually independent and runnable in any order.

---

## `ping.py`

### `PingTarget`

A frozen dataclass: `scope_id`, `target`, `source`, `routing_instance`, `resolved_from`
(`arp` | `nd` | `subnet-fallback`), `family` (4 | 6), `interface` (only for IPv6 link-local
targets — see below). `to_dict()` produces the base of the snapshot record, into which the
measured values are then merged.

`scope_id` matters: it is how `Scope.select()` later attributes the probe back to its service.

### `source_address(scope, family)`

The address of the given family to ping from — the source family must match the target's,
otherwise Junos ping rejects it:

1. **`virtual_gw_v4`/`virtual_gw_v6` takes precedence** — on an IRB interface the correct
   source is the virtual-gateway address, not the box's own address,
2. otherwise the first `local_ipv4`/`local_ipv6` entry,
3. otherwise `None` (ping runs without `source`).

The prefix is stripped (`198.11.13.1/30` → `198.11.13.1`).

### `subnet_fallback(addresses, family, owned=None)`

When the ARP/ND table for the interface is empty, the **first usable address in the
subnet** that is not our own is tried. `owned` are other addresses the scope owns that must
never come back as a target — typically an IRB interface's virtual-gateway address; without
it the fallback would return the VGW as a target while `source_address()` already used the
VGW as the source, producing a ping at itself.

Network and broadcast addresses are skipped only for **IPv4** networks wider than /31 — on
point-to-point links (/31) both addresses are legitimate hosts, and in IPv6 the
all-zeros address is the subnet-router anycast, not a broadcast, so nothing is skipped
there at all. Networks whose prefix equals the maximum (/32, /128) are skipped entirely.

**IPv6 additionally applies `IPV6_FALLBACK_MIN_PREFIX = 126`** — the fallback is only tried
on `/126` networks and longer (point-to-point ranges). Guessing a random address inside a
`/64` makes no sense: it is a guaranteed failure that the report would read as an
unreachable CPE.

Typically: the PE is `.1`, so `.2` gets tried.

### `resolve_targets(scopes, arp_entries, nd_entries=None)`

The heart of the "ARP/ND → ping" phase. For each scope and each family (4, 6) it:

- **skips the device scope and every service type except `Internet` and `IPVPN`.** `Core`,
  `E-Line` and `E-LAN` get no ping — `lo0.0` is categorised as `Core`, so it drops out
  automatically;
- passes `routing_instance` to ping **only for `IPVPN`** (`ping <ip> routing-instance <RI>`);
  `Internet` runs in the default `inet.0`;
- **IPv4**: takes all ARP addresses learned on the scope's interfaces;
- **IPv6**: takes only ND entries that are **usable** (`_usable_nd()`: have a MAC and a state
  that is not `unreachable`/`incomplete`) and are not link-local — **unless the service itself
  is configured with a link-local address as its only address**
  (`_link_local_configured()`), in which case the link-local neighbour is kept as a
  legitimate target;
- **a link-local target without an interface is rejected by Junos ping** — so
  `PingTarget.interface` carries the ND entry's interface name whenever the target is
  link-local;
- for both families: not typical, but should ARP/ND ever return our own address, the target
  is dropped (a ping at yourself is a meaningless result);
- falls back to `subnet_fallback()` when ARP/ND yields nothing, marking the record
  `resolved_from: "subnet-fallback"`.

Within one scope, IPv4 targets come before IPv6; across scopes the order no longer holds —
nothing depends on it, checks read the family from the `family` field, not from position in
the list.

Management interfaces never reach this point, because they never become scopes at all
(`scoping/builder.py`). Without that rule the tool would ping into the management network.

### `run_ping(device, target, count)`

Runs `device.rpc.ping(host=..., count=..., rapid=True, [source=...],
[routing_instance=...], [interface=...])`. `rapid=True` cuts the run from ~5 s to ~0.3 s per
target (verified against the lab that the response shape — `probe-results-summary` and its
fields — stays the same, so `parse_ping_result()` needs no change). `interface` is passed
only for link-local targets.

**A failure is not a tool error but a measurement result.** The exception is therefore caught
and recorded as an entry with 100 % loss and an `error` key. Catching everything is
deliberate: besides the expected RPC errors, vMX occasionally returns malformed XML
(`<ping-results>` with no closing tag) on which PyEZ raises `XMLSyntaxError` — verified as
transient and fine on retry. Bringing down an entire capture over one advisory probe would be
worse.

### `parse_ping_result(xml)`

It distinguishes **two kinds of failure** that Junos returns:

| case | in the XML | what gets recorded |
|---|---|---|
| "no response" — ping left, nothing came back | summary exists, reports 100 % loss | a valid measurement result |
| "internal error" — ping never left (e.g. `bind: Can't assign requested address`) | summary **missing**, `ping-error-message` present | `loss_percent: 100` plus an `error` key with the reason |

Without that distinction the second case would look like a successful measurement of zero
packets. When the summary is missing, `loss_percent: 100` is recorded — truer than `None`,
which would read as "not measured" in the report.

`rtt_avg_ms` is computed from microseconds (`rtt-average`) and is filled **only when something
actually came back**. The failure reason is gathered from both `ping-failure` and
`ping-error-message` (`_failure_reason()`), deduplicated and joined into a single string.

---

## Configuration

The packet count is driven from the CLI: `capture --ping-count N` (default 5, the constant
`DEFAULT_COUNT`). The `ping_reachability.count` option in `config.yml` configures the
**check** — the measurement itself already happened during collection.
