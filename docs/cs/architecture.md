# Architektura a interakce souborů

Tenhle dokument popisuje **jak spolu jednotlivé části mluví** a jaká pravidla to drží
pohromadě. Popis jednotlivých souborů je v [files/](index.md#pokrytí-souborů),
provozní návod v [README.md](README.md).

---

## 1. Vrstvy a směr závislostí

```
                     ┌── sahá na síť ──┐
  connection/junos ──►  collectors/*   ──┐
        │            │  probes/ping    ──┤
        └────────────┘                   ├──►  Snapshot (JSON na disku)
                                         │
  models/inventory ──► scoping/builder ──┘
                                         
  ─────────────── hranice sítě: nad ní se už nikdy nepřipojuje ───────────────

  Snapshot ──► scoping/matcher ──┐
  Snapshot ──► models/scope ─────┼──►  engine  ──►  checks/*  ──►  models/result
                                 │                                     │
                                 └── config ──────────────────────────┐ │
                                                                      ▼ ▼
                                                                 reporting/*
```

| vrstva | smí | nesmí |
|---|---|---|
| `connection` | připojit se, poslat RPC, vrátit XML | interpretovat obsah |
| `collectors` | XML → strukturovaný dict | rozhodovat PASS/FAIL, znát služby |
| `probes` | aktivní testy (ping) | cokoliv jiného |
| `scoping` | inventory → scopy, párování starý↔nový | číst nasbíraná data |
| `checks` | čistá funkce `(fakta, scope) → Finding` | sáhnout na síť |
| `reporting` | výsledky → text / JSON | vyhodnocovat |

Pravidlo je vynucené i importy: `checks/base.py` má v docstringu explicitní zákaz
importovat cokoliv z `migration_validator.connection` a žádný modul pod `checks/` to
neporušuje.

---

## 2. Dvě cesty kódem

### Cesta A — `capture` (sahá na síť)

```
cli._cmd_capture
  └─ api.capture
       ├─ connection.junos.connect ....... otevře PyEZ Device, přeloží chyby
       └─ capture.capture_device
            ├─ connection.junos.detect_platform ..... junos | junos-evo
            ├─ collectors.registry.collectors_for ... vybere collectory pro platformu
            ├─ collector.collect(device, platform) .. per oblast → facts[oblast]
            ├─ scoping.builder.build_scopes ......... inventory → [Scope]
            ├─ probes.ping.resolve_targets .......... scopy + facts["arp"|"nd"] → cíle
            ├─ probes.ping.run_ping ................. aktivní ICMP, per cíl
            └─ models.snapshot.Snapshot ............. + save_snapshot() na disk
```

Pořadí uvnitř `capture_device` **není libovolné**: ping potřebuje cíle, které se odvozují
z ARP (IPv4) a ND (IPv6), takže bulk sběr musí proběhnout dřív. Celá tahle závislost se
odehraje **tady**.
Do snapshotu se uloží už hotový výsledek pingu jako obyčejná data — a proto jsou checky
při vyhodnocení navzájem nezávislé a můžou běžet v libovolném pořadí.

### Cesta B — `evaluate` (nesahá na síť)

```
cli._cmd_evaluate
  ├─ models.snapshot.load_snapshot ....... subject (a volitelně baseline)
  ├─ scoping.mapping.load_mapping ........ mapping.yml
  ├─ config.load_config .................. config.yml
  └─ api.evaluate
       ├─ checks.all.load_all ............ import = registrace checků
       └─ engine.evaluate_snapshots
            ├─ scoping.matcher.match_scopes ...... baseline scopy ↔ subject scopy
            ├─ Scope.select(facts, probes) ....... profiltruje data pro scope
            ├─ engine._aligned_baseline_data ..... přejmenuje klíče baseline rozhraní
            ├─ checks.base.run_check ............. Finding → CheckResult (status)
            └─ models.result.RunResult
  └─ reporting.text_report.render / json_report.to_json
```

---

## 3. Kontrakty, na kterých to stojí

Tohle je jádro věci. Nejsou to importy, ale **dohody mezi vrstvami** — když se poruší,
nástroj většinou nespadne, jen tiše přestane měřit.

### 3.1 Fact-schéma je tvrdé rozhraní mezi collectorem a checkem

Collector zapíše data pod klíč, `Scope.select()` podle stejného klíče filtruje a check je
pod ním hledá. Když collector klíč přejmenuje, check nedostane nic a vrátí `SKIP`.
**A protože `SKIP` není `FAIL`, nikde jinde se to neprojeví** — nástroj mlčky přestane
kontrolovat celou oblast.

| oblast | tvar | filtruje se podle |
|---|---|---|
| `interfaces` | `{ifname: {admin_status, oper_status, input_pps, output_pps, input_errors, output_errors, framing_errors}}` | název rozhraní (logická jednotka i fyzický rodič) |
| `arp` | `[{ip, mac, interface, learned_via, routing_instance}]` | `interface` |
| `nd` | `[{ip, mac, interface, state, learned_via}]` — IPv6 protějšek `arp`, ND tabulka | `interface` |
| `bgp` | `{peer_ip: {state, peer_as, routing_instance, ribs: {rib_name: {received, accepted, advertised, active, suppressed}}}}` — počty se drží **za každou RIB zvlášť**, nesčítají se | `peer_ip` ∈ `bgp_neighbors` |
| `evpn_vpws` | `{routing_instance: {interfaces: [{name, status, mode, local_sid, remote_sid}]}}` — `local_sid`/`remote_sid` mají tvar `{value, peers}` | **klíč = routing-instance** |
| `evpn_esi` | `{esi: {status, df_role, interface}}` | `interface` |
| `evpn_mac` | `{routing_instance: {vlan_id: count}}` | **klíč = routing-instance**; vnitřní klíč je **VLAN id** jako string (`"313"`), u vlan-based `"-"` |

Dvě místa, kde je keying záměrně netriviální:

- **`evpn_vpws` a `evpn_mac` se klíčují názvem routing-instance, ne rozhraním.** Název
  instance je při migraci stabilní, název portu ne.
- **`evpn_mac` používá jako vnitřní klíč VLAN id, ne název domény.** Tutéž doménu pojmenuje
  MX `BD-313` a EVO `VL-313`; při klíčování názvem by check po migraci nenašel protějšek
  v baseline a místo porovnání počtu MAC adres by vypsal jen stav.

Pojistkou je `tests/collectors/test_conformance.py`: vyrobí fakta **skutečnými collectory
z nahraného XML z laborky**, prožene je **skutečnými checky** a tvrdí, že nevrátí samé
`SKIP`. Je to jediný test, který obě poloviny švu připíná proti sobě.

### 3.2 `Scope.select()` je jediný filtr

Check nikdy nevidí data celého zařízení — dostane je už profiltrovaná. Díky tomu nemůže
omylem sáhnout na cizí službu a **nemá jedinou větev pro režim „s inventory / bez inventory"**:

| režim | scope | co check dostane |
|---|---|---|
| bez inventory | jeden `device` scope s prázdnými selektory | všechna data, bez filtru |
| s inventory | jeden scope na službu | jen rozhraní / RI / peery / VLAN dané služby |

`bgp_session_state` tedy bez inventory řekne „z 12 peerů je 11 Established", s inventory
totéž rozpadlé na služby. Stejný kód, stejný tvar JSON, jiná granularita.

### 3.3 Fakta jsou inventory-independent, probes nikoliv

Bulk sběr jede pevnou sadu RPC podle platformy, takže blok `facts` vypadá stejně
s inventory i bez ní. Ping ale potřebuje cíl a source adresu, což je informace ze scope —
**v device režimu se proto vůbec nespouští** a snapshot má `probes.ping: []`.

### 3.4 Zarovnání názvů rozhraní při porovnání (AR‑6b)

`interface_traffic` porovnává baseline vs. subject **podle názvu rozhraní**. Jenže při
migraci se port přejmenuje (`ge-0/0/2.113` → `et-0/0/8.113`), takže baseline data leží pod
jiným klíčem, než jaký check u subjektu hledá.

`engine._aligned_baseline_data()` proto u spárované dvojice **přejmenuje klíče baseline
rozhraní na názvy subjektu**, než data předá checkům. Mapování je poziční
(`zip(baseline.selectors.interfaces, subject.selectors.interfaces)`) a je jednoznačné,
protože service scope má v selektoru právě jedno logické rozhraní (`scoping/builder.py`).

Bez toho by porovnání tiše spadlo do stavového režimu a ztratil by se signál o poklesu
provozu přesně u služeb, kde se rozhraní přejmenovalo.

> Spec (AR‑6b) doporučuje ten invariant „jedno rozhraní na scope" pojistit assertem, aby
> selhal hlasitě, kdyby kdy padl. V kódu takový assert **není** — `zip` by prostě tiše
> zarovnal jen první dvojici. Je to poznámka o stavu kódu, ne návod k opravě.

Zarovnání (R-6, spec 2026-09-08) se netýká jen `interfaces` — stejný poziční mapping se
aplikuje na všechny oblasti, kde se jméno rozhraní objevuje jako klíč slovníku nebo jako
pole v záznamu, konkrétně: `interfaces`, `evpn_mac` (`interfaces` uvnitř instance),
`optics` (i členové LAGu), `isis_adjacency`, `isis_interface`, `ldp_neighbor`,
`pim_neighbor`, `mpls_interface`, `igmp_group` (klíč slovníku), pole `interface`
v záznamech `arp`, `nd`, `bfd`, `evpn_esi`, a zanořené `name` v `evpn_instance`
(`local_interfaces.entries[]`, `irb_interfaces.entries[]`). BGP se klíčuje IP adresou
peera a zůstává při migraci stabilní bez přejmenování. Oblasti `pim_join`
a `multicast_route` se záměrně nepřeklíčovávají — jméno rozhraní tam leží jen v hodnotě
záznamu, která se s baseline neporovnává, takže by rename byl bez efektu.

### 3.5 Odvození statusu žije na jednom místě

Check **nevrací status**. Vrací `Finding` s naměřeným výsledkem (`Outcome`) a status z něj
odvodí framework v `models/result.py::derive_status()`:

| check vrátí | severity `critical` | severity `advisory` |
|---|---|---|
| `ok` | PASS | PASS |
| `unchanged` (R-3: stejný špatný stav v baseline i subjektu) | PASS (se značkou `unchanged_since_baseline`) | PASS (se značkou `unchanged_since_baseline`) |
| `degraded` (částečný úspěch) | **WARN** | **WARN** |
| `broken` | FAIL | WARN |
| `recovered` (zlepšení proti baseline) | **RECV** | **RECV** |
| `skip(reason)` | SKIP | SKIP |

Pravidlo *„částečný úspěch = WARN"* je tím zapsané jednou, ne v každém checku. Ping na
2 ze 3 adres je WARN i tehdy, když je ping v configu přepnutý na `critical`. Autor nového
checku tuhle konvenci nemůže omylem porušit.

Navazuje na to jedno pravidlo v `engine.py`: **status scope = nejhorší stav jeho checků,
ale `SKIP` vyhraje jen tehdy, když není co lepšího hlásit.** Bez toho by zdravá IPVPN
služba v režimu bez baseline svítila `SKIP` jen proto, že `bgp_prefix_counts` je
compare-only — a operátor by přišel o zelený signál právě u služeb s nejvíc kontrolami.

### 3.6 Chybějící data nikdy nedají PASS

Nadřazené pravidlo celého nástroje. Realizace je rozeseta po vrstvách a stojí za to ji
vidět pohromadě:

| situace | kde | chování |
|---|---|---|
| nepodařilo se připojit | `connection/junos.py` | `JunosConnectionError` → snapshot nevznikne, kód 2, hláška rozliší auth / timeout / refused |
| selhalo RPC jednoho collectoru | `capture.py` | oblast zůstane prázdná se správným typem, chyba se zapíše do `capture.collectors` |
| check potřebuje oblast, která selhala | `checks/base.py::run_check` | `SKIP` s **původní chybovou hláškou** |
| porovnávací check bez baseline | `checks/base.py::run_check` | `SKIP` s důvodem |
| check vyžaduje inventory, snapshot ji nemá | `checks/base.py::run_check` | `SKIP` s důvodem |
| check sám vyhodí výjimku | `checks/base.py::run_check` | `SKIP` — jeden rozbitý check nesmí zabít celý běh |
| snapshot má jinou `schema_version` | `models/snapshot.py` | `SnapshotVersionError` → kód 2, vypíše obě verze |
| ping neprojde | `probes/ping.py` | **není to chyba**, je to normální naměřený výsledek |

### 3.7 Registry se plní importem

Collectory i checky se registrují dekorátorem `@register` **při importu svého modulu**.
Naimportovat je musí někdo:

- `collectors/all.py` importuje `arp`, `bgp`, `evpn`, `interfaces`, `nd`; `capture.py` ho
  importuje na úrovni modulu.
- `checks/all.py` importuje `bgp`, `evpn`, `ifaces`, `reachability`; `api.evaluate()` a
  `api.list_checks()` volají `load_all()` explicitně, aby registry byla naplněná i tehdy,
  když GUI nebo test volá API přímo bez CLI.

`checks/all.py` je **samostatný modul, ne `__init__.py`, kvůli cyklickému importu**:
`checks/ifaces.py` importuje `checks/base.py`, takže `checks/__init__.py` nesmí importovat
`ifaces`. Stejný důvod platí pro `collectors/all.py`.

Praktický důsledek: **collector zapomenutý v `all.py` by tiše vypustil celou oblast.**
Hlídá to `tests/test_capture.py::test_every_area_is_registered_for_both_platforms`.

### 3.8 Nikdy se nehádá

Matcher při nejednoznačnosti **nespáruje**. Vyjde-li na některé úrovni víc než jeden
kandidát, scope jde do `unmatched` s důvodem `ambiguous: N kandidatu (...)`. Tichý špatný
match by u migrace znamenal zelenou na rozbité službě.

Symetricky platí, že se nic tiše nezahazuje:

- `unmatched` (služby bez protějšku) je první třídou výstupu a v textovém reportu se tiskne
  **vždy**, i když je všechno ostatní zelené a i když filtr smazal všechny scopy;
- `unassigned.bgp_peers` drží peery, které se nepodařilo přiřadit k žádné službě.

---

## 4. Platformní rozdíly řeší collector

MX a EVO se liší v RPC i ve tvaru odpovědi. **Celý rozdíl se pohltí uvnitř collectoru**,
navenek vrací obě platformy stejné schéma — a proto žádný check neobsahuje `if platform ==
"evo"`. Je to předpoklad, na kterém stojí cross-device porovnání.

| oblast | Junos (MX) | Junos EVO (ACX/PTX) |
|---|---|---|
| MAC tabulka | dvě RPC: `get_bridge_mac_table` (vlan-aware) + `get_evpn_mac_table` (vlan-based) | jedno RPC: `get_mac_vrf_mac_table` (obojí naráz) |
| tvar MAC záznamů | `l2ald-*` | `l2ng-l2ald-*` |
| název domény | `BD-313`, u vlan-based `__EVPN-VLAN-BASED-...__` | `VL-313`, u vlan-based `VL-NONE` |
| BGP `peer-address` | `150.0.0.1+179` | `150.0.0.1+57010` (efemerní port) |

Detekce platformy (`connection/junos.py::detect_platform`) hledá `EVO` v řetězci verze,
sekundárně model podle prefixu (`PTX10`, `ACX7`, `QFX5700`, `MX304`).

`Collector.rpc_names()` existuje kvůli tomu prvnímu řádku tabulky: collector s více RPC ho
musí přepsat, jinak by `record` a `--record-raw` uložily jen první z nich a nahrané fixtures
by byly tiše nekompletní.

---

## 5. Šev pro budoucí GUI

`api.py` je **jediné, co GUI bude volat**. `cli.py` je tenký obal nad ním, ne alternativní
implementace — cokoliv umí CLI, umí i GUI, protože jdou stejnou cestou.

Z toho plyne pár konkrétních vlastností:

- všechny výsledky jsou serializovatelné do JSON (`to_dict()` na každém modelu),
- `list_checks()` existuje proto, aby GUI nemuselo mít hardcoded seznam testů: check se
  přidá do registry a objeví se v obou rozhraních,
- `render()` a `to_json()` jen formátují — **nic nepočítají**. Souhrn i stav scope spočítá
  engine, takže se terminál a GUI nemohou rozejít.

---

## 6. Ověřeno proti laborce

Collectory nejsou psané naslepo; XPath byly ověřené proti nahranému XML z containerlab
topologie `pop-migration`:

| adresa | model | verze | platforma |
|---|---|---|---|
| `172.20.20.4` | VMX | `24.2R1-S2.5` | `junos` |
| `172.20.20.5` | PTX10002-36QDD | `25.2R1.8-EVO` | `junos-evo` |

Nahrávky z obou platforem jsou v `tests/fixtures/rpc/{junos,junos-evo}/` a slouží jako
regresní fixtures. Přístupové údaje do laborky nejsou v repozitáři — popis prostředí je
v `docs/superpowers/plans/2026-07-24-migration-validator-capture.md`.

---

## 7. Architektonická rozhodnutí

Podrobné zdůvodnění je ve specifikaci
(`docs/superpowers/specs/2026-07-24-migration-validator-design.md`); tady jen seznam, aby
se dalo dohledat, proč je něco tak, jak je:

| # | rozhodnutí |
|---|---|
| AR‑1 | Čistý PyEZ, vlastní framework — ne pyATS, ne JSNAPy |
| AR‑2 | Snapshot-centric model: `capture` → `evaluate` |
| AR‑3 | `evaluate` s volitelným baseline (stavové vs. porovnávací checky) |
| AR‑4 | Inventory je volitelná vrstva, ne povinný vstup — realizováno abstrakcí `Scope` |
| AR‑5 | Sběr je device-scoped, korelace service-scoped |
| AR‑6 | Tvrdé oddělení collector / check |
| AR‑6b | Zarovnání názvů rozhraní při porovnání |
| AR‑7 | Platformní rozdíly řeší collector, ne check |
| AR‑8 | Existující parsery zůstávají beze změny, validator konzumuje jejich YAML |
