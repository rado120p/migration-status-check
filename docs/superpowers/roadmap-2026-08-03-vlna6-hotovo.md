# Vlna 6 hotová — sémantika závažnosti u mlčící baseline, stav k 2026-08-03

**Výchozí bod:** větev `vlna6-semantika-zavaznosti` založená z `main` na
commitu `09fca25` (spec i plán jsou součástí základu větve, ne jejího obsahu),
všechny čtyři úlohy hotové, ostré ověření (tahle úloha) provedeno.
**636 testů zelených, 0 přeskočených.** Zámek parserů drží na 146 řádcích.

Zadáním byly body 3 a 4 v „Co zbývá" roadmapy vlny 5
([`roadmap-2026-08-03-vlna5-hotovo.md`](roadmap-2026-08-03-vlna5-hotovo.md)).
Návrh je
[`specs/2026-08-03-vlna6-semantika-zavaznosti-design.md`](specs/2026-08-03-vlna6-semantika-zavaznosti-design.md),
provedení
[`plans/2026-08-03-vlna6-semantika-zavaznosti.md`](plans/2026-08-03-vlna6-semantika-zavaznosti.md).

---

## Co vlna 6 přinesla

Malá vlna: jedna změna chování, jeden chybějící test a jedna oprava
dokumentace. Tři implementační commity (`7a73848`, `6fb3ca5`, `7120a75`),
plus tenhle dokument.

- **AR-43** — `baseline.get("active", True)` ve
  `StaticRouteStatusCheck._finding` (`migration_validator/checks/routes.py`)
  četlo mlčení baseline jako „routa forwardovala", a neaktivní routa v
  subjektu proto eskalovala na FAIL; nově je z toho WARN (`Outcome.DEGRADED`)
  s vlastní hláškou `„…: je v tabulce, ale neni aktivni; baseline aktivitu
  neuvadi"`, odlišnou od případu, kdy baseline chybí celá.
- **AR-44** — doplněn test
  `test_route_without_active_key_does_not_mask_healthy_siblings`
  (`tests/test_engine.py`) na scénář, který roadmapa vlny 5 vedla jako vadu:
  SKIP routa nesmí zakrýt PASSy sourozenců ve status scopu. Test nic nového
  netvrdí, připíná existující chování — proto prošel napoprvé, bez červené fáze.
- **AR-45** — bod 4 v roadmapách vln 4 a 5 přepsán na změřenou pravdu: vada,
  kterou popisoval, nikdy neexistovala; `engine.py:145` SKIPy z hlasování
  `Status.worst()` vyfiltruje dřív, než se hlasuje.

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""   # 636 passed, 0 skipped
diff mx_parser.py evo_parser.py | wc -l    # 146
```

Obě čísla jsou změřená v této úloze nad finálním stavem větve (`7120a75`) a
obě odpovídají tomu, co plán čekal.

**Ostrý report ze společných fixtures** (`tests/fixtures/172.20.20.4.yml` /
`.5.yml`) se vyrenderuje takhle — postup je tu napsaný celý schválně, ne
odkazem (viz „Co vyšlo jinak", bod 1):

1. Založ dočasný soubor `tests/test_tmp_render.py` (musí být v `tests/`, aby
   se na něj vztáhl fixture `synthetic_snapshot` z `tests/conftest.py`).
2. V testu si vyrob dvojici snapshotů
   `synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")` a
   `synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")` (konstanty
   `DEVICE_4`/`DEVICE_5` viz `tests/test_end_to_end.py:11-13`), prožeň je
   `api.evaluate(new, baseline=old, now=NOW)` a výsledek předej
   `migration_validator.reporting.text_report.render(result, detail=True)`.
3. Výstup vytiskni a spusť `.venv/bin/python -m pytest -o addopts="" -s -q
   tests/test_tmp_render.py`.
4. **Soubor po přečtení smaž** — je jednorázový a nesmí se objevit v diffu.

## Co vyšlo jinak, než plán čekal

**Na úrovni jednotlivých úloh žádná odchylka nebyla.** Všechny tři reporty
uzavírají sekci „Concerns" slovem „None" a měření v nich sedí s předpovědí
briefů řádek po řádku: AR-43 dalo 635 = 633 + 2 nové testy bez ripple,
oba mutanti shodili přesně předpovězené testy; AR-44 dalo 636 a jeho mutant
předpovězené tři testy; AR-45 je čistě textová oprava dvou souborů. To je
samo o sobě měření, ne mlčení — vlna byla malá a předpovězená správně.

