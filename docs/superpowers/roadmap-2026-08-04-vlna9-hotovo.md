# Vlna 9 hotová — deaktivace je nález a peer se bere ze záměru, stav k 2026-08-04

**Výchozí bod:** větev `vlna9-deaktivace-je-nalez` založená z `main` na
commitu `4817e08` (spec i plán jsou součástí základu větve), všech pět úloh
hotových, závěrečné whole-branch review provedeno a jeho nálezy opraveny.
**676 testů zelených, 0 přeskočených** (výchozí stav 657). Zámek parserů drží
na 146 řádcích. Schema zůstalo **5** u inventory i snapshotu — vlna nezavedla
jediný nový klíč. Adresář `migration_validator/reporting/` je **beze změny**.

Zadáním byly body 14 a 15 v „Co zbývá" roadmapy vlny 8
([`roadmap-2026-08-04-vlna8-hotovo.md`](roadmap-2026-08-04-vlna8-hotovo.md)).
Návrh je
[`specs/2026-08-04-vlna9-deaktivace-je-nalez-design.md`](specs/2026-08-04-vlna9-deaktivace-je-nalez-design.md),
provedení
[`plans/2026-08-04-vlna9-deaktivace-je-nalez.md`](plans/2026-08-04-vlna9-deaktivace-je-nalez.md).

---

## Co vlna 9 přinesla

**Deaktivovaný prvek konfigurace je sám o sobě nález.** Rozhodnutí uživatele
z 2026‑08‑04: konfigurace by deaktivované prvky běžně obsahovat neměla, takže
služba, která nějaký nese, nesmí být PASS. Baseline neurčuje *jestli* se to
hlásí, jen *jak nahlas*.

Jedna sdílená funkce `deactivation_outcome()` (`checks/deactivation.py`) nese
celou sémantiku a volají ji tři konzumenti:

| subject | baseline | `Outcome` | Status |
|---|---|---|---|
| vypnuto | zapnuto | `BROKEN` | FAIL — v baseline běželo, migrace nedokončena |
| vypnuto | vypnuto | `DEGRADED` | WARN |
| vypnuto | není | `DEGRADED` | WARN |
| zapnuto | vypnuto | `DEGRADED` | WARN — **jen u služby**, viz níž |

- **Bod 14 padl bez jediné změny v rendereru.** Deaktivovaný prvek přestal
  vyrábět `SKIP`, takže ho `engine.py:145` neodfiltruje před `Status.worst()`,
  služba jde na WARN a `text_report.py:390` blok rozbalí sám. Zásada „blok se
  rozbaluje na stav, ne na obsah" zůstala nedotčená — změnil se stav, ne
  pravidlo.
- **Bod 15** — `BgpSessionStateCheck` iteruje sjednocení čtyř zdrojů místo
  samotného měření, zrcadlově k `checks/routes.py`. Nakonfigurovaný aktivní
  peer bez session dostane `BROKEN`; peer jen v měření baselinu taky.
- **Zrušena výjimka u služby** deaktivované v subjectu i v baselinu — dosud
  PASS, nově WARN. Poslední místo, kde deaktivace končila jako PASS.

