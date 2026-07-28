# Roadmap — co zbývá na větvi `ipv6-a-report`

**Datum:** 2026-07-28
**Větev:** `ipv6-a-report`, odbočena z `main` na `a24efc8`, 30 commitů
**Stav testů:** 411 prošlo, 1 přeskočen
**Stav větve:** **nemergováno.** Závěrečná review celé větve ji nepropustila.

Všech 15 naplánovaných úkolů je hotových a každý prošel vlastní review; každý
nález závažnosti Critical/Important byl opraven a ověřen mutací. Závěrečná
review celé větve ale viděla to, co žádná dílčí vidět nemohla, a našla šest
dalších závažných věcí. Tenhle soubor je jejich seznam.

---

## 1. Dvě rozhodnutí, která čekají na člověka

Bez nich nemá smysl začít opravovat — obě mění, co se má vlastně napsat.

### R-1: Prázdná sekce rodiny v bloku služby

Služba bez IPv6 dnes v reportu dostane:

```
 -- IPv6  - ----------------------------------------------------------------
 SKIP | ND  : sluzba nema nakonfigurovanou IPv6 adresu  |
```

Spec (řádek 497) říká, že se sekce nemá vykreslit vůbec. Jenže sekci potlačit
znamená zahodit i ten `SKIP` řádek — a rozhodnutí z Tasku 7 výslovně říká, že
check nemá z reportu mizet beze stopy, protože *chybějící check je k
nerozeznání od checku, který prošel*. Ta dvě pravidla se tady sráží a
`nd_present`, který svou rodinu značkuje i když ta rodina není nakonfigurovaná,
je místo srážky.

Možnosti:

| | dopad |
|---|---|
| **a) sekci zrušit, `SKIP` přesunout mezi společné řádky nahoře** | podle spec, check nezmizí, report je tišší. Vyžaduje, aby řádek bez adresy ztratil `family` |
| **b) sekci nechat, do hlavičky psát `neni nakonfigurovano` místo `-`** | odchyluje se od spec, zato je vidět, že se IPv6 zvažovalo a není |
| **c) sekci i `SKIP` řádek zrušit** | přesně podle spec, nejtišší; porušuje pravidlo z Tasku 7 |

Souvisí s tím i hollow test `test_service_without_ipv6_has_no_ipv6_section`
(`tests/reporting/test_text_report.py:424`), který je pojmenovaný po službě,
jež tohle chování porušuje, a prochází jen proto, že jeho fixture žádný
family-6 check neobsahuje. Po rozhodnutí ho opravit tak, aby chytal realitu.

### R-2: BGP relace, která se během migrace zlepšila

`Connect` → `Established` se hlásí jako WARN. Chování je starší než tahle
práce. Je to buď falešné varování na zdravé službě, nebo naopak legitimní
signál, že relace během migrace přeblikla.

**Doporučení: rozhodnout až po opravě F-1.** Teprve pak se zlepšení i regrese
vykreslí symetricky (`bylo Connect` proti `bylo Established`) a půjde z jednoho
sloupce poznat, jestli relace jen přeblikla, nebo se rozbila. To může změnit,
jak se to WARN čte.

---

## 2. Opravit před mergem

### F-1 (Important) — spadlá BGP relace hlásí `bez baseline`

`migration_validator/checks/bgp.py:58-69`

Větev `state != ESTABLISHED` vytváří `Finding` bez `baseline_value`, protože
`baseline_state` se počítá až za jejím `continue`. Oprava tedy není přehození
řádků, ale vytažení toho dohledání nahoru.

Doloženo na reálných datech: peer `152.11.13.2` je `Established` v
`runs/ipv6/pre.json` a `Connect` v post snapshotu. Report vypíše:

```
 FAIL | BGP status  : Connect  | bez baseline
```

Baseline existuje a říká `Established`. Sloupec, který vznikl proto, aby byla
regrese vidět, ji zamlčí přesně u nejdůležitějšího případu. Task 13 tenhle
rozpor našel a opravil **jen ve větvi pro zlepšení**; větev pro rozbitou relaci
je ta horší polovina a zůstala otevřená.

**Zároveň uzavřít odloženou drobnost T11a** — chybí fixture s `mode="both"` a
`baseline_value=None`. Ověřeno mutací: mutace vracející `""` místo
`bez baseline` pro BOTH režim projde všemi 411 testy. Ta drobnost **je**
chybějící pojistka na tenhle živý defekt, ne hypotetická mezera.

### F-3 (Important) — prázdná sekce rodiny

Viz R-1 výše. Oprava závisí na rozhodnutí.

### F-4 (Important) — hlavička sekce se nezapočítává do rámečku bloku

`migration_validator/reporting/text_report.py:52-60` a `:108`

`_section_header()` doplní hlavičku **na** šířku `width`, ale její vlastní
délka se do `width` nikdy nezapočítá. Třetí výskyt téhož tvaru — u hlavičky
bloku a u souhrnné tabulky to ostré ověření našlo už dvakrát.

