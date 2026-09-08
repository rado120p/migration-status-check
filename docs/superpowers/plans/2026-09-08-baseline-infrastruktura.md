# Baseline porovnání — vlna 1: infrastruktura — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Framework umí říct „stejný špatný stav jako v baseline" (`Outcome.UNCHANGED` → PASS se značkou), „tahle hodnota se neporovnává" (`Finding.compared=False`), ví, které oblasti baseline změřila, překlíčuje všechny per-interface oblasti baseline podle mappingu, umí vyloučit subtyp a collector rout slučuje více `rt-entry` jednoho prefixu.

**Architecture:** Změny jen v `models/result.py`, `checks/base.py`, `engine.py`, `reporting/view.py` + `gui/static/view.js`, `reporting/text_report.py`, `collectors/routes.py`, `probes/ping.py`, `checks/reachability.py`, `models/scope.py`. Žádný check kromě reachability v této vlně nemění rozhodování — adopce UNCHANGED je vlna 2 (`2026-09-08-baseline-adopce-v-checkach.md`), multicast vlna 3.

**Tech Stack:** Python 3.13, lxml, pytest (`pyats-venv/bin/python -m pytest`), Node test runner pro `tests/js` (`node --test tests/js/*.test.js`).

**Spec:** `docs/superpowers/specs/2026-09-08-baseline-porovnani-a-multicast-opravy-design.md`

## Global Constraints

- Větev `baseline-porovnani-2026-09-08` z `main`. Commity s trailerem `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` a `Claude-Session: https://claude.ai/code/session_015vDBdZ4yTQibpQ1d6bHoQy`.
- Stav se nikdy nefabuluje: `baseline_value = value` jako obejití je zakázané; UNCHANGED jen s `ctx.baseline_measured(area)`.
- Hlášky bez diakritiky (jako celý `checks/`). Přesné texty:
  - ZMENA u UNCHANGED řádku: `beze zmeny (chyba uz v baseline)`
  - souhrn: `  z toho {n} PASS beze zmeny proti baseline (chyba uz pred migraci)`
- JSON výstup jen aditivní: `details.unchanged_since_baseline`, `details.compared`, `summary.pass_unchanged`, `describe().excluded_subtypes`. `SCHEMA_VERSION` 13 a `INVENTORY_SCHEMA_VERSION` 9 se nemění.
- `view.py` a `view.js` v zámku — stejné řetězce, stejná logika, oba testy.
- Suite zelená po každém tasku: `pyats-venv/bin/python -m pytest -q` a `node --test tests/js/*.test.js`.

---

## Task 1: Collector rout slučuje více `rt-entry` jednoho prefixu

**Files:**
- Modify: `migration_validator/collectors/routes.py:133-158` (`parse`)
- Test: `tests/collectors/test_routes.py`
- Fixture (už v repu, nahráno z MX1-POP1 2026-09-08): `tests/fixtures/cases/routes_qnh.xml` — mimo `tests/fixtures/rpc/<platform>/`, protože `tests/collectors/test_conformance.py` tam každý soubor páruje s collectorem a osiřelou nahrávku odmítá — inet.2 `10.11.11.1/32` se dvěma `rt-entry` (pref 5 `*` via `ge-0/0/0.0`; pref 7 bez `*` s `nh` `ge-0/0/0.0` i `ge-0/0/1.0`).

**Interfaces:**
- Produces: tvar faktu beze změny `{"next_hop": [...], "via": [...], "active": bool, "protocol": str}`; `active = any(entry active)`, hopy sjednocené bez duplicit, hopy aktivního záznamu první.

- [ ] **Step 1: Failing test**

Do `tests/collectors/test_routes.py` přidej `from pathlib import Path` k importům a na konec:

```python
CASES = Path(__file__).resolve().parents[1] / "fixtures" / "cases"


def test_two_rt_entries_of_one_prefix_are_merged():
    """next-hop + qualified-next-hop = dva rt-entry pod jednim prefixem
    (MX1-POP1 2026-09-08). Posledni zaznam nesmi prepsat aktivni.
    Nahravka lezi mimo rpc/<platform>/, kde test_conformance kazdy soubor
    paruje s collectorem."""
    xml = etree.parse(str(CASES / "routes_qnh.xml")).getroot()
    result = RoutesCollector().parse(xml, "junos")

    route = result["inet.2"]["10.11.11.1/32"]
    assert route["active"] is True
    assert route["via"] == ["ge-0/0/0.0", "ge-0/0/1.0"]
    assert route["next_hop"] == ["10.1.2.0", "10.1.0.5"]
    assert route["protocol"] == "static"


def test_single_entry_route_shape_is_unchanged(rpc_fixture):
    result = RoutesCollector().parse(rpc_fixture("junos", "routes"), "junos")
    for prefixes in result.values():
        for data in prefixes.values():
            assert set(data) == {"next_hop", "via", "active", "protocol"}
            assert len(data["via"]) == len(set(data["via"]))
```

