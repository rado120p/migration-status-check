# Opravné kolo hlášek a verdiktů checků – implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Opravit 26 odsouhlasených nesrovnalostí v hláškách a verdiktech checků (viz spec) a zavést nový Outcome `RECOVERED` / Status `RECV` pro zlepšení proti baseline.

**Architecture:** Každý check žije v `migration_validator/checks/<modul>.py` a vrací `list[Finding]`; framework (`checks/base.py::run_check`) převádí `Finding.outcome` na `Status` přes `models/result.py::derive_status`. Změny jsou lokální v jednotlivých checcích, kromě Task 1, který sahá do modelu, engine souhrnu, textového reportu a GUI. Dokumentace se aktualizuje v Task 9.

**Tech Stack:** Python 3, pytest (`./pyats-venv/bin/python -m pytest`), vanilla JS v GUI.

**Spec:** `docs/superpowers/audit-2026-09-03-check-outcomes.md` (tabulky všech větví) + rozhodnutí uživatele 2026-09-03 zaznamenaná v sekci „Rozhodnutí“ níže.

## Global Constraints

- Hlášky jsou česky **bez diakritiky** (konvence celého projektu). Výjimky `chybí v outputu` a `nakonfigurován` se v tomto kole odstraňují.
- `value` je krátká hodnota do sloupce, `message` je celá věta (pravidlo AR-4). Nikdy nedávat větu do `value`, s výjimkou bodu 5 níže, kde to uživatel explicitně chce.
- Stav se nikdy nefabuluje: chybějící data = SKIP/WARN s důvodem, ne PASS.
- Každá změna hlášky má test, který kontroluje přesný text (`assert finding.message == "..."`), a test na Outcome.
- Před každým commitem celá sada zelená: `./pyats-venv/bin/python -m pytest -q`. Výchozí stav: vše zelené (1381+ testů).
- Práce na větvi `oprava-hlasek-2026-09`, ne na `main`.
- Commit message česky bez diakritiky, prefix `fix(<modul>):` / `feat(...)` / `docs(...)`, zakončení
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` a `Claude-Session: https://claude.ai/code/session_01UhUoEp3FpUuWHonTgkhsap`.

## Rozhodnutí (spec)

| # | check | změna |
|---|---|---|
| 1 | interface_traffic | bez baseline a 0 pps: message `<iface>: <key> 0 pps, ocekavan nenulovy provoz` |
| 2 | interface_traffic | baseline 0 pps a subject 0 pps: OK, message `<iface>: <key> stejne jako baseline (0 pps)` |
| 3 | bfd_session_state | session ne-Up: `<peer>: session <state>, ocekavano Up` |
| 4 | bfd_session_state | session bez konfigurace: `<peer>: BFD session existuje (<state>), ale parser ji nenasel v konfiguraci sluzby`, value `parser nenasel konfiguraci` |
| 5 | bgp_session_state, bfd_session_state | value `neni ve sluzbe` → `v baseline patril k teto sluzbe, v subjektu uz ne` |
| 6 | bgp_session_state | deaktivovaný peer: `baseline_value` = baseline stav peera, když je v `baseline["bgp"]` |
| 7 | evpn_vpws_status | peer BROKEN: `<instance>: <side> peer <ip> neni Resolved (<status or 'chybi'>)`; INFO řádky klíčů: `esi` v message psát `ESI` |
| 8 | evpn_esi_status | message `<esi>: <df_role>` (bez prefixu `DF ` když `df_role` už začíná na `DF`); `df_role == ""` chovat jako None → INFO `DF bez zaznamu` |
| 9 | interface_optics_alarms | `<name>: <tag> je aktivni` |
| 10 | multicast_forwarding_status | upstream prázdný: `... - S,G je v tabulce ale nema upstream interface`; upstream se špatným prefixem: `<sg>: upstream <up> neni z ocekavane role (ocekavano <prefixy>)` kde prefixy = `ge-/xe-/et-/ae` nebo `lsi./vt-` |
| 11 | mvpn_cmulticast_status | `<sg>: provider tunnel <tunnel> - sender PE se zmenil <was_pe> -> <pe>` |
| 12 | aggregate_route_status | větev „baseline aktivitu neuvadi“: `baseline_value=None` |
| 13 | ping_reachability | `ping neproveden - mimo profil (<profile>)`, value `mimo profil (<profile>)`; `sent == 0`: SKIP `<target>: ping neodeslan`, value `<target> neodeslan` |
| 14 | core_protocols | `MISSING = "chybi v outputu"`, `nakonfigurován` → `nakonfigurovan` |
| 15 | core_multicast_forwarding | zrušit 4 SKIP řádky `bez streamu`; přidat souhrnný řádek jako první: BROKEN `<n> z <m> inet.2 prefixu bez streamu` / OK `<m> inet.2 prefixu se streamem` |
| 16 | isis_interface_info | když level 2 chybí: jen řádek `IS-IS level 2` BROKEN, řádek passive se neemituje (transit i loopback) |
| 17 | ldp/pim_neighbor_state | adresa souseda None (a žádná změna proti baseline): BROKEN `<iface>: adresa souseda chybi`, value `chybi v outputu` |
| 18 | interface_errors | fyzický transit bez klíčů `input_errors`/`output_errors`/`framing_errors`: DEGRADED `<name>: chybove countery nebyly zmereny (rozhrani nevraci error countery)`, value `nezmereno` |
| 19 | isis_overview | `subject["isis_overview"]` prázdný: DEGRADED `chybi data z collectoru isis_overview`, value `bez dat` |
| 20 | bgp_prefix_counts | nárůst nad `abs(tolerance)` %: DEGRADED `<peer>/<rib>: narust <key> <b> -> <s>, prah je +<tol> %`; RIB v baseline a ne v subjektu: BROKEN `<peer>/<rib>: RIB v baseline byla, v subjektu chybi`, value `chybi`, label `BGP prefixy (<rib>)` |
| 21 | static/aggregate_route_status | `subject is None` a routa není v baseline měření: BROKEN `<rib> <prefix>: nakonfigurovana, ale neni v routovaci tabulce` value `neni v tabulce` i v device scope; `v baseline byla, v subjektu neni` jen když baseline záznam existuje |
| 22 | mpls_interface_state, bfd_transit_state | porovnání s baseline: Up ↔ Up = OK; Up nyní, baseline ne-Up = RECOVERED; ne-Up nyní = BROKEN (beze změny), `baseline_value` vždy vyplněno když baseline záznam je |
| 23 | arp_present | MAC `00:00:00:00:00:00`: BROKEN `ARP zaznam <ip> neni resolved (incomplete)`, value `incomplete -> <ip>`; nd_present: state `incomplete`/`unreachable`: BROKEN `ND zaznam <ip> neni resolved (<state>)`, value `<state> -> <ip>` |
| 24 | evpn_mac_count | interface v baseline a ne v subjektu: BROKEN `<L>: v baseline <b> MAC, v subjektu chybi`, value `chybi`, baseline_value `<b>` |
| 25 | evpn_instance_status | řádky EVPN interface, IRB interface, EVPN neighbor, ESI: vyplnit `baseline_value` z baseline instance, když tam odpovídající položka je |
| 26 | model | Outcome `RECOVERED` → Status `RECV`; použít v `isis_adjacency_state` (Up, baseline ne-Up), `deactivation_state` (aktivní, baseline deaktivovaná), `bgp_session_state` (Established, baseline jiný), `static_route_status` (aktivní, baseline neaktivní – nová větev), bod 22 |

