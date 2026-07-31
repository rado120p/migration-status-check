# MX BFD na živých datech a dokončení deaktivace — návrh

**Datum:** 2026-07-31
**Stav:** k revizi uživatelem
**Navazuje na:** [`roadmap-2026-07-30-vlna3-hotovo.md`](../roadmap-2026-07-30-vlna3-hotovo.md),
sekce „Co zbývá" — body 2, 4 (první odrážka) a 5
**Výchozí stav:** 588 testů zelených / 1 přeskočen, `diff mx_parser.py evo_parser.py | wc -l` = 146

## Účel

Vlna 3 nechala tři druhy nedodělků. Tenhle spec bere ty, které jdou uzavřít
bez zásahu do schematu: pokrytí MX BFD na reálných datech (bod 2), chybějící
test na jedné cestě deaktivace (bod 4, první odrážka) a čtyři drobnosti, které
nikdo neblokoval (bod 5).

Bod 2 byl v roadmapě zapsaný jako blokovaný stavem laborky. Uživatel ho
2026‑07‑31 odblokoval tím, že služby na `172.20.20.4` aktivoval, takže na MX
vznikly BFD session. Měření provedené při psaní tohoto specu ale ukázalo, že
**premisa roadmapy neplatí** — viz níž.

## Rozsah

**V rozsahu:**

- neprázdná MX BFD nahrávka a end-to-end pokrytí `bfd_session_state` na `junos`
- regenerace `172.20.20.4.yml` a `tests/fixtures/rpc/junos/*.xml` proti laborce
- test na top-level `<protocols inactive="inactive">`
- čtyři drobnosti z bodu 5 roadmapy

**Mimo rozsah:**

- **bod 1 roadmapy** — příznaky na hlubších úrovních konfigurace (jednotlivá
  `route`, `neighbor`, `bfd-liveness-detection`). Zůstává odloženo tak, jak to
  rozhodl uživatel 2026‑07‑30.
- **bod 4, druhá odrážka** — top-level `<routing-options inactive>` zahodí
  globální statiky beze stopy. Je to **táž díra jako bod 1**, ne samostatná
  věc, a odkládá se s ním. Rozhodnutí uživatele z 2026‑07‑31.
- **bod 3 roadmapy** — report (F‑2, F‑11, F‑14). Vlastní spec, podle
  rozhodnutí z 2026‑07‑28.
- **regenerace `172.20.20.5.yml` a `tests/fixtures/rpc/junos-evo/`.** Laborka
  na `.5` se mezitím také změnila (viz níž), ale commitnutý pár je záměrně
  postavený migrační scénář, ne zrcadlo laborky. Resync `.5` by přepsal
  očekávání testů na obou stranách najednou; když ho uživatel bude chtít, je to
  vlastní položka s vlastním rozpočtem na ripple.

## Ověřený výchozí stav

Vše ověřeno 2026‑07‑31 proti laborce, ne odhadem.

### Premisa roadmapy u bodu 2 neplatí

Roadmapa tvrdí: *„samotné parsování MX BFD odpovědi na reálném XML"* není
pokryté, tedy že MX odpověď má vlastní tvar. **Nemá.** Nahrávka z `.4`
pořízená `record` je element po elementu shodná s `tests/fixtures/rpc/junos-evo/bfd.xml`:

```
session-neighbor, session-state, session-interface, session-detection-time,
session-transmission-interval, session-adaptive-multiplier,
bfd-client/client-name, local-diagnostic, remote-diagnostic,
session-version, remote-state, session-type
```

Liší se jen jména rozhraní (`ge-0/0/2.13` proti `et-0/0/8.13`) a hodnoty.
`BfdCollector.parse()` nemá žádnou platformní větev a ani ji nepotřebuje.

**Bod 2 se tím scvrkává** z „napsat MX-specifické parsování" na „zamrazit
neprázdnou MX nahrávku a projít ji testem". Do plánu to patří jako korekce
zadání, aby se nehledal rozdíl, který neexistuje.

### Co je na `.4` k dispozici

```xml
<bfd-session><session-neighbor>152.11.13.2</session-neighbor>
  <session-state>Up</session-state>
  <session-interface>ge-0/0/2.13</session-interface>
  ... <bfd-client><client-name>BGP</client-name> ...
  <remote-state>Up</remote-state>
<bfd-session><session-neighbor>198.11.13.2</session-neighbor>
  <session-state>Down</session-state>
  <session-interface>ge-0/0/2.113</session-interface>
  ... <remote-state>AdminDown</remote-state>
```

