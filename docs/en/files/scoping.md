# `scoping/` — scopes and service pairing

Files: `builder.py`, `matcher.py`, `mapping.py` and an empty `__init__.py`.

This layer **never reads collected data**. It works purely with the inventory and with
scopes — it is the "which service is which" layer, not the "how is it doing" layer.

---

## `builder.py` — inventory → scopes

A single function, `build_scopes(inventory) -> list[Scope]`.

### Scope eligibility

A scope is created only for an entry that:

1. has a `service_type` in `MIGRATED_SERVICE_TYPES` = `Internet`, `IPVPN`, `E-Line`, `E-LAN`,
   `Core`,
2. **is not a management interface** (`is_management()`: `fxp`, `em`, `me`, `vme`, `bme`,
   `re0:mgmt-`, `re1:mgmt-`).

The management exclusion is built into the tool rather than into `mapping.yml`, so that every
operator does not have to re-enter it. It is necessary because the parser still classifies
these interfaces as `Internet` (`fxp0.0`, `re0:mgmt-0.0`) — **without this rule the tool would
ping into the management network.**

### Layer1 entries

`Layer1` / `physical-port` entries **never become scopes of their own.** They serve only as
confirmation that the physical parent exists in the inventory, and are added to the
`physical_interfaces` of the logical unit sitting on top of them.

The reason is practical: `Layer1` is the most common type in real data (16 of 39 entries in
the sample). If each became a scope, unpaired physical ports would make up the bulk of the
`unmatched` list and it would stop being readable — which would kill exactly the property it
exists for.

The practical effect: `interface_state` does run on the physical port, but as part of the
service running over it.

### Id construction

`svc:<description or interface name>:<service_type>`, for example
`svc:L3VPN-CPE13-NNI:IPVPN`. If several entries would land on the same `ScopeKey` (a `Counter`
over the keys), the interface name is appended: `svc:et-0/0/10.0:IPVPN`. Without that, two
different services would share one id.

### Populating the selectors

| selector | source in `ServiceEntry` |
|---|---|
| `interfaces` | `interface` (always exactly one — AR‑6b depends on this) |
| `physical_interfaces` | `physical_name`, but only when a `Layer1` entry exists for it |
| `routing_instances` | `routing_instance` |
| `bgp_neighbors` | `bgp_neighbor` |
| `local_ipv4` | `ipv4_address` |
| `local_ipv6` | `ipv6_address` |
| `virtual_gw_v4` | `virtual_gw_ipv4_address` |
| `virtual_gw_v6` | `virtual_gw_ipv6_address` |
| `vlans` | `customer_vlan` |
| `bridge_domains` | `bridge_domain` |

---

## `matcher.py` — pairing baseline ↔ subject

`match_scopes(baseline, subject, mapping) -> MatchSet` with the fields `pairs`,
`unmatched_baseline`, `unmatched_subject`.

### The procedure

1. **Ignore** — scopes matching `ignore:` in `mapping.yml` disappear from both sides.
2. **Manual mapping** (`_apply_manual`) — takes absolute precedence; the resulting pair carries
   `method: "manual"`, `confidence: "manual"`.
3. **Automatic rules** in priority order; each rule works only with the scopes left over from
   the previous ones.

| order | `method` | `confidence` | key |
|---|---|---|---|
| 1 | `description+service_type+service_subtype` | high | triple, only when both description and subtype are set |
| 2 | `description+service_type` | high | pair |
| 3 | `routing_instance+service_type` | medium | one key **per** routing instance in the scope |
| 4 | `subnet+service_type` | medium | the network address of every `local_ipv4` **and** `local_ipv6` entry; p2p prefixes (network of ≤ 4 addresses: /30, /31, /127…) are keyed by the full host address so the two ends of one link never pair |
| 5 | `vlan+service_type` | low | every VLAN in the scope |

The key is always **composite**, never the description alone — one description may carry
several entries (`ge-0/0/5` physical and `ge-0/0/5.0` logical share it).

4. Whatever remains goes to `unmatched` with the reason `zadny kandidat na subject`
   ("no candidate on the subject", baseline side) or `nova sluzba, chybi baseline`
   ("new service, no baseline", subject side).

### It never guesses

A pair is formed only when a given key has **exactly one** candidate on each side. Otherwise
every candidate goes to `unmatched` with the reason `ambiguous: N kandidatu (id, id, ...)`.

A silently wrong match would, during a migration, mean a green light on a broken service — so
an admitted non-pairing is better.

### Tracking `paired` and `dropped`

Within a single rule, two sets are maintained (keyed by object `id()`). This exists because of
rules 3–5, which **generate several keys per scope**: a scope discarded as ambiguous under one
key would otherwise be paired under a sibling key of the same rule and end up **in both
`pairs` and `unmatched`**. Both sets are therefore checked.

### Manual mapping and ambiguity

A rule from `mappings:` must resolve to **exactly one** scope on each side. If it resolves to
more, no pair is created and all affected scopes go to `unmatched` with `ambiguous` — the same
semantics as for the automatic rules.

---

## `mapping.py` — `mapping.yml`

### `Selector`

A frozen dataclass with `description`, `service_type`, `interface`. `matches(scope)` is a
logical **AND** over the populated fields; unset fields are ignored. An empty selector is
rejected at load time (`ValueError`), because it would match everything.

`interface` is compared against `scope.selectors.interfaces`, i.e. against the **logical
unit**. Writing `ge-0/0/2` does not hit the five services running over that port — it hits
nothing. That is intentional: under `mappings` a rule must resolve to exactly one scope per
side, and giving the same notation the opposite meaning under `ignore` would be confusing.

### `MappingRule` and `Mapping`

`MappingRule` = a `baseline` selector + a `subject` selector + an optional `note` (pure
documentation; the code does not use it). `Mapping` holds the list of rules and the `ignore`
list; `is_ignored(scope)` answers the matcher's question.

### `load_mapping()` / `empty_mapping()`

`load_mapping()` reads YAML and validates: a rule must have both `baseline` and `subject`,
otherwise `ValueError` with the file path. `empty_mapping()` returns an empty mapping — used
whenever the user does not pass `--mapping`, so the matcher never has to handle `None`.

The file format and examples: [../reference.md](../reference.md#3-mappingyml).
