# Vlna 9 — deaktivace je nález, a peer se bere ze záměru

**Výchozí bod:** `main` na commitu `10f347f` (merge vlny 8), 657 testů
zelených, 0 přeskočených, zámek parserů 146 řádků, inventory i snapshot
schema 5.

**Zadáním jsou body 14 a 15** v „Co zbývá" roadmapy vlny 8
([`../roadmap-2026-08-04-vlna8-hotovo.md`](../roadmap-2026-08-04-vlna8-hotovo.md)).
Body 2, 5, 9, 12, 16 a 17 zůstávají otevřené a tahle vlna se jich netýká.

Uživatel k nim 2026‑08‑04 rozhodl dvě věci, které spec provádí, nerozporuje:

1. **Deaktivovaný prvek konfigurace je sám o sobě nález.** Konfigurace by
   deaktivované prvky běžně obsahovat neměla, takže služba, která nějaký nese,
   nesmí být PASS. Baseline neurčuje *jestli* se to hlásí, jen *jak nahlas*.
2. **Totéž pravidlo platí i pro deaktivovanou celou službu** — výjimka pro
   službu vypnutou v subjectu i v baselinu se ruší.

Rozhodnutí 1 je **vědomé zpřísnění vlny 8**. Ta rozhodla, že deaktivovaný
prvek nesmí strhnout službu *bezdůvodně*; nerozhodovala, že ji nesmí strhnout
nikdy. Rozhodnutí 2 ruší poslední místo, kde deaktivace končila jako PASS.

---

## Měření provedená před psaním tohoto specu

Vlna 8 zaplatila CRITICALem za to, že plán přeložil výčet ze specu jen
zčásti, a dvěma nálezy za tvrzení, která nikdo nezměřil. Všechno níž je
změřené průchodem skutečnou cestou, ne odvozené z kódu.

### Nález 1 — hláška „sluzba nema zadne BGP peery" lže i o aktivních peerech

Nejsilnější nález tohohle speccování a důvod, proč je bod 15 víc než
kosmetika. Změřeno voláním `BgpSessionStateCheck.run` nad ručně složeným
scopem:

```
-- aktivni nakonfigurovany peer bez session
   skip      sluzba nema zadne BGP peery
-- aktivni peer bez session + jiny se session
   ok        192.0.2.9: Established          # 192.0.2.1 zmizel beze stopy
-- peer byl v baseline, v subjektu neni
   skip      sluzba nema zadne BGP peery
-- deaktivovany peer bez session
   skip      peer 192.0.2.2 je v konfiguraci deaktivovan
```

Vlna 8 tuhle přesnou lež opravila — ale jen pro **deaktivované** peery.
Komentář `checks/bgp.py:61-63` to říká doslova: *„sluzba, jejiz jediny peer je
deaktivovany, nesmi dostat 'nema zadne BGP peery' - to by tvrdilo, ze v
konfiguraci zadny neni"*. Táž věta platí slovo od slova o peeru **aktivním**,
a ten případ je v provozu ten častější: aktivní nakonfigurovaný peer, který
nenavázal session, je právě to, co má migrační validátor hlásit nejhlasitěji.

Roadmapa bod 15 popisuje jako „peer je v reportu neviditelný". Měření ukazuje
horší stav: peer není jen neviditelný, nástroj o něm aktivně **tvrdí
nepravdu**.

### Nález 2 — `Scope.select` peera bez session vůbec nepropustí

`models/scope.py:163` filtruje naměřená BGP fakta podle členství:

```python
bgp = {
    peer: data
    for peer, data in (facts.get("bgp") or {}).items()
    if peer in self.selectors.bgp_neighbors
    or peer in self.selectors.bgp_neighbors_inactive
}
```

Filtruje se **měření podle záměru**, ne naopak. Nakonfigurovaný peer, pro
kterého žádná session nepřišla, tedy v `facts["bgp"]` není a do
`ctx.subject["bgp"]` se dostat nemůže. **Identitu takového peera lze vzít
jedině ze selektorů.**

Zapisuje se to sem proto, že tohle je přesně tvar chyby, na které vlna 8
vyrobila CRITICAL: implementer, který by novou větev postavil nad
`ctx.subject`, by napsal kód, který nikdy nic nenajde, a test nad ručně
složeným `subject` by mu to potvrdil jako správné.

