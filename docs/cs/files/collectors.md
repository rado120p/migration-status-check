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
jinak `record` uloží jen první z nich a nahrané fixtures budou tiše
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

Výstup (schema 14, spec 2026-09-25): seznam záznamů, jeden na `<bgp-peer>`, každý nese
celou svou identitu — ne vnořený slovník klíčovaný adresou jako dřív:

```json
[{"address": "150.0.0.1", "routing_instance": "L3VPN-CPE13-NNI",
  "local_interface": "ae0.100", "state": "Established", "peer_as": 65013,
  "ribs": {"inet.0": {"received": 14, "accepted": 14, "advertised": 3,
                       "active": 3, "suppressed": 0}}}]
```

Dřívější tvar `{peer_ip: {...}}` se přepisoval, když dvě VRF měly peera na stejné adrese
(ostrý běh MX → ACX 2026-09-23) — poslední zaznamenaný peer vyhrál a baseline jedné služby
tak nesla session té druhé. `Scope.select` (`models/scope.py::owns_bgp_peer`) si pro pohled
služby dál drží dnešní klíčovaný tvar `{address: záznam}` — scope má nejvýš jednu RI, takže
uvnitř služby je adresa jednoznačná z konstrukce; kolizi vidí jen **surová** fakta.

Čtyři věci ověřené proti laborce:

- **`peer-address` nese port** (`150.0.0.1+179` na MX, efemerní `150.0.0.1+57010` na EVO).
  `strip_port()` ho odřízne — bez toho by se peer nikdy nepotkal s `bgp_neighbor`
  z inventory.
- **Jeden peer může mít až 11 RIB** (`bgp.rtarget.0`, `inet.0`, `bgp.l3vpn.0`, ...) a počty
  se ukládají **za každou RIB zvlášť, ne sečtené**. Součtem by se IPv4 a IPv6 slily do
  jednoho čísla a pokles v `inet6.0` kompenzovaný nárůstem v `inet.0` by prošel bez
  povšimnutí. `checks/bgp.py::peer_family()` pak rodinu řádku odvozuje z **adresy peeru**,
  ne z názvu RIB (ten rodinu nemusí nést vůbec, např. `bgp.l3vpn.0`).
- **`local_interface`** je z `<local-interface-name>`; chybí-li, `null`. Ukládá se od
  schema 14, vlastnictví ho zatím nepoužívá — je to podklad pro pozdější rozlišení dvou
  link-local peerů na stejné adrese v jedné RI, ne dnešní chování.
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

Výstup (schema 14, spec 2026-09-25): seznam záznamů, jeden na `(instance, ESI)`:

```json
[{"instance": "EVPN-VLAN-AWARE-POP1", "esi": "00:11:12:13:14:00:00:00:00:00",
  "resolved_status": "Resolved by IFL ae0.4093", "df_role": "10.0.0.1",
  "interfaces": {"ae0.4093": {"status": "Up", "mode": "all-active"},
                 "ae0.4094": {"status": "Up", "mode": "all-active"}}}]
```

Dřívější tvar `{esi: {status, df_role, interface}}` klíčoval jen holým ESI a nesl jediné
`interface` — stejné ESI (per-port na AE) ale může ležet ve víc instancích, každá s vlastním
IFL, a jeden segment může mít víc lokálních IFL zároveň (`evpn-esi-num-local-intf` = 2).
Klíč je proto `(instance, ESI)`, ne holé ESI, a `interfaces` je slovník **všech** IFL
segmentu z **téže instance** — ne jediné `interface` z ESI bloku.

- **ESI začínající `05:` se ignoruje.** Box si ho generuje sám (per-IRB, auto-derived) a
  nenese ani status, ani DF — v reportu by u každé L3-extended služby přibyl řádek bez
  vypovědní hodnoty.
- `interfaces` je `evpn-interface` z tabulky `evpn-interface-status-table` **téže instance**,
  jejichž `evpn-interface-esi` je rovno ESI segmentu — `status` v ní je `Up` / `Down`
  (per-IFL tabulka), ne `Up/Forwarding` z ESI bloku (`evpn-esi-local-intf-status`), který se
  už nečte. Segment, jehož ESI žádný řádek per-IFL tabulky nenese, se uloží s
  `interfaces: {}` — stav se nefabuluje, žádný scope ho nevybere.
