# Vlna 7 — RIB ve sdílených fixtures a zpřísnění trojcestné větve

**Výchozí bod:** `main` na commitu `095136e` (merge vlny 6), 636 testů
zelených, 0 přeskočených, zámek parserů 146 řádků.

**Zadáním jsou body 7, 8 a 11** v „Co zbývá" roadmapy vlny 6
([`../roadmap-2026-08-03-vlna6-hotovo.md`](../roadmap-2026-08-03-vlna6-hotovo.md)).
Body 1, 2, 5 a 9 zůstávají otevřené a tahle vlna se jich netýká.

Je to malá konsolidační vlna: dvě opravy syntetických fixtures a jedno
jednořádkové zpřísnění produkčního kódu. Nesahá na parsery, na kolektory ani
na laborku.

---

## Měření provedená před psaním tohoto specu

Obě fixture změny byly zkusmo aplikovány a zase odstraněny; strom byl po
každém měření čistý.

| změna | výsledek |
|---|---|
| v6 peer dostane `inet6.0` (bod 7) | 636 passed, **0 ripple** |
| každý peer dostane druhou RIB (bod 8) | 636 passed, **0 ripple** |
| **obojí dohromady, v podobě, kterou spec předepisuje** (jeden peer, druhá RIB s odlišnými countery) | 636 passed, **0 ripple** |

Třetí řádek je podstatný: první dvě měření běžela s countery `14/14/14/3` v
obou RIB, ale spec předepisuje odlišná čísla. Doměřeno zvlášť, aby ripple
odpovídal tomu, co se skutečně nasadí, a ne přibližné variantě.

