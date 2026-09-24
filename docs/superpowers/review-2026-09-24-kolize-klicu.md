# Revize: kolize klíčů a chybné přiřazení faktů službě (2026-09-24)

Podnět: kolize adres peerů z ostrého běhu MX → ACX 2026-09-23 (viz
`followup-2026-09-23-kolize-adres-peeru-vrstva-2.md`). Uživatel chtěl před
vrstvou 2 projít všechny oblasti, jestli jinde klíč nesplývá napříč
instancemi / rozhraními / rodinami, nebo jestli se fakta jinak nedostanou
k cizí službě.

Revize proběhla nad main `b459b57` jen čtením kódu a fixtures, bez zásahu
do kódu; nejzávažnější nálezy jsou ověřené syntetickým vstupem přes
skutečné funkce.

## Jak číst nálezy

Každý nález má **úroveň doložení**:

- **R** – reprodukováno: syntetický vstup puštěný skutečným kódem, nebo
  vidět v reálném běhu / ostrém provozu.
- **K** – jen z čtení kódu; logika je jasná, ale nikdo to nepustil.
- **J** – závisí na chování Junosu, které není v žádné nahrávce (tvar XML
  nebo existence duplicitních položek). Bez ověření v laborce to není
  nález stejné váhy jako R.

A **dopad na formát snapshotu**:

- **F** – oprava mění tvar faktů → SCHEMA_VERSION bump (13 → 14). Loader
  přijímá jen přesnou shodu, takže každý bump znamená `mig-validate
  upgrade` z raw XML a ztrátu snapshotů bez raw záznamu. Všechny F nálezy
  proto patří do jednoho bumpu, jinak přijde 14 → 15.
- **E** – jen vyhodnocení (Scope.select, checky, parser inventory,
  párování, probe). Formát beze změny, samostatné vlny.

## Souhrn

| # | Oblast | Problém | Doložení | Formát | Dopad |
|---|---|---|---|---|---|
| F1 | bgp | klíč jen adresa, přepis mezi VRF | R (ostrý běh) | F | falešný WARN/FAIL, ztracená session |
| F2 | bfd | klíč jen adresa; multihop bez instance | R (ostrý běh) | F | falešný FAIL, u transit Core bez kolizního SKIP |
| F3 | evpn_esi | klíč jen ESI; přepis mezi instancemi, jen první lokální IFL | R (syntetika) / J (tvar XML pro víc IFL) | F | Down ESI schovaný → PASS |
| F4 | pim_join | (S,G) bez příznaku RPT/SPT | J | F | převrácená/zmizelá role, falešný BROKEN |
| F5 | pim_neighbor | klíč rozhraní, rodina/víc sousedů ignorováno | J | F | falešný DEGRADED, v6 Up maskuje v4 Down |
| F6 | isis_adjacency | klíč rozhraní, level ignorován | J | F | Up L1 maskuje Down L2 |
| F7 | ldp_neighbor | klíč rozhraní | J (teoretické) | F | flip adresy souseda |
| F8 | routes | `setdefault` – static vs aggregate na stejném (rib, prefix) | J | F | falešný FAIL aggregate |
| E1 | scope id | `svc:{desc}:{typ}` bez subtypu → duplicitní id | R | E | cizí IRB link, zdvojené bloky, GUI toggle |
| E2 | evpn_vpws | check nefiltruje AC služby, baseline páruje pozičně | R (syntetika) | E | zdravý PW ukáže FAIL cizího AC; UNCHANGED na cizí baseline |
| E3 | ping | Internet ve virtual-routeru pinguje v master | R | E | falešný no-reply / PASS přes cizí linku |
| E4 | ping | baseline cíle podle subnetu napříč VRF, dedup jen IP | R | E | cizí hosty jako cíle, potlačený fallback |
| E5 | matcher | nejednoznačný klíč → scope zahozen, k pravidlu RI se nedostane | R | E | služba nesparovaná; ve step běhu zmizí úplně |
| E6 | static routes | routa s hopy do dvou služeb nese obě | K | E | změna hopu B = WARN/FAIL u A |
| E7 | LAG optika | poziční rename členů (řazení jako text) | R | E | porovnání cizího transceiveru |
| E8 | evpn_mac | VLAN scopu ≠ learn-vlan faktů | R (runs/migration-pop1, změřil agent revize) | E | fantomové řádky, vlan-based řádek vypadne (false PASS) |
| E9 | Internet ve VR | multicast tabulky se na MX nečtou; BGP/BFD/static nekonzistentní | R (parser) / K | E | asymetrie pre/post |
| E10 | bgp parser | link-local soused klíčován jen adresou, `local-interface` se nečte | R (syntetika) / J (link-local BGP v nahrávkách není) | E | oba scopy vlastní tutéž session |
| E11 | linker | IRB unit == VLAN heuristika | K | E | cizí IRB link |
| E12 | evpn_* | žádné NEZARAZENO ani kolizní značky | K | E | ztráta je neviditelná |

