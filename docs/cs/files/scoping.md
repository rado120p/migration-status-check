# `scoping/` — scopy a párování služeb

Soubory: `builder.py`, `matcher.py`, `mapping.py` a prázdný `__init__.py`.

Tahle vrstva **nečte nasbíraná data**. Pracuje výhradně s inventory a se scopy — je to
vrstva „která služba je která", ne „jak se jí daří".

---

## `builder.py` — inventory → scopy

Jediná funkce `build_scopes(inventory) -> list[Scope]`.

### Způsobilost pro scope

Scope vznikne jen pro záznam, který:

1. má `service_type` v `MIGRATED_SERVICE_TYPES` = `Internet`, `IPVPN`, `E-Line`, `E-LAN`,
   `Core`,
2. **není management rozhraní** (`is_management()`: `fxp`, `em`, `me`, `vme`, `bme`,
   `re0:mgmt-`, `re1:mgmt-`).

Vyloučení managementu je zabudované v nástroji, ne v `mapping.yml`, aby ho nemusel vyplňovat
každý operátor znovu. Je nutné proto, že parser tato rozhraní pořád kategorizuje jako
`Internet` (`fxp0.0`, `re0:mgmt-0.0`) — **bez tohohle pravidla by nástroj pingoval do
management sítě.**

### Layer1 záznamy

`Layer1` / `physical-port` se **samostatným scopem nestanou.** Slouží jen jako potvrzení,
že fyzický rodič v inventory existuje, a doplní se do `physical_interfaces` logické jednotky,
která na něm sedí.

Důvod je praktický: `Layer1` je v reálných datech nejčastější typ (16 z 39 záznamů ve
vzorku). Kdyby se z každého stal scope, tvořily by nespárované fyzické porty většinu seznamu
`unmatched` a ten by přestal být čitelný — což by zabilo přesně tu vlastnost, kvůli které
existuje.

Praktický dopad: `interface_state` na fyzickém portu proběhne, ale jako součást služby,
která přes něj jede.

### Tvorba id

`svc:<description nebo název rozhraní>:<service_type>`, například
`svc:L3VPN-CPE13-NNI:IPVPN`. Když by na stejný `ScopeKey` vyšlo víc záznamů (`Counter` nad
klíči), přidá se na konec ještě název rozhraní: `svc:et-0/0/10.0:IPVPN`. Bez toho by dvě
různé služby sdílely jedno id.

### Naplnění selektorů

| selektor | zdroj v `ServiceEntry` |
|---|---|
| `interfaces` | `interface` (vždy právě jedno — na tom stojí AR‑6b) |
| `physical_interfaces` | `physical_name`, jen když pro něj existuje `Layer1` záznam |
| `routing_instances` | `routing_instance` |
| `bgp_neighbors` | `bgp_neighbor` |
| `local_ipv4` | `ipv4_address` |
| `local_ipv6` | `ipv6_address` |
| `virtual_gw_v4` | `virtual_gw_ipv4_address` |
| `virtual_gw_v6` | `virtual_gw_ipv6_address` |
| `vlans` | `customer_vlan` |
| `bridge_domains` | `bridge_domain` |

---

## `matcher.py` — párování baseline ↔ subject

`match_scopes(baseline, subject, mapping) -> MatchSet` s poli `pairs`,
`unmatched_baseline`, `unmatched_subject`.

### Postup

1. **Ignore** — scopy odpovídající `ignore:` z `mapping.yml` zmizí z obou stran.
2. **Ruční mapování** (`_apply_manual`) — má absolutní přednost, výsledný pár má
   `method: "manual"`, `confidence: "manual"`.
3. **Automatická pravidla** v pořadí priority; každé pravidlo pracuje jen se scopy, které
   zbyly po předchozích.

| pořadí | `method` | `confidence` | klíč |
|---|---|---|---|
| 1 | `description+service_type+service_subtype` | high | trojice, jen když jsou description i subtype vyplněné |
| 2 | `description+service_type` | high | dvojice |
| 3 | `routing_instance+service_type` | medium | jeden klíč **na každou** routing-instance ve scope |
| 4 | `subnet+service_type` | medium | síťová adresa každého záznamu z `local_ipv4` **i** `local_ipv6`; p2p prefixy (síť ≤ 4 adresy: /30, /31, /127…) se klíčují celou hostitelskou adresou, aby se nespárovaly protilehlé konce téhož linku |
| 5 | `vlan+service_type` | low | každá VLAN ze scope |

Klíč je vždy **složený**, ne samotná description — jedna description může nést víc záznamů
(`ge-0/0/5` fyzické i `ge-0/0/5.0` logické mají stejnou).

4. Co zbude, jde do `unmatched` s důvodem `zadny kandidat na subject` (baseline) nebo
   `nova sluzba, chybi baseline` (subject).

### Nikdy se nehádá

Pár vznikne jen tehdy, když pod daným klíčem existuje **právě jeden** kandidát na každé
straně. Jinak jdou všichni kandidáti do `unmatched` s důvodem
`ambiguous: N kandidatu (id, id, ...)`.

Tichý špatný match by u migrace znamenal zelenou na rozbité službě — proto je přiznané
nespárování lepší.

### Sledování `paired` a `dropped`

Uvnitř jednoho pravidla se drží dvě množiny (podle `id()` objektu). Je to kvůli pravidlům
3–5, která **generují víc klíčů na jeden scope**: scope zahozený jako nejednoznačný pod
jedním klíčem by se pod jiným klíčem téhož pravidla jinak spároval a skončil by **zároveň
v `pairs` i v `unmatched`**. Kontrolují se proto obě množiny.

### Ruční mapování a nejednoznačnost

Pravidlo z `mappings:` musí vyjít na **právě jeden** scope na každé straně. Když jich vyjde
víc, pár nevznikne a všechny zasažené scopy jdou do `unmatched` s `ambiguous` — sémantika
je stejná jako u automatických pravidel.

---

## `mapping.py` — `mapping.yml`

### `Selector`

Frozen dataclass s `description`, `service_type`, `interface`. `matches(scope)` je logický
**AND** přes vyplněná pole; nevyplněná se ignorují. Prázdný selektor je odmítnutý při
načítání (`ValueError`), protože by matchoval všechno.

`interface` se porovnává proti `scope.selectors.interfaces`, tedy proti **logické jednotce**.
Napsat `ge-0/0/2` nezasáhne pět služeb, které přes ten port jedou — nezasáhne nic. Je to
záměr: u `mappings` musí pravidlo vyjít na právě jeden scope na každé straně, a mít
u `ignore` opačnou sémantiku téhož zápisu by bylo matoucí.

### `MappingRule` a `Mapping`

`MappingRule` = `baseline` selektor + `subject` selektor + volitelná `note` (jen dokumentace,
kód ji nepoužívá). `Mapping` drží seznam pravidel a seznam `ignore`; `is_ignored(scope)`
odpovídá na dotaz matcheru.

### `load_mapping()` / `empty_mapping()`

`load_mapping()` čte YAML a validuje: pravidlo musí mít `baseline` i `subject`, jinak
`ValueError` s cestou k souboru. `empty_mapping()` vrací prázdné mapování — používá se
vždy, když uživatel `--mapping` nezadá, takže matcher nemusí řešit `None`.

Formát souboru a příklady: [../reference.md](../reference.md#3-mappingyml).
