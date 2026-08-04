# Vlna 10 — tichý blok deaktivované služby a pravdivá hláška o peeru

**Výchozí bod:** `main` na commitu `ed04b4b` (merge vlny 9), 676 testů
zelených, 0 přeskočených, zámek parserů 146 řádků, inventory i snapshot
schema 5.

**Zadáním je celý zbytek** „Co zbývá" roadmapy vlny 9
([`../roadmap-2026-08-04-vlna9-hotovo.md`](../roadmap-2026-08-04-vlna9-hotovo.md)):
body **18, 19, 16, 17, 9, 12 a 2**. Uživatel 2026‑08‑04 rozhodl, že vlna 10
pobere všechny — nezůstane otevřený žádný bod z vln 4 až 9.

Tahle vlna je první od vlny 5, která **sahá do `reporting/`** — poslední
změna toho adresáře je z 2026‑08‑03, commit `4f60b36`. Akceptační kritérium
„`reporting/` beze změny", které nesly vlny 8 i 9, se tím retiruje; nahrazuje
ho užší kritérium na JSON (akceptační kritérium 7).

---

## Měření provedená před psaním tohoto specu

Pravidlo projektu zní „měření má přednost před zadáním" a ve vlnách 5 až 9 se
uplatnilo víc než dvacetkrát. Všechno níž je změřené průchodem skutečnou
cestou, ne odvozené z roadmapy.

### Nález 1 — bod 18 není důsledek vlny 9

Roadmapa vlny 9 zapsala bod 18 jako **nový** bod vzniklý tou vlnou. Měření to
vyvrací. Blok služby rozbaluje `text_report.py:390` podmínkou
`detail or view.status is not Status.PASS`, takže rozbalený byl vždycky
u FAIL i u nespárované služby — a `checks/base.py:146` nad deaktivovanou
službou SKIPuje ostatní checky od vlny 4. Šum tedy na `main` existuje
**dnes a bez jakéhokoli zásahu**: laboratorní fixtures nesou čtyři
deaktivované služby a tři z nich ten blok tisknou.

Vlna 9 přidala **jediný nový případ** — službu deaktivovanou v subjectu
i v baselinu, která byla dosud PASS a nerozbalovala se.

Praktický důsledek do plánu: bod 18 se nesmí psát jako oprava regrese vlny 9.
Je to původní vada návrhu reportu a plán ji nesmí zúžit na jeden případ.

### Nález 2 — potlačení se nesmí vázat na `Status.SKIP`

Změřeno na renderovaném výstupu. Blok `svc:et-0/0/10.0:Internet`:

```
 SKIP | ARP               : interface deactivated
 SKIP | BFD               : interface deactivated
 SKIP | BGP prefixy       : bez baseline           <- SKIP z JINEHO duvodu
 SKIP | BGP status        : interface deactivated
 WARN | Deaktivace        : interface deactivated | bez baseline
 SKIP | Interface errors  : interface deactivated
 SKIP | Interface status  : interface deactivated
 SKIP | Interface traffic : interface deactivated
 SKIP | ND                : interface deactivated
 SKIP | Ping              : interface deactivated
 SKIP | Staticka routa    : interface deactivated
```

Řádek `BGP prefixy : bez baseline` je SKIP, ale říká něco úplně jiného —
porovnávací check bez baseline snapshotu (`checks/base.py:138`). Sloučit ho
mezi deaktivační SKIPy by zakrylo informaci, kterou nic jiného nenese.

Implementace, která by slévala podle `Status.SKIP`, tedy hlásí ztrátu dat.
Implementace, která by slévala podle textu zprávy, je vázaná na řetězec, který
nic nehlídá.

### Nález 3 — bod 19 je dosažitelný a měřený

Změřeno vlastním testem přes `api.evaluate` + `render` nad laboratorními
fixtures, ne převzato z roadmapy. Postup: služba `INTERNET-CPE13-NNI`, peer
`152.11.13.2` odebrán ze **subjektových** `selectors.bgp_neighbors`, fakta
netknutá (session zůstala živá), baseline scope peera nárokuje dál.

```
>>> peer ve faktech subjektu: True
>>> BLOK svc:INTERNET-CPE13-NNI:Internet: FAIL 152.11.13.2: v baseline byl, v subjektu neni

NEZARAZENO (jen subject)
  BGP peer     152.11.13.2  RI -
  BFD session  152.11.13.2  et-0/0/8.13   Up
```

