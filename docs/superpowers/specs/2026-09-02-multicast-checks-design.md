# Multicast: IGMP, multicast forwarding a MVPN-IGMP checky — design

Datum: 2026-09-02

## Cíl

První vlna multicastových testů pro tři role:

- **Internet, subtype `multicast`** — zákaznické rozhraní pod
  `protocols igmp`: IGMP membership report + multicast forwarding status.
- **Core, subtype `loopback`** — statiky v `inet.2` patří lo0.0 a ke každé
  se očekává multicast stream se zdrojem uvnitř jejího prefixu: inet.2
  statiky (stávající check) + core multicast forwarding.
- **IPVPN, subtype `mvpn-igmp`** — IRB v MVPN VRF s přímo připojenými IGMP
  receivery (L2 port v globální bridge-domain / vlan): IGMP membership
  report + multicast forwarding status + C-multicast / provider tunnel.

Vše pro obě platformy (MX i EVO), fixtures z živé laborky.

## Uzavřená rozhodnutí (brainstorm 2026-09-02, nerelitigovat)

- **Detekce subtype:** Internet + `igmp` v protokolech ⇒ `multicast`;
  IPVPN + `igmp` v protokolech + `mvpn` v protokolech instance ⇒
  `mvpn-igmp`. IGMP bez `mvpn` zůstává `IPVPN, None`. Subtype je aditivní
  — stávající checky Internet/IPVPN běží dál.
- **Všechny globální `inet.2` statiky patří Core lo0.0** bez ohledu na
  next-hop (stejný princip jako globální agregáty z vlny 2026-08-19).
  `inet.2` uvnitř VRF se neočekává; zůstává na dnešním subnet pravidle.
- Box bez `inet.2` statik ⇒ na lo0.0 **žádné multicast řádky** (ticho, ne
  SKIP) — gate na záměr jako u PIM.
- **Upstream interface:**
  - Core loopback: striktně — upstream musí být **jeden z `via`** rozhraní
    odpovídající `inet.2` statiky změřených route collectorem (ECMP /
    qualified-next-hop dávají víc `via`). Při více pokrývajících prefixech
    vyhrává nejdelší.
  - Internet/multicast: prefix `ge-`, `xe-`, `et-`, `ae`.
  - IPVPN/mvpn-igmp: prefix `lsi.` nebo `vt-`.
  - Řádek `Upstream interface` se renderuje ve všech třech blocích.
- **Chybějící IGMP report kaskáduje:** IGMP množina definuje očekávané
  streamy; bez ní jsou forwarding i MVPN řádky SKIP (varianta 1, ne
  nezávislé hledání downstream rozhraní v tabulce).
- **Porovnání proti baseline** jen tam, kde má smysl:
  - množina (S,G) z IGMP: WARN při rozdílu;
  - množina (S,G) na inet.2 prefix (Core): WARN při rozdílu;
  - provider tunnel: WARN při změně **sender PE adresy** (první adresa za
    `P2MP:`), ne celého řetězce — tunnel id se může přesignalovat;
  - **upstream rozhraní se neporovnává nikde** (lsi.X číslo se změní
    téměř vždy), stejně tak stream/downstream řádky, forwarding rate
    a uptime.
- **Služba MVPN-IGMP je IRB.** Přístupový L2 port v globální
  bridge-domain / vlan dostává jen inventory vazbu (`l2_interface`) a
  poznámku v hlavičce bloku — žádné checky na L2 portu v této vlně,
  `show igmp snooping membership` odloženo (duplikuje pohled z IRB).
- **Struktura (varianta A):** tři collectory (jedno RPC = jeden collector),
  čtyři checky; forwarding logika rozdělená na IGMP-řízený check
  (Internet + IPVPN, role-aware upstream pravidlo) a inet.2-řízený check
  (Core loopback). Sdílí se jen renderování řádků per (S,G).
- **Per-stream group header:** řádky každého (S,G) sedí pod vlastní
  skupinou `   -- (S, G)`, aby při více streamech nebyly `Forwarding-rate`
  řádky nejednoznačné.
- Route uptime je vždy INFO (v příkladech uživatele PASS, pravidlo říká
  INFO — platí pravidlo).

## 1. Inventory a parser

### Záměr IGMP

