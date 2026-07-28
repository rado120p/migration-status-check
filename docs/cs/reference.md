# Reference

Tabulky a schémata k dohledání. Provozní návod je v [README.md](README.md), souvislosti
v [architecture.md](architecture.md).

---

## 1. Katalog checků

Výpis odpovídá `mig-validate checks` (stav ke commitu `d0d024a`):

| id | mode | severity | typy služeb | co ověřuje |
|---|---|---|---|---|
| `interface_state` | state | critical | všechny | `admin_status` i `oper_status` je `up` |
| `interface_errors` | state | advisory | všechny | nulové `input/output/framing` chyby — **jen tranzitní rozhraní** |
| `interface_traffic` | both | advisory | všechny | `input_pps`/`output_pps` > 0; s baseline navíc pokles proti toleranci — **jen tranzitní rozhraní** |
| `traffic_ceased` | compare | advisory | všechny | na starém rozhraní provoz po migraci utichl — **výchozí stav: vypnuto** |
| `arp_present` | state | advisory | Internet, IPVPN | na rozhraní služby existuje ≥ 1 ARP záznam |
| `ping_reachability` | state | advisory | Internet, IPVPN | odpovědi z cílů zjištěných při `capture` |
| `bgp_session_state` | both | critical | Internet, IPVPN | stav je `Established`; s baseline navíc hlásí změnu stavu |
| `bgp_prefix_counts` | compare | advisory | Internet, IPVPN | received / accepted / advertised proti toleranci |
| `evpn_vpws_status` | both | critical | E-Line | stav rozhraní instance je `Up` a přišel remote SID |
| `evpn_esi_status` | both | critical | E-LAN | stav lokálního rozhraní v ESI je `Up`, hlásí DF |
| `evpn_mac_count` | both | advisory | E-LAN | počet naučených MAC > 0; s baseline navíc pokles proti toleranci |

Význam `mode`:

- `state` — běží vždy,
- `compare` — bez baseline vrací `SKIP` s důvodem `porovnavaci check bez baseline snapshotu`,
- `both` — bez baseline dělá stavovou kontrolu, s baseline navíc porovnání.

Detaily chování jednotlivých checků: [files/checks.md](files/checks.md).

### Poznámky, kde katalog překvapí

- **`traffic_ceased` má v `service_types` „vsechny", ne omezení na Core.** Spec ho popisuje
  jako volitelný check; v kódu opravdu není omezený typem služby, jen vypnutý defaultem.
- **`bgp_session_state` s baseline nevrací FAIL při změně stavu na Established.** Změna
  `Idle -> Established` je `degraded`, tedy WARN — je to zlepšení, ne rozbití, ale stojí za
  zmínku, že se stav změnil.
- **`evpn_vpws_status` nevyžaduje shodu local a remote SID.** Každá strana inzeruje svoje
  service ID; rovnost není invariant. FAIL nastane, když remote SID vůbec nepřijde.

### Klasifikace rozhraní

Counter-based checky (`interface_errors`, `interface_traffic`, `traffic_ceased`) běží **jen
na tranzitních rozhraních**; jinde vrátí `SKIP`, ne WARN.

```
tranzitní (allowlist):  ge  xe  et  ae
```

```
interní (counter checky se přeskočí):
fxp  em    me    bme   cbp   pip   tap   jsrv  esi   vtep  pp0
lc-  demux lsi   mtun  pime  pimd  gre   ipip  dsc   pfe   pfh
vcp  sxe   vme   fti   lo0   re0   irb
```

Klasifikace je vlastnost **checku, ne scope**: `irb.14` counter checky nedostane, ale ARP,
ping i BGP checky na něm proběhnou normálně.

### Způsobilost pro service scope

Jiná vrstva než klasifikace výše. Scope vznikne jen pro **migrované typy služeb**:

```
Internet   IPVPN   E-Line   E-LAN   Core
```

- Záznamy `Layer1` / `physical-port` **se samostatným scopem nestanou.** Použijí se jen jako
  potvrzení, že fyzický rodič existuje, a doplní se do `physical_interfaces` logické jednotky.
  (Bez toho pravidla by nespárované fyzické porty tvořily většinu `unmatched` a seznam by
  přestal být čitelný — `Layer1` je v reálných datech nejčastější typ.)
