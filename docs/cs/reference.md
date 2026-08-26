# Reference

Tabulky a schémata k dohledání. Provozní návod je v [README.md](README.md), souvislosti
v [architecture.md](architecture.md).

---

## 1. Katalog checků

Výpis odpovídá `mig-validate checks` (stav k 2026-08-26, vlna Core transit/loopback):

| id | mode | severity | typy služeb | co ověřuje |
|---|---|---|---|---|
| `interface_state` | state | critical | všechny | `admin_status` i `oper_status` je `up` — jeden nález na každé z obou zvlášť |
| `interface_errors` | state | advisory | všechny | nulové `input/output/framing` chyby — **jen tranzitní rozhraní** |
| `interface_traffic` | both | advisory | všechny | `input_pps`/`output_pps` > 0; s baseline navíc pokles proti toleranci — **jen tranzitní rozhraní**, jeden nález na směr |
| `traffic_ceased` | compare | advisory | všechny | na starém rozhraní provoz po migraci utichl — **výchozí stav: vypnuto** |
| `arp_present` | state | advisory | Internet, IPVPN | na rozhraní služby existuje ≥ 1 IPv4 ARP záznam; `SKIP`, když služba nemá IPv4 adresu |
| `nd_present` | state | advisory | Internet, IPVPN | na rozhraní služby existuje ≥ 1 použitelný IPv6 ND záznam; `SKIP`, když služba nemá IPv6 adresu |
| `ping_reachability` | state | advisory | Internet, IPVPN | odpovědi z cílů (IPv4 i IPv6) zjištěných při `capture` |
| `bgp_session_state` | both | critical | Internet, IPVPN | stav je `Established`; s baseline navíc hlásí změnu stavu |
| `bgp_prefix_counts` | compare | advisory | Internet, IPVPN | received / accepted / advertised / active proti toleranci — **za každou RIB zvlášť** |
| `evpn_vpws_status` | both | critical | E-Line | stav rozhraní instance je `Up` a přišel remote SID |
| `evpn_esi_status` | both | critical | E-LAN | stav lokálního rozhraní v ESI je `Up`, hlásí DF |
| `evpn_instance_status` | both | critical | E-LAN | local interfaces > 0 a všechna up; IRB up (pokud IRB existují); EVPN neighbors > 0; ESI „resolved"; s baseline: pokles EVPN neighbors = WARN, počty local/IRB interfaců se na rovnost neporovnávají (rozdíl ukazuje sloupec ZMENA — konsolidace do jedné mac-vrf instance je při migraci mění) |
| `evpn_mac_count` | both | advisory | E-LAN | počty MAC z `count` výpisu per VLAN a per interface; > 0 a s baseline pokles proti toleranci |
| `static_route_status` | both | critical | všechny | nakonfigurovaná statická routa je v routovací tabulce a next-hop se nezměnil |
| `bfd_session_state` | both | critical | všechny | BFD session nakonfigurovaného peeru je `Up`; `SKIP`, dokud není BGP `Established`; na Core transitu neběží vůbec (viz `bfd_transit_state`) |
| `deactivation_state` | both | critical | všechny | deaktivace služby (`RI`/`interface`) se proti baseline nezhoršila; zdravá služba (obě strany aktivní) nález nedostane vůbec |
| `isis_adjacency_state` | both | critical | Core (transit) | IS-IS adjacency je `Up`, soused a adresy sedí proti baseline; chybějící rozhraní v outputu = FAIL |
| `isis_interface_info` | state | critical | Core (transit, loopback) | level 2 nakonfigurován, level 1 ne; Passive flag role-aware (loopback ho vyžaduje, transit ne) |
| `isis_overview` | state | advisory | Core (loopback) | overload bit routeru není nastaven |
| `ldp_neighbor_state` | both | critical | Core (transit) | LDP soused je vždy očekávaný; `uptime_seconds > 0`, adresa proti baseline |
| `pim_neighbor_state` | both | critical | Core (transit) | jen tam, kde je rozhraní pod `protocols pim` (jinak žádný nález, ne SKIP); jinak stejně jako LDP |
| `mpls_interface_state` | both | critical | Core (transit) | MPLS na rozhraní je `Up`; chybějící rozhraní v outputu = FAIL |
| `bfd_transit_state` | both | critical | Core (transit) | BFD session vázaná na rozhraní (ne na peer adresu) je vždy očekávaná a `Up` |

Význam `mode`:

- `state` — běží vždy,
- `compare` — bez baseline vrací `SKIP` s důvodem `porovnavaci check bez baseline snapshotu`,
- `both` — bez baseline dělá stavovou kontrolu, s baseline navíc porovnání.

Detaily chování jednotlivých checků: [files/checks.md](files/checks.md).

### Poznámky, kde katalog překvapí

- **`traffic_ceased` má v `service_types` „vsechny", ne omezení na Core.** Spec ho popisuje
  jako volitelný check; v kódu opravdu není omezený typem služby, jen vypnutý defaultem.
- **`bgp_session_state` s baseline nevrací FAIL ani WARN při změně stavu na Established.**
  Změna `Idle -> Established` je PASS — je to zlepšení, ne rozbití, a oranžový řádek na zdravé
  službě je falešný poplach (rozhodnutí R-2). Že se stav změnil, řekne zpráva a sloupec
  `ZMENA`.
