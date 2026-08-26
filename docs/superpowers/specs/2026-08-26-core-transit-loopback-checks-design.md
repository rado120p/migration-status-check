# Core: rozdělení na transit a loopback + protokolové checky — design

Datum: 2026-08-26

## Cíl

Služba typu Core dnes hází do jednoho pytle tranzitní rozhraní i lo0.0.
Obě role mají dostat vlastní sadu testů:

- **Core transit** (ge/xe/et/ae s family iso/mpls): stávající interface
  checky (stav, chyby, optika, traffic) + nově IS-IS adjacency, IS-IS
  interface, LDP neighbor, PIM neighbor, MPLS interface a BFD session.
- **Core loopback** (lo0.\*): stávající interface stav/admin +
  `aggregate_route_status` (zůstává z vlny 2026-08-19) + nově IS-IS
  interface (varianta s povinným Passive), IS-IS overview (overload bit)
  a BGP checky nad interními peery, kteří se na lo0.0 nově mapují.

## Uzavřená rozhodnutí (brainstorm 2026-08-26, nerelitigovat)

- Rozlišení rolí přes **`service_subtype`** (`"transit"` / `"loopback"`),
  ne přes konvenci jmen v checcích ani nový service_type.
- Interní BGP peer se pozná z explicitního **`type internal|external`**
  na group/neighbor; bez příkazu fallback na porovnání peer-as ==
  local-as.
- **LDP neighbor je na tranzitním Core rozhraní očekávaný vždy** —
  chybí-li, je to FAIL. **PIM jen tam, kde je rozhraní pod
  `protocols pim`** — záměr se parsuje do inventory
  (`protocol: ["pim"]`); bez záměru check mlčí (žádné řádky, ne SKIP).
- **BFD session je na tranzitním Core rozhraní očekávaná vždy** (jako
  LDP) — žádné parsování ISIS BFD konfigurace. BFD páry k BGP sessions
  interních peerů se v této vlně neřeší.
- `isis_interface_info` je **jeden check pro obě role**: loopback
  vyžaduje Passive flag na level 2, transit vyžaduje jeho absenci
  (pasivní tranzitní port by nesestavil adjacency).
- **Agregáty zůstávají na lo0.0** — seznam loopback checků je aditivní,
  nic z vlny 2026-08-19 se nerozvazuje.
- Rozhraní chybějící v IS-IS adjacency výpisu = FAIL „chybí v outputu"
  (stejná filosofie jako MPLS).
- Vše se dělá pro **obě platformy** (MX i EVO parser/collector),
  fixtures z živé laborky.

## 1. Inventory a parser

### Klasifikace Core

`_classify` v obou parserech vrací pro rozhraní s family iso/mpls:

- `("Core", "loopback", ...)` pro `lo0.*`,
- `("Core", "transit", ...)` pro ostatní.

`INVENTORY_SCHEMA_VERSION` 6 → **7** (subtype je odvozené datum, staré
inventory se musí přegenerovat — konzistentní s předchozími bumpy).

### PIM záměr

Parser čte `protocols pim interface <name>` (globálně i per
routing-instance, kde existuje) a připojí `"pim"` do **existujícího**
pole `ServiceEntry.protocol` odpovídající služby. Žádné nové pole.
PIM neighbor je očekávaný právě tehdy, když má rozhraní `"pim"`
v `protocol`.

### Interní BGP peeři → lo0.0

BGP parsování dostane per-neighbor příznak `internal: bool`:

1. explicitní `type internal|external` na neighbor, jinak na group,
2. bez příkazu fallback: peer-as == local-as (s respektem ke group/
   neighbor `local-as` overridům) ⇒ internal.

`_assign_bgp_neighbors` dostane druhou větev: interní peeři se
přiřazují do `bgp_neighbor` záznamu Core **loopback** (ne Internet/
IPVPN). Dva explicitně ošetřené důsledky:

- Docstring `_assign_bfd` varuje, že rozšíření filtru
  `_assign_bgp_neighbors` vyžaduje gate — ten gate se přidá: interní
  peeři na lo0.0 **nedostávají** BFD záměry.
- Interní peeři zmizí z NEZAŘAZENO, protože nově mají vlastníka.

### Stavba scopů

`ScopeKey.service_subtype` už builderem protéká, takže lo0.0 scope a
tranzitní scopy jsou rozlišitelné bez zásahu do builderu (nad rámec
selektorů níže).

## 2. Sběr a snapshot

Šest nových fact areas, každá vlastní collector v existujícím registry
vzoru (přes `rpc_calls`, takže `record` CLI je sbírá automaticky):

| Fact area | RPC | Klíčováno |
|---|---|---|
| `isis_adjacency` | `get-isis-adjacency-information(detail=True)` | jméno rozhraní |
| `isis_interface` | `get-isis-interface-information(detail=True)` | jméno rozhraní |
| `isis_overview` | `get-isis-overview-information` | jediný dict (device-global) |
| `ldp_neighbor` | `get-ldp-neighbor-information(detail=True)` | jméno rozhraní; `lo0.*` záznamy se zahazují už při parsování |
| `pim_neighbor` | `get-pim-neighbors-information` | jméno rozhraní |
| `mpls_interface` | `get-mpls-interface-information` | jméno rozhraní |

