# Porovnání proti baseline: „beze změny" = PASS, sloupec ZMENA, multicast opravy — design

Datum: 2026-09-08

## Cíl

Produkční test (bulk single-device pre/post po upgradu softwaru, 2026-09-08)
ukázal, že skoro každý check rozhoduje FAIL/WARN jen ze stavu subjektu a
baseline čte jen do sloupce ZMENA. Stav, který byl v baseline stejný
(peer down, ARP prázdné, neighbor chybí, routa chybí…), tak svítí
FAIL/WARN, ačkoliv se migrací nic nezměnilo. K tomu sloupec ZMENA tiskne
„bez baseline" na řádcích, které baseline hodnotu nenesou jen proto, že
ji check zapomněl vyplnit nebo ji z definice neporovnává, a „bylo X" tam,
kde `value` a `baseline_value` mají různý slovník.

Tři vlny v jedné větvi `baseline-porovnani-2026-09-08`:

1. **Infrastruktura** — collector rout (qualified-next-hop), nový
   `Outcome.UNCHANGED`, značka „neporovnává se", failed collectors baseline
   do kontextu, překlíčování zbylých fact areas podle mappingu, vyloučení
   subtypů (ping/ARP/ND mimo multicast).
2. **Adopce v checkách** — každá BROKEN/DEGRADED větev, kde jde stav
   porovnat, umí UNCHANGED; slovníky `value`/`baseline_value` sjednocené.
3. **Multicast** — WARN místo FAIL pro chybějící S,G a sender bez
   receiveru; baseline hodnoty a značky v c-multicast a core inet.2 řádcích.

## Zjištění z produkce (kořenové příčiny, ověřeno 2026-09-08)

| Symptom | Příčina |
|---|---|
| inet.2 statika s `next-hop` + `qualified-next-hop`: upstream „neni mezi via inet.2 routy (ge-0/0/1.100)", static routa PASS s „neni aktivni" | Junos vrací **dva `<rt-entry>`** pod jedním prefixem (preference 5 aktivní `*`, preference 7 neaktivní). `collectors/routes.py:148` přiřazuje `prefixes[prefix] = {...}` v cyklu přes `rt-entry`, poslední vyhrává → `active=False`, hopy jen z neaktivního záznamu. Ověřeno na MX1-POP1 (10.11.11.1/32, fixture `tests/fixtures/rpc/junos/routes_qnh.xml`). |
| C-multicast status PASS + „bez baseline" | `mvpn_cmulticast_status` je BOTH, OK řádek (`multicast.py:657-661`) `baseline_value` nenastavuje. |
| Core inet.2 řádky „bez baseline" / „bylo (S,G)" | Souhrnný řádek a per-stream řádky (upstream, downstream, rate, uptime) baseline hodnotu nenesou; „Existuje S,G pro X" má `value` větu a `baseline_value` seznam (S,G) — nikdy se nerovnají. |
| FAIL/WARN při shodě baseline a subjektu (10 hlášených + další) | Viz katalog níže. |

## Uzavřená rozhodnutí (2026-09-08, nerelitigovat)

### R-3: Shodný špatný stav v baseline i subjektu je PASS

- Nový `Outcome.UNCHANGED` → `Status.PASS` bez ohledu na severity.
  Řádek **dál říká, co je rozbité** (message i `value`), jen přidá
  „, stejne jako v baseline". Sloupec ZMENA tiskne
  `beze zmeny (chyba uz v baseline)`, ne prázdno — jinak by se řádek
  nedal odlišit od zdravého PASS.
- `run_check()` u UNCHANGED zapíše `details["unchanged_since_baseline"] = True`
  (konstanta `UNCHANGED_SINCE_BASELINE` v `models/result.py`, stejný vzor
  jako `SKIPPED_BECAUSE`). Renderer i GUI čtou značku, ne text.
- Souhrn nese nový aditivní klíč `pass_unchanged` (počet takových řádků);
  textový report ho vypíše za řádkem `Checky:` jako
  `  z toho N PASS beze zmeny proti baseline (chyba uz pred migraci)`,
  jen když N > 0. Bez něj by operátor v „PASS 42" šest zděděných chyb
  neviděl.