- `resolved_status` je popisný text (`Resolved by IFL ae0.14`) z `evpn-esi-status` — nese
  jméno IFL, které se migrací mění, takže se na rovnost neporovnává; v reportu je to
  samostatný řádek „ESI Status".
- `df_role` je **IP adresa zvoleného DF**, ne role tohohle boxu. Určit „jsem DF?" by
  znamenalo interpretovat, a to collectoru nepatří.
- Pohled pro službu (`Scope.select`) zůstává zploštělý na dnešní tvar, který check čte —
  `interface`/`status`/`mode` z IFL scopu (scope má jedno rozhraní, takže vzniká jeden
  stavový řádek na službu). Pojistkou zůstává conformance test
  `test_esi_interface_matches_a_scope`.

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
s víc než jedním RPC voláním — vrátí obě dvojice `(rpc_name, kwargs)`; `record`
z ní nahraje **obě** odpovědi, takže fixtures nesou `routes.xml`
(protocol=static) i `routes.2.xml` (protocol=aggregate).

Filtr na protokol drží odpověď malou i na zařízení s plnou internetovou tabulkou.
`all=True` se **nepoužívá** — přidává jen `__juniper_private*` tabulky, což je šum.

`collect()` slučuje výsledky obou průchodů — od schema 14 (spec 2026-09-25) **jen
zřetězením** (`records.extend(parsed)`), žádné `setdefault` proti identitě prefixu.
**Static a aggregate mohou legitimně sdílet stejné `(rib, prefix)`** (potvrzeno proti
laborce 2026-09-24) — dřívější docstring tvrdil opak a `collect()` proto druhý průchod
jen tiše zahazoval, kdykoli agregát ležel na stejném prefixu jako statika. Každý protokol
je od schema 14 svůj vlastní záznam. Selhání **kteréhokoliv** z obou průchodů je chyba
celého collectoru (`CollectorError`) — stejný důvod jako u `EvpnMacCollector`: částečná
data (jen statiky bez agregátů) by check přečetl jako „agregát zmizel", což je falešný
poplach.

Výstup (schema 14) je **plochý seznam záznamů** na `(rib, prefix, protocol)`, ne vnořený
slovník — tak jak ho vyrobí collector ze zřetězení nahrávek
`tests/fixtures/rpc/junos-evo/routes.xml` (static) a `routes.2.xml` (aggregate):

```json
[
  {"rib": "inet.0", "prefix": "198.62.1.0/29", "protocol": "static",
   "next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": true},
  {"rib": "inet.0", "prefix": "198.62.2.0/24", "protocol": "static",
   "next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": true},
  {"rib": "inet.0", "prefix": "10.1.0.0/23", "protocol": "aggregate",
   "next_hop": [], "via": [], "active": true},
  {"rib": "inet6.0", "prefix": "2001:aaaa::/64", "protocol": "static",
   "next_hop": ["2001:abcd:11:13::b"], "via": ["et-0/0/8.13"], "active": true},
  {"rib": "L3VPN-CPE13-NNI.inet.0", "prefix": "172.26.1.0/29", "protocol": "static",
   "next_hop": ["198.11.13.2"], "via": ["et-0/0/8.113"], "active": true},
  {"rib": "L3VPN-CPE13-NNI.inet6.0", "prefix": "2001:eeee::/64", "protocol": "static",
   "next_hop": ["2001:db8:11:13::b"], "via": ["et-0/0/8.113"], "active": true}
]
```

Klíč `protocol` nese hodnotu `protocol-name` z RPC, malými písmeny (`"static"` /
`"aggregate"`). Pohled pro službu (`Scope.select`) tenhle plochý seznam přeskupí zpátky
na dnešní tvar, který checky čtou —
`{protokol: {rib: {prefix: záznam}}}` — pomocí identity `(rib, prefix, protocol)`
(`models/scope.py::route_key()`, sdílená se `engine.py::_unassigned_static_routes`, aby
záznam bez protokolu nezůstal neviditelný ani v jednom z obou míst). Selektor bez
`route_type` znamená `"static"` — dnešní default, `_flatten()` (`checks/routes.py`) ho
z pohledu čte přímo, žádný vlastní fallback na chybějící klíč už nepotřebuje: `schema_version`
je exact-match, takže starý soubor bez `protocol` se přes `Snapshot.from_dict` vůbec
nenačte.

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

