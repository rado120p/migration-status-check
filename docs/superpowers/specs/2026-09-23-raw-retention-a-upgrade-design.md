# Raw retention + `mig-validate upgrade` — design

Datum: 2026-09-23

## Cíl

Inventory (schéma 10) i snapshot (schéma 13) se při jiné `schema_version`
odmítnou načíst. V produkci baseline starého boxu po migraci znovu sejmout
nejde (služby i konfigurace jsou z něj pryč), takže bump schématu uprostřed
migrace by baseline nenávratně odstřihl. Tahle vlna to řeší obecně: každý
capture v runu uloží syrová data ze zařízení (konfiguraci, všechny RPC
odpovědi včetně pingů, facts) a příkaz `mig-validate upgrade` z nich
přegeneruje inventory i snapshoty aktuální verzí nástroje — z CLI i z GUI.

Výsledek vlny: bump schématu (první bude vrstva 2 kolize adres peerů,
`docs/superpowers/followup-2026-09-23-kolize-adres-peeru-vrstva-2.md`)
přestane být produkční problém pro všechno, co bylo zachyceno po nasazení
této vlny. Opravu parsování lze navíc zpětně promítnout i do starších
snímků (např. oprava zlomkového packet-loss na EVO z 2026-09-23).

Snapshot schéma se v této vlně **nemění** (zůstává 13), inventory taky ne.

## Uzavřená rozhodnutí (brainstorm 2026-09-09 a 2026-09-23, nerelitigovat)

- **Nahrávání na hranici `device.rpc`, replay přes nezměněný capture kód.**
  Obal kolem `device.rpc` zapíše každé volání v session; `upgrade` pustí
  stávající `generate_inventory` a `capture_device` proti replay zařízení,
  které vrací nahrané odpovědi. Zamítnuto: cesta „parse ze souborů“ per
  collector (duplikovala by merge logiku v `collect()` a volání závislá na
  zařízení — interfaces extensive+terse, průchody routes, per-VRF multicast
  na MX) a řetězy upgrade funkcí (odvozená pole jako subtype bez
  konfigurace přepočítat nejdou; rozhodnuto už 2026-09-09).
- **Rozsah konfigurace = dnešní hierarchie parseru + `policy-options`,
  `firewall`, `class-of-service`.** Parser dál dostane jen hierarchie, o které
  žádá. Zamítnuto: celá konfigurace (hesla, SNMP, AAA na disku) i celá
  konfigurace bez `system`/`snmp`/`access`/`security`. Budoucí parser, který
  bude chtít něco mimo tuto sadu (např. `chassis`), starší capture
  přegenerovat neumí — `upgrade` to řekne jménem hierarchie.
- **Snímky bez raw záznamu se nepřevádějí.** Žádný shim 13 → 14 ani ve
  vrstvě 2. Po bumpu schématu je nová verze nenačte; `upgrade` je vypíše
  jako „nelze – bez raw záznamu“ a nechá beze změny. Platí to i pro ostrý
  běh MX → ACX z 2026-09-23 — kontroly jsou hotové, služby i konfigurace
  z MX už jsou pryč, uživatel to přijal.
- **Upgrade na místě se zálohou**, ne nový run adresář. Run běží dál pod
  stejným jménem, další post capture jde do stejného runu.
- **Přegeneruje se všechno, co má raw**, bez ohledu na současnou verzi
  schématu — tak se do starších snímků dostane i oprava parsování bez bumpu.
- **GUI dostane „Upgrade run“ i „Upgrade group“**, oprávnění `ADMIN` (stejné
  jako archivace).
- **Selhání zápisu raw je varování, ne selhání capture.** Změřený stav
  z produkce se kvůli disku nezahazuje.
- **`--record-raw` se ruší** (volá každé RPC dvakrát a konfiguraci
  nenahrává). Příkaz `record` pro testovací fixtures zůstává.
- **`capture --output` (mimo run) raw neukládá** — `upgrade` pracuje nad runy.

## 1. Nahrávání

### Co se nahrává

Obal kolem `device.rpc` (`RecordingDevice`) je v **obou** sessions, které
capture v runu otevírá:

1. session `--parse-services` (`_parse_services` v `runs/orchestrate.py`),
   která stahuje konfiguraci pro inventory,
2. session samotného capture (`api.capture` → `capture_device`).

Pro každé volání v pořadí se uloží: jméno RPC, argumenty, a buď odpověď,
nebo chyba (jméno třídy výjimky + text). Odpověď se serializuje přesně
(`etree.tostring` bez `pretty_print`, aby replay dal collectoru tytéž
textové uzly); nevrátí-li PyEZ element (např. `True`), uloží se typ
a hodnota. Pingy jsou normální RPC (`ping`) a nahrávají se stejně.

Za session se uloží i `device.facts` (serializovatelné hodnoty) a
`device.hostname` — `detect_platform`, `device_meta` a `_hostname` z nich
čtou.

Záznam se drží v paměti a zapisuje se až po uložení snapshotu / inventory
(viz Životní cyklus). Počet NETCONF sessions ani RPC se nemění (produkční
rate-limit 3/min, `followup-2026-09-07-netconf-rate-limit.md`).

### Konfigurace

`retrieve_configuration` (`parsers/core.py`) stáhne jedním `get_config`
hierarchie parseru **plus** `RAW_EXTRA_HIERARCHIES = ("policy-options",
"firewall", "class-of-service")`. Nahrávka dostane celou odpověď; parser
dostane kořen `configuration` jen s hierarchiemi, o které žádal
(`CONFIG_HIERARCHIES`). Bez ořezu by parser, který hledá elementy podle
jména, mohl sebrat `class-of-service interfaces interface` jako rozhraní.

Obě platformy musí rozšířený filtr přijmout — NETCONF odmítne hierarchii,
kterou schéma platformy nezná (`vlans` na MX, `b01078f`). Ověří se
v laborce (sekce 8).

### Rozložení na disku

Adresář raw má stejný kmen jako soubor, ke kterému patří:
`snapshot_<X>.json` → `raw/<X>/`, `inventory_<Y>.yml` → `raw/inventory_<Y>/`.

```
runs/<run>/
  inventory_PTX1-POP1_et_0_0_8.yml
  snapshot_post_PTX1-POP1_et_0_0_8.json
  raw/
    inventory_PTX1-POP1_et_0_0_8/     ← session --parse-services
      session.json
      0001-get_config.xml.gz
    post_PTX1-POP1_et_0_0_8/          ← session capture
      session.json
      inventory/                      ← kopie raw inventory, ze které capture vyšel
        session.json
        0001-get_config.xml.gz
      0001-get_interface_information.xml.gz
      0002-get_interface_information.xml.gz
      …
      0147-ping.xml.gz
```

Soubory odpovědí jsou gzip (fixtures z laborky: 0,5–1,2 MB XML → 32–36 kB).
Jednotlivé soubory místo archivu, aby šly prohlížet (`zcat`) a později
případně použít jako zdroj fixtures.

### `session.json`

```jsonc
{
  "raw_format": 1,
  "kind": "capture",                     // capture | inventory
  "tool": {"version": "0.1.0", "commit": "7ffae10…"},
  "address": "10.0.0.8",
  "hostname": "PTX1-POP1",
  "facts": {"hostname": "PTX1-POP1", "model": "PTX10001-36MR", "version": "…EVO", …},
  "started_at": "2026-09-23T10:01:02Z",
  "finished_at": "2026-09-23T10:02:40Z",
  "params": {
    "phase": "post",
    "port": "et-0/0/8",
    "collectors": ["interfaces", "arp", …],   // rozřešené, ne odkaz na profil
    "ping_count": 5,
    "service_types": ["IPVPN", "Internet"],
    "profile_name": "core"
  },
  "inventory": {"file": "inventory_PTX1-POP1_et_0_0_8.yml", "raw": true},
  "baselines": ["snapshot_pre_MX1-POP1_ge_0_0_2.json"],
  "calls": [
    {"seq": 1, "rpc": "get_interface_information", "kwargs": {"extensive": true},
     "reply": "0001-get_interface_information.xml.gz"},
    {"seq": 9, "rpc": "get_bfd_session_information", "kwargs": {},
     "error": {"type": "RpcError", "message": "…"}}
  ]
}
```

