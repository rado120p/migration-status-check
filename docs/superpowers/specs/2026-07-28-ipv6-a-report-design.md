# IPv4/IPv6 separace a nový report — návrh

**Datum:** 2026-07-28
**Stav:** schváleno, připraveno k tvorbě implementačního plánu
**Navazuje na:** [2026-07-24-migration-validator-design.md](2026-07-24-migration-validator-design.md)

## Účel

První reálné testy nástroje odhalily dvě skupiny problémů:

1. **IPv4 a IPv6 se v datech slévají.** Parser ukládá obě rodiny do jednoho seznamu
   `ip_address`, takže nic dál po řetězci neví, se kterou rodinou pracuje. Důsledky:
   ping běží jen na IPv4, zdrojová adresa pingu se vybírá náhodně a výsledky obou
   rodin se v reportu míchají na přeskáčku.
2. **Report je příliš řídký.** Souhrnný řádek na službu neukáže, co se vlastně
   kontrolovalo, ani na jaké adrese. Operátor při migraci potřebuje vidět službu
   popsanou celou — porty, adresy, virtual gateway, routing-instance — a výsledky
   rozdělené po rodinách.

Tento návrh řeší obojí. Jde o jednu změnu, ne dvě: nový report nelze postavit,
dokud rodiny nejsou oddělené v datech.

## Rozsah

**V rozsahu:**

- rozdělení IPv4/IPv6 v obou parserech, v inventory, ve scope selektorech a v reportu
- rozdělení virtual gateway adres na `virtual_gw_ipv4_address` / `virtual_gw_ipv6_address`
- ping s volbou `rapid`
- ping na IPv6 s cíli odvozenými z `show ipv6 neighbors` (nový collector `nd`)
- nový check `nd_present` — IPv6 obdoba `arp_present`
- pravidlo pro link-local adresy v ND výpisu
- rozpad BGP prefix counterů po RIB (bez toho nelze BGP rozdělit na rodiny)
- doplnění counterů `active-prefix-count` a `suppressed-prefix-count`
- MAC adresa ve výpisu ARP i ND
- nový textový report: blok na službu, sekce po rodinách, sloupec se změnou proti baseline
- zvýšení `schema_version` inventory i snapshotu a regenerace fixtures

**Mimo rozsah — patří do navazující spec „BFD a statické routy":**

- parsování statických rout a BFD z konfigurace do inventory
- mapování statických rout a BFD na jednotlivé služby
- RPC `show route protocol static` a `show bfd session`
- checky nad statickými routami a BFD

**Mimo rozsah trvale:**

- HTML report a GUI — řeší se až jako celkové GUI, ne jako offline export
- interaktivní rozbalování v terminálu (TUI)

**Vztah k druhé spec:** formát reportu se rozhoduje **jednou, tady**. Příklady
níže proto obsahují řádky BFD a statických rout označené jako budoucí. Přidání
checku pak znamená přidat řádek, ne přepsat renderer.

## Ověřeno proti laboratoři

Vše níže bylo změřeno proti `172.20.20.4` (vMX, Junos 24.2R1-S2.5) a `172.20.20.5`
(PTX10002-36QDD, Junos 25.2R1.8-EVO). Žádný z těchto údajů není odhad.

| co | zjištění |
|---|---|
| RPC pro `show ipv6 neighbors` | `get_ipv6_nd_information` — **stejné na obou platformách** |
| prvky odpovědi | `ipv6-nd-entry` → `ipv6-nd-neighbor-address`, `ipv6-nd-neighbor-l2-address`, `ipv6-nd-state`, `ipv6-nd-interface-name` |
| rozdíl platforem | MX obaluje texty novými řádky, EVO ne. `_text()` už stripuje, platformová větev není potřeba |
| `rapid` v ping RPC | PyEZ ho přijímá jako `rapid=True`. **Tvar XML se nemění** — `probe-results-summary` má stále `probes-sent`, `responses-received`, `packet-loss`, `rtt-average`. 5 paketů trvá ~0,3 s místo ~5 s |
| ping na IPv6 | funguje s obyčejným `host=`, žádný `inet6` přepínač. Junos si sám zvolí zdrojovou adresu |
| ping na link-local | **selže bez `interface=`**, s ním projde (ověřeno na `fe80::c66b:b8ff:fe48:0` přes `ge-0/0/0.0`) |
| `active-prefix-count`, `suppressed-prefix-count` | přítomné v odpovědi `get_bgp_neighbor_information` na obou platformách |
| jméno RIB | `bgp-rib/name` — `inet.0`, `inet6.0`, `L3VPN-CPE13-NNI.inet.0`, `bgp.l3vpn.0`, … |

