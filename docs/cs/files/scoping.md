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
`svc:L3VPN-CPE13-NNI:IPVPN`. Název rozhraní se přidá na konec (`svc:et-0/0/10.0:IPVPN`), když se
opakuje jedno ze dvou: celý `ScopeKey` (popis, typ a subtyp společně — samo id subtyp nenese,
takže dvě jednotky se stejným popisem a typem, ale jiným subtypem, např. IPVPN a IPVPN+mvpn na
jednom portu, by se jinak slily), nebo výše sestavené id (např. bezpopisková unit, jejíž název
rozhraní se shoduje s popisem jiné služby). Bez toho by dvě různé služby sdílely jedno id.

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

4. Co zbude, jde do `unmatched` s důvodem, viz „Nespárovaný scope na konci" níže.

### Nikdy se nehádá — ale nejednoznačnost z poolu nevyřadí (E5, spec 2026-09-25)

Pár vznikne jen tehdy, když pod daným klíčem existuje **právě jeden** kandidát na každé
straně. To platí dál. Co se změnilo: **nejednoznačný scope zůstává v `remaining_*` poolu**
místo aby z něj navždy zmizel — po každém pravidle se odeberou jen scopy, které se
**spárovaly**. Pozdější (slabší) pravidlo — `routing_instance`, `subnet`, `vlan` — tak smí
nejednoznačný scope rozlišit, přesně jako dnes rozlišuje scope bez shody description.
Tichý špatný match by u migrace znamenal zelenou na rozbité službě — proto zůstává přiznané
nespárování, ne hádání.

Uvnitř **jednoho** pravidla se ale nesmí hádat. Vyhodnocení je **dvouprůchodové** a
nezávisí na pořadí (klíčů ve scope ani scopů v seznamu):

1. **První průchod** posoudí každý klíč pravidla sám za sebe. Buď dá jednoznačného
   kandidáta (1:1 shoda), nebo je pod ním scope nejednoznačný a zapíše se to.
2. Scope, který má napříč **různými klíči téhož pravidla** víc než jednoho odlišného
   kandidáta, je taky nejednoznačný — i když byl každý dílčí klíč sám o sobě 1:1, pravidlo
   nesmí hádat, který kandidát je ten pravý (pojistka proti pravidlům 3–5, která generují
   víc klíčů na jeden scope).
3. **Druhý průchod** spáruje jen ty, co po prvním průchodu zůstaly jednoznačné na obou
   stranách.
4. **Blokovaný kandidát** (final review I-1): 1:1 shoda, jejíž jeden konec je nejednoznačný
   (bod 1 nebo 2), se ve druhém průchodu nespáruje. Oba její konce si proto zapamatují
   nejednoznačnost, kde soupeřem je druhý konec hrany; důvod vyjmenuje všechny kandidáty
   nejednoznačného scope pod tímto pravidlem. Příklad: baseline B má vlany 10 a 20, subjekt
   S1 jen 10, S2 a S3 oba 20. B je nejednoznačný (vlan 20), S1 je jeho jediný 1:1 kandidát.
   S1 dostane `ambiguous: 3 kandidatu (S1, S2, S3)`, ne `nova sluzba, chybi baseline` —
   jinak by ho step běh tiše vyřadil do `excluded_services`, přestože jeho kandidát B zůstal
   nespárovaný. Když B později spáruje slabší pravidlo, nejednoznačnost S1 přestane trvat
   a S1 dostane `nova sluzba` jako dřív.

### Nespárovaný scope na konci

Seznam nespárovaných se staví **až na konci**, ne pravidlo po pravidle. Když je scope pod
klíčem (nebo napříč klíči, viz bod 2 výš) nejednoznačný, zapamatuje se důvod i scopy druhé
strany, se kterými soupeřil — a to **každá** zaznamenaná nejednoznačnost, ne jen první.
Scope, který nakonec nespárovalo žádné pravidlo, dostane:

