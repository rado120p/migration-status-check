# EVPN rozšíření a run management — zastřešující design (5 fází)

Datum: 2026-08-05
Stav: schváleno v brainstormu, implementace po fázích (každá fáze dostane
vlastní implementační plán, až na ni dojde)

## Kontext

Vlny 1–10 pokryly L3 služby (BGP, BFD, ARP/ND, ping, deaktivace) a základ
EVPN (VPWS status, ESI, MAC count). Laborka nově obsahuje EVPN-VLAN-AWARE
služby s L3 rozšířením (IRB) a plán migrace počítá s postupnou migrací
port po portu, s možností rollbacku a s EX switchem mezi novým EVO boxem
a CPE.

Referenční vstupy: inventory `172.20.20.4.yml` / `172.20.20.5.yml`
v kořeni repa, captures v `runs/mig01/`. XML příklady všech nových výpisů
jsou v zadání brainstormu (`show evpn instance extensive`,
`show mac-vrf routing instance extensive`, `show evpn vpws-instance`,
`show * mac-table count`).

## Ověřené nálezy v dnešním kódu (podklad fází)

1. **ARP přes IRB se ztrácí ve filtru, ne ve sběru.** `collectors/arp.py`
   ukládá interface doslova (`irb.14[ ae0.14 ]`); `Selectors.
   matches_interface()` (`models/scope.py:72`) porovnává přesnou shodu
   proti `irb.14`. Záznam je ve snapshotu, ale check hlásí „zadny zaznam".
2. **Auto-generované ESI `05:...` se dnes neignorují.**
   `EvpnEsiCollector.parse()` bere všechny `evpn-esi` uzly; `05:` ESI
   nemají status a do reportu nepatří.
3. **RPC pro 1d už běží.** `EvpnEsiCollector` volá
   `get_evpn_instance_information` s `extensive` — tentýž výpis nese
   `local-interfaces`, `irb-interfaces` (+ `l3-context`),
   `evpn-num-neighbors`. Jde o rozšíření parsování, ne nové RPC.
   Na EVO ověřit jméno RPC přes `| display xml rpc` (CLI příkaz je
   `show mac-vrf routing instance extensive`; odhadnutá RPC jména
   v minulosti dvakrát neseděla).
4. **VPWS collector ignoruje `evpn-vpws-sid-pe-status`** a čte jen první
   rozhraní instance. Label nálezu je jméno instance (`label=name`),
   proto se v reportu objevuje název RI místo popisu checku.
5. **Parsery `mx_parser.py` a `evo_parser.py` jsou z ~96 % identické**
   (2644 vs. 2645 řádků, diff ~105 řádků). Rozdíly jen v detekci VPLS
   a určení EVPN subtype.
6. **Interface errors běží i na unitech**, které reálné error countery
   nemají.

## Fáze 1 — Rychlé opravy

Bez změny architektury, každý bod samostatně testovatelný.

### 1.1 Normalizace ARP/ND interface z IRB

Collector ARP i ND rozdělí `irb.14[ ae0.14 ]` na:

```json
{"interface": "irb.14", "learned_via": "ae0.14", ...}
```

Scope filtr pak záznam najde. Report u takového záznamu ukáže
`[via ae0.14]`. Nutná fixture z laborky (ARP přes IRB). `learned_via`
je zároveň vstup pro fázi 3 (měření na L2 rozhraní).

### 1.2 Interface errors jen na fyzickém rozhraní

Error-counter check se na logických jednotkách (unitech) přeskočí — platí
pro všechny typy služeb. Traffic (pps) na unitech zůstává beze změny.

### 1.3 Ignorace ESI `05:...`

`EvpnEsiCollector` vynechá ESI začínající `05:` (auto-generované, bez
statusu). Zároveň platí: `evpn-esi-value`/`-status` nemusí být v každém
výpisu — bez dat je výsledek SKIP, ne chyba.