---

## Architektonická rozhodnutí

### AR-1: Rodiny se rozdělí v celém řetězci, ne jen v inventory

**Rozhodnutí:** `ipv4_address` / `ipv6_address` a `virtual_gw_ipv4_address` /
`virtual_gw_ipv6_address` se propíší z parserů přes `ServiceEntry` a `Selectors`
až do reportu. Nikde po cestě se rodiny neslévají zpět.

**Důvod:** `probes/ping.py:source_address()` bere `virtual_gw[0]`, případně
`local_addresses[0]`. Nad slitým seznamem je rodina zdrojové adresy náhodná —
IPv6 ping se bez rozdělení až v `Selectors` opravit nedá. Rozdělení, které by
skončilo v inventory, by problém neřešilo.

### AR-2: Stará data selžou hlasitě

**Rozhodnutí:** inventory YAML dostane `schema_version: 2` a `load_inventory()`
na starším nebo chybějícím čísle vyhodí chybu. `models/snapshot.py:SCHEMA_VERSION`
se zvedne z 1 na 2 (mechanismus hlasitého pádu tam už existuje).

**Důvod:** `Selectors.from_dict()` doplňuje chybějící klíče prázdným seznamem.
Po přejmenování polí by každý snapshot v `runs/` i každá stará inventory tiše
přišly o adresy a nástroj by hlásil zdravé služby bez pingu. To je přesně
selhání, kterému se nástroj podle vlastního pravidla vyhýbá — *při nejednoznačnosti
se nikdy nehádá*. Tolerantní čtení obou formátů se nezavádí; migrace je jedno
spuštění parseru.

**Důsledek:** regenerovat `172.20.20.4.yml`, `172.20.20.5.yml` a inventory
fixtures v `tests/fixtures`. Snapshoty v `runs/` jsou jednorázová data z testů,
neregenerují se.

### AR-3: Rodina je datové pole, ne odvození z textu

**Rozhodnutí:** `PingTarget` a `Finding` (a přes něj `CheckResult`) dostanou pole
`family` s hodnotou `None` (společné pro obě rodiny), `4` nebo `6`. Renderer
rodinu nikdy neurčuje parsováním adresy z textu.

**Důvod:** tohle je vlastní oprava zadání „výsledky jsou na přeskáčku". Pořadí
v reportu (nejdřív společné, pak IPv4, pak IPv6) pak vyplývá z třídicího klíče,
ne z heuristiky nad řetězci. Renderer zůstává hloupý a testovatelný.

### AR-4: Řádek reportu je struktura, ne věta

**Rozhodnutí:** `Finding` dostane vedle `message` tři pole pro sazbu:

| pole | obsah |
|---|---|
| `value` | hodnota do sloupce POST (`Up`, `460 pps`, `Established`) |
| `baseline_value` | hodnota z baseline pro sloupec ZMENA |
| `delta` | už spočtený rozdíl (`-12 %`, `-1`) |

`message` zůstává tím, čím je dnes — krátkým důvodem, který se tiskne ve
sbaleném řádku ve sloupci NALEZ.

**Důvod:** požadovaný formát je `popisek : hodnota`, ale checky dnes vracejí
celou českou větu. Rozklad na `label` + `value` musí udělat check, protože jen
on ví, co je u dané veličiny hodnota a co vysvětlení. Totéž u `delta`: jen check
ví, jestli je hodnota číslo a co u ní znamená pokles. Kdyby to počítal renderer,
musel by hodnoty zpětně parsovat.

**Důsledek:** checky začnou vracet jemnější findingy. `interface_state` místo
jednoho verdiktu vrátí `Interface admin status` a `Interface operational status`
zvlášť. Mechanismus na to už existuje (`Finding.label`), jen se nevyužívá.

### AR-5: Šířky sloupců se počítají z obsahu a nikdy se neořezává

**Rozhodnutí:** šířky sloupců se v každém bloku služby spočítají z řádků, které
v něm skutečně jsou. Ořezávání se neprovádí. Jméno RIB nejde do sloupce s
popiskem, ale na odsazený podřádek pod řádkem `BGP status`.

