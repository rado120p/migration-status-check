# `mx_parser.py` a `evo_parser.py` — parsery konfigurace

Dva tenké skripty v kořeni repozitáře, spouští se přímo:

```bash
.venv/bin/python mx_parser.py  172.20.20.4 -o 172.20.20.4.yml
.venv/bin/python evo_parser.py 172.20.20.5 -o 172.20.20.5.yml
```

| skript | zařízení |
|---|---|
| `mx_parser.py` | MX — klasický Junos |
| `evo_parser.py` | ACX / PTX — Junos OS Evolved |

Vyrábějí **inventory**: YAML se seznamem rozhraní a služeb, které na nich běží. Validator ho
konzumuje přes `--inventory` a uloží si ho do snapshotu.

---

## Vztah k validatoru (AR‑8, sjednoceno ve fázi 4)

**Implementace se od fáze 4 přestěhovala do balíčku** — žije v
`migration_validator/parsers/`:

| soubor | co v něm je |
|---|---|
| `parsers/core.py` | sdílené jádro: datové modely, XML pomocné funkce, CLI, připojení, zápis YAML, `main()` |
| `parsers/mx.py` | `JunosServiceParser` — specifika MX |
| `parsers/evo.py` | `JunosEvoAcxServiceParser` — specifika ACX/PTX |
| `parsers/__init__.py` | `parser_for_platform(platform)` — vrátí **třídu** parseru pro `junos`/`junos-evo` |

