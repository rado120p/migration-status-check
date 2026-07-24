# Migration Validator — návrh

**Datum:** 2026-07-24
**Stav:** schváleno, připraveno k tvorbě implementačního plánu

## Účel

Nástroj pro validaci stavu síťových služeb při migraci z routeru MX (běžný Junos) na router
s Junos EVO. Ověřuje stav služeb před migrací, po migraci, a porovnává obojí mezi dvěma
různými zařízeními, kde se názvy portů liší (`ge-0/0/0` → `et-0/0/6`).

Primární rozhraní je CLI. Návrh ale počítá s tím, že nástroj bude později orchestrovat GUI,
takže veškerá funkcionalita je dostupná přes programové API a všechny výsledky jsou
serializovatelné do JSON.

## Cílový workflow operátora

| krok | akce | volání |
|---|---|---|
| 1 | operátor vytvoří workflow pro původní a nové zařízení | — |
| 2 | spustí parser služeb na původním zařízení | (existující parser) |
| 3 | spustí sběr a validaci na původním zařízení | `capture(old)` + `evaluate(snap_old)` |
| 4 | provede migraci (přepojení kabelu) | — |
| 5 | spustí parser služeb na novém zařízení | (existující parser) |
| 6 | spustí sběr a validaci na novém zařízení | `capture(new)` + `evaluate(snap_new, baseline=snap_old)` |
| 7 | vidí souhrn výsledků pro všechny služby | `RunResult` → GUI / text |
| 8 | filtruje podle identifikátoru služby | filtrování hotového `RunResult` |

## Rozsah

**V rozsahu:**

- sběr operačního stavu z Junos i Junos EVO přes PyEZ
- zmrazení stavu do self-contained JSON snapshotu
- validace jednoho snapshotu (stavové checky)
- porovnání dvou snapshotů napříč zařízeními (porovnávací checky)
- párování služeb mezi zařízeními včetně fallbacků a ručního override
- CLI s textovým a JSON výstupem
- programové API jako šev pro budoucí GUI

**Mimo rozsah:**

- samotné GUI
- refaktoring existujících parserů `mx_parser.py` / `evo_parser.py` (viz *Vztah k existujícím parserům*)
- automatická migrace konfigurace

---

## Architektonická rozhodnutí

### AR-1: Čistý PyEZ, vlastní framework — ne pyATS, ne JSNAPy

**Rozhodnutí:** nástroj stojí na `jnpr.junos` (PyEZ). pyATS ani JSNAPy se nepoužijí.

**Důvod:** pyATS (Easypy/AEtest) i JSNAPy jsou postavené kolem modelu *"snapshot A vs snapshot B
na stejném zařízení"*. Jakmile je potřeba mapovat služby mezi dvěma různými zařízeními s jinými
názvy portů, ani jeden nepomáhá a porovnávací logika se stejně píše ručně. Genie `Diff` umí jen
before/after na stejném boxu.

Navíc:

- Genie parsery pro Junos parsují **CLI text**; PyEZ dává strukturované XML přes RPC.
- PyEZ má vestavěný deklarativní parser framework — **Tables & Views** — kde je operační parser
  pár řádků YAML (RPC + XPath → dict) místo ručního lxml. To pokrývá potřebu "custom Juniper
  parserů" nativně.
- Easypy chce vlastnit životní cyklus běhu a produkuje vlastní reporty; cílové GUI chce vlastnit
  životní cyklus taky. To se pere.

**Co se ztrácí a jak se to nahrazuje:** testbed inventory → vlastní inventory YAML;
multi-vendor → nepotřebný, obě strany jsou Juniper; reporting → vlastní, což je stejně požadavek.

Existující JSNAPy testy nejsou ztracené — jsou to `snapcheck` definice nad RPC/XPath a dají se
mechanicky převést do stejného collector + check modelu.

### AR-2: Snapshot-centric model (capture → evaluate)

**Rozhodnutí:** dvě oddělené operace. `capture` sáhne na zařízení a zmrazí veškerý stav do JSON
snapshotu. `evaluate` je **čistá funkce nad snapshoty a nesahá na síť vůbec**.

**Důvod:**

- Pre-migration stav musí být zamrzlý *před* migrací; jiný model to neumožňuje bez toho, aby
  stejně zavedl ukládání.
- `evaluate` je testovatelné bez laboratoře — dva JSONy a `pytest`.
- Opakované spuštění validace nezatěžuje produkci a **vrací identický výsledek**.
- Mapuje se doslova na kroky 2/3/5/6 workflow.

**Zamítnuté alternativy:** live-compare v jednom příkazu (nefunguje pro pre-migration fázi,
netestovatelné); hybrid, kde `evaluate` bere snapshot *nebo* živé zařízení (zdvojuje vstupní
cesty a testovací matici — přidá se, až si to vyžádá praxe).

### AR-3: `evaluate` s volitelným baseline

**Rozhodnutí:**

```python
capture(device, *, inventory=None, collectors=None) -> Snapshot
evaluate(snapshot, *, baseline=None, mapping=None, config=None) -> RunResult
list_checks() -> list[CheckDescriptor]
```

**Důvod:** krok 3 workflow validuje původní zařízení *před* migrací, kdy žádné "nové" zařízení
neexistuje. `compare(old, new)` to neumí vyjádřit.

Bez `baseline` běží jen **stavové** checky. S `baseline` navíc **porovnávací**. Check deklaruje
svůj `mode`; porovnávací check bez baseline vrací `SKIP` s důvodem, nikoli chybu.

### AR-4: Inventory je volitelná vrstva, ne povinný vstup