Rozpor je doložený: `v subjektu neni` proti živé session téhož peera o pár
řádků níž.

**Co se při měření ukázalo jako slabší, než autor specu nejdřív tvrdil.**
Řádky `PASS ARP zaznam 152.11.13.2` a `PASS 152.11.13.2: odpovedelo 5 z 5`
v témž bloku **nejdou** z `bgp_neighbors`, ale z `local_ipv4` přes
`_neighbour_for()` — je to sousední adresa /30, která se s adresou peera
shoduje. Odebrání peera ze selektorů na ně nemá vliv. Blok si tedy neodporuje
**uvnitř BGP checku**; odporuje si napříč sekcemi reportu. Zapsáno proto, že
plán nesmí stavět test na premise, kterou měření nepodpírá.

### Nález 4 — proč se identita peera nedá zpřesnit bez nové vazby

Větev `without_session` (`checks/bgp.py:186`) rozhoduje mezi dvěma hláškami
podle `peer in configured`. Rozlišit „peer ze zařízení zmizel" od „peer je na
zařízení, jen ho tahle služba nenárokuje" nelze: `Scope.select`
(`models/scope.py:163`) filtruje měření podle záměru, takže `ctx.subject`
nefiltrovaná fakta zařízení neobsahuje a `CheckContext` je nenese.

Odtud plyne návrh v bodu 1 níž — formulace, která je pravdivá v obou
případech, místo protažení nefiltrovaných fakt do `CheckContext`.

### Nález 5 — bod 17 je nedosažitelný, doloženo na živé laborce

Změřeno **uživatelem** na `clab-pop-migration-MX1-POP1` dočasnou konfigurací:

```
routing-options static route 198.62.254.0/29
    next-hop 152.11.14.4;
    inactive: qualified-next-hop 152.11.14.3;
    qualified-next-hop 152.11.14.2;
```

Parser z ní vydal **jediný** záznam:

```yaml
- rib: inet.0
  prefix: 198.62.254.0/29
  next_hop: [152.11.14.4]
  active: true
```

Kolize `(rib, prefix)` v množině `deactivated` (`checks/routes.py:93`) tedy
nastat nemůže — parser dva záznamy pro tentýž prefix nevyrábí. Bod 17 se
uzavírá jako nedosažitelný, s měřením místo domněnky.

### Nález 6 — parsery qualified-next-hop zahazují a odůvodnění v kódu je věcně špatně

Vedlejší nález téhož měření, **mimo rozsah vlny 10** (rozhodnutí uživatele:
speciální případ, který teď nestojí za investovaný čas).

`mx_parser.py:749` a `evo_parser.py:749` říkají doslova:

> *Jen holý next-hop. discard, reject, next-table a qualified-next-hop nemají
> adresu k porovnání se subnetem rozhraní*

`discard`, `reject` a `next-table` adresu opravdu nemají. **`qualified-next-hop`
nese buď adresu, nebo `interface-name`** — do výčtu tvarů bez adresy tedy
nepatří. Původ tvrzení je
[`2026-07-29-bfd-a-staticke-routy-design.md:461`](2026-07-29-bfd-a-staticke-routy-design.md),
kde bylo přijato s dovětkem „v laborce se žádný takový tvar nevyskytuje" —
což tehdy platilo a měřením z 2026‑08‑04 přestalo.

Vlna 10 **chování parserů nemění**. Opraví jen komentář v obou souborech
(bod 3 níž) a zapíše qualified-next-hop do roadmapy jako nový otevřený bod.

Funkční díra, kterou tím vlna vědomě nechává otevřenou: routa směrovaná
**výhradně** přes qualified-next-hop dostane `next_hop: []`, nenamapuje se na
žádnou službu, a pokud není nainstalovaná, zmizí beze stopy.

### Nález 7 — bod 9 je redundantní ve 100 % laboratorních případů, a přesto se nemění

Změřeno na `render(result, detail=True)`: laboratorní fixtures mají **sedm**
rodinových sekcí a **všechny** nesou právě jednoho peera. Kvalifikátor
`BGP status (152.11.14.4)` je tam tedy redundantní vždycky.

Měření zároveň ukazuje jeho mez: fixtures případ, kvůli kterému kvalifikátor
vznikl (dva peery téže rodiny na sdílené podsíti `/29` nebo `/24`), **vůbec
nemodelují**. Redundance je změřená, cena jejího odstranění ne.

---

## Návrh

### 1. Bod 19 — hláška o členství, ne o existenci