- **`evpn_vpws_status` nevyžaduje shodu local a remote SID.** Každá strana inzeruje svoje
  service ID; rovnost není invariant. FAIL nastane, když remote SID vůbec nepřijde.
- **`arp_present`/`nd_present` nevrátí vůbec nic, když služba nemá adresu dané rodiny.**
  Bez toho by třeba čistě IPv6 služba dostala WARN za chybějící ARP záznam, který nikdy
  nemohl vzniknout. Dřív se vracel `SKIP` označkovaný rodinou — jenže právě ta značka si
  v bloku vynutila sekci rodiny, kterou má renderer vynechat, takže žádný `Finding` je
  jediné, co obě pravidla splní naráz (rozhodnutí R-1). Cena: takový check je v reportu
  k nerozeznání od checku, který prošel. Sousedé se navíc nikdy nesčítají do jedné věty,
  každý záznam je vlastní `Finding` (`MAC -> IP`), takže report vypíše řádek na každého souseda.
- **`bgp_prefix_counts` počty nesčítá napříč RIB.** Peer s víc RIB (`inet.0`, `bgp.l3vpn.0`,
  ...) dostane samostatnou sadu řádků na každou — pokles jen v jedné RIB se jinak ztratí
  v součtu s ostatními.
- **`static_route_status` a `bfd_session_state` mají v `service_types` „vsechny", ale řádek
  dostane jen služba, která ten záměr v konfiguraci má.** Služba bez BFD nemá v reportu
  o BFD ani zmínku (rozhodnutí R‑1); omezovat je typem služby by bylo zbytečné, protože L2
  rozhraní se na statiku ani na peera stejně namatchovat nemůže.
- **`static_route_status` je jediný check, který porovnává konfigurační *záměr* proti
  naměřené realitě.** Ostatní se ptají „je to nahoře?", tenhle „je tam to, co jsi si
  objednal?". Právě proto odhalí routu, která je v konfiguraci, ale do tabulky se nikdy
  nedostala (`neni v tabulce`) — třeba když se její next-hop stal nedosažitelným po
  deaktivaci rozhraní.
- **`bfd_session_state` čeká na BGP.** Dokud peer není `Established`, vrací `SKIP`
  s hodnotou `BGP neni Established` místo FAILu. BFD nemůže naběhnout bez BGP a dva červené
  řádky za jednu příčinu jsou důvod, proč operátoři výpisy přeskakují.
- **`interface_errors`/`interface_traffic` na L3 části vázané služby (IRB spárovaný s L2
  přes `scopes[].link`, scope sám nemá tranzitní rozhraní) neměří IRB.** Počítadla nese
  fyzicky L2 tranzit. `interface_errors` vrátí jeden `INFO` řádek s popiskem
  „Interface errors / traffic" a hodnotou `mereno na L2 (<L2 rozhraní>) - viz blok nize`
  místo obvyklého SKIP/OK; `interface_traffic` pro tu samou službu nevrátí vůbec žádný
  nález (aby se INFO odkaz nezdvojil). Podrobnosti a ukázka reportu: „Vazba L2+L3"
  v kapitole 5.

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

**`Core` má od vlny 2026-08-26 dva `service_subtype`:**

- `transit` — tranzitní Core rozhraní (`ge`/`xe`/`et`/`ae` s family iso/mpls). Vedle
  stávajících interface checků dostává i nové protokolové checky
  (`isis_adjacency_state`, `isis_interface_info`, `ldp_neighbor_state`,
  `pim_neighbor_state`, `mpls_interface_state`, `bfd_transit_state`).
- `loopback` — `lo0.*`. Kromě stávajícího interface stavu/admin a
  `aggregate_route_status` (vlna 2026-08-19) dostává `isis_interface_info` (variantu
  s povinným Passive) a nový `isis_overview` (overload bit). Interní BGP peeři (viz níž)
  se od téhle vlny mapují právě sem, ne do NEZAŘAZENO.

