# E-LAN `local`: globální bridge-domain / vlan (default-switch) — design

Datum: 2026-09-09

## Cíl

Služba „IPVPN + globální bridge-domain“ (laboratorní případ NGMVPN
receiver: IRB v MVPN instanci, access port v `bridge-domains` na MX resp.
`vlans` na EVO, tedy v instanci `default-switch`) se dnes do per-port
inventory nedostane vůbec: access port skončí jako `Unknown / layer2`
a IRB, která bydlí mimo filtrovaný port, se k němu nepřitáhne. V whole-box
inventory je IRB klasifikována správně (IPVPN / mvpn); chybí jen L2
polovina a její runtime vazba.

Výsledek vlny: access port je **E-LAN / `local`** se svým blokem v reportu
(MAC count domény i portu, stav/chybovost/provoz rozhraní, deaktivace),
blok stojí hned za blokem své IRB stejně jako u EVPN + IPVPN, a per-port
běh vidí obě poloviny služby.

Obě platformy (MX i EVO). Konfigurační i RPC ukázky pocházejí z laborky
(MX1-POP1, PTX1-POP1, 2026-09-09).

## Uzavřená rozhodnutí (brainstorm 2026-09-09, nerelitigovat)

- **Žádný nový collector.** `show bridge domain` / `show vlans`
  (`get-bridge-instance-information` / `get-vlan-information`) se
  nesbírá. Count výpis MAC tabulky, který collector `evpn_mac` už dnes
  volá na obou platformách (`get-bridge-mac-table` + `get-evpn-mac-table`
  s `<count/>` na MX, `get-mac-vrf-mac-table` s `<count/>` na EVO),
  instanci `default-switch` už obsahuje včetně názvu domény
  a per-interface počtů. Konfigurace dává členství a vazbu na IRB.
  Cena v produkci (rate-limit NETCONF) se nemění.
- **Subtype se jmenuje `local`** (ne `global`). Odlišnost od `vlan-aware`
  je chybějící EVPN/MPLS transport — L2 je lokální na boxu a opouští ho
  jen přes IRB. `global` popisuje umístění stanzy, které je navíc
  platformně různé (`bridge-domains` vs `vlans`). Budoucí lokálně
  spínaný VPWS dostane analogicky E-Line / `vpws-local`.
- **Přístup A: vlastní scope pro L2 půlku, vazba z konfigurace.**
  Zamítnuto B (řádky L2 přibalit do bloku IRB — port by zůstal Unknown,
  per-port filtr by IRB nepřitáhl, dva access porty jedné IRB by se
  slily) i C (nový service type — sahá do GUI, profilů a všech
  type-vázaných checků kvůli službě, která je sémanticky E-LAN).
- **Řádek „IRB interface“ na L2 bloku se nedělá.** U vlan-aware pochází
  z EVPN instance výpisu, který tu neexistuje. Stav IRB (admin/oper) je
  o blok výš v párovaném bloku IRB jako `Interface status irb.X`.
  Reprodukce by vyžadovala nový selektor (bump snapshot schématu) nebo
  speciální případ v enginu.
- **Bump inventory schématu na 10** (nový subtype + poprvé zapisované
  pole `l3_interface`). Snapshot schéma zůstává 13.
- **Raw retention + replay je samostatný spec** (rozhodnuto tamtéž):
  každý capture uloží config XML i všechny RPC odpovědi a příkaz
  `mig-validate upgrade <run>` z nich přegeneruje inventory i snapshot
  aktuálním nástrojem. Řeší produkční problém s bumpy schémat u baseline,
  kterou už nejde znovu sejmout. Není součástí této vlny.

## 1. Parser a inventory

Parser už dnes čte globální kontejnery `bridge-domains` (MX) a `vlans`
(EVO) do `global_l2_domains` a používá je pro IRB stranu (`bridge_domain`,
`customer_vlan`, `l2_interface` na IRB záznamu). Mění se dvě věci.

**Vyhledání domény pro access port.** `_find_interface_bridge_domains`
vrací u unitu bez routing-instance prázdný seznam. Přibývá druhá větev:
bez instance se hledá v `global_l2_domains` **jen podle explicitního
členství** (`interface/name` domény obsahuje jméno unitu nebo fyzického
portu). Žádný fallback podle překryvu VLAN — `default-switch` na EVO nese
i `default` (VLAN 1) a hádání trunku by přivěsilo náhodné porty.

