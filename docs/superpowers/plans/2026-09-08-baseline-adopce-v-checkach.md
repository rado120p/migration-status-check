# Baseline porovnání — vlna 2: adopce UNCHANGED v checkách — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Každá BROKEN/DEGRADED větev, kde jde stav porovnat s baseline, vrací `Outcome.UNCHANGED`, když baseline (změřená) měla stejný stav; `value` a `baseline_value` mluví stejným slovníkem; řádky, které se neporovnávají, nesou `compared=False`.

**Architecture:** Jeden sdílený helper `checks/baseline.py: unchanged_or(outcome, ctx, area, same)` drží podmínku R-3 na jednom místě. Každý modul dostane úpravu per větev podle katalogu ve specu. STATE checky, které porovnat jde (`interface_state`, `arp_present`, `nd_present`, `ping_reachability`, `interface_optics_alarms`), přejdou na `Mode.BOTH` a čtou baseline záznam podle klíče (jméno rozhraní po R-6, IP, target, port+lane).

**Tech Stack:** Python 3.13, pytest (`pyats-venv/bin/python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-09-08-baseline-porovnani-a-multicast-opravy-design.md` (R-3, R-5, katalog větví)

## Global Constraints

- Větev `baseline-porovnani-2026-09-08`, navazuje na vlnu 1 (`2026-09-08-baseline-infrastruktura.md`) — všech 7 tasků vlny 1 musí být hotových (`Outcome.UNCHANGED`, `Finding.compared`, `ctx.baseline_measured`, R-6 překlíčování).
- UNCHANGED **jen** přes `unchanged_or(...)` (Task 1), nikdy přímo — helper vynucuje `ctx.baseline_measured(area)`.
- `ctx.baseline_measured(area)` je **pozitivní důkaz** (final review vlny 1): `CheckContext.baseline_collectors[area]["status"] == "ok"`; pro `"ping"` = baseline má probe záznamy scopu. **Každý testovací helper, který předává `baseline`, musí předat i `baseline_collectors={area: {"status": "ok"} for area in baseline}`**, jinak UNCHANGED nikdy nenaskočí (test o selhaném/nezaznamenaném collectoru předá vlastní dict). Stávající `_ctx` helpery v testech (`test_bgp`, `test_ifaces`, `test_routes`, `test_reachability`, `test_evpn`, `test_bfd`, `test_optics`, `test_multicast`) o tento default rozšiř v rámci tasku, který je používá.
- Zpráva UNCHANGED řádku = původní zpráva + `, stejne jako v baseline`. `value` zůstává (stav dál říká, co je rozbité).
- `baseline_value` u UNCHANGED = tentýž řetězec jako `value` (vzniká stejnou funkcí, R-5).
- Nemění se: `deactivation_state`, deaktivační větve (`deactivation_outcome`), `traffic_ceased`, `sender site bez vzdaleneho receiveru`, RECOVERED logika.
- Suite zelená po každém tasku: `pyats-venv/bin/python -m pytest -q`.

---

## Task 1: Sdílený helper `unchanged_or`

**Files:**
- Create: `migration_validator/checks/baseline.py`
- Test: `tests/checks/test_baseline_helper.py`

**Interfaces:**
- Produces:
  ```python
  def unchanged_or(outcome: Outcome, ctx: CheckContext, area: str, same: bool) -> Outcome
  def suffix(outcome: Outcome) -> str   # ", stejne jako v baseline" pro UNCHANGED, jinak ""
  ```
  `unchanged_or` vrátí `Outcome.UNCHANGED`, když `outcome in (BROKEN, DEGRADED)`, `same` je True a `ctx.baseline_measured(area)`; jinak vrátí `outcome` beze změny.

- [ ] **Step 1: Failing test**

```python
"""Jedina brana pro Outcome.UNCHANGED (R-3, spec 2026-09-08)."""
from migration_validator.checks.base import CheckContext
from migration_validator.checks.baseline import suffix, unchanged_or
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _ctx(baseline, collectors=None):
    scope = Scope(id="svc:x:Core", kind="service", key=ScopeKey("x", "Core", "transit"),
                  selectors=Selectors(interfaces=["ge-0/0/1.0"]))
    if collectors is None and baseline is not None:
        collectors = {area: {"status": "ok"} for area in baseline}
    return CheckContext(scope=scope, subject={}, baseline=baseline, config=default_config(),
                        baseline_collectors=collectors or {})


def test_broken_with_same_measured_baseline_is_unchanged():
    assert unchanged_or(Outcome.BROKEN, _ctx({"ldp_neighbor": {}}), "ldp_neighbor", True) is Outcome.UNCHANGED
    assert unchanged_or(Outcome.DEGRADED, _ctx({"ldp_neighbor": {}}), "ldp_neighbor", True) is Outcome.UNCHANGED


def test_not_same_or_no_baseline_or_failed_collector_keeps_outcome():
    assert unchanged_or(Outcome.BROKEN, _ctx({"ldp_neighbor": {}}), "ldp_neighbor", False) is Outcome.BROKEN
    assert unchanged_or(Outcome.BROKEN, _ctx(None), "ldp_neighbor", True) is Outcome.BROKEN
    failed = _ctx({"ldp_neighbor": {}}, collectors={"ldp_neighbor": {"status": "error", "message": "RpcError"}})
    assert unchanged_or(Outcome.BROKEN, failed, "ldp_neighbor", True) is Outcome.BROKEN
    unrecorded = _ctx({"ldp_neighbor": {}}, collectors={})
    assert unchanged_or(Outcome.BROKEN, unrecorded, "ldp_neighbor", True) is Outcome.BROKEN


def test_ok_and_skip_pass_through():
    assert unchanged_or(Outcome.OK, _ctx({"x": {}}), "x", True) is Outcome.OK
    assert unchanged_or(Outcome.SKIP, _ctx({"x": {}}), "x", True) is Outcome.SKIP


def test_suffix():
    assert suffix(Outcome.UNCHANGED) == ", stejne jako v baseline"
    assert suffix(Outcome.BROKEN) == ""
```

- [ ] **Step 2: Ověř pád** — `pyats-venv/bin/python -m pytest tests/checks/test_baseline_helper.py -q` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implementace**

```python
"""Jedina brana pro Outcome.UNCHANGED (R-3, spec 2026-09-08).

Shodny spatny stav v baseline i subjektu je PASS se znackou - ale jen
kdyz baseline tu oblast zmerila. Selhany collector stare krabice nesmi
z chyby migrace udelat "stejne jako v baseline" (stav se nefabuluje).
Checky helper volaji misto primeho Outcome.UNCHANGED, aby podminka
zila na jednom miste.
"""

from __future__ import annotations

from migration_validator.checks.base import CheckContext
from migration_validator.models.result import Outcome

UNCHANGED_SUFFIX = ", stejne jako v baseline"


def unchanged_or(outcome: Outcome, ctx: CheckContext, area: str, same: bool) -> Outcome:
    if outcome not in (Outcome.BROKEN, Outcome.DEGRADED):
        return outcome
    if same and ctx.baseline_measured(area):
        return Outcome.UNCHANGED
    return outcome


def suffix(outcome: Outcome) -> str:
    return UNCHANGED_SUFFIX if outcome is Outcome.UNCHANGED else ""
```

- [ ] **Step 4: Testy** → PASS. **Step 5: Commit** `feat(checks): helper unchanged_or - jedina brana pro Outcome.UNCHANGED`.

---

## Task 2: `core_protocols.py` — IS-IS, LDP, PIM, MPLS, BFD transit

**Files:**
- Modify: `migration_validator/checks/core_protocols.py:57-133` (IS-IS), `:198-267` (`_neighbor_findings`), `:330-364` (MPLS), `:405-466` (BFD transit)
- Test: `tests/checks/test_core_protocols.py`

**Interfaces:**
- Consumes: `unchanged_or`, `suffix` (Task 1); baseline klíčovaná jménem subjektu (R-6).
- Produces: v `_neighbor_findings` nová pomocná `_neighbor_value(entry) -> str` (`"Up for …"` / `"Down"`), používaná pro obě strany.

- [ ] **Step 1: Failing testy** (helpery `_scope`, `_ctx(subject_adj, baseline_adj)`, `_ctx_for` už existují; pro LDP/PIM/MPLS/BFD postav kontext stejně jako `_ctx`, jen s jinou oblastí — v souboru už takové testy jsou, drž jejich tvar)

```python
from migration_validator.models.result import UNCHANGED_SINCE_BASELINE, Status
from migration_validator.checks.base import run_check


def _both(area, subject, baseline, collectors=None):
    if collectors is None:
        collectors = {area: {"status": "ok"}}
    return CheckContext(
        scope=_scope(), subject={area: subject}, baseline={area: baseline},
        config=default_config(), baseline_collectors=collectors,
    )


def test_isis_adjacency_missing_in_both_is_unchanged_pass():
    [row] = run_check(IsisAdjacencyStateCheck(), _both("isis_adjacency", {}, {}))
    assert row.status is Status.PASS
    assert row.details[UNCHANGED_SINCE_BASELINE] is True
    assert row.value == MISSING and row.baseline_value == MISSING
    assert row.message.endswith(", stejne jako v baseline")


def test_isis_adjacency_missing_in_both_but_baseline_collector_failed_stays_fail():
    ctx = _both("isis_adjacency", {}, {}, collectors={"isis_adjacency": {"status": "error", "message": "RpcError"}})
    [row] = run_check(IsisAdjacencyStateCheck(), ctx)
    assert row.status is Status.FAIL
    assert row.baseline_value is None


def test_isis_address_missing_in_both_is_unchanged():
    adj = {"system_name": "P1", "state": "Up", "ip_address": None, "ipv6_address": None}
    rows = run_check(IsisAdjacencyStateCheck(), _both("isis_adjacency", {IFACE: adj}, {IFACE: adj}))
    v4 = [r for r in rows if r.label.startswith("IS-IS neighbor IPv4")][0]
    assert v4.status is Status.PASS and v4.details[UNCHANGED_SINCE_BASELINE] is True
    assert v4.baseline_value == MISSING


def test_isis_adjacency_down_in_both_is_unchanged():
    adj = {"system_name": "P1", "state": "Down", "ip_address": "10.0.0.1", "ipv6_address": None}
    rows = run_check(IsisAdjacencyStateCheck(), _both("isis_adjacency", {IFACE: adj}, {IFACE: adj}))
    state = [r for r in rows if r.label.startswith("IS-IS adjacency state")][0]
    assert state.status is Status.PASS and state.value == "Down" == state.baseline_value


def test_ldp_neighbor_missing_in_both_is_unchanged_and_down_row_has_baseline_value():
    [missing] = run_check(LdpNeighborStateCheck(), _both("ldp_neighbor", {}, {}))
    assert missing.status is Status.PASS and missing.baseline_value == "Down"

    down = {"neighbor_address": "10.0.0.1", "uptime_seconds": 0}
    rows = run_check(LdpNeighborStateCheck(), _both("ldp_neighbor", {IFACE: down}, {IFACE: down}))
    status = [r for r in rows if r.label.startswith("LDP neighbor status")][0]
    assert status.status is Status.PASS and status.value == "Down" == status.baseline_value


def test_ldp_neighbor_up_row_carries_baseline_value_same_vocabulary():
    up = {"neighbor_address": "10.0.0.1", "uptime_seconds": 120}
    rows = run_check(LdpNeighborStateCheck(), _both("ldp_neighbor", {IFACE: up}, {IFACE: up}))
    status = [r for r in rows if r.label.startswith("LDP neighbor status")][0]
    assert status.value == "Up for 2m 0s" == status.baseline_value


def test_ldp_uptime_unreadable_row_is_not_compared():
    from migration_validator.models.result import NOT_COMPARED
    entry = {"neighbor_address": "10.0.0.1", "uptime_seconds": None}
    rows = run_check(LdpNeighborStateCheck(), _both("ldp_neighbor", {IFACE: entry}, {IFACE: entry}))
    status = [r for r in rows if r.label.startswith("LDP neighbor status")][0]
    assert status.details[NOT_COMPARED] is False


def test_mpls_down_in_both_is_unchanged_with_normalized_baseline_value():
    entry = {"state": "down"}
    [row] = run_check(MplsInterfaceStateCheck(), _both("mpls_interface", {IFACE: entry}, {IFACE: entry}))
    assert row.status is Status.PASS and row.value == "Down" == row.baseline_value


def test_bfd_transit_no_session_in_both_is_unchanged():
    [row] = run_check(BfdTransitStateCheck(), _both("bfd", {}, {}))
    assert row.status is Status.PASS and row.baseline_value == "Down"
```

- [ ] **Step 2: Ověř pád** — `pyats-venv/bin/python -m pytest tests/checks/test_core_protocols.py -q -k "unchanged or baseline_value or not_compared"` → FAIL.

- [ ] **Step 3: Implementace**

Import: `from migration_validator.checks.baseline import suffix, unchanged_or`.

**IS-IS chybí (`:59-70`)**: nahraď

```python
            if adj is None:
                outcome = unchanged_or(Outcome.BROKEN, ctx, "isis_adjacency", same=was is None)
                was_state = (was or {}).get("state")
                findings.append(Finding(
                    outcome,
                    f"{name}: rozhrani neni v IS-IS adjacency vypisu{suffix(outcome)}",
                    label=qualified(self.label, name),
                    value=MISSING,
                    baseline_value=(
                        MISSING if outcome is Outcome.UNCHANGED
                        else str(was_state) if was_state is not None else None
                    ),
                ))
                continue
            findings.extend(self._rows(ctx, name, adj, was))
```

`_rows(self, ctx, name, adj, was)` — `has_baseline = ctx.has_baseline`; blok adjacency state (`:98-115`):

```python
        if state != "Up":
            outcome = unchanged_or(Outcome.BROKEN, ctx, "isis_adjacency", same=was_state == state)
            message = f"{name}: adjacency {state}{suffix(outcome)}"
        elif ...RECOVERED beze změny...
```

Blok adres (`:117-133`):

```python
            if has_baseline and was is not None and value != was_value:
                outcome = Outcome.DEGRADED
            elif value is not None:
                outcome = Outcome.OK
            else:
                outcome = unchanged_or(
                    Outcome.BROKEN, ctx, "isis_adjacency",
                    same=was is not None and was_value is None,
                )
            rows.append(Finding(
                outcome,
                f"{name}: {label} {value or 'chybi'}{suffix(outcome)}",
                label=qualified(label, name),
                value=str(value) if value else MISSING,
                baseline_value=(
                    MISSING if outcome is Outcome.UNCHANGED
                    else str(was_value) if was_value else None
                ),
            ))
```

**`_neighbor_findings` (`:198-267`)** — přidej nad funkci:

```python
def _neighbor_value(entry: dict[str, Any] | None) -> str | None:
    """Stejny slovnik pro subject i baseline (R-5). None = uptime nezmereno."""
    if entry is None:
        return "Down"
    seconds = entry.get("uptime_seconds")
    if seconds is None:
        return None
    return f"Up for {format_uptime(seconds)}" if seconds > 0 else "Down"
```

Větev `entry is None`:

```python
        if entry is None:
            was_value = _neighbor_value(was)   # "Down" i kdyz was is None
            outcome = unchanged_or(Outcome.BROKEN, ctx, area, same=was_value == "Down")
            findings.append(Finding(
                outcome, f"{name}: soused ve vypisu neni{suffix(outcome)}",
                label=qualified(status_label, name), value="Down",
                baseline_value=was_value if was is not None or outcome is Outcome.UNCHANGED else None,
            ))
            continue
```

Větev `seconds is None`: přidej `compared=False` (uptime nezměřen, není co porovnat). Větev up/down:

```python
        else:
            value = _neighbor_value(entry)
            was_value = _neighbor_value(was) if was is not None else None
            up = seconds > 0
            outcome = Outcome.OK if up else unchanged_or(
                Outcome.BROKEN, ctx, area, same=was_value == "Down",
            )
            findings.append(Finding(
                outcome,
                f"{name}: session {'bezi' if up else 'nebezi'}{suffix(outcome)}",
                label=qualified(status_label, name),
                value=value,
                baseline_value=("Down" if outcome is Outcome.UNCHANGED else was_value),
            ))
```

Pozn.: u `Up for X` se hodnoty s baseline liší časem → ZMENA vypíše „bylo Up for Y". To je záměr, ne šum (uptime rostl); pokud by to vadilo, ve vlně 3 lze přidat `compared=False` — teď ne.

Adresa souseda (`:243-266`): `if address is None and not changed: outcome = unchanged_or(Outcome.BROKEN, ctx, area, same=was is not None and was_address is None)`; message `+ suffix(outcome)`; `baseline_value = MISSING if outcome is Outcome.UNCHANGED else (str(was_address) if was_address is not None else None)`.

**MPLS (`:342-363`)**: `was_state` normalizuj stejně jako value: `was_value = None if was_raw_state is None else ("Up" if str(was_raw_state) == "Up" else "Down")` — pozor, RECOVERED podmínka `was_state not in (None, "Up")` zůstává nad syrovým stavem. Chybí: `outcome = unchanged_or(Outcome.BROKEN, ctx, "mpls_interface", same=was is None)`, `baseline_value = MISSING if UNCHANGED else was_value`. Down: `outcome = unchanged_or(Outcome.BROKEN, ctx, "mpls_interface", same=was_value == "Down")`, message `+ suffix(outcome)`, `baseline_value=was_value`.

**BFD transit (`:424-466`)**: bez session: `outcome = unchanged_or(Outcome.BROKEN, ctx, "bfd", same=not baseline_entries)`, `baseline_value = "Down" if outcome is Outcome.UNCHANGED else baseline_value`. Per session down: `outcome = unchanged_or(Outcome.BROKEN, ctx, "bfd", same=was_state == state)` (syrové stavy obou stran, stejný slovník), message `+ suffix(outcome)`.

- [ ] **Step 4: Testy** — `pyats-venv/bin/python -m pytest tests/checks/test_core_protocols.py tests/test_end_to_end.py -q` → PASS. Existující testy, které tvrdí FAIL pro „down v obou" s baseline, uprav na PASS+značku (jsou-li).

- [ ] **Step 5: Commit** `feat(core_protocols): UNCHANGED pro IS-IS/LDP/PIM/MPLS/BFD shodne s baseline`.

---

## Task 3: `bgp.py` — session state a prefix counts

**Files:**
- Modify: `migration_validator/checks/bgp.py:139-158` (state ≠ Established), `:236-264` (bez session), `:204-231` (deaktivovaný — jen slovník), `:336-345` (RIB chybí)
- Test: `tests/checks/test_bgp.py`

- [ ] **Step 1: Failing testy** (helpery `_ctx(subject, baseline, ...)`, `_peer(...)`, `_by_label` existují)

```python
from migration_validator.models.result import UNCHANGED_SINCE_BASELINE


def test_active_state_in_both_is_unchanged_pass():
    peer = {"198.11.13.2": _peer(state="Active")}
    ctx = _ctx({"bgp": peer}, baseline={"bgp": peer})
    [row] = run_check(BgpSessionStateCheck(), ctx)
    assert row.status is Status.PASS
    assert row.details[UNCHANGED_SINCE_BASELINE] is True
    assert row.value == "Active" == row.baseline_value


def test_configured_peer_without_session_in_both_is_unchanged():
    ctx = _ctx({"bgp": {}}, baseline={"bgp": {}})
    [row] = run_check(BgpSessionStateCheck(), ctx)
    assert row.status is Status.PASS
    assert row.value == "bez session" == row.baseline_value


def test_peer_without_session_now_but_established_before_is_fail_with_bylo():
    ctx = _ctx({"bgp": {}}, baseline={"bgp": {"198.11.13.2": _peer(state="Established")}})
    [row] = run_check(BgpSessionStateCheck(), ctx)
    assert row.status is Status.FAIL and row.baseline_value == "Established"


def test_deactivated_peer_baseline_value_uses_same_vocabulary():
    ctx = _ctx({"bgp": {}}, baseline={"bgp": {}}, bgp_neighbors=[],
               bgp_neighbors_inactive=["198.11.13.2"],
               baseline_neighbors=[], baseline_neighbors_inactive=["198.11.13.2"])
    [row] = run_check(BgpSessionStateCheck(), ctx)
    assert row.status is Status.WARN            # deaktivace zustava WARN (vyjimka R-3)
    assert row.baseline_value == "deaktivovan"


def test_prefix_counts_missing_rib_carries_baseline_value():
    subject = {"198.11.13.2": _peer(rib="inet.0")}
    baseline_peer = _peer(rib="inet.0")
    baseline_peer["ribs"]["inet6.0"] = dict(_peer(rib="inet6.0")["ribs"]["inet6.0"])
    rows = run_check(BgpPrefixCountsCheck(), _ctx({"bgp": subject}, baseline={"bgp": {"198.11.13.2": baseline_peer}}))
    missing = _by_label(rows, "BGP prefixy (inet6.0)")
    assert missing.value == "chybi" and missing.baseline_value == "v tabulce"
```

- [ ] **Step 2: Ověř pád.**

- [ ] **Step 3: Implementace**

State ≠ Established (`:145`):

```python
            if state != ESTABLISHED:
                outcome = unchanged_or(Outcome.BROKEN, ctx, "bgp", same=baseline_state == state)
                findings.append(Finding(
                    outcome,
                    f"{peer}: stav {state}, ocekavano {ESTABLISHED}{suffix(outcome)}",
                    ...value=state, baseline_value=baseline_state, ...
                ))
                continue
```

Bez session (`:238-264`): `was_value = "bez session" if peer not in baseline_peers else str(baseline_peers[peer].get("state", "unknown"))` — ale jen když baseline existuje: `was_value = None if not ctx.has_baseline else (...)`. Pro `in_config`: `outcome = unchanged_or(Outcome.BROKEN, ctx, "bgp", same=peer not in baseline_peers)`; pro peer mimo službu (`NOT_IN_SERVICE`) stejný helper se `same=peer not in baseline_peers` a `was_value = NOT_IN_SERVICE` v UNCHANGED případě (sentinel obou stran). Message `+ suffix(outcome)`.

Deaktivovaný (`:224-229`): `baseline_value = "deaktivovan" if baseline_off else (str(baseline_peers[peer].get("state", "unknown")) if peer in baseline_peers else None)`.

RIB chybí (`:336-345`): přidej `baseline_value=IN_TABLE` s konstantou `IN_TABLE = "v tabulce"` na úrovni modulu (zrcadlí `routes.IN_TABLE`; neimportovat cross-modul, je to jen řetězec).

- [ ] **Step 4: Testy** `tests/checks/test_bgp.py tests/test_end_to_end.py` → PASS (uprav test `test_active_state_fails`, pokud běží s baseline se stejným stavem — zkontroluj, že testuje bez baseline; jinak drž FAIL bez baseline a přidej nový s baseline).

- [ ] **Step 5: Commit** `feat(bgp): UNCHANGED pro shodny stav session, sjednoceny slovnik baseline_value`.

---

## Task 4: `ifaces.py` — `interface_state` na BOTH, `interface_errors` nezměřeno

**Files:**
- Modify: `migration_validator/checks/ifaces.py:104-144` (InterfaceStateCheck), `:211-218` (nezmereno)
- Test: `tests/checks/test_ifaces.py`

- [ ] **Step 1: Failing testy** (`_ctx(subject, baseline, interfaces)` existuje)

```python
from migration_validator.models.result import NOT_COMPARED, UNCHANGED_SINCE_BASELINE


def _iface(admin="up", oper="up"):
    return {"interfaces": {"ge-0/0/2.113": {"admin_status": admin, "oper_status": oper,
                                            "input_pps": 1, "output_pps": 1}}}


def test_interface_state_down_in_both_is_unchanged_pass():
    rows = run_check(InterfaceStateCheck(), _ctx(_iface(oper="down"), baseline=_iface(oper="down")))
    oper = [r for r in rows if r.label.startswith("Interface operational")][0]
    assert oper.status is Status.PASS
    assert oper.details[UNCHANGED_SINCE_BASELINE] is True
    assert oper.value == "Down" == oper.baseline_value


def test_interface_state_down_now_up_before_is_fail_with_bylo():
    rows = run_check(InterfaceStateCheck(), _ctx(_iface(oper="down"), baseline=_iface()))
    oper = [r for r in rows if r.label.startswith("Interface operational")][0]
    assert oper.status is Status.FAIL and oper.baseline_value == "Up"


def test_interface_state_up_rows_carry_baseline_value_so_zmena_is_blank():
    rows = run_check(InterfaceStateCheck(), _ctx(_iface(), baseline=_iface()))
    assert all(r.baseline_value == r.value for r in rows)


def test_interface_state_without_baseline_record_has_no_baseline_value():
    rows = run_check(InterfaceStateCheck(), _ctx(_iface(oper="down"), baseline={"interfaces": {}}))
    assert all(r.baseline_value is None for r in rows)
    assert rows[1].status is Status.FAIL


def test_errors_unmeasured_row_is_not_compared():
    subject = {"interfaces": {"ge-0/0/2": {"input_pps": 1, "output_pps": 1}}}
    rows = run_check(InterfaceErrorsCheck(), _layer1_ctx(subject, baseline=subject))
    assert rows[0].value == "nezmereno" and rows[0].details[NOT_COMPARED] is False
```

- [ ] **Step 2: Ověř pád.**

- [ ] **Step 3: Implementace**

`InterfaceStateCheck`: `mode = Mode.BOTH`; v `run`:

```python
        baseline_ifaces = (ctx.baseline or {}).get("interfaces", {})
        ...
            was = baseline_ifaces.get(name)
            for label, key in (...):
                state = str(data.get(key, "unknown"))
                was_state = str(was.get(key, "unknown")) if was is not None else None
                ok = state == "up"
                outcome = Outcome.OK if ok else unchanged_or(
                    Outcome.BROKEN, ctx, "interfaces", same=was_state == state,
                )
                findings.append(Finding(
                    outcome,
                    f"{name}: {key} {state}{suffix(outcome)}",
                    label=...,
                    value=state.capitalize(),
                    baseline_value=was_state.capitalize() if was_state is not None else None,
                    subject={key: state},
                ))
```

Docstring/komentář třídy: „BOTH od 2026-09-08 (R-3): down v obou = PASS se znackou; bez baseline zaznamu (nesparovane rozhrani) chova se jako drive."

`interface_errors` nezměřeno (`:212-217`): přidej `compared=False`.

- [ ] **Step 4: Testy** `tests/checks/test_ifaces.py tests/test_engine.py tests/test_end_to_end.py tests/reporting -q` → PASS. Pozor: `test_reporting` a `test_end_to_end` mohou tvrdit, že `interface_state` je `mode: state` nebo že ZMENA u něj je prázdná — s baseline hodnotou rovnou value zůstává prázdná; oprav očekávání módu.

- [ ] **Step 5: Docs** `docs/cs/reference.md` katalog: `interface_state` mode `both`. **Step 6: Commit** `feat(ifaces): interface_state porovnava s baseline (UNCHANGED), errors nezmereno compared=False`.

---

## Task 5: `routes.py` — routa chybí v obou

**Files:**
- Modify: `migration_validator/checks/routes.py:106-116` (signatura `_presence_finding` dostane `ctx`), `:173-195`, volání v `:395-405` a `:601-611` (`AggregateRouteStatusCheck`)
- Test: `tests/checks/test_routes.py`

- [ ] **Step 1: Failing testy** (`_ctx(subject_routes, baseline_routes, scope)` existuje)

```python
from migration_validator.models.result import UNCHANGED_SINCE_BASELINE


def test_configured_route_missing_in_both_is_unchanged_pass():
    [row] = run_check(StaticRouteStatusCheck(), _ctx({}, baseline_routes={}))
    assert row.status is Status.PASS
    assert row.details[UNCHANGED_SINCE_BASELINE] is True
    assert row.value == "neni v tabulce" == row.baseline_value


def test_configured_route_missing_in_both_with_failed_baseline_collector_stays_fail():
    ctx = _ctx({}, baseline_routes={})
    ctx.baseline_collectors = {"routes": {"status": "error", "message": "RpcError"}}
    [row] = run_check(StaticRouteStatusCheck(), ctx)
    assert row.status is Status.FAIL and row.baseline_value is None


def test_route_present_in_baseline_missing_now_is_still_fail():
    [row] = run_check(StaticRouteStatusCheck(), _ctx({}, baseline_routes=_installed()))
    assert row.status is Status.FAIL and row.value == "chybi"
```

- [ ] **Step 2: Ověř pád.**

- [ ] **Step 3: Implementace**

`_presence_finding(..., ctx: CheckContext)` — nový keyword parametr, oba volající ho předají. Větev `subject is None` (`:173-195`):

```python
    if subject is None:
        if baseline is not None:
            value = MISSING_ENTIRELY
            message = f"{rib} {prefix}: v baseline byla, v subjektu neni"
            outcome = Outcome.BROKEN
        else:
            value = MISSING_FROM_TABLE
            outcome = unchanged_or(Outcome.BROKEN, ctx, "routes", same=True)
            message = (
                f"{rib} {prefix}: nakonfigurovana, ale neni v routovaci tabulce"
                f"{suffix(outcome)}"
            )
        return Finding(
            outcome, message, label=label, group=group, family=family, value=value,
            baseline_value=(MISSING_FROM_TABLE if outcome is Outcome.UNCHANGED else was),
            baseline=baseline,
        )
```

Pozn.: `same=True` je správně — `baseline is None` tady znamená „v baseline tabulce nebyla" a `unchanged_or` sám odmítne, když baseline chybí nebo collector selhal.

- [ ] **Step 4: Testy** `tests/checks/test_routes.py tests/test_end_to_end.py -q` → PASS (`test_configured_but_not_installed_is_broken` běží bez baseline → dál FAIL).

- [ ] **Step 5: Commit** `feat(routes): routa chybejici v baseline i subjektu je UNCHANGED`.

---

## Task 6: `reachability.py` — ARP, ND, ping na BOTH

**Files:**
- Modify: `migration_validator/checks/reachability.py:81-150` (ARP), `:152-229` (ND), `:231-330` + `_ping_findings:360-414` (ping)
- Test: `tests/checks/test_reachability.py`

**Interfaces:**
- Produces: `_ping_findings(probes, family, prefixes, baseline_probes, ctx)`; ARP/ND baseline záznam se hledá podle `ip`, ping podle `target`.

- [ ] **Step 1: Failing testy** (`_ctx(subject, service_type, scope)` existuje — přidej parametr `baseline=None`; s baseline předej `baseline_collectors={area: {"status": "ok"} for area in baseline}`)

```python
from migration_validator.models.result import UNCHANGED_SINCE_BASELINE


def test_arp_empty_in_both_is_unchanged_pass():
    [row] = run_check(ArpPresentCheck(), _ctx({"arp": []}, baseline={"arp": []}))
    assert row.status is Status.PASS and row.details[UNCHANGED_SINCE_BASELINE] is True
    assert row.value == "zadny zaznam" == row.baseline_value


def test_arp_empty_now_present_before_is_fail():
    before = {"arp": [{"ip": "198.11.13.2", "mac": "0c:00:00:00:00:01", "interface": "ge-0/0/2.113"}]}
    [row] = run_check(ArpPresentCheck(), _ctx({"arp": []}, baseline=before))
    assert row.status is Status.FAIL and row.baseline_value == "1 zaznam"


def test_arp_incomplete_in_both_is_unchanged():
    entry = {"ip": "198.11.13.2", "mac": "00:00:00:00:00:00", "interface": "ge-0/0/2.113"}
    [row] = run_check(ArpPresentCheck(), _ctx({"arp": [entry]}, baseline={"arp": [entry]}))
    assert row.status is Status.PASS and row.value == row.baseline_value


def test_arp_ok_row_baseline_value_is_same_entry_text():
    entry = {"ip": "198.11.13.2", "mac": "0c:00:00:00:00:01", "interface": "ge-0/0/2.113"}
    [row] = run_check(ArpPresentCheck(), _ctx({"arp": [entry]}, baseline={"arp": [entry]}))
    assert row.baseline_value == row.value


def test_nd_unreachable_in_both_is_unchanged():
    entry = {"ip": "2001:db8:11:13::2", "mac": "none", "interface": "ge-0/0/2.113", "state": "unreachable"}
    scope = Scope(id="svc:X:IPVPN", kind="service", key=ScopeKey("X", "IPVPN", None),
                  selectors=Selectors(interfaces=["ge-0/0/2.113"], local_ipv6=["2001:db8:11:13::1/64"]))
    [row] = run_check(NdPresentCheck(), _ctx({"nd": [entry]}, baseline={"nd": [entry]}, scope=scope))
    assert row.status is Status.PASS and row.value == "unreachable -> 2001:db8:11:13::2" == row.baseline_value


def test_ping_failed_in_both_is_unchanged_warn_becomes_pass():
    probe = {"scope_id": "svc:X:IPVPN", "target": "198.11.13.2", "family": 4, "sent": 5, "received": 0}
    [row] = run_check(PingReachabilityCheck(), _ctx({"ping": [probe]}, baseline={"ping": [probe]}))
    assert row.status is Status.PASS and row.details[UNCHANGED_SINCE_BASELINE] is True
    assert row.baseline_value == "0/5  198.11.13.2 neodpovedel"


def test_ping_ok_row_baseline_value_same_shape():
    probe = {"scope_id": "svc:X:IPVPN", "target": "198.11.13.2", "family": 4, "sent": 5, "received": 5, "rtt_avg_ms": 1.0}
    [row] = run_check(PingReachabilityCheck(), _ctx({"ping": [probe]}, baseline={"ping": [probe]}))
    assert row.baseline_value == row.value
```

Pozn.: ping baseline je „změřená", když `ctx.has_baseline` — probes nemají collector; `unchanged_or(..., area="ping", ...)` projde, protože `"ping"` nikdy není ve `failed_collectors`. To je záměr: baseline ping s `sent>0, received==0` je poctivé měření.

- [ ] **Step 2: Ověř pád.**

- [ ] **Step 3: Implementace**

Všechny tři třídy `mode = Mode.BOTH`. Sdílený helper v modulu:

```python
def _count_text(entries: list[dict[str, Any]]) -> str:
    n = len(entries)
    return "zadny zaznam" if n == 0 else f"{n} zaznam" if n == 1 else f"{n} zaznamy" if n < 5 else f"{n} zaznamu"
```

ARP: `baseline_entries = [e for e in (ctx.baseline or {}).get("arp", []) if e.get("ip")]` (None pokud `not ctx.has_baseline`), `by_ip = {e["ip"]: e for e in baseline_entries}`.
- prázdné: `outcome = unchanged_or(Outcome.BROKEN, ctx, "arp", same=baseline_entries == [])`; `baseline_value = _count_text(baseline_entries) if baseline_entries is not None else None`; message `+ suffix`.
- incomplete: `was = by_ip.get(ip)`; `outcome = unchanged_or(Outcome.BROKEN, ctx, "arp", same=was is not None and was.get("mac") == ZERO_MAC)`; `baseline_value = _arp_value(was) if was else None` kde `_arp_value(entry)` vrací `f"incomplete -> {ip}"` pro ZERO_MAC a `_entry_value(entry)` jinak — a použij ji i pro OK řádek (`value=_arp_value(entry)`, `baseline_value=_arp_value(was) if was else None`).

ND: totéž s `_nd_value(entry)` (`f"{state} -> {ip}"` pro unresolved, jinak `_entry_value`), klíč `ip`, `same=was is not None and (was.get("state") or "").lower() in UNRESOLVED_ND_STATES`.

Ping: `_ping_findings(batch, family, prefixes, baseline_batch, ctx)` kde `baseline_batch = [p for p in (ctx.baseline or {}).get("ping", []) if p.get("family") == family]`, `by_target = {str(p.get("target")): p for p in baseline_batch}`. Přidej `_ping_value(probe) -> str` (přesně dnešní skládání value pro OK/BROKEN/neodeslan) a použij pro obě strany. BROKEN: `outcome = unchanged_or(Outcome.BROKEN, ctx, "ping", same=was is not None and int(was.get("sent", 0)) > 0 and int(was.get("received", 0)) == 0)`; `baseline_value = _ping_value(was) if was else None` na všech třech větvích. SKIP větve (`mimo profil`, `bez cile`, oversized) beze změny.

- [ ] **Step 4: Testy** `tests/checks/test_reachability.py tests/test_end_to_end.py tests/reporting -q` → PASS (`view.py` už nevypíše nic navíc: ZMENA prázdná při shodě, „bylo …" při rozdílu).

- [ ] **Step 5: Docs** `docs/cs/reference.md` katalog: `arp_present`, `nd_present`, `ping_reachability` mode `both`; poznámka „prázdná tabulka v obou = PASS se značkou (R-3)". **Step 6: Commit** `feat(reachability): ARP/ND/ping porovnavaji s baseline (UNCHANGED)`.

---

## Task 7: `evpn.py` — VPWS, ESI, instance, MAC

**Files:**
- Modify: `migration_validator/checks/evpn.py:115-125`, `:163-196`, `:233-267`, `:346-393`, `:397-431`, `:574-608`, `:617-650`, `:689-716`, `:855-901`
- Test: `tests/checks/test_evpn.py`

- [ ] **Step 1: Failing testy** (`_vpws_ctx(subject, baseline)`, `_vpws_subject(...)`, `PEER_OK`, `_by_label` existují; `_ctx(subject, baseline, service_type, subtype)` pro E-LAN)

```python
from migration_validator.models.result import UNCHANGED_SINCE_BASELINE


def test_vpws_missing_remote_peer_in_both_is_unchanged_with_sentinel_baseline():
    subject = _vpws_subject(remote_peers=())
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=subject))
    pe = _by_label(rows, "EVPN VPWS SID remote PE")
    assert pe.status is Status.PASS and pe.details[UNCHANGED_SINCE_BASELINE] is True
    assert pe.baseline_value == "Neznamy peer"
    status = _by_label(rows, "EVPN VPWS SID remote status")
    assert status.status is Status.PASS and status.baseline_value == "Unresolved / Chybi"


def test_vpws_unresolved_peer_in_both_is_unchanged():
    peer = {**PEER_OK, "status": "Unresolved"}
    subject = _vpws_subject(remote_peers=(peer,))
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=subject))
    status = _by_label(rows, "EVPN VPWS SID remote status")
    assert status.status is Status.PASS and status.value == "Unresolved" == status.baseline_value


def test_vpws_local_interface_down_in_both_is_unchanged():
    subject = _vpws_subject(status="Down", remote_peers=(PEER_OK,))
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=subject))
    assert _by_label(rows, "EVPN VPWS local interface status").status is Status.PASS


def test_esi_unresolved_and_df_not_elected_in_both_are_unchanged():
    esi = {"00:11": {"interface": "ge-0/0/2.313", "status": "Up/Forwarding",
                     "resolved_status": "Unresolved", "df_role": "DF not elected yet"}}
    rows = run_check(EvpnEsiStatusCheck(), _ctx({"evpn_esi": esi}, baseline={"evpn_esi": esi}))
    assert _by_label(rows, "ESI Status").status is Status.PASS
    assert _by_label(rows, "ESI DF").status is Status.PASS


def test_instance_missing_unit_in_both_is_unchanged_with_baseline_value():
    data = {"neighbors": {"total": 1, "addresses": ["150.0.0.13"]},
            "local_interfaces": {"entries": []}, "irb_interfaces": {"entries": []}, "esis": {}}
    subject = {"evpn_instance": {"EVPN-AWARE-CPE13": data}}
    rows = run_check(EvpnInstanceStatusCheck(), _ctx(subject, baseline=subject))
    unit = [r for r in rows if r.value == "ge-0/0/2.313 chybi v instanci"][0]
    assert unit.status is Status.PASS and unit.baseline_value == unit.value


def test_mac_count_zero_in_both_is_unchanged():
    from migration_validator.checks.evpn import _mac_compare_finding
    finding = _mac_compare_finding("MAC count", 0, 0, -60.0)
    assert finding.outcome is Outcome.DEGRADED   # bez ctx helper nejde; viz implementace
```

Poslední test uprav podle implementace: `_mac_compare_finding` dostane parametr `ctx` a vrací `unchanged_or(Outcome.BROKEN, ctx, "evpn_mac", same=baseline == 0)`; test pak volá s `_ctx(...)` a čeká `Outcome.UNCHANGED`.

- [ ] **Step 2: Ověř pád.**

- [ ] **Step 3: Implementace** (`_interface_findings`, `_sid_findings`, `_esi_block`, `_instance_findings`, `_mac_compare_finding` dostanou `ctx`):

- VPWS local iface (`:115-125`): `outcome = Outcome.OK if up else unchanged_or(Outcome.BROKEN, ctx, "evpn_vpws", same=baseline_status == status)`; message `+ suffix`.
- VPWS bez peerů (`:171-196`): `same = baseline_iface is not None and not baseline_peers`; PE řádek `outcome = unchanged_or(Outcome.BROKEN, ctx, "evpn_vpws", same=same)`, `baseline_value = "Neznamy peer" if same else (ipaddr prvního baseline peeru | None)`; status řádek stejně s `"Unresolved / Chybi"`.
- VPWS peer (`:233-267`): `was_resolved = baseline_peer is not None and (baseline_peer.get("status") or "").strip().lower() == "resolved"`; `outcome = Outcome.OK if resolved else unchanged_or(Outcome.BROKEN, ctx, "evpn_vpws", same=baseline_peer is not None and not was_resolved)`; oba řádky (PE i status) dostanou tento outcome, message `+ suffix`.
- ESI Status (`:346-354`): `same = baseline.get("resolved_status") is not None and not baseline["resolved_status"].lower().startswith("resolved")`. ESI local iface (`:358-370`): `same = bool(baseline.get("status")) and not _is_up(str(baseline["status"]))`; `value`/`baseline_value` — **změna slovníku**: `value=status` (bez jména IFL, které se migrací mění; jméno je v labelu bloku/ESI hlavičce), `baseline_value=str(baseline["status"]) if baseline.get("status") else None`. ESI DF (`:379-393`): `same = "not elected" in str(baseline.get("df_role") or "").lower()`; `baseline_value = baseline.get("df_role") or ("-" if baseline else None)`.
- `_count_finding` (`:397-431`): přidej parametr `same_broken: bool`; `if not ok: outcome = unchanged_or(Outcome.BROKEN, ctx, "evpn_instance", same=same_broken)` — volající předá `same_broken = baseline_neighbors is not None and int(baseline_neighbors.get("total") or 0) == 0`; `baseline.get("neighbors") or None` na `:535` změň na `baseline.get("neighbors")` a `baseline_value = str(baseline_neighbors.get("total") or 0) if baseline_neighbors is not None else None` (prázdný dict = změřená nula, ne „bez baseline").
- EVPN interface down (`:574-591`): `same = baseline_entry is not None and not _is_up(str(baseline_entry["status"]))`; **slovník**: `value = entry["status"]`, `baseline_value = baseline_entry["status"]`, jméno rozhraní do labelu: `label(f"EVPN interface ({entry['name']})")`. Chybějící unit (`:599-608`): `same = unit not in baseline_local_by_name and bool(baseline)`; `baseline_value = f"{unit} chybi v instanci" if outcome is UNCHANGED else (f"{b['status']}" if b := baseline_local_by_name.get(unit) else None)`.
- IRB (`:617-650`): stejně — `value = status (+ " (context)")`, jméno do labelu, chybějící IRB jako u unitu.
- ESI řádky instance (`:706-716`): `same = baseline_status is not None and not baseline_status.lower().startswith("resolved")`.
- `_mac_compare_finding(label, baseline, subject, tolerance, ctx)` `subject == 0` větev: `outcome = unchanged_or(Outcome.BROKEN, ctx, "evpn_mac", same=baseline == 0)`.

- [ ] **Step 4: Testy** `tests/checks/test_evpn.py tests/test_end_to_end.py tests/reporting -q` → PASS; opravit testy, které zamykají `value == "ge-0/0/3.0 Up/Forwarding"` (nový tvar bez jména).

- [ ] **Step 5: Docs** `docs/cs/reference.md` řádky `evpn_*`: „shodný nevyřešený stav s baseline = PASS se značkou; hodnoty stavů bez jména IFL (jméno v labelu)". **Step 6: Commit** `feat(evpn): UNCHANGED pro shodny stav s baseline, hodnoty bez jmena IFL`.

---

## Task 8: `bfd.py` a `optics.py`

**Files:**
- Modify: `migration_validator/checks/bfd.py:96-186`, `migration_validator/checks/optics.py:104-180` (levels), `:184-237` (alarms → BOTH)
- Test: `tests/checks/test_bfd.py`, `tests/checks/test_optics.py`

- [ ] **Step 1: Failing testy**

```python
# test_bfd.py
def test_session_down_in_both_is_unchanged():
    down = {"198.11.13.2": {"state": "Down"}}
    [row] = run_check(BfdSessionStateCheck(), _ctx(down, baseline_sessions=down))
    assert row.status is Status.PASS and row.value == "Down" == row.baseline_value


def test_missing_session_in_both_with_established_bgp_is_unchanged():
    [row] = run_check(BfdSessionStateCheck(), _ctx({}, baseline_sessions={}))
    assert row.status is Status.PASS and row.baseline_value == "bez session"


def test_unconfigured_session_baseline_value_is_same_sentinel():
    session = {"10.0.0.9": {"state": "Up"}}
    [row] = run_check(BfdSessionStateCheck(), _ctx(session, baseline_sessions=session,
                                                   scope=_scope(bfd_peers=[])))
    assert row.status is Status.WARN                      # zustava (mimo zamer = nalez)
    assert row.baseline_value == "parser nenasel konfiguraci"


# test_optics.py
def test_levels_dark_in_both_is_unchanged():
    lane = _lane(rx=float("-inf"))
    ctx = _ctx({"optics": {"ae0": {"lanes": [lane]}}}, baseline={"optics": {"ae0": {"lanes": [lane]}}})
    [row] = run_check(OpticalLevelsCheck(), ctx)
    assert row.status is Status.PASS and row.details[UNCHANGED_SINCE_BASELINE] is True


def test_levels_missing_baseline_lane_row_is_not_compared():
    from migration_validator.models.result import NOT_COMPARED
    ctx = _ctx({"optics": {"ae0": {"lanes": [_lane()]}}}, baseline={"optics": {}})
    [row] = run_check(OpticalLevelsCheck(), ctx)
    assert row.details[NOT_COMPARED] is False


def test_alarm_raised_in_both_is_unchanged():
    lane = _lane(alarms={"rx-loss-of-signal": True})
    ctx = _ctx({"optics": {"ae0": {"lanes": [lane]}}}, baseline={"optics": {"ae0": {"lanes": [lane]}}})
    [row] = run_check(OpticalAlarmsCheck(), ctx)
    assert row.status is Status.PASS and row.value == "rx-loss-of-signal" == row.baseline_value
```

- [ ] **Step 2: Ověř pád.**

- [ ] **Step 3: Implementace**

`bfd.py` `_finding(..., ctx)`: přidej `_session_value(session, configured, is_device)` vracející přesně to, co dnes končí ve `value` každé větve (`PARSER_MISSED` / stav / `SESSION_GONE` / `NOT_IN_SERVICE` / `NO_SESSION`; pro `bgp_state != ESTABLISHED` větev `BGP_NOT_UP`). `was = _session_value(baseline, configured, is_device) if ctx.has_baseline else None` — pro baseline session použij tutéž funkci se stejnými `configured`/`is_device` (konfigurace subjektu), takže `baseline_value` mluví slovníkem řádku. UNCHANGED: down `same=baseline is not None and str(baseline.get("state")) == state`; bez session s BGP up `same=baseline is None`; `SESSION_GONE`/`NOT_IN_SERVICE` větve jsou z definice „v baseline byla" → `same=False` (beze změny).

`optics.py` `_level_finding(..., ctx)`: `baseline_lane is None` → `compared=False`; `dark`: `outcome = unchanged_or(Outcome.BROKEN, ctx, "optics", same=_dark_sides(baseline_lane) == dark)`, message `+ suffix`.

`OpticalAlarmsCheck`: `mode = Mode.BOTH`; `baseline_raised = _raised(baseline_optics.get(name))` (vytáhni dnešní smyčku do `_raised(data) -> list[tuple]`); bez alarmů: `baseline_value = "bez alarmu" if baseline_data is not None and not baseline_raised else (", ".join(tags) if baseline_data else None)`; per alarm: `same = (lane_no, tag) in {(l, t) for l, t, _ in baseline_raised}`; `outcome = unchanged_or(outcome, ctx, "optics", same=same)`; `baseline_value = tag if same else None`.

- [ ] **Step 4: Testy** `tests/checks/test_bfd.py tests/checks/test_optics.py tests/test_end_to_end.py -q` → PASS.

- [ ] **Step 5: Docs** katalog: `interface_optics_alarms` mode `both`. **Step 6: Commit** `feat(bfd,optics): UNCHANGED a sjednoceny slovnik baseline_value`.

---

## Task 9: `multicast.py` — IGMP report a PIM join bez párů v obou

**Files:**
- Modify: `migration_validator/checks/multicast.py:247-268` (IGMP BROKEN větev), `:306-325` (PIM BROKEN větev)
- Test: `tests/checks/test_multicast.py`

- [ ] **Step 1: Failing testy** (`_ctx`, `_scope`, `_igmp`, `_pim` existují)

```python
from migration_validator.models.result import UNCHANGED_SINCE_BASELINE


def test_igmp_no_report_in_both_is_unchanged_with_sentinel():
    subject = {**_igmp(POST), **_pim()}
    baseline = {**_igmp(PRE), **_pim()}
    ctx = _ctx(subject, baseline=baseline, scope=_scope(POST), baseline_scope=_scope(PRE))
    [row] = run_check(IgmpMembershipReportCheck(), ctx)
    assert row.status is Status.PASS and row.details[UNCHANGED_SINCE_BASELINE] is True
    assert row.value == NO_REPORT == row.baseline_value


def test_igmp_no_report_now_but_report_before_is_fail():
    subject = {**_igmp(POST), **_pim()}
    baseline = {**_igmp(PRE, SG), **_pim()}
    ctx = _ctx(subject, baseline=baseline, scope=_scope(POST), baseline_scope=_scope(PRE))
    [row] = run_check(IgmpMembershipReportCheck(), ctx)
    assert row.status is Status.FAIL and row.baseline_value == "(10.11.11.1, 232.1.1.1)"


def test_pim_join_none_in_both_is_unchanged():
    subject = {**_igmp(POST), **_pim()}
    ctx = _ctx(subject, baseline={**_igmp(PRE), **_pim()}, scope=_scope(POST), baseline_scope=_scope(PRE))
    [row] = run_check(PimJoinCheck(), ctx)
    assert row.status is Status.PASS and row.value == NO_JOIN == row.baseline_value
```

- [ ] **Step 2: Ověř pád.**

- [ ] **Step 3: Implementace** — IGMP poslední větev:

```python
            outcome = unchanged_or(Outcome.BROKEN, ctx, "igmp_group", same=was == [])
            return [Finding(
                outcome, f"receiver neposila zadny IGMP membership report{suffix(outcome)}",
                label=self.label, value=NO_REPORT,
                baseline_value=NO_REPORT if outcome is Outcome.UNCHANGED else was_value,
            )]
```

(`was == []` platí jen s baseline; bez baseline je `was` None.) PIM analogicky s `"pim_join"`, `same=ctx.has_baseline and not was`, sentinel `NO_JOIN`.

- [ ] **Step 4: Testy** `tests/checks/test_multicast.py -q` → PASS. **Step 5: Commit** `feat(multicast): IGMP/PIM join bez paru v obou snimcich je UNCHANGED`.

---

## Task 10: Průchod celé suite, docs, ověření na uložených bězích

- [ ] **Step 0: Sweep placeholderu `"unknown"` (nález review Tasku 7).** Collectory dosazují `"unknown"`, když XML element chybí; dvě nezměřené hodnoty nesmí dát UNCHANGED (stav se nefabuluje). Projdi `checks/bgp.py` (`state = str(peers[peer].get("state", "unknown"))`), `checks/ifaces.py` (`interface_state`, `str(data.get(key, "unknown"))`), `checks/core_protocols.py` (IS-IS `state`, MPLS `state`, BFD transit `state`), `checks/bfd.py` (`state`) a všude, kde `same=` porovnává stav, doplň `and <hodnota> != "unknown"` (sdílená konstanta `UNKNOWN = "unknown"` v `checks/baseline.py`, evpn.py ji importuje místo své lokální). Pro každý modul jeden test: stav `"unknown"` v obou snímcích zůstává FAIL. Commit `fix(checks): placeholder unknown nikdy nedava UNCHANGED`.
- [ ] **Step 1:** `pyats-venv/bin/python -m pytest -q` a `node --test tests/js/*.test.js` → zelené.
- [ ] **Step 2:** `docs/cs/reference.md` sekce „Poznámky, kde katalog překvapí": nový odstavec **R-3** (co je UNCHANGED, podmínka změřené baseline, výjimky). `docs/cs/files/checks.md`: odstavec o `checks/baseline.py`.
- [ ] **Step 3:** Přehraj `evaluate` nad `runs/mig01-mx1-pop1` (pre vs post) a `runs/mig01-ptx1-pop1`; do spec sekce „Ověření" zapiš počty `pass_unchanged` a zkontroluj, že žádný řádek nemá „bez baseline" kromě nesparovaných služeb. Pokud produkční pre/post snapshoty z 2026-09-08 jsou k dispozici, přehrát i ty.
- [ ] **Step 4:** Commit `docs: R-3 v referenci, overeni vlny 2 na ulozenych bezich`.