Beze změny (rozhodnuto): isis_adjacency chybějící IPv6 = FAIL; mvpn tunnel id v baseline_value; tiché větve ARP/ND bez adresy, PIM bez intentu, inet.2 bez statik, BFD bez peerů.

---

### Task 0: Větev

- [ ] **Step 1:** `git checkout -b oprava-hlasek-2026-09`
- [ ] **Step 2:** `./pyats-venv/bin/python -m pytest -q` → vše zelené.

---

### Task 1: Outcome RECOVERED / Status RECV

**Files:**
- Modify: `migration_validator/models/result.py` (Status, _STATUS_RANK, count_statuses, Outcome, derive_status)
- Modify: `migration_validator/reporting/text_report.py:27-46` (SYMBOL, _ANSI), `:330` (COUNT_NAMES)
- Modify: `migration_validator/reporting/view.py:27` (_STATUS_ORDER)
- Modify: `migration_validator/gui/static/view.js:56`, `migration_validator/gui/static/style.css:277,403`
- Modify: `docs/en/reference.md` sekce 5 Result format (status list), `docs/cs/reference.md` totéž
- Test: `tests/models/test_result.py`, `tests/reporting/test_text_report.py`

**Interfaces:**
- Produces: `Outcome.RECOVERED = "recovered"`, `Status.RECV = "RECV"`, rank 0.5 mezi PASS(0) a SKIP(1) → použij celočíselně: INFO -1, PASS 0, RECV 1, SKIP 2, WARN 3, FAIL 4. `count_statuses` vrací klíč `"recv"`.

- [ ] **Step 1: Failing tests**

```python
# tests/models/test_result.py
def test_recovered_maps_to_recv_regardless_of_severity():
    assert derive_status(Outcome.RECOVERED, Severity.CRITICAL) is Status.RECV
    assert derive_status(Outcome.RECOVERED, Severity.ADVISORY) is Status.RECV

def test_recv_ranks_between_pass_and_skip():
    assert Status.worst([Status.PASS, Status.RECV]) is Status.RECV
    assert Status.worst([Status.RECV, Status.SKIP]) is Status.SKIP
    assert Status.worst([Status.RECV, Status.WARN]) is Status.WARN

def test_count_statuses_has_recv_counter():
    assert count_statuses([Status.RECV, Status.PASS]) == {
        "pass": 1, "warn": 0, "fail": 0, "skip": 0, "info": 0, "recv": 1,
    }
```

```python
# tests/reporting/test_text_report.py
def test_recv_token_rendered_and_counted():
    # vyrob RunResult s jednim CheckResult status=Status.RECV (pouzij existujici
    # helper v souboru, ktery stavi RunResult) a renderuj bez barvy
    text = render_text(result, color=False)
    assert "RECV" in text
    assert re.search(r"\b1 RECV\b", text)
```

Uprav existující parametrizovaný `test_derive_status` a `test_status_worst_ranks_skip_above_pass` jen pokud spadnou kvůli novým hodnotám rank.

- [ ] **Step 2:** Run `./pyats-venv/bin/python -m pytest tests/models/test_result.py tests/reporting/test_text_report.py -q` → FAIL (AttributeError RECOVERED/RECV).

- [ ] **Step 3: Implementace**

```python
# models/result.py
class Status(str, Enum):
    PASS = "PASS"; RECV = "RECV"; SKIP = "SKIP"; WARN = "WARN"; FAIL = "FAIL"; INFO = "INFO"

_STATUS_RANK = {Status.INFO: -1, Status.PASS: 0, Status.RECV: 1, Status.SKIP: 2, Status.WARN: 3, Status.FAIL: 4}

def count_statuses(statuses):
    counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0, "info": 0, "recv": 0}
    ...

class Outcome(str, Enum):
    OK = "ok"; INFO = "info"; RECOVERED = "recovered"; DEGRADED = "degraded"; BROKEN = "broken"; SKIP = "skip"

def derive_status(outcome, severity):
    ...
    if outcome is Outcome.RECOVERED:
        return Status.RECV
    ...
```

