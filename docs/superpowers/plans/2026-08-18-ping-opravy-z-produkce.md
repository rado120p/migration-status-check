# Ping opravy z produkce — implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tři produkční opravy ping testů: cílová adresa i u úspěšného řádku, zrušení explicitní source adresy, vyloučení `.local` (remote-PE) záznamů z ping cílů.

**Architecture:** Vše žije ve dvou souborech: odvozování cílů a RPC v `migration_validator/probes/ping.py`, render řádků v `migration_validator/checks/reachability.py`. Self-ping guardy přecházejí z porovnání proti `source` na existující množinu `own_addresses`. Snapshot schema zůstává 9 (pole `source` nikdo nečte, bump by zneplatnil rozběhnuté migrace).

**Tech Stack:** Python 3, pytest, lxml, ruff (mechanický formát, bez magic trailing comma — viz commit 736d63c).

**Spec:** `docs/superpowers/specs/2026-08-18-ping-opravy-z-produkce-design.md`

## Global Constraints

- Komentáře a docstringy česky bez diakritiky v kódu (styl souboru), dokumentace v `docs/cs/` s diakritikou.
- Testy se pouští `.venv/bin/python -m pytest` z kořene repa (`/home/rado/Desktop/scripts/migration-status-check`).
- Snapshot `SCHEMA_VERSION` zůstává 9 — žádný bump.
- Fixtures s `source` se upravují vědomě per test, ne hromadným sedem.
- Commit message česky, prefix `fix:`/`refactor:`/`test:`/`docs:` podle obsahu (viz `git log --oneline`).

---

### Task 1: Cílová adresa u úspěšného ping řádku

**Files:**
- Modify: `migration_validator/checks/reachability.py:259-270` (`_ping_findings`, OK větev)
- Test: `tests/checks/test_reachability.py`

**Interfaces:**
- Consumes: `_ping_findings(probes, family, prefixes)` — beze změny signatury.
- Produces: OK `Finding.value` má tvar `"{received}/{sent}  {rtt} ms  {target}"`, bez RTT `"{received}/{sent}  {target}"`. BROKEN větev beze změny. Task 4 na tenhle tvar odkazuje v docs.

- [ ] **Step 1: Napiš failing testy**

Do `tests/checks/test_reachability.py` za `test_ping_all_targets_reachable_passes` přidej:

```python
def test_ping_ok_value_carries_target():
    """Produkce: u uspesneho radku nebylo poznat, KAM ping sel - cil
    nesla jen message a BROKEN vetev. Hodnota ma tvar '5/5  2.1 ms  IP'."""
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "198.11.13.2",
                    "family": 4,
                    "sent": 5,
                    "received": 5,
                    "rtt_avg_ms": 2.1,
                }
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.value == "5/5  2.1 ms  198.11.13.2"


def test_ping_ok_value_without_rtt_still_carries_target():
    ctx = _ctx(
        {"ping": [{"target": "198.11.13.2", "family": 4, "sent": 5, "received": 5}]}
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.value == "5/5  198.11.13.2"
```

- [ ] **Step 2: Ověř, že padají**

Run: `.venv/bin/python -m pytest tests/checks/test_reachability.py -k carries_target -v`
Expected: 2× FAIL na assertu value (`"5/5  2.1 ms" != "5/5  2.1 ms  198.11.13.2"`).

- [ ] **Step 3: Implementace**

V `migration_validator/checks/reachability.py`, `_ping_findings`, OK větev (`if received:`) — v konstrukci `Finding` změň jen argument `value`:

```python
                    value=f"{value}  {target}",
```

BROKEN větev nech beze změny (cíl už nese).

- [ ] **Step 4: Ověř, že projdou i zbytek souboru**

Run: `.venv/bin/python -m pytest tests/checks/test_reachability.py -v`
Expected: vše PASS (žádný stávající test OK hodnotu nepinuje).

- [ ] **Step 5: Commit**

```bash
git add tests/checks/test_reachability.py migration_validator/checks/reachability.py
git commit -m "fix: uspesny ping radek nese cilovou adresu"
```

---

### Task 2: Zrušení explicitní source adresy

**Files:**
- Modify: `migration_validator/probes/ping.py` (smazat `source_address`, pole `source` z `PingTarget`, guardy na `own_addresses`, `run_ping` bez `source` kwarg)
- Modify: `tests/probes/test_ping.py` (smazat testy `source_address`, přepsat guard testy)
- Modify: `tests/conftest.py:329` (fixture `synthetic_snapshot` bez klíče `source`)

