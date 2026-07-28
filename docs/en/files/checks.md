# `checks/` — the evaluation logic

Files: `base.py`, `registry.py`, `all.py`, `ifaces.py`, `bgp.py`, `evpn.py`,
`reachability.py` and an empty `__init__.py`.

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
    mode: Mode                    # state | compare | both
    requires: tuple[str, ...]     # areas from facts/probes, e.g. ("bgp",)
    requires_inventory: bool
    service_types: frozenset[str] | None    # None = all
    default_severity: Severity

    def run(self, ctx) -> list[Finding]
```

`applies_to(scope)` returns `True` for the **device scope always** (there is nothing to filter
by) and otherwise compares `service_type`.

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

The difference between steps 1–2 (empty list) and 3–6 (`SKIP`) is deliberate: *"does not apply
here"* should not count towards the summary, *"not measured"* should.

## `registry.py`

`@register` stores an instance under `cls.id`; a duplicate raises `ValueError`. `all_checks()`
returns an alphabetically sorted list — which is why check ordering in the output is stable.
`checks_for(scope, config)` and `get_check(id)` are auxiliary queries (the engine uses
`all_checks()` and filters inside `run_check`).

## `all.py`

Imports `bgp`, `evpn`, `ifaces`, `reachability`. It is a separate module **because of a
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

### `interface_errors` (state, advisory)

The sum of `input_errors`, `output_errors` and `framing_errors` must be 0. Transit interfaces
only.

### `interface_traffic` (both, advisory)

- **without a baseline** (or when the interface is absent from the baseline): with
  `require_nonzero`, both `input_pps` and `output_pps` must be > 0, otherwise `broken` → WARN
  (`provoz netece` — "no traffic flowing");
- **with a baseline**: the percentage drop against `tolerance_percent` (default −60 %). The
  per-direction change goes into `details`, the raw numbers into `baseline`/`subject`.

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

- state ≠ `Established` → `broken` → **FAIL**,
- state `Established` but **different in the baseline** → `degraded` → **WARN** with the
  message `stav se zmenil X -> Established`. An improvement is still worth mentioning, but it
  is not a fault,
- state `Established` and unchanged (or no baseline) → PASS.

### `bgp_prefix_counts` (compare, advisory)

Compares `received` / `accepted` / `advertised` against `tolerance_percent` (default −10 %). A
peer absent from the baseline gets a `SKIP` — not a PASS.

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

Both checks have `requires_inventory = True` (so they `SKIP` on a device scope) and run only
on `Internet` and `IPVPN`. Both are knowingly **best-effort** — the CPE may be powered off or
block ICMP — hence the default severity `advisory`.

### `arp_present` (state, advisory)

There must be at least one ARP entry on the service's interfaces. The **entire list** of
learned addresses is stored in `subject`; on non-p2p subnets there may be several.

### `ping_reachability` (state, advisory)

Reads finished results from the snapshot — the targets were resolved back during `capture`
(ARP → ping).

| situation | outcome | status |
|---|---|---|
| every target responded | `ok` | PASS |
| some responded | `degraded` | **WARN** (even if the check were switched to `critical`) |
| none responded | `broken` | WARN (advisory) / FAIL (critical) |
| the snapshot has no targets | `skip` | SKIP |

`details` carries a **per-address** breakdown (`sent`, `received`, `loss_percent`,
`resolved_from`, `rtt_avg_ms`), so the result shows which address failed to answer and whether
it came from ARP or was derived from the subnet.