**Rozhodnutí:** validační checky musí být použitelné samostatně, bez inventory.

**Realizace přes abstrakci `Scope`:**

| režim | scope | co check dostane |
|---|---|---|
| bez inventory | jeden scope = celé zařízení | všechna data, bez filtru |
| s inventory | jeden scope per služba | filtr: rozhraní, RI, BGP peer IP, VLAN, bridge domain |

Check je vždy stejná funkce `check(facts, scope) → CheckResult` a neví, odkud scope přišel.
Bez inventory `bgp_session_state` řekne "z 12 peerů je 11 Established"; s inventory totéž
rozpadlé na služby. **Stejný kód, stejný JSON tvar, jiná granularita.**

Důsledky:

- **Bulk sběr na inventory nezávisí.** `capture` jede pevnou sadu RPC podle platformy, takže blok
  `facts` je v obou režimech stejný.
- **Aktivní probes na inventory závisí.** Ping potřebuje cíl a source adresu, což jsou informace
  ze scope. V device režimu (bez inventory) se ping **nespouští** a snapshot má `probes.ping: []`.
  Invariant tedy zní: *`facts` jsou inventory-independent, `probes` nikoliv.*
- **Inventory se ukládá do snapshotu**, pokud byla použita → snapshot je self-contained.
- Checky, které bez inventory nedávají smysl (ping potřebuje cíl a source adresu), to deklarují
  přes `requires_inventory` a bez ní vrací `SKIP`.
- Párování je operace nad **scopy**, ne nad checky. Bez inventory je triviální: jeden scope proti
  jednomu scope.

### AR-5: Sběr je device-scoped, korelace service-scoped

**Rozhodnutí:** nejedou se RPC per služba. Jede se jeden `get-bgp-summary-information` na celé
zařízení a výsledek se až následně rozdělí mezi služby.

**Výjimka:** ping je z podstaty aktivní a cílený, běží per cíl. Má proto vlastní kategorii
(`probes`, ne `collectors`).

### AR-6: Tvrdé oddělení collector / check

**Rozhodnutí:**

- **Collector nikdy neinterpretuje.** Vrací syrová strukturovaná data, nikdy `{"bgp_ok": true}`.
- **Check nikdy nesahá na síť.** Nemá `Device`, socket ani timeout.

**Důsledek:** změní-li se kritérium, mění se check, ne sběr — a staré snapshoty zůstávají
použitelné. Celá rozhodovací logika je testovatelná offline.

### AR-6b: Zarovnání názvů rozhraní při porovnání (doplněno při implementaci)

**Problém:** checky dostávají data profiltrovaná scopem a `interface_traffic` porovnává baseline vs
subject **podle názvu rozhraní**. Při migraci se ale port přejmenuje (`ge-0/0/2.113` → `et-0/0/8.113`),
takže baseline data jsou pod jiným klíčem, než jaký check hledá u subjektu. Bez ošetření by porovnání
tiše spadlo do stavového režimu a ztratil se signál o poklesu datovosti — přesně u služeb, kde se
rozhraní přejmenovalo.

**Řešení:** engine u spárované dvojice přejmenuje klíče baseline rozhraní na názvy subjektu, než data
předá checkům (`engine._aligned_baseline_data`). Je to poziční mapování a je jednoznačné, protože
service scope má v selektoru právě jedno logické rozhraní (AR-8, `scoping/builder`).

**Rozsah:** dotčen je jen `interface_traffic`. BGP se klíčuje IP adresou peera a EVPN názvem instance —
obojí zůstává při migraci stabilní, takže se zarovnání netýká. Kdyby invariant „jedno rozhraní na scope"
kdy padl, mapování se má chránit assertem, ať selže hlasitě.

### AR-7: Platformní rozdíly řeší collector, ne check

**Rozhodnutí:** collector deklaruje podporované platformy a případně má variantu RPC per
platforma, ale navenek vrací **stejné schéma**.

**Příklad:** EVPN VLAN-aware je na MX `virtual-switch` + `bridge-domain`, na EVO `mac-vrf` + VLAN.
Collector obojí přeloží do jednoho tvaru, takže check `evpn_mac_count` nikdy neobsahuje
`if platform == "evo"`. Tohle je předpoklad, na kterém stojí cross-device porovnání.

### AR-8: Vztah k existujícím parserům

**Rozhodnutí:** `mx_parser.py` a `evo_parser.py` zůstávají zatím beze změny. Validator konzumuje
jejich YAML výstup jako inventory. Sdílené jádro (connection, inventory model, output) se navrhuje
správně od začátku, ale parsery se do něj přesunou až jako samostatný krok — **pokud vůbec**.

**Důvod:** oba soubory jsou z ~90 % identické a refaktoring by dával smysl, ale není to to, o co
jde teď, a zdržel by validator. Inventory model je v novém balíku definovaný jako dataclass, takže
integrace později znamená jen přepojení výstupu.

---

## Architektura

### Vrstvy a směr závislostí

```
connection  ──►  collectors  ──►  snapshot (JSON)
                 probes      ──►

                                  snapshot ──►  scoping  ──►  matcher
                                                                 │
                                                                 ▼
                                              snapshot ──►    checks   ──►  results
                                                                              │
                                                                              ▼
                                                                          reporting
```

| vrstva | smí | nesmí |
|---|---|---|
| `connection` | připojit se, poslat RPC, vrátit XML | interpretovat obsah |
| `collectors` | XML → strukturovaný dict | rozhodovat PASS/FAIL, znát služby |
| `probes` | aktivní testy (ping) | cokoliv jiného |
| `scoping` | inventory → scopy, párování starý↔nový | číst nasbíraná data |
| `checks` | čistá funkce `(facts, scope) → CheckResult` | sáhnout na síť |
| `reporting` | results → JSON / text | vyhodnocovat |

