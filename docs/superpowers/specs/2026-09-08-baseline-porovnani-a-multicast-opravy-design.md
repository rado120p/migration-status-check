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
