# `models/` — datové modely

Soubory: `inventory.py`, `scope.py`, `snapshot.py`, `result.py` a prázdný `__init__.py`.

Všechny modely jsou `@dataclass` a všechny mají `to_dict()` (a kde je potřeba i
`from_dict()`), protože **veškerý výstup musí být serializovatelný do JSON** — je to
podmínka pro budoucí GUI.

---

## `inventory.py` — vstup z parserů

Model YAML, který vyrábějí `mx_parser.py` / `evo_parser.py`.

### `ServiceEntry`

Jeden záznam = jedno rozhraní a služba, která na něm běží.

| pole | typ | poznámka |
|---|---|---|
| `interface` | `str` | povinné, logická jednotka nebo fyzický port |
| `service_type` | `str` | povinné (`Internet`, `IPVPN`, `E-Line`, `E-LAN`, `Core`, `Layer1`, ...) |
| `description` | `str \| None` | popisek z konfigurace — nese hlavní tíhu párování |
| `service_subtype` | `str \| None` | `vpws`, `vlan-aware`, `vlan-based`, `physical-port`, ... |
| `ipv4_address` | `list[str]` | IPv4 adresy s prefixem |
| `ipv6_address` | `list[str]` | IPv6 adresy s prefixem |
| `virtual_gw_ipv4_address` | `list[str]` | IPv4 virtual-gateway-address u IRB |
| `virtual_gw_ipv6_address` | `list[str]` | IPv6 virtual-gateway-address u IRB |
| `routing_instance` | `str \| None` | |
| `active` | `bool` | |
| `protocol`, `bgp_neighbor`, `bridge_domain`, `customer_vlan` | `list[str]` | |
| `static_route` | `list[dict]` | záměr z konfigurace: `{rib, prefix, next_hop: list[str]}` |
| `bfd` | `list[dict]` | záměr z konfigurace: `{peer, minimum_interval, multiplier, source}` |

`static_route` a `bfd` jsou **konfigurační záměr, ne měření**. Právě proti nim checky
`static_route_status` a `bfd_session_state` porovnávají, co se v tabulce a v session
skutečně našlo — bez nich by nešlo odlišit „nakonfigurováno a nefunguje" od „nic tu nebylo".
`bfd[].source` je `neighbor` nebo `group` podle toho, na které úrovni BGP hierarchie byl
záměr nalezen.

Vlastnost `physical_name` vrátí část před tečkou (`ge-0/0/2.113` → `ge-0/0/2`).

`from_dict()` je záměrně **tolerantní k neznámým klíčům** — parser přidává i
`detection_confidence` a `detection_reason`, které validator nepotřebuje, a rozšíření
parseru tedy nerozbije načtení. Tvrdě selže jen při chybějícím `interface` nebo
`service_type`.

Pomocné funkce `_as_list()` / `_as_optional_str()` normalizují skalár na seznam a čísla na
řetězce, takže na VLAN `113` zapsanou jako číslo se nespadne.

### `Inventory` a `load_inventory()`

`Inventory` = `device` (adresa) + `entries`. `load_inventory(path)` čte YAML a vyžaduje
mapping s klíčem `interfaces`; jinak vyhodí `ValueError` s cestou k souboru v hlášce.

Inventory nese top-level klíč `schema_version` (`INVENTORY_SCHEMA_VERSION = 3`).
`load_inventory()` **jinou hodnotu tvrdě odmítne** — nedopočítává starou strukturu.

Důvod je u obou zvýšení stejný: chybějící pole by se neprojevilo jako chyba, ale jako
zelená služba.

- **1 → 2**: adresy se přejmenovaly na rodiny (`ip_address` → `ipv4_address` /
  `ipv6_address`), takže tolerantní čtení starého souboru by tiše vrátilo službu bez jediné
  adresy — ping by se nespustil a služba by přesto svítila zeleně.
- **2 → 3**: přibyla pole `static_route` a `bfd`. Inventory verze 2 je nemá, takže by
  `Selectors.static_routes` i `.bfd_peers` zůstaly prázdné, checky by neměly co porovnávat
  a nakonfigurovaná routa chybějící v tabulce by se nikdy neohlásila.

Inventory se proto po zvýšení verze musí **znovu vygenerovat parserem**, ne doupravit ručně.

---

## `scope.py` — filtr nad fakty

Nejdůležitější soubor celého balíčku z pohledu architektury: **scope je jediné místo, kde se
rozhoduje, která data patří které službě.**

### `ScopeKey`

Trojice `description` + `service_type` + `service_subtype`. Frozen dataclass, takže se dá
použít jako klíč slovníku — čehož využívá `builder.py` při detekci duplicit.

### `Selectors`

