# `checks/` — the evaluation logic

Files: `base.py`, `registry.py`, `all.py`, `ifaces.py`, `bgp.py`, `evpn.py`,
`reachability.py`, `routes.py`, `bfd.py`, `core_protocols.py`, `deactivation.py`
and an empty `__init__.py`.

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
    service_subtypes: frozenset[str] | None # AND with service_types, None = no filter
    default_severity: Severity

    def run(self, ctx) -> list[Finding]
```

`applies_to(scope)` returns `True` for the **device scope always** (there is nothing to filter
by) and otherwise compares `service_type` and — when set — `service_subtype` too (both must
match, it is an AND, not an alternative). It distinguishes roles within one `service_type`,
typically Core **transit** vs. Core **loopback** (2026-08-26 wave): `service_types={"Core"}`
alone cannot tell the two roles apart; `service_subtypes={"transit"}` keeps `isis_overview`
(which only belongs on lo0.0) away from transit scopes.

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
5. `check.id` is not `deactivation_state` and `scope.is_deactivated` is `True` → `SKIP`
   (`sluzba je v konfiguraci deaktivovana ({reason})`) — this gate deliberately sits after
   gate 3 (`requires_inventory`), because a device scope has no inventory and therefore has
   nothing to read the flag from; `deactivation_state` itself is the one check that does not
   pass through this gate — otherwise there would be nothing left to compare and the service
   would vanish from the report into an unexplained `SKIP`,
6. an area in `requires` is in `failed_collectors` → `SKIP` **carrying the original error
   message from the capture**,
7. `check.run()` raises → `SKIP` (`check selhal: ...`) — one broken check must not kill the
   whole run,
8. otherwise every `Finding` becomes a `CheckResult` via `derive_status(outcome, severity)`.

A `SKIP` from steps 3–7 is a **full report row**: it takes the check's `label` and a short
reason as its `value` (`bez inventory`, `bez baseline`, `RI deactivated`,
`interface deactivated`, `RI + interface deactivated`, `collector selhal`, `check selhal`).
The full sentence stays in `message` for the `NALEZ` column and the machine output — while
those rows had no value the renderer reached for that sentence instead, and a failed
collector's 190-character RPC error stretched the block to 270 characters wide.

The difference between steps 1–2 (empty list) and 3–7 (`SKIP`) is deliberate: *"does not apply
here"* should not count towards the summary, *"not measured"* should.

## `registry.py`

`@register` stores an instance under `cls.id`; a duplicate raises `ValueError`. `all_checks()`
returns the list sorted by `(order, id)` — `Check.order` defaults to 0 (alphabetical by id, as before); the multicast checks set 10–13 so their rows sit together after the interface rows. This order is the row order inside a report block.
`checks_for(scope, config)` and `get_check(id)` are auxiliary queries (the engine uses
`all_checks()` and filters inside `run_check`).

## `all.py`

Imports `bfd`, `bgp`, `deactivation`, `evpn`, `ifaces`, `reachability`, `routes`. It is a
separate module **because of a circular import**: `checks/ifaces.py` imports
`checks/base.py`, so `checks/__init__.py` must not import `ifaces`. `load_all()` is an
idempotent no-op — the import already did the work.

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

| situation | Outcome | status | `value` |
|---|---|---|---|
| no interface data in the subject | `SKIP` | SKIP | `bez dat` |
| `admin_status == "up"` | `ok` | PASS | `Up` |
| `admin_status != "up"` (missing → `"unknown"`) | `broken` | FAIL | capitalised state, e.g. `Down`, `Unknown` |
| `oper_status == "up"` | `ok` | PASS | `Up` |
| `oper_status != "up"` (missing → `"unknown"`) | `broken` | FAIL | capitalised state |

### `interface_errors` (state, advisory)

The sum of `input_errors`, `output_errors` and `framing_errors` must be 0. Transit interfaces
only. One `Finding` per interface (label `Interface errors (<name>)`) — the counters are folded
into a single message (`input_errors=3`), unlike `interface_state`/`interface_traffic` this one
does not split into separate rows. A physical transit interface whose data carries **none** of
the three counter keys at all is `degraded` (not a silent `ok`) — the interface simply does not
report error counters, which is not the same claim as "measured, zero errors": message
`<name>: chybove countery nebyly zmereny (rozhrani nevraci error countery)`, `value` =
`nezmereno`.

| situation | Outcome | status | `value` |
|---|---|---|---|
| service scope with an L3 link whose L2 side has no transit interface | `INFO` | INFO | `mereno na L2 (<peers>) - viz blok/bloky nize` |
| service scope with an L1 (layer1) parent absorbing the transit interface | *(no finding)* | — | — |
| scope has transit interfaces but only logical units, no physical one | `SKIP` | SKIP | `jen unity` |
| no transit interface at all | `SKIP` | SKIP | `netranzitni rozhrani` |
| physical transit interface, none of the three counter keys present | `degraded` | WARN | `nezmereno` |
| physical transit interface, sum of counters == 0 | `ok` | PASS | `bez chyb` |
| physical transit interface, sum of counters > 0 | `broken` | WARN (advisory) | comma-joined non-zero counters, e.g. `input_errors=3` |

### `interface_traffic` (both, advisory)

- **without a baseline** (or when the interface is absent from the baseline): with
  `require_nonzero`, both `input_pps` and `output_pps` must be > 0, otherwise `broken` → WARN
  with the message naming the reason, `<name>: <input_pps|output_pps> 0 pps, ocekavan
  nenulovy provoz`;
- **with a baseline, baseline pps == 0 and subject pps == 0**: `ok`, message `<name>: <key>
  stejne jako baseline (0 pps)` — `require_nonzero` is deliberately not re-applied when a
  baseline exists, since a service that had no traffic before the migration should not FAIL
  just for still having none;
- **with a baseline otherwise**: the percentage drop against `tolerance_percent` (default
  −60 %). The per-direction change goes into `details`, the raw numbers into
  `baseline`/`subject`.

**One `Finding` per direction, not per interface**: `input_pps` (label `Interface traffic in
(<name>)`) and `output_pps` (label `Interface traffic out (<name>)`) are two separate rows, so
the report shows a drop only on the direction where it actually happened.

**Every label carries the interface name in parentheses.** A scope holds both the physical and
the logical interface, so without it a block would carry pairs of rows with identical labels,
different values and contradictory `ZMENA` columns — with no way to tell which interface is
which.

The baseline data arrives with its interfaces already renamed — see
`engine._aligned_baseline_data()`.

| situation | Outcome | status | `value` |
|---|---|---|---|
| service scope with an L3 link whose L2 side has no transit interface | *(no finding)* | — | — |
| no transit interface at all | `SKIP` | SKIP | `netranzitni rozhrani` |
| no baseline for the interface, `require_nonzero` and pps == 0 | `broken` | WARN (advisory) | `0 pps` |
| no baseline for the interface, otherwise | `ok` | PASS | measured pps |
| baseline pps == 0, subject pps == 0 | `ok` | PASS | `0 pps` |
| baseline pps == 0, subject pps > 0 | `ok` | PASS | measured pps |
| baseline pps > 0, drop past `tolerance_percent` | `broken` | WARN (advisory) | measured pps |
| baseline pps > 0, within tolerance (including any growth) | `ok` | PASS | measured pps |

### `traffic_ceased` (compare, advisory, **disabled by default**)

Inverted logic: it verifies that traffic on the **old** interface dropped to zero after the
migration. It catches a forgotten shutdown and duplicate forwarding. It requires a third
capture (the old box after the migration) — in the CLI that is a normal `capture` +
`evaluate`, no special mode.

It returns `SKIP` (not WARN) when the interface is missing from the baseline, or when the
baseline carried no traffic at all — in which case ceasing cannot be verified.

| situation | Outcome | status | `value` |
|---|---|---|---|
| service scope with an L3 link whose L2 side has no transit interface | *(no finding)* | — | — |
| no transit interface at all | `SKIP` | SKIP | `netranzitni rozhrani` |
| interface missing from the baseline entirely | `SKIP` | SKIP | `bez baseline` |
| baseline had zero traffic in both directions | `SKIP` | SKIP | `bez provozu v baseline` |
| residual (max of both directions) > `max_residual_pps` | `broken` | WARN (advisory) | residual pps |
| residual within the threshold | `ok` | PASS | residual pps |

---

## `optics.py` — optical levels and alarms

Both checks run **only on layer1/device scope** (`service_types = frozenset()`, which never
matches any service). Ports iterated: the interface itself plus its sorted LAG members. A port
absent from `optics` entirely gets a single `SKIP` (`<name>: rozhrani nevraci opticka data`,
`value` = `bez optiky`); otherwise one row per lane.

### `interface_optics_levels` (both, critical, layer1 only)

Per lane: RX/TX power in dBm. A "dark side" (RX and/or TX non-finite, i.e. no light) is
`broken` regardless of baseline. With a baseline and no dark side, a shift beyond
`tolerance_db` (default 2.0 dB) on either side is `degraded` (always WARN); within tolerance
is `ok`.

| situation | Outcome | status | `value` |
|---|---|---|---|
| port not reporting optics at all | `SKIP` | SKIP | `bez optiky` |
| RX and/or TX non-finite (no baseline, or with baseline) | `broken` | FAIL | `RX <x> / TX <y>` |
| baseline present, not dark, shift beyond `tolerance_db` | `degraded` | WARN | `RX <x> / TX <y>` |
| baseline present, not dark, within tolerance (or no baseline at all) | `ok` | PASS | `RX <x> / TX <y>` |

### `interface_optics_alarms` (state, critical, layer1 only)

Per port: a quiet port (no lane has any raised `alarms`/`warnings` entry) gets one summary row.
An alarm and a warning both use the same wording, only the Outcome/status differ — `<name>:
<tag> je aktivni` (previously `je zvednuty`, which read like a question rather than a
statement of what is wrong).

| situation | Outcome | status | `value` |
|---|---|---|---|
| port not reporting optics at all | `SKIP` | SKIP | `bez optiky` |
| no raised alarm/warning on any lane | `ok` | PASS | `bez alarmu` |
| per raised `alarms[tag]` | `broken` | FAIL | the tag |
| per raised `warnings[tag]` | `degraded` | WARN | the tag |

---

## `bgp.py`

Both checks have `service_types={"Internet", "IPVPN"}`, but **since the 2026-08-26 wave they
also run on the Core loopback scope** (`service_subtype == "loopback"`) — the internal iBGP
peers on lo0.0. The `_AppliesToCoreLoopback` mixin does this by overriding `applies_to()`: for
`service_type == "Core"` it returns `True` only when `service_subtype == "loopback"` (Core
transit has no BGP peers and would get empty SKIP/FAIL rows), otherwise it defers to
`super().applies_to()` (i.e. to `service_types`). The machine-readable catalog (`describe()`,
`mig-validate checks`) discloses this extended applicability too —
`_AppliesToCoreLoopback.describe()` adds `"Core"` to `service_types` and adds
`service_subtypes_by_type: {"Core": ["loopback"]}`, since bare `service_types` alone would
misstate where the check runs (finding from the final review). With no peers in scope, it
returns `SKIP`.

### `bgp_session_state` (both, critical)

- state ≠ `Established` → `broken` → **FAIL**, message `<peer>: stav <state>, ocekavano
  Established`. It carries `baseline_value`, so the `ZMENA` column shows `bylo Established` —
  the regression is visible exactly where it matters,
- state `Established` but **different in the baseline** → **`recovered`** → **RECV** with the
  message `stav se zmenil X -> Established`. This branch is only reachable with state
  `Established` (worse states leave earlier), so it covers precisely and only the case where
  the session **improved** during the migration — and an improvement is not a silent PASS, the
  operator should see it recovered (decision R-2/point 20). The change does not vanish: the
  message names it and the `ZMENA` column shows the previous state,
- state `Established` and unchanged (or no baseline) → PASS.

Every `Finding` carries `label=f"BGP status ({peer})"` and a `family` derived by `peer_family()` from
the peer's address — the report uses it to place the row in the `IPv4`/`IPv6` section.

| situation | Outcome | status | `value` |
|---|---|---|---|
| peer measured, state ≠ `Established` | `broken` | FAIL | measured state |
| peer measured, state `Established`, baseline had a different state | `recovered` | RECV | `Established` |
| peer measured, state `Established`, unchanged or no baseline | `ok` | PASS | `Established` |
| peer deactivated in config, baseline had it running | `broken` (`deactivation_outcome`) | FAIL | `deaktivovan` |
| peer deactivated in config, baseline also deactivated/unknown | `degraded` (`deactivation_outcome`) | WARN | `deaktivovan` |
| peer configured, no session, not deactivated | `broken` | FAIL | `bez session` |
| peer only in baseline (no longer claimed by this service) | `broken` | FAIL | `v baseline patril k teto sluzbe, v subjektu uz ne` |
| no configured/inactive/measured/baseline peer at all | `SKIP` | SKIP | `zadny peer` |

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

The row label is `<key>-prefix-count` (e.g. `active-prefix-count`) and the identity goes
into `group=f"BGP {peer} / {rib_name}"`, so the report gets five separate rows per RIB
gathered under a named peer/RIB heading, not one summary row.

Comparison uses **a tolerance, not 1:1 equality**. Experience from JSNAPy is that exact
matching generates a flood of FAILs over a difference of a few routes, which is not
significant. **Growth is not ignored either** — it is symmetric: a drop past
`tolerance_percent` is `broken` (WARN at the group's default advisory severity), a rise past
the same threshold in the other direction (`change > abs(tolerance)`) is `degraded` (always
WARN) — a surprising jump upward is exactly as worth a look as a drop, even though nothing is
"down". A RIB the baseline had but the subject never measured at all (a family disconnected
during the migration) is its own `broken` finding, not silence.

| situation | Outcome | status | `value` |
|---|---|---|---|
| peer absent from the subject entirely | `SKIP` | SKIP | `zadna session` |
| peer absent from the baseline | `SKIP` | SKIP | `bez baseline` |
| RIB absent from the baseline for a peer present in both | `SKIP` | SKIP | `bez baseline` |
| per counter, drop past `tolerance_percent` (message `pokles <key> <b> -> <s>, prah je <tol> %`) | `broken` | WARN (advisory) | measured count |
| per counter, rise past `abs(tolerance_percent)` (message `narust <key> <b> -> <s>, prah je +<tol> %`) | `degraded` | WARN | measured count |
| per counter, within tolerance | `ok` | PASS | measured count |
| RIB the baseline had, the subject does not measure at all (message `RIB v baseline byla, v subjektu chybi`) | `broken` | WARN (advisory) | `chybi` |

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

Per peer (local or remote side), the "peer status" row's message names the reason instead of
repeating the OK sentence: `<instance>: <side> peer <ip> neni Resolved (<status or 'chybi'>)`.
The `mode`/`esi`/`role` INFO rows use the same display name (`ESI`, capitalized) in both the
message and the label — the message no longer echoes the raw dict key (`esi`, lowercase),
which used to disagree with the label of the very same row.

| situation | Outcome | status | `value` |
|---|---|---|---|
| local interface status `Up` | `ok` | PASS | measured status |
| local interface status not `Up` | `broken` | FAIL | measured status |
| remote side, no peers at all | `broken` (PE row) + `broken` (status row) | FAIL | `Neznamy peer` / `Unresolved / Chybi` |
| local side, no peers (single-homed, expected) | `INFO` | INFO | mode, annotated "multi-homing peer not found" |
| per peer, status `Resolved` | `ok` | PASS | peer address |
| per peer, status not `Resolved` (message names the status) | `broken` | FAIL | peer address |

### `evpn_esi_status` (both, critical, E-LAN only)

The local interface status in the segment must be `Up`; the message includes the DF (the IP
address of the elected designated forwarder).

| situation | Outcome | status | `value` |
|---|---|---|---|
| local interface status `Up` | `ok` | PASS | `<interface> <status>` |
| local interface status not `Up` | `broken` | FAIL | `<interface> <status>` |
| `df_role` contains "not elected" | `broken` | FAIL | the raw `df_role` text (message is `<esi>: <df_role>`, no duplicated `DF` prefix when `df_role` already starts with it) |
| `df_role` is `None` or `""` (no DF on record) | `INFO` | INFO | `-` (message `DF bez zaznamu`) |
| `df_role` otherwise (elected DF address) | `ok` | PASS | the raw `df_role` text |

### `evpn_instance_status` (both, critical, E-LAN only)

Per instance: EVPN neighbor count (`> 0`, WARN below baseline), one `INFO` row per neighbor
address, ESI status/local-interface/IRB blocks (only when the scope has no interface
selectors — a service with selectors gets its own ESI detail from `evpn_esi_status`
instead), one row per local EVPN interface and per IRB interface. **`baseline_value` is
filled for EVPN interface, IRB interface, EVPN neighbor and ESI rows whenever the baseline
carries the matching item** — earlier these rows never compared against baseline at all.

| situation | Outcome | status | `value` |
|---|---|---|---|
| EVPN neighbors total > 0, not below baseline | `ok` | PASS | measured total |
| EVPN neighbors total > 0 but below baseline | `degraded` | WARN | measured total |
| EVPN neighbors total is 0/missing | `broken` | FAIL | `0` |
| local EVPN interface status `Up` | `ok` | PASS | `<name> <status>` |
| local EVPN interface status not `Up` | `broken` | FAIL | `<name> <status>` |
| unit expected by selectors but missing from the instance | `broken` | FAIL | `<unit> chybi v instanci` |
| IRB interface status `Up` | `ok` | PASS | `<name> <status>` (+ `(<l3_context>)`) |
| IRB interface status not `Up` | `broken` | FAIL | same, `ocekavano Up` |
| ESI in baseline, missing from subject | `broken` | FAIL | `chybi` |
| ESI status starts with "resolved" | `ok` | PASS | measured status |
| ESI status present, not resolved | `broken` | FAIL | measured status |
| ESI status is `""` | `broken` | FAIL | `bez statusu` |

### `evpn_mac_count` (both, advisory, E-LAN only)

Iterates instances and, within them, domains (keyed by VLAN id, or `"-"` for vlan-based). The
label is `instance/vlan`, or just `instance` for vlan-based.

- **without a baseline**: 0 learned MACs → `broken` → WARN,
- **with a baseline**: a drop beyond `tolerance_percent` (default −60 %) → WARN. Zero MACs is a
  WARN even when the drop would fit inside the tolerance.

| situation | Outcome | status | `value` |
|---|---|---|---|
| no baseline, count > 0 | `ok` | PASS | measured count |
| no baseline, count == 0 | `broken` | WARN (advisory) | `0` |
| both records, drop beyond tolerance | `broken` | WARN (advisory) | measured count |
| both records, within tolerance | `ok` | PASS | measured count |
| interface in baseline, missing from the subject (message `v baseline <b> MAC, v subjektu chybi`) | `broken` | WARN (advisory) | `chybi` |

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
- **`link_local_is_configured(scope)`** from `migration_validator/addressing.py` (used by
  both `nd_present` and `probes/ping.py` — since AR-41 a single shared occurrence instead of
  the earlier duplication) — does the service have a link-local address
  configured directly under the interface? This checks **presence, not exclusivity**: one
  link-local address among the configured ones is enough, even alongside an ordinary
  routable address — either way it returns `True`. Link-local neighbours show up on every
  IPv6 interface and say nothing about the customer service by themselves, which is why they
  are otherwise filtered out. But some deployments have the service use link-local — then
  those neighbours are exactly what the service talks to, and the filter must let them
  through. Configuration decides, not a heuristic.

### `arp_present` (state, critical)

- **no IPv4 address configured** (`scope.selectors.local_ipv4` empty) → **no `Finding` at all**
  — a service without IPv4 should not get an ARP finding, let alone a WARN for a neighbour that
  could never have existed. It used to return a `SKIP` stamped `family=4`, but that stamp was
  exactly what forced a section for a family the renderer is supposed to omit (decision R-1);
- no ARP entry on the service's interfaces → `broken` → FAIL, message `na rozhranich
  sluzby neni zadny ARP zaznam`, `value` = `zadny zaznam`;
- a MAC of `00:00:00:00:00:00` (an unresolved ARP entry) → `broken` → FAIL, message `ARP zaznam
  <ip> neni resolved (incomplete)`, `value` = `incomplete -> <ip>`;
- otherwise **one `Finding` per ARP entry**: message `ARP zaznam <ip>`, `label="ARP"`,
  `family=4`, `value` = `<mac> -> <ip>` (`?` when the MAC is missing).

| situation | Outcome | status | `value` |
|---|---|---|---|
| no IPv4 address configured | *(no finding)* | — | — |
| IPv4 configured, no ARP entry with an `ip` at all | `broken` | FAIL | `zadny zaznam` |
| entry MAC is `00:00:00:00:00:00` (unresolved) | `broken` | FAIL | `incomplete -> <ip>` |
| entry resolved (any other MAC) | `ok` | PASS | `<mac or '?'> -> <ip>` |

### `nd_present` (state, critical)

Mirrors `arp_present` for IPv6:

- no IPv6 address configured → **no `Finding` at all** (see `arp_present` above);
- link-local neighbours are **dropped** unless the service itself has link-local as a
  configured address (`link_local_is_configured()`);
- no **usable** ND entry remains → `broken`, message `na rozhranich sluzby neni zadny
  pouzitelny ND zaznam` (note the extra word "pouzitelny" compared to ARP — precisely
  because of the filtered-out link-local neighbours), `value` = `zadny zaznam`;
- entry state `incomplete` or `unreachable` (an unresolved ND entry) → `broken` → FAIL, message `ND
  zaznam <ip> neni resolved (<state>)`, `value` = `<state> -> <ip>`;
- otherwise **one `Finding` per ND entry**: message `ND zaznam <ip>`, `label="ND"`,
  `family=6`, `value` = `<mac> -> <ip>`; `subject` additionally carries `state` (ND, unlike
  ARP, has an entry state).

| situation | Outcome | status | `value` |
|---|---|---|---|
| no IPv6 address configured | *(no finding)* | — | — |
| IPv6 configured, no usable entry (all filtered or empty) | `broken` | FAIL | `zadny zaznam` |
| entry state `incomplete` or `unreachable` (unresolved) | `broken` | FAIL | `<state> -> <ip>` |
| entry resolved (any other state) | `ok` | PASS | `<mac or '?'> -> <ip>` |

### `ping_reachability` (state, advisory)

Reads finished results from the snapshot — the targets were resolved back during `capture`
(ARP/ND → ping, `probes/ping.py`).

- a local subnet above the P2P threshold (IPv4 wider than `/30`, IPv6 wider than `/126`)
  that no target landed in → a per-subnet `SKIP` (`<net>: zadny cil - subnet vetsi nez /30,
  fallback by cil jen hadal`, `value` `<net>  bez cile (subnet > /30)`) — the resolver
  deliberately keeps the fallback out of it (see `IPV4_FALLBACK_MIN_PREFIX` in
  `probes/ping.py`), and silence would read as "checked, OK";
- `ping_skipped` set (the profile excluded this scope from `capture`) → `SKIP`, message
  `ping neproveden - mimo profil (<profile>)`, `value` = `mimo profil (<profile>)` — naming
  the profile so the operator does not have to go dig it out of `run.yml`;
- the snapshot has no targets for this scope and no such subnet explains it → `SKIP`
  (`pro tento scope nejsou ve snapshotu zadne cile pingu`);
- a probe with no recognised family (`family` outside `4`/`6`) → its own `SKIP` naming the
  targets (`probe bez rodiny nelze vyhodnotit: ...`) — otherwise the probe would silently
  vanish from the result instead of saying it was never evaluated;
- otherwise **one `Finding` per target**, grouped by `family`:
  - `sent == 0` → `SKIP`, message `<target>: ping neodeslan`, `value` = `<target> neodeslan`
    — nothing sent is not the same claim as "sent and no answer";
  - at least one packet answered → `ok`, message `<target>: odpovedelo N z M`,
  - `sent > 0` and none answered → `broken`, message `<target>: neodpovedel (M paketu)`.

`value` is `<received>/<sent>` plus `  <rtt> ms` on a successful reply (omitted when the RTT
could not be measured), then always `  <target>` — e.g. `5/5  2.1 ms  10.1.1.1`. On failure
it is `  <target> neodpovedel`. `details` carries `resolved_from` (`arp` | `nd` |
`subnet-fallback`) and `address` (`owning_prefix()`), so the result shows which configured
range the target belongs to and whether it came from ARP/ND or was derived from the subnet.

| situation | Outcome | status | `value` |
|---|---|---|---|
| `ping_skipped` set, profile known | `SKIP` | SKIP | `mimo profil (<profile>)` |
| `ping_skipped` set, profile unknown | `SKIP` | SKIP | `mimo profil` |
| no probes and no oversized subnet | `SKIP` | SKIP | `bez cile` |
| local subnet above the P2P threshold with no target in it | `SKIP` | SKIP | `<net>  bez cile (subnet > /<threshold>)` |
| probe with `sent == 0` | `SKIP` | SKIP | `<target> neodeslan` |
| probe with `received > 0` | `ok` | PASS | `<received>/<sent>[  <rtt> ms]  <target>` |
| probe with `sent > 0` and `received == 0` | `broken` | WARN (advisory) | `<received>/<sent>  <target> neodpovedel` |
| probe with an unrecognised `family` | `SKIP` | SKIP | `bez rodiny` |

---

## `routes.py` — static and aggregate routes

### `static_route_status` (both, critical)

The only check that **compares configured intent against measured reality**. Every other
check asks "is it up?"; this one asks "is what you ordered actually there?".

**It only reads records with `protocol == "static"` (facts) / `route_type == "static"`
(intent).** Since the 2026-08-19 QNH wave an aggregate route has its own check
(`aggregate_route_status` below) — this one filters aggregates out of both the configuration
and the facts up front, so the two comparison mechanisms (next-hop for a static, presence-only
for an aggregate) never mix inside one loop. A missing `protocol`/`route_type` key means a
record from before schema 10/6, when only statics were collected/parsed — the default there
is "static", not an error. That only protects the check's internals (hand-built facts in
tests, calling the check directly without going through `Snapshot.from_dict`) — a real old
baseline file still hits `SnapshotVersionError` (`models/snapshot.py`, `schema_version !=
SCHEMA_VERSION`) before it ever reaches this check, so it still needs a fresh capture.

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
"one route vanished and another appeared". The identity stays a plain pair even after
per-hop next hops (QNH): a partial deactivation (some next hops off, others not) lives
inside a single record's `next_hops`, not as a second identity — the parser
(`_static_routes_under`) emits one `StaticRoute` per `(rib, prefix)` regardless of how many
next hops a route has or how many of them are active.

**Under ECMP the value is the whole *set* of next hops, not their order in the XML.** That is
why `_next_hop_text()` is `", ".join(sorted(...))`: Junos guarantees no `<nh>` ordering across
platforms or releases — and this check spans exactly that boundary (`junos` → `junos-evo`).
Without the sort, two snapshots reporting the same next hops in a different order would produce
a spurious `WARN … next-hop se zmenil A, B -> B, A`, because the `ZMENA` branch compares the
values as strings. A sorted rendering is deterministic on top of that.

The sort applies to the **value only**. The raw evidence a finding carries into the JSON output
(`subject` / `baseline`) deliberately stays in XML order — evidence should be verbatim. A JSON
consumer may therefore see a sorted `value` alongside an unsorted `next_hop` in the evidence;
that is not an inconsistency but two different roles.

| situation | Outcome | status | `value` |
|---|---|---|---|
| in the table, next hop unchanged or no baseline | `ok` | PASS | next hops sorted and joined by commas (`-` when none) |
| in the table, next hop changed vs. baseline | `degraded` | WARN | new next hop; `ZMENA` carries the old one |
| in the table, baseline had it inactive (route reactivated) | `recovered` | RECV | new next hop (message adds `(v baseline nebyla aktivni)`) |
| in the table but unstarred (`active: false`), baseline also inactive | `ok` | PASS | `neni aktivni` |
| in the table but unstarred, baseline was active | `broken` | FAIL | `neni aktivni` |
| in the table but unstarred, baseline record present but silent on activity (message adds `; baseline aktivitu neuvadi`, `baseline_value` stays `None`, not fabricated) | `degraded` | WARN | `neni aktivni` |
| in the table but unstarred, no baseline record at all | `degraded` | WARN | `neni aktivni` |
| absent from the table, no matching baseline measurement (message `nakonfigurovana, ale neni v routovaci tabulce`) — applies in device scope too | `broken` | FAIL | `neni v tabulce` |
| absent from the table, present in baseline measurement | `broken` | FAIL | `chybi` |

A route that is **in the table but unstarred** is not forwarding — Junos does not drop it
from the listing, another source has just outranked it. That is a different situation from
`neni v tabulce` (which drops the route from the listing entirely): hence the separate value
`neni aktivni`, not the same one used for a missing route. Without a baseline there is no way
to tell whether it was already inactive before, so per R‑2 the ambiguity is not escalated to
FAIL — it stays at WARN.

What separates the last two rows is **whether the baseline measured the route**, not the
scope kind: a baseline record present means "it was there, now it is gone" (`chybi`); no
baseline record at all means only the configuration claims the route belongs in the table,
and the table itself says it does not (`neni v tabulce`) — this holds in device scope too,
since it is a statement about what the table shows, not about intent the device scope cannot
see (AR‑17).

The label is `<RIB> <prefix>` and the identity goes into `group="Staticke routy"` — routes
from different RIBs end up in one named row group instead of separate sub-rows distinguished
only by a qualifier in the label. `family` is derived **from the prefix**, not from the RIB
name, because the name need not carry a family at all (`bgp.l3vpn.0`).

**Recorded assumption:** RIB names survive the migration. `_aligned_baseline_data` in the
engine re-keys only the `interfaces` area between baseline and subject; `routes` are keyed
`table -> prefix` and get no re-keying. Should a future migration rename a VRF, every route
in it would read as "missing" plus "new".

#### Annotating a deactivated next hop

The configuration can leave a route active as a whole while deactivating just **one** of its
next hops (a `qualified-next-hop` can be deactivated individually — see
[parsers.md](parsers.md#static-routes-per-hop-next-hops-and-aggregates-qnh-schema-6)). Such a
row must not read as healthy just because the route otherwise landed in the table with
activity — a deactivated piece of configuration is itself a finding (a user decision from
2026-08-04, shared with `checks/deactivation.py`).

`_annotate_inactive_hops()` runs over every row that reached the active table (both the
`ZMENA` branch and the ordinary OK/WARN/FAIL branches shared with `aggregate_route_status`
via `_presence_finding`), and for every deactivated next hop calls the shared
`deactivation_outcome()` — the same function `deactivation_state` uses for a whole service.
The worst outcome across hops wins (`BROKEN > DEGRADED > original`); the message gains
`; deaktivovany next-hop: <to[ via <interface>]>` for every off hop and one summary note —
`- migrace nedokoncena` when the outcome is `BROKEN` or a hop was still active in the baseline
(newly deactivated), otherwise `(stejne jako v baseline)` when it was already off there.

**No active next hop means the intent is not to forward, not a discrepancy.** When **every**
next hop of a route is deactivated and the route is simply absent from the table, that is an
informational state with the same semantics as a fully deactivated route
(`deactivation_outcome`), not a `BROKEN … neni v tabulce`: the operator deliberately turned
off the next hops, so absence from the table is expected, not a conflict. The message reads
`<rib> <prefix>: vsechny next-hopy jsou deaktivovane`, with `- migrace nedokoncena` appended
when the route was still alive in the baseline. This branch is tested **before** the ordinary
`neni v tabulce` branch — the other order would let the more general branch catch it first and
manufacture a FAIL for next hops nobody wanted active.

---

### `aggregate_route_status` (both, critical)

An aggregate route (`route_type == "aggregate"` / `protocol == "aggregate"`) **has no next
hop** — in the configuration it carries `discard`/`reject`, not a next hop, so it is compared
against the table only on presence and activity, not on a next hop like a static (see
[parsers.md](parsers.md#static-routes-per-hop-next-hops-and-aggregates-qnh-schema-6)). It
shares with `static_route_status` the union of three sources and the "missing"/"deactivated"
branches (both via the common `_presence_finding()`); it does not share the next-hop
comparison or the hop-level annotation — an aggregate has no next hop, so `was` and `now`
always come out equal and the `ZMENA` branch would never trigger.

A customer aggregate that vanishes after the migration is a signal of an outage — hence
`CRITICAL`, same as for statics, not `advisory`.

The label is `<RIB> <prefix>`, same as for a static, but the identity goes into its own group,
`group="Agregatni routy"` — aggregates do not get mixed into the same group as statics in the
report, even though both are still "a route in the table".

| situation | Outcome | status | `value` |
|---|---|---|---|
| in the table and active, baseline unchanged or absent | `ok` | PASS | `v tabulce` |
| in the table and active, baseline had it inactive (route reactivated) | `recovered` | RECV | `v tabulce` (message adds `(v baseline nebyla aktivni)`) |
| in the table but unstarred (`active: false`), baseline also inactive | `ok` | PASS | `neni aktivni` |
| in the table but unstarred, baseline was active | `broken` | FAIL | `neni aktivni` |
| in the table but unstarred, baseline record present but silent on activity (message adds `; baseline aktivitu neuvadi`, `baseline_value` stays `None`, not fabricated) | `degraded` | WARN | `neni aktivni` |
| in the table but unstarred, no baseline record at all | `degraded` | WARN | `neni aktivni` |
| absent from the table, no matching baseline measurement (message `nakonfigurovana, ale neni v routovaci tabulce`) — applies in device scope too | `broken` | FAIL | `neni v tabulce` |
| absent from the table, present in baseline measurement | `broken` | FAIL | `chybi` |
| configured, deactivated in the configuration, and absent from the table | `deactivation_outcome(...)` | depends on baseline | `deaktivovana` |

---

## `bfd.py` — BFD sessions

### `bfd_session_state` (both, critical)

Like `static_route_status`, it **iterates over the union of three sources** (AR‑14): the
intent from the configuration (`Selectors.bfd_peers`), the session in the subject and the
session in the baseline. **A peer that never had BFD gets no row** (decision R‑1) — so a
service without BFD carries no mention of BFD in the report at all.

The check requires **two areas**: `("bfd", "bgp")`.

**This check does not run on any Core scope at all** — `applies_to()` is overridden and
returns `False` for `scope.service_type == "Core"` before ever calling
`super().applies_to()`, regardless of `service_subtype`. Transit BFD is measured by the new
`core_protocols.bfd_transit_state` (2026-08-26 wave), which matches sessions by **interface**,
not by peer address from intent configuration — transit has no such intent. Without this gate
`bfd_session_state` would keep running and print `WARN | parser nenasel konfiguraci` for every transit
session, since it would never find a matching `Selectors.bfd_peers` entry.

The loopback scope (`service_subtype == "loopback"`) was added to the gate **during the
branch's final review** (2026-08-26): as of this wave `Scope.select` also assigns internal
(iBGP) peers on lo0.0 into that scope's `bgp_neighbors`, so without the gate that session
would get the same false `WARN | parser nenasel konfiguraci` — the intent exists in the configuration,
but `Selectors.bfd_peers` never parses it. Testing iBGP BFD on the loopback is a deliberately
deferred decision, not a gap; such a session stays visible in NEZAŘAZENO
(`engine.py:_unassigned_bfd_sessions` deliberately leaves it there) until it gets its own
check.

| situation | Outcome | status | `value` |
|---|---|---|---|
| session exists, state `Up` | `ok` | PASS | `Up` |
| session exists, any other state (message states the expectation: `<state>, ocekavano Up`) | `broken` | FAIL | measured state (`Down`, …) |
| session exists but is not in the service configuration (service scope) | `degraded` | WARN | `parser nenasel konfiguraci` |
| no session and no intent, but the baseline had one (service scope) | `broken` | FAIL | `v baseline patril k teto sluzbe, v subjektu uz ne` |
| no session, but the baseline had one (device scope) — **currently unreachable, see below** | `broken` | FAIL | `session zmizela` |
| intent present, no session, **BGP not `Established`** | `SKIP` | SKIP | `BGP neni Established` |
| intent present, BGP running, still no session | `broken` | FAIL | `bez session` |

**The dependency on BGP state is deliberate, not cosmetic.** BFD cannot come up while BGP is
down, so without it a service with a dropped BGP session would collect two FAIL rows for one
cause. In a live run that would repeat for every service that has not yet come up, and the
operator would learn to skim past the listing.

**`is_device` is inert here** — just like the identical-looking conjunct in `routes.py`. Only a
peer with no session in the subject reaches the "no intent either" branch, and such a peer can
only enter the union from the baseline. But a device scope never pairs: `device_scope()` has
`key=None`, and every key function in `scoping/matcher.py` returns an empty list for `None`, so
the scope ends up in `unmatched_subject` and the engine hands it no baseline at all
(`_run_scope(..., None, None, ...)`). `baseline_sessions` is therefore always empty in a device
scope, and the `session zmizela` row cannot currently be printed.

The code stays regardless. Should a future snapshot format give a device scope a baseline, then
without this distinction the tool would claim, for every vanished session, that it "is not
configured in the subject" — about a configuration it cannot see in that mode at all (AR‑17).
The difference is in the **value**, not merely in the message: the value column is what ships in
the report (F‑7/AR‑4), while the message never appears in the text output. It is the same
pattern as `MISSING_FROM_TABLE` vs `MISSING_ENTIRELY` in `routes.py`.

**The "no session and no intent either" branch (service scope) asserts only MEMBERSHIP in the
service, not existence on the device.** A peer this service no longer claims can still have a
live BFD session on the device under a different service — `engine.py:_unassigned_bfd_sessions`
would surface it in NEZAŘAZENO. The message "is not configured in the subject" would lie there;
`parser nenasel konfiguraci` is a distinct value from this branch, so there is no risk of confusing the
two. The wording `v baseline patril k teto sluzbe, v subjektu uz ne` is true in both cases that
reach this branch (BFD vanished from the device, or BFD moved under a different service) — the
same fix the analogous branch in `checks/bgp.py` received earlier.

---

## `core_protocols.py` — Core transit and lo0.0 protocols (2026-08-26 wave)

Seven new checks over six new collectors (`isis_adjacency`, `isis_interface`,
`isis_overview`, `ldp_neighbor`, `pim_neighbor`, `mpls_interface`). All of them share two
decisions:

- **A missing interface in an output is a measurement, not a hole.** The collector never
  synthesizes a "Down" row — if the interface is missing from the output, its key is simply
  absent from the facts. What that means is decided by the check, and it is almost always
  `FAIL | ... : chybi v outputu` (the same "missing from the table" vs. "missing
  entirely" distinction `static_route_status` makes in `routes.py` via
  `MISSING_FROM_TABLE`/`MISSING_ENTIRELY`).
- **The measured units come from the selector (intent), not from the facts** —
  `_scope_transit_interfaces` returns transit interfaces from `Scope.selectors.interfaces`,
  not keys from the subject. An interface that vanished entirely from an output still gets a
  row (FAIL "missing from output") instead of going silent.

### `isis_adjacency_state` (transit, critical)

`service_types={"Core"}`, `service_subtypes={"transit"}`. Requires `isis_adjacency`.

Interface missing from the adjacency output → a single row `FAIL | IS-IS adjacency state :
chybi v outputu` (`baseline_value` is the state from that same baseline row, if any — it
speaks in the language of that row's presence, not the next-hop's).

Otherwise four rows:

| field | without baseline | against baseline |
|---|---|---|
| `system-name` (neighbor) | INFO | PASS on match; WARN on mismatch |
| `adjacency-state` | PASS if `Up`, else FAIL | PASS when it matches a baseline state of `Up`; **RECV** (`recovered`) when it is `Up` now but was not `Up` in the baseline (message adds `(v baseline <state>)`); else FAIL |
| `ip-address` (IPv4 neighbor) | PASS if present, else FAIL | PASS on match; WARN on mismatch |
| `global-ipv6-address` (IPv6 neighbor) | PASS if present, else FAIL | PASS on match; WARN on mismatch |

Mutant kill (2026-09-03, verified by running it): flipping `Outcome.RECOVERED` →
`Outcome.OK` in the "Up now / not Up in baseline" branch makes
`test_baseline_state_down_before_up_now_is_recovered` fail.

| situation | Outcome | status | `value` |
|---|---|---|---|
| interface missing from the adjacency output | `broken` | FAIL | `chybi v outputu` |
| neighbor (`system-name`) row, baseline present and name differs | `degraded` | WARN | measured name (or `chybi v outputu` if `None`) |
| neighbor row, baseline present and name matches | `ok` | PASS | measured name |
| neighbor row, no baseline (or no baseline row for the interface) | `info` | INFO | measured name |
| `adjacency-state` row, state ≠ `Up` | `broken` | FAIL | measured state |
| `adjacency-state` row, state `Up` now, baseline state present and ≠ `Up` | `recovered` | RECV | `Up` |
| `adjacency-state` row, state `Up` now, baseline `Up` or absent | `ok` | PASS | `Up` |
| IPv4/IPv6 neighbor address row, baseline present and address differs | `degraded` | WARN | measured address (or `chybi v outputu` if `None`) |
| address row, unchanged (or no baseline), address present | `ok` | PASS | measured address |
| address row, unchanged (or no baseline), address `None` | `broken` | FAIL | `chybi v outputu` |

### `isis_interface_info` (transit + loopback, critical)

`service_types={"Core"}`, `service_subtypes={"transit", "loopback"}` — **one check for both
roles**, behavior branches on `ctx.scope.service_subtype`. Requires `isis_interface`.

- Interface missing from the ISIS interface output → `FAIL | IS-IS interface : chybi
  v outputu`.
- Level 2 present in `levels` → PASS `nakonfigurovan`; missing → FAIL `chybi v outputu`, and
  **the passive row is not emitted at all** in that case (for either role) — without an
  adjacency the passive flag measures nothing, so one FAIL replaces what used to be two rows
  for the same cause on loopback. Level 1 present → **its own FAIL row** (level 1 has no
  business on a Core interface), value `nakonfigurovan`.
- The passive flag on level 2 is **role-aware**: loopback requires it (`ok = passive`),
  transit requires its absence (`ok = not passive`) — a passive transit port would never
  form the adjacency that `isis_adjacency_state` measures.

Mutant kill (2026-08-26, verified by running it): `ok = passive if loopback else not
passive` → `ok = passive` makes both `test_transit_non_passive_level2_is_pass` and
`test_transit_passive_level2_is_fail` fail (plus the end-to-end regression).

| situation | Outcome | status | `value` |
|---|---|---|---|
| interface missing from the ISIS interface output | `broken` | FAIL | `chybi v outputu` |
| level 2 present in `levels` | `ok` | PASS | `nakonfigurovan` |
| level 2 missing from `levels` (no passive row emitted for this interface) | `broken` | FAIL | `chybi v outputu` |
| level 1 present in `levels` | `broken` | FAIL | `nakonfigurovan` |
| level 2 present, passive flag matches the role (loopback: passive; transit: not passive) | `ok` | PASS | `Passive` (loopback) / `bez Passive` (transit) |
| level 2 present, passive flag mismatches the role | `broken` | FAIL | `bez Passive` (loopback) / `Passive` (transit) |

### `ldp_neighbor_state` (transit, critical) — always expected

`service_types={"Core"}`, `service_subtypes={"transit"}`. Requires `ldp_neighbor`. **No gate
on intent** — LDP on a transit Core interface is always expected (2026-08-26 decision), so a
missing neighbor is a straight FAIL, never quiet nothing.

| situation | Outcome | status | `value` |
|---|---|---|---|
| neighbor missing from output | `broken` | FAIL | `Down` |
| `uptime_seconds > 0` | `ok` | PASS | `Up for <uptime>` |
| `uptime_seconds` missing or `0` | `broken` | FAIL | `Down` |
| neighbor address is `None`, unchanged vs. baseline (or no baseline) | `broken` | FAIL | `chybi v outputu` (message `adresa souseda chybi`) |
| neighbor address present, unchanged vs. baseline (or no baseline) | `info` | INFO | address |
| neighbor address changed vs. baseline, new address present | `degraded` | WARN | address |
| neighbor address changed vs. baseline, new address is `None` | `degraded` | WARN | `chybi v outputu` |

The collector drops `lo0.*` records for LDP already at parse time (LDP on the loopback has
no meaning for this check) — see `collectors.md`.

### `pim_neighbor_state` (transit, critical) — gated on intent

`service_types={"Core"}`, `service_subtypes={"transit"}`. Requires `pim_neighbor`.

**Without intent (`"pim"` not in `ctx.scope.selectors.protocols`) the check returns an empty
finding list — silence, not SKIP** (2026-08-26 decision): a service without PIM is not less
healthy, so it should get no row at all, let alone a SKIP that would read as "unmeasured" in
the summary. Intent is written to inventory by the parser from `protocols pim interface
<name>` (globally and per routing-instance) into the existing `ServiceEntry.protocol` field.

With intent it behaves the same as `ldp_neighbor_state` (shared `_neighbor_findings`
skeleton): a missing neighbor is FAIL `Down`, otherwise PASS/FAIL by `uptime_seconds`,
address INFO/WARN.

Mutant kill (2026-08-26, verified by running it): deleting the `if "pim" not in ...` gate
makes `test_pim_neighbor_without_intent_is_silent_not_skip` fail (plus the end-to-end
regression).

| situation | Outcome | status | `value` |
|---|---|---|---|
| `"pim"` not in `scope.selectors.protocols` | *(no finding)* | — | — |
| neighbor missing from output | `broken` | FAIL | `Down` |
| `uptime_seconds > 0` | `ok` | PASS | `Up for <uptime>` |
| `uptime_seconds` missing or `0` | `broken` | FAIL | `Down` |
| neighbor address is `None`, unchanged vs. baseline (or no baseline) | `broken` | FAIL | `chybi v outputu` |
| neighbor address present, unchanged vs. baseline (or no baseline) | `info` | INFO | address |
| neighbor address changed vs. baseline, new address present | `degraded` | WARN | address |
| neighbor address changed vs. baseline, new address is `None` | `degraded` | WARN | `chybi v outputu` |

### `mpls_interface_state` (transit, critical)

`service_types={"Core"}`, `service_subtypes={"transit"}`. Requires `mpls_interface`.

PASS if state is `Up`, FAIL if `Dn` or anything else, FAIL `chybi v outputu` on absence.
**With a baseline, `Up` now and the baseline state was something else (not `Up`) is `RECV`**
(`recovered`), not a silent PASS — message adds `(v baseline <state>)`; `Up` now matching a
baseline of `Up`, or no baseline at all, stays `ok`.

| situation | Outcome | status | `value` |
|---|---|---|---|
| interface missing from the MPLS output | `broken` | FAIL | `chybi v outputu` |
| state `Up`, baseline state was something else (not `Up`) | `recovered` | RECV | `Up` |
| state `Up`, baseline `Up` or no baseline | `ok` | PASS | `Up` |
| state anything else (e.g. `Dn`, `unknown`) | `broken` | FAIL | `Down` |

### `bfd_transit_state` (transit, critical) — always expected

`service_types={"Core"}`, `service_subtypes={"transit"}`. Requires `bfd`. **A separate check
next to `bfd.py`**, so the intent-based customer logic of `bfd_session_state` stays untouched
— and that check does not run on any Core scope at all anyway (see the gate in the `bfd.py`
section above).

Sessions are matched by **interface**, not by peer address — `by_interface` is built from
`data.get("interface")` on every BFD session in the subject.

| situation | Outcome | status | `value` |
|---|---|---|---|
| no session on the interface | `broken` | FAIL | `Down` |
| session exists, state `Up`, baseline for that peer was also `Up` or absent | `ok` | PASS | `Up` (raw state, same vocabulary as `bfd.py:113`) |
| session exists, state `Up`, baseline for that peer was something else | `recovered` (message adds `(v baseline <state>)`) | RECV | `Up` |
| session exists, other state | `broken` | FAIL | the measured state |

Multiple sessions on the same interface get multiple rows (sorted by peer).

Mutant kill (2026-08-26, verified by running it): the variant actually run was `entries =
by_interface.get(name) or []` (a literal deletion of the `if not entries` branch would crash
with `TypeError` in `sorted(None)`, see the roadmap) — it makes
`test_bfd_transit_missing_session_is_fail_down` fail.

### `isis_overview` (loopback, advisory)

`service_types={"Core"}`, `service_subtypes={"loopback"}`. Requires `isis_overview` — a
device-global fact that `Scope.select()` only lets through for Core loopback scopes (see
`models.md`), so on transit `ctx.subject["isis_overview"]` is always an empty dict.
Scoping (`Scope.select()`) and the check's binding (`service_subtypes`) are two
**independent** safety nets against the same mistake: if scoping let the area through on
transit too, the empty dict would always assert a false `PASS | nenastaven` there (the
overload bit reading as never set, because the area is empty) — if only the check's
binding failed, the subtype gate in `Scope.select()` would still keep the area off
transit.

A single row: the `isis_overview` area empty entirely (collector reported nothing, distinct
from a measured "not set") → `WARN | IS-IS overload bit : bez dat` (message `chybi data z
collectoru isis_overview`); `overload_enabled` → `WARN | IS-IS overload bit : nastaven` (the
router avoids transit traffic), else `PASS | IS-IS overload bit : nenastaven`. The baseline
adds nothing (`mode = STATE`).

| situation | Outcome | status | `value` |
|---|---|---|---|
| `isis_overview` area empty entirely (collector reported nothing) | `degraded` | WARN | `bez dat` |
| `overload_enabled` truthy | `degraded` | WARN | `nastaven` |
| `overload_enabled` falsy (with data present) | `ok` | PASS | `nenastaven` |

Mutant kill (2026-08-26, verified by running it): deleting the `if self.service_subtype ==
"loopback"` condition in `Scope.select()` (for `isis_overview`) makes
`test_isis_overview_goes_only_to_loopback_scope` (`tests/models/test_scope_core.py`) fail —
the transit scope would receive the overview fact too.

---

## `multicast.py` — IGMP, multicast forwarding, MVPN c-multicast (2026-09-02 wave)

Four new checks over three new collectors (`igmp_group`, `multicast_route`,
`mvpn_instance`). They cover three roles: **Internet/multicast** (a customer receiver
under `protocols igmp`), **Core/loopback** (global `inet.2` statics on lo0.0) and
**IPVPN/mvpn-igmp** (an IRB in an MVPN VRF with an IGMP receiver). They share the module,
two shared helper sections (`igmp_pairs`, `multicast_table`, `stream_rows`) and one rule
across all of them:

- **A missing IGMP set cascades into SKIP.** `igmp_membership_report` defines the expected
  streams; `multicast_forwarding_status` and `mvpn_cmulticast_status` without it return a
  single `SKIP` row, not an independent lookup into the table.
- **Against baseline, only the (S,G) set and the tunnel's sender PE are compared** —
  upstream, downstream, forwarding rate and route uptime are never compared, because by
  definition they change with the migration (different interfaces, different lsi.X
  numbers).
- **Absence of `forwarding_rate_pps` is not zero.** junos-evo often returns
  `<multicast-statistics-timed-out/>` even on a live Forwarding route — `stream_rows()`
  on `None` returns `SKIP | ... : statistics unavailable`, not `BROKEN` with an invented
  zero.
- **Per-stream group header.** The rows for each (S,G) sit under their own group header
  `   -- (S, G)`, so `Forwarding-rate`/`Route uptime` are unambiguous with more than one
  stream.

### `igmp_membership_report` (Internet/multicast, IPVPN/mvpn-igmp, both, critical)

`service_types={"Internet", "IPVPN"}`, `service_subtypes={"multicast", "mvpn-igmp"}`.
Requires `igmp_group`.

Groups on the service interface from the scope, sorted, deduplicated; ASM entries
(no source) render as `(*, G)`. No groups → `BROKEN | IGMP membership report : Receiver
neposila zadny IGMP membership report`. Against baseline: same set → `OK`; a different
set → `DEGRADED`, `value` is the current set, `baseline_value` the old one; baseline with
no groups → no comparison (no-baseline rule — `baseline_value` is `None`, not "it was
empty").

Mutant kill (2026-09-03, verified by running it): `Outcome.DEGRADED` → `Outcome.OK` in the
"set differs" branch makes `test_igmp_report_changed_set_is_warn` fail.

| situation | Outcome | status | `value` |
|---|---|---|---|
| no groups on the service's interfaces | `broken` | FAIL | `Receiver neposila zadny IGMP membership report` |
| baseline had groups, current set differs | `degraded` | WARN | current `(S, G)` set, joined |
| current set matches baseline (or no baseline, or baseline had none) | `ok` | PASS | current `(S, G)` set, joined |

### `multicast_forwarding_status` (Internet/multicast, IPVPN/mvpn-igmp, state, critical)

Same two subtypes. Requires `igmp_group`, `multicast_route`.

No IGMP groups → a single `SKIP | Multicast forwarding status : bez IGMP reportu`, no
stream rows at all. Otherwise a summary row (`OK` `{n} S,G`, or `BROKEN`
`{k}/{n} S,G nefunguje`, counted from whether the S,G is missing from the table or its
Stream/Upstream row fails) and for each pair:

- **Stream** — `OK` if the service interface is in the route's `downstream_interfaces`;
  otherwise `BROKEN`.
- **Upstream interface** — role-aware prefix: Internet/multicast `ge-`/`xe-`/`et-`/`ae`,
  IPVPN/mvpn-igmp `lsi.`/`vt-` (`_upstream_ok`). Match → `OK` with the name; otherwise
  `BROKEN`, value the found name or `-`. **The message distinguishes the two failure
  reasons**: an empty upstream is `<sg>: upstream - S,G je v tabulce ale nema upstream
  interface`, an upstream present but with the wrong prefix is `<sg>: upstream <up> neni z
  ocekavane role (ocekavano <prefixes>)` — the earlier wording claimed "no upstream
  interface" even when one existed with the wrong role.
- **Forwarding-rate**, **Route uptime** — `stream_rows()`, see above.

If the S,G is missing from the table entirely, only `BROKEN | Stream : S,G neni v
multicast tabulce` is emitted, no further stream rows. No baseline comparison at all
(`mode = STATE`).

Mutant kill (2026-09-03, verified by running each):
- `_upstream_ok`: always return `True` → fails `test_forwarding_internet_upstream_must_be_transit`
  and `test_forwarding_mvpn_upstream_must_be_lsi_or_vt` (and, as a bonus,
  `test_forwarding_missing_upstream_renders_dash`).
- `iface in downstream` → `bool(downstream)` → fails
  `test_forwarding_downstream_without_service_interface_fails_stream_row`.
- `stream_rows`: `raw_pps is None` → `raw_pps == 0` (the rate SKIP branch) → fails
  `test_stream_rows_skip_when_rate_missing` (and the resulting `TypeError` in
  `int(None)` also takes down `test_forwarding_missing_rate_is_skip_not_failure` and
  `test_core_missing_rate_is_skip`).

| situation | Outcome | status | `value` |
|---|---|---|---|
| no IGMP groups on the scope | `SKIP` | SKIP | `bez IGMP reportu` |
| summary row: at least one (S,G) failed | `broken` | FAIL | `<failed>/<total> S,G nefunguje` |
| summary row: none failed | `ok` | PASS | `<total> S,G` |
| pair has no matching route in the table at all | `broken` | FAIL | `S,G neni v multicast tabulce` |
| per matched route, service interface in `downstream_interfaces` | `ok` | PASS | `Stream se na <iface> posila` |
| per matched route, service interface not in `downstream_interfaces` | `broken` | FAIL | `S,G je v tabulce ale stream se na <iface> neposila` |
| per matched route, upstream empty | `broken` | FAIL | upstream name or `-` |
| per matched route, upstream present with the wrong role prefix | `broken` | FAIL | upstream name |
| per matched route, upstream present with the expected role prefix | `ok` | PASS | upstream name |
| per matched route, `forwarding_rate_pps` is `None` | `SKIP` | SKIP | `statistiky nedostupne` |
| per matched route, `pps > 0` | `ok` | PASS | `<pps> pps` |
| per matched route, `pps <= 0` | `broken` | FAIL | `<pps> pps` |
| per matched route, route uptime (always) | `info` | INFO | formatted uptime or `-` |

### `core_multicast_forwarding` (Core/loopback, both, critical)

`service_types={"Core"}`, `service_subtypes={"loopback"}`. Requires `multicast_route`,
`routes`. Driven by the **global `inet.2` statics from `Selectors.static_routes`**, not
the IGMP set — Core lo0.0 has no IGMP intent at all.

A scope with no inet.2 statics → **no rows at all** (silence, not SKIP — an intent gate
just like `pim_neighbor_state`). Otherwise a summary row is inserted first: `OK | Multicast
forwarding status : {m} inet.2 prefixu se streamem` when every inet.2 prefix has at least one
stream, or `BROKEN | ... : {k} z {m} inet.2 prefixu bez streamu` otherwise — a servicing-level
count instead of the four empty per-prefix `SKIP` rows a prefix without a stream used to get.
Then, for each inet.2 prefix, `assign_sources()` assigns routes from the multicast table by
whether the source lies inside the prefix — with several covering prefixes, the longest wins,
and each route is counted only once.

- At least one route with a source inside the prefix → `OK | Multicast forwarding status
  : Existuje S,G pro {prefix}` (`DEGRADED` if the S,G set differs against baseline);
  otherwise `BROKEN | ... : Neexistuje S,G pro {prefix}` — no further per-prefix rows for a
  prefix without a stream, the summary row already carries that count.
- Per assigned (S,G): **Upstream interface** — `OK` if the upstream is one of the `via`
  values measured by the route collector for that inet.2 prefix
  (`routes["inet.2"][prefix]["via"]`, ECMP/qualified-next-hop give several `via`); if the
  inet.2 route is not in the table at all → `SKIP : routa neni v tabulce` (the `FAIL` for
  that cause belongs to `static_route_status`, not this check again). **Downstream
  interfaces** — `OK` with a comma-joined list if non-empty; otherwise `BROKEN : Zadne
  downstream interfacy`. **Forwarding rate packets**, **Route uptime** —
  `stream_rows()`.

Mutant kill (2026-09-03, verified by running each):
- `assign_sources`: sort by `prefixlen` ascending instead of descending → fails
  `test_assign_sources_longest_prefix_wins_and_each_route_once`.
- `upstream in vias` → `upstream.startswith(("ge-", "xe-", "et-", "ae"))` → fails
  `test_core_upstream_must_be_one_of_via` (an ECMP scenario with two `via`, only one of
  which matches).

| situation | Outcome | status | `value` |
|---|---|---|---|
| no inet.2 statics in the scope's selectors | *(no finding)* | — | — |
| summary row: at least one inet.2 prefix has no stream | `broken` | FAIL | `<k>/<m> bez streamu` |
| summary row: every inet.2 prefix has a stream | `ok` | PASS | `<m> inet.2 prefixu` |
| prefix with no assigned route at all | `broken` | FAIL | `Neexistuje S,G pro <prefix>` |
| prefix with assigned route(s), baseline S,G set differs | `degraded` | WARN | `Existuje S,G pro <prefix>` |
| prefix with assigned route(s), set unchanged (or no baseline) | `ok` | PASS | `Existuje S,G pro <prefix>` |
| per route, inet.2 route not in the table at all | `SKIP` | SKIP | `routa neni v tabulce` |
| per route, upstream not among the inet.2 route's `via` | `broken` | FAIL | upstream name or `-` |
| per route, upstream is one of the `via` values | `ok` | PASS | upstream name |
| per route, `downstream_interfaces` empty | `broken` | FAIL | `Zadne downstream interfacy` |
| per route, `downstream_interfaces` non-empty | `ok` | PASS | comma-joined list |
| per route, `forwarding_rate_pps` is `None` | `SKIP` | SKIP | `statistiky nedostupne` |
| per route, `pps > 0` | `ok` | PASS | `<pps> pps` |
| per route, `pps <= 0` | `broken` | FAIL | `<pps> pps` |
| per route, route uptime (always) | `info` | INFO | formatted uptime or `-` |

### `mvpn_cmulticast_status` (IPVPN/mvpn-igmp, both, critical)

`service_types={"IPVPN"}`, `service_subtypes={"mvpn-igmp"}`. Requires `igmp_group`,
`mvpn_instance`.

No IGMP groups → `SKIP | C-Multicast status : bez IGMP reportu`. The instance is missing
from the `mvpn_instance` listing → `BROKEN | ... : instance neni v mvpn vypisu`.
Otherwise, for each (S,G) pair:

- **C-Multicast status** — `OK` with `S/32:G/32`, if a c-multicast entry exists with a
  matching source and group prefix (`_cmulticast_entry`, ASM `(*, G)` compares group
  only); otherwise `BROKEN : chybi c-multicast zaznam`.
- **Provider tunnel** — `OK` with the full `provider_tunnel_id`, if `sender_pe` parsed;
  `BROKEN : bez provider tunelu` if it is empty or `I-P-tnl:invalid`. Against baseline:
  `DEGRADED` if **only the sender PE** differs (the first address after `P2MP:`) — the
  tunnel id changes on LSP re-signaling without a service change, so the full string is
  never compared.

Mutant kill (2026-09-03, verified by running it): `_tunnel_row`: replacing the
`was_pe != pe` comparison with `was_tunnel != tunnel` (the full string instead of the
sender PE) makes `test_mvpn_sender_pe_change_is_warn_but_tunnel_id_change_is_not` fail
(a re-signaled tunnel with the same PE address would get a false `DEGRADED`).

| situation | Outcome | status | `value` |
|---|---|---|---|
| no IGMP groups on the scope | `SKIP` | SKIP | `bez IGMP reportu` |
| instance missing from the `mvpn_instance` listing | `broken` | FAIL | `instance neni v mvpn vypisu` |
| pair has no matching c-multicast entry | `broken` | FAIL | `chybi c-multicast zaznam` |
| pair has a matching c-multicast entry | `ok` | PASS | `<source_prefix>:<group_prefix>` |
| tunnel row, `sender_pe` empty/falsy | `broken` | FAIL | tunnel id or `-` |
| tunnel row, `sender_pe` present, baseline sender PE differs | `degraded` | WARN | tunnel id |
| tunnel row, `sender_pe` present, baseline sender PE matches (or no baseline) | `ok` | PASS | tunnel id |

---

## `deactivation.py` — deactivation state

### `deactivation_state` (both, critical)

A deactivated service is never dropped from the inventory — it still has to be migrated, so
disappearing from the output would be a defect, not a fix (`models/scope.py`). Every other
check therefore SKIPs it (gate 5 of `run_check()` above), but something still has to say
*what* changed against the baseline — that is this check's job. It is the **one check that
does not pass through gate 5**: if it SKIPped like every other check, the service would
vanish from the report into an unexplained SKIP.

It has no `requires` (needs no collector) and no `service_types` restriction (applies to
every service type). It requires inventory (`requires_inventory = True`) — without it the
scope has no way to know whether it is deactivated.

**A healthy service (live in both subject and baseline) gets no row at all** — not SKIP, no
finding. Same R‑1 decision as elsewhere: what is not being checked does not appear in the
block, and a "service is active" row would be added to every healthy block and say nothing.

| situation | Outcome | status | `value` |
|---|---|---|---|
| subject and baseline both deactivated | `ok` | PASS | the subject's deactivation reason |
| subject deactivated, baseline was running | `broken` | FAIL | the subject's deactivation reason |
| subject running, baseline was deactivated | `recovered` | RECV | `aktivni` |
| subject deactivated, no baseline to compare | `SKIP` | SKIP | the subject's deactivation reason |
| subject and baseline both running | — | — (no finding) | — |

The deactivation reason (`Scope.deactivation_reason`) is one of three values:
`RI deactivated`, `interface deactivated`, `RI + interface deactivated` — depending on
whether the deactivated part is the routing instance, the interface, or both.

**Branch order matters:** `v baseline patril k teto sluzbe, v subjektu uz ne` is tested **before** `BGP neni Established`. The
reverse order would silently lose the case where the migration dropped protection that used
to be there *and* BGP had not come up — which is exactly the combination worth seeing.

The label is `BFD (<peer>)`, with `family` derived by `peer_family()` from the peer address
(shared with `checks/bgp.py`), so an IPv6 peer inherited from a BGP group lands in the
`IPv6` section.