Docstring `derive_status`: doplnit „RECOVERED je vzdy RECV - zlepseni proti baseline neni varovani, ale ma byt videt.“

```python
# text_report.py
SYMBOL[Status.RECV] = "RECV"
_ANSI[Status.RECV] = "\x1b[36;1m"   # tucna cyan, lisi se od zelene PASS i od INFO
COUNT_NAMES = (("pass","PASS"), ("recv","RECV"), ("warn","WARN"), ("fail","FAIL"), ("skip","SKIP"), ("info","INFO"))
```

```python
# view.py
_STATUS_ORDER = (Status.FAIL, Status.WARN, Status.SKIP, Status.RECV, Status.PASS)
```
`_worst_message` už přeskakuje jen PASS; RECV řádek tedy dodá `worst_message` bloku, což je žádoucí (řekne co se spravilo).

```js
// gui/static/view.js:56  – pořadí pro worst message
for (const status of ["FAIL", "WARN", "SKIP", "RECV"]) {
```
```css
/* style.css */
.count-recv { color: #0e7490; }
.status-token.recv { color: #0e7490; font-weight: 600; }
```
Zkontroluj v `view.js`/šablonách, kde se generuje třída `status-token.<lower>` a `count-<key>`, že RECV/recv projde stejnou cestou jako INFO (grep `count-info`, `toLowerCase`). Pokud je seznam klíčů souhrnu natvrdo, přidej `recv` za `pass`.

Engine: `summary` používá `count_statuses`, nic dalšího. `cli.py:64` parsuje `Status(...)` z `--fail-on`/filtru – RECV projde automaticky.

Docs: v `docs/en/reference.md` sekce 5 (Result format) doplnit řádek k seznamu statusů: `RECV – measurement is healthy now and was not in the baseline (recovered); does not affect exit code`. Totéž česky v `docs/cs/reference.md`. V sekci 6 Exit codes doplnit větu „RECV nemení exit code“.

- [ ] **Step 4:** Celá sada: `./pyats-venv/bin/python -m pytest -q` → PASS. Zkontroluj i `tests/gui` a `tests/js` (pokud js testy běží přes node, spusť je způsobem, který používá `tests/js/README` nebo conftest).
- [ ] **Step 5:** Commit `feat(result): Outcome RECOVERED a Status RECV pro zlepseni proti baseline`.

---

### Task 2: ifaces.py + optics.py (body 1, 2, 9, 18)

**Files:**
- Modify: `migration_validator/checks/ifaces.py` (`_traffic_finding`, error-counter větev v `InterfaceErrorsCheck.run`)
- Modify: `migration_validator/checks/optics.py` (alarm/warning message)
- Test: `tests/checks/test_ifaces.py`, `tests/checks/test_optics.py`

- [ ] **Step 1: Failing tests** (použij existující helpery `_ctx`/`_scope` v těch souborech; podívej se, jak stávající testy staví `subject["interfaces"]`).

```python
def test_traffic_zero_without_baseline_says_why():
    f = [x for x in InterfaceTrafficCheck().run(ctx_no_baseline(pps=0)) if "input_pps" in x.message][0]
    assert f.outcome is Outcome.BROKEN
    assert f.message == "xe-0/0/1: input_pps 0 pps, ocekavan nenulovy provoz"

def test_traffic_zero_same_as_baseline_zero_is_ok_with_message():
    f = [x for x in InterfaceTrafficCheck().run(ctx(pps=0, baseline_pps=0)) if "input_pps" in x.message][0]
    assert f.outcome is Outcome.OK
    assert f.message == "xe-0/0/1: input_pps stejne jako baseline (0 pps)"
    assert f.value == "0 pps" and f.baseline_value == "0 pps"

def test_errors_missing_counters_is_degraded():
    # subject interface bez klicu input_errors/output_errors/framing_errors
    f = InterfaceErrorsCheck().run(ctx_l1(iface={"admin_status": "up", "oper_status": "up"}))[0]
    assert f.outcome is Outcome.DEGRADED
    assert f.message == "xe-0/0/1: chybove countery nebyly zmereny (rozhrani nevraci error countery)"
    assert f.value == "nezmereno"

def test_optics_alarm_message_says_aktivni():
    f = [x for x in InterfaceOpticsAlarmsCheck().run(ctx_with_alarm("rx_los")) if x.outcome is Outcome.BROKEN][0]
    assert f.message == "xe-0/0/1: rx_los je aktivni"
```

- [ ] **Step 2:** Run → FAIL na textech.
- [ ] **Step 3: Implementace**

`_traffic_finding` (ifaces.py): větev bez baseline:
```python
if baseline_value is None:
    broken = require_nonzero and value == 0
    message = f"{name}: {key} {value} pps" + (", ocekavan nenulovy provoz" if broken else "")
```
větev s baseline, `percent_change` vrátí None (baseline 0):
```python
if change is None:
    if value == 0:
        message = f"{name}: {key} stejne jako baseline (0 pps)"
    else:
        message = f"{name}: {key} v toleranci {tolerance:.0f} %"
    outcome = Outcome.OK
```
`InterfaceErrorsCheck`: před součtem
```python
counters = {k: data[k] for k in ("input_errors", "output_errors", "framing_errors") if k in data}
if not counters:
    findings.append(Finding(Outcome.DEGRADED,
        f"{name}: chybove countery nebyly zmereny (rozhrani nevraci error countery)",
        label=label, value="nezmereno"))
    continue
```
optics.py: `f"{name}: {tag} je zvednuty"` → `f"{name}: {tag} je aktivni"` (obě větve). Uprav existující testy, které kontrolují `je zvednuty`.

