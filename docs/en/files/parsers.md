# `mx_parser.py` and `evo_parser.py` — configuration parsers

Two thin scripts in the repository root, run directly:

```bash
.venv/bin/python mx_parser.py  172.20.20.4 -o 172.20.20.4.yml
.venv/bin/python evo_parser.py 172.20.20.5 -o 172.20.20.5.yml
```

| script | device |
|---|---|
| `mx_parser.py` | MX — classic Junos |
| `evo_parser.py` | ACX / PTX — Junos OS Evolved |

They produce the **inventory**: a YAML file listing interfaces and the services running on
them. The validator consumes it via `--inventory` and stores it inside the snapshot.

---

## Relationship to the validator (AR‑8, unified as of phase 4)

**The implementation moved into the package** — it now lives in
`migration_validator/parsers/`:

| file | contents |
|---|---|
| `parsers/core.py` | shared core: data models, XML helpers, CLI, connection, YAML output, `main()` |
| `parsers/mx.py` | `JunosServiceParser` — MX specifics |
| `parsers/evo.py` | `JunosEvoAcxServiceParser` — ACX/PTX specifics |
| `parsers/__init__.py` | `parser_for_platform(platform)` — returns the parser **class** for `junos`/`junos-evo` |

`mx_parser.py` and `evo_parser.py` in the repository root are now **thin wrappers** — they
just import `main()` and the matching class and call `main(parser_cls=...)`. **The CLI, its
flags, the output format and the exit codes are unchanged** — anyone who invoked the scripts
before can keep doing so without modification.

The new consumer of the unified parsers is `mig-validate capture --run ... --parse-services`
— instead of running `mx_parser.py`/`evo_parser.py` by hand and passing `--inventory`,
`capture` in `--run` mode can build the inventory itself. It calls `parser_for_platform()`
based on the platform detected on connect and writes straight to the path
`RunStore.inventory_path()` expects (`runs/<name>/inventory_<node>_<port>.yml`).

In practice this means: **the validator has no runtime dependency on the parsers** outside of
`--parse-services` — `--inventory` just reads a finished YAML file, and the inventory can just
as well be written by hand.

---

## The flow

```
main()
 ├─ parse_arguments()            argparse
 ├─ resolve_connection_options() key vs password, optionally prompts for a passphrase
 ├─ build_device()               PyEZ Device (gather_facts=False, auto_probe=timeout)
 ├─ retrieve_configuration()     a single get_config RPC, committed + inherit
 ├─ JunosServiceParser(...).parse()
 ├─ create_yaml_data()
 └─ write_yaml()
```

`retrieve_configuration()` fetches `interfaces`, `routing-options`, `routing-instances`,
`protocols`, `bridge-domains`, `vlans` and `switch-options` **in a single filter**, so the
parser sees the relationships between them at once. `inherit` means `apply-groups` are
applied.

**`routing-options` is in the filter because of the global instance's static routes.**
Without it the parser would only see statics inside `routing-instances`, and routes in the
default instance would silently vanish from the inventory — making the service look like it
had no intent at all.

---

## Service classification

`JunosServiceParser.parse()` (in the EVO variant `JunosEvoAcxServiceParser`) proceeds in this
order:

1. `_parse_routing_instances()` — instances, their type, protocols, RD/RT, bridge domains,
   VLANs, BGP neighbours **and the instance's BFD intent**;
2. `_parse_static_routes()` — statics from the global `routing-options` and from every
   instance;
3. `_parse_default_bgp_neighbors()` — top-level `protocols bgp` belongs to the default
   instance (also fills `self.default_bfd`);
4. `_parse_global_l2circuits()`, `_parse_global_connections()` — E-Line outside instances;
5. `_parse_interfaces()` → `_build_interface_config()` — interfaces, families, VLANs,
   addresses, virtual-gateway;
6. `_classify_interface()` → `_detect_service()` — the actual service-type decision;
7. `_assign_bgp_neighbors()` — attributes peers to services by subnet match with the interface;
8. `_assign_static_routes()` — attributes statics by next hop **and** RIB match;
9. `_assign_bfd()` — attributes the BFD intent to services.

**The order of the last three steps is not arbitrary.** `_assign_bfd()` reads an
already-populated `service.bgp_neighbor`; run before `_assign_bgp_neighbors()` it would
silently attribute nothing — the peer list would be empty, and "a service without BFD" is a
legitimate state, so no test need catch it.

`_detect_service()` tries, in order: **IPVPN → E-Line VPWS → E-Line CCC → E-LAN VPLS → E-LAN
EVPN → Core → Internet → Layer 1 → unrecognised L2.** The order matters: the earlier rule wins.