Rozhodnutí uživatele: **obě sekce mluví dál, ale pravdivě.** Peer zůstává
v NEZAŘAZENO — jeho session opravdu k žádné službě nesedí a NEZAŘAZENO je
jediná sekce, která ji ukáže. Blok služby si řádek nechává, protože „v
baselinu sem patřil" je legitimní migrační nález.

Mění se jediná hláška v `checks/bgp.py:192`:

| | dnes | po vlně 10 |
|---|---|---|
| hláška | `{peer}: v baseline byl, v subjektu neni` | `{peer}: v baseline patril k teto sluzbe, v subjektu uz ne` |
| `value` | `chybi uplne` | `neni ve sluzbe` |

Sesterská větev (`{peer}: nakonfigurovan, ale session neexistuje`,
`value="bez session"`) se **nemění** — ta o existenci nic netvrdí.

Nová formulace je pravdivá v **obou** případech, které do větve spadají:
peer ze zařízení zmizel i peer přešel pod jinou službu. Tím se návrh vyhýbá
nové vazbě v `CheckContext` (Nález 4).

`engine.py:_unassigned_bgp_peers` (`:168`) i `_unassigned_bfd_sessions`
(`:233`) zůstávají **beze změny**. `checks/routes.py` a
`_unassigned_static_routes` se nedotýkají — varianta „sjednotit i statické
routy" byla nabídnuta a uživatelem odmítnuta jako širší rozsah bez
podpírajícího nálezu.

### 2. Bod 18 — jeden souhrnný řádek místo N deaktivačních SKIPů

**Změřený stav dnes** (blok `EVPN-VLAN-AWARE-CPE13-NNI`, `detail=False`):

```
 STAV | CHECK             : POST (et-0/0/8.313)   | ZMENA PROTI ge-0/0/2.313
 -----+-------------------+-----------------------+-------------------------
 SKIP | BFD               : interface deactivated |
 WARN | Deaktivace        : interface deactivated |
 SKIP | EVPN ESI status   : interface deactivated |
 SKIP | EVPN MAC count    : interface deactivated |
 SKIP | Interface errors  : interface deactivated |
 SKIP | Interface status  : interface deactivated |
 SKIP | Interface traffic : interface deactivated |
 SKIP | Staticka routa    : interface deactivated |
```

Sloupec ZMENA je u řádku `Deaktivace` prázdný, i když jde o službu vypnutou
v obou snímcích: `value == baseline_value`, a `change_text` (`view.py:95`)
v tom případě vrací prázdný řetězec. Zapsáno proto, že první verze tohohle
specu tam „bylo interface deactivated" uváděla — a bylo to zrekonstruované,
ne změřené.

**Cílový stav téhož bloku** (změřeno dočasnou aplikací návrhu nad `main`):

```
 STAV | CHECK          : POST (et-0/0/8.313)   | ZMENA PROTI ge-0/0/2.313
 -----+----------------+-----------------------+-------------------------
 WARN | Deaktivace     : interface deactivated |
 SKIP | Ostatni checky : 7 preskoceno          |
```

**Počet ve sloučeném řádku počítá jen sloučené deaktivační SKIPy**, ne
všechny SKIPy v bloku. V bloku `svc:et-0/0/10.0:Internet` z Nálezu 2 je SKIPů
deset — **devět deaktivačních a jeden cizí**. Sloučený řádek tedy řekne
`9 preskoceno` a `BGP prefixy : bez baseline` zůstane vedle něj samostatně.
Implementer, který spočítá `len(skips)`, dostane deset — a bez tohohle
odstavce by mu to nic nevytklo.

Počet zůstává vidět schválně: mizející checky by nikdo nepoznal.

**Souhrnný řádek `Checky: … 42 SKIP` se nemění.** Ověřeno v kódu: bere se
z `result.summary`, který engine počítá z `CheckResult`ů
(`text_report.py:332`), ne z postavených `ServiceView`. Sloučení je čistě
zobrazovací a do souhrnu se nepropíše — plán si to nemusí odvozovat znovu.

**Značka, ne shoda řetězců.** Deaktivační SKIP vzniká na jediném místě —
`checks/base.py:146`. To místo označí výsledek **strukturálně**, aby ho
renderer poznal bez ohledu na znění zprávy. Nález 2 je důvod, proč se
nesmí použít ani `Status.SKIP`, ani text.

