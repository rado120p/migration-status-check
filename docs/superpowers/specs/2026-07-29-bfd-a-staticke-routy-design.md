# BFD a statické routy — návrh

**Datum:** 2026-07-29
**Stav:** schváleno, připraveno k tvorbě implementačního plánu
**Navazuje na:** [2026-07-28-ipv6-a-report-design.md](2026-07-28-ipv6-a-report-design.md),
sekce „Otevřené otázky pro navazující spec"

## Účel

Nástroj dnes BFD ani statické routy nekontroluje vůbec. Není to doladění
existující schopnosti, je to schopnost chybějící: po migraci se operátor
z reportu nedozví, jestli statická routa dojela na nové zařízení, ani jestli
BFD relace, která hlídá dobu výpadku, vůbec běží.

Oproti dosavadním oblastem je tu jeden rozdíl, který tvaruje celý návrh:
u statických rout a BFD existuje **zapsaný záměr**. Konfigurace říká, co tam
být má; RPC říká, co tam je. Ty dvě věci se v laboratoři právě teď rozcházejí
(viz níže), a ten rozpor je přesně ta chyba, kterou má migrační validátor
chytat.

## Rozsah

**V rozsahu:**

- parsování statických rout z konfigurace do inventory, v obou parserech
- parsování BFD u BGP sousedů včetně dědění `neighbor` > `group` > `protocols bgp`
- mapování obojího na jednotlivé služby
- nové collectory `routes` (`show route protocol static`) a `bfd` (`show bfd session detail`)
- nové checky `static_route_status` a `bfd_session_state`
- `RunResult.unassigned` dostane `static_routes` a `bfd_sessions`
- zvýšení `schema_version` inventory **i snapshotu** a regenerace fixtures

**Mimo rozsah:**

- BFD u jiných klientů než BGP (OSPF, IS-IS, statické routy) — v laboratoři
  se nevyskytuje, přidání je čistě aditivní
- statické routy bez adresy next-hopu (`discard`, `reject`, `next-table`,
  `qualified-next-hop`) — viz „Zapsané předpoklady"
- F-2 (podřádky se jménem RIB v rendereru) — zůstává ve vlně 3, tento návrh
  si na renderer nesahá
- F-14 (kosmetika v `NESPAROVANO`, sjednocení idiomů popisků)

## Ověřeno proti laboratoři

Všechna tvrzení níže pocházejí z captureů pořízených 2026-07-29 proti
`172.20.20.4` (VMX, junos) a `172.20.20.5` (PTX10002-36QDD, junos-evo).
Soubory jsou v `runs/bfd-static-2026-07-29/` (mimo git, `runs/` je
v `.gitignore`): `cfg/` je `get-config` obou zařízení v surové i dědičné
variantě, `rpc/` jsou odpovědi `get-route-information` a
`get-bfd-session-information`.

### Konfigurace statik není mezi rodinami symetrická — potvrzeno

Na obou zařízeních, globálně i uvnitř VRF:

| kde | XPath | příklad z laborky |
|---|---|---|
| globální IPv4 | `routing-options/static/route` | `198.62.1.0/29 → 152.11.13.2` |
| globální IPv6 | `routing-options/rib[name=inet6.0]/static/route` | `2001:aaaa::/64 → 2001:abcd:11:13::b` |
| VRF IPv4 | `routing-instances/instance/routing-options/static/route` | `172.26.1.0/29 → 198.11.13.2` |
| VRF IPv6 | `routing-instances/instance/routing-options/rib[name=X.inet6.0]/static/route` | `2001:eeee::/64 → 2001:db8:11:13::b` |

### V RPC ta asymetrie neexistuje

`get-route-information protocol=static` vrací `table-name`, což je přímo jméno
RIB (`inet.0`, `inet6.0`, `L3VPN-CPE13-NNI.inet.0`, `L3VPN-CPE13-NNI.inet6.0`,
`mgmt_junos.inet.0`, `mgmt_junos.inet6.0`), a `via`, což je výstupní rozhraní
(`et-0/0/8.13`, `fxp0.0`). Rodina i příslušnost k RIB jsou v jednom poli
a mapování na rozhraní je přímé.

### Konfigurace a routovací tabulka se rozcházejí

