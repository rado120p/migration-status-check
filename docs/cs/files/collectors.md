# `collectors/` — sběr operačního stavu

Soubory: `base.py`, `registry.py`, `all.py`, `interfaces.py`, `arp.py`, `bgp.py`, `evpn.py`
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

Importuje `arp`, `bgp`, `evpn`, `interfaces`, čímž se spustí jejich `@register`. Existuje
jako samostatný modul (ne `__init__.py`), aby nevznikl cyklický import.

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

## `bgp.py` — stav peerů a počty prefixů

RPC: `get_bgp_neighbor_information`. Použití `neighbor` místo `summary` varianty je
záměrné — summary neobsahuje počet **advertised** prefixů.

Výstup: `{peer_ip: {state, peer_as, routing_instance, prefixes: {received, accepted,
advertised}}}`.

Tři věci ověřené proti laborce:

- **`peer-address` nese port** (`150.0.0.1+179` na MX, efemerní `150.0.0.1+57010` na EVO).
  `strip_port()` ho odřízne — bez toho by se peer nikdy nepotkal s `bgp_neighbor`
  z inventory.
- **Jeden peer může mít až 11 RIB** (`bgp.rtarget.0`, `inet.0`, `bgp.l3vpn.0`, ...) a počty
  se **sčítají přes všechny**.
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