### Struktura balíku

```
migration_validator/
├── cli.py              # tenký entrypoint nad api.py
├── api.py              # capture(), evaluate(), list_checks()  ← šev pro GUI
├── config.py           # config.yml: severity overrides, tolerance, timeouty
│
├── models/
│   ├── inventory.py    #   ServiceEntry — schéma YAML z parserů
│   ├── snapshot.py     #   Snapshot, DeviceMeta, FactSet
│   ├── scope.py        #   Scope (device | service) + selektory
│   └── result.py       #   Status, CheckResult, ScopeResult, RunResult
│
├── connection/junos.py # PyEZ Device wrapper, auth, retry, context manager
│
├── collectors/
│   ├── base.py         #   ABC: name, platforms, rpc(), parse()
│   ├── registry.py
│   ├── interfaces.py   #   traffic-statistics (input/output pps), oper state, errors
│   ├── arp.py
│   ├── bgp.py
│   ├── evpn.py         #   vpws-sid-pe-status, esi-status, MAC counts
│   └── tables/*.yml    #   PyEZ Tables & Views
│
├── probes/ping.py
│
├── scoping/
│   ├── builder.py      # inventory → [ServiceScope]; bez inventory → [DeviceScope]
│   └── matcher.py      # párování scopů + fallback řetěz + mapping.yml
│
├── checks/
│   ├── base.py         # ABC + odvození statusu
│   ├── registry.py
│   ├── interface_state.py      bgp_session_state.py   evpn_vpws_status.py
│   ├── interface_errors.py     bgp_prefix_counts.py   evpn_esi_status.py
│   ├── interface_traffic.py                           evpn_mac_count.py
│   ├── arp_present.py
│   └── ping_reachability.py
│
└── reporting/
    ├── json_report.py
    └── text_report.py
```

### Šev pro GUI

`api.py` je jediné, co GUI volá. `cli.py` je **tenký obal nad `api.py`, ne alternativní
implementace** — cokoliv umí CLI, umí i GUI, protože jdou stejnou cestou.

`list_checks()` existuje proto, aby GUI nemuselo mít hardcoded seznam testů: check se přidá do
registry a objeví se v obou rozhraních.

---

## Datový tok

### Průběh `capture`

```
1. connect                    PyEZ Device, auth, facts (model, verze, platforma)
2. bulk collectors            paralelně, jeden RPC = jedna oblast → facts
3. build scopes               z inventory (nebo jediný device scope)
4. resolve ping targets       scope + ARP facts → seznam cílů   (jen service scopy)
5. probes: ping               aktivní, per cíl                  (v device režimu se přeskočí)
6. freeze                     zápis snapshot.json
```

Závislost ARP → ping se odehraje **celá zde**. Ve fázi `evaluate` je výsledek pingu obyčejná data,
takže **checky jsou navzájem nezávislé** a lze je pouštět v libovolném pořadí nebo paralelně.

### Formát snapshotu

Self-contained. `evaluate` k němu nepotřebuje ani inventory, ani síť.

```jsonc
{
  "schema_version": 1,
  "device": {
    "address": "172.20.20.4", "hostname": "MX1-POP1",
    "platform": "junos",              // junos | junos-evo
    "model": "mx204", "version": "21.4R3-S4", "uptime_seconds": 8123456
  },
  "capture": {
    "started_at": "2026-07-24T09:12:03Z", "finished_at": "2026-07-24T09:12:41Z",
    "phase": "pre-migration",
    "collectors": {
      "interfaces": {"status": "ok"},
      "bgp":        {"status": "ok"},
      "evpn_esi":   {"status": "error", "message": "RpcError: syntax error"}
    }
  },
  "inventory": [ /* ServiceEntry z parseru, nebo null */ ],
  "scopes":    [ /* viz níže */ ],
  "facts": {
    "interfaces": {
      "ge-0/0/2.113": {
        "admin_status": "up", "oper_status": "up",
        "input_pps": 412, "output_pps": 388,
        "input_errors": 0, "output_errors": 0
      }
    },
    "arp": [
      {"ip": "198.11.13.2", "mac": "00:1b:...", "interface": "ge-0/0/2.113",
       "routing_instance": "L3VPN-CPE13-NNI"}
    ],
    "bgp": {
      "198.11.13.2": {
        "state": "Established", "peer_as": 65013,
        "routing_instance": "L3VPN-CPE13-NNI",
        "prefixes": {"received": 14, "accepted": 14, "advertised": 3}
      }
    },
    "evpn_vpws":  { "EVPN-VPWS-CPE13-NNI": {"local_sid": 213, "remote_sid": 213, "status": "Up"} },
    "evpn_esi":   { "00:11:22:...": {"status": "Up", "df_role": "DF", "interface": "ae0"} },
    "evpn_mac":   { "EVPN-VLAN-AWARE-CPE13-NNI": {"BD-313": 42} }
  },
  "probes": {
    "ping": [
      {"scope_id": "svc:L3VPN-CPE13-NNI:IPVPN", "target": "198.11.13.2",
       "source": "198.11.13.1", "routing_instance": "L3VPN-CPE13-NNI",
       "resolved_from": "arp",
       "sent": 5, "received": 5, "loss_percent": 0, "rtt_avg_ms": 1.24}
    ]
  }
}
```

