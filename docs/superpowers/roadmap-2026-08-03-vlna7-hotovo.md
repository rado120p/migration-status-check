# Vlna 7 hotová — RIB ve sdílených fixtures a zpřísnění trojcestné větve, stav k 2026-08-03

**Výchozí bod:** větev `vlna7-fixtures-a-zprisneni` založená z `main` na
commitu `89233bf` (spec i plán jsou součástí základu větve, ne jejího
obsahu), obě úlohy hotové, závěrečné ověření provedeno.
**640 testů zelených, 0 přeskočených.** Zámek parserů drží na 146 řádcích.

Zadáním byly body 7, 8 a 11 v „Co zbývá" roadmapy vlny 6
([`roadmap-2026-08-03-vlna6-hotovo.md`](roadmap-2026-08-03-vlna6-hotovo.md)).
Návrh je
[`specs/2026-08-03-vlna7-fixtures-a-zprisneni-design.md`](specs/2026-08-03-vlna7-fixtures-a-zprisneni-design.md),
provedení
[`plans/2026-08-03-vlna7-fixtures-a-zprisneni.md`](plans/2026-08-03-vlna7-fixtures-a-zprisneni.md).

---

## Co vlna 7 přinesla

Malá konsolidační vlna: dvě opravy syntetických fixtures a jedno
jednořádkové zpřísnění produkčního kódu. Obsah vlny nese rozsah
`89233bf..HEAD`; dvě úlohy daly dva commity — `dd79739` (fixtures, test-only)
a `1a1fe41` (zpřísnění) — a zbytek je tenhle dokument.

- **Bod 7** — `_facts_for` v `tests/conftest.py` přišíval každému BGP peerovi
  jedinou RIB jménem `inet.0` bez ohledu na rodinu; u IPv6 peera to bylo v
  rozporu se sousední skupinou statických rout ve stejné sekci, která
  `inet6.0` používala správně. Nově o jménu rozhoduje `_ribs_for(peer,
  family)`. Zdroj vady **nebyl** `migration_validator/checks/bgp.py` —
  `_prefix_finding` bere `rib_name` beze změny z dat, která dostane.
- **Bod 8** — peer `152.11.13.2` (konstanta `DUAL_RIB_PEER`) nese nově druhou
  RIB `inet6.0` s vlastními countery `6/5/2/5/1`, takže motivující scénář
  AR-36 je **poprvé k vidění ve vyrenderovaném reportu**. Peer je vybraný
  záměrně: je na obou fixtures, takže baseline i subject nesou tutéž
  strukturu a `bgp_prefix_counts` je porovná místo aby hlásil „RIB neni v
  baseline".