Doloženo na službě se čtyřmi IPv6 rozsahy: rámeček 65 znaků, hlavička sekce
105. Tedy přesně na tom případu s víc rozsahy, kvůli kterému AR-5b vzniklo.

A stejná kritika, jakou vznesl Task 12, o řádek vedle:
`test_block_frame_agrees_with_its_widest_line` si filtruje řádky podle
prefixů `" STAV |"`, `" PASS |"` a spol. Hlavičky sekcí začínají `" -- "` a
jsou z měření vyloučené. **Test napsaný na tohle selhání je vůči němu slepý.**

### F-5 (Important) — z řádků rozhraní zmizelo jméno rozhraní

`migration_validator/checks/ifaces.py:68-82` a `:148-158`

Před touhle prací používal `InterfaceStateCheck` `label=name` (jméno rozhraní).
Rozdělení podle AR-4 ho nahradilo pevnými popisky a jméno zahodilo. Každý scope
drží fyzické i logické rozhraní, takže **každý blok v ostrém reportu** vypadá
takhle:

```
 PASS | Interface admin status       : Up     |
 PASS | Interface operational status : Up     |
 PASS | Interface admin status       : Up     |
 PASS | Interface operational status : Up     |
 WARN | Interface traffic in         : 0 pps  | bez baseline
 WARN | Interface traffic in         : 0 pps  | bylo 987 pps   -100 %
```

Dva řádky se stejným popiskem, jinými hodnotami a protichůdnými sloupci změny —
a není poznat, které rozhraní je které. `InterfaceErrorsCheck` v témže souboru
`label=name` pořád používá, takže modul si sám odporuje.

Oprava je v duchu AR-5b: kvalifikovat popisek stejně, jako se kvalifikují
rodiny s víc adresami. AR-5b vyřešilo vzácný případ (dva rozsahy v rodině) a
minulo ten univerzální.

### F-6 (Important) — bez baseline se nevypíše žádný port

`migration_validator/engine.py:77-96` (`_identity`), `reporting/view.py:145-146`

AR-10 označuje `evaluate --snapshot X` bez `--baseline` za způsob, jak si
prohlédnout stav jednoho zařízení. V tom režimu je `match=None` a
`subject_interfaces` se plní jen z `MatchInfo`. Výsledek:

```
STAV  SLUZBA                    TYP      STARY PORT NOVY PORT RI
FAIL  INTERNET-CPE13-NNI        Internet -          -         -
```

Prázdné oba sloupce, u každé služby. Nejde o regresi — starý renderer sloupce
s porty vůbec neměl. Špatně je, že **nový** sloupec je prázdný právě v režimu,
který spec doporučuje, a `_identity()` bylo navržené jako místo, kde se tomu
předejde (`"vše, co report o službě vypisuje"`), ale `selectors.interfaces`
nenese.

### T8 (drobnost, ale opravit hned) — z fixtures zmizel stav `unreachable`

Rozdělení testu v Tasku 8 vyhodilo `state="unreachable"` úplně; v
`tests/probes/test_ping.py` po něm nezůstala stopa. Ověřeno mutací: zúžení
pravidla na `("incomplete",)` projde všemi 411 testy — a `unreachable` je
přitom stav, který se v nahraném fixture z laborky **reálně vyskytuje**
(`2001:db8::1`). Netestovaná je ta polovina pravidla, která na reálných datech
sepne. Jeden řádek ve fixture.

### F-9 (formálně drobnost, ale 16 z 36 varování v ostrém běhu)

`migration_validator/engine.py:52-74` (`_aligned_baseline_data`)

Přejmenovává `selectors.interfaces`, ale ne `physical_interfaces`. Fyzické
rozhraní tak nikdy nenajde svou baseline a každá migrovaná služba vypíše dva
trvalé řádky `WARN … 0 pps | bez baseline`. V ostrém běhu je to 16 z 36 varování.

Příčina je starší než tahle větev, ale tahle větev počet těch řádků zdvojila
(jeden finding → dva) a zviditelnila je novým sloupcem ZMENA. Je to přesně ten
trvalý oranžový svit, proti kterému se rozhodovalo v Tasku 7 — operátor si
zvykne výpis přeskakovat a přehlédne to jedno varování, na kterém záleželo.

---

## 3. Follow-up (nebrání mergi)

### F-2 (Important, ale zatím latentní) — AR-5 podřádek s názvem RIB nikdy nevznikl

`migration_validator/reporting/view.py:76-95`, `reporting/text_report.py:_block`

AR-5 předepisuje název RIB na odsazeném podřádku pod `BGP status`. Mechanismus
podřádků v rendereru neexistuje a `_row()` `check.details["rib"]` zahazuje.
Peer se dvěma RIB se vykreslí jako deset řádků, pět popisků dvakrát, název RIB
nikde. To maří smysl AR-7: operátor vidí pokles, ale ne **ve které** RIB.

Na dnešní topologii laborky nesepne — každý servisní peer má jednu RIB. Nahrané
fixtures ale obsahují peery s 11 RIB a `bgp_prefix_counts` je přesně na ně
navržený.

