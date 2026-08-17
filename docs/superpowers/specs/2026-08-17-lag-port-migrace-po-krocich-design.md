# LAG porty a migrace po krocích — design

Datum: 2026-08-17
Stav: schváleno v brainstormingu, čeká na implementační plán

## Motivace — změřený problém

Při port-po-portu práci (capture/evaluate s `--port`) narazila praxe na
případ, kdy je původní služba na UNI portu starého boxu (`ge-0/0/4`),
ale na novém boxu jde přes sdílený LAG (`ae0`). Model nástroje z fáze 4
je „port = jednotka migrace"; realita je „starý UNI port → unit na
sdíleném LAGu" (N:1). Tři konkrétní selhání, všechna změřená v kódu:

1. **Inventory pro LAG port se neregeneruje.** `cli.py` (větev
   `_capture_into_run`, dnes řádky 447–462): existující per-port
   inventory má přednost a `--parse-services` vypíše „inventory jiz
   existuje, generovani se preskakuje". Pojistka z fáze 4 proti tichému
   přepsání znamená, že druhá vlna služeb namigrovaná na tentýž `ae0`
   se do inventory nedostane → služby nemají scope → nevalidují se.

2. **Párování portů kolabuje na N:1.** `RunManifest.paired_old()`
   (`runs/manifest.py:87`) vyžaduje právě jeden match. Schéma run.yml
   s víc old porty na jeden nový počítá, ale jakmile tam záznamy jsou,
   `len(matches) != 1` → `None` → `find_pre_baseline` potichu spadne na
   celoboxový pre snímek (`runs/pairing.py:45-49`). Per-port baseline
   párování u LAGu fakticky neexistuje.

3. **`--port` neumí logical unit.** Filtr v `generate_inventory`
   (`runs/services.py:38`) porovnává `service.interface.split(".", 1)[0]
   == port`; při `--port ae0.15` nikdy nesedí → prázdné inventory.

Párovací mašinerie služeb (`scoping/matcher.py`, 5 pravidel,
description-first, při nejednoznačnosti nikdy nehádá) přitom potřebnou
práci už umí — vrací zvlášť `unmatched_baseline` (chybějící služby
starého portu → hlásit) a `unmatched_subject` (služby cizích vln →
potlačit). Design je proto hlavně věc plánování evaluací a filtru
v enginu, ne nové párovací logiky.

## Rozhodnutí (uzavřeno v brainstormu)

- **Starý port = klíč migračního kroku.** Starý UNI port je vždy
  unikátní; na nové straně se scope vybírá dynamicky párováním podle
  description z celého LAGu. Unit-level mapping (`ge-0/0/4 → ae0.15`
  v run.yml) se **nezavádí** — zamítnuto.
- **Post capture = celý LAG + filtr přes baseline.** Capture sebere celý
  `ae0` (inventory se přegeneruje), evaluate ukáže jen služby spárované
  s baseline starého portu + chybějící baseline služby.
- **Potlačené služby = souhrnný řádek.** Report je nevypisuje po
  blocích, ale hlavička nese pravdivý počet; JSON nese jejich seznam.
  Nic se neskrývá tiše.
- **`--parse-services` vždy přegeneruje** inventory z aktuální
  konfigurace (mění rozhodnutí fáze 4 „žádné tiché přepsání" — flag je
  explicitní úmysl, tiché to není). Bez flagu se existující soubor
  použije beze změny.
- **Pojistka pre snímku:** přepsání existujícího pre snímku jen
  s explicitním `--overwrite`, jinak ToolError. Post/rollback se
  přepisují jako dosud.
- **Přístup A — evaluace per mapping.** Každý záznam old→new
  v `interface_mapping` = jedna evaluace (pre snímek starého portu ×
  post snímek nového portu) s filtrem přes baseline. Jeden post snímek
  LAGu → N reportů, včetně regresní kontroly dřívějších vln. Zamítnuty:
  B (slitý baseline — ztrácí provenienci, nový merge mechanismus),
  C (filtr až v rendereru — checky běží i na cizích službách, JSON
  šumí).
- **EX podpora (fáze 5) zůstává odložena.** Tento design má přednost;
  EX z description párování později těží.

## 1. Capture a run.yml

**run.yml schema zůstává 1.** Tvar `interface_mapping` se nemění — N:1
už dnes schéma umí zapsat, jen ho nikdo neuměl číst. `--maps-to` při
post capture na `ae0` funguje beze změny (`add_mapping` N:1 nebrání;
duplicitu hlídá po old endpointu).

**`--parse-services`:** vždy stáhne konfiguraci a inventory přegeneruje.
Pokud předchozí soubor existoval, vypíše srovnání: „inventory
přegenerována: 5 služeb (+2 nové, -0 odebrané)". Regenerace nikdy
zpětně nemění existující snímky — scopes jsou zapečené ve snapshot
JSONu, párování inventory soubor nečte. Služby z baseline proto zmizet
nemohou; na starém boxu po migraci by regenerace pravdivě řekla
„služby už tu nejsou", ale pre snímek je nedotčený.