Odchylky, které vznikly, jsou na úrovni celé vlny, ne úloh:

### 1. Doc drift v roadmapě vlny 5 — odkaz na `.superpowers/sdd/`

Roadmapa vlny 5 vede postup na vyrenderování ostrého reportu odkazem na
`.superpowers/sdd/2026-08-03-vlna5-report-skupiny-a-nezarazeno/task-8-brief.md`.
Změřeno 2026‑08‑03: **ten soubor ani ten adresář neexistují.** Artefakty
vlny 5 v repu nejsou.

Dvě upřesnění, obě změřená:

- Konvence, kterou ten odkaz předpokládá — datovaný podadresář pod
  `.superpowers/sdd/` — je dnes skutečná: tahle vlna svůj workspace takhle
  vede. Neplatný je konkrétní cíl, ne tvar cesty.
- Brief téhle úlohy tvrdil, že `ls -d .superpowers/sdd/*/` nevrací nic.
  Změřeno: vrací
  `.superpowers/sdd/2026-08-03-vlna6-semantika-zavaznosti/`, tedy adresář
  téhle vlny. Podstatné tvrzení (cíl odkazu je pryč) drží, ta parentéza ne.
  Malý, ale učebnicový případ pravidla „měření má přednost před zadáním" —
  tentokrát se mýlil brief.

Proto je v „Jak si vyrobit důkazy" postup napsaný celý inline. Odkaz na
mizící artefakt by ten drift jen předal dál.

### 2. Změněná větev AR-43 se na ostrém reportu neukáže

Ostré ověření vyrenderovalo report nad společnými fixtures (postupem výše) a
hledalo, jestli se změněné chování projeví. **Neprojeví, a bylo to čekané.**
`tests/conftest.py:138-143` zrcadlí každou routu ze záměru do naměřených
faktů s `"active": True`, takže větev „routa je v tabulce, ale není aktivní"
na těchhle datech nenastane vůbec — ani ve staré, ani v nové podobě.

Změřeno dvěma způsoby, oba souhlasí:

- grep na vykresleném textu: žádný řádek neobsahuje `neni aktivni`
  (prázdný seznam),
- výčet všech výsledků checku `static_route_status` napříč scopy: osm
  výskytů, z toho šest PASS (routa v tabulce se stejným next-hopem) a dva
  SKIP (`sluzba je v konfiguraci deaktivovana (interface deactivated)`).
  Žádný BROKEN ani DEGRADED.

Zapsáno jako změřený fakt, ne jako mezera: **AR-43 je ověřené výhradně
jednotkovými testy a ostrý report ho potvrdit nemůže.** Je to týž tvar jako
bod 8 roadmapy vlny 5 (motivující scénář AR-36 na fixtures není k vidění) —
a stejně jako tam platí, že až se fixtures doplní o routu bez `active`,
stojí za to si ten blok přečíst očima.

### 3. Odložené drobnosti z review úloh

Čtyři, žádná neblokovala svou úlohu, žádná se v téhle vlně neopravovala:

- Report úlohy 1 se u argumentu, že dva větvové predikáty (`elif baseline:`
  vs. `if baseline`) sedí, odvolává na `":89"` — ta reference nesedí.
  Argument sám platí, jen ten řádek ne.
- Report úlohy 1 popsal červenou fázi druhého testu prózou místo vlepeného
  výstupu pytestu. Doměřeno jinudy: mutant B (sloučení obou DEGRADED hlášek)
  shodil právě a jen `test_silent_baseline_has_its_own_message`, což je
  totéž tvrzení podepřené měřením.
- Implementer úlohy 1 ověřil podmínku „bez diakritiky" čtením diffu.
  Reviewer tu mezeru zavřel bajtovým scanem (`grep -nP '[^\x00-\x7F]'`) a
  doporučil ho jako metodu vždycky, když je ASCII-only tvrdá podmínka.
  Promítnuto do pravidel pro vlnu 7.
