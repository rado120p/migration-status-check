# Roadmap — stav větve `ipv6-a-report`

**Založeno:** 2026-07-28 · **Naposledy přepsáno:** 2026-07-29
**Větev:** `ipv6-a-report`, odbočena z `main` na `a24efc8`, 41 commitů
**Stav testů:** 424 prošlo, 1 přeskočen
**Stav větve:** opravná vlna hotová, ostré ověření proti laborce prošlo, **zbývá merge**

Závěrečná review z 2026-07-28 větev nepropustila a našla dvě rozhodnutí pro
člověka a sedm věcí k opravě. Obojí je vyřízené (2026-07-29). Tenhle soubor
teď slouží jako záznam, co se rozhodlo a proč, a co ještě zbývá.

---

## 1. Rozhodnutí, která padla

### R-1 — prázdná sekce rodiny → **varianta c**

Služba bez adresy dané rodiny nedostane **ani sekci, ani řádek**. Přesně podle
spec (řádek 497); vědomě se tím opouští pravidlo z Tasku 7, že check nemá
z reportu mizet beze stopy.

Rozhodl uživatel. Cena je zapsaná v kódu i v dokumentaci: **chybějící check je
v reportu k nerozeznání od checku, který prošel**, a nezůstane po něm stopa ani
ve strojovém výstupu. Alternativou byla prázdná sekce IPv6 u většiny služeb.

Opravou byly `arp_present` a `nd_present`, ne renderer: sekce vzniká právě
z toho, že do ní nějaký řádek patří, takže sekci potlačit a řádek nechat nešlo.

### R-2 — BGP relace, která se během migrace zlepšila → **PASS**

`Connect` → `Established` je PASS, ne WARN. Rozhodovalo se až po opravě F-1,
jak roadmapa doporučovala — teprve s ní se obě strany vykreslí symetricky:

```
 FAIL | BGP status : Connect     | bylo Established     <- regrese
 PASS | BGP status : Established | bylo Connect         <- zlepšení
```

Ukázalo se přitom, že ta větev je dosažitelná **jen** se stavem `Established`
(horší stavy odcházejí dřív), takže pokrývala právě a jen případ zlepšení.
Změna nemizí: pojmenuje ji zpráva a sloupec ZMENA.

### T9b — counter `suppressed` → **vynechat z reportu úplně**

Rozhodl uživatel: damping se v tomhle nasazení nepoužívá, takže řádek je vždy
nulový. Odpadá s ním i to, že se u `suppressed` porovnání četlo obráceně
(pokles potlačených rout je zlepšení, ne regrese) — o směru se nemusí
rozhodovat vůbec. Collector counter sbírá dál, snapshot zůstává věrným
záznamem zařízení.

---

## 2. Opraveno v této vlně

Každá položka má vlastní commit, každá začala padajícím testem.

| | co bylo špatně | doloženo |
|---|---|---|
| **F-1** + T11a | spadlá BGP relace hlásila `bez baseline`, protože se baseline dohledávala až za `continue` větve pro nefunkční stav | na `runs/ipv6`: peer `152.11.13.2` je `Established` v pre a `Connect` v post; report teď píše `bylo Established`. Mutace vracející pro režim BOTH `""` místo `bez baseline` prošla všemi 411 testy, teď padne |
| **F-9** | `_aligned_baseline_data` přejmenovávala jen `interfaces`, ne `physical_interfaces` | počet řádků `WARN 0 pps \| bez baseline` klesl na `runs/ipv6` z 22 na 8; zbylých 8 připadá na služby, které v baseline nejsou vůbec, kde je hláška správně |
| **F-6** | bez baseline byly sloupce `STARY PORT` i `NOVY PORT` prázdné u každé služby | `_identity()` teď nese `interfaces` a view po nich sáhne, když `match` není |
| **F-5** | z řádků rozhraní zmizelo jméno rozhraní, takže každý blok měl dvojice řádků se stejným popiskem a protichůdnými sloupci ZMENA | kvalifikace v duchu AR-5b. Srovnán celý modul — `InterfaceErrorsCheck` a `TrafficCeasedCheck` používaly holý název rozhraní, což je přesně to, co AR-4 odstraňovalo |
| **F-4** | hlavička sekce se doplňovala **na** šířku bloku, ale do té šířky nikdy nevstoupila | na fixture se čtyřmi rozsahy: rámeček 60 znaků, hlavička 105. Doplněno o test, který **měří všechny** řádky bloku — původní allowlist prefixů hlavičky sekcí z měření vylučoval |
| **F-3** | viz R-1 | hollow test `test_service_without_ipv6_has_no_ipv6_section` přesunut do `test_end_to_end.py` nad skutečná data; před opravou padal na reálné službě `svc:EVPN-VLAN-AWARE-INTERNET:Internet` |
| **T8** | z fixtures zmizel stav `unreachable` | mutace zužující pravidlo na `("incomplete",)` prošla všemi 420 testy, teď padne. Beze změny produkčního kódu |