| zařízení | statik v konfiguraci | statik v tabulce |
|---|---|---|
| `.4` (MX) | 5 servisních + 2 mgmt | **jen 2 mgmt** |
| `.5` (EVO) | 5 servisních | všech 5, mgmt žádná |

Servisní statiky na `.4` nejsou nainstalované, protože jejich next-hop
neexistuje — rozhraní je po migraci deaktivované. Je to živý případ toho, co
má check `static_route_status` hlásit.

### BFD: `inherit` hierarchii BGP nerozbaluje

Ověřeno experimentem. Na `.5` je BFD nakonfigurované na skupině `CPE14`
v instanci `L3VPN-CPE14-UNI`; její dva sousedé (`198.11.14.2`,
`2001:db8:11:14::b`) nemají vlastní `bfd-liveness-detection`. Konfigurace
stažená **s** `inherit` i **bez** něj vypadá stejně: element sedí na skupině,
u sousedů není. Junosí `inherit` rozbaluje `apply-groups`, ne hierarchii
protokolu. **Průchod hierarchií si tedy musí udělat parser sám.**

### Matice stavů BFD, kterou laborka pokrývá

| peer | RI | BGP | BFD v konfiguraci | BFD session |
|---|---|---|---|---|
| `152.11.13.2` | master | Established | ano (neighbor) | Up |
| `198.11.13.2` | L3VPN-CPE13-NNI | Established | ano (neighbor) | **Down**, `remote-state AdminDown` |
| `198.11.14.2` | L3VPN-CPE14-UNI | **Idle** | ano (**group**) | žádná |
| `2001:db8:11:14::b` | L3VPN-CPE14-UNI | **Idle** | ano (group, dědí i IPv6 peer) | žádná |
| `150.0.0.1`, `150.0.0.2`, `152.11.14.4`, `2001:abcd:11:13::b` | — | Established | ne | žádná |

Na `.4` je BFD nakonfigurované u dvou sousedů, BGP je u obou Idle a session je
**nula**.

Session nese `session-neighbor`, `session-interface`, `session-state`,
`remote-state`, `local-diagnostic` a `bfd-client` s `client-name` (v laborce
vždy `BGP`).

## Architektonická rozhodnutí

Číslování navazuje na předchozí spec (AR-1 až AR-10).

### AR-11: Jméno RIB se normalizuje na tvar, který používá RPC

Parser převede obě konfigurační podoby na totéž jméno, jaké vrací `table-name`:

| konfigurace | RIB |
|---|---|
| `routing-options/static` | `inet.0` |
| `routing-options/rib[name=N]/static` | `N` (verbatim) |
| `instance[X]/routing-options/static` | `X.inet.0` |
| `instance[X]/routing-options/rib[name=N]/static` | `N` (verbatim) |

Ověřeno proti všem šesti tabulkám v laborce.

Tím se asymetrie mezi rodinami zastaví na hranici inventáře a nešíří se dál.
Naivní `//static/route` by našel obojí, ale ztratil by příslušnost k RIB —
a to je právě ten údaj, který odlišuje `::/0` v `mgmt_junos.inet6.0` od
`::/0` v `inet6.0`.

**Rodina se odvozuje z prefixu, ne ze jména RIB.** Stejný důvod jako
u `peer_family` (AR-3): jméno RIB rodinu obsahovat nemusí.

### AR-12: Identita routy je `(RIB, prefix)`, next-hop je hodnota

Změna next-hopu se čte jako **změněná routa** — jeden řádek se sloupcem ZMENA
(`152.11.13.2 -> 152.11.13.9`) — ne jako routa zmizelá a jiná přibylá. Je to
jeden fakt o jedné routě, takže jeden finding (AR-4).

- **RIB je v identitě**, protože týž prefix ve dvou RIB jsou dvě různé routy.
- **`via` rozhraní v identitě není.** Migrace ho mění záměrně
  (`ge-0/0/2.113` → `et-0/0/8.113`); kdyby bylo v identitě, každá migrovaná
  routa by se přečetla jako výpadek.
- Při ECMP je hodnotou celá množina next-hopů.

### AR-13: BFD se dědí `neighbor` > `group` > `protocols bgp`, specifičtější vyhrává