## Nálezy podrobně

### F1 – bgp: klíč jen adresa (známé, vrstva 2)

`collectors/bgp.py` – `peers[address] = …`. Viz follow-up note.
Nahrávka nese `peer-cfg-rti` i `local-interface-name` (MX i EVO), takže
klíč (instance, adresa) jde postavit a pro link-local (E10) je k dispozici
i rozhraní.

### F2 – bfd: klíč jen adresa (známé, vrstva 2)

`collectors/bfd.py` – `sessions[neighbor] = …`. Single-hop nese
`session-interface`. Multihop v detail výstupu nenese instanci ani rozhraní
(ověřeno na `tests/fixtures/rpc/junos/bfd.xml`, session 198.11.14.4 –
`session-interface` prázdné). **Neověřeno:** zda `extensive` nese instanci
– v laborce teď multihop session není (2026-09-24 změřeno: MX 1 session,
EVO 2, všechny single-hop).

Navíc: `bfd_transit_state` (`checks/core_protocols.py:516`) kolizní SKIP
nemá – `bfd_collisions` vzniká jen pro `peer in bgp_neighbors` a transit
Core žádné nemá, takže přepsaná transit session dá falešný BROKEN „zadna
BFD session“.

### F3 – evpn_esi: klíč jen ESI

`collectors/evpn.py:114-139` iteruje `evpn-esi` pod každou `evpn-instance`
a ukládá `segments[esi]`; `interface` bere `node.find` = první lokální IFL.

Scénář: ESI na fyzickém AE (`interfaces ae0 esi 00:… all-active`), units
v různých EVI. Stejné ESI je pak pod každou instancí, přežije poslední.
Syntetika: Down kopie ESI pod dřívější instancí pro et-0/0/8.313, pozdější
Up pro ae0.4094 → scope et-0/0/8.313 dostal SKIP „bez dat“ a SKIP se ze
statusu služby vypouští, když jsou jiné výsledky (`engine.py:391-392`) →
služba PASS.

Tvar XML při `evpn-esi-num-local-intf` > 1 **neověřen** (laborka má vždy 1).

### F4 – pim_join: (S,G) bez RPT/SPT

`collectors/multicast.py:298` – `route_key(source or "*", group)`. V ASM se
SPT switchover může Junos vypsat (S,G,rpt) prune vedle (S,G) SPT se
stejným S a G; rozlišuje je `pim-group-flags` (`rptree` / `spt`), collector
ho ignoruje. Poslední vyhrává a jeho upstream/downstream jde do
`pim_pairs` (`checks/multicast.py:98-122`). **J:** všechny nahrané joiny
jsou SSM `sparse spt`.

### F5 – pim_neighbor: klíč rozhraní

`collectors/pim.py:244`. Dual-stack PIM (v4 + v6 řádek) nebo LAN s víc
sousedy → jeden přežije. XML nese `ip-protocol-version`, collector ho
nečte. **J:** nahrávky mají jen v4 a p2p. Riziko false PASS: Down v4
soused maskovaný Up v6 sousedem.

### F6 – isis_adjacency: klíč rozhraní

