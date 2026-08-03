# Vlna 5 hotová — skupiny řádků a NEZARAZENO v textovém reportu, stav k 2026-08-03

**Výchozí bod:** větev `vlna5-report-skupiny-a-nezarazeno`, všech osm úloh
hotových, ostré ověření (tahle úloha) provedeno.
**631 testů zelených, 0 přeskočených.** Zámek parserů drží na 146 řádcích.

Zadáním byl bod „Report" v [`roadmap-2026-07-31-vlna4-hotovo.md`](roadmap-2026-07-31-vlna4-hotovo.md)
(F-2, F-11 a jejich rozšíření). Návrh je
[`specs/2026-08-03-report-skupiny-a-nezarazeno-design.md`](specs/2026-08-03-report-skupiny-a-nezarazeno-design.md),
provedení [`plans/2026-08-03-vlna5-report-skupiny-a-nezarazeno.md`](plans/2026-08-03-vlna5-report-skupiny-a-nezarazeno.md).

---

## Co vlna 5 přinesla

Report vykresloval BGP countery a statické routy pod holým popiskem
(`BGP active-prefix-count`) bez peeru a bez RIB — peer se dvěma RIB dával
osm nerozlišitelných řádků. Vlna zavedla pojmenované skupiny řádků a
sekci pro objekty, které `engine.py` nedokáže spárovat s žádnou službou.

- **AR-41** — link-local predikáty (`is_link_local` apod.) sjednoceny do
  jednoho výskytu v novém `migration_validator/addressing.py`, dřív žily
  duplicitně ve `reachability.py` a `ping.py` (F-11).
- **AR-36** — pole `group` na `Finding` a `CheckResult`; BGP countery nesou
  `BGP {peer} / {rib}`, statické routy `Staticke routy`.
- **AR-37 / AR-38** — `view.py` skládá skupiny řádků, `text_report.py` je
  sází pod odsazený nadpis bez pomlček a započítává jejich šířku do rámce
  bloku.
- **AR-40** — sloupec TYP v sekci NESPAROVANO se doplňuje na šířku (dřív
  ujížděl, když se lišila délka jména typu).
- **AR-39** — nová sekce NEZARAZENO na konci reportu: objekty, které
  existují jen na straně subjektu a nenašly párovou službu, se dřív
  vypisovaly jen do JSON (F-14). Sekce se vypisuje vždy, i prázdná
  (`(nic)`), aby chybějící sekce neznamenala „nic nezařazeného" tiše
  splývající s „sekce se nevytiskla".