- **Management rozhraní jsou vyloučena bez ohledu na `service_type`:**
  `fxp`, `em`, `me`, `vme`, `bme`, `re0:mgmt-*`, `re1:mgmt-*`. Nestanou se scopem, nepárují
  se, neobjeví se v `unmatched` a **nepingují se**. To je nutné proto, že parser je pořád
  kategorizuje jako `Internet` — bez tohoto pravidla by nástroj pingoval do management sítě.

---

## 2. `config.yml`

Předává se přes `--config`. Nepovinné; bez něj platí defaulty z
`migration_validator/config.py`.

```yaml
checks:
  interface_traffic:
    tolerance_percent: -60
    require_nonzero: true
  bgp_prefix_counts:
    tolerance_percent: -10
  evpn_mac_count:
    tolerance_percent: -60
  ping_reachability:
    count: 5
  traffic_ceased:
    enabled: false
    max_residual_pps: 1
```

Výchozí hodnoty (přesně to, co je v kódu):

| check | volba | default |
|---|---|---|
| `interface_traffic` | `tolerance_percent` | `-60` |
| `interface_traffic` | `require_nonzero` | `true` |
| `bgp_prefix_counts` | `tolerance_percent` | `-10` |
| `evpn_mac_count` | `tolerance_percent` | `-60` |
| `ping_reachability` | `count` | `5` |
| `traffic_ceased` | `enabled` | `false` |
| `traffic_ceased` | `max_residual_pps` | `1` |

Univerzální volby platné pro **kterýkoliv** check:

| volba | typ | význam |
|---|---|---|
| `enabled` | bool | `false` = check se vůbec nespustí a v reportu není (výchozí `true` u všech kromě `traffic_ceased`) |
| `severity` | `critical` \| `advisory` | přepíše výchozí severity z kódu |

Poznámky:

- `ping_reachability.count` je konfigurace **checku**, ale počet paketů se rozhoduje už při
  sběru — na CLI je to `capture --ping-count`.
- Přepnutí severity **nezmění** pravidlo „částečný úspěch = WARN". `degraded` je WARN vždy.

---

## 3. `mapping.yml`

Předává se přes `--mapping` u `evaluate` i `match`.

```yaml
mappings:
  - baseline: {description: EVPN-VLAN-AWARE-INTERNET, service_type: Internet}
    subject:  {description: EVPN-VLAN-AWARE-INTERNET, service_type: E-LAN}
    note: "sluzba restrukturalizovana na EVPN vlan-aware s IRB gateway"
  - baseline: {interface: "ge-0/0/4.0"}
    subject:  {interface: "et-0/0/10.0"}

ignore:
  - {description: "EVPN-VLAN-AWARE-L3VPN"}
  - {interface: "ge-0/0/7.0"}
```

Selektor smí kombinovat `description`, `service_type` a `interface`; aspoň jedno musí být
vyplněné, jinak načtení selže hláškou `prazdny selektor v mapping.yml`.

Sémantika:

- **`mappings`** — jeden řádek = jedna služba. Pravidlo musí vyjít na **právě jeden** scope
  na každé straně; když jich vyjde víc, pár nevznikne a všechny kandidáty jdou do
  `unmatched` s důvodem `ambiguous`. Ruční mapování má prioritu před automatickými pravidly
  a výsledný pár má `confidence: manual`.
- **`ignore`** — scope se odstraní z obou stran ještě před párováním. Pro případy specifické
  pro danou migraci; management rozhraní se sem psát nemusí.
- Selektor `{interface: ...}` cílí na **logickou jednotku** (`ge-0/0/2.113`). Napsat
  `ge-0/0/2` tedy nezasáhne pět služeb, které přes port jedou — nezasáhne nic.

### Pravidla automatického párování

Aplikují se v tomhle pořadí; první, které dá jednoznačný pár, vyhrává:

| priorita | pravidlo (`method` ve výstupu) | confidence |
|---|---|---|
| 0 | `mapping.yml` → `manual` | `manual` |
| 1 | `description+service_type+service_subtype` | high |
| 2 | `description+service_type` | high |
| 3 | `routing_instance+service_type` | medium |
| 4 | `subnet+service_type` (síťová adresa local subnetu) | medium |
| 5 | `vlan+service_type` | low |

