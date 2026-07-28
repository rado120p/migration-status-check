# Reference

Lookup tables and schemas. The operating manual is [README.md](README.md); the wider context
is in [architecture.md](architecture.md).

---

## 1. Check catalogue

Matches the output of `mig-validate checks` (as of commit `1584a43`):

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
| `bgp_prefix_counts` | compare | advisory | Internet, IPVPN | received / accepted / advertised / active / suppressed against tolerance — **per RIB** |
| `evpn_vpws_status` | both | critical | E-Line | the instance's interface status is `Up` and a remote SID arrived |
| `evpn_esi_status` | both | critical | E-LAN | the local interface status in the ESI is `Up`, reports the DF |
| `evpn_mac_count` | both | advisory | E-LAN | learned MAC count > 0; with a baseline, also the drop against tolerance |

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
- **`bgp_session_state` with a baseline does not FAIL on a state change to Established.**
  `Idle -> Established` is `degraded`, i.e. WARN — an improvement rather than a breakage, but
  worth flagging that the state moved.
- **`evpn_vpws_status` does not require local and remote SIDs to match.** Each side advertises
  its own service ID; equality is not an invariant. It FAILs when no remote SID arrives at all.
- **`arp_present`/`nd_present` return `SKIP`, not WARN, when the service has no address in
  that family.** Without this, a pure-IPv6 service would get a WARN for a missing ARP entry
  that could never have existed — neighbours are also never folded into one sentence, each
  entry is its own `Finding` (`MAC -> IP`), so the report prints one row per neighbour.
- **`bgp_prefix_counts` never sums counts across RIBs.** A peer with several RIBs (`inet.0`,
  `bgp.l3vpn.0`, ...) gets its own set of rows per RIB — a drop confined to a single RIB
  would otherwise disappear into the sum with the others.

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

`schema_version: 2` (was `1` — the version bumped together with the address-by-family split,
see below). A snapshot is **self-contained** — `evaluate` needs neither an inventory nor the
network. A different schema version is a hard error (`SnapshotVersionError`), not an attempt
at data migration.

```jsonc
{
  "schema_version": 2,
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
    "evpn_mac":  {"EVPN-VLAN-AWARE-CPE13-NNI": {"313": 42}}
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
    "bridge_domains":      []
  }
}
```

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
          "label": "Interface traffic out",
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
    "bgp_peers": [{"peer": "10.1.0.5", "routing_instance": null, "snapshot": "subject"}]
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
  (`Interface admin status` / `Interface operational status`; `Interface traffic in` /
  `Interface traffic out`), not one summary result per interface. `bgp_prefix_counts` returns
  one per **RIB × counter** (`BGP <counter>-prefix-count`), not one summary across RIBs.
- A scope's `status` is the worst status of its checks (`SKIP` only when there is nothing
  better to report); `summary` aggregates across all checks. Neither the terminal nor a GUI
  computes anything.
- `match` is `null` for a run without a baseline; for an unpaired subject scope it carries
  `status: "unmatched"` and a `reason`.
- `identity` carries everything the report needs about the service (addresses, VGW, RI,
  description) — without it, that would stay inside the `Scope`, which the renderer cannot
  see.
- **Unpaired baseline scopes get no entry in `scopes`** — they are not on the subject, so
  there is nothing to measure. They appear only in `unmatched.baseline`.
- `unassigned.bgp_peers` currently reports only peers on the **subject** (the new device). In
  device mode the list is always empty.
- `message` is in Czech (without diacritics), consistently with the rest of the tool.

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
| `evpn_mac` | `get_bridge_mac_table` + `get_evpn_mac_table` | `get_mac_vrf_mac_table` | — |
| ping (probe) | `ping` | same | `host`, `count`, `rapid=True`, optionally `source`, `routing_instance`, `interface` (IPv6 link-local target only) |

`extensive` on `evpn_esi` is not cosmetic: without it, `show evpn instance` returns a summary
with no ESI at all and the collector would silently return nothing.