Nulový ripple je podle pravidla vlny 6 **nález, ne potvrzení**, a tenhle
konkrétní nález zní: **jméno RIB u BGP peera dnes neasertuje žádný test.**
Obě změny jsou proto mechanicky zadarmo a celá jejich hodnota leží v tom, co
se objeví ve vyrenderovaném reportu. To bylo ověřeno zvlášť (postup viz
„Jak si vyrobit důkazy" v roadmapě vlny 6) a blok AR-36 se skutečně vykreslí:

```
 -- IPv4  152.11.13.1/30 -----------------------------------------------
 PASS | BGP status (152.11.13.2)          : Established         |
   -- BGP 152.11.13.2 / inet.0
 PASS | active-prefix-count               : 14                  |
   ...
   -- BGP 152.11.13.2 / inet6.0
 PASS | active-prefix-count               : 5                   |
```

Zároveň bylo očima ověřeno, že v6 peer `2001:abcd:11:13::b` sedí ve **vlastní
rodinové sekci**, takže dva bloky s `inet6.0` se v reportu nepletou.

---

## Návrh

### Body 7 a 8 — `tests/conftest.py`

`_facts_for` dnes každému BGP peerovi přišije napevno jedinou RIB jménem
`inet.0` s countery `14/14/14/3`. Nahradí to pomocník se dvěma pravidly:

```python
DUAL_RIB_PEER = "152.11.13.2"

def _ribs_for(peer, family):
    # bod 7: v6 peer -> inet6.0, v4 peer -> inet.0
    # bod 8: DUAL_RIB_PEER navic inet6.0 s vlastnimi countery
```

**Bod 7** opravuje to, co roadmapa vede jako vadu: v6 peer nesl `inet.0`,
ačkoliv sousední skupina statických rout ve stejné sekci správně používá
`inet6.0`. Zdroj vady **není** `migration_validator/checks/bgp.py` —
`_prefix_finding` bere `rib_name` beze změny z dat, která dostane — ale právě
tenhle syntetický pomocník.

**Bod 8** dává právě jednomu peerovi druhou RIB, aby motivující scénář AR-36
(peer se dvěma RIB, kvůli kterému vlna 5 přepsala BGP report tak, aby řádky
rozlišoval podle RIB) šel poprvé přečíst očima ve vyrenderovaném reportu.

**Volba `152.11.13.2` je záměrná, ne libovolná:**

- Peer je na **obou** fixtures (`.4` i `.5`, služba Internet, `ge-0/0/2.13`
  → `et-0/0/8.13`), takže baseline i subject nesou tutéž strukturu RIB a
  `bgp_prefix_counts` je porovná místo aby hlásil „RIB neni v baseline,
  nelze porovnat".
- Je to IPv4 peer, který navíc nese `inet6.0` — modeluje multiprotokolovou
  session, ne fabulaci.

**Countery ostatních peerů zůstávají uniformní `14/14/14/3`.** Roadmapa je
zmiňuje jako známý fakt, ale žádný otevřený bod je nežádá opravit;
rozrůznění counterů napříč peery je samostatná položka pro pozdější vlnu.
Nová `inet6.0` RIB u `DUAL_RIB_PEER` **vlastní čísla dostane** — jsou to nově
přidávaná data uvnitř bodu 8 a bez nich by se dva bloky téhož peera lišily
jen hlavičkou. Konkrétní hodnoty
(`received 6 / accepted 5 / advertised 2 / active 5 / suppressed 1`) jsou
libovolné v tom smyslu, že nic neměří; podstatné je, že se liší od
`14/14/14/3` a že `accepted < received`, takže na řádcích jde vidět, že to
nejsou tytéž countery zkopírované podruhé. Ověřeno renderem — viz měření výš.

### Bod 11 — `migration_validator/checks/routes.py:194`

`if was_active:` → `if was_active is True:`, symetricky s existujícím
`was_active is False` o pár řádků výš.

Chování všech větví bylo **změřeno** přímým voláním
`StaticRouteStatusCheck().run(...)` nad baseline s podstrčenou hodnotou
`active` — před změnou, po ní i pod mutantem:

| `was_active` | dnes | po `is True` | pod mutantem `is not False` |
|---|---|---|---|
| `True` | BROKEN | BROKEN | BROKEN |
| `False` | OK | OK | OK |
| klíč chybí (`None`) | DEGRADED | DEGRADED | DEGRADED |
| `0` | DEGRADED | DEGRADED | **BROKEN** |
| `1` | BROKEN | DEGRADED | BROKEN |
| `"false"` | **BROKEN** | DEGRADED | BROKEN |
| `""` | DEGRADED | DEGRADED | **BROKEN** |

Z toho plynou tři fakta, která spec drží jako změřená, ne odvozená:

1. **`"false"` je jediná vada závažnosti** — eskaluje nejednoznačnost na
   FAIL, což je přesně to, co pravidlo R-2 zakazuje. Změna na `is True` ji
   zavírá. Vedlejším důsledkem přestane být FAIL i `1`.
2. **`0` má špatnou hlášku, a tahle vlna ji nemění.** DEGRADED dostane před
   změnou i po ní; větev `elif baseline:` mu přiřkne „baseline aktivitu
   neuvádí", ačkoliv `0` neaktivitu uvádí.
3. **Zpřísnění nezmění chování žádné hodnoty, která dnes reálně nastane** —
   `True` i `False` procházejí beze změny.

**Hláška u `0` se v téhle vlně nemění. Je to vědomé rozhodnutí, ne
opomenutí.** Opravit ji by znamenalo rozšířit i větev `was_active is False`
na „klíč je přítomen a je nepravdivý", což by ale `""`, `[]` a `0.0`
prohlásilo za explicitní tvrzení „routa byla neaktivní i v baseline",
ačkoliv prázdný řetězec je spíš chybějící údaj. Jedna nepřesná hláška na
nedosažitelném vstupu by se tak vyměnila za jinou, aniž by se cokoliv
zavřelo. Roadmapa žádala jen opravu eskalace.

**Ani jeden z těch vstupů dnes nenastane:** `collectors/routes.py:84`
produkuje skutečný `bool`, baseline snapshoty jdou týmž kolektorem a YAML
round-tripuje bool jako bool. Celý bod 11 je obranné zpřísnění, jehož smysl
je věta z roadmapy: *R-2 bude platit konstrukcí, ne argumentem o
nedosažitelnosti.*

---

## Testovací strategie

Findingy `bgp_prefix_counts` nesou `group = f"BGP {peer} / {rib_name}"` a
`details["rib"]`, takže se všechna nová tvrzení dají vyslovit **nad objekty**.
Pravidlo *„test, který hledá řetězec kdekoliv ve výstupu, neměří sekci —
měří výstup"* se tedy v téhle vlně neuplatní; přeneseno pro reportovou vrstvu.

| # | test | soubor | mutant, který ho shodí | červený před opravou? |
|---|---|---|---|---|
| 1 | v6 peer nese `inet6.0`, ne `inet.0` | `tests/test_end_to_end.py` | `_prefix_finding` v `checks/bgp.py` má `rib_name` napevno na `"inet.0"` | ne |
| 2 | `DUAL_RIB_PEER` dá dva findingy s různým `details["rib"]` | `tests/test_end_to_end.py` | týž | ne |
| 3 | `{"active": "false"}` → `DEGRADED` | `tests/checks/test_routes.py` | návrat `is True` → `if was_active:` | **ano** |
| 4 | `{"active": 0}` → `DEGRADED`, ne FAIL | `tests/checks/test_routes.py` | `if was_active is not False:` | ne |

**Mutanti u testů 1 a 2 míří schválně do produkčního kódu, ne do
`conftest.py`.** Mutant, který mění týž generátor fixtures, proti kterému
test asertuje, neprokazuje nic — test by spadl proto, že mu někdo smazal
vstup, ne proto, že hlídá chování. Cíl je proto
`migration_validator/checks/bgp.py`, `_prefix_finding` (řádky ~188 a ~194,
kde se z `rib_name` staví `group` a `details["rib"]`): napevno nastavené
`inet.0` shodí oba nové testy a doloží, že skutečně chrání produkční
chování. Změřeno při psaní plánu: shodí **tři** testy — oba nové plus
existující `test_bgp_group_carries_peer_and_rib` z vlny 5. Úloha 1 je přitom celá test-only, takže podle pravidla 6 níž **tohohle
mutanta musí změřit někdo mimo review té úlohy** — produkční soubor v jejím
diffu z definice nebude.

**Mutant u testu 4 není návrat změny.** Změřeno: `if was_active:` i
`if was_active is True:` dají u `{"active": 0}` shodně DEGRADED, takže návrat
změny ten test nezabije. Zabije ho až blízký překlep vlastní opravy —
`is not False` — pod kterým `0` skončí jako BROKEN. Test 4 tedy nechrání
změnu samotnou, ale její okolí; to je legitimní důvod ho napsat, a spec ho
takhle pojmenovává, aby nikdo netvrdil víc, než co měření unese.

Tři ze čtyř testů připínají chování, které po své změně už platí — proto
projdou napoprvé a nemají červenou fázi. To je totéž jako u AR-44 ve vlně 6 a
zapisuje se to předem, aby se to nemuselo vysvětlovat zpětně. Červený je
jediný test — `{"active": "false"}`, dnes BROKEN — a jeho červená fáze se
doloží vlepeným výstupem pytestu, ne prózou.

Očekávaná sada: **636 + 4 = 640**, bez ripple na existující testy. Pokud
ripple nastane, je to nález a měření má přednost před tímhle odhadem.

---

## Pořadí úloh je povinné, ne náhodné

1. **Úloha 1** — body 7 a 8 (`tests/conftest.py`) a jejich dva testy.
2. **Úloha 2** — bod 11 (`migration_validator/checks/routes.py`) a jeho dva
   testy.

Vlna mění fixtures, takže platí pravidlo z vlny 4, které tehdy vyrobilo
špatné zadání právě tím, že se porušilo: **mutant se pouští nad tím stavem
repa, ve kterém poběží doopravdy.** Mutanti úlohy 2 se proto měří **až nad
novým `conftest.py`**, ne před ním. Toto pořadí je součástí zadání.

---

## Co vlna 7 nezavírá

- **Bod 2 z „Co vyšlo jinak" vlny 6** — routa bez klíče `active` není na
  sdílených fixtures k vidění, takže AR-43 potvrzuje jen jednotkový test. Má
  **jinou spouštěcí podmínku** než bod 8 (routa bez `active`, ne druhá RIB u
  peera) a doplněné fixtures ho **nezavírají**. Zůstává otevřený.
- **Body 1, 2, 5 a 9** roadmapy vlny 6 — beze změny.
- **Rozrůznění BGP counterů napříč peery** — nový bod, viz výš.

### Předrozhodnutý směr pro bod 1

Rozhodnuto uživatelem 2026-08-03, mimo rozsah téhle vlny, zapsáno aby se
neztratilo: **deaktivovaná jednotlivá `route`/`bfd-liveness-detection`/
`neighbor` má zůstat v záměru a hlásit SKIP**, symetricky s existujícím
chováním deaktivované služby (`sluzba je v konfiguraci deaktivovana`).
Tiché vypouštění beze stopy je vada, ne rozhodnutí. Vlna, která bod 1 vezme,
tuhle otázku už řešit nemusí.

---

## Pravidla, která musí plán vlny 7 nést

Přenesená z vlny 6, včetně dvou nově zaplacených:

1. **Mutant se pouští nad tím stavem repa, ve kterém poběží doopravdy.**
   Kritické pro tuhle vlnu — viz „Pořadí úloh".
2. **Měření má přednost před zadáním.** Včetně tohohle specu.
3. **Test, který hledá řetězec kdekoliv ve výstupu, neměří sekci.** V téhle
   vlně se neuplatní (tvrdí se nad objekty), přenáší se dál.
4. **Nulový ripple po změně chování není potvrzení, je nález.** V téhle vlně
   se už jednou uplatnil — je zdrojem tvrzení „jméno RIB neasertuje nikdo".
5. **Je-li ASCII-only tvrdá podmínka, dokazuje se bajtovým scanem**
   (`grep -nP '[^\x00-\x7F]' <soubor>` s prázdným výstupem), ne čtením diffu.
   Vlna 7 nepřidává žádný nový uživatelský řetězec, ale přidává komentáře a
   jména konstant — pravidlo platí na ně.
6. **Test-only diff nemůže doložit tvrzení o produkčním kódu.** Úloha 1 je
   celá test-only; její tvrzení o tom, co dělá `checks/bgp.py`, musí změřit
   controller nebo závěrečné ověření vlny, ne review té úlohy.

---

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""   # ocekava se 640 passed, 0 skipped
diff mx_parser.py evo_parser.py | wc -l    # ocekava se 146 (vlna na parsery nesaha)
```

Ostrý report ze společných fixtures (`tests/fixtures/172.20.20.4.yml` /
`.5.yml`) — postup je tu napsaný celý inline schválně, protože roadmapa vlny
5 ho vedla odkazem na soubor pod `.superpowers/`, který je v `.gitignore` a
mezitím zmizel:

1. Založ dočasný soubor `tests/test_tmp_render.py` (musí být v `tests/`, aby
   se na něj vztáhl fixture `synthetic_snapshot` z `tests/conftest.py`).
2. V testu vyrob dvojici snapshotů
   `synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")` a
   `synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")` (konstanty
   viz `tests/test_end_to_end.py:11-13`), prožeň je
   `api.evaluate(new, baseline=old, now=NOW)` a výsledek předej
   `migration_validator.reporting.text_report.render(result, detail=True)`.
3. Výstup vytiskni a spusť
   `.venv/bin/python -m pytest -o addopts="" -s -q tests/test_tmp_render.py`.
4. **Soubor po přečtení smaž** — je jednorázový a nesmí se objevit v diffu.
