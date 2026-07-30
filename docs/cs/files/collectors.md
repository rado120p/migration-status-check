# `collectors/` — sběr operačního stavu

Soubory: `base.py`, `registry.py`, `all.py`, `interfaces.py`, `arp.py`, `nd.py`, `bgp.py`,
`evpn.py`, `routes.py`, `bfd.py` a prázdný `__init__.py`.

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
nekompletní. Jediný takový je zatím `EvpnMacCollector` na MX.

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

Výstup: `[{ip, mac, interface, routing_instance}]`. Záznamy bez IP nebo bez rozhraní se
zahazují.

ARP je **zároveň producent dat pro ping probe** — z něj se při `capture` odvozují cíle.

Poznámka ke `routing_instance`: ani MX, ani EVO ho v odpovědi neuvádějí
(`arp-table-entry-flags` nese jen `<none/>`), takže klíč zpravidla zůstane `None`. Ve
schématu je záměrně — kontrakt ho předepisuje a scope filtruje ARP podle `interface`,
ne podle instance.

## `nd.py` — ND tabulka (IPv6 sousedé)

RPC: `get_ipv6_nd_information`. Dvojče `arp.py` — z ND se stejně jako z ARP odvozují cíle
pingu, jen pro rodinu IPv6.

Výstup: `[{ip, mac, interface, state}]`. Záznamy bez IP nebo bez rozhraní se zahazují.
Na rozdíl od `probes/ping.py` collector **záznamy nefiltruje** — link-local sousedé i
záznamy bez MAC se vrátí všechny; rozhodnutí, co je použitelný cíl pingu, patří probe,
protože závisí na konfiguraci služby, kterou collector nezná.

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

RPC: `get_evpn_vpws_information`. Výstup `{routing_instance: {status, local_sid, remote_sid}}`.

- **Klíčem je název instance, ne rozhraní** — název instance je při migraci stabilní,
  název portu ne.
- `status` je **stav rozhraní instance** (`Up`), ne stav vzdáleného PE (`Resolved`). Obě
  hodnoty v odpovědi existují a znamenají něco jiného; check porovnává proti `Up`, takže se
  emituje ta souměřitelná.
- `local_sid` / `remote_sid` se čtou z **prvního rozhraní instance** v pořadí dokumentu.
  Instance s víc rozhraními je v této topologii vzácná a kontrakt typuje SID jako jedno
  číslo; pokrytí víc rozhraní by nejdřív vyžadovalo rozšířit schéma.

### `EvpnEsiCollector` (`evpn_esi`)

RPC: `get_evpn_instance_information` s **`extensive=True`**. Bez `extensive` vrátí
`show evpn instance` souhrn bez jediného ESI a collector by tiše vracel prázdno.

Výstup `{esi: {status, df_role, interface}}`.

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

## `routes.py` — statické routy z routovací tabulky

RPC: `get_route_information` s `rpc_kwargs` `{"protocol": "static"}` (obě platformy).

Filtr na protokol drží odpověď malou i na zařízení s plnou internetovou tabulkou.
`all=True` se **nepoužívá** — přidává jen `__juniper_private*` tabulky, což je šum.

Výstup je dvouúrovňový slovník `{RIB: {prefix: {next_hop, via, active}}}`, tak jak ho
vyrobí collector z nahrávky `tests/fixtures/rpc/junos-evo/routes.xml`:

```json
{
  "inet.0": {
    "198.62.1.0/29": { "next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": true },
    "198.62.2.0/24": { "next_hop": ["152.11.13.2"], "via": ["et-0/0/8.13"], "active": true }
  },
  "inet6.0": {
    "2001:aaaa::/64": { "next_hop": ["2001:abcd:11:13::b"], "via": ["et-0/0/8.13"], "active": true }
  },
  "L3VPN-CPE13-NNI.inet.0": {
    "172.26.1.0/29": { "next_hop": ["198.11.13.2"], "via": ["et-0/0/8.113"], "active": true }
  },
  "L3VPN-CPE13-NNI.inet6.0": {
    "2001:eeee::/64": { "next_hop": ["2001:db8:11:13::b"], "via": ["et-0/0/8.113"], "active": true }
  }
}
```

Tři věci ověřené proti laborce:

- **`table-name` nese jméno RIB včetně rodiny** (`L3VPN-CPE13-NNI.inet6.0`). Asymetrie,
  kterou má mezi IPv4 a IPv6 konfigurace (`routing-options` vs. `rib inet6.0`), se v RPC
  nevyskytuje — collector proto žádnou normalizaci názvu dělat nemusí.
- **`via` nese výstupní rozhraní**, takže mapování routy na službu nepotřebuje aritmetiku
  nad next-hopem. Toho využívá `engine.py` při plnění `unassigned.static_routes`.
- **`to` a `via` sedí uvnitř `<nh>`, ne přímo pod `<rt-entry>`** — proto `_texts()` používá
  `iter()`, ne `find()`.

Tři pojistky, které vypadají zbytečně a nejsou:

- **Filtr na `protocol-name` v `parse()`** je druhá obrana za filtrem v RPC. Nasazení, které
  by RPC zavolalo bez `protocol`, by jinak zapsalo BGP routy jako statické.
- **Prázdné tabulky se zahazují.** RPC vrací přes dvacet tabulek, většinu prázdných;
  ukládat je znamená nafouknout každý snímek o řádky, které nic neříkají.
- **`.strip()` v `_texts()`** je parita se sousedními collectory (`interfaces.py:32`,
  `bgp.py:30`), kde MX texty obalené novými řádky opravdu vrací. **Žádná současná nahrávka
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