Následná review vlny našla ještě dvě věci, obě opravené:

- `TrafficCeasedCheck` byl jediná produkční změna z vlny bez testu (čtyři sity
  proběhly dávkově) a jeho popisek `Utichnuti` neseděl ke svým sourozencům.
- `_assert_frame_wraps_block` porovnával nejširší řádek proti rámečku **prvního**
  bloku; u dvou služeb by užší blok měřil proti cizímu rámečku.

**Dokumentace** je srovnaná se skutečným výstupem — ukázkové bloky v CS i EN
README a `files/reporting.md` jsou přehrané rendererem, ne dopsané ručně.

---

## 3. Ostré ověření proti laborce (2026-07-29)

Čerstvý capture obou zařízení (`172.20.20.4` pre, `172.20.20.5` post), pak
`evaluate`. Heslo se načítá `eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"`
— v Bash volání je `$MIG_LAB_PASSWORD` jinak nenastavené.

| co se ověřovalo | výsledek |
|---|---|
| **F-4** — rámečky bloků | 131 změřených řádků, **0 přerůstajících** |
| **F-9** — `bez baseline` na spárovaných službách | **0**. Zbylé 4 připadají na nespárované služby, kde je hláška správně |
| **F-6** — porty v režimu bez baseline | `NOVY PORT` vyplněný u **každé** služby |
| **F-5** — jména rozhraní v popiskách | nesou je všechny řádky, včetně `irb`, `lo0`, `ae0` |
| **F-3** — rodina bez konfigurace | **0** řádků i sekcí |
| **T8** — stav `unreachable` | v živých datech se **opravdu vyskytuje** (`2001:db8::1`), takže opravená fixture odpovídá realitě |
| selhaný collector (`evpn_mac`, l2-learning na PTX neběží) | 3 řádky, všechny **SKIP**, žádný PASS — chybějící data nedají zelenou |

Dvě věci, které ověření **nedokázalo**, a proč to nevadí:

- **Služba s víc rozsahy v jedné rodině na téhle topologii není** (0 hlaviček
  sekce s čárkou), takže se F-4 živě reprodukovat nedá. Pokrývá ho fixture
  `_many_ranges_scope` — 105 znaků hlavičky proti 60znakovému rámečku.
- **Stav `"incomplete"` se v živých datech nevyskytl ani teď** (jsou tam
  `unreachable`, `delay`, `reachable`, `stale`). Předpoklad u `_usable_nd`
  zůstává zaznamenaný jako předpoklad, viz níže.

Zbývá **merge**; follow-upy níže zvlášť, ne před ním.

---

## 4. Follow-up (nebrání mergi)

### F-2 (Important, ale zatím latentní) — AR-5 podřádek s názvem RIB nikdy nevznikl

`migration_validator/reporting/view.py`, `reporting/text_report.py:_block`

AR-5 předepisuje název RIB na odsazeném podřádku pod `BGP status`. Mechanismus
podřádků v rendereru neexistuje a `_row()` `check.details["rib"]` zahazuje.
Peer se dvěma RIB se vykreslí jako dvojice řádků s týmiž popisky, název RIB
nikde. To maří smysl AR-7: operátor vidí pokles, ale ne **ve které** RIB.

Na dnešní topologii laborky nesepne — každý servisní peer má jednu RIB. Nahrané
fixtures ale obsahují peery s 11 RIB.

*Poznámka: související mezera „vyhození `active` nebo `suppressed` z `PREFIX_KEYS`
projde všemi testy" je uzavřená — `PREFIX_KEYS` je připnutý testem.*

### Drobnosti z ledgeru, které mají zůstat

