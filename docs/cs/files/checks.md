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

### `interface_errors` (state, advisory)

Součet `input_errors`, `output_errors`, `framing_errors` musí být 0. Jen tranzitní rozhraní.

### `interface_traffic` (both, advisory)

- **bez baseline** (nebo když rozhraní v baseline není): `require_nonzero` → `input_pps`
  i `output_pps` musí být > 0, jinak `broken` → WARN (`provoz netece`);
- **s baseline**: pokles v procentech proti `tolerance_percent` (default −60 %). Do `details`
  se zapíše změna per směr, do `baseline`/`subject` surová čísla.

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

- stav ≠ `Established` → `broken` → **FAIL**,
- stav `Established`, ale **v baseline byl jiný** → `degraded` → **WARN** se zprávou
  `stav se zmenil X -> Established`. I zlepšení stojí za zmínku, ale není to porucha,
- stav `Established` a shodný (nebo bez baseline) → PASS.

### `bgp_prefix_counts` (compare, advisory)

Porovnává `received` / `accepted` / `advertised` proti `tolerance_percent` (default −10 %).
Peer, který v baseline není, dostane `SKIP` — ne PASS.

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

Oba checky mají `requires_inventory = True` (na device scope tedy vrací `SKIP`) a běží jen
na `Internet` a `IPVPN`. Oba jsou vědomě **best-effort** — CPE může být vypnuté nebo blokovat
ICMP — proto default severity `advisory`.

### `arp_present` (state, advisory)

Na rozhraních služby musí být aspoň jeden ARP záznam. Do `subject` se uloží **celý seznam**
naučených adres; u ne-p2p subnetů jich může být víc.

### `ping_reachability` (state, advisory)

Čte hotové výsledky ze snapshotu — cíle se resolvovaly už při `capture` (ARP → ping).

| situace | outcome | status |
|---|---|---|
| odpověděly všechny cíle | `ok` | PASS |
| odpověděla část | `degraded` | **WARN** (i kdyby byl check přepnutý na `critical`) |
| neodpověděl nikdo | `broken` | WARN (advisory) / FAIL (critical) |
| ve snapshotu nejsou žádné cíle | `skip` | SKIP |

Do `details` jde rozpad **per adresu** (`sent`, `received`, `loss_percent`, `resolved_from`,
`rtt_avg_ms`), takže je z výsledku vidět, která adresa neodpověděla a jestli byla zjištěná
z ARP nebo dopočtená ze subnetu.