**Důvod:** dnešní renderer ořezává (`{check.id:<22.22}`). Řádek
`0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b` má 39 znaků a
`BGP status (L3VPN-CPE13-NNI.inet6.0)` má 37 znaků — obojí přeteče pevné
sloupce, a to právě na IPv6 řádcích, kvůli kterým se celá změna dělá. Ořezaná
IPv6 adresa nebo ořezané jméno RIB jsou horší než nic. Bloky mohou být různě
široké; čtou se po jednom, ne jako jedna tabulka.

### AR-6: Rozbalování řídí stav, ne interaktivita

**Rozhodnutí:** výchozí výstup `evaluate` je souhrn + jeden řádek na službu +
**plný blok automaticky rozbalený u služeb se stavem WARN nebo FAIL**. `--detail`
rozbalí bloky u všech služeb včetně PASS. Nový výstup nahrazuje ten dosavadní.

**Zamítnuté varianty:**

- *TUI s klikáním* — nová závislost, nejde grepovat ani uložit do logu, nástroj
  by přestal být použitelný neinteraktivně.
- *HTML report* — řeší se až jako celkové GUI. Nabalovat na CLI ještě offline
  export by zvýšilo složitost nástroje bez odpovídajícího přínosu.
- *Široká tabulka s checkem na sloupec* — kompaktnější, ale šířka je tvrdý strop:
  každý nový check přidá sloupec. V blokovém formátu přidá řádek. Nástroj bude
  checky přibírat (BFD, statické routy a dál), takže formát nesmí růst do šířky.

### AR-7: BGP prefixy se ukládají po RIB, ne v součtu

**Rozhodnutí:** `BgpCollector` přestane sčítat countery přes všechny RIB jednoho
peeru a uloží je jednotlivě pod jménem RIB, včetně `active` a `suppressed`.
`bgp_prefix_counts` porovnává po RIB.

**Důvod:** bez jména RIB nelze BGP rozdělit na IPv4 a IPv6 — součet obě rodiny
slévá do jednoho čísla. Vedlejší efekt je ale stejně důležitý: dnes by pokles
prefixů v `inet6.0` kompenzovaný nárůstem v `inet.0` prošel jako beze změny.

**Rodina BGP řádku** se odvodí z adresy peeru (`ipaddress.ip_address(peer).version`),
ne ze jména RIB — jméno RIB nemusí rodinu obsahovat (`bgp.l3vpn.0`).

### AR-8: Link-local ND záznamy se ignorují, pokud nejsou nakonfigurované

**Rozhodnutí:** záznam z `fe80::/10` se zahodí, pokud daná služba nemá link-local
adresu explicitně nakonfigurovanou pod rozhraním — tedy pokud se žádná adresa z
`fe80::/10` nevyskytuje v `local_ipv6` daného scope. Je-li nakonfigurovaná,
záznam se použije a ping na něj dostane `interface=<logické rozhraní scope>`.

**Důvod:** link-local sousedé se v ND tabulce objeví u každého IPv6 rozhraní
(v laboratoři jich je většina) a nevypovídají nic o zákaznické službě. Zároveň
ale existují nasazení, kde je link-local jediná nakonfigurovaná adresa a pak je
to legitimní cíl. Rozhoduje konfigurace, ne heuristika. Ověřeno, že ping na
link-local bez `interface=` selže.

### AR-9: IPv6 fallback jen na point-to-point prefixech

**Rozhodnutí:** `subnet_fallback()` zůstává IPv4 úvahou. Pro IPv6 se použije jen
u prefixů `/126` a delších; u kratších se fallback přeskočí a služba bez ND
záznamu dostane SKIP.

**Důvod:** vyloučení network a broadcast adresy je IPv4 uvažování — v IPv6 je
adresa se samými nulami subnet-router anycast, ne broadcast. Střelit si první
adresu z `/64` znamená s jistotou neúspěšný ping, který se v reportu čte jako
nedostupné CPE. Poctivé SKIP je lepší než falešný FAIL.

### AR-10: `capture` zůstává sběrný příkaz

**Rozhodnutí:** `capture` sbírá fakta a zapisuje snapshot; žádné checky nespouští
a nedostane vlastní renderer. Výpis stavu jednoho zařízení se získá jako
`evaluate --snapshot X` bez `--baseline`, což funguje už dnes a dává STATE
verdikty. V tomto režimu se sloupec ZMENA nevykresluje.

**Důvod:** jinak by vznikl třetí renderer nad `Snapshot`, který by musel
duplikovat vyhodnocovací logiku, aby měl co tisknout do sloupce STAV.

---

## Datový model

