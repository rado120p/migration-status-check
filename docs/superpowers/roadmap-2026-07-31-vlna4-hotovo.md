# Vlna 4 hotová — MX BFD na živých datech a dokončení deaktivace, stav k 2026-07-31

**Výchozí bod:** větev `vlna4-mx-bfd-a-dokonceni-deaktivace`, všech sedm úloh
hotových, závěrečné review celé větve uzavřené.
**605 testů zelených, 0 přeskočených.** Zámek parserů drží na 146 řádcích.

Zadání byla „Co zbývá" v [`roadmap-2026-07-30-vlna3-hotovo.md`](roadmap-2026-07-30-vlna3-hotovo.md),
body 2, 4 (první odrážka) a 5. Návrh je
[`specs/2026-07-31-mx-bfd-a-dokonceni-deaktivace-design.md`](specs/2026-07-31-mx-bfd-a-dokonceni-deaktivace-design.md),
provedení [`plans/2026-07-31-vlna4-mx-bfd-a-dokonceni-deaktivace.md`](plans/2026-07-31-vlna4-mx-bfd-a-dokonceni-deaktivace.md).

---

## Co vlna 4 přinesla

- **AR-30** — regenerace `172.20.20.4.yml` a `tests/fixtures/rpc/junos/*.xml`
  proti laborce. `.5` se **nedotkla** (viz „Co zbývá", bod 3).
- **AR-31 / AR-32** — `bfd_session_state` vydá na `junos` reálný verdikt.
  K parametrizaci `test_specific_check_sees_data` přibylo
  `("junos", "bfd_session_state")` a přibyl
  `test_bfd_check_really_reads_the_session_table`, který vyžaduje konkrétně
  **PASS** — „aspoň jeden ne-SKIP" tu šev nedrží (viz níž). Collector dostal
  vlastní testy proti reálnému MX XML.
- **AR-33** — test na top-level `<protocols inactive="inactive">`, tedy na
  cestu `_parse_default_bgp_neighbors()` / `default_bfd`, kterou dosavadní
  testy míjely.
- **AR-34** — smazány **tři** redundantní guardy v obou parserech
  (`:697` `rib`, `:817` `group`, `:1103` disjunkt na `unit`) a doplněny dva
  testy, které tím teprve začaly něco měřit.
- **AR-34b** — test deaktivované jednotky pod **aktivním** fyzickým
  rozhraním. V roadmapě vlny 3 tahle díra nebyla; našel ji mutant.
- **AR-34c** — chybějící `active` v měření je `SKIP`, ne tichý PASS.
- **AR-34d** — docstring
  `test_inactive_route_that_was_inactive_before_passes` jmenuje mutanta,
  kterého test opravdu zabíjí, a výslovně se zříká toho, co hlídá sourozenec.

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""   # 605 passed, 0 skipped
diff mx_parser.py evo_parser.py | wc -l    # 146
```

**Laborka:** `172.20.20.4` (vMX, platforma `junos`), `172.20.20.5`
(PTX10002-36QDD, platforma `junos-evo`), uživatel `admin`, autentizace
**heslem, ne klíčem**. `MIG_LAB_PASSWORD` je v `~/.bashrc` pod stráží na
neinteraktivní shell — načíst explicitně (`eval "$(grep -h
MIG_LAB_PASSWORD ~/.bashrc)"`). `mx_parser.py` čte heslo přes `getpass`,
takže se mu podává rourou (`printf '%s\n' "$MIG_LAB_PASSWORD" | ...`).

Stav laborky, proti kterému je `.4` nahrané: deaktivované právě `ge-0/0/3`
a instance `EVPN-VPWS-CPE24-UNI`, nic jiného. Uživatel to takhle nastavil
2026‑07‑31 na vyžádání — volba je jediná, která zároveň nenese BFD, drží
WARN větev matice AR‑23 a pokrývá kombinovaný důvod `RI + interface
deactivated`.