Značka nesmí být v `label` ani ve `value` — obojí se tiskne. Přirozený domov
je `CheckResult.details`, které `_skip()` dnes nevyplňuje vůbec; alternativou
je nové volitelné pole. Volbu provede plán, spec vyžaduje jen to, aby byla
strukturální a aby ji šlo zabít mutantem odděleně od zprávy.

**Pozor na `details` a JSON.** `to_dict()` propisuje `details` do strojového
výstupu (`models/result.py:141`). Značka v `details` tedy do JSON přibude —
aditivně a smysluplně (strojový konzument se dozví **proč** byl check
přeskočen). Plán tu volbu musí udělat **vědomě** a její důsledek pro JSON
zapsat; tiché přibytí klíče do strojového výstupu je nález, ne detail.

**Kde se slévá.** V `reporting/view.py` při stavbě `ServiceView`, které
dostane příznak `detail`. Sloučený řádek nese `family=None`, takže padne do
bezhlavičkové sekce nad rodinovými — přesně tam, kde deaktivační SKIPy leží
dnes (`FAMILY_ORDER = (None, 4, 6)`, `view.py:18`).

**Co `--detail` dělá:** rozepíše je po jednom, beze změny proti dnešku. To je
zapsaná zásada z `reporting-is-next-work-area`: „`--detail` rozbalí všechno
včetně PASS."

**Zásada „blok se rozbaluje na stav, ne na obsah" zůstává nedotčená.**
Vlna 10 nemění, **kdy** se blok rozbalí — mění, **co** rozbalený blok
obsahuje. Varianta „blok deaktivované služby nerozbalovat" byla nabídnuta
a odmítnuta právě proto, že by tu zásadu porušila.

**JSON se nemění.** Sloučení je vlastnost textového reportu, ne výsledku
běhu. `RunResult.to_dict()` vydá všechny checky dál, bez ohledu na `detail`.

### 3. Body bez architektury

| bod | co se udělá | soubor |
|---|---|---|
| 16 | `test_inventory_rejects_schema_three` se přejmenuje tak, aby název odpovídal tělu — to asertuje hodnotu konstanty, ne odmítnutí. Skutečné odmítnutí pokrývá `test_old_inventory_fails_loudly`, který zůstává | `tests/models/test_inventory.py:305` |
| 17 | **uzavřen jako nedosažitelný** (Nález 5). Kód se nemění; do `checks/routes.py` se dopíše, že klíčování `(rib, prefix)` je bezpečné, protože parser pro tentýž prefix dva záznamy nevydá — s odkazem na měření | `checks/routes.py:93` |
| 17b | komentář o qualified-next-hop se opraví v **obou** parserech identicky: qualified-next-hop nese adresu nebo `interface-name`, takže do výčtu tvarů bez adresy nepatří; důvod jeho vynechání se přepíše na „vědomě odloženo" (Nález 6) | `mx_parser.py:749`, `evo_parser.py:749` |
| 9 | **uzavřen.** Kvalifikátor zůstává bezpodmínečný; do `checks/bgp.py` se dopíše proč: popisek je identifikátor řádku i v JSON, a podmíněný kvalifikátor by přibytím druhého peera přejmenoval i řádek toho prvního | `checks/bgp.py` |
| 12 | countery se rozrůzní deterministicky z adresy peera. Baseline i subject **stejná** čísla (jinak by `bgp_prefix_counts` začal hlásit rozdíly všude) a `accepted <= received` | `tests/conftest.py:63` |
| 2 | capture z laborky a přegenerování kořenových `172.20.20.{4,5}.yml` a `tests/fixtures/rpc/junos-evo/` | mimo `migration_validator/` |

U bodu 12 platí zvláštní požadavek: **plán musí ripple do testů nejdřív
změřit, ne odhadnout.** Vlna 9 zaplatila za odhad ripplu ve specu, který se
měřením ukázal jako nesprávný.

### 4. Co se nemění

- **Schema zůstává 5** u inventory i u snapshotu. Vlna 10 nezavádí jediný nový
  klíč. Kdyby úloha nový klíč potřebovala, je to nález — zastavit a nahlásit.
- **Zámek parserů drží na 146 řádcích.** Vlna 10 mění v parserech jen komentář
  a musí ho změnit v obou souborech doslova stejně.
- **Chování parserů.** Qualified-next-hop se dál nezaznamenává (Nález 6).
- **`engine.py`.** Ani jeden ze dvou `_unassigned_*` seznamů se nemění.
- **`checks/routes.py` chování.** Mění se jen komentář (bod 17).
- **BFD.** `test_inactive_bfd_override_inherits_group_value` fixuje korektní
  chování a **nesmí se opravovat jako vada** — přeneseno z vln 8 a 9.

