# `connection/` — connecting to the device

Files: `junos.py` and an empty `__init__.py`.

**The only layer in the whole tool that touches the network.** Checks never import it — an
explicit rule written into the docstring of `checks/base.py`.

The layer is deliberately thin, because it is the only thing that cannot be tested without a
real box. Everything testable offline lives elsewhere.

---

## `junos.py`

### `ConnectionOptions`

A dataclass of connection parameters:

| field | default |
|---|---|
| `host` | — (required) |
| `username` | `ansible` |
| `auth_type` | `key` (or `password`) |
| `key_file` | `~/.ssh/id_rsa` |
| `password` | `None` |
| `port` | `22` |
| `timeout` | `30` |

`device_kwargs()` assembles the arguments for the PyEZ `Device` from these. Validation lives
here rather than in PyEZ:

- `auth_type="password"` without a password → `ValueError("auth_type 'password' vyzaduje heslo")`,
- an unknown `auth_type` → `ValueError` listing the permitted values.

The defaults follow the convention of the existing parsers, so an operator does not have to
switch habits between tools.

### `connect()` — a context manager

```python
with connect(options) as device:
    ...
```

Opens the connection and **translates PyEZ exceptions into `JunosConnectionError` with a
message that says why**:

| PyEZ exception | message |
|---|---|
| `ConnectAuthError` | `<host>: autentizace selhala (uzivatel <user>) - ...` |
| `ConnectTimeoutError` | `<host>: timeout po <N> s - ...` |
| `ConnectRefusedError` | `<host>: spojeni odmitnuto - ...` |
| `ConnectError` (other) | `<host>: pripojeni selhalo - ...` |

The distinction is a requirement from the specification: *a wrong key* and *an unreachable
box* call for different operator responses. The CLI converts this exception into a `ToolError`
and returns **exit code 2** — in that case no snapshot is written at all.

Closing happens in a `finally`, so `device.close()` also runs if the block raises.

### `detect_platform(device)`

Returns `"junos"` or `"junos-evo"`:

1. `EVO` in the version string (case-insensitive) → `junos-evo`,
2. otherwise a model starting with `PTX10`, `ACX7`, `QFX5700`, `MX304` → `junos-evo`,
3. otherwise `junos`.

Verified against the lab (VMX `24.2R1-S2.5` and PTX10002 `25.2R1.8-EVO`): the first rule
decides and the model fallback never kicks in. The function reads `device.facts` via
`getattr`, so it works with any object that has `facts` — which is why tests call it with a
`FakeDevice`.

The result is passed to `collectors_for(platform)` and to every `collector.collect()` — it is
the single input that decides the platform-specific RPC variants.

### `device_meta(device, address)`

Assembles a `DeviceMeta` from `device.facts` (`hostname`, `model`, `version`) and the detected
platform. `uptime_seconds` stays `None` — the schema field exists, but nothing fills it yet.

---

## What is deliberately absent

- **No retry.** A connection failure is a hard error for the whole run; retrying makes sense
  at the level of individual RPCs, and that is handled by the collector (`CollectorError`) or
  by ping (`run_ping`, which also swallows the transiently malformed XML from vMX).
- **No content parsing.** It returns a `Device`; the collector fetches RPC responses itself.
- **No knowledge of services.** The layer knows nothing about scopes or inventories.