### Nález 3 — sémantiku deaktivace drží pět testů, ne desítky

Změřeno třemi mutanty nad zacommitovaným stromem, každý zvlášť, každý
vrácen `git checkout --` před dalším:

| mutant | padlo | které |
|---|---|---|
| `deactivation.py`: `OK` → `DEGRADED` | 2 | `test_deactivation.py::test_matrix[True-True-ok]`, `test_engine.py::test_service_deactivated_on_both_sides_is_pass` |
| `routes.py`: deaktivovaná routa `SKIP` → `DEGRADED` | 1 | `test_routes.py::test_deactivated_route_yields_skip_and_sibling_stays_ok` |
| `bgp.py`: deaktivovaný peer `SKIP` → `DEGRADED` | 2 | `test_bgp.py::test_deactivated_peer_without_session_yields_skip`, `test_bgp.py::test_service_with_only_deactivated_peers_skips_per_peer` |

Pět testů celkem, všechny různé.

**Tohle vyvrací tvrzení, které autor specu prezentoval uživateli**, totiž že
změna „dotkne se existujících testů vln 3, 4 a 8" a posune souhrnné countery
v širším rozsahu. Nedotkne. Zejména `_counts_lines` a souhrn za služby
nehlídá u deaktivace **nikdo** kromě jediného testu v `test_engine.py`.

Praktický důsledek do plánu: nízký ripple je tady **nález, ne potvrzení**
(pravidlo vlny 4, uplatněné vlnou 8 na kontejnerových guardech). Znamená, že
sémantika deaktivace je tence pokrytá, a vlna 9 musí pokrytí doplnit, ne se
o ně opřít.

### Nález 4 — `requires` check na prázdných datech nezastaví

`checks/base.py:154` přeskakuje check jen tehdy, když je collector v
`failed_collectors`. Prázdná `facts["bgp"]` check nezastaví. Sjednocení
zdrojů v bodu 15 tedy nepotřebuje žádnou změnu v `run_check` — check se
spustí i u služby, která nemá jedinou naměřenou session.

---

## Návrh

### 1. Jedna tabulka deaktivace, tři konzumenti

Dnes existují dvě nesouvisející sémantiky: služba se posuzuje proti baseline
(`checks/deactivation.py`, čtyři větve), podprvek dostane bezpodmínečný SKIP
(`routes.py:129`, `bgp.py:126`). Vlna 9 je sjednotí.

| subject | baseline | `Outcome` | Status | význam |
|---|---|---|---|---|
| vypnuto | zapnuto | `BROKEN` | FAIL | v baseline běželo, migrace nedokončena |
| vypnuto | vypnuto | `DEGRADED` | WARN | deaktivovaný prvek v konfiguraci |
| vypnuto | není | `DEGRADED` | WARN | totéž, není s čím porovnat |
| zapnuto | vypnuto | `DEGRADED` | WARN | v baseline bylo vypnuté, teď běží |
| zapnuto | zapnuto/není | — | — | žádný řádek nevzniká |

`DEGRADED` je vždy WARN nezávisle na severity (`models/result.py:77`), takže
se pravidlo nemíchá se severitami. `BROKEN` při `CRITICAL` dá FAIL a všechny
tři dotčené třídy `CRITICAL` mají.

Tabulka žije jako **jedna sdílená funkce**, ne jako trojí zkopírovaná
větvení. Podpis pracuje s příznaky, ne s doménovými objekty, aby ji šlo
volat z checku nad službou, nad routou i nad peerem:

```python
def deactivation_outcome(subject_off: bool, baseline_off: bool | None) -> Outcome | None
```

`None` znamená „žádný řádek". Zprávu skládá volající — jen on ví, jestli
mluví o službě, routě, nebo peerovi.

Umístění: `migration_validator/checks/deactivation.py` je pro to přirozený
domov (modul už tuhle sémantiku vlastní) a vyhne se novému souboru pro jednu
funkci. `routes.py` a `bgp.py` z něj budou importovat; cyklus nevzniká,
protože `deactivation.py` importuje jen z `base`, `registry` a `models`.

**Poznámka k `checks/base.py:146`:** nad deaktivovanou službou ostatní checky
dál SKIPnou. To se nemění — deaktivovaná služba svůj nález dostane od
`DeactivationStateCheck` a řádky o jejích podprvcích by nad vypnutou službou
nic neřekly.

