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

## Documentation / Dokumentace

| | |
|---|---|
| 🇬🇧 **English** | [docs/en/README.md](docs/en/README.md) — operator guide · [architecture](docs/en/architecture.md) · [per-file docs](docs/en/index.md) · [reference](docs/en/reference.md) |
| 🇨🇿 **Česky** | [docs/cs/README.md](docs/cs/README.md) — provozní příručka · [architektura](docs/cs/architecture.md) · [popis souborů](docs/cs/index.md) · [reference](docs/cs/reference.md) |

The design specification and implementation plans (Czech) live in `docs/superpowers/`.

> The tool's own interface is Czech: CLI help, log messages and result messages are all in
> Czech, written without diacritics. The English documentation quotes those strings verbatim
> so they can be grepped in real output.