Jedna Up a jedna Down — tedy dva různé verdikty, ne jen jeden.

### Stav deaktivace v laborce

Na `172.20.20.4` jsou po zásahu uživatele deaktivované právě dva uzly:

```
interfaces / interface[ge-0/0/3]
routing-instances / instance[EVPN-VPWS-CPE24-UNI]
```

Tenhle stav je součástí návrhu, ne náhoda. Před zásahem byla `.4` deaktivací
úplně prostá a **samotná výměna nahrávky by end-to-end pokrytí nedala**:
obě BFD session sedí na `ge-0/0/2`, jehož služby jsou v commitnuté inventory
deaktivované, takže `bfd_session_state` vrací samé SKIP. Ověřeno spuštěním
`api.evaluate` nad snapshotem z nové nahrávky a staré inventory — osm scopů,
osm SKIPů, žádný verdikt.

Volba `ge-0/0/3` + `EVPN-VPWS-CPE24-UNI` je jediná, která splní všechny tři
podmínky zároveň:

1. **nenese BFD** — obě session zůstanou nedotčené,
2. **protějšek `ae0.224` je na `.5` aktivní** — drží se tím WARN větev matice
   AR‑23 (baseline deaktivováno → subject aktivní), která by jinak regenerací
   `.4` zmizela celá a zůstal by jen směr FAIL,
3. je to **jediná služba s kombinovaným důvodem** `RI + interface deactivated`
   (AR‑20/AR‑21).

Na `172.20.20.5` je dnes deaktivované `et-0/0/5`, `et-0/0/8`, `ae0`,
`ae0.4094`, `irb`, `irb.4094` a celý kontejner `routing-instances`. Do rozsahu
to nespadá — `.5` se neregeneruje.

### Kde dnes leží prázdná MX BFD nahrávka

`tests/collectors/test_conformance.py::test_every_collector_has_its_fixtures`
asertuje **rovnost množin** jmen souborů na disku a jmen odvozených
z collectorů. Přidat nahrávku jako druhý soubor vedle (`bfd.2.xml`) proto
nejde — test by ji ohlásil jako osiřelou. Nahrávka se vymění na místě.

Prázdný výpis je dnes **nosný**: parametrizace `test_specific_check_sees_data`
vynechává `("junos", "bfd_session_state")` s komentářem, že fixture je záměrně
prázdná. Ten komentář výměnou přestane platit a musí zmizet spolu s ní.

## Přejímací požadavky

### AR-30 — MX BFD nahrávka je neprázdná a inventory `.4` odpovídá laborce

`tests/fixtures/rpc/junos/*.xml` a `172.20.20.4.yml` (plus jeho kopie
`tests/fixtures/172.20.20.4.yml`, dnes bajtově shodná) se pořídí znovu proti
laborce ve stavu popsaném výš.

Očekávaný výsledek **není predikce** — inventory byla při psaní tohoto specu
vygenerována do scratchpadu (`mx_parser.py 172.20.20.4 --auth password
-u admin`) a změřena:

```
schema 4, 24 služeb
  ge-0/0/3      interface_active: false
  ge-0/0/3.0    interface_active: false, routing_instance_active: false
```

Tedy dvě položky s `interface_active: false` a jedna s
`routing_instance_active: false`, žádná další. Počet služeb se nemění (24,
stejně jako v commitnuté inventory) — deaktivovaná služba z inventory
nevypadává, přesně podle AR‑20. Jiný výsledek při provádění plánu znamená, že
se laborka mezitím změnila; plán se v tom případě zastaví a stav se ověří
znovu, místo aby se čísla dopsala podle skutečnosti.

### AR-31 — `bfd_session_state` vydá na `junos` reálný verdikt

Do parametrizace `test_specific_check_sees_data` přibude
`("junos", "bfd_session_state")` a komentář o záměrně prázdné fixture se
odstraní.

„Aspoň jeden ne-SKIP" tady drží šev poctivě — na rozdíl od statik, kde ho
vydá i manufakturovaný FAIL ze samotného záměru (AR‑29). `BfdSessionStateCheck`
iteruje přes záměry z inventory a bez přečtené oblasti `bfd` by vydal jen
„session chybí"; **to je potřeba ověřit, ne předpokládat** — plán mutanta
(`ctx.subject.get("bfd_x")`) pustí a zapíše výsledek. Pokud mutant přežije,
platí stejný závěr jako u statik a k parametru se přidá test vyžadující
konkrétně PASS na `152.11.13.2`.

### AR-32 — parser MX BFD odpovědi má vlastní test proti reálnému XML