Subtype odvozuje parser (`inventory schema 7`) a nese ho `ScopeKey.service_subtype`;
`Check.service_subtypes` je AND k `service_types` — podrobnosti v
[files/checks.md](files/checks.md#basepy--kostra).

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
| 4 | `subnet+service_type` (síťová adresa local subnetu; u p2p prefixů — /30, /31, /127 a delších — celá hostitelská adresa, aby se nespárovaly protilehlé konce téhož linku) | medium |
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

`schema_version: 11`. Snapshot je **self-contained** — `evaluate` k němu nepotřebuje ani
inventory, ani síť. Jiná verze schématu vede k tvrdé chybě (`SnapshotVersionError`), ne
k pokusu o migraci dat.

Historie verzí:

| verze | co se změnilo |
|---|---|
| 1 → 2 | adresy rozdělené na rodiny, přibyla oblast `nd` |
| 2 → 3 | přibyly oblasti `routes` a `bfd` a klíče `unassigned.static_routes` / `.bfd_sessions` |
| 3 → 4 | `Scope` nese příznaky deaktivace (`routing_instance_active`, `interface_active`) — AR-21 |
| 4 → 5 | snapshot i inventory schema srovnány na 5 — commit `d9e77bc` |
| 5 → 6 | ARP/ND přes IRB nesou `learned_via`, záznam už neutíká scope filtru — commit `6df6e1a` |
| 6 → 7 | `evpn_mac` collector čte `count` RPC (per-VLAN a per-interface počty, tvar `{vlans, interfaces}`); přibyla oblast `evpn_instance` — commit `e547a24` |
| 10 → 11 | šest nových fact areas (`isis_adjacency`, `isis_interface`, `isis_overview`, `ldp_neighbor`, `pim_neighbor`, `mpls_interface`) pro Core transit/loopback checky — vlna 2026-08-26 |

Mezi 7 a 10 proběhly další bumpy beze zápisu do téhle tabulky — mezera je vědomě
přiznaná, ne dopočítaná (viz [`files/models.md`](files/models.md) pro aktuální hodnotu
konstanty).

> **Starší snímky nejdou přehrát.** Zvýšení na 3 znamená, že `runs/ipv6/`
> a `runs/ipv6-live-2026-07-29/` — pořízené se `schema_version: 2` — už `evaluate` odmítne.
> Není to chyba: snímek verze 2 oblasti `routes` ani `bfd` neobsahuje, takže by oba nové
> checky neměly co číst a služba s nakonfigurovanou, ale neinstalovanou routou by prošla
> jako zdravá. Kdo takový snímek potřebuje vyhodnotit, musí **pořídit nový `capture`**;
> dopočítat chybějící oblasti ze starého souboru nejde.
>
> **`runs/mig01` je od 2026-08-26 přesnímaný na `schema_version: 11`** (inventory na 7).
> Předchozí bump 6 → 7 svého času vyžadoval totéž přesnímání (`pre`/`post` na verzi 6
> nástroj verze 7 odmítal) — historie se opakuje při každém zvýšení, ne jen u tohohle.

```jsonc
{
  "schema_version": 11,
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
      "nd":         {"status": "ok"},
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
    "nd": [{"ip": "2001:db8:11:13::b", "mac": "00:11:...", "interface": "ge-0/0/2.113",
            "state": "reachable"}],
    "bgp": {
      "198.11.13.2": {
        "state": "Established", "peer_as": 65013,
        "routing_instance": "L3VPN-CPE13-NNI",
        "ribs": {
          "inet.0": {"received": 14, "accepted": 14, "advertised": 3,
                     "active": 3, "suppressed": 0}
        }
      }
    },
    "evpn_vpws": {"EVPN-VPWS-CPE13-NNI": {"local_sid": 213, "remote_sid": 213, "status": "Up"}},
    "evpn_esi":  {"00:11:22:...": {"status": "Up/Forwarding", "df_role": "10.0.0.5",
                                   "interface": "ae0.14"}},
    // Od schema 7 ctou evpn_mac i evpn_instance per-instance data z 'count'
    // resp. extensive vypisu. VLAN klic je skutecny learn-vlan (drive "-").
    "evpn_instance": {
      "EVPN-VLAN-AWARE-CPE13-NNI": {
        "local_interfaces": {"total": 2, "up": 2, "entries": [
          {"name": "ge-0/0/2.313", "status": "Up"}]},
        "irb_interfaces": {"total": 0, "up": 0, "entries": []},
        "neighbors": {"total": 1, "addresses": ["150.0.0.13"]},
        "esis": {}
      }
    },
    "evpn_mac": {
      "EVPN-VLAN-AWARE-CPE13-NNI": {
        "vlans": {"313": {"count": 2, "domain": "BD-313"}},
        "interfaces": {"ge-0/0/2.313": {"count": 1, "name": "ge-0/0/2.313:313",
                                         "domain": "BD-313"}}
      }
    },
    // Doslovne z runs/bfd-static-2026-07-29/pre.json: na tomhle zarizeni je
    // pet servisnich statik nakonfigurovanych, ale v tabulce nejsou (next-hop
    // zesel po deaktivaci ge-0/0/2), a BFD session nevznikla ani jedna.
    "routes": {
      "mgmt_junos.inet.0":  {"0.0.0.0/0": {"next_hop": ["10.0.0.2"],
                                           "via": ["fxp0.0"], "active": true}},
      "mgmt_junos.inet6.0": {"::/0": {"next_hop": ["2001:db8::1"],
                                      "via": ["fxp0.0"], "active": true}}
    },
    "bfd": {}
  },
  "probes": {
    "ping": [
      {"scope_id": "svc:L3VPN-CPE13-NNI:IPVPN", "target": "198.11.13.2",
       "source": "198.11.13.1", "routing_instance": "L3VPN-CPE13-NNI",
       "resolved_from": "arp", "family": 4, "interface": null,
       "sent": 5, "received": 5, "loss_percent": 0, "rtt_avg_ms": 1.24},
      {"scope_id": "svc:L3VPN-CPE13-NNI:IPVPN", "target": "2001:db8:11:13::b",
       "source": "2001:db8:11:13::a", "routing_instance": "L3VPN-CPE13-NNI",
       "resolved_from": "nd", "family": 6, "interface": null,
       "sent": 5, "received": 5, "loss_percent": 0, "rtt_avg_ms": 1.31}
    ]
  }
}
```

Vlastnosti:

- `facts` jsou **syrová, device-scoped data** klíčovaná přirozeným klíčem. Žádné `service_id`
  uvnitř. Kdyby se párování ukázalo jako špatné, opraví se a staré snapshoty se přehodnotí
  znovu, bez sahání na zařízení.
- `facts.nd` je IPv6 protějšek `facts.arp` — stejný tvar, navíc pole `state`.
- `facts.bgp[peer].ribs` drží počty **za každou RIB zvlášť**, nikdy sečtené — jeden peer
  může mít až 11 RIB (`bgp.rtarget.0`, `inet.0`, `bgp.l3vpn.0`, ...).
- `facts.routes` je klíčované **jménem RIB, pak prefixem**. Jméno nese rodinu
  (`...inet6.0`), takže asymetrie, kterou má mezi IPv4 a IPv6 konfigurace, se ve snímku
  nevyskytuje. Prázdné tabulky se neukládají — RPC jich vrací přes dvacet.
- `facts.bfd` je klíčované **adresou souseda** — pod stejným klíčem hledá check i záměr
  z inventory a stav BGP. Prázdný slovník je platný stav (BFD nakonfigurované, session
  nevznikla), ne chyba sběru.
- `capture.collectors` nese stav každého sběru zvlášť — selhání jednoho RPC nezruší capture.
- `device.uptime_seconds` je v modelu, ale `device_meta()` ho zatím vždy plní `None`.
- Ping záznam nese `family` (4/6) a `interface` — to druhé je vyplněné jen u IPv6 link-local
  cíle, protože ten Junos ping bez odchozího rozhraní odmítne.
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
    "local_ipv4":          ["198.11.13.1/30"],
    "local_ipv6":          ["2001:db8:11:13::a/127"],
    "virtual_gw_v4":       [],
    "virtual_gw_v6":       [],
    "vlans":               ["113"],
    "bridge_domains":      [],
    "static_routes": [
      {"rib": "L3VPN-CPE13-NNI.inet.0",  "prefix": "172.26.1.0/29",
       "next_hop": ["198.11.13.2"]},
      {"rib": "L3VPN-CPE13-NNI.inet6.0", "prefix": "2001:eeee::/64",
       "next_hop": ["2001:db8:11:13::b"]}
    ],
    "bfd_peers": [
      {"peer": "198.11.13.2", "minimum_interval": 3000, "multiplier": 3,
       "source": "neighbor"}
    ]
  }
}
```

`static_routes` a `bfd_peers` jsou **konfigurační záměr**, ne měření — jsou to jediné
selektory, které nesou hodnoty, a ne jen jména. `static_routes` slouží zároveň jako filtr
(routa patří scope, když sedí dvojice `(rib, prefix)`); `bfd_peers` jako filtr **neslouží** —
session se vybírají přes `bgp_neighbors`, aby session peeru chybějícího v záměru nezmizela
beze stopy.

Scope je **čistě filtr**, neobsahuje naměřená data. Device scope má `kind: "device"`
a prázdné selektory = „ber všechno". Adresy i virtual-gateway jsou rozdělené podle rodiny
(`local_ipv4`/`local_ipv6`, `virtual_gw_v4`/`virtual_gw_v6`) — stejně jako v inventory YAML
(viz [files/parsers.md](files/parsers.md#výstupní-formát)).

`id` je `svc:<description nebo název rozhraní>:<service_type>`. Když by dvě služby vyšly na
stejný klíč, přidá se za něj ještě název rozhraní (`svc:et-0/0/10.0:IPVPN`).

---

## 5. Formát výsledku

`schema_version: 1` — **nezměnilo se** rozdělením IPv4/IPv6 (na rozdíl od inventory
a snapshotu výš); `models/result.py::RunResult.schema_version` zůstává `1`.

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

  // jen u vysledku, ktery prosel filtrem (--filter / --status); jinak klic chybi
  "filtered": {"scopes_shown": 2, "scopes_total": 11, "statuses": ["FAIL"]},

  "scopes": [
    {
      "scope_id": "svc:L3VPN-CPE13-NNI:IPVPN",
      "key": {"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN", "service_subtype": null},
      "status": "WARN",
      "match": {
        "status": "matched", "method": "description+service_type", "confidence": "high",
        "baseline_interfaces": ["ge-0/0/2.113"], "subject_interfaces": ["et-0/0/8.113"]
      },
      "identity": {
        "description": "L3VPN-CPE13-NNI", "service_type": "IPVPN", "service_subtype": null,
        "routing_instance": "L3VPN-CPE13-NNI",
        "ipv4": ["198.11.13.1/30"], "ipv6": ["2001:db8:11:13::a/127"],
        "virtual_gw_v4": [], "virtual_gw_v6": []
      },
      "checks": [
        {
          "id": "interface_traffic", "mode": "both",
          "status": "WARN", "severity": "advisory",
          "message": "et-0/0/8.113: output_pps kleslo o 72 % (410 -> 115), prah je -60 %",
          "label": "Interface traffic out (et-0/0/8.113)",
          "value": "115 pps", "baseline_value": "410 pps", "delta": "-72 %",
          "baseline": {"output_pps": 410},
          "subject":  {"output_pps": 115},
          "details":  {"tolerance_percent": -60.0}
        },
        {
          "id": "bgp_prefix_counts", "mode": "compare",
          "status": "WARN", "severity": "advisory", "family": 4,
          "message": "198.11.13.2/inet.0: pokles advertised 14 -> 3, prah je -10 %",
          "label": "BGP advertised-prefix-count",
          "value": "3", "baseline_value": "14", "delta": "-11",
          "baseline": {"advertised": 14}, "subject": {"advertised": 3},
          "details": {"rib": "inet.0", "tolerance_percent": -10.0, "change_percent": -78.6}
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
    "bgp_peers": [{"peer": "10.1.0.5", "routing_instance": null, "snapshot": "subject"}],
    "static_routes": [
      {"rib": "mgmt_junos.inet.0", "prefix": "0.0.0.0/0", "next_hop": ["10.0.0.2"],
       "via": ["fxp0.0"], "snapshot": "subject"},
      {"rib": "mgmt_junos.inet6.0", "prefix": "::/0", "next_hop": ["2001:db8::1"],
       "via": ["fxp0.0"], "snapshot": "subject"}
    ],
    "bfd_sessions": [
      {"peer": "10.1.0.5", "interface": "ge-0/0/9.0", "state": "Up",
       "snapshot": "subject"}
    ]
  }
}
```