`collectors/isis.py:75`, XML nese `<level>`. Broadcast linka s L1+L2 nebo
LAN s víc sousedy → poslední vyhrává, Up L1 může maskovat Down L2. **J:**
Ethernet je v Junosu default broadcast, pokud není `point-to-point`;
laborka je p2p. Částečně tlumí `isis_interface_info` (L1 na Core = BROKEN).

### F7 – ldp_neighbor: klíč rozhraní

`collectors/ldp.py:36-39`. LAN s víc LDP peery nebo dual-transport (v4+v6)
→ flip adresy/uptime. Teoretické.

### F8 – routes: static vs aggregate

`collectors/routes.py:137` – `target.setdefault(prefix, data)`. Docstring
tvrdí, že Junos obojí v jedné RIB nedrží; neověřeno. Kdyby ano, aggregate
vypadne → falešný FAIL `aggregate_route_status`.

### E1 – duplicitní scope id

`scoping/builder.py:71-73`: `key_counts` počítá celý `ScopeKey` (vč.
subtypu), ale id je `svc:{label}:{service_type}`. Unit bez vlastního popisu
dědí popis portu (`parsers/core.py:1165`), takže na jednom portu stačí
IPVPN + IPVPN mvpn, Internet + Internet multicast, nebo E-LAN vlan-based +
vlan-aware.

Reprodukce: dva E-LAN scopy `svc:TRUNK:E-LAN` (ae0.100, ae0.200), IRB link
jen pro ae0.100 → `_link_payloads` (klíč scope id) dá link obou,
`_service_units` v `checks/evpn.py` pak čte irb.100 jako IRB ae0.200.
Dále: `linker.py:111`, `_reorder_linked` / `_group_by_layer1`
(`engine.py:262-306`), `shown` v `text_report.py:559-568`, GUI
`run_results.js:59` / `app.js:1008` (jeden toggle otevře oba bloky).

### E2 – EVPN-VPWS: check nefiltruje AC služby

Collector klíčuje `evpn_vpws` jménem instance, `Scope.select` vybere celou
instanci a `EvpnVpwsStatusCheck.run` (`checks/evpn.py:78-92`) projde
všechna rozhraní instance – `selectors.interfaces` nepoužije. Inventory
přitom dělá scope per IFL. Baseline páruje pozicí `idx`.

```
routing-instances VPWS {
    instance-type evpn-vpws;
    protocols evpn {
        interface ge-0/0/1.100 { vpws-service-id { local 100; remote 200; } }
        interface ge-0/0/1.101 { vpws-service-id { local 101; remote 201; } }
    }
}
```

Když je .101 Down, scope .100 dostane tři BROKEN řádky .101 – zdravá služba
svítí FAIL. Jiné pořadí/počet AC na novém boxu spáruje cizí baseline a
`unchanged_or` může Down AC označit UNCHANGED. Laborka má vždy jeden AC.
Párovat se má podle SID (local/remote), ne podle pozice.

### E3 – ping: Internet ve virtual-routeru

`probes/ping.py:235-239` nastaví instanci jen pro IPVPN. Internet scope
s `routing_instances=["VR-INET"]` pinguje v master: když adresa v inet.0
není, falešný no-reply (ADVISORY → WARN); když master drží stejnou adresu
na jiné lince, falešný PASS. BGP záměrně jde podle masteru
(`Scope.bgp_instance`), ping musí jít podle instance rozhraní.

### E4 – ping: baseline cíle napříč VRF

`_baseline_addresses` (`ping.py:159-195`, `:299-321`) bere z ARP/ND starého
boxu IP v subnetu scopu bez ohledu na rozhraní a instanci;
`_merged_baseline_entries` (`capture.py:52-70`) deduplikuje jen IP. ARP
instanci nenese. Produkční /30 případ je neškodný (oba scopy pingnou
192.168.1.2 ve své VRF). Rizikový je překryv větších LAN (stejná RFC1918
/24 ve dvou VRF, PE jako IRB gateway): cizí hosty se stanou cíli, mají
přednost před vlastní ARP vrstvou a `.local` záznam z cizí VRF umlčí
fallback. Běží jen v post capture s baseline a jen pro rodinu bez BGP
souseda.

### E5 – matcher: nejednoznačnost zahodí scope natrvalo

