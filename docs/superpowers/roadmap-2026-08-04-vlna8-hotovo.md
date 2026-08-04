# Vlna 8 hotová — deaktivované podprvky zůstávají v záměru, stav k 2026-08-04

**Výchozí bod:** větev `vlna8-deaktivovane-podprvky` založená z `main` na
commitu `1abd1b2` (spec i plán jsou součástí základu větve, ne jejího
obsahu), všech šest úloh hotových, závěrečné whole-branch review provedeno
a jeho nález opraven.
**657 testů zelených, 0 přeskočených** (výchozí stav 640). Zámek parserů
drží na 146 řádcích. Inventory i snapshot schema je nově **5**.

Zadáním byl bod 1 v „Co zbývá" roadmapy vlny 7
([`roadmap-2026-08-03-vlna7-hotovo.md`](roadmap-2026-08-03-vlna7-hotovo.md)),
**v plném rozsahu včetně top-level `<routing-options inactive>`** — rozhodnutí
uživatele z 2026‑08‑04. Návrh je
[`specs/2026-08-04-vlna8-deaktivovane-podprvky-design.md`](specs/2026-08-04-vlna8-deaktivovane-podprvky-design.md),
provedení
[`plans/2026-08-04-vlna8-deaktivovane-podprvky.md`](plans/2026-08-04-vlna8-deaktivovane-podprvky.md).

---

## Co vlna 8 přinesla

Deaktivovaný prvek konfigurace se ze záměru **nevypouští**. Zůstane v něm s
příznakem a v reportu dostane SKIP na svém vlastním řádku, aniž by strhl
sourozence nebo celou službu — `engine.py:145` SKIPy odfiltruje ještě před
hlasováním `Status.worst()`.

- **Statické routy** — `StaticRoute` nese `active: bool`. V parserech padly
  čtyři kontejnerové guardy a jeden listový; příznak se čte na listu, a
  protože `_is_inactive` chodí po předcích, pokryje tím deaktivaci na
  **libovolné** úrovni nad routou včetně celého `routing-options` i celé VRF.
- **BGP sousedi** — deaktivovaný soused jde do paralelního seznamu
  `bgp_neighbor_inactive` / `Selectors.bgp_neighbors_inactive`. Paralelní
  seznam, ne mapa: `bgp_neighbors` slouží jako **selektor**, podle kterého se
  ke službě párují naměřené session, a přepis na mapu by rozbil čtyři testy
  členství bez užitku.
- **Checky** — `checks/routes.py` dá SKIP za deaktivovanou routu, která v
  tabulce není; `checks/bgp.py` dá SKIP za deaktivovaného peera bez session a
  přestal u služby s jediným deaktivovaným peerem tvrdit „sluzba nema zadne
  BGP peery". `checks/deactivation.py` zůstal na granularitě služby.
- **Schema 4 → 5** u inventory i snapshotu. Důvod je ten samý, kvůli kterému
  vlna vznikla: starý soubor nové klíče nemá, `.get()` by dosadil default a
  **každý deaktivovaný prvek by se tiše přečetl jako aktivní**. Hlasitý pád je
  smysl bumpu, ne jeho cena.