`_parse_global_protocol_interfaces("igmp")` vedle stávajícího `pim`
(`parsers/core.py` ~410): `protocols igmp interface <name>` přidá `"igmp"`
do `protocol` odpovídající služby přes existující
`global_protocols_by_interface`. `protocols mvpn` uvnitř VRF už dnes
protéká přes `instance.protocols` → `_collect_protocols`; žádná změna.
`routing-options multicast` se neparsuje (nic ho nečte).

### Detekce subtype

V `_detect_service` po určení typu:

- větev Internet: `"igmp" in protocol_set` ⇒
  `("Internet", "multicast", "high", reasons + ["Rozhraní je pod protocols igmp."])`,
  jinak beze změny `("Internet", None, "medium", ...)`.
- větev IPVPN: `"igmp" in protocol_set and "mvpn" in instance.protocols` ⇒
  `("IPVPN", "mvpn-igmp", "high", reasons + ["Rozhraní je pod protocols igmp a instance má protocols mvpn."])`,
  jinak beze změny.
- Core beze změny.

### inet.2 statiky → lo0.0

`_route_matches_service`: routa s `route.rib == "inet.2"` (instance
`None`) se mapuje **jen** na `service_type == "Core" and interface ==
"lo0.0"`, ve stejné větvi jako globální agregáty, a nikdy na tranzitní
rozhraní přes subnet next-hopu. `rib_instance("inet.2")` vrací `None`,
takže RI podmínka sedí. `<instance>.inet.2` do téhle větve nespadá a
zůstává na subnet pravidle.

Stávající `static_route_status` pak vykreslí `inet.2 10.11.11.1/32 :
10.1.1.2` ve skupině „Staticke routy" na bloku lo0.0 bez další změny;
measured `via` pro inet.2 už route collector sbírá
(`get_route_information(protocol=static)` vrací všechny tabulky).

### L2 strana IRB

`InterfaceService` / `ServiceEntry` dostane pole
`l2_interface: list[str]`. Pro rozhraní `irb.*` parser najde
bridge-domains / vlans, jejichž `routing-interface` (MX) nebo
`l3-interface` (EVO) je jméno IRB, a naplní `bridge_domain`,
`customer_vlan` a `l2_interface` (access porty těch domén). Platí pro
každý IRB, ne jen MVPN — je to inventory, ne multicast logika.

Text report: hlavička bloku IRB služby dostane poznámku `L2: <l2_interface,
...>` ve stejném stylu jako poznámka z EVPN linkeru. Selektory scopu se
nemění (L2 port se do IRB scopu nepřitahuje).

### Schema

`INVENTORY_SCHEMA_VERSION` 7 → **8**. Subtype je odvozené datum; staré
inventory by na pre straně nesly `None`, matcher by párovací pravidlo
`description+type+subtype` přeskočil a baseline scope by `applies_to`
z nových checků vyřadil. Oba parsery (MX i EVO) se mění v zámku, testy
parametrizované nad oběma.

## 2. Sběr a snapshot

Tři nové fact areas, jeden collector na RPC, obě platformy (shodné RPC
na MX i EVO — potvrdí první živý `record`):

| Fact area | RPC | Klíčováno |
|---|---|---|
| `igmp_group` | `get-igmp-group-information` | jméno rozhraní → seznam `{source, group}` |
| `multicast_route` | `get-multicast-route-information(extensive=True, instance="all")` | jméno instance → `"source,group"` → payload |
| `mvpn_instance` | `get-mvpn-instance-information(inet=True)` | jméno instance → `{c_multicast: [...]}` |

Ekvivalentní CLI: `show igmp group`, `show multicast route instance all
extensive`, `show mvpn instance inet`.

Payload drží jen to, co checky čtou:

- `igmp_group`: pseudo-rozhraní `local` se zahazuje při parsování (není
  služba). Skupiny se zdrojem `0.0.0.0` (ASM / Exclude) zůstávají se
  `source: None`, aby je check mohl vypsat jako `(*, G)`.
- `multicast_route`: `upstream_interface: str | None`,
  `downstream_interfaces: list[str]`, `forwarding_rate_pps: int`,
  `uptime_seconds: int` (z `junos:seconds`; display string se formátuje
  až v reportu), `state`, `forwarding_state`. Parsuje se jen
  `address-family INET`; INET6 se ignoruje. `multicast-instance master`
  je pod klíčem `master`.