Na `.5` je dnes deaktivované `et-0/0/5`, `et-0/0/8`, `ae0`, `ae0.4094`,
`irb`, `irb.4094` a celý kontejner `routing-instances`. **Commitnutý pár se
tím s laborkou rozešel** a je to v pořádku: pár je konstruovaný migrační
scénář, ne zrcadlo.

---

## Co vyšlo jinak, než plán čekal

Šest odchylek, z toho tři byly chyby v zadání, ne v provedení.

### 1. Premisa bodu 2 neplatila

Roadmapa vlny 3 vedla bod 2 jako „MX-specifické parsování BFD odpovědi není
pokryté na živých datech", tedy že MX odpověď má vlastní tvar. **Nemá** —
nahrávka z `.4` je element po elementu shodná s `junos-evo`, liší se jen
jména rozhraní. `BfdCollector.parse()` nemá platformní větev a nepotřebuje
ji. Bod se tím scvrkl na „zamrazit neprázdnou nahrávku a projít ji testem".

### 2. Mutant AR-31 byl změřený nad smíchanou sadou fixtures

Nejdůležitější nález celé vlny, a je metodický.

Spec tvrdil, že mutant `ctx.subject.get("bfd_x", {})` shodí obě
parametrizace, a vyvodil z toho, že „aspoň jeden ne-SKIP" tu šev drží.
**Neplatilo.** To měření běželo s **novou `bfd.xml` proti staré `bgp.xml`**,
ve které bylo BGP na `.4` ještě Idle, takže check SKIPoval. Po regeneraci
celé `junos` sady je BGP Established, check si z holého záměru vyrobí FAIL
„bez session" — a ten je ne-SKIP, takže slabší asertace projde naslepo.
Přesně týž manufakturovaný verdikt, kvůli kterému vlna 3 vyhodila statiky
z téže parametrizace (AR‑29).

Našel to implementer úlohy 5 tím, že se odmítl spokojit s tím, že mu čísla
nesedí se zadáním. Spec i plán byly opraveny a přibyl test vyžadující PASS.

**Pravidlo do vlny 5:** *mutant puštěný nad nekonzistentní sadou fixtures
neměří nic.* Pravidlo vlny 3 znělo „mutant se pouští, ne popisuje"; tohle je
jeho druhá půlka — **pouští se nad tím stavem repa, ve kterém poběží
doopravdy.** Když se v téže vlně regenerují fixtures, mutanti změření před
regenerací se musí pustit znovu po ní.

### 3. `rib` a `group` nebyly mezera v pokrytí, ale její příčina

Roadmapa vlny 3 vedla „volání `_is_inactive` na uzlech `rib` a `group` mají
jen nepřímé pokrytí" jako drobnost k doplnění testů. Testy ale nešly napsat:
oba guardy volaly `_is_inactive` na uzlu, který atribut `inactive` nese
**přímo**, takže vracely `True` i pod mutantem, který chůzi po předcích
úplně zrušil. **Guard stínil to, co se mělo testovat.**

Totéž platilo o disjunktu `physical_inactive or` na `:1103`, vedeném
v roadmapě jako samostatná drobnost. Tři odrážky, jeden vzorec. Teprve po
smazání všech tří začaly testy mutanta zabíjet.

Čísla, ať jsou reprodukovatelná: mutant chůze po předcích proti
`tests/parsers/test_inactive.py` shodil **8** testů, dokud guardy stály.
Po smazání samotného disjunktu na `:1103`, bez jediného nového testu, **10**
— ty dva navíc jsou obě parametrizace `test_deactivated_interface_is_flagged`.
Konečných **14** je stav po smazání všech tří guardů **i** po přidání nových
testů; není to přírůstek ze samotného mazání.

### 4. Deaktivovaná jednotka pod aktivním rozhraním neměla test

