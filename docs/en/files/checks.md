# `checks/` — the evaluation logic

Files: `base.py`, `registry.py`, `all.py`, `ifaces.py`, `bgp.py`, `evpn.py`,
`reachability.py`, `routes.py`, `bfd.py` and an empty `__init__.py`.

Two rules:

1. **A check never touches the network.** It has no `Device`, no socket, no timeout. The
   docstring of `checks/base.py` explicitly forbids importing anything from
   `migration_validator.connection`.
2. **A check does not return a status.** It returns a `Finding` carrying the measurement; the
   framework derives the status.

The consequence: the entire decision logic is a pure function over data and is tested offline,
without a lab.

The full catalogue with tolerances is in [../reference.md](../reference.md#1-check-catalogue).

---

## `base.py` — the skeleton

### `CheckContext`

What a check receives:

| field | contents |
|---|---|
| `scope` | the filter (needed for `service_type` and interface names) |
| `subject` | facts **already filtered** by the scope |
| `baseline` | the same from the baseline snapshot, or `None` |
| `config` | tolerances and severities |
| `failed_collectors` | `{area: error message}` from the snapshot |

A check therefore **cannot accidentally reach into another service** — filtering is the scope
layer's responsibility and is tested separately.

### `Check`

```python
class Check(ABC):
    id: str                       # "bgp_session_state"
    title: str                    # "Stav BGP session"
    label: str                    # "BGP status" — the CHECK column label
    mode: Mode                    # state | compare | both
    requires: tuple[str, ...]     # areas from facts/probes, e.g. ("bgp",)
    requires_inventory: bool
    service_types: frozenset[str] | None    # None = all
    default_severity: Severity

    def run(self, ctx) -> list[Finding]
```

`applies_to(scope)` returns `True` for the **device scope always** (there is nothing to filter
by) and otherwise compares `service_type`.

`label` is **required** and is not `title`: `title` is a sentence about the check ("Stav BGP
session"), `label` is the report's `CHECK` column label ("BGP status"). It is used for rows
that did not originate inside the check (framework skips) and fills in for a finding that
carries no label of its own. Without it a row fell back to the check `id` and
`SKIP | evpn_esi_status` sat among readable labels — which is why `run_check()` fills it in,
not the renderer, which would have nothing better to reach for.

`describe()` is what `mig-validate checks` and a future GUI see.

### `run_check()` — the only path to a status

The gates a check passes through, in order:

1. `config.enabled(id)` is `False` → **empty list** (the check does not appear in the report
   at all),
2. `applies_to(scope)` is `False` → empty list,
3. `requires_inventory` and the scope is a device scope → `SKIP`
   (`check vyzaduje inventory, snapshot ji neobsahuje`),
4. `mode == COMPARE` and no baseline → `SKIP` (`porovnavaci check bez baseline snapshotu`),
5. an area in `requires` is in `failed_collectors` → `SKIP` **carrying the original error
   message from the capture**,
6. `check.run()` raises → `SKIP` (`check selhal: ...`) — one broken check must not kill the
   whole run,
7. otherwise every `Finding` becomes a `CheckResult` via `derive_status(outcome, severity)`.

A `SKIP` from steps 3–6 is a **full report row**: it takes the check's `label` and a short
reason as its `value` (`bez inventory`, `bez baseline`, `collector selhal`, `check selhal`).
The full sentence stays in `message` for the `NALEZ` column and the machine output — while
those rows had no value the renderer reached for that sentence instead, and a failed
collector's 190-character RPC error stretched the block to 270 characters wide.

The difference between steps 1–2 (empty list) and 3–6 (`SKIP`) is deliberate: *"does not apply
here"* should not count towards the summary, *"not measured"* should.

## `registry.py`

`@register` stores an instance under `cls.id`; a duplicate raises `ValueError`. `all_checks()`
returns an alphabetically sorted list — which is why check ordering in the output is stable.
`checks_for(scope, config)` and `get_check(id)` are auxiliary queries (the engine uses
`all_checks()` and filters inside `run_check`).

## `all.py`

Imports `bfd`, `bgp`, `evpn`, `ifaces`, `reachability`, `routes`. It is a separate module **because of a
circular import**: `checks/ifaces.py` imports `checks/base.py`, so `checks/__init__.py` must
not import `ifaces`. `load_all()` is an idempotent no-op — the import already did the work.

---

## `ifaces.py` — interfaces

Shared pieces:

- **`is_transit(name)`** — an allowlist of `ge`, `xe`, `et`, `ae`. Only on such interfaces do
  counter-based checks carry information.
- **`INTERNAL_PREFIXES`** — the list of internal interfaces (`fxp`, `lo0`, `irb`, `vtep`,
  `lsi`, …). On those, counter checks return **`SKIP`, not WARN**: `SKIP` means "this test does
  not belong here", `WARN` means "something is wrong". If internal interfaces glowed amber
  permanently, the operator would learn to skip the output and the tool would lose its point.
- **`percent_change(old, new)`** — returns `None` when the baseline was zero (no dividing by
  zero). Also used by `bgp.py` and `evpn.py`.

The classification is a property of the **check, not the scope**: `irb.14` gets no counter
checks, but it is still a full-blown Internet service and ARP, ping and BGP checks run on it
normally.

### `interface_state` (state, critical)

Both `admin_status` and `oper_status` must be `up`. It runs on **all** of the scope's
interfaces, including internal ones — unlike counters, state is meaningful there. With no data
it returns `SKIP`.

**One `Finding` per fact, not per interface**: `admin_status` and `oper_status` are reported
as two separate rows (label `Interface admin status (<name>)` / `Interface operational status
(<name>)`), so
the report can show which of the two is actually broken instead of just "interface not OK".
The message is `<name>: admin_status <state>` resp. `<name>: oper_status <state>`; the value
column carries the state capitalised (`Up`, `Down`).

### `interface_errors` (state, advisory)

The sum of `input_errors`, `output_errors` and `framing_errors` must be 0. Transit interfaces
only. One `Finding` per interface (label `Interface errors (<name>)`) — the counters are folded
into a single message (`input_errors=3`), unlike `interface_state`/`interface_traffic` this one
does not split into separate rows.

### `interface_traffic` (both, advisory)

- **without a baseline** (or when the interface is absent from the baseline): with
  `require_nonzero`, both `input_pps` and `output_pps` must be > 0, otherwise `broken` → WARN
  with the message `<name>: <input_pps|output_pps> <value> pps` (e.g. `et-0/0/8: input_pps 0
  pps`);
- **with a baseline**: the percentage drop against `tolerance_percent` (default −60 %). The
  per-direction change goes into `details`, the raw numbers into `baseline`/`subject`.

**One `Finding` per direction, not per interface**: `input_pps` (label `Interface traffic in
(<name>)`) and `output_pps` (label `Interface traffic out (<name>)`) are two separate rows, so
the report shows a drop only on the direction where it actually happened.

**Every label carries the interface name in parentheses.** A scope holds both the physical and
the logical interface, so without it a block would carry pairs of rows with identical labels,
different values and contradictory `ZMENA` columns — with no way to tell which interface is
which.

The baseline data arrives with its interfaces already renamed — see
`engine._aligned_baseline_data()`.

### `traffic_ceased` (compare, advisory, **disabled by default**)

Inverted logic: it verifies that traffic on the **old** interface dropped to zero after the
migration. It catches a forgotten shutdown and duplicate forwarding. It requires a third
capture (the old box after the migration) — in the CLI that is a normal `capture` +
`evaluate`, no special mode.

It returns `SKIP` (not WARN) when the interface is missing from the baseline, or when the
baseline carried no traffic at all — in which case ceasing cannot be verified.

---

## `bgp.py`

Both checks run only on `Internet` and `IPVPN` and return `SKIP` when the scope has no peers.

### `bgp_session_state` (both, critical)

- state ≠ `Established` → `broken` → **FAIL**. It carries `baseline_value`, so the `ZMENA`
  column shows `bylo Established` — the regression is visible exactly where it matters,
- state `Established` but **different in the baseline** → **PASS** with the message
  `stav se zmenil X -> Established`. This branch is only reachable with state `Established`
  (worse states leave earlier), so it covers precisely and only the case where the session
  **improved** during the migration — and an improvement is not a warning (decision R-2). The
  change does not vanish: the message names it and the `ZMENA` column shows the previous state,
- state `Established` and unchanged (or no baseline) → PASS.

Every `Finding` carries `label="BGP status"` and a `family` derived by `peer_family()` from
the peer's address — the report uses it to place the row in the `IPv4`/`IPv6` section.

### `bgp_prefix_counts` (compare, advisory)

Compares `received` / `accepted` / `advertised` / `active` against
`tolerance_percent` (default −10 %). A peer absent from the baseline gets a `SKIP` — not a
PASS (message `<peer>: peer neni v baseline snapshotu, nelze porovnat`).

**Counts are kept and compared per RIB** (`bgp.rtarget.0`, `inet.0`, `bgp.l3vpn.0`, ...) —
that is how the collector stored them in the first place (see
[collectors.md](collectors.md#bgppy--peer-state-and-prefix-counts)). If an otherwise-paired
peer is missing a specific RIB in the baseline, that pair gets its own `SKIP`
(`<peer>/<rib>: RIB neni v baseline, nelze porovnat`) rather than a silently computed zero.
`peer_family()` derives a row's family from the **peer's address**, not the RIB name — the
name need not carry a family at all (`bgp.l3vpn.0`), and a peer with an IPv4 address that
also carries an IPv6 RIB is filed entirely under the IPv4 section (a documented limitation).

The row label is `BGP <key>-prefix-count` (e.g. `BGP active-prefix-count`), so the report
gets five separate rows per RIB, not one summary row.

Comparison uses **a tolerance, not 1:1 equality**. Experience from JSNAPy is that exact
matching generates a flood of FAILs over a difference of a few routes, which is not
significant. Prefix growth is not a problem.

---

## `evpn.py`

The shared **`_is_up(status)`** helper compares only the part before the slash: the local
interface in an ESI reports `Up/Forwarding`, a VPWS interface only `Up`. Exact equality would
mark the former as broken.

None of these checks contains a platform branch — the MX (`virtual-switch` / `bridge-domain`)
vs EVO (`mac-vrf` / VLAN) difference was absorbed by the collector.

### `evpn_vpws_status` (both, critical, E-Line only)

- the interface status must be `Up`,
- **a remote SID must arrive**. Local and remote SIDs deliberately **differ** in EVPN-VPWS —
  each side advertises its own service ID (`local 1000; remote 2000`), so equality is not an
  invariant. It FAILs only when no remote SID arrives at all.

### `evpn_esi_status` (both, critical, E-LAN only)

The local interface status in the segment must be `Up`; the message includes the DF (the IP
address of the elected designated forwarder).

### `evpn_mac_count` (both, advisory, E-LAN only)

Iterates instances and, within them, domains (keyed by VLAN id, or `"-"` for vlan-based). The
label is `instance/vlan`, or just `instance` for vlan-based.

- **without a baseline**: 0 learned MACs → `broken` → WARN,
- **with a baseline**: a drop beyond `tolerance_percent` (default −60 %) → WARN. Zero MACs is a
  WARN even when the drop would fit inside the tolerance.

---

## `reachability.py`

Three checks: `arp_present`, `nd_present` and `ping_reachability` — ARP is the IPv4 variant,
ND its IPv6 counterpart. All three have `requires_inventory = True` (so they `SKIP` on a
device scope) and run only on `Internet` and `IPVPN`. All three are knowingly
**best-effort** — the CPE may be powered off or block ICMP — hence the default severity
`advisory`.

**Each check returns one `Finding` per entry** (ARP/ND) **or per target** (ping) — the report
prints the rows individually, not as a summary sentence.

Shared helpers:

- **`owning_prefix(address, prefixes)`** — which configured range contains this address.
  Used for `details["address"]`, so a service with several ranges in one family gets a row
  that says which range it belongs to; the report only prints it in the label when the
  family has more than one address (`view.py::_row`, `qualify=len(own) > 1`) — with a single
  address it is redundant, since the address is already in the section header.
- **`link_local_is_configured(scope)`** (in `nd_present`) / the functionally identical
  `_link_local_configured()` in `probes/ping.py` — does the service have a link-local address
  configured directly under the interface? This checks **presence, not exclusivity**: one
  link-local address among the configured ones is enough, even alongside an ordinary
  routable address — either way it returns `True`. Link-local neighbours show up on every
  IPv6 interface and say nothing about the customer service by themselves, which is why they
  are otherwise filtered out. But some deployments have the service use link-local — then
  those neighbours are exactly what the service talks to, and the filter must let them
  through. Configuration decides, not a heuristic.

### `arp_present` (state, advisory)

- **no IPv4 address configured** (`scope.selectors.local_ipv4` empty) → **no `Finding` at all**
  — a service without IPv4 should not get an ARP finding, let alone a WARN for a neighbour that
  could never have existed. It used to return a `SKIP` stamped `family=4`, but that stamp was
  exactly what forced a section for a family the renderer is supposed to omit (decision R-1);
- no ARP entry on the service's interfaces → `broken` → FAIL/WARN, message `na rozhranich
  sluzby neni zadny ARP zaznam`, `value` = `zadny zaznam`;
- otherwise **one `Finding` per ARP entry**: message `ARP zaznam <ip>`, `label="ARP"`,
  `family=4`, `value` = `<mac> -> <ip>` (`?` when the MAC is missing).

### `nd_present` (state, advisory)

Mirrors `arp_present` for IPv6:

- no IPv6 address configured → **no `Finding` at all** (see `arp_present` above);
- link-local neighbours are **dropped** unless the service itself has link-local as a
  configured address (`link_local_is_configured()`);
- no **usable** ND entry remains → `broken`, message `na rozhranich sluzby neni zadny
  pouzitelny ND zaznam` (note the extra word "pouzitelny" compared to ARP — precisely
  because of the filtered-out link-local neighbours), `value` = `zadny zaznam`;
- otherwise **one `Finding` per ND entry**: message `ND zaznam <ip>`, `label="ND"`,
  `family=6`, `value` = `<mac> -> <ip>`; `subject` additionally carries `state` (ND, unlike
  ARP, has an entry state).

### `ping_reachability` (state, advisory)

Reads finished results from the snapshot — the targets were resolved back during `capture`
(ARP/ND → ping, `probes/ping.py`).

- the snapshot has no targets for this scope → `SKIP` (`pro tento scope nejsou ve snapshotu
  zadne cile pingu`);
- a probe with no recognised family (`family` outside `4`/`6`) → its own `SKIP` naming the
  targets (`probe bez rodiny nelze vyhodnotit: ...`) — otherwise the probe would silently
  vanish from the result instead of saying it was never evaluated;
- otherwise **one `Finding` per target**, grouped by `family`:
  - at least one packet answered → `ok`, message `<target>: odpovedelo N z M`,
  - none answered → `broken`, message `<target>: neodpovedel (M paketu)`.

`value` is `<received>/<sent>` plus `  <rtt> ms` on a successful reply; on failure it is
`  <target> neodpovedel`. `details` carries `resolved_from` (`arp` | `nd` |
`subnet-fallback`) and `address` (`owning_prefix()`), so the result shows which configured
range the target belongs to and whether it came from ARP/ND or was derived from the subnet.

---

## `routes.py` — static routes

### `static_route_status` (both, critical)

The only check that **compares configured intent against measured reality**. Every other
check asks "is it up?"; this one asks "is what you ordered actually there?".

**It iterates over the union of three sources** (AR‑14) — the subject's configuration
(`Selectors.static_routes`), the subject's measurement (`facts["routes"]`) and the
baseline's measurement. Each closes one gap:

| without this source | what would silently disappear |
|---|---|
| configuration | the "configured, not in the table" discrepancy |
| subject measurement | there would be nothing to print in inventory-less mode |
| baseline measurement | a route removed from the configuration — it never reaches the subject's selectors, so no scope would ever ask about it being gone |

**A route's identity is the pair (RIB, prefix); the next hop is the value.** That way a
next-hop change reads as a *changed* route — one row with a `ZMENA` column — rather than
"one route vanished and another appeared".

| situation | Outcome | status | `value` |
|---|---|---|---|
| in the table, next hop unchanged or no baseline | `ok` | PASS | next hops joined by commas (`-` when none) |
| in the table, next hop changed vs. baseline | `degraded` | WARN | new next hop; `ZMENA` carries the old one |
| configured, absent from the table (service scope) | `broken` | FAIL | `neni v tabulce` |
| present in baseline, absent from subject | `broken` | FAIL | `chybi` |

`is_device` separates the last two rows: in a device scope the intent is unknown (AR‑17), so
the tool reports `chybi`, not `neni v tabulce`.

The label is `Staticka routa (<RIB> <prefix>)` — the RIB name goes into the label qualifier,
not onto a sub-row of its own. `family` is derived **from the prefix**, not from the RIB
name, because the name need not carry a family at all (`bgp.l3vpn.0`).

**Recorded assumption:** RIB names survive the migration. `_aligned_baseline_data` in the
engine re-keys only the `interfaces` area between baseline and subject; `routes` are keyed
`table -> prefix` and get no re-keying. Should a future migration rename a VRF, every route
in it would read as "missing" plus "new".

---

## `bfd.py` — BFD sessions

### `bfd_session_state` (both, critical)

Like `static_route_status`, it **iterates over the union of three sources** (AR‑14): the
intent from the configuration (`Selectors.bfd_peers`), the session in the subject and the
session in the baseline. **A peer that never had BFD gets no row** (decision R‑1) — so a
service without BFD carries no mention of BFD in the report at all.

The check requires **two areas**: `("bfd", "bgp")`.

| situation | Outcome | status | `value` |
|---|---|---|---|
| session exists, state `Up` | `ok` | PASS | `Up` |
| session exists, any other state | `broken` | FAIL | measured state (`Down`, …) |
| session exists but is not in the service configuration (service scope) | `degraded` | WARN | `bez konfigurace` |
| no session and no intent, but the baseline had one | `broken` | FAIL | `BFD odstraneno` |
| intent present, no session, **BGP not `Established`** | `SKIP` | SKIP | `BGP neni Established` |
| intent present, BGP running, still no session | `broken` | FAIL | `bez session` |

**The dependency on BGP state is deliberate, not cosmetic.** BFD cannot come up while BGP is
down, so without it a service with a dropped BGP session would collect two FAIL rows for one
cause. In a live run that would repeat for every service that has not yet come up, and the
operator would learn to skim past the listing.

**Branch order matters:** `BFD odstraneno` is tested **before** `BGP neni Established`. The
reverse order would silently lose the case where the migration dropped protection that used
to be there *and* BGP had not come up — which is exactly the combination worth seeing.

The label is `BFD (<peer>)`, with `family` derived by `peer_family()` from the peer address
(shared with `checks/bgp.py`), so an IPv6 peer inherited from a BGP group lands in the
`IPv6` section.
