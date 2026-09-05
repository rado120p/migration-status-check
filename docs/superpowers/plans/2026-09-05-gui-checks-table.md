# GUI checks table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The profile editor (spec 3) gets an editable table of every registered check — enable toggle, severity, typed options — that always shows defaults, marks overrides, and saves only what differs from the defaults; a live YAML preview shows the exact file Save will write.

**Architecture:** The loader (`config.load_profile`) learns to validate the `checks:` section (option types from `DEFAULTS`, severity from the enum, `enabled` is a bool) so bad values come back as `422` with the check id and key in the message. The store's `document_to_yaml` canonicalises overrides (`strip_check_defaults`) so the file never carries a value equal to its default no matter what the client sends. On the client a new pure module `static/profile_diff.js` (tested with `node --test`) owns the form-state ↔ overrides diff (`checksForm`, `checksDocument`, `isOverride`, `groupChecks`, `errorTarget`); `app.js` renders the table, the preview panel and inline errors; `style.css` styles them. The old read-only "Registered checks" listing in the editor is replaced.

**Tech Stack:** Python 3.13, FastAPI + TestClient, pytest, PyYAML, vanilla JS + CSS (no build step), `node --test` (Node 22) for JS logic.

**Spec:** `docs/superpowers/specs/2026-09-04-gui-checks-table-design.md` (spec 4 of the 2026-09-04 GUI brainstorm; builds on spec 3 `docs/superpowers/specs/2026-09-04-gui-profile-store-design.md`, implemented by `docs/superpowers/plans/2026-09-05-gui-profile-store.md`).

## Global Constraints

- Tool strings (API `detail`, exceptions) are Czech **without diacritics**, like the rest of the code base. GUI copy in `GUIDE_TEXT` is Czech with diacritics; button and link labels are short English (`reset`, `remove`, `Save profile`). Table copy required by the spec, exactly: `bez voleb` (grey, check without options), `neznamy check` (group header for unknown ids), `<n> overrides` under the preview (e.g. `2 overrides`, `0 overrides`).
- Severity values are only `critical` / `advisory` (`models.result.Severity`). The select offers exactly these two.
- Loader messages for `checks:` follow the existing `"<path>: ..."` shape so `ProfileStore.save` can swap the temp path for `profiles/<name>.yml`. Exact texts:
  - `<path>: sekce checks: ocekavan mapping, nalezeno <type>`
  - `<path>: check '<id>': ocekavan mapping, nalezeno <type>`
  - `<path>: check '<id>': severity '<value>' neni platna (zname: advisory, critical)`
  - `<path>: check '<id>': volba '<key>' ocekava <boolean|number|string>, nalezeno <type>` (`enabled` is reported as `volba 'enabled' ocekava boolean`)