### 2. Bod 14 padá bez nového mechanismu v rendereru

Deaktivovaný prvek přestane vyrábět `SKIP`, takže ho `engine.py:145` už
neodfiltruje před `Status.worst()`, služba půjde na WARN a
`text_report.py:390` blok **rozbalí sám**.

Žádná poznámka ve sloupci NALEZ, žádný třetí režim zobrazení vedle sbaleného
a rozbaleného, **žádná změna v `reporting/`**. Zásada „blok se rozbaluje na
stav, ne na obsah" zůstává nedotčená — změnil se stav, ne pravidlo.

Tohle je hlavní důvod, proč se bod 14 řeší sémantikou a ne kosmetikou: každá
z variant s poznámkou v souhrnném řádku by přidala mechanismus, který tahle
varianta nepotřebuje.

### 3. Bod 15 — `BgpSessionStateCheck` iteruje sjednocení, ne měření

Zrcadlo toho, co `checks/routes.py:100` dělá od vlny 4. Iterační množina:

| zdroj | výraz |
|---|---|
| konfigurace, aktivní | `ctx.scope.selectors.bgp_neighbors` |
| konfigurace, vypnutá | `ctx.scope.selectors.bgp_neighbors_inactive` |
| měření subject | `ctx.subject["bgp"]` |
| měření baseline | `(ctx.baseline or {})["bgp"]` |

Baseline **záměr** (pro tabulku z bodu 1) se bere z
`ctx.baseline_scope.selectors.bgp_neighbors{,_inactive}`. Že je dostupný, je
ověřené: `engine.py:279` staví scopy baseline přes `_scopes_of(baseline)`,
takže každý snapshot nese vlastní inventory a otázka „byl ten peer v baseline
aktivní?" je zodpověditelná. Bez toho by tabulka měla jen dvě použitelné
větve.

Větve, seřazené tak, jak se vyhodnocují:

1. **peer je v konfiguraci deaktivovaný** → tabulka z bodu 1. Rozhoduje
   dvojice (příznak v subjectu, příznak v baselinu), ne přítomnost session.
2. **peer je deaktivovaný, ale session pro něj přesto přišla** → beze změny
   vlny 8: normální větev stavu session. Je to rozpor konfigurace se stavem a
   ta informace je cennější než příznak. `Scope.select` takovou session
   propouští (`scope.py:163`), takže větev je dosažitelná.
3. **aktivní nakonfigurovaný peer bez session kdekoli** → `BROKEN`,
   `"{peer}: nakonfigurovan, ale session neexistuje"`. Dnes buď zmizí, nebo
   vyrobí lživou hlášku z Nálezu 1.
4. **peer jen v měření baseline** → `BROKEN`,
   `"{peer}: v baseline byl, v subjektu neni"`. Zrcadlí `routes.py:167`.
5. **peer se session** → beze změny, existující větve `Established` /
   ostatní stavy.

Hláška **„sluzba nema zadne BGP peery" zůstává**, ale její podmínka se
zpřísní: vydá se jen tehdy, když je **sjednocení všech čtyř zdrojů prázdné**.
Teprve pak je pravdivá.

**Device scope:** `device_scope()` má vždy prázdné selektory, takže větev 3 je
v něm nedosažitelná stejně jako obdobná větev v `routes.py:154-162`. Stráž
`not is_device` se ze stejného důvodu jako tam **nepřidává** — byla by
nečinná a `routes.py` ji drží jen jako zapsaný záměr AR-17.

**`BgpPrefixCountsCheck` se nesjednocuje.** Nakonfigurovaný peer bez session
žádné countery nemá, takže by řádek jen zdvojoval nález ze session checku.
Jeho vlastní hláška `"sluzba nema zadne BGP peery"` (`bgp.py:152`) je ale
týmž tvrzením jako v Nálezu 1 a stejně nepravdivá — **přeformuluje se** na
tvrzení o měření (`"zadna namerena BGP session, neni co porovnat"`), což je
jednořádková změna a nic dalšího nevleče.

### 4. Výčet míst členství peera — čtyři, a plán je odškrtne po jednom

