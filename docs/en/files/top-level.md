# Package root — entry points and orchestration

Files: `pyproject.toml`, `.gitignore`, `migration_validator/__init__.py`, `api.py`, `cli.py`,
`capture.py`, `engine.py`, `config.py`.

These files measure and parse nothing — they **control the order in which the other layers
are called**.

---

## `pyproject.toml`

The package definition. The relevant bits:

| item | value |
|---|---|
| name / version | `migration-validator` 0.1.0 |
| Python | ≥ 3.11 |
| dependencies | `junos-eznc>=2.7` (PyEZ), `PyYAML>=6.0`, `lxml>=5.0` |
| `dev` extra | `pytest>=8.0` |
| entry point | `mig-validate = migration_validator.cli:main` |
| pytest | `testpaths = ["tests"]`, `addopts = "-q"` |

The entry point is why `pip install -e .` gives you a `mig-validate` command. The parsers in
the repository root (`mx_parser.py`, `evo_parser.py`) are **not part of the package** and run
directly as scripts.

## `.gitignore`

Ignores `__pycache__/`, `*.py[cod]`, `.venv/`, `pyats-venv/`, `*.egg-info/`, `.pytest_cache/`
and **`runs/`** — run outputs (snapshots and results) are neither edited nor versioned.

## `migration_validator/__init__.py`

Just a docstring and `__version__ = "0.1.0"`, asserted by `tests/test_package.py`.

---

## `api.py` — the programmatic interface

The single seam a future GUI will call. Three functions:

```python
capture(host, *, inventory=None, options=None, collectors=None,
        phase=None, ping_count=5, record_raw=None) -> Snapshot
evaluate(snapshot, *, baseline=None, mapping=None, config=None, now=None) -> RunResult
list_checks() -> list[dict]
```

- **`capture()`** accepts `inventory` either as a ready `Inventory` object or as a path
  string (which it then loads itself). It opens the connection through `connect()` as a
  context manager and calls `capture_device()` inside. The connection is always closed, even
  on an exception.
- **`evaluate()`** first calls `checks.all.load_all()` — without it the check registry would
  be empty if a GUI or a test called the API directly. It then delegates to
  `engine.evaluate_snapshots()`. The `now` parameter allows a deterministic timestamp in tests.
- **`list_checks()`** returns `describe()` for every registered check. It exists so a GUI does
  not need a hard-coded list of tests.

The essential property: **`evaluate` has no way to reach the network.** It receives no
`Device`, no hostname and no credentials — only finished snapshots.

## `cli.py` — the terminal interface

A thin wrapper over `api.py`, not an alternative implementation. It defines six subcommands:

| subcommand | function | what it does |
|---|---|---|
| `capture` | `_cmd_capture` | `api.capture()` + `save_snapshot()`, warnings about failed collectors on stderr; with `--run` also writes into the run manifest (`_capture_into_run`) |
| `evaluate` | `_cmd_evaluate` | loads snapshots, mapping and config, calls `api.evaluate()`, filters and renders; with `--run` evaluates every paired snapshot from the manifest (`_evaluate_run`) |
| `status` | `_cmd_status` | overview of old↔new port pairing and captured phases for a given run directory |
| `match` | `_cmd_match` | `match_scopes()` only — for debugging `mapping.yml` without a full validation |
| `checks` | `_cmd_checks` | prints the check registry, text or JSON |
| `record` | `_cmd_record` | stores every collector's raw RPC XML as fixtures |

Other notable parts of the file:

- **`ToolError`** — a dedicated exception for "the tool failed". `main()` catches it and
  returns exit code 2; it catches `OSError` and `ValueError` for the same reason. A failed
  *test*, by contrast, is exit code 1 and is expressed as a return value, not an exception.
- **`_load_snapshot()`** converts four kinds of failure (`FileNotFoundError`,
  `SnapshotVersionError`, `JSONDecodeError`, `KeyError`/`TypeError`) into a `ToolError` with a
  Czech message. A broken snapshot therefore never ends in a traceback, only in a readable
  error.
- **`_add_auth_arguments()`** — shared authentication flags for `capture` and `record`, a
  convention inherited from the parsers: `--username` (default `ansible`),
  `--auth key|password`, `--key-file` (default `~/.ssh/id_rsa`), `--password`, `--timeout`,
  and the SSH port. The SSH-port flag name is parameterized (`port_flag`/`port_dest`):
  `record` calls it with the default `--port`, `capture` calls it with `--ssh-port` — under
  `capture`, `--port` means the network port (`ge-0/0/0`) in `--run` mode, not SSH, so both
  had to be selectable at the same time.