Session `inventory` má místo `params`/`baselines`/`inventory` pole
`port_filter` (port, se kterým `generate_inventory` filtroval; `null` =
celý box) a nemá `finished_at`.

- **Parametry rozřešené, ne odkaz na profil** — profil se po capture může
  změnit.
- **`baselines`** jsou snímky, které post capture skutečně použil pro ping
  cíle (výstup `mapped_olds` / `find_pre_baseline` v okamžiku capture).
- **`inventory.file`** je soubor, ze kterého capture opravdu četl (včetně
  fallbacku na `_all`). `inventory/` je kopie jeho raw session, protože
  příští `--parse-services` soubor i jeho raw přepíše.
- **`started_at` a `finished_at`** — `capture_device` dnes bere jedno `now`
  pro obě pole; dostane samostatný parametr `finished_at`, aby replay
  zachoval oba původní časy.
- **`tool.commit`** je `git rev-parse HEAD` checkoutu nástroje, pokud jde
  zjistit, jinak `null` (`__version__` je statické 0.1.0).

### Stabilita formátu

Raw formát je jediná věc, která se nikdy nesmí sama potřebovat upgradovat.
Každá budoucí verze nástroje musí přečíst každý starší `raw_format`;
změna formátu = čtečka umí obě verze, staré bundly se nepřevádějí. Proto
bundle nese jen data ze zařízení (odpovědi, chyby, facts) a vstupy
capture, nic odvozeného.

### Životní cyklus

- Raw adresář se nahrazuje spolu se svým souborem: post každou vlnou,
  pre při `--overwrite`, inventory při každém `--parse-services`.
- **Starý raw adresář se odstraní dřív, než se zapíše nový.** Kdyby zápis
  nového selhal a starý zůstal, `upgrade` by přes nový snapshot
  přegeneroval předchozí capture — fabulace. Zápis jde do dočasného
  adresáře vedle a přejmenuje se na místo.
- Pojistka při upgradu: `started_at` v `session.json` musí souhlasit
  s `taken` záznamu v `run.yml`. Nesouhlasí-li, capture je „nelze – raw
  nepatří k tomuto snímku“.
- **Zápis raw selže** (disk, práva): capture uspěje, `CaptureOutcome.warnings`
  dostane „raw záznam se nepodařilo uložit – tento snímek po upgradu
  nástroje nepůjde přegenerovat (<důvod>)“. Varování vidí CLI i GUI task.
- **Inventory bez raw** (vyrobená před touto vlnou, nebo externí
  `--inventory` soubor): bundle capture uloží kopii YAML jako
  `inventory/inventory.yml` a v `session.json` `"raw": false`. `upgrade` ji
  pak použije jen tehdy, když ji aktuální verze načte; jinak „nelze –
  inventory bez raw záznamu“.

### Švy v kódu

- Nový balíček `migration_validator/raw/`:
  - `recorder.py` — `RecordingDevice` (obal `rpc`, `facts`, `hostname`)
    a `SessionRecording` (seznam volání v paměti),
  - `bundle.py` — zápis/čtení adresáře session, `raw_format`, cesty,
  - `replay.py` — `ReplayDevice`, výjimka `NotRecorded`,
  - `upgrade.py` — plán a provedení upgradu runu, report.
- `api.capture` dostane místo `record_raw` volitelný `recorder`; po
  `connect` obalí zařízení. `capture_into_run` ho vytvoří a po
  `save_snapshot` zapíše bundle.
- `_parse_services` nahrává svou session a zapisuje `raw/inventory_<Y>/`
  hned po zápisu inventory YAML.
- `RunStore` dostane cesty `raw_dir(...)`.

## 2. Replay

`ReplayDevice(session)`:

- `facts` a `hostname` ze `session.json`.
- `rpc.<jméno>(**kwargs)` hledá nahrané volání podle jména a argumentů
  (argumenty kanonicky: JSON se seřazenými klíči). Stejné volání nahrané
  vícekrát se vrací v pořadí nahrání a poslední odpověď se opakuje
  (např. `get_instance_information`, které volají dva multicast collectory).