Ekvivalentní CLI příkazy: `show isis adjacency detail`,
`show isis interface detail`, `show isis overview`,
`show ldp neighbor detail`, `show pim neighbors`, `show mpls interface`.

Payload drží jen to, co checky čtou:

- `isis_adjacency`: `system_name`, `state`, `ip_address`,
  `ipv6_address`,
- `isis_interface`: `levels` s per-level `passive` flagem,
- `isis_overview`: `overload_enabled: bool`
  (přítomnost `<isis-overload-enabled/>`),
- `ldp_neighbor` / `pim_neighbor`: `neighbor_address`,
  `uptime_seconds` (celé číslo z `junos:seconds` — display string se
  formátuje až v reportu, nikdy se neparsuje zpět),
- `mpls_interface`: `state`.

`SCHEMA_VERSION` snapshotu 10 → **11**, šest nových areas do
`FACT_AREAS`. `runs/mig01` byl re-capture dlužen už od schema 6 — teď
je bezpodmínečný.

**Sémantika absence se zachovává při sběru:** rozhraní chybějící ve
výpisu RPC prostě nemá klíč v area. Co absence znamená, rozhoduje
**check** (FAIL pro ISIS/LDP/MPLS/BFD na transitu, gate na
`protocol: pim` pro PIM). Collector nikdy nesyntetizuje „Down" řádky —
stav se nefabuluje.

Jeden collector na protokol (ne jeden mega-collector) — odpovídá
stávajícímu layoutu; otevřený refaktor sdíleného multi-RPC helperu je
může sloučit později.

## 3. Scoping a vazba checků

### Selekce

Pět per-interface areas se v `Scope.select()` vybírá přes
`matches_interface`, stejně jako `interfaces`/`optics`.
`isis_overview` je device-global a dostane ho **jen** Core scope se
subtype `loopback` (ostatní prázdný dict). Device scope propouští vše.

### BFD podle rozhraní

`Scope.select()` dostane druhou BFD cestu: pro Core-transit scope se
BFD sessions vybírají podle rozhraní session, vedle stávající cesty
přes peer adresu pro zákaznické služby.

**Precondition k ověření na začátku implementace:** BFD collector
zaznamenává rozhraní session. Pokud ne, collector to pole doplní —
z RPC, ne odvozením.

### Vazba checků

`Check` dostane volitelný
`service_subtypes: ClassVar[frozenset[str] | None]` vedle stávajícího
`service_types`; `applies_to` vyžaduje obojí, je-li nastaveno.

- **Core transit** (`service_types={"Core"}`, subtype `transit`):
  nové checky `isis_adjacency_state`, `isis_interface_info`,
  `ldp_neighbor_state`, `pim_neighbor_state`, `mpls_interface_state`,
  `bfd_transit_state` + stávající interface stav/chyby/optika/traffic
  checky beze změny.
- **Core loopback** (subtype `loopback`): `isis_interface_info`
  (varianta s povinným Passive), nový `isis_overview` check; stávající
  interface-state a BGP checky se rozjedou přes selektory z bodu 1;
  `aggregate_route_status` zůstává beze změny.
- Subtype gate drží nové protokolové checky mimo zákaznické služby.

### NEZAŘAZENO

Žádné nové unassigned sekce. Co mluví ISIS/LDP/MPLS, má family
iso/mpls, a je tedy parserem klasifikováno jako Core — bezvlastnický
případ neexistuje. Kdyby se v laborce objevila adjacency na ne-Core
rozhraní, je to klasifikační chyba a má se ukázat jako chyba, ne
zamaskovat report sekcí.

## 4. Checky — evaluace a řádky

Všechny řádky drží stávající gramatiku reportu
(`STATUS | popisek : hodnota`). V baseline režimu baseline pravidlo
nahrazuje no-baseline pravidlo všude, kde baseline řádek existuje;
kde baseline záznam chybí úplně, platí no-baseline pravidlo
(řeč přítomnosti, dle konvence z agregátní vlny).

### `isis_adjacency_state` (transit) — per rozhraní scopu

Rozhraní chybí v adjacency výpisu → jediný řádek
`FAIL | IS-IS adjacency state : chybí v outputu`.

Jinak:

| Pole | Bez baseline | Proti baseline |
|---|---|---|
| `system-name` | INFO | WARN při rozdílu, jinak PASS |
| `adjacency-state` | PASS pokud `Up`, jinak FAIL | PASS při shodě s baseline; WARN pokud teď `Up`, ale v baseline Down; jinak FAIL |
| `ip-address` | PASS pokud přítomna | WARN při rozdílu |
| `global-ipv6-address` | PASS pokud přítomna | WARN při rozdílu |