`facts` jsou syrová, device-scoped, klíčovaná přirozeným klíčem (rozhraní, IP peera, jméno
instance). Žádné `service_id` uvnitř. Kdyby se párování ukázalo jako špatné, opraví se a **staré
snapshoty se přehodnotí znovu**, bez sahání na zařízení.

`capture.collectors` nese stav každého sběru zvlášť — selhání jednoho RPC nezruší celý capture.

### Scope

```jsonc
{
  "id": "svc:L3VPN-CPE13-NNI:IPVPN",
  "kind": "service",                      // service | device
  "key": {"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN", "service_subtype": null},
  "selectors": {
    "interfaces":        ["ge-0/0/2.113"],
    "routing_instances": ["L3VPN-CPE13-NNI"],
    "bgp_neighbors":     ["198.11.13.2", "2001:db8:11:13::b"],
    "local_addresses":   ["198.11.13.1/30", "2001:db8:11:13::a/127"],
    "virtual_gw":        [],
    "vlans":             ["113"],
    "bridge_domains":    []
  }
}
```

Scope je **čistě filtr**, neobsahuje naměřená data. Device scope má `kind: "device"` a prázdné
selektory = "ber všechno". Tím se režim bez inventory realizuje bez jediné větve v kódu checků:
check si vždy vytáhne data přes `scope.select(facts)`.

### Formát výsledku

```jsonc
{
  "schema_version": 1,
  "evaluated_at": "2026-07-24T11:40:02Z",
  "subject":  {"address": "172.20.20.5", "phase": "post-migration", "captured_at": "..."},
  "baseline": {"address": "172.20.20.4", "phase": "pre-migration",  "captured_at": "..."},

  "summary": {
    "pass": 40, "warn": 6, "fail": 2, "skip": 3,
    "scopes_matched": 18, "unmatched_baseline": 1, "unmatched_subject": 2
  },

  "scopes": [
    {
      "scope_id": "svc:L3VPN-CPE13-NNI:IPVPN",
      "key": {"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN"},
      "status": "WARN",
      "match": {
        "status": "matched", "method": "description+service_type", "confidence": "high",
        "baseline_interfaces": ["ge-0/0/2.113"], "subject_interfaces": ["et-0/0/8.113"]
      },
      "checks": [
        {
          "id": "interface_traffic", "mode": "compare",
          "status": "WARN", "severity": "advisory",
          "message": "output-pps kleslo o 72 % (410 → 115), práh je -60 %",
          "baseline": {"input_pps": 412, "output_pps": 410},
          "subject":  {"input_pps": 398, "output_pps": 115},
          "details":  {"tolerance_percent": -60}
        }
      ]
    }
  ],

  "unmatched": {
    "baseline": [{"scope_id": "svc:L3VPN-CPE99-NNI:IPVPN", "reason": "no candidate on subject"}],
    "subject":  [{"scope_id": "svc:EVPN-VLAN-AWARE-INTERNET:E-LAN",  "reason": "new service, no baseline"}]
  },

  "unassigned": {
    "bgp_peers": [{"peer": "10.1.0.5", "routing_instance": null, "snapshot": "subject"}]
  }
}
```

Vlastnosti tvaru:

- **`unmatched` je první třídou výstupu**, ne poznámkou pod čarou. GUI i terminál ho musí
  zobrazit vždy.
- **`unassigned`** drží data, která se nepodařilo přiřadit k žádnému scope (typicky BGP peery
  mimo známé služby). Stejná logika jako u `unmatched` — nic se tiše nezahazuje.
- **Každý check nese `baseline` i `subject` bloky se surovými čísly**, ne jen verdikt.
- **`message` je česky** — konzistentně s existujícími parsery.
- `status` scope = nejhorší stav jeho checků; `summary` = agregát. GUI ani terminál nic nepočítají.

### Artefakty na disku

```
runs/2026-07-24_MX1-POP1_migration/
├── pre/172.20.20.4.snapshot.json
├── post/172.20.20.5.snapshot.json
├── pre-validation.result.json
└── post-validation.result.json
```

Výchozí konvence; CLI vždy umožňuje `--output <cesta>` explicitně, protože GUI si bude chtít cesty
řídit samo.

---

## Checky

### Rozhraní

```python
class Check(ABC):
    id: str                          # "bgp_session_state"
    title: str                       # "Stav BGP session"
    mode: Mode                       # state | compare | both
    requires: tuple[str, ...]        # klíče z facts/probes — např. ("bgp",)
    requires_inventory: bool = False
    applies_to: ServiceFilter        # service_type/subtype, nebo ANY
    default_severity: Severity       # critical | advisory

    def run(self, ctx: CheckContext) -> list[CheckResult]: ...
```

```python
ctx.scope       # filtr
ctx.subject     # facts VYBRANÉ scopem — už profiltrované
ctx.baseline    # totéž z baseline snapshotu, nebo None
ctx.config      # tolerance, severity override
```

Check dostává **už profiltrovaná data**, takže nemůže omylem sáhnout na cizí službu. Filtrování je
zodpovědnost scope vrstvy a testuje se zvlášť.

### Odvození statusu

Check nevrací `PASS`/`FAIL` přímo — vrací výsledek měření a framework z něj status odvodí:

| check vrátí | severity `critical` | severity `advisory` |
|---|---|---|
| `ok` | PASS | PASS |
| `degraded` (částečný úspěch) | **WARN** | **WARN** |
| `broken` | FAIL | WARN |
| `skip(reason)` | SKIP | SKIP |