- důvod **první** (v pořadí pravidel) zapamatované nejednoznačnosti, která **ještě** má
  aspoň jednoho nespárovaného soupeře. Nejednoznačnost tak může přetrvat i díky pozdějšímu
  (slabšímu) pravidlu, přestože ta z nejsilnějšího pravidla už je vyřešená,
- jinak `zadny kandidat na subject` (baseline) / `nova sluzba, chybi baseline` (subject) —
  žádná ze zapamatovaných nejednoznačností už nemá nespárovaného soupeře, takže žádná
  netrvá.

Modelový příklad (viz design, §4): baseline má „CPE" ve VRF-a, subjekt „CPE" ve VRF-a
(tenhle krok) a „CPE" ve VRF-b (dřívější vlna). Popis je 1×2 nejednoznačný,
`routing_instance` spáruje VRF-a ↔ VRF-a. VRF-b soupeřil jen s baseline VRF-a, ta má pár,
takže VRF-b dostane `nova sluzba, chybi baseline` — ne `ambiguous`, který by vytáhl scope
do NESPAROVANO a jmenoval kandidáta, jenž už je spárovaný.

### Ruční mapování a nejednoznačnost

Pravidlo z `mappings:` musí vyjít na **právě jeden** scope na každé straně. Když jich vyjde
víc, pár nevznikne: **jen strana s víc než jedním zásahem** jde do `unmatched` s
`ambiguous` a zmizí z poolu nastálo — osamělý scope na druhé straně v poolu zůstává pro
další pravidla. Na rozdíl od automatických pravidel se tahle nejednoznačnost **nesleduje
dál** a nemůže ji vyřešit ani pozdější pravidlo, ani zápočet nespárovaného soupeře:
`ambiguous` je tu konečný důvod. To platí i pro ruční pravidlo, které je nejednoznačné
samo v sobě (víc scopů na jedné straně matchuje jeden zápis) — takové scopy skončí
v NESPAROVANO s `ambiguous` místo aby je pohltilo vyloučení „cizí vlny" ve step běhu
(`engine.py`, viz [top-level.md](top-level.md#enginepy--orchestrace-vyhodnocení)):
rozbité ruční pravidlo tak zůstane vidět, ne potichu vyřazené.

---

## `mapping.py` — `mapping.yml`

### `Selector`

Frozen dataclass s `description`, `service_type`, `interface`, `routing_instance` (E5,
schema 15). `matches(scope)` je logický **AND** přes vyplněná pole; nevyplněná se ignorují.
Prázdný selektor je odmítnutý při načítání (`ValueError`), protože by matchoval všechno —
hláška teď jmenuje i čtvrté pole: `prazdny selektor v mapping.yml - uved description,
service_type, interface nebo routing_instance`. Platí pro `mappings` i `ignore`.

`interface` se porovnává proti `scope.selectors.interfaces`, tedy proti **logické jednotce**.
Napsat `ge-0/0/2` nezasáhne pět služeb, které přes ten port jedou — nezasáhne nic. Je to
záměr: u `mappings` musí pravidlo vyjít na právě jeden scope na každé straně, a mít
u `ignore` opačnou sémantiku téhož zápisu by bylo matoucí.

`routing_instance` se porovnává proti `scope.selectors.routing_instances` (`in`, ne
rovnost — scope může nést víc RI). Rozlišuje služby se stejným popisem v různých VRF, což
`description` samo neumí — typický případ je stejná `description` na CPE portu, který
migrace přesune do jiné VRF.

### `MappingRule` a `Mapping`

`MappingRule` = `baseline` selektor + `subject` selektor + volitelná `note` (jen dokumentace,
kód ji nepoužívá). `Mapping` drží seznam pravidel a seznam `ignore`; `is_ignored(scope)`
odpovídá na dotaz matcheru.

### `load_mapping()` / `empty_mapping()`

`load_mapping()` čte YAML a validuje: pravidlo musí mít `baseline` i `subject`, jinak
`ValueError` s cestou k souboru. `empty_mapping()` vrací prázdné mapování — používá se
vždy, když uživatel `--mapping` nezadá, takže matcher nemusí řešit `None`.

Formát souboru a příklady: [../reference.md](../reference.md#3-mappingyml).