**Klasifikace.** V `_detect_service` přibývá větev těsně před stávající
EVPN E-LAN větví:

    rozhraní je L2 (family bridge / ethernet-switching, resp. L2
    encapsulation) AND nemá routing-instance AND má ≥1 globální doménu
    ⇒ ("E-LAN", "local", "high",
       ["Rozhraní je členem globální bridge-domain / vlan (default-switch),
         bez EVPN instance."])

Stávající fallback `Unknown / layer2` zůstává pro L2 porty bez domény.

**Pole záznamu.** `bridge_domain`, `customer_vlan` a `l3_interface` se
naplní stávajícím kódem (`routing_interface` domény = `routing-interface`
na MX, `l3-interface` na EVO). `routing_instance` zůstává `null`.
Per-port filtr v `runs/services.py` se nemění — IRB se přitáhne přes
`l3_interface`, které bylo dosud prázdné.

**Aktivita domény.** `BridgeDomain` dostává `active: bool`, čtené stejným
testem `_is_inactive` (prochází předky), takže `bridge-domains inactive`
resp. `vlans inactive` označí všechny domény kontejneru. U záznamu E-LAN
`local` nese `routing_instance_active` příznak domény — doména je
kontejner služby stejně, jako je RI kontejner u EVPN. Záznam IRB tím
příznakem dotčen není (config ji nedeaktivuje).

**Inventory schéma 10.** Changelog: subtype E-LAN `local`; pole
`l3_interface` (irb protějšky domén unitu) se poprvé zapisuje do YAML
a do `ServiceEntry` (s výchozí prázdnou hodnotou při čtení vnořené
inventory ve snapshotu). Stará inventory se odmítá jako dosud; důvod:
pravidlo párování `description+service_type+service_subtype` by starý
záznam (`Unknown/layer2`) vůbec nedostalo do scope.

`irb.2` / `irb.10` žádnou změnu klasifikace nepotřebují: whole-box
inventory je dnes klasifikuje IPVPN / mvpn; v per-port běhu chyběly
jen kvůli prázdnému `l3_interface`.

## 2. Scope a vazba L2 ↔ IRB

Builder scope se strukturálně nemění: záznam E-LAN `local` je service
scope s `interfaces`, `bridge_domains`, `vlans`; `routing_instances`
zůstává prázdné. `default-switch` se do `routing_instances` **nedává**
— matcher má pravidlo `routing_instance+service_type` a dvě lokální
domény jednoho boxu by vypadaly jako táž služba.

**Výběr faktů** (`Scope.select`): `evpn_mac` se dnes vybírá podle
`routing_instances`. Přibývá pravidlo: scope E-LAN se subtype `local`
dostane položku `default-switch`. Relevance filtr checku (`_service_units`:
VLAN + interfaces scope) pak omezí řádky na tuto doménu, ostatní lokální
domény boxu do bloku neprosáknou.

**Vazba** (`scoping/linker.py`): vedle stávajícího zdroje z faktů
`evpn_instance` přibývá konfigurační zdroj pro `local` scope:

- kandidát L3 = scope typu Internet/IPVPN, jehož selektor `l2_interfaces`
  (seznam access portů IRB z inventory, ve snapshotu od schématu 12)
  obsahuje rozhraní L2 scope;
- `irb_interface` = rozhraní L3 scope; `l3_context` = jeho RI nebo
  `master` (engine přepisuje na `inet.0`); `l2_instance` = `default-switch`;
- právě jeden kandidát, jinak žádná vazba (stejné pravidlo jako dnes);
  N L2 : 1 L3 zůstává (dva access porty jedné IRB = dvě vazby).

Engine (`_link_payloads`, `_reorder_linked`, label „(L2 část)“, IRB
z linku pro relevance filtr) se používá beze změny.

**Snapshot schéma**: bez bumpu. Tvar scope i faktů se nemění. Proti staré
baseline se nový `local` scope ukáže jako nespárovaný na subject straně
— viditelné, ne tiše špatné.

Per-port běh `ge-0/0/2` pak v inventory nese `ge-0/0/2.10`,
`ge-0/0/2.12`, `irb.10`, `irb.2`; bloky IRB dostanou plné IPVPN
a multicast checky a za každým následuje jeho L2 blok.

## 3. Collector a checky

**Collector `evpn_mac`**: `default-switch` se vyřazuje ze
`SYSTEM_INSTANCES` a zpracovává se jako každá instance. Nahrané fixtures
z laborky ho na obou platformách už obsahují. Položka `default` (VLAN 1)
na EVO nemá žádné počty, takže neemituje řádek. Přejmenování rozhraní
mezi boxy (`ge-0/0/2.12` → `et-0/0/8.12`) řeší engine pozičním
mappingem klíčů `evpn_mac` už dnes.

Checky na bloku E-LAN `local`:

| Check | Vazba | Poznámka |
|---|---|---|
| `evpn_mac_count` | beze změny (E-LAN) | řádky „`<doména>` MAC count“ a „Interface `<port>` MAC count“ omezené relevance filtrem; label se mění z „EVPN MAC count“ na **„MAC count“** pro všechny E-LAN subtypy, id zůstává (profily se nemění) |
| `interface_state`, `interface_errors`, `interface_traffic`, `traffic_ceased` | beze změny | vážou se na každý service scope |
| `evpn_esi_status`, `evpn_instance_status` | `excluded_subtypes = {"local"}` | jinak by dávaly SKIP „bez dat“ na každém lokálním bloku |
| `deactivation_state` | beze změny | přes příznak domény ze sekce 1 |

Bez řádku „IRB interface“ (uzavřené rozhodnutí).

## 4. Deaktivace a okrajové případy

- **Deaktivovaný celý kontejner** (laboratorní případ): všechny záznamy
  E-LAN `local` mají `routing_instance_active: false`; check hlásí BROKEN
  (baseline běžela), PASS se značkou (obě off), RECOVERED (obnoveno).
  Ostatní checky na deaktivovaném scope skipují jako dosud.
- **Deaktivovaná jediná doména**: táž cesta, jen její záznamy.
- **Text důvodu**: `Scope.deactivation_reason` dnes tiskne „RI
  deactivated“. U scope bez RI se subtype `local` tiskne
  **„bridge-domain deactivated“**.
- **IRB s deaktivovanou doménou**: záznam IRB zůstává aktivní (config
  ji nedeaktivuje) a její blok hlásí, co box naměří (typicky oper down,
  multicast checky BROKEN). Stav se nededukuje. Vazba se vytvoří, L2 blok
  s „bridge-domain deactivated“ stojí přímo pod padajícím blokem IRB.
- **Dva access porty v jedné doméně**: dva `local` scopy, obě vazby na
  jednu IRB.
- **Lokální doména bez IRB** (čistá L2 mezi dvěma porty): E-LAN `local`
  s MAC count a interface řádky, bez vazby.
- **Trunk s `vlan-id-list` v globální doméně**: jen explicitní členství,
  nikdy překryv VLAN.
- **EVO `default` VLAN 1**: bez členů nic neklasifikuje; s členem by šlo
  o běžný E-LAN `local`.
- **Různé názvy domén přes swap** (`BD-…` na MX, `VL-…` na PTX): klíče
  řádků jsou VLAN a rozhraní, název je jen label — porovnání s baseline
  se nemění.

## 5. Report a GUI

- Textový report: blok IRB drží poznámku „L2: ge-0/0/2.12“ v hlavičce
  (stávající); access port má vlastní blok „E-LAN / local (L2 část)“
  hned za blokem IRB s řádky MAC count, stav/chybovost/provoz rozhraní,
  deaktivace.
- GUI: mapa subtypů neexistuje, filtr typů E-LAN už má — bez změny
  front-endu. Porty přestanou být v parse-services výstupu
  neklasifikované. Po merge restartovat laboratorní GUI.
- Dokumentace: stránky parseru a modelů (`docs/cs`, `docs/en`) dostanou
  subtype `local` a pole `l3_interface`.

## 6. Testy, fixtures, ověření

- **Parser** (MX i EVO, přesné stanzy z laborky, aktivní i `inactive`):
  access port ⇒ E-LAN `local` s `bridge_domain`, `customer_vlan`,
  `l3_interface`; IRB má `l2_interface` jako dosud; L2 port bez domény
  zůstává Unknown/layer2; trunk s vlan-id-list se překryvem nepřiřadí;
  příznak domény končí v `routing_instance_active`. Per-port filtr:
  inventory `ge-0/0/2` obsahuje `irb.2` a `irb.10`.
- **Inventory schéma 10**: test odmítnutí staré verze, round-trip
  `l3_interface` přes YAML. Laboratorní inventory fixtures obou boxů
  (`172.20.20.4.yml`, `172.20.20.5.yml`) se **přegenerují z laborky**,
  ne ručně — konformní test pak jede reálné collectory nad reálným XML
  proti inventory s `local` scopy.
- **Collector**: test zamykající vyřazení `default-switch`
  (`tests/collectors/test_evpn.py`) se obrací. Nové RPC nahrávky nejsou
  potřeba.
- **Linker**: konfigurační vazba `local` scope; dvě kandidátní IRB ⇒ bez
  vazby; dva porty na jedné IRB ⇒ dvě vazby; EVPN vazby beze změny.
- **Checky**: MAC count na `local` scope ukazuje jen svou VLAN
  a rozhraní; ESI a instance check se na `local` neaplikují; důvod
  deaktivace „bridge-domain deactivated“; přejmenovaný label „MAC count“
  ve stávajících testech. Syntetický snapshot v `tests/conftest.py`
  (`_facts_for`) dostane větev `local` (`evpn_mac` pod `default-switch`).
- **Mutanty**: implementátor každé tvrzení o zabitém mutantu ověří
  spuštěním mutanta po přegenerování fixtures, ne docstringem.
- **Laborka**: per-port běhy `ge-0/0/2` (MX1-POP1) a `et-0/0/8`
  (PTX1-POP1) s doménami aktivními a poté deaktivovanými, porovnání
  pre/post reportů — vizuální kontrola uživatele.