Pravidlo *"částečný úspěch = WARN"* je tím zapsané **jednou na jednom místě**, ne v každém checku.
Ping na 2 ze 3 adres je WARN i tehdy, když je ping v configu přepnutý na `critical`. Autor nového
checku tuhle konvenci nemůže omylem porušit.

### Konfigurace

```yaml
checks:
  ping_reachability:
    enabled: true
    severity: advisory
    count: 5
  interface_traffic:
    tolerance_percent: -60
    require_nonzero: true
  bgp_prefix_counts:
    tolerance_percent: -10
    severity: advisory
```

Severity má default v kódu a jde přepsat v configu. Tolerance nejsou nikdy zadrátované.

### Katalog

**Společné (jakýkoliv scope, i device bez inventory)**

| id | mode | severity | co dělá | omezení |
|---|---|---|---|---|
| `interface_state` | state | critical | admin/oper status je `up` | — |
| `interface_errors` | state | advisory | input/output/framing errors | **jen tranzitní rozhraní** |
| `interface_traffic` | both | advisory | `input_pps`/`output_pps` > 0; porovnání s tolerancí (default −60 %) | **jen tranzitní rozhraní** |

**Core** (uplink do core, P-linky, loopback)

| id | mode | severity | co dělá |
|---|---|---|---|
| `interface_state` | state | critical | jako výše |
| `interface_errors` | state | advisory | jen na tranzitních rozhraních; na `lo0.0` se přeskočí |
| `interface_traffic` | both | advisory | dtto |

Core scope **nedostává** ARP, ping ani BGP checky — ty jsou definované pro zákaznické služby.
Sada checků pro Core se bude rozšiřovat později (kandidát: ISIS adjacency, MPLS/LDP stav); zatím
je záměrně minimální a nebyla v původním zadání.

**Internet + IPVPN**

| id | mode | severity | co dělá |
|---|---|---|---|
| `arp_present` | state | advisory | na rozhraní existuje ≥1 ARP entry |
| `ping_reachability` | state | advisory | ping na **všechny** ARP adresy |
| `bgp_session_state` | both | critical | stav je `Established`; při porovnání PASS jen když je stav **stejný jako v baseline** |
| `bgp_prefix_counts` | compare | advisory | received / accepted / advertised s tolerancí |

**E-Line (vpws)**

| id | mode | severity | co dělá |
|---|---|---|---|
| `evpn_vpws_status` | both | critical | `evpn-vpws-sid-pe-status` je Up, local/remote SID sedí |

**E-LAN (vlan-aware / vlan-based)**

| id | mode | severity | co dělá |
|---|---|---|---|
| `evpn_esi_status` | both | critical | `evpn-esi-status` Up, DF role |
| `evpn_mac_count` | both | advisory | vlan-aware: počet MAC **per bridge domain**; vlan-based: per instance. Stav = >0, porovnání s tolerancí |

**Volitelné (default vypnuto)**

| id | mode | co dělá |
|---|---|---|
| `traffic_ceased` | compare | ověří, že na **starém** rozhraní provoz po migraci klesl k nule — chytá zapomenuté vypnutí / duplicitní forwarding |

`traffic_ceased` vyžaduje třetí capture (starý box po migraci). V CLI je to normální `capture` +
`evaluate` s tímto jedním checkem, žádný speciální režim.

`mode: both` znamená, že stejný check funguje v obou fázích: v kroku 3 ověří "teče provoz / jsou
MACy", v kroku 6 navíc porovná proti baseline. Nejsou potřeba dvě sady testů.

### Klasifikace rozhraní

Ne každé rozhraní nese zákaznický provoz a counter-based checky na interních rozhraních nemají
výpovědní hodnotu — jen by generovaly šum.

**Tranzitní rozhraní** (jediná, na kterých běží `interface_errors` a `interface_traffic`):

```
ge   xe   et   ae
```

**Interní rozhraní** — pro counter-based checky se vždy přeskočí (`SKIP`, ne WARN):

```
fxp   em    me    bme   cbp   pip   tap   jsrv  esi   vtep  pp0
lc-   demux lsi   mtun  pime  pimd  gre   ipip  dsc   pfe   pfh
vcp   sxe   vme   fti   lo0   re0   irb
```

Rozlišení `SKIP` vs `WARN` je tu podstatné: SKIP znamená *"tenhle test sem nepatří"*, WARN
*"něco je špatně"*. Kdyby interní rozhraní trvale svítila oranžově, operátor si zvykne výstup
přeskakovat a nástroj ztratí smysl.

Klasifikace je **vlastnost checku, ne scope**. Rozhraní `irb.14` je na seznamu interních, takže
nedostane counter checky — ale pořád je to plnohodnotná Internet služba a ARP, ping i BGP checky
na něm proběhnou normálně.

### Způsobilost pro service scope

Odlišná vrstva od klasifikace výše. Ta říká *"na tomhle nemá smysl měřit countery"*; tahle říká
*"tohle vůbec není migrovaná služba"*.

**Service scope vzniká jen pro migrované typy služeb:**

```
Internet   IPVPN   E-Line   E-LAN   Core
```

**Záznamy `Layer1` / `physical-port` se nestanou samostatnými scopy.** Použijí se jako selektor
rodičovského fyzického rozhraní u logických jednotek, které na nich sedí — `interface_state` na
fyzickém portu tedy proběhne, jen jako součást služby, která přes něj jede.

**Důvod:** `Layer1` je v reálných datech nejčastější typ (16 z 39 záznamů ve vzorku). Kdyby se
z každého stal scope, tvořily by nespárované fyzické porty většinu seznamu `unmatched` a ten by
přestal být čitelný — což by zabilo přesně tu vlastnost, kvůli které existuje.