- **Unknown check ids and unknown option keys are not loader errors.** The GUI shows them in a `neznamy check` group / read-only with a `remove` link so a file can be cleaned without a text editor (spec §2); rejecting them would make such a profile unloadable.
- **Deliberate reading of the spec:** the route tests in the spec ("a severity equal to the default is not written; an option equal to its default is not written") only mean something if the **server** also strips defaults. So `document_to_yaml` canonicalises through `strip_check_defaults` (mirror of the JS `checksDocument`). `GET /api/profiles/{name}` therefore always returns the canonical overrides.
- **Deliberate addition:** the registry has `title` but no description field, so option helper text comes from a small `OPTION_HINTS` table in `profile_diff.js` (text taken from `docs/cs/reference.md`'s config section: negative `tolerance_percent` = allowed drop). Do not invent hints for options not listed there.
- Number inputs have no `min`/`max` (spec §4: no client-side clamping). An emptied numeric field means "back to default", never `null`.
- Permission levels unchanged: `GET /api/profiles/catalogue` needs `view`, `POST /api/profiles/preview` and `PUT` need `admin`.
- No new Python or JS dependencies, no build step. `profile_diff.js` follows the `view.js` pattern: plain functions, a `MigDiff` object, `module.exports` guarded by `typeof module !== "undefined"`.
- Test commands: `pyats-venv/bin/pytest -o addopts="" -q` (main is at **1562 passed / 1 skipped**) and `node --test 'tests/js/*.test.js'` (**31 tests**; the quoted glob is required — `node --test tests/js/` fails on Node 22.22 with `MODULE_NOT_FOUND`). Run both before every commit.
- Commit messages in repo style: `feat(config): ...`, `feat(profiles): ...`, `feat(gui): ...`, `test(...)`, `docs: ...`, Czech without diacritics, ending with the `Co-Authored-By` and `Claude-Session` trailers given by the session.
- Work on branch `gui-checks-table` off `main` (create it in Task 1, Step 0).

---

## File structure

| File | Responsibility |
|---|---|
| `migration_validator/config.py` | `option_type`, `option_matches_type`, `_validate_checks` used by `load_profile` |
| `migration_validator/profiles/catalogue.py` | imports `option_type` from config (no local copy) |
| `migration_validator/profiles/store.py` | `strip_check_defaults`, used by `document_to_yaml` |
| `migration_validator/gui/static/profile_diff.js` | pure diff/grouping helpers (`MigDiff`) |
| `migration_validator/gui/static/index.html` | loads `profile_diff.js` before `app.js` |
| `migration_validator/gui/static/app.js` | checks table, YAML preview, inline save errors; removes `loadChecks`/registry listing from the editor |
| `migration_validator/gui/static/style.css` | `.checks-layout`, `.checks-table*`, `.yaml-preview*`; removes `.checks-registry-*` |
| `tests/test_profile.py` | loader `checks:` validation |
| `tests/test_profile_store.py` | `strip_check_defaults` / `document_to_yaml` |
| `tests/gui/test_profile_routes.py` | preview + save behaviour from the spec's Testing section |
| `tests/js/profile_diff.test.js` | `MigDiff` unit tests |
| `docs/cs/reference.md`, `docs/en/reference.md` | profiles section: checks validation + canonical file |

---

### Task 1: Loader validates the `checks:` section

**Files:**
- Modify: `migration_validator/config.py` (after `PING_COUNT_DEFAULT`, and inside `load_profile`)
- Modify: `migration_validator/profiles/catalogue.py` (drop local `option_type`, import it)
- Test: `tests/test_profile.py`

**Interfaces:**
- Produces: `config.option_type(value: Any) -> str` (`"boolean" | "number" | "string"`), `config.option_matches_type(value: Any, expected: str) -> bool`, `config._validate_checks(raw: Any, path) -> dict[str, dict[str, Any]]`. `load_profile` now raises `ValueError` for bad `checks:` values with the exact messages listed in Global Constraints. `CheckConfig.raw` keeps unknown checks/keys verbatim.

- [ ] **Step 0: Create the branch**

```bash
git checkout -b gui-checks-table main
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_profile.py`:

```python
# -- sekce checks: typy podle DEFAULTS, severity z enumu, enabled bool -----

def test_checks_number_jako_string_je_chyba_se_jmenem_checku_a_klice(tmp_path):
    path = write(tmp_path, "checks:\n  interface_traffic:\n    tolerance_percent: '-40'\n")
    with pytest.raises(ValueError) as info:
        load_profile(path)
    assert str(info.value) == (
        f"{path}: check 'interface_traffic': volba 'tolerance_percent' "
        "ocekava number, nalezeno str"
    )


def test_checks_boolean_jako_number_je_chyba(tmp_path):
    path = write(tmp_path, "checks:\n  interface_traffic:\n    require_nonzero: 1\n")
    with pytest.raises(ValueError, match="volba 'require_nonzero' ocekava boolean, nalezeno int"):
        load_profile(path)


def test_checks_float_a_int_jsou_oboji_number(tmp_path):
    path = write(
        tmp_path,
        "checks:\n"
        "  interface_traffic:\n    tolerance_percent: -40.5\n"
        "  interface_optics_levels:\n    tolerance_db: 3\n",
    )
    profile = load_profile(path)
    assert profile.checks.options("interface_traffic")["tolerance_percent"] == -40.5
    assert profile.checks.options("interface_optics_levels")["tolerance_db"] == 3


def test_checks_neplatna_severity_je_chyba(tmp_path):
    path = write(tmp_path, "checks:\n  bgp_prefix_counts:\n    severity: warning\n")
    with pytest.raises(ValueError) as info:
        load_profile(path)
    assert str(info.value) == (
        f"{path}: check 'bgp_prefix_counts': severity 'warning' neni platna "
        "(zname: advisory, critical)"
    )


def test_checks_platna_severity_projde(tmp_path):
    path = write(tmp_path, "checks:\n  bgp_prefix_counts:\n    severity: critical\n")
    profile = load_profile(path)
    assert profile.checks.severity("bgp_prefix_counts", Severity.ADVISORY) == Severity.CRITICAL


def test_checks_enabled_musi_byt_bool(tmp_path):
    path = write(tmp_path, "checks:\n  interface_state:\n    enabled: 'false'\n")
    with pytest.raises(ValueError, match="check 'interface_state': volba 'enabled' ocekava boolean, nalezeno str"):
        load_profile(path)


def test_checks_neznamy_check_a_neznama_volba_nejsou_chyba(tmp_path):
    # GUI je ukaze jako "neznamy check" / read-only k odebrani; loader je
    # nesmi odmitnout, jinak by soubor nesel vycistit bez editoru.
    path = write(
        tmp_path,
        "checks:\n"
        "  old_check:\n    enabled: false\n"
        "  interface_traffic:\n    foo: bar\n",
    )
    profile = load_profile(path)
    assert profile.checks.raw == {
        "old_check": {"enabled": False},
        "interface_traffic": {"foo": "bar"},
    }


def test_checks_prazdny_check_je_prazdny_mapping(tmp_path):
    path = write(tmp_path, "checks:\n  interface_state:\n")
    profile = load_profile(path)
    assert profile.checks.raw == {"interface_state": {}}
    assert profile.checks.enabled("interface_state")


def test_checks_neni_mapping_je_chyba(tmp_path):
    path = write(tmp_path, "checks:\n  - interface_state\n")
    with pytest.raises(ValueError, match="sekce checks: ocekavan mapping, nalezeno list"):
        load_profile(path)
    path = write(tmp_path, "checks:\n  interface_state: [enabled]\n")
    with pytest.raises(ValueError, match="check 'interface_state': ocekavan mapping, nalezeno list"):
        load_profile(path)
```

Add `Severity` to the import at the top of the file:

```python
from migration_validator.config import Profile, default_profile, load_profile
from migration_validator.models.result import Severity
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pyats-venv/bin/pytest -o addopts="" -q tests/test_profile.py`
Expected: the six new `..._je_chyba` tests FAIL (`DID NOT RAISE`), `test_checks_prazdny_check_je_prazdny_mapping` fails on `raw == {"interface_state": {}}` (today it is `{"interface_state": None}`), the others pass.

- [ ] **Step 3: Implement validation in `config.py`**

Below `PING_COUNT_DEFAULT = 5` add:

```python
def option_type(value: Any) -> str:
    """Typ volby checku odvozeny z Python typu defaultu - sdileny katalogem
    (GUI formular) a validaci profilu."""
    # bool je podtrida int - musi byt prvni.
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def option_matches_type(value: Any, expected: str) -> bool:
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, str)


_SEVERITY_VALUES = sorted(severity.value for severity in Severity)


def _validate_checks(raw: Any, path: str | Path) -> dict[str, dict[str, Any]]:
    """Sekce checks: volby z DEFAULTS musi mit typ defaultu, severity je
    z enumu, enabled je bool. Neznamy check ani neznama volba chybou
    nejsou - GUI je ukaze jako 'neznamy check' / read-only k odebrani a
    soubor tak jde vycistit; loader ho nesmi odmitnout."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path}: sekce checks: ocekavan mapping, nalezeno {type(raw).__name__}"
        )
    checks: dict[str, dict[str, Any]] = {}
    for check_id, overrides in raw.items():
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, dict):
            raise ValueError(
                f"{path}: check '{check_id}': ocekavan mapping, "
                f"nalezeno {type(overrides).__name__}"
            )
        defaults = DEFAULTS.get(check_id, {})
        for key, value in overrides.items():
            if key == "severity":
                if not isinstance(value, str) or value not in _SEVERITY_VALUES:
                    raise ValueError(
                        f"{path}: check '{check_id}': severity '{value}' neni platna "
                        f"(zname: {', '.join(_SEVERITY_VALUES)})"
                    )
                continue
            expected = "boolean" if key == "enabled" else (
                option_type(defaults[key]) if key in defaults else None
            )
            if expected is not None and not option_matches_type(value, expected):
                raise ValueError(
                    f"{path}: check '{check_id}': volba '{key}' ocekava {expected}, "
                    f"nalezeno {type(value).__name__}"
                )
        checks[check_id] = dict(overrides)
    return checks
```

In `load_profile`, replace `checks=CheckConfig(raw.get("checks") or {}),` with:

```python
        checks=CheckConfig(_validate_checks(raw.get("checks"), path)),
```

Leave `load_config` (legacy `--config`) untouched — spec §4 talks about `load_profile` only.

- [ ] **Step 4: Point the catalogue at the shared `option_type`**

In `migration_validator/profiles/catalogue.py` delete the local `option_type` function and change the import line to:

```python
from migration_validator.config import DEFAULTS, PING_COUNT_DEFAULT, option_type
```

- [ ] **Step 5: Run the tests**

Run: `pyats-venv/bin/pytest -o addopts="" -q tests/test_profile.py tests/test_profile_store.py tests/gui/test_profile_routes.py`
Expected: all PASS.

- [ ] **Step 6: Full suite, then commit**

Run: `pyats-venv/bin/pytest -o addopts="" -q` → `1571 passed, 1 skipped`; `node --test 'tests/js/*.test.js'` → 31 pass.

```bash
git add migration_validator/config.py migration_validator/profiles/catalogue.py tests/test_profile.py
git commit -m "feat(config): load_profile validuje sekci checks - typy voleb, severity, enabled

Neznamy check a neznama volba zustavaji pruchozi (GUI je ukaze k odebrani).
option_type se stehuje z katalogu do config, katalog ho importuje.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015QxyDv8dVznMMrt9Rqipju"
```

---

### Task 2: Server canonicalises overrides (`strip_check_defaults`)

**Files:**
- Modify: `migration_validator/profiles/store.py` (imports, new function, `document_to_yaml`)
- Test: `tests/test_profile_store.py`, `tests/gui/test_profile_routes.py`

**Interfaces:**
- Consumes: `config.DEFAULTS`, `config.option_type`, `config.option_matches_type` (Task 1); `checks.all.load_all`, `checks.registry.all_checks` (existing).
- Produces: `store.strip_check_defaults(checks: dict[str, Any]) -> dict[str, dict[str, Any]]`; `document_to_yaml` output never contains a value equal to its default.

- [ ] **Step 1: Write the failing store tests**

Append to `tests/test_profile_store.py` (add `strip_check_defaults` to the existing `from migration_validator.profiles.store import (...)`):

```python
# -- strip_check_defaults: soubor nese jen to, co se lisi od defaultu ------

def test_strip_vyhodi_hodnoty_rovne_defaultu_a_prazdny_check():
    checks = {
        "interface_traffic": {"tolerance_percent": -60, "require_nonzero": True,
                              "severity": "advisory", "enabled": True},
        "bgp_prefix_counts": {"tolerance_percent": -5},
    }
    assert strip_check_defaults(checks) == {"bgp_prefix_counts": {"tolerance_percent": -5}}


def test_strip_enabled_false_zustava_jen_u_defaultne_zapnutych():
    assert strip_check_defaults({"interface_state": {"enabled": False}}) == {
        "interface_state": {"enabled": False},
    }
    # traffic_ceased je defaultne vypnuty: enabled: true je override,
    # enabled: false ne.
    assert strip_check_defaults({"traffic_ceased": {"enabled": True}}) == {
        "traffic_ceased": {"enabled": True},
    }
    assert strip_check_defaults({"traffic_ceased": {"enabled": False}}) == {}


def test_strip_severity_jina_nez_default_zustava():
    assert strip_check_defaults({"bgp_prefix_counts": {"severity": "critical"}}) == {
        "bgp_prefix_counts": {"severity": "critical"},
    }
    assert strip_check_defaults({"interface_state": {"severity": "critical"}}) == {}


def test_strip_necha_neznamy_check_a_neznamou_volbu():
    checks = {"old_check": {"enabled": False}, "interface_traffic": {"foo": 1}}
    assert strip_check_defaults(checks) == checks


def test_strip_necha_hodnotu_spatneho_typu_loaderu():
    # "-60" se rovna defaultu jen na pohled - stripnout ji by loaderu
    # sebralo chybu, kterou ma uzivatel videt.
    checks = {"interface_traffic": {"tolerance_percent": "-60", "require_nonzero": 1}}
    assert strip_check_defaults(checks) == checks


def test_document_to_yaml_pouziva_strip():
    doc = _doc()
    doc["checks"] = {
        "interface_traffic": {"tolerance_percent": -60, "require_nonzero": True},
        "interface_optics_levels": {"enabled": False, "tolerance_db": 2.0},
    }
    assert document_to_yaml(doc) == "checks:\n  interface_optics_levels:\n    enabled: false\n"
```

- [ ] **Step 2: Write the failing route tests**

Append to `tests/gui/test_profile_routes.py`:

```python
# -- preview / save: jen odchylky od defaultu (spec 4) -----------------------

def _preview(client, checks):
    doc = _doc()
    doc["checks"] = checks
    return client.post("/api/profiles/preview", json={"document": doc}).json()["yaml"]


def test_preview_bez_overrides_nema_sekci_checks(tmp_path):
    client = _client(tmp_path)
    assert _preview(client, {}) == ""
    assert _preview(client, {"interface_traffic": {"tolerance_percent": -60}}) == ""


def test_preview_enabled_false_pise_jen_enabled(tmp_path):
    client = _client(tmp_path)
    yaml_text = _preview(client, {"interface_optics_levels": {
        "enabled": False, "severity": "critical", "tolerance_db": 2.0,
    }})
    assert yaml_text == "checks:\n  interface_optics_levels:\n    enabled: false\n"


def test_preview_severity_rovna_defaultu_se_nepise(tmp_path):
    client = _client(tmp_path)
    assert _preview(client, {"bgp_prefix_counts": {"severity": "advisory"}}) == ""
    assert _preview(client, {"bgp_prefix_counts": {"severity": "critical"}}) == (
        "checks:\n  bgp_prefix_counts:\n    severity: critical\n"
    )


def test_preview_volba_rovna_defaultu_se_nepise(tmp_path):
    client = _client(tmp_path)
    yaml_text = _preview(client, {"interface_traffic": {
        "tolerance_percent": -40, "require_nonzero": True,
    }})
    assert yaml_text == "checks:\n  interface_traffic:\n    tolerance_percent: -40\n"


def test_put_string_misto_cisla_je_422_se_jmenem_checku_a_klice(tmp_path):
    client = _client(tmp_path)
    client.post("/api/profiles", json={"name": "p", "document": empty_document()})
    doc = _doc()
    doc["checks"] = {"interface_traffic": {"tolerance_percent": "-40"}}
    resp = client.put("/api/profiles/p", json={"document": doc})
    assert resp.status_code == 422
    assert resp.json()["detail"] == (
        f"{tmp_path / 'profiles' / 'p.yml'}: check 'interface_traffic': "
        "volba 'tolerance_percent' ocekava number, nalezeno str"
    )
    # soubor zustal prazdny (validace bezi na temp souboru)
    assert (tmp_path / "profiles" / "p.yml").read_text(encoding="utf-8") == ""


def test_get_po_save_vraci_kanonicke_overrides(tmp_path):
    client = _client(tmp_path)
    doc = _doc()
    doc["checks"] = {
        "interface_traffic": {"tolerance_percent": -60, "require_nonzero": False},
        "interface_state": {"enabled": True},
    }
    resp = client.post("/api/profiles", json={"name": "p", "document": doc})
    assert resp.status_code == 201
    assert resp.json()["document"]["checks"] == {"interface_traffic": {"require_nonzero": False}}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pyats-venv/bin/pytest -o addopts="" -q tests/test_profile_store.py tests/gui/test_profile_routes.py`
Expected: the new `strip_*` tests FAIL with `ImportError`/`NameError`; `test_document_to_yaml_pouziva_strip`, `test_preview_*`, `test_get_po_save_vraci_kanonicke_overrides` FAIL on content; `test_put_string_misto_cisla_je_422...` PASSES already (Task 1).

- [ ] **Step 4: Implement `strip_check_defaults`**

In `migration_validator/profiles/store.py` change the config import to:

```python
from migration_validator.config import (
    DEFAULTS,
    Profile,
    load_profile,
    option_matches_type,
    option_type,
)
```

Add above `document_to_yaml`:

```python
def _default_severities() -> dict[str, str]:
    # Pozdni import: registr checku se nacita az pri save/preview, ne pri
    # importu store (config nesmi tahat checky).
    from migration_validator.checks.all import load_all
    from migration_validator.checks.registry import all_checks

    load_all()
    return {check.id: check.default_severity.value for check in all_checks()}


def strip_check_defaults(checks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Z overrides vyhodi to, co se rovna defaultu (enabled, severity,
    volby z DEFAULTS); check bez rozdilu zmizi. Neznamy check, neznama
    volba a hodnota spatneho typu zustavaji beze zmeny - loader je bud
    pusti (GUI je ukaze k odebrani) nebo odmitne s hlaskou.
    Zrcadlo checksDocument v gui/static/profile_diff.js."""
    severities = _default_severities()
    result: dict[str, dict[str, Any]] = {}
    for check_id, overrides in (checks or {}).items():
        defaults = DEFAULTS.get(check_id, {})
        default_enabled = bool(defaults.get("enabled", True))
        kept: dict[str, Any] = {}
        for key, value in (overrides or {}).items():
            if value is None:
                continue
            if key == "enabled":
                if isinstance(value, bool) and value == default_enabled:
                    continue
            elif key == "severity":
                if value == severities.get(check_id):
                    continue
            elif key in defaults:
                expected = option_type(defaults[key])
                if option_matches_type(value, expected) and value == defaults[key]:
                    continue
            kept[key] = value
        if kept:
            result[check_id] = kept
    return result
```

Replace the checks loop in `document_to_yaml` (the `checks: dict[...] = {}` / `for check_id, overrides in ...` block) with:

```python
    checks = strip_check_defaults(document.get("checks") or {})
```

and extend its docstring with one line: `Overrides rovne defaultu se vynechaji (strip_check_defaults).`

- [ ] **Step 5: Run the tests**

Run: `pyats-venv/bin/pytest -o addopts="" -q tests/test_profile_store.py tests/gui/test_profile_routes.py tests/test_profile.py`
Expected: all PASS.

- [ ] **Step 6: Full suite, then commit**

Run: `pyats-venv/bin/pytest -o addopts="" -q` → `1583 passed, 1 skipped`; `node --test 'tests/js/*.test.js'` → 31 pass.

```bash
git add migration_validator/profiles/store.py tests/test_profile_store.py tests/gui/test_profile_routes.py
git commit -m "feat(profiles): document_to_yaml pise jen odchylky od defaultu (strip_check_defaults)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015QxyDv8dVznMMrt9Rqipju"
```

---

### Task 3: Pure diff module `profile_diff.js`

**Files:**
- Create: `migration_validator/gui/static/profile_diff.js`
- Modify: `migration_validator/gui/static/index.html:54-55`
- Test: `tests/js/profile_diff.test.js`

**Interfaces:**
- Consumes: catalogue shape from `GET /api/profiles/catalogue`: `checks: [{id, title, group, default_severity, default_enabled, options: {key: {type, default}}}]`.
- Produces global `MigDiff` with:
  - `SEVERITIES = ["critical", "advisory"]`
  - `OPTION_HINTS: {tolerance_percent, tolerance_db, max_residual_pps, count, require_nonzero}` → string
  - `defaultRow(entry) -> {enabled, severity, options: {key: default}, extra: {}}`
  - `checksForm(catalogue, overrides) -> {checks: {id: row}, unknown: {id: overrides}}` — effective values per known check; unknown ids kept verbatim; unknown keys of a known check land in `row.extra`.
  - `isOverride(entry, row) -> boolean`
  - `checksDocument(catalogue, form) -> overrides` — the `checks` object to put into the document.
  - `overrideCount(catalogue, form) -> number` — rows that differ (known overrides + unknown checks).
  - `groupChecks(catalogue) -> [{group, checks: [entry]}]` — groups in order of first appearance, entries in catalogue order.
  - `errorTarget(message, catalogue) -> {checkId, key} | null` — for inline errors.
  - Option values in a row: number → JS number or `""` (empty field = default), boolean → boolean.

- [ ] **Step 1: Write the failing tests**

Create `tests/js/profile_diff.test.js`:

```js
const test = require("node:test");
const assert = require("node:assert");
const MigDiff = require("../../migration_validator/gui/static/profile_diff.js");

const CATALOGUE = {
  checks: [
    { id: "interface_state", title: "Stav rozhrani", group: "interfaces",
      default_severity: "critical", default_enabled: true, options: {} },
    { id: "interface_traffic", title: "Datovost rozhrani", group: "interfaces",
      default_severity: "advisory", default_enabled: true,
      options: { tolerance_percent: { type: "number", default: -60 },
                 require_nonzero: { type: "boolean", default: true } } },
    { id: "bgp_prefix_counts", title: "Pocty BGP prefixu", group: "bgp",
      default_severity: "advisory", default_enabled: true,
      options: { tolerance_percent: { type: "number", default: -10 } } },
    { id: "traffic_ceased", title: "Utichnuti", group: "interfaces",
      default_severity: "advisory", default_enabled: false,
      options: { max_residual_pps: { type: "number", default: 1 } } },
  ],
};

test("defaultRow: enabled/severity/options from the catalogue entry", () => {
  assert.deepStrictEqual(MigDiff.defaultRow(CATALOGUE.checks[1]), {
    enabled: true, severity: "advisory",
    options: { tolerance_percent: -60, require_nonzero: true }, extra: {},
  });
  assert.strictEqual(MigDiff.defaultRow(CATALOGUE.checks[3]).enabled, false);
});

test("checksForm: no overrides -> every check at defaults, nothing unknown", () => {
  const form = MigDiff.checksForm(CATALOGUE, {});
  assert.deepStrictEqual(Object.keys(form.checks), CATALOGUE.checks.map((c) => c.id));
  assert.deepStrictEqual(form.checks.interface_traffic, MigDiff.defaultRow(CATALOGUE.checks[1]));
  assert.deepStrictEqual(form.unknown, {});
});

test("checksForm: overrides land on the row; unknown check and unknown key are preserved", () => {
  const form = MigDiff.checksForm(CATALOGUE, {
    interface_traffic: { tolerance_percent: -40, severity: "critical", foo: "x" },
    interface_state: { enabled: false },
    old_check: { enabled: false },
  });
  assert.deepStrictEqual(form.checks.interface_traffic, {
    enabled: true, severity: "critical",
    options: { tolerance_percent: -40, require_nonzero: true }, extra: { foo: "x" },
  });
  assert.strictEqual(form.checks.interface_state.enabled, false);
  assert.deepStrictEqual(form.unknown, { old_check: { enabled: false } });
});

test("checksDocument: nothing differs -> {}", () => {
  const form = MigDiff.checksForm(CATALOGUE, {});
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {});
  assert.strictEqual(MigDiff.overrideCount(CATALOGUE, form), 0);
});

test("checksDocument: enabled written only when it differs from default_enabled", () => {
  const form = MigDiff.checksForm(CATALOGUE, {});
  form.checks.interface_state.enabled = false;
  form.checks.traffic_ceased.enabled = true;
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {
    interface_state: { enabled: false },
    traffic_ceased: { enabled: true },
  });
  form.checks.traffic_ceased.enabled = false;
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), { interface_state: { enabled: false } });
});

test("checksDocument: severity equal to default omitted, different one written", () => {
  const form = MigDiff.checksForm(CATALOGUE, {});
  form.checks.bgp_prefix_counts.severity = "advisory";
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {});
  form.checks.bgp_prefix_counts.severity = "critical";
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), { bgp_prefix_counts: { severity: "critical" } });
});

test("checksDocument: option equal to default omitted; emptied numeric field drops the key", () => {
  const form = MigDiff.checksForm(CATALOGUE, { interface_traffic: { tolerance_percent: -40 } });
  form.checks.interface_traffic.options.require_nonzero = true;
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), { interface_traffic: { tolerance_percent: -40 } });
  form.checks.interface_traffic.options.tolerance_percent = "";
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {});
  form.checks.interface_traffic.options.tolerance_percent = -60;
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {});
});

test("checksDocument: unknown check preserved until removed; unknown key on known check preserved", () => {
  const form = MigDiff.checksForm(CATALOGUE, {
    old_check: { enabled: false }, interface_traffic: { foo: "x" },
  });
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {
    old_check: { enabled: false }, interface_traffic: { foo: "x" },
  });
  assert.strictEqual(MigDiff.overrideCount(CATALOGUE, form), 2);
  delete form.unknown.old_check;
  delete form.checks.interface_traffic.extra.foo;
  assert.deepStrictEqual(MigDiff.checksDocument(CATALOGUE, form), {});
});

test("isOverride: enable, severity or any option differing marks the row", () => {
  const entry = CATALOGUE.checks[1];
  assert.strictEqual(MigDiff.isOverride(entry, MigDiff.defaultRow(entry)), false);
  assert.strictEqual(MigDiff.isOverride(entry, { ...MigDiff.defaultRow(entry), enabled: false }), true);
  assert.strictEqual(MigDiff.isOverride(entry, { ...MigDiff.defaultRow(entry), severity: "critical" }), true);
  const row = MigDiff.defaultRow(entry);
  row.options.tolerance_percent = -40;
  assert.strictEqual(MigDiff.isOverride(entry, row), true);
  row.options.tolerance_percent = "";
  assert.strictEqual(MigDiff.isOverride(entry, row), false);
  const extra = MigDiff.defaultRow(entry);
  extra.extra.foo = 1;
  assert.strictEqual(MigDiff.isOverride(entry, extra), true);
});

test("groupChecks: groups in order of first appearance, rows in catalogue order", () => {
  assert.deepStrictEqual(
    MigDiff.groupChecks(CATALOGUE).map((g) => [g.group, g.checks.map((c) => c.id)]),
    [
      ["interfaces", ["interface_state", "interface_traffic", "traffic_ceased"]],
      ["bgp", ["bgp_prefix_counts"]],
    ]
  );
  assert.deepStrictEqual(MigDiff.groupChecks({ checks: [] }), []);
});

test("errorTarget: check id and option key found in a loader message; longest id wins", () => {
  const msg = "profiles/p.yml: check 'interface_traffic': volba 'tolerance_percent' ocekava number, nalezeno str";
  assert.deepStrictEqual(MigDiff.errorTarget(msg, CATALOGUE), { checkId: "interface_traffic", key: "tolerance_percent" });
  assert.deepStrictEqual(
    MigDiff.errorTarget("x: check 'interface_state': volba 'enabled' ocekava boolean, nalezeno str", CATALOGUE),
    { checkId: "interface_state", key: null }
  );
  assert.strictEqual(MigDiff.errorTarget("profiles/p.yml: neznamy collector 'iface'", CATALOGUE), null);
  assert.strictEqual(MigDiff.errorTarget(null, CATALOGUE), null);
});

test("OPTION_HINTS covers the catalogue's numeric tolerances", () => {
  for (const key of ["tolerance_percent", "tolerance_db", "max_residual_pps", "count", "require_nonzero"]) {
    assert.strictEqual(typeof MigDiff.OPTION_HINTS[key], "string");
  }
  assert.deepStrictEqual(MigDiff.SEVERITIES, ["critical", "advisory"]);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test 'tests/js/*.test.js'`
Expected: `profile_diff.test.js` fails with `Cannot find module`.

- [ ] **Step 3: Implement `profile_diff.js`**

Create `migration_validator/gui/static/profile_diff.js`:

```js
/* Checks table diff (spec 4). Form state holds the effective value of every
   catalogue check; the document holds only what differs from the catalogue
   defaults. checksDocument is the single place that decides what is written -
   the server mirrors it in profiles/store.py strip_check_defaults. */

const SEVERITIES = ["critical", "advisory"];

/* Helper text under option fields. The registry has no description field,
   so the sign convention lives here (see docs/cs/reference.md, config). */
const OPTION_HINTS = {
  tolerance_percent: "zaporne = povoleny pokles v % proti baseline",
  tolerance_db: "povoleny posun RX/TX v dB proti baseline",
  max_residual_pps: "zbytkovy provoz v pps, ktery jeste znamena 'utichlo'",
  count: "pocet pingu na jednu adresu",
  require_nonzero: "nulovy provoz je nalez i bez baseline",
};

function defaultRow(entry) {
  const options = {};
  for (const [key, spec] of Object.entries(entry.options || {})) options[key] = spec.default;
  return { enabled: !!entry.default_enabled, severity: entry.default_severity, options, extra: {} };
}

function checksForm(catalogue, overrides) {
  const form = { checks: {}, unknown: {} };
  const known = new Set();
  for (const entry of catalogue.checks || []) {
    known.add(entry.id);
    const row = defaultRow(entry);
    for (const [key, value] of Object.entries((overrides && overrides[entry.id]) || {})) {
      if (value === null || value === undefined) continue;
      if (key === "enabled") row.enabled = !!value;
      else if (key === "severity") row.severity = value;
      else if (key in row.options) row.options[key] = value;
      else row.extra[key] = value;
    }
    form.checks[entry.id] = row;
  }
  for (const [id, value] of Object.entries(overrides || {})) {
    if (!known.has(id) && value && Object.keys(value).length) form.unknown[id] = { ...value };
  }
  return form;
}

/* "" in a numeric field means "back to default". Numbers compare by value
   so 2 and 2.0 are equal. */
function optionDiffers(spec, value) {
  if (value === "" || value === null || value === undefined) return false;
  if (spec.type === "number") return Number(value) !== Number(spec.default);
  return value !== spec.default;
}

function rowOverrides(entry, row) {
  const out = {};
  if (row.enabled !== !!entry.default_enabled) out.enabled = row.enabled;
  if (row.severity !== entry.default_severity) out.severity = row.severity;
  for (const [key, spec] of Object.entries(entry.options || {})) {
    const value = row.options[key];
    if (optionDiffers(spec, value)) out[key] = spec.type === "number" ? Number(value) : value;
  }
  for (const [key, value] of Object.entries(row.extra || {})) out[key] = value;
  return out;
}

function isOverride(entry, row) {
  return Object.keys(rowOverrides(entry, row)).length > 0;
}

function checksDocument(catalogue, form) {
  const out = {};
  for (const entry of catalogue.checks || []) {
    const row = form.checks[entry.id];
    if (!row) continue;
    const overrides = rowOverrides(entry, row);
    if (Object.keys(overrides).length) out[entry.id] = overrides;
  }
  for (const [id, value] of Object.entries(form.unknown || {})) {
    if (value && Object.keys(value).length) out[id] = { ...value };
  }
  return out;
}

function overrideCount(catalogue, form) {
  return Object.keys(checksDocument(catalogue, form)).length;
}

function groupChecks(catalogue) {
  const groups = [];
  const byName = new Map();
  for (const entry of catalogue.checks || []) {
    let group = byName.get(entry.group);
    if (!group) {
      group = { group: entry.group, checks: [] };
      byName.set(entry.group, group);
      groups.push(group);
    }
    group.checks.push(entry);
  }
  return groups;
}

/* Loader messages quote the check id and the option key in single quotes;
   the longest catalogue id contained in the message wins (interface_state
   vs interface_state_x). */
function errorTarget(message, catalogue) {
  if (!message) return null;
  let entry = null;
  for (const candidate of catalogue.checks || []) {
    if (message.includes(`'${candidate.id}'`) && (!entry || candidate.id.length > entry.id.length)) entry = candidate;
  }
  if (!entry) return null;
  let key = null;
  for (const candidate of Object.keys(entry.options || {})) {
    if (message.includes(`'${candidate}'`)) key = candidate;
  }
  return { checkId: entry.id, key };
}

const MigDiff = {
  SEVERITIES,
  OPTION_HINTS,
  defaultRow,
  checksForm,
  isOverride,
  checksDocument,
  overrideCount,
  groupChecks,
  errorTarget,
};

if (typeof module !== "undefined" && module.exports) module.exports = MigDiff;
```

- [ ] **Step 4: Load it in the page**

In `migration_validator/gui/static/index.html` change the script tags to:

```html
<script src="/static/view.js"></script>
<script src="/static/profile_diff.js"></script>
<script src="/static/app.js"></script>
```

- [ ] **Step 5: Run the tests**

Run: `node --test 'tests/js/*.test.js'`
Expected: 43 tests, all pass (31 + 12).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui/static/profile_diff.js migration_validator/gui/static/index.html tests/js/profile_diff.test.js
git commit -m "feat(gui): profile_diff.js - form state checku vs overrides, grouping, cil chyby

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015QxyDv8dVznMMrt9Rqipju"
```

---

### Task 4: Checks table in the editor

**Files:**
- Modify: `migration_validator/gui/static/app.js` — `GUIDE_TEXT.profiles` (~line 78), `cache` init (~134), `goToProfiles` (~857-866), `loadProfile` body (~868-891), `saveProfile` (~954-980), `discardProfile` (~982-988), `loadChecks` (~990-1006, delete), `buildChecksRegistry` (~3063-3122, replace), `renderProfilesView` (~3124-3179)
- Modify: `migration_validator/gui/static/style.css` — replace the `.checks-registry-*` block (lines 556-603), add table styles
- Test: manual in browser (no DOM tests in this repo); `node --test` and pytest must stay green

**Interfaces:**
- Consumes: `MigDiff.*` from Task 3; `editor` state from spec 3: `{name, doc, saved, saving, error, loadError}`; `this.cache.catalogue`.
- Produces: `editor.form` (`MigDiff.checksForm(catalogue, doc.checks)`), rebuilt whenever `editor.doc` is replaced (load, save, discard); every table edit calls `this.applyChecksForm()` which sets `editor.doc.checks = MigDiff.checksDocument(catalogue, editor.form)` and re-renders. `buildChecksTable(editor, readonly)` returns the table element. Task 5 adds the preview beside it via `buildChecksSection`.

- [ ] **Step 1: Keep `editor.form` in sync with the document**

In `goToProfiles` change the editor init line to include `form: null`:

```js
    this.state.profileEditor = { name, doc: null, saved: null, saving: false, error: null, errorTarget: null, loadError: null, form: null };
```

and drop `this.loadChecks()` from the `Promise.all` (the registry listing goes away):

```js
    await Promise.all([this.loadProfiles(), this.loadCatalogue()]);
```

Add a helper right after `editorDirty()`:

```js
  // editor.form is the table's state (effective value of every catalogue
  // check); editor.doc.checks is what gets saved. Rebuild the form whenever
  // the document is replaced, and rebuild the document after every edit.
  resetChecksForm(editor) {
    const catalogue = this.cache.catalogue || { checks: [] };
    editor.form = editor.doc ? MigDiff.checksForm(catalogue, editor.doc.checks || {}) : null;
  }

  applyChecksForm() {
    const editor = this.state.profileEditor;
    if (!editor || !editor.doc || !editor.form) return;
    const catalogue = this.cache.catalogue || { checks: [] };
    editor.doc.checks = MigDiff.checksDocument(catalogue, editor.form);
    editor.errorTarget = null;
    this.render();
  }
```

Then call `this.resetChecksForm(editor)` at each place `editor.doc` is assigned. Read `loadProfile` (around line 868) and add the call after both assignments (the `(default)` branch and the fetched-profile branch), e.g.:

```js
      editor.doc = JSON.parse(JSON.stringify(doc));
      editor.saved = JSON.parse(JSON.stringify(doc));
      this.resetChecksForm(editor);
```

In `saveProfile`, after `editor.saved = JSON.parse(JSON.stringify(document));` add `this.resetChecksForm(editor);`. In `discardProfile`, after `editor.doc = JSON.parse(JSON.stringify(editor.saved));` add `editor.errorTarget = null; this.resetChecksForm(editor);`.

If `loadCatalogue` can finish after `loadProfile` (they run in `Promise.all`), the form built without a catalogue would be empty. Guard it: at the end of `goToProfiles`, after the `await Promise.all(...)` and the existing `await this.loadProfile()` (or whatever loads the editor document — read the function), add `this.resetChecksForm(this.state.profileEditor);` before the final `this.render()`.

- [ ] **Step 2: Remove the registry listing and its loader**

Delete `loadChecks()` (~lines 990-1006), the `checks: null, checksError: null` cache entries (~line 133-134; keep the object valid) and the whole `buildChecksRegistry()` method with its leading comment. Run `grep -n "cache.checks\|loadChecks\|buildChecksRegistry\|checksError" migration_validator/gui/static/app.js` — it must print nothing.

- [ ] **Step 3: Add `buildChecksTable`**

Insert where `buildChecksRegistry` was:

```js
  // -- checks table (spec 4) ---------------------------------------------

  buildChecksTable(editor, readonly) {
    const catalogue = this.cache.catalogue;
    if (!catalogue) {
      return el("div", { className: "notice notice-warn", text: this.cache.catalogueError || "katalog checků se načítá…" });
    }
    const form = editor.form;
    const target = editor.errorTarget;
    const table = el("div", { className: "checks-table" });
    table.appendChild(
      el("div", {
        className: "checks-table-header",
        children: [
          el("span", { text: "" }),
          el("span", { text: "Check" }),
          el("span", { text: "Severity" }),
          el("span", { text: "Options" }),
          el("span", { text: "" }),
        ],
      })
    );
    for (const group of MigDiff.groupChecks(catalogue)) {
      table.appendChild(el("div", { className: "checks-group mono", text: group.group }));
      for (const entry of group.checks) {
        table.appendChild(this.buildCheckRow(entry, form.checks[entry.id], readonly,
          target && target.checkId === entry.id ? target : null, editor.error));
      }
    }
    const unknownIds = Object.keys(form.unknown);
    if (unknownIds.length) {
      table.appendChild(el("div", { className: "checks-group mono unknown", text: "neznamy check" }));
      for (const id of unknownIds) table.appendChild(this.buildUnknownCheckRow(id, form.unknown[id], readonly));
    }
    return table;
  }

  buildCheckRow(entry, row, readonly, errorTarget, errorText) {
    const override = MigDiff.isOverride(entry, row);
    const toggle = el("input", { attrs: { type: "checkbox", title: row.enabled ? "check běží" : "check vypnutý" } });
    toggle.checked = row.enabled;
    if (readonly) toggle.setAttribute("disabled", "disabled");
    toggle.addEventListener("change", (e) => { row.enabled = e.target.checked; this.applyChecksForm(); });

    const severity = el("select", { className: "form-select mono severity-select" });
    for (const value of MigDiff.SEVERITIES) severity.appendChild(el("option", { text: value, attrs: { value } }));
    severity.value = row.severity;
    severity.classList.toggle("is-default", row.severity === entry.default_severity);
    if (readonly) severity.setAttribute("disabled", "disabled");
    severity.addEventListener("change", (e) => { row.severity = e.target.value; this.applyChecksForm(); });

    const options = el("div", { className: "check-options" });
    const optionKeys = Object.keys(entry.options || {});
    if (!optionKeys.length && !Object.keys(row.extra).length) {
      options.appendChild(el("span", { className: "check-no-options", text: "bez voleb" }));
    }
    for (const key of optionKeys) {
      options.appendChild(this.buildOptionField(entry, row, key, readonly, errorTarget && errorTarget.key === key));
    }
    for (const [key, value] of Object.entries(row.extra)) {
      const field = el("div", { className: "check-option unknown", children: [
        el("label", { className: "option-label mono", text: key }),
        el("span", { className: "option-value mono", text: JSON.stringify(value) }),
      ] });
      if (!readonly) {
        field.appendChild(el("button", { className: "link-btn", text: "remove", attrs: { type: "button" },
          onClick: () => { delete row.extra[key]; this.applyChecksForm(); } }));
      }
      options.appendChild(field);
    }

    const reset = el("button", { className: "link-btn", text: "reset", attrs: { type: "button", title: "vrátit enable, severity i volby na default" },
      onClick: () => { Object.assign(row, MigDiff.defaultRow(entry)); this.applyChecksForm(); } });
    if (readonly || !override) reset.setAttribute("disabled", "disabled");

    const idCell = el("div", { className: "check-id-cell", children: [
      el("span", { className: "check-id mono", text: entry.id }),
      override ? el("span", { className: "override-dot", text: " ●", attrs: { title: "liší se od defaultu" } }) : null,
      el("div", { className: "check-title", text: entry.title }),
    ] });
    const rowEl = el("div", {
      className: "checks-row" + (override ? " override" : "") + (row.enabled ? "" : " disabled"),
      children: [toggle, idCell, severity, options, reset],
    });
    if (errorTarget) {
      rowEl.classList.add("has-error");
      rowEl.appendChild(el("div", { className: "field-error row-error", text: errorText }));
    }
    return rowEl;
  }

  buildOptionField(entry, row, key, readonly, hasError) {
    const spec = entry.options[key];
    const value = row.options[key];
    const isDefault = value === "" || value === null || value === undefined
      || (spec.type === "number" ? Number(value) === Number(spec.default) : value === spec.default);
    let input;
    if (spec.type === "boolean") {
      input = el("input", { attrs: { type: "checkbox" } });
      input.checked = !!value;
      input.addEventListener("change", (e) => { row.options[key] = e.target.checked; this.applyChecksForm(); });
    } else {
      // No min/max: the loader validates on save (spec §4).
      input = el("input", { className: "form-input mono option-input", attrs: { type: "number", step: "any", placeholder: String(spec.default) } });
      input.value = value === "" || value === null || value === undefined ? "" : String(value);
      input.addEventListener("change", (e) => {
        const raw = e.target.value.trim();
        row.options[key] = raw === "" ? "" : Number(raw);
        this.applyChecksForm();
      });
    }
    input.classList.toggle("is-default", isDefault);
    if (hasError) input.classList.add("input-error");
    if (readonly) input.setAttribute("disabled", "disabled");
    const children = [el("label", { className: "option-label mono", text: key }), input];
    const hint = MigDiff.OPTION_HINTS[key];
    if (hint) children.push(el("div", { className: "option-hint", text: `${hint} (default ${spec.default})` }));
    return el("div", { className: "check-option" + (isDefault ? " is-default" : ""), children });
  }

  buildUnknownCheckRow(id, overrides, readonly) {
    const remove = el("button", { className: "link-btn", text: "remove", attrs: { type: "button", title: "odebrat ze souboru" },
      onClick: () => { delete this.state.profileEditor.form.unknown[id]; this.applyChecksForm(); } });
    if (readonly) remove.setAttribute("disabled", "disabled");
    return el("div", {
      className: "checks-row override unknown",
      children: [
        el("span", { text: "" }),
        el("div", { className: "check-id-cell", children: [
          el("span", { className: "check-id mono", text: id }),
          el("div", { className: "check-title", text: "check už není v registru" }),
        ] }),
        el("span", { text: "" }),
        el("div", { className: "check-options", children: [
          el("span", { className: "option-value mono", text: JSON.stringify(overrides) }),
        ] }),
        remove,
      ],
    });
  }
```

- [ ] **Step 4: Render the table in `renderProfilesView`**

Replace `this.mainEl.appendChild(this.buildChecksRegistry());` with:

```js
    if (!editor.form) this.resetChecksForm(editor);
    this.mainEl.appendChild(this.buildChecksSection(editor, readonly));
```

and add, for now (Task 5 replaces it with the two-column layout):

```js
  buildChecksSection(editor, readonly) {
    return el("div", {
      className: "form-card",
      children: [
        el("div", { className: "form-section-label", text: "Checks" }),
        this.buildChecksTable(editor, readonly),
      ],
    });
  }
```

- [ ] **Step 5: Update `GUIDE_TEXT.profiles`**

Replace the second bullet's text about `(default)` in `GUIDE_TEXT.profiles` (around line 82) with three bullets — keep the first bullet as is and use:

```js
      "(default) je serverový profil z --profile (nebo vestavěný prázdný) a v GUI se needituje — Duplicate z něj udělá pojmenovanou kopii.",
      "Tabulka checků ukazuje všechny registrované checky s defaulty (šedě kurzívou). Vypnutí, jiná severity nebo změněná tolerance udělá z řádku override (žlutě, ●); reset ho vrátí na default. Do souboru se zapíše jen to, co se od defaultu liší — náhled YAML vpravo je přesně to, co Save uloží.",
      "Prázdné číselné pole znamená default. Hodnoty se nekontrolují za psaní — chybu vrátí Save a ukáže ji u řádku.",
```

- [ ] **Step 6: Styles**

In `style.css` replace the whole `/* Registered checks table (screen 3) */` block (`.checks-registry-table` through `.checks-registry-row .reg-enabled.off`) with:

```css
/* Checks table (profile editor, spec 4) */
.checks-table {
  background: #fff;
  border: 1px solid #e2e5ea;
  border-radius: 8px;
  overflow: hidden;
}

.checks-table-header,
.checks-row {
  display: grid;
  grid-template-columns: 28px 240px 120px 1fr 56px;
  gap: 0 12px;
  padding: 10px 16px;
  align-items: start;
}

.checks-table-header {
  background: #f9fafb;
  border-bottom: 1px solid #e2e5ea;
  font: 600 11px 'IBM Plex Sans', sans-serif;
  letter-spacing: 0.06em;
  color: #6b7280;
  text-transform: uppercase;
}

.checks-group {
  padding: 6px 16px;
  background: #f3f4f6;
  border-top: 1px solid #e2e5ea;
  border-bottom: 1px solid #e2e5ea;
  font-size: 11.5px;
  color: #4b5563;
}
.checks-group.unknown { color: #b91c1c; }

.checks-row { border-bottom: 1px solid #f1f3f6; font-size: 12.5px; }
.checks-row.override { background: #fefce8; }
.checks-row.disabled .check-id-cell,
.checks-row.disabled .check-options { opacity: 0.55; }
.checks-row.has-error { background: #fef2f2; }
.checks-row .row-error { grid-column: 2 / -1; }

.check-id { color: #111827; }
.override-dot { color: #ca8a04; }
.check-title { font-size: 11.5px; color: #6b7280; margin-top: 2px; }

.severity-select.is-default { color: #9ca3af; font-style: italic; }

.check-options { display: flex; flex-wrap: wrap; gap: 8px 18px; }
.check-option { display: flex; flex-direction: column; gap: 2px; }
.check-option .option-label { font-size: 11.5px; color: #4b5563; }
.check-option .option-input { width: 110px; }
.check-option .option-input.is-default { color: #9ca3af; font-style: italic; }
.check-option .option-input.input-error { border-color: #b91c1c; }
.check-option .option-hint { font-size: 11px; color: #9ca3af; max-width: 260px; }
.check-option.unknown .option-value { color: #6b7280; }
.check-no-options { color: #9ca3af; font-style: italic; }

.link-btn {
  background: none;
  border: none;
  padding: 0;
  color: #2563eb;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.link-btn:hover { text-decoration: underline; }
.link-btn:disabled { color: #d1d5db; cursor: default; text-decoration: none; }
```

- [ ] **Step 7: Check in the browser**

Run: `pyats-venv/bin/mig-validate gui --run-root runs --profiles-root profiles` (or whatever `pyats-venv/bin/python -m migration_validator.cli gui --help` shows), open the GUI, press `Profiles`, create a profile:

- every registered check appears once, under group headers (`interfaces`, `bgp`, …, `general` for `deactivation_state`);
- defaults are grey italic; unchecking a toggle turns the row yellow with `●` and enables `reset`;
- typing `-40` into `interface_traffic.tolerance_percent`, then clearing the field, returns the row to default;
- `(default)` shows the same table disabled;
- console shows no errors (`grep`-check the terminal too).

- [ ] **Step 8: Full suite, then commit**

Run: `pyats-venv/bin/pytest -o addopts="" -q` and `node --test 'tests/js/*.test.js'` — all green.

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): tabulka checku v editoru profilu - vsechny checky s defaulty, overrides zlute, reset

Nahrazuje read-only vypis registru; loadChecks z editoru odchazi.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015QxyDv8dVznMMrt9Rqipju"
```

---

### Task 5: Live YAML preview and override counter

**Files:**
- Modify: `migration_validator/gui/static/app.js` — `buildChecksSection` (Task 4), new `schedulePreview`, `loadPreview`; `goToProfiles`, `applyChecksForm`, `buildProfileSectionForm`'s `set` (~line 2974) so profile-section edits refresh the preview too
- Modify: `migration_validator/gui/static/style.css`

**Interfaces:**
- Consumes: `POST /api/profiles/preview` `{document}` → `{yaml}` (admin); `MigDiff.overrideCount`.
- Produces: `editor.preview = {yaml: string|null, error: string|null, pending: boolean}`; `this.previewTimer` (debounce handle, 300 ms).

- [ ] **Step 1: Preview state and debounce**

In `goToProfiles` extend the editor init with `preview: { yaml: null, error: null, pending: false }`. Add after `applyChecksForm`:

```js
  // Preview is the exact text Save writes (spec §3): always from the
  // server, never rendered client-side. Debounced so typing a tolerance
  // does not fire a request per keystroke.
  schedulePreview() {
    const editor = this.state.profileEditor;
    if (!editor || !editor.doc) return;
    editor.preview.pending = true;
    if (this.previewTimer) clearTimeout(this.previewTimer);
    this.previewTimer = setTimeout(() => this.loadPreview(), 300);
  }

  async loadPreview() {
    const editor = this.state.profileEditor;
    if (!editor || !editor.doc || this.state.view !== "profiles") return;
    const snapshot = JSON.stringify(editor.doc);
    try {
      const res = await fetch("/api/profiles/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document: editor.doc }),
      });
      // A later edit may have replaced the document meanwhile - drop stale answers.
      if (this.state.profileEditor !== editor || JSON.stringify(editor.doc) !== snapshot) return;
      if (res.ok) {
        editor.preview = { yaml: (await res.json()).yaml, error: null, pending: false };
      } else {
        const body = await res.json().catch(() => ({}));
        editor.preview = { yaml: null, error: body.detail || `náhled se nepodařilo načíst (${res.status})`, pending: false };
      }
    } catch (err) {
      editor.preview = { yaml: null, error: String(err), pending: false };
    }
    this.render();
  }
```

Call `this.schedulePreview()`:
- at the end of `applyChecksForm()` (before `this.render()`),
- inside `buildProfileSectionForm`'s `set(key, value)` before `this.render()`,
- in `goToProfiles` right after the document is loaded (next to `resetChecksForm`), and in `saveProfile`/`discardProfile` next to their `resetChecksForm` calls.

- [ ] **Step 2: Two-column section with the preview panel**

Replace `buildChecksSection` from Task 4 with:

```js
  buildChecksSection(editor, readonly) {
    const catalogue = this.cache.catalogue || { checks: [] };
    const count = editor.form ? MigDiff.overrideCount(catalogue, editor.form) : 0;
    const preview = editor.preview || { yaml: null, error: null, pending: false };
    const panelChildren = [
      el("div", { className: "form-section-label", text: "YAML preview" }),
      el("pre", { className: "yaml-preview mono" + (preview.pending ? " pending" : ""),
        text: preview.yaml === null ? "" : (preview.yaml || "# prázdný profil - všechno default") }),
    ];
    if (preview.error) panelChildren.push(el("div", { className: "field-error", text: preview.error }));
    panelChildren.push(el("div", { className: "override-count", text: `${count} overrides` }));
    return el("div", {
      className: "checks-layout",
      children: [
        el("div", { className: "form-card", children: [
          el("div", { className: "form-section-label", text: "Checks" }),
          this.buildChecksTable(editor, readonly),
        ] }),
        el("div", { className: "form-card yaml-panel", children: panelChildren }),
      ],
    });
  }
```

- [ ] **Step 3: Styles**

Append to `style.css` after the `.link-btn` rules:

```css
.checks-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 20px;
  align-items: start;
  margin-top: 16px;
}
.yaml-panel { position: sticky; top: 16px; }
.yaml-preview {
  margin: 0;
  padding: 12px 14px;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  min-height: 120px;
  max-height: 70vh;
  overflow: auto;
  white-space: pre;
}
.yaml-preview.pending { opacity: 0.6; }
.override-count { font-size: 12.5px; color: #6b7280; }
@media (max-width: 1100px) {
  .checks-layout { grid-template-columns: 1fr; }
  .yaml-panel { position: static; }
}
```

- [ ] **Step 4: Check in the browser**

Start the GUI, open a profile, change `interface_traffic.tolerance_percent` to `-40` and disable `interface_optics_levels`. Expect within ~0.3 s:

```
checks:
  interface_optics_levels:
    enabled: false
  interface_traffic:
    tolerance_percent: -40
```

and `2 overrides` below. Reset both rows → preview shows `# prázdný profil - všechno default` and `0 overrides`. Changing a collector chip in the profile section also refreshes the preview. Open the Network tab and type several digits quickly: one preview request per pause, not per keystroke.

- [ ] **Step 5: Full suite, then commit**

Run: `pyats-venv/bin/pytest -o addopts="" -q` and `node --test 'tests/js/*.test.js'`.

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): zivy YAML nahled profilu z POST /api/profiles/preview a pocet overrides

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015QxyDv8dVznMMrt9Rqipju"
```

---

### Task 6: Save errors shown inline in the row

**Files:**
- Modify: `migration_validator/gui/static/app.js` — `saveProfile` error branch, footer in `renderProfilesView`

**Interfaces:**
- Consumes: `MigDiff.errorTarget(message, catalogue)`; `editor.errorTarget` (read by `buildChecksTable` since Task 4).

- [ ] **Step 1: Set the target on a 422**

In `saveProfile`'s error branch, after `editor.error = body.detail || ...;` add:

```js
        editor.errorTarget = res.status === 422
          ? MigDiff.errorTarget(editor.error, this.cache.catalogue || { checks: [] })
          : null;
```

and in the `catch` branch `editor.errorTarget = null;`. Also set `editor.errorTarget = null;` where `editor.error = null;` is set at the top of `saveProfile`.

- [ ] **Step 2: Keep the footer message**

The spec wants the error under the Save button **and** inline. `renderProfilesView` already pushes `editor.error` into `footerChildren`; leave it. `buildChecksTable` (Task 4) already adds `row-error` to the targeted row and `input-error` to the option input when `errorTarget.key` matches.

- [ ] **Step 3: Check in the browser (forced bad value)**

Number inputs cannot produce a string, so force one from the console while the editor is open:

```js
app = document.querySelector("body").__app; // if not exposed, use the DevTools "Store as global" on the App instance from a breakpoint in saveProfile
```

If the `App` instance is not reachable, temporarily test by editing the file directly:

```bash
printf 'checks:\n  interface_traffic:\n    tolerance_percent: "-40"\n' > profiles/bad.yml
```

then open `bad` in the editor. Expected: `GET /api/profiles/bad` → the editor shows the loader message in the `loadError` notice (`profiles/bad.yml: check 'interface_traffic': volba 'tolerance_percent' ocekava number, nalezeno str`). Delete the file afterwards (`rm profiles/bad.yml`). For the save path, in the DevTools console with the editor open run:

```js
fetch("/api/profiles/<name>", { method: "PUT", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ document: { profile: {}, checks: { interface_traffic: { tolerance_percent: "-40" } } } }) })
  .then((r) => r.json()).then(console.log);
```

Expected `detail` names the check and the key. The inline path (`errorTarget` → red row) is verified by the `errorTarget` unit tests in Task 3 plus reading `buildCheckRow`.

- [ ] **Step 4: Full suite, then commit**

```bash
git add migration_validator/gui/static/app.js
git commit -m "feat(gui): chyba ulozeni profilu se ukaze i u radku checku, ktery ji zpusobil

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015QxyDv8dVznMMrt9Rqipju"
```

---

### Task 7: Docs

**Files:**
- Modify: `docs/cs/reference.md` (section `### Profily (profiles/, GUI)`, ~line 773-800)
- Modify: `docs/en/reference.md` (section `### Profiles (profiles/, GUI)`, ~line 783-810)

- [ ] **Step 1: Czech reference**

In `docs/cs/reference.md`, after the paragraph ending `(`409 profil pouziva <n> runu`).` add:

```markdown
Sekce `checks:` se při načtení validuje: volba známého checku musí mít typ svého defaultu
(`tolerance_percent: "-40"` → `422 profiles/<jméno>.yml: check 'interface_traffic': volba
'tolerance_percent' ocekava number, nalezeno str`), `severity` je jen `critical` nebo
`advisory`, `enabled` je bool. Neznámý check ani neznámá volba chybou **nejsou** — editor je
ukáže ve skupině `neznamy check` / read-only s odkazem `remove`, aby šel soubor vyčistit bez
textového editoru. Soubor je kanonický: hodnota rovná defaultu (včetně `enabled: true`,
u `traffic_ceased` `enabled: false`) se nikdy nezapíše, check bez odchylky v souboru není.

Editor profilu v GUI ukazuje tabulku všech registrovaných checků (skupiny podle prvního
collectoru z `requires`, `general` pro checky bez collectoru) s defaulty šedě kurzívou; řádek
s odchylkou je žlutý s `●` a odkazem `reset`. Vpravo je živý náhled YAML z `POST /preview`
(300 ms po poslední změně) a počet `<n> overrides`. Uložený profil se projeví při dalším
načtení přehledu runu — už zachycené snapshoty se nemění.
```

- [ ] **Step 2: English reference**

In `docs/en/reference.md`, after the paragraph ending `(`409 profil pouziva <n> runu`).` add:

```markdown
The `checks:` section is validated on load: an option of a known check must have the type of
its default (`tolerance_percent: "-40"` → `422 profiles/<name>.yml: check 'interface_traffic':
volba 'tolerance_percent' ocekava number, nalezeno str`), `severity` is only `critical` or
`advisory`, `enabled` is a bool. An unknown check or an unknown option is **not** an error —
the editor lists it under `neznamy check` / read-only with a `remove` link so the file can be
cleaned without a text editor. The file is canonical: a value equal to its default (including
`enabled: true`, or `enabled: false` for `traffic_ceased`) is never written, and a check with
no deviation is absent.

The GUI profile editor shows a table of every registered check (grouped by the first
collector in `requires`, `general` for checks without one) with defaults in grey italics; a
row that deviates is yellow with `●` and a `reset` link. To the right a live YAML preview
comes from `POST /preview` (300 ms after the last edit) with `<n> overrides` below. A saved
profile takes effect on the next load of a run overview — captured snapshots never change.
```

- [ ] **Step 3: Commit**

```bash
git add docs/cs/reference.md docs/en/reference.md
git commit -m "docs: validace sekce checks, kanonicky soubor profilu a tabulka checku v GUI

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_015QxyDv8dVznMMrt9Rqipju"
```

---

### Task 8: Manual lab check (needs the lab; user runs it)

Not automatable here — the spec's Testing section asks for it, so it stays on the checklist for the user's lab pass before merge. `MIG_LAB_PASSWORD` sits in `~/.bashrc` below the non-interactive guard (load it with an explicit `eval` of that line).

- [ ] In the GUI create profile `lab-tuned`: disable `interface_optics_levels`, set `interface_traffic.tolerance_percent` to `-40`. Preview shows exactly those two overrides; `profiles/lab-tuned.yml` on disk equals the preview.
- [ ] Create a run with profile `lab-tuned`, capture pre and post, open the run overview.
- [ ] Confirm `interface_optics_levels` is absent from every block and the `interface_traffic` result detail shows `tolerance_percent: -40.0`.
- [ ] Edit the profile back to `-60`, reload the overview: the detail shows `-60.0` without re-capturing (spec §5).

---

## Self-review

- **Spec coverage:** §1 table (Task 4: groups, columns, typed fields, `bez voleb`, grey defaults, hints, override tint/●/reset). §2 diff model (Task 3 `checksDocument`; Task 2 mirrors server-side; unknown check group + read-only unknown keys with `remove` in Task 4). §3 preview (Task 5, debounced 300 ms, server text, `<n> overrides`). §4 validation (Task 1 loader, Task 6 footer + inline; no clamping in Task 4 inputs; select offers only valid values). §5 runs (behaviour already true after spec 3; documented in Task 7, checked in Task 8). Testing section: route tests Task 2, JS tests Task 3, lab check Task 8.
- **Placeholders:** none; every code step has its content. Task 6 Step 3 gives two concrete ways to provoke the error because number inputs cannot emit strings.
- **Type consistency:** `editor.form = {checks, unknown}` (Task 3 ↔ 4 ↔ 5), `editor.errorTarget = {checkId, key}|null` (Task 3 ↔ 4 ↔ 6), `editor.preview = {yaml, error, pending}` (Task 5), `option_type`/`option_matches_type` (Task 1 ↔ 2), `strip_check_defaults` (Task 2), `resetChecksForm`/`applyChecksForm`/`schedulePreview` (Task 4 ↔ 5 ↔ 6).