Every entry carries a `detection_confidence` (`high` / `medium` / `low`) and a
`detection_reason` (a list of sentences explaining why the type was chosen). The validator
**does not use** these fields, but they are invaluable in the YAML when debugging unpaired
services.

`_should_ignore_interface()` drops from the output only genuinely empty interfaces (no
description, family, VLAN, address, encapsulation and no instance) — not interfaces merely
because their type came out `Unknown`.

---

## Static routes: normalising the RIB name

The configuration is **not symmetric between families**:

| family | where it sits in the configuration |
|---|---|
| IPv4 | directly under `routing-options/static` |
| IPv6 | under `routing-options/rib <name>.inet6.0/static` |

`show route`, however, knows no such difference in its `table-name` field — it returns the
full RIB name for both families. `_parse_static_routes()` therefore **flattens the asymmetry
right at the input**: a `static` without a `rib` gets a derived name (`inet.0` in the global
instance, `<instance>.inet.0` in a named one), while a `rib` carries its own. The difference
spreads no further, and the check compares the configured name directly against the name from
the RPC, with no lookup table in between.

`_assign_static_routes()` attributes a route to a service only when **both** conditions hold
at once: the next hop lies in the interface's subnet **and** the RIB belongs to the same
routing instance (`rib_instance(route.rib) == service.routing_instance`). Without the second
condition, a next hop that happens to fall inside an interface's subnet in a different VRF
would land on the wrong service.