Nese se celá hodnota (`minimum-interval`, `multiplier`), ne jen ano/ne, a k ní
`source` — na které úrovni se pravidlo našlo. Bez `source` nejde v reportu ani
v debugu odlišit „soused má vlastní timery" od „zdědil je ze skupiny".

Skupinové pravidlo platí i pro **IPv6** sousedy v téže skupině (v laborce
`2001:db8:11:14::b` ze skupiny `CPE14`).

Průchod je povinně vlastní, ne spoléhání na `inherit` — viz důkaz výše.

### AR-14: Checky iterují přes sjednocení záměru, skutečnosti a baseline

Množina, přes kterou se iteruje, je **konfigurace subjektu ∪ naměřeno
v subjektu ∪ naměřeno v baseline**. Každý ze tří zdrojů zavírá jednu díru:

- **Bez konfigurace subjektu** by chyběl celý AR-15 (nakonfigurováno, není
  v tabulce).
- **Bez naměřeného v subjektu** by session peeru, kterého parser do záměru
  nedoplnil, sice patřila scopu, ale nikdy by se nevykreslila. Chyba
  v průchodu hierarchií podle AR-13 by se tím **skryla před vlastním výstupem
  nástroje** — přesně ta třída chyby, kvůli které AR-13 vzniklo.
- **Bez naměřeného v baseline** by tiše zmizelo všechno, co migrace odstranila.
  Statická routa vyřazená z konfigurace se do selektoru subjektu nedostane,
  takže by se scope na její chybění nikdy nezeptal. Přitom právě „bylo to tam
  před migrací a teď to tam není" je otázka, kvůli které nástroj existuje.

U statik má sjednocení ještě druhou pojistku: routu v tabulce, kterou parser
neuměl přečíst, si nenárokuje žádný scope, takže spadne do
`unassigned.static_routes` místo aby zmizela.

V service scope je „naměřeno v subjektu" podmnožinou konfigurace subjektu
(selektor pouští jen nakonfigurované routy), takže tam sjednocení pracuje
s prvním a třetím zdrojem. V device scope je naopak prázdný první zdroj a
projdou všechny naměřené routy — proto je formulace množinová a ne výčtem větví.

### AR-15: Rozpor mezi konfigurací a tabulkou je vlastní nález

Nástroj dosud porovnával snímek se snímkem nebo měřil stav. Tady přibývá třetí
třída: **záměr proti skutečnosti uvnitř jednoho snímku.** Nakonfigurovaná
routa, která není v routovací tabulce, je FAIL i v běhu bez baseline.

Bez toho by routa, která nebyla nainstalovaná ani před migrací, prošla tiše —
a v režimu podle AR-10 (prohlídka jednoho zařízení) by se o statikách nedalo
zjistit nic.

### AR-16: Služba bez statik a bez BFD nemá ani řádek

Shodně s R-1: chybějící konfigurace se v bloku neprojeví vůbec, ani sekcí, ani
řádkem. Cena je táž a přijímá se vědomě — chybějící check je k nerozeznání od
toho, který prošel.

Konkrétně: prázdné sjednocení podle AR-14 → check nevrátí žádný finding.
V laborce nemá BFD 7 z 9 peerů, takže opačné rozhodnutí by report nafouklo
o řádky, které nic neříkají.

Sjednocení s AR-14 si neodporuje: „bez BFD" znamená bez konfigurace **a**
bez session **a** bez session v baseline. Služba, které session běží nebo
běžela, řádek dostane — jen ne tu, která BFD nikdy neměla.

### AR-17: Bez inventáře se hlásí stav, ne rozpor

V device scope (režim bez inventory) není záměr znám. Checky proto vypíšou
nainstalované routy a existující session s jejich stavem, ale **nehlásí**
„nakonfigurováno a chybí" ani „session bez konfigurace" — obojí by v tomhle
režimu bylo tvrzení o něčem, co nástroj nemůže vědět.

## Datový model

### Inventory (výstup parserů) — `schema_version` 3

`ServiceEntry` dostane dvě pole:

```yaml
- interface: et-0/0/8.113
  service_type: IPVPN
  routing_instance: L3VPN-CPE13-NNI
  bgp_neighbor: [198.11.13.2, 2001:db8:11:13::b]
  static_route:
    - rib: L3VPN-CPE13-NNI.inet.0
      prefix: 172.26.1.0/29
      next_hop: [198.11.13.2]
    - rib: L3VPN-CPE13-NNI.inet6.0
      prefix: 2001:eeee::/64
      next_hop: [2001:db8:11:13::b]
  bfd:
    - peer: 198.11.13.2
      minimum_interval: 3000
      multiplier: 3
      source: neighbor
```

Jsou to seznamy mapping, ne seznamy řetězců jako dosavadní pole. `from_dict`
dostane obdobu `_as_list` pro tenhle tvar.

Inventory **nedostane sekci na úrovni zařízení.** Nakonfigurovaná routa,
kterou nelze přiřadit žádné službě, se do inventory nedostane; pokud je
nainstalovaná, chytí ji `unassigned` z RPC. Rozhodnuto 2026-07-29.

### Mapování routy na službu

Dvě podmínky současně:

1. **subnet containment next-hopu** proti adresám rozhraní — táž mechanika jako
   `_bgp_neighbor_matches_interface`,
2. **shoda routing-instance** odvozené z RIB se `routing_instance` služby.

Bez druhé podmínky by next-hop, který náhodou padne do subnetu rozhraní v jiné
VRF, sedl na špatnou službu.

Žádný gate na `service_type` (na rozdíl od `_assign_bgp_neighbors`, který se
omezuje na `Internet`/`IPVPN`): L2 rozhraní nemá IP adresu, takže se
namatchovat nemůže. Filtr by tu byl duplikát podmínky, kterou už dělá shoda
adres.

### Mapování BFD na službu

BFD se doplňuje jen k peerům, které služba už má v `bgp_neighbor`. Přiřazení
tedy nevyžaduje novou mechaniku a musí běžet **po** `_assign_bgp_neighbors`.

### Snapshot — `schema_version` 3

Dvě nové oblasti faktů:

```json
"routes": {
  "L3VPN-CPE13-NNI.inet.0": {
    "172.26.1.0/29": {"next_hop": ["198.11.13.2"], "via": ["et-0/0/8.113"], "active": true}
  }
},
"bfd": {
  "198.11.13.2": {
    "state": "Down", "interface": "et-0/0/8.113", "remote_state": "AdminDown",
    "local_diagnostic": "None", "clients": ["BGP"],
    "detection_time": "0.000", "transmission_interval": "3.000", "multiplier": 3
  }
}
```

`routes` je klíčované `table → prefix`, což odpovídá identitě podle AR-12.

**Verze snímku se zvyšuje spolu s inventory.** Starý snímek by kontrolou verze
prošel, ale přišel by bez obou oblastí — a `failed_collectors` je nevypíše,
protože collector neselhal, on vůbec neběžel. `bfd_session_state` by pak viděl
záměr z inventáře, nula session, BGP Established → **FAIL na každém starém
snímku**. To není tichá zeleň, to je falešný poplach, a mlčenlivé dopočítání by
ho neodstranilo. Politika „stará data selžou hlasitě" už platí (AR-2).

Důsledek: `runs/ipv6/` a `runs/ipv6-live-2026-07-29/` přestanou jít přehrát
a je potřeba nový capture.

### `models/scope.py`

`Selectors` dostane:

- `static_routes` — seznam mapping `{rib, prefix, next_hop}`. Slouží zároveň
  jako filtr (výběr podle `(rib, prefix)`) i jako záměr pro AR-15.
- `bfd_peers` — seznam mapping `{peer, minimum_interval, multiplier, source}`.
  **Jen záměr.** Session se vybírají přes existující `bgp_neighbors`; jsou to
  dvě různé věci a slévat je do jednoho seznamu by znamenalo držet je v synchronu.

`Scope.select()` má **dvě** cesty a obě musí nové oblasti znát:

- `FACT_AREAS` (řídí větev pro device scope),
- explicitní výčet ve větvi pro service scope.

Kdyby se doplnila jen jedna, rozbije se to tiše přesně v režimu, který AR-10
označuje za doporučený způsob prohlídky jednoho zařízení. `_empty()` vrací
`{}`, což je pro obě nové oblasti správný tvar.

### `models/result.py`

`RunResult.unassigned` dostane klíče `static_routes` a `bfd_sessions`, tvarem
i chováním shodné s dnešním `bgp_peers` — včetně pojistky, že v device scope se
vrací prázdný seznam.

