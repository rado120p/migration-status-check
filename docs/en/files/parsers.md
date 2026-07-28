# `mx_parser.py` and `evo_parser.py` — configuration parsers

Two standalone scripts in the repository root. **They are not part of the
`migration_validator` package** and are run directly:

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

## Relationship to the validator (AR‑8)

The parsers stay **unchanged for now** and the validator merely consumes their output. The two
files are ~90 % identical and refactoring them into a shared package would make sense, but
that is a separate decision — the inventory model is defined as a dataclass inside the
validator (`models/inventory.py`), so integrating later means only rewiring the output.

In practice this means: **the validator has no runtime dependency on the parsers.** It consumes
only a YAML file. The inventory can just as well be written by hand.

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

`retrieve_configuration()` fetches `interfaces`, `routing-instances`, `protocols`,
`bridge-domains`, `vlans` and `switch-options` **in a single filter**, so the parser sees the
relationships between them at once. `inherit` means `apply-groups` are applied.

---

## Service classification

`JunosServiceParser.parse()` (in the EVO variant `JunosEvoAcxServiceParser`) proceeds in this
order:

1. `_parse_routing_instances()` — instances, their type, protocols, RD/RT, bridge domains,
   VLANs, BGP neighbours;
2. `_parse_default_bgp_neighbors()` — top-level `protocols bgp` belongs to the default
   instance;
3. `_parse_global_l2circuits()`, `_parse_global_connections()` — E-Line outside instances;
4. `_parse_interfaces()` → `_build_interface_config()` — interfaces, families, VLANs,
   addresses, virtual-gateway;
5. `_classify_interface()` → `_detect_service()` — the actual service-type decision;
6. `_assign_bgp_neighbors()` — attributes peers to services by subnet match with the interface.

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

```yaml
device: 172.20.20.4
interfaces:
- interface: ge-0/0/2.113
  description: L3VPN-CPE13-NNI
  service_type: IPVPN
  service_subtype: null
  ip_address:
  - 198.11.13.1/30
  virtual_gw_ip_address: []
  routing_instance: L3VPN-CPE13-NNI
  active: true
  protocol:
  - inet
  bgp_neighbor:
  - 198.11.13.2
  bridge_domain: []
  customer_vlan:
  - '113'
  detection_confidence: high
  detection_reason:
  - Rozhraní je přiřazeno do routing instance typu vrf.
```

The key order is fixed (`clean_service_dict()`) and `service_subtype` stays in the YAML even
when `null`, so downstream scripts get a stable structure.

Which keys the validator actually reads is described in
[models.md](models.md#inventorypy--the-input-from-the-parsers).

---

## `172.20.20.4.yml` and `172.20.20.5.yml` in the root

Sample outputs from the lab topology (MX and PTX). They are **untracked** in git — working
artefacts.

The reproducible copies the tests run against live in **`tests/fixtures/`** (commits `26d8461`
and `671615b` moved them there precisely so that the tests do not depend on whatever happens
to be in the root). When testing, edit the fixtures; the root files can be overwritten freely
by a new parser run.