Aktualizuj docstringy v `ifaces.py` u `_traffic_finding` („require_nonzero se uplatni jen bez baseline; s baseline 0 -> 0 je zamerne OK, aby sluzba, ktera nefungovala uz pred migraci, nesvitila FAIL“).

- [ ] **Step 4:** `./pyats-venv/bin/python -m pytest -q` → PASS.
- [ ] **Step 5:** Commit `fix(ifaces,optics): duvod u 0 pps, stejne jako baseline, nezmerene countery, alarm je aktivni`.

---

### Task 3: bfd.py + bgp.py (body 3, 4, 5, 6, 20, 26-BGP)

**Files:**
- Modify: `migration_validator/checks/bfd.py`, `migration_validator/checks/bgp.py`
- Test: `tests/checks/test_bfd.py`, `tests/checks/test_bgp.py`

- [ ] **Step 1: Failing tests**

```python
# test_bfd.py
def test_session_down_message_states_expectation():
    f = BfdSessionStateCheck().run(_ctx({"198.11.13.2": {"state": "Down"}}))[0]
    assert f.message == "198.11.13.2: session Down, ocekavano Up"

def test_unconfigured_session_blames_parser():
    f = BfdSessionStateCheck().run(_ctx({"198.11.13.2": {"state": "Up"}}, scope=_scope(bfd_peers=[])))[0]
    assert f.outcome is Outcome.DEGRADED
    assert f.message == "198.11.13.2: BFD session existuje (Up), ale parser ji nenasel v konfiguraci sluzby"
    assert f.value == "parser nenasel konfiguraci"

def test_peer_only_in_baseline_value_is_full_sentence():
    f = BfdSessionStateCheck().run(_ctx({}, baseline_sessions={"198.11.13.2": {"state": "Up"}}, scope=_scope(bfd_peers=[], bgp_neighbors=())))[0]
    assert f.value == "v baseline patril k teto sluzbe, v subjektu uz ne"
```
```python
# test_bgp.py
def test_peer_only_in_baseline_value_is_full_sentence(): ...  # analogicky, value == message bez prefixu peera
def test_deactivated_peer_carries_baseline_state():
    # peer v selectors.bgp_neighbors_inactive, baseline["bgp"][peer]["state"] == "Established"
    assert f.baseline_value == "Established"
def test_established_after_idle_is_recovered():
    # baseline state Idle, subject Established
    assert f.outcome is Outcome.RECOVERED
    assert f.message == "10.0.0.2: stav se zmenil Idle -> Established"
def test_prefix_growth_over_tolerance_is_degraded():
    # baseline accepted 100, subject 120, tolerance -10
    assert f.outcome is Outcome.DEGRADED
    assert f.message == "10.0.0.2/inet.0: narust accepted 100 -> 120, prah je +10 %"
def test_rib_missing_in_subject_is_broken():
    # baseline peer ma ribs inet.0 a inet6.0, subject jen inet.0
    f = [x for x in findings if x.value == "chybi"][0]
    assert f.outcome is Outcome.BROKEN
    assert f.message == "10.0.0.2/inet6.0: RIB v baseline byla, v subjektu chybi"
    assert f.label == "BGP prefixy (inet6.0)"
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implementace**

bfd.py:
- `f"{peer}: session {state}"` → `f"{peer}: session {state}, ocekavano Up"` (jen BROKEN větev; OK zůstává `session Up`).
- větev `not configured and not is_device`: message `f"{peer}: BFD session existuje ({state}), ale parser ji nenasel v konfiguraci sluzby"`, value `"parser nenasel konfiguraci"`.
- konstanta hodnoty `neni ve sluzbe` → `NOT_IN_SERVICE = "v baseline patril k teto sluzbe, v subjektu uz ne"` (v bfd.py i bgp.py; pokud je sdílená, dej ji do `checks/base.py` a importuj).

bgp.py `BgpSessionStateCheck`:
- deaktivovaný peer: `baseline_value=str(baseline_peers[peer].get("state", "unknown")) if peer in baseline_peers else None`.
- větev `baseline_state is not None and baseline_state != state` (state Established): `Outcome.RECOVERED` místo OK, message beze změny.

bgp.py `BgpPrefixCountsCheck`, per key:
```python
if change is not None and change < tolerance:
    outcome, message = Outcome.BROKEN, f"{peer}/{rib_name}: pokles {key} {baseline} -> {subject}, prah je {tolerance:.0f} %"
elif change is not None and change > abs(tolerance):
    outcome, message = Outcome.DEGRADED, f"{peer}/{rib_name}: narust {key} {baseline} -> {subject}, prah je +{abs(tolerance):.0f} %"
else:
    outcome, message = Outcome.OK, f"{peer}/{rib_name}: {key} {subject}"
```
Za smyčkou přes subject RIBy přidej:
```python
for rib_name in sorted(set(baseline_ribs) - set(ribs)):
    findings.append(Finding(Outcome.BROKEN,
        f"{peer}/{rib_name}: RIB v baseline byla, v subjektu chybi",
        label=f"{self.label} ({rib_name})", family=family, value="chybi"))