- [ ] **Step 2: Ověř, že padá**

Run: `pyats-venv/bin/python -m pytest tests/collectors/test_routes.py -q -k merged`
Expected: FAIL — `assert False is True` (active z posledního záznamu).

- [ ] **Step 3: Implementace**

V `parse()` nahraď vnitřní cyklus přes `rt-entry`:

```python
                entries: list[dict[str, Any]] = []
                for entry in route.iter("rt-entry"):
                    protocol = (_text(entry, "protocol-name") or "").lower()
                    if protocol not in PROTOCOLS:
                        continue
                    entries.append({
                        "next_hop": _texts(entry, "to"),
                        "via": _texts(entry, "via"),
                        "active": _text(entry, "active-tag") == ACTIVE_TAG,
                        "protocol": protocol,
                    })
                if entries:
                    prefixes[prefix] = _merge_entries(entries)
```

a nad třídu přidej:

```python
def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if not (v in seen or seen.add(v))]


def _merge_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """next-hop + qualified-next-hop s jinou preferenci = dva <rt-entry>
    pod jednim prefixem (MX1-POP1 2026-09-08, routes_qnh.xml). Drive
    posledni zaznam prepsal prvni, takze aktivni routa vysla jako
    neaktivni a upstream check videl jen hopy neaktivniho zaznamu.
    Aktivni zaznam jde prvni, aby poradi hopu odpovidalo forwardingu."""
    ordered = sorted(entries, key=lambda e: not e["active"])
    return {
        "next_hop": _unique([hop for e in ordered for hop in e["next_hop"]]),
        "via": _unique([via for e in ordered for via in e["via"]]),
        "active": any(e["active"] for e in ordered),
        "protocol": ordered[0]["protocol"],
    }
```

- [ ] **Step 4: Testy**

Run: `pyats-venv/bin/python -m pytest tests/collectors/test_routes.py tests/checks/test_routes.py tests/checks/test_multicast.py -q`
Expected: PASS.

- [ ] **Step 5: Docstring modulu** — do hlavičky `collectors/routes.py` doplň odstavec: „Prefix muze mit vic rt-entry (next-hop + qualified-next-hop s jinou preferenci) — slucuji se, active = aspon jeden aktivni."

- [ ] **Step 6: Commit**

```bash
git add migration_validator/collectors/routes.py tests/collectors/test_routes.py tests/fixtures/cases/routes_qnh.xml
git commit -m "fix(collectors): routes slucuje vic rt-entry jednoho prefixu (qualified-next-hop)"
```

---

## Task 2: `Outcome.UNCHANGED`, `Finding.compared`, značky v `details`

**Files:**
- Modify: `migration_validator/models/result.py:66-103` (Outcome, derive_status, konstanty), `:118-135` (Finding)
- Modify: `migration_validator/checks/base.py:209-231` (run_check → CheckResult)
- Test: `tests/models/test_result.py`, `tests/checks/test_base.py`

**Interfaces:**
- Produces: `Outcome.UNCHANGED = "unchanged"`; `derive_status(Outcome.UNCHANGED, any) == Status.PASS`; konstanty `UNCHANGED_SINCE_BASELINE = "unchanged_since_baseline"`, `NOT_COMPARED = "compared"`; `Finding.compared: bool = True`; `run_check` zapíše `details[UNCHANGED_SINCE_BASELINE] = True` pro UNCHANGED a `details[NOT_COMPARED] = False` pro `compared=False`.

- [ ] **Step 1: Failing testy**

`tests/models/test_result.py` — na konec:

```python
from migration_validator.models.result import Outcome, Severity, Status, derive_status


def test_unchanged_outcome_is_pass_for_both_severities():
    assert derive_status(Outcome.UNCHANGED, Severity.CRITICAL) is Status.PASS
    assert derive_status(Outcome.UNCHANGED, Severity.ADVISORY) is Status.PASS
```

`tests/checks/test_base.py` — na konec (DummyCheck už v souboru existuje; použij jeho vzor):

```python
from migration_validator.models.result import NOT_COMPARED, UNCHANGED_SINCE_BASELINE


def test_unchanged_finding_is_pass_with_marker():
    class Unchanged(DummyCheck):
        def run(self, ctx):
            return [Finding(Outcome.UNCHANGED, "down, stejne jako v baseline", value="Down",
                            baseline_value="Down")]

    [result] = run_check(Unchanged(), _ctx(baseline={"interfaces": {}}))
    assert result.status is Status.PASS
    assert result.details[UNCHANGED_SINCE_BASELINE] is True
    assert result.baseline_value == "Down"


def test_uncompared_finding_carries_marker_and_ok_finding_does_not():
    class Mixed(DummyCheck):
        def run(self, ctx):
            return [
                Finding(Outcome.OK, "uptime", value="1d", compared=False),
                Finding(Outcome.OK, "state", value="Up", baseline_value="Up"),
            ]

    uncompared, compared = run_check(Mixed(), _ctx(baseline={"interfaces": {}}))
    assert uncompared.details[NOT_COMPARED] is False
    assert NOT_COMPARED not in compared.details
    assert UNCHANGED_SINCE_BASELINE not in compared.details
```