Osm commitů, `a4a403b..fc7143b`.

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""   # 631 passed, 0 skipped
diff mx_parser.py evo_parser.py | wc -l    # 146
```

Ostrý report ze společných fixtures (`tests/fixtures/172.20.20.4.yml` /
`.5.yml`) se vyrenderuje dočasným testem podle
[`task-8-brief.md`](../../.superpowers/sdd/2026-08-03-vlna5-report-skupiny-a-nezarazeno/task-8-brief.md),
krok 1 — soubor je jednorázový a po přečtení se maže. Naplněná sekce
NEZARAZENO na těchto fixtures nejde vidět (je prázdná); k jejímu vytištění
slouží pomocník `_unassigned_result()` v
`tests/reporting/test_text_report.py`.

---

## Co vyšlo jinak, než plán čekal

Pět odchylek, všechny změřené, ne odhadnuté.

### 1. Ripple byl 9 testů, ne 220 řádků grepu

Spec u AR-42 vedl `grep` na dotčené popisky jako horní odhad (220 řádků
napříč `tests/`) a výslovně zakázal z něj dělat seznam padlých testů —
seznam se měl zjistit teprve po provedení. Prototyp úlohy 5 předpověděl 9
testů ve dvou souborech; po provedení padlo **přesně těch devět**
(`tests/checks/test_bgp.py` osm, `tests/checks/test_routes.py` jeden), ani
jeden navíc. Skutečný ripple byl o dva řády menší než horní odhad a
soustředěný přesně tam, kde plán čekal.

### 2. Mutant M2 přežil první návrh testu na AR-37

Šev `view.py` ↔ `text_report.py` (pořadí neseskupených řádků před
skupinami) potřeboval dva testy, jeden na každou stranu. Test nad datovou
strukturou samotnou (`test_ungrouped_rows_stand_before_groups` v
`test_view.py`) prohozené pořadí v *rendereru* nechytí — data zůstanou
správná, prohodí se až sazba. Zjištěno při psaní plánu na prototypu, oba
testy dodány v úloze 4; po commitu `f500581` mutant M2 zabil jen nový test
`test_rendered_block_puts_ungrouped_rows_above_the_first_group_header`,
sourozenec v `test_view.py` mutanta ověřeně přežil — přesně to je smysl
toho, že test stojí na obou stranách švu.

### 3. Test na prázdnou sekci NEZARAZENO nic neměřil

Nejostřejší nález vlny. Assert `"(nic)" in out` byl splněný, i když
prázdná větev `_unassigned_lines` nic nevytiskla — protože **týž řetězec
`(nic)` tiskne sekce NESPAROVANO** hned nad ní, a fixture neměla žádné
nespárované položky. Test tedy měřil existenci NESPAROVANO, ne NEZARAZENO.
Zdroj byl plán, ne implementace úlohy 7 — implementer napsal test přesně
podle zadání a ten prošel; teprve review úlohy 7 to **dokázala mutantem**
(ručně odstraněný `lines.append("  (nic)")` z prázdné větve testem
neprocházel), nevydedukovala z čtení kódu. Oprava: test hledá `(nic)` na
řádku hned za nadpisem `NEZARAZENO (jen subject)`
(`lines[start + 1] == "  (nic)"`), stejně jako už dělal sourozenec
`test_unassigned_detail_column_stands_in_one_line`. Je to týž tvar jako
nález 2: test, který vypadá, že měří, a neměří — jen o úroveň jinde (sdílený
literál napříč sekcemi místo sdíleného pořadí napříč vrstvami).

### 4. Mutant M5 shodil čtyři testy, ne dva

Plán u AR-38 čekal, že mutant „`Section.all_rows()` vrací jen neseskupené
řádky" shodí dva testy (šířku bloku a `all_rows()` samotné). Ve
skutečnosti padly čtyři: `_block` staví slovník `changes` z `all_rows()`,
takže bez řádků ve skupinách sazba spadne na `KeyError` dřív, než vůbec
dojde na výpočet šířky — `changes[id(row)]` u skupinového řádku nemá kam
sáhnout. Guard je tím silnější, než plán předpokládal, a žádný z padlých
testů nebyl upravován, aby vyšel podle plánu (měření má přednost).

### 5. Globální podmínka o diakritice byla nepřesná — dvakrát

Nejdřív hrubě: plán tvrdil, že soubory v `migration_validator/` a `tests/`
jsou bez diakritiky. Opraveno uprostřed vlny (commit `6f537bc`) na
změřené „9 z 94 souborů ji nese".

Ostré ověření v této úloze přeměřilo tutéž podmínku znovu, nad finálním
stavem větve (`fc7143b`, 94 souborů), a **číslo 9 samo nesedí** — souborů s
diakritikou je **7**: `migration_validator/collectors/evpn.py`,
`migration_validator/reporting/text_report.py`,
`tests/checks/test_bgp.py`, `tests/checks/test_routes.py`,
`tests/collectors/test_bfd.py`, `tests/parsers/test_inactive.py`,
`tests/reporting/test_text_report.py`. Přeměřeno i zpětně na commitu
`6f537bc` (kde se „9" poprvé objevilo) — i tam vychází 7, nikoli 9. Zdroj
nesrovnalosti nebyl dohledán. Dopad je nulový, protože pravidlo samo („do
souboru, který diakritiku nemá, ji nezanášej") implementeři úloh 5–7
dodrželi bez ohledu na přesné číslo. Zapsáno, protože „měření má přednost
před zadáním" platí i pro čísla v samotné roadmapě.

### Vedlejší nález review úlohy 5 — dvě opravy testů byly zesílení, ne oslabení

Nejde o odchylku od plánu, ale stojí to za zaznamenání, protože se to
snadno plete se slabnutím testu při úpravě po přejmenování popisků:

- `test_session_findings_carry_peer_family`: `all(finding.label == "BGP
  status" ...)` nešlo po zavedení peera v popisku splnit jedním řetězcem
  (dva peery, dva popisky) — nahrazeno množinovou shodou
  `{finding.label for ...} == {"BGP status (152.11.13.2)", "BGP status
  (2001:abcd:11:13::b)"}`. Původní tvrzení bylo pravdivé jen náhodou (díky
  společnému popisku před vlnou); nové tvrdí totéž, ale správně.
- `test_prefix_counts_produce_a_row_per_counter`: filtr
  `startswith("BGP ")` bral omylem i SKIP řádek `"BGP prefixy"` (souhrnný
  nález bez baseline peera). Po přejmenování counterů na
  `{key}-prefix-count` by starý filtr vybíral přesný opak toho, co měl.
  Nahrazeno `endswith("-prefix-count")`, které je vůči původnímu záměru
  **užší, ne širší**.

---

## Co zbývá

### 1. Příznaky na hlubších úrovních konfigurace

Beze změny z vlny 3 a 4, včetně obou částí (deaktivovaná jednotlivá
`route`/`bfd-liveness-detection`/`neighbor` a top-level
`<routing-options inactive>` se dál vypouští ze záměru beze stopy).

### 2. Resync `172.20.20.5.yml` a `tests/fixtures/rpc/junos-evo/`

Beze změny z vlny 4. Vlna 5 se laborky ani fixtures netýkala.

### 3. Baseline bez `active` dnes eskaluje na FAIL

Beze změny z vlny 4. Otázka pro některou z dalších vln: nemá chybějící
`active` v baseline dávat `DEGRADED` (WARN) podle R-2, stejně jako to
AR-34c udělalo pro `subject`?

### 4. Priorita SKIP nad PASS

Beze změny z vlny 4 (`models/result.py:44-48`). Jedna routa bez `active`
stáhne celý `static_route_status` scopu na SKIP a zakryje PASSy sourozenců.

### 5. Drobnosti ze závěrečného review vlny 4

Beze změny — pět kosmetických bodů (viz roadmapa vlny 4, „Co zbývá" bod 6),
žádná neblokovala merge tehdy ani teď.

### 6. Odložené drobnosti z téhle vlny

- `tests/reporting/test_text_report.py` docstring
  `test_rendered_block_puts_ungrouped_rows_above_the_first_group_header`
  nese slovo „Sesterský" s diakritikou v souboru, který jich měl **před
  vlnou 5 nula**. Zdroj byl plán; plán opraven commitem `6f537bc`, kód ne.
- Docstringy `test_bgp_group_carries_peer_and_rib` a
  `test_static_routes_from_different_ribs_share_one_group` tvrdí „s jedinou
  RIB by mutant prošel". Neplatí — assert porovnává přesné řetězce, takže
  mutant padne i s jednou RIB. Dvě RIB jsou pořád správná fixture (modelují
  skutečný problém v reportu, kdy peer se dvěma RIB dával nerozlišitelné
  řádky), jen zdůvodnění v docstringu je nadhodnocené.
- ~~Podmínka o diakritice tvrdí „9 z 94".~~ **Vyřízeno 2026‑08‑03:** ostré
  přeměření implementera úlohy 8 dalo **7 z 94** a mělo pravdu — původní
  devítka smíchala jmenovatele, počítala i dvě YAML fixtures, zatímco
  devadesát čtyři je jen soubory `.py`. Plán opraven.

### 7. Sdílený syntetický pomocník dává IPv6 peerům skupinu `inet.0`

Ostré ověření (tato úloha) našlo, že na obou fixtures každý BGP peer,
IPv4 i IPv6, nese ve svém skupinovém popisku countery `BGP {peer} /
inet.0` — u IPv6 peerů to má být `inet6.0`, jak správně dělá sousední
skupina statických rout ve stejné sekci. Zdroj **není**
`migration_validator/checks/bgp.py` (`_prefix_finding` bere `rib_name`
beze změny z dat, která dostane), ale `tests/conftest.py:100-109`
(`_facts_for`), který pro každého peera bez ohledu na rodinu syntetizuje
`"ribs": {"inet.0": {...}}`. Report jako celek má navíc všechny countery
uniformně `14/14/14/3` — sdílené fixtures nikdy nemodelovaly RIB podle
rodiny ani reálné hodnoty per peer. Oprava patří do `tests/conftest.py`,
ne do reportu; přidat ji sem, ne do „Co vyšlo jinak", protože ostré
ověření vadu nenašlo v kódu vlny.

### 8. Motivující scénář AR-36 není na sdílených fixtures k vidění

Peer se dvěma RIB, který dával osm nerozlišitelných řádků a celou vlnu
motivoval, se v ostrém reportu nevyskytuje — každá BGP skupina má přesně
jednoho peera a jednu RIB. Scénář je ověřený jen v jednotkových testech
(`CONFIGURED_TWO_RIBS` / `_installed_two_ribs()` v `test_routes.py`,
`test_bgp_group_carries_peer_and_rib` v `test_bgp.py`). Až se fixtures
příště regenerují nebo doplní o druhou RIB u některého peera, stojí za to
si ten blok znovu přečíst očima — tahle vlna to nemohla, protože takový
blok na dnešních datech neexistuje.

### 9. Peer je v popisku `BGP status` bezpodmínečně

Vědomé rozhodnutí plánu AR-37, ne opomenutí — ale na ostrém výstupu je
vidět, že je to redundantní tam, kde rodinová sekce má jen jednoho peera:
`-- IPv4  152.11.13.1/30` následuje `BGP status (152.11.13.2)`, ačkoliv
hlavička už `152.11.13.2` jako jediného souseda dané `/30` implikuje.
Vstup pro některou z dalších vln, ne vada téhle.

---

## Pravidla do plánu vlny 6

Vlna 4 předala dvě pravidla a obě se ve vlně 5 osvědčila. „Mutant se pouští
nad tím stavem repa, ve kterém poběží doopravdy" platilo doslova: každý
implementer pouštěl mutanty až po commitu, a past se sklapla znovu i při
psaní tohohle plánu — měření M2 až M4 se muselo dělat dvakrát, přesně jak
`global-constraints.md` čeká (viz `progress.md`). „Měření má přednost před
zadáním" neslo nálezy 1, 4 a 5 v této roadmapě — ve všech třech se mýlilo
zadání (spec, plán nebo `global-constraints.md`), ne provedení.

Přidává se třetí, zaplacené nálezem 3:

**Test, který hledá řetězec kdekoliv ve výstupu, neměří sekci — měří
výstup.** Když dvě sekce reportu sdílejí tentýž řetězec (`(nic)`), assert
`x in out` projde, i když zkoumaná sekce se vůbec nevytiskla — projde díky
tomu, že řetězec vytiskla sousední sekce. Test na obsah konkrétní sekce
musí hledat **uvnitř** té sekce (např. na pozici hned za jejím nadpisem),
ne kdekoliv v celém vykresleném textu. Platí to obecně, ne jen pro
NEZARAZENO — kdekoli report sdílí formátovací literál napříč sekcemi.