```
Aktualizuj modulový docstring bgp.py (nárůst = WARN se stejnou tolerancí; chybějící RIB = FAIL).

- [ ] **Step 4:** celá sada → PASS. Pozor: `tests/test_end_to_end.py` a `tests/reporting` mohou kontrolovat text `neni ve sluzbe`; oprav očekávání.
- [ ] **Step 5:** Commit `fix(bgp,bfd): ocekavani u Down, parser-hlaska, plna veta misto 'neni ve sluzbe', narust prefixu WARN, chybejici RIB FAIL, RECOVERED u BGP`.

---

### Task 4: reachability.py (body 13, 23)

**Files:**
- Modify: `migration_validator/checks/reachability.py`
- Modify: `migration_validator/capture.py:160-165` (do `ping_skipped` přidat `"profile": profile_name`), a její volající v `cli.py` (předat `profile.name`), pokud `capture()` ještě jméno profilu nedostává – ověř signaturu `capture(...)`.
- Test: `tests/checks/test_reachability.py`, `tests/test_capture.py`

- [ ] **Step 1: Failing tests**

```python
def test_arp_zero_mac_is_broken():
    f = ArpPresentCheck().run(_ctx(arp=[{"ip": "10.0.0.2", "mac": "00:00:00:00:00:00", "interface": "xe-0/0/1.0"}]))[0]
    assert f.outcome is Outcome.BROKEN
    assert f.message == "ARP zaznam 10.0.0.2 neni resolved (incomplete)"
    assert f.value == "incomplete -> 10.0.0.2"

@pytest.mark.parametrize("state", ["incomplete", "unreachable"])
def test_nd_unresolved_states_are_broken(state):
    f = NdPresentCheck().run(_ctx(nd=[{"ip": "2001:db8::2", "mac": None, "state": state, "interface": "xe-0/0/1.0"}]))[0]
    assert f.outcome is Outcome.BROKEN
    assert f.message == f"ND zaznam 2001:db8::2 neni resolved ({state})"
    assert f.value == f"{state} -> 2001:db8::2"

def test_nd_stale_is_ok():
    ... state "stale" → Outcome.OK, value "aa:bb:cc:dd:ee:ff -> 2001:db8::2"

def test_ping_out_of_profile_names_profile():
    f = PingReachabilityCheck().run(_ctx(ping=[], ping_skipped=[{"scope_id": "svc:x", "reason": "mimo profil", "profile": "core-only"}]))[0]
    assert f.message == "ping neproveden - mimo profil (core-only)"
    assert f.value == "mimo profil (core-only)"

def test_ping_zero_sent_is_skip():
    f = PingReachabilityCheck().run(_ctx(ping=[{"target": "10.0.0.2", "family": 4, "sent": 0, "received": 0}]))[0]
    assert f.outcome is Outcome.SKIP
    assert f.message == "10.0.0.2: ping neodeslan"
    assert f.value == "10.0.0.2 neodeslan"
```
Test v `tests/test_capture.py`: `ping_skipped[0]["profile"] == "<jmeno profilu>"` (rozšiř existující test, který kontroluje `reason == "mimo profil"`).

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implementace**

reachability.py, ARP smyčka per entry:
```python
ZERO_MAC = "00:00:00:00:00:00"
if entry.get("mac") == ZERO_MAC:
    findings.append(Finding(Outcome.BROKEN, f"ARP zaznam {ip} neni resolved (incomplete)",
        family=4, value=f"incomplete -> {ip}", subject={...}, details={"address": ...}))
    continue
```
ND smyčka: `UNRESOLVED_ND_STATES = {"incomplete", "unreachable"}`; `state = (entry.get("state") or "").lower()`; když ve množině → BROKEN s texty výše. Pozor: „žádný záznam“ větev (`zadny zaznam`) se nadále počítá z filtrovaných záznamů včetně nerozřešených – to je správně (záznam existuje).

Ping: ve větvi `ping_skipped` vezmi `profile = next((s.get("profile") for s in skipped if s.get("profile")), None)`; message `f"ping neproveden - mimo profil ({profile})"` když profile, jinak stávající text. Před `received > 0` větví:
```python
if sent == 0:
    Finding(Outcome.SKIP, f"{target}: ping neodeslan", family=family, value=f"{target} neodeslan", subject={...})
```
capture.py: `{"scope_id": s.id, "reason": "mimo profil", "profile": profile_name}` – `profile_name` doplň do signatury `capture(...)` jako kwarg `profile_name: str | None = None` a předej z `cli.py` (řádek ~87 už `profile_name=profile.name or None` používá pro něco – zkontroluj, zda to už do `capture` neputuje).

- [ ] **Step 4:** celá sada → PASS.
- [ ] **Step 5:** Commit `fix(reachability): incomplete ARP/ND je FAIL, ping mimo profil uvadi profil, sent 0 je SKIP`.

---

### Task 5: evpn.py (body 7, 8, 24, 25)

**Files:**
- Modify: `migration_validator/checks/evpn.py`
- Test: `tests/checks/test_evpn.py`

- [ ] **Step 1: Failing tests**

```python
def test_vpws_unresolved_peer_message_carries_reason():
    f = [x for x in run_vpws(peer_status="Unresolved") if x.label == "EVPN VPWS SID remote PE"][0]
    assert f.outcome is Outcome.BROKEN
    assert f.message == "ELINE-1: remote peer 10.1.1.2 neni Resolved (Unresolved)"

def test_vpws_missing_peer_status_message_says_chybi():
    ... peer bez klice status → message endswith "neni Resolved (chybi)"

def test_vpws_esi_info_row_uses_uppercase():
    f = [x for x in findings if x.label.endswith("ESI")][0]
    assert f.message == "ELINE-1: remote peer ESI 00:11:22:33:44:55:66:77:88:99"

def test_esi_df_not_elected_no_double_df():
    f = [x for x in run_esi(df_role="DF not elected yet") if x.label == "ESI DF"][0]
    assert f.outcome is Outcome.BROKEN
    assert f.message == "00:11:...: DF not elected yet"