Mutant „úroveň jednotky se ignoruje" (`active=not physical_inactive`)
přežíval **celou sadu**. Všechny fixtures deaktivovaly až fyzické rozhraní,
takže se zděděná a vlastní deaktivace nedaly rozlišit. V roadmapě vlny 3
tenhle bod nebyl — vypadl z mutanta puštěného při psaní specu.

### 5. Plán jmenoval u jednoho mutanta špatný test

Krok 7 úlohy 2 čekal mezi padlými testy `test_deactivated_unit_under_active_interface_is_flagged`.
Ten pod mutantem chůze po předcích **prochází právem** — jeho fixture nese
`inactive` přímo na `<unit>`, takže chůze a přímý dotaz dají stejnou
odpověď. Nahlásil to implementer úlohy 2; plán byl opraven.

### 6. Zdůvodnění asymetrie v `checks/routes.py` bylo špatně v obou premisách

Našlo až závěrečné review celé větve. Komentář tvrdil, že default na
`baseline` zůstává, protože (a) baseline může pocházet ze staršího schematu
a (b) bez defaultu by chybějící klíč skončil jako BROKEN.

- **(a) je nemožné** — `Snapshot.from_dict` vyhodí `SnapshotVersionError` na
  jakoukoliv neshodu `schema_version` a baseline i subject jdou touž cestou.
- **(b) má obrácené znaménko** — *s* defaultem dá chybějící klíč `True`
  → `BROKEN`; *bez* něj `None` → `DEGRADED`. Ponechaný default tu eskalaci
  na FAIL **vyrábí**.

Opraveno bylo zdůvodnění, ne chování — to je mimo rozsah vlny. Otázka, kterou
to odkrylo, je níž v „Co zbývá".

---

## Co zbývá

### 1. Příznaky na hlubších úrovních konfigurace

Beze změny z vlny 3, včetně obou částí:

- Deaktivovaná jednotlivá `route`, `bfd-liveness-detection` nebo `neighbor`
  se dál vypouští ze záměru **beze stopy**.
- **Top-level `<routing-options inactive>` zahodí globální statiky beze
  stopy.** Služby v default instanci si nechají `routing_instance_active:
  true`, takže se deaktivace nikde neprojeví. Je to **táž díra**, ne
  samostatná položka — rozhodnutí uživatele z 2026‑07‑31 je odložit obojí
  společně.

### 2. Report

- **F-2** — podřádky se jménem RIB u statických rout.
- **F-11** — dvě kopie logiky pro link-local adresy.
- **F-14** — `unassigned.static_routes`/`.bfd_sessions` jde jen do JSON,
  textový report je nevypisuje.

Vlastní spec, podle rozhodnutí z 2026‑07‑28.

### 3. Resync `172.20.20.5.yml` a `tests/fixtures/rpc/junos-evo/`

Vlna 4 regenerovala jen `.4`. Laborka na `.5` se mezitím výrazně změnila
(viz výš). Resync je vlastní položka s vlastním rozpočtem na ripple —
přepsal by očekávání testů na obou stranách najednou, a v `.5` je dnes
deaktivované i `et-0/0/8`, na kterém sedí obě BFD session té strany.

**Než se do toho půjde:** dnešní pár drží obě větve matice AR‑23 (FAIL
i WARN). Resync `.5` je může zlikvidovat stejně, jako to hrozilo u `.4` —
takže se stejně jako v AR‑30 musí předem rozmyslet, co v laborce nechat
deaktivované, a teprve pak nahrávat.

### 4. Baseline bez `active` dnes eskaluje na FAIL

Otevřeno nálezem 6 výš. `checks/routes.py:169` čte
`baseline.get("active", True)`; chybějící klíč v baseline tedy skončí jako
`was_active=True` → `Outcome.BROKEN`, a protože
`StaticRouteStatusCheck.default_severity` je `CRITICAL`, jako **FAIL**.