- **Nahraná chyba** se vyhodí znovu jako výjimka se stejným jménem třídy
  a textem (dynamická podtřída `RecordedRpcError`), aby text statusu
  collectoru vyšel stejně jako při živém capture.
- **`get_config`** se páruje jen jménem. Vrátí nahranou konfiguraci
  ořezanou na hierarchie, o které filtr žádá. Žádá-li filtr hierarchii,
  která v nahrávce není, vyhodí `NotRecorded("konfigurace nemá hierarchii
  X")` — upgrade z toho udělá „nelze“, nikdy inventory z neúplné
  konfigurace.

### Co v nahrávce není, se nikdy nevydává za výsledek

Nový collector, změněné argumenty RPC, nový ping cíl → `NotRecorded`:

- **Collector:** `collect()` ji chytí jako každou jinou výjimku →
  `CollectorError` → status `error` → checky SKIP. Stávající cesta, žádná
  změna.
- **Ping:** `run_ping` dnes chytá cokoliv a zapíše `sent=count,
  received=0` — to by byl vymyšlený BROKEN „neodpovedel“. `run_ping`
  proto chytí `NotRecorded` **před** catch-all a zapíše `sent=0,
  received=0, error="neni v raw zaznamu"`. Check `ping_reachability` na
  `sent == 0` už dnes vrací SKIP „ping neodeslan“; nově k textu připojí
  `error`, je-li v záznamu: `X: ping neodeslan (neni v raw zaznamu)`.
  Snapshot nedostává nové pole.

`NotRecorded` bydlí v `raw/replay.py`; `probes/ping.py` ji importuje.

## 3. `mig-validate upgrade`

```
mig-validate upgrade RUN [RUN …] [--group NAME] [--dry-run] [--run-root DIR]
```

Postup pro jeden run:

1. **Inventory.** Každý `inventory_*.yml`, který má `raw/inventory_*`, se
   přegeneruje aktuálním parserem se stejným `port_filter`.
2. **pre a rollback capture** (nezávisí na baseline): inventory z vlastní
   kopie `inventory/` bundlu → `capture_device(ReplayDevice(...), …)`
   s parametry a časy ze `session.json`.
3. **post capture**: stejně, jako `baselines` dostane přegenerované verze
   snímků jmenovaných v `session.json` (ne dnešní `mapped_olds`). Nejde-li
   baseline přegenerovat ani načíst, post se přehraje bez ní a report to
   uvede; ping cíle, které se tím změní, skončí SKIP (sekce 2).
4. **Staging.** Všechno se zapisuje do `runs/<run>/.upgrade-staging/`.
   Spadne-li replay (výjimka jiná než `NotRecorded`/nahraná chyba = chyba
   nástroje), upgrade runu se zastaví, staging se smaže a v runu se nic
   nezmění.
5. **Výměna.** Nahrazované soubory se přesunou do
   `runs/<run>/backup/upgrade-<timestamp>/`, nové na jejich místo.
   `run.yml` se nemění (`taken` = původní `started_at`). Výměna je řada
   přejmenování, ne atomická operace; při pádu uprostřed je všechno
   původní v záloze.

Capture, které přegenerovat nejde, zůstávají beze změny a upgrade ostatních
nezastaví. Důvody:

- `bez raw záznamu (zachyceno před zavedením)`
- `konfigurace nemá hierarchii <X>`
- `inventory bez raw záznamu a nejde načíst`
- `raw nepatří k tomuto snímku` (nesouhlasí `started_at` / `taken`)

Report, řádek na capture (a na inventory):

```
pre  MX1-POP1  all        přegenerováno – beze změny
post PTX1-POP1 et-0/0/8   přegenerováno – změněno
post PTX1-POP1 ae0        nelze – bez raw záznamu (zachyceno před zavedením)
pre  MX1-POP1  ge-0/0/2   nelze – konfigurace nemá hierarchii chassis
záloha: runs/migration-01/backup/upgrade-20260923T101500Z/
```

