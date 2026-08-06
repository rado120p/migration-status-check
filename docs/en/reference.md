# Reference

Lookup tables and schemas. The operating manual is [README.md](README.md); the wider context
is in [architecture.md](architecture.md).

---

## 1. Check catalogue

Matches the output of `mig-validate checks` (as of commit `59fc550`):

| id | mode | severity | service types | what it verifies |
|---|---|---|---|---|
| `interface_state` | state | critical | all | both `admin_status` and `oper_status` are `up` — one finding for each, separately |
| `interface_errors` | state | advisory | all | zero `input/output/framing` errors — **transit interfaces only** |
| `interface_traffic` | both | advisory | all | `input_pps`/`output_pps` > 0; with a baseline, also the drop against tolerance — **transit interfaces only**, one finding per direction |
| `traffic_ceased` | compare | advisory | all | traffic on the old interface went quiet after the migration — **disabled by default** |
| `arp_present` | state | advisory | Internet, IPVPN | at least one IPv4 ARP entry on the service's interfaces; `SKIP` if the service has no IPv4 address |
| `nd_present` | state | advisory | Internet, IPVPN | at least one usable IPv6 ND entry on the service's interfaces; `SKIP` if the service has no IPv6 address |
| `ping_reachability` | state | advisory | Internet, IPVPN | responses from the targets (IPv4 and IPv6) resolved during `capture` |
| `bgp_session_state` | both | critical | Internet, IPVPN | state is `Established`; with a baseline it also reports a state change |
| `bgp_prefix_counts` | compare | advisory | Internet, IPVPN | received / accepted / advertised / active against tolerance — **per RIB** |
| `evpn_vpws_status` | both | critical | E-Line | the instance's interface status is `Up` and a remote SID arrived |
| `evpn_esi_status` | both | critical | E-LAN | the local interface status in the ESI is `Up`, reports the DF |
| `evpn_instance_status` | both | critical | E-LAN | local interfaces > 0 and all up; IRB up (if any IRBs exist); EVPN neighbors > 0; ESI "resolved"; with a baseline, also equal counts |
| `evpn_mac_count` | both | advisory | E-LAN | MAC counts from the `count` output per VLAN and per interface; > 0 and, with a baseline, the drop against tolerance |
| `static_route_status` | both | critical | all | a configured static route is in the routing table and its next hop has not changed |
| `bfd_session_state` | both | critical | all | the BFD session of a configured peer is `Up`; `SKIP` until BGP is `Established` |
| `deactivation_state` | both | critical | all | the service's deactivation (`RI`/`interface`) has not worsened against the baseline; a healthy service (both sides active) gets no finding at all |

`mode` semantics:

- `state` — always runs,
- `compare` — without a baseline returns `SKIP` with the reason
  `porovnavaci check bez baseline snapshotu`,
- `both` — performs a state check without a baseline and additionally compares with one.

Per-check behaviour: [files/checks.md](files/checks.md).

### Where the catalogue surprises

- **`traffic_ceased` lists "vsechny" (all) service types, not just Core.** The spec presents
  it as an optional check; in the code it is not restricted by service type, only disabled by
  default.
- **`bgp_session_state` with a baseline returns neither FAIL nor WARN on a state change to
  Established.** `Idle -> Established` is PASS — an improvement rather than a breakage, and an
  orange row on a healthy service is a false alarm (decision R-2). The message and the `ZMENA`
  column still say that the state moved.
- **`evpn_vpws_status` does not require local and remote SIDs to match.** Each side advertises
  its own service ID; equality is not an invariant. It FAILs when no remote SID arrives at all.
- **`arp_present`/`nd_present` return nothing at all when the service has no address in
  that family.** Without this, a pure-IPv6 service would get a WARN for a missing ARP entry
  that could never have existed. It used to return a `SKIP` stamped with the family — but that
  stamp forced a section for a family the renderer is supposed to omit, so emitting no
  `Finding` is the only thing that satisfies both rules at once (decision R-1). The price: such
  a check is indistinguishable from one that passed. Neighbours are also never folded into one
  sentence, each entry is its own `Finding` (`MAC -> IP`), so the report prints one row per
  neighbour.