**Řádek 4 se u podprvků neuplatňuje.** Rozhodnutí plánu nad rámec specu:
routa nebo peer, které jsou *teď aktivní*, žádný deaktivovaný prvek nenesou, a
projekt má zapsané pravidlo R-2 (`checks/bgp.py:99-104`), že zlepšení není
varování. U služby řádek 4 zůstává, protože tam je `DeactivationStateCheck`
jediným nositelem té informace; u routy i peera ji nese jejich vlastní stavový
řádek. Funkce implementuje všech pět řádků; konzumenti nad podprvky ji volají
jen s `subject_off=True`.

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""   # 676 passed, 0 skipped
diff mx_parser.py evo_parser.py | wc -l    # 146
git diff main --stat -- migration_validator/reporting/   # prazdne
```

---

## Co vyšlo jinak, než plán čekal

### 1. Devět zastaralých tvrzení o mutantech — nová vada tohohle projektu

Nejsilnější poučení vlny, a vzniklo až během ní.

Projekt má dobrý zvyk psát do docstringů, **kterého mutanta test zabíjí**. Vlna
9 ale změnila `Outcome` na třech místech naráz — a tím zneplatnila devět
takových tvrzení. Ne v testech, které měnila: **v sousedních**, které se jí
netýkaly.

První našla per-úlohová review u úlohy 1: přejmenování `PASS` → `WARN`
zneplatnilo docstring úplně jiného testu, protože `Status.worst()` řadí WARN
(rank 2) nad SKIP (1), takže WARN vyhraje bez ohledu na filtr SKIPů — kdežto
původní PASS (rank 0) na tom filtru skutečně visel.

Od úlohy 3 dostal každý implementer do zadání pravidlo: *každý docstring, který
jmenuje konkrétní `Outcome` nebo konkrétní zabíjející test, se ověří spuštěním
toho mutanta — a nahlásí i tehdy, když je oprava mimo rozsah úlohy.* Zabralo to
okamžitě; zbylých osm se našlo takhle, poslední dva až v závěrečné review, jeden
z nich v **názvu testu**.

**Tvrzení o mutantovi je kód, ne komentář.** Zastarává stejně tiše jako kód a
nic ho nehlídá.

### 2. Nulový ripple byl zase nález, ne potvrzení

Změřeno při psaní specu třemi mutanty: celou sémantiku deaktivace držela
**pětice testů**, a souhrnné countery u deaktivace nehlídal nikdo.

To **vyvrátilo tvrzení, které autor specu prezentoval uživateli** — že se změna
dotkne testů vln 3, 4 a 8 a posune countery v širším rozsahu. Nedotkla.
Praktický důsledek do plánu: úloha 5 musela pokrytí doplnit, ne se o ně opřít.

### 3. Rozhodnutí implementované dvakrát, zamčené jednou

Nález závěrečné review a přesně ten tvar, kvůli kterému se whole-branch review
dělá — týž tvar jako CRITICAL vlny 8.

Vynechání řádku 4 se týká **dvou** konzumentů. U rout ho zamykal test. U peerů
**nezamykalo nic**: reviewer si postavil dva mutanty, které řádek 4 pro peery
zavedou, a **oba nechaly sadu celou zelenou**. Kód byl správně, ale ta
správnost nebyla ničím držená, a žádná per-úlohová review to vidět nemohla —
úloha 2 svůj zámek měla, úloha 3 o cizím nevěděla.

### 4. Fixtures deaktivovanou routu vyjádřit neumí

Nález úlohy 5, doložený měřením. Nastavit routě `active = False` v selektorech
**nestačí**: `tests/conftest.py:178` počítá syntetická fakta dřív a natvrdo je
označí `"active": True`, takže routa zůstane v tabulce a do deaktivační větve
se nedostane. Test ji musí navíc odebrat z `facts["routes"]`.

Review to prověřila jako možnou tautologii a **vyvrátila měřením**: vrátila
příznak na aktivní, routu nechala chybět, a test spadl na FAIL proti očekávanému
WARN. Test tedy měří deaktivační větev, ne „routa chybí v tabulce". Odebrání
z faktů je navíc věrné provozu — deaktivovaná routa v RIB opravdu není.

### 5. Dvakrát se zahazovala aserce, dvakrát to musel doložit mutant

Úloha 3 zahodila `is not Status.SKIP`, opravná vlna totéž na jiném testu. Obojí
bylo v pořádku — zbylá aserce na konkrétní hodnotu tu odstraněnou logicky
pohlcuje — ale ani jednou se to nevzalo na slovo. Review v obou případech
pustila mutanta, který ten test jmenuje, a doložila, že pod ním pořád padá.

### 6. Plán měl tři vady, které našla review před spuštěním

Výběr cílové služby v úloze 5 byl nedeterministický (vnitřní `break` opouštěl
jen vnitřní smyčku, takže se v každém snímku mohla vypnout jiná služba a test
by měřil FAIL místo WARN); test counterů neověřoval, že cíl byl před změnou
PASS; a vrstevní mutant úlohy 5 byl **nečinný** — `sed` mířil na řetězec, který
po úloze 2 v `routes.py` neexistuje.

Nečinný mutant je horší než žádný: vypadá jako důkaz a není.

---

## Co zbývá

### 2. Resync `172.20.20.5.yml` a `tests/fixtures/rpc/junos-evo/`

Beze změny z vln 4 a 8. Kořenové `172.20.20.{4,5}.yml` jsou **ověřeně na
schematu 4**, takže je nástroj odmítne načíst. Žádný test je nečte.

### 5. Drobnosti ze závěrečného review vlny 4

Beze změny — pět kosmetických bodů.

### 9. Peer je v popisku `BGP status` bezpodmínečně

Beze změny z vln 5 až 8.

### 12. BGP countery jsou napříč peery uniformní

Beze změny z vln 7 a 8.

### 16. `test_inventory_rejects_schema_three` má název, který lže dvakrát

Beze změny z vlny 8. Vlna 9 ten soubor needitovala.

### 17. Množina `deactivated` je klíčovaná jen `(rib, prefix)`

Beze změny z vlny 8. Doměřeno při psaní specu vlny 9: `configured` je
klíčovaná stejně, takže nejde o nekonzistenci, ale o sdílený důsledek.
Nedoloženo, že v praxi nastává.

### 18. Deaktivovaná služba se rozbalí na WARN řádek a deset SKIP řádků

**Nový bod, vstup pro reportovou vlnu.** Změřeno na laboratorních fixtures:
služba deaktivovaná v subjectu i v baselinu je nově WARN, takže se ve stručném
výpisu rozbalí — a `checks/base.py:146` nad ní všechny ostatní checky SKIPne,
takže blok obsahuje jeden WARN řádek a **deset SKIP řádků** „interface
deactivated".

Spec ty SKIPy předpokládal, ale ne že se začnou tisknout. Není to vada vlny 9;
`reporting/` bylo v ní zamčené mandátem. Otázka pro uživatele: **má se blok
deaktivované služby zkrátit, nebo je deset SKIP řádků přijatelná cena?**

### 19. Peer může být vidět dvakrát a rozporuplně

**Nový bod.** `universe` v `checks/bgp.py` obsahuje i `baseline_peers`, takže
peer se **živou session v subjectu**, kterého si žádný subjektový scope
nenárokuje, ale baseline scope ano, dostane `FAIL "v baseline byl, v subjektu
neni"` v bloku služby — a **současně** je se svou živou session v NEZAŘAZENO.