„Beze změny“ = přegenerovaný JSON se rovná původnímu souboru (porovnání
načtených dict, ne bajtů).

`--dry-run` udělá celý replay do stagingu, vypíše report a staging smaže.
Replay je offline (žádný NETCONF, žádné přihlašovací údaje), běží v řádu
sekund.

`--group NAME` = všechny runy skupiny postupně; selhání jednoho runu
ostatní nezastaví.

Návratové kódy: `0` vše přegenerováno; `1` (`EXIT_FAILED_CHECKS`) některé
capture přegenerovat nešlo; `2` (`EXIT_TOOL_ERROR`) upgrade některého runu
spadl a run zůstal beze změny.

Adresáře `raw/`, `backup/` a `.upgrade-staging/` uvnitř runu nic jiného
nečte; archivace runu je přesouvá s ním.

## 4. GUI

### Chyby verze schématu místo 500

- `SnapshotVersionError` i chyba verze inventory (nová
  `InventoryVersionError(ValueError)` v `models/inventory.py`) dostanou
  v textu radu: „… nástroj umí 14 – přegeneruj run: `mig-validate upgrade
  <run>`“.
- `GET /api/runs/{run}/evaluation` a
  `GET /api/runs/{run}/snapshots/{file}/evaluation` vrátí 422
  s `detail` a `code: "schema_outdated"` místo 500.
- Souhrn skupiny: `run_verdict` / `_member_row` chytá dnes jen
  `ValueError` a `OSError`, takže `SnapshotVersionError` shodí celý
  endpoint. Nově řádek runu dostane `error` „zastaralé snímky – upgrade“
  a zbytek skupiny se vykreslí.

### Detail runu

`snapshot_list` (`gui/serializers.py`) přidá ke každému snímku
`schema_version` (`json.load` souboru, bez `load_snapshot`, takže funguje
i pro zastaralé schéma) a `has_raw`. Seznam
snímků ukáže štítek „zastaralý“ a zvlášť „bez raw“ (nepůjde přegenerovat).

### Upgrade run

- Tlačítko „Upgrade run“ vedle „Archive run“, oprávnění `ADMIN`. Je vidět
  vždy, nejen u zastaralých snímků — upgrade slouží i k promítnutí opravy
  parsování bez bumpu.
- Otevření modalu zavolá `POST /api/runs/{run}/upgrade?dry_run=1`; modal
  ukáže report (sekce 3) jako tabulku.
- „Apply“ zavolá tentýž endpoint bez `dry_run`; výsledek ukáže finální
  report a cestu k záloze, pak se znovu načte detail i evaluace runu.
- Požadavek je synchronní (replay je offline a krátký) — nejde přes frontu
  `CaptureManager`.

Odpověď:

```json
{"run": "migration-01", "dry_run": false,
 "backup": "backup/upgrade-20260923T101500Z",
 "items": [
   {"kind": "capture", "phase": "post", "device": "PTX1-POP1",
    "port": "et-0/0/8", "file": "snapshot_post_PTX1-POP1_et_0_0_8.json",
    "result": "changed", "reason": null},
   {"kind": "inventory", "file": "inventory_PTX1-POP1_all.yml",
    "result": "unchanged", "reason": null}
 ],
 "error": null}
```

`result` ∈ `unchanged | changed | not_regenerable`; spadlý run má
`error` s textem a prázdné `backup`.

### Upgrade group

- Tlačítko „Upgrade group“ vedle „Archive group“, oprávnění `ADMIN`.
- `POST /api/groups/{group}/upgrade?dry_run=1` / bez `dry_run` — runy
  postupně, odpověď = seznam odpovědí per run; modal má sekci na run.
- Selhání jednoho runu ostatní nezastaví.
- Odmítne 409, má-li kterýkoliv run skupiny aktivní capture (jako archive).

### Zámek runu

`CaptureManager` dostane per-run příznak údržby (`maintenance(run)` jako
context manager):

- vstup odmítne (409), když na runu čeká nebo běží capture, nebo když už
  run údržbu má,