---

## Pořadí úloh a jeho důvod

```
19  ->  18  ->  16 + 17 + 9  ->  12  ->  2
```

Body **12 a 2 mění fixtures**, a v tomhle projektu platí, že po přegenerování
fixtures se **každý mutant musí pustit znovu** — mutant změřený nad jinou
sadou fixtures nedokazuje nic. Kdyby fixtures přišly doprostřed, všechny
dosavadní důkazy o citlivosti testů by se zneplatnily.

Bod 2 je poslední i z druhého důvodu: znamená skutečnou práci v laborce.
Služby jsou aktuálně aktivní na `.4`, takže pořadí je capture z `.4` →
přemigrovat služby na `.5` → capture z `.5`. Vyžádá si ho **až** poslední
úloha plánu, explicitní žádostí uživateli.

Poznámka k bodu 2: uživatelova dočasná konfigurace s qualified-next-hopy na
`.4` zůstává na krabici. Do přegenerované kořenové inventory se promítne, ale
kořenové `172.20.20.{4,5}.yml` **nečte žádný test**, takže to nic neshodí.
Přegenerování `tests/fixtures/rpc/junos-evo/` naopak testy číst budou — ripple
z něj musí plán změřit stejně jako u bodu 12.

---

## Testovací strategie

### Bod 19

Existující testy na hlášku `v baseline byl, v subjektu neni` se **upraví, ne
smažou** — mění se očekávání, ne aserce. Změřit, kolik jich je, musí plán;
`test_peer_measured_only_in_baseline_is_fail` (`tests/checks/test_bgp.py`) je
jistý.

Nový test **přes skutečnou cestu**: peer se živou session v subjektových
faktech, kterého subjektový scope nenárokuje a baseline scope ano, dá v bloku
služby hlášku o členství a **současně** je v NEZAŘAZENO. Test musí jít přes
`api.evaluate` + `render` a musí asertovat **obě** místa — jinak nedokáže, že
si nadále neodporují.

Test musí hledat řetězec **uvnitř** příslušné sekce, ne kdekoli ve výstupu.
Pravidlo vlny 5: dvě sekce sdílejí formátovací literály a `x in out` projde
i tehdy, když se zkoumaná sekce vůbec nevytiskla.

**Past v pořadí kroků, změřená při psaní tohohle specu.** `_facts_for()`
v `tests/conftest.py` odvozuje `facts["bgp"]` **ze selektorů**. Kdo peera ze
`selectors.bgp_neighbors` odebere **před** stavbou snímku, nedostane pro něj
žádnou session — peer se do NEZAŘAZENO nedostane, aserce na něj projde
vakuově a test nedokazuje nic. Selektor se tedy musí měnit **až nad hotovým
snímkem**, který `synthetic_snapshot` vrátil.

Je to týž tvar jako hardcode `"active": True` v conftestu z vlny 9: fixture
neumí vyjádřit stav, který test potřebuje, a tiše ho nahradí jiným.
Rozlišující mutant, který to **měří** místo aby to tvrdil: přesunout odebrání
peera před stavbu snímku — aserce na NEZAŘAZENO musí spadnout.

### Bod 18

Tři testy, každý dokazuje něco jiného:

1. **Sloučení přes skutečnou cestu** — deaktivovaná služba přes
   `evaluate_snapshots` + `render(detail=False)` má v bloku právě jeden SKIP
   řádek a ten nese počet. Test nad ručně složeným `ServiceView` by prošel
   i nad rozbitou cestou; tenhle projekt na to doplatil třikrát.
2. **`--detail` rozepíše** — týž běh s `detail=True` má v bloku všechny
   původní řádky.
3. **Cizí SKIP se neslévá** — blok, který obsahuje deaktivační SKIPy
   **i** `BGP prefixy : bez baseline`, si ten druhý řádek nechává samostatně.
   Přesně scénář z Nálezu 2; bez tohohle testu by implementace slévající podle
   `Status.SKIP` prošla.

Plus jeden test na **JSON**: `to_dict()` vydá všechny checky bez ohledu na
`detail`.

### Bod 12

Ripple se **měří**, ne odhaduje. Plán ho změřil: dočasně rozrůznil countery
nad `main`, ověřil, že mutace je aktivní (peery dostaly různá čísla a report
je vytiskl), a sada zůstala **676 passed, 0 failed**.

