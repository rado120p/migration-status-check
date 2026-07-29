# `reporting/` — output

Files: `json_report.py`, `view.py`, `text_report.py` and an empty `__init__.py`.

This layer only **formats** — it evaluates nothing and computes nothing. The summary and each
scope's status were already computed by the engine, so the terminal and a future GUI cannot
drift apart on the numbers.

`view.py` and `text_report.py` are deliberately separate: section order and a row's family
assignment can be tested by comparing data structures, while column widths have to be tested
against a string full of spaces. In one file, the logic would end up being tested through
spacing.

---

## `json_report.py`

Two functions over `RunResult.to_dict()`:

- `to_json(result, indent=2)` — a string with `ensure_ascii=False`, so Czech characters stay
  readable instead of collapsing into `í`;
- `write_json(result, path)` — writes UTF‑8 to a file, creates the parent directory and adds a
  trailing newline.

JSON is the **canonical result format**. The text report is a simplified view of it, not the
other way round.

---

## `view.py` — the data behind the report

Turns a `ScopeResult` into what the typesetting needs, without deciding a single space
character.

### `Row`, `Section`, `ServiceView`

- **`Row`** — one line of a block: `status`, `label`, `value`, `baseline_value`, `delta`,
  `mode`.
- **`Section`** — a group of rows for one family: `family` (`4` | `6` | `None`), `addresses`,
  `virtual_gw`, `rows`. `family=None` is for rows that do not depend on a family (interface
  state, error counters, traffic) — they sit right under the block header and have no section
  heading of their own, because a section heading carries an address and these rows carry
  none.
- **`ServiceView`** — everything one service's block needs: `status`, `description`,
  `service_type`, `routing_instance`, `baseline_interfaces`, `subject_interfaces`,
  `worst_message`, `sections`.

### `build_view(scope)`

Builds a `ServiceView` from a `ScopeResult`:

1. reads `scope.identity` (addresses, VGW, description, RI — see
   [models.md](models.md#resultpy--the-result-models));
2. for each family in `FAMILY_ORDER = (None, 4, 6)` it selects the checks with a matching
   `family` and assembles a `Section` — **an empty family gets no section at all**: a service
   with no IPv6 must not have an empty IPv6 section;
3. `_row(check, qualify)` turns a `CheckResult` into a `Row`; `qualify=True` when the family
   has more than one address — then rows tied to a specific address (ARP, ND, ping) carry
   that address in the label (`ARP (198.11.13.0/29)`). With a single address, the label is
   omitted, because the address is already in the section heading.

### `change_text(row, has_baseline)`

The content of the `ZMENA` (change) column:

- no baseline at all → `""` (the column is dropped entirely in `text_report.py`, see below),
- `row.mode == "state"` → `""` — state checks (`arp_present`, `nd_present`,
  `ping_reachability`, `interface_state`, ...) have no baseline value by definition, so
  without this rule `bez baseline` ("no baseline") would print on almost every row and nobody
  would read it,
- `baseline_value is None` → `NO_BASELINE` (`"bez baseline"`),
- `baseline_value == value` → `""` (unchanged),
- otherwise `f"bylo {baseline_value}   {delta}"`, or just `f"bylo {baseline_value}"` when
  there is no delta.

### `_worst_message(scope)`

The message of the worst finding by `_STATUS_ORDER = (FAIL, WARN, SKIP, PASS)`; returns `""`
for a `PASS` scope. This is the text shown in the `NALEZ` column of the summary table.

---

## `text_report.py` — typesetting

This layer receives finished data from `view.py` and only has to decide **how to line it up
into columns**.

### `filter_result(result, *, text=None, statuses=None)`

Returns a **copy** of the result (`dataclasses.replace`) with a filtered list of scopes:

- `text` — a case-insensitive substring of `scope_id` **or** `key["description"]`,
- `statuses` — a set of desired statuses.

**The `NESPAROVANO` section stays whole.** It is the main safeguard against overlooking an
unmigrated service, and a filter must not hide it.

### `SYMBOL`

The symbols are plain text, not unicode, and all four characters long: `PASS`, `WARN`,
`FAIL`, `SKIP`. (The specification drew `✓ ⚠ ✗`; the code does not use them.) `PASS` used to
be `"OK "` — that changed together with the report rewrite, so every symbol is the same
length and the `STAV` column does not need special handling.

### `render(result, *, detail=False)`

Assembles five parts:

1. **Header** — with a baseline, `Migrace: <address> (<phase>) -> <address> (<phase>)`;
   without one, `Validace: <address> (<phase>)`.
2. **Summary** — PASS/WARN/FAIL/SKIP counts and the paired / unpaired service counts.
3. **Summary table** — one row per service, columns `STAV`, `SLUZBA` (description, else scope
   id), `TYP`, `STARY PORT`, `NOVY PORT`, `RI`, `NALEZ` (the worst finding,
   `_worst_message()`; empty on a green row). Every column's width **is computed from the
   data**, never hard-coded — a fixed `SLUZBA`/`RI` width used to overflow on real lab names
   and knock the alignment out to the right.
4. **A block for every service that is not `PASS`** — printed automatically, no `--detail`
   needed. `--detail` additionally expands the blocks of `PASS` services too.
5. **The `NESPAROVANO` section** — always, even when everything else is green (then `(nic)`,
   "nothing"), and filters do not apply to it.

### One service's block (`_block`)

Rows inside a block run in `FAMILY_ORDER = (None, 4, 6)` order:

1. rows tied to the interface, not to an address (interface state, error counters, traffic) —
   with no section heading of their own;
2. the `IPv4` section, headed by its configured addresses and any `VGW` address;
3. the `IPv6` section, the same way.

Column widths **are computed from ALL rows of the block at once** (`label_width`,
`value_width`, `change_width`), not per section — otherwise the shared separator line, which
is drawn once across the whole block, would not line up. **Nothing is truncated**: a long RIB
name or IPv6 address is worse missing than cut off.

**The `ZMENA` (change) column is dropped entirely with no baseline loaded** — not just left
blank, the whole column and its header (`ZMENA PROTI <port>`) disappear from the output.
Without a baseline the value column's header differs too: `POST (<port>)` with a baseline,
`HODNOTA` ("value") without one.

The block header (`=====`, the service description, `RI: <instance>`) counts towards the
frame width too — a long service name or routing instance from the lab can be longer than the
column table, so the frame has to wrap that line as well, not just the table.

### An example of real output

Generated directly from `render()` over data shaped like
`tests/reporting/test_text_report.py` (`_dual_stack_scope()` + `_result()`, `detail=True`):

```
Migrace: 172.20.20.4 (pre-migration) -> 172.20.20.5 (post-migration)

  1 PASS   1 WARN   0 FAIL   0 SKIP
  Sparovano 1 sluzeb, 0 nesparovana v baseline, 0 nesparovane v subject

STAV  SLUZBA             TYP      STARY PORT  NOVY PORT   RI NALEZ
WARN  INTERNET-CPE13-NNI Internet ge-0/0/2.13 et-0/0/8.13 -  pokles

================================================================================================================
 WARN  INTERNET-CPE13-NNI   Internet   ge-0/0/2.13 -> et-0/0/8.13   RI: -
================================================================================================================
 STAV | CHECK                                : POST (et-0/0/8.13)                      | ZMENA PROTI ge-0/0/2.13
 -----+--------------------------------------+-----------------------------------------+------------------------
 PASS | Interface admin status (et-0/0/8.13) : Up                                      |
 WARN | Interface traffic in (et-0/0/8.13)   : 460 pps                                 | bylo 520 pps   -12 %

 -- IPv4  152.11.13.1/30 ---------------------------------------------------------------------------------------
 PASS | ARP                                  : 0c:00:ef:5e:df:01 -> 152.11.13.2        |

 -- IPv6  2001:abcd:11:13::a/127 -------------------------------------------------------------------------------
 PASS | ND                                   : 0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b |

NESPAROVANO
  (nic)
```

Without `--baseline` the `ZMENA` column disappears entirely (the value column's header
changes to `HODNOTA`) and the block header shows only `NOVY PORT` instead of `STARY -> NOVY`.
On an IRB interface, the section heading also gets a `VGW <address>` suffix after the
address — e.g. `-- IPv4  152.11.14.2/29   VGW 152.11.14.1`.

---

## Current state and known gaps

What the report does **not** have today:

- no colours and no unicode symbols,
- `unassigned.bgp_peers` is **not printed** in the text output (it is present in JSON),
- `match.method` and `confidence` do not appear in the text — the `match` subcommand is for
  that.