`mx_parser.py` a `evo_parser.py` v kořeni repozitáře jsou teď **tenké wrappery** — jen
naimportují `main()` a příslušnou třídu a zavolají `main(parser_cls=...)`. **CLI, přepínače,
výstupní formát i návratové kódy se nezměnily** — kdo skripty volal předtím, může beze změny
dál (viz [`CLI parserů`](#cli-parserů) níže).

Nový spotřebitel sjednocených parserů je `mig-validate capture --run ... --parse-services` —
místo ručního spuštění `mx_parser.py`/`evo_parser.py` a `--inventory` si `capture` v `--run`
režimu umí inventory vyrobit samo. Volá `parser_for_platform()` podle platformy zjištěné při
připojení a rovnou zapíše výsledek na místo, kde ho čeká `RunStore.inventory_path()`
(`runs/<nazev>/inventory_<node>_<port>.yml`). Detaily CLI a pravidla, kdy se generuje a kdy
ne (existující soubor se nikdy nepřepisuje), jsou v [../README.md](../README.md#3a-run-management---run).

Prakticky to znamená: **validator na parsery nezávisí za běhu** mimo `--parse-services` —
`--inventory` čte jen hotový YAML soubor a ten se dá klidně napsat i ručně.

---

## Průběh

```
main()
 ├─ parse_arguments()            argparse
 ├─ resolve_connection_options() klíč vs. heslo, případně dotaz na passphrase
 ├─ build_device()               PyEZ Device (gather_facts=False, auto_probe=timeout)
 ├─ retrieve_configuration()     jedno get_config RPC, committed + inherit
 ├─ JunosServiceParser(...).parse()
 ├─ create_yaml_data()
 └─ write_yaml()
```

`retrieve_configuration()` bere **jedním filtrem** `interfaces`, `routing-options`,
`routing-instances`, `protocols`, `bridge-domains`, `vlans` a `switch-options`, aby parser
viděl vazby mezi nimi naráz. `inherit` znamená, že se aplikují `apply-groups`.

**`routing-options` je ve filtru kvůli statickým routám globální instance.** Bez něj by
parser viděl jen statiky uvnitř `routing-instances` a routy v default instanci by z inventory
tiše zmizely — služba by pak vypadala, že žádný záměr nemá.

---

## Klasifikace služeb

`JunosServiceParser.parse()` (v EVO variantě `JunosEvoAcxServiceParser`) jede v pořadí:

1. `_parse_routing_instances()` — instance, jejich typ, protokoly, RD/RT, bridge domény,
   VLAN, BGP neighbory **a BFD záměr instance**;
2. `_parse_static_routes()` — statiky z globálních `routing-options` i ze všech instancí;
3. `_parse_default_bgp_neighbors()` — top-level `protocols bgp` patří do default instance
   (naplní i `self.default_bfd`);
4. `_parse_global_l2circuits()`, `_parse_global_connections()` — E-Line mimo instance;
5. `_parse_interfaces()` → `_build_interface_config()` — rozhraní, family, VLAN, adresy,
   virtual-gateway;
6. `_classify_interface()` → `_detect_service()` — vlastní rozhodnutí o typu služby;
7. `_assign_bgp_neighbors()` — přiřazení peerů ke službám podle shody se subnetem rozhraní;
8. `_assign_static_routes()` — přiřazení statik podle next-hopu **a** shody RIB;
9. `_assign_bfd()` — přiřazení BFD záměru ke službám.

**Pořadí posledních tří kroků není libovolné.** `_assign_bfd()` čte už naplněné
`service.bgp_neighbor`; spuštěné dřív než `_assign_bgp_neighbors()` by tiše nepřiřadilo nic —
seznam peerů by byl prázdný a výsledek „služba bez BFD" je legitimní stav, takže by na to
nemusel upozornit žádný test.

`_detect_service()` zkouší v pořadí: **IPVPN → E-Line VPWS → E-Line CCC → E-LAN VPLS →
E-LAN EVPN → Core → Internet → Layer 1 → nerozpoznané L2.** Pořadí je podstatné: dřívější
pravidlo vyhrává.

Každý záznam nese `detection_confidence` (`high` / `medium` / `low`) a `detection_reason`
(seznam vět, proč byl typ zvolen). Validator tato pole **nepoužívá**, ale při ladění
nespárovaných služeb jsou v YAML k nezaplacení.

`_should_ignore_interface()` vypustí z výstupu jen doopravdy prázdná rozhraní (bez
description, family, VLAN, adres, encapsulation a bez instance) — ne rozhraní jen proto, že
typ vyšel `Unknown`.

---

## Statické routy: normalizace jména RIB

Konfigurace **není mezi rodinami symetrická**:

| rodina | kde v konfiguraci leží |
|---|---|
| IPv4 | přímo pod `routing-options/static` |
| IPv6 | pod `routing-options/rib <jméno>.inet6.0/static` |

`show route` ale v poli `table-name` tenhle rozdíl nezná — vrací plné jméno RIB u obou rodin.
`_parse_static_routes()` proto asymetrii **zahladí hned na vstupu**: `static` bez `rib`
dostane odvozené jméno (`inet.0` v globální instanci, `<instance>.inet.0` v pojmenované),
`rib` si nese jméno vlastní. Dál se rozdíl nešíří a check porovnává jméno z konfigurace
přímo se jménem z RPC, bez jediné převodní tabulky.

`_assign_static_routes()` přiřadí routu službě, jen když platí **obě** podmínky současně:
next-hop leží v subnetu rozhraní **a** RIB patří téže routing-instanci
(`rib_instance(route.rib) == service.routing_instance`). Bez druhé podmínky by next-hop, který
náhodou padne do subnetu rozhraní v jiné VRF, sedl na špatnou službu.

Routa, která nesedne na žádnou službu (typicky `mgmt_junos.inet.0 0.0.0.0/0` přes `fxp0.0`),
v inventory nikde není. Ve výsledku validace se objeví v `unassigned.static_routes` —
viz [../reference.md](../reference.md#5-formát-výsledku).

---

## Statické routy: per-hop next-hopy a agregáty (QNH, schema 6)

`StaticRoute.next_hops` je od schema 6 seznam **per-hop záznamů**, ne plochý seznam next-hop
adres: `{"to", "interface", "qualified", "active"}`. Důvod je qualified-next-hop
(`qualified-next-hop`) — na rozdíl od holého `next-hop` jde deaktivovat **individuálně**
(ověřeno na laborce 2026-08-19: `inactive` sedí přímo na jeho uzlu), takže routa může mít
část next-hopů živých a část vypnutých zároveň. Plochý seznam adres by tohle nešlo vyjádřit;
proto nese aktivitu hop, ne routa.

`_parse_next_hops()`:

- holý `next-hop` vydá `{"to": adresa, "interface": None, "qualified": False, "active": True}`
  — deaktivovat ho jde jen jako celou routu (`route_type`/route-level `active`), takže hop sám
  je vždy aktivní;
- `qualified-next-hop` vydá navíc `interface` (pokud ho stanza nese) a vlastní `active`
  (`not self._is_inactive(qnh_node)`); protože `_is_inactive` chodí po předcích, hop pod
  deaktivovanou routou vyjde neaktivní taky;
- `discard`, `reject` a next-table next-hop (adresa tabulky místo IP) žádný hop nevydávají —
  nemají `to`, takže je není co porovnávat s next-hopem v routovací tabulce.

**Mapování na službu má precedenci, ne fallback** (`_route_matches_service()`): hop s
`interface` se mapuje **jen** podle jména rozhraní, subnet adresy se u něj vůbec nezkouší.
Bez toho by link-local next-hop (`fe80::...`), nakonfigurovaný typicky na víc rozhraních
najednou (změřeno na `et-0/0/8.13`, 2026-08-19), rozstřelil routu i na služby, kterých se
netýká. Hop bez `interface`, jehož `to` navíc není platná IP adresa, se čte jako jméno
rozhraní místo adresy (měření z vlny 10) — i pak se hledá jen shoda jména, žádný subnet.
Teprve hop bez `interface` a s platnou IP adresou zkouší subnet rozhraní. **Matchují se i
neaktivní hopy** — routa, jejíž jediný next-hop operátor deaktivoval, musí zůstat u své
služby, jinak by spadla do `unassigned.static_routes` přesně ve chvíli, kdy má report hlásit
nedokončenou migraci na úrovni next-hopu, ne díru v inventáři.

**Agregáty** (`route_type == "aggregate"`) parsuje tatáž `_static_routes_under()` — containery
`./aggregate` vedle `./static`, globálně pod `routing-options` i uvnitř každé
`routing-instances/instance`, s týmž odvozením jména RIB. Agregát nemá next-hopy
(`next_hops == []`) — v konfiguraci nese `discard`/`reject`, ne next-hop, a ty se stejně jako
u statiky neparsují. Mapování na službu proto neběží přes next-hop, ale přes RIB samotnou:
VRF (`rib_instance(route.rib)` není `None`) mapuje na **všechny** služby té instance, globální
RIB (`rib_instance` je `None`) mapuje **jen** na Core `lo0.0` — ne na libovolné tranzitní
rozhraní (rozhodnutí uživatele 2026-08-19). Zápis do `detection_reason` má vlastní větu
(„Agregátní routa patří této službě: …"), oddělenou od věty pro statiky, protože jde
o odlišný mechanismus přiřazení, ne o variantu téhož.

---

## BFD: dědění hierarchií BGP

`_parse_bfd()` čte `bfd-liveness-detection` na třech úrovních a specifičtější přepisuje
obecnější:

```
protocols bgp                      →  source: bgp
  group <jméno>                    →  source: group
    neighbor <adresa>              →  source: neighbor
```

Dvě věci, na kterých to stojí:

- **Přepisuje se celá hodnota, ne položka po položce.** Soused s vlastním `minimum-interval`
  si nedědí `multiplier` ze skupiny. Slévání po položkách by vyrobilo záměr, který v žádné
  úrovni konfigurace takhle nestojí. Nesou to **dva** řádky tvaru `… or …` — jeden u souseda
  (`_bfd_values(…) or inherited`) a jeden u skupiny (`_bfd_values(…) or protocol_level`) —
  a každý potřebuje vlastní měřítko, protože slévání po položkách může přežít na jednom
  z nich a na druhém ne. Měří je tři testy `test_partial_override_of_*`: soused nad skupinou,
  soused nad `protocols bgp` a skupina nad `protocols bgp`. Soused (nebo skupina), který
  nastavuje *oba* údaje, ten rozdíl neuvidí, protože obě implementace u něj dají totéž.
- **Junosí `inherit` tuhle hierarchii nerozbaluje.** Rozbaluje `apply-groups`, ne hierarchii
  protokolu. Ověřeno proti laborce 2026‑07‑29, kdy skupina `CPE14` nesla BFD a její sousedé ho
  neměli ani v konfiguraci stažené s `inherit` — kdyby se parser na `inherit` spolehl, oba
  peery skupiny (včetně toho IPv6) by v inventory zůstaly bez BFD.

Pole `source` v YAML říká, na které úrovni byl záměr nalezen — je to jediná stopa po tom, že
řádek v reportu pochází ze skupiny, a ne od souseda.

Služba bez routing-instance sahá do `self.default_bfd` (top-level `protocols bgp`), služba
v instanci do `instance.bfd`.

---

## Deaktivovaná konfigurace nevyrábí záměr

`deactivate` je standardní junosí idiom pro vyřazení konfigurace při migraci — stanza
v souboru zůstane, ale zařízení ji nepoužívá a v XML nese `inactive="inactive"`. Tady je
potřeba rozlišit dvě různé věci, které se v inventory dějí při deaktivaci nezávisle na sobě:

- **Služba se z inventory nikdy nevypouští.** Deaktivovaná služba se pořád musí zmigrovat,
  takže zmizet z výstupu by byla chyba, ne oprava. Místo toho nese dva příznaky —
  `routing_instance_active` a `interface_active` (schema 4, AR‑20/AR‑21) — které říkají, jestli
  je deaktivovaná routing instance, rozhraní, nebo obojí. Validator ty příznaky čte a takovou
  službu SKIPne s důvodem `interface deactivated` / `RI deactivated` (obojí `RI + interface
  deactivated`), místo aby nad ní počítal FAIL/WARN, jako by běžela.
- **Záměr (statická routa, BFD relace, BGP soused) se z deaktivovaného kontejneru pořád
  vypouští úplně** — bez příznaku, beze stopy. Kdyby validator záměr přečetl, hlásil by
  `FAIL … neni v tabulce` nebo `FAIL … bez session` za něco, co operátor vypnul úmyslně, a
  falešný rozpor je jediný výstup, který podrývá celý smysl porovnávání konfigurace se
  skutečností.

`_is_inactive()` rozpozná tři podoby: `inactive="inactive"`, `active="false"` i namespacovaný
YANG atribut `active`. Deaktivace se navíc dědí z předků dolů (AR‑19) — `deactivate
routing-instances` tedy zabírá na všechny `instance` pod sebou, `deactivate protocols` na
`bgp`/`group`/`neighbor` pod sebou atd., i když sám vnořený uzel atribut `inactive` nenese.
Bez dědění by si úroveň kontejneru a úroveň položky odporovaly: deaktivovat jednu VRF statiky
vypustí, deaktivovat **všechny** VRF neudělá nic.

Kontroluje se (s děděním z předků) na těchto uzlech:

| uzel | kde | co by jinak vzniklo |
|---|---|---|
| `instance` | `_parse_static_routes()` přeskočí; `_parse_routing_instances()` ji zapíše s `routing_instance_active: false` | statiky vyřazené VRF |
| `routing-options` | `_parse_static_routes()`, **obě** smyčky (globální i v instanci) | všechny statiky té úrovně |
| `rib` | `_static_routes_under()` | statiky celé tabulky (typicky IPv6) |
| `static` | `_static_routes_under()`, jedno místo pro `static` pod `routing-options` i pod `rib` | statiky toho kontejneru |
| `route` | `_static_routes_under()` | jedna statika |
| `group` | `_parse_bfd()` | BFD záměr celé skupiny |
| `neighbor` | `_parse_bfd()`, `_parse_bgp_neighbors()` | BGP peer a jeho BFD |
| `bfd-liveness-detection` | `_bfd_node()` | BFD záměr té úrovně |

Díky dědění z předků pokrývá řádek `neighbor` i deaktivovaný `protocols`/`bgp` o úroveň výš
(ancestor walk z `neighbor` na ně narazí cestou nahoru) a řádek `instance` pokrývá i
deaktivovaný kontejner `routing-instances` jako celek — samostatné řádky pro tyhle kontejnery
proto nejsou potřeba.

**U `bfd-liveness-detection` má přeskočení ještě druhý efekt:** `_bfd_node()` vrátí `None`,
takže dědění pokračuje o úroveň výš — soused s deaktivovaným BFD spadne pod pravidlo skupiny.
Přesně to udělá i Junos, takže to není zjednodušení, ale shoda se zařízením.

**Kvůli tomuhle je `_bfd_node()` metoda, ne funkce modulu.** Potřebuje `self._is_inactive()`,
a duplikovat kontrolu atributu inline by znamenalo mít pravidlo „co je neaktivní" na dvou
místech.

**Deaktivace rozhraní (`interfaces`, `interface`, `unit`) do tohohle stromu nezasahuje.**
`<interfaces>` a `<routing-instances>`/`<routing-options>` jsou v XML oddělené podstromy, takže
ancestor walk z uzlu `neighbor` nebo `route` na deaktivované rozhraní nikdy nenarazí. Deaktivovat
jen rozhraní tedy služba se statikami/BGP sousedy nadále nese v záměru — a je to správně:
Junos taky nepřestane instalovat routu jen proto, že rozhraní, na kterém sedí navázaná služba,
je vypnuté, dokud je vypnutá jen ta jednotka. Validator tenhle případ řeší jinak: přes
`interface_active`, který službu SKIPne celou (viz výš), takže manufakturovaný FAIL na routě
stejně nevznikne — jen z jiného mechanismu než dědění deaktivace do záměru.

Hlubší úrovně (deaktivovaná jednotlivá `route`, `bfd-liveness-detection` nebo `neighbor`) se
záměru pořád vypouští beze stopy — bez vlastního příznaku jako `interface_active`. Rozšířit
příznaky i na tuhle úroveň je vědomě odložené, mimo rozsah téhle vlny.

**Výjimka od schema 6: `qualified-next-hop` má vlastní `active`.** Na rozdíl od `route` výš
tenhle uzel *se* svojí deaktivací ven pouští stopu — hop, ne jen routa jako celek, protože
qualified-next-hop jde deaktivovat nezávisle na sourozencích ve stejné routě (routa se dvěma
next-hopy může mít jeden živý a jeden vypnutý). Bez per-hop `active` by check neměl jak
rozlišit „next-hop se přestal používat" od „routa je pořád v tabulce, ale s jiným next-hopem" —
viz sekci „Statické routy: per-hop next-hopy a agregáty" výš. Holý `next-hop` výjimku nemá — nejde deaktivovat samostatně, jen jako celá routa.

---

## Multicast: IGMP záměr, PIM záměr, `mvpn_site`, `inet.2` → lo0.0, IRB `l2_interface` (vlna 2026-09-02, PIM/mvpn_site 2026-09-07)

**IGMP záměr** se čte z `protocols igmp interface X` — globálně i uvnitř `routing-instances/
instance/protocols/igmp` (`_parse_igmp_interfaces()`). XPath je sjednocený přes obě větve
(`|`), protože záměr se váže na rozhraní, ne na instanci — kdyby se místo toho použilo
`RoutingInstance.protocols`, dostalo by IGMP záměr každé rozhraní instance, ne jen to pod
`igmp interface`. Rozhraní pod tímhle záměrem jsou v `self.igmp_interfaces`.

**PIM záměr** (spec 2026-09-07) se čte stejným způsobem: `protocols pim interface X`,
globálně i uvnitř `routing-instances/instance/protocols/pim` (`_parse_pim_interfaces()`),
XPath sjednocený stejně přes `|`. Rozhraní jsou v `self.pim_interfaces` — je to jediný
zdroj per-rozhraní PIM záměru pro detekci subtype `mvpn`; instance-level `"pim"`, které
`RoutingInstance.protocols` (`child_names(./protocols/*)`) rozsévá na všechna rozhraní
instance, na detekci subtype nestačí (gate PIM neighbor checku na Core tranzitu se
nemění a dál čte instance-level `"pim"`).

**Subtypy `multicast` a `mvpn`.** Detekce služby (`_detect_service`):

- Internet rozhraní v `self.igmp_interfaces` → `("Internet", "multicast", "high", …)`.
- IPVPN rozhraní, jehož instance má `protocols mvpn`, **a** rozhraní je v
  `self.igmp_interfaces` nebo `self.pim_interfaces` → `("IPVPN", "mvpn", "high", …)`
  (`_ipvpn_subtype()`). Reason text vyjmenuje nalezené záměry, např. „Rozhraní je pod
  protocols pim a instance má protocols mvpn — MVPN site." (u obou záměrů zároveň
  `protocols igmp a pim`). Bez IGMP ani PIM záměru na rozhraní, nebo bez `protocols mvpn`
  v instanci, zůstává IPVPN subtype, jaký byl předtím (plain IPVPN, žádný multicast).
  Subtype `mvpn` nahrazuje dřívější IGMP-only variantu (do 2026-09-07 jen s IGMP
  záměrem, jiné jméno subtype).

### `mvpn_site`

`RoutingInstance.mvpn_site: list[str]` (`_parse_mvpn_site()`) — role MVPN site
odvozená z `./protocols/mvpn` a `./provider-tunnel` uzlu instance, hodnoty z
`{"sender", "receiver"}`, seřazeno:

| konfigurace | `mvpn_site` |
|---|---|
| bez `protocols mvpn` | `[]` |
| `mvpn sender-site`, bez tunelu | `["sender"]` |
| `mvpn receiver-site` | `["receiver"]` |
| `provider-tunnel` + `mvpn sender-site` | `["sender"]` |
| `provider-tunnel` + `mvpn receiver-site` | `["receiver", "sender"]` |
| `provider-tunnel`, mvpn bez site klíčového slova | `["receiver", "sender"]` |
| mvpn bez site klíčového slova, bez tunelu | `["receiver"]` |

Pravidlo: `sender` ⇔ `sender-site` nebo `provider-tunnel`; `receiver` ⇔ `receiver-site`
nebo (`sender-site` není nakonfigurováno) — Junos default bez site klíčového slova je
obojí. `InterfaceService` i `ServiceEntry` dostanou `mvpn_site` zkopírované z instance
(prázdné mimo MVPN), `Selectors.mvpn_site` totéž — check `pim_join` z něj čte roli
sender-only site, ne z konfigurace přímo. `provider-tunnel` bez site klíčového slova dá
obě role — přijaté riziko: takový site může hostit receiver a tabulka to nerozliší.

`INVENTORY_SCHEMA_VERSION` 8 → **9** (nový klíč `mvpn_site`, přejmenovaný subtype). Staré
inventory se starým subtype jménem se na pre straně nepáruje přes subtype pravidlo —
stejný dopad jako při zavedení subtype 2026-09-02.

**Globální `inet.2` statiky patří Core lo0.0.** `ServiceInstance._matches_route()`: routa
s `route.rib == "inet.2"` patří `service.service_type == "Core" and service.interface ==
"lo0.0"` bez ohledu na next-hop — ne rozhraní, do jehož subnetu next-hop padne (obecné
pravidlo pro ostatní routy). `<RI>.inet.2` sem nespadá — `rib_instance("X.inet.2")` vrací
`"X"`, ne `None`, takže filtr `rib_instance(route.rib) != service.routing_instance` ho
zachytí dřív. Zdůvodnění: multicast RPF statiky patří routeru jako celku
(`core_multicast_forwarding` je čte z lo0.0 scopu a upstream porovnává s jejich `via`),
ne konkrétní tranzitní lince, kterou náhodou ukazuje next-hop.

**IRB `l2_interface`.** `ServiceEntry.l2_interface` (plurál pole je `Selectors.l2_interfaces`,
builder překládá) se plní z `_domains_routed_by()` — bridge-domains/vlans (globální i
uvnitř libovolné routing-instance), jejichž `routing-interface` (MX) / `l3-interface` (EVO)
je zrovna tenhle IRB. Funguje i napříč instancemi: `irb.4094` v `MGMT` dostane
`bridge_domain`/`customer_vlan` domény `BD-4094`, i když ta doména žije v jiné (EVPN)
instanci — to je záměr specu „IRB nese svou L2 stranu", je to N L2 : 1 L3 vztah. Pro
checky je pole inertní (nikdo ho zatím nečte pro branching), slouží jen jako poznámka
`L2: …` v hlavičce reportu.

---

## Kde se ty dva soubory liší

Rozdíl je soustředěný do detekce EVPN E-LAN a EVPN/VPLS instancí:

| | MX (`mx_parser.py`) | EVO (`evo_parser.py`) |
|---|---|---|
| VPLS | `instance-type vpls` nebo `protocols vpls` | navíc `virtual-switch` + `protocols vpls` |
| E-LAN subtype | `virtual-switch` + bridge domain → `vlan-aware`; `instance-type evpn` → `vlan-based` | nejdřív explicitní `service-type` (`vlan-aware` / `vlan-based` / `vlan-bundle`), pak `mac-vrf` podle počtu VLAN/domén, pak `virtual-switch` + `protocols evpn` |
| EVPN instance | `instance-type evpn` | `instance-type evpn` **nebo** `mac-vrf` |

Zbytek souboru (datové modely, XML pomocné funkce, CLI, připojení, zápis YAML) je shodný —
liší se jen jméno loggeru a texty v docstringu.

---

## CLI parserů

```
usage: mx_parser.py [-h] [--auth {key,password}] [-u USERNAME] [-k KEY_FILE]
                    [--ask-key-passphrase] [-p PORT] [--timeout TIMEOUT]
                    [-o OUTPUT] [--debug] hostname
```

| přepínač | default |
|---|---|
| `--auth` | `key` |
| `-u/--username` | `ansible` (v key režimu) |
| `-k/--key-file` | `~/.ssh/id_rsa` |
| `--ask-key-passphrase` | vyžádá passphrase klíče |
| `-p/--port` | `22` |
| `--timeout` | `30` |
| `-o/--output` | `<hostname>.yml` (nebezpečné znaky se nahradí `_`) |
| `--debug` | podrobné logování |

### Návratové kódy parserů

**Nejsou stejné jako u `mig-validate`** — parsery rozlišují druh selhání jemněji:

| kód | význam |
|---|---|
| `0` | v pořádku |
| `2` | přihlášení selhalo |
| `3` | timeout připojení |
| `4` | NETCONF odmítnut (`system services netconf ssh`) |
| `5` | jiná chyba připojení |
| `6` | chyba Junos RPC |
| `7` | chyba vstupu/výstupu nebo XML |
| `99` | neočekávaná chyba |
| `130` | přerušeno uživatelem |

---

## Výstupní formát

Ukázka je vygenerovaná z `tests/fixtures/172.20.20.5.yml`, ne psaná ručně. (Stejná služba
L3VPN-CPE13-NNI existuje i na `172.20.20.4.yml` na `ge-0/0/2.113`, ale po regeneraci proti
laborce v AR-29 je tam `ge-0/0/2` deaktivované — pro ukázku běžného, aktivního tvaru záznamu je
proto zdrojem `.5`, kde stejná služba běží na `et-0/0/8.113`.)

```yaml
schema_version: 6
device: 172.20.20.5
interfaces:
- interface: et-0/0/8.113
  description: L3VPN-CPE13-NNI
  service_type: IPVPN
  service_subtype: null
  ipv4_address:
  - 198.11.13.1/30
  ipv6_address:
  - 2001:db8:11:13::a/127
  virtual_gw_ipv4_address: []
  virtual_gw_ipv6_address: []
  routing_instance: L3VPN-CPE13-NNI
  routing_instance_active: true
  interface_active: true
  protocol:
  - inet
  - inet6
  - bgp
  - vrf
  bgp_neighbor:
  - 198.11.13.2
  - 2001:db8:11:13::b
  bgp_neighbor_inactive: []
  bridge_domain: []
  customer_vlan:
  - '113'
  static_route:
  - rib: L3VPN-CPE13-NNI.inet.0
    prefix: 172.26.1.0/29
    active: true
    route_type: static
    next_hops:
    - to: 198.11.13.2
      interface: null
      qualified: false
      active: true
  - rib: L3VPN-CPE13-NNI.inet6.0
    prefix: 2001:eeee::/64
    active: true
    route_type: static
    next_hops:
    - to: 2001:db8:11:13::b
      interface: null
      qualified: false
      active: true
  bfd:
  - peer: 198.11.13.2
    minimum_interval: 3000
    multiplier: 3
    source: neighbor
  detection_confidence: high
  detection_reason:
  - Rozhraní je přiřazeno do routing instance typu vrf.
  - 'BGP neighbor odpovídá subnetu rozhraní: 198.11.13.2, 2001:db8:11:13::b'
  - 'Statická routa odpovídá subnetu rozhraní: 172.26.1.0/29, 2001:eeee::/64'
```

`routing_instance_active` a `interface_active` (schema 4) říkají, jestli je deaktivovaná
routing instance nebo rozhraní té služby — viz sekci „Deaktivovaná konfigurace nevyrábí záměr"
výš. Zdravá, plně aktivní služba jako tahle má oba `true`.

**`static_route` je od schema 6 seznam per-hop záznamů, ne plochý `next_hop`.** Každý prvek
nese `route_type` (`static`/`aggregate`), route-level `active` a `next_hops` — u statiky
seznam hopů se svým vlastním `to`/`interface`/`qualified`/`active` (viz sekci „Statické routy: per-hop next-hopy a agregáty" výš), u agregátu prázdný seznam. Bez inventáře k ukázce agregátní routy — laboratorní služby
výš žádnou nemají namapovanou — ale tvar je stejný jako u statiky, jen s `route_type: aggregate`
a `next_hops: []`.

Adresy jsou rozdělené podle rodiny — `ipv4_address`/`ipv6_address` a
`virtual_gw_ipv4_address`/`virtual_gw_ipv6_address` — a nezávisle na obsahu se do YAML vždy
zapíše top-level klíč `schema_version: 6`. Validator jinou hodnotu `schema_version` **tvrdě
odmítne** (`models/inventory.py::load_inventory()`), místo aby starou inventory tiše přečetl
jako službu bez adres nebo bez záměru — viz [models.md](models.md#inventorypy--vstup-z-parserů).

Pořadí klíčů je pevné (`clean_service_dict()`) a `service_subtype` zůstane v YAML i s
hodnotou `null`, aby měly navazující skripty stabilní strukturu.

Které klíče validator opravdu čte, je popsáno v [models.md](models.md#inventorypy--vstup-z-parserů).

---

## `172.20.20.4.yml` a `172.20.20.5.yml` v kořeni

Vzorové výstupy z laboratorní topologie (MX a PTX), v gitu **sledované**.

Kopie, na kterých běží testy, žijí v **`tests/fixtures/`** (commity `26d8461` a `671615b` je
tam přesunuly právě proto, aby testy nezávisely na tom, co je zrovna v kořeni). Obojí jsou
doslovné výstupy parseru — **neupravujte je ručně**; po změně parseru nebo po zvýšení
`schema_version` se oba páry regenerují novým během parseru proti laborce a zkopírují do
`tests/fixtures/`.

**Nejsou to výstupy z uložených captureů v `runs/`, ale z živého běhu** — a u `.5` se to dá
poznat. `172.20.20.5.yml` bylo naposledy regenerováno v commitu `977783f` proti laborce **po**
přestavbě služby CPE14 na `ae0.15` / `irb.15`, zatímco
`runs/bfd-static-2026-07-29/cfg/172.20.20.5.*.xml` nese
`commit-localtime="2026-07-29 11:43:50 UTC"` — jeho obsah je tedy konfigurace k tomu commitu,
**před** přestavbou. (`runs/` je gitignorované, takže ty captureje s branchí neputují; leží
v pracovní kopii, ze které se běh dělal.)
Kdo ten capture přeparsuje offline, dostane inventory bez `ae0.15` a s `irb.15` bez
description — a je to rozdíl v laborce, ne v parseru. Pro `.4` je offline reparse captureu
s commitnutým YAML byte za bytem shodný, takže na něm jde změny parseru ověřovat přímo.