Výstup (schema 14, spec 2026-09-25) je **seznam záznamů**, jeden na `<bfd-session>`, ne
slovník klíčovaný sousedem jako dřív:

```json
[
  {"neighbor": "152.11.13.2", "interface": "et-0/0/8.13", "multihop": false,
   "state": "Up", "remote_state": "Up", "local_diagnostic": "None",
   "clients": ["BGP"], "detection_time": "9.000",
   "transmission_interval": "3.000", "multiplier": 3},
  {"neighbor": "198.11.13.2", "interface": "et-0/0/8.113", "multihop": false,
   "state": "Down", "remote_state": "AdminDown", "local_diagnostic": "None",
   "clients": ["BGP"], "detection_time": "0.000",
   "transmission_interval": "3.000", "multiplier": 3}
]
```

z nahrávky `tests/fixtures/rpc/junos-evo/bfd.xml`.

Dřívější klíčovaný tvar `{peer_ip: {...}}` (přesně pod adresou souseda check hledal záměr
z inventory i stav BGP) se přepisoval, kdykoli dvě multihop session mířily na stejnou
adresu — `extensive` výpis nenese ani rozhraní, ani VRF, takže je nejde rozlišit vůbec
(ověřeno v laborce 2026-09-24). **`multihop`** je nové pole (`<session-type>` obsahuje
„Multi hop"; chybí-li `session-type`, rozhoduje `interface is None`) — `Scope.select`
(`models/scope.py::owns_bfd_session`) ho potřebuje, aby single-hop session odlišil podle
rozhraní a multihop session nechal splynout do `bfd_ambiguous`, když se dvě takové na
stejnou adresu potkají. Pohled pro službu zůstává klíčovaný sousedem jako dřív —
mění se jen surová fakta v snímku.

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
rozhraní; při víc než jednom `pim-neighbor` pod jedním `pim-interface` (multi-access
segment), nebo při víc než jednom `pim-interface` bloku se stejným jménem rozhraní, collector
bere **první IPv4** souseda a další tiše zahazuje (`if interface in neighbors: break`) —
v nahrávkách z laborky k tomu při jedné rodině nedochází (1:1).

**Jen IPv4 se vyhodnocuje** — `<ip-protocol-version>` se testuje na doslovné `"6"` a takový
soused se přeskočí (`continue`), ne `break`, takže nezablokuje pozdější IPv4 blok. Chybějící
element `<ip-protocol-version>` znamená IPv4. Na dual-stack rozhraní PTX laborka
(2026-09-24) vypisuje IPv4 `pim-interface` blok, pak IPv6 blok se stejným jménem rozhraní —
bez filtru by IPv6 soused přepsal IPv4 (fixture
`tests/fixtures/cases/pim_neighbors_dual_stack.xml`). Pravidlo platí bez ohledu na pořadí
bloků v odpovědi: syntetický test s IPv6 blokem **před** IPv4 blokem
(`tests/collectors/test_pim.py::test_v6_block_before_v4_block_keeps_v4`) ověřuje, že filtr
sedí na verzi, ne na tom, že v laborce IPv4 chodí první.

### `mpls.py` — `MplsInterfaceCollector`

`{rozhraní: {state}}`. `state` defaultuje na `"unknown"`, pokud `mpls-interface-state`
chybí.

## `multicast.py` — IGMP, PIM join, multicast forwarding, MVPN c-multicast (vlna 2026-09-02, `pim_join` 2026-09-07)

Čtyři collectory pro pět checků z `checks/multicast.py`: `igmp_group`,
`multicast_route`, `mvpn_instance`, `pim_join`. CLI ekvivalenty:

| fact area | RPC (`rpc_name` + `rpc_kwargs`) | CLI ekvivalent |
|---|---|---|
| `igmp_group` | `get_igmp_group_information` | `show igmp group` |
| `multicast_route` | `get_multicast_route_information(extensive=True[, instance=...])` | `show multicast route instance all extensive` |
| `mvpn_instance` | `get_mvpn_instance_information(inet=True)` | `show mvpn instance inet` |
| `pim_join` | `get_pim_join_information(extensive=True[, instance=...])` | `show pim join instance all extensive` |

Žádný z collectorů neinterpretuje verdikt — `local` u IGMP se zahazuje jen proto, že to
není rozhraní (nikdy nemůže být servisní rozhraní), ne kvůli PASS/FAIL. Absence
rozhraní/instance ve výpisu znamená absenci klíče, ne prázdný seznam.

### `IgmpGroupCollector` (`igmp_group`)

`{rozhraní: [{source, group}]}`. `multicast-source-address` `"0.0.0.0"` (ASM `(*, G)`) se
mapuje na `source: None`, ne na text `"0.0.0.0"` — check pak testuje `is None`, ne
magický řetězec. Pseudo-rozhraní `local` (skupiny, které si router sám nasadil, ne
receiver) se zahazuje při parsování. Rozhraní bez skupin nedostává klíč s prázdným
seznamem — absence je absence.

### `_PerInstanceCollector` — sdílený základ (spec 2026-09-07)

`MulticastRouteCollector` i `PimJoinCollector` dědí z `_PerInstanceCollector`: sdílí
`record_calls()` (MX dopočítá VRF z `get-instance-information(brief=True)`, junos-evo
jedno volání s `instance="all"`) i `collect()` (per-RPC agregace tabulek, `CollectorError`
při jakémkoli neúspěchu volání nebo při selhání zjištění seznamu instancí). Podtřída jen
dodává `rpc_name()`/`rpc_kwargs()` a `parse(xml, platform) -> {instance: {klíč:
payload}}`. Refaktor nemění chování `multicast_route` — existující testy zůstávají
zelené.

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

### `PimJoinCollector` (`pim_join`, spec 2026-09-07)

`{instance: {"S,G": {source, group, upstream_interface, upstream_neighbor,
downstream_interfaces, uptime_seconds}}}`. Jen `address-family INET` (INET6 se
ignoruje). Klíč je `route_key(source or "*", group)` — pro `(*, G)` je klíč
`"*,group"`, aby zůstal JSON-safe a jednoznačný; `source` v payloadu samotném zůstává
`None`. ASM se normalizuje defenzivně (rozhodnutí 2026-09-07): chybějící
`multicast-source-address` element, prázdný text, `"*"` i `"0.0.0.0"` všechny dají
`source = None` — zachycené fixture jsou všechny SSM, ale reálný ASM join může nést
kterýkoli z těchto zápisů (mirror `IgmpGroupCollector`'s `ASM_SOURCE` handling,
nefabuluje se nic navíc). `pim-instance` má prefix `PIM.`, který se stripuje
(`PIM.master` → `master`, `PIM.NGMVPN-PIM-SOURCE` → `NGMVPN-PIM-SOURCE`).

`upstream_interface` a `upstream_neighbor` se drží verbatim — `"Through BGP"`,
`"Through MVPN"` zůstávají textem, roli přiřazuje až check. `downstream_interfaces`
sbírá z každého `downstream-interface` jak `pim-interface-name`, tak
`pim-pseudo-downstream-interface-name` (obě, jsou-li přítomny), bez duplicit, v pořadí
výpisu — Internet/multicast join v master instanci nese downstream `Pseudo-GMP` a
skutečné jméno rozhraní až v `pim-pseudo-downstream-interface-name`. `uptime_seconds`
z `junos:seconds` atributu elementu `uptime` (`_seconds_attr`). Instance bez
`join-group` nemá klíč — collector nic nesyntetizuje.

Sdílí `_PerInstanceCollector` s `multicast_route` (viz výš): stejné dva RPC tvary podle
platformy (`get_pim_join_information(extensive=True, instance="all")` na junos-evo,
`get_pim_join_information(extensive=True)` + jedno volání per VRF na MX), stejný
`record_calls()`. `record` proto na MX uloží `pim_join.xml` (master) +
`pim_join.2.xml`, `.3.xml`… per RI, přesně jako `multicast_route`.