- Opravený text bodu 4 v roadmapě vlny 5 (AR-45) jmenuje jen dva
  předcházející testy, které mutant `engine.py:145` shodí, a nezmiňuje, že
  po AR-44 mu podléhá i nový test — dohromady tři. Není to nepravda (text
  nikde netvrdí, že je výčet úplný), ale podceňuje to ochranu, která dneska
  existuje. **Zapsáno, ne opraveno** — diff téhle úlohy smí obsahovat jen
  tenhle soubor. Nese se dál jako bod 6 v „Co zbývá".

### 4. Mutanta úlohy 2 musel přeměřit controller

Review úlohy 2 nemohla ověřit tvrzení o mutantovi v `engine.py`, protože
diff úlohy (správně) žádný produkční soubor neobsahuje — je to test-only
změna. Controller proto mutanta pustil sám: cíl v `engine.py` existuje a
shodí přesně tři testy včetně nového. Měření tedy sedí; nesedělo to, že by
šlo ověřit z diffu. Lekce je kandidát na pravidlo vlny 7.

---

## Co zbývá

### 1. Příznaky na hlubších úrovních konfigurace

Beze změny z vlny 3, 4 a 5, včetně obou částí (deaktivovaná jednotlivá
`route`/`bfd-liveness-detection`/`neighbor` a top-level
`<routing-options inactive>` se dál vypouští ze záměru beze stopy).

### 2. Resync `172.20.20.5.yml` a `tests/fixtures/rpc/junos-evo/`

Beze změny z vlny 4. Vlna 6 se laborky ani fixtures netýkala.

### 3. ~~Baseline bez `active` dnes eskaluje na FAIL~~

**Vyřízeno — AR-43** (commit `7a73848`). Mlčící baseline u statické routy
dává `DEGRADED` (WARN) podle R-2, stejně jako to AR-34c udělalo pro
`subject`, a nese vlastní hlášku odlišnou od případu „baseline chybí celá".

### 4. ~~Priorita SKIP nad PASS — změřeno, vada neexistuje~~

**Vyřízeno — AR-44** (commit `6fb3ca5`). Vada neexistovala už ve vlně 5:
`engine.py:145` SKIPy z hlasování `Status.worst()` vyfiltruje ještě předtím,
než se hlasuje, takže SKIP routa sourozence nezakrývá. Zbýval z toho jediný
chybějící test — scénář přes `static_route_status` netvrdil nikdo — a ten
doplnila tahle vlna jako
`test_route_without_active_key_does_not_mask_healthy_siblings`.
**Pořadí v `_STATUS_RANK` se nezměnilo** a měnit se nemá.

### 5. Drobnosti ze závěrečného review vlny 4