### Inventory (výstup parserů)

```yaml
schema_version: 2
device: 172.20.20.5
interfaces:
  - interface: et-0/0/8.13
    description: INTERNET-CPE13-NNI
    service_type: Internet
    ipv4_address: ["152.11.13.1/30"]
    ipv6_address: ["2001:abcd:11:13::a/127"]
    virtual_gw_ipv4_address: []
    virtual_gw_ipv6_address: []
    routing_instance: null
    ...
```

Pole `ip_address` a `virtual_gw_ip_address` zanikají bez náhrady.

### Parsery — místa k úpravě

Oba soubory (`evo_parser.py`, `mx_parser.py`) se mění **v zámku**; liší se dnes
jen 146 řádky a tato změna se jich netýká. Čísla řádků níže jsou z
`evo_parser.py`; v `mx_parser.py` sedí s posunem do čtyř řádků.

| místo | změna |
|---|---|
| ř. 811–841 | rodiny se už dnes počítají zvlášť (`ipv4_addresses`, `ipv6_addresses`, `virtual_gw_ipv4_addresses`, `virtual_gw_ipv6_addresses`) — beze změny |
| ř. 865–871 | **přestane slévat** `unique(ipv4 + ipv6)`; `InterfaceConfig` ponese čtyři seznamy |
| ř. 923–924 | `InterfaceService` ponese čtyři pole |
| ř. ~1122–1126 | `_assign_bgp_neighbors` bude **family-correct**: v6 peer se páruje jen proti v6 adresám rozhraní |
| ř. 1498, 1542, 1673 | podmínky typu „rozhraní nemá žádnou adresu" se přepíšou na „nemá adresu ani v jedné rodině" |
| ř. ~1802 | seznam klíčů YAML výstupu + `schema_version: 2` |

`_assign_bgp_neighbors` je tímto load-bearing pro tři mapování: BGP sousedy dnes,
a ve druhé spec next-hopy statických rout a BFD. Family-correctness se proto
opravuje tady, dokud na tom stojí jen jedna věc.

### `models/scope.py`

```python
class Selectors:
    local_ipv4: list[str]
    local_ipv6: list[str]
    virtual_gw_v4: list[str]
    virtual_gw_v6: list[str]
    # ostatní beze změny
```

`FACT_AREAS` se rozšíří o `"nd"`. **Pozor na `_empty()`** — dnes vrací
`[] if area == "arp" else {}`, takže nový seznamový obor by tiše dostal `{}`.
Stejně tak `capture.py:LIST_AREAS`. `Scope.select()` dostane filtrovací větev
pro `nd` i v servisní cestě, nejen v device cestě.

### `models/result.py`

`ScopeResult` dostane `identity` — vše, co report potřebuje o službě vypsat a co
dnes končí ve `Scope.selectors` a do výsledku se nedostane:

```python
identity = {
    "description": ..., "service_type": ..., "service_subtype": ...,
    "routing_instance": ...,
    "ipv4": [...], "ipv6": [...],
    "virtual_gw_v4": [...], "virtual_gw_v6": [...],
}
```

Plní se v `engine._run_scope()` ze `scope.selectors` a `scope.key`, serializuje
se v `to_dict()` (tedy i do JSON reportu). Starý a nový port už k dispozici jsou
— `MatchInfo.baseline_interfaces` / `subject_interfaces`.

`Finding` a `CheckResult` dostanou `family`, `value`, `baseline_value`, `delta`
podle AR-3 a AR-4.

---

## Sběr

### Nový collector `nd`

Dvojče `collectors/arp.py`, RPC `get_ipv6_nd_information`, oblast `nd`.

Záznam:

```python
{"ip": ..., "mac": ..., "interface": ..., "state": ...}
```

Nad ním nový check `nd_present` — IPv6 obdoba `arp_present`, stejná severity
(advisory), `family = 6`. `arp_present` dostane `family = 4` a obě do `value`
tisknou `MAC -> IP`, ne jen počet záznamů.

Filtrování při resolvování cílů pingu:

- link-local podle AR-8
- záznamy bez MAC (`l2-address` = `none`) nebo ve stavu `unreachable` /
  `incomplete` **se do reportu dostanou**, ale **nestanou se cílem pingu** —
  střílet na neúplný záznam nemá smysl. V laboratoři takový existuje
  (`2001:db8::1`, `unreachable`, `none`).

### Ping

- `rapid=True` **vždy**. `parse_ping_result()` se nemění — tvar XML je ověřený
  v tabulce výše.