- **`bgp_prefix_counts` never sums counts across RIBs.** A peer with several RIBs (`inet.0`,
  `bgp.l3vpn.0`, ...) gets its own set of rows per RIB — a drop confined to a single RIB
  would otherwise disappear into the sum with the others.
- **`static_route_status` and `bfd_session_state` list "all" service types, but only a
  service that actually carries the intent gets a row.** A service without BFD carries no
  mention of BFD in the report (decision R‑1); restricting them by service type would be
  redundant, since an L2 interface can match neither a static route nor a peer anyway.
- **`static_route_status` is the only check comparing configured *intent* against measured
  reality.** The others ask "is it up?"; this one asks "is what you ordered actually there?".
  That is how it surfaces a route that is in the configuration but never made it into the
  table (`neni v tabulce`) — for example when its next hop became unreachable after an
  interface was deactivated.
- **`bfd_session_state` waits for BGP.** While the peer is not `Established` it returns
  `SKIP` with the value `BGP neni Established` instead of a FAIL. BFD cannot come up without
  BGP, and two red rows for one cause are why operators learn to skim past listings.
- **`interface_errors`/`interface_traffic` on the L3 part of a linked service (an IRB paired
  with an L2 scope via `scopes[].link`, itself with no transit interface) don't measure the
  IRB.** Counters physically live on the L2 transit. `interface_errors` returns a single
  `INFO` finding labelled "Interface errors / traffic" with value
  `mereno na L2 (<L2 interface>) - viz blok nize` instead of the usual SKIP/OK;
  `interface_traffic` returns no finding at all for that same service (so the INFO pointer
  isn't duplicated). Details and a report example: "L2+L3 linking" in chapter 5.

### Interface classification

Counter-based checks (`interface_errors`, `interface_traffic`, `traffic_ceased`) run **only
on transit interfaces**; elsewhere they return `SKIP`, not WARN.

```
transit (allowlist):  ge  xe  et  ae
```

```
internal (counter checks are skipped):
fxp  em    me    bme   cbp   pip   tap   jsrv  esi   vtep  pp0
lc-  demux lsi   mtun  pime  pimd  gre   ipip  dsc   pfe   pfh
vcp  sxe   vme   fti   lo0   re0   irb
```

The classification is a property of the **check, not the scope**: `irb.14` gets no counter
checks, yet ARP, ping and BGP checks run on it normally.

### Eligibility for a service scope

A different layer from the classification above. A scope is created only for **migrated
service types**:

```
Internet   IPVPN   E-Line   E-LAN   Core
```

- `Layer1` / `physical-port` entries **never become scopes of their own.** They serve only as
  confirmation that the physical parent exists and are added to the logical unit's
  `physical_interfaces`. (Without that rule, unpaired physical ports would make up the bulk
  of `unmatched` and the list would stop being readable — `Layer1` is the most common type in
  real data.)
- **Management interfaces are excluded regardless of `service_type`:** `fxp`, `em`, `me`,
  `vme`, `bme`, `re0:mgmt-*`, `re1:mgmt-*`. They do not become scopes, are not paired, never
  appear in `unmatched` and **are never pinged**. This is necessary because the parser still
  classifies them as `Internet` — without the rule, the tool would ping into the management
  network.

---

## 2. `config.yml`

Passed via `--config`. Optional; without it the defaults from
`migration_validator/config.py` apply.

```yaml
checks:
  interface_traffic:
    tolerance_percent: -60
    require_nonzero: true
  bgp_prefix_counts:
    tolerance_percent: -10
  evpn_mac_count:
    tolerance_percent: -60
  ping_reachability:
    count: 5
  traffic_ceased:
    enabled: false
    max_residual_pps: 1
```

Defaults exactly as they appear in the code:

| check | option | default |
|---|---|---|
| `interface_traffic` | `tolerance_percent` | `-60` |
| `interface_traffic` | `require_nonzero` | `true` |
| `bgp_prefix_counts` | `tolerance_percent` | `-10` |
| `evpn_mac_count` | `tolerance_percent` | `-60` |
| `ping_reachability` | `count` | `5` |
| `traffic_ceased` | `enabled` | `false` |
| `traffic_ceased` | `max_residual_pps` | `1` |

Universal options valid for **any** check:

| option | type | meaning |
|---|---|---|
| `enabled` | bool | `false` = the check never runs and does not appear in the report (default `true` for everything except `traffic_ceased`) |
| `severity` | `critical` \| `advisory` | overrides the default severity from the code |

Notes:

- `ping_reachability.count` configures the **check**, but the packet count is decided at
  collection time — on the CLI that is `capture --ping-count`.
- Overriding severity **does not change** the "partial success = WARN" rule. `degraded` is
  always WARN.

---

## 3. `mapping.yml`

Passed via `--mapping` to both `evaluate` and `match`.

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

A selector may combine `description`, `service_type` and `interface`; at least one must be
present, otherwise loading fails with `prazdny selektor v mapping.yml` ("empty selector").

Semantics:

- **`mappings`** — one line = one service. A rule must resolve to **exactly one** scope on
  each side; if it resolves to more, no pair is created and all candidates go to `unmatched`
  with the reason `ambiguous`. Manual mapping takes precedence over the automatic rules, and
  the resulting pair carries `confidence: manual`.
- **`ignore`** — the scope is removed from both sides before pairing. Meant for cases specific
  to a given migration; management interfaces need not be listed.
- The `{interface: ...}` selector targets a **logical unit** (`ge-0/0/2.113`). Writing
  `ge-0/0/2` therefore does not hit the five services running over that port — it hits nothing.

### Automatic pairing rules

Applied in this order; the first one producing an unambiguous pair wins:

| priority | rule (`method` in the output) | confidence |
|---|---|---|
| 0 | `mapping.yml` → `manual` | `manual` |
| 1 | `description+service_type+service_subtype` | high |
| 2 | `description+service_type` | high |
| 3 | `routing_instance+service_type` | medium |
| 4 | `subnet+service_type` (network address of the local subnet) | medium |
| 5 | `vlan+service_type` | low |

The key is always **composite**, never the description alone: one description may carry
several entries (`ge-0/0/5` physical and `ge-0/0/5.0` logical share it).

Reasons in `unmatched`:

| text | meaning |
|---|---|
| `zadny kandidat na subject` | the baseline service has no counterpart — suspect a forgotten migration |
| `nova sluzba, chybi baseline` | the subject service is extra — new or restructured |
| `ambiguous: N kandidatu (id, id, ...)` | the rule produced several candidates; the tool does not guess |

---

## 4. Snapshot format

`schema_version: 7`. A snapshot is **self-contained** — `evaluate` needs neither an inventory
nor the network. A different schema version is a hard error (`SnapshotVersionError`), not an
attempt at data migration.

Version history:

| version | what changed |
|---|---|
| 1 → 2 | addresses split by family, the `nd` area was added |
| 2 → 3 | the `routes` and `bfd` areas plus the `unassigned.static_routes` / `.bfd_sessions` keys were added |
| 3 → 4 | `Scope` carries deactivation flags (`routing_instance_active`, `interface_active`) — AR-21 |
| 4 → 5 | snapshot and inventory schema aligned at 5 — commit `d9e77bc` |
| 5 → 6 | ARP/ND over IRB carry `learned_via`, the entry no longer escapes the scope filter — commit `6df6e1a` |
| 6 → 7 | the `evpn_mac` collector reads the `count` RPC (per-VLAN and per-interface counts, shape `{vlans, interfaces}`); the `evpn_instance` area was added — commit `e547a24` |

> **Older snapshots cannot be replayed.** The bump to 3 means `runs/ipv6/` and
> `runs/ipv6-live-2026-07-29/` — taken with `schema_version: 2` — are now rejected by
> `evaluate`. That is not a defect: a version 2 snapshot contains neither `routes` nor `bfd`,
> so both new checks would have nothing to read and a service with a configured but
> uninstalled route would pass as healthy. Anyone needing such a snapshot evaluated must
> **take a fresh `capture`**; the missing areas cannot be derived from the old file.
>
> The same applies to the 6 → 7 bump: `runs/mig01/pre.json` and
> `runs/mig01/post.json` carry `schema_version: 6` and a version-7 tool
> rejects them. They need a fresh capture (`pre` = services on the MX,
> `post` = after migration to the EVO).

```jsonc
{
  "schema_version": 7,
  "device": {
    "address": "172.20.20.4", "hostname": "MX1-POP1",
    "platform": "junos",              // junos | junos-evo
    "model": "mx204", "version": "21.4R3-S4", "uptime_seconds": null
  },
  "capture": {
    "started_at": "2026-07-24T09:12:03Z", "finished_at": "2026-07-24T09:12:41Z",
    "phase": "pre-migration",
    "collectors": {
      "interfaces": {"status": "ok"},
      "bgp":        {"status": "ok"},
      "nd":         {"status": "ok"},
      "evpn_esi":   {"status": "error", "message": "RpcError: syntax error"}
    }
  },
  "inventory": [ /* ServiceEntry from the parser, or null */ ],
  "scopes":    [ /* see below */ ],
  "facts": {
    "interfaces": {
      "ge-0/0/2.113": {"admin_status": "up", "oper_status": "up",
                       "input_pps": 412, "output_pps": 388,
                       "input_errors": 0, "output_errors": 0, "framing_errors": 0}
    },
    "arp": [{"ip": "198.11.13.2", "mac": "00:11:...", "interface": "ge-0/0/2.113",
             "routing_instance": null}],
    "nd": [{"ip": "2001:db8:11:13::b", "mac": "00:11:...", "interface": "ge-0/0/2.113",
            "state": "reachable"}],
    "bgp": {
      "198.11.13.2": {
        "state": "Established", "peer_as": 65013,
        "routing_instance": "L3VPN-CPE13-NNI",
        "ribs": {
          "inet.0": {"received": 14, "accepted": 14, "advertised": 3,
                     "active": 3, "suppressed": 0}
        }
      }
    },
    "evpn_vpws": {"EVPN-VPWS-CPE13-NNI": {"local_sid": 213, "remote_sid": 213, "status": "Up"}},
    "evpn_esi":  {"00:11:22:...": {"status": "Up/Forwarding", "df_role": "10.0.0.5",
                                   "interface": "ae0.14"}},
    // Since schema 7, evpn_mac and evpn_instance both carry per-instance data
    // from the 'count' resp. extensive output. The VLAN key is the real
    // learn-vlan (previously "-").
    "evpn_instance": {
      "EVPN-VLAN-AWARE-CPE13-NNI": {
        "local_interfaces": {"total": 2, "up": 2, "entries": [
          {"name": "ge-0/0/2.313", "status": "Up"}]},
        "irb_interfaces": {"total": 0, "up": 0, "entries": []},
        "neighbors": {"total": 1, "addresses": ["150.0.0.13"]},
        "esis": {}
      }
    },
    "evpn_mac": {
      "EVPN-VLAN-AWARE-CPE13-NNI": {
        "vlans": {"313": {"count": 2, "domain": "BD-313"}},
        "interfaces": {"ge-0/0/2.313": {"count": 1, "name": "ge-0/0/2.313:313",
                                         "domain": "BD-313"}}
      }
    },
    // Verbatim from runs/bfd-static-2026-07-29/pre.json: on this device five
    // service statics are configured but absent from the table (their next hops
    // died when ge-0/0/2 was deactivated), and not one BFD session came up.
    "routes": {
      "mgmt_junos.inet.0":  {"0.0.0.0/0": {"next_hop": ["10.0.0.2"],
                                           "via": ["fxp0.0"], "active": true}},
      "mgmt_junos.inet6.0": {"::/0": {"next_hop": ["2001:db8::1"],
                                      "via": ["fxp0.0"], "active": true}}
    },
    "bfd": {}
  },
  "probes": {
    "ping": [
      {"scope_id": "svc:L3VPN-CPE13-NNI:IPVPN", "target": "198.11.13.2",
       "source": "198.11.13.1", "routing_instance": "L3VPN-CPE13-NNI",
       "resolved_from": "arp", "family": 4, "interface": null,
       "sent": 5, "received": 5, "loss_percent": 0, "rtt_avg_ms": 1.24},
      {"scope_id": "svc:L3VPN-CPE13-NNI:IPVPN", "target": "2001:db8:11:13::b",
       "source": "2001:db8:11:13::a", "routing_instance": "L3VPN-CPE13-NNI",
       "resolved_from": "nd", "family": 6, "interface": null,
       "sent": 5, "received": 5, "loss_percent": 0, "rtt_avg_ms": 1.31}
    ]
  }
}
```

Properties:

- `facts` are **raw, device-scoped data** keyed by their natural key. No `service_id` inside.
  If the pairing turns out to be wrong, it gets fixed and old snapshots are re-evaluated
  without touching the devices.
- `facts.nd` is the IPv6 counterpart of `facts.arp` — same shape, plus a `state` field.
- `facts.bgp[peer].ribs` keeps counts **per RIB, never summed** — one peer may have up to 11
  RIBs (`bgp.rtarget.0`, `inet.0`, `bgp.l3vpn.0`, ...).
- `facts.routes` is keyed **by RIB name, then by prefix**. The name carries the family
  (`...inet6.0`), so the asymmetry the configuration has between IPv4 and IPv6 does not appear
  in the snapshot. Empty tables are not stored — the RPC returns over twenty of them.
- `facts.bfd` is keyed **by neighbour address** — the same key under which the check looks up
  the inventory intent and the BGP state. An empty dictionary is a valid state (BFD
  configured, no session came up), not a collection failure.
- `capture.collectors` records the status of each collection separately — a single failed RPC
  does not abort the capture.
- `device.uptime_seconds` exists in the model, but `device_meta()` currently always sets it to
  `None`.
- A ping record carries `family` (4/6) and `interface` — the latter is only set for an IPv6
  link-local target, because Junos ping rejects one without an outgoing interface.
- A ping record may gain an `error` key with the reason when ICMP never left the box (e.g.
  `bind: Can't assign requested address`) — without it, that would look like a successful
  measurement of zero packets.

### Scope

```jsonc
{
  "id": "svc:L3VPN-CPE13-NNI:IPVPN",
  "kind": "service",                      // service | device
  "key": {"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN", "service_subtype": null},
  "selectors": {
    "interfaces":          ["ge-0/0/2.113"],
    "physical_interfaces": ["ge-0/0/2"],
    "routing_instances":   ["L3VPN-CPE13-NNI"],
    "bgp_neighbors":       ["198.11.13.2", "2001:db8:11:13::b"],
    "local_ipv4":          ["198.11.13.1/30"],
    "local_ipv6":          ["2001:db8:11:13::a/127"],
    "virtual_gw_v4":       [],
    "virtual_gw_v6":       [],
    "vlans":               ["113"],
    "bridge_domains":      [],
    "static_routes": [
      {"rib": "L3VPN-CPE13-NNI.inet.0",  "prefix": "172.26.1.0/29",
       "next_hop": ["198.11.13.2"]},
      {"rib": "L3VPN-CPE13-NNI.inet6.0", "prefix": "2001:eeee::/64",
       "next_hop": ["2001:db8:11:13::b"]}
    ],
    "bfd_peers": [
      {"peer": "198.11.13.2", "minimum_interval": 3000, "multiplier": 3,
       "source": "neighbor"}
    ]
  }
}
```

`static_routes` and `bfd_peers` are **configured intent**, not measurement — they are the only
selectors carrying values rather than just names. `static_routes` doubles as a filter (a route
belongs to the scope when the `(rib, prefix)` pair matches); `bfd_peers` does **not** —
sessions are selected via `bgp_neighbors`, so that a session for a peer missing from the intent
does not disappear without a trace.

A scope is **purely a filter** and holds no measured data. The device scope has
`kind: "device"` and empty selectors = "take everything". Addresses and virtual-gateway are
split by family (`local_ipv4`/`local_ipv6`, `virtual_gw_v4`/`virtual_gw_v6`) — same as in the
inventory YAML (see [files/parsers.md](files/parsers.md#output-format)).

`id` is `svc:<description or interface name>:<service_type>`. If two services would produce
the same key, the interface name is appended (`svc:et-0/0/10.0:IPVPN`).

---

## 5. Result format

`schema_version: 1` — **unchanged** by the IPv4/IPv6 split (unlike the inventory and the
snapshot above); `models/result.py::RunResult.schema_version` stays `1`.

```jsonc
{
  "schema_version": 1,
  "evaluated_at": "2026-07-24T11:40:02Z",
  "subject":  {"address": "172.20.20.5", "phase": "post-migration", "captured_at": "..."},
  "baseline": {"address": "172.20.20.4", "phase": "pre-migration",  "captured_at": "..."},

  "summary": {
    "pass": 68, "warn": 15, "fail": 1, "skip": 7,
    "scopes_matched": 8, "unmatched_baseline": 2, "unmatched_subject": 3
  },

  // only on a result that went through a filter (--filter / --status); absent otherwise
  "filtered": {"scopes_shown": 2, "scopes_total": 11, "statuses": ["FAIL"]},

  "scopes": [
    {
      "scope_id": "svc:L3VPN-CPE13-NNI:IPVPN",
      "key": {"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN", "service_subtype": null},
      "status": "WARN",
      "match": {
        "status": "matched", "method": "description+service_type", "confidence": "high",
        "baseline_interfaces": ["ge-0/0/2.113"], "subject_interfaces": ["et-0/0/8.113"]
      },
      "identity": {
        "description": "L3VPN-CPE13-NNI", "service_type": "IPVPN", "service_subtype": null,
        "routing_instance": "L3VPN-CPE13-NNI",
        "ipv4": ["198.11.13.1/30"], "ipv6": ["2001:db8:11:13::a/127"],
        "virtual_gw_v4": [], "virtual_gw_v6": []
      },
      "checks": [
        {
          "id": "interface_traffic", "mode": "both",
          "status": "WARN", "severity": "advisory",
          "message": "et-0/0/8.113: output_pps kleslo o 72 % (410 -> 115), prah je -60 %",
          "label": "Interface traffic out (et-0/0/8.113)",
          "value": "115 pps", "baseline_value": "410 pps", "delta": "-72 %",
          "baseline": {"output_pps": 410},
          "subject":  {"output_pps": 115},
          "details":  {"tolerance_percent": -60.0}
        },
        {
          "id": "bgp_prefix_counts", "mode": "compare",
          "status": "WARN", "severity": "advisory", "family": 4,
          "message": "198.11.13.2/inet.0: pokles advertised 14 -> 3, prah je -10 %",
          "label": "BGP advertised-prefix-count",
          "value": "3", "baseline_value": "14", "delta": "-11",
          "baseline": {"advertised": 14}, "subject": {"advertised": 3},
          "details": {"rib": "inet.0", "tolerance_percent": -10.0, "change_percent": -78.6}
        }
      ]
    }
  ],

  "unmatched": {
    "baseline": [{"scope_id": "...", "description": "...", "service_type": "Core",
                  "reason": "zadny kandidat na subject"}],
    "subject":  [{"scope_id": "...", "description": "...", "service_type": "E-LAN",
                  "reason": "nova sluzba, chybi baseline"}]
  },

  "unassigned": {
    "bgp_peers": [{"peer": "10.1.0.5", "routing_instance": null, "snapshot": "subject"}],
    "static_routes": [
      {"rib": "mgmt_junos.inet.0", "prefix": "0.0.0.0/0", "next_hop": ["10.0.0.2"],
       "via": ["fxp0.0"], "snapshot": "subject"},
      {"rib": "mgmt_junos.inet6.0", "prefix": "::/0", "next_hop": ["2001:db8::1"],
       "via": ["fxp0.0"], "snapshot": "subject"}
    ],
    "bfd_sessions": [
      {"peer": "10.1.0.5", "interface": "ge-0/0/9.0", "state": "Up",
       "snapshot": "subject"}
    ]
  }
}
```

Properties:

- **Every check carries both a `baseline` and a `subject` block with raw numbers**, not just a
  verdict, and (since the report rewrite) also `label`, `value`, `baseline_value`, `delta` and
  an optional `family` — splitting a measured value into a label and a value for the report's
  columns has to be done by the check, because only it knows what counts as the value and what
  counts as explanation (`CheckResult.to_dict()` omits empty optional keys).
- `interface_state` and `interface_traffic` now return **one check result per fact/direction**
  (`Interface admin status (<name>)` / `Interface operational status (<name>)`;
  `Interface traffic in (<name>)` / `Interface traffic out (<name>)`), not one summary result
  per interface. `bgp_prefix_counts` returns
  one per **RIB × counter** (`label="<counter>-prefix-count"`, `group=f"BGP {peer} / {rib}"`),
  not one summary across RIBs.
- A scope's `status` is the worst status of its checks (`SKIP` only when there is nothing
  better to report); `summary` aggregates across **checks**. The terminal derives the service
  counts from `scopes` itself — the same computation holds for the whole run and for a
  filtered selection, so they need not live in `summary`.
- **After a filter (`--filter`, `--status`), `pass`/`warn`/`fail`/`skip` in `summary` describe
  the selection shown, not the whole run**, and `filtered` says so. `scopes_matched` and both
  `unmatched_*` are not recomputed — filtering does not apply to `NESPAROVANO`.
- `match` is `null` for a run without a baseline; for an unpaired subject scope it carries
  `status: "unmatched"` and a `reason`.
- `identity` carries everything the report needs about the service (addresses, VGW, RI,
  description) — without it, that would stay inside the `Scope`, which the renderer cannot
  see.
- **Unpaired baseline scopes get no entry in `scopes`** — they are not on the subject, so
  there is nothing to measure. They appear only in `unmatched.baseline`.
- `unassigned` currently reports data from the **subject** only (the new device), and all
  three lists (`bgp_peers`, `static_routes`, `bfd_sessions`) are always empty in device mode —
  the device scope claims everything, so "unassigned" has no meaning there.
- **`unassigned.static_routes` doubles as a safety net against parsing gaps.** Statics in the
  management instance land here (`mgmt_junos.inet.0 0.0.0.0/0` via `fxp0.0` — `fxp0.0` can
  never become a scope), but so does a route whose configuration shape the parser could not
  read: it never reaches the selectors, yet it is plainly visible in the table.
- **`unassigned.bfd_sessions`** holds sessions of peers absent from every `bgp_neighbors` —
  typically BFD held by a client other than BGP, whose intent the parser does not read at all.
- `unassigned` **is rendered in the text report** in the `NEZARAZENO` section (AR-39) — that is
  a different thing than `NESPAROVANO`, which prints `unmatched`. The `NEZARAZENO` section is
  always printed, even when empty (`(nic)`), and filtering does not apply to it.
- `message` is in Czech (without diacritics), consistently with the rest of the tool.
- **`scopes[].link` is an optional key** — it carries the L3 (IRB) <-> L2 (E-LAN transit) link
  within the same EVPN instance, see "L2+L3 linking" below. Without a link, the key is absent
  from the scope entirely (an additive key, same rule as the other optional fields in this
  format).

### L2+L3 linking

A service of type Internet/IPVPN whose L3 interface is an IRB in an EVPN mac-vrf instance has
two parts in the configuration: an L3 part (the IRB, the service's RI) and an L2 part (E-LAN,
the transit interface of the same mac-vrf instance). The engine recognizes this pair and links
them.

Source of the link: the IRB interface's `l3_context` from `show evpn instance extensive` /
`show mac-vrf routing instance extensive` (fact `evpn_instance`, phase 2.1), plus a matching
VLAN/unit within the same instance; `master` means inet.0 (an Internet scope with no RI).
Pairing is 1:1 — multiple candidates produce no link at all (no link beats a wrong one).

**In the text report** (`render`), the L2 block (`E-LAN (L2 cast)`) is printed immediately
after the L3 block of a linked service, and both carry a mutual pointer in their headers:

```
L3 cast: irb.15 v L3VPN-CPE14-UNI (blok vyse)
```
```
L2 cast: ae0.15 v EVPN-VLAN-AWARE-POP1 (blok nize)
```

If `--filter`/`--status` selects one side of the pair, the filter keeps the other partner too,
even though it doesn't match the criteria itself — otherwise the "blok nize/vyse" pointer would
point at nothing. The partner is computed from the combined result of both criteria (`--filter`
and `--status` together), not from each separately, so a pair only stays together when at least
one side actually matches — a pair where neither the L3 nor the L2 side matches is dropped
entirely. The partner's checks also count into the recomputed summary in `filtered` — it IS
shown, same as any other selected scope. Without `--detail`, the usual PASS-collapse rule still
applies: a PASS partner shows up in the summary table, but its block only expands when it is
shown (FAIL/WARN, or `--detail`).

Errors/traffic are not measured directly on the L3 part (the IRB itself is not a transit
interface) — the L3 block instead carries a single INFO row
`mereno na L2 (ae0.15) - viz blok nize`; the actual counter lives in the L2 block. See the
check catalogue above for details.

**The structured result** (`evaluate --format json`) carries the same information as the
optional key `scopes[].link`:

```jsonc
"link": {
  "role": "l3",
  "peer_scope_id": "svc:EVPN-VLAN-AWARE-POP1:E-LAN",
  "peer_interface": "ae0.15",
  "peer_instance": "EVPN-VLAN-AWARE-POP1"
}
```

`role` is `"l3"` on the IRB side of the scope and `"l2"` on the E-LAN side; `peer_scope_id`,
`peer_interface` and `peer_instance` describe the other side of the link. Without a link, the
`link` key is absent from the scope entirely.

---

## 6. Exit codes

| code | constant in `cli.py` | meaning |
|---|---|---|
| `0` | `EXIT_OK` | no FAIL |
| `1` | `EXIT_FAILED_CHECKS` | at least one FAIL, or at least one WARN with `--warn-as-error` |
| `2` | `EXIT_TOOL_ERROR` | tool error: could not connect, snapshot missing / broken / wrong `schema_version`, unknown collector, invalid YAML |

WARN on its own does not change the exit code — otherwise CI would fail constantly and stop
being believed.

*The tool failed* and *a test failed* are two different things and must not blur — hence 2 vs 1.

---

## 7. RPC overview

| collector | Junos | Junos EVO | arguments |
|---|---|---|---|
| `interfaces` | `get_interface_information` | same | `extensive=True` |
| `arp` | `get_arp_table_information` | same | `no_resolve=True` |
| `nd` | `get_ipv6_nd_information` | same | — |
| `bgp` | `get_bgp_neighbor_information` | same | — |
| `evpn_vpws` | `get_evpn_vpws_information` | same | — |
| `evpn_esi` | `get_evpn_instance_information` | same | `extensive=True` |
| `evpn_instance` | `get_evpn_instance_information` | `get_mac_vrf_instance_information` | `extensive=True` |
| `evpn_mac` | `get_bridge_mac_table` + `get_evpn_mac_table` | `get_mac_vrf_mac_table` | `count=True` |
| `routes` | `get_route_information` | same | `protocol="static"` |
| `bfd` | `get_bfd_session_information` | same | `detail=True` |
| ping (probe) | `ping` | same | `host`, `count`, `rapid=True`, optionally `source`, `routing_instance`, `interface` (IPv6 link-local target only) |

`extensive` on `evpn_esi` is not cosmetic: without it, `show evpn instance` returns a summary
with no ESI at all and the collector would silently return nothing.

Two more arguments that look cosmetic and are not:

- **`protocol="static"`** keeps the response small even on a device carrying a full internet
  table.
- **`detail=True`** is a necessity, not a convenience: the brief BFD listing has neither
  `bfd-client` nor `remote-state`, so the collector would silently gather data from which
  there is no way to tell that BGP holds the session and that the far end shut it down
  administratively.