- [ ] **Step 2: Ověř pád** — `pyats-venv/bin/python -m pytest tests/models/test_result.py tests/checks/test_base.py -q` → FAIL (`AttributeError: UNCHANGED`).

- [ ] **Step 3: Implementace `models/result.py`**

Do `Outcome` přidej `UNCHANGED = "unchanged"` (za `RECOVERED`). V `derive_status` před `if outcome is Outcome.INFO`:

```python
    if outcome is Outcome.UNCHANGED:
        # Shodny spatny stav v baseline i subjektu (R-3, spec 2026-09-08):
        # migrace nic nezhorsila, tak PASS - ale se znackou, aby se radek
        # dal od zdraveho PASS odlisit (run_check ji zapisuje do details).
        return Status.PASS
```

Za `SKIP_DEACTIVATED` přidej:

```python
# Znacka radku, ktery je PASS jen proto, ze stejna chyba byla uz v baseline
# (Outcome.UNCHANGED). Renderer podle ni tiskne ZMENA a souhrn ji scita.
UNCHANGED_SINCE_BASELINE = "unchanged_since_baseline"

# Znacka radku, ktery hodnotu meri, ale proti baseline ji z definice
# neporovnava (multicast upstream, uptime, souhrnne radky). Hodnota False
# rika rendereru "ZMENA prazdna", ne "bez baseline" (R-4).
NOT_COMPARED = "compared"


def count_unchanged(checks: Iterable["CheckResult"]) -> int:
    return sum(1 for check in checks if check.details.get(UNCHANGED_SINCE_BASELINE))
```

Do `Finding` přidej pole (za `delta`): `compared: bool = True` s komentářem „False = hodnota se proti baseline neporovnava, renderer necha ZMENA prazdnou".

- [ ] **Step 4: Implementace `checks/base.py`**

V `run_check` nahraď `details=finding.details` za `details=_details(finding)` a přidej nad `run_check`:

```python
def _details(finding: Finding) -> dict[str, Any]:
    """Strukturalni znacky pro renderer - ne text, ne status."""
    details = dict(finding.details)
    if finding.outcome is Outcome.UNCHANGED:
        details[UNCHANGED_SINCE_BASELINE] = True
    if not finding.compared:
        details[NOT_COMPARED] = False
    return details
```

Import `NOT_COMPARED, UNCHANGED_SINCE_BASELINE` z `models.result`.

- [ ] **Step 5: Testy** — `pyats-venv/bin/python -m pytest tests/models tests/checks/test_base.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/models/result.py migration_validator/checks/base.py tests/models/test_result.py tests/checks/test_base.py
git commit -m "feat(result): Outcome.UNCHANGED (PASS se znackou) a Finding.compared"
```

---

## Task 3: Sloupec ZMENA čte značky (view.py + view.js)

**Files:**
- Modify: `migration_validator/reporting/view.py:29-40` (Row), `:86-108` (change_text), `:148-176` (_row)
- Modify: `migration_validator/gui/static/view.js:6-18` (changeText), `:40-52` (checkRow)
- Test: `tests/reporting/test_view.py`, `tests/js/view.test.js`

**Interfaces:**
- Produces: `Row.unchanged: bool = False`, `Row.compared: bool = True`; `change_text` vrací `UNCHANGED_TEXT = "beze zmeny (chyba uz v baseline)"` pro unchanged a `""` pro `compared=False`. JS řádek nese `unchanged` a `compared`.

- [ ] **Step 1: Failing testy**

`tests/reporting/test_view.py` — na konec:

```python
from migration_validator.models.result import NOT_COMPARED, UNCHANGED_SINCE_BASELINE
from migration_validator.reporting.view import UNCHANGED_TEXT, _row


def _row_of(**details):
    check = _check("x", mode="both", value="Down", baseline_value="Down")
    check.details.update(details)
    return _row(check, qualify=False)


def test_change_text_unchanged_marker_prints_text_even_when_values_equal():
    row = _row_of(**{UNCHANGED_SINCE_BASELINE: True})
    assert change_text(row, True) == UNCHANGED_TEXT
    assert change_text(row, False) == ""


def test_change_text_uncompared_row_is_blank_not_bez_baseline():
    check = _check("x", mode="both", value="1d 00:00:00", baseline_value=None)
    check.details[NOT_COMPARED] = False
    assert change_text(_row(check, qualify=False), True) == ""


def test_change_text_missing_baseline_without_marker_still_says_bez_baseline():
    check = _check("x", mode="both", value="v", baseline_value=None, status=Status.WARN)
    assert change_text(_row(check, qualify=False), True) == "bez baseline"
```

