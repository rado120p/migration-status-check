# Odstranění device režimu — design

Datum: 2026-09-24

## Cíl

Device režim (jediný pseudo-scope `device`, který propouští všechna fakta
boxu) se ruší. Snímek bez služeb se místo něj vyhodnotí jako „žádné
migrované služby“: žádné checky, jasná hláška v reportu i v GUI, NEZARAZENO
běží dál.

Proč teď: vrstva 2 kolize adres peerů (re-key collectorů bgp/bfd/evpn_esi,
bump schématu 13 → 14, viz
`docs/superpowers/review-2026-09-24-kolize-klicu.md`) by jinak musela řešit
popisky řádků pro device režim, kde se peery všech VRF sejdou v jednom
bloku. Bez device režimu vrstva 2 řeší jen per-service bloky, kde je VRF
dána službou.

Formát snapshotu ani inventory se **nemění** (schéma zůstává 13 / 10).

## Uzavřená rozhodnutí (brainstorm 2026-09-24, nerelitigovat)

- **Device režim jako vyhodnocovací režim zaniká.** Nástroj se používá
  hlavně přes GUI a všechny run cesty (GUI i CLI `--run`) inventory
  vyžadují (`capture_into_run`: „inventory nenalezena - spust s
  --parse-services“). Rozhodnutí AR-10 (`evaluate --snapshot X` bez
  inventory jako prohlížení jednoho zařízení) a AR-17 (bez inventory se
  nehlásí chybějící konfigurace) tím padají.
- **Dnešní skrytá cesta do device režimu se ruší taky.** `engine._scopes_of`
  padá do device scope u *každého* snímku bez scopů — i u per-port capture
  portu, který nenese žádnou migrovanou službu (`build_scopes()` vrátí `[]`,
  protože Layer1 scope vzniká jen pro port se službou). Takový port se dnes
  potichu vyhodnotí jako celý box v jednom bloku s vypnutým NEZARAZENO.
- **Snímek bez služeb není chyba.** Neutrální stav, CLI exit `EXIT_OK`,
  report i GUI to řeknou výslovně. Nic se nezkontrolovalo, nic tedy
  neselhalo.
- **„Žádné služby“ = subjekt nemá žádný *service* scope**, ne „nemá žádný
  scope“. Dnes je to totéž (Layer1 scope bez služby nevzniká), ale budoucí
  box-level scope (viz Mimo rozsah) by jinak tuhle logiku rozbil.
- **`mig-validate capture` bez `--inventory` zůstává** jako čistě datový
  capture (debug, raw). Jeho vyhodnocení dá „žádné služby“ + NEZARAZENO celého
  boxu. Stávající snímky se `scopes: []` se dál načtou.
- **Jméno `device` se pro budoucí box-level scope nepoužije znovu** — staré
  JSON výstupy nesou `scope_id: "device"` ve smyslu „všechno na boxu“.

## 1. Chování

### Engine

- `_scopes_of(snapshot)` vrací `snapshot.scopes` beze změny, klidně prázdný
  seznam. `device_scope()` se nevolá nikde.
- Subjekt bez service scopů: žádný `ScopeResult` za službu. Layer1 scopy se
  řídí svým dnešním pravidlem (bez služby na portu nevznikají).
- Baseline bez scopů (starý capture bez inventory) + subjekt se službami:
  služby subjektu skončí jako nesparované, stejně jako když matcher nenajde
  protějšek. Žádná zvláštní větev.
- **NEZARAZENO běží i bez scopů.** Tři `_unassigned_*` funkce ztratí
  `if any(scope.is_device …): return []`. S prázdným seznamem scopů:
  - celoboxový snímek → vypíše všechny BGP peery, statiky a BFD session
    (u datového capture bez inventory to je přehled zařízení),
  - per-port snímek → dnešní zúžení na port (`_interface_on_port`,
    `_scope_instances`, `_peer_in_scope_subnets`) zůstává; bez scopů
    přirozeně zůstane jen to, co jde přes rozhraní toho portu.
- `RunResult` dostane aditivní klíč **`no_services: true`**, vyplněný jen
  když subjekt nemá žádný service scope (stejné pravidlo jako `filtered`,
  `excluded_services`: prázdný/nepravdivý se do JSON nezapisuje, výstup
  běžných běhů se nemění). `RunResult.schema_version` zůstává 1.
- Filtr profilu (`service_types`) a step filtr (`excluded_services`) na
  `no_services` nemají vliv — klíč popisuje subjekt, ne co z něj zbylo po
  filtru. (Subjekt se službami, ze kterých filtr všechno vyřadil, má dnešní
  chování, `no_services` nedostane.)

### Report (text)

Místo bloků služeb jeden řádek:

```
Subjekt nema v inventory zadne migrovane sluzby - checky nebezely.
```

Hlavička (zařízení, fáze, profil) a sekce NEZARAZENO / NESPAROVANO se
vypisují jako dnes.

### GUI

Párování, jehož vyhodnocení má `no_services`, ukáže neutrální stav
**„No migrated services“** — ne PASS, ne FAIL, ne prázdnou tabulku. Počty
PASS/WARN/FAIL zůstanou nulové. NEZARAZENO se zobrazí jako dnes.

### CLI

`evaluate` nad snímkem bez služeb: report výše, exit `EXIT_OK`, i s
`--warn-as-error`. Exit se dnes řídí jen `summary["fail"]` /
`summary["warn"]`, které počítají výsledky checků; NEZARAZENO ani
NESPAROVANO exit nemění. Bez checků jsou oba počty nulové, takže se v CLI
nemění nic — test to jen zafixuje.