**Pojistka pre snímku:** `capture --phase pre --run` na node/port,
který už `pre` záznam v manifestu má, skončí `ToolError` („pre snímek
už existuje: <soubor>; přepiš s --overwrite"). Nový flag `--overwrite`
platí jen pro pre.

**Ping cíle při post capture LAGu:** místo dnešního `find_pre_baseline`
(u N:1 tichý pád na celoboxový snímek) se vezme **sjednocení ARP/ND ze
všech pre snímků starých portů namapovaných na tento nový port** —
každý migrační krok přinesl své hosty a všichni už na LAGu žijí.
Bez jediného mapovaného pre snímku zůstává dnešní fallback (vlastní
ARP + subnet). Deferred minor z fáze 4 „dedup baseline ping kandidátů"
se tímto stává povinným (sjednocení přes N snímků duplicity vyrábí).

## 2. Plánování evaluací (`runs/pairing.py`)

`plan_evaluations` pro post snímky přestane volat `find_pre_baseline`:

- Pro post capture portu `P` na novém boxu najde **všechny** záznamy
  `interface_mapping`, kde `new == (node, P)`. Každý záznam
  s existujícím pre snímkem svého old portu vyrobí jednu
  `Evaluation(subject=post, baseline=pre-old-portu, step=mapping)` —
  tedy i regresní kontrolu dřívějších vln na tomtéž LAGu.
- Mapping bez pre snímku → evaluace s `reason`
  („chybí pre snímek <old port>") — dnešní vzor.
- Post capture portu **bez jakéhokoli mappingu** → dnešní celoboxový
  fallback beze změny. 1:1 případ (právě jeden mapping) dá per-mapping
  plán totéž co dnes.
- `evaluate --run --port X` filtruje podle **starého portu** kroku.
  Celoboxové a rollback evaluace beze změny (rollback zůstává
  same-device+port).
- `Evaluation` dostane nové pole `step` (old node/port → new
  node/port); teče do enginu a hlavičky reportu.

## 3. Filtr přes baseline v enginu

`evaluate_snapshots` dostane volitelný parametr `step` (`None` =
dnešní chování beze změny). Se zadaným `step`:

- **Spárované dvojice** (`matches.pairs`) běží checky normálně.
- **`unmatched_baseline`** zůstává celé — služba starého portu, která
  na LAGu chybí nebo se nespárovala, je hlavní nález kroku (vyjede
  jako dosud NESPAROVANO s důvodem). Služba zmigrovaná se špatnou
  description se nespáruje a vypadne právě tudy — neztratí se.
- **`unmatched_subject`** se nevyhodnocuje ani nerenderuje po blocích —
  spočítá se a scope id + description se uloží do JSON. Přes tyto
  služby neběží žádné checky ani pingy.
- Filtr je nezávislý na profilovém `service_types` filtru a skládá se
  s ním: baseline filtr rozhoduje, kdo vůbec hraje; profil filtruje
  checky spárovaných.
- **Device a layer1 scopes filtru nepodléhají** (precedens profilového
  filtru). L1 blok LAGu nese fyzické zdraví portu, patří do každého
  kroku; device scope taky.
- **L2/L3 páry (fáze 3): partner se přitahuje.** Když se jedna půlka
  páru spáruje s baseline a druhá ne, druhá se do reportu přitáhne —
  stejné pravidlo, jakým `filter_result` drží páry pohromadě. Řeší i
  visící „blok níže" známý z profilového filtru.

## 4. Report a JSON

- Hlavička evaluace nese krok: `[krok ge-0/0/4 -> ae0]` vedle
  `[profil X]`.
- Souhrnný řádek pod hlavičkou, jen když N > 0:
  `Dalsi sluzby na ae0 mimo tento krok: N (nesparovano s baseline
  ge-0/0/4)`.
- „X z Y sluzeb" počítá jen služby kroku (spárované + chybějící
  baseline), ne potlačené.
- JSON (`RunResult`): aditivní klíče `step`
  (`{old: {node, port}, new: {node, port}}`) a `excluded_services`
  (seznam `{scope_id, description, service_type, reason}` — tvar
  `_unmatched_entry`). Klíče jen když krok
  existuje — vzor `profile`. **Snapshot ani RunResult schema se
  nemění** (čistě aditivní).
- `status --run`: jeden new port může mít víc řádků — po jednom na
  old port (řeší se tím část deferred minoru „_status_rows řadí
  l2-switch do OLD sloupce" jen pokud se ho dotkne; jinak zůstává).

## 5. Testy a ověření

- **Planner** (`tests/runs/`): post snímek s 2 mappingy → 2 evaluace se
  správnými baseline; mapping bez pre snímku → reason; port bez
  mappingu → celoboxový fallback (regresní test dnešního chování);
  `--port` filtr podle starého portu.
- **Engine filtr**: subject snímek se službami dvou vln (různé
  descriptions), baseline jedné vlny → spárované běží, cizí skončí
  v `excluded_services`, chybějící baseline služba vyjede NESPAROVANO;
  napůl spárovaný L2/L3 pár se přitáhne celý; device/L1 scopes vždy
  přítomné. Mutant: vypnout filtr → test musí spadnout na počtu bloků.
- **CLI**: `--parse-services` přepíše existující inventory a vypíše
  delta; pre overwrite guard (ToolError bez `--overwrite`, projde
  s ním); ping cíle = sjednocení (a dedup) ARP z více pre snímků.
- **Fixtures**: scénář dvou vln se staví v conftest továrnách, ne
  regenerací lab ymls (`tests/fixtures/172.20.20.{4,5}.yml` záměrně
  drží starý commitnutý stav). Platí pravidla o mutantech nad
  konzistentní sadou fixtures.
- **Lab ověření**: žádné nové RPC — lab jen na end-to-end smoke:
  pre na `ge-0/0/4` (vMX), post na LAGu (PTX EVO), dvě vlny po sobě.