Vlastnosti:

- **Každý check nese `baseline` i `subject` bloky se surovými čísly**, ne jen verdikt, a
  navíc (od přepisu reportu) `label`, `value`, `baseline_value`, `delta` a volitelně
  `family` — rozklad naměřené hodnoty na popisek a hodnotu ve sloupcích musí udělat check,
  protože jen on ví, co je hodnota a co vysvětlení (`CheckResult.to_dict()` prázdné volitelné
  klíče vynechá).
- `interface_state` a `interface_traffic` teď vrací **jeden check-výsledek na fakt/směr**
  (`Interface admin status (<jméno>)` / `Interface operational status (<jméno>)`;
  `Interface traffic in (<jméno>)` / `Interface traffic out (<jméno>)`), ne jeden souhrnný
  na rozhraní. `bgp_prefix_counts` vrací jeden na
  **RIB × counter** (`label="<counter>-prefix-count"`, `group=f"BGP {peer} / {rib}"`), ne
  souhrn napříč RIB.
- `status` scope = nejhorší stav jeho checků (`SKIP` jen když není co lepšího hlásit);
  `summary` = agregát přes **checky**. Počty služeb si terminál dopočítá ze `scopes` — je to
  týž výpočet za celý běh i za filtrovaný výběr, takže v `summary` být nemusí.