Související mezera: vyhození `"active"` nebo `"suppressed"` z `PREFIX_KEYS`
projde všemi 411 testy. Oba countery jsou zafixované na úrovni collectoru, ne
checku.

### Drobnosti z ledgeru, které mají zůstat

| # | co | proč |
|---|---|---|
| T13a | Testy pojistky proti self-pingu dokazují „pojistka existuje", ne „platí na každý prvek" — vlastní adresa leží ve fixtures na indexu 0. Ověřeno mutací: `if index > 0 or address != source` projde | jeden přeuspořádaný fixture |
| T13b | V ND testu se očekávaný cíl rovná tomu, co by vrátil `subnet_fallback` — rozbitá ND větev by prošla přes fallback | stejná úprava fixture jako T13a |
| T9b | `_prefix_finding` bere pokles counteru `suppressed` jako zhoršení. U potlačených rout je pokles **zlepšení**, signálem je nárůst. V laborce nesepne (všude nula), na síti s dampingem by hlásil zlepšení jako regresi | stejná třída jako R-2, potřebuje rozhodnutí |
| T1, T2a, T7a, T7b | drobné mezery v pokrytí fixtures | dávkově, jedním commitem |
| F-7 | `_row` při chybějícím `value` sáhne po `check.message`, takže do sloupce hodnot padají celé věty (jeden řádek měl 151 znaků) | opak AR-4 |
| F-8 | Souhrnný řádek počítá checky, tabulka pod ním služby — `79 PASS 36 WARN` nad 11 řádky | rozdělení podle AR-4 to znásobilo |
| F-10 | Findingy bez popisku spadnou na `check.id`, takže mezi hezkými popisky sedí `SKIP | evpn_esi_status` | pohltí i T9a |
| F-11 | `probes/ping.py` a `checks/reachability.py` mají dvě kopie téže logiky pro link-local. Už jednou to způsobilo chybu: Task 13b opravoval docstring, který zasel nepravdivé tvrzení v dokumentaci; kopie v `ping.py` docstring nemá vůbec | rozcházení je pořád na místě |
| F-12 | `filter_result` nechává původní `summary`, takže `--filter` tiskne počty za nefiltrovaný běh | starší než tahle větev |
| F-14 | Sloupec s typem služby v NESPAROVANO není odsazený, sloupec s důvodem je rozházený | kosmetika |

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

### Zaznamenaný předpoklad

`_usable_nd` bere doslovný řetězec `"incomplete"` jako reálný stav, který Junos
vrací. V nahraných fixtures se vyskytuje jen `unreachable`. Ověřit se to bez
zařízení v tom stavu nedá; kdyby předpoklad neplatil, nic se nerozbije.

---

## 4. Co je na téhle větvi solidní

Aby bylo jasné, kde se nemusí nic hledat:

- **Rozdělení rodin je úplné.** `ip_address` ani `virtual_gw_ip_address`
  nepřežily nikde ve stromu. Jediné místo, kde se rodiny slévají zpět
  (`scoping/matcher.py:72`), je vědomé rozhodnutí — párování služeb podle
  subnetu musí vidět obě.
- **Hlasitý pád na starých datech funguje.** Chybějící verze, `1` i řetězec
  `"2"` shodí načtení u inventory i snapshotu, se srozumitelnou hláškou. Na tom
  stojí celý přechod na schéma 2.
- **Oba parsery se změnily v přesném zámku** — porovnáno řádek po řádku, jejich
  změny jsou znak po znaku identické. Udržet to přes 29 commitů není
  samozřejmost.
- **ND collector má správný šev.** Nefiltruje nic a říká proč; rozhodnutí, co
  je použitelný cíl, žije v `probes/ping.py`, kde je známá konfigurace služby.
- **Datový model BGP po RIB je správný** a jeho anti-regresní testy mají zuby —
  vrácení collectoru ke sčítání i sčítání uvnitř checku shodí testy. Mezera je
  jen v prezentaci (F-2).
- **Ostré ověření proti laborce se vyplatilo.** Tři z nejostřejších chyb na
  téhle větvi našlo ono, ne testy. To, že závěrečná review našla čtvrtý výskyt
  téhož tvaru (F-4), je argument pro to spustit ho po opravách znovu — ne proti
  té metodě.
- **Ledger byl nejcennější artefakt.** Dva z nejzávažnějších nálezů závěrečné
  review (F-1 a T8) už byly zapsané jako odložené drobnosti lidmi, kteří
  správně poznali, že je sami posoudit nemůžou. Disciplína odkládání fungovala
  přesně tak, jak měla.

---

## 5. Doporučené pořadí příště

1. Rozhodnout R-1 a R-2.
2. Jedna opravná vlna: F-1 (+T11a), F-3, F-4, F-5, F-6, T8, F-9.
3. Jedna cílená re-review té vlny.
4. **Znovu spustit ostré ověření proti laborce** — F-4 je třetí výskyt tvaru,
   který ostré ověření chytilo dvakrát, takže po opravách má smysl se podívat
   znovu.
5. Teprve pak merge; F-2 a zbytek follow-upů zvlášť.
