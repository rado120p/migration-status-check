# `collectors/` — sběr operačního stavu

Soubory: `base.py`, `registry.py`, `all.py`, `interfaces.py`, `arp.py`, `nd.py`, `bgp.py`,
`evpn.py`, `routes.py`, `bfd.py`, `isis.py`, `ldp.py`, `pim.py`, `mpls.py`
a prázdný `__init__.py`.

Dvě pravidla, na kterých celá vrstva stojí:

1. **Collector nikdy neinterpretuje.** Vrací syrová strukturovaná data, nikdy
   `{"bgp_ok": true}`. Změní-li se kritérium, mění se check, ne sběr — a staré snapshoty
   zůstanou použitelné.
2. **Platformní rozdíly MX vs. EVO se řeší tady.** Navenek vrací obě platformy **stejné
   schéma**, takže žádný check neobsahuje `if platform == "evo"`.

Tvar dat, který collector musí dodržet, je závazný kontrakt —
viz [../architecture.md](../architecture.md), sekce „Fact-schéma je tvrdé rozhraní mezi
collectorem a checkem".

---

## `base.py` — abstraktní collector

```python
class Collector(ABC):
    name: str                     # zároveň klíč v facts
    platforms: tuple[str, ...]    # default: obě

    def rpc_name(self, platform) -> str          # povinné
    def rpc_names(self, platform) -> tuple[str]  # default: (rpc_name(),)
    def rpc_kwargs(self, platform) -> dict       # default: {}
    def rpc_calls(self, platform) -> tuple[tuple[str, dict]]  # default: rpc_names × rpc_kwargs
    def parse(self, xml, platform) -> Any        # povinné
    def collect(self, device, platform) -> Any   # šablona: RPC + parse
```

`collect()` je šablonová metoda a **každý druh selhání převádí na `CollectorError`**
s hláškou, která říká který collector, které RPC a co se stalo:

| selhání | hláška |
|---|---|
| nepodporovaná platforma | `collector 'X' nepodporuje platformu 'Y'` |
| RPC neexistuje na `device.rpc` | `collector 'X': RPC 'Y' neni dostupne - ...` |
| RPC vyhodí cokoliv | `collector 'X': RPC 'Y' selhalo - <typ>: <text>` |
| `parse()` vyhodí cokoliv | `collector 'X': parsovani selhalo - <typ>: <text>` |

`CollectorError` chytá `capture.py` — sběr pokračuje ostatními oblastmi.

