# Migration Validator

Validation of network service state during a migration from **MX (classic Junos)** to
**Junos EVO** (ACX/PTX). Collects operational state from both devices, freezes it into
snapshots and compares them — despite port names differing between the two boxes
(`ge-0/0/2.113` → `et-0/0/8.113`).

Validace stavu síťových služeb při migraci z **MX (běžný Junos)** na **Junos EVO** (ACX/PTX).

```bash
pip install -e .

mig-validate capture  --device 172.20.20.4 --inventory 172.20.20.4.yml \
                      --phase pre-migration --output runs/mig01/pre.json
mig-validate evaluate --snapshot runs/mig01/post.json --baseline runs/mig01/pre.json
```

For migrations that go port by port and span several capture steps (`pre` / `post` /
`rollback`), `mig-validate capture --run <name> ...` / `evaluate --run <name>` / `status
--run <name>` track devices, old↔new port pairing and captured snapshots in a
`runs/<name>/run.yml` manifest instead of explicit `--output`/`--snapshot`/`--baseline`
paths. The explicit workflow above keeps working unchanged; see [docs/cs/README.md kap.
3a](docs/cs/README.md#3a-run-management---run) (Czech) for the full walkthrough.

Pro migrace po portech a přes víc kroků (`pre`/`post`/`rollback`) existuje vedle výše
uvedeného ruční cesty i `mig-validate capture --run <nazev> ...` / `evaluate --run <nazev>` /
`status --run <nazev>` — párování starý↔nový port a přehled pořízených snímků si drží
`runs/<nazev>/run.yml`. Ruční workflow beze změny funguje dál; podrobný postup je
v [docs/cs/README.md, kap. 3a](docs/cs/README.md#3a-run-management---run).

## Profil a auth soubor

Profil (sdileny, klidne v gitu) rika, CO beh testuje:

```yaml
profile:
  collectors: [interfaces, bgp, evpn_instance]
  service_types: [Internet, IPVPN]
  ping_count: 3
checks:
  interface_optics_levels:
    enabled: false
```

```bash
mig-validate evaluate --run mig01 --profile profiles/core-only.yml
```

Auth soubor (per-user, default ~/.config/mig-validate/auth.yml) rika,
KDO se pripojuje - heslo pres env promennou, plaintext jen pri 0600:

```yaml
username: rmohyla
auth: password
password_env: MIG_PROD_PASSWORD
```

Sdileny ansible ucet: `--auth-file /cesta/k/ansible-auth.yml`.
Precedence vsude: CLI flag > soubor > vestavena default.
Heslo do env bez ~/.bash_history: `read -s MIG_PROD_PASSWORD && export MIG_PROD_PASSWORD`.

## Documentation / Dokumentace

| | |
|---|---|
| 🇬🇧 **English** | [docs/en/README.md](docs/en/README.md) — operator guide · [architecture](docs/en/architecture.md) · [per-file docs](docs/en/index.md) · [reference](docs/en/reference.md) |
| 🇨🇿 **Česky** | [docs/cs/README.md](docs/cs/README.md) — provozní příručka · [architektura](docs/cs/architecture.md) · [popis souborů](docs/cs/index.md) · [reference](docs/cs/reference.md) |

The design specification and implementation plans (Czech) live in `docs/superpowers/`.

> The tool's own interface is Czech: CLI help, log messages and result messages are all in
> Czech, written without diacritics. The English documentation quotes those strings verbatim
> so they can be grepped in real output.