def test_esi_df_role_empty_string_is_info_bez_zaznamu():
    f = [x for x in run_esi(df_role="") if x.label == "ESI DF"][0]
    assert f.outcome is Outcome.INFO and f.value == "-"

def test_mac_count_interface_only_in_baseline_is_broken():
    # baseline instance ma interface ae5.100 s 12 MAC, subject ho nema
    f = [x for x in findings if "ae5.100" in x.label][0]
    assert f.outcome is Outcome.BROKEN
    assert f.message == "Interface ae5.100 MAC count: v baseline 12 MAC, v subjektu chybi"
    assert f.value == "chybi" and f.baseline_value == "12"

def test_instance_status_rows_carry_baseline_value():
    # baseline instance: neighbors.addresses ["10.0.0.1"], local_interfaces.entries [{"name":"ae5.100","status":"Up"}],
    # irb_interfaces.entries [{"name":"irb.100","status":"Up"}], esis {"00:..": "Resolved"}
    by_label = {x.label: x for x in findings}
    assert by_label["EVPN interface"].baseline_value == "ae5.100 Up"
    assert by_label["IRB interface"].baseline_value == "irb.100 Up"
    assert by_label["EVPN neighbors"].baseline_value == "1"   # uz funguje
    neighbor_row = [x for x in findings if x.message.endswith("neighbor 10.0.0.1")][0]
    assert neighbor_row.baseline_value == "10.0.0.1"
```
Přizpůsob názvy helperů skutečným helperům v `tests/checks/test_evpn.py`.

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implementace**

`_sid_findings` per peer, řádek PE:
```python
resolved = str(peer.get("status") or "").strip().lower() == "resolved"
message = f"{instance}: {side} peer {ipaddr}" + ("" if resolved else f" neni Resolved ({peer.get('status') or 'chybi'})")
```
INFO řádky klíčů: `shown = {"mode": "mode", "esi": "ESI", "role": "role"}[key]` v message.

`evpn_esi_status` DF:
```python
df_role = data.get("df_role") or None          # "" -> None
if df_role is None: INFO "DF bez zaznamu", value "-"
else:
    text = df_role if df_role.upper().startswith("DF") else f"DF {df_role}"
    message = f"{esi}: {text}"
```

`evpn_mac_count` po smyčce přes subject interfaces:
```python
for key in sorted(set(baseline_ifaces) - set(subject_ifaces)):
    if units.active and key not in units.interfaces: continue
    b = int(baseline_ifaces[key].get("mac_count", 0))
    findings.append(Finding(Outcome.BROKEN, f"{row_label}: v baseline {b} MAC, v subjektu chybi",
        label=..., value="chybi", baseline_value=str(b), baseline={"mac_count": b}))
```
(`row_label` stejným způsobem jako pro existující interface řádky.)

`evpn_instance_status`: v `_interface_findings`/IRB/neighbor/ESI větvích dohledej v `baseline_instance` položku se stejným `name`/adresou/ESI a nastav `baseline_value` na stejný formát jako `value` (`f"{name} {status}"`, adresa, status ESI). Odstraň komentář, který tvrdí, že baseline_value tam není potřeba, pokud existuje.

- [ ] **Step 4:** celá sada → PASS.
- [ ] **Step 5:** Commit `fix(evpn): duvod u neresolved peera, ESI velkymi, DF bez zdvojeni, chybejici interface v MAC count, baseline_value u instance radku`.

---

### Task 6: routes.py + deactivation.py (body 12, 21, 26-routy/deaktivace)

**Files:**
- Modify: `migration_validator/checks/routes.py` (`_presence_finding`, `_presence_text` použití), `migration_validator/checks/deactivation.py`
- Test: `tests/checks/test_routes.py`, `tests/checks/test_deactivation.py`

- [ ] **Step 1: Failing tests**

```python
def test_device_scope_configured_route_missing_is_not_blamed_on_baseline():
    # device scope, routa v configured (test si ji vlozi do selectors), subject bez ni, baseline bez ni
    assert f.message == "inet.0 10.0.0.0/24: nakonfigurovana, ale neni v routovaci tabulce"
    assert f.value == "neni v tabulce"

def test_route_missing_present_in_baseline_says_so():
    # baseline mereni ji ma, subject ne
    assert f.message == "inet.0 10.0.0.0/24: v baseline byla, v subjektu neni" and f.value == "chybi"

def test_aggregate_baseline_without_activity_has_no_baseline_value():
    # subject active False, baseline zaznam bez klice active
    assert f.message.endswith("baseline aktivitu neuvadi")
    assert f.baseline_value is None

def test_static_route_active_after_inactive_baseline_is_recovered():
    # subject active True, baseline active False
    assert f.outcome is Outcome.RECOVERED
    assert f.message == "inet.0 10.0.0.0/24: 10.0.0.1 (v baseline nebyla aktivni)"
    assert f.baseline_value == "neni aktivni"

def test_reactivated_service_is_recovered():   # test_deactivation.py
    assert f.outcome is Outcome.RECOVERED
    assert f.message == "sluzba byla v baseline deaktivovana (interface), ted je aktivni"
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implementace**

`_presence_finding`, větev `subject is None`:
```python
if baseline is not None:
    BROKEN "v baseline byla, v subjektu neni" / "chybi"
else:
    BROKEN "nakonfigurovana, ale neni v routovaci tabulce" / "neni v tabulce"
```
(podmínka `configured and not is_device` odpadá; uprav komentář nad větví.)

Větev „baseline aktivitu neuvadi“ (subject inactive, baseline bez `active`): `baseline_value=None` v obou checcích.