- **Podmínka (kvůli migraci starý → nový box):** UNCHANGED smí vzniknout
  jen když baseline **tu oblast změřila** - a to je pozitivní důkaz, ne
  pouhá absence chyby. `CheckContext` dostane `baseline_collectors` (celý
  `baseline.capture.collectors`, tedy `{collector: {"status", "message"}}`)
  a metodu `baseline_measured(area) -> bool`
  (`has_baseline and baseline_collectors.get(area, {}).get("status") ==
  "ok"`; pro `"ping"`, který vlastní collector nemá, `bool(baseline["ping"])`
  - dukazem jsou probe zaznamy scopu). Baseline bez záznamu daného
  collectoru (starý snapshot, `--collectors` výběr) tak nevypadá jako
  změřená stejně jako selhaný collector - obojí je „nezměřeno" → dnešní
  FAIL/WARN zůstává. Stav se nefabuluje.
- „Chybí v obou" (neighbor/routa/ARP/session není ani v baseline ani
  v subjektu, oblast změřena) **je** UNCHANGED.
- Výjimky, kde shoda s baseline PASS **není**: `deactivation_state`
  a deaktivační větve v `routes.py`/`bgp.py` (deaktivovaný prvek je
  nález sám o sobě, rozhodnutí 2026-08-04), `traffic_ceased` (chce změnu),
  `sender site bez vzdaleneho receiveru` (DEGRADED z rozhodnutí 2026-09-07
  zůstává WARN).

### R-4: Značka „neporovnává se" místo prázdné baseline hodnoty

