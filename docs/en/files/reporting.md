# `reporting/` — output

Files: `json_report.py`, `text_report.py` and an empty `__init__.py`.

This layer only **formats** — it evaluates nothing and computes nothing. The summary and each
scope's status were already computed by the engine, so the terminal and a future GUI cannot
drift apart on the numbers.

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

## `text_report.py`

### `filter_result(result, *, text=None, statuses=None)`

Returns a **copy** of the result (`dataclasses.replace`) with a filtered list of scopes:

- `text` — a case-insensitive substring of `scope_id` **or** `key["description"]`,
- `statuses` — a set of desired statuses.

**The `unmatched` section stays whole.** It is the main safeguard against overlooking an
unmigrated service, and a filter must not hide it. Asserted by
`tests/test_end_to_end.py::test_render_after_filter_still_shows_unmatched_section`: even when
the filter removes every scope, the section is still rendered.

### `render(result, *, detail=False)`

Assembles four parts:

1. **Header** — with a baseline, `Migrace: <address> (<phase>) -> <address> (<phase>)`;
   without one, `Validace: <address> (<phase>)`.
2. **Summary** — PASS/WARN/FAIL/SKIP counts and the paired / unpaired service counts.
3. **Service table** — columns `SLUZBA` (description, else scope id), `TYP` (type), `STAV`
   (status), `DETAIL`. The detail is **only the worst finding** (`_worst_message()`), empty on
   a green row.
4. **The `NESPAROVANO` section** — always, even when everything else is green (then `(nic)`,
   "nothing").

The symbols are plain text, not unicode: `OK `, `WARN`, `FAIL`, `SKIP`. (The specification drew
`✓ ⚠ ✗`; the code does not use them.)

### `--detail`

`_detail_lines()` prints **every** check of a service, ordered `FAIL → WARN → SKIP → PASS`,
then by id and label. Each line carries the status, the check id, the severity and the message.

It exists for a concrete reason: the summary line shows only the worst finding, so without the
detail there is no way to tell what else was checked — and above all whether `OK` means
"verified" or "the check never ran".

An example of real output:

```
L3VPN-CPE13-NNI                  IPVPN      WARN  198.11.13.2: stav se zmenil Idle -> Established
    WARN  bgp_session_state      critical  198.11.13.2: stav se zmenil Idle -> Established
    WARN  interface_traffic      advisory  et-0/0/8.113: provoz netece (in 0 pps, out 0 pps)
    OK    arp_present            advisory  nalezeno 1 ARP zaznamu: 198.11.13.2
    OK    bgp_prefix_counts      advisory  198.11.13.2: pocty prefixu v toleranci -10 %
    OK    interface_state        critical  et-0/0/8.113: up/up
    OK    ping_reachability      advisory  vsech 1 cilu odpovedelo
```

---

## Current state and known gaps

Reporting is the simplest layer of the tool so far, and `--detail` was the first step towards
making it denser. What it does **not** have today:

- no colours and no unicode symbols,
- `unassigned.bgp_peers` is **not printed** in the text output (it is present in JSON),
- `match.method` and `confidence` do not appear in the text — the `match` subcommand is for
  that,
- column widths are fixed and longer descriptions are truncated (`{:<32.32}`).