### 1.4 EVPN-VPWS: SID/peer checky (bod 2 zadání)

- Label řádku stavu rozhraní: **„EVPN VPWS local interface status"**
  (místo jména instance), hodnota `Up`.
- Collector nově parsuje `evpn-vpws-service-id-{local,remote}-status-table`
  včetně vnořených `evpn-vpws-sid-pe-info` (může jich být více — remote
  multihoming; tabulka může být prázdná — peer nenakonfigurován/down).
- Schéma per instance: seznam rozhraní (ne jen první), per rozhraní
  local SID {value, peers[]} a remote SID {value, peers[]}, peer =
  {esi, ipaddr, mode, role, status}.
- Check: **stav se vyhodnocuje per SID + peer, výhradně podle
  `evpn-vpws-sid-pe-status`** — `Resolved` = PASS, jinak/chybí = FAIL
  („Neznamy peer", „Unresolved / Chybi"). Hodnoty SID, mode, role, ESI
  jsou informativní řádky bez stavu.
- Detekci „ESI != all-0s ⇒ měl být multihoming" neimplementujeme
  (vědomě odloženo).

## Fáze 2 — EVPN instance checky (1d) + MAC count přes `count` RPC

### 2.1 Kolekce `instance extensive`

Rozšířit parsování stávajícího extensive výpisu o per-instance data:
`local-interfaces`, `local-interfaces-up`, seznam
`evpn-interface-{name,status}`, `irb-interfaces`, `irb-interfaces-up`,
seznam `irb-interface-{name,status,l3-context}`, `evpn-num-neighbors`,
seznam `evpn-neighbor-address`, ESI hodnoty a stavy (bez `05:`).
Na EVO ověřit RPC jméno proti laborce.

### 2.2 Brief checky (pravidla ze zadání)

| Check | Pravidlo |
|---|---|
| Local interfaces | `local-interfaces > 0` PASS, jinak FAIL |
| Local interfaces up | `up == total` PASS, jinak FAIL |
| IRB interfaces | informativní (instance IRB mít nemusí) |
| IRB interfaces up | pokud `irb > 0`: `up == total` PASS, jinak FAIL |
| EVPN neighbors | `> 0` PASS, jinak FAIL |
| ESI status | `"resolved" in status` PASS, jinak FAIL; bez dat SKIP |

`--detail` rozepíše jmenovité řádky interfaců, IRB a neighborů.

### 2.3 MAC count přes `count` RPC

Náhrada dnešního počítání záznamů z plné MAC tabulky:

- Junos: `get_...` pro `show evpn mac-table count` + `show bridge
  mac-table count` (obě, jako dnes plné tabulky).
- EVO: `show mac-vrf forwarding mac-table count`.
- Výstup per instance: per-VLAN počet (`learn-vlan`/`mac-count`) a
  per-interface počet (`interface-name`/`mac-count`).
- Render: `BD-313 MAC count`, `BD-313 Interface ge-0/0/2.313:313
  MAC count` (vlan-based obdobně bez BD).
- **Per-VLAN i per-interface počet se porovnávají pre/post.** Per-VLAN
  klíčem je VLAN id (jako dnes). Per-interface počet se porovnává přes
  dvojici rozhraní spárované služby (starý port ↔ nový port ze scope
  párování, resp. z run.yml mappingu) — jména rozhraní se migrací mění,
  takže klíčem není jméno, ale pár. Když EVO `interface-name` nevrátí
  (v `count` výpisu bývá prázdné), per-interface řádek se vynechá a
  porovnává se jen per-VLAN.

### 2.4 Compare semantika

Pre/post: hodnoty se musí rovnat, jinak FAIL — s výjimkou jmen
interfaců a IRB interfaců, které na novém boxu u L3-extended služeb
přibývají (vazbu řeší fáze 3).

## Fáze 3 — Vazba L2+L3 (1a, schválená varianta A)