| # | co | proč |
|---|---|---|
| T13a | Testy pojistky proti self-pingu dokazují „pojistka existuje", ne „platí na každý prvek" — vlastní adresa leží ve fixtures na indexu 0. Ověřeno mutací: `if index > 0 or address != source` projde | jeden přeuspořádaný fixture |
| T13b | V ND testu se očekávaný cíl rovná tomu, co by vrátil `subnet_fallback` — rozbitá ND větev by prošla přes fallback | stejná úprava fixture jako T13a |
| T1, T2a, T7a, T7b | drobné mezery v pokrytí fixtures | dávkově, jedním commitem |
| F-7 | `_row` při chybějícím `value` sáhne po `check.message`, takže do sloupce hodnot padají celé věty. V ostrém reportu je vidět např. u `interface_errors` a u SKIP řádků | opak AR-4 |
| F-8 | Souhrnný řádek počítá checky, tabulka pod ním služby — `83 PASS 32 WARN` nad 11 řádky | rozdělení podle AR-4 to znásobilo |
| F-10 | Findingy bez popisku spadnou na `check.id`, takže mezi hezkými popisky sedí `SKIP \| evpn_esi_status` | pohltí i T9a |
| F-11 | `probes/ping.py` a `checks/reachability.py` mají dvě kopie téže logiky pro link-local. Už jednou to způsobilo chybu | rozcházení je pořád na místě |
| F-12 | `filter_result` nechává původní `summary`, takže `--filter` tiskne počty za nefiltrovaný běh | starší než tahle větev |
| F-14 | Sloupec s typem služby v NESPAROVANO není odsazený, sloupec s důvodem je rozházený | kosmetika |
| F-15 | `change_text` nekouká na stav, takže i **SKIP** řádek dostane do sloupce ZMENA `bez baseline` — a zpráva vedle už říká totéž (`peer neni v baseline snapshotu`). V ostrém běhu je to 14 z 18 výskytů té hlášky | našlo ostré ověření 2026-07-29; starší než tahle větev, patří k F-7/F-10 |

*Poznámka k F-7 a F-10: po F-5 jsou obě v ostrém reportu vidět víc než dřív,
protože sousední řádky se zkvalitnily. Řeší se dohromady, ne po jedné.*

### Zahozeno (ať to nikdo neotvírá znovu)

- **T3** — chybí test na `schema_version: 1` a na řetězec `"2"`. Ověřeno
  chováním: chybějící, `1` i `"2"` shodí načtení u inventory i snapshotu. Kód
  je správně, test by byl navíc.
- **T4** — `test_selectors_survive_roundtrip` ověřuje pole jednotlivě;
  skutečnou pojistkou je starší `test_scope_round_trip`.
- **T5, T10, T12** — pokrytí bez expozice, případně tautologie, která jen
  dokumentuje záměr.
- **T6** — už uzavřeno, `tests/test_engine.py` obor `nd` obsahuje.
- **T2b, T2c** — poznámky k procesu, ne ke kódu.
- **T9b** — vyřešeno vynecháním counteru `suppressed`, viz rozhodnutí výše.

### Zaznamenaný předpoklad

`_usable_nd` bere doslovný řetězec `"incomplete"` jako reálný stav, který Junos
vrací. V nahraných fixtures se vyskytuje jen `unreachable`. Ověřit se to bez
zařízení v tom stavu nedá; kdyby předpoklad neplatil, nic se nerozbije.

---

## 5. Co je na téhle větvi solidní

- **Rozdělení rodin je úplné.** `ip_address` ani `virtual_gw_ip_address`
  nepřežily nikde ve stromu. Jediné místo, kde se rodiny slévají zpět
  (`scoping/matcher.py:72`), je vědomé rozhodnutí — párování služeb podle
  subnetu musí vidět obě.
- **Hlasitý pád na starých datech funguje.** Chybějící verze, `1` i řetězec
  `"2"` shodí načtení u inventory i snapshotu, se srozumitelnou hláškou.
- **Oba parsery se změnily v přesném zámku** — porovnáno řádek po řádku.
- **ND collector má správný šev.** Nefiltruje nic a říká proč; rozhodnutí, co
  je použitelný cíl, žije v `probes/ping.py`.
- **Datový model BGP po RIB je správný** a jeho anti-regresní testy mají zuby.
  Mezera je jen v prezentaci (F-2).
- **Ostré ověření proti laborce se vyplatilo.** Tři z nejostřejších chyb na
  téhle větvi našlo ono, ne testy — a F-4 byl čtvrtý výskyt téhož tvaru. To je
  argument pro to spustit ho po opravách znovu, ne proti té metodě.
- **Ledger byl nejcennější artefakt.** Dva z nejzávažnějších nálezů závěrečné
  review (F-1 a T8) už byly zapsané jako odložené drobnosti lidmi, kteří
  správně poznali, že je sami posoudit nemůžou.
- **Mutace jako důkaz, ne pocit.** Každá oprava, jejíž test by mohl být
  tautologický, prošla ověřením, že na zavedené mutaci opravdu padne — T11a,
  T8, `PREFIX_KEYS` i měření rámečku po blocích.