A route that matches no service (typically `mgmt_junos.inet.0 0.0.0.0/0` via `fxp0.0`) appears
nowhere in the inventory. In the validation result it shows up under
`unassigned.static_routes` — see [../reference.md](../reference.md#5-result-format).

---

## Static routes: per-hop next hops and aggregates (QNH, schema 6)

Since schema 6, `StaticRoute.next_hops` is a list of **per-hop records**, not a flat list of
next-hop addresses: `{"to", "interface", "qualified", "active"}`. The reason is the
qualified-next-hop (`qualified-next-hop`) — unlike a bare `next-hop`, it can be deactivated
**individually** (verified against the lab on 2026-08-19: `inactive` sits directly on its
node), so a route can have some next hops live and others turned off at the same time. A flat
list of addresses could not express that; that is why activity belongs to the hop, not the
route.

`_parse_next_hops()`:

- a bare `next-hop` yields `{"to": address, "interface": None, "qualified": False, "active":
  True}` — it can only be deactivated as the whole route (`route_type`/route-level `active`),
  so the hop itself is always active;
- a `qualified-next-hop` additionally yields `interface` (if the stanza carries one) and its
  own `active` (`not self._is_inactive(qnh_node)`); because `_is_inactive` walks ancestors, a
  hop under a deactivated route also comes out inactive;
- `discard`, `reject` and a next-table next hop (a table name instead of an IP) yield no hop
  at all — they have no `to`, so there is nothing to compare against a next hop in the
  routing table.

**Mapping onto a service is precedence, not fallback** (`_route_matches_service()`): a hop
with an `interface` is mapped **only** by that interface name — the address subnet is never
tried for it. Without this, a link-local next hop (`fe80::...`), which is typically configured
on more than one interface at once (measured on `et-0/0/8.13`, 2026-08-19), would scatter the
route onto services it has nothing to do with. A hop without `interface` whose `to` is not a
valid IP address is read as an interface name instead of an address (measured in wave 10) —
even then, only a name match is tried, never a subnet. Only a hop without `interface` and with
a valid IP address tries the interface's subnet. **Inactive hops still match** — a route whose
only next hop the operator deactivated must stay with its service, otherwise it would fall
into `unassigned.static_routes` exactly when the report is supposed to flag an incomplete
migration at the next-hop level, not a gap in the inventory.

**Aggregates** (`route_type == "aggregate"`) are parsed by the same `_static_routes_under()`
— `./aggregate` containers next to `./static`, both globally under `routing-options` and
inside every `routing-instances/instance`, with the same RIB-name derivation. An aggregate has
no next hops (`next_hops == []`) — in the configuration it carries `discard`/`reject`, not a
next hop, and those are not parsed, same as for statics. Mapping onto a service therefore does
not go through the next hop but through the RIB itself: a VRF RIB (`rib_instance(route.rib)`
is not `None`) maps onto **every** service of that instance, the global RIB
(`rib_instance` is `None`) maps **only** onto the Core `lo0.0` — not onto any transit interface
(a user decision from 2026-08-19). The `detection_reason` entry has its own sentence
("Agregátní routa patří této službě: …"), separate from the static-route sentence, because it
is a different attribution mechanism, not a variant of the same one.

---

## BFD: inheritance through the BGP hierarchy

`_parse_bfd()` reads `bfd-liveness-detection` at three levels, the more specific overriding
the more general:

```
protocols bgp                      →  source: bgp
  group <name>                     →  source: group
    neighbor <address>             →  source: neighbor
```

Two things this rests on:

- **The whole value is overridden, not merged item by item.** A neighbour with its own
  `minimum-interval` does not inherit `multiplier` from the group. Merging per item would
  produce an intent that appears at no level of the configuration in that form. **Two** lines of
  the form `… or …` carry this — one at the neighbour (`_bfd_values(…) or inherited`) and one at
  the group (`_bfd_values(…) or protocol_level`) — and each needs its own measurement, because a
  per-item merge can survive at one of them and not the other. Three `test_partial_override_of_*`
  tests measure them: neighbour over group, neighbour over `protocols bgp`, and group over
  `protocols bgp`. A neighbour (or group) that sets *both* fields cannot see the difference,
  because both implementations agree for it.
- **Junos `inherit` does not expand this hierarchy.** It expands `apply-groups`, not the
  protocol hierarchy. Verified against the lab on 2026‑07‑29, when group `CPE14` carried BFD
  and its neighbours had none even in the configuration fetched with `inherit` — had the
  parser relied on `inherit`, both peers of that group (including the IPv6 one) would have
  been left without BFD in the inventory.

The `source` field in the YAML says at which level the intent was found — it is the only
trace that a report row came from a group rather than from the neighbour itself.

A service without a routing instance reaches into `self.default_bfd` (top-level
`protocols bgp`); a service inside an instance into `instance.bfd`.

---

## Deactivated configuration produces no intent

`deactivate` is the standard Junos idiom for retiring configuration during a migration — the
stanza stays in the file, but the device does not use it and the XML carries
`inactive="inactive"`. Two different things happen on deactivation, independently of each
other, and it is worth separating them:

- **The service is never dropped from the inventory.** A deactivated service still has to be
  migrated, so disappearing from the output would be a defect, not a fix. Instead it carries two
  flags — `routing_instance_active` and `interface_active` (schema 4, AR‑20/AR‑21) — that say
  whether the deactivated part is the routing instance, the interface, or both. The validator
  reads those flags and SKIPs such a service with the reason `interface deactivated` /
  `RI deactivated` (both: `RI + interface deactivated`), instead of scoring FAIL/WARN as if it
  were running.
- **Intent (a static route, a BFD session, a BGP neighbor) is still dropped completely** from a
  deactivated container — no flag, no trace. If the validator read the intent anyway, it would
  report `FAIL … neni v tabulce` or `FAIL … bez session` for something the operator deliberately
  turned off, and a *false* divergence is the one output that undermines the whole point of
  comparing configuration against reality.

`_is_inactive()` recognises three forms: `inactive="inactive"`, `active="false"` and the
namespaced YANG `active` attribute. Deactivation also inherits from ancestors downward (AR‑19)
— `deactivate routing-instances` reaches every `instance` under it, `deactivate protocols`
reaches `bgp`/`group`/`neighbor` under it, and so on, even when the nested node itself carries
no `inactive` attribute. Without that inheritance, the container level and the item level would
contradict each other: deactivating one VRF drops its statics, deactivating **all** VRFs would
do nothing.

It is checked (with ancestor inheritance) on these nodes:

| node | where | what would otherwise appear |
|---|---|---|
| `instance` | `_parse_static_routes()` skips it; `_parse_routing_instances()` records it with `routing_instance_active: false` | statics of a retired VRF |
| `routing-options` | `_parse_static_routes()`, **both** loops (global and per instance) | every static at that level |
| `rib` | `_static_routes_under()` | the statics of a whole table (typically IPv6) |
| `static` | `_static_routes_under()`, one place covering `static` under `routing-options` and under `rib` alike | the statics of that container |
| `route` | `_static_routes_under()` | a single static |
| `group` | `_parse_bfd()` | the BFD intent of a whole group |
| `neighbor` | `_parse_bfd()`, `_parse_bgp_neighbors()` | a BGP peer and its BFD |
| `bfd-liveness-detection` | `_bfd_node()` | the BFD intent of that level |

Thanks to ancestor inheritance, the `neighbor` row also covers a deactivated `protocols`/`bgp`
one level up (the ancestor walk from `neighbor` reaches them on the way up), and the `instance`
row also covers the `routing-instances` container as a whole — separate rows for those
containers are not needed.

**On `bfd-liveness-detection` the skip has a second effect:** `_bfd_node()` returns `None`, so
inheritance carries on one level up — a neighbour whose BFD is deactivated falls under the
group's rule. That is exactly what Junos does too, so it is not a simplification but agreement
with the device.

**This is why `_bfd_node()` is a method rather than a module-level function.** It needs
`self._is_inactive()`, and duplicating the attribute check inline would put the rule for "what
counts as inactive" in two places.

**Deactivating an interface (`interfaces`, `interface`, `unit`) does not reach into this tree.**
`<interfaces>` and `<routing-instances>`/`<routing-options>` are separate subtrees in the XML, so
an ancestor walk starting from a `neighbor` or `route` node never crosses into a deactivated
interface. Deactivating only the interface therefore leaves the service's statics and BGP
neighbors in the intent — and that is correct: Junos does not stop installing a route just
because the interface a service sits on is disabled, as long as only that unit is disabled. The
validator handles this case differently, through `interface_active`, which SKIPs the whole
service (see above), so the manufactured FAIL on the route still never happens — just through a
different mechanism than inheriting deactivation into the intent.

Deeper levels (an individually deactivated `route`, `bfd-liveness-detection`, or `neighbor`) are
still dropped from intent without a trace — without a flag of their own like `interface_active`.
Extending the flags to that level is deliberately deferred, out of scope for this wave.

**Exception since schema 6: `qualified-next-hop` has its own `active`.** Unlike `route` above,
this node *does* leave a trace of its deactivation — on the hop, not just on the route as a
whole, because a qualified-next-hop can be deactivated independently of its siblings on the
same route (a route with two next hops can have one live and one off). Without a per-hop
`active`, the check would have no way to tell "this next hop stopped being used" apart from
"the route is still in the table, just with a different next hop" — see the section "Static
routes: per-hop next hops and aggregates" above. A bare `next-hop` has no such exception — it
cannot be deactivated on its own, only as the whole route.

---

## Where the two files differ

The difference is concentrated in the detection of EVPN E-LAN and EVPN/VPLS instances:

| | MX (`mx_parser.py`) | EVO (`evo_parser.py`) |
|---|---|---|
| VPLS | `instance-type vpls` or `protocols vpls` | additionally `virtual-switch` + `protocols vpls` |
| E-LAN subtype | `virtual-switch` + bridge domain → `vlan-aware`; `instance-type evpn` → `vlan-based` | first an explicit `service-type` (`vlan-aware` / `vlan-based` / `vlan-bundle`), then `mac-vrf` by VLAN/domain count, then `virtual-switch` + `protocols evpn` |
| EVPN instance | `instance-type evpn` | `instance-type evpn` **or** `mac-vrf` |

The rest of the file (data models, XML helpers, CLI, connection handling, YAML writing) is
identical — only the logger name and the docstring wording differ.

---

## The parsers' CLI

```
usage: mx_parser.py [-h] [--auth {key,password}] [-u USERNAME] [-k KEY_FILE]
                    [--ask-key-passphrase] [-p PORT] [--timeout TIMEOUT]
                    [-o OUTPUT] [--debug] hostname
```

| flag | default |
|---|---|
| `--auth` | `key` |
| `-u/--username` | `ansible` (in key mode) |
| `-k/--key-file` | `~/.ssh/id_rsa` |
| `--ask-key-passphrase` | prompts for the key passphrase |
| `-p/--port` | `22` |
| `--timeout` | `30` |
| `-o/--output` | `<hostname>.yml` (unsafe characters replaced with `_`) |
| `--debug` | verbose logging |

### The parsers' exit codes

**They are not the same as `mig-validate`'s** — the parsers distinguish failure kinds more
finely:

| code | meaning |
|---|---|
| `0` | success |
| `2` | authentication failed |
| `3` | connection timeout |
| `4` | NETCONF refused (`system services netconf ssh`) |
| `5` | other connection error |
| `6` | Junos RPC error |
| `7` | I/O or XML error |
| `99` | unexpected error |
| `130` | interrupted by the user |

---

## Output format

The sample is generated from `tests/fixtures/172.20.20.5.yml`, not written by hand. (The same
L3VPN-CPE13-NNI service also exists in `172.20.20.4.yml` on `ge-0/0/2.113`, but after the AR‑29
regeneration against the lab `ge-0/0/2` there is deactivated — so for a sample of the ordinary,
active shape of a record the source is `.5`, where the same service runs on `et-0/0/8.113`.)

```yaml
schema_version: 6
device: 172.20.20.5
interfaces:
- interface: et-0/0/8.113
  description: L3VPN-CPE13-NNI
  service_type: IPVPN
  service_subtype: null
  ipv4_address:
  - 198.11.13.1/30
  ipv6_address:
  - 2001:db8:11:13::a/127
  virtual_gw_ipv4_address: []
  virtual_gw_ipv6_address: []
  routing_instance: L3VPN-CPE13-NNI
  routing_instance_active: true
  interface_active: true
  protocol:
  - inet
  - inet6
  - bgp
  - vrf
  bgp_neighbor:
  - 198.11.13.2
  - 2001:db8:11:13::b
  bgp_neighbor_inactive: []
  bridge_domain: []
  customer_vlan:
  - '113'
  static_route:
  - rib: L3VPN-CPE13-NNI.inet.0
    prefix: 172.26.1.0/29
    active: true
    route_type: static
    next_hops:
    - to: 198.11.13.2
      interface: null
      qualified: false
      active: true
  - rib: L3VPN-CPE13-NNI.inet6.0
    prefix: 2001:eeee::/64
    active: true
    route_type: static
    next_hops:
    - to: 2001:db8:11:13::b
      interface: null
      qualified: false
      active: true
  bfd:
  - peer: 198.11.13.2
    minimum_interval: 3000
    multiplier: 3
    source: neighbor
  detection_confidence: high
  detection_reason:
  - Rozhraní je přiřazeno do routing instance typu vrf.
  - 'BGP neighbor odpovídá subnetu rozhraní: 198.11.13.2, 2001:db8:11:13::b'
  - 'Statická routa odpovídá subnetu rozhraní: 172.26.1.0/29, 2001:eeee::/64'
```

(The `detection_reason` sentences are Czech because the parsers emit them that way.)

`routing_instance_active` and `interface_active` (schema 4) say whether the routing instance or
the interface of that service is deactivated — see "Deactivated configuration produces no
intent" above. A healthy, fully active service like this one has both `true`.

**Since schema 6, `static_route` is a list of per-hop records, not a flat `next_hop`.** Each
element carries `route_type` (`static`/`aggregate`), a route-level `active`, and `next_hops`
— for a static, a list of hops each with their own `to`/`interface`/`qualified`/`active` (see
the section "Static routes: per-hop next hops and aggregates" above); for an aggregate, an
empty list. There is no aggregate route in the lab services shown here to sample from, but the
shape is the same as for a static, just with `route_type: aggregate` and `next_hops: []`.

Addresses are now split by family — `ipv4_address`/`ipv6_address` and
`virtual_gw_ipv4_address`/`virtual_gw_ipv6_address` — and the YAML always carries a
top-level `schema_version: 6` key. The validator **rejects any other `schema_version`
outright** (`models/inventory.py::load_inventory()`) instead of silently reading a stale
file as a service with no addresses or no intent — see
[models.md](models.md#inventorypy--the-input-from-the-parsers).

The key order is fixed (`clean_service_dict()`) and `service_subtype` stays in the YAML even
when `null`, so downstream scripts get a stable structure.

Which keys the validator actually reads is described in
[models.md](models.md#inventorypy--the-input-from-the-parsers).

---

## `172.20.20.4.yml` and `172.20.20.5.yml` in the root

Sample outputs from the lab topology (MX and PTX), **tracked** in git.

The copies the tests run against live in **`tests/fixtures/`** (commits `26d8461` and
`671615b` moved them there precisely so that the tests do not depend on whatever happens to be
in the root). Both pairs are literal parser output — **do not edit them by hand**; after a
parser change or a `schema_version` bump, regenerate both with a fresh parser run against the
lab and copy them into `tests/fixtures/`.

**They are not produced from the stored captures under `runs/`, but from a live run** — and on
`.5` that is visible. `172.20.20.5.yml` was last regenerated in commit `977783f` against the
lab **after** the CPE14 service was rebuilt onto `ae0.15` / `irb.15`, whereas
`runs/bfd-static-2026-07-29/cfg/172.20.20.5.*.xml` carries
`commit-localtime="2026-07-29 11:43:50 UTC"` — its content is therefore the configuration as of
that commit, **before** the rebuild. (`runs/` is gitignored, so those captures do not travel with
the branch; they live in the working copy the run was made from.) Re-parsing that capture offline therefore yields an inventory without `ae0.15`
and with `irb.15` carrying no description — a difference in the lab, not in the parser. For
`.4`, an offline reparse of the capture is byte-for-byte identical to the committed YAML, so
parser changes can be verified against it directly.
