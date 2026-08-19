# `checks/` — vyhodnocovací logika

Soubory: `base.py`, `registry.py`, `all.py`, `ifaces.py`, `bgp.py`, `evpn.py`,
`reachability.py`, `routes.py`, `bfd.py`, `deactivation.py` a prázdný `__init__.py`.

Dvě pravidla:

1. **Check nikdy nesahá na síť.** Nemá `Device`, socket ani timeout. `checks/base.py` má
   v docstringu explicitní zákaz importovat cokoliv z `migration_validator.connection`.
2. **Check nevrací status.** Vrací `Finding` s naměřeným výsledkem; status z něj odvodí
   framework.

Důsledek: celá rozhodovací logika je čistá funkce nad daty a testuje se offline, bez laborky.

Katalog všech checků s tolerancemi je v [../reference.md](../reference.md#1-katalog-checků).

---

## `base.py` — kostra

### `CheckContext`

Co check dostane:

| pole | obsah |
|---|---|
| `scope` | filtr (kvůli `service_type` a názvům rozhraní) |
| `subject` | fakta **už profiltrovaná** scopem |
| `baseline` | totéž z baseline snapshotu, nebo `None` |
| `config` | tolerance a severity |
| `failed_collectors` | `{oblast: chybová hláška}` ze snapshotu |

Check tedy **nemůže omylem sáhnout na cizí službu** — filtrování je zodpovědnost scope
vrstvy a testuje se zvlášť.

### `Check`

```python
class Check(ABC):
    id: str                       # "bgp_session_state"
    title: str                    # "Stav BGP session"
    label: str                    # "BGP status" — popisek sloupce CHECK
    mode: Mode                    # state | compare | both
    requires: tuple[str, ...]     # oblasti z facts/probes, např. ("bgp",)
    requires_inventory: bool
    service_types: frozenset[str] | None    # None = všechny
    default_severity: Severity

    def run(self, ctx) -> list[Finding]
```

`applies_to(scope)` vrací `True` pro **device scope vždy** (nemá podle čeho filtrovat)
a jinak porovnává `service_type`.

`label` je **povinný** a není to `title`: `title` je věta o checku („Stav BGP session"),
`label` je popisek sloupce `CHECK` v reportu („BGP status"). Použije se pro řádky, které
nevznikly uvnitř checku (skipy od frameworku), a doplní se i findingu, který si vlastní
popisek nenese. Bez toho spadl řádek na `id` checku a mezi hezkými popisky seděl
`SKIP | evpn_esi_status` — doplňuje ho proto `run_check()`, ne renderer, který by neměl
odkud vzít nic lepšího.

`describe()` je to, co vidí `mig-validate checks` a budoucí GUI.

### `run_check()` — jediná cesta ke statusu

Pořadí bran, kterými check projde:

1. `config.enabled(id)` je `False` → **prázdný seznam** (check v reportu vůbec není),
2. `applies_to(scope)` je `False` → prázdný seznam,
3. `requires_inventory` a scope je device → `SKIP` (`check vyzaduje inventory, snapshot ji neobsahuje`),
4. `mode == COMPARE` a není baseline → `SKIP` (`porovnavaci check bez baseline snapshotu`),
5. `check.id` není `deactivation_state` a `scope.is_deactivated` je `True` → `SKIP`
   (`sluzba je v konfiguraci deaktivovana ({reason})`) — brána je záměrně až za bránou 3
   (`requires_inventory`), protože device scope inventory nemá a nemá tedy ani z čeho
   příznak vzít; `deactivation_state` samotný check touhle bránou neprojde, jinak by nebylo
   co porovnat a služba by z reportu zmizela do `SKIP` bez důvodu,
6. některá oblast z `requires` je v `failed_collectors` → `SKIP` **s původní chybovou
   hláškou z capture**,
7. `check.run()` vyhodí výjimku → `SKIP` (`check selhal: ...`) — jeden rozbitý check nesmí
   zabít celý běh,
8. jinak se každý `Finding` převede na `CheckResult` přes `derive_status(outcome, severity)`.

Rozdíl mezi bodem 1–2 (prázdný seznam) a 3–7 (`SKIP`) je záměrný: *„sem to nepatří"* se
nemá počítat do souhrnu, *„nezměřeno"* ano.

`SKIP` z bodů 3–7 je **plnohodnotný řádek reportu**: dostane `label` z checku a krátký důvod
do `value` (`bez inventory`, `bez baseline`, `RI deactivated`, `interface deactivated`,
`RI + interface deactivated`, `collector selhal`, `check selhal`). Celá věta zůstává
v `message` pro sloupec `NALEZ` a pro strojový výstup — dokud řádek hodnotu neměl,
sahal renderer právě po té větě a u selhaného collectoru (věta o RPC chybě, 190 znaků)
roztáhla blok na 270 znaků šířky.

## `registry.py`

`@register` zapíše instanci pod `cls.id`; duplicita je `ValueError`. `all_checks()` vrací
abecedně seřazený seznam — proto je pořadí checků ve výstupu stabilní. `checks_for(scope,
config)` a `get_check(id)` jsou pomocné dotazy (engine používá `all_checks()` a filtruje
až v `run_check`).

## `all.py`

Importuje `bfd`, `bgp`, `deactivation`, `evpn`, `ifaces`, `reachability`, `routes`. Je to
samostatný modul **kvůli cyklickému importu**: `checks/ifaces.py` importuje `checks/base.py`,
takže `checks/__init__.py` nesmí importovat `ifaces`. `load_all()` je idempotentní no-op —
práci udělal import.

---

## `ifaces.py` — rozhraní

Sdílené prvky:

- **`is_transit(name)`** — allowlist `ge`, `xe`, `et`, `ae`. Jen na takových rozhraních mají
  counter-based checky výpovědní hodnotu.
- **`INTERNAL_PREFIXES`** — seznam interních rozhraní (`fxp`, `lo0`, `irb`, `vtep`, `lsi`, …).
  Na nich counter checky vrací **`SKIP`, ne WARN**: `SKIP` znamená „tenhle test sem nepatří",
  `WARN` „něco je špatně". Kdyby interní rozhraní trvale svítila oranžově, operátor si zvykne
  výstup přeskakovat a nástroj ztratí smysl.
- **`percent_change(old, new)`** — vrací `None` při nulové baseline (dělit nulou nelze).
  Používá ho i `bgp.py` a `evpn.py`.

Klasifikace je vlastnost **checku, ne scope**: `irb.14` counter checky nedostane, ale je to
pořád plnohodnotná Internet služba a ARP, ping i BGP checky na něm proběhnou normálně.

### `interface_state` (state, critical)

`admin_status` i `oper_status` musí být `up`. Běží na **všech** rozhraních scope, včetně
interních — u nich má stav smysl, na rozdíl od counterů. Bez dat vrací `SKIP`.

**Jeden Finding na fakt, ne na rozhraní**: `admin_status` a `oper_status` se hlásí jako dva
samostatné řádky (label `Interface admin status (<jméno>)` / `Interface operational status
(<jméno>)`), takže
report umí ukázat, který z obou je rozbitý, ne jen že „rozhraní není v pořádku". Zpráva je
`<jméno>: admin_status <stav>` resp. `<jméno>: oper_status <stav>`, hodnota ve sloupci je
stav s velkým první písmenem (`Up`, `Down`).

### `interface_errors` (state, advisory)

Součet `input_errors`, `output_errors`, `framing_errors` musí být 0. Běží jen na
**tranzitních fyzických** rozhraních (`is_physical()` — bez tečky v názvu): logická
jednotka vlastní chybové countery na žádné z platforem nemá, plní se nulami
(`collectors/interfaces.py`), takže řádek „bez chyb" na unitu by tvrdil měření, které
neproběhlo. Jeden Finding na rozhraní (label `Interface errors (<jméno>)`) — countery se
do zprávy sesypou dohromady (`input_errors=3`), na rozdíl od
`interface_state`/`interface_traffic` se nerozpadají na samostatné řádky.

Když scope obsahuje jen tranzitní unity, žádné fyzické rozhraní ne, dostane `SKIP` se
zprávou, že countery nese jen fyzické rozhraní — ne zavádějící `SKIP` ze sdílené větve
„není tranzitní rozhraní", protože tranzitní rozhraní tu jsou, jen bez counterů.

### `interface_traffic` (both, advisory)

- **bez baseline** (nebo když rozhraní v baseline není): `require_nonzero` → `input_pps`
  i `output_pps` musí být > 0, jinak `broken` → WARN se zprávou `<jméno>: <input_pps|
  output_pps> <hodnota> pps` (např. `et-0/0/8: input_pps 0 pps`);
- **s baseline**: pokles v procentech proti `tolerance_percent` (default −60 %). Do `details`
  se zapíše změna per směr, do `baseline`/`subject` surová čísla.

**Jeden Finding na směr, ne na rozhraní**: `input_pps` (label `Interface traffic in (<jméno>)`)
a `output_pps` (label `Interface traffic out (<jméno>)`) jsou dva samostatné řádky, takže report
ukáže pokles jen na tom směru, kde se opravdu stal.

**Každý popisek nese jméno rozhraní v závorce.** Scope drží fyzické i logické rozhraní, takže
bez něj by v bloku stály dvojice řádků se stejným popiskem, jinými hodnotami a protichůdnými
sloupci `ZMENA` — a nešlo by poznat, které rozhraní je které.

Baseline data má už přejmenovaná rozhraní — viz `engine._aligned_baseline_data()`.

### `traffic_ceased` (compare, advisory, **výchozí stav: vypnuto**)

Obrácená logika: ověřuje, že na **starém** rozhraní provoz po migraci klesl k nule. Chytá
zapomenuté vypnutí a duplicitní forwarding. Vyžaduje třetí capture (starý box po migraci) —
v CLI je to normální `capture` + `evaluate`, žádný speciální režim.

`SKIP` (ne WARN) dostane, když rozhraní v baseline chybí nebo když v baseline nebyl žádný
provoz — utichnutí se pak nedá ověřit.

---

## `bgp.py`

Oba checky běží jen na `Internet` a `IPVPN` a bez peerů ve scope vrací `SKIP`.

### `bgp_session_state` (both, critical)

- stav ≠ `Established` → `broken` → **FAIL**. Nese `baseline_value`, takže sloupec `ZMENA`
  ukáže `bylo Established` — regrese je vidět přesně tam, kde na ní záleží,
- stav `Established`, ale **v baseline byl jiný** → **PASS** se zprávou
  `stav se zmenil X -> Established`. Tahle větev je dosažitelná jen se stavem `Established`
  (horší stavy odejdou výš), takže pokrývá právě a jen případ, kdy se relace během migrace
  **zlepšila** — a zlepšení není varování (rozhodnutí R-2). Změna nezmizí: pojmenuje ji
  zpráva a sloupec `ZMENA` píše předchozí stav,
- stav `Established` a shodný (nebo bez baseline) → PASS.

Každý Finding nese `label=f"BGP status ({peer})"` a `family` odvozenou `peer_family()` z adresy peeru —
report tak řádek zařadí do sekce `IPv4`/`IPv6`.

### `bgp_prefix_counts` (compare, advisory)

Porovnává `received` / `accepted` / `advertised` / `active` proti
`tolerance_percent` (default −10 %). Peer, který v baseline není, dostane `SKIP` — ne PASS
(zpráva `<peer>: peer neni v baseline snapshotu, nelze porovnat`).

**Počty se drží a porovnávají za každou RIB zvlášť** (`bgp.rtarget.0`, `inet.0`,
`bgp.l3vpn.0`, ...) — collector je tak i uložil (viz [collectors.md](collectors.md#bgppy--stav-peerů-a-počty-prefixů)).
Chybí-li konkrétní RIB v baseline u jinak spárovaného peeru, dostane ta dvojice vlastní `SKIP`
(`<peer>/<rib>: RIB neni v baseline, nelze porovnat`), ne mlčky spočítanou nulu. Rodinu řádku
odvozuje `peer_family()` z **adresy peeru**, ne z názvu RIB — název ji nemusí nést vůbec
(`bgp.l3vpn.0`), a peer s IPv4 adresou nesoucí zároveň IPv6 RIB se celý zařadí do sekce IPv4
(zdokumentované omezení).

Label řádku je `<klíč>-prefix-count` (např. `active-prefix-count`) a identita jde do
`group=f"BGP {peer} / {rib_name}"`, takže report má pět samostatných řádků na RIB seskupených
pod jmenovaný nadpis peeru a RIB, ne jeden souhrnný.

Porovnává se **s tolerancí, ne 1:1**. Zkušenost z JSNAPy je, že přesná shoda generuje
množství FAILů kvůli rozdílu několika rout, což není signifikantní. Růst počtu prefixů
problém není.

---

## `evpn.py`

Sdílená funkce **`_is_up(status)`** porovnává jen část před lomítkem: lokální rozhraní v ESI
hlásí `Up/Forwarding`, VPWS rozhraní jen `Up`. Porovnání na přesnou rovnost by to první
označilo za rozbité.

Žádný z těchto checků neobsahuje větev na platformu — rozdíl MX (`virtual-switch` /
`bridge-domain`) vs. EVO (`mac-vrf` / VLAN) pohltil collector.

### `evpn_vpws_status` (both, critical, jen E-Line)

Vyhodnocuje se **per SID a per peer**, ne jen podle stavu rozhraní a shody dvou čísel SID
jako dřív — stav se teď čte výhradně z `evpn-vpws-sid-pe-status` (`collectors/evpn.py`,
`EvpnVpwsCollector`). Na rozhraní s víc než jednou instancí se popisek kvalifikuje jménem
rozhraní (`qualified()`), aby řádky obou instancí nesplynuly.

Na jedno rozhraní vzniká postupně:

- **stav rozhraní** (`Up` → OK, jinak BROKEN),
- **local i remote SID** — hodnota SID jde jako `INFO` řádek (nese číslo, ne stav, nemá
  proti čemu být PASS/FAIL) a nemá `baseline_value`, takže sloupec `ZMENA` u ní zůstává
  prázdný,
- **peer local strany** — bez peerů je to očekávaný stav u single-homed rozhraní (`INFO`,
  ne chyba); u multi-homed přijde `INFO` navíc s módem, ESI a rolí,
- **peer remote strany** — remote peer musí existovat vždy; jeho absence je `BROKEN` na
  dvou řádcích (`... PE` a `... status`), protože chybějící druhá strana SID znamená
  nenakonfigurovaný nebo spadlý remote PE. `status == "resolved"` (case-insensitive) je OK,
  cokoliv jiného BROKEN.

### `evpn_esi_status` (both, critical, jen E-LAN)

Stav lokálního rozhraní v segmentu musí být `Up`; do zprávy se přidá DF (IP adresa
zvoleného designated forwardera).

### `evpn_mac_count` (both, advisory, jen E-LAN)

Iteruje instance a v nich domény (klíčované VLAN id, u vlan-based `"-"`). Label je
`instance/vlan`, u vlan-based jen `instance`.

- **bez baseline**: 0 naučených MAC → `broken` → WARN,
- **s baseline**: pokles proti `tolerance_percent` (default −60 %) → WARN. Nula MAC je WARN
  i tehdy, když by pokles do tolerance vešel.

---

## `reachability.py`

Tři checky, `arp_present`, `nd_present` a `ping_reachability` — ARP je IPv4 varianta, ND
IPv6 protějšek. Všechny mají `requires_inventory = True` (na device scope tedy vrací `SKIP`)
a běží jen na `Internet` a `IPVPN`. Všechny jsou vědomě **best-effort** — CPE může být
vypnuté nebo blokovat ICMP — proto default severity `advisory`.

**Každý check vrací jeden `Finding` na záznam** (ARP/ND) resp. **na cíl** (ping) — report
tiskne řádky jednotlivě, ne jako souhrnnou větu.

Sdílené pomocné funkce:

- **`owning_prefix(address, prefixes)`** — který nakonfigurovaný rozsah danou adresu
  obsahuje. Používá se v `details["address"]`, aby report u služby s víc rozsahy jedné
  rodiny popsal, ke kterému rozsahu řádek patří; report ho vypíše v labelu, jen když má
  rodina víc než jednu adresu (`view.py::_row`, `qualify=len(own) > 1`) — u jediné adresy je
  zbytečný, protože už je v hlavičce sekce.
- **`link_local_is_configured(scope)`** z `migration_validator/addressing.py` (používá ji
  `nd_present` i `probes/ping.py` — od AR-41 jeden společný výskyt místo dřívější duplicity)
  — má služba link-local adresu přímo nakonfigurovanou pod rozhraním? Testuje se
  **přítomnost, ne výlučnost**: stačí, aby mezi
  nakonfigurovanými adresami byla jedna link-local, klidně i vedle běžné routovatelné, a
  vrací `True`. Link-local sousedé se objeví u každého IPv6 rozhraní a o zákaznické službě
  sami o sobě neříkají nic — proto se jinak vyřazují. Existují ale nasazení, kde služba
  link-local používá — pak jsou to přesně ti sousedé, se kterými služba mluví, a filtr je
  musí nechat projít. Rozhoduje konfigurace, ne heuristika.

### `arp_present` (state, advisory)

- **žádná IPv4 adresa nakonfigurovaná** (`scope.selectors.local_ipv4` prázdné) → **žádný
  Finding** — služba bez IPv4 nemá mít ARP nález vůbec, natož WARN za souseda, který nikdy
  nemohl existovat. Dřív se vracel `SKIP` označkovaný `family=4`, jenže právě ta značka si
  v bloku vynutila sekci rodiny, kterou má renderer vynechat (rozhodnutí R-1);
- žádný ARP záznam na rozhraních služby → `broken` → FAIL/WARN, zpráva `na rozhranich
  sluzby neni zadny ARP zaznam`, `value` = `zadny zaznam`;
- jinak **jeden `Finding` na ARP záznam**: zpráva `ARP zaznam <ip>`, `label="ARP"`,
  `family=4`, `value` = `<mac> -> <ip>` (`?` když MAC chybí).

### `nd_present` (state, advisory)

Zrcadlí `arp_present` pro IPv6:

- žádná IPv6 adresa nakonfigurovaná → **žádný Finding** (viz `arp_present` výše);
- link-local sousedé se **vyřadí**, pokud služba sama nemá link-local jako nakonfigurovanou
  adresu (`link_local_is_configured()`);
- žádný **použitelný** ND záznam nezbyde → `broken`, zpráva `na rozhranich sluzby neni zadny
  pouzitelny ND zaznam` (všimni si slova „pouzitelny" navíc oproti ARP — právě kvůli
  odfiltrovaným link-local sousedům), `value` = `zadny zaznam`;
- jinak **jeden `Finding` na ND záznam**: zpráva `ND zaznam <ip>`, `label="ND"`, `family=6`,
  `value` = `<mac> -> <ip>`, `subject` navíc nese `state` (ND má na rozdíl od ARP stav
  záznamu).

### `ping_reachability` (state, advisory)

Čte hotové výsledky ze snapshotu — cíle se resolvovaly už při `capture` (ARP/ND → ping,
`probes/ping.py`).

- lokální subnet nad P2P prahem (IPv4 širší než `/30`, IPv6 širší než `/126`), do kterého
  nepadl žádný cíl → `SKIP` na subnet (`<sit>: zadny cil - subnet vetsi nez /30, fallback by
  cil jen hadal`, `value` `<sit>  bez cile (subnet > /30)`) — resolver tam fallback vědomě
  nepouští (viz `IPV4_FALLBACK_MIN_PREFIX` v `probes/ping.py`) a mlčení by se četlo jako
  „zkontrolováno OK";
- ve snapshotu nejsou pro tenhle scope žádné cíle a žádný takový subnet to nevysvětluje →
  `SKIP` (`pro tento scope nejsou ve snapshotu zadne cile pingu`);
- probe bez rozpoznané rodiny (`family` mimo `4`/`6`) → vlastní `SKIP` jmenující cíle
  (`probe bez rodiny nelze vyhodnotit: ...`) — jinak by probe z výsledku tiše zmizel, místo
  aby řekl, že se nevyhodnotil;
- jinak **jeden `Finding` na cíl**, seskupené podle `family`:
  - odpověděl aspoň jeden paket → `ok`, zpráva `<cil>: odpovedelo N z M`,
  - neodpověděl žádný → `broken`, zpráva `<cil>: neodpovedel (M paketu)`.

`value` je `<received>/<sent>` a u úspěšné odpovědi ještě `  <rtt> ms` (chybí, pokud se
nepodařilo změřit RTT), pak vždy `  <cil>` — např. `5/5  2.1 ms  10.1.1.1`. U neúspěchu
`  <cil> neodpovedel`. `details` nese `resolved_from` (`arp` | `nd` | `subnet-fallback`) a
`address` (`owning_prefix()`), takže je z výsledku vidět, který nakonfigurovaný rozsah cíl
zastupuje a jestli byl zjištěný z ARP/ND, nebo dopočtený ze subnetu.

---

## `routes.py` — statické a agregátní routy

### `static_route_status` (both, critical)

Jediný check, který **porovnává konfigurační záměr proti naměřené realitě**. Ostatní checky
se ptají jen „je to nahoře?"; tenhle se ptá „je tam to, co jsi si objednal?".

**Čte jen záznamy s `protocol == "static"` (fakta) / `route_type == "static"` (záměr).**
Od 2026-08-19 QNH má agregátní routa vlastní check (`aggregate_route_status` níž) — tenhle
si z konfigurace i z faktů předem odfiltruje agregáty, aby se dva mechanismy porovnání
(next-hop u statiky, jen přítomnost u agregátu) nemíchaly v jednom cyklu. Chybějící klíč
`protocol`/`route_type` znamená záznam z doby před schema 10/6, kdy se sbíraly/parsovaly jen
statiky — default je tam „static", ne chyba, takže starý baseline proti novému subjektu
degraduje elegantně místo pádu.

**Iteruje přes sjednocení tří zdrojů** (AR‑14) — konfigurace subjektu (`Selectors.static_routes`),
měření subjektu (`facts["routes"]`) a měření baseline. Každý z nich zavírá jednu díru:

| bez tohoto zdroje | co by tiše zmizelo |
|---|---|
| konfigurace | rozpor „nakonfigurováno, není v tabulce" |
| měření subjektu | v režimu bez inventory by nebylo co vypsat |
| měření baseline | routa vyřazená z konfigurace — do selektorů subjektu se nedostane, takže by se scope na její chybění nikdy nezeptal |

**Identita routy je dvojice (RIB, prefix), next-hop je hodnota.** Díky tomu se změna
next-hopu čte jako *změněná* routa — jeden řádek se sloupcem `ZMENA` — ne jako „routa zmizela
a jiná přibyla". Klíčování zůstává jen dvojicí i po zavedení per-hop next-hopů (QNH):
částečná deaktivace (některé next-hopy vypnuté, jiné ne) žije uvnitř jednoho záznamu, v jeho
`next_hops`, ne jako druhá identita — parser (`_static_routes_under`) vydá jeden `StaticRoute`
na `(rib, prefix)` bez ohledu na to, kolik next-hopů routa má a kolik z nich je aktivních.

**Při ECMP je hodnotou celá množina next-hopů, ne jejich pořadí v XML.** `_next_hop_text()`
je proto `", ".join(sorted(...))`: Junos pořadí `<nh>` negarantuje ani mezi platformami, ani
mezi verzemi — a tenhle check prochází právě tu hranici (`junos` → `junos-evo`). Bez setřídění
by dva snímky s týmiž next-hopy v jiném pořadí daly falešný
`WARN … next-hop se zmenil A, B -> B, A`, protože větev `ZMENA` porovnává hodnoty jako
řetězce. Setříděný výpis je navíc deterministický.

Setřídění se týká **jen hodnoty**. Surová evidence, kterou nález nese do JSON výstupu
(`subject` / `baseline`), zůstává v pořadí z XML záměrně — evidence má být doslovná. Konzument
JSONu tedy může vidět setříděnou `value` a nesetříděný `next_hop` v evidenci; není to
nekonzistence, ale dvě různé role.

| situace | Outcome | status | `value` |
|---|---|---|---|
| v tabulce, next-hop shodný nebo bez baseline | `ok` | PASS | next-hopy setříděné a oddělené čárkou (`-` když žádný) |
| v tabulce, next-hop se proti baseline změnil | `degraded` | WARN | nový next-hop, `ZMENA` nese starý |
| v tabulce, ale bez hvězdičky (`active: false`), baseline taky neaktivní | `ok` | PASS | `neni aktivni` |
| v tabulce, ale bez hvězdičky, v baseline byla aktivní | `broken` | FAIL | `neni aktivni` |
| v tabulce, ale bez hvězdičky, bez baseline | `degraded` | WARN | `neni aktivni` |
| nakonfigurovaná, v tabulce není (service scope) | `broken` | FAIL | `neni v tabulce` |
| v baseline byla, v subjektu není | `broken` | FAIL | `chybi` |

Routa **v tabulce, ale bez hvězdičky** neforwarduje — Junos ji nevyhodí z výpisu, jen ji
přebije jiný zdroj. Je to jiná situace než `neni v tabulce` (ta routu z výpisu úplně vyhodí):
proto samostatná hodnota `neni aktivni`, ne stejná jako u chybějící routy. Bez baseline není
z čeho poznat, že neaktivní byla i předtím, takže se nejednoznačnost podle R‑2 neeskaluje na
FAIL, ale zůstává na WARN.

Poslední dva řádky rozlišuje `configured`, tedy „je routa v selektorech scopu": device scope
inventory nemá, jeho selektory jsou vždy prázdné, takže se tam hlásí `chybi`, ne
`neni v tabulce` — záměr není znám (AR‑17). Podmínka v kódu je
`configured and not is_device`; **konjunkt `not is_device` je nečinný**, protože `configured`
už ne‑device implikuje. Zůstává jako zapsaný záměr AR‑17, ne jako práce. V `bfd.py` vypadá
stejná podmínka stejně, ale **je živá** — tam se do odpovídající větve chodí právě
s `configured=False`. Nemají se harmonizovat.

Label je `<RIB> <prefix>` a identita jde do `group="Staticke routy"` — routy z různých RIB
tak skončí v jedné pojmenované skupině řádků, ne v samostatných podřádcích rozlišených jen
kvalifikátorem v popisku. `family` se odvozuje **z prefixu**, ne z názvu RIB, protože název
rodinu nést nemusí (`bgp.l3vpn.0`).

**Zapsaný předpoklad:** jména RIB migraci přežijí. `_aligned_baseline_data` v enginu
přeslovňuje mezi baseline a subjektem jen oblast `interfaces`; `routes` jsou klíčované
`table -> prefix` a přeslovnění nedostanou. Kdyby budoucí migrace přejmenovala VRF, každá
routa v ní by se přečetla jako „chybí" + „nová".

#### Anotace deaktivovaného next-hopu

Konfigurace může routu nechat aktivní jako celek a přitom deaktivovat jen **jeden**
z jejích next-hopů (`qualified-next-hop` jde deaktivovat individuálně — viz
[parsers.md](parsers.md#statické-routy-per-hop-next-hopy-a-agregáty-qnh-schema-6)). Takový
řádek se nesmí tvářit zdravě jen proto, že routa jinak dorazila do tabulky s aktivitou —
deaktivovaný prvek konfigurace je sám o sobě nález (rozhodnutí uživatele 2026-08-04, sdílené
se `checks/deactivation.py`).

`_annotate_inactive_hops()` běží nad každým řádkem, který dosáhl aktivní tabulky (`ZMENA` i
běžné OK/WARN/FAIL větve sdílené s `aggregate_route_status` přes `_presence_finding`), a
pro každý deaktivovaný next-hop zavolá sdílenou `deactivation_outcome()` — stejnou funkci,
jakou používá `deactivation_state` pro celou službu. Vyhrává nejhorší výsledek napříč hopy
(`BROKEN > DEGRADED > původní`); ke zprávě se připojí `; deaktivovany next-hop: <to[ via
<interface>]>` pro každý vypnutý hop a jedna souhrnná poznámka — `- migrace nedokoncena`, když
je výsledek `BROKEN` nebo hop byl v baselinu ještě aktivní (nově deaktivovaný), jinak
`(stejne jako v baseline)`, když byl vypnutý už tam.

**Žádný aktivní next-hop = záměr neforwardovat, ne rozpor.** Když jsou **všechny** next-hopy
routy deaktivované a routa přitom v tabulce vůbec není, je to informační stav se stejnou
sémantikou jako deaktivovaná routa jako celek (`deactivation_outcome`), ne `BROKEN … neni v
tabulce`: operátor next-hopy vědomě vypnul, takže absence v tabulce je očekávaná, ne rozpor.
Zpráva zní `<rib> <prefix>: vsechny next-hopy jsou deaktivovane`, případně s `- migrace
nedokoncena`, pokud byla routa v baselinu ještě naživu. Tahle větev se testuje **před**
běžnou `neni v tabulce` větví, jinak by ji ta obecnější odchytila první a vyrobila falešný
FAIL za next-hopy, které nikdo nechtěl mít aktivní.

---

### `aggregate_route_status` (both, critical)

Agregátní routa (`route_type == "aggregate"` / `protocol == "aggregate"`) **nemá next-hop** —
v konfiguraci nese `discard`/`reject`, ne next-hop, takže se s tabulkou porovnává jen
přítomnost a aktivita, ne next-hop jako u statiky (viz
[parsers.md](parsers.md#statické-routy-per-hop-next-hopy-a-agregáty-qnh-schema-6)). Sdílí se
`static_route_status` sjednocení tří zdrojů i větve „chybí"/„deaktivovaná" (obojí přes
společnou `_presence_finding()`); nesdílí se porovnání next-hopu a hop-level anotaci — u
agregátu žádný next-hop není, takže `was` a `now` vždy vyjdou stejné a `ZMENA` větev by
nikdy nenastala.

Zmizelý zákaznický agregát po migraci je signál výpadku — proto `CRITICAL`, stejně jako u
statik, ne `advisory`.

Label je `<RIB> <prefix>` stejně jako u statiky, ale identita jde do vlastní skupiny
`group="Agregatni routy"` — agregáty se v reportu nemíchají do stejné skupiny se statikami,
ačkoliv obojí je pořád „routa v tabulce".

| situace | Outcome | status | `value` |
|---|---|---|---|
| v tabulce a aktivní | `ok` | PASS | `v tabulce` |
| v tabulce, ale bez hvězdičky (`active: false`), baseline taky neaktivní | `ok` | PASS | `neni aktivni` |
| v tabulce, ale bez hvězdičky, v baseline byla aktivní | `broken` | FAIL | `neni aktivni` |
| v tabulce, ale bez hvězdičky, bez baseline | `degraded` | WARN | `neni aktivni` |
| nakonfigurovaná, v tabulce není (service scope) | `broken` | FAIL | `neni v tabulce` |
| v baseline byla, v subjektu není | `broken` | FAIL | `chybi` |
| nakonfigurovaná, v konfiguraci deaktivovaná a není v tabulce | `deactivation_outcome(...)` | podle baseline | `deaktivovana` |

---

## `bfd.py` — BFD session

### `bfd_session_state` (both, critical)

Stejně jako `static_route_status` **iteruje přes sjednocení tří zdrojů** (AR‑14): záměr
z konfigurace (`Selectors.bfd_peers`), session v subjektu a session v baseline.
**Peer, který BFD nikdy neměl, řádek nedostane** (rozhodnutí R‑1) — služba bez BFD tedy
v reportu nemá o BFD ani zmínku.

Check vyžaduje **dvě oblasti**: `("bfd", "bgp")`.

| situace | Outcome | status | `value` |
|---|---|---|---|
| session existuje, stav `Up` | `ok` | PASS | `Up` |
| session existuje, jiný stav | `broken` | FAIL | naměřený stav (`Down`, …) |
| session existuje, ale v konfiguraci služby není (service scope) | `degraded` | WARN | `bez konfigurace` |
| session není a není ani záměr, ale v baseline byla (service scope) | `broken` | FAIL | `neni ve sluzbe` |
| session není, v baseline byla (device scope) — **dnes nedosažitelné, viz níž** | `broken` | FAIL | `session zmizela` |
| záměr je, session není, **BGP není `Established`** | `SKIP` | SKIP | `BGP neni Established` |
| záměr je, BGP běží, session přesto není | `broken` | FAIL | `bez session` |

**Vazba na stav BGP je záměrná, ne kosmetická.** BFD nemůže naběhnout, dokud neběží BGP,
takže bez ní by služba se spadlým BGP dostala dva FAIL řádky za jednu příčinu. V ostrém běhu
by se to opakovalo u každé nedojeté služby a operátor by si zvykl výpis přeskakovat.

**`is_device` je tady nečinný — stejně jako stejně vypadající podmínka v `routes.py`.**
Do větve „není ani záměr" spadne jen peer, který v subjektu session nemá; a takový peer se do
sjednocení dostane jedině z baseline. Device scope se ale nikdy nespáruje: `device_scope()` má
`key=None` a každá klíčovací funkce v `scoping/matcher.py` na `None` vrací prázdný seznam,
takže scope skončí v `unmatched_subject` a engine mu baseline vůbec nepředá
(`_run_scope(..., None, None, ...)`). `baseline_sessions` je proto v device scope vždy prázdné
a řádek `session zmizela` se dnes nevypíše.

Kód se přesto drží. Kdyby budoucí formát snapshotu device scope baseline dal, nástroj by bez
tohoto rozlišení u každé zmizelé session tvrdil „v subjektu není nakonfigurované" —
o konfiguraci, kterou v tomhle režimu vůbec nevidí (AR‑17). Rozdíl je v **hodnotě**, ne jen
v hlášce: do reportu jde sloupec s hodnotou (F‑7/AR‑4), hlášku textový výpis nezobrazí. Je to
týž vzorec jako `MISSING_FROM_TABLE` vs `MISSING_ENTIRELY` v `routes.py`.

**Větev „session není a není ani záměr" (service scope) tvrdí jen o CLENSTVI ve službě, ne
o existenci na zařízení.** Peer, kterého tato služba už nenárokuje, může mít na zařízení dál
živou BFD session pod jinou službou — `engine.py:_unassigned_bfd_sessions` ji ukáže
v NEZAŘAZENO. Hláška „v subjektu není nakonfigurované" by tam lhala; `bez konfigurace` je navíc
odlišná hodnota od téhle větve, takže záměna nehrozí. Formulace `v baseline patril k teto
sluzbe, v subjektu uz ne` je pravdivá v obou případech, které do větve spadají (BFD ze zařízení
zmizelo i BFD přešlo pod jinou službu) — stejná oprava, jakou dřív dostala analogická větev
v `checks/bgp.py`.

---

## `deactivation.py` — stav deaktivace

### `deactivation_state` (both, critical)

Deaktivovaná služba se z inventory nikdy nevypouští — pořád se musí zmigrovat, takže zmizet
z výstupu by byla chyba, ne oprava (`models/scope.py`). Ostatní checky nad ní proto SKIPnou
(brána 5 v `run_check()` výše), ale nějaký řádek musí říct, *co* se změnilo proti baseline —
a to je práce tohohle checku. Je to **jediný check, který bránou 5 neprojde**: kdyby SKIPnul
jako všechny ostatní, služba by z reportu zmizela do SKIPu bez důvodu.

Nemá `requires` (nepotřebuje žádný collector) ani `service_types` (týká se všech typů služeb).
Vyžaduje inventory (`requires_inventory = True`) — bez ní scope neví, jestli je deaktivovaná.

**Zdravá služba (živá v subjektu i baseline) řádek nedostane vůbec** — ne SKIP, žádný nález.
Je to stejné rozhodnutí R‑1 jako jinde: co se nekontroluje, se v bloku neobjeví, a řádek
„služba je aktivní" by u každého zdravého bloku přibyl a neřekl nic.

| situace | Outcome | status | `value` |
|---|---|---|---|
| subjekt i baseline deaktivované | `ok` | PASS | důvod deaktivace subjektu |
| subjekt deaktivovaný, baseline běžela | `broken` | FAIL | důvod deaktivace subjektu |
| subjekt běží, baseline byla deaktivovaná | `degraded` | WARN | `aktivni` |
| subjekt deaktivovaný, bez baseline k porovnání | `SKIP` | SKIP | důvod deaktivace subjektu |
| subjekt i baseline běží | — | — (žádný nález) | — |

Důvod deaktivace (`Scope.deactivation_reason`) je jedna ze tří hodnot: `RI deactivated`,
`interface deactivated`, `RI + interface deactivated` — podle toho, jestli je deaktivovaná
routing instance, rozhraní, nebo obojí.

**Na pořadí větví záleží:** `neni ve sluzbe` se testuje **před** `BGP neni Established`.
Opačné pořadí by tiše ztratilo případ, kdy migrace shodila ze stolu ochranu, která tam byla,
a zároveň nedojelo BGP — což je přesně kombinace, kterou je potřeba vidět.

Label je `BFD (<peer>)`, `family` odvozená `peer_family()` z adresy peeru (sdílená
s `checks/bgp.py`), takže IPv6 peer zděděný z BGP skupiny skončí v sekci `IPv6`.