**BFD se vědomě nezměnilo.** Deaktivované `bfd-liveness-detection` pod živou
skupinou **správně** dědí skupinovou hodnotu: Junosí `inactive` znamená
„příkaz se neuplatní", takže BFD na krabici opravdu poběží se skupinovou
hodnotou. Nový test `test_inactive_bfd_override_inherits_group_value` to
fixuje i s proveniencí (`source == "group"`), aby to příští vlna
neopravovala jako vadu.

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""   # 657 passed, 0 skipped
diff mx_parser.py evo_parser.py | wc -l    # 146
```

---

## Co vyšlo jinak, než plán čekal

### 1. Závěrečné review našlo CRITICAL, který žádná per-úlohová review vidět nemohla

Nejcennější nález vlny a přesně ten důvod, proč se whole-branch review dělá.

`Scope.select` (`models/scope.py`) filtroval naměřená fakta jen podle
`bgp_neighbors`. Vlna přidala `bgp_neighbors_inactive` do sjednocení v
`engine.py`, ale **ne sem** — spec jmenoval **všechna čtyři** místa členství,
plán z nich do úloh přeložil dvě. Vada tedy vznikla v překladu specu do
plánu, ne v žádné úloze; každá ze šesti prošla review čistě, protože žádná se
toho místa nedotkla.

Důsledek byl **regrese proti `main`**: deaktivovaný peer se živou session
zmizel úplně. `select` session zahodil → `checks/bgp.py` ho viděl jako
bezrelačního → vydal SKIP „deaktivovan" → a engine ho už nepočítal mezi
nezařazené, takže nespadl ani do `NEZARAZENO`. Na `main` byl aspoň tam.

Reviewer to **změřil přes skutečnou cestu**, neodvodil.

### 2. Dva testy tu mizící session zafixovaly

Druhá vrstva téhož nálezu a samostatné poučení. Oba testy si stavěly
`subject` ručně a `Scope.select` obcházely, takže potvrzovaly chování, které
ve skutečné cestě neexistovalo. **Zelená sada aktivně kryla regresi.**

Je to potřetí, co tenhle projekt platí za pravidlo *test nad datovou
strukturou neměří, co dělá skutečná cesta*. Oprava proto obsahuje i testy,
které jdou přes `Scope.select`, a mutant doloženě ukazuje, že **nové testy
padnou, zatímco starý ručně stavěný zůstane zelený** — tím je rozdíl mezi
oběma vrstvami změřený, ne tvrzený.

### 3. Kontejnerové guardy byly čirá redundance

Změřeno při psaní specu: zrušení čtyř kontejnerových guardů samo o sobě dá
**640 passed, nulový ripple**. Byly úplně zastíněné listovým guardem, protože
`_is_inactive` chodí po předcích. Teprve listový guard shodí 12 případů.

Týž tvar, jaký našla vlna 4 u tří redundantních guardů. Praktický důsledek do
plánu: jejich odstranění **nesmělo** být samostatná úloha s kritériem „testy
zelené" — to kritérium by splnilo i nicnedělání.

### 4. Podmínka „ASCII-only" v plánu byla vymyšlená

Autor plánu přečetl podmíněnou větu roadmapy vlny 7 („*je-li* ASCII-only tvrdá
podmínka, dokazuje se bajtovým scanem") jako tvrzení a neověřil ji. Nahlásil
to implementer úlohy 1. Doměřeno: repo-wide grep prázdný **není**, na `main`
je 61 řádků s ne‑ASCII v šesti souborech.

Prakticky to bylo horší než kosmetika: každá úloha měla v akceptačních
kritériích nesplnitelnou bránu, a `tests/parsers/test_inactive.py` — kam
úloha 3 sype nejvíc testů — je psaný **česky s diakritikou**, zatímco plán tam
velel ASCII.

### 5. Povinný mutant dvakrát odhalil díru v zadání, ne v implementaci

- **Úloha 4:** implementer správně upravil **obě** funkce enginu, ale test i
  mutant mířily jen na BGP větev. Sjednocení v `_unassigned_bfd_sessions`
  nehlídal nikdo. Kód byl v pořádku, chyběl důkaz.
- **Úloha 5:** test na „sourozenec zůstane PASS" byl **necitlivý na vlastního
  mutanta** — sourozenec měl `subject is not None`, takže SKIP větev pro něj
  byla nedosažitelná a rozšíření podmínky s testem nehnulo. Implementer test
  zcitlivěl třetí routou místo aby oslabil mutanta.

Navíc reviewer úlohy 5 si **pustil vlastního mutanta** a zjistil, že druhý
konjunkt `if deactivated and subject is None:` nehlídá nikdo.

### 6. Implementer vyvrátil odůvodnění, které mu zadání dalo

Zadání závěrečné opravy tvrdilo, že `checks/bfd.py` iteruje `bfd_peers`, a
proto by vybraná session neměla kde vykreslit řádek. Změřeno: iteruje
`set(intent) | set(sessions) | set(baseline_sessions)`, takže řádek by
vzniknout **mohl** — jako `WARN … v konfiguraci sluzby neni`.

Rozhodnutí to nezměnilo (ta hláška je u deaktivovaného peera nepravdivá — v
konfiguraci ten peer je, jen je vypnutý), ale implementer odmítl opsat cizí
odůvodnění do nosného komentáře a přepsal ho podle měření. Zvláštní commit
`c2076d8`.

### 7. Revert mutanta zahodil neuloženou práci

Plán velel pustit mutanta dřív, než se práce úlohy commitne. `git checkout --`
pak smazal i ji. Implementer úlohy 2 si toho všiml (vrátilo se 12 padlých
testů) a kroky zopakoval. **Tentýž trap byl ještě v úlohách 3, 4, 5 a 6** a
opravil se plošně.

---

## Co zbývá

### 1. ~~Příznaky na hlubších úrovních konfigurace~~

**Vyřízeno.** Statické routy i BGP sousedi zůstávají v záměru a hlásí SKIP,
včetně deaktivace na kontejnerové a top-level úrovni.

### 2. Resync `172.20.20.5.yml` a `tests/fixtures/rpc/junos-evo/`

Beze změny z vlny 4. Kořenové `172.20.20.{4,5}.yml` navíc **zůstaly na
schematu 4**, takže je nástroj po vlně 8 odmítne načíst. Žádný test je nečte —
testy sahají do `tests/fixtures/`.

### 5. Drobnosti ze závěrečného review vlny 4

Beze změny — pět kosmetických bodů, žádná neblokovala merge.

### 9. Peer je v popisku `BGP status` bezpodmínečně

Beze změny z vln 5, 6 a 7.

### 12. BGP countery jsou napříč peery uniformní

Beze změny z vlny 7.

### 14. Deaktivovaný prvek není vidět na sbaleném řádku služby

**Nový bod, vstup pro reportovou vlnu.** Změřeno: `text_report.py:390`
rozbaluje blok služby jen když `detail or view.status is not Status.PASS`, a
`engine.py:145` SKIPy odfiltruje před `Status.worst()`. Zdravá služba s jednou
deaktivovanou routou tedy zůstane PASS a **nerozbalí se** — nový SKIP řádek je
vidět až pod `--detail` nebo v JSON.

Není to vada vlny 8; je to důsledek platného návrhu reportu (blok se rozbaluje
na stav, ne na obsah). Opravit to uvnitř vlny 8 nešlo, aniž by SKIP zase
strhával službu. Otázka pro uživatele zní: **má se deaktivovaný prvek nějak
projevit i na sbaleném řádku služby?**

### 15. Aktivní nakonfigurovaný peer bez session je v reportu neviditelný

**Nový bod.** `checks/bgp.py` iteruje přes **naměřené** peery, na rozdíl od
`checks/routes.py`, který jde přes sjednocení tří zdrojů (konfigurace, měření
subjektu, měření baseline). Vlna 8 to vědomě nesjednotila — řešila jen
deaktivované peery.

### 16. `test_inventory_rejects_schema_three` má název, který lže dvakrát

**Nový bod, odložený minor ze závěrečné review.** V názvu není „three" ani
odmítnutí — tělo jen asertuje hodnotu konstanty. Skutečné odmítnutí pokrývá
`test_old_inventory_fails_loudly`. Vlna 8 ten řádek editovala a mohla ho
přejmenovat zadarmo.

### 17. Množina `deactivated` je klíčovaná jen `(rib, prefix)`

**Nový bod, odložený minor.** Duplicitní identita s různými příznaky by
umlčela i tu aktivní. Konzistentní s tím, jak je klíčovaná `configured`;
nedoloženo, že v praxi nastává.

---

## Pravidla do plánu vlny 9

### Nové, zaplacené touhle vlnou

**Když spec vyjmenuje N míst, plán musí ukázat, že jich adresuje N.** Spec
vlny 8 jmenoval čtyři místa členství peera, plán implementoval dvě, a per‑úlohová
review to z principu chytit nemohla — žádná úloha se toho místa nedotkla.
Zaplaceno CRITICALem v závěrečné review. Recept: u každého výčtu ve specu si
plán odškrtne pokrytí položku po položce.

**Test, který obchází skutečnou cestu, umí regresi zafixovat, ne odhalit.**
Dva testy tady stavěly `subject` ručně místo přes `Scope.select` a držely
mizející session jako správné chování. Potřetí v tomhle projektu. Recept, který
tady zabral: mutant musí ukázat **nový test padat, zatímco starý ručně stavěný
zůstane zelený** — teprve to ten rozdíl dokazuje.

**Mutant se pouští až nad zacommitovanou prací.** `git checkout --` při revertu
mutanta zahodí i neuložené změny téže úlohy.

**Nesplnitelná akceptační brána je horší než žádná.** „ASCII-only" v plánu
vzniklo přečtením podmíněné věty starší roadmapy jako tvrzení. Než se
podmínka zapíše do plánu, změří se.

### Přenesená z vln 5 až 7

**„Měření má přednost před zadáním."** Uplatnilo se v téhle vlně **pětkrát**, a
pokaždé proti zadání: třetí konstanta schematu, kterou brief nejmenoval; „přesně
čtyři křehké testy", kterých byly dva; necitlivý test úlohy 5; heuristika na
fixtures, která minula čtyři blokové seznamy v každém souboru; a odůvodnění u
`checks/bfd.py`, které bylo věcně špatně.

**„Nulový ripple po změně chování není potvrzení, je nález."** Uplatnilo se u
kontejnerových guardů (viz „Co vyšlo jinak", bod 3).

**„Test, jehož název slibuje víc než jeho aserce, je slabší, než jak vypadá."**
Uplatnilo se u všech přejmenovaných testů; review to u každého kontrolovala
zvlášť.

**„Mutant nesmí mířit do téhož souboru, proti kterému test asertuje."**
Uplatnilo se — mutanti mířili do produkčního kódu, ne do `tests/conftest.py`.