**`rpc_names()` je pojistka pro nahrávání fixtures.** Collector s více RPC ji musí přepsat,
jinak `record` a `--record-raw` uloží jen první z nich a nahrané fixtures budou tiše
nekompletní. Takové jsou `EvpnMacCollector` na MX (dva různé tvary odpovědi) a od 2026-08-19
QNH i `RoutesCollector` (dvakrát totéž RPC s jiným `protocol`) — viz sekci
[„`routes.py`"](#routespy--statické-a-agregátní-routy-z-routovací-tabulky) níž. Autoritou pro
`record` je ve skutečnosti `rpc_calls()` (RPC jméno + kwargs dohromady, v pořadí volání);
`rpc_names()`/`rpc_kwargs()` zůstávají jako pohodlnější rozhraní pro collectory, jejichž volání
se liší jen jménem RPC, ne kwargs.

## `registry.py` — registr

`@register` dekorátor zapíše **instanci** třídy do modulového slovníku pod `cls.name`;
duplicitní jméno je `ValueError`. `all_collectors()` vrací seřazený seznam,
`collectors_for(platform)` z něj filtruje ty, které platformu podporují.

## `all.py` — naplnění registru

Importuje `arp`, `bfd`, `bgp`, `evpn`, `interfaces`, `nd`, `routes`, čímž se spustí jejich
`@register`.
Existuje jako samostatný modul (ne `__init__.py`), aby nevznikl cyklický import.

**Collector zapomenutý v tomhle souboru by tiše vypustil celou oblast** — checky nad ní by
vracely `SKIP` a nikde jinde by se to neprojevilo. Hlídá to
`tests/test_capture.py::test_every_area_is_registered_for_both_platforms`.

---

## `interfaces.py` — stav a countery rozhraní

RPC: `get_interface_information` s `extensive=True` (obě platformy).

Výstup: `{název: {admin_status, oper_status, input_pps, output_pps, input_errors,
output_errors, framing_errors}}` — a to **jak pro fyzická rozhraní, tak pro logické
jednotky**, obojí ve stejném slovníku.

Zjištění ověřená proti nahranému XML z laborky (24.2R1-S2.5 / 25.2R1.8-EVO):

- fyzická rozhraní nesou `traffic-statistics`, logické jednotky `transit-traffic-statistics`
  — `_rates()` proto zkusí obojí v tomhle pořadí;
- **logická jednotka nemá vlastní chybové countery** na žádné z platforem → plní se nulami;
- logická jednotka nemusí mít `oper-status` → dědí ho od fyzického rodiče, `admin_status`
  dědí vždy.

Používá se `input-pps` / `output-pps`, protože **Junos je počítá sám — je to rate, ne
kumulativní counter.** Není proto potřeba dvojité vzorkování ani čekací okno. Absolutní
byte countery se záměrně nesbírají: mezi dvěma boxy s různým uptime jsou nesrovnatelné.

Soubor navíc poskytuje pomocné funkce `_text()` a `_int()`, které importují ostatní
collectory. `_int()` snese i desetinný zápis a při nesmyslu vrátí 0.

## `arp.py` — ARP tabulka

RPC: `get_arp_table_information` s `no_resolve=True`.

Výstup: `[{ip, mac, interface, learned_via, routing_instance}]`. Záznamy bez IP nebo bez
rozhraní se zahazují.

ARP je **zároveň producent dat pro ping probe** — z něj se při `capture` odvozují cíle.

Poznámka ke `routing_instance`: ani MX, ani EVO ho v odpovědi neuvádějí
(`arp-table-entry-flags` nese jen `<none/>`), takže klíč zpravidla zůstane `None`. Ve
schématu je záměrně — kontrakt ho předepisuje a scope filtruje ARP podle `interface`,
ne podle instance.

Junos u záznamů naučených přes IRB připojí k názvu rozhraní v hranaté závorce L2
rozhraní, přes které se soused naučil (`irb.14[ ae0.14 ]`). `split_learned_via()`
(sdílená s `nd.py`) tenhle tvar rozdělí na `interface` (`irb.14`) a `learned_via`
(`ae0.14`) — neořezaný tvar by scope, který filtruje přes přesnou shodu jména,
vyřadil úplně a v reportu by u služby, která ARP má, stálo „žádný záznam".

## `nd.py` — ND tabulka (IPv6 sousedé)

RPC: `get_ipv6_nd_information`. Dvojče `arp.py` — z ND se stejně jako z ARP odvozují cíle
pingu, jen pro rodinu IPv6.

Výstup: `[{ip, mac, interface, state, learned_via}]`. Záznamy bez IP nebo bez rozhraní se
zahazují. Na rozdíl od `probes/ping.py` collector **záznamy nefiltruje** — link-local
sousedé i záznamy bez MAC se vrátí všechny; rozhodnutí, co je použitelný cíl pingu, patří
probe, protože závisí na konfiguraci služby, kterou collector nezná. `interface`/
`learned_via` se rozdělují stejným `split_learned_via()` jako v `arp.py`.

Ověřeno proti laborce: RPC i jména elementů jsou shodná na vMX i na EVO. MX obaluje texty
novými řádky, EVO ne — `_text()` (sdílené s `arp.py`/`interfaces.py`) to řeší stripováním.

## `bgp.py` — stav peerů a počty prefixů

RPC: `get_bgp_neighbor_information`. Použití `neighbor` místo `summary` varianty je
záměrné — summary neobsahuje počet **advertised** prefixů.

Výstup: `{peer_ip: {state, peer_as, routing_instance, ribs: {rib_name: {received, accepted,
advertised, active, suppressed}}}}`.

Tři věci ověřené proti laborce:

- **`peer-address` nese port** (`150.0.0.1+179` na MX, efemerní `150.0.0.1+57010` na EVO).
  `strip_port()` ho odřízne — bez toho by se peer nikdy nepotkal s `bgp_neighbor`
  z inventory.
- **Jeden peer může mít až 11 RIB** (`bgp.rtarget.0`, `inet.0`, `bgp.l3vpn.0`, ...) a počty
  se ukládají **za každou RIB zvlášť, ne sečtené**. Součtem by se IPv4 a IPv6 slily do
  jednoho čísla a pokles v `inet6.0` kompenzovaný nárůstem v `inet.0` by prošel bez
  povšimnutí. `checks/bgp.py::peer_family()` pak rodinu řádku odvozuje z **adresy peeru**,
  ne z názvu RIB (ten rodinu nemusí nést vůbec, např. `bgp.l3vpn.0`).
- `peer-cfg-rti` s hodnotou `master` / `default` / prázdnou se normalizuje na `None`,
  aby default instance nevypadala jako pojmenovaná VRF.

`peer_as` se převede na `int` jen když je to opravdu číslo, jinak `None`.

## `evpn.py` — E-Line a E-LAN

Tři collectory v jednom souboru. **Nejvíc platformní logiky v celém balíčku.**

Poznámka z implementace: plán uváděl RPC jména, která na žádné platformě neexistují
(`get_evpn_vpws_instance_information`, `get_mac_vrf_forwarding_mac_table`); správná jsou
ta níže, ověřená přes `| display xml rpc`.

### `EvpnVpwsCollector` (`evpn_vpws`)

RPC: `get_evpn_vpws_information`. Výstup
`{routing_instance: {interfaces: [{name, status, mode, local_sid, remote_sid}]}}`.

- **Klíčem je název instance, ne rozhraní** — název instance je při migraci stabilní,
  název portu ne. Collector nese **všechna** rozhraní instance, ne jen první.
- `status` u rozhraní je **stav rozhraní instance** (`Up`), ne stav vzdáleného PE
  (`Resolved`). Obě hodnoty v odpovědi existují a znamenají něco jiného; check porovnává
  proti `Up`, takže se emituje ta souměřitelná.
- `local_sid` i `remote_sid` mají tvar `{value, peers}`. `value` je číslo SID; `peers` je
  seznam `{esi, ipaddr, mode, role, status}` čtený z `evpn-vpws-sid-pe-status-table` —
  tuhle tabulku dřívější schéma vůbec nečetlo, takže `evpn_vpws_status` neuměl rozlišit
  konkrétního peera od druhé strany SID.

### `EvpnEsiCollector` (`evpn_esi`)

RPC: `get_evpn_instance_information` s **`extensive=True`**. Bez `extensive` vrátí
`show evpn instance` souhrn bez jediného ESI a collector by tiše vracel prázdno.

Výstup `{esi: {status, df_role, interface}}`.

- **ESI začínající `05:` se ignoruje.** Box si ho generuje sám (per-IRB, auto-derived) a
  nenese ani status, ani DF — v reportu by u každé L3-extended služby přibyl řádek bez
  vypovědní hodnoty.
- `status` je `evpn-esi-local-intf-status` (`Up/Forwarding`) — `evpn-esi-status` je proti
  tomu popisný text (`Resolved by IFL ae0.14`), který se nedá porovnávat.
- `df_role` je **IP adresa zvoleného DF**, ne role tohohle boxu. Určit „jsem DF?" by
  znamenalo interpretovat, a to collectoru nepatří.
- `interface` je **logická jednotka** (`ae0.14`, `irb.14`) — přesně to, co drží scope
  v selektorech. Specifikace tady předpokládala problém (Junos prý hlásí fyzický název);
  proti laborce se obava nepotvrdila, `evpn-esi-local-intf-name` vrací rovnou logickou
  jednotku. Pojistkou je conformance test `test_esi_interface_matches_a_scope`.

### `EvpnMacCollector` (`evpn_mac`)

Výstup `{routing_instance: {vlan_id: počet}}`.

**Platformní rozdíl v počtu RPC:**

| platforma | RPC |
|---|---|
| `junos` | `get_bridge_mac_table` (vlan-aware) **+** `get_evpn_mac_table` (vlan-based) |
| `junos-evo` | `get_mac_vrf_mac_table` (obojí naráz) |

Na MX je potřeba obě, protože každé vidí jiný typ instance. Na EVO `show evpn mac-table`
vůbec neexistuje, zatímco mac-vrf tabulka vrací obojí. Uvádějí se proto jen RPC, která na
dané platformě opravdu platí — selhání kteréhokoliv pak znamená skutečnou chybu, ne dotaz
na něco neznámého.

Collector přepisuje `collect()`, aby výsledky RPC **sloučil**. A dělá to přísně:
**selhání kteréhokoliv z RPC je chyba celého collectoru.** Vrátit částečná data jako `ok`
by znamenalo, že check porovná zkrácený počet MAC adres proti plnému baseline a vyhodnotí
to jako propad. Raději `SKIP` než tichý nesmysl.

`parse()` prochází **oba tvary XML** (`l2ald-*` na MX, `l2ng-l2ald-*` na EVO) v jednom
průchodu, takže nepotřebuje větev na platformu ani správný argument `platform` — stačí mu
XML, což drží testy jednoduché.

**Klíčem domény je VLAN id, ne její název** (`_normalise_domain()`):

- tutéž doménu pojmenuje MX `BD-313` a EVO `VL-313` → při klíčování názvem by check po
  migraci nenašel protějšek v baseline a místo porovnání počtu MAC by vypsal jen stav;
- **vlan-based instance vlastní doménu nemá** a kontrakt pro ni předepisuje `"-"`. MX to
  prozradí tím, že VLAN hlásí jako `none`, EVO tím, že doméně říká `VL-NONE`
  (`_is_no_domain()` chytá obojí: prefix `__` i suffix `NONE`). VLAN id ale obě platformy
  uvedou, takže podle něj samotného by vlan-based instance nesedly.

## `routes.py` — statické a agregátní routy z routovací tabulky

RPC: `get_route_information`, **dvakrát** za sebou — jednou s `{"protocol": "static"}`,
podruhé s `{"protocol": "aggregate"}` (obě platformy). Vzorem je `InterfacesCollector`
(`extensive` + `terse` — dvakrát totéž RPC jméno, jen s jinými kwargs), ne `EvpnMacCollector`
(ten volá dvě **různá** jména RPC se stejnými kwargs) — tady se místo tvaru odpovědi mění
filtr na protokol.
`rpc_calls()` — nová autoritativní metoda z `collectors/base.py`, sdílená všemi collectory
s víc než jedním RPC voláním — vrátí obě dvojice `(rpc_name, kwargs)`; `record` a
`--record-raw` z ní nahrají **obě** odpovědi, takže fixtures nesou `routes.xml`
(protocol=static) i `routes.2.xml` (protocol=aggregate).

Filtr na protokol drží odpověď malou i na zařízení s plnou internetovou tabulkou.
`all=True` se **nepoužívá** — přidává jen `__juniper_private*` tabulky, což je šum.

`collect()` slučuje výsledky obou průchodů do jedné tabulky — **přísně přídavně**: druhý
průchod (`aggregate`) jen doplňuje prefixy, které první (`static`) nepřinesl
(`target.setdefault(prefix, data)`), nikdy nepřepisuje. Prefix nemůže být v jedné RIB
zároveň static i aggregate, takže kolize identity by znamenala poškozená data, ne legitimní
update. Selhání **kteréhokoliv** z obou průchodů je chyba celého collectoru (`CollectorError`)
— stejný důvod jako u `EvpnMacCollector`: částečná data (jen statiky bez agregátů) by check
přečetl jako „agregát zmizel", což je falešný poplach.

Výstup je dvouúrovňový slovník `{RIB: {prefix: {next_hop, via, active, protocol}}}`, tak jak
ho vyrobí collector ze sloučení nahrávek `tests/fixtures/rpc/junos-evo/routes.xml` (static) a
`routes.2.xml` (aggregate):

```json
{
  "inet.0": {
    "198.62.1.0/29": { "next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": true, "protocol": "static" },
    "198.62.2.0/24": { "next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": true, "protocol": "static" },
    "10.1.0.0/23": { "next_hop": [], "via": [], "active": true, "protocol": "aggregate" }
  },
  "inet6.0": {
    "2001:aaaa::/64": { "next_hop": ["2001:abcd:11:13::b"], "via": ["et-0/0/8.13"], "active": true, "protocol": "static" }
  },
  "L3VPN-CPE13-NNI.inet.0": {
    "172.26.1.0/29": { "next_hop": ["198.11.13.2"], "via": ["et-0/0/8.113"], "active": true, "protocol": "static" }
  },
  "L3VPN-CPE13-NNI.inet6.0": {
    "2001:eeee::/64": { "next_hop": ["2001:db8:11:13::b"], "via": ["et-0/0/8.113"], "active": true, "protocol": "static" }
  }
}
```

Klíč `protocol` nese hodnotu `protocol-name` z RPC, malými písmeny (`"static"` / `"aggregate"`)
— checky ho čtou při rozdělování na `static_route_status` a `aggregate_route_status`. Chybějící
klíč `protocol` znamená snapshot pořízený před schema 10, kdy collector sbíral jen statiky;
`checks/routes.py::_flatten()` v tom případě defaultuje na `"static"`. Tenhle default chrání
jen vnitřek checku — reálný starý soubor snapshotu se přes `Snapshot.from_dict` nedostane vůbec
(`schema_version != SCHEMA_VERSION` skončí na `SnapshotVersionError`), takže starý baseline
stejně vyžaduje novou capturu.

Čtyři věci ověřené proti laborce:

- **`table-name` nese jméno RIB včetně rodiny** (`L3VPN-CPE13-NNI.inet6.0`). Asymetrie,
  kterou má mezi IPv4 a IPv6 konfigurace (`routing-options` vs. `rib inet6.0`), se v RPC
  nevyskytuje — collector proto žádnou normalizaci názvu dělat nemusí.
- **`via` nese výstupní rozhraní**, takže mapování routy na službu nepotřebuje aritmetiku
  nad next-hopem. Toho využívá `engine.py` při plnění `unassigned.static_routes`.
- **`to` a `via` sedí uvnitř `<nh>`, ne přímo pod `<rt-entry>`** — proto `_texts()` používá
  `iter()`, ne `find()`.
- **Agregátní `rt-entry` nemá žádné `<nh>`** — `<nh-type>` (`Discard`/`Reject`) sedí přímo
  pod `<rt-entry>`, takže `next_hop` i `via` vyjdou u agregátu vždy prázdné (`[]`). `protocol-
  name` u něj nese přesně `"Aggregate"`.

Tři pojistky, které vypadají zbytečně a nejsou:

- **Filtr na `protocol-name` v `parse()`** je druhá obrana za filtrem v RPC. Nasazení, které
  by RPC zavolalo bez `protocol`, by jinak zapsalo BGP routy jako statické nebo agregátní.
- **Prázdné tabulky se zahazují.** RPC vrací přes dvacet tabulek, většinu prázdných;
  ukládat je znamená nafouknout každý snímek o řádky, které nic neříkají.
- **`.strip()` v `_texts()`** je parita se sousedními collectory (`interfaces.py:32`,
  `bgp.py:30`) — v nahrávce rozhraní má bílé znaky 175 hodnot, takže u `interfaces.py` je ta
  pojistka doložená pozorováním; u `bgp.py` jde o týž idiom, ne o naměřený vstup. **Žádná současná nahrávka
  rout ale bílé znaky nemá** — ani na `<to>`, `<via>`, `<rt-destination>`, ani na
  `<table-name>` — takže tuhle větev nic netestuje. Drží se kvůli konzistenci, ne kvůli
  pozorovanému vstupu. Test, který tvrdil, že ji měří (`test_values_are_stripped`), byl
  smazán: neměřil nic a jeho popis o nahrávce navíc lhal.

## `bfd.py` — stav BFD session

RPC: `get_bfd_session_information` s `rpc_kwargs` `{"detail": True}` (obě platformy).

**Bez `detail` by collector tiše sbíral neúplná data.** Stručný výpis nemá ani `bfd-client`,
ani `remote-state` — bez klienta nejde odlišit session drženou BGP od jiné a bez
`remote-state` nejde poznat, že protějšek session administrativně vypnul. Že se příznak
na RPC opravdu dostane, hlídá až conformance test; nahrané fixtures to nesou v atributu
`<bfd-session-information style="detail">`.

Výstup je `{peer_ip: {state, interface, remote_state, local_diagnostic, clients,
detection_time, transmission_interval, multiplier}}`, z nahrávky
`tests/fixtures/rpc/junos-evo/bfd.xml`:

```json
{
  "152.11.13.2": {
    "state": "Up", "interface": "et-0/0/8.13", "remote_state": "Up",
    "local_diagnostic": "None", "clients": ["BGP"],
    "detection_time": "9.000", "transmission_interval": "3.000", "multiplier": 3
  },
  "198.11.13.2": {
    "state": "Down", "interface": "et-0/0/8.113", "remote_state": "AdminDown",
    "local_diagnostic": "None", "clients": ["BGP"],
    "detection_time": "0.000", "transmission_interval": "3.000", "multiplier": 3
  }
}
```

**Klíčem je adresa souseda**, protože přesně pod ní check hledá záměr z inventory
(`Selectors.bfd_peers`) i stav BGP (`facts["bgp"]`).

**Prázdný výpis je platný stav, ne chyba.** Nahrávka z vMX
(`tests/fixtures/rpc/junos/bfd.xml`) je `<sessions>0</sessions>` — BFD je tam
nakonfigurované, ale session nevznikla. Rozhodnout, jestli je to problém, umí až check:
závisí to na tom, jestli je BFD vůbec v konfiguraci a jestli běží BGP.

## `isis.py`, `ldp.py`, `pim.py`, `mpls.py` — protokoly Core transitu a lo0.0 (vlna 2026-08-26)

Šest nových collectorů pro sedm nových checků z `checks/core_protocols.py` (`isis_overview`
sdílí collector s `isis_interface_info` co do modulu, ne co do fact area). CLI ekvivalenty:

| fact area | RPC (`rpc_name` + `rpc_kwargs`) | CLI ekvivalent |
|---|---|---|
| `isis_adjacency` | `get_isis_adjacency_information(detail=True)` | `show isis adjacency detail` |
| `isis_interface` | `get_isis_interface_information(detail=True)` | `show isis interface detail` |
| `isis_overview` | `get_isis_overview_information` | `show isis overview` |
| `ldp_neighbor` | `get_ldp_neighbor_information(detail=True)` | `show ldp neighbor detail` |
| `pim_neighbor` | `get_pim_neighbors_information` | `show pim neighbors` |
| `mpls_interface` | `get_mpls_interface_information` | `show mpls interface` |

**Namespace-agnostická iterace.** Živá PyEZ odpověď může nést prvky v namespace
`junos-routing` (`xmlns` prefix), zatímco normalizované nahrávky z Úkolu 1 žádný prefix
nemají. Všech šest collectorů proto iteruje přes wildcard `{*}element` (`xml.iter("{*}isis-
adjacency")`) místo `xml.iter("isis-adjacency")`, který by na jmenném prostoru nic nenašel.
Texty jednotlivých elementů čte sdílený `_localname_text()` (definovaný v `isis.py`, importují
ho `ldp.py`, `pim.py`, `mpls.py`) — hledá potomka podle **localname**, ne přesné cesty, takže
funguje bez ohledu na to, jestli se odpověď zanoří jinak, než čekáme.

`_seconds_attr()` (`isis.py`, sdílené `ldp.py`/`pim.py`) čte atribut `junos:seconds` — ten
nese verzi OS přímo v URI atributu, takže matchuje na obě varianty (`seconds` u nahrávek bez
namespace, `{...}seconds` u živé odpovědi s prefixem `junos:`), ne na doslovný klíč.

### `isis.py` — `IsisAdjacencyCollector`, `IsisInterfaceCollector`, `IsisOverviewCollector`

- `isis_adjacency`: `{rozhraní: {system_name, state, ip_address, ipv6_address}}`. `state`
  defaultuje na `"unknown"`, pokud `adjacency-state` chybí — collector si `Up`/`Down` sám
  nevymýšlí.
- `isis_interface`: `{rozhraní: {levels: {level: {passive: bool}}}}`. `passive` je `True`,
  jen pokud text elementu `<passive>` je přesně `"Passive"` — cokoliv jiného (chybějící
  element, jiný text) je `False`.
- `isis_overview`: **device-global**, ne per-rozhraní — jediný dict `{overload_enabled:
  bool}`. Přítomnost elementu `<isis-overload-enabled/>` (bez ohledu na text) znamená `True`;
  nepřítomnost `False`. Do scopu tenhle fakt pouští jen Core loopback (viz `models.md`).

### `ldp.py` — `LdpNeighborCollector`

`{rozhraní: {neighbor_address, uptime_seconds}}`. **`lo0.*` záznamy se zahazují už při
parsování** — LDP soused na loopbacku je *targeted session* mezi loopbacky zařízení napříč
meshem, ne stav tranzitního linku, a tenhle collector měří jen fyzická/agregovaná rozhraní.
Rozhraní bez jména (`interface-name` chybí) se přeskočí stejně jako u ostatních.

### `pim.py` — `PimNeighborCollector`

`{rozhraní: {neighbor_address, uptime_seconds}}`. Vnoření odpovědi je `pim-neighbors-
information > pim-interface > pim-neighbor` — `pim-interface` **bez** vnořeného
`pim-neighbor` (rozhraní s PIM zapnutým, ale bez souseda) nedostává syntetický záznam, klíč
prostě chybí (absence je absence, ne vymyšlené Down). Kontrakt klíčuje jedním sousedem na
rozhraní; při víc než jednom `pim-neighbor` pod jedním `pim-interface` (multi-access segment)
collector bere první a další tiše zahazuje — v nahrávkách z laborky k tomu nedochází (1:1).

### `mpls.py` — `MplsInterfaceCollector`

`{rozhraní: {state}}`. `state` defaultuje na `"unknown"`, pokud `mpls-interface-state`
chybí.

## `multicast.py` — IGMP, multicast forwarding, MVPN c-multicast (vlna 2026-09-02)

Tři collectory pro čtyři nové checky z `checks/multicast.py`: `igmp_group`,
`multicast_route`, `mvpn_instance`. CLI ekvivalenty:

| fact area | RPC (`rpc_name` + `rpc_kwargs`) | CLI ekvivalent |
|---|---|---|
| `igmp_group` | `get_igmp_group_information` | `show igmp group` |
| `multicast_route` | `get_multicast_route_information(extensive=True[, instance=...])` | `show multicast route instance all extensive` |
| `mvpn_instance` | `get_mvpn_instance_information(inet=True)` | `show mvpn instance inet` |

Žádný z collectorů neinterpretuje verdikt — `local` u IGMP se zahazuje jen proto, že to
není rozhraní (nikdy nemůže být servisní rozhraní), ne kvůli PASS/FAIL. Absence
rozhraní/instance ve výpisu znamená absenci klíče, ne prázdný seznam.

### `IgmpGroupCollector` (`igmp_group`)

`{rozhraní: [{source, group}]}`. `multicast-source-address` `"0.0.0.0"` (ASM `(*, G)`) se
mapuje na `source: None`, ne na text `"0.0.0.0"` — check pak testuje `is None`, ne
magický řetězec. Pseudo-rozhraní `local` (skupiny, které si router sám nasadil, ne
receiver) se zahazuje při parsování. Rozhraní bez skupin nedostává klíč s prázdným
seznamem — absence je absence.

### `MulticastRouteCollector` (`multicast_route`)

`{instance: {"S,G": {upstream_interface, downstream_interfaces, forwarding_rate_pps,
uptime_seconds, state, forwarding_state}}}`. Jen `address-family INET` (IPv6 multicast se
nesbírá). Klíč `S,G` je řetězec `f"{source},{group}"` (`route_key()`), ne tuple — snapshot
musí zůstat JSON-safe.

**`forwarding_rate_pps` je `int | None`, nikdy vymyšlená nula.** junos-evo často vrací
`<multicast-statistics-timed-out/>` místo `forwarding-rate-packets` i na živé Forwarding
routě (změřeno 2026-09-02) — element pak v odpovědi chybí a `_int()` vrací `None`.
Checky na `None` renderují `SKIP : statistiky nedostupne`, ne `BROKEN` s 0 pps.

**MX nezná `instance="all"`.** `get-multicast-route-information(instance="all")` na MX
vrací `<output>instance is not running</output>` (probe 2026-09-02) — MX žádnou
all-instances formu nemá. Proto má collector dvě různé cesty podle platformy:

- **junos-evo**: jedno volání `rpc_kwargs()` → `{"extensive": True, "instance": "all"}`.
- **junos (MX)**: `rpc_kwargs()` vrátí jen `{"extensive": True}` (master instance bez
  argumentu); `record_calls(device, platform)` navíc dynamicky zjistí seznam VRF
  (`get-instance-information(brief=True)`, filtr `instance-type == "vrf"`) a přidá jedno
  volání s `instance=<jméno>` za každou RI. Jména RI se **nikdy** neberou z inventory —
  collector inventory nemá, a hardcodovat je zakázáno.

`record_calls()` je hook v `collect()`/`record` (default = `rpc_calls()`), díky kterému
`mig-validate record` uloží MX odpovědi jako `multicast_route.xml` (master) +
`multicast_route.2.xml`, `.3.xml`… (jedna na RI), zatímco junos-evo pořád jen jednu
`multicast_route.xml`. `_fixture_paths` v conformance testech proto globuje
`name.xml` + `name.N.xml` místo počítání položek v `rpc_names`.

### `MvpnInstanceCollector` (`mvpn_instance`)

`{instance: {"c_multicast": [{source_prefix, group_prefix, provider_tunnel_id,
sender_pe}]}}`. `c-multicast-address` má tvar `"S/32:G/32"` — rozdělí se na první
dvojtečce na `source_prefix`/`group_prefix`. `sender_pe` je vytažená PE adresa z
`provider_tunnel_id` (`parse_sender_pe()`, regex `P2MP:(\d+\.\d+\.\d+\.\d+)`) — `None`,
pokud tunnel id chybí nebo obsahuje `"invalid"` (`I-P-tnl:invalid`). Instance, která je ve
výpisu, ale bez c-multicast záznamů, si klíč nechává s prázdným seznamem — check tak
rozlišuje „instance není ve výpisu vůbec" od „instance je, ale bez c-multicast".