Test na úrovni collectoru nad novou nahrávkou. Mutant, kterého zabíjí:
**vypuštění smyčky přes `bfd-client`**, po němž `clients` zůstane prázdný
seznam. Check bez klientů nerozliší session drženou BGP od jiné — což je
důvod, proč collector vůbec používá `detail` variantu (docstring
`collectors/bfd.py:6`).

Test asertuje obě session zvlášť, včetně `remote_state` `Up` a `AdminDown` —
ty jsou v odpovědi jediné dvě pole, která odlišují „soused nás nevidí" od
„soused nás vidí a shodil to schválně".

### AR-33 — top-level `<protocols inactive>` má vlastní test

`tests/parsers/test_inactive.py` procvičuje `<protocols>` a `<bgp>` jen
vnořené pod deaktivovanou `routing-instances`. K top-level kontejneru přitom
vede **samostatná cesta**: `_parse_default_bgp_neighbors()` (`mx_parser.py:952`)
plní `self.default_bgp_neighbors` a `self.default_bfd`, které pak konzumují
služby **bez** routing-instance (`mx_parser.py:1570`).

Fixture s top-level `<protocols inactive="inactive">` a asertace, že služba
bez routing-instance dostane prázdné `bfd` i `bgp_neighbor`. Podle pravidla
z vlny 3 se test píše pro **oba** parsery zvlášť — jeden mutant v `mx_parser.py`
neříká nic o `evo_parser.py`.

Mutant: v `_is_inactive` (`:406`) se zastaví chůze po předcích na prvním uzlu.

### AR-34 — čtyři drobnosti z bodu 5

**(a) Redundantní disjunkt.** `mx_parser.py:1103` a `evo_parser.py:1103`:
`not (physical_inactive or self._is_inactive(unit_node))`. Po AR‑19 dojde
chůze po předcích z `unit` na fyzické rozhraní stejně, takže první disjunkt
nic nepřidává. Odstraní se v obou parserech; `diff | wc -l` musí zůstat **146**.

Tohle je jediná změna produkčního kódu, u které hrozí, že by ji sada
nezachytila. Plán proto **nejdřív** ověří, že existující test na deaktivované
fyzické rozhraní s aktivní jednotkou padne, když se odstraní i druhý disjunkt
— pokud ne, chybí pokrytí a doplní se dřív, než se maže.

**(b) Tichý default v `checks/routes.py:152`.** `subject.get("active", True)`
znamená, že by se regrese collectoru (přejmenovaný nebo vypuštěný klíč)
přečetla jako **PASS**, ne jako chybějící kontrola. Je to jediné takové místo
v repu.

Nové chování: chybí-li klíč `active` v **měření** (`subject`) úplně, check
vydá `Outcome.SKIP` se zprávou, že měření aktivitu routy neobsahuje. Hodnota
`False` se chová dál přesně jako dnes (AR‑25). Default `True` na `:152`
**zaniká**.

Na `:153` (`baseline.get("active", True) if baseline else None`) default
**zůstává**, a to záměrně. Kdyby zanikl i tam, chybějící klíč v baseline by
skončil jako `was_active=True`, což kód o pár řádků níž překlápí na
`Outcome.BROKEN` — tedy eskalace chybějícího údaje na FAIL, přímo proti R‑2,
o který se opírá AR‑25. Baseline navíc může legitimně pocházet ze staršího
schematu; `subject` ne, ten vzniká vždy aktuálním collectorem. Asymetrie obou
řádků je tím pádem věcná, ne přehlédnutí — plán ji zapíše do komentáře
u kódu, aby ji příští čtenář nesjednotil.

Rozlišení proti (b)-alternativě „nechat default, jen přidat test": test by
hlídal, že se `True` doplní, tedy zabetonoval by chování, které je špatné.

**(c) Přímé pokrytí `_is_inactive` na `rib` a `group`.** Volání na
`mx_parser.py:697` (`rib`) a `:817` (`group`) mají dnes jen nepřímé pokrytí
přes jednu sdílenou fixture. Dostanou vlastní test s vlastním mutantem
(`_node_is_inactive` ignoruje atribut na tomto typu uzlu), aby budoucí změna
atributové sémantiky spadla na konkrétním místě.

**(d) Docstring.** `test_inactive_route_that_was_inactive_before_passes`
slibuje širšího mutanta, než jaký ten test doopravdy zabíjí. Docstring se
opraví na mutanta, kterého test skutečně zabíjí — ověřeno spuštěním, ne
přečtením.

### AR-35 — ripple po regeneraci se vyjmenuje měřením, ne odhadem

