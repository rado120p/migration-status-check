# `mx_parser.py` a `evo_parser.py` — parsery konfigurace

Dva samostatné skripty v kořeni repozitáře. **Nejsou součástí balíčku `migration_validator`**
a spouští se přímo:

```bash
.venv/bin/python mx_parser.py  172.20.20.4 -o 172.20.20.4.yml
.venv/bin/python evo_parser.py 172.20.20.5 -o 172.20.20.5.yml
```

| skript | zařízení |
|---|---|
| `mx_parser.py` | MX — klasický Junos |
| `evo_parser.py` | ACX / PTX — Junos OS Evolved |

Vyrábějí **inventory**: YAML se seznamem rozhraní a služeb, které na nich běží. Validator ho
konzumuje přes `--inventory` a uloží si ho do snapshotu.

---

## Vztah k validatoru (AR‑8)

Parsery zůstávají zatím **beze změny** a validator jen konzumuje jejich výstup. Oba soubory
jsou z ~90 % identické a refaktoring do sdíleného balíčku by dával smysl, ale je to
samostatné rozhodnutí — inventory model je ve validatoru definovaný jako dataclass
(`models/inventory.py`), takže případná integrace znamená jen přepojení výstupu.

Prakticky to znamená: **validator na parsery nezávisí za běhu.** Konzumuje jen YAML soubor.
Inventory se dá klidně napsat i ručně.

---

## Průběh

```
main()
 ├─ parse_arguments()            argparse
 ├─ resolve_connection_options() klíč vs. heslo, případně dotaz na passphrase
 ├─ build_device()               PyEZ Device (gather_facts=False, auto_probe=timeout)
 ├─ retrieve_configuration()     jedno get_config RPC, committed + inherit
 ├─ JunosServiceParser(...).parse()
 ├─ create_yaml_data()
 └─ write_yaml()
```

`retrieve_configuration()` bere **jedním filtrem** `interfaces`, `routing-instances`,
`protocols`, `bridge-domains`, `vlans` a `switch-options`, aby parser viděl vazby mezi nimi
naráz. `inherit` znamená, že se aplikují `apply-groups`.

---

## Klasifikace služeb

`JunosServiceParser.parse()` (v EVO variantě `JunosEvoAcxServiceParser`) jede v pořadí:

1. `_parse_routing_instances()` — instance, jejich typ, protokoly, RD/RT, bridge domény,
   VLAN, BGP neighbory;
2. `_parse_default_bgp_neighbors()` — top-level `protocols bgp` patří do default instance;
3. `_parse_global_l2circuits()`, `_parse_global_connections()` — E-Line mimo instance;
4. `_parse_interfaces()` → `_build_interface_config()` — rozhraní, family, VLAN, adresy,
   virtual-gateway;
5. `_classify_interface()` → `_detect_service()` — vlastní rozhodnutí o typu služby;
6. `_assign_bgp_neighbors()` — přiřazení peerů ke službám podle shody se subnetem rozhraní.

`_detect_service()` zkouší v pořadí: **IPVPN → E-Line VPWS → E-Line CCC → E-LAN VPLS →
E-LAN EVPN → Core → Internet → Layer 1 → nerozpoznané L2.** Pořadí je podstatné: dřívější
pravidlo vyhrává.

Každý záznam nese `detection_confidence` (`high` / `medium` / `low`) a `detection_reason`
(seznam vět, proč byl typ zvolen). Validator tato pole **nepoužívá**, ale při ladění
nespárovaných služeb jsou v YAML k nezaplacení.

`_should_ignore_interface()` vypustí z výstupu jen doopravdy prázdná rozhraní (bez
description, family, VLAN, adres, encapsulation a bez instance) — ne rozhraní jen proto, že
typ vyšel `Unknown`.

---

## Kde se ty dva soubory liší

Rozdíl je soustředěný do detekce EVPN E-LAN a EVPN/VPLS instancí:

| | MX (`mx_parser.py`) | EVO (`evo_parser.py`) |
|---|---|---|
| VPLS | `instance-type vpls` nebo `protocols vpls` | navíc `virtual-switch` + `protocols vpls` |
| E-LAN subtype | `virtual-switch` + bridge domain → `vlan-aware`; `instance-type evpn` → `vlan-based` | nejdřív explicitní `service-type` (`vlan-aware` / `vlan-based` / `vlan-bundle`), pak `mac-vrf` podle počtu VLAN/domén, pak `virtual-switch` + `protocols evpn` |
| EVPN instance | `instance-type evpn` | `instance-type evpn` **nebo** `mac-vrf` |

Zbytek souboru (datové modely, XML pomocné funkce, CLI, připojení, zápis YAML) je shodný —
liší se jen jméno loggeru a texty v docstringu.

---

## CLI parserů

```
usage: mx_parser.py [-h] [--auth {key,password}] [-u USERNAME] [-k KEY_FILE]
                    [--ask-key-passphrase] [-p PORT] [--timeout TIMEOUT]
                    [-o OUTPUT] [--debug] hostname
```

| přepínač | default |
|---|---|
| `--auth` | `key` |
| `-u/--username` | `ansible` (v key režimu) |
| `-k/--key-file` | `~/.ssh/id_rsa` |
| `--ask-key-passphrase` | vyžádá passphrase klíče |
| `-p/--port` | `22` |
| `--timeout` | `30` |
| `-o/--output` | `<hostname>.yml` (nebezpečné znaky se nahradí `_`) |
| `--debug` | podrobné logování |

### Návratové kódy parserů

**Nejsou stejné jako u `mig-validate`** — parsery rozlišují druh selhání jemněji:

| kód | význam |
|---|---|
| `0` | v pořádku |
| `2` | přihlášení selhalo |
| `3` | timeout připojení |
| `4` | NETCONF odmítnut (`system services netconf ssh`) |
| `5` | jiná chyba připojení |
| `6` | chyba Junos RPC |
| `7` | chyba vstupu/výstupu nebo XML |
| `99` | neočekávaná chyba |
| `130` | přerušeno uživatelem |

---

## Výstupní formát

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

Pořadí klíčů je pevné (`clean_service_dict()`) a `service_subtype` zůstane v YAML i s
hodnotou `null`, aby měly navazující skripty stabilní strukturu.

Které klíče validator opravdu čte, je popsáno v [models.md](models.md#inventorypy--vstup-z-parserů).

---

## `172.20.20.4.yml` a `172.20.20.5.yml` v kořeni

Vzorové výstupy z laboratorní topologie (MX a PTX). V gitu jsou **nesledované** — jsou to
pracovní artefakty.

Reprodukovatelné kopie, na kterých běží testy, žijí v **`tests/fixtures/`** (commity
`26d8461` a `671615b` je tam přesunuly právě proto, aby testy nezávisely na tom, co je
zrovna v kořeni). Když testujete, upravujte fixtures; kořenové soubory klidně přepisujte
novým během parseru.