Klíč je vždy **složený**, ne samotná description: jedna description může nést víc záznamů
(`ge-0/0/5` fyzické i `ge-0/0/5.0` logické mají stejnou).

Důvody v `unmatched`:

| text | význam |
|---|---|
| `zadny kandidat na subject` | baseline služba nemá protějšek — podezření na zapomenutou migraci |
| `nova sluzba, chybi baseline` | subject služba je navíc — nová nebo restrukturalizovaná |
| `ambiguous: N kandidatu (id, id, ...)` | pravidlo dalo víc kandidátů, nástroj nehádá |

---

## 4. Formát snapshotu

`schema_version: 1`. Snapshot je **self-contained** — `evaluate` k němu nepotřebuje ani
inventory, ani síť. Jiná verze schématu vede k tvrdé chybě, ne k pokusu o migraci dat.

```jsonc
{
  "schema_version": 1,
  "device": {
    "address": "172.20.20.4", "hostname": "MX1-POP1",
    "platform": "junos",              // junos | junos-evo
    "model": "mx204", "version": "21.4R3-S4", "uptime_seconds": null
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
      "ge-0/0/2.113": {"admin_status": "up", "oper_status": "up",
                       "input_pps": 412, "output_pps": 388,
                       "input_errors": 0, "output_errors": 0, "framing_errors": 0}
    },
    "arp": [{"ip": "198.11.13.2", "mac": "00:11:...", "interface": "ge-0/0/2.113",
             "routing_instance": null}],
    "bgp": {
      "198.11.13.2": {"state": "Established", "peer_as": 65013,
                      "routing_instance": "L3VPN-CPE13-NNI",
                      "prefixes": {"received": 14, "accepted": 14, "advertised": 3}}
    },
    "evpn_vpws": {"EVPN-VPWS-CPE13-NNI": {"local_sid": 213, "remote_sid": 213, "status": "Up"}},
    "evpn_esi":  {"00:11:22:...": {"status": "Up/Forwarding", "df_role": "10.0.0.5",
                                   "interface": "ae0.14"}},
    "evpn_mac":  {"EVPN-VLAN-AWARE-CPE13-NNI": {"313": 42}}
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

Vlastnosti:

- `facts` jsou **syrová, device-scoped data** klíčovaná přirozeným klíčem. Žádné `service_id`
  uvnitř. Kdyby se párování ukázalo jako špatné, opraví se a staré snapshoty se přehodnotí
  znovu, bez sahání na zařízení.
- `capture.collectors` nese stav každého sběru zvlášť — selhání jednoho RPC nezruší capture.
- `device.uptime_seconds` je v modelu, ale `device_meta()` ho zatím vždy plní `None`.
- U pingu může přibýt klíč `error` s důvodem, když ICMP vůbec neodešlo (např. `bind: Can't
  assign requested address`) — bez něj by to vypadalo jako úspěšně změřených nula paketů.

### Scope

```jsonc
{
  "id": "svc:L3VPN-CPE13-NNI:IPVPN",
  "kind": "service",                      // service | device
  "key": {"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN", "service_subtype": null},
  "selectors": {
    "interfaces":          ["ge-0/0/2.113"],
    "physical_interfaces": ["ge-0/0/2"],
    "routing_instances":   ["L3VPN-CPE13-NNI"],
    "bgp_neighbors":       ["198.11.13.2", "2001:db8:11:13::b"],
    "local_addresses":     ["198.11.13.1/30"],
    "virtual_gw":          [],
    "vlans":               ["113"],
    "bridge_domains":      []
  }
}
```

Scope je **čistě filtr**, neobsahuje naměřená data. Device scope má `kind: "device"`
a prázdné selektory = „ber všechno".

`id` je `svc:<description nebo název rozhraní>:<service_type>`. Když by dvě služby vyšly na
stejný klíč, přidá se za něj ještě název rozhraní (`svc:et-0/0/10.0:IPVPN`).

---

## 5. Formát výsledku

`schema_version: 1`.

```jsonc
{
  "schema_version": 1,
  "evaluated_at": "2026-07-24T11:40:02Z",
  "subject":  {"address": "172.20.20.5", "phase": "post-migration", "captured_at": "..."},
  "baseline": {"address": "172.20.20.4", "phase": "pre-migration",  "captured_at": "..."},

  "summary": {
    "pass": 68, "warn": 15, "fail": 1, "skip": 7,
    "scopes_matched": 8, "unmatched_baseline": 2, "unmatched_subject": 3
  },

  "scopes": [
    {
      "scope_id": "svc:L3VPN-CPE13-NNI:IPVPN",
      "key": {"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN", "service_subtype": null},
      "status": "WARN",
      "match": {
        "status": "matched", "method": "description+service_type", "confidence": "high",
        "baseline_interfaces": ["ge-0/0/2.113"], "subject_interfaces": ["et-0/0/8.113"]
      },
      "checks": [
        {
          "id": "interface_traffic", "mode": "both",
          "status": "WARN", "severity": "advisory",
          "message": "et-0/0/8.113: output_pps kleslo o 72 % (410 -> 115), prah je -60 %",
          "label": "et-0/0/8.113",
          "baseline": {"input_pps": 412, "output_pps": 410},
          "subject":  {"input_pps": 398, "output_pps": 115},
          "details":  {"tolerance_percent": -60.0, "output_pps_change_percent": -72.0}
        }
      ]
    }
  ],

  "unmatched": {
    "baseline": [{"scope_id": "...", "description": "...", "service_type": "Core",
                  "reason": "zadny kandidat na subject"}],
    "subject":  [{"scope_id": "...", "description": "...", "service_type": "E-LAN",
                  "reason": "nova sluzba, chybi baseline"}]
  },

  "unassigned": {
    "bgp_peers": [{"peer": "10.1.0.5", "routing_instance": null, "snapshot": "subject"}]
  }
}
```

Vlastnosti:

- **Každý check nese `baseline` i `subject` bloky se surovými čísly**, ne jen verdikt.
- `status` scope = nejhorší stav jeho checků (`SKIP` jen když není co lepšího hlásit);
  `summary` = agregát přes všechny checky. Terminál ani GUI nic nepočítají.
- `match` je `null` u běhu bez baseline; u nespárovaného subject scope má
  `status: "unmatched"` a `reason`.
- **Nespárované baseline scopy nemají vlastní záznam v `scopes`** — nejsou v subjektu, není
  co měřit. Jsou jen v `unmatched.baseline`.
- `unassigned.bgp_peers` hlásí zatím jen peery na **subjektu** (nové zařízení). V device
  režimu je seznam vždy prázdný.
- `message` je česky (bez diakritiky), konzistentně se zbytkem nástroje.

---

## 6. Návratové kódy

| kód | konstanta v `cli.py` | význam |
|---|---|---|
| `0` | `EXIT_OK` | žádný FAIL |
| `1` | `EXIT_FAILED_CHECKS` | aspoň jeden FAIL, nebo aspoň jeden WARN při `--warn-as-error` |
| `2` | `EXIT_TOOL_ERROR` | chyba nástroje: nepřipojil se, snapshot chybí / je rozbitý / má jinou `schema_version`, neznámý collector, nevalidní YAML |

WARN sám o sobě návratový kód nemění — jinak by CI padalo pořád a přestalo by se tomu věřit.

*Nástroj selhal* a *test selhal* jsou dvě různé věci a nesmí splynout — proto 2 vs. 1.

---

## 7. Přehled RPC

| collector | Junos | Junos EVO | argumenty |
|---|---|---|---|
| `interfaces` | `get_interface_information` | totéž | `extensive=True` |
| `arp` | `get_arp_table_information` | totéž | `no_resolve=True` |
| `bgp` | `get_bgp_neighbor_information` | totéž | — |
| `evpn_vpws` | `get_evpn_vpws_information` | totéž | — |
| `evpn_esi` | `get_evpn_instance_information` | totéž | `extensive=True` |
| `evpn_mac` | `get_bridge_mac_table` + `get_evpn_mac_table` | `get_mac_vrf_mac_table` | — |
| ping (probe) | `ping` | totéž | `host`, `count`, volitelně `source`, `routing_instance` |

`extensive` u `evpn_esi` není kosmetika: bez něj `show evpn instance` vrátí souhrn bez
jediného ESI a collector by tiše vracel prázdno.