`matcher.py:188-206`: kolize klíče (popis, typ) správně nepáruje, ale
scopy jdou do `dropped` a k pravidlu `routing_instance + service_type`,
které by je rozlišilo, se nedostanou. Reprodukce: popis „CPE“, VRF
customer-a a customer-b, obě 192.168.1.1/30 na obou boxech → 0 párů; se
`step=…` jdou oba subjektové scopy do `excluded_services` – žádné checky,
nic v NESPAROVANO. Ruční mapping (`mapping.py:19-35`) nemá pole
`routing_instance`, rozliší jen rozhraním.

### E6 – static route sdílená dvěma službami

`_route_matches_service` (`parsers/core.py:1507-1520`) vrací True, když
kterýkoli hop padne do služby – celá routa se všemi hopy pak patří každé
takové službě. Dual-homed routa (next-hop + qualified-next-hop na dva CPE
na dvou unitech): změna hopu B = DEGRADED u A, deaktivovaný hop B přes
`_annotate_inactive_hops` = BROKEN/FAIL u A
(`checks/routes.py:337-340`, `:408-409`, `:492-544`).

### E7 – LAG členové: poziční rename

`engine.py:109-115` zipuje seřazené `lag_members` (textové řazení:
`et-0/0/10` < `et-0/0/8`). Jiný počet nebo pořadí členů → optika
porovnaná s cizím transceiverem; když se jméno objeví na obou stranách,
dict comprehension na `engine.py:136-139` jeden klíč přepíše.

### E8 – evpn_mac: VLAN scopu vs learn-vlan

Selektor `vlans` z inventory (`0x8100.4094`, `none`, `413`) vs klíče faktů
learn-vlan (`4094`, `0`). Sjednocení + filtr v `checks/evpn.py:896-918`
vyrábí fantomové řádky „VL-4094 MAC count 0“ / „VL-NONE …“ a skutečný
vlan-based řádek (klíč `0`) vyfiltruje → počet MAC v té doméně se nikdy
neporovná. Změřeno agentem revize na `runs/migration-pop1`
(ge-0/0/2 → et-0/0/8); nepřeměřeno.

### E9 – Internet služba v ne-VRF instanci

- `_vrf_instances` (`collectors/multicast.py:70-84`) bere jen `vrf`
  instance; MX tabulku virtual-routeru nestáhne, EVO (`instance all`) ano
  → asymetrie pre/post.
- Parser: BGP peery z masteru, BFD záměr z `instance.bfd` virtual-routeru,
  statika z RIB virtual-routeru (`parsers/core.py:1369-1371` vs
  `1550-1554`). Reprodukce: VR1 a master se stejnou /30 → obě služby
  vlastní master session, session VR1 skončí v NEZARAZENO.

### E10 – link-local BGP soused

Parser (`parsers/core.py:621-641`, `1380-1390`, `887`) klíčuje souseda jen
adresou a `local-interface` nečte; dva fe80::2 se různým `local-interface`
v jedné RI splynou v `unique()` a přepíšou se v `_parse_bfd`. Collector
nese `local-interface-name`. **J:** link-local BGP soused není v žádné
nahrávce (fe80 měřené 2026-08-19 se týká next-hopů statiky, ne BGP).

### E11 – linker: IRB unit == VLAN

`linker.py:55-60` bere číslo unitu IRB jako VLAN. irb.100 routující VLAN
200 → cizí L2 scope s VLAN/unitem 100 dostane cizí IRB. Konfigurační
pravda (`ServiceEntry.l3_interface`, schema 10) do `Selectors` nejde.

### E12 – EVPN bez NEZARAZENO

NEZARAZENO pokrývá jen bgp, statiku a bfd (`engine.py:459-598`), kolizní
značky jen bgp/bfd. Přepsaná nebo nevybraná EVPN položka beze stopy zmizí.

## Nízká závažnost / teoretické

- Matcher: scope s víc klíči se spáruje podle prvního v dictu; rozdělení
  (v4 → S1, v6 → S2) se nehlásí jako nejednoznačné.
- ASM (*,G) v `routes_for` (`checks/multicast.py:160-162`) a
  `_cmulticast_entry` (`collectors/multicast.py:704-714`) – první shoda
  vyhrává.
