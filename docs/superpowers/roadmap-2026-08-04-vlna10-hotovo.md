# Vlna 10 hotová — tichý blok deaktivované služby a pravdivá hláška o peeru, stav k 2026-08-04

**Výchozí bod:** větev `vlna10-tichy-blok-a-pravdiva-hlaska` založená z `main`
na commitu `5f1e4d0` (merge-base main..HEAD, změřeno), všech šest úloh hotových. **685 testů
zelených, 0 přeskočených** (výchozí stav 676). Zámek parserů drží na
**146 řádcích**. Schema zůstalo **5** u inventory i u snapshotu.

Zadáním byl celý zbytek „Co zbývá" roadmapy vlny 9
([`roadmap-2026-08-04-vlna9-hotovo.md`](roadmap-2026-08-04-vlna9-hotovo.md)):
body 18, 19, 16, 17, 9, 12 a 2. Uživatel 2026‑08‑04 rozhodl, že vlna 10
pobere všechny — po ní nezůstává otevřený žádný bod z vln 4 až 9. Návrh je
[`specs/2026-08-04-vlna10-tichy-blok-a-pravdiva-hlaska-design.md`](specs/2026-08-04-vlna10-tichy-blok-a-pravdiva-hlaska-design.md),
provedení
[`plans/2026-08-04-vlna10-tichy-blok-a-pravdiva-hlaska.md`](plans/2026-08-04-vlna10-tichy-blok-a-pravdiva-hlaska.md).

---

## Co vlna 10 přinesla