Vlna 9 mění, které peery `checks/bgp.py` vidí. Spec proto vyjmenovává **všechna**
místa, kde se členství peera ve službě rozhoduje, a plán u každého musí
ukázat, jak s ním naložil — jednotlivě, ne souhrnně. Právě tenhle překlad
výčtu do plánu vyrobil ve vlně 8 CRITICAL.

| # | místo | co s ním vlna 9 dělá |
|---|---|---|
| 1 | `models/scope.py:163` — BGP filtr v `Scope.select` | **beze změny.** Filtruje měření podle záměru; nová větev 3 identitu bere ze selektorů, ne odsud (Nález 2). |
| 2 | `models/scope.py:213` — BFD filtr v `Scope.select` | **beze změny.** Vlna 8 tu záměrně nepřipočítává `bgp_neighbors_inactive`, BFD se nemění. |
| 3 | `checks/bgp.py` — iterace peerů | **mění se**, viz bod 3. |
| 4 | `engine.py:_unassigned_bgp_peers` | **beze změny.** Sjednocuje oba seznamy už od vlny 8; nakonfigurovaný peer bez session žádnou session do NEZARAZENO nepřináší. |
| 5 | `engine.py:_unassigned_bfd_sessions` | **beze změny.** Vědomá asymetrie vlny 8, BFD se nemění. |

Míst je pět, ne čtyři, jak je jmenovala vlna 8 — BFD filtr v `Scope.select`
je šesté rozhodovací místo, které její výčet nezahrnoval. Mění se z nich
**jediné**.

### 5. Co se nemění

- **Schema zůstává 5.** Příznak `active` u rout i `bgp_neighbor_inactive`
  zavedla vlna 8; vlna 9 čte existující klíče a žádný nový nezavádí. Baseline
  starší než vlna 8 je schema 4 a nástroj ho odmítne už dnes, takže tiché
  přečtení deaktivovaného prvku jako aktivního nehrozí.
- **Parsery.** Vlna 9 se jich nedotýká; zámek 146 řádků musí držet beze změny.
- **`reporting/`.** Viz bod 2 — bod 14 se řeší sémantikou.
- **BFD.** Vlna 8 doložila, že deaktivované `bfd-liveness-detection` pod živou
  skupinou **správně** dědí skupinovou hodnotu: Junosí `inactive` znamená
  „příkaz se neuplatní", takže BFD na krabici opravdu poběží se skupinovou
  hodnotou. To je dědičnost, ne deaktivace prvku, a tabulka z bodu 1 se na ni
  nevztahuje. `test_inactive_bfd_override_inherits_group_value` to fixuje
  včetně provenience a **nesmí se opravovat jako vada**.

---

## Testovací strategie

### Existující testy, které stojí na starém kontraktu

Nález 3 je vyjmenoval měřením. Všech pět **musí** vlna 9 upravit, a u každého
platí, že se mění **očekávání, ne aserce** — test, který přestane tvrdit
cokoli, je horší než smazaný.

| test | dnes tvrdí | po vlně 9 |
|---|---|---|
| `test_deactivation.py::test_matrix[True-True-ok]` | vypnuto v obou → `OK` | → `DEGRADED`; parametr se přejmenuje z `ok` na `degraded`, jinak název lže |
| `test_engine.py::test_service_deactivated_on_both_sides_is_pass` | služba → PASS | → WARN; **přejmenovat**, „is_pass" v názvu by lhal |
| `test_routes.py::test_deactivated_route_yields_skip_and_sibling_stays_ok` | routa → SKIP | → WARN; sourozenec zůstává OK. Třetí routa, kterou vlna 8 doplnila kvůli citlivosti na mutanta, zůstává |
| `test_bgp.py::test_deactivated_peer_without_session_yields_skip` | peer → SKIP | → WARN; **přejmenovat** |
| `test_bgp.py::test_service_with_only_deactivated_peers_skips_per_peer` | řádek na peera, SKIP | → řádek na peera, WARN; **přejmenovat** |

