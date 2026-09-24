# Odstranění device režimu — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `device` pseudo-scope (the "everything on the box in one block" fallback) and replace it with an explicit "no migrated services" result, with NEZARAZENO still running.

**Architecture:** The engine stops substituting `device_scope()` for an empty scope list. `RunResult` gains an additive `no_services` flag, computed from *service* scopes. The text report and GUI render that flag as a neutral state. Every `is_device` branch (checks, probes, linker, NEZARAZENO) and the `requires_inventory` machinery are then deleted. The work is ordered so the suite stays green after each task: engine first, then checks, then the model, then presentation, then docs.

**Tech Stack:** Python 3 (pytest, run via `pyats-venv/bin/python -m pytest`), vanilla JS GUI (tests via `node --test tests/js/*.test.js`).

**Spec:** `docs/superpowers/specs/2026-09-24-odstraneni-device-rezimu-design.md`

## Global Constraints

- Snapshot schema stays **13**, inventory schema stays **10**, `RunResult.schema_version` stays **1**.
- `no_services` is an **additive** key: `to_dict()` writes it only when true. JSON from a normal run must not change.
- `no_services` = the subject has **no scope with `kind == "service"`** (Layer1 scopes don't count). It is computed from `subject.scopes` **before** the profile or step filters.
- Snapshot without services → CLI exit **`EXIT_OK`**, including with `--warn-as-error`.
- Report line, verbatim: `Subjekt nema v inventory zadne migrovane sluzby - checky nebezely.`
- GUI strings, verbatim: pairing-header notice `No migrated services`; panel text `No migrated services in the inventory — checks did not run`.
- `mig-validate capture` without `--inventory` must keep working (data-only capture).
- Don't reuse the name `device` for anything new.
- Leave the AR-10 comment in `engine._identity` alone. It is about running without a baseline, not without an inventory.
- Code comments follow repo style: Czech, no diacritics. Docs under `docs/` keep diacritics.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Work on the current branch `oprava-scope-id-2026-09-24`. Don't merge and don't push.

## Review Focus

1. **Profile filter hides every service.** The subject *has* services, but `service_types` excluded them all, so `no_services` must stay False and the report must not claim the inventory is empty. Pinned in Task 1.
2. **Per-port snapshot of a port with no services.** NEZARAZENO must stay narrowed to that port and must not list the whole box. Pinned in Task 1.
3. **Legacy baseline captured without an inventory, paired with a subject that has services.** The subject's services show as unpaired, with no exception and no `no_services`. Pinned in Task 1.
4. **A filtered text report (`--status fail`) of a no-services run** must still print the no-services line. `filter_result` uses `dataclasses.replace`, so the flag survives; the test pins it. Pinned in Task 4.
5. **A GUI pairing whose evaluation has no services** must not be flagged "needs attention", and its model must say `noServices`. Pinned in Task 5.

---

### Task 1: Engine — no device fallback, `no_services`, NEZARAZENO without scopes

**Files:**
- Modify: `migration_validator/models/result.py:242-299` (`RunResult`)
- Modify: `migration_validator/engine.py:26` (import), `:53-54` (`_scopes_of`), `:459-598` (three `_unassigned_*`), `:620-625` (`_in_profile` comment), `:750-765` (`RunResult(...)` construction)
- Test: `tests/test_engine.py`

**Interfaces:**
- Produces: `RunResult.no_services: bool` (default `False`). `to_dict()` emits `"no_services": True` only when true. Tasks 4 and 5 read this field.
- Produces: `engine._scopes_of(snapshot) -> list[Scope]`, which returns `snapshot.scopes` unchanged (possibly `[]`).

- [ ] **Step 1: Replace the two device-mode engine tests with the new behaviour tests**

In `tests/test_engine.py`, delete `test_device_scope_reports_nothing_as_unassigned` (around line 879) and `test_snapshot_without_scopes_falls_back_to_device_scope` (around line 906). Add these tests in their place:

```python
def test_snapshot_without_scopes_runs_no_checks_and_says_so():
    # Spec 2026-09-24: snimek bez sluzeb uz nepada do device scope - zadne
    # checky, explicitni priznak ve vysledku.
    subject = _new()
    subject.scopes = []
    subject.inventory = None

    result = api.evaluate(subject, now=NOW)

    assert result.scopes == []
    assert result.no_services is True
    assert result.to_dict()["no_services"] is True
    assert result.summary["fail"] == 0
    assert result.summary["warn"] == 0


def test_snapshot_without_scopes_still_lists_unassigned():
    # NEZARAZENO bezi i bez scopu - celoboxovy datovy capture tak aspon
    # ukaze, co na boxu je.
    subject = _new()
    subject.scopes = []
    subject.facts["routes"] = MGMT_ROUTE
    subject.facts["bfd"] = {"10.1.1.1": {"state": "Up", "interface": "et-0/0/9.0"}}
    subject.facts["bgp"] = {
        "10.1.1.1": {"state": "Established", "routing_instance": None, "ribs": {}}
    }

    result = api.evaluate(subject, now=NOW)

    assert [r["prefix"] for r in result.unassigned["static_routes"]] == ["0.0.0.0/0"]
    assert [s["peer"] for s in result.unassigned["bfd_sessions"]] == ["10.1.1.1"]
    assert [p["peer"] for p in result.unassigned["bgp_peers"]] == ["10.1.1.1"]


def test_per_port_snapshot_without_scopes_narrows_unassigned_to_its_port():
    # Port bez migrovane sluzby: NEZARAZENO nesmi vypsat cely box.
    subject = _new()
    subject.scopes = []
    subject.facts["bfd"] = {
        "10.1.1.1": {"state": "Up", "interface": "et-0/0/8.200"},
        "10.2.2.2": {"state": "Up", "interface": "et-0/0/9.0"},
    }
    subject.facts["bgp"] = {
        "10.9.9.9": {"state": "Established", "routing_instance": "CUST-B", "ribs": {}}
    }

    result = api.evaluate(subject, now=NOW, port="et-0/0/8")

    assert [s["peer"] for s in result.unassigned["bfd_sessions"]] == ["10.1.1.1"]
    assert result.unassigned["bgp_peers"] == []


def test_layer1_only_subject_counts_as_no_services():
    # no_services se pocita ze service scopu, ne ze vsech - budouci
    # box-level scope by jinak tuhle logiku rozbil (spec, Mimo rozsah).
    subject = _new()
    subject.scopes = [
        Scope(
            id="l1:et-0/0/8",
            kind="layer1",
            key=ScopeKey(None, "Layer1", "physical-port"),
            selectors=Selectors(interfaces=["et-0/0/8"]),
        )
    ]

    result = api.evaluate(subject, now=NOW)

    assert result.no_services is True


def test_run_with_services_has_no_no_services_key():
    result = api.evaluate(_new(), now=NOW)

    assert result.no_services is False
    assert "no_services" not in result.to_dict()


def test_profile_filter_hiding_all_services_is_not_no_services():
    # Subjekt sluzby ma, jen je profil vyfiltroval - report nesmi tvrdit,
    # ze inventory je prazdna.
    result = api.evaluate(_new(), now=NOW, service_types=["E-LAN"])

    assert result.scopes == []
    assert result.no_services is False


def test_baseline_without_scopes_leaves_subject_services_unmatched():
    # Stary baseline zachyceny bez inventory: sluzby subjektu nemaji s cim
    # se sparovat, zadna zvlastni vetev.
    baseline = _old()
    baseline.scopes = []

    result = api.evaluate(_new(), baseline=baseline, now=NOW)

    assert [item["description"] for item in result.unmatched["subject"]] == ["L3VPN"]
    assert result.no_services is False
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run: `pyats-venv/bin/python -m pytest tests/test_engine.py -q -k "without_scopes or layer1_only or no_services or profile_filter_hiding or baseline_without_scopes"`
Expected: FAIL. Most fail with `AttributeError: 'RunResult' object has no attribute 'no_services'`. The unassigned tests fail because device mode returns `[]`.

- [ ] **Step 3: Add the field to `RunResult`**

In `migration_validator/models/result.py`, add the field just before `schema_version: int = 1`:

```python
    # Subjekt nema zadny service scope (port bez migrovane sluzby, capture
    # bez inventory) - checky nebezely. Pocita se ze service scopu pred
    # filtrem profilu/kroku, takze vyfiltrovane sluzby tohle nenastavi.
    # Aditivni klic jako `filtered`: zapisuje se jen kdyz plati (spec
    # 2026-09-24, odstraneni device rezimu).
    no_services: bool = False
```

In `to_dict()`, before `return payload`, add:

```python
        if self.no_services:
            payload["no_services"] = True
```

- [ ] **Step 4: Remove the fallback and compute the flag in `engine.py`**

Change the import at line 26 to:

```python
from migration_validator.models.scope import LAYER1_SERVICE_TYPE, Scope
```

Change `_scopes_of` to:

```python
def _scopes_of(snapshot: Snapshot) -> list[Scope]:
    return list(snapshot.scopes)
```

Change the `_in_profile` comment (around line 621) from "device a layer1 scopy jsou infrastruktura" to:

```python
        # Filtr je jen na service typy: layer1 scopy jsou infrastruktura,
        # ne sluzba, a v reportu zustavaji vzdy.
```

In the `RunResult(...)` call at the end of `evaluate_snapshots`, add the keyword argument:

```python
        no_services=not any(scope.kind == "service" for scope in subject_scopes),
```

- [ ] **Step 5: Let NEZARAZENO run without scopes**

In `_unassigned_bgp_peers`, `_unassigned_static_routes` and `_unassigned_bfd_sessions`, delete these two lines each time:

```python
    if any(scope.is_device for scope in scopes):
        return []
```

- [ ] **Step 6: Run the engine tests**

Run: `pyats-venv/bin/python -m pytest tests/test_engine.py -q`
Expected: all pass. `test_identity_on_device_scope_is_empty_not_crashing` still passes because `device_scope()` still exists; Task 3 deletes it.

- [ ] **Step 7: Run the full suite**

Run: `pyats-venv/bin/python -m pytest -q`
Expected: all pass. Any failure means another test relied on the fallback. Fix it by giving that test a service scope, and note it in the commit message.

- [ ] **Step 8: Commit**

```bash
git add migration_validator/models/result.py migration_validator/engine.py tests/test_engine.py
git commit -m "feat(engine): snapshot without services runs no checks and says so

No more device-scope fallback for an empty scope list (a per-port capture
of a port without migrated services was silently evaluated as the whole
box). RunResult.no_services is additive and counts only service scopes;
NEZARAZENO keeps running, narrowed to the port as before.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Checks — drop `is_device` branches and `requires_inventory`

**Files:**
- Modify: `migration_validator/checks/base.py:87` (attribute), `:105-108` (`applies_to`), `:132` (`describe`), `:209-215` (skip), `:222-224` (comment)
- Modify: `requires_inventory = True` lines in `checks/deactivation.py`, `checks/multicast.py` (×5), `checks/reachability.py` (×3), `checks/core_protocols.py` (×7)
- Modify: `migration_validator/checks/bfd.py:30-41` (comment + `SESSION_GONE`), `:96`, `:104-115`, `:146-200`, `:260-277`
- Modify: `migration_validator/checks/routes.py:7`, `:175-181` (comments only)
- Modify: `migration_validator/checks/optics.py:36-37` (docstring only)
- Test: `tests/checks/test_base.py`, `tests/checks/test_bfd.py`, `tests/checks/test_core_protocols.py`, `tests/checks/test_optics.py`, `tests/checks/test_reachability.py`, `tests/checks/test_routes.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `Check.describe()` no longer has a `requires_inventory` key. `Check.applies_to(scope)` has no device branch. `bfd._session_value(session, configured, bgp_state) -> str` (the `is_device` parameter is removed).

- [ ] **Step 1: Update the tests first (deletions and rewrites)**

`tests/checks/test_base.py`:
- Delete the `InventoryCheck` class (lines 34-36).
- Delete `test_check_requiring_inventory_skips_on_device_scope`, `test_check_applies_to_device_scope_regardless_of_service_types` and `test_missing_inventory_skip_is_a_row_not_a_veta`.
- In `test_check_with_service_subtypes_gates_on_subtype`, delete the line `assert check.applies_to(device_scope()) is True`.
- In `test_finding_without_label_borrows_the_one_from_the_check` and `test_run_check_propagates_presentation_fields`, replace `scope=device_scope()` with `scope=_scope()`.
- Change the import to `from migration_validator.models.scope import Scope, ScopeKey, Selectors`.
- Add:

```python
def test_describe_has_no_requires_inventory_key():
    # Spec 2026-09-24: bez device rezimu bezi kazdy check s inventory,
    # priznak nema co rozlisovat.
    assert "requires_inventory" not in DummyCheck().describe()
```

`tests/checks/test_bfd.py`:
- Delete `test_device_scope_never_claims_anything_about_the_configuration` and `test_device_scope_reports_state_without_intent`.
- In the comments around lines 136 and 157, replace `configured/is_device` with `configured`.
- Remove the `device_scope` import if nothing else uses it.

`tests/checks/test_core_protocols.py`: rename `test_old_bfd_check_still_applies_to_internet_and_device_scope` to `test_old_bfd_check_still_applies_to_internet`. Delete its inner `from migration_validator.models.scope import device_scope` and the line `assert old_check.applies_to(device_scope()) is True`.

`tests/checks/test_optics.py`: delete `test_device_scope_bez_portu_nespada` and the comment block above it (from `# Check.applies_to() pousti device scope` to the test). Remove the `device_scope` import if it becomes unused.

`tests/checks/test_reachability.py`: delete `test_arp_skips_on_device_scope`. Remove the `device_scope` import if it becomes unused.

`tests/checks/test_routes.py`:
- Replace `test_device_scope_with_baseline_record_says_missing_against_baseline` with a service-scope version. The behaviour doesn't depend on the scope kind:

```python
def test_service_scope_with_baseline_record_says_missing_against_baseline():
    """Kdyz baseline mereni routu ma, rozpor se hlasi proti nemu ('chybi')."""
    scope = Scope(
        id="svc:X:Internet",
        kind="service",
        key=ScopeKey("X", "Internet", None),
        selectors=Selectors(static_routes=list(CONFIGURED)),
    )
    ctx = CheckContext(
        scope=scope,
        subject={"routes": {}},
        baseline={"routes": _installed()},
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "chybi"
    assert findings[0].message == "inet.0 198.62.1.0/29: v baseline byla, v subjektu neni"
```

- Delete `test_device_scope_configured_route_missing_is_not_blamed_on_baseline`. `test_service_scope_reports_missing_from_table_without_baseline` already covers it.
- If `ScopeKey` isn't imported in that file yet, add it to the existing `from migration_validator.models.scope import ...` line.

- [ ] **Step 2: Run the check tests and confirm the new describe test fails**

Run: `pyats-venv/bin/python -m pytest tests/checks -q`
Expected: exactly one FAIL, `test_describe_has_no_requires_inventory_key`, because the key is still present. Everything else passes.

- [ ] **Step 3: Remove `requires_inventory` and the device branch from `checks/base.py`**

- Delete the line `requires_inventory: ClassVar[bool] = False`.
- In `describe()`, delete `"requires_inventory": self.requires_inventory,`.
- `applies_to` becomes:

```python
    def applies_to(self, scope: Scope) -> bool:
        if scope.kind == "layer1":
            return self.layer1
        if self.service_types is not None:
            if scope.service_type not in self.service_types:
                return False
        if self.service_subtypes is not None:
            if scope.service_subtype not in self.service_subtypes:
                return False
        if self.excluded_subtypes is not None:
            if scope.service_subtype in self.excluded_subtypes:
                return False
        return True
```

- In `run_check`, delete the whole `if check.requires_inventory and ctx.scope.is_device:` block (the `_skip(... "bez inventory")` call).
- Delete the three comment lines starting `# Poradi je soucast pozadavku: zkratka jde az za requires_inventory` that sit above the deactivation shortcut.

Then remove the attribute from the 16 checks:

```bash
sed -i '/^    requires_inventory = True$/d' migration_validator/checks/*.py
grep -rn "requires_inventory" migration_validator tests
```

Expected grep output: nothing.

- [ ] **Step 4: Simplify `checks/bfd.py`**

- Replace the comment block and constant at lines 30-41 (from `# NOT_IN_SERVICE je tvrzeni o CLENSTVI` through `SESSION_GONE = "session zmizela"`) with:

```python
# NOT_IN_SERVICE je tvrzeni o CLENSTVI ve sluzbe, ne o existenci na zarizeni.
# Peer, ktereho uz tato sluzba nenarokuje, muze na zarizeni dal bezet pod
# jinou sluzbou - engine.py:_unassigned_bfd_sessions ho ukaze v NEZARAZENO.
# Konstanta je sdilena s bgp.py - stejny konstrukt, stejna formulace.
```

- In `run`, delete the argument line `is_device=ctx.scope.is_device,`.
- In `_finding`, delete the parameter `is_device: bool,`. Change both `_session_value(...)` calls to pass `(…, configured, bgp_state)` with no `is_device`.
- Change the comments that say `configured/is_device` to say `configured`.
- In the `session is not None` branch, replace the condition and its two-line comment:

```python
            if not configured:
```

(The old comment "Bez inventory neni zamer znam, takze se nehlasi, ze konfigurace chybi (AR-17)." is deleted.)

- In the `if not configured:` branch below, delete the whole inner `if is_device:` block (the `SESSION_GONE` Finding and its comment). What remains is the `NOT_IN_SERVICE` return.
- `_session_value` becomes:

```python
def _session_value(
    session: dict[str, Any] | None,
    configured: bool,
    bgp_state: str,
) -> str:
    """Vraci presne to, co dnes konci ve `value` prislusne vetve `_finding`.
    Pouziva se pro subjekt i pro baseline session (se stejnym configured
    subjektu), takze baseline_value mluvi stejnym slovnikem jako value (R-5)."""
    if session is not None:
        state = str(session.get("state", UNKNOWN))
        if not configured:
            return PARSER_MISSED
        return state
    if not configured:
        return NOT_IN_SERVICE
    if bgp_state != ESTABLISHED:
        return BGP_NOT_UP
    return NO_SESSION
```

Check: `grep -n "is_device\|SESSION_GONE\|AR-17" migration_validator/checks/bfd.py` prints nothing.

- [ ] **Step 5: Comment-only edits in `routes.py` and `optics.py`**

`checks/routes.py` line 7: replace `- bez mereni subjektu by v rezimu bez inventory nebylo co vypsat,` with:

```python
- bez mereni subjektu by tise zmizela routa, kterou parser do selektoru
  nedostal (neznamy tvar konfigurace),
```

`checks/routes.py` around lines 175-181: replace the comment above `if baseline is not None:` with:

```python
        # Rozliseni je podle toho, jestli je co srovnavat s baselinem:
        # baseline zaznam existuje -> chybi proti baselinu; baseline zaznam
        # neni -> jen konfigurace tvrdi, ze routa ma byt v tabulce, a neni.
```

`checks/optics.py` lines 36-37: replace the docstring with:

```python
    """Port L1 scopu; None, kdyz scope zadne rozhrani nenese."""
```

- [ ] **Step 6: Run the check tests, then the full suite**

Run: `pyats-venv/bin/python -m pytest tests/checks -q && pyats-venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/checks tests/checks
git commit -m "refactor(checks): drop device-scope branches and requires_inventory

Every check now runs with an inventory, so the device shortcut in
applies_to, the 'bez inventory' skip and the AR-17 BFD branch
(SESSION_GONE) have nothing left to distinguish.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Model, linker, ping — remove `device_scope`

**Files:**
- Modify: `migration_validator/models/scope.py:1-6` (module docstring), `:13` (`DEVICE_SCOPE_ID`), `:~150` (`kind` comment), `:is_device`, `:254-262` (device branch in `select`), `:464-470` (`_empty`, `device_scope`)
- Modify: `migration_validator/scoping/linker.py:74,82,142,146`
- Modify: `migration_validator/probes/ping.py:6` (module docstring), `:227`
- Test: `tests/models/test_scope.py`, `tests/models/test_scope_core.py`, `tests/models/test_scope_multicast.py`, `tests/scoping/test_linker.py`, `tests/probes/test_ping.py`, `tests/test_engine.py`, `tests/collectors/test_conformance.py`, `tests/gui/test_evaluation_routes.py`

**Interfaces:**
- Consumes: Task 1 (the engine no longer imports `device_scope`) and Task 2 (checks no longer read `is_device`).
- Produces: `Scope` has no `is_device` property. `models.scope` exports no `device_scope` or `DEVICE_SCOPE_ID`. `Scope.kind` is `"service"` or `"layer1"`.

- [ ] **Step 1: Update the tests**

`tests/models/test_scope.py`:
- Delete `test_device_scope_passes_evpn_instance`, `test_device_scope_selects_everything`, `test_device_scope_is_flagged`, `test_device_scope_ping_skipped_je_vzdy_false` and `test_device_scope_sees_all_routes_and_sessions`.
- Rewrite `test_nd_area_is_a_list_when_missing`:

```python
def test_nd_area_is_a_list_when_missing():
    """Spatny prazdny typ by check videl jako prazdny slovnik a tise prosel."""
    selected = _service_scope().select({})

    assert selected["nd"] == []
    assert "nd" in FACT_AREAS
```

- Add:

```python
def test_device_scope_no_longer_exists():
    # Spec 2026-09-24: jmeno `device` se pro nic noveho nepouzije.
    import migration_validator.models.scope as scope_module

    assert not hasattr(scope_module, "device_scope")
    assert not hasattr(scope_module, "DEVICE_SCOPE_ID")
    assert not hasattr(Scope, "is_device")
```

- Remove `device_scope` from the import line.

`tests/models/test_scope_core.py`: delete `test_isis_overview_reaches_device_scope` and remove `device_scope` from the imports.

`tests/models/test_scope_multicast.py`: delete `test_device_scope_passes_everything` and remove `device_scope` from the imports.

`tests/scoping/test_linker.py`: delete `test_device_scope_is_ignored`.

`tests/probes/test_ping.py`: delete `test_device_scope_produces_no_targets`.

`tests/test_engine.py`: delete `test_identity_on_device_scope_is_empty_not_crashing`, and remove `device_scope` from the `from migration_validator.models.scope import ...` line.

`tests/collectors/test_conformance.py` line 264: replace

```python
    service_scopes = [scope for scope in snapshot.scopes if not scope.is_device]
```

with

```python
    service_scopes = [scope for scope in snapshot.scopes if scope.kind == "service"]
```

`tests/gui/test_evaluation_routes.py` line 381: replace `if s["scope_id"] != "device" and s["identity"].get("service_type") != "Layer1"` with `if s["identity"].get("service_type") != "Layer1"`.

- [ ] **Step 2: Run the new model test and confirm it fails**

Run: `pyats-venv/bin/python -m pytest tests/models/test_scope.py::test_device_scope_no_longer_exists -q`
Expected: FAIL (`device_scope` still exists).

- [ ] **Step 3: Remove the device scope from `models/scope.py`**

- Replace the module docstring (lines 1-6) with:

```python
"""Scope - filtr nad device-scoped fakty.

Scope neobsahuje zadna namerena data. Snimek bez service scopu se
nevyhodnocuje (engine: `no_services`) - zadny scope, ktery by propoustel
vsechno, neexistuje (spec 2026-09-24).
"""
```

- Delete `DEVICE_SCOPE_ID = "device"`.
- In the `Scope` dataclass, change `kind: str  # service | device` to `kind: str  # service | layer1`.
- Delete the `is_device` property.
- In `select`, delete the whole `if self.is_device:` block at the top (the one building `selected` from `FACT_AREAS` with `_empty`, plus `ping` and `ping_skipped`).
- Delete `_empty` and `device_scope` at the end of the file.

Check: `grep -n "is_device\|device_scope\|DEVICE_SCOPE_ID\|_empty" migration_validator/models/scope.py` prints nothing.

- [ ] **Step 4: Remove the checks from the linker and ping**

`scoping/linker.py`:
- Lines 74 and 146: `if scope.is_device or scope.service_type ...` becomes `if scope.service_type ...` (keep the rest of each condition).
- Line 82: `if scope.is_device or scope.service_type != L2_SERVICE_TYPE:` becomes `if scope.service_type != L2_SERVICE_TYPE:`.
- Line 142: `if not scope.is_device and scope.service_type in L3_SERVICE_TYPES` becomes `if scope.service_type in L3_SERVICE_TYPES`.

`probes/ping.py`:
- In `resolve_targets`, delete the line `scope.is_device` together with its trailing `or`, so the condition starts with `scope.service_type not in PING_SERVICE_TYPES`.
- In the module docstring, replace the sentence starting `Ping bezi jen v service rezimu: bez inventory neni znam cil` (through the end of that sentence) with: `Ping bezi jen pro service scopy Internet/IPVPN - cil i zdrojova adresa se berou ze scopu.`

Check: `grep -rn "is_device\|device_scope\|DEVICE_SCOPE_ID" migration_validator tests --include=*.py` prints nothing.

- [ ] **Step 5: Run the full suite**

Run: `pyats-venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/models/scope.py migration_validator/scoping/linker.py migration_validator/probes/ping.py tests
git commit -m "refactor(scope): remove the device scope

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Text report — the no-services line

**Files:**
- Modify: `migration_validator/reporting/text_report.py:~510-563` (`render`)
- Test: `tests/reporting/test_text_report.py`

**Interfaces:**
- Consumes: `RunResult.no_services` (Task 1).
- Produces: constant `NO_SERVICES_LINE` in `reporting/text_report.py`, and helper `_service_lines(result, detail, has_baseline, color) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/reporting/test_text_report.py` (next to the `_result` helper):

```python
def _no_services_result() -> RunResult:
    return replace(_result([], baseline=False), no_services=True)


def test_no_services_prints_one_line_instead_of_the_service_table():
    output = render(_no_services_result())

    assert "Subjekt nema v inventory zadne migrovane sluzby - checky nebezely." in output
    assert "SLUZBA" not in output
    assert "NESPAROVANO" in output


def test_filtered_no_services_report_keeps_the_line():
    shown = filter_result(_no_services_result(), statuses={Status.FAIL})

    assert "zadne migrovane sluzby" in render(shown)


def test_run_with_services_has_no_no_services_line():
    assert "zadne migrovane sluzby" not in render(_result([]))
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `pyats-venv/bin/python -m pytest tests/reporting/test_text_report.py -q -k no_services`
Expected: the first two FAIL (the line is missing and the `SLUZBA` header is present). The third passes.

- [ ] **Step 3: Implement**

In `reporting/text_report.py`, add near the other module constants:

```python
# Snimek bez service scopu (port bez migrovane sluzby, capture bez
# inventory) - spec 2026-09-24. Prazdna tabulka by nerekla proc je prazdna.
NO_SERVICES_LINE = "Subjekt nema v inventory zadne migrovane sluzby - checky nebezely."
```

Move the code from `views = [(scope, build_view(scope, detail=detail)) for scope in result.scopes]` through the final `for scope, view in views: if scope.scope_id in shown: lines.extend(_block(view, has_baseline, color))` **unchanged** into a new module-level function:

```python
def _service_lines(
    result: RunResult, detail: bool, has_baseline: bool, color: bool
) -> list[str]:
    """Tabulka sluzeb a rozbalene bloky - telo reportu mezi souhrnem a
    NESPAROVANO."""
    lines: list[str] = []
    # ... presunuty kod beze zmeny, jen `lines` je lokalni ...
    return lines
```

In `render`, where the moved code used to be, put:

```python
    if result.no_services:
        lines.append(NO_SERVICES_LINE)
        lines.append("")
    else:
        lines.extend(_service_lines(result, detail, has_baseline, color))
```

(`has_baseline` is the variable `render` already passes to `_block`. Use the same name and value.)

- [ ] **Step 4: Run the report tests, then the full suite**

Run: `pyats-venv/bin/python -m pytest tests/reporting -q && pyats-venv/bin/python -m pytest -q`
Expected: all pass. The existing report tests show the move changed no output for normal runs.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/reporting/text_report.py tests/reporting/test_text_report.py
git commit -m "feat(report): say explicitly when the subject has no migrated services

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: GUI — drop the device classification, neutral "No migrated services"

**Files:**
- Modify: `migration_validator/gui/static/run_results.js:11,14-15` (device classification), `:67-85` (`buildEvaluationModel` return)
- Modify: `migration_validator/gui/static/app.js:~3092-3100` (pairing notices), `:~3151-3152` (panel text), `:~3186` (auxiliary section text)
- Test: `tests/js/run_results.test.js`

**Interfaces:**
- Consumes: the `no_services` key in the evaluation `result` JSON (Task 1).
- Produces: `buildEvaluationModel(...)` returns an extra `noServices: boolean`.

- [ ] **Step 1: Write the failing JS tests**

In `tests/js/run_results.test.js`:
- Delete the `DEVICE` constant (line 17).
- Replace the first test with:

```js
test("classifyScope: layer1 by raw type, everything else service", () => {
  assert.strictEqual(R.classifyScope(L1("ae0")), "layer1");
  assert.strictEqual(R.classifyScope(scope("A", "IPVPN")), "service");
  assert.strictEqual(R.classifyScope({ scope_id: "x", key: { service_type: "Layer1" }, identity: {} }), "layer1");
  assert.strictEqual(R.classifyScope({ scope_id: "weird", identity: {} }), "service");
});
```

- Add:

```js
test("buildEvaluationModel: noServices follows result.no_services, never attention", () => {
  const empty = evaluation("post-ae0.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), []);
  empty.result.no_services = true;
  const normal = evaluation("post-ae0.json", "pre-ge4.json", step("ge-0/0/4", "ae0"), [scope("A", "Internet")]);

  assert.strictEqual(R.buildEvaluationModel(empty, 0, SNAPSHOTS, "s").noServices, true);
  assert.strictEqual(R.buildEvaluationModel(normal, 0, SNAPSHOTS, "s").noServices, false);

  const group = R.buildPairingGroups({
    runName: "r", rows: [row("ge-0/0/4", "ae0")], evaluations: [empty], snapshots: SNAPSHOTS,
  }).groups[0];
  assert.strictEqual(R.groupNeedsAttention(group, R.ALL_TYPES), false);
});
```

- [ ] **Step 2: Run the JS tests and confirm the new one fails**

Run: `node --test tests/js/*.test.js`
Expected: the `noServices` test FAILs (`undefined !== true`). All the others pass.

- [ ] **Step 3: Implement in `run_results.js`**

- Delete `const DEVICE_SCOPE_ID = "device";`.
- Delete the line `if (scope.scope_id === DEVICE_SCOPE_ID) return "device";` from `classifyScope`.
- In the object returned by `buildEvaluationModel`, add after `filtered: result.filtered || null,`:

```js
    noServices: result.no_services === true,
```

- [ ] **Step 4: Render it in `app.js`**

- In the pairing-header notices loop (`for (const m of group.evaluations) {`, around line 3095), add as its first line:

```js
      if (m.noServices) notice("neutral", "No migrated services");
```

- In `buildEvaluationPanel`, change the `serviceEntries.length === 0` notice to:

```js
      panel.appendChild(el("div", { className: "pairing-notice", text: model.noServices
        ? "No migrated services in the inventory — checks did not run"
        : "No service results in this evaluation" }));
```

- In `buildAuxiliarySection`, change the `"No service results in this evaluation"` notice the same way, using `m.noServices`.

- [ ] **Step 5: Run the JS tests and the Python GUI tests**

Run: `node --test tests/js/*.test.js && pyats-venv/bin/python -m pytest tests/gui -q`
Expected: all pass.

- [ ] **Step 6: Visual check in the lab GUI**

Restart the lab GUI (see memory: restart after merges; here, after this change). In a run, open a pairing whose new port carries no migrated service; if there isn't one, capture any port without services. Expected: the header shows the grey "No migrated services" pill and no PASS badge, and the panel shows "No migrated services in the inventory — checks did not run". If no such port is available, write in the commit message that the visual check is left for the user.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/gui/static/run_results.js migration_validator/gui/static/app.js tests/js/run_results.test.js
git commit -m "feat(gui): neutral 'No migrated services' state, no device classification

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: CLI exit code test and documentation

**Files:**
- Test: `tests/test_cli.py`
- Modify: `docs/cs/README.md:93-95,111`, `docs/en/README.md:92-94,110`
- Modify: `docs/cs/architecture.md:125-139`, `docs/en/architecture.md:128-142`
- Modify: `docs/cs/reference.md:456,590-591`, `docs/en/reference.md:470,607-608`
- Modify: `docs/cs/files/models.md:165-166,183,196-202`, `docs/en/files/models.md:173,192,206-213`
- Modify: `docs/cs/files/top-level.md:144,155`, `docs/en/files/top-level.md:149,161-162`
- Modify: `docs/cs/files/probes.md:63`, `docs/en/files/probes.md:65`
- Modify: `docs/cs/files/checks.md:46,55,86-90,103,294,509,647,678,690-691,758,797,805-813,1295`, and the matching lines in `docs/en/files/checks.md` (48, 56, 79-84, 95, 245, 451-452, 620, 633-634, 703, 743, 752-760, 1250)

**Interfaces:**
- Consumes: everything above.
- Produces: no code.

- [ ] **Step 1: Write the CLI test**

Add to `tests/test_cli.py`:

```python
def test_evaluate_snapshot_without_services_exits_ok(tmp_path, capsys):
    # Spec 2026-09-24: nic se nezkontrolovalo, nic neselhalo - exit 0
    # i s --warn-as-error.
    path = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113")
    snapshot = load_snapshot(path)
    snapshot.scopes = []
    save_snapshot(snapshot, path)

    code = main(["evaluate", "--snapshot", str(path), "--warn-as-error"])

    assert code == EXIT_OK
    assert "zadne migrovane sluzby" in capsys.readouterr().out
```

- [ ] **Step 2: Run it**

Run: `pyats-venv/bin/python -m pytest tests/test_cli.py::test_evaluate_snapshot_without_services_exits_ok -q`
Expected: PASS. Tasks 1 and 4 already implement the behaviour; this test pins the exit code. If it fails, the CLI counts something other than check results. Stop and report instead of changing the exit logic.

- [ ] **Step 3: Update the READMEs**

`docs/cs/README.md` lines 93-95: replace the paragraph `**Bez inventory nástroj funguje taky**…` with:

```markdown
**Bez inventory** `capture` sebere jen data (vhodné pro ladění nebo raw záznam).
`evaluate` nad takovým snímkem žádné checky nespustí: vypíše „Subjekt nema v inventory
zadne migrovane sluzby - checky nebezely." a sekci NEZARAZENO se vším, co na boxu je.
Totéž platí pro per-port capture portu, který nenese žádnou migrovanou službu.
```

`docs/cs/README.md` line 111: the `--inventory` table row becomes `| \`--inventory\` | YAML z parseru; bez něj vznikne datový snímek bez služeb (evaluate nad ním checky nespustí) |`.

`docs/en/README.md` lines 92-94 and 110: the same content in English:

```markdown
**Without an inventory** `capture` only collects data (useful for debugging or a raw
record). `evaluate` on such a snapshot runs no checks: it prints "Subjekt nema v inventory
zadne migrovane sluzby - checky nebezely." and the NEZARAZENO section with everything on
the box. The same applies to a per-port capture of a port that carries no migrated service.
```

and `| \`--inventory\` | YAML from the parser; without it the snapshot has no services (evaluate runs no checks on it) |`.

- [ ] **Step 4: Update the architecture and reference docs**

- `docs/cs/architecture.md:125-139` and `docs/en/architecture.md:128-142`: delete the "bez inventory" table row and the device-mode sentences, including the one about ping not running in device mode. Replace them with one paragraph: a snapshot without service scopes isn't evaluated; the result carries `no_services` and NEZARAZENO still runs (spec 2026-09-24).
- `docs/cs/reference.md:456` and `docs/en/reference.md:470`: delete the sentence about the device scope's `kind: "device"`, and state that `kind` is `service` or `layer1`.
- `docs/cs/reference.md:590-591` and `docs/en/reference.md:607-608`: replace the "always empty in device mode" sentence with: without service scopes, NEZARAZENO lists everything (on a per-port snapshot, only what belongs to the port). Add `no_services` to the result-JSON key list next to `filtered` / `excluded_services`, with the additive-key rule.

- [ ] **Step 5: Update the per-file docs**

- `docs/{cs,en}/files/models.md`: delete the device-scope paragraphs (cs 196-202 / en 206-213) and the "device scope passes everything" clauses (cs 165-166, 183 / en 173, 192). Add one line: `RunResult.no_services` is additive and is computed from service scopes before filtering.
- `docs/{cs,en}/files/top-level.md`: cs 144 / en 149 (device scope as the inventory-less mode) becomes "an empty scope list → no checks, `no_services`". cs 155 / en 161-162 becomes "without scopes it lists every peer not owned by a scope, narrowed by `port`".
- `docs/{cs,en}/files/probes.md` cs 63 / en 65: "skips the device scope and every service type except…" becomes "skips every service type except…".
- `docs/{cs,en}/files/checks.md`:
  - delete `requires_inventory` from the attribute listing (cs 46 / en 48);
  - delete the device sentence in `applies_to` (cs 55 / en 56);
  - delete gate 3 of `run_check` (cs 86-90 / en 79-84) and renumber the gates that follow;
  - delete `bez inventory` from the list of skip values (cs 103 / en 95);
  - "layer1/device scope" becomes "layer1 scope" (cs 294 / en 245);
  - delete "Všechny mají `requires_inventory = True` (na device scope tedy vrací `SKIP`)" (cs 509 / en 451-452);
  - in the routes section, delete the "platí i v device scope" clauses and the AR-17 sentence (cs 647, 678, 690-691, 758 / en 620, 633-634, 703), and change the cs 647 / en row to match the new `routes.py` comment from Task 2;
  - in the BFD section, delete the `session zmizela` row and the whole "`is_device` je tady nečinný" subsection (cs 797, 805-813 / en 743, 752-760);
  - deactivation check (cs 1295 / en 1250): delete "(`requires_inventory = True`)", keeping the sentence "vyžaduje inventory" / "requires inventory" as a plain statement.

Check:

```bash
grep -rn -i "device scope\|device\" scope\|device režim\|device mode\|device_scope\|is_device\|requires_inventory\|session zmizela" docs/cs docs/en
```

Expected: no hits. (`docs/superpowers/` is history and stays untouched.)

- [ ] **Step 6: Full verification**

Run: `pyats-venv/bin/python -m pytest -q && node --test tests/js/*.test.js`
Expected: both suites pass. Write down the Python test count; it should be the 2065 before this plan, minus the deleted device tests, plus the new ones.

Run: `grep -rn "is_device\|device_scope\|DEVICE_SCOPE_ID\|requires_inventory\|SESSION_GONE" migration_validator tests`
Expected: nothing.

- [ ] **Step 7: Commit**

```bash
git add tests/test_cli.py docs/cs docs/en
git commit -m "docs: device mode removed; snapshot without services

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