- `start()` odmítne capture do runu v údržbě (409 „run se upgraduje“),
- kontrola i nastavení příznaku jsou pod stávajícím zámkem manageru, takže
  tu není závod, který přiznává komentář u archivace,
- zámek drží i `dry_run` (čte snímky a raw; kolize s capture by dala
  nekonzistentní náhled).

Upgrade skupiny bere zámek run po runu. Spustí-li někdo capture do runu,
na který skupina ještě nedošla, upgrade toho runu dostane 409, zapíše se
do odpovědi jako `error` a skupina pokračuje dalším runem.

## 5. Odstraněno

- `--record-raw` z CLI (`capture`), parametr `record_raw` z `api.capture`,
  `capture_into_run` a `capture_device`, funkce `capture._record`.
- Příkaz `record` (fixtures pro testy) zůstává beze změny.

## 6. Testy

- **Recorder a replay (unit):** pořadí volání; odpovědi jako XML
  (serializace bez ztráty textových uzlů); nahraná chyba se vrátí se
  stejným jménem třídy a textem; opakovaná stejná volání v pořadí, poslední
  se opakuje; nenahrané volání → `NotRecorded`; `get_config` ořez na
  požadované hierarchie; chybějící hierarchie → `NotRecorded`.
- **Round-trip identita** na MX i EVO fixtures: `generate_inventory`
  a `capture_device` přes `RecordingDevice`, zápis bundlu, replay → shodná
  inventory i snapshot. Hlavní pojistka, že replay neodbočí od živého
  capture.
- **Nenahrané není selhání:**
  - ping na nenahraný cíl → SKIP „ping neodeslan (neni v raw zaznamu)“,
    nikdy BROKEN. Implementer ověří spuštěním mutantu (miss propadne do
    catch-all v `run_ping`), že test selže.
  - collector bez nahrávky → status `error` → jeho checky SKIP.
- **Retrieve configuration:** parser dostane jen své hierarchie i když
  odpověď nese `class-of-service interfaces interface`.
- **Životní cyklus bundlu:** přepis post/pre odstraní starý raw; selhání
  zápisu raw = varování a žádný starý raw na místě; inventory bez raw →
  kopie YAML a `"raw": false`.
- **Upgrade nad testovacím run adresářem** (pre + post s raw, jeden
  capture bez raw, jedna inventory bez raw): post dostane přegenerovanou
  pre jako baseline; staging, záloha, report; `--dry-run` nic nezmění; pád
  replaye nechá run nedotčený; capture bez raw se vypíše a soubor se
  nezmění; nesouhlasící `started_at`/`taken` → „nelze“; návratové kódy.
- **GUI:** dry-run i apply; 409 při běžícím capture; capture odmítnutý
  během upgradu; druhý upgrade téhož runu 409; „Upgrade group“ pokračuje
  po selhání jednoho runu; 422 `schema_outdated` místo 500 u obou
  evaluačních endpointů; řádek souhrnu skupiny s `error` místo pádu;
  `schema_version` a `has_raw` v detailu runu.

## 7. Dokumentace

README: sekce o raw retention (co se ukládá, kde, že je v tom konfigurace
včetně `$9$` klíčů pod `protocols`) a o `mig-validate upgrade` včetně GUI
tlačítek.

## 8. Laboratorní ověření (akceptace)

- `capture --parse-services` na MX1-POP1 (MX) a PTX1-POP1 (EVO): obě
  platformy přijmou rozšířený `get_config` filtr.
- Na čerstvém laboratorním runu vrátí `upgrade --dry-run` pro všechny
  capture „beze změny“.
- Zapsat velikost raw (gzip) na capture jako podklad k odhadu pro produkci.
- Po merge restartovat laboratorní GUI.

## Mimo rozsah

- Vrstva 2 kolize adres peerů (překlíčování `bgp`/`bfd`, bump schématu na
  14) — samostatný spec, staví na této vlně.
- Shim pro snímky zachycené před touto vlnou (zamítnuto výše).
- Capture v jedné NETCONF session (parkované z rate-limit follow-upu).
- Generování testovacích fixtures z raw bundlu.
- Deduplikace kopií konfigurace mezi bundly a automatický úklid
  `backup/`.