Pravidlo vlny 6 („test, jehož název slibuje víc než jeho aserce, je slabší,
než jak vypadá") se tu obrací: tři z pěti názvů budou po změně **slibovat něco
jiného, než tvrdí**. Přejmenování není kosmetika, je součást úlohy.

### Nové testy

Rozděleno podle toho, co dokazují.

**Tabulka deaktivace (bod 1)** — parametrizovaně přes všech pět řádků,
zvlášť pro službu, routu a peera. Tři sady, ne jedna: sdílená funkce se dá
zavolat správně a přesto se v jednom ze tří konzumentů použít špatně.

**Bod 14 přes skutečnou cestu** — a tady je nejpřísnější požadavek celé vlny.
Test musí jít **přes `evaluate_snapshots` a `text_report.render`**, ne nad
ručně složeným `ServiceView`. Tvrzení, které dokazuje: zdravá služba s jednou
deaktivovanou routou dá ve **stručném** výpisu (`detail=False`) WARN na
souhrnném řádku **a rozbalený blok** se SKIP-nahrazujícím WARN řádkem.

Vlna 8 zaplatila regresí za dva testy, které stavěly `subject` ručně a
`Scope.select` obcházely. Je to potřetí v tomhle projektu. Test nad
`ServiceView` by tady prošel, i kdyby `engine.py:145` SKIPy dál filtroval.

**Bod 15, větev po větvi** — pět nových testů, jeden na každou větev z bodu 3,
plus jeden na zpřísněnou podmínku „sjednocení je prázdné → sluzba nema zadne
BGP peery". Test pro větev 3 musí `subject` získat **přes `Scope.select`**,
ne ručně — jinak nedokáže nic (Nález 2).

**Souhrnné countery** — Nález 3 ukázal, že je u deaktivace nehlídá nikdo.
Jeden test na `_counts_lines`: běh, v němž je jedna služba PASS a jedna nese
deaktivovaný prvek, vypíše `1 warn`, ne `2 pass`.

### Mutanti

Povinní, podle pravidel zaplacených vlnou 8:

- **Mutant se pouští až nad zacommitovanou prací** — `git checkout --` při
  revertu mutanta zahodí i neuložené změny téže úlohy. Vlna 8 na tenhle trap
  narazila v úloze 2 a měla ho ještě v úlohách 3 až 6.
- **Mutant nesmí mířit do téhož souboru, proti kterému test asertuje** —
  míří do produkčního kódu, ne do `tests/conftest.py`.
- **U testu pro bod 14 musí mutant ukázat rozdíl mezi vrstvami:** mutace
  `engine.py:145` (vrátit filtrování SKIPů) musí shodit nový test jdoucí přes
  `render`, zatímco test nad ručně složeným `ServiceView` zůstane zelený.
  Teprve to ten rozdíl **měří**, místo aby ho tvrdilo.
- **Každá větev z bodu 3 potřebuje mutanta na sebe.** Vlna 8 dvakrát zjistila,
  že test byl necitlivý na vlastního mutanta, protože testovaný stav byl
  nedosažitelný. U větve 3 to hrozí přesně: peer, který je v `subject`, do ní
  nespadne.

---

## Akceptační kritéria

1. `.venv/bin/python -m pytest -o addopts=""` — **0 failed, 0 skipped**.
   Výchozí stav je 657 passed; vlna smí počet jen zvýšit.
2. `diff mx_parser.py evo_parser.py | wc -l` — **146**, beze změny.
3. Sjednocená tabulka deaktivace má **jednu implementaci**; `grep` neukáže
   trojí zkopírované větvení podle příznaků.
4. Žádný check nevydá `Outcome.SKIP` za deaktivovaný prvek nebo službu.
5. `BgpSessionStateCheck` nad scopem s jediným aktivním nakonfigurovaným
   peerem bez session vydá `BROKEN`, ne `"sluzba nema zadne BGP peery"`.
6. Ve **stručném** výpisu (`detail=False`) je služba s deaktivovaným prvkem
   WARN a její blok je rozbalený — doloženo testem přes `evaluate_snapshots` a
   `render`.
7. Adresář `migration_validator/reporting/` je beze změny.
8. Schema zůstává 5 u inventory i u snapshotu.
9. Plán u **každé** z pěti položek výčtu v bodu 4 ukazuje, jak s ní naložil.

Kritérium „ASCII-only" se **nezavádí**. Vlna 8 doložila, že na `main` je 61
řádků s ne-ASCII v šesti souborech a `tests/parsers/test_inactive.py` je psaný
česky s diakritikou; nesplnitelná brána je horší než žádná.