Deset seznamů řetězců: `interfaces`, `physical_interfaces`, `routing_instances`,
`bgp_neighbors`, `local_ipv4`, `local_ipv6`, `virtual_gw_v4`, `virtual_gw_v6`, `vlans`,
`bridge_domains`. Adresy i virtual-gateway jsou rozdělené podle rodiny — stejně jako
`ServiceEntry` výš — protože ping a report musí umět zdroj/cíl vybrat podle rodiny cíle, ne
podle pořadí v jednom smíchaném seznamu.

K nim dva seznamy slovníků, které nesou **konfigurační záměr**:

| selektor | tvar položky | role |
|---|---|---|
| `static_routes` | `{rib, prefix, next_hop}` | zároveň **filtr** (vybírá routy podle dvojice `(rib, prefix)`) i **množina**, proti které check pozná, že nakonfigurovaná routa v tabulce chybí |
| `bfd_peers` | `{peer, minimum_interval, multiplier, source}` | **jen záměr** — session se vybírají přes `bgp_neighbors` |

Že `bfd_peers` neslouží jako filtr, je záměr: jsou to dvě různé věci a slít je do jednoho
seznamu by znamenalo držet je v synchronu. Session peeru, kterého parser do záměru
nedoplnil, by se do scope nedostala a chyba v průchodu parseru by zmizela beze stopy.

`matches_interface(name)` vrací `True`, když je název mezi logickými **nebo** fyzickými
rozhraními. Díky tomu se stavové checky spustí i na fyzickém rodiči (`et-0/0/8`), pokud
inventory obsahuje odpovídající `Layer1` záznam.

### `Scope` a `Scope.select()`

```python
scope.select(facts, probes) -> dict   # klíče: interfaces, arp, nd, bgp,
                                      #        evpn_vpws, evpn_esi, evpn_mac,
                                      #        routes, bfd, ping
```

Modulová konstanta **`FACT_AREAS`** vyjmenovává oblasti, které smí ve faktech být:
`interfaces`, `arp`, `nd`, `bgp`, `evpn_vpws`, `evpn_esi`, `evpn_mac`, `routes`, `bfd`.
Device scope podle ní vrací všechny oblasti beze změny, takže **oblast zapomenutá v tomhle
seznamu by v režimu bez inventory zmizela**.

Filtrování per oblast:

| oblast | podle čeho |
|---|---|
| `interfaces` | `matches_interface(název)` |
| `arp` | `matches_interface(entry["interface"])` |
| `nd` | `matches_interface(entry["interface"])` |
| `bgp` | `peer ∈ selectors.bgp_neighbors` |
| `evpn_vpws` | klíč (název instance) `∈ selectors.routing_instances` |
| `evpn_esi` | `matches_interface(data["interface"])` |
| `evpn_mac` | klíč (název instance) `∈ selectors.routing_instances` |
| `routes` | dvojice `(RIB, prefix)` `∈ selectors.static_routes` |
| `bfd` | `peer ∈ selectors.bgp_neighbors` |
| `ping` | `probe["scope_id"] == scope.id` |

Dvě věci, které stojí za zdůraznění:

- **`routes` se filtrují na dvojici, ne jen na prefix.** Stejný prefix může existovat ve víc
  RIB (typicky `0.0.0.0/0`) a výběr podle samotného prefixu by službě přiřadil cizí routu.
  Prázdná tabulka se nevrací — v reportu by nic neřekla a check by ji musel přeskakovat.
- **`bfd` se filtruje podle `bgp_neighbors`, ne podle `bfd_peers`.** Kdyby se vybíralo podle
  záměru, session peeru, kterého parser do záměru nedoplnil, by se do scope nedostala —
  a chyba v průchodu parseru by tím zmizela beze stopy.

**Device scope** (`kind: "device"`, prázdné selektory) je zkratka: vrátí všechny oblasti
beze změny. Tím se realizuje režim bez inventory tak, že checky nemají jedinou větev navíc.

`select()` je odolné vůči chybějícím oblastem — `facts.get(area) or {}` vrátí prázdno místo
výjimky, takže snapshot z běhu s vypnutými collectory se pořád dá vyhodnotit.

Funkce `device_scope()` vyrobí ten jediný scope pro režim bez inventory.

---

## `snapshot.py` — zmrazený stav zařízení

### `DeviceMeta`

`address`, `hostname`, `platform` (`junos` | `junos-evo`), `model`, `version`,
`uptime_seconds`. Poslední jmenované zatím vždy `None` — `connection/junos.py::device_meta()`
ho neplní.

### `CaptureMeta`