- **Po filtru (`--filter`, `--status`) jsou `pass`/`warn`/`fail`/`skip` v `summary` za
  zobrazenou množinu, ne za celý běh**, a `filtered` říká, že se to stalo.
  `scopes_matched` a obojí `unmatched_*` se nepřepočítávají — filtrování se na `NESPAROVANO`
  nevztahuje.
- `match` je `null` u běhu bez baseline; u nespárovaného subject scope má
  `status: "unmatched"` a `reason`.
- `identity` nese vše, co report o službě potřebuje (adresy, VGW, RI, popisek) — bez toho by
  to zůstalo jen ve `Scope`, ke kterému renderer nemá přístup.
- **Nespárované baseline scopy nemají vlastní záznam v `scopes`** — nejsou v subjektu, není
  co měřit. Jsou jen v `unmatched.baseline`.
- `unassigned` hlásí zatím jen data ze **subjektu** (nové zařízení) a ve všech třech
  seznamech (`bgp_peers`, `static_routes`, `bfd_sessions`) je v device režimu vždy prázdno —
  device scope si nárokuje všechno, takže „nepřiřazené" nemá význam.
- **`unassigned.static_routes` je zároveň pojistka proti mezerám v parsování.** Spadne sem
  statika v management instanci (`mgmt_junos.inet.0 0.0.0.0/0` přes `fxp0.0` — `fxp0.0` se
  scopem nikdy nestane), ale i routa, jejíž konfigurační tvar parser neuměl přečíst: do
  selektorů se nedostane, v tabulce ji ale vidět je.
- **`unassigned.bfd_sessions`** obsahuje session peeru, který není v žádném `bgp_neighbors` —
  typicky BFD držené jiným klientem než BGP, jehož záměr parser vůbec nečte.
- **Interní BGP peeři (vlna 2026-08-26) nespadají do `unassigned.bgp_peers`.** Parser
  pozná interní peer podle explicitního `type internal` na neighbor/group; bez příkazu
  fallback na `peer-as == local-as` (s respektem k `local-as` overridům). Takový peer se
  přiřadí do `bgp_neighbor` záznamu **Core loopback** (lo0.0), ne do žádné zákaznické
  služby ani do `unassigned` — dřív, když interní peer neměl vlastníka, končil v
  `NEZARAZENO` (sekce viz níž); od téhle vlny má vlastníka vždy. Druhý důsledek: interní
  peeři na lo0.0 **nedostávají BFD záměry** (`_assign_bfd` má na rozšíření filtru
  `_assign_bgp_neighbors` explicitní gate).