**Management rozhraní jsou vyloučena bez ohledu na `service_type`:**

```
fxp   em   me   vme   bme   re0:mgmt-*   re1:mgmt-*
```

Nestanou se scopem, nepárují se, neobjeví se v `unmatched` a **nepingují se**. To je nutné proto,
že parser je stále kategorizuje jako `Internet` (`fxp0.0`, `re0:mgmt-0.0` ve vzorových datech) —
bez tohoto pravidla by nástroj pingoval do management sítě.

Vyloučení je zabudované v nástroji, ne v `mapping.yml`, aby ho nemusel vyplňovat každý operátor
znovu. `ignore:` v `mapping.yml` zůstává pro případy specifické pro danou migraci.

### Detaily ping checku

- **Ping běží pouze na scopech typu `Internet` a `IPVPN`.** Ostatní typy (`Core`, `E-Line`,
  `E-LAN`) ho nedostanou vůbec — `lo0.0` je díky nové kategorizaci `Core`, takže se přeskočí
  automaticky.
- `arp_present` běží vždy **před** ping checkem (pořadí je dané fázemi `capture`, ne DAGem checků).
- ARP check uloží **celý seznam** naučených adres na rozhraní; u ne-p2p subnetů jich může být víc.
- Pingují se **všechny** adresy ze seznamu.
- Fallback při prázdném ARP: první použitelná adresa ze subnetu (je-li PE `.1`, zkusí se `.2`).
- IPVPN → `ping <ip> routing-instance <RI>`; Internet → `ping <ip>` (default `inet.0`).
- Vždy se přidá `source <adresa rozhraní>`; **u IRB rozhraní se místo toho použije
  `virtual_gw_ip_address`** z inventory.
- Částečný úspěch (2 ze 3 adres) → `degraded` → WARN, s detailem per adresu.

Jsou to vědomě best-effort testy — proto default `advisory`.

### Datovost

Používá se `transit-traffic-statistics/input-pps` a `output-pps`. Junos je počítá sám, takže jde
o **rate, ne kumulativní counter** — není potřeba dvojité vzorkování ani čekací okno, stačí jeden
RPC průchod.

Absolutní byte countery jsou mezi dvěma boxy nesrovnatelné (kumulativní od různých uptime), proto
se neporovnávají vůbec.

Kritérium: rate > 0 **a** odchylka proti baseline v rámci tolerance (default −60 %, konfigurovatelné).

### BGP

Peer se ke službě přiřadí přes `bgp_neighbor` z inventory (parser už ho doplňuje na základě shody
se subnetem rozhraní). Peery, které se nepodaří přiřadit k žádné službě, se vypíší zvlášť jako
`unassigned_bgp_peers`, ať se tiše neztratí.

- **Stav peeringu:** stavový check → `Established`, jinak FAIL. Porovnávací check → PASS jen při
  stejném stavu jako v baseline.
- **Počty prefixů:** porovnávají se s tolerancí, ne 1:1. Zkušenost z JSNAPy je, že přesná shoda
  generuje množství FAILů kvůli rozdílu několika rout, což není signifikantní. Default tolerance
  je −10 %, konfigurovatelná; přesná hodnota se doladí podle provozu při implementaci checku.

---

## Párování služeb

Matcher běží nad seznamy scopů z obou snapshotů.

| priorita | pravidlo | confidence |
|---|---|---|
| 0 | `mapping.yml` — ruční override | `manual` |
| 1 | `description` + `service_type` + `service_subtype` | high |
| 2 | `description` + `service_type` | high |
| 3 | `routing_instance` + `service_type` | medium |
| 4 | překryv **síťové adresy** local subnetu + `service_type` | medium |
| 5 | `customer_vlan` + `service_type` | low |

Klíč je **složený**, ne samotná description: jedna description může nést víc záznamů
(`ge-0/0/5` fyzické i `ge-0/0/5.0` logické mají stejnou).

### Dvě tvrdá pravidla

**Nikdy se nehádá.** Vyjde-li na některé úrovni víc než jeden kandidát, scope se **nespáruje** a
jde do `unmatched` s `reason: "ambiguous: N kandidátů"` a jejich výpisem. Tichý špatný match je
horší než přiznané nespárování — u migrace by znamenal zelenou na rozbité službě.

**Nespárované se vždy hlásí.** `unmatched.baseline` = služba byla na starém a na novém není
(podezření na zapomenutou migraci). `unmatched.subject` = na novém je něco navíc. Obojí je
v `summary` a musí být vidět v GUI i v terminálu.

### Případy z reálných dat

| případ | řešení |
|---|---|
| prázdná description (`ge-0/0/4.0` ↔ `et-0/0/10.0`, obojí IPVPN) | pravidlo 3 — `routing_instance: L3VPN-CPE14-UNI` |
| duplicitní description na fyzickém i logickém rozhraní | složený klíč s `service_type` |
| restrukturalizovaná služba (`ge-0/0/5.0` Internet ↔ `ae0.14` E-LAN + `irb.14` Internet) | Internet↔Internet (`ge-0/0/5.0` ↔ `irb.14`) se spáruje pravidlem 2. `ae0.14` (E-LAN) zůstane v `unmatched.subject` jako nová služba — což je správně, na starém boxu opravdu neexistovala. Doplní se ručně přes `mapping.yml`, pokud ji operátor chce spárovat. |
| management rozhraní (`fxp0.0`, `re0:mgmt-0.0`, oboje `service_type: Internet`) | nestanou se scopem vůbec — vyloučeno způsobilostí, nikoli párováním |