## 2. Co se odstraní

| Soubor | Změna |
|---|---|
| `models/scope.py` | `device_scope()`, `DEVICE_SCOPE_ID`, `Scope.is_device`, `kind="device"` v komentáři dataclass, device větev v `Scope.select`, docstring modulu („bez inventory existuje jediny device scope…“), zmínka v docstringu `_empty` |
| `checks/base.py` | device větev v `applies_to`; atribut `requires_inventory` a jeho klíč v `describe()`; skip „check vyzaduje inventory“ v `run_check`; komentář o pořadí zkratek u deaktivace se zjednoduší |
| `checks/*.py` | `requires_inventory = True` u 16 checků (deactivation, multicast ×5, reachability ×3, core_protocols ×7) |
| `checks/bfd.py` | parametr `is_device` v `_finding` / `_session_value`, větev `if is_device` (AR-17), konstanta `SESSION_GONE` (jinde se nepoužívá), komentáře AR-17 |
| `checks/routes.py` | jen komentáře o device scope / AR-17 v modulu a u `subject is None`; větev sama zůstává (routa v baseline, v subjektu chybí — platí pro služby) |
| `probes/ping.py` | `scope.is_device` v `resolve_targets`, docstring modulu („Ping bezi jen v service rezimu…“) |
| `scoping/linker.py` | čtyři `scope.is_device` podmínky |
| `engine.py` | `device_scope` import a fallback v `_scopes_of`, tři `is_device` návraty v `_unassigned_*`, komentář AR-10 v `_identity` |
| `gui/static/run_results.js` | `DEVICE_SCOPE_ID` a klasifikace `"device"`; nový neutrální stav pro `no_services` (a jeho render v `app.js`) |

`describe()` / `/api/checks` / `mig-validate checks` ztratí klíč
`requires_inventory` — v repu ho nic nečte (ověřeno grepem: jen
`checks/base.py` a `tests/checks/test_base.py`).

Žádný check tím nepřestane běžet: jediné checky s prázdným `service_types`
(`interface_optics_alarms`, `interface_optics_levels`) mají `layer1 = True`;
`service_types = None` znamená „všechny služby“.

## 3. Dokumentace

Aktualizovat zmínky o device režimu:

- `docs/cs/README.md` (odstavec „Bez inventory nástroj funguje taky…“,
  tabulka přepínačů `--inventory`), `docs/cs/architecture.md:139`,
  `docs/cs/reference.md:590`, `docs/cs/files/{probes,top-level,models,checks}.md`
- `docs/en/reference.md`, `docs/en/files/{probes,checks,models,top-level}.md`

Nové znění: bez inventory `capture` sebere data, `evaluate` řekne „žádné
migrované služby“ a vypíše NEZARAZENO.

## 4. Testy

Změřeno 2026-09-24: s `device_scope()` nahrazeným výjimkou padá 24 testů
(test_scope 6, test_base 6, test_engine 3, test_bfd 2, po jednom
test_linker, test_ping, test_scope_multicast, test_scope_core,
test_reachability, test_optics, test_core_protocols). Každý se buď přepíše
na service scope (kde testoval check/výběr a device scope byl jen
pohodlný fixture), nebo smaže (kde testoval čistě device chování — AR-17,
propouštění všeho, `requires_inventory` skip).

Nové testy (TDD, nejdřív červené):

- engine: celoboxový snímek bez scopů → žádné ScopeResulty, `no_services`,
  NEZARAZENO vyplněné (bgp, statika, bfd).
- engine: per-port snímek bez scopů → NEZARAZENO zúžené na port (BFD na
  rozhraní portu ano, peer cizí VRF ne).
- engine: subjekt jen s Layer1 bez služeb se nemůže stát (builder), ale
  `no_services` se počítá ze service scopů — test se syntetickým snímkem,
  který nese jen layer1 scope, dostane `no_services`.
- engine: subjekt se službami → klíč `no_services` v `to_dict()` chybí.
- engine: baseline bez scopů + subjekt se službou → služba nesparovaná,
  žádná výjimka.
- text report: řádek „Subjekt nema v inventory zadne migrovane sluzby…“.
- CLI: `evaluate` nad snímkem bez služeb → exit 0.
- JS (`tests/js/run_results.test.js`): vyhodnocení s `no_services` →
  neutrální stav, ne PASS.

Celá sada + JS testy zelené; počet testů se sníží o smazané device testy
a zvýší o nové — v plánu se čísla změří, ne odhadnou.

## Mimo rozsah

- **Box-level checky** (`show chassis fpc`, `show version`, alarmy): budoucí
  vlastní scope `kind="chassis"` (id `chassis:<hostname>`), který vytvoří
  builder vždy a checky se k němu přihlásí atributem jako dnes `layer1`.
  Fakta dostane explicitně ve `Scope.select` (vzor: `isis_overview` jen pro
  Core loopback), párování starý → nový box vlastním 1:1 pravidlem (vzor:
  `_l1_baseline`). Tahle vlna jim nic nebrání; `no_services` počítá jen
  service scopy právě kvůli nim.
- Vrstva 2 kolize adres peerů — samostatný spec po této vlně.
- Zpětná úprava uložených `report.txt` / výsledků se `scope_id: "device"` —
  zůstávají, jak vznikly.