`tests/js/view.test.js` — na konec:

```javascript
test("changeText: unchanged marker prints text even when values equal", () => {
  const row = { mode: "both", status: "PASS", value: "Down", baseline_value: "Down",
    delta: null, unchanged: true, compared: true };
  assert.strictEqual(MigView.changeText(row, true), "beze zmeny (chyba uz v baseline)");
  assert.strictEqual(MigView.changeText(row, false), "");
});

test("changeText: uncompared row is blank, not 'bez baseline'", () => {
  const row = { mode: "both", status: "PASS", value: "1d", baseline_value: null,
    delta: null, unchanged: false, compared: false };
  assert.strictEqual(MigView.changeText(row, true), "");
});

test("checkRow: carries unchanged and compared from details", () => {
  const row = MigView.checkRow({ id: "x", mode: "both", status: "PASS", value: "v",
    details: { unchanged_since_baseline: true, compared: false } }, false);
  assert.strictEqual(row.unchanged, true);
  assert.strictEqual(row.compared, false);
  const plain = MigView.checkRow({ id: "y", mode: "both", status: "PASS", value: "v" }, false);
  assert.strictEqual(plain.unchanged, false);
  assert.strictEqual(plain.compared, true);
});
```

- [ ] **Step 2: Ověř pád** — `pyats-venv/bin/python -m pytest tests/reporting/test_view.py -q` a `node --test tests/js/*.test.js` → FAIL.

- [ ] **Step 3: Implementace `view.py`**

Konstanta vedle `NO_BASELINE`: `UNCHANGED_TEXT = "beze zmeny (chyba uz v baseline)"`. `Row` dostane pole `unchanged: bool = False` a `compared: bool = True`. `change_text`:

```python
    if not has_baseline:
        return ""
    if row.mode == "state":
        return ""
    if row.unchanged:
        # Hodnoty se rovnaji, ale prazdno by radek splynulo se zdravym PASS.
        return UNCHANGED_TEXT
    if not row.compared:
        # Namereno, ale z definice neporovnavano (R-4) - ne "bez baseline".
        return ""
    if row.baseline_value is None:
        return "" if row.status is Status.SKIP else NO_BASELINE
    ...
```

V `_row`: `unchanged=bool(check.details.get(UNCHANGED_SINCE_BASELINE))`, `compared=check.details.get(NOT_COMPARED, True) is not False`. Import konstant z `models.result`.

- [ ] **Step 4: Implementace `view.js`** — zrcadlově: `const UNCHANGED_TEXT = "beze zmeny (chyba uz v baseline)";`; v `changeText` po kontrole `state`: `if (row.unchanged) return UNCHANGED_TEXT; if (row.compared === false) return "";`. V `checkRow`: `unchanged: !!((check.details || {}).unchanged_since_baseline)`, `compared: (check.details || {}).compared !== false`.

- [ ] **Step 5: Testy** — `pyats-venv/bin/python -m pytest tests/reporting -q && node --test tests/js/*.test.js` → PASS.

- [ ] **Step 6: Docs** — `docs/cs/files/reporting.md:60-68` (obsah sloupce ZMENA): doplň dvě odrážky pro značky `unchanged_since_baseline` → `beze zmeny (chyba uz v baseline)` a `compared=False` → prázdno, před odrážku `baseline_value is None`.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/reporting/view.py migration_validator/gui/static/view.js tests/reporting/test_view.py tests/js/view.test.js docs/cs/files/reporting.md
git commit -m "feat(reporting): ZMENA cte znacky unchanged/compared (view.py + view.js)"
```

---

## Task 4: Souhrn `pass_unchanged`

**Files:**
- Modify: `migration_validator/engine.py:582-589` (summary), `migration_validator/reporting/text_report.py:180-182` (filtrovaný summary), `:338-360` + `:489` (řádek souhrnu)
- Test: `tests/test_engine.py`, `tests/reporting/test_text_report.py`

**Interfaces:**
- Consumes: `count_unchanged` z Task 2.
- Produces: `summary["pass_unchanged"]: int` (engine i filtrovaný report); řádek `  z toho {n} PASS beze zmeny proti baseline (chyba uz pred migraci)` jen při n > 0.

- [ ] **Step 1: Failing testy**

`tests/reporting/test_text_report.py` — na konec:

```python
from migration_validator.models.result import UNCHANGED_SINCE_BASELINE


def test_unchanged_line_printed_only_when_nonzero():
    result = _legacy_result()
    assert "beze zmeny proti baseline" not in render(result)

    result.summary["pass_unchanged"] = 2
    result.scopes[0].checks[0].details[UNCHANGED_SINCE_BASELINE] = True
    output = render(result)
    assert "  z toho 2 PASS beze zmeny proti baseline (chyba uz pred migraci)" in output