- `unassigned` se **do textového reportu vypisuje** v sekci `NEZARAZENO` (AR-39) — to je něco
  jiného než `NESPAROVANO`, která vypisuje `unmatched`. Sekce `NEZARAZENO` se vypisuje vždy,
  i prázdná (`(nic)`), a filtrování se na ni nevztahuje.
- `message` je česky (bez diakritiky), konzistentně se zbytkem nástroje.
- **`scopes[].link` je volitelný klíč** — nese vazbu L3 (IRB) ↔ L2 (E-LAN tranzit) v téže
  EVPN instanci, viz „Vazba L2+L3" níže. Bez vazby klíč u scope chybí úplně (aditivní klíč,
  stejné pravidlo jako u ostatních volitelných polí v tomto formátu).
- **`step` a `excluded_services` jsou aditivní klíče z `evaluate --run` na migračním kroku**
  (viz [oddíl 8](#8-run-management---run-fáze-4)). `step` nese
  `{"old": {"node", "port"}, "new": {"node", "port"}}`; bez kroku chybí úplně. `excluded_services`
  je seznam ve tvaru `unmatched.subject` (`scope_id`, `description`, `service_type`, `reason`)
  — služby na sdíleném LAG portu, které patří jinému kroku (nesparovaly se s baseline tohoto
  kroku); plní se **jen** s `step` a jen když se pro krok našla baseline (bez baseline žádný
  filtr neproběhl, klíč chybí úplně), i prázdný seznam pak znamená „filtr proběhl".

### Vazba L2+L3

Služba typu Internet/IPVPN, jejíž L3 rozhraní je IRB v EVPN mac-vrf instanci, má v konfiguraci
dvě části: L3 (IRB, RI služby) a L2 (E-LAN, tranzitní rozhraní téže mac-vrf instance). Engine
tuhle dvojici pozná a spáruje.

Zdroj vazby: `l3_context` IRB rozhraní z `show evpn instance extensive` / `show mac-vrf routing
instance extensive` (fakt `evpn_instance`, fáze 2.1) + shoda VLAN/unitu v téže instanci;
`master` znamená inet.0 (Internet scope bez RI). Vazba je **N L2 : 1 L3** — jeden IRB
obsluhuje všechny L2 scopy své bridge domain (lab 172.20.20.4, BD-4094 se dvěma access
porty), každý L2 scope má právě jeden L3 protějšek. Nejednoznačnost na L3 straně (víc L3
kandidátů pro jeden IRB) vazbu nevytvoří — raději žádný odkaz než špatný.

**V textovém reportu** (`render`) se L2 blok (`E-LAN (L2 cast)`) vypíše hned za L3 blokem
spárované služby a oba nesou v hlavičce vzájemný odkaz:

```
L3 cast: irb.15 v L3VPN-CPE14-UNI (blok vyse)
```
```
L2 cast: ae0.15 v EVPN-VLAN-AWARE-POP1 (blok nize)
```

Pokud `--filter`/`--status` vybere jeden z páru, filtr ponechá i druhého partnera, i když sám
kritériu neodpovídá — jinak by odkaz „blok nize/vyse" ukazoval do prázdna. Partner se počítá až
z výsledku obou kritérií (`--filter` i `--status` dohromady), ne z každého zvlášť, takže dvojici
drží pohromadě jen tehdy, když aspoň jedna strana skutečně sedí — pár, kde nesedí ani L3, ani
L2, filtr smaže celý. Partnerovy checky se počítají i do přepočítaného souhrnu ve `filtered` -
je zobrazený stejně jako kterýkoli jiný vybraný scope. Bez `--detail` navíc platí obvyklé
PASS-collapse pravidlo: partner s PASS se v tabulce objeví, ale jeho blok se rozbalí, jen když
se ukazuje (FAIL/WARN nebo `--detail`).

Errors/traffic se u L3 části neměří přímo (IRB sám o sobě není tranzitní rozhraní) — L3 blok
místo toho nese jeden INFO řádek `mereno na L2 (ae0.15) - viz blok nize`; skutečné počítadlo
je v L2 bloku. Podrobnosti viz katalog checků výše.

**Strukturovaný výsledek** (`evaluate --format json`) nese totéž jako volitelný klíč
`scopes[].link`:

```jsonc
// L3 (IRB) strana - seznam vsech L2 protejsku:
"link": {
  "role": "l3",
  "peers": [
    {"scope_id": "svc:EVPN-VLAN-AWARE-POP1:E-LAN",
     "interface": "ae0.15",
     "instance": "EVPN-VLAN-AWARE-POP1"}
  ]
}
// L2 (E-LAN) strana - protejsek je vzdy prave jeden:
"link": {
  "role": "l2",
  "peer_scope_id": "svc:L3VPN-CPE14-UNI:IPVPN",
  "peer_interface": "irb.15",
  "peer_instance": "L3VPN-CPE14-UNI"
}
```

`role` je `"l3"` na IRB straně scope a `"l2"` na E-LAN straně. L3 strana nese seznam
`peers` (N L2 : 1 L3); L2 strana má skalární `peer_scope_id`, `peer_interface` a
`peer_instance`. Bez vazby klíč `link` u scope chybí úplně.

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
| `nd` | `get_ipv6_nd_information` | totéž | — |
| `bgp` | `get_bgp_neighbor_information` | totéž | — |
| `evpn_vpws` | `get_evpn_vpws_information` | totéž | — |
| `evpn_esi` | `get_evpn_instance_information` | totéž | `extensive=True` |
| `evpn_instance` | `get_evpn_instance_information` | `get_mac_vrf_instance_information` | `extensive=True` |
| `evpn_mac` | `get_bridge_mac_table` + `get_evpn_mac_table` | `get_mac_vrf_mac_table` | `count=True` |
| `routes` | `get_route_information` | totéž | `protocol="static"` |
| `bfd` | `get_bfd_session_information` | totéž | `detail=True` |
| ping (probe) | `ping` | totéž | `host`, `count`, `rapid=True`, volitelně `source`, `routing_instance`, `interface` (jen IPv6 link-local cíl) |

Dva argumenty, které vypadají jako kosmetika a nejsou:

- **`protocol="static"`** drží odpověď malou i na zařízení s plnou internetovou tabulkou.
- **`detail=True`** je nutnost, ne pohodlí: stručný výpis BFD nemá ani `bfd-client`, ani
  `remote-state`, takže by collector tiše sbíral data, ze kterých se nedá poznat, že session
  drží BGP a že ji protějšek administrativně vypnul.

`extensive` u `evpn_esi` není kosmetika: bez něj `show evpn instance` vrátí souhrn bez
jediného ESI a collector by tiše vracel prázdno.

---

## 8. Run management (`--run`, fáze 4)

Provozní návod se stromem, hybridním `run.yml` a odvozeným příkladem je v
[README.md kap. 3a](README.md#3a-run-management---run). Tady jen suché tabulky.

### `run.yml` (`schema_version: 1`)

| sekce | klíče | poznámka |
|---|---|---|
| `devices` | `<node>: {host, platform, role}` | `role` ∈ `old`/`new`/`l2-switch`; fáze 4 podporuje jednoho `old` a jednoho `new` |
| `interface_mapping` | seznam `{old: {node, port[, l2_switch]}, new: {node, port[, l2_switch]}}` | páruje logické jednotky (`ge-0/0/0`), stejný tvar jako `mapping.yml` selektor `interface` |
| `captures` | seznam `{phase, device, port, snapshot, taken}` | `port: all` v souboru odpovídá `port: null` v modelu (celoboxová capture); vede ji aplikace, ne operátor |

Soubory v `runs/<nazev>/` normalizují port náhradou `-`/`/` za `_`
(`ge-0/0/0` → `ge_0_0_0`): `inventory_<node>_<port|all>.yml`,
`snapshot_<pre|post|rollback>_<node>_<port|all>.json`.

### Nové přepínače `capture`

| přepínač | výchozí | poznámka |
|---|---|---|
| `--run` | — | vzájemně vylučné s `--output` |
| `--run-root` | `runs` | kořen run adresářů |
| `--port` | — | logická/fyzická jednotka pro `--run` režim (`ge-0/0/0`); **jen s `--run`** — bez něj `--port` hlásí chybu, protože mimo `--run` je `--port` u `capture` přejmenován na `--ssh-port` |
| `--maps-to` | — | `NODE:PORT`, vyžaduje `--port`; zapíše pár do `interface_mapping` jako protistranu téhle capture |
| `--parse-services` | — | vyžaduje `--run`; inventory vyrobí z konfigurace, existující soubor **vždy přegeneruje** a vypíše deltu (`inventory pregenerovana: ... (+N nove, -M odebrane)`), poprvé `inventory vyrobena: ...` |
| `--overwrite` | — | jen v `--run` režimu (uplatní se v `_capture_into_run`, mimo `--run` je no-op); povolí přepis existujícího `pre` snímku (stejný node/port); bez něj druhý `--phase pre` skončí chybou `pre snimek uz existuje: ...; prepis povol s --overwrite` |
| `--ssh-port` | `22` | **přejmenováno z `--port`**, aby `--port` mohlo znamenat síťový port v `--run` režimu. `record` si ponechává původní `--port` pro SSH — kolize u něj nehrozí |

`--phase` v `--run` režimu je uzavřený výčet `pre`/`post`/`rollback` (mimo `--run` je to volný
text, viz [oddíl 4](#4-formát-snapshotu) — pole `capture.phase` ve snapshotu).

### Nové přepínače `evaluate`

| přepínač | výchozí | poznámka |
|---|---|---|
| `--run` | — | vzájemně vylučné s `--snapshot` i s `--output`; vyhodnotí sparovane snimky z manifestu |
| `--run-root` | `runs` | kořen run adresářů |
| `--ports` | — | čárkou oddělený filtr portů pro `--run` režim; u jakéhokoli mapovaného kroku (včetně 1:1, nejen N:1/LAG) filtruje podle **starého** portu kroku; nemapované/celoboxové evaluace filtruje podle portu snímku |

`evaluate --run` nejdřív ověří, že soubory všech `captures` z manifestu existují
(`RunStore.missing_snapshots`) — chybí-li jeden, skončí chybou a nevyhodnotí nic.

### Subcommand `status`

```
mig-validate status --run <nazev> [--run-root runs]
```

Tabulka `OLD | NEW | PRE | POST | ROLLBACK` — jeden řádek na pár z `interface_mapping` (plus
řádek na každé celoboxové zařízení bez portu), `ano`/`-` podle toho, jestli `find_capture()`
pro danou fázi/uzel/port najde záznam. Čistě přehledový příkaz — nečte snapshoty, jen
manifest.

### Pravidla párování `evaluate --run`

Jedna evaluace na **každý migrační krok** (`pre` capture je jen zdroj baseline a evaluaci sama
netvoří). Na N:1 mapovaném (LAG) portu — víc `interface_mapping` záznamů se stejným `new` —
to znamená jednu `post` evaluaci na každý mapping, ne jednu na celou `post` capture.

| fáze subjektu | baseline (v pořadí, první nalezená vyhrává) | když nic nevyjde |
|---|---|---|
| `post` (na krok) | 1. `pre` starého portu daného kroku, spárovaného přes `interface_mapping` 2. `pre` celého starého boxu (capture bez portu) | vyhodnotí se bez baseline, stderr: `chybi pre snimek stareho boxu` |
| `rollback` | `pre` **téhož** zařízení a **téhož** portu | vyhodnotí se bez baseline, stderr: `chybi puvodni pre snimek stejneho zarizeni a portu` |

Pro každou evaluaci se vytiskne záhlaví `=== <subject snapshot> vs <baseline snapshot|"bez
baseline">{krok} ===`, kde `{krok}` je `[krok STARY_NODE:STARY_PORT -> NOVY_NODE:NOVY_PORT]`,
právě když evaluace vznikla z mapovaného kroku (`post` na mapovaném portu) —
**nezávisle na tom, jestli se baseline našla**: chybí-li `pre` snímek starého portu,
záhlaví je `=== ... vs bez baseline [krok ...] ===`, `{krok}` nemizí. `rollback` evaluace
`{krok}` nenese nikdy (`_plan_rollback` v `runs/pairing.py` ji staví bez `step` — baseline je
`pre` téhož zařízení a portu, ne mapovaná protistrana). Návratový kód `evaluate --run` je
nejhorší napříč všemi evaluacemi (`EXIT_FAILED_CHECKS`, jakmile má FAIL kterákoliv z nich).

**Filtr přes baseline (N:1).** Když se na LAG portu potkají služby patřící více krokům
(víc starých portů namapovaných na stejný nový), report jednoho kroku vyhodnotí jen ty
služby, které se spárovaly s baseline **tohoto** kroku — zbytek se do checků nezapočítá,
report o nich jen vypíše souhrnný řádek `Dalsi sluzby na <novy port> mimo tento krok: N
(nesparovano s baseline <stary port>)` (jen když krok existuje a nějaké takové služby jsou).
Výjimka: nespárovaný L2 scope, jehož propojený L3 protějšek (`scopes[].link`, viz „Vazba
L2+L3" níže) se **spároval** s baseline tohoto kroku, se filtrem nevylučuje — jde s ním jako
s jednou entitou. Ve výsledku `evaluate` (`docs/cs/files/reporting.md` a
[oddíl 5](#5-formát-výsledku)) to nese JSON klíč `excluded_services` — přítomný jen s `step`
a jen když se pro krok našla baseline (bez baseline klíč chybí, filtr neměl podle čeho
filtrovat).

### Ping z baseline při `--phase post`

Doplňuje [oddíl 4](#4-formát-snapshotu), pole `probes.ping[].resolved_from`.

| `resolved_from` | zdroj cíle | kdy se použije |
|---|---|---|
| `baseline-arp` | IPv4 ARP z `pre` snímku (snímků) spárovaného starého portu (portů) | `--phase post` uvnitř `--run`, pár existuje a jeho `pre` snímek je na disku |
| `baseline-nd` | IPv6 ND z týchž `pre` snímků | totéž, rodina IPv6 |
| `arp` / `nd` | vlastní ARP/ND nového zařízení | baseline nedostupná (chybí pár, chybí `pre` snímek, nebo capture běží mimo `--run`) |
| `subnet-fallback` | první volná adresa ze subnetu služby | ani vlastní ARP/ND nic nevrátily |

Na N:1 mapovaném portu (víc starých portů → jeden nový LAG port) se cíle vezmou ze **všech**
mapovaných `pre` snímků najednou: `_merged_baseline_entries()` sjednotí jejich ARP/ND
záznamy a deduplikuje podle IP, první výskyt vyhrává (pořadí dané pořadím mappingů v
`run.yml`). U 1:1 mapování je to degenerovaný případ jednoho snímku beze změny chování.

Baseline záznamy se mapují na scope přes **shodu IP se subnetem rozhraní**, ne přes jméno
portu — jména rozhraní starého boxu na novém neexistují. Link-local ND záznamy z baseline se
vždy vylučují (nejsou přenositelné mezi boxy) a vlastní adresy/virtual-gateway se z cílů
vylučují stejně jako u dnešního odvozování z vlastní ARP.