Otázka pro vlnu 5: **nemá chybějící `active` v baseline dávat `DEGRADED`
(tedy WARN) podle R‑2?** Argument pro je stejný, jaký R‑2 už použil na
„bez baseline neni z ceho poznat". Argument proti je, že dnešní snapshot
schema klíč vždycky nese, takže je to hypotetický stav — jenže přesně to
samé se říkalo o `subject`, a AR‑34c to změnil.

### 5. Vedlejší důsledek AR-34c, který spec neprobral

`Status.SKIP` má vyšší prioritu než `Status.PASS`
(`models/result.py:44-48`). Jedna routa bez klíče `active` proto stáhne
celý `static_route_status` daného scopu na SKIP a zakryje PASSy sourozenců.
Obhajitelné („nešlo doměřit"), ale je to nové chování, které spec nezvažoval.

**Opraveno 2026‑08‑03:** tvrzení neplatí. `engine.py:145` SKIPy z hlasování
odfiltruje dřív, než se hlasuje, takže scope zůstane PASS a sourozenci zakrytí
nejsou. Změřeno ve vlně 6 — viz bod 4 v
[`roadmap-2026-08-03-vlna5-hotovo.md`](roadmap-2026-08-03-vlna5-hotovo.md).
Text výše se nemaže: je to zápis toho, co si vlna 4 tehdy myslela.

### 6. Drobnosti, které závěrečné review nechalo projít

Vesměs kosmetika; žádná neblokovala merge.

- `ACTIVE_TOP_LEVEL_PROTOCOLS` vzniká `.replace()` na deaktivované fixtuře.
  Kdyby se literál změnil, replace by tiše nic neudělal — chytí to kontrolní
  test, proto je odložené.
- `test_empty_output_is_a_valid_state` je parametrizovaný přes obě platformy,
  ačkoliv `BfdCollector.parse` na platformu nevětví. Pin invariantu, ne
  pokrytí.
- `test_mx_sessions_are_recorded_verbatim` a `test_mx_client_names_are_collected`
  duplikují své `junos-evo` dvojče; šlo by je sloučit do parametrizovaných
  párů.
- Nový SKIP `Finding` v `checks/routes.py` nenese `baseline_value`, ačkoliv
  `was` je o pár řádků výš spočítané. (Dva reviewery se na tomhle bodu
  neshodly — sourozenecké SKIP nálezy v `evpn.py`/`ifaces.py` ho taky
  nenesou.)
- `test_route_without_active_key_is_skipped` neasertuje `value == "bez dat"`.
- Tři testy v `tests/collectors/test_bfd.py`, kterých se vlna dotkla,
  nejmenují mutanta: `test_returns_mapping_keyed_by_neighbor`,
  `test_empty_output_is_a_valid_state`, `test_mx_sessions_are_recorded_verbatim`.
  Chybějící tvrzení je mnohem menší vada než tvrzení nepravdivé, ale proti
  domácímu pravidlu to je.

---

## Pravidla do plánu vlny 5

Vlna 3 předala pravidlo „u každého testu, který plán předepisuje doslova, se
mutant pustí už při psaní plánu". Vlna 4 ho dodržela — všech šest mutantů
bylo puštěno při psaní specu — a **stejně vyrobila špatné zadání**, protože
jeden z nich běžel nad smíchanou sadou fixtures.

Přidávají se proto dvě věty, obě zaplacené:

1. **Mutant se pouští nad tím stavem repa, ve kterém poběží doopravdy.**
   Když vlna regeneruje fixtures, všichni mutanti změření předtím se pouští
   znovu potom. (Nález 2.)
2. **Když implementer hlásí, že mu měření nesedí se zadáním, má přednost
   měření.** Ve vlně 4 to nastalo třikrát (nálezy 2, 5 a nepřímo 6) a ve
   všech třech případech se mýlilo zadání, ne provedení. Zadání psal tentýž,
   kdo ho pak kontroloval — proto je tenhle signál cenný a nesmí se
   přehlasovat.

A pořád platí past z vlny 3: **mutantí bloky končí `git checkout <soubor>`,
takže se pouští až po commitu.**