- **`_parse_statuses()`** turns `--status pass,warn` into a set of `Status`.

Two things worth watching:

1. **With `--format text` and `--output` set**, the terminal receives text but the file
   receives **JSON** — and the unfiltered `result`, not the filtered `shown`. With
   `--format json` the filtered output is written instead.
2. **The `record` subcommand exists in addition to what the specification describes**, which
   mentions only `capture --record-raw`. Both work, and they differ: `record` reports success
   or failure per RPC and keeps going with a message; `capture --record-raw` is a silent
   best-effort (`capture._record`).

## `capture.py` — collection orchestration

One public function, `capture_device(device, address, ...)`. The phase order is deliberate:

```
1. detect_platform(device)          junos | junos-evo
2. _select_collectors(...)          filter by platform and --collectors
3. for collector in selected:       collector.collect() → facts[name]
4. build_scopes(inventory)          only when an inventory is present
5. resolve_targets(scopes, ARP)     ping targets from scopes and the ARP table
6. run_ping(...) per target         active measurement
7. Snapshot(...)                    freeze
```

Key points:

- **A collector failure does not abort the capture.** `CollectorError` is caught, the area is
  filled with an empty value **of the correct type** (`_empty_for()` — `[]` for `arp`, `{}`
  otherwise) and the error is recorded in `capture.collectors`. Checks over that area then get
  `SKIP`, never `PASS`.
- **An unknown name in `--collectors` is a hard error** (`ValueError`), not a silent omission
  — otherwise a typo would look like a successful capture missing an area.
- **Ping only runs when scopes exist**, i.e. only with an inventory. Without one the snapshot
  carries `probes.ping: []`.
- **`_record()`** stores raw XML for `--record-raw`. It iterates `collector.rpc_names()`, not
  just `rpc_name()`, so a collector with multiple RPCs (`evpn_mac` on MX) stores both — the
  first as `evpn_mac.xml`, the second as `evpn_mac.2.xml`. It is best-effort: a failed RPC is
  silently skipped, because recording fixtures must not bring down a capture.
- The module-level import of `migration_validator.collectors.all` is what **populates the
  collector registry**.

## `engine.py` — evaluation orchestration

`evaluate_snapshots(subject, baseline=None, mapping=None, config=None, now=None)`. It never
touches the network; all data comes from the snapshots.

The flow:

1. `_scopes_of()` — takes the scopes from the snapshot and, **when there are none, produces a
   single device scope**. That is how the inventory-less mode is realised without a single
   branch inside the checks.
2. Without a baseline: `_run_scope()` runs for each scope with no baseline data.
3. With a baseline: `match_scopes()` pairs the scopes, and then
   - matched pairs are evaluated **against the baseline**,
   - unpaired subject scopes are evaluated **in state mode** and also go into
     `unmatched.subject` (a new service is still checked, it just has nothing to compare to),
   - unpaired baseline scopes go **only** into `unmatched.baseline` — they are not on the
     subject, so there is nothing to measure.
4. `summary` is totalled across every check of every scope (`count_statuses()` from
   `models/result.py` — the same function the filter uses when recomputing and the renderer
   uses for the service counts).
5. `_unassigned_bgp_peers()` finds subject peers that fell into no scope. In device mode it
   returns an empty list (the device scope "owns" everything).

Two non-trivial functions:

- **`_aligned_baseline_data()`** renames baseline interface keys to the subject's names
  (`ge-0/0/2.113` → `et-0/0/8.113`) so `interface_traffic` has something to compare against.
  Details in [../architecture.md](../architecture.md), section "Interface-name alignment when
  comparing".
- **A scope's status** is `Status.worst()` across its checks, but **only across those that did
  not return `SKIP`**. If nothing remains, the result is `SKIP`. Without that condition a
  healthy IPVPN service without a baseline would show `SKIP` merely because of a compare-only
  check.

## `config.py` — tolerances and severities

A small file with a large effect: **no tolerance or severity is hard-wired inside a check.**

- `DEFAULTS` — a dict of default values per check id.
- `CheckConfig.options(check_id)` — merges the defaults with the user's YAML (the user wins).
- `CheckConfig.severity(check_id, default)` — overrides severity from the config, otherwise
  returns the check class's default.
- `CheckConfig.enabled(check_id)` — `false` means the check never runs at all (the only one
  disabled by default is `traffic_ceased`).
- `load_config(path)` reads the `checks:` key from YAML; `default_config()` returns an empty
  configuration.

The list of defaults is in [../reference.md](../reference.md#2-configyml).
