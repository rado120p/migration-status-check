# `probes/` — active tests

Files: `ping.py` and an empty `__init__.py`.

Ping is the **only active test** in the tool — hence its own category outside the collectors:

| | collectors | probes |
|---|---|---|
| nature | passive state reading | active traffic generation |
| scope | device-scoped (one RPC for the whole device) | per target |
| inventory dependency | none | **yes** — without a scope, neither target nor source address is known |
| when it runs | in the first phase of `capture` | after bulk collection (targets come from ARP) |

At `evaluate` time the ping result is ordinary data in the snapshot. That is what makes the
checks mutually independent and runnable in any order.

---

## `ping.py`

### `PingTarget`

A frozen dataclass: `scope_id`, `target`, `source`, `routing_instance`, `resolved_from`
(`arp` | `subnet-fallback`). `to_dict()` produces the base of the snapshot record, into which
the measured values are then merged.

`scope_id` matters: it is how `Scope.select()` later attributes the probe back to its service.

### `source_address(scope)`

The address to ping from:

1. **`virtual_gw` takes precedence** — on an IRB interface the correct source is the
   virtual-gateway address, not the box's own address,
2. otherwise the first `local_addresses` entry,
3. otherwise `None` (ping runs without `source`).

The prefix is stripped (`198.11.13.1/30` → `198.11.13.1`).

### `subnet_fallback(scope)`

When the ARP table for the interface is empty, the **first usable address in the subnet** that
is not our own is tried. Network and broadcast addresses are skipped — but only for networks
wider than /31, because on point-to-point links (/31, /127) both addresses are legitimate
hosts. Networks whose prefix equals the maximum (/32, /128) are skipped entirely.

Typically: the PE is `.1`, so `.2` gets tried.

### `resolve_targets(scopes, arp_entries)`

The heart of the "ARP → ping" phase. For each scope it:

- **skips the device scope and every service type except `Internet` and `IPVPN`.** `Core`,
  `E-Line` and `E-LAN` get no ping — `lo0.0` is categorised as `Core`, so it drops out
  automatically;
- passes `routing_instance` to ping **only for `IPVPN`** (`ping <ip> routing-instance <RI>`);
  `Internet` runs in the default `inet.0`;
- takes **all** ARP addresses learned on the scope's interfaces — on non-p2p subnets there may
  be several, and all of them are pinged;
- falls back to `subnet_fallback()` when ARP yields nothing, marking the record
  `resolved_from: "subnet-fallback"`.

Management interfaces never reach this point, because they never become scopes at all
(`scoping/builder.py`). Without that rule the tool would ping into the management network.

### `run_ping(device, target, count)`

Runs `device.rpc.ping(host=..., count=..., [source=...], [routing_instance=...])`.

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