- **Bod 11** — `if was_active:` ve `StaticRouteStatusCheck._finding`
  (`migration_validator/checks/routes.py`) je nově `if was_active is True:`,
  symetricky s existujícím `was_active is False` o dvacet řádků výš. Hodnota
  `"false"` je neprázdný řetězec, tedy pravdivá, a proto dosud eskalovala
  nejednoznačnost na tvrdý FAIL — přesně to, co pravidlo R-2 zakazuje. **Je
  to zpřísnění, ne oprava vady:** `collectors/routes.py:84` vyrábí skutečný
  `bool` a YAML round-tripuje bool jako bool, takže žádný takový vstup dnes
  nenastane. Smysl je, aby R-2 platilo konstrukcí, ne argumentem o
  nedosažitelnosti.

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""   # 640 passed, 0 skipped
diff mx_parser.py evo_parser.py | wc -l    # 146
```

Obě čísla jsou změřená v závěrečném ověření nad commitem `1a1fe41`, tedy nad
stavem po poslední úloze, která sáhla na kód a testy.

**Ostrý report ze společných fixtures** (`tests/fixtures/172.20.20.4.yml` /
`.5.yml`) se vyrenderuje takhle — postup je tu napsaný celý schválně, ne
odkazem, protože roadmapa vlny 5 ho vedla odkazem na soubor pod
`.superpowers/`, který je v `.gitignore` a mezitím zmizel:

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

Změřeno v závěrečném ověření, dvěma způsoby:

- V sekci `-- IPv4  152.11.13.1/30` jsou **dva** bloky téhož peera,
  `-- BGP 152.11.13.2 / inet.0` a `-- BGP 152.11.13.2 / inet6.0`, a liší se
  i čísly, ne jen hlavičkou. V pořadí, v jakém je report tiskne
  (`active` / `received` / `accepted` / `advertised`), je to `14/14/14/3`
  proti `5/6/5/2` — druhá RIB je v datech zapsaná jako
  `received 6 / accepted 5 / advertised 2 / active 5 / suppressed 1`.
- Grep na řádky tvaru `-- BGP <adresa s dvojtečkou> / inet.0` vrací
  **prázdný seznam** — žádný IPv6 peer už `inet.0` nenese.

## Co vyšlo jinak, než plán čekal

### 1. Číslo řádku v mutantech plánu zestárlo mezi kroky téže úlohy

Nejcennější nález vlny. Plán úlohy 2 předepisoval mutanty jako
`sed -i '194s/…/…/'`, jenže krok 3 téže úlohy nad ten řádek vkládal
šestiřádkový komentář, takže v okamžiku, kdy se mutant pouštěl, byl cíl na
řádku 200. **První pokus byl tichý no-op** — `sed` nenašel nic, testy zůstaly
na 640 a bez další kontroly by z toho vyšel závěr „mutant nezabil žádný test",
tedy pravý opak pravdy.

Chytila to **kontrola, kterou plán sám předepisoval**: `git diff --stat` po
každém `sed`, s instrukcí ověřit, že mutant *skutečně sedl*. Implementer ji
provedl, viděl prázdný výstup a cíl opravil na 200; teprve pak čísla seděla.

Plán je v tomhle vadný a vada je obecná: **číslo řádku v mutantovi zestárne,
kdykoliv táž úloha nad ten řádek něco vloží.** Promítnuto do pravidel pro
vlnu 8.

Doměřeno v review úlohy 2: to prvotní tvrzení o no-opu bylo v reportu nejdřív
jen popsané prózou, ne doložené vlepeným výstupem — jediné měření v celém
reportu, které tenhle tvar mělo. Zavřelo to fix kolo 1: měření se zopakovalo
a výstup je v reportu doslova.

### 2. Spec měl mutanty dvakrát špatně — obojí odhalilo měření při psaní plánu

Ani jedna z těch vad se nedostala do provedení, protože plán vznikl tak, že
autor obě úlohy provedl nanečisto. Zapisuje se to proto, že jde o dva různé
druhy chyby a oba se dají udělat znovu:

- **Tautologický mutant.** První znění specu mířilo mutanty bodů 7 a 8 do
  `tests/conftest.py`, tedy do téhož generátoru, proti kterému test asertuje.
  Takový mutant neprokazuje nic — test by spadl proto, že mu někdo smazal
  vstup, ne proto, že hlídá chování. Cíl je správně `_prefix_finding` v
  `checks/bgp.py`.
- **Mutant, který svůj test nezabíjí.** První znění specu uvádělo u testu na
  `{"active": 0}` jako mutanta návrat změny. Změřeno: `if was_active:` i
  `if was_active is True:` dají u `0` shodně DEGRADED, takže návrat změny ten
  test nezabije. Skutečný mutant je blízký překlep vlastní opravy,
  `is not False`.

### 3. Test-only diff opět nešlo ověřit z review úlohy

Úloha 1 je celá test-only, takže její tvrzení o produkčním mutantovi v
`checks/bgp.py` review z diffu ověřit nemohla — produkční soubor v něm z
definice není. Reviewer to správně označil jako „nelze ověřit z diffu" a
**doměřil to controller** nad commitem `dd79739`: mutant dal `3 failed,
635 passed`, jmenovitě `test_bgp_group_carries_peer_and_rib` (existující, z
vlny 5), `test_ipv6_peers_carry_inet6_rib` a
`test_dual_rib_peer_yields_two_distinguishable_blocks`.

Je to druhý výskyt téhož tvaru po vlně 6 — pravidlo funguje a přenáší se.

### 4. Nulový ripple obou fixture změn byl nález, ne potvrzení

Změřeno ještě před psaním specu: obě změny fixtures projdou beze změny počtu
i složení testů. Nález z toho zní **jméno RIB u BGP peera dosud neasertoval
žádný test** — proto vlna 7 dva takové testy přidala. Pravidlo „nulový ripple
není potvrzení, je nález" se tedy tentokrát uplatnilo jako nástroj, na rozdíl
od vlny 6, kde jen popisovalo už známý stav.

### 5. Odložená drobnost z review úlohy 2

Jedna, neblokovala, neopravovala se: nový šestiřádkový komentář ve
`checks/routes.py` navazuje **bez prázdného řádku** na starší osmiřádkový
blok, který končí zdůvodněním DEGRADED větve. Spojený čtrnáctiřádkový blok
pak vizuálně působí, jako by se to zdůvodnění vztahovalo i na následující
řádek `if was_active is True:`, který ale vede k BROKEN. Text komentáře
implementer převzal doslova ze zadání, takže vada je **v předpisu plánu**, ne
v jeho úsudku. Nese se dál jako bod 13 v „Co zbývá".

---

## Co zbývá

### 1. Příznaky na hlubších úrovních konfigurace

Beze změny z vln 3 až 6, včetně obou částí (deaktivovaná jednotlivá
`route`/`bfd-liveness-detection`/`neighbor` a top-level
`<routing-options inactive>` se dál vypouští ze záměru beze stopy).

**Sémantika je ale nově rozhodnutá, takže ji příští vlna neřeší znovu.**
Rozhodnutí uživatele z 2026‑08‑03: deaktivovaný prvek má **zůstat v záměru a
hlásit SKIP**, symetricky s existujícím chováním deaktivované služby
(`sluzba je v konfiguraci deaktivovana`). Tiché vypouštění beze stopy je
vada, ne rozhodnutí.

### 2. Resync `172.20.20.5.yml` a `tests/fixtures/rpc/junos-evo/`

Beze změny z vlny 4. Vlna 7 se laborky netýkala — sáhla jen na syntetický
generátor `tests/conftest.py`, ne na nahraná data.

### 5. Drobnosti ze závěrečného review vlny 4

Beze změny — pět kosmetických bodů (viz roadmapa vlny 4, „Co zbývá" bod 6),
žádná neblokovala merge tehdy ani teď.

### 7. ~~Sdílený syntetický pomocník dává IPv6 peerům skupinu `inet.0`~~

**Vyřízeno** (commit `dd79739`). `_ribs_for` v `tests/conftest.py` rozhoduje o
jménu RIB podle rodiny peera. Countery ostatních peerů zůstaly uniformní —
viz nový bod 12.

### 8. ~~Motivující scénář AR-36 není na sdílených fixtures k vidění~~

**Vyřízeno** (commit `dd79739`). Peer `152.11.13.2` nese dvě RIB a blok se v
ostrém reportu vykreslí; ověřeno očima v závěrečném ověření.

**Pozor — tohle nezavírá bod 2 z „Co vyšlo jinak" vlny 6.** Ten má **jinou
spouštěcí podmínku**: routa bez klíče `active`, ne druhá RIB u peera. Na
dnešních fixtures pořád nenastane, protože `tests/conftest.py` zrcadlí každou
routu ze záměru s `"active": True`. AR-43 z vlny 6 je tedy dál ověřené
výhradně jednotkovými testy a doplněné fixtures na tom nic nezměnily.

### 9. Peer je v popisku `BGP status` bezpodmínečně

Beze změny z vln 5 a 6. Vědomé rozhodnutí plánu AR-36 (`checks/bgp.py`), ale
na ostrém výstupu je vidět, že je to redundantní tam, kde rodinová sekce má
jen jednoho peera. Vstup pro některou z dalších vln.

### 11. ~~Robustnost trojcestné větve u `active` na nebool hodnotách~~

**Vyřízeno** (commit `1a1fe41`). `if was_active is True:` uzavírá jedinou
vadu závažnosti: `"false"` už neeskaluje na FAIL. Vedlejším důsledkem
přestala být FAIL i hodnota `1`.

**Hláška u `{"active": 0}` se vědomě nezměnila** — dostane
„baseline aktivitu neuvadi", ačkoliv `0` neaktivitu uvádí. Opravit ji by
znamenalo rozšířit i větev `was_active is False` na „přítomné a nepravdivé",
což by `""`, `[]` a `0.0` prohlásilo za explicitní tvrzení „byla neaktivní i
v baseline"; jedna nepřesná hláška na nedosažitelném vstupu vyměněná za
jinou. Roadmapa vlny 6 žádala jen opravu eskalace a ta je hotová.

### 12. BGP countery jsou napříč peery uniformní

Nový bod. Každý peer kromě `DUAL_RIB_PEER` nese `14/14/14/3` — sdílené
fixtures nikdy nemodelovaly reálné hodnoty per peer. Vlna 7 to vědomě
nechala být: rozrůznění counterů nežádal žádný otevřený nález a bylo by to
šíření rozsahu. Kdyby se to jednou udělalo, vedlejším přínosem by bylo, že
záměna peerů v kódu by byla na reportu vidět.

### 13. Komentář ve `checks/routes.py` splývá se starším blokem

Nový bod, zapsaný review úlohy 2, neopravovaný. Nový šestiřádkový komentář
navazuje bez prázdného řádku na starší osmiřádkový, takže zdůvodnění DEGRADED
větve vizuálně přetéká na řádek, který vede k BROKEN. Oprava je jeden prázdný
řádek; patří do vlny, která na ten soubor sáhne příště.

---

## Pravidla do plánu vlny 8

### Nové, zaplacené nálezy téhle vlny

**Mutant se v plánu neadresuje číslem řádku, když ho táž úloha posouvá.**
Zaplaceno úlohou 2 (viz „Co vyšlo jinak", bod 1): plán mířil `sed` na řádek
194, ale krok 3 téže úlohy nad něj vložil šestiřádkový komentář, takže cíl
byl při běhu na 200 a mutant byl tichý no-op. Buď se adresuje vzorem místo
čísla, nebo se číslo přepočítá po vložení — a **v každém případě platí, že
`git diff --stat` po `sed` je povinný krok, ne ozdoba.** Právě on tenhle
no-op odhalil.

**Mutant, který svůj test nezabíjí, je horší než chybějící mutant.** Před
zapsáním mutanta do plánu se pouští; „je to zjevně ten správný" je odhad.
Zaplaceno psaním specu vlny 7 (viz „Co vyšlo jinak", bod 2), kde návrh
tvrdil, že návrat změny shodí test na `{"active": 0}` — neshodí, protože ta
hodnota dává DEGRADED před změnou i po ní.

**Mutant nesmí mířit do téhož souboru, proti kterému test asertuje.** Když
test tvrdí něco o datech z generátoru fixtures, mutant patří do produkčního
kódu, který ta data zpracovává. Mutant do generátoru dokazuje jen to, že
smazaný vstup shodí test.

### Přenesená z vln 5 a 6

**„Mutant se pouští nad tím stavem repa, ve kterém poběží doopravdy."**
Uplatnilo se: mutanti úlohy 2 běželi až nad novým `tests/conftest.py` z úlohy
1, jak plán závazně předepisoval.

**„Měření má přednost před zadáním."** Uplatnilo se dvakrát: implementer
úlohy 2 opravil zestaralé číslo řádku podle toho, co viděl, a autor plánu
přepsal dvě tvrzení specu podle měření ještě před provedením.

**„Test-only diff nemůže doložit tvrzení o produkčním kódu — musí ho změřit
někdo mimo review té úlohy."** Uplatnilo se u úlohy 1, podruhé po vlně 6.
Měřil controller.

**„Nulový ripple po změně chování není potvrzení, je nález."** V téhle vlně
se poprvé uplatnilo jako nástroj — viz „Co vyšlo jinak", bod 4.

**„Je-li ASCII-only tvrdá podmínka, dokazuje se bajtovým scanem."**
Uplatnilo se v obou úlohách, obě doložily prázdný výstup
`grep -nP '[^\x00-\x7F]'`.

**„Test, který hledá řetězec kdekoliv ve výstupu, neměří sekci — měří
výstup."** V téhle vlně **se neuplatnilo**: všechny čtyři nové testy tvrdí
nad objekty (`Outcome`, `check.details["rib"]`, `check.group`), ne nad
vykresleným textem. Přenáší se dál pro reportovou vrstvu.