**Interfaces:**
- Consumes: dnešní `PingTarget(scope_id, target, source, routing_instance, resolved_from, family, interface=None)`.
- Produces: **nová signatura** `PingTarget(scope_id, target, routing_instance, resolved_from, family, interface=None)`; `to_dict()` bez klíče `source`; `run_ping` RPC kwargs jen `host`, `count`, `rapid` (+ `routing_instance`, `interface` když jsou). Task 3 staví na této signatuře, Task 4 ji dokumentuje.

- [ ] **Step 1: Přepiš testy v `tests/probes/test_ping.py`**

a) Smaž těchto 5 testů (funkce `source_address` končí): `test_source_is_interface_address`, `test_source_prefers_virtual_gw_on_irb`, `test_source_none_when_no_address`, `test_source_follows_target_family`, `test_virtual_gateway_wins_over_interface_address`. Z importu na řádku 5-10 odeber `source_address` a přidej `PingTarget, run_ping`.

b) V `test_targets_come_from_arp` nahraď řádek `assert all(target.source == "198.11.13.1" for target in targets)` za:

```python
    assert "source" not in targets[0].to_dict()
```

c) V `test_no_resolved_target_ever_equals_its_own_source` přejmenuj na `test_no_resolved_target_is_ever_own_address`, docstring uprav („ping na vlastni adresu je vzdy nesmysl — guard je own_addresses, ne source") a závěrečný assert nahraď za:

```python
    own = {"152.11.14.2", "152.11.14.1", "2001:db8:11:15::0", "2001:db8:11:15::1", "198.11.13.1"}
    assert targets
    assert all(target.target not in own for target in targets)
```

d) V `test_arp_guard_drops_own_address_at_any_position` a `test_nd_guard_drops_own_address_at_any_position` smaž poslední assert (`assert all(t.target != t.source ...)`) — assert na přesný seznam `neighbours` self-ping pokrývá sám. V docstrinzích nahraď zmínky o `if address != source` za `own_addresses guard v resolve_targets` (tvrzení o mutantech nech, pozice-parametrizace platí dál).

e) V `test_fallback_guard_catches_self_ping_owned_does_not_cover` uprav docstring: scénář (dvě vlastní adresy, /32 + /30, žádný VGW) teď chytá `owned` naplněné všemi local adresami, ne `fallback != source`. Assert `targets == []` zůstává.

f) Přidej test na RPC kwargs (na konec souboru, k `parse_ping_result` testům):

```python
class _RpcRecorder:
    def __init__(self):
        self.kwargs = None

    def ping(self, **kwargs):
        self.kwargs = kwargs
        return etree.fromstring(
            "<ping-results><probe-results-summary>"
            "<probes-sent>5</probes-sent><responses-received>5</responses-received>"
            "<packet-loss>0</packet-loss><rtt-average>2100</rtt-average>"
            "</probe-results-summary></ping-results>"
        )


class _RpcDevice:
    def __init__(self):
        self.rpc = _RpcRecorder()


def test_run_ping_never_sets_source():
    """Router voli egress adresu sam - explicitni source u multi-range irb
    miril mimo subnet cile a ping padal (produkce 2026-08)."""
    device = _RpcDevice()
    target = PingTarget("svc:X:IPVPN", "198.11.13.2", "L3VPN-CPE13-NNI", "arp", 4)

    record = run_ping(device, target)

    assert "source" not in device.rpc.kwargs
    assert device.rpc.kwargs["host"] == "198.11.13.2"
    assert device.rpc.kwargs["routing_instance"] == "L3VPN-CPE13-NNI"
    assert "source" not in record
```

- [ ] **Step 2: Ověř, že padají**

Run: `.venv/bin/python -m pytest tests/probes/test_ping.py -v`
Expected: FAIL — `test_run_ping_never_sets_source` na TypeError (PingTarget má ještě 7 polí, `"L3VPN-CPE13-NNI"` padne do `source`), `test_targets_come_from_arp` na `"source" not in to_dict()`.

- [ ] **Step 3: Implementace v `migration_validator/probes/ping.py`**

a) Smaž celou funkci `source_address` (řádky 47-61).

b) `PingTarget`: smaž pole `source: str | None` a klíč `"source"` z `to_dict()`. Nové pořadí polí: `scope_id, target, routing_instance, resolved_from, family, interface`.

c) Přidej helper (vedle `_usable_nd`):

```python
def _is_own(address: str, own: set[ipaddress.IPv4Address | ipaddress.IPv6Address]) -> bool:
    """Textove porovnani nestaci - zkraceny IPv6 zapis by proklouzl."""
    try:
        return ipaddress.ip_address(address) in own
    except ValueError:
        return False
```