- `static_routes` — routa v `facts["routes"]`, kterou si nenárokuje žádný scope.
  Sem spadne mgmt statika z `.4` (`0.0.0.0/0 → 10.0.0.2` přes `fxp0.0`) i každá
  routa, kterou parser neuměl přečíst.
- `bfd_sessions` — session, jejíž `session-neighbor` není v žádném
  `bgp_neighbors`.

## Sběr

### Collector `routes`

RPC `get_route_information` s `protocol="static"`. **Bez** `all=True` —
ta varianta přidá jen `__juniper_private*` tabulky, což je šum.

Výstup je bezpečné procházet defenzivně: `rt` může mít víc `rt-entry`, i když
filtr na protokol by měl vrátit jednu.

### Collector `bfd`

RPC `get_bfd_session_information` s `detail=True` — kvůli `bfd-client`
a `remote-state`; stručná varianta ani jedno nemá.

Prázdná odpověď (`.4` má 0 session) je **platný stav, ne chyba**. Collector
vrátí `{}`.

## Checky

### `static_route_status`

- `mode = BOTH`, `requires = ("routes",)`, `requires_inventory = False`
- jeden finding na `(RIB, prefix)`, přes sjednocení podle AR-14

| stav | výsledek | hodnota | `baseline_value` |
|---|---|---|---|
| v tabulce, bez baseline nebo next-hop stejný | PASS | next-hop | next-hop |
| v tabulce, next-hop jiný než v baseline | **WARN** | next-hop | starý next-hop |
| nakonfigurovaná, není v tabulce | **FAIL** | `neni v tabulce` | starý next-hop, pokud byl |
| byla v baseline, v subjektu není ani v konfiguraci, ani v tabulce | **FAIL** | `chybi` | starý next-hop |
| v tabulce, v baseline nebyla | PASS | next-hop | — |

Poslední dva řádky vyžadují baseline; bez něj se neuplatní. V device scope se
neuplatní řádek „nakonfigurovaná, není v tabulce" (AR-17).

### `bfd_session_state`

- `mode = BOTH`, `requires = ("bfd", "bgp")`, `requires_inventory = False`
- jeden finding na peer, přes sjednocení podle AR-14

| stav | výsledek | hodnota |
|---|---|---|
| session Up | PASS | `Up` |
| session Down | **FAIL** | `Down` |
| bez session, BGP Established | **FAIL** | `bez session` |
| bez session, BGP mimo Established | **SKIP** | `BGP neni Established` |
| BFD běželo v baseline, v subjektu není ani v konfiguraci | **FAIL** | `BFD odstraneno` |
| session bez záměru v konfiguraci | **WARN** | `bez konfigurace` |

`baseline_value` nese předchozí stav session, takže sloupec ZMENA u spadlé
relace ukáže `bylo Up`.

Předposlední řádek je regrese ochrany, kterou migrace nemá tiše shodit ze
stolu — proto FAIL, ne WARN. Poslední řádek je detektor díry v průchodu podle
AR-13; v device scope se neuplatní stejně jako `bez session` (AR-17).

Vazba na stav BGP je záměrná: BFD relace nemůže naběhnout, dokud neběží BGP,
takže bez ní by `L3VPN-CPE14-UNI` v laborce dostalo **dva FAIL řádky za jednu
příčinu**. V ostrém běhu by se to opakovalo u každé nedojeté služby.

## Formát reportu

Ploše, bez podřádků — F-2 zůstává ve vlně 3 a tento návrh renderer nemění.

**Identita routy jde celá do popisku, next-hop je hodnota.** To není kosmetika:
sloupec ZMENA se v `view.change_text` skládá jako `bylo <baseline_value>`, kde
`baseline_value` odpovídá `value`. Kdyby hodnota nesla `prefix -> next-hop`,
ZMENA by u přesměrované routy vypsala celý pár dvakrát. Jméno RIB tedy jde do
kvalifikátoru popisku spolu s prefixem, ne na podřádek a ne do hodnoty:

```
CHECK                                                   HODNOTA        ZMENA
 PASS | Staticka routa (inet.0 198.62.1.0/29)         : 152.11.13.2
 WARN | Staticka routa (inet6.0 2001:aaaa::/64)       : 2001:abcd::c   bylo 2001:abcd::b
 FAIL | Staticka routa (L3VPN-...inet.0 172.26.1.0/29): neni v tabulce bylo 198.11.13.2
 FAIL | BFD (198.11.13.2)                             : Down           bylo Up
 SKIP | BFD (198.11.14.2)                             : BGP neni Established
```

Použit je idiom `check (kvalifikátor)` z vlny 1 (jako `Interface errors
(et-0/0/8)`), ne idiom `BGP status` s peerem schovaným ve větě. Nové řádky mají
být rovnou ty lepší; sjednocení starých patří k F-14.

Věta zůstává v `message`, hodnota je krátká — F-7. Popisek statické routy je
nejširší v celém reportu (`Staticka routa (L3VPN-CPE13-NNI.inet6.0
2001:eeee::/64)` je 54 znaků); podle AR-5 se sloupce počítají z obsahu a
neořezává se, takže to blok rozšíří, ale nic neutne.

## Chování při chybách

- Selhání kteréhokoli z nových collectorů zneplatní jen svůj check
  (`requires`), zbytek běhu pokračuje.
- Prázdný BFD výstup ani prázdná tabulka statik nejsou chyba.
- Neparsovatelný next-hop (jiný tvar než adresa) se přeskočí a routa se
  nenamapuje — skončí v `unassigned`, pokud je nainstalovaná.

## Testy

- **Parsery, oba v zámku, inline XML** ve stylu `tests/parsers/test_family_split.py`:
  globální v4, globální v6 přes `rib`, VRF v4, VRF v6 přes `rib`, mgmt statika
  (nemapovatelná), BFD na `neighbor`, BFD na `group`, BFD na obojím
  s **různými** timery (specifičtější musí vyhrát), BFD na `protocols bgp`,
  IPv6 peer dědící ze skupiny.
- **Collectory** proti skutečným odpovědím z laborky
  (`runs/bfd-static-2026-07-29/rpc/`), včetně prázdného BFD výstupu z `.4`.
  Fixtures se uloží do `tests/fixtures/rpc/junos/` a `junos-evo/`.
- **Checky** nad ručně sestavenými dicty — checky na XML nesahají.
- **Invariant nad skutečnými daty**: každý nový řádek má popisek i hodnotu
  (rozšíření `test_every_row_has_a_label_and_a_value_on_real_data`).

**Mutační disciplína z vlny 1** (T13a/T13b): nejdřív zavést mutanta, ověřit, že
test padne, teprve pak psát implementaci. Povinně u:

- průchodu hierarchií BFD — mutant ignoruje `group` úroveň,
- normalizace jména RIB — mutant vrací vždy `inet.0`,
- podmínky shody routing-instance při mapování routy.

## Zapsané předpoklady

### Jména RIB migraci přežijí

`_aligned_baseline_data` (`engine.py`) přeslovňuje mezi baseline a subjectem
jen `data["interfaces"]`. Oblast `routes` je klíčovaná `table → prefix`
a přeslovnění nedostane, takže identita podle AR-12 předpokládá, že se jméno
RIB migrací nemění.

V laborce to platí — `L3VPN-CPE13-NNI.inet.0` i `.inet6.0` jsou na obou
zařízeních stejné. Sady instancí se ale liší (`mgmt_junos` je jen na `.4`),
takže předpoklad není zadarmo. Kdyby budoucí migrace přejmenovala VRF, každá
routa v ní se přečte jako chybějící + nová.

### Statiky bez adresy next-hopu

`discard`, `reject`, `next-table` a `qualified-next-hop` nemají adresu, kterou
by šlo porovnat se subnetem rozhraní, takže se na službu nenamapují.
Nainstalované spadnou do `unassigned.static_routes`; nenainstalované
a nemapovatelné zmizí beze stopy. Přijato vědomě 2026-07-29 — v laborce se
žádný takový tvar nevyskytuje.

### BFD jen pro BGP klienty

Parser hledá `bfd-liveness-detection` jen pod `protocols bgp`. V laborce mají
všechny session `client-name = BGP`. Session s jiným klientem by se do žádného
záměru netrefila a check by ji podle AR-14 ukázal jako „bez konfigurace" —
tedy hlasitě, ne tiše.