- cíle: IPv4 z `arp`, IPv6 z `nd` — stejný princip, jiný zdroj.
- zdrojová adresa se řídí **rodinou cíle**: pro v6 cíl `virtual_gw_v6[0]`,
  jinak `local_ipv6[0]`; pro v4 cíl obdobně.
- link-local cíl dostane navíc `interface=` s logickým rozhraním scope. Každý
  servisní scope má v selektorech právě jedno rozhraní (`scoping/builder.py`),
  takže je jednoznačné.
- `PingTarget` nese `family`.
- `subnet_fallback()` podle AR-9.

### BGP

```python
peers[address] = {
    "state": ..., "peer_as": ..., "routing_instance": ...,
    "ribs": {
        "inet.0": {"received": 4, "accepted": 4, "advertised": 3,
                   "active": 4, "suppressed": 0},
        "L3VPN-CPE13-NNI.inet6.0": {...},
    },
}
```

---

## Formát reportu

Šířky v příkladech jsou ilustrativní — skutečné se počítají z obsahu (AR-5).
Řádky `BFD` a `Static routes` jsou **budoucí** (druhá spec), uvedené proto, aby
se formát rozhodl jednou.

### Výchozí `evaluate` (s baseline)

```
Migrace: 172.20.20.4 (pre-migration) -> 172.20.20.5 (post-migration)

  2 PASS   1 WARN   1 FAIL   0 SKIP     Sparovano 4 sluzby, 0 nesparovanych

STAV  SLUZBA                    TYP       STARY PORT    NOVY PORT     RI               NALEZ
----  ------------------------  --------  ------------  ------------  ---------------  --------------------------
WARN  INTERNET-CPE13-NNI        Internet  ge-0/0/2.13   et-0/0/8.13   -                pokles received prefixu
PASS  L3VPN-CPE13-NNI           IPVPN     ge-0/0/2.113  et-0/0/8.113  L3VPN-CPE13-NNI  -
PASS  L3VPN-CPE14-UNI           IPVPN     ge-0/0/4.0    et-0/0/10.0   L3VPN-CPE14-UNI  -
FAIL  EVPN-VLAN-AWARE-INTERNET  Internet  ge-0/0/5.0    irb.14        -                zadny ARP zaznam, ping 0/1

========================================================================================================
 WARN  INTERNET-CPE13-NNI          Internet    ge-0/0/2.13 -> et-0/0/8.13     RI: -
========================================================================================================
 STAV | CHECK                        : POST (et-0/0/8.13)               | ZMENA PROTI ge-0/0/2.13
 -----+------------------------------+---------------------------------+------------------------------
 PASS | Interface admin status       : Up                               |
 PASS | Interface operational status : Up                               |
 PASS | Interface traffic in         : 460 pps                          | bylo 520 pps    -12 %
 PASS | Interface traffic out        : 330 pps                          | bylo 360 pps    -8 %

 -- IPv4  152.11.13.1/30 --------------------------------------------------------------------------------
 PASS | ARP                          : 0c:00:ef:5e:df:01 -> 152.11.13.2 |
 PASS | BGP status                   : Established                      |
      |   RIB inet.0                                                    |
 PASS | BGP active-prefix-count      : 2                                |
 WARN | BGP received-prefix-count    : 1                                | bylo 2          -1
 WARN | BGP accepted-prefix-count    : 1                                | bylo 2          -1
 PASS | BGP suppressed-prefix-count  : 0                                |
 PASS | Ping                         : 1/1  2.1 ms                      |
      | (budouci: BFD, Static routes)                                   |

 -- IPv6  2001:abcd:11:13::a/127 ------------------------------------------------------------------------
 PASS | ND                           : 0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b |
 PASS | BGP status                   : Established                            |
      |   RIB inet6.0                                                         |
 PASS | BGP received-prefix-count    : 0                                      |
 PASS | Ping                         : 1/1  2.4 ms                            |

(sluzby se stavem PASS jsou jen v tabulce nahore - rozbali je --detail)

NESPAROVANO
  (nic)
```

### Sloupec ZMENA — čtyři stavy

| situace | co se vytiskne |
|---|---|
| baseline vůbec není (`evaluate` bez `--baseline`) | sloupec ZMENA se nevykreslí |
| baseline je, ale check pro ni hodnotu nemá | `bez baseline` |
| `baseline_value == value` | prázdno |
| jinak | `bylo <hodnota>` a `<delta>` |