- `Finding.compared: bool = True`. Check, který hodnotu měří, ale proti
  baseline ji z definice neporovnává (multicast upstream/downstream/uptime,
  souhrnné řádky, „uptime nezmereno"), dá `compared=False`. `run_check()`
  to přenese do `details["compared"] = False` (konstanta `NOT_COMPARED`).
- `change_text()` (view.py i view.js) vrátí `""` pro `compared=False`.
  „bez baseline" tak zůstane jen tam, kde baseline hodnota chybět **neměla**
  — tj. je to nadále signál chyby checku, ne šum.
- `baseline_value = value` jako obejití je zakázané (fabuluje baseline).

### R-5: `value` a `baseline_value` ze stejné funkce

Každá větev, která nastavuje obě, je počítá **jednou funkcí** nad
subjektovým a baseline záznamem (`_value_of(record)`), aby shodný stav
byl shodný řetězec. Sentinely (`bez session`, `Neznamy peer`,
`deaktivovan`, `chybi v outputu`) platí pro obě strany: když baseline
session chyběla, `baseline_value` je tentýž sentinel, ne stav.

### R-6: Překlíčování baseline podle mappingu pro všechny per-interface oblasti

`engine._aligned_baseline_data` dnes přejmenovává jen `interfaces`,
`evpn_mac`, `optics`. Rozšíří se o klíče slovníků `isis_adjacency`,
`isis_interface`, `ldp_neighbor`, `pim_neighbor`, `mpls_interface`,
`igmp_group` a o pole `interface` v záznamech `arp`, `nd`, `bfd`,
`evpn_esi`. Tohle byl původní důvod mappingu; bez toho R-3 na migraci
starý → nový box nikdy nenajde baseline záznam. Stejně tak
`evpn_instance`: jméno rozhraní zanořené v
`local_interfaces.entries[].name` a `irb_interfaces.entries[].name`
(klíč instance se nemění, přejmenovává se jen vnořený název — a jen
tam, kde klíč `name` v záznamu vůbec je, stav se nefabrikuje). `pim_join` a
`multicast_route` nesou jména rozhraní jen v hodnotách, které se
neporovnávají — nemění se.

### R-7: Vyloučení subtypů

`Check.excluded_subtypes: ClassVar[frozenset[str] | None]` — AND
k `service_types`, scope se subtypem v množině check nedostane.
`arp_present`, `nd_present`, `ping_reachability` vyloučí
`MULTICAST_SUBTYPES = {"multicast", "mvpn"}` (konstanta se stěhuje do
`models/scope.py`, `checks/multicast.py` ji odtud importuje).
`probes/ping.resolve_targets` multicast scopy přeskočí taky — jinak by
capture pingal dál a v produkci pálil NETCONF session na měření, které
nikdo nečte. `describe()` značku vydává, katalog profilů ji ukáže.

### R-8: Collector rout slučuje `rt-entry` per prefix

`active = any(entry.active-tag == "*")`, `next_hop`/`via` = sjednocení
přes všechny záznamy bez duplicit, hopy aktivního záznamu první. Tvar
faktu (`next_hop`, `via`, `active`, `protocol`) se nemění, `SCHEMA_VERSION`
zůstává 13. Upstream check tak vidí oba via a shoda „upstream je jeden
z nich" je PASS — přesně to, co uživatel čeká (upstream je vždy jen jeden).

### R-9: Multicast

- `multicast_forwarding_status`: „S,G neni v multicast tabulce" a sender
  „S,G je v tabulce ale nema zadny downstream" jsou DEGRADED (WARN).
  Souhrnný řádek: BROKEN jen když je aspoň jeden tvrdý FAIL; jen měkké
  → DEGRADED s textem `{n}/{total} S,G bez streamu`. Check přejde na
  BOTH kvůli R-3 (S,G chybí v obou = UNCHANGED); ostatní řádky
  `compared=False`.
- `mvpn_cmulticast_status`: OK řádek nese `baseline_value` = tentýž tvar
  (`S/32:G/32`) z baseline záznamu; chybějící záznam v obou = UNCHANGED.
  Provider tunnel řádek: `value = "{tunnel} (PE {pe})"`; porovnává se jen
  sender PE (rozhodnutí 2026-09-02) → při shodě PE `compared=False`, při
  změně `baseline_value = "PE {was_pe}"`.
- `core_multicast_forwarding`: „Existuje S,G pro X" má `value` =
  seznam (S,G) (tentýž `_labels_of`), `baseline_value` = seznam z baseline;
  „Neexistuje S,G" v obou = UNCHANGED. Souhrn, upstream, downstream,
  uptime `compared=False`. Forwarding rate: `baseline_value = "{pps} pps"`
  z baseline routy téhož klíče (S,G), když ji baseline má a pps není None;
  jinak `compared=False`. `stream_rows()` dostane `baseline_route`.
- `igmp_membership_report` / `pim_join`: BROKEN „bez reportu/joinu" je
  UNCHANGED, když baseline (změřená) neměla páry taky; `baseline_value`
  je pak tentýž sentinel (`NO_REPORT` / `NO_JOIN`).

## Katalog větví pro R-3 (co se mění ve vlně 2)

| Modul | Větev | Klíč baseline záznamu |
|---|---|---|
| `bgp.py` | state ≠ Established (:145); peer bez session (:243) | peer IP |
| `core_protocols.py` | adjacency chybí (:59) / state ≠ Up (:103) / adresa chybí (:126); LDP+PIM soused chybí (:210) / down (:237) / adresa chybí (:251); MPLS chybí (:342) / down (:357); BFD transit bez session (:424) / down (:458) | jméno rozhraní (po R-6) / peer |
| `ifaces.py` | `interface_state` admin/oper ≠ up (:135) → mode BOTH | jméno rozhraní |
| `routes.py` | routa chybí v tabulce (:173-195); neaktivní bez `active` v baseline zůstává DEGRADED | (rib, prefix) |
| `reachability.py` | ARP prázdné/incomplete, ND prázdné/unreachable, ping bez odpovědi → mode BOTH | IP / target |
| `evpn.py` | VPWS bez peerů (:173, :186), peer unresolved (:236), iface down (:117); ESI unresolved (:348), iface down (:360), DF not elected (:379); instance 0 neighbors (:419), iface down (:580), chybějící unit (:603) / IRB (:645), ESI (:708); MAC 0 v obou (:883) | RI + iface/peer/ESI |
| `bfd.py` | down (:120), bez session (:180), session mimo záměr (:110) | peer |
| `optics.py` | dark v obou (:156); alarmy (:214/:217) → mode BOTH | port + lane |
| `multicast.py` | viz R-9 | (S,G) |

Řádky, které mají `baseline_value` chybět a nejsou SKIP (→ `compared=False`
nebo doplnit): `bgp.py:337-345`, `core_protocols.py:229-233`, `:236-241`,
`evpn.py:601-607`, `:644-649`, `optics.py:132`, `ifaces.py:212-217`.

## Co se nemění

- Tvar JSON výstupu je aditivní (`details` klíče, `summary.pass_unchanged`,
  `describe().excluded_subtypes`). Schema verze snapshotu a inventory
  zůstávají (13 / 9).
- GUI checks table a report: jen `view.js` port `change_text` a značka
  v řádku; žádné nové obrazovky.
- Deaktivace, RECV a R-1/R-2 pravidla platí dál.

## Ověření

- Jednotkové testy per task (viz plány), suite zelená po každém tasku.
- Laborka: MX1-POP1 má inet.2 statiku 10.11.11.1/32 s `next-hop 10.1.2.0`
  a `qualified-next-hop 10.1.0.3 preference 7` (stream 10.11.11.1,
  232.1.1.1). Po vlně 1 musí `Upstream interface` u Core lo0.0 být PASS
  a `Staticke routy` PASS bez „neni aktivni".
- Pre/post na témže boxu (upgrade scénář) i pre starý → post nový box
  (mig01 runs) — u druhého ověřit, že po R-6 zmizí „bez baseline" u LDP/PIM/
  IS-IS řádků.

### Vlna 1 ověřena v laborce 2026-09-08 (MX1-POP1, pre/post na témže boxu)

- `Staticke routy inet.2 10.11.11.1/32`: PASS s hodnotou `10.1.0.5, 10.1.2.0`,
  bez „neni aktivni" (R-8). `Upstream interface ge-0/0/0.0`: PASS.
- Multicast scopy (`INET2-MULTICAST-RECEIVER`, dvě NGMVPN instance) nemají
  žádný ping probe ani řádky ARP/ND/Ping; ostatní Internet/IPVPN služby je
  mají dál (R-7).
- Core lo0.0 blok zatím tiskne „bez baseline" u souhrnu, upstream, downstream,
  rate a uptime a „bylo (S,G)" u řádku Existuje S,G — očekáváno, řeší vlna 3.
- Použitá inventory `runs/mig01-mx1-pop1/inventory_MX1-POP1_all.yml` je
  zastaralá (hlásí rozhraní deaktivovaná, stream přitom běží) — pro vlnu 3
  přegenerovat přes `--parse-services`.

### Vlna 2 ověřena na uložených bězích 2026-09-08

Přehráno `evaluate --detail --no-color` nad oběma uloženými pre/post páry
(Task 10). Produkční pre/post snapshoty z 2026-09-08 k dispozici nebyly.

- `runs/mig01-mx1-pop1` (`snapshot_pre_MX1-POP1_all.json` →
  `snapshot_post_MX1-POP1_all.json`): `pass_unchanged = 0` (řádek „z toho N
  PASS beze zmeny" se nevytiskl vůbec — souhrn ho tiskne jen při nenulové
  hodnotě). Sparováno 17 služeb, 0 nesparovaných v baseline, 0 nesparovaných
  v subjektu.
- `runs/mig01-ptx1-pop1` (`snapshot_pre_PTX1-POP1_all.json` →
  `snapshot_post_PTX1-POP1_all.json`): `pass_unchanged = 182`. Sparováno 19
  služeb, 0 nesparovaných v baseline, 0 nesparovaných v subjektu.

„bez baseline" v ZMĚNA sloupci (`grep " bez baseline$"` na oba výstupy),
obě sady služeb byly plně sparované, takže žádný výskyt nepatří
nesparované službě:

- `mig01-mx1-pop1`: 1 výskyt — `Multicast forwarding status` (Core lo0.0
  blok), stejný nález jako u vlny 1, řeší vlna 3.
- `mig01-ptx1-pop1`: 4 výskyty — 1× `Multicast forwarding status` (stejná
  příčina jako výše, vlna 3) a 3× `Interface errors / traffic` s hodnotou
  `mereno na L2 (...) - viz blok(y) nize` (INFO řádek u L3 části vázané
  služby, viz `docs/cs/reference.md` bod o vazbě L2+L3). Tenhle INFO řádek
  z definice nikdy baseline_value nenese (skutečné porovnání jede na L2
  bloku), takže „bez baseline" tiskne i u plně sparované služby — chování
  je předchozí (nezavedla ho vlna 2) a mimo očekávaný výčet výjimek z
  Tasku 10 briefu (multicast řádky vlny 3 + nesparované služby); zapsáno
  jako nález v `task-10-report.md`, neopravováno v rámci Tasku 10.

### Vlna 2 — fix wave, replay 2026-09-08

Fix wave po závěrečném review vlny 2 (MPLS `was_value` fabrikoval „Down"
z placeholderu `unknown`, RECOVERED gate ignoroval `unknown` baseline,
optics alarm řádky bez baseline dat portu, EVPN `same=` uznávalo dvě
různé ne-Up hodnoty za shodné, L2-vázaný INFO řádek `interface_errors`
neměl `compared=False`). Přehráno stejné trojici běhů jako u vlny 2
(`evaluate --detail --no-color`):

- `runs/migration-pop1` (`snapshot_pre_MX1-POP1_ge_0_0_2.json` →
  `snapshot_post_PTX1-POP1_et_0_0_8.json`, cross-box): `pass_unchanged = 1`
  (řádek „VL-4094 Interface … MAC count: beze zmeny (chyba uz v baseline)").
  Sparováno 8 služeb, 0 nesparovaných v baseline, 0 nesparovaných v subjektu.
- `runs/mig01-mx1-pop1`: `pass_unchanged = 0` (řádek se nevytiskl). Sparováno
  17 služeb, 0/0 nesparovaných.
- `runs/mig01-ptx1-pop1`: `pass_unchanged = 182`. Sparováno 19 služeb, 0/0
  nesparovaných.

„bez baseline" v ZMĚNA sloupci, seskupeno podle CHECK labelu:

- `runs/migration-pop1`: `IGMP membership report` (1×), `EVPN neighbor`
  (1×, nová adresa 150.0.0.11 jen v subjektu), `ARP (10.40.95.254/24)` (1×),
  `ARP (10.40.94.254/24)` (1×) — nové ARP adresy od baseline, přijímáno.
- `runs/mig01-mx1-pop1`: `Multicast forwarding status` (1×) — Core lo0.0
  blok, vlna 3.
- `runs/mig01-ptx1-pop1`: `Multicast forwarding status` (1×) — stejná
  příčina, vlna 3.

Oproti vlně 2 zmizely: `interface_optics_alarms` řádky portu, který
baseline vůbec nezměřila (dřív by v takovém běhu tiskly „bez baseline" na
každém alarm/OK řádku — R-4 fix z Tasku 1/3 této vlny), a 3× `Interface
errors / traffic` L2-vázaný INFO řádek u `mig01-ptx1-pop1`, který vlna 2
zapsala jako otevřený nález (`task-10-report.md`) — Task 6 mu doplnil
`compared=False`, takže se v aktuálním replay už netiskne.

Zbývající výskyty jsou přesně očekávané kategorie (multicast řádky vlny 3
+ nové ARP/ND/EVPN neighbor adresy od baseline). Žádné jiné „bez baseline"
výskyty nalezeny nebyly.

### Vlna 3 ověřena v laborce 2026-09-08 (čerstvá inventory, MX1-POP1 + PTX1-POP1, pre/post na témže boxu)

- MX1-POP1: 188 PASS / 12 WARN / 0 FAIL, `pass_unchanged` 3; PTX1-POP1: 425 PASS / 4 WARN / 0 FAIL,
  `pass_unchanged` 177 (virtuální optika s alarmy na všech lanech v obou snímcích).
- **Žádný řádek „bez baseline"** na žádném z boxů; žádné „bylo (S,G)".
- Core lo0.0 (MX): stream 232.1.1.1 v laborce v tu chvíli neexistoval — souhrn i řádek
  „Neexistuje S,G" jsou PASS `beze zmeny (chyba uz v baseline)` (UNCHANGED včetně souhrnu).
- PTX: Internet multicast (et-0/0/8.11) i obě MVPN instance — IGMP report, PIM join, Stream,
  Upstream, Forwarding-rate, C-Multicast status, Provider tunnel `... (PE 150.0.0.13)` vše PASS
  s prázdným ZMENA; Core inet.2: `Existuje S,G` hodnota `(10.11.11.1, 232.1.1.1)`, upstream
  et-0/0/0.0 PASS, rate `6 pps`.
- WARN jen deaktivace (MX má služby CPE13 a multicast receivery v konfiguraci deaktivované —
  potvrzeno čerstvou inventory, není to zastaralý soubor) a pokles provozu na dvou tranzitech.
- Zbývající šum „bylo …": ping (RTT se liší při každém měření) a LDP `Up for …` (uptime roste)
  — řeší fix wave vlny 3 (`compared=False`, když je ztráta / stav shodný).
- PTX NETCONF na portu 830 odmítal spojení (SSH 22 v pořádku) — capture přes `--ssh-port 22`.

### Po fix wave vlny 3 (tytéž snímky, 2026-09-08 večer)

- MX1-POP1 i PTX1-POP1: **0 řádků „bez baseline"**, souhrny beze změny (188/12/0 a 425/4/0,
  `pass_unchanged` 3 a 177).
- Jediné zbývající „bylo …" jsou `Interface traffic in/out` s reálnou deltou v procentech
  (záměr od vlny 5). Ping (RTT) a LDP/PIM (`Up for …`) už ZMENA netisknou — porovnává se
  ztrátovost, resp. stav Up/Down (`compared=False` při shodě).