Nová větev v `static_route_status` pro `subject["active"] is True and baseline is not None and baseline.get("active") is False`: `Outcome.RECOVERED`, message `f"{rib} {prefix}: {now} (v baseline nebyla aktivni)"`, value `now`, baseline_value `"neni aktivni"`. Umísti před běžnou OK větev; anotace neaktivních hopů se aplikuje stejně jako u OK. V `aggregate_route_status` analogicky: RECOVERED `f"{rib} {prefix}: v tabulce (v baseline nebyla aktivni)"`.

`deactivation.py`: větev „aktivní teď, deaktivovaná v baseline“ → `Outcome.RECOVERED` (message beze změny). `deactivation_outcome` se sdílí s routes/BGP pro deaktivaci → tam se `(False, True)` nevolá; funkci neměň, jen v `DeactivationStateCheck.run` nahraď výsledek pro tento případ. Uprav docstring `deactivation_outcome` řádek 4 („zlepseni je RECOVERED, ne varovani“).

- [ ] **Step 4:** celá sada → PASS.
- [ ] **Step 5:** Commit `fix(routes,deactivation): pravdiva hlaska o chybejici route, baseline_value bez fabulace, RECOVERED pro reaktivaci`.

---

### Task 7: core_protocols.py (body 14, 16, 17, 19, 22, 26-ISIS)

**Files:**
- Modify: `migration_validator/checks/core_protocols.py`
- Test: `tests/checks/test_core_protocols.py`

- [ ] **Step 1: Failing tests**

```python
def test_missing_marker_has_no_diacritics():
    from migration_validator.checks.core_protocols import MISSING
    assert MISSING == "chybi v outputu"
    # a value radku level 2 OK == "nakonfigurovan"

def test_isis_interface_missing_level2_emits_single_fail(loopback_or_transit):
    labels = [f.label for f in findings if f.outcome is Outcome.BROKEN]
    assert labels == ["IS-IS level 2 (lo0.0)"]
    assert not any(f.label.startswith("IS-IS level 2 passive") for f in findings)

def test_ldp_neighbor_address_missing_is_broken():
    f = [x for x in findings if x.label == "LDP neighbor address (xe-0/0/1.0)"][0]
    assert f.outcome is Outcome.BROKEN
    assert f.message == "xe-0/0/1.0: adresa souseda chybi" and f.value == "chybi v outputu"
# totez pro PIM

def test_isis_overview_without_data_is_degraded():
    f = IsisOverviewCheck().run(ctx(subject={"isis_overview": {}}))[0]
    assert f.outcome is Outcome.DEGRADED
    assert f.message == "chybi data z collectoru isis_overview" and f.value == "bez dat"

def test_mpls_up_after_down_baseline_is_recovered():
    assert f.outcome is Outcome.RECOVERED and f.baseline_value == "Down" and f.value == "Up"
def test_bfd_transit_up_after_down_baseline_is_recovered(): ...  # baseline["bfd"][peer]["state"] == "Down"
def test_bfd_transit_carries_baseline_value(): ...
def test_isis_adjacency_up_after_down_is_recovered():
    assert f.outcome is Outcome.RECOVERED   # driv DEGRADED
```
Uprav existující testy, které očekávaly `chybí v outputu`, `nakonfigurován`, DEGRADED u adjacency Up-po-Down, druhý FAIL passive.

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implementace**

- `MISSING = "chybi v outputu"`; `"nakonfigurován"` → `"nakonfigurovan"`.
- `isis_interface_info`: po řádku level 2, `if "2" not in levels: continue` (přeskočí passive řádek); level 1 řádek nech před tím `continue`.
- `_neighbor_findings` adresní řádek: když `address is None` a nejde o změnu proti baseline → `Outcome.BROKEN`, message `f"{iface}: adresa souseda chybi"`, value `MISSING`. Změna proti baseline zůstává DEGRADED.
- `isis_overview`: `if not overview: return [Finding(Outcome.DEGRADED, "chybi data z collectoru isis_overview", value="bez dat")]`.
- `mpls_interface_state`: `was_state`; když `state == "Up"` a `was_state not in (None, "Up")` → RECOVERED, message `f"{iface}: MPLS Up (v baseline {was_state})"`.
- `bfd_transit_state`: načti `baseline["bfd"]`, seskup podle interface stejně jako subject; per session `was_state = baseline_sessions.get(peer, {}).get("state")`; `baseline_value=was_state`; Up po ne-Up → RECOVERED `f"{iface}: BFD session s {peer} Up (v baseline {was_state})"`. Řádek „zadna BFD session“ dostane `baseline_value="Up"` pokud baseline session pro interface existovala.
- `isis_adjacency_state` řádek stav: `Outcome.DEGRADED` (Up, was ne-Up) → `Outcome.RECOVERED`, message `f"{iface}: adjacency Up (v baseline {was_state})"`. Řádek soused při shodě: `baseline_value=str(was_system)`.
- Docstringy: uprav ty, které tvrdí „zlepšení je pořád změna/WARN“ a „baseline se nepoužívá“.

- [ ] **Step 4:** celá sada → PASS.
- [ ] **Step 5:** Commit `fix(core): bez diakritiky, jeden FAIL za chybejici level 2, chybejici adresa souseda FAIL, overview bez dat WARN, MPLS/BFD/ISIS porovnani s RECOVERED`.

---

### Task 8: multicast.py (body 10, 11, 15)

**Files:**
- Modify: `migration_validator/checks/multicast.py`
- Test: `tests/checks/test_multicast.py`

- [ ] **Step 1: Failing tests**