### `mapping.yml`

```yaml
mappings:
  - baseline: {description: EVPN-VLAN-AWARE-INTERNET, service_type: Internet}
    subject:  {description: EVPN-VLAN-AWARE-INTERNET, service_type: E-LAN}
    note: "služba restrukturalizována na EVPN vlan-aware s IRB gateway"
  - baseline: {interface: "ge-0/0/4.0"}
    subject:  {interface: "et-0/0/10.0"}

ignore:
  - {description: "EVPN-VLAN-AWARE-L3VPN"}   # v této migraci se neřeší
  - {interface: "ge-0/0/7.0"}
```

**Jeden řádek = jedna služba.** Selektor `{interface: ...}` cílí na **logickou jednotku**
(`ge-0/0/2.113`), ne na fyzický port. Napsat `ge-0/0/2` tedy nezasáhne pět služeb, které přes něj
jedou — nezasáhne nic. Je to záměr: u `mappings` musí pravidlo vyjít na právě jeden scope na každé
straně, jinak vznikne nejednoznačnost a pár se nevytvoří, a mít u `ignore` opačnou sémantiku téhož
zápisu by bylo matoucí.

`ignore` slouží pro případy specifické pro danou migraci. Management rozhraní se sem psát nemusí —
jsou vyloučena už na úrovni způsobilosti pro scope (viz výše). Bez obojího by seznam `unmatched`
zaplavily položky, které nikoho nezajímají, operátor by si zvykl ho přeskakovat a nástroj by přišel
o svou hlavní pojistku.

---

## CLI

```bash
# krok 2 a 5 — sběr
mig-validate capture --device 172.20.20.4 \
                     --inventory 172.20.20.4.yml \
                     --phase pre-migration \
                     --output runs/mig01/pre/172.20.20.4.json

# krok 3 — validace starého boxu, bez baseline
mig-validate evaluate --snapshot runs/mig01/pre/172.20.20.4.json --format text

# krok 6 — validace nového + porovnání
mig-validate evaluate --snapshot  runs/mig01/post/172.20.20.5.json \
                      --baseline  runs/mig01/pre/172.20.20.4.json \
                      --mapping   mapping.yml \
                      --format json --output runs/mig01/post.result.json

# ladicí nástroje
mig-validate checks
mig-validate match --baseline pre.json --subject post.json
```

`match` je samostatné sloveso záměrně: **ladění `mapping.yml` nesmí vyžadovat běh celé validace.**
Operátor si vypíše, co se spárovalo a co ne, doplní mapování, opakuje — v řádu sekund a bez sahání
na síť.

Autentizace přebírá konvenci z existujících parserů (`--auth key|password`, `--username`,
`--key-file`, default `ansible` + `~/.ssh/id_rsa`).

Filtrování (krok 8 workflow) je vlastnost reportingu, ne běhu:

```bash
mig-validate evaluate --snapshot post.json --baseline pre.json \
                      --filter CPE13 --status fail,warn
```

### Textový výstup

```
Migrace: 172.20.20.4 (pre-migration) → 172.20.20.5 (post-migration)

  ✓ 40 PASS   ⚠ 6 WARN   ✗ 2 FAIL   – 3 SKIP
  Spárováno 18 služeb, 1 nespárovaná v baseline, 2 nespárované v subject

SLUŽBA                        TYP        STAV   DETAIL
INTERNET-CPE13-NNI            Internet   ✓
L3VPN-CPE13-NNI               IPVPN      ⚠      provoz -72 % (410 → 115 pps)
EVPN-VPWS-CPE13-NNI           E-Line     ✗      vpws-sid-pe-status: Down
EVPN-VLAN-AWARE-CPE13-NNI     E-LAN      ⚠      MAC v BD-313: 42 → 11

NESPÁROVÁNO
  baseline  L3VPN-CPE99-NNI          (IPVPN)   žádný kandidát na subject
  subject   EVPN-VLAN-AWARE-INTERNET (E-LAN)   nová služba, chybí baseline
```

Nespárované jsou v tabulce **vždy**, i když je všechno ostatní zelené.

### Exit kódy

| kód | význam |
|---|---|
| `0` | žádný FAIL |
| `1` | aspoň jeden FAIL |
| `2` | chyba nástroje (nepřipojil se, rozbitý snapshot) |

WARN exit kód nemění — jinak by CI padalo pořád a přestalo by se tomu věřit. Přepínatelné přes
`--warn-as-error`.

---

## Error handling

Nadřazené pravidlo:

> **Chybějící data nikdy nedají PASS.**

Nepodařilo-li se něco změřit, výsledek je `SKIP` s důvodem — nikdy PASS, nikdy tiché vynechání
řádku. "Nevím" je legitimní odpověď; "vypadá to dobře, protože jsem se nezeptal" není.

| situace | chování |
|---|---|
| nepřipojím se (auth / timeout / refused) | capture skončí, **snapshot nevznikne**, exit 2, chyba rozliší *proč* (špatný klíč ≠ nedostupný box) |
| jeden collector selže (`RpcError`) | ostatní pokračují; chyba se zapíše do `capture.collectors`; checky s `requires` na tu oblast → **SKIP s odkazem na tu chybu** |
| neznámá platforma | capture odmítne s jasnou hláškou |
| inventory nesedí na zařízení (rozhraní není ve facts) | varování při stavbě scopů + `SKIP` u dotčených checků |
| snapshot má jinou `schema_version` | načtení selže hlasitě, s výpisem obou verzí |
| ping neprojde | **není to chyba** — je to normální naměřený výsledek |

