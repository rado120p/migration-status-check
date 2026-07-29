# `checks/` — vyhodnocovací logika

Soubory: `base.py`, `registry.py`, `all.py`, `ifaces.py`, `bgp.py`, `evpn.py`,
`reachability.py` a prázdný `__init__.py`.

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
    mode: Mode                    # state | compare | both
    requires: tuple[str, ...]     # oblasti z facts/probes, např. ("bgp",)
    requires_inventory: bool
    service_types: frozenset[str] | None    # None = všechny
    default_severity: Severity

    def run(self, ctx) -> list[Finding]
```

`applies_to(scope)` vrací `True` pro **device scope vždy** (nemá podle čeho filtrovat)
a jinak porovnává `service_type`.

`describe()` je to, co vidí `mig-validate checks` a budoucí GUI.

### `run_check()` — jediná cesta ke statusu

Pořadí bran, kterými check projde:

1. `config.enabled(id)` je `False` → **prázdný seznam** (check v reportu vůbec není),
2. `applies_to(scope)` je `False` → prázdný seznam,
3. `requires_inventory` a scope je device → `SKIP` (`check vyzaduje inventory, snapshot ji neobsahuje`),
4. `mode == COMPARE` a není baseline → `SKIP` (`porovnavaci check bez baseline snapshotu`),
5. některá oblast z `requires` je v `failed_collectors` → `SKIP` **s původní chybovou
   hláškou z capture**,
6. `check.run()` vyhodí výjimku → `SKIP` (`check selhal: ...`) — jeden rozbitý check nesmí
   zabít celý běh,
7. jinak se každý `Finding` převede na `CheckResult` přes `derive_status(outcome, severity)`.

Rozdíl mezi bodem 1–2 (prázdný seznam) a 3–6 (`SKIP`) je záměrný: *„sem to nepatří"* se
nemá počítat do souhrnu, *„nezměřeno"* ano.

## `registry.py`

`@register` zapíše instanci pod `cls.id`; duplicita je `ValueError`. `all_checks()` vrací
abecedně seřazený seznam — proto je pořadí checků ve výstupu stabilní. `checks_for(scope,
config)` a `get_check(id)` jsou pomocné dotazy (engine používá `all_checks()` a filtruje
až v `run_check`).

## `all.py`

Importuje `bgp`, `evpn`, `ifaces`, `reachability`. Je to samostatný modul **kvůli cyklickému
importu**: `checks/ifaces.py` importuje `checks/base.py`, takže `checks/__init__.py` nesmí
importovat `ifaces`. `load_all()` je idempotentní no-op — práci udělal import.

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

Součet `input_errors`, `output_errors`, `framing_errors` musí být 0. Jen tranzitní rozhraní.
Jeden Finding na rozhraní (label `Interface errors (<jméno>)`) — countery se do zprávy sesypou
dohromady (`input_errors=3`), na rozdíl od `interface_state`/`interface_traffic` se nerozpadají
na samostatné řádky.

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

Každý Finding nese `label="BGP status"` a `family` odvozenou `peer_family()` z adresy peeru —
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

Label řádku je `BGP <klíč>-prefix-count` (např. `BGP active-prefix-count`), takže report má
pět samostatných řádků na RIB, ne jeden souhrnný.

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

- stav rozhraní musí být `Up`,
- **musí přijít remote SID**. Local a remote SID se u EVPN-VPWS záměrně **liší** — každá
  strana inzeruje svoje service ID (`local 1000; remote 2000`), takže rovnost není invariant.
  FAIL nastane, až když remote SID vůbec nepřijde.

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
- **`link_local_is_configured(scope)`** (v `nd_present`) / funkčně stejná
  `_link_local_configured()` v `probes/ping.py` — má služba link-local adresu přímo
  nakonfigurovanou pod rozhraním? Testuje se **přítomnost, ne výlučnost**: stačí, aby mezi
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

- ve snapshotu nejsou pro tenhle scope žádné cíle → `SKIP` (`pro tento scope nejsou ve
  snapshotu zadne cile pingu`);
- probe bez rozpoznané rodiny (`family` mimo `4`/`6`) → vlastní `SKIP` jmenující cíle
  (`probe bez rodiny nelze vyhodnotit: ...`) — jinak by probe z výsledku tiše zmizel, místo
  aby řekl, že se nevyhodnotil;
- jinak **jeden `Finding` na cíl**, seskupené podle `family`:
  - odpověděl aspoň jeden paket → `ok`, zpráva `<cil>: odpovedelo N z M`,
  - neodpověděl žádný → `broken`, zpráva `<cil>: neodpovedel (M paketu)`.

`value` je `<received>/<sent>` a u úspěšné odpovědi ještě `  <rtt> ms`; u neúspěchu
`  <cil> neodpovedel`. `details` nese `resolved_from` (`arp` | `nd` | `subnet-fallback`) a
`address` (`owning_prefix()`), takže je z výsledku vidět, který nakonfigurovaný rozsah cíl
zastupuje a jestli byl zjištěný z ARP/ND, nebo dopočtený ze subnetu.