- `mvpn_instance`: per instance `c_multicast: list[{source_prefix,
  group_prefix, provider_tunnel_id, sender_pe}]`. `provider_tunnel_id`
  verbatim; `sender_pe` = první adresa za `P2MP:`, `None` když je řetězec
  prázdný nebo `I-P-tnl:invalid`. Neighbor sekce se neukládá.

Sémantika absence: rozhraní bez IGMP skupin nemá klíč, instance bez
multicast rout nemá klíč, instance mimo MVPN výpis nemá klíč. Collector
nic nesyntetizuje.

`SCHEMA_VERSION` snapshotu 11 → **12**, tři areas do `FACT_AREAS`. Raw
capture přes stávající `record` do `igmp_group.xml`, `multicast_route.xml`,
`mvpn_instance.xml` per platforma — collectory se registrují přes
`rpc_calls`, fixtures jsou zadarmo.

**Precondition k ověření na začátku implementace:** PyEZ keyword pro
`instance all` u `get-multicast-route-information` (žádný stávající
collector instance argument nepředává). Rozhodne první živé volání; do
té doby se nic neodhaduje.

## 3. Scoping a vazba checků

### Selekce

`Scope.select()` dostane tři areas v **obou** větvích (device scope
propouští vše, service větev filtruje):

- `igmp_group`: per-interface, `matches_interface` jako `interfaces` /
  `optics` / ISIS areas.
- `multicast_route`: podle instance. Scope bez RI dostane záznam `master`,
  scope s RI záznam té RI. Dostávají ho jen service scopy typu Internet,
  IPVPN a Core/loopback; ostatní prázdný dict. Filtr per (S,G) dělá
  check, scope vybírá jen tabulku.
- `mvpn_instance`: podle `routing_instances`, stejný filtr jako
  `evpn_instance`.

### Vazba checků

| Check | service_types | service_subtypes | requires |
|---|---|---|---|
| `igmp_membership_report` | Internet, IPVPN | multicast, mvpn-igmp | igmp_group |
| `multicast_forwarding_status` | Internet, IPVPN | multicast, mvpn-igmp | igmp_group, multicast_route |
| `core_multicast_forwarding` | Core | loopback | multicast_route, routes |
| `mvpn_cmulticast_status` | IPVPN | mvpn-igmp | igmp_group, mvpn_instance |

### Párování

Se subtype na obou stranách páruje stávající high-confidence pravidlo
`description+type+subtype`. Scope id subtype nenese, id zůstávají
stabilní. Regresní test: subtype na jedné Internet službě nepřejmenuje
žádný jiný Internet scope na témže boxu (ScopeKey kolize).

### NEZAŘAZENO

Žádné nové sekce. Multicast routa v `master`, jejíž zdroj nepokrývá
žádná inet.2 statika, nebo downstream na rozhraní bez známé služby,
v této vlně v reportu není — kandidát na pozdější vlnu (viz Odloženo).

## 4. Checky — evaluace a řádky

Všechny řádky drží gramatiku `STATUS | popisek : hodnota`. Uptime je vždy
INFO, formátovaný ze sekund. Forwarding rate: PASS pokud `> 0`, jinak
FAIL; hodnota `{n} pps` v obou případech. Kde není řečeno jinak, řádek se
proti baseline neporovnává (`mode` state).

### Layout streamů

Každé (S,G) má vlastní skupinu `   -- (S, G)`; skupiny streamů následují
za nezaskupenými řádky a před skupinou „Staticke routy" (AR-37 platí).

```
 PASS | IGMP membership report        : (10.11.11.1, 232.1.1.1), (10.11.11.2, 232.1.1.2)
 PASS | Multicast forwarding status   : 2 S,G

   -- (10.11.11.1, 232.1.1.1)
 PASS | Stream                        : Stream se na et-0/0/8.11 posila
 PASS | Upstream interface            : et-0/0/0.0
 PASS | Forwarding-rate               : 6 pps
 INFO | Route uptime                  : 00:54:26

   -- (10.11.11.2, 232.1.1.2)
 ...
```

Helper pro řádky per (S,G) (label skupiny, `Forwarding-rate`, `Route
uptime`) je sdílený mezi třemi checky; upstream/downstream pravidla si
každý check drží vlastní.

### `igmp_membership_report` (Internet/multicast, IPVPN/mvpn-igmp)

- Skupiny na servisním rozhraní ze `igmp_group`, seřazené. Hodnota =
  seznam `(S, G)`; ASM položky jako `(*, G)`.
