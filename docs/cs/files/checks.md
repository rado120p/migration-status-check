# `checks/` — vyhodnocovací logika

Soubory: `base.py`, `registry.py`, `all.py`, `ifaces.py`, `bgp.py`, `evpn.py`,
`reachability.py`, `routes.py`, `bfd.py`, `core_protocols.py`, `deactivation.py`
a prázdný `__init__.py`.

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
    service_subtypes: frozenset[str] | None # AND ke service_types, None = nefiltruje
    default_severity: Severity

    def run(self, ctx) -> list[Finding]
```

`applies_to(scope)` vrací `True` pro **device scope vždy** (nemá podle čeho filtrovat)
a jinak porovnává `service_type` a — je-li nastaven — i `service_subtype` (obojí musí sedět,
je to AND, ne alternativa). Slouží k rozlišení rolí v rámci jednoho `service_type`, typicky
Core **transit** vs. Core **loopback** (vlna 2026-08-26): `service_types={"Core"}` samo
o sobě obě role nerozliší, `service_subtypes={"transit"}` je odstřihne od `isis_overview`,
který patří jen na lo0.0.

`label` je **povinný** a není to `title`: `title` je věta o checku („Stav BGP session"),
`label` je popisek sloupce `CHECK` v reportu („BGP status"). Použije se pro řádky, které
nevznikly uvnitř checku (skipy od frameworku), a doplní se i findingu, který si vlastní
popisek nenese. Bez toho spadl řádek na `id` checku a mezi hezkými popisky seděl
`SKIP | evpn_esi_status` — doplňuje ho proto `run_check()`, ne renderer, který by neměl
odkud vzít nic lepšího.

`describe()` je to, co vidí `mig-validate checks` a budoucí GUI.

### `run_check()` — jediná cesta ke statusu

Status se odvozuje z `Finding.outcome` a severity checku z `config`. `Finding` může nést
vlastní `severity`, která pro ten jeden řádek severity checku přebije — používá to
`arp_present`/`nd_present`: prázdná tabulka zůstává WARN, nerozřešený záznam je FAIL.

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
seznam seřazený podle `(order, id)` — `Check.order` je výchozí 0 (abecedně podle id, jako dřív); multicast checky mají 10–13, aby jejich řádky seděly pohromadě až za interface řádky. Tohle pořadí je zároveň pořadí řádků v bloku reportu. `checks_for(scope,
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

| situace | Outcome | status | `value` |
|---|---|---|---|
| pro scope nejsou žádná data o rozhraních | `SKIP` | SKIP | `bez dat` |
| `admin_status == "up"` | `ok` | PASS | `Up` |
| `admin_status != "up"` (chybí → `"unknown"`) | `broken` | FAIL | stav s velkým písmenem, např. `Down`, `Unknown` |
| `oper_status == "up"` | `ok` | PASS | `Up` |
| `oper_status != "up"` (chybí → `"unknown"`) | `broken` | FAIL | stav s velkým písmenem |

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

Fyzické tranzitní rozhraní, jehož fakta neobsahují **žádný** ze tří klíčů counterů, dostane
`degraded` (ne tiché `ok`) — rozhraní error countery prostě nevrací, což není totéž tvrzení
jako „změřeno, nula chyb": zpráva `<jméno>: chybove countery nebyly zmereny (rozhrani
nevraci error countery)`, `value` = `nezmereno`.

| situace | Outcome | status | `value` |
|---|---|---|---|
| service scope s L3 linkem, jehož L2 strana nemá tranzitní rozhraní | `INFO` | INFO | `mereno na L2 (<peers>) - viz blok/bloky nize` |
| service scope s L1 (layer1) rodičem, který tranzitní rozhraní pohlcuje | *(žádný nález)* | — | — |
| scope má tranzitní rozhraní, ale jen logické unity, žádné fyzické | `SKIP` | SKIP | `jen unity` |
| žádné tranzitní rozhraní vůbec | `SKIP` | SKIP | `netranzitni rozhrani` |
| fyzické tranzitní rozhraní, žádný ze tří klíčů counterů není přítomen | `degraded` | WARN | `nezmereno` |
| fyzické tranzitní rozhraní, součet counterů == 0 | `ok` | PASS | `bez chyb` |
| fyzické tranzitní rozhraní, součet counterů > 0 | `broken` | WARN (advisory) | čárkou spojené nenulové countery, např. `input_errors=3` |

### `interface_traffic` (both, advisory)

- **bez baseline** (nebo když rozhraní v baseline není): `require_nonzero` → `input_pps`
  i `output_pps` musí být > 0, jinak `broken` → WARN se zprávou uvádějící důvod, `<jméno>:
  <input_pps|output_pps> 0 pps, ocekavan nenulovy provoz`;
- **s baseline, baseline pps == 0 i subjekt pps == 0**: `ok`, zpráva `<jméno>: <key> stejne
  jako baseline (0 pps)` — `require_nonzero` se u baseline záměrně znovu neuplatňuje, protože
  služba, která provoz neměla už před migrací, nemá dostat FAIL jen za to, že ho pořád nemá;
- **s baseline jinak**: pokles v procentech proti `tolerance_percent` (default −60 %). Do
  `details` se zapíše změna per směr, do `baseline`/`subject` surová čísla.

**Jeden Finding na směr, ne na rozhraní**: `input_pps` (label `Interface traffic in (<jméno>)`)
a `output_pps` (label `Interface traffic out (<jméno>)`) jsou dva samostatné řádky, takže report
ukáže pokles jen na tom směru, kde se opravdu stal.

**Každý popisek nese jméno rozhraní v závorce.** Scope drží fyzické i logické rozhraní, takže
bez něj by v bloku stály dvojice řádků se stejným popiskem, jinými hodnotami a protichůdnými
sloupci `ZMENA` — a nešlo by poznat, které rozhraní je které.

Baseline data má už přejmenovaná rozhraní — viz `engine._aligned_baseline_data()`.

| situace | Outcome | status | `value` |
|---|---|---|---|
| service scope s L3 linkem, jehož L2 strana nemá tranzitní rozhraní | *(žádný nález)* | — | — |
| žádné tranzitní rozhraní vůbec | `SKIP` | SKIP | `netranzitni rozhrani` |
| bez baseline pro rozhraní, `require_nonzero` a pps == 0 | `broken` | WARN (advisory) | `0 pps` |
| bez baseline pro rozhraní, jinak | `ok` | PASS | naměřené pps |
| baseline pps == 0, subjekt pps == 0 | `ok` | PASS | `0 pps` |
| baseline pps == 0, subjekt pps > 0 | `ok` | PASS | naměřené pps |
| baseline pps > 0, pokles pod `tolerance_percent` | `broken` | WARN (advisory) | naměřené pps |
| baseline pps > 0, v toleranci (i nárůst) | `ok` | PASS | naměřené pps |

### `traffic_ceased` (compare, advisory, **výchozí stav: vypnuto**)

Obrácená logika: ověřuje, že na **starém** rozhraní provoz po migraci klesl k nule. Chytá
zapomenuté vypnutí a duplicitní forwarding. Vyžaduje třetí capture (starý box po migraci) —
v CLI je to normální `capture` + `evaluate`, žádný speciální režim.

`SKIP` (ne WARN) dostane, když rozhraní v baseline chybí nebo když v baseline nebyl žádný
provoz — utichnutí se pak nedá ověřit.

| situace | Outcome | status | `value` |
|---|---|---|---|
| service scope s L3 linkem, jehož L2 strana nemá tranzitní rozhraní | *(žádný nález)* | — | — |
| žádné tranzitní rozhraní vůbec | `SKIP` | SKIP | `netranzitni rozhrani` |
| rozhraní úplně chybí v baseline | `SKIP` | SKIP | `bez baseline` |
| baseline měla nulový provoz na obou směrech | `SKIP` | SKIP | `bez provozu v baseline` |
| reziduál (max obou směrů) > `max_residual_pps` | `broken` | WARN (advisory) | reziduál v pps |
| reziduál v mezích prahu | `ok` | PASS | reziduál v pps |

---

## `optics.py` — optické úrovně a alarmy

Oba checky běží **jen na layer1/device scope** (`service_types = frozenset()`, což nesedí na
žádný service scope). Porty se iterují: rozhraní samo plus jeho seřazení LAG členové. Port,
který v `optics` úplně chybí, dostane jediný `SKIP` (`<name>: rozhrani nevraci opticka data`,
`value` = `bez optiky`); jinak jeden řádek na lane.

### `interface_optics_levels` (both, critical, jen layer1)

Za lane: RX/TX výkon v dBm. „Tmavá strana" (RX a/nebo TX nekonečné, tedy bez světla) je
`broken` bez ohledu na baseline. S baseline a bez tmavé strany je posun nad `tolerance_db`
(default 2.0 dB) na kterékoli straně `degraded` (vždy WARN); v toleranci je `ok`.

| situace | Outcome | status | `value` |
|---|---|---|---|
| port nevrací optická data vůbec | `SKIP` | SKIP | `bez optiky` |
| RX a/nebo TX nekonečné (bez baseline i s baseline) | `broken` | FAIL | `RX <x> / TX <y>` |
| baseline je, bez tmavé strany, posun nad `tolerance_db` | `degraded` | WARN | `RX <x> / TX <y>` |
| baseline je, bez tmavé strany, v toleranci (nebo bez baseline vůbec) | `ok` | PASS | `RX <x> / TX <y>` |

### `interface_optics_alarms` (state, critical, jen layer1)

Za port: tichý port (žádná lane nemá zvednutý žádný záznam v `alarms`/`warnings`) dostane
jeden souhrnný řádek. Alarm i warning mají stejnou formulaci, liší se jen Outcome/status —
`<name>: <tag> je aktivni` (dřív `je zvednuty`, což znělo jako otázka, ne jako tvrzení o tom,
co je špatně).

| situace | Outcome | status | `value` |
|---|---|---|---|
| port nevrací optická data vůbec | `SKIP` | SKIP | `bez optiky` |
| žádná lane nemá zvednutý alarm/warning | `ok` | PASS | `bez alarmu` |
| per zvednutý `alarms[tag]` | `broken` | FAIL | daný tag |
| per zvednutý `warnings[tag]` | `degraded` | WARN | daný tag |

---

## `bgp.py`

Oba checky mají `service_types={"Internet", "IPVPN"}`, ale **od vlny 2026-08-26 běží i na
Core loopback scope** (`service_subtype == "loopback"`) — interní iBGP peery na lo0.0.
Zajišťuje to mixin `_AppliesToCoreLoopback`, který `applies_to()` přetěžuje: pro
`service_type == "Core"` vrací `True` jen když je `service_subtype == "loopback"` (Core
transit žádné BGP peery nemá a dostal by prázdné SKIP/FAIL řádky), jinak deleguje na
`super().applies_to()` (tedy na `service_types`). Strojový katalog (`describe()`, `mig-validate
checks`) tuhle rozšířenou platnost taky přiznává — `_AppliesToCoreLoopback.describe()` dopočítá
`service_types` o `"Core"` a přidá `service_subtypes_by_type: {"Core": ["loopback"]}`, protože
holé `service_types` samo o sobě prostor pravdu neřekne (nalez finálního review). Bez peerů ve
scope vrací `SKIP`.

### `bgp_session_state` (both, critical)

- stav ≠ `Established` → `broken` → **FAIL**, zpráva `<peer>: stav <state>, ocekavano
  Established`. Nese `baseline_value`, takže sloupec `ZMENA` ukáže `bylo Established` —
  regrese je vidět přesně tam, kde na ní záleží,
- stav `Established`, ale **v baseline byl jiný** → **`recovered`** → **RECV** se zprávou
  `stav se zmenil X -> Established`. Tahle větev je dosažitelná jen se stavem `Established`
  (horší stavy odejdou výš), takže pokrývá právě a jen případ, kdy se relace během migrace
  **zlepšila** — a zlepšení není tiché PASS, operátor má vidět, že se relace zotavila
  (rozhodnutí R-2/bod 20). Změna nezmizí: pojmenuje ji zpráva a sloupec `ZMENA` píše předchozí
  stav,
- stav `Established` a shodný (nebo bez baseline) → PASS.

Každý Finding nese `label=f"BGP status ({peer})"` a `family` odvozenou `peer_family()` z adresy peeru —
report tak řádek zařadí do sekce `IPv4`/`IPv6`.

| situace | Outcome | status | `value` |
|---|---|---|---|
| peer změřen, stav ≠ `Established` | `broken` | FAIL | naměřený stav |
| peer změřen, stav `Established`, baseline měla jiný stav | `recovered` | RECV | `Established` |
| peer změřen, stav `Established`, beze změny nebo bez baseline | `ok` | PASS | `Established` |
| peer deaktivovaný v konfiguraci, v baseline běžel | `broken` (`deactivation_outcome`) | FAIL | `deaktivovan` |
| peer deaktivovaný v konfiguraci, baseline taky deaktivovaná/neznámá | `degraded` (`deactivation_outcome`) | WARN | `deaktivovan` |
| peer nakonfigurovaný, session není, není deaktivovaný | `broken` | FAIL | `bez session` |
| peer jen v baseline (tato služba ho už nenárokuje) | `broken` | FAIL | `v baseline patril k teto sluzbe, v subjektu uz ne` |
| žádný nakonfigurovaný/neaktivní/naměřený/baseline peer | `SKIP` | SKIP | `zadny peer` |

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
množství FAILů kvůli rozdílu několika rout, což není signifikantní. **Ani růst se
neignoruje** — tolerance je symetrická: pokles pod `tolerance_percent` je `broken` (WARN při
výchozí advisory severitě téhle skupiny), nárůst nad stejnou hranici v opačném směru
(`change > abs(tolerance)`) je `degraded` (vždy WARN) — překvapivý skok počtu prefixů nahoru
je stejně tak hodný pozornosti jako pokles, i když nic „nefunguje". RIB, kterou baseline měla
a subjekt ji vůbec nezměřil (rodina odpojená při migraci), je vlastní `broken` nález, ne ticho.

| situace | Outcome | status | `value` |
|---|---|---|---|
| peer v subjektu vůbec chybí | `SKIP` | SKIP | `zadna session` |
| peer chybí v baseline | `SKIP` | SKIP | `bez baseline` |
| RIB chybí v baseline u peera, který v baseline jinak je | `SKIP` | SKIP | `bez baseline` |
| per counter, pokles pod `tolerance_percent` (zpráva `pokles <key> <b> -> <s>, prah je <tol> %`) | `broken` | WARN (advisory) | naměřený počet |
| per counter, nárůst nad `abs(tolerance_percent)` (zpráva `narust <key> <b> -> <s>, prah je +<tol> %`) | `degraded` | WARN | naměřený počet |
| per counter, v toleranci | `ok` | PASS | naměřený počet |
| RIB, kterou baseline měla, subjekt ji vůbec neměří (zpráva `RIB v baseline byla, v subjektu chybi`) | `broken` | WARN (advisory) | `chybi` |

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

Řádek „peer status" u neresolvnutého peera nese důvod přímo v hlášce, ne stejný text jako
OK: `<instance>: <side> peer <ip> neni Resolved (<status or 'chybi'>)`. INFO řádky
`mode`/`esi`/`role` mají v hlášce i v labelu stejný zobrazovaný název (`ESI`, velkým) —
zpráva už neopakuje syrový klíč slovníku (`esi`, malým), který se dřív lišil od labelu
téhož řádku.

| situace | Outcome | status | `value` |
|---|---|---|---|
| stav lokálního rozhraní `Up` | `ok` | PASS | naměřený stav |
| stav lokálního rozhraní jiný | `broken` | FAIL | naměřený stav |
| remote strana, žádný peer | `broken` (řádek PE) + `broken` (řádek status) | FAIL | `Neznamy peer` / `Unresolved / Chybi` |
| local strana, bez peerů (single-homed, očekávané) | `INFO` | INFO | mode, s poznámkou „multi-homing peer ve vypisu nenalezen" |
| per peer, status `Resolved` | `ok` | PASS | adresa peera |
| per peer, status jiný (hláška uvádí konkrétní status) | `broken` | FAIL | adresa peera |

### `evpn_esi_status` (both, critical, jen E-LAN)

Stav lokálního rozhraní v segmentu musí být `Up`; do zprávy se přidá DF (IP adresa
zvoleného designated forwardera).

| situace | Outcome | status | `value` |
|---|---|---|---|
| stav lokálního rozhraní `Up` | `ok` | PASS | `<interface> <status>` |
| stav lokálního rozhraní jiný | `broken` | FAIL | `<interface> <status>` |
| `df_role` obsahuje „not elected" | `broken` | FAIL | surový text `df_role` (hláška `<esi>: <df_role>`, bez zdvojeného `DF`, pokud `df_role` už jím začíná) |
| `df_role` je `None` nebo `""` (bez záznamu o DF) | `INFO` | INFO | `-` (hláška `DF bez zaznamu`) |
| `df_role` jinak (adresa zvoleného DF) | `ok` | PASS | surový text `df_role` |

### `evpn_instance_status` (both, critical, jen E-LAN)

Za instanci: počet EVPN neighbors (`> 0`, WARN pod baseline), jeden `INFO` řádek na adresu
souseda, blok ESI status/local interface/IRB (jen když scope nemá selektory rozhraní —
službě se selektory dává vlastní detail ESI `evpn_esi_status`), jeden řádek na lokální EVPN
interface a na IRB interface. **`baseline_value` je teď vyplněný u řádků EVPN interface, IRB
interface, EVPN neighbor a ESI, kdykoli baseline odpovídající položku nese** — dřív tyhle
řádky s baseline vůbec neporovnávaly.

| situace | Outcome | status | `value` |
|---|---|---|---|
| EVPN neighbors total > 0, ne pod baseline | `ok` | PASS | naměřený total |
| EVPN neighbors total > 0, ale pod baseline | `degraded` | WARN | naměřený total |
| EVPN neighbors total je 0/chybí | `broken` | FAIL | `0` |
| lokální EVPN interface stav `Up` | `ok` | PASS | `<name> <status>` |
| lokální EVPN interface stav jiný | `broken` | FAIL | `<name> <status>` |
| unit očekávaný selektory, v instanci chybí | `broken` | FAIL | `<unit> chybi v instanci` |
| IRB interface stav `Up` | `ok` | PASS | `<name> <status>` (+ `(<l3_context>)`) |
| IRB interface stav jiný | `broken` | FAIL | totéž, `ocekavano Up` |
| ESI v baseline, v subjektu chybí | `broken` | FAIL | `chybi` |
| ESI status začíná „resolved" | `ok` | PASS | naměřený status |
| ESI status je, ale nezačíná „resolved" | `broken` | FAIL | naměřený status |
| ESI status je `""` | `broken` | FAIL | `bez statusu` |

### `evpn_mac_count` (both, advisory, jen E-LAN)

Iteruje instance a v nich domény (klíčované VLAN id, u vlan-based `"-"`). Label je
`instance/vlan`, u vlan-based jen `instance`.

- **bez baseline**: 0 naučených MAC → `broken` → WARN,
- **s baseline**: pokles proti `tolerance_percent` (default −60 %) → WARN. Nula MAC je WARN
  i tehdy, když by pokles do tolerance vešel.

| situace | Outcome | status | `value` |
|---|---|---|---|
| bez baseline, počet > 0 | `ok` | PASS | naměřený počet |
| bez baseline, počet == 0 | `broken` | WARN (advisory) | `0` |
| oba záznamy, pokles nad toleranci | `broken` | WARN (advisory) | naměřený počet |
| oba záznamy, v toleranci | `ok` | PASS | naměřený počet |
| interface v baseline, v subjektu chybí (hláška `v baseline <b> MAC, v subjektu chybi`) | `broken` | WARN (advisory) | `chybi` |

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
- MAC `00:00:00:00:00:00` (nerozresolvovaný ARP záznam) → `broken` s `Finding.severity=critical`,
  tedy FAIL i když je check advisory; zpráva `ARP zaznam
  <ip> neni resolved (incomplete)`, `value` = `incomplete -> <ip>`;
- jinak **jeden `Finding` na ARP záznam**: zpráva `ARP zaznam <ip>`, `label="ARP"`,
  `family=4`, `value` = `<mac> -> <ip>` (`?` když MAC chybí).

| situace | Outcome | status | `value` |
|---|---|---|---|
| žádná IPv4 adresa nakonfigurovaná | *(žádný nález)* | — | — |
| IPv4 nakonfigurováno, žádný ARP záznam s `ip` | `broken` | WARN (advisory) | `zadny zaznam` |
| MAC záznamu je `00:00:00:00:00:00` (nerozresolvovaný) | `broken` | **FAIL** (řádek si vynucuje `critical`) | `incomplete -> <ip>` |
| záznam resolvovaný (jakýkoli jiný MAC) | `ok` | PASS | `<mac or '?'> -> <ip>` |

### `nd_present` (state, advisory)

Zrcadlí `arp_present` pro IPv6:

- žádná IPv6 adresa nakonfigurovaná → **žádný Finding** (viz `arp_present` výše);
- link-local sousedé se **vyřadí**, pokud služba sama nemá link-local jako nakonfigurovanou
  adresu (`link_local_is_configured()`);
- žádný **použitelný** ND záznam nezbyde → `broken`, zpráva `na rozhranich sluzby neni zadny
  pouzitelny ND zaznam` (všimni si slova „pouzitelny" navíc oproti ARP — právě kvůli
  odfiltrovaným link-local sousedům), `value` = `zadny zaznam`;
- stav záznamu `incomplete` nebo `unreachable` (nerozresolvovaný ND záznam) → `broken`
  s `Finding.severity=critical` (FAIL), zpráva `ND zaznam <ip> neni resolved (<state>)`, `value` = `<state> -> <ip>`;
- jinak **jeden `Finding` na ND záznam**: zpráva `ND zaznam <ip>`, `label="ND"`, `family=6`,
  `value` = `<mac> -> <ip>`, `subject` navíc nese `state` (ND má na rozdíl od ARP stav
  záznamu).

| situace | Outcome | status | `value` |
|---|---|---|---|
| žádná IPv6 adresa nakonfigurovaná | *(žádný nález)* | — | — |
| IPv6 nakonfigurováno, žádný použitelný záznam (vše vyfiltrováno nebo prázdné) | `broken` | WARN (advisory) | `zadny zaznam` |
| stav záznamu `incomplete` nebo `unreachable` (nerozresolvovaný) | `broken` | **FAIL** (řádek si vynucuje `critical`) | `<state> -> <ip>` |
| záznam resolvovaný (jakýkoli jiný stav) | `ok` | PASS | `<mac or '?'> -> <ip>` |

### `ping_reachability` (state, advisory)

Čte hotové výsledky ze snapshotu — cíle se resolvovaly už při `capture` (ARP/ND → ping,
`probes/ping.py`).

- lokální subnet nad P2P prahem (IPv4 širší než `/30`, IPv6 širší než `/126`), do kterého
  nepadl žádný cíl → `SKIP` na subnet (`<sit>: zadny cil - subnet vetsi nez /30, fallback by
  cil jen hadal`, `value` `<sit>  bez cile (subnet > /30)`) — resolver tam fallback vědomě
  nepouští (viz `IPV4_FALLBACK_MIN_PREFIX` v `probes/ping.py`) a mlčení by se četlo jako
  „zkontrolováno OK";
- `ping_skipped` nastaveno (profil scope při `capture` vynechal) → `SKIP`, zpráva `ping
  neproveden - mimo profil (<profile>)`, `value` = `mimo profil (<profile>)` — jmenuje
  profil, aby ho operátor nemusel dohledávat v `run.yml`;
- ve snapshotu nejsou pro tenhle scope žádné cíle a žádný takový subnet to nevysvětluje →
  `SKIP` (`pro tento scope nejsou ve snapshotu zadne cile pingu`);
- probe bez rozpoznané rodiny (`family` mimo `4`/`6`) → vlastní `SKIP` jmenující cíle
  (`probe bez rodiny nelze vyhodnotit: ...`) — jinak by probe z výsledku tiše zmizel, místo
  aby řekl, že se nevyhodnotil;
- jinak **jeden `Finding` na cíl**, seskupené podle `family`:
  - `sent == 0` → `SKIP`, zpráva `<cil>: ping neodeslan`, `value` = `<cil> neodeslan` —
    nic neodeslat není totéž jako „odesláno a bez odpovědi";
  - odpověděl aspoň jeden paket → `ok`, zpráva `<cil>: odpovedelo N z M`,
  - `sent > 0` a neodpověděl žádný → `broken`, zpráva `<cil>: neodpovedel (M paketu)`.

`value` je `<received>/<sent>` a u úspěšné odpovědi ještě `  <rtt> ms` (chybí, pokud se
nepodařilo změřit RTT), pak vždy `  <cil>` — např. `5/5  2.1 ms  10.1.1.1`. U neúspěchu
`  <cil> neodpovedel`. `details` nese `resolved_from` (`arp` | `nd` | `subnet-fallback`) a
`address` (`owning_prefix()`), takže je z výsledku vidět, který nakonfigurovaný rozsah cíl
zastupuje a jestli byl zjištěný z ARP/ND, nebo dopočtený ze subnetu.

| situace | Outcome | status | `value` |
|---|---|---|---|
| `ping_skipped` nastaveno, profil znám | `SKIP` | SKIP | `mimo profil (<profile>)` |
| `ping_skipped` nastaveno, profil neznám | `SKIP` | SKIP | `mimo profil` |
| žádné probe a žádný předimenzovaný subnet | `SKIP` | SKIP | `bez cile` |
| lokální subnet nad P2P prahem bez jediného cíle | `SKIP` | SKIP | `<sit>  bez cile (subnet > /<threshold>)` |
| probe se `sent == 0` | `SKIP` | SKIP | `<cil> neodeslan` |
| probe s `received > 0` | `ok` | PASS | `<received>/<sent>[  <rtt> ms]  <cil>` |
| probe se `sent > 0` a `received == 0` | `broken` | WARN (advisory) | `<received>/<sent>  <cil> neodpovedel` |
| probe s nerozpoznanou `family` | `SKIP` | SKIP | `bez rodiny` |

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
statiky — default je tam „static", ne chyba. Chrání to jen vnitřek checku (ručně sestavená
fakta v testech, kde se check volá přímo bez `Snapshot.from_dict`) — reálný starý soubor
baseline stejně skončí na `SnapshotVersionError` (`models/snapshot.py`, `schema_version !=
SCHEMA_VERSION`) dřív, než se k tomuhle checku vůbec dostane, takže je potřeba nová captura.

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
| v tabulce, baseline měla neaktivní (routa se znovu aktivovala) | `recovered` | RECV | `v tabulce` (zpráva přidává `(v baseline nebyla aktivni)`) |
| v tabulce, ale bez hvězdičky, baseline záznam je, ale mlčí o aktivitě (zpráva přidává `; baseline aktivitu neuvadi`, `baseline_value` zůstává `None`, ne fabulované) | `degraded` | WARN | `neni aktivni` |
| v tabulce, ale bez hvězdičky, žádný baseline záznam | `degraded` | WARN | `neni aktivni` |
| chybí v tabulce, žádné odpovídající baseline měření (zpráva `nakonfigurovana, ale neni v routovaci tabulce`) — platí i v device scope | `broken` | FAIL | `neni v tabulce` |
| chybí v tabulce, v baseline měření je | `broken` | FAIL | `chybi` |

Routa **v tabulce, ale bez hvězdičky** neforwarduje — Junos ji nevyhodí z výpisu, jen ji
přebije jiný zdroj. Je to jiná situace než `neni v tabulce` (ta routu z výpisu úplně vyhodí):
proto samostatná hodnota `neni aktivni`, ne stejná jako u chybějící routy. Bez baseline není
z čeho poznat, že neaktivní byla i předtím, takže se nejednoznačnost podle R‑2 neeskaluje na
FAIL, ale zůstává na WARN.

Poslední dva řádky rozlišuje **to, jestli baseline routu vůbec změřila**, ne druh scope: baseline
záznam přítomen znamená „byla tam, teď chybí" (`chybi`); žádný baseline záznam znamená, že
jen konfigurace tvrdí, že routa do tabulky patří, a tabulka sama říká, že tam není
(`neni v tabulce`) — platí to i v device scope, protože je to tvrzení o tom, co ukazuje
tabulka, ne o záměru, který device scope nevidí (AR‑17).

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
| v tabulce a aktivní, baseline beze změny nebo bez záznamu | `ok` | PASS | `v tabulce` |
| v tabulce a aktivní, baseline měla neaktivní (routa se znovu aktivovala) | `recovered` | RECV | `v tabulce` (zpráva přidává `(v baseline nebyla aktivni)`) |
| v tabulce, ale bez hvězdičky (`active: false`), baseline taky neaktivní | `ok` | PASS | `neni aktivni` |
| v tabulce, ale bez hvězdičky, v baseline byla aktivní | `broken` | FAIL | `neni aktivni` |
| v tabulce, ale bez hvězdičky, baseline záznam je, ale mlčí o aktivitě | `degraded` | WARN | `neni aktivni` |
| v tabulce, ale bez hvězdičky, žádný baseline záznam | `degraded` | WARN | `neni aktivni` |
| chybí v tabulce, žádné odpovídající baseline měření (zpráva `nakonfigurovana, ale neni v routovaci tabulce`) — platí i v device scope | `broken` | FAIL | `neni v tabulce` |
| chybí v tabulce, v baseline měření je | `broken` | FAIL | `chybi` |
| nakonfigurovaná, v konfiguraci deaktivovaná a není v tabulce | `deactivation_outcome(...)` | podle baseline | `deaktivovana` |

---

## `bfd.py` — BFD session

### `bfd_session_state` (both, critical)

Stejně jako `static_route_status` **iteruje přes sjednocení tří zdrojů** (AR‑14): záměr
z konfigurace (`Selectors.bfd_peers`), session v subjektu a session v baseline.
**Peer, který BFD nikdy neměl, řádek nedostane** (rozhodnutí R‑1) — služba bez BFD tedy
v reportu nemá o BFD ani zmínku.

**Na žádném Core scope tenhle check vůbec neběží** — `applies_to()` je přetížené a pro
`scope.service_type == "Core"` vrací `False` ještě před voláním `super().applies_to()`, bez
ohledu na `service_subtype`. Tranzitní BFD měří nový `core_protocols.bfd_transit_state` (vlna
2026-08-26), který session páruje podle **rozhraní**, ne podle peer adresy ze záměrové
konfigurace — na tranzitu žádný takový záměr není. Bez téhle brány by `bfd_session_state`
běžel dál a za každou tranzitní session vypsal `WARN | parser nenasel konfiguraci`, protože žádný
`Selectors.bfd_peers` záznam by nenašel.

Loopback scope (`service_subtype == "loopback"`) je v bráně **od finálního review branch**
(2026-08-26): `Scope.select` od téhle vlny zařazuje interní (iBGP) peery na lo0.0 i do jejich
`bgp_neighbors`, takže by bez brány dostala i tahle session falešný `WARN | parser nenasel konfiguraci` —
záměr v konfiguraci existuje, ale `Selectors.bfd_peers` ho neparsuje. Testovat iBGP BFD na
loopbacku je vědomě odložené rozhodnutí, ne mezera; taková session zůstává viditelná
v NEZAŘAZENO (`engine.py:_unassigned_bfd_sessions` ji tam schválně nechává), dokud pro ni
nevznikne vlastní check.

Check vyžaduje **dvě oblasti**: `("bfd", "bgp")`.

| situace | Outcome | status | `value` |
|---|---|---|---|
| session existuje, stav `Up` | `ok` | PASS | `Up` |
| session existuje, jiný stav (hláška uvádí očekávání: `<stav>, ocekavano Up`) | `broken` | FAIL | naměřený stav (`Down`, …) |
| session existuje, ale v konfiguraci služby není (service scope) | `degraded` | WARN | `parser nenasel konfiguraci` |
| session není a není ani záměr, ale v baseline byla (service scope) | `broken` | FAIL | `v baseline patril k teto sluzbe, v subjektu uz ne` |
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
v NEZAŘAZENO. Hláška „v subjektu není nakonfigurované" by tam lhala; `parser nenasel konfiguraci` je navíc
odlišná hodnota od téhle větve, takže záměna nehrozí. Formulace `v baseline patril k teto
sluzbe, v subjektu uz ne` je pravdivá v obou případech, které do větve spadají (BFD ze zařízení
zmizelo i BFD přešlo pod jinou službu) — stejná oprava, jakou dřív dostala analogická větev
v `checks/bgp.py`.

---

## `core_protocols.py` — protokoly Core transitu a lo0.0 (vlna 2026-08-26)

Sedm nových checků nad šesti novými collectory (`isis_adjacency`, `isis_interface`,
`isis_overview`, `ldp_neighbor`, `pim_neighbor`, `mpls_interface`). Všechny sdílí dvě
rozhodnutí:

- **Absence rozhraní ve výpisu je měření, ne díra.** Collector nikdy nesyntetizuje
  „Down" řádek — pokud rozhraní ve výpisu chybí, je to prostě chybějící klíč ve faktech.
  Co to znamená, vykládá až check, a skoro vždy je to `FAIL | ... : chybi v outputu`
  (stejné rozlišení „chybí v tabulce" vs. „chybí úplně", jaké `static_route_status`
  dělá v `routes.py` přes `MISSING_FROM_TABLE`/`MISSING_ENTIRELY`).
- **Měřené jednotky se berou ze selektoru (záměr), ne z faktů** — `_scope_transit_interfaces`
  vrací tranzitní rozhraní ze `Scope.selectors.interfaces`, ne klíče ze subjektu. Rozhraní,
  které z výpisu úplně zmizelo, tak pořád dostane řádek (FAIL „chybi v outputu"), místo aby
  ztichlo.

### `isis_adjacency_state` (transit, critical)

`service_types={"Core"}`, `service_subtypes={"transit"}`. Vyžaduje `isis_adjacency`.

Rozhraní chybí v adjacency výpisu → jediný řádek `FAIL | IS-IS adjacency state : chybí
v outputu` (`baseline_value` je stav ze stejného řádku baseline, je-li k dispozici — mluví
v řeči přítomnosti tohoto řádku, ne next-hopu).

Jinak čtyři řádky:

| pole | bez baseline | proti baseline |
|---|---|---|
| `system-name` (soused) | INFO | PASS při shodě; WARN při rozdílu |
| `adjacency-state` | PASS pokud `Up`, jinak FAIL | PASS při shodě s baseline stavem `Up`; **RECV** (`recovered`) pokud teď `Up`, ale v baseline nebyl `Up` (zpráva přidává `(v baseline <state>)`); jinak FAIL |
| `ip-address` (IPv4 souseda) | PASS pokud přítomna, jinak FAIL | PASS při shodě; WARN při rozdílu |
| `global-ipv6-address` (IPv6 souseda) | PASS pokud přítomna, jinak FAIL | PASS při shodě; WARN při rozdílu |

Mutant kill (2026-09-03, ověřeno spuštěním): prohození `Outcome.RECOVERED` → `Outcome.OK`
ve větvi „Up teď / v baseline ne-Up" nechá padnout
`test_baseline_state_down_before_up_now_is_recovered`.

| situace | Outcome | status | `value` |
|---|---|---|---|
| rozhraní chybí v adjacency výpisu | `broken` | FAIL | `chybi v outputu` |
| řádek soused (`system-name`), baseline je a jméno se liší | `degraded` | WARN | naměřené jméno (nebo `chybi v outputu`, je-li `None`) |
| řádek soused, baseline je a jméno sedí | `ok` | PASS | naměřené jméno |
| řádek soused, bez baseline (nebo bez baseline řádku pro rozhraní) | `info` | INFO | naměřené jméno |
| řádek `adjacency-state`, stav ≠ `Up` | `broken` | FAIL | naměřený stav |
| řádek `adjacency-state`, stav `Up` teď, baseline stav je a ≠ `Up` | `recovered` | RECV | `Up` |
| řádek `adjacency-state`, stav `Up` teď, baseline `Up` nebo bez baseline | `ok` | PASS | `Up` |
| řádek adresy IPv4/IPv6 souseda, baseline je a adresa se liší | `degraded` | WARN | naměřená adresa (nebo `chybi v outputu`, je-li `None`) |
| řádek adresy, beze změny (nebo bez baseline), adresa je | `ok` | PASS | naměřená adresa |
| řádek adresy, beze změny (nebo bez baseline), adresa `None` | `broken` | FAIL | `chybi v outputu` |

### `isis_interface_info` (transit + loopback, critical)

`service_types={"Core"}`, `service_subtypes={"transit", "loopback"}` — **jeden check pro
obě role**, chování se větví podle `ctx.scope.service_subtype`. Vyžaduje `isis_interface`.

- Rozhraní chybí v ISIS interface výpisu → `FAIL | IS-IS interface : chybi v outputu`.
- Level 2 přítomen v `levels` → PASS `nakonfigurovan`; chybí → FAIL `chybi v outputu`, a
  **passive řádek se v tom případě vůbec neemituje** (pro žádnou roli) — bez adjacency
  passive flag neměří nic, takže jeden FAIL nahrazuje to, co dřív na loopbacku bylo dvakrát
  FAIL za jednu příčinu. Level 1 přítomen → **vlastní FAIL řádek** (level 1 nemá na Core
  rozhraní co dělat), value `nakonfigurovan`.
- Passive flag na level 2 je **role-aware**: loopback ho vyžaduje (`ok = passive`), transit
  vyžaduje jeho absenci (`ok = not passive`) — pasivní tranzitní port by nesestavil
  adjacency, kterou měří `isis_adjacency_state`.

Mutant kill (2026-08-26, ověřeno spuštěním): `ok = passive if loopback else not passive` →
`ok = passive` nechá padnout `test_transit_non_passive_level2_is_pass` i
`test_transit_passive_level2_is_fail` (a taky end-to-end regresi).

| situace | Outcome | status | `value` |
|---|---|---|---|
| rozhraní chybí v ISIS interface výpisu | `broken` | FAIL | `chybi v outputu` |
| level 2 přítomen v `levels` | `ok` | PASS | `nakonfigurovan` |
| level 2 chybí v `levels` (passive řádek se pro rozhraní neemituje) | `broken` | FAIL | `chybi v outputu` |
| level 1 přítomen v `levels` | `broken` | FAIL | `nakonfigurovan` |
| level 2 je, passive flag sedí na roli (loopback: passive; transit: ne passive) | `ok` | PASS | `Passive` (loopback) / `bez Passive` (transit) |
| level 2 je, passive flag na roli nesedí | `broken` | FAIL | `bez Passive` (loopback) / `Passive` (transit) |

### `ldp_neighbor_state` (transit, critical) — očekávaný vždy

`service_types={"Core"}`, `service_subtypes={"transit"}`. Vyžaduje `ldp_neighbor`. **Žádný
gate na záměr** — LDP na tranzitním Core rozhraní je očekávaný vždy (rozhodnutí 2026-08-26),
takže chybějící soused je rovnou FAIL, ne tiché nic.

| situace | Outcome | status | `value` |
|---|---|---|---|
| soused ve výpisu není | `broken` | FAIL | `Down` |
| `uptime_seconds > 0` | `ok` | PASS | `Up for <uptime>` |
| `uptime_seconds` chybí nebo `0` | `broken` | FAIL | `Down` |
| adresa souseda je `None`, beze změny proti baseline (nebo bez baseline) | `broken` | FAIL | `chybi v outputu` (zpráva `adresa souseda chybi`) |
| adresa souseda je, beze změny proti baseline (nebo bez baseline) | `info` | INFO | adresa |
| adresa souseda se změnila proti baseline, nová adresa je | `degraded` | WARN | adresa |
| adresa souseda se změnila proti baseline, nová adresa je `None` | `degraded` | WARN | `chybi v outputu` |

Collector u LDP zahazuje záznamy pro `lo0.*` už při parsování (LDP na loopbacku nemá
smysl měřit tímhle checkem) — viz `collectors.md`.

### `pim_neighbor_state` (transit, critical) — gate na záměr

`service_types={"Core"}`, `service_subtypes={"transit"}`. Vyžaduje `pim_neighbor`.

**Bez záměru (`"pim"` není v `ctx.scope.selectors.protocols`) check vrátí prázdný seznam
nálezů — ticho, ne SKIP** (rozhodnutí 2026-08-26): služba bez PIM není méně zdravá, takže
nemá dostat řádek vůbec, natož SKIP, který by v souhrnu vypadal jako nezměřená věc. Záměr do
inventory zapisuje parser z `protocols pim interface <name>` (globálně i per routing-instance)
do existujícího pole `ServiceEntry.protocol`.

Se záměrem se chová stejně jako `ldp_neighbor_state` (sdílená kostra `_neighbor_findings`):
chybějící soused je FAIL `Down`, jinak PASS/FAIL podle `uptime_seconds`, adresa INFO/WARN.

Mutant kill (2026-08-26, ověřeno spuštěním): smazání gate `if "pim" not in ...` nechá padnout
`test_pim_neighbor_without_intent_is_silent_not_skip` (a end-to-end regresi).

| situace | Outcome | status | `value` |
|---|---|---|---|
| `"pim"` není v `scope.selectors.protocols` | *(žádný nález)* | — | — |
| soused ve výpisu není | `broken` | FAIL | `Down` |
| `uptime_seconds > 0` | `ok` | PASS | `Up for <uptime>` |
| `uptime_seconds` chybí nebo `0` | `broken` | FAIL | `Down` |
| adresa souseda je `None`, beze změny proti baseline (nebo bez baseline) | `broken` | FAIL | `chybi v outputu` |
| adresa souseda je, beze změny proti baseline (nebo bez baseline) | `info` | INFO | adresa |
| adresa souseda se změnila proti baseline, nová adresa je | `degraded` | WARN | adresa |
| adresa souseda se změnila proti baseline, nová adresa je `None` | `degraded` | WARN | `chybi v outputu` |

### `mpls_interface_state` (transit, critical)

`service_types={"Core"}`, `service_subtypes={"transit"}`. Vyžaduje `mpls_interface`.

PASS pokud stav `Up`, FAIL pokud `Dn` nebo jiný, FAIL `chybi v outputu` při absenci.
**S baseline: `Up` teď a baseline stav byl jiný (ne `Up`) je `RECV`** (`recovered`), ne tiché
PASS — zpráva přidává `(v baseline <state>)`; `Up` teď proti baseline `Up`, nebo bez baseline
vůbec, zůstává `ok`.

| situace | Outcome | status | `value` |
|---|---|---|---|
| rozhraní chybí v MPLS výpisu | `broken` | FAIL | `chybi v outputu` |
| stav `Up`, baseline stav byl jiný (ne `Up`) | `recovered` | RECV | `Up` |
| stav `Up`, baseline `Up` nebo bez baseline | `ok` | PASS | `Up` |
| stav cokoli jiného (např. `Dn`, `unknown`) | `broken` | FAIL | `Down` |

### `bfd_transit_state` (transit, critical) — očekávaná vždy

`service_types={"Core"}`, `service_subtypes={"transit"}`. Vyžaduje `bfd`. **Samostatný check
vedle `bfd.py`**, aby záměrová zákaznická logika `bfd_session_state` zůstala nedotčená — a ta
se navíc na žádném Core scope vůbec nespustí (viz gate v sekci `bfd.py` výše).

Session se páruje podle **rozhraní**, ne podle peer adresy — `by_interface` je postavené
z `data.get("interface")` každé BFD session v subjektu.

| situace | Outcome | status | `value` |
|---|---|---|---|
| na rozhraní není žádná session | `broken` | FAIL | `Down` |
| session existuje, stav `Up`, baseline daného peera byl taky `Up` nebo bez baseline | `ok` | PASS | `Up` (syrový stav, stejný slovník jako `bfd.py:113`) |
| session existuje, stav `Up`, baseline daného peera byl jiný | `recovered` (zpráva přidává `(v baseline <state>)`) | RECV | `Up` |
| session existuje, jiný stav | `broken` | FAIL | naměřený stav |

Víc session na stejném rozhraní dostane víc řádků (setříděných podle peera).

Mutant kill (2026-08-26, ověřeno spuštěním): spuštěná varianta `entries =
by_interface.get(name) or []` (literální smazání větve `if not entries` by spadlo na
`TypeError` v `sorted(None)`, viz roadmap) nechá padnout
`test_bfd_transit_missing_session_is_fail_down`.

### `isis_overview` (loopback, advisory)

`service_types={"Core"}`, `service_subtypes={"loopback"}`. Vyžaduje `isis_overview` —
device-global fakt, do scopu ho `Scope.select()` pustí jen pro Core loopback (viz
`models.md`), takže na transitu je `ctx.subject["isis_overview"]` vždy prázdný dict.
Scoping (`Scope.select()`) a vazba checku (`service_subtypes`) jsou dvě **nezávislé**
pojistky proti stejné chybě: kdyby scoping propustil area i na transit, prázdný dict by
tam vždy tvrdil falešné `PASS | nenastaven` (overload bit nikdy nenastavený, protože area
je prázdná) — kdyby selhala jen vazba checku, subtype gate ve `Scope.select()` pořád drží
area mimo transit.

Jediný řádek: oblast `isis_overview` úplně prázdná (collector nic nevrátil, jiná situace než
naměřené „nenastaven") → `WARN | IS-IS overload bit : bez dat` (zpráva `chybi data z
collectoru isis_overview`); `overload_enabled` → `WARN | IS-IS overload bit : nastaven`
(router se vyhýbá tranzitnímu provozu), jinak `PASS | IS-IS overload bit : nenastaven`.
Baseline nic nepřidává (`mode = STATE`).

| situace | Outcome | status | `value` |
|---|---|---|---|
| oblast `isis_overview` úplně prázdná (collector nic nevrátil) | `degraded` | WARN | `bez dat` |
| `overload_enabled` truthy | `degraded` | WARN | `nastaven` |
| `overload_enabled` falsy (data jsou) | `ok` | PASS | `nenastaven` |

Mutant kill (2026-08-26, ověřeno spuštěním): smazání podmínky `if self.service_subtype ==
"loopback"` v `Scope.select()` (u `isis_overview`) nechá padnout
`test_isis_overview_goes_only_to_loopback_scope` (`tests/models/test_scope_core.py`) — transit
scope by dostal overview taky.

---

## `multicast.py` — IGMP, multicast forwarding, MVPN c-multicast (vlna 2026-09-02)

Čtyři nové checky nad třemi novými collectory (`igmp_group`, `multicast_route`,
`mvpn_instance`). Pokrývají tři role: **Internet/multicast** (zákaznický receiver pod
`protocols igmp`), **Core/loopback** (globální `inet.2` statiky na lo0.0) a
**IPVPN/mvpn-igmp** (IRB v MVPN VRF s IGMP receiverem). Sdílejí modul, dvě sdílené
sekce (`igmp_pairs`, `multicast_table`, `stream_rows`) a jedno pravidlo napříč všemi:

- **Chybějící IGMP množina kaskáduje do SKIP.** `igmp_membership_report` definuje
  očekávané streamy; `multicast_forwarding_status` a `mvpn_cmulticast_status` bez ní
  vrátí jediný `SKIP` řádek, ne nezávislé hledání v tabulce.
- **Proti baseline se porovnává jen množina (S,G) a sender PE tunelu** — upstream,
  downstream, forwarding rate a route uptime se neporovnávají nikdy, protože se
  migrací mění z definice (jiná rozhraní, jiná lsi.X čísla).
- **Absence `forwarding_rate_pps` není nula.** junos-evo často vrací
  `<multicast-statistics-timed-out/>` i na živé Forwarding routě — `stream_rows()`
  na `None` vrátí `SKIP | ... : statistiky nedostupne`, ne `BROKEN` s vymyšlenou
  nulou.
- **Per-stream group header.** Řádky každého (S,G) sedí pod vlastní skupinou
  `   -- (S, G)`, aby `Forwarding-rate`/`Route uptime` nebyly u víc streamů
  nejednoznačné.

### `igmp_membership_report` (Internet/multicast, IPVPN/mvpn-igmp, both, critical)

`service_types={"Internet", "IPVPN"}`, `service_subtypes={"multicast", "mvpn-igmp"}`.
Vyžaduje `igmp_group`.

Skupiny na servisním rozhraní ze scopu, seřazené, bez duplicit; ASM položky (bez
zdroje) jako `(*, G)`. Žádné skupiny → `BROKEN | IGMP membership report : Receiver
neposila zadny IGMP membership report`. Proti baseline: shodná množina → `OK`; jiná
množina → `DEGRADED`, `value` je aktuální množina, `baseline_value` ta stará; baseline
bez skupin → bez porovnání (no-baseline pravidlo — `baseline_value` `None`, ne "bylo
prázdno").

Mutant kill (2026-09-03, ověřeno spuštěním): `Outcome.DEGRADED` → `Outcome.OK` ve
větvi „množina se liší" nechá padnout `test_igmp_report_changed_set_is_warn`.

| situace | Outcome | status | `value` |
|---|---|---|---|
| na rozhraních služby žádné skupiny | `broken` | FAIL | `Receiver neposila zadny IGMP membership report` |
| baseline měla skupiny, aktuální množina se liší | `degraded` | WARN | aktuální množina `(S, G)`, spojená |
| aktuální množina sedí na baseline (nebo bez baseline, nebo baseline žádnou neměla) | `ok` | PASS | aktuální množina `(S, G)`, spojená |

### `multicast_forwarding_status` (Internet/multicast, IPVPN/mvpn-igmp, state, critical)

Stejné dva subtype. Vyžaduje `igmp_group`, `multicast_route`.

Bez IGMP skupin → jediný `SKIP | Multicast forwarding status : bez IGMP reportu`,
žádné řádky streamů. Jinak souhrnný řádek (`OK` `{n} S,G`, nebo `BROKEN`
`{k}/{n} S,G nefunguje`, počítáno z toho, jestli S,G chybí v tabulce nebo mu selže
Stream/Upstream řádek) a pro každý pár:

- **Stream** — `OK`, je-li servisní rozhraní v `downstream_interfaces` routy; jinak
  `BROKEN`.
- **Upstream interface** — role-aware prefix: Internet/multicast `ge-`/`xe-`/`et-`/`ae`,
  IPVPN/mvpn-igmp `lsi.`/`vt-` (`_upstream_ok`). Splněno → `OK` se jménem; jinak
  `BROKEN`, hodnota nalezené jméno nebo `-`. **Zpráva rozlišuje dva důvody selhání**:
  prázdný upstream je `<sg>: upstream - S,G je v tabulce ale nema upstream interface`,
  upstream, který je, ale se špatným prefixem, je `<sg>: upstream <up> neni z ocekavane
  role (ocekavano <prefixes>)` — dřívější formulace tvrdila „nemá upstream interface" i
  když nějaký existoval, jen se špatnou rolí.
- **Forwarding-rate**, **Route uptime** — `stream_rows()`, viz výš.

Chybí-li S,G v tabulce vůbec, vydá se jen `BROKEN | Stream : S,G neni v multicast
tabulce`, další řádky streamu se nevydávají. Žádné porovnání proti baseline
(`mode = STATE`).

Mutant kill (2026-09-03, ověřeno spuštěním):
- `_upstream_ok`: vrať `True` vždy → padne `test_forwarding_internet_upstream_must_be_transit`
  i `test_forwarding_mvpn_upstream_must_be_lsi_or_vt` (a bonusem
  `test_forwarding_missing_upstream_renders_dash`).
- `iface in downstream` → `bool(downstream)` → padne
  `test_forwarding_downstream_without_service_interface_fails_stream_row`.
- `stream_rows`: `raw_pps is None` → `raw_pps == 0` (rate SKIP větev) → padne
  `test_stream_rows_skip_when_rate_missing` (a `TypeError` v `int(None)` strhne
  s sebou i `test_forwarding_missing_rate_is_skip_not_failure` a
  `test_core_missing_rate_is_skip`).

| situace | Outcome | status | `value` |
|---|---|---|---|
| bez IGMP skupin na scopu | `SKIP` | SKIP | `bez IGMP reportu` |
| souhrnný řádek: aspoň jedno S,G selhalo | `broken` | FAIL | `<failed>/<total> S,G nefunguje` |
| souhrnný řádek: žádné neselhalo | `ok` | PASS | `<total> S,G` |
| pár nemá v tabulce žádnou odpovídající routu | `broken` | FAIL | `S,G neni v multicast tabulce` |
| per spárovaná routa, servisní rozhraní je v `downstream_interfaces` | `ok` | PASS | `Stream se na <iface> posila` |
| per spárovaná routa, servisní rozhraní v `downstream_interfaces` není | `broken` | FAIL | `S,G je v tabulce ale stream se na <iface> neposila` |
| per spárovaná routa, upstream prázdný | `broken` | FAIL | jméno upstreamu nebo `-` |
| per spárovaná routa, upstream je, ale se špatným prefixem role | `broken` | FAIL | jméno upstreamu |
| per spárovaná routa, upstream odpovídá očekávané roli | `ok` | PASS | jméno upstreamu |
| per spárovaná routa, `forwarding_rate_pps` je `None` | `SKIP` | SKIP | `statistiky nedostupne` |
| per spárovaná routa, `pps > 0` | `ok` | PASS | `<pps> pps` |
| per spárovaná routa, `pps <= 0` | `broken` | FAIL | `<pps> pps` |
| per spárovaná routa, route uptime (vždy) | `info` | INFO | formátovaný uptime nebo `-` |

### `core_multicast_forwarding` (Core/loopback, both, critical)

`service_types={"Core"}`, `service_subtypes={"loopback"}`. Vyžaduje `multicast_route`,
`routes`. Řízeno **globálními `inet.2` statikami ze `Selectors.static_routes`**, ne
IGMP množinou — Core lo0.0 žádný IGMP záměr nemá.

Scope bez inet.2 statik → **žádné řádky** (ticho, ne SKIP — gate na záměr jako
u `pim_neighbor_state`). Jinak se první vloží souhrnný řádek: `OK | Multicast forwarding
status : {m} inet.2 prefixu se streamem`, mají-li všechny inet.2 prefixy aspoň jeden stream,
jinak `BROKEN | ... : {k} z {m} inet.2 prefixu bez streamu` — souhrnný počet místo čtyř
prázdných SKIP řádků, které dřív dostal prefix bez streamu. Pak pro každý inet.2 prefix
`assign_sources()` přiřadí routy z multicast tabulky podle toho, jestli zdroj leží uvnitř
prefixu — při víc pokrývajících prefixech vyhrává nejdelší, každá routa se počítá jen jednou.

- Existuje aspoň jedna routa se zdrojem v prefixu → `OK | Multicast forwarding
  status : Existuje S,G pro {prefix}` (`DEGRADED`, liší-li se množina S,G proti
  baseline); jinak `BROKEN | ... : Neexistuje S,G pro {prefix}` — žádné další řádky pro
  prefix bez streamu, jeho počet už nese souhrnný řádek.
- Per přiřazené (S,G): **Upstream interface** — `OK`, je-li upstream jedním z `via`
  změřených route collectorem pro ten inet.2 prefix (`routes["inet.2"][prefix]["via"]`,
  ECMP/qualified-next-hop dávají víc `via`); není-li inet.2 routa v tabulce vůbec →
  `SKIP : routa neni v tabulce` (`FAIL` za tuhle příčinu nese `static_route_status`,
  ne tenhle check znovu). **Downstream interfaces** — `OK` se seznamem spojeným
  čárkou, je-li neprázdný; jinak `BROKEN : Zadne downstream interfacy`.
  **Forwarding rate packets**, **Route uptime** — `stream_rows()`.

Mutant kill (2026-09-03, ověřeno spuštěním):
- `assign_sources`: sort podle `prefixlen` vzestupně místo sestupně → padne
  `test_assign_sources_longest_prefix_wins_and_each_route_once`.
- `upstream in vias` → `upstream.startswith(("ge-", "xe-", "et-", "ae"))` → padne
  `test_core_upstream_must_be_one_of_via` (ECMP scénář se dvěma `via`, kde jen jeden
  odpovídá).

| situace | Outcome | status | `value` |
|---|---|---|---|
| žádné inet.2 statiky v selektorech scopu | *(žádný nález)* | — | — |
| souhrnný řádek: aspoň jeden inet.2 prefix bez streamu | `broken` | FAIL | `<k>/<m> bez streamu` |
| souhrnný řádek: každý inet.2 prefix má stream | `ok` | PASS | `<m> inet.2 prefixu` |
| prefix bez přiřazené routy vůbec | `broken` | FAIL | `Neexistuje S,G pro <prefix>` |
| prefix s přiřazenou routou/routami, množina S,G se liší od baseline | `degraded` | WARN | `Existuje S,G pro <prefix>` |
| prefix s přiřazenou routou/routami, množina beze změny (nebo bez baseline) | `ok` | PASS | `Existuje S,G pro <prefix>` |
| per routa, inet.2 routa vůbec není v tabulce | `SKIP` | SKIP | `routa neni v tabulce` |
| per routa, upstream není mezi `via` inet.2 routy | `broken` | FAIL | jméno upstreamu nebo `-` |
| per routa, upstream je jednou z hodnot `via` | `ok` | PASS | jméno upstreamu |
| per routa, `downstream_interfaces` prázdné | `broken` | FAIL | `Zadne downstream interfacy` |
| per routa, `downstream_interfaces` neprázdné | `ok` | PASS | seznam spojený čárkou |
| per routa, `forwarding_rate_pps` je `None` | `SKIP` | SKIP | `statistiky nedostupne` |
| per routa, `pps > 0` | `ok` | PASS | `<pps> pps` |
| per routa, `pps <= 0` | `broken` | FAIL | `<pps> pps` |
| per routa, route uptime (vždy) | `info` | INFO | formátovaný uptime nebo `-` |

### `mvpn_cmulticast_status` (IPVPN/mvpn-igmp, both, critical)

`service_types={"IPVPN"}`, `service_subtypes={"mvpn-igmp"}`. Vyžaduje `igmp_group`,
`mvpn_instance`.

Bez IGMP skupin → `SKIP | C-Multicast status : bez IGMP reportu`. Instance chybí
v `mvpn_instance` výpisu → `BROKEN | ... : instance neni v mvpn vypisu`. Jinak pro
každý (S,G) pár:

- **C-Multicast status** — `OK` s `S/32:G/32`, existuje-li c-multicast záznam
  se shodným source i group prefixem (`_cmulticast_entry`, ASM `(*, G)` porovnává jen
  group); jinak `BROKEN : chybi c-multicast zaznam`.
- **Provider tunnel** — `OK` s celým `provider_tunnel_id`, je-li `sender_pe`
  parsovaný; `BROKEN : bez provider tunelu`, je-li prázdný nebo `I-P-tnl:invalid`.
  Proti baseline: `DEGRADED`, liší-li se **jen sender PE** (první adresa za
  `P2MP:`) — tunnel id se při re-signalizaci LSP mění bez zmeny služby, takže se
  neporovnává celý řetězec.

Mutant kill (2026-09-03, ověřeno spuštěním): `_tunnel_row`: porovnání `was_pe != pe`
nahrazeno `was_tunnel != tunnel` (celý řetězec místo sender PE) nechá padnout
`test_mvpn_sender_pe_change_is_warn_but_tunnel_id_change_is_not` (re-signalizovaný
tunnel se stejnou PE adresou by dostal falešné `DEGRADED`).

| situace | Outcome | status | `value` |
|---|---|---|---|
| bez IGMP skupin na scopu | `SKIP` | SKIP | `bez IGMP reportu` |
| instance chybí ve výpisu `mvpn_instance` | `broken` | FAIL | `instance neni v mvpn vypisu` |
| pár nemá odpovídající c-multicast záznam | `broken` | FAIL | `chybi c-multicast zaznam` |
| pár má odpovídající c-multicast záznam | `ok` | PASS | `<source_prefix>:<group_prefix>` |
| řádek tunelu, `sender_pe` prázdný/falsy | `broken` | FAIL | tunnel id nebo `-` |
| řádek tunelu, `sender_pe` je, baseline sender PE se liší | `degraded` | WARN | tunnel id |
| řádek tunelu, `sender_pe` je, baseline sender PE sedí (nebo bez baseline) | `ok` | PASS | tunnel id |

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
| subjekt běží, baseline byla deaktivovaná | `recovered` | RECV | `aktivni` |
| subjekt deaktivovaný, bez baseline k porovnání | `SKIP` | SKIP | důvod deaktivace subjektu |
| subjekt i baseline běží | — | — (žádný nález) | — |

Důvod deaktivace (`Scope.deactivation_reason`) je jedna ze tří hodnot: `RI deactivated`,
`interface deactivated`, `RI + interface deactivated` — podle toho, jestli je deaktivovaná
routing instance, rozhraní, nebo obojí.

**Na pořadí větví záleží:** `v baseline patril k teto sluzbe, v subjektu uz ne` se testuje **před** `BGP neni Established`.
Opačné pořadí by tiše ztratilo případ, kdy migrace shodila ze stolu ochranu, která tam byla,
a zároveň nedojelo BGP — což je přesně kombinace, kterou je potřeba vidět.

Label je `BFD (<peer>)`, `family` odvozená `peer_family()` z adresy peeru (sdílená
s `checks/bgp.py`), takže IPv6 peer zděděný z BGP skupiny skončí v sekci `IPv6`.