Beze změny — pět kosmetických bodů (viz roadmapa vlny 4, „Co zbývá" bod 6),
žádná neblokovala merge tehdy ani teď.

### 6. Opravený bod 4 v roadmapě vlny 5 podceňuje počet chránících testů

Text z AR-45 jmenuje dva testy, které mutant `engine.py:145` shodí; po AR-44
jsou tři. Nepravdivé to není (výčet se nikde neprohlašuje za úplný), jen
slabší, než jaká je skutečnost. Neopraveno záměrně: diff uzavírací úlohy smí
obsahovat jen tenhle dokument. Jednořádková oprava pro vlnu 7.

### 7. Sdílený syntetický pomocník dává IPv6 peerům skupinu `inet.0`

Beze změny z vlny 5. Na obou fixtures nese každý BGP peer, IPv4 i IPv6,
countery `BGP {peer} / inet.0`; u IPv6 peerů to má být `inet6.0`. Zdroj
**není** `migration_validator/checks/bgp.py`, ale `tests/conftest.py:100-109`
(`_facts_for`), který každému peerovi bez ohledu na rodinu syntetizuje
`"ribs": {"inet.0": {...}}`. Countery jsou navíc uniformně `14/14/14/3` —
sdílené fixtures nikdy nemodelovaly RIB podle rodiny ani reálné hodnoty per
peer. Oprava patří do `tests/conftest.py`, ne do reportu.

### 8. Motivující scénář AR-36 není na sdílených fixtures k vidění

Beze změny z vlny 5. Peer se dvěma RIB, který dával osm nerozlišitelných
řádků a vlnu 5 motivoval, se v ostrém reportu nevyskytuje — každá BGP
skupina má právě jednoho peera a jednu RIB. Scénář je ověřený jen
jednotkovými testy (`CONFIGURED_TWO_RIBS` / `_installed_two_ribs()` v
`test_routes.py`, `test_bgp_group_carries_peer_and_rib` v `test_bgp.py`).
Vlna 6 přidala do stejné kategorie svůj vlastní případ — viz „Co vyšlo
jinak", bod 2.

### 9. Peer je v popisku `BGP status` bezpodmínečně

Beze změny z vlny 5. Vědomé rozhodnutí plánu AR-36 (`checks/bgp.py`), ne
opomenutí — ale na ostrém výstupu je vidět, že je to redundantní tam, kde
rodinová sekce má jen jednoho peera: `-- IPv4  152.11.13.1/30` následuje
`BGP status (152.11.13.2)`, ačkoliv hlavička už `152.11.13.2` jako jediného
souseda dané `/30` implikuje. Vstup pro některou z dalších vln.

---

## Pravidla do plánu vlny 7

### Přenesená z vlny 5

**„Mutant se pouští nad tím stavem repa, ve kterém poběží doopravdy."**
Osvědčilo se, uplatněno dvakrát: implementer úlohy 1 měřil oba mutanty až
nad commitem `7a73848`, implementer úlohy 2 nad `6fb3ca5`, a oba po měření
doložili čistý strom.

**„Měření má přednost před zadáním."** Nejsilnější pravidlo téhle vlny —
celá vlna z něj vznikla. AR-45 existuje jen proto, že měření ve vlně 5
vyvrátilo vadu, kterou roadmapa tvrdila, a AR-44 jen proto, že tomu měření
zbyla jedna nezakrytá díra. V téhle vlně navíc vyvrátilo tvrzení briefu o
`ls -d .superpowers/sdd/*/` (viz „Co vyšlo jinak", bod 1).

**„Test, který hledá řetězec kdekoliv ve výstupu, neměří sekci — měří
výstup."** Přeneseno, ale v téhle vlně **se neuplatnilo**: oba nové testy
(úloha 1 i 2) tvrdí nad objekty — `Outcome`, `Status`, `message` konkrétního
findingu — ne nad vykresleným textem, takže situace, na kterou pravidlo míří,
vůbec nenastala. Platí dál pro reportovou vrstvu.

### Čtvrté, z plánu vlny 6

**„Nulový ripple po změně chování není potvrzení, je nález."** Nulový ripple
skutečně nastal: AR-43 změnilo chování a sada šla z 633 na 635 pouze o dva
nové testy, žádný existující se nepohnul — tedy staré FAIL na mlčící baseline
nedržel nikdo. Ale **jako nástroj se pravidlo v téhle vlně neuplatnilo**: ten
nulový ripple byl změřen už před psaním specu (plán, řádek 200, ho výslovně
předpovídá) a je vlastně důvodem, proč vlna vznikla. Během provedení tedy nic
nového neodhalilo. Doporučení: **přenést**, protože jeho cena se platí až v
okamžiku, kdy ripple vyjde nulový *nečekaně* — a to se v téhle vlně stát
nemohlo.

### Nová, zaplacená nálezy téhle vlny

**Je-li ASCII-only tvrdá podmínka, dokazuje se bajtovým scanem, ne čtením
diffu.** `grep -nP '[^\x00-\x7F]' <soubor>` s prázdným výstupem je důkaz;
„v diffu jsem žádnou diakritiku neviděl" je odhad. Zaplaceno review úlohy 1,
která tuhle mezeru zavřela za implementera (viz „Co vyšlo jinak", bod 3).

**Test-only diff nemůže doložit tvrzení o produkčním kódu — musí ho změřit
někdo mimo review té úlohy.** Když úloha přidává jen test a jeho hodnota
stojí na tom, co dělá produkční kód (typicky „tenhle mutant v `engine.py` ho
shodí"), reviewer to z diffu ověřit nedokáže: produkční soubor v něm z
definice není. Buď to změří controller, nebo závěrečné ověření vlny — ale
někdo to změřit musí, jinak tvrzení v docstringu zůstane nepodložené.
Zaplaceno úlohou 2 (viz „Co vyšlo jinak", bod 4).