*Nástroj selhal* a *test selhal* jsou dvě různé věci a nesmí splynout — proto exit kód 2 vs 1.

---

## Testování

Přímý zisk z toho, že checky jsou čisté funkce: na testy není potřeba ani jednou sáhnout na router.

| vrstva | jak se testuje | čím |
|---|---|---|
| `collectors` | syrové RPC XML → očekávaný dict | `tests/fixtures/rpc/{junos,evo}/*.xml` |
| `scoping` | inventory YAML → očekávané scopy | `172.20.20.4.yml` / `172.20.20.5.yml` |
| `matcher` | tabulkové testy | IRB případ, prázdná description, nejednoznačnost |
| `checks` | facts + scope → očekávaný `CheckResult` | ručně psané minimální fixtures |
| end-to-end | dva snapshoty → `result.json` | golden file |
| `connection` | jediná vrstva, co chce reálný box | tenká záměrně |

Dvě věci, které to udrží živé v čase:

**`capture --record-raw <dir>`** uloží při sběru syrové RPC XML. Neznámý output z produkce se
zkopíruje do fixtures a regresní test je hotový za minutu — bez laborky, bez ručního psaní XML.
Je to jediný udržitelný způsob, jak collectory neshnijí.

**Fixtures z obou platforem povinně.** Každý collector musí mít fixture pro Junos i EVO. Tím se
vynutí, že platformní rozdíly se řeší v collectoru a neprosáknou do checků — což je předpoklad,
na kterém stojí celé cross-device porovnání (AR-7).

Test suite běží pod `pytest`, bez markerů "potřebuje síť" u čehokoliv kromě `connection`.

---

## Kontrakt fact-schématu (závazné pro collectory Plánu 2)

Checky čtou `facts` přes `Scope.select()`. Klíče, podle kterých se filtruje, jsou proto **závazné
rozhraní**: pokud collector v Plánu 2 vyprodukuje data pod jiným klíčem, check tiše vrátí `SKIP` —
a protože SKIP není FAIL, offline testy to nemusí odhalit (fixtures v `conftest.py` si data staví
ze stejných selektorů, takže jsou konzistentní z konstrukce). Proto je kontrakt sepsán zde
explicitně a Plán 2 má u collectorů conformance test.

| oblast | tvar | filtruje se podle |
|---|---|---|
| `interfaces` | `{ifname: {admin_status, oper_status, input_pps, output_pps, input_errors, output_errors, framing_errors}}` | název rozhraní (logická jednotka i fyzický rodič) |
| `arp` | `[{ip, mac, interface, routing_instance}]` | `interface` |
| `bgp` | `{peer_ip: {state, peer_as, routing_instance, prefixes: {received, accepted, advertised}}}` | `peer_ip` ∈ `bgp_neighbors` |
| `evpn_vpws` | `{routing_instance: {status, local_sid, remote_sid}}` | **klíč = `routing_instance`** |
| `evpn_esi` | `{esi: {status, df_role, interface}}` | `interface` (viz níže) |
| `evpn_mac` | `{routing_instance: {bridge_domain: count}}` | **klíč = `routing_instance`**; `bridge_domain` je `"-"` u vlan-based |

Dvě místa, kde je keying křehký a Plán 2 na ně musí dát pozor:

- **`evpn_esi.interface` musí být název, který scope spolehlivě matchne.** Junos hlásí ESI status
  s fyzickým/AE názvem (`ae0`, `et-0/0/8`), zatímco scope drží logickou jednotku (`ae0.14`).
  Match projde jen tehdy, když je fyzický rodič v `physical_interfaces` — a ten se plní pouze
  pokud v inventory existuje `Layer1` záznam pro daný port. Doporučení pro Plán 2: ESI collector
  ať emituje `interface`, který scope má (logickou jednotku), **nebo** ať se do schématu doplní
  `routing_instance` a ESI se matchuje i podle něj (robustnější). Regresní fixture s ESI na portu
  bez `Layer1` rodiče to má pojistit.
- **`evpn_vpws` a `evpn_mac` se klíčují názvem routing-instance, ne rozhraním.** To je záměr
  (název instance je při migraci stabilní, název portu ne — viz AR-6b), ale collector to musí
  dodržet přesně.

**`unassigned.bgp_peers`** hlásí zatím jen peery na *subjektu* (nové zařízení), nepřiřazené k žádné
službě — to je pro migrační workflow to podstatné. Baseline-side nepřiřazené peery se záměrně
nehlásí; kdyby se ukázalo, že jsou potřeba, přidá se symetricky.

## Předpoklady a známé limity

- **Inventory je vstup, ne výstup validatoru.** Kvalita párování je omezená kvalitou description
  na zařízeních. `mapping.yml` je únikový ventil, ne náhrada disciplíny v popiscích.
- **Porovnání datovosti je best-effort.** Mezi pre- a post-snapshotem uběhne reálný čas
  (přepojení kabelu) a provoz se může legitimně lišit. Proto `advisory` a velkorysá tolerance.
- **Ping je best-effort.** CPE může být vypnuté nebo blokovat ICMP. Proto `advisory`.
- **Tolerance jsou úvodní odhady** (−60 % provoz, −10 % prefixy) a doladí se podle toho, jak se
  osvědčí v produkci. Jsou konfigurovatelné právě proto.
- **Sběr je snímek jednoho okamžiku.** Nástroj nedělá kontinuální monitoring.

## Následující kroky

1. Implementační plán (skill `writing-plans`).
2. Případný refaktoring parserů do sdíleného balíku — samostatné rozhodnutí, viz AR-8.