- Žádné skupiny ⇒
  `FAIL | IGMP membership report : Receiver neposila zadny IGMP membership report`.
- Baseline (`Mode.BOTH`): shodná množina ⇒ PASS; jiná ⇒ WARN, `bylo
  <baseline seznam>`; baseline bez skupin ⇒ bez porovnání (no-baseline
  pravidlo, `baseline_value` None).

### `multicast_forwarding_status` (stejné dva subtype, řízeno IGMP množinou)

- Bez IGMP skupin ⇒ jediný
  `SKIP | Multicast forwarding status : bez IGMP reportu`, žádné skupiny
  streamů.
- Souhrnný řádek `Multicast forwarding status`: PASS `{n} S,G`, pokud jsou
  všechny řádky streamů PASS/INFO; FAIL `{k}/{n} S,G nefunguje`, pokud
  nějaký stream v tabulce chybí nebo mu selže Stream/Upstream/rate řádek.
- Per (S,G), hledáno v multicast tabulce scopu:
  - není v tabulce ⇒ jediný `FAIL | Stream : S,G neni v multicast tabulce`,
    další řádky streamu se nevydávají;
  - `Stream`: PASS `Stream se na {iface} posila`, je-li servisní rozhraní
    v downstream seznamu; jinak FAIL
    `S,G je v tabulce ale stream se na {iface} neposila`;
  - `Upstream interface`: Internet vyžaduje prefix `ge-`/`xe-`/`et-`/`ae`;
    mvpn-igmp prefix `lsi.`/`vt-`. Splněno ⇒ PASS s jménem; jinak FAIL
    `S,G je v tabulce ale nema upstream interface`, hodnota = nalezené
    jméno nebo `-`;
  - `Forwarding-rate`, `Route uptime` dle pravidel výše.
- Žádné porovnání proti baseline.

### `core_multicast_forwarding` (Core/loopback, řízeno inet.2 statikami)

- Scope bez inet.2 statik v `selectors.static_routes` ⇒ žádné řádky.
- Per inet.2 prefix:
  - existuje aspoň jedna routa v `master`, jejíž zdroj leží uvnitř
    prefixu ⇒ `PASS | Multicast forwarding status : Existuje S,G pro {prefix}`;
  - jinak `FAIL | Multicast forwarding status : Neexistuje S,G pro {prefix}`
    a čtyři SKIP řádky `S,G`, `Forwarding rate packets`,
    `Upstream interface`, `Downstream interfaces` s prázdnou hodnotou
    (tvar z příkladu uživatele).
- Přiřazení zdroje: při více pokrývajících inet.2 prefixech vyhrává
  nejdelší; jedna routa se počítá jen jednou.
- Per přiřazené (S,G), ve skupině streamu:
  - `Upstream interface`: PASS, je-li upstream jedním z `via` změřených
    route collectorem pro ten inet.2 prefix (`routes["inet.2"][prefix]
    ["via"]`); jinak FAIL s nalezeným jménem. Není-li inet.2 routa
    v tabulce vůbec ⇒ `SKIP | Upstream interface : routa neni v tabulce`
    (FAIL nese `static_route_status`);
  - `Downstream interfaces`: PASS se seznamem spojeným čárkou, když je
    seznam neprázdný; jinak FAIL `Zadne downstream interfacy`;
  - `Forwarding rate packets`, `Route uptime` dle pravidel výše.
- Baseline (`Mode.BOTH`): množina (S,G) per prefix se porovnává jako
  u IGMP (WARN při rozdílu na souhrnném řádku). Nic dalšího.

Příklad PASS:

```
 PASS | Multicast forwarding status   : Existuje S,G pro 10.11.11.1/32

   -- (10.11.11.1, 232.1.1.1)
 PASS | Upstream interface            : et-0/0/0.0
 PASS | Downstream interfaces         : et-0/0/8.11, irb.2
 PASS | Forwarding rate packets       : 6 pps
 INFO | Route uptime                  : 00:54:26

   -- Staticke routy
 PASS | inet.2 10.11.11.1/32          : 10.1.1.2 via et-0/0/0.0
```

### `mvpn_cmulticast_status` (IPVPN/mvpn-igmp)

- Bez IGMP skupin ⇒ `SKIP | C-Multicast status : bez IGMP reportu`.
- Instance chybí v `mvpn_instance` ⇒
  `FAIL | C-Multicast status : instance neni v mvpn vypisu`.