```python
def test_upstream_wrong_role_message(internet_scope):
    # Internet/multicast, route upstream "lsi.1048576"
    f = [x for x in findings if x.label == "Upstream interface"][0]
    assert f.outcome is Outcome.BROKEN
    assert f.message == "(10.1.1.1, 232.1.1.1): upstream lsi.1048576 neni z ocekavane role (ocekavano ge-/xe-/et-/ae)"
def test_upstream_missing_message_unchanged():
    assert f.message == "(10.1.1.1, 232.1.1.1): upstream - - S,G je v tabulce ale nema upstream interface"
def test_mvpn_sender_pe_change_names_both():
    assert f.message == "(10.1.1.1, 232.1.1.1): provider tunnel <tunnel> - sender PE se zmenil 10.255.0.1 -> 10.255.0.2"
def test_core_no_stream_has_no_filler_skip_rows_and_summary():
    assert not any(f.outcome is Outcome.SKIP and f.value == "" for f in findings)
    assert findings[0].label == "Multicast forwarding status"
    assert findings[0].message == "1 z 2 inet.2 prefixu bez streamu" and findings[0].outcome is Outcome.BROKEN
def test_core_all_streams_summary_ok():
    assert findings[0].message == "2 inet.2 prefixu se streamem" and findings[0].value == "2 inet.2 prefixu"
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implementace**

```python
def _upstream_problem(subtype, upstream) -> str | None:
    if not upstream: return " - S,G je v tabulce ale nema upstream interface"
    prefixes = MVPN_UPSTREAM_PREFIXES if subtype == "mvpn-igmp" else INTERNET_UPSTREAM_PREFIXES
    if upstream.startswith(prefixes): return None
    return f" neni z ocekavane role (ocekavano {'/'.join(prefixes)})"
```
a v řádku upstream: `problem = _upstream_problem(...)`; `Outcome.OK if problem is None else Outcome.BROKEN`; message `f"{sg}: upstream {upstream or '-'}{problem or ''}"`. `_upstream_ok` může zůstat jako `_upstream_problem(...) is None`.

MVPN: `f"{sg}: provider tunnel {tunnel} - sender PE se zmenil {was_pe} -> {pe}"`.

core_multicast_forwarding: smaž `CORE_SKIP_LABELS` a `findings.extend(... bez streamu ...)`. Spočítej `missing = [p for p in prefixes if not assigned[p]]`; na začátek `findings.insert(0, Finding(BROKEN if missing else OK, f"{len(missing)} z {len(prefixes)} inet.2 prefixu bez streamu" if missing else f"{len(prefixes)} inet.2 prefixu se streamem", label=self.label, value=f"{len(missing)}/{len(prefixes)} bez streamu" if missing else f"{len(prefixes)} inet.2 prefixu"))`. Zkontroluj `tests/reporting/test_view.py` a `test_text_report.py`, zda něco nespoléhá na 4 SKIP řádky (grep `bez streamu`).

- [ ] **Step 4:** celá sada → PASS.
- [ ] **Step 5:** Commit `fix(multicast): upstream spatne role vs. chybejici, novy sender PE v hlasce, souhrn inet.2 prefixu misto prazdnych SKIPu`.

---

### Task 9: Dokumentace

**Files:**
- Modify: `docs/en/reference.md` sekce 1 (katalog) a 5 (Result format), `docs/cs/reference.md` totéž
- Modify: `docs/en/files/checks.md`, `docs/cs/files/checks.md`
- Modify: `docs/superpowers/audit-2026-09-03-check-outcomes.md` – hlavička „stav po opravném kole“, tabulky upravit podle nových hlášek

- [ ] **Step 1:** Katalog: přidat řádky `aggregate_route_status` (both, critical, all: „configured aggregate route is in the table and active“), `interface_optics_levels` (both, critical, layer1 only: „RX/TX per lane, no dark side, shift vs baseline within tolerance_db“), `interface_optics_alarms` (state, critical, layer1 only: „no raised alarm (FAIL) or warning (WARN) on any lane“). Datum v úvodní větě katalogu → 2026-09-03.
- [ ] **Step 2:** `checks.md` (en i cs): přidat sekci `evpn_instance_status`; u každého checku bez tabulky doplnit tabulku `situation | Outcome | status | value` převzatou z auditu **po opravách** (zkopíruj z aktualizovaného audit dokumentu, ověř proti kódu grepem na message).
- [ ] **Step 3:** Result format: popsat `RECV`. Doplnit odstavec „Improvements“: kde vzniká RECOVERED (IS-IS adjacency, deaktivace, BGP stav, statické/agregátní routy, MPLS, BFD transit).
- [ ] **Step 4:** Audit dokument: v tabulkách přepsat změněné hlášky; sekci „Kandidáti pro opravné kolo“ přejmenovat na „Provedené opravy 2026-09“ a u každé odrážky doplnit „hotovo“ nebo „beze změny (rozhodnutí)“.
- [ ] **Step 5:** Ověření: `grep -rn "neni ve sluzbe\|je zvednuty\|chybí v outputu\|nakonfigurován" migration_validator docs/en docs/cs` → žádný výskyt v kódu; v docs jen v historických plánech/roadmapách.
- [ ] **Step 6:** Commit `docs: katalog uplny (30 checku), tabulky vysledku u vsech checku, status RECV`.

---

### Task 10: Závěr

- [ ] `./pyats-venv/bin/python -m pytest -q` zelené; počet testů zapsat do závěrečné zprávy.
- [ ] Spustit `mig-validate evaluate` nad `runs-example` (viz README, sekce run management) a přiložit blok reportu, kde je vidět RECV a nové hlášky.
- [ ] Předat uživateli k merge (superpowers:finishing-a-development-branch).