- evpn_mac: trunk IFL ve víc BD sečten pod první BD (jen popisek), víc BD
  s learn-vlan 0 by se sečetlo (neověřeno).
- Rename v `_aligned_baseline_data`: když nové jméno IFL = jiné IFL
  v téže baseline instanci (výměna portů na stejné platformě), dict
  přepíše.
- `engine._rib_instance` vs `core.rib_instance` se liší jen pro jméno RI
  obsahující `.inet`.

## Ověřeno jako bezpečné

- arp / nd výběr podle přesného jména unitu; `by_ip` v `arp_present` /
  `nd_present` jen uvnitř scopu.
- interfaces, optics, mpls_interface, isis_interface, igmp_group – klíč
  jméno rozhraní/unitu, unikátní na boxu; igmp jako seznam → set.
- routes – `table-name` nese instanci (`X.inet.0`), víc rt-entry pod
  prefixem se slévá (QNH fix); `(rib, prefix)` v selektoru i NEZARAZENO.
- evpn_vpws / evpn_instance / mvpn_instance / multicast_route – klíč jméno
  instance; per-VRF MX odpovědi nesou `multicast-instance`.
- VPWS SID 1000/2000 ve dvou instancích (PTX) – SID není klíč.
- Replay `call_key` (`raw/calls.py:62`) obsahuje instanci i rozhraní.
- Ping BGP/ARP vrstva – cíl per scope se svou RI.
- Matcher nikdy nepáruje při nejednoznačnosti; `_apply_manual` jen 1:1;
  `runs/pairing.py` klíč (node, port).
- Serializace snapshotu, inventory, výsledků – seznamy, ne slovníky.
- Popisky řádků nejsou nikde identitou pro dedup (view.py, engine).
- Rename ARP/ND/BFD/ESI mění jen pole `interface`, klíče ne.

## Ověření v laborce (odpovědi uživatele 2026-09-24)

1. BFD `extensive` u multihop session – **instanci nenese.** Multihop
   session dvou VRF na stejnou adresu collector nerozliší ani po vrstvě 2;
   zbývá je obě uchovat a ve vyhodnocení hlásit nejednoznačnost.
2. PIM dual-stack – **potvrzeno živě (PTX1-POP1, basic RPC, které collector
   volá).** Každá rodina má vlastní `pim-interface` se stejným jménem, v6 až
   za v4. Dnešní collector nad tou odpovědí vrátí pro et-0/0/1.0
   `fe80::e00:34ff:fe2e:e602`, uptime 14 s místo 10.1.0.4 / 87773 s. IPv6 PIM
   uživatele teď nezajímá → collector bere jen `ip-protocol-version` 4.
   Záznam: `lab-captures/2026-09-24/ptx1-pop1_pim_neighbors_basic.xml`.
3. IS-IS broadcast / L1+L2 – **mimo zájem** (F6 se nedělá).
4. ESI – **per-port ESI se v provozu očekává; oba případy potvrzeny živě
   (PTX1-POP1).**
   - Dvě instance, stejné ESI (ae0.4093 v EVPN-VLAN-AWARE-4093, ae0.4094
     v EVPN-VLAN-AWARE-POP1): dnešní collector vrátí jedinou položku
     s `interface: ae0.4094` – služba ae0.4093 ESI ztratí.
   - Jedna instance, dva IFL: `evpn-esi-num-local-intf` = 2, ale
     `evpn-esi-local-intf-information` vypisuje jen **jedno** jméno
     (ae0.4094), zatímco `evpn-esi-status` říká „Resolved by IFL ae0.4093“.
     Per-IFL stav a ESI nese jen `evpn-interface-status-table`
     (`evpn-interface-name`, `-esi`, `-mode`, `-status`).
   Záznamy: `lab-captures/2026-09-24/ptx1-pop1_evpn_esi_*.xml` (zkrácené
   o instance bez ESI).
5. ASM – **mimo zájem** (F4 se nedělá).
6. static + aggregate na stejném prefixu v jedné RIB – **ano, jde to**
   (F8 je reálný).