Explicitní `bez baseline` je podstatné: prázdné místo jinak znamená současně
*shodu* i *chybějící srovnání* (nový check, nová služba, spadlý collector), což
jsou opačné zprávy.

### Služba s virtual gateway

```
 -- IPv4  152.11.14.2/29   VGW 152.11.14.1 ---------------------------------------------------------------
```

### Pořadí

1. řádky bez rodiny (interface, společné)
2. IPv4
3. IPv6

Uvnitř sekce v pořadí registrace checků. Pořadí plyne z `family` (AR-3), ne z
třídění textu.

### Rozdělení souborů

`reporting/text_report.py` má dnes 124 řádků a tímto by narostl na násobek.
Rozdělí se na dvě věci s jedním účelem:

- **`reporting/view.py`** — `ScopeResult` → `ServiceView`: identita a řádky
  seskupené po rodinách. Čistá data, žádné formátování. Testuje se bez
  porovnávání mezer.
- **`reporting/text_report.py`** — sazba: šířky sloupců, sbalování, filtry.
  Stávající `filter_result()` zůstává.

---

## Chování při chybách

Beze změny proti stávajícímu návrhu; nové oblasti se do něj jen zařadí.

| situace | chování |
|---|---|
| collector `nd` selže | oblast zůstane prázdný **seznam**, checky nad `nd` vrátí SKIP (přes `failed_collectors()`) |
| služba nemá IPv6 adresu | sekce IPv6 se v bloku vůbec nevykreslí |
| služba má IPv6 adresu, ale žádný ND záznam | `nd_present` vrátí BROKEN (advisory), ping dostane SKIP, pokud prefix nedovolí fallback podle AR-9 |
| ping RPC selže | zaznamená se důvod (chování z commitu `c85bcd0` zůstává) |
| inventory nebo snapshot má starou `schema_version` | hlasitá chyba, návratový kód 2 |

---

## Testy

**Nové fixtures** se nahrají existujícím `--record-raw` proti laboratoři, ne ručně:

- `tests/fixtures/rpc/junos/nd.xml` a `tests/fixtures/rpc/junos-evo/nd.xml`
- odpověď ping s `rapid` pro obě platformy

**Jednotkové testy:**

- rozdělení rodin v obou parserech, včetně rozhraní jen s jednou rodinou
- family-correctness `_assign_bgp_neighbors` (v6 peer se nespáruje s v4 adresou)
- parsování ND: běžný záznam, záznam bez MAC, link-local se i bez konfigurace,
  link-local s konfigurací
- výběr cíle a zdroje pingu po rodinách; `interface=` u link-local cíle
- `subnet_fallback` pro IPv6: `/127` ano, `/64` ne
- BGP per-RIB včetně `active` a `suppressed`
- `ServiceView`: pořadí sekcí, zařazení řádků podle `family`

**Golden testy rendereru:**

- dual-stack služba s baseline (`INTERNET-CPE13-NNI`)
- služba s virtual gateway (`irb.14`)
- služba, kde baseline chybí → sloupec `bez baseline`
- běh bez `--baseline` → sloupec ZMENA se nevykreslí
- šířka sloupců u dlouhé IPv6 adresy — kontrola, že se **neořezává**

**Testy na hlasitý pád:** inventory i snapshot se starou `schema_version`.

**E2E:** nad regenerovanými `172.20.20.4.yml` a `172.20.20.5.yml`.

---

## Otevřené otázky pro navazující spec

Zaznamenané, aby se na ně nezapomnělo — **neřeší se tady**:

1. **Nemapované statické routy.** Konfigurace obsahuje statiku v `mgmt_junos`
   (`next-hop 10.0.0.2`), která padne do `10.0.0.15/24` na `fxp0.0`; ta je ale
   management a scope se z ní nikdy nestane. Routa by tiše zmizela.
   `RunResult.unassigned` už má přesně na tento tvar `bgp_peers` — přibude
   `static_routes`.
2. **Statická konfigurace není mezi rodinami symetrická.** IPv4 je pod
   `routing-options static`, IPv6 pod `routing-options rib <jmeno>.inet6.0 static`
   (uvnitř VRF stejně). Naivní `//static/route` najde obojí, ale ztratí
   příslušnost k RIB — a právě tu report zobrazuje.
3. **Co znamená změněná routa** oproti chybějící, a jestli je BFD down na službě
   bez nakonfigurovaného BFD stav SKIP, nebo PASS.