d) V `resolve_targets`:
- smaž řádek `source = source_address(scope, family)`,
- baseline list comprehension: podmínku `if address != source and ipaddress.ip_address(address) not in own_addresses` zjednoduš na `if ipaddress.ip_address(address) not in own_addresses`,
- arp/nd tier: podmínku `if address != source` nahraď za `if not _is_own(address, own_addresses)` a uprav komentář (guard je own_addresses — gratuitous ARP / duplicitni adresa muze vlastni IP do tabulky dostat na kterekoliv pozici),
- subnet-fallback větev: volání změň na `subnet_fallback(local, family, owned=[*local, *owned])` a podmínku `if fallback and fallback != source:` na `if fallback:` (owned s local adresami pokrývá překryv /32+/30, který dřív chytal `!= source` — viz test e),
- všechny konstrukce `PingTarget(...)` uprav na novou signaturu (vypadne argument `source`).

e) `run_ping`: smaž blok `if target.source: kwargs["source"] = target.source`.

f) Docstringy: v module docstringu „bez inventory neni znam cil ani source adresa" → „bez inventory neni znam cil"; v `subnet_fallback` docstringu přepiš odstavec o `source_address()` a VGW (owned teď nese local + VGW, historka o zdroji odpadá); v `resolve_targets` docstringu žádná zmínka o source není — zkontroluj a nech.

- [ ] **Step 4: Uprav `tests/conftest.py`**

Ve fixture `synthetic_snapshot` (řádek ~329) smaž řádek:

```python
                "source": scope.selectors.local_ipv4[0].split("/")[0],
```

- [ ] **Step 5: Ověř celou sadu**

Run: `.venv/bin/python -m pytest`
Expected: vše PASS (1 skip je známý stav). Kdyby padl test mimo `probes`/`conftest`, čti ho — pinuje source a patří do vědomé úpravy, ne do sedu.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/probes/ping.py tests/probes/test_ping.py tests/conftest.py
git commit -m "fix: ping bez explicitni source adresy, self-ping guard pres own_addresses"
```

---

### Task 3: Vyloučení `.local` (remote-PE) záznamů

**Files:**
- Modify: `migration_validator/probes/ping.py` (`_is_remote_learned` + tři místa v resoluci)
- Test: `tests/probes/test_ping.py`

**Interfaces:**
- Consumes: `PingTarget(scope_id, target, routing_instance, resolved_from, family, interface=None)` z Tasku 2; ARP/ND entry dicty s klíčem `learned_via` (plní `collectors/arp.py:split_learned_via`, ND stejně).
- Produces: `_is_remote_learned(entry) -> bool`; záznamy s `learned_via` začínajícím `.local` se nikdy nestanou ping cílem (živý ARP, živý ND, baseline).

- [ ] **Step 1: Napiš failing testy**

Do `tests/probes/test_ping.py` za `test_targets_come_from_arp` přidej:

```python
def test_local_learned_arp_entry_is_not_a_target():
    """EVPN VLAN-AWARE: zaznam nauceny pres .local..N je host za vzdalenym
    PE - pingat ho nema smysl (produkce 2026-08). Prefix, ne substring:
    learned_via 'ae0.14' projit musi."""
    arp = [
        {"ip": "198.11.13.2", "interface": "ge-0/0/2.113", "learned_via": ".local..9"},
        {"ip": "198.11.13.3", "interface": "ge-0/0/2.113", "learned_via": "ae0.14"},
        {"ip": "198.11.13.4", "interface": "ge-0/0/2.113", "learned_via": None},
    ]

    targets = resolve_targets([_scope()], arp)

    assert [t.target for t in targets] == ["198.11.13.3", "198.11.13.4"]


def test_local_learned_nd_entry_is_not_a_target():
    scope = _scope(
        interfaces=("et-0/0/8.13",), addresses=(), local_ipv6=("2001:abcd:11:13::a/64",)
    )
    nd = [
        {
            "ip": "2001:abcd:11:13::b",
            "mac": "0c:00:ef:5e:df:01",
            "interface": "et-0/0/8.13",
            "state": "reachable",
            "learned_via": ".local..7",
        },
        {
            "ip": "2001:abcd:11:13::c",
            "mac": "0c:00:ef:5e:df:02",
            "interface": "et-0/0/8.13",
            "state": "reachable",
            "learned_via": None,
        },
    ]

    targets = resolve_targets([scope], [], nd)

    assert [t.target for t in targets] == ["2001:abcd:11:13::c"]