`started_at`, `finished_at`, `phase`, `collectors`. Metoda **`failed_collectors()`** vrací
`{jméno: chybová hláška}` jen pro ty, které neskončily `ok` — a je to přesně ten kanál,
kterým se selhaný sběr promítne do `SKIP` u checků (`CheckContext.failed_collectors`).

### `Snapshot`

`device`, `capture`, `facts`, `probes`, `scopes`, `inventory`, `schema_version`.

`from_dict()` **tvrdě odmítne jinou `schema_version`** (`SnapshotVersionError` s výpisem obou
verzí). Žádná snaha o migraci starých dat: raději hlasité selhání než tichá špatná
interpretace.

`save_snapshot()` / `load_snapshot()` zapisují a čtou JSON v UTF‑8 s `ensure_ascii=False`
a zakládají cílový adresář. Round-trip přes disk ověřuje
`tests/test_capture.py::test_snapshot_round_trips_to_disk`.

---

## `result.py` — modely výsledku

### `Status` a `Severity`

`Status`: `PASS` / `SKIP` / `WARN` / `FAIL`, s pořadím pro `worst()`:

```
PASS (0)  <  SKIP (1)  <  WARN (2)  <  FAIL (3)
```

`SKIP` je tedy „horší" než `PASS` — nezměřeno není v pořádku. `Status.worst()` prázdné sady
vrací `SKIP`.

`Severity`: `critical` | `advisory`.

### `Outcome` a `derive_status()`

`Outcome` je to, co **naměří check**: `ok` / `degraded` / `broken` / `skip`.
`derive_status(outcome, severity)` z toho udělá `Status`:

| outcome | critical | advisory |
|---|---|---|
| `ok` | PASS | PASS |
| `degraded` | **WARN** | **WARN** |
| `broken` | FAIL | WARN |
| `skip` | SKIP | SKIP |

`degraded` je WARN **vždy**, i při severity `critical`. Pravidlo „částečný úspěch = WARN"
je tím zapsané jednou na jednom místě.

### `Finding` → `CheckResult`

`Finding` je surový výstup checku: `outcome`, `message`, `label`, `family` (4 / 6 / `None`),
`value`, `baseline_value`, `delta`, `baseline`, `subject`, `details`. `CheckResult` je totéž
**po odvození statusu** plus `id`, `mode` a `severity`. Převod dělá
`checks/base.py::run_check()`, ne check sám.

`label`/`value`/`baseline_value`/`delta` existují kvůli reportu: rozklad naměřené hodnoty na
popisek a hodnotu ve sloupcích musí udělat check, protože jen on ví, co je u dané veličiny
hodnota a co vysvětlení — renderer čísla zpětně neparsuje ze `message`. `family` řadí nález do
sekce `IPv4`/`IPv6` v textovém reportu (`reporting/view.py`); `None` znamená řádek vázaný na
rozhraní, ne na adresu (např. stav rozhraní).

`CheckResult.to_dict()` vynechává prázdné volitelné klíče, takže JSON nezaplaví `null`.

### `ScopeResult` a `RunResult`

`ScopeResult`: `scope_id`, `key`, `status`, `match` (`MatchInfo | None`), `checks`, `identity`.

`identity` (naplňuje `engine.py::_identity()`) nese vše, co report o službě potřebuje a co by
jinak zůstalo jen ve `Scope`: `description`, `service_type`, `service_subtype`,
`routing_instance`, `ipv4`, `ipv6`, `virtual_gw_v4`, `virtual_gw_v6`. Renderer nemá přístup ke
scopům, jen k `RunResult`, takže bez tohohle by sloupce s adresami a virtual gateway neměly
odkud vzít data.

`MatchInfo`: `status` (`matched` | `unmatched`), `method`, `confidence`,
`baseline_interfaces`, `subject_interfaces`, `reason`.

`RunResult`: `evaluated_at`, `subject`, `baseline`, `summary`, `scopes`, `unmatched`,
`unassigned`, `filtered`, `schema_version`. Je to jediná věc, kterou reporting dostane —
**spočítané je všechno předem**, takže se textový výstup a GUI nemohou rozejít.

`filtered` je `None` u celého běhu a slovník
(`scopes_shown`, `scopes_total`, volitelně `text` a `statuses`) u výsledku, který prošel
`filter_result()`. `to_dict()` ho v prvním případě do JSON vůbec nedá, takže nefiltrovaný
výstup má přesně ten tvar, jaký měl dřív.

`count_statuses(statuses)` rozpadá libovolné stavy na čtyři countery. Bere stavy, ne checky,
právě proto, aby ji mohl použít souhrn za checky (engine, filtr) i souhrn za služby
(renderer) — a rozdíl mezi těmi dvěma jednotkami byl v reportu neoznačený.