```

`tests/test_engine.py` — na konec:

```python
def test_summary_counts_unchanged_rows(monkeypatch):
    from migration_validator.checks import base as check_base
    from migration_validator.models.result import Finding, Outcome

    class Unchanged(check_base.Check):
        id = "unchanged_dummy"
        title = "t"
        label = "Dummy"
        mode = check_base.Mode.BOTH

        def run(self, ctx):
            return [Finding(Outcome.UNCHANGED, "x", value="Down", baseline_value="Down")]

    monkeypatch.setattr("migration_validator.engine.all_checks", lambda: [Unchanged()])
    result = api.evaluate(_new(), baseline=_old(), now=NOW)
    assert result.summary["pass_unchanged"] == 1
    assert result.summary["pass"] == 1
```

- [ ] **Step 2: Ověř pád** — `pyats-venv/bin/python -m pytest tests/test_engine.py tests/reporting/test_text_report.py -q -k unchanged` → FAIL (KeyError / text chybí).

- [ ] **Step 3: Implementace**

`engine.py` summary:

```python
    all_checks_results = [check for scope_result in scope_results for check in scope_result.checks]
    summary = {
        **count_statuses(check.status for check in all_checks_results),
        "pass_unchanged": count_unchanged(all_checks_results),
        "scopes_matched": matched_count,
        ...
```

`text_report.py` filtrovaný summary (`:180`): za `**count_statuses(...)` přidej `"pass_unchanged": count_unchanged(check for scope in scopes for check in scope.checks),`. U `:489` za `lines.extend(_counts_lines(...))`:

```python
    unchanged = int(summary.get("pass_unchanged", 0))
    if unchanged:
        # Sest zdedenych chyb v "PASS 42" by jinak nikdo nevidel (R-3).
        lines.append(
            f"  z toho {unchanged} PASS beze zmeny proti baseline (chyba uz pred migraci)"
        )
```

Import `count_unchanged` v obou souborech.

- [ ] **Step 4: Testy** — `pyats-venv/bin/python -m pytest tests/test_engine.py tests/reporting tests/gui -q` → PASS (GUI `groups._add` iteruje jen své klíče, aditivní klíč nevadí).

- [ ] **Step 5: Docs** — `docs/cs/reference.md:537` (seznam statusů): doplň větu „`PASS` se značkou `unchanged_since_baseline` = stejná chyba byla už v baseline (R-3, spec 2026-09-08); souhrn nese `pass_unchanged`."

- [ ] **Step 6: Commit**

```bash
git add migration_validator/engine.py migration_validator/reporting/text_report.py tests/test_engine.py tests/reporting/test_text_report.py docs/cs/reference.md
git commit -m "feat(summary): pass_unchanged v souhrnu a radek v textovem reportu"
```

---

## Task 5: `CheckContext.baseline_failed_collectors` a `baseline_measured()`

> **Změněno po final review vlny 1 (2026-09-08):** pole je `baseline_collectors` (celý `capture.collectors` dict baseline) a `baseline_measured(area)` je pozitivní důkaz `status == "ok"`; pro `"ping"` = baseline má probe záznamy scopu. Text tasku níže je původní, implementace viz commit fix wave.

**Files:**
- Modify: `migration_validator/checks/base.py:38-56` (CheckContext), `migration_validator/engine.py:294-302` (_run_scope)
- Test: `tests/checks/test_base.py`, `tests/test_engine.py`

**Interfaces:**
- Produces: `CheckContext.baseline_failed_collectors: dict[str, str] = {}`; `CheckContext.baseline_measured(area: str) -> bool` = `self.has_baseline and area not in self.baseline_failed_collectors`. Vlna 2 volá výhradně tuhle metodu před `Outcome.UNCHANGED`.

- [ ] **Step 1: Failing testy**

`tests/checks/test_base.py`:

```python
def test_baseline_measured_requires_baseline_and_ok_collector():
    assert _ctx(baseline=None).baseline_measured("ldp_neighbor") is False
    ctx = _ctx(baseline={"ldp_neighbor": {}},
               baseline_failed_collectors={"ldp_neighbor": "RpcError"})
    assert ctx.baseline_measured("ldp_neighbor") is False
    assert ctx.baseline_measured("pim_neighbor") is True
```

`tests/test_engine.py`:

```python
def test_engine_passes_baseline_failed_collectors_to_checks(monkeypatch):
    from migration_validator.checks import base as check_base
    from migration_validator.models.result import Finding, Outcome

    seen = {}

    class Probe(check_base.Check):
        id = "probe_dummy"
        title = "t"
        label = "Probe"
        mode = check_base.Mode.BOTH

        def run(self, ctx):
            seen["failed"] = dict(ctx.baseline_failed_collectors)
            seen["measured"] = ctx.baseline_measured("arp")
            return [Finding(Outcome.OK, "x", value="v", baseline_value="v")]

    monkeypatch.setattr("migration_validator.engine.all_checks", lambda: [Probe()])
    baseline = _old()
    baseline.capture.collectors["arp"] = {"status": "error", "message": "RpcError: timeout"}
    api.evaluate(_new(), baseline=baseline, now=NOW)
    assert seen["failed"] == {"arp": "RpcError: timeout"}
    assert seen["measured"] is False
```

- [ ] **Step 2: Ověř pád** — `pyats-venv/bin/python -m pytest tests/checks/test_base.py tests/test_engine.py -q -k baseline_measured or baseline_failed` → FAIL (`TypeError: unexpected keyword`).

- [ ] **Step 3: Implementace**

`checks/base.py` `CheckContext` — za `link`:

```python
    # Collectory, ktere v BASELINE selhaly. Bez toho by "chybi v obou"
    # nesel odlisit od "baseline to nezmerila" a UNCHANGED (R-3) by
    # schoval chybu migrace za selhany collector stare krabice.
    baseline_failed_collectors: dict[str, str] = field(default_factory=dict)

    def baseline_measured(self, area: str) -> bool:
        """Baseline existuje a collector oblasti v ni probehl. Jedina
        brana pro Outcome.UNCHANGED - stav se nefabuluje."""
        return self.has_baseline and area not in self.baseline_failed_collectors
```

`engine._run_scope`: do `CheckContext(...)` přidej `baseline_failed_collectors=(baseline.capture.failed_collectors() if baseline_data is not None and baseline is not None else {})`.

- [ ] **Step 4: Testy** — `pyats-venv/bin/python -m pytest tests/checks/test_base.py tests/test_engine.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/base.py migration_validator/engine.py tests/checks/test_base.py tests/test_engine.py
git commit -m "feat(checks): CheckContext zna selhane collectory baseline (baseline_measured)"
```

---

## Task 6: Překlíčování všech per-interface oblastí baseline (R-6)

**Files:**
- Modify: `migration_validator/engine.py:56-120` (`_aligned_baseline_data`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Produces: v aligned baseline datech jsou klíče slovníků `isis_adjacency`, `isis_interface`, `ldp_neighbor`, `pim_neighbor`, `mpls_interface`, `igmp_group` a pole `interface` v záznamech `arp`, `nd`, `bfd` (hodnoty slovníku podle peeru), `evpn_esi` přejmenované na jména subjektu.

- [ ] **Step 1: Failing test**

`tests/test_engine.py` — na konec (`_aligned_baseline_data` importuj z `migration_validator.engine`):

```python
def test_aligned_baseline_renames_protocol_areas_and_interface_fields():
    from migration_validator.engine import _aligned_baseline_data

    old = _scope("svc:CORE:Core", "CORE", "Core", "ge-0/0/1.0")
    new = _scope("svc:CORE:Core", "CORE", "Core", "et-0/0/1.0")
    baseline = _snapshot("172.20.20.4", "ge-0/0/1.0", [old])
    baseline.facts.update({
        "isis_adjacency": {"ge-0/0/1.0": {"state": "Up"}},
        "isis_interface": {"ge-0/0/1.0": {"levels": {}}},
        "ldp_neighbor": {"ge-0/0/1.0": {"uptime_seconds": 5}},
        "pim_neighbor": {"ge-0/0/1.0": {"uptime_seconds": 5}},
        "mpls_interface": {"ge-0/0/1.0": {"state": "Up"}},
        "igmp_group": {"ge-0/0/1.0": [{"source": "10.0.0.1", "group": "232.1.1.1"}]},
        "bfd": {"10.1.0.5": {"state": "Up", "interface": "ge-0/0/1.0"}},
        "evpn_esi": {"00:11": {"interface": "ge-0/0/1.0", "status": "Resolved"}},
    })
    baseline.capture.collectors.update(
        {name: {"status": "ok"} for name in ("isis_adjacency", "isis_interface",
         "ldp_neighbor", "pim_neighbor", "mpls_interface", "igmp_group", "bfd", "evpn_esi")}
    )

    data = _aligned_baseline_data(old, new, baseline)

    for area in ("isis_adjacency", "isis_interface", "ldp_neighbor",
                 "pim_neighbor", "mpls_interface", "igmp_group"):
        assert list(data[area]) == ["et-0/0/1.0"], area
    assert data["arp"][0]["interface"] == "et-0/0/1.0"
    assert data["nd"][0]["interface"] == "et-0/0/1.0"
    assert data["evpn_esi"]["00:11"]["interface"] == "et-0/0/1.0"
```

Pozn.: `bfd` se v `Scope.select` vybírá podle `bgp_neighbors` nebo Core transit podle rozhraní — pro test se `_scope` helperem (IPVPN-like key) session nevybere; to je v pořádku, `bfd` přejmenování pokryje test ve vlně 2 u `bfd_transit_state`. Přesto `bfd` do rename zahrň.

- [ ] **Step 2: Ověř pád** — `pyats-venv/bin/python -m pytest tests/test_engine.py -q -k protocol_areas` → FAIL.

- [ ] **Step 3: Implementace**

Za blok `optics` v `_aligned_baseline_data` přidej:

```python
    if rename:
        # R-6 (spec 2026-09-08): puvodni duvod mappingu. Bez toho R-3
        # (UNCHANGED) na migraci stary -> novy box nikdy nenajde baseline
        # zaznam a LDP/PIM/IS-IS radky sviti "bez baseline".
        for area in _RENAMED_KEY_AREAS:
            if data.get(area):
                data[area] = {rename.get(k, k): v for k, v in data[area].items()}
        for area in _RENAMED_FIELD_LIST_AREAS:
            if data.get(area):
                data[area] = [
                    {**entry, "interface": rename.get(entry.get("interface"), entry.get("interface"))}
                    for entry in data[area]
                ]
        for area in _RENAMED_FIELD_DICT_AREAS:
            if data.get(area):
                data[area] = {
                    key: {**entry, "interface": rename.get(entry.get("interface"), entry.get("interface"))}
                    for key, entry in data[area].items()
                }
    return data
```

a konstanty na úrovni modulu:

```python
_RENAMED_KEY_AREAS = (
    "isis_adjacency", "isis_interface", "ldp_neighbor", "pim_neighbor",
    "mpls_interface", "igmp_group",
)
_RENAMED_FIELD_LIST_AREAS = ("arp", "nd")
_RENAMED_FIELD_DICT_AREAS = ("bfd", "evpn_esi")
```

Docstring funkce: doplň odstavec o R-6 a o tom, že `pim_join`/`multicast_route` se nepřejmenovávají (jména rozhraní jen v hodnotách, které se neporovnávají).

- [ ] **Step 4: Testy** — `pyats-venv/bin/python -m pytest tests/test_engine.py tests/test_end_to_end.py -q` → PASS.

- [ ] **Step 5: Docs** — `docs/cs/files/top-level.md` (sekce engine, `_aligned_baseline_data`): vyjmenuj všechny přejmenovávané oblasti.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/engine.py tests/test_engine.py docs/cs/files/top-level.md
git commit -m "feat(engine): baseline preklicovani pro vsechny per-interface oblasti (R-6)"
```

---

## Task 7: `excluded_subtypes` — ping/ARP/ND mimo multicast (R-7)

**Files:**
- Modify: `migration_validator/models/scope.py` (nová konstanta `MULTICAST_SUBTYPES`), `migration_validator/checks/multicast.py:23-25` (import místo definice), `migration_validator/checks/base.py:58-100` (ClassVar + applies_to + describe), `migration_validator/checks/reachability.py:81-90, 152-161, 231-240`, `migration_validator/probes/ping.py:224-226`, `migration_validator/profiles/catalogue.py:20-30`
- Test: `tests/checks/test_base.py`, `tests/checks/test_reachability.py`, `tests/probes/test_ping.py`

**Interfaces:**
- Produces: `MULTICAST_SUBTYPES = frozenset({"multicast", "mvpn"})` v `models/scope.py`; `Check.excluded_subtypes: ClassVar[frozenset[str] | None] = None`; `describe()["excluded_subtypes"]: list[str] | None`; `resolve_targets` přeskočí scope se `service_subtype in MULTICAST_SUBTYPES`.

- [ ] **Step 1: Failing testy**

`tests/checks/test_base.py`:

```python
def test_check_with_excluded_subtypes_skips_that_subtype():
    class NoMulticast(DummyCheck):
        service_types = frozenset({"Internet", "IPVPN"})
        excluded_subtypes = frozenset({"multicast", "mvpn"})

    def _typed(service_type, subtype):
        return Scope(id="svc:x", kind="service", key=ScopeKey("x", service_type, subtype),
                     selectors=Selectors(interfaces=["ge-0/0/1.0"]))

    check = NoMulticast()
    assert check.applies_to(_typed("Internet", None)) is True
    assert check.applies_to(_typed("Internet", "multicast")) is False
    assert check.applies_to(_typed("IPVPN", "mvpn")) is False
    assert check.describe()["excluded_subtypes"] == ["multicast", "mvpn"]
```

`tests/checks/test_reachability.py` (helper `_ctx(subject, service_type, scope)` už existuje):

```python
import pytest
from migration_validator.checks.reachability import (
    ArpPresentCheck, NdPresentCheck, PingReachabilityCheck,
)


@pytest.mark.parametrize("check_class", [ArpPresentCheck, NdPresentCheck, PingReachabilityCheck])
@pytest.mark.parametrize("service_type,subtype", [("Internet", "multicast"), ("IPVPN", "mvpn")])
def test_reachability_checks_do_not_apply_to_multicast_services(check_class, service_type, subtype):
    scope = Scope(id="svc:M:" + service_type, kind="service",
                  key=ScopeKey("M", service_type, subtype),
                  selectors=Selectors(interfaces=["ge-0/0/2.11"], local_ipv4=["10.1.1.1/30"]))
    assert check_class().applies_to(scope) is False
    assert run_check(check_class(), _ctx({"arp": [], "nd": [], "ping": []}, scope=scope)) == []
```

`tests/probes/test_ping.py`:

```python
def test_resolve_targets_skips_multicast_subtypes():
    scope = _scope()
    scope.key = ScopeKey("X", "Internet", "multicast")
    arp = [{"ip": "198.11.13.2", "interface": "ge-0/0/2.113"}]
    assert resolve_targets([scope], arp) == []
```

- [ ] **Step 2: Ověř pád** — `pyats-venv/bin/python -m pytest tests/checks/test_base.py tests/checks/test_reachability.py tests/probes/test_ping.py -q -k "excluded or multicast"` → FAIL.

- [ ] **Step 3: Implementace**

`models/scope.py` (nad `Selectors`):

```python
# Subtypy sluzeb Internet/IPVPN, ktere nesou multicast stream. Sdili je
# checks/multicast.py (kde se checky zapinaji) a checks/reachability.py +
# probes/ping.py (kde se ping/ARP/ND vypinaji, rozhodnuti 2026-09-08).
MULTICAST_SUBTYPES = frozenset({"multicast", "mvpn"})
```

`checks/multicast.py`: `from migration_validator.models.scope import MULTICAST_SUBTYPES, Scope` a smaž lokální definici.

`checks/base.py` `Check`: za `service_subtypes` přidej

```python
    # Subtypy, ktere check NEdostane, i kdyz service_types sedi. Ping/ARP/ND
    # na multicast sluzbe nic nemeri (rozhodnuti 2026-09-08).
    excluded_subtypes: ClassVar[frozenset[str] | None] = None
```

v `applies_to` za blok `service_subtypes`:

```python
        if self.excluded_subtypes is not None:
            if scope.service_subtype in self.excluded_subtypes:
                return False
```

v `describe()`: `"excluded_subtypes": sorted(self.excluded_subtypes) if self.excluded_subtypes else None`.

`checks/reachability.py`: u všech tří tříd `excluded_subtypes = MULTICAST_SUBTYPES` (import z `models.scope`).

`probes/ping.py` `resolve_targets`: `if scope.is_device or scope.service_type not in PING_SERVICE_TYPES or scope.service_subtype in MULTICAST_SUBTYPES: continue` s komentářem „multicast sluzba ping nema (R-7) - jinak by capture palil session na mereni, ktere check necte".

`profiles/catalogue.py` `_check_entry`: přidej `"excluded_subtypes": described.get("excluded_subtypes")`.

- [ ] **Step 4: Testy** — `pyats-venv/bin/python -m pytest -q` → PASS (celá suite; `tests/gui` katalog snapshoty aditivní klíč snesou — pokud některý porovnává přesný dict, doplň klíč do očekávání).

- [ ] **Step 5: Docs** — `docs/cs/reference.md` katalog: u `arp_present`, `nd_present`, `ping_reachability` doplň „mimo subtypy multicast/mvpn"; `docs/cs/files/checks.md` sekce `base.py`: odstavec o `excluded_subtypes`.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/models/scope.py migration_validator/checks/multicast.py migration_validator/checks/base.py migration_validator/checks/reachability.py migration_validator/probes/ping.py migration_validator/profiles/catalogue.py tests/ docs/cs
git commit -m "feat(checks): excluded_subtypes - ping/ARP/ND se na multicast sluzbach nemeri (R-7)"
```

---

## Task 8: Ověření v laborce (MX1-POP1, inet.2 statika s qualified-next-hop)

**Files:** žádné změny kódu; výstup do `runs/` přes GUI nebo CLI.

- [ ] **Step 1:** `eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"` a capture pre+post na `172.20.20.4` (viz `README.md`, capture s inventory MX1-POP1).
- [ ] **Step 2:** `evaluate --snapshot <post> --baseline <pre>`; v bloku Core lo0.0 ověř: `Upstream interface` u `(10.11.11.1, 232.1.1.1)` je PASS (upstream `ge-0/0/0.0` je mezi via `ge-0/0/0.0, ge-0/0/1.0`), `Staticke routy inet.2 10.11.11.1/32` je PASS s next-hop hodnotou, ne „neni aktivni".
- [ ] **Step 3:** U Internet multicast služby (ge-0/0/2.11) nejsou řádky ARP/ND/Ping; u ostatních Internet/IPVPN služeb zůstaly.
- [ ] **Step 4:** Zapiš výsledek do spec sekce „Ověření" (datum, co sedělo, co ne). Commit `docs(spec): overeni vlny 1 v laborce`.