def test_baseline_skips_local_learned_entries():
    """Baseline paruje jen podle subnetu - bez filtru by remote-PE hosty ze
    stareho boxu vratil presne pri cutoveru, kdy se baseline pouziva."""
    scope = _scope(interfaces=("irb.14",), addresses=("152.11.14.1/29",))
    baseline_arp = [
        {"ip": "152.11.14.4", "interface": "irb.14", "learned_via": ".local..5"},
        {"ip": "152.11.14.5", "interface": "irb.14", "learned_via": "ae0.14"},
    ]

    targets = resolve_targets([scope], [], baseline_arp=baseline_arp)

    assert [t.target for t in targets] == ["152.11.14.5"]
    assert targets[0].resolved_from == "baseline-arp"
```

- [ ] **Step 2: Ověř, že padají**

Run: `.venv/bin/python -m pytest tests/probes/test_ping.py -k local_learned -v`
Expected: 3× FAIL — `.local` záznamy jsou dnes v seznamech cílů navíc.

- [ ] **Step 3: Implementace v `migration_validator/probes/ping.py`**

a) Helper vedle `_usable_nd`:

```python
def _is_remote_learned(entry: dict[str, Any]) -> bool:
    """EVPN VLAN-AWARE: zaznam nauceny pres '.local..N' je host za vzdalenym
    PE - lokalni ping na nej nic nemeri. Prefix, ne substring: learned_via
    'ae0.14' je platny lokalni L2 protejsek a projit musi."""
    return (entry.get("learned_via") or "").startswith(".local")
```

b) `_baseline_addresses`: do smyčky za `if not ip: continue` přidej:

```python
        if _is_remote_learned(entry):
            continue
```

c) Živý ARP tier — do list comprehension přidej podmínku `and not _is_remote_learned(entry)`. Živý ND tier — totéž.

- [ ] **Step 4: Ověř celou sadu**

Run: `.venv/bin/python -m pytest`
Expected: vše PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/probes/ping.py tests/probes/test_ping.py
git commit -m "fix: .local (remote-PE) zaznamy nejsou ping cile v zadnem tieru"
```

---

### Task 4: Dokumentace a závěrečná kontrola

**Files:**
- Modify: `docs/cs/files/probes.md` (sekce `source_address` pryč, `PingTarget` pole, `run_ping` kwargs, `.local` filtr)
- Modify: `docs/cs/files/checks.md` (sekce `ping_reachability` — tvar OK hodnoty, pokud tvar hodnoty zmiňuje)

**Interfaces:**
- Consumes: chování z Tasků 1-3 (tvar OK hodnoty, `PingTarget` bez `source`, `_is_remote_learned`).
- Produces: dokumentace odpovídá kódu; žádný další task.

- [ ] **Step 1: Uprav `docs/cs/files/probes.md`**

- řádek ~11 (tabulka): „bez scope není znám cíl ani source adresa" → „bez scope není znám cíl",
- řádek ~23: výčet polí `PingTarget` bez `source`,
- sekci `### source_address(scope, family)` (řádky ~30-38) smaž celou a nahraď krátkým odstavcem: source se nenastavuje — router volí egress adresu per cíl; u rozhraní s více rozsahy explicitní source mířil mimo subnet cíle a ping padal (produkce 2026-08),
- řádek ~47 (komentář u `subnet_fallback` o VGW jako zdroji): přepiš — `owned` nese všechny vlastní adresy (local + VGW), fallback nikdy nevrátí vlastní IP,
- řádek ~93: `device.rpc.ping(host=..., count=..., rapid=True, [source=...], ...)` → bez `[source=...]`,
- doplň odstavec o `.local` filtru: záznamy s `learned_via` začínajícím `.local` (EVPN remote-PE) se nepingují v žádném tieru, ve faktech a ARP/ND checkách zůstávají.

- [ ] **Step 2: Uprav `docs/cs/files/checks.md`**

V sekci `### ping_reachability` (řádek ~319) zkontroluj, zda popisuje tvar sloupce HODNOTA; pokud ano, dopiš že OK řádek nese i cílovou adresu (`5/5  2.1 ms  10.1.1.1`). Pokud tvar popisuje jen obecně, nic neměň.

- [ ] **Step 3: Závěrečná kontrola**

```bash
grep -rn "source" migration_validator/probes/ping.py   # ocekavano: zadny vyskyt
.venv/bin/python -m pytest
.venv/bin/ruff check migration_validator tests && .venv/bin/ruff format --check migration_validator tests
```

Expected: grep prázdný, testy PASS (1 známý skip), ruff bez nálezů.

- [ ] **Step 4: Commit**

```bash
git add docs/cs/files/probes.md docs/cs/files/checks.md
git commit -m "docs: probes a checks po zruseni source a .local filtru"
```