Služba typu Internet/IPVPN implementovaná jako VLAN-AWARE EVPN
s L3 rozšířením má na novém boxu dvě části: IRB (L3) a tranzitní L2
rozhraní (ge/xe/et/ae) v mac-vrf.

- **Zdroj vazby:** `irb-interface-l3-context` (fáze 2.1) + bridge-domain
  → L2 interface z téže instance. `master` = inet.0 (Internet), jméno RI
  = IPVPN.
- **Model:** dva scopy zůstávají, přibude vazba (odkaz IRB scope ↔ L2
  scope).
- **Render:** bloky pod sebou — L3 blok první, L2 blok hned za ním;
  hlavičky nesou vzájemný odkaz („L2 cast: ae0.15 v EVPN-VLAN-AWARE-…"
  / „L3 cast: irb.15 v L3VPN-…"). Mockup schválen
  (artifact f62369d0).
- **Měření:** interface errors/traffic u L3-extended služby na L2
  rozhraní (včetně LAG a jeho fyzického rodiče); v L3 bloku zůstane
  admin/oper status IRB + informativní odkaz na L2 blok.
- Párování starý (čisté L3 na UNI) → nový (IRB + L2): beze změny přes
  description (matcher pravidlo 1); vazba řeší jen prezentaci a měření
  na správném rozhraní.

## Fáze 4 — Run management (bod 4) + ping z baseline ARP (1c)

### 4.1 Struktura runs/

```
runs/<nazev-migrace>/
    run.yml
    inventory_<node>_<all|port>.yml
    snapshot_<pre|post|rollback>_<node>_<all|port>.json
```

Porty v názvech souborů normalizované (`ge-0/0/0` → `ge_0_0_0`).

### 4.2 Hybridní run.yml

Jediný zdroj pravdy o migraci. Lze napsat ručně předem (migrační plán),
NEBO ho aplikace plní flagy — obě cesty vedou k témuž souboru.

```yaml
devices:
  MX1-POP1:  {host: 172.20.20.4, platform: junos,     role: old}
  PTX1-POP1: {host: 172.20.20.5, platform: junos-evo, role: new}
  EX1-POP1:  {host: 172.20.20.7, platform: junos-ex,  role: l2-switch}

interface_mapping:
  # jeden zaznam = jeden migrovany stary port
  - old: {node: MX1-POP1, port: ge-0/0/0}
    new: {node: PTX1-POP1, port: et-0/0/0}
  # vic zaznamu muze sdilet stejny novy port (LAG); EX patri pod `new`,
  # je soucasti nove topologie (EVO -> EX -> CPE)
  - old: {node: MX1-POP1, port: ge-0/0/1}
    new:
      node: PTX1-POP1
      port: ae0
      l2_switch: {node: EX1-POP1, ae_port: ae0, access_port: ge-0/0/0}
  - old: {node: MX1-POP1, port: ge-0/0/2}
    new:
      node: PTX1-POP1
      port: ae0
      l2_switch: {node: EX1-POP1, ae_port: ae0, access_port: ge-0/0/1}

captures:                        # vede aplikace
  - {phase: pre, device: MX1-POP1, port: ge-0/0/0,
     snapshot: snapshot_pre_MX1-POP1_ge_0_0_0.json, taken: ...}
```

Formát old/new dvojic je připraven na více zdrojových boxů
(2× MX → ACX) a EX řetězení; **implementace fáze 4 podporuje 1 starý +
1 nový box**, víc boxů a `l2_switch` se aktivují později beze změny
formátu. Evaluate ověřuje, že soubory z manifestu existují.

### 4.3 CLI

```
mig-validate capture --run <nazev> --device <IP> \
    --phase pre|post|rollback [--port ge-0/0/0] \
    [--parse-services] [--maps-to NODE:PORT]
mig-validate evaluate --run <nazev> [--ports et-0/0/0,...]
mig-validate status  --run <nazev>          # volitelne, prehled portu
```

- `--port` omezí inventory/snapshot na služby daného portu; bez něj
  režim `all` (párování služeb pak dělá matcher přes description jako
  dnes). **Celoboxový režim zůstává plnohodnotný** — dnešní workflow
  „snapshot celého boxu pre/post" funguje beze změny, per-port režim je
  doplněk, ne náhrada. Obdobně `evaluate` bez `--ports` vyhodnotí
  všechno, co v runu je.
- `--maps-to` při post capture zapíše pár do `interface_mapping`;
  je-li pár už v run.yml, flag není potřeba.
- `--phase rollback`: snímek starého boxu; evaluate ho páruje proti
  původnímu `pre` téhož portu.
- Stávající explicitní `evaluate --snapshot/--baseline` zůstává
  (zpětná kompatibilita, ladění).

### 4.4 Integrace parserů (`--parse-services`)

- `mx_parser.py` + `evo_parser.py` se sjednotí do
  `migration_validator/parsers/`: společné jádro + platformní podtřídy
  (~105 řádků rozdílů: detekce VPLS, EVPN subtype). Fáze 5 přidá třetí
  podtřídu pro EX.
- `capture --parse-services` stáhne konfiguraci, vyrobí inventory do
  runs/ složky a pokračuje snapshotem. Bez flagu a bez existujícího
  inventory poradí chybová hláška „spusť s --parse-services".

### 4.5 Ping z baseline ARP (1c, schválená varianta 2)

Při `--phase post` se cíle pingu odvodí z ARP/ND záznamů **pre snímku
spárovaného portu** (dohledán přes run.yml) — řeší /24 rozsahy s mnoha
hosty, kde na novém boxu ARP hned nenaskočí. Dnešní odvozování
z vlastního ARP + subnet-fallback zůstává jako fallback, když pre snímek
neexistuje. Vlastní adresy/VGW se z cílů vylučují jako dosud.

## Fáze 5 — EX podpora (1b)

Samostatný design až přijde na řadu (vlastní brainstorm/spec). Rámec:

- Třetí platformní podtřída parseru (extended-vlan-bridge, varianty
  untagged s `native-vlan-id` vs. VLAN transparent s `vlan-id-list` +
  push/pop).
- Capture na EX, návaznost na EVO snímek přes `l2_switch` záznamy
  v run.yml.
- Párování služeb: primárně description (shodný napříč boxy), sekundárně
  VLAN id / outer tag po dokončení migrace na EVO.

## Pořadí a závislosti

```
faze 1 (opravy)  →  faze 2 (instance data)  →  faze 3 (L2+L3 vazba)
                                             ↘
                        faze 4 (run.yml, parsery, ping z baseline)
                                             ↘
                                                faze 5 (EX)
```

Fáze 4 nezávisí na 2–3 (lze předsunout, pokud bude potřeba dřív dělat
capture v nové struktuře); ping z baseline (4.5) závisí jen na 4.1–4.3.
Fáze 5 závisí na 4 (run.yml) a využije 1.1 (learned_via) i 3 (vazby).

## Rozhodnutí (uzavřeno v brainstormu)

- Render L2+L3: **varianta A** — dva bloky pod sebou, vzájemný odkaz,
  každý blok vlastní stav. (Mockup: artifact f62369d0.)
- Interface errors: nikdy na unitech, u všech typů služeb.
- VPWS stav: per SID+peer, jen podle `evpn-vpws-sid-pe-status`.
- Ping cíle: z baseline ARP pre snímku (varianta 2), ne z inventory.
- Parsery: sjednotit; generování inventory jako `--parse-services` flag
  (opt-in), ne default.
- Rollback: třetí fáze `--phase rollback`, baseline = původní pre.
- Párování portů: hybridní run.yml (ručně i flagy), old/new dvojice,
  `l2_switch` pod `new` (EX je součást nové topologie);
  multi-box jen ve schématu, implementace později.
- EX: odloženo do fáze 5, detailní design samostatně.