- Per (S,G) z IGMP, ve skupině streamu:
  - `C-Multicast status`: PASS s `S/32:G/32`, existuje-li c-multicast
    záznam se shodným source i group prefixem; jinak FAIL
    `chybi c-multicast zaznam`;
  - `Provider tunnel`: PASS s celým `provider_tunnel_id`, je-li
    `sender_pe` parsovaný; FAIL `bez provider tunelu`, je-li prázdný nebo
    `invalid`. Baseline (`Mode.BOTH`): WARN, liší-li se `sender_pe`;
    hodnota zůstává celý řetězec, `baseline_value` baseline řetězec.

Výchozí závažnost nových checků sleduje sousední protokolové checky
(`checks/core_protocols.py`).

## 5. Testy a ověření

### Fixtures z laborky, obě platformy

Tři RPC se nahrají na MX i EVO přes `record`, jakmile collectory existují,
do `tests/fixtures/rpc/{junos,junos-evo}/`. Tvary, které laborka na povel
nevyrobí, se odvodí editací hodnot v nahraných souborech a okomentují —
žádná vymyšlená XML struktura: rozhraní bez IGMP skupiny, (S,G) chybějící
v tabulce, downstream seznam bez servisního rozhraní, forwarding rate 0,
upstream na netranzitním rozhraní, `I-P-tnl:invalid`, inet.2 statika se
dvěma `via`. Inventory fixtures obou lab zařízení se přegenerují novým
parserem (subtype a `l2_interface` jsou z konfigurace).

### Vrstvy testů

- **Parser:** záměr `igmp`; detekce `Internet/multicast` a
  `IPVPN/mvpn-igmp`; IPVPN s IGMP bez mvpn zůstává `None`; inet.2 statika
  na lo0.0 a ne na tranzitní port; `<instance>.inet.2` na subnet pravidle;
  naplnění `l2_interface` u IRB (MX `routing-interface`, EVO
  `l3-interface`). Obě podtřídy parseru.
- **Collector:** tvar payloadu každé area nad nahraným XML; `local`
  zahozen; ASM zdroj None; INET6 ignorováno; parsování `sender_pe`
  včetně prázdného a `invalid`; absence ⇒ absence klíče;
  `rpc_kwargs` per collector.
- **Check:** každé pravidlo z bodu 4, bez baseline i proti baseline,
  včetně kaskády do SKIP, výběru nejdelšího prefixu, ECMP `via`, WARN
  přechodů na množině (S,G) a sender PE, role-aware upstream pravidla.
- **Scoping:** subtype gate `applies_to`; `multicast_route` podle
  instance; `mvpn_instance` jen do RI scopů; zákaznické scopy bez subtype
  žádnou novou area nevidí; scope id beze změny při objevení subtype.
- **Schema:** verzní gate inventory 7→8 a snapshot 11→12.
- **Conformance a end-to-end:** nové collectory do `COLLECTORS`
  v conformance testu a do `COLLECTOR_NAMES` + zdravých syntetických
  faktů v `tests/conftest.py`, aby držel full-run test „žádný nevysvětlený
  FAIL/WARN".
- **Report:** per-stream skupiny před skupinou „Staticke routy", poznámka
  `L2:` v hlavičce IRB bloku.

### Závazné konvence (z projektové paměti)

- Každý mutant se přeměří proti finální sadě fixtures; docstring tvrdící
  výsledek mutanta se ověřuje spuštěním toho mutanta.
- Fixture `conftest` hardcoduje `active: True` — deaktivační tvary
  vyžadují současnou úpravu facts.
- Stav se nefabuluje: chybí-li pole v RPC, řádek se nevydá nebo je SKIP,
  nikdy se neodvozuje.

### Dokumentace a dokončení

- `docs/cs` + `docs/en` (checks.md, collectors.md, reference.md) rozšířit
  ve stejné vlně.
- `runs/mig01` přesnímat na schema 12 na konci.

## Odloženo (mimo tuto vlnu)

- Check `igmp snooping membership` na přístupovém L2 portu
  (`get-igmp-snooping-membership-information`) — duplikuje pohled z IRB.
- Port checky (stav, chyby, optika) pro access porty v globální
  bridge-domain / vlan — obecná mezera klasifikace, ne multicast.
- NEZAŘAZENO pro multicast routy bez vlastnící služby.
- `inet.2` statiky uvnitř VRF.