Nula je tady **nález**: hodnoty counterů ze sdílených fixtures nehlídá žádný
test, takže samotné rozrůznění by bylo dekorativní. K bodu 12 proto patří
i zamykající test, jinak se vedlejší přínos („záměna peerů by byla na reportu
vidět") neuskuteční.

Totéž měření se udělalo pro bod 18 a dopadlo stejně — vykreslený obsah bloku
deaktivované služby dnes taky nehlídá nikdo.

### Mutanti

Povinní, podle pravidel zaplacených vlnami 8 a 9:

- **Mutant se pouští až nad zacommitovanou prací.** `git checkout --` při
  revertu zahodí i neuložené změny téže úlohy.
- **Mutant nesmí mířit do téhož souboru, proti kterému test asertuje.**
- **Každý mutant v plánu končí `grep -n MUTANT <soubor>`, který musí něco
  vypsat.** Vlna 9 měla `sed` mířící na řetězec, který v té fázi neexistoval;
  nečinný mutant vypadá jako důkaz a není.
- **Tvrzení o mutantovi je kód, ne komentář.** Každý docstring, který jmenuje
  konkrétní `Outcome` nebo zabíjející test, se ověří spuštěním toho mutanta —
  **i když je oprava mimo rozsah úlohy**. Vlna 9 takhle našla devět zastaralých
  tvrzení, poslední dvě až v závěrečné review.
- **Když se jedno rozhodnutí implementuje na N místech, plán musí zamknout N
  testů.** Vlna 9 zaplatila tím, že vynechání řádku 4 zamykal jeden test ze
  dvou konzumentů.
- **U bodu 18 musí mutant ukázat rozdíl mezi vrstvami:** mutace, která
  slévá podle `Status.SKIP` místo podle značky, musí shodit test 3, zatímco
  testy 1 a 2 zůstanou zelené. Teprve to ten rozdíl **měří**.
- **Po bodech 12 a 2 se všechny mutanty vlny pouštějí znovu.** Zapsáno jako
  samostatný krok poslední úlohy, ne jako doporučení.

---

## Akceptační kritéria

1. `.venv/bin/python -m pytest -o addopts=""` — **0 failed, 0 skipped**.
   Výchozí stav je 676 passed; vlna smí počet jen zvýšit.
2. `diff mx_parser.py evo_parser.py | wc -l` — **146**, beze změny.
3. Schema zůstává **5** u inventory i u snapshotu.
4. Blok deaktivované služby má bez `--detail` **právě jeden deaktivační** SKIP
   řádek a ten nese počet sloučených checků — doloženo testem přes
   `evaluate_snapshots` a `render` nad blokem, který cizí SKIP nemá
   (`EVPN-VLAN-AWARE-CPE13-NNI` má osm řádků, z toho sedm deaktivačních).
   Blok s cizím SKIPem má řádky dva, viz kritérium 6 — „právě jeden" se
   **nevztahuje** na SKIPy jiného původu.
5. S `--detail` má týž blok všechny původní řádky.
6. Sloučení se **neváže** na `Status.SKIP` ani na text zprávy — doloženo
   testem, kde je v témž bloku SKIP z jiného důvodu a ten se neslije, a
   mutantem, který slévá podle `Status.SKIP` a ten test shodí.
7. JSON report obsahuje **všechny** checky bez ohledu na `--detail`.
8. Žádná hláška netvrdí o peeru se živou session v subjektu, že „v subjektu
   neni". Doloženo testem, který asertuje **blok služby i NEZAŘAZENO** v témž
   běhu.
9. Body 9, 16 a 17 jsou v roadmapě vlny 10 zapsané jako **uzavřené**, ne jako
   přenesené — u 17 s odkazem na měření z laborky.
10. Qualified-next-hop je v roadmapě vlny 10 zapsaný jako **nový otevřený bod**
    s měřením z Nálezu 6 a s tím, jakou funkční díru vlna vědomě nechává.
11. Countery jsou napříč peery rozrůzněné, `accepted <= received`, a baseline
    se subjektem se shoduje.

Kritérium „`reporting/` beze změny" se **ruší** — nahrazuje ho kritérium 7.
Kritérium „ASCII-only" se **nezavádí**; na `main` je ne-ASCII v šesti
souborech a `tests/parsers/test_inactive.py` je psaný česky s diakritikou.