Změřeno end-to-end. Downgradováno na minor, protože **statické routy se takhle
chovají od vlny 4** (`routes.py:200` + `_unassigned_static_routes`) — BGP tedy
kopíruje zavedené chování, nevymýšlí si. Sjednotit se to dá jen rozhodnutím,
která z těch dvou sekcí má peera vlastnit.

---

## Pravidla do plánu vlny 10

### Nová, zaplacená touhle vlnou

**Tvrzení o mutantovi je kód, ne komentář.** Devět docstringů a komentářů této
vlny tvrdilo `Outcome`, který po ní neplatil — a všechny v testech, které se
neměnily. Recept, který zabral: každý implementer dostane do zadání příkaz
ověřit spuštěním mutanta každý docstring, který jmenuje konkrétní `Outcome`
nebo zabíjející test, **i když je oprava mimo rozsah jeho úlohy**.

**Když se jedno rozhodnutí implementuje na N místech, plán musí zamknout N
testů.** Vynechání řádku 4 se týkalo dvou konzumentů a zamykal ho jeden test.
Per‑úlohová review to chytit nemohla. Je to sourozenec pravidla vlny 8 („když
spec vyjmenuje N míst, plán musí ukázat, že jich adresuje N") — jenže tady šlo
o **pokrytí**, ne o implementaci.

**Mutant, který se neaplikoval, neměří nic.** Plán vlny 9 obsahoval `sed`
mířící na řetězec, který v té fázi v souboru už neexistoval. Od té doby každý
mutant v plánu končí `grep -n MUTANT <soubor>`, který musí něco vypsat.

**Zahození aserce se dokazuje mutantem, ne úvahou.** Dvakrát v této vlně, a
dvakrát to bylo v pořádku — ale ani jednou se to nevzalo na slovo.

### Přenesená z vln 5 až 8

**„Měření má přednost před zadáním."** Uplatnilo se **šestkrát**: pětice testů
místo předpokládaného širokého ripplu; obě vady helperu v úloze 5; hardcode
`"active": True` v conftestu; nečinný mutant; a rozpor ve výčtu zabitých testů,
kde měl **implementer pravdu proti reviewerovi** (dva testy, ne jeden) — a
třetí měření to rozhodlo.

**„Nulový ripple po změně chování není potvrzení, je nález."** Uplatnilo se
u pětice testů držící sémantiku deaktivace (viz „Co vyšlo jinak", bod 2).

**„Test, který obchází skutečnou cestu, umí regresi zafixovat, ne odhalit."**
Úloha 5 byla celá o tomhle a review ji prověřila stavbou vlastního
rozlišujícího mutanta.

**„Test, jehož název slibuje víc než jeho aserce, je slabší, než jak vypadá."**
Uplatnilo se u šesti přejmenovaných testů; jeden název lhal až do závěrečné
review.