**Bod 18 — blok deaktivované služby sleva SKIPy do jednoho řádku.**
`checks/base.py:170` označuje deaktivační SKIP strukturální značkou
(`skipped_because=SKIP_DEACTIVATED`, `models/result.py:84,89`), ne shodou
textu ani `Status.SKIP`. `reporting/view.py:125` tuhle značku čte a bez
`--detail` slévá odpovídající řádky do jednoho `SKIP | Ostatni checky : N
preskoceno`, kde N počítá jen sloučené deaktivační SKIPy — cizí SKIP
(např. „BGP prefixy : bez baseline") zůstává samostatně. S `--detail` se
rozepíšou všechny původní řádky beze změny proti dnešku. JSON (`to_dict()`)
vydává všechny checky bez ohledu na `detail` — sloučení je vlastnost
textového reportu, ne výsledku běhu. Commit `939faef`.

**Bod 19 — hláška o peeru mluví o členství ve službě, ne o existenci.**
`checks/bgp.py` v `without_session` mění hlášku `{peer}: v baseline byl,
v subjektu neni` (value `chybi uplne`) na `{peer}: v baseline patril
k teto sluzbe, v subjektu uz ne` (value `neni ve sluzbe`) — formulaci
pravdivou v obou případech, které do větve spadají (peer ze zařízení
zmizel i peer přešel pod jinou službu), bez nové vazby v `CheckContext`.
Sesterská větev o chybějící session se nemění. Peer zůstává vidět
současně v bloku služby i v NEZAŘAZENO — obě sekce mluví dál, každá o
něčem jiném (blok o členství, NEZAŘAZENO o session, kterou si žádná
služba nenárokuje). Commity `c82bc17`, `5a36224`.

**Uzavřeny body 16, 17 a 9 zapsaným odůvodněním (commit `4e947e3`):**
- **16** — `test_inventory_rejects_schema_three` přejmenován tak, aby
  název odpovídal tělu (asertuje hodnotu konstanty, ne odmítnutí);
  skutečné odmítnutí kryje `test_old_inventory_fails_loudly`.
- **17** — uzavřen jako **nedosažitelný**, doloženo měřením na živé
  laborce (viz níž): parser pro tentýž `(rib, prefix)` nevydává dva
  záznamy, takže kolize v množině `deactivated` nastat nemůže.
  `checks/routes.py:93` nese odůvodnění přímo v komentáři.
- **9** — kvalifikátor `BGP status (adresa)` zůstává bezpodmínečný;
  `checks/bgp.py` nese odůvodnění: popisek je identifikátor řádku i v
  JSON, podmíněný kvalifikátor by přibytím druhého peera přejmenoval i
  řádek prvního.

**Bod 12 — countery se rozrůznily deterministicky z adresy peera.**
`tests/conftest.py` nahradil sdílenou konstantu `_RIB_COUNTERS` funkcí
`_counters_for(peer)`; baseline i subject dostávají stejná čísla (jinak
by `bgp_prefix_counts` začal hlásit rozdíly všude) a `accepted <=
received` platí. K rozrůznění patří i **dva nové zamykající testy**
(`test_peers_of_one_service_carry_different_prefix_counts`,
`test_prefix_counts_match_between_baseline_and_subject`), protože
hodnoty counterů ze sdílených fixtures nehlídal žádný z 676 testů — bez
zámku by rozrůznění bylo dekorativní. Commit `d797db0`.

**Bod 2 — resync kořenových inventory a RPC fixtures obou platforem.**
Capture z `clab-pop-migration-MX1-POP1` (`.4`) a `MX1-POP2` (`.5`),
kořenové `172.20.20.{4,5}.yml` přegenerovány na schema 5,
`tests/fixtures/rpc/junos-evo/` resynchronizován. Všechny čtyři mutanty
vlny znovu spuštěny nad novými fixtures — každý zabíjí přesně tytéž
testy jako předtím. Vedlejší oprava: komentář v `mx_parser.py:749` a
`evo_parser.py:749` už netvrdí, že qualified-next-hop nemá adresu —
nese buď adresu, nebo `interface-name` (viz bod 20 níže). Commit
`64d07bc`, doplněno `10732cf` (oprava příkazu `record` v plánu).

## Jak si vyrobit důkazy

```bash
cd /home/rado/Desktop/scripts/migration-status-check
.venv/bin/python -m pytest -o addopts=""      # 685 passed, 0 skipped
diff mx_parser.py evo_parser.py | wc -l       # 146
head -1 172.20.20.4.yml                       # schema_version: 5
head -1 172.20.20.5.yml                       # schema_version: 5
grep -rn "skipped_because\|SKIP_DEACTIVATED" migration_validator/
  # jediné volání _skip() se znackou: migration_validator/checks/base.py:170
git log --oneline main..HEAD
```

---

## Co vyšlo jinak, než plán čekal

### 1. Nulový ripple se ve vlně potvrdil dvakrát a pokaždé jako nález

Bod 12 (hodnoty counterů) a bod 18 (vykreslený obsah bloku deaktivované
služby) prošly beze změny počtu testů, přestože měnily pozorovatelné
chování. Resync fixtures nulový ripple potvrdil taky, ale je to jiný
případ: tam šlo o potvrzení, že vlna nezavedla regresi, ne o nález (viz
níž). U prvních dvou musela vlna pokrytí
doplnit — bez zamykajících testů z úlohy 4 by rozrůznění counterů bylo
dekorativní; bez testu `test_foreign_skip_is_not_collapsed` z úlohy 2 by
implementace slévající podle `Status.SKIP` prošla v `test_view.py` tiše
(oprava vlny 10, nález 2: pod stejným mutantem padá i
`test_text_report.py::test_deactivated_service_shows_the_reason_in_the_report`,
takže `test_foreign_skip_is_not_collapsed` není jediný test, který ho
zabíjí, jen jediný v tomto souboru). U resyncu fixtures nulový ripple
potvrdil, že vlna nezavedla regresi — čtyři mutanty vlny nad novými
fixtures zabíjejí přesně tytéž testy jako předtím.

### 2. Pravidlo „tvrzení o mutantovi je kód" se uplatnilo třikrát, a pokaždé proti PLÁNU, ne proti sousedním testům

To je posun proti vlně 9, kde mířilo na okolní testy. Konkrétně: úloha 2
našla v briefu dvě zastaralá tvrzení a změřila je (commit `9792f11`);
review úlohy 4 našla docstring jmenující `_RIB_COUNTERS`, který tentýž
commit mazal — doslovný pokus o toho mutanta by skončil `NameError`
(oprava `f387020`). Obě opravy nahradily tvrzení popisem mutanta, který
implementer skutečně spustil a jehož výsledek je zaznamenaný v reportu.

### 3. Vada plánu v úloze 1: test asertoval hlášku uvnitř bloku služby, kam se hláška netiskne

`_block()` v `text_report.py` vypisuje `status | label : value | zmena`,
nikdy `message`; celá hláška je v souhrnné tabulce ve sloupci NALEZ.
Implementer to doložil debug printem plného renderu a neobešel — nahlásil
jako `DONE_WITH_CONCERNS` a čekal na potvrzení. Koordinátor nález nezávisle
potvrdil, plán se opravil (přeskupení asercí: plná hláška na řádku
souhrnné tabulky, `value` zvlášť uvnitř bloku) a implementer úlohu dokončil
(commity `c82bc17`, `2bd6ec8`, `5a36224`). Bez opravy by e2e test zůstal
červený a nekryl by nic navíc oproti unit testu.

### 4. `record --output-dir` si sám připojuje jméno platformy

Zadání `--output-dir tests/fixtures/rpc/junos-evo` vyrobí
`junos-evo/junos-evo/` a původní soubory nechá být — tiše, bez chyby.
Stálo to dvě kola capture; plán se opravil, aby zadával nadřazený
adresář (commit `10732cf`).

### 5. První capture z `.4` zachytil mezistav a nebyl to nález

Ukazoval BFD `152.11.13.2` jako `Down` a jeden ESI segment s
`interface=None`, což shodilo tři testy. Po přegenerování uživatelem obojí
zmizelo. Zapsáno jako **past, ne jako vada nástroje** — poučení je, že
capture z laborky se musí posuzovat proti tomu, co uživatel o stavu
laborky tvrdí, ne převzít jako pravdu.

---

## Vědomě uzavřeno, znovu neotvírat

> **Statické routy se chovají stejně jako BGP a sjednocovat se nebudou.**
> `checks/routes.py` bere `ctx.baseline["routes"]` do svého sjednocení
> a `_unassigned_static_routes` počítá `assigned` jen ze subjektových scopů
> — tedy týž tvar, kvůli kterému vznikl bod 19 u peerů. Varianta „sjednotit
> i statické routy" byla uživateli 2026‑08‑04 nabídnuta a **odmítnuta**:
> širší rozsah bez podpírajícího nálezu. Bod 19 se u BGP vyřešil
> přeformulováním hlášky, ne změnou vlastnictví, takže u rout není co
> dorovnávat.
>
> **Peer zůstává v NEZAŘAZENO i v bloku služby.** Varianty „blok mlčí"
> a „NEZAŘAZENO mlčí" byly nabídnuty a odmítnuty. Obě sekce mluví dál,
> každá pravdivě o něčem jiném: blok o členství ve službě, NEZAŘAZENO
> o session, kterou si žádná služba nenárokuje.
>
> **Popisek `BGP status (adresa)` zůstává bezpodmínečný** (bod 9) a
> **klíčování `(rib, prefix)` se nemění** (bod 17) — odůvodnění je zapsané
> přímo v `checks/bgp.py` a `checks/routes.py`.

---

## Co zbývá

### 20. Parsery zahazují qualified-next-hop

Nový bod, změřený uživatelem na živé laborce 2026‑08‑04. Konfigurace
`route 198.62.254.0/29` s holým next-hopem a dvěma qualified-next-hopy
(jeden `inactive`) dala v inventory jediný záznam s `next_hop:
[152.11.14.4]`. Routa směrovaná **výhradně** přes qualified-next-hop tedy
dostane prázdný `next_hop`, nenamapuje se na žádnou službu, a pokud není
nainstalovaná, zmizí beze stopy.

Odloženo vědomě (rozhodnutí uživatele: speciální případ, který teď nestojí
za investovaný čas). Vlna 10 opravila jen komentář v obou parserech —
qualified-next-hop nese buď adresu, nebo `interface-name`, takže do výčtu
tvarů bez adresy nepatří. Až se to bude dělat, obnoví se tím i bod 17:
deaktivovat qualified-next-hop individuálně **jde**, takže jedna routa může
nést zároveň aktivní a deaktivovaný next-hop, a klíčování `(rib, prefix)`
se pak musí navrhnout znovu.

Invariant, o který se nový komentář opírá (qualified-next-hop nese buď
adresu, nebo `interface-name`, nikdy nic jiného), necvičí žádná fixture —
drží ho jen jednorázové měření na živé laborce 2026‑08‑04, ne regresní
test.

### 21. Dual-homed ESI a neasertovaná DF role

Nový bod, změřený při resyncu fixtures. Dual-homed ESI
`00:11:12:13:14:00:00:00:00:00` (rozhraní `ae0.14`, DF `150.0.0.12`) je
nově v `tests/fixtures/rpc/junos-evo/evpn_esi.xml` a uživatel 2026‑08‑04
potvrdil, že aplikace ten tvar neumí a má se řešit v budoucnu. Změřeno, že
`df_role` se **nikde neasertuje** — jen se formátuje do zprávy
(`checks/evpn.py:48,149,155`); jeden ESI ve fixtures nese dokonce
`df_role=None` a nic to nevytkne. Nulový ripple po přidání toho tvaru do
fixtures je tedy nález, ne potvrzení.

---

## Pravidla do plánu vlny 11

### Nová, zaplacená touhle vlnou

**Nulový ripple se v této vlně potvrdil dvakrát a pokaždé jako nález.**
Bod 12 (hodnoty counterů) i bod 18 (vykreslený obsah bloku deaktivované
služby) prošly beze změny počtu testů, přestože obojí měnilo pozorovatelné
chování. Obojí znamenalo, že tu vlastnost nehlídal nikdo — a v obou
případech musela vlna pokrytí doplnit, ne se o ně opřít.

**Tvrzení o mutantovi je kód — a míří i na plán, ne jen na sousední testy.**
Vlna 9 to formulovala proti okolním testům. Vlna 10 ukázala druhý zdroj:
brief a plán samotné mohou nést tvrzení, které vlastní pozdější krok
zneplatní (jmenovaná konstanta smazaná ve stejném commitu, který test
přidal). Recept se nemění — ověřit spuštěním mutanta — jen zdroj rizika je
širší, než se čekalo.

**Autor plánu má ověřit, kam se která část nálezu v reportu doopravdy
tiskne, než na ni napíše aserci.** Úloha 1 zahodila hodiny práce
implementera na to, že plán předpokládal `message` uvnitř `_block_of()`
výřezu, ačkoli `_block()` tiskne jen `status/label/value/zmena` a celá
hláška je jinde (souhrnný řádek SLUZBA/NALEZ). Levný krok předem: dočasný
debug print plného renderu nad cílovým scénářem, než se test napíše.

**Nástrojové příkazy v plánu se ověřují spuštěním, ne odvozují z dokumentace
nástroje.** `record --output-dir X` si tiše připojuje jméno platformy
(`X/junos-evo/`), takže zadaná cesta z plánu vyrobila vnořený adresář a
nechala staré fixtures ležet vedle. Stálo to dvě kola capture, než se plán
opravil.

**Capture z laborky se posuzuje proti tomu, co uživatel o jejím stavu
tvrdí, ne přebírá jako pravda.** Vlna 10 dostala capture zachycující
mezistav (BFD Down, ESI s `interface=None`) — vypadalo to jako tři nové
nálezy, ale po přegenerování uživatelem obojí zmizelo. Nebyla to vada
nástroje ani nálezu hodná zápisu do „Co zbývá" — byla to past čtení
snímku, který ještě neustálil.

**Oprava hlášky na jednom checku nestačí, když souhrnný řádek vybírá podle
pořadí checků.** Opravná vlna po vlně 10 zjistila, že `checks/bfd.py` nesl
tutéž vadu, kterou bod 19 opravil v `checks/bgp.py`: hláška „BFD bylo
v baseline, v subjektu není nakonfigurované" tvrdila o **zařízení** něco,
co check ví jen o **službě** — přesně vzor, kvůli kterému bod 19 vznikl.
Whole-branch review na konci vlny 10 to nenašla; našla ji až samostatná
opravná vlna. Navíc `all_checks()` řadí podle `id`, a `bfd_session_state`
jde abecedně (`bf` < `bg`) **před** `bgp_session_state` — takže
`_worst_message()` bere do souhrnného řádku BFD hlášku, ne opravenou BGP
hlášku, kdykoli jsou oba checky na stejně nejhorším stavu. V přesně tom
scénáři, kvůli kterému bod 19 vznikl, tedy uživatel dál četl nepravdu i po
opravě `bgp.py`. Poučení pro další vlny: kde souhrnný řádek vybírá hlášku
podle pořadí checků, oprava jednoho checku nestačí — je potřeba zkontrolovat
každý check, který se může ocitnout na stejném nejhorším stavu.

### Přenesená z vlny 9

**„Měření má přednost před zadáním."** Uplatnilo se znovu: nulový ripple
u bodů 12 a 18 nahradil odhad měřením, past v pořadí kroků u bodu 19
(`_facts_for()` odvozuje `facts["bgp"]` ze selektorů, takže odebrání peera
před stavbou snímku by aserci na NEZAŘAZENO nechalo projít vakuově) se
řešila stejně jako hardcode `"active": True` z vlny 9 — fixture neumí
vyjádřit stav, který test potřebuje, dokud se selektor nezmění až nad
hotovým snímkem.

**„Nulový ripple po změně chování není potvrzení, je nález."** Uplatnilo
se u bodů 12 a 18, viz „Co vyšlo jinak" výše.

**„Mutant, který se neaplikoval, neměří nic."** Každý mutant vlny 10 v
plánu i reportech nese `grep -n MUTANT <soubor>`, který něco vypsal —
žádný nečinný mutant se v této vlně neobjevil.

**„Zahození aserce se dokazuje mutantem, ne úvahou."** Nedotčeno, žádná
úloha vlny 10 asercii nezahazovala bez mutanta.
