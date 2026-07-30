# Deaktivovaná konfigurace, aktivita statik a integrita testů — návrh

**Datum:** 2026-07-30
**Stav:** schváleno, připraveno k tvorbě implementačního plánu
**Navazuje na:** [`roadmap-2026-07-29-vlna2-hotovo.md`](../roadmap-2026-07-29-vlna2-hotovo.md),
sekce „Co zbývá" — body 1, 2, 4, 5, 6, 7
**Výchozí stav:** 547 testů zelených / 1 přeskočen, `diff mx_parser.py evo_parser.py | wc -l` = 146

## Účel

Junosí `deactivate` je standardní idiom migrace: konfigurace zůstane
v zařízení, ale neplatí. Nástroj s tím dnes zachází nedůsledně a na dvou
místech přímo špatně.

Zásadní je, **kterým směrem se to má sjednotit**, a rozhodnutí uživatele
z 2026‑07‑30 jde proti tomu, co roadmapa navrhovala. Do parseru se čtení
`inactive` dostalo záměrně proto, aby se **nemíjely** routing-instance, které
jsou deaktivované — dočasně deaktivovaná služba se pořád musí zmigrovat.
Vypouštět takovou službu z inventory je tedy chyba, ne oprava. Bod 1 roadmapy
(„přidat chybějící guardy, aby se vypouštělo důsledně") tento návrh proto
**nahrazuje**, ne provádí.

Správné chování: služba zůstane v inventory, deaktivace se **zaznamená**
a **je vidět ve výsledku**, a párování se jí neřídí.

## Rozsah

**V rozsahu:**

- čtení `inactive` na `interface` a `unit`, v obou parserech
- dědění příznaku `inactive` z kontejnerů (`interfaces`, `routing-instances`,
  `protocols`, `bgp`) na jejich potomky
- inventory schema **4**: pole `active` nahrazují `routing_instance_active`
  a `interface_active`
- snapshot schema **4**: `Scope` nese oba příznaky a serializuje je
- centrální SKIP pro deaktivovanou službu v `run_check()`
- nový check `deactivation_state`
- `StaticRouteStatusCheck` začne číst `active` z měření
- integrita conformance testů (bod 6 roadmapy) a tří nediskriminujících
  testů (bod 7)
- regenerace inventory a přenahrání `tests/fixtures/rpc/junos-evo/interfaces.xml`

**Mimo rozsah:**

- **příznaky na hlubších úrovních konfigurace** — jednotlivá deaktivovaná
  routa, deaktivovaná `bfd-liveness-detection` stanza a deaktivovaný
  `neighbor` se dál **vypouštějí ze záměru** tak, jak to zavedla vlna 2.
  Rozhodnutí uživatele z 2026‑07‑30: „ačkoliv by bylo hezké mít podobný flag
  v hlubších částech konfigu, tak bych to teď nutně neimplementoval, jsou
  důležitější věci co by tool měl prvně zvládat." Odloženo vědomě.
- F‑2 (podřádky se jménem RIB v rendereru) — vlastní spec
- F‑11 (dvě kopie logiky pro link-local), F‑14 (kosmetika v `NESPAROVANO`,
  `unassigned` v textovém reportu)
- přejmenování `RoutingInstance.active` (`mx_parser.py:104`) — tam je to
  jméno **pravdivé**, popisuje stav routing-instance. Matoucí je
  `InterfaceService.active` (`:156`), a ten se rozpadá na dvě pole podle
  AR‑20. Roadmapa obě jména slučovala; tady se rozlišují.

## Ověřený výchozí stav

Všechna tvrzení pocházejí ze čtení kódu na commitu `764afed` a z captureů
v `runs/bfd-static-2026-07-29/` (mimo git).

### Co se dnes zachytí a co ne

| co je deaktivované | dnešní chování | místo v kódu |
|---|---|---|
| `instance` (jedna VRF) | statiky té VRF se vypustí, `RoutingInstance.active = False` | `mx_parser.py:622`, `:450` |
| `routing-options`, `rib`, `static`, `route` | vypustí se | `:611`, `:671`, `:694`, `:700` |
| `bfd-liveness-detection`, `neighbor`, `group` | vypustí se | `:751`, `:577`, `:791`, `:809` |
| **`routing-instances`** (kontejner) | **nic** — instance se čtou jako živé | — |
| **`protocols`, `bgp`** (kontejnery) | **nic** | — |
| **`interface`, `unit`** | **nic** — `_parse_interfaces` atribut nečte | `:1010` |

Úroveň kontejneru a úroveň položky si tedy dnes odporují: deaktivovat jednu
VRF a deaktivovat všechny VRF dá protichůdnou odpověď. To je chyba za
jakékoli politiky.

### Pole `active` je mrtvý sloupec

`ServiceEntry.active` (`models/inventory.py:61`) se plní **výhradně** ze stavu
routing-instance — `mx_parser.py:1245`, `active=instance.active if instance
else True`. Služba na deaktivovaném rozhraní má tedy dnes `active: true`.

Pole nečte **nikdo**: ověřeno grepem přes `scoping/`, `engine.py`
a `reporting/`. Ani matcher, ani renderer.

### Laborka

V živých capturech jsou čtyři deaktivovaná rozhraní:

| zařízení | rozhraní | description |
|---|---|---|
| `172.20.20.4` | `ge-0/0/2` | `NNI1-TO-CPE1` |
| `172.20.20.4` | `ge-0/0/4` | `L3VPN-CPE14-UNI` |
| `172.20.20.4` | `ge-0/0/5` | `EVPN-VLAN-AWARE-INTERNET` |
| `172.20.20.5` | `et-0/0/10` | (bez description) |

Všechna se v commitnuté `172.20.20.4.yml` objevují s `active: true`. Služba
na nich tedy dnes hlásí FAIL na všem (ping down, BGP down) bez jakéhokoli
vysvětlení. Žádná deaktivovaná routing-instance v laborce momentálně není —
`<routing-instances inactive="inactive">` z roadmapy byl syntetický test.

### Snímek nese inventory i scopes

`capture_device` ukládá do snímku `scopes=scopes` a `inventory=inventory.entries`
(`capture.py:121-122`). Stav baseline je tedy při vyhodnocení k dispozici —
což je nutná podmínka pro AR‑23. `Scope` ani `Selectors` ale dnes pole
o deaktivaci nemají (`models/scope.py:51`, `:96`), takže se do snímku
nedostane.

### Kam se dá zahákovat

- `run_check()` (`checks/base.py:113`) už zkratkuje na `_skip(...)` ve čtyřech
  situacích: chybí inventory, chybí baseline, selhal collector, spadl check.
  Pátý důvod je jedna změna, která pokryje **všechny** checky najednou.
- `_run_scope()` (`engine.py:139`) skládá stav služby jako `Status.worst()`
  z ne‑SKIP výsledků; když SKIPnou všechny, služba je SKIP.
- `build_scopes()` (`scoping/builder.py:56`) dělá **jeden scope na jeden
  záznam inventory**, takže příznak se mapuje 1:1.

---

## Přejímací požadavky

### AR-18 — parser čte `inactive` na `interface` a `unit`

`_parse_interfaces` (`mx_parser.py:1010`) zjistí deaktivaci fyzického
rozhraní i jednotky a předá ji do `InterfaceConfig` (`:119`), odkud ji
`_classify_interface` (`:1245`) donese do `InterfaceService`. Deaktivované
rozhraní **se z výsledku nevypouští** — jen se označí.

Jednotka pod deaktivovaným fyzickým rozhraním je deaktivovaná také, i když
sama atribut nemá.

### AR-19 — příznak `inactive` se dědí z kontejneru dolů

`<interfaces inactive="inactive">` označí všechna rozhraní i jednotky pod
sebou. `<routing-instances inactive="inactive">` označí všechny instance,
takže se jejich statiky vypustí a jejich služby se označí — přesně tak, jako
kdyby byla deaktivovaná každá `instance` zvlášť. `<protocols>` a `<bgp>`
označí všechny sousedy pod sebou, takže se jejich BGP i BFD záměry vypustí
stejně, jako když je deaktivovaný jednotlivý `neighbor`.

Tím se sjednotí rozpor mezi úrovní kontejneru a položky, který je popsaný
výš.

Změna jde do **obou** parserů symetricky. `diff mx_parser.py evo_parser.py |
wc -l` musí po celou dobu vracet **146**; jiná hodnota znamená, že se guard
aplikoval jen na jednu stranu.

### AR-20 — inventory schema 4: dvě čestná pole místo `active`

`ServiceEntry.active` (`models/inventory.py:61`) i jeho protějšek
`InterfaceService.active` v parserech (`mx_parser.py:156`) **zanikají**
a nahrazují je:

```yaml
routing_instance_active: true    # dřívější `active`, jen pod pravdivým jménem
interface_active: false          # nové
```

Obě mají default `true`. Jméno `active` bylo matoucí od začátku — popisovalo
stav routing-instance, ne rozhraní — a se dvěma zdroji deaktivace by
nestačilo vůbec.

`schema_version` inventory jde na **4**. Migrace starých souborů se nedělá:
inventory je odvozený artefakt, generuje se znovu (AR‑29).

### AR-21 — `Scope` nese oba příznaky a serializuje je

`Scope` (`models/scope.py:96`) dostane `routing_instance_active`
a `interface_active`, `build_scopes()` je opíše ze `ServiceEntry`,
`to_dict()` / `from_dict()` je zapíšou a přečtou. Bez serializace by baseline
svůj stav nenesla a AR‑23 by se nedal vyhodnotit.

Příznaky patří na `Scope`, ne do `Selectors` — nejsou to selektory, jsou to
vlastnosti služby.

`schema_version` snímku jde na **4**.

### AR-22 — deaktivovaná služba SKIPuje na všech checcích

`run_check()` dostane pátý důvod pro `_skip(...)`: je-li scope deaktivovaný
(kterýkoli z obou příznaků je `False`), check se nespustí a vrátí SKIP
s důvodem `RI deactivated`, resp. `interface deactivated`. Jsou-li
deaktivované oba, důvod jmenuje oba.

Jediná výjimka je check `deactivation_state` (AR‑23) — ten se musí spustit,
jinak by nebylo co porovnat.

**Přijatá cena:** `deactivate routing-instances X` nesloží rozhraní, takže
u služby s deaktivovanou VRF přijdeme i o informaci, že port je nahozený.
Rozhodnutí uživatele z 2026‑07‑30 ve prospěch jednoduchosti: každý check by
jinak musel deklarovat, na čem závisí, a to je víc míst, kde se dá chybit.
Falešný FAIL to nevyrábí.

### AR-23 — check `deactivation_state`

Nový check, `mode = Mode.BOTH`, `requires_inventory = True`,
`default_severity = Severity.CRITICAL`. Jako jediný se nespouští přes
zkratku z AR‑22.

| baseline | subjekt | `Outcome` | status služby | proč |
|---|---|---|---|---|
| deaktivováno | deaktivováno | `OK` | **PASS** | stav se nezměnil |
| deaktivováno | aktivní | `DEGRADED` | **WARN** | změna proti baseline: dřív deaktivováno, teď nahozené |
| aktivní | deaktivováno | `BROKEN` | **FAIL** | služba zákazníkovi běžela a teď neběží — migrace nedokončena |
| bez baseline | deaktivováno | `SKIP` | **SKIP** | není s čím porovnat; důvod je v hlášce vidět |
| aktivní | aktivní | *žádný finding* | — | zdravá služba nemá v bloku přibýt řádek |

Poslední řádek je záměrný: check vrátí prázdný seznam, ne `OK`. Jinak by
každý zdravý blok narostl o řádek, který nic neříká.

Kombinace se skládá z `run_check()` a `_run_scope()` bez zvláštní logiky:
u řádku „deaktivováno / deaktivováno" SKIPnou všechny ostatní checky, projde
jen `OK` z tohoto checku, a `Status.worst()` z jediného ne‑SKIP výsledku dá
PASS.

### AR-24 — párování se deaktivací neřídí

`scoping/matcher.py` příznaky **nečte**. Služba se spáruje se svým protějškem
bez ohledu na to, jestli je deaktivovaná na jedné straně, na druhé, nebo na
obou. Dnes to platí náhodou (pole nikdo nečte); po AR‑21 to musí platit
záměrně a musí to být pokryté testem.

### AR-25 — `StaticRouteStatusCheck` čte aktivitu routy

Collector u každé statiky ukládá `active` = zda má routa v tabulce hvězdičku
(`active-tag == "*"`, `collectors/routes.py:84`). Check to pole dnes
ignoruje, takže routa, kterou přebil jiný zdroj, projde jako PASS.

| baseline | subjekt | výsledek |
|---|---|---|
| neaktivní | neaktivní | **PASS** — stav se nezměnil |
| aktivní | neaktivní | **FAIL** |
| bez baseline | neaktivní | **FAIL** — viz předpoklad níž |
| neaktivní | aktivní | **PASS** — zlepšení, v souladu s R‑2 |

Neaktivní routa se **nezaměňuje** s hláškou `neni v tabulce`. Ta má vlastní
příčinu a vlastní text.

### AR-26 — conformance test nesmí schovat přejmenovanou oblast

Dnešní `pytest.skip` na chybějící fixture způsobí, že přejmenování
`RoutesCollector.name` shodí celý modul do skipu a sada hlásí
`527 passed / 19 skipped, nula failů`. „Prošlo" tam znamená „neproběhlo".

Test musí asertovat, že `{collector.name for collector in COLLECTORS}`
odpovídá jménům fixture, které na disku **skutečně jsou**. Chybějící fixture
i osiřelá fixture jsou fail, ne skip.

### AR-27 — MX šev pro `bfd_session_state`

Parametr `("junos", "bfd_session_state")` v conformance testu chybí. Fixture
je záměrně prázdný výpis (BGP `Idle` na obou peerech), takže check správně
vrací všechno SKIP — ale důsledek je, že na MX by přejmenovaný klíč byl
neviditelný. Parametr se doplní; test musí ověřit, že check ten klíč
**přečetl**, ne jen že nespadl.

### AR-28 — tři nediskriminující testy

- `test_mapping_list_rejects_scalars` — `match="mapping"` sedí i na jméno
  adresáře `tmp_path`. Dnes diskriminuje náhodou, ne návrhem. Zpřísnit vzor.
- `test_device_scope_reports_state_without_intent` v testech routes **i** bfd
  — `device_scope()` má vždy prázdné selektory, takže `configured` je `False`
  bez ohledu na větvení. Test musí větvení skutečně procvičit, nebo být
  nahrazený testem, který to umí.

### AR-29 — regenerace inventory a fixture

Až **po** AR‑18 … AR‑25, protože ty mění formát i obsah.

- Regenerovat inventory proti současné laborce novým parserem. Přibude služba
  `EVPN-VPWS-CPE24-UNI` (`ae0.224` na `.5`, `ge-0/0/3` na `.4`), kterou
  commitnutá schema‑3 inventory z 2026‑07‑29 21:53 nezná. Čtyři rozhraní
  z tabulky výš dostanou `interface_active: false`.
- Přenahrát `tests/fixtures/rpc/junos-evo/interfaces.xml` — dnešní fixture
  předchází přestavbě laborky a nemá `irb.15` / `ae0.15`, takže conformance
  test službu CPE14 nevidí.
- Mění se tím `tests/fixtures/172.20.20.{4,5}.yml`, takže se pohnou očekávání
  v testech (počty služeb, conformance). To je součást úlohy, ne překvapení.

`stash@{0}` se **nedropuje** — je to jediný artefakt kromě prózy roadmapy,
který nese záznam o `EVPN-VPWS-CPE24-UNI` před regenerací.

---

## Přejímací kritéria implementačního plánu

Nejsou to poznámky na konec, jsou to podmínky, které plán vlny 3 musí splnit.
Vlna 2 našla **osm** testů, které procházely proti špatné implementaci, a obě
příčiny pocházely z předepsaného testovacího kódu v plánu.

1. **Každý předepsaný test musí říct, kterou špatnou implementaci zabíjí.**
   Test, který popisuje správné chování, aniž by ho odlišil od nesprávného,
   se čte jako pokrytí a není jím.
2. **U parametrizovaného testu se mutant pouští na každou větev
   parametrizace zvlášť.** Jeden mutant v `mx_parser.py` neříká nic
   o `evo_parser.py`.
3. **Každý mutant se grepem potvrdí, že dopadl tam, kam měl.** Neaplikovaný
   mutant vypadá identicky jako nediskriminující test. Vlna 2 na to doplatila
   dvakrát: `str.replace(…, 1)` zasáhl byte-identický řádek v jiné funkci
   a `requires = ("route",)` nedělá nic.
4. **Zámek 146 řádků se kontroluje po každé změně parseru**, ne až na konci.
   Pro test-only změnu je to nutná, ne dostatečná podmínka; pro AR‑18 a AR‑19
   je to živý test symetrie.

---

## Zapsané předpoklady

### Bez baseline je neaktivní routa FAIL

AR‑25 nemá v režimu bez baseline z čeho poznat, že routa byla neaktivní
i předtím. Volí přísnější výsledek. Alternativa (SKIP) by znamenala, že
`evaluate --snapshot X` bez `--baseline` — podle AR‑10 doporučený způsob, jak
si prohlédnout jedno zařízení — o neaktivní routě mlčí.

### `requires` nechrání proti neexistujícímu klíči

Zjištění z vlny 2, které platí i pro nový check: `requires` hlídá výhradně
`ctx.failed_collectors` (`checks/base.py:135`). `requires = ("route",)` místo
`("routes",)` projde celou sadou. Check `deactivation_state` na collectory
nesahá vůbec, takže se ho to netýká, ale při psaní testů k AR‑26 a AR‑27 to
platí.

### Deaktivovaná routing-instance nesloží rozhraní

AR‑22 SKIPuje i checky, které by na deaktivované VRF stále měly co měřit
(stav portu). Přijato vědomě, viz text u AR‑22.

### Sady instancí se mezi zařízeními liší

Pokračuje předpoklad z vlny 2: `mgmt_junos` je jen na `.4`. AR‑24 tím není
dotčený — párování jede přes `ScopeKey`, ne přes jméno VRF.