```
 PASS|WARN      | IS-IS neighbor name      : <system-name>
 PASS|FAIL|WARN | IS-IS adjacency state    : <adjacency-state>
 PASS|WARN      | IS-IS neighbor IPv4 address : <ip-address>
 PASS|WARN      | IS-IS neighbor IPv6 address : <global-ipv6-address>
```

### `isis_interface_info` (transit + loopback, jeden check, role-aware)

- Level 2 přítomen → PASS řádek; level 1 přítomen → FAIL řádek.
- Passive flag na level 2: **loopback** ho vyžaduje (přítomen = PASS,
  chybí = FAIL); **transit** vyžaduje absenci (přítomen = FAIL,
  chybí = PASS).
- Rozhraní chybí v ISIS interface výpisu → FAIL „chybí v outputu".

### `ldp_neighbor_state` (transit) — očekávaný vždy

- Žádný neighbor na rozhraní → `FAIL | LDP neighbor status : Down`.
- Jinak: PASS pokud `uptime_seconds > 0`, hodnota `Up for <uptime>`;
  adresa INFO (baseline: WARN při rozdílu).

```
 PASS|FAIL | LDP neighbor status  : Up for <ldp-up-time> | Down
 INFO|WARN | LDP neighbor address : <ldp-neighbor-address>
```

### `pim_neighbor_state` (transit) — gate na `"pim"` v `protocol`

- Bez PIM záměru → žádné řádky (ticho, ne SKIP).
- Záměr + žádný neighbor → `FAIL | PIM neighbor status : Down`.
- Jinak: PASS pokud `uptime_seconds > 0`; adresa INFO
  (baseline: WARN při rozdílu).

```
 PASS|FAIL | PIM neighbor status  : Up for <pim-neighbor-uptime> | Down
 INFO|WARN | PIM neighbor address : <pim-neighbor-address>
```

### `mpls_interface_state` (transit)

PASS pokud `Up`, FAIL pokud `Dn`, FAIL „chybí v outputu" při absenci.
Stejné pravidlo s baseline i bez.

```
 PASS|FAIL | MPLS interface status : Up | Down | chybí v outputu
```

### `bfd_transit_state` (transit) — očekávaná vždy

- Žádná session na rozhraní → FAIL `Down`.
- Session `Up` → PASS (s uptime hodnotou, je-li k dispozici),
  jinak FAIL.

Přebírá stávající BFD slovník řádků; je to **samostatný check** vedle
`checks/bfd.py`, aby záměrová zákaznická logika zůstala nedotčená.

### `isis_overview` (loopback)

`overload_enabled` → `WARN | IS-IS overload bit : nastaven`, jinak
`PASS | IS-IS overload bit : nenastaven`. Baseline nic nepřidává.

### Formát uptime

Hodnoty se renderují z `junos:seconds` (existující formátovač trvání,
pokud v kódu je; jinak malý sdílený helper).

## 5. Testy a ověření

### Fixtures z laborky, obě platformy

Šest nových RPC se nahraje na živé laborce (MX i EVO) přes `record`
CLI — collectory se registrují přes `rpc_calls`, takže capture je
zadarmo, jakmile existují. Fixtures do stávajících per-platform stromů
`tests/fixtures/rpc/`. Tvary, které laborka na povel nevyrobí
(adjacency Down, nakonfigurovaný level 1, chybějící Passive na lo0,
nastavený overload bit), se ručně odvodí ze zachycených souborů a
okomentují — žádná vymyšlená XML struktura, jen editace hodnot na
změřených tvarech.

### Vrstvy testů

- **Parser:** klasifikace Core subtype (lo0 vs transit),
  `protocols pim` → `protocol: ["pim"]`, BGP `type internal` +
  AS-fallback, interní peeři na lo0.0 záznamu — každé pro obě
  podtřídy parseru.
- **Collector:** tvar payloadu každé area; absence rozhraní ⇒ absence
  klíče (nikdy syntetizovaný Down).
- **Check:** každé pravidlo z bodu 4, varianty bez baseline i proti
  baseline, včetně WARN přechodů (teď Up / dřív Down, rozdílné
  adresy/system-name) a PIM ticha bez záměru.
- **Scoping:** subtype-gated `applies_to`, `isis_overview` jen do
  loopback scopu, BFD cesta přes rozhraní na transitu, regrese že
  zákaznické scopy žádnou novou area nevidí.
- **Schema:** verzní gate testy inventory 6→7 a snapshot 10→11,
  zrcadlící existující.

### Závazné konvence (z projektové paměti)

- Každý mutant se přeměří proti finální sadě fixtures; docstring
  tvrdící výsledek mutanta se ověřuje spuštěním toho mutanta.
- Fixture `conftest` hardcoduje `active: True` — nic tady nesmí
  spoléhat na deaktivační tvary bez současné úpravy facts.

### Dokumentace a dokončení

- `docs/cs` + `docs/en` (checks.md, collectors.md, reference.md)
  rozšířit ve stejné vlně.
- `runs/mig01` přesnímat na schema 11 na konci.