Úloha 12 vlny 3 ukázala, že tabulka předpovězených opravovaných testů byla
vedle na obou stranách: predikovala kaskádu, která nenastala, a minula dva
konkrétní důvody, které nastaly.

Plán proto **nesmí obsahovat seznam testů, které regenerace rozbije.**
Místo toho předepíše krok: regenerovat, spustit sadu, a teprve pak
u každého padlého testu rozhodnout, jestli jde o (a) změněnou realitu
laborky, nebo (b) regresi kódu — a rozhodnutí zdůvodnit. Kategorie (b) je
důvod se zastavit, ne opravit očekávání.

Očekávatelný dopad, který **není** predikcí seznamu: služby na `ge-0/0/2.*`,
`ge-0/0/4*` a `ge-0/0/5*` přejdou z deaktivovaných na aktivní, takže
`deactivation_state` u nich přestane hlásit WARN a checky, které dosud
vracely SKIP, začnou měřit.

## Přejímací kritéria implementačního plánu

Platí kritéria z vlny 3 ([`2026-07-30-deaktivovana-konfigurace-design.md`](2026-07-30-deaktivovana-konfigurace-design.md),
sekce „Přejímací kritéria implementačního plánu") beze změny, a k nim pravidlo,
které si vlna 3 do plánu vlny 4 sama vyžádala:

**U každého testu, který plán předepisuje doslova, se mutant pustí už při
psaní plánu, ne až při jeho provádění.** Vlna 3 to pravidlo měla zapsané
a přesto dvakrát předepsala test, který mutanta jmenoval a nezabíjel ho —
protože autor plánu mutanta nikdy nespustil, jen popsal. Když to nejde
(test se opírá o kód, který ještě neexistuje), plán to musí říct nahlas
a uložit mutanta jako povinný krok úlohy.

Konkrétně to znamená, že mutanti u **AR‑31, AR‑32, AR‑33 a AR‑34(a)** se
pustí ve fázi psaní plánu. U AR‑33 a AR‑34(a) je to přímočaré — jde o dnešní
kód i dnešní fixtures. U AR‑31 a AR‑32 mutant potřebuje neprázdnou `junos`
BFD nahrávku, která je až výstupem AR‑30; „ještě to nejde" ale neplatí ani
tam, protože nahrávka **už je pořízená** ve scratchpadu a na dobu spuštění
mutanta se dá dočasně podstrčit na místo commitnuté fixture. Přesně tak
vznikl důkaz o osmi SKIPech v sekci „Stav deaktivace v laborce".

A past z úlohy 5: **mutantí bloky končí `git checkout <soubor>`, takže se
pouští až po commitu.** Před commitem si tím implementace smaže vlastní práci.

## Zapsané předpoklady

### Laborka se během vlny nesmí měnit

AR‑30 fixuje stav `.4` na dva deaktivované uzly. Jakákoliv další změna
konfigurace v laborce během vlny znehodnotí regenerovanou inventory i nahrávky
a projeví se jako nevysvětlitelný ripple. Když bude potřeba laborkou hnout,
udělá se to **před** AR‑30, ne mezi ním a AR‑35.

### `.4` a `.5` se rozešly a je to v pořádku

Commitnutý pár modeluje migraci MX → PTX: baseline `.4` má deaktivované
služby, subject `.5` je má aktivní. Živá laborka dnes ukazuje skoro opačný
obraz. Pár se tím nestává neplatným — je to konstruovaný scénář, ne snímek.
Regenerace `.4` ho posouvá o krok „migrace se vrátila zpět" u služeb CPE13
a CPE14, a `ge-0/0/3` je to jediné, co z původního směru zůstává.

### Prázdný BFD výpis přestane být v repu zastoupený

Po AR‑30 nemá žádná nahrávka nulový počet session. Ověřeno čtením, ne úvahou:
`tests/collectors/test_bfd.py::test_empty_output_is_a_valid_state` čte
**přímo `junos` fixture** a asertuje `result == {}`, takže AR‑30 ho rozbije.
Prázdný výpis je platný stav (docstring `collectors/bfd.py:10`) a nesmí
zůstat nepokrytý — test dostane vlastní syntetické XML, ne nahrávku, a tím
se odváže od stavu laborky nadobro.

Ve stejném souboru padne s AR‑30 i výjimka
`if platform == "junos-evo"` v `test_returns_mapping_keyed_by_neighbor`
a modulový docstring, který prázdnou `junos` fixture popisuje. Obojí je
materiál AR‑32, ne samostatná položka.
