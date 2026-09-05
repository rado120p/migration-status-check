# GUI run kinds (single / migration) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a run be created for a single device (pre/post around an upgrade) without typing the same box twice: manifest gains `kind`, `profile`, reserved `group`; the New run form gets type cards; the run view collapses to one Device column for a single run.

**Architecture:** `runs/manifest.py` owns the new fields and load-time validation. `runs/pairing.py` treats a `single` run as "post vs own pre" (one evaluation, `same_device=True`). `api.create_run` gets a new signature (`kind`, `devices` list) and `api.update_mapping` refuses single runs. `gui/app.py` routes stay thin: new request body, `kind`/`profile`/`created` in list and detail. Frontend stays vanilla JS: a pure `defaultRunKind(runs)` helper in `view.js` (tested with `node --test`), type cards and the single sub-form in `app.js`, styles in `style.css`.

**Tech Stack:** Python 3.13, FastAPI + TestClient, pytest, PyYAML, vanilla JS + CSS (no build step), `node --test` (Node 22) for JS logic.

**Spec:** `docs/superpowers/specs/2026-09-04-gui-run-kinds-design.md`

## Global Constraints

- Tool strings (API `detail`, exceptions) are Czech **without diacritics**, exactly as the rest of the code base (`run 'x' neexistuje`, `nedostatecne opravneni: vyzaduje operate`).
- GUI copy in `GUIDE_TEXT` is Czech with diacritics; button and card labels are short English (`Single device`, `Two devices`, `Bulk`). The bulk note is exactly `bulk · pripravuje se` (spec §4).
- Manifest values: `kind` ∈ {`single`, `migration`}, missing → `migration`; `schema_version` stays `1`; `VALID_ROLES` = {`old`, `new`, `l2-switch`, `single`}.
- A `single` run has exactly one device, role `single`, no mappings. A `migration` run created through the API has exactly one `old` and one `new`.
- **Load-time rule (deviation from spec, deliberate):** the spec says a migration manifest "without old/new" is rejected on load, but it also says CLI-written manifests remain valid, and two existing tests plus the documented CLI flow (`capture --run` on a hand-written run.yml that lists only the old box so far) rely on a one-sided migration loading fine. The plan therefore rejects on load only: unknown `kind`; `single` with ≠1 device or a device whose role is not `single`; `migration` containing a `single` role or more than one `old` / more than one `new`. A migration with a missing side still loads. `create_run` enforces the strict "exactly one old and one new".
- Permission levels: create run and update mapping need `operate` (already wired through `require(Permission.OPERATE)`).
- `profile` in `POST /api/runs`: until spec 3 lands, any non-null value is a 409 (`profil '<x>' neexistuje - profile store zatim neni k dispozici`).
- No new Python dependencies, no JS dependencies, no build step.
- Test commands: `pyats-venv/bin/pytest -q` (expect all green; main was ~1380 tests at spec 1 merge) and `node --test tests/js/`. Run both before every commit.
- Commit messages in the repo style: `feat(runs): ...`, `feat(api): ...`, `feat(gui): ...`, Czech without diacritics, ending with the Co-Authored-By and Claude-Session trailers given by the session.
- Work on a branch `gui-run-kinds` off `main` (create it in Task 1, Step 0).

---

## File structure

| File | Responsibility |
|---|---|
| `migration_validator/runs/manifest.py` | `RUN_KINDS`, `DEFAULT_KIND`, role `single`, `RunManifest.kind/profile/group`, `check_kind_devices`, load/save of the new fields |
| `migration_validator/runs/pairing.py` | single-run baseline (own pre) in `find_pre_baseline` and `_plan_post` |
| `migration_validator/api.py` | `create_run(name, *, kind, devices, mappings, profile, run_root)`, single guard in `update_mapping`, preserve `kind/profile/group` on rebuild |
| `migration_validator/gui/app.py` | `DeviceBody.role`, new `CreateRunBody`, `kind`/`profile`/`created` in summaries and detail |
| `migration_validator/gui/static/view.js` | pure `defaultRunKind(runs)` |
| `migration_validator/gui/static/app.js` | type cards, profile picker, single sub-form, single run view, capture-form baseline node |
| `migration_validator/gui/static/style.css` | type cards, single devices grid, `cols-single` table, kind tag |
| `tests/runs/test_manifest.py` | new fields round-trip, defaults, validation |
| `tests/runs/test_pairing.py` | single-run pairing |
| `tests/test_api_runs.py` | new `create_run` signature, single guards, update_mapping preserves fields |
| `tests/gui/conftest.py`, `tests/gui/test_authz.py`, `tests/gui/test_routes.py`, `tests/test_cli.py` | callers of `create_run` updated |
| `tests/gui/test_write_routes.py` | new body, 409 cases, `kind` in detail, operate required |
| `tests/gui/test_serializers.py` | single run rows |
| `tests/js/view.test.js` | `defaultRunKind` |
| `docs/cs/README.md`, `docs/en/README.md` | `kind`, role `single`, `profile`, `group` in the run.yml section |

---

### Task 1: Manifest fields `kind`, `profile`, `group` and role `single`

**Files:**
- Modify: `migration_validator/runs/manifest.py`
- Test: `tests/runs/test_manifest.py`

**Interfaces:**
- Produces: `RUN_KINDS = frozenset({"single", "migration"})`, `DEFAULT_KIND = "migration"`; `RunManifest` dataclass fields `kind: str = DEFAULT_KIND`, `profile: str | None = None`, `group: str | None = None` (appended after `captures`); `check_kind_devices(kind: str, devices: dict[str, RunDevice]) -> None` raising `ValueError`; `load_manifest` reads the three fields and validates; `save_manifest` writes `kind` always, `profile` and `group` only when not `None`.

- [ ] **Step 0: Create the branch**

```bash
git checkout -b gui-run-kinds main
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/runs/test_manifest.py`:

```python
# -- kind / profile / group (GUI vlna 2026-09, spec 2) ----------------------

from migration_validator.runs.manifest import (  # noqa: E402
    DEFAULT_KIND,
    RUN_KINDS,
    VALID_ROLES,
    check_kind_devices,
)


def _single_manifest():
    return RunManifest(
        devices={
            "PTX1-POP1": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="single"
            )
        },
        kind="single",
    )


def test_run_kinds_and_roles_constants():
    assert RUN_KINDS == {"single", "migration"}
    assert DEFAULT_KIND == "migration"
    assert "single" in VALID_ROLES


def test_manifest_defaults_to_migration_without_profile_or_group():
    manifest = _manifest()
    assert manifest.kind == "migration"
    assert manifest.profile is None
    assert manifest.group is None


def test_load_without_kind_is_migration(tmp_path):
    path = tmp_path / "run.yml"
    save_manifest(_manifest(), path)
    text = path.read_text(encoding="utf-8").replace("kind: migration\n", "")
    assert "kind" not in text
    path.write_text(text, encoding="utf-8")
    loaded = load_manifest(path)
    assert loaded.kind == "migration"
    assert loaded == _manifest()


def test_roundtrip_single_with_profile_and_group(tmp_path):
    manifest = _single_manifest()
    manifest.profile = "core-only"
    manifest.group = "batch-2026-09"
    manifest.captures.append(CaptureRecord("pre", "PTX1-POP1", None, "a.json", "T1"))
    path = tmp_path / "run.yml"
    save_manifest(manifest, path)
    text = path.read_text(encoding="utf-8")
    assert "kind: single" in text
    assert "profile: core-only" in text
    assert "group: batch-2026-09" in text
    loaded = load_manifest(path)
    assert loaded == manifest
    assert loaded.kind == "single"
    assert loaded.devices["PTX1-POP1"].role == "single"


def test_save_omits_profile_and_group_when_none(tmp_path):
    path = tmp_path / "run.yml"
    save_manifest(_manifest(), path)
    text = path.read_text(encoding="utf-8")
    assert "profile" not in text
    assert "group" not in text
    assert text.splitlines()[0] == "schema_version: 1"
    assert text.splitlines()[1] == "kind: migration"


def test_load_rejects_unknown_kind(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "kind: bulk\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: single}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="neznamy kind 'bulk', ocekavano single nebo migration") as excinfo:
        load_manifest(path)
    assert str(path) in str(excinfo.value)


def test_load_rejects_single_with_two_devices(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "kind: single\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: single}\n"
        "  Y: {host: 1.2.3.5, platform: junos, role: single}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run typu single") as excinfo:
        load_manifest(path)
    assert str(path) in str(excinfo.value)


def test_load_rejects_single_with_old_role(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "kind: single\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: old}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run typu single"):
        load_manifest(path)


def test_load_rejects_migration_with_single_role(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: single}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run typu migration"):
        load_manifest(path)


def test_load_rejects_migration_with_two_old(tmp_path):
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "devices:\n"
        "  X: {host: 1.2.3.4, platform: junos, role: old}\n"
        "  Y: {host: 1.2.3.5, platform: junos, role: old}\n"
        "  Z: {host: 1.2.3.6, platform: junos-evo, role: new}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="jeden box role 'old', nalezeno 2"):
        load_manifest(path)


def test_load_accepts_one_sided_migration(tmp_path):
    # CLI flow: run.yml sepsany rucne zatim jen se starym boxem musi jit nacist
    path = tmp_path / "run.yml"
    path.write_text(
        "schema_version: 1\n"
        "devices:\n"
        "  MX1-POP1: {host: 172.20.20.4, platform: junos, role: old}\n",
        encoding="utf-8",
    )
    assert load_manifest(path).kind == "migration"


def test_check_kind_devices_direct():
    check_kind_devices("migration", _manifest().devices)
    check_kind_devices("single", _single_manifest().devices)
    with pytest.raises(ValueError, match="neznamy kind"):
        check_kind_devices("bulk", {})
    with pytest.raises(ValueError, match="run typu single"):
        check_kind_devices("single", {})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/runs/test_manifest.py -q`
Expected: ImportError on `DEFAULT_KIND` (collection fails). That is the failing state.

- [ ] **Step 3: Implement the manifest changes**

In `migration_validator/runs/manifest.py`:

Replace the constants block:

```python
RUN_SCHEMA_VERSION = 1
RUN_KINDS = frozenset({"single", "migration"})
DEFAULT_KIND = "migration"
VALID_ROLES = frozenset({"old", "new", "l2-switch", "single"})
```

Add the three fields to `RunManifest` (after `captures`):

```python
@dataclass
class RunManifest:
    devices: dict[str, RunDevice] = field(default_factory=dict)
    interface_mapping: list[InterfaceMapping] = field(default_factory=list)
    captures: list[CaptureRecord] = field(default_factory=list)
    # Druh runu: "migration" (old -> new s mappingem) nebo "single" (jeden box,
    # pre/post kolem upgradu). Chybi-li v run.yml, je to migration.
    kind: str = DEFAULT_KIND
    # Jmeno profilu z profile store (spec 3); None = serverovy default.
    profile: str | None = None
    # Rezervovano pro bulk: N single runu se stejnou skupinou. Nikdo to zatim nepise.
    group: str | None = None
```

Add the validator after the `RunManifest` class (before `_load_device`):

```python
def check_kind_devices(kind: str, devices: dict[str, RunDevice]) -> None:
    """Strukturalni pravidla kind vs. role. Volane pri nacteni run.yml a pri
    zakladani runu; jednostranna migrace (zatim jen old) projde - CLI ji
    dopisuje postupne."""
    if kind not in RUN_KINDS:
        raise ValueError(f"neznamy kind '{kind}', ocekavano single nebo migration")
    roles = [device.role for device in devices.values()]
    if kind == "single":
        if len(devices) != 1 or roles != ["single"]:
            raise ValueError(
                "run typu single ma prave jedno zarizeni role 'single', "
                f"nalezeno {len(devices)} zarizeni s rolemi {sorted(roles)}"
            )
        return
    if "single" in roles:
        raise ValueError("run typu migration nesmi obsahovat zarizeni role 'single'")
    for role in ("old", "new"):
        count = roles.count(role)
        if count > 1:
            raise ValueError(
                f"run typu migration podporuje jeden box role '{role}', nalezeno {count}"
            )
```

In `load_manifest`, after the devices loop and before `interface_mapping`:

```python
    kind = raw.get("kind") or DEFAULT_KIND
    try:
        check_kind_devices(kind, devices)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
```

and change the return:

```python
    return RunManifest(
        devices=devices,
        interface_mapping=interface_mapping,
        captures=captures,
        kind=kind,
        profile=raw.get("profile"),
        group=raw.get("group"),
    )
```

In `save_manifest`, build `data` so `kind` follows `schema_version` and the optional fields are only emitted when set:

```python
    data: dict[str, Any] = {
        "schema_version": RUN_SCHEMA_VERSION,
        "kind": manifest.kind,
    }
    if manifest.profile is not None:
        data["profile"] = manifest.profile
    if manifest.group is not None:
        data["group"] = manifest.group
    data["devices"] = {
        node: {
            "host": device.host,
            "platform": device.platform,
            "role": device.role,
        }
        for node, device in manifest.devices.items()
    }
    data["interface_mapping"] = [
        {"old": _dump_endpoint(mapping.old), "new": _dump_endpoint(mapping.new)}
        for mapping in manifest.interface_mapping
    ]
    data["captures"] = [
        {
            "phase": record.phase,
            "device": record.device,
            "port": record.port if record.port is not None else "all",
            "snapshot": record.snapshot,
            "taken": record.taken,
        }
        for record in manifest.captures
    ]
```

(The `path.write_text(...)` call stays as it is.)

- [ ] **Step 4: Run the manifest tests, then the whole suite**

Run: `pyats-venv/bin/pytest tests/runs/test_manifest.py -q`
Expected: all PASS, including the pre-existing `test_save_manifest_puts_schema_version_first` and `test_load_missing_sections_default_to_empty`.

Run: `pyats-venv/bin/pytest -q`
Expected: all green. If a test elsewhere compares a saved run.yml against a literal string, it now needs the `kind: migration` line; fix that test, not the writer.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/runs/manifest.py tests/runs/test_manifest.py
git commit -m "feat(runs): manifest nese kind/profile/group, role single, validace kind vs. role"
```

---

### Task 2: Pairing for a single run

**Files:**
- Modify: `migration_validator/runs/pairing.py:38-93`
- Test: `tests/runs/test_pairing.py`

**Interfaces:**
- Consumes: `RunManifest.kind` from Task 1.
- Produces: for `kind == "single"`, `find_pre_baseline(manifest, node, port)` returns the device's own pre (exact port first, then whole-box), never the `old` fallback; `_plan_post` for a single run returns exactly one `Evaluation` with `same_device=True`, or one with `reason="chybi pre snimek <node>:<port|all>"`. `plan_evaluations` therefore yields one evaluation per post capture in a single run (the `_plan_same_device` pass dedups by snapshot pair).

- [ ] **Step 1: Write the failing tests**

Append to `tests/runs/test_pairing.py`:

```python
# -- single run (jeden box, pre/post kolem upgradu) -------------------------


def _single_manifest():
    return RunManifest(
        devices={
            "PTX1-POP1": RunDevice(
                host="172.20.20.5", platform="junos-evo", role="single"
            )
        },
        kind="single",
    )


def test_single_run_pre_and_post_is_one_same_device_evaluation():
    manifest = _single_manifest()
    pre = CaptureRecord("pre", "PTX1-POP1", None, "pre.json", "T1")
    post = CaptureRecord("post", "PTX1-POP1", None, "post.json", "T2")
    manifest.captures = [pre, post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    evaluation = evaluations[0]
    assert evaluation.subject == post
    assert evaluation.baseline == pre
    assert evaluation.reason is None
    assert evaluation.same_device is True
    assert evaluation.step is None


def test_single_run_post_without_pre_names_the_device():
    manifest = _single_manifest()
    post = CaptureRecord("post", "PTX1-POP1", None, "post.json", "T2")
    manifest.captures = [post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    assert evaluations[0].baseline is None
    assert evaluations[0].reason == "chybi pre snimek PTX1-POP1:all"


def test_single_run_per_port_post_falls_back_to_whole_box_pre():
    manifest = _single_manifest()
    pre_all = CaptureRecord("pre", "PTX1-POP1", None, "pre_all.json", "T1")
    post = CaptureRecord("post", "PTX1-POP1", "et-0/0/0", "post.json", "T2")
    manifest.captures = [pre_all, post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    assert evaluations[0].baseline == pre_all
    assert evaluations[0].same_device is True


def test_single_run_rollback_pairs_with_own_pre():
    manifest = _single_manifest()
    pre = CaptureRecord("pre", "PTX1-POP1", None, "pre.json", "T1")
    rollback = CaptureRecord("rollback", "PTX1-POP1", None, "rb.json", "T3")
    manifest.captures = [pre, rollback]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    assert evaluations[0].subject == rollback
    assert evaluations[0].baseline == pre
    assert evaluations[0].same_device is False


def test_find_pre_baseline_single_run_returns_own_pre():
    manifest = _single_manifest()
    pre_all = CaptureRecord("pre", "PTX1-POP1", None, "pre_all.json", "T1")
    manifest.captures = [pre_all]
    assert find_pre_baseline(manifest, "PTX1-POP1", "et-0/0/0") == pre_all
    assert find_pre_baseline(manifest, "PTX1-POP1", None) == pre_all
    manifest.captures = []
    assert find_pre_baseline(manifest, "PTX1-POP1", None) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/runs/test_pairing.py -q -k single_run`
Expected: `test_single_run_pre_and_post_is_one_same_device_evaluation` FAILS with `len(evaluations) == 2` (the missing-baseline evaluation plus the same-device one); `test_single_run_post_without_pre_names_the_device` FAILS on the reason text (`chybi pre snimek stareho boxu`); `test_single_run_per_port_post_falls_back_to_whole_box_pre` FAILS (`len == 1` but `same_device` is False... or `len == 2`); the rollback test passes already.

- [ ] **Step 3: Implement**

In `migration_validator/runs/pairing.py`, add a helper above `find_pre_baseline` and extend both functions:

```python
def _own_pre(manifest: RunManifest, node: str, port: str | None) -> CaptureRecord | None:
    """Pre snimek tehoz zarizeni: nejdriv presny port, pak celoboxovy."""
    return manifest.find_capture("pre", node, port) or manifest.find_capture(
        "pre", node, None
    )


def find_pre_baseline(
    manifest: RunManifest, node: str, port: str | None
) -> CaptureRecord | None:
    """Najde pre snimek stareho boxu pro dany node/port.

    Nejdriv zkusi per-port parovani pres interface_mapping, pak spadne na
    celoboxovy pre snimek stareho boxu (role "old"). Single run zadny stary
    box nema - baseline je vlastni pre snimek zarizeni. Sdileno mezi
    plan_evaluations (evaluate --run) a orchestrate.capture_into_run
    (baseline pro ping cile pri post capture).
    """
    if manifest.kind == "single":
        return _own_pre(manifest, node, port)

    baseline: CaptureRecord | None = None

    if port is not None:
        old_endpoint = manifest.paired_old(node, port)
        if old_endpoint is not None:
            baseline = manifest.find_capture("pre", old_endpoint.node, old_endpoint.port)

    if baseline is None:
        old = manifest.device_with_role("old")
        if old is not None:
            old_node, _ = old
            baseline = manifest.find_capture("pre", old_node, None)

    return baseline
```

At the top of `_plan_post`, before `steps = [...]`:

```python
    if manifest.kind == "single":
        # Jeden box: post vs vlastni pre, vzdy prave jedna evaluace.
        # _plan_same_device tuhle dvojici pozna podle snapshotu a nezdvoji ji.
        baseline = find_pre_baseline(manifest, subject.device, subject.port)
        if baseline is None:
            return [
                Evaluation(
                    subject=subject,
                    baseline=None,
                    reason=(
                        "chybi pre snimek "
                        f"{subject.device}:{subject.port or 'all'}"
                    ),
                )
            ]
        return [Evaluation(subject=subject, baseline=baseline, same_device=True)]
```

- [ ] **Step 4: Run the tests**

Run: `pyats-venv/bin/pytest tests/runs/test_pairing.py tests/runs/test_orchestrate.py -q`
Expected: PASS.

Run: `pyats-venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/runs/pairing.py tests/runs/test_pairing.py
git commit -m "feat(runs): single run paruje post s vlastnim pre, jedna evaluace same_device"
```

---

### Task 3: `api.create_run` with `kind`/`devices`, single guard in `update_mapping`

**Files:**
- Modify: `migration_validator/api.py:104-150` (`_run_device`, `create_run`) and `:245-288` (`update_mapping`)
- Modify (callers): `tests/gui/conftest.py`, `tests/gui/test_authz.py:15`, `tests/gui/test_routes.py:57-59`, `tests/test_cli.py:1459,1481,1497,1513,1533,1550`
- Test: `tests/test_api_runs.py`

**Interfaces:**
- Consumes: `RunManifest(kind=, profile=)`, `check_kind_devices`, `RUN_KINDS` from Task 1.
- Produces: `api.create_run(name: str, *, kind: str, devices: list[dict[str, str]], mappings: list[tuple[str, str]] | None = None, profile: str | None = None, run_root: str | Path = Path("runs")) -> RunManifest`. Each device dict has keys `node`, `host`, `platform`, `role`. Validation order: name → duplicate dir → kind → devices (keys, role, duplicates) → kind/role structure → mappings → profile. `api.update_mapping` raises `ValueError("run typu single nema interface mapping")` for a single run and preserves `kind`, `profile`, `group` when it rebuilds the manifest.

- [ ] **Step 1: Rewrite the tests in `tests/test_api_runs.py` for the new signature**

Replace the constants and every `create_run` call in `tests/test_api_runs.py`. The constants become:

```python
OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}
SINGLE = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "single"}
```

Every existing call `api.create_run("x", old_device=OLD, new_device=NEW, ...)` becomes `api.create_run("x", kind="migration", devices=[OLD, NEW], ...)` (keep the other keyword arguments as they are). The existing `test_create_run_chybejici_klic`-style test that passes `old_device={"node": "MX1", "platform": "junos"}` (line ~79) becomes:

```python
def test_create_run_chybejici_klic_zarizeni(tmp_path):
    with pytest.raises(ValueError, match="chybi 'host'"):
        api.create_run(
            "bad", kind="migration",
            devices=[{"node": "MX1", "platform": "junos", "role": "old"}, NEW],
            run_root=tmp_path,
        )
```

Then append the new tests:

```python
# -- kind single / migration ---------------------------------------------------


def test_create_run_zapise_kind_migration(tmp_path):
    api.create_run("mig02", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    raw = yaml.safe_load((tmp_path / "mig02" / "run.yml").read_text())
    assert raw["kind"] == "migration"
    assert "profile" not in raw


def test_create_run_single(tmp_path):
    manifest = api.create_run("upg01", kind="single", devices=[SINGLE], run_root=tmp_path)
    assert manifest.kind == "single"
    assert manifest.devices["PTX1"].role == "single"
    raw = yaml.safe_load((tmp_path / "upg01" / "run.yml").read_text())
    assert raw["kind"] == "single"
    assert raw["interface_mapping"] == []


def test_create_run_neznamy_kind(tmp_path):
    with pytest.raises(ValueError, match="neznamy kind 'bulk'"):
        api.create_run("b", kind="bulk", devices=[SINGLE], run_root=tmp_path)
    assert not (tmp_path / "b").exists()


def test_create_run_single_se_dvema_zarizenimi(tmp_path):
    with pytest.raises(ValueError, match="run typu single"):
        api.create_run(
            "s", kind="single",
            devices=[SINGLE, {**OLD, "role": "single"}], run_root=tmp_path,
        )
    assert not (tmp_path / "s").exists()


def test_create_run_single_se_spatnou_roli(tmp_path):
    with pytest.raises(ValueError, match="run typu single"):
        api.create_run("s", kind="single", devices=[OLD], run_root=tmp_path)


def test_create_run_single_s_mappingem(tmp_path):
    with pytest.raises(ValueError, match="run typu single nema interface mapping"):
        api.create_run(
            "s", kind="single", devices=[SINGLE],
            mappings=[("ge-0/0/1", "et-0/0/1")], run_root=tmp_path,
        )


def test_create_run_migration_bez_new(tmp_path):
    with pytest.raises(ValueError, match="jedno zarizeni role 'old' a jedno role 'new'"):
        api.create_run("m", kind="migration", devices=[OLD], run_root=tmp_path)


def test_create_run_migration_se_single_roli(tmp_path):
    with pytest.raises(ValueError, match="role 'single'"):
        api.create_run("m", kind="migration", devices=[OLD, SINGLE], run_root=tmp_path)


def test_create_run_duplicitni_node(tmp_path):
    with pytest.raises(ValueError, match="uvedeno dvakrat"):
        api.create_run(
            "d", kind="migration",
            devices=[OLD, {**NEW, "node": "MX1"}], run_root=tmp_path,
        )


def test_create_run_profil_zatim_nelze(tmp_path):
    # do spec 3 (profile store) je kazdy nenulovy profil chyba
    with pytest.raises(ValueError, match="profil 'core-only' neexistuje"):
        api.create_run(
            "p", kind="single", devices=[SINGLE], profile="core-only", run_root=tmp_path,
        )
    assert not (tmp_path / "p").exists()


def test_update_mapping_odmitne_single(tmp_path):
    api.create_run("upg01", kind="single", devices=[SINGLE], run_root=tmp_path)
    with pytest.raises(ValueError, match="run typu single nema interface mapping"):
        api.update_mapping("upg01", [("ge-0/0/1", "et-0/0/1")], run_root=tmp_path)


def test_update_mapping_zachova_kind_profile_group(tmp_path):
    from migration_validator.runs.manifest import load_manifest, save_manifest
    api.create_run("mig02", kind="migration", devices=[OLD, NEW], run_root=tmp_path)
    path = tmp_path / "mig02" / "run.yml"
    manifest = load_manifest(path)
    manifest.profile = "core-only"
    manifest.group = "g1"
    save_manifest(manifest, path)
    api.update_mapping("mig02", [("ge-0/0/1", "et-0/0/1")], run_root=tmp_path)
    reloaded = load_manifest(path)
    assert reloaded.kind == "migration"
    assert reloaded.profile == "core-only"
    assert reloaded.group == "g1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/test_api_runs.py -q`
Expected: every `create_run` call FAILS with `TypeError: create_run() got an unexpected keyword argument 'kind'`.

- [ ] **Step 3: Implement `create_run`**

In `migration_validator/api.py`, extend the manifest import:

```python
from migration_validator.runs.manifest import (
    RUN_KINDS,
    MappingEndpoint,
    RunDevice,
    RunManifest,
    check_kind_devices,
)
```

Replace `_run_device` and `create_run`:

```python
_RUN_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
_DEVICE_KEYS = ("node", "host", "platform", "role")


def _run_device(data: dict[str, str]) -> tuple[str, RunDevice]:
    label = data.get("node") or data.get("role") or "?"
    for key in _DEVICE_KEYS:
        if not data.get(key):
            raise ValueError(f"zarizeni '{label}': chybi '{key}'")
    try:
        device = RunDevice(host=data["host"], platform=data["platform"], role=data["role"])
    except ValueError as exc:
        raise ValueError(f"zarizeni '{data['node']}': {exc}") from exc
    return data["node"], device


def create_run(
    name: str,
    *,
    kind: str,
    devices: list[dict[str, str]],
    mappings: list[tuple[str, str]] | None = None,
    profile: str | None = None,
    run_root: str | Path = Path("runs"),
) -> RunManifest:
    """Zalozi runs/<name>/run.yml - schopnost, kterou CLI nema (run.yml
    se dosud psal rucne).

    kind "single": prave jedno zarizeni role single, zadny mapping.
    kind "migration": prave jeden old a jeden new; mapping je volitelny,
    bez nej vznika sekvencni run s volnym capture formularem.
    `profile` se overi proti profile store (spec 3); do te doby je kazda
    nenulova hodnota chyba."""
    if not _RUN_NAME_RE.match(name):
        raise ValueError(
            f"nevalidni jmeno runu '{name}' - povolene znaky: a-z 0-9 _ -"
        )
    store = RunStore(Path(run_root), name)
    if store.dir.exists():
        raise ValueError(f"run '{name}' uz existuje ({store.dir})")
    if kind not in RUN_KINDS:
        raise ValueError(f"neznamy kind '{kind}', ocekavano single nebo migration")

    loaded: dict[str, RunDevice] = {}
    for data in devices:
        node, device = _run_device(data)
        if node in loaded:
            raise ValueError(f"zarizeni '{node}' je uvedeno dvakrat")
        loaded[node] = device
    check_kind_devices(kind, loaded)

    manifest = RunManifest(devices=loaded, kind=kind, profile=profile)
    if kind == "single":
        if mappings:
            raise ValueError("run typu single nema interface mapping")
    else:
        roles = [device.role for device in loaded.values()]
        if roles.count("old") != 1 or roles.count("new") != 1:
            raise ValueError(
                "run typu migration ma prave jedno zarizeni role 'old' a jedno role 'new'"
            )
        old_node = manifest.device_with_role("old")[0]
        new_node = manifest.device_with_role("new")[0]
        for old_port, new_port in mappings or []:
            manifest.add_mapping(
                old=MappingEndpoint(node=old_node, port=old_port.strip()),
                new=MappingEndpoint(node=new_node, port=new_port.strip()),
            )

    if profile is not None:
        raise ValueError(
            f"profil '{profile}' neexistuje - profile store zatim neni k dispozici"
        )

    store.save(manifest)
    return manifest
```

In `update_mapping`, right after `manifest = store.load()`:

```python
    if manifest.kind == "single":
        raise ValueError("run typu single nema interface mapping")
```

and change the rebuild line to carry the new fields:

```python
    rebuilt = RunManifest(
        devices=manifest.devices,
        captures=manifest.captures,
        kind=manifest.kind,
        profile=manifest.profile,
        group=manifest.group,
    )
```

- [ ] **Step 4: Update the other callers**

`tests/gui/conftest.py`:

```python
OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}


@pytest.fixture
def client(tmp_path):
    api.create_run(
        "mig01", kind="migration", devices=[OLD, NEW],
        mappings=[("ge-0/0/1", "et-0/0/1")], run_root=tmp_path,
    )
    app = create_app(run_root=tmp_path)
    return TestClient(app)
```

`tests/gui/test_authz.py`: add `"role": "old"` / `"role": "new"` to its `OLD`/`NEW` constants and change line 15 to `api.create_run("mig01", kind="migration", devices=[OLD, NEW], run_root=tmp_path)`.

`tests/gui/test_routes.py:57-59`: the test defines or imports `OLD`/`NEW`; make sure they carry `role` and change both calls to `api.create_run("mig01", kind="migration", devices=[OLD, NEW], run_root=tmp_path)` / `"mig02"` likewise.

`tests/test_cli.py`: six identical call sites (lines ~1459, 1481, 1497, 1513, 1533, 1550) of the form

```python
    api.create_run("mig01", old_device={"node": "MX1", "host": "10.0.0.1", "platform": "junos"},
                   new_device={"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"},
                   run_root=tmp_path)
```

Each becomes

```python
    api.create_run("mig01", kind="migration",
                   devices=[{"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"},
                            {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}],
                   run_root=tmp_path)
```

Verify no caller is left: `grep -rn "old_device=" --include=*.py . | grep -v pyats-venv | grep -v .venv` must print nothing.

The route in `gui/app.py` still calls the old signature; that is fixed in Task 4. Until then `tests/gui/test_write_routes.py` fails, which is expected for this commit only if you commit Task 3 alone. **Prefer to do Task 3 and Task 4 in one sitting and commit them together** if you want the suite green at every commit; otherwise commit Task 3 with the message below and immediately continue.

- [ ] **Step 5: Run the tests**

Run: `pyats-venv/bin/pytest tests/test_api_runs.py tests/runs tests/test_cli.py tests/gui/test_authz.py tests/gui/test_routes.py -q`
Expected: PASS (test_write_routes is handled in Task 4).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/api.py tests/test_api_runs.py tests/gui/conftest.py tests/gui/test_authz.py tests/gui/test_routes.py tests/test_cli.py
git commit -m "feat(api): create_run s kind a seznamem zarizeni, update_mapping odmita single"
```

---

### Task 4: GUI routes — new body, `kind`/`profile`/`created` in list and detail

**Files:**
- Modify: `migration_validator/gui/app.py:26-63` (bodies, `_run_summary`), `:115-125` (`_detail`), `:132-146` (`create_run` route)
- Test: `tests/gui/test_write_routes.py`, `tests/gui/test_routes.py`, `tests/gui/test_serializers.py`

**Interfaces:**
- Consumes: `api.create_run(name, kind=, devices=, mappings=, profile=, run_root=)` from Task 3.
- Produces: `POST /api/runs` body `{"name", "kind", "profile": null, "devices": [{"node","host","platform","role"}], "mappings": [[old,new], ...]}`; `GET /api/runs` items carry `kind`, `profile`, `created` (ISO 8601 UTC with `Z`, manifest file mtime); `GET /api/runs/{run}` and the create response carry `kind`, `profile`. The frontend (Tasks 6 and 7) reads `detail.kind` and `run.created`.

- [ ] **Step 1: Rewrite `tests/gui/test_write_routes.py` for the new body and add the new cases**

Replace the constants and every request body in the file. Constants:

```python
OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"}
SINGLE = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "single"}


def _migration(name="mig02", mappings=None):
    return {"name": name, "kind": "migration", "profile": None,
            "devices": [OLD, NEW], "mappings": mappings or []}
```

Every existing body `{"name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": X}` becomes `_migration(mappings=X)` (for `X == []`, just `_migration()`). Then append:

```python
def test_post_runs_single(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "upg01", "kind": "single", "profile": None,
        "devices": [SINGLE], "mappings": [],
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "single"
    assert body["profile"] is None
    assert body["devices"]["PTX1"]["role"] == "single"
    assert body["rows"] == []


def test_post_runs_vraci_kind_migration(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json=_migration())
    assert resp.status_code == 201
    assert resp.json()["kind"] == "migration"


def test_post_runs_single_se_dvema_zarizenimi_je_409(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "upg01", "kind": "single", "profile": None,
        "devices": [SINGLE, {**OLD, "role": "single"}], "mappings": [],
    })
    assert resp.status_code == 409
    assert "run typu single" in resp.json()["detail"]


def test_post_runs_migration_s_jednim_zarizenim_je_409(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "mig02", "kind": "migration", "profile": None,
        "devices": [OLD], "mappings": [],
    })
    assert resp.status_code == 409
    assert "role 'old' a jedno role 'new'" in resp.json()["detail"]


def test_post_runs_neznamy_kind_je_409(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "b", "kind": "bulk", "profile": None, "devices": [SINGLE], "mappings": [],
    })
    assert resp.status_code == 409
    assert "neznamy kind 'bulk'" in resp.json()["detail"]


def test_post_runs_profil_je_zatim_409(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "p", "kind": "single", "profile": "core-only",
        "devices": [SINGLE], "mappings": [],
    })
    assert resp.status_code == 409
    assert "profil 'core-only' neexistuje" in resp.json()["detail"]


def test_put_mapping_na_single_je_409(tmp_path):
    client = _client(tmp_path)
    client.post("/api/runs", json={
        "name": "upg01", "kind": "single", "profile": None,
        "devices": [SINGLE], "mappings": [],
    })
    resp = client.put("/api/runs/upg01/mapping", json={"mappings": [["ge-0/0/1", "et-0/0/1"]]})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "run typu single nema interface mapping"


def test_post_runs_vyzaduje_operate(tmp_path):
    from migration_validator.gui.authz import Actor
    app = create_app(run_root=tmp_path)
    client = TestClient(app)
    app.state.actor_provider = lambda request: Actor(role="viewer")
    resp = client.post("/api/runs", json=_migration())
    assert resp.status_code == 403
    assert resp.json()["detail"] == "nedostatecne opravneni: vyzaduje operate"
    assert not (tmp_path / "mig02").exists()
```

- [ ] **Step 2: Add list/detail tests to `tests/gui/test_routes.py`**

Append:

```python
def test_runs_nesou_kind_profile_a_created(client):
    run = client.get("/api/runs").json()["runs"][0]
    assert run["kind"] == "migration"
    assert run["profile"] is None
    assert run["created"].endswith("Z")
    assert run["created"].startswith("20")


def test_run_detail_nese_kind_a_profile(client):
    data = client.get("/api/runs/mig01").json()
    assert data["kind"] == "migration"
    assert data["profile"] is None


def test_runs_single_run_ma_kind_single(client, tmp_path):
    from migration_validator import api
    api.create_run(
        "upg01", kind="single",
        devices=[{"node": "PTX9", "host": "10.0.0.9", "platform": "junos-evo", "role": "single"}],
        run_root=tmp_path,
    )
    runs = {r["name"]: r for r in client.get("/api/runs").json()["runs"]}
    assert runs["upg01"]["kind"] == "single"
    assert runs["mig01"]["kind"] == "migration"
```

- [ ] **Step 3: Add the single-run serializer test to `tests/gui/test_serializers.py`**

Append:

```python
def test_single_run_radek_je_pod_old_s_flagy():
    manifest = RunManifest(
        devices={"PTX1": RunDevice(host="10.0.0.2", platform="junos-evo", role="single")},
        kind="single",
    )
    manifest.record_capture(CaptureRecord(
        phase="pre", device="PTX1", port=None, snapshot="pre.json", taken="t1",
    ))
    manifest.record_capture(CaptureRecord(
        phase="post", device="PTX1", port=None, snapshot="post.json", taken="t2",
    ))
    rows = status_rows(manifest)
    assert rows == [{
        "old": {"node": "PTX1", "port": None},
        "new": None,
        "pre": True, "post": True, "rollback": False,
    }]
```

- [ ] **Step 4: Run the GUI tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/gui -q`
Expected: write-route tests FAIL with 422 (unknown body fields) or `TypeError` from the route; `test_runs_nesou_kind_profile_a_created` FAILS with `KeyError: 'kind'`; the serializer test PASSES already (no serializer change needed).

- [ ] **Step 5: Implement the routes**

In `migration_validator/gui/app.py`:

Add the import:

```python
from datetime import datetime, timezone
```

Replace the request models:

```python
class DeviceBody(BaseModel):
    node: str
    host: str
    platform: str
    role: str


class CreateRunBody(BaseModel):
    name: str
    kind: str
    profile: str | None = None
    devices: list[DeviceBody]
    mappings: list[tuple[str, str]] = []
```

Replace `_run_summary`:

```python
def _created_iso(path: Path) -> str:
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_summary(store: RunStore) -> dict:
    manifest = store.load()
    return {
        "name": store.name,
        "kind": manifest.kind,
        "profile": manifest.profile,
        "created": _created_iso(store.manifest_path),
        "devices": _devices_dict(manifest),
        "snapshots": len(manifest.captures),
        "mapped_ports": len(manifest.interface_mapping),
    }
```

In `_detail`, add the two keys:

```python
        return {
            "name": run,
            "kind": manifest.kind,
            "profile": manifest.profile,
            "devices": _devices_dict(manifest),
            "rows": status_rows(manifest),
            "snapshots": snapshot_list(manifest),
        }
```

Replace the body of the create route:

```python
    @app.post("/api/runs", status_code=201)
    def create_run(body: CreateRunBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        try:
            api.create_run(
                body.name,
                kind=body.kind,
                devices=[d.model_dump() for d in body.devices],
                mappings=body.mappings,
                profile=body.profile,
                run_root=run_root,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _detail(body.name)
```

- [ ] **Step 6: Run the tests**

Run: `pyats-venv/bin/pytest tests/gui -q`
Expected: PASS.

Run: `pyats-venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/gui/app.py tests/gui/test_write_routes.py tests/gui/test_routes.py tests/gui/test_serializers.py
git commit -m "feat(gui): POST /api/runs s kind a devices, kind/profile/created v seznamu a detailu"
```

---

### Task 5: Pure helper `defaultRunKind(runs)` in `view.js`

**Files:**
- Modify: `migration_validator/gui/static/view.js:174-197`
- Test: `tests/js/view.test.js`

**Interfaces:**
- Consumes: `GET /api/runs` items with `created` and `kind` (Task 4).
- Produces: `MigView.defaultRunKind(runs) -> "single" | "migration"`: the `kind` of the run with the lexicographically greatest `created` (ISO UTC strings sort correctly), `"migration"` when the list is empty, when no run has `created`, or when the newest run's kind is anything other than `"single"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/js/view.test.js`:

```js
test("defaultRunKind: empty or no created -> migration", () => {
  assert.strictEqual(MigView.defaultRunKind([]), "migration");
  assert.strictEqual(MigView.defaultRunKind(null), "migration");
  assert.strictEqual(MigView.defaultRunKind([{ name: "a", kind: "single" }]), "migration");
});

test("defaultRunKind: kind of the most recently created run", () => {
  const runs = [
    { name: "old", kind: "migration", created: "2026-09-01T10:00:00Z" },
    { name: "new", kind: "single", created: "2026-09-05T08:00:00Z" },
    { name: "mid", kind: "migration", created: "2026-09-03T08:00:00Z" },
  ];
  assert.strictEqual(MigView.defaultRunKind(runs), "single");
  assert.strictEqual(MigView.defaultRunKind(runs.slice(0, 1)), "migration");
});

test("defaultRunKind: unknown kind on newest run -> migration", () => {
  const runs = [{ name: "x", kind: "bulk", created: "2026-09-05T08:00:00Z" }];
  assert.strictEqual(MigView.defaultRunKind(runs), "migration");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test tests/js/`
Expected: the three new tests FAIL with `TypeError: MigView.defaultRunKind is not a function`.

- [ ] **Step 3: Implement**

In `migration_validator/gui/static/view.js`, after `filterRuns`:

```js
/* Kind preselected on the New run form: the kind of the most recently
   created run (ISO UTC strings compare lexicographically), else migration. */
function defaultRunKind(runs) {
  let newest = null;
  for (const run of runs || []) {
    if (!run || typeof run.created !== "string") continue;
    if (newest === null || run.created > newest.created) newest = run;
  }
  if (newest === null) return "migration";
  return newest.kind === "single" ? "single" : "migration";
}
```

and add `defaultRunKind,` to the `MigView` object literal.

- [ ] **Step 4: Run the tests**

Run: `node --test tests/js/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/static/view.js tests/js/view.test.js
git commit -m "feat(gui): defaultRunKind - typ noveho runu podle naposledy zalozeneho"
```

---

### Task 6: New run form — type cards, profile picker, single sub-form

**Files:**
- Modify: `migration_validator/gui/static/app.js` (`GUIDE_TEXT.newrun` ~line 66; `openNewRunForm`, `submitNewRun`, `renderNewRunForm` ~lines 2186-2375)
- Modify: `migration_validator/gui/static/style.css` (after `.devices-grid`, ~line 746)

**Interfaces:**
- Consumes: `MigView.defaultRunKind` (Task 5); `POST /api/runs` body from Task 4; existing `buildDeviceSubform(title, device, touched)`, `buildMappingCard(rows, opts)`, `mappingDupErrors(rows)`, `buildCaptureField(label, control)`.
- Produces: `this.state.newRunForm = { name, kind, touched, old, new, mappings, submitting, submitError }`. The single sub-form is bound to `form.old` (same object), which is exactly what keeps the values when switching kind in either direction (spec §4). New methods `setNewRunKind(kind)`, `buildRunTypeCards(form)`, `buildProfilePicker()`. No JS unit tests (DOM code; there is no DOM harness in this repo) — verification is the browser click-through in Step 5.

- [ ] **Step 1: Add the run-type constant and guide copy**

In `app.js`, directly above `const GUIDE_TEXT = {`:

```js
/* New run type cards. Bulk is a placeholder: rendered, never selectable. */
const RUN_TYPES = [
  {
    kind: "single",
    title: "Single device",
    desc: "pre/post snapshots of one box: upgrade, reconfiguration, maintenance",
  },
  {
    kind: "migration",
    title: "Two devices",
    desc: "old → new migration with port pairing",
  },
  {
    kind: "bulk",
    title: "Bulk",
    desc: "many single-device runs from a device list",
    disabled: true,
    note: "bulk · pripravuje se",
  },
];
```

Replace `GUIDE_TEXT.newrun`:

```js
  newrun: {
    title: "New run",
    body: [
      "Vyber typ runu: Single device = pre/post snímky jednoho boxu (upgrade, rekonfigurace), Two devices = migrace old → new s párováním portů. Bulk se připravuje.",
      "Pojmenuj run a vyplň zařízení. U Two devices můžeš mapování portů doplnit i později přes Edit mapping.",
      "Profil zatím zůstává serverový default; výběr profilu přinese další vlna.",
    ],
  },
```

- [ ] **Step 2: Form state and kind switching**

Replace `openNewRunForm` and add `setNewRunKind` right after `cancelNewRunForm`:

```js
  openNewRunForm() {
    this.state.view = "newrun";
    this.state.selectedSnapshot = null;
    this.state.newRunForm = {
      name: "",
      kind: MigView.defaultRunKind(this.cache.runs),
      touched: false,
      // The single-device sub-form edits `old` too, so switching kind in
      // either direction keeps what was typed (spec §4).
      old: { node: "", host: "", platform: "junos" },
      new: { node: "", host: "", platform: "junos-evo" },
      mappings: [],
      submitting: false,
      submitError: null,
    };
    this.render();
  }

  setNewRunKind(kind) {
    const form = this.state.newRunForm;
    if (!form || form.submitting) return;
    const type = RUN_TYPES.find((t) => t.kind === kind);
    if (!type || type.disabled) return;
    form.kind = kind;
    form.submitError = null;
    this.render();
  }
```

- [ ] **Step 3: Submit with the new body**

Replace `submitNewRun`:

```js
  async submitNewRun() {
    const form = this.state.newRunForm;
    form.touched = true;
    const single = form.kind === "single";
    const nameErr = this.newRunNameError();
    const dup = single ? new Set() : this.mappingDupErrors(form.mappings);
    const deviceOk = (d) => d.node.trim() && d.host.trim();
    const devicesOk = single ? deviceOk(form.old) : deviceOk(form.old) && deviceOk(form.new);
    const mappingsOk = single || form.mappings.every((r) => r.old.trim() && r.new.trim());
    if (nameErr || !devicesOk || dup.size || !mappingsOk) {
      this.render();
      return;
    }
    const trimDevice = (d, role) => ({
      node: d.node.trim(),
      host: d.host.trim(),
      platform: d.platform,
      role,
    });
    const devices = single
      ? [trimDevice(form.old, "single")]
      : [trimDevice(form.old, "old"), trimDevice(form.new, "new")];
    const mappings = single ? [] : form.mappings.map((r) => [r.old.trim(), r.new.trim()]);
    form.submitting = true;
    form.submitError = null;
    this.render();
    try {
      const res = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name.trim(),
          kind: form.kind,
          profile: null,
          devices,
          mappings,
        }),
      });
      if (res.status === 201) {
        const detail = await res.json();
        const runName = form.name.trim();
        this.cache.detail = detail;
        this.cache.runs = (await (await fetch("/api/runs")).json()).runs;
        this.state.newRunForm = null;
        this.state.run = runName;
        this.state.view = "run";
        this.state.selectedSnapshot = null;
        this.state.openResults = {};
        await this.loadRun();
        this.render();
        return;
      }
      const body = await res.json().catch(() => ({}));
      form.submitError = body.detail || `run se nepodarilo vytvorit (${res.status})`;
    } catch (err) {
      form.submitError = String(err);
    }
    form.submitting = false;
    this.render();
  }
```

- [ ] **Step 4: Render — cards, name + profile, devices by kind, mapping only for migration**

Add two builders before `renderNewRunForm`:

```js
  buildRunTypeCards(form) {
    const cards = el("div", { className: "type-cards", attrs: { role: "radiogroup" } });
    for (const type of RUN_TYPES) {
      const on = form.kind === type.kind;
      const children = [
        el("span", { className: "type-card-title", text: type.title }),
        el("span", { className: "type-card-desc", text: type.desc }),
      ];
      if (type.note) children.push(el("span", { className: "type-card-note", text: type.note }));
      const attrs = { type: "button", role: "radio", "aria-checked": on ? "true" : "false" };
      if (type.disabled) attrs.disabled = "disabled";
      cards.appendChild(
        el("button", {
          className: "type-card" + (on ? " on" : "") + (type.disabled ? " disabled" : ""),
          attrs,
          children,
          onClick: type.disabled ? null : () => this.setNewRunKind(type.kind),
        })
      );
    }
    return cards;
  }

  buildProfilePicker() {
    // Spec 3 fills this from the profile store; until then only the
    // server default is offered and the control stays disabled.
    const select = el("select", {
      className: "form-select",
      attrs: { disabled: "disabled" },
      children: [el("option", { text: "(default)", attrs: { value: "" } })],
    });
    return this.buildCaptureField("Profile", select);
  }
```

Replace `renderNewRunForm`:

```js
  renderNewRunForm() {
    clear(this.mainEl);
    const form = this.state.newRunForm;
    if (!form) return;
    const single = form.kind === "single";

    this.mainEl.appendChild(
      el("div", {
        className: "breadcrumb",
        children: [el("span", { className: "crumb-current", text: "new run" })],
      })
    );
    this.mainEl.appendChild(el("h1", { className: "capture-h1", text: "New run" }));

    this.mainEl.appendChild(
      el("div", {
        className: "form-card",
        children: [
          el("div", { className: "form-section-label", text: "Run type" }),
          this.buildRunTypeCards(form),
        ],
      })
    );

    const nameInput = el("input", {
      className: "form-input mono",
      attrs: { type: "text", placeholder: single ? "e.g. upgrade-ptx1" : "e.g. mig01" },
    });
    nameInput.value = form.name;
    nameInput.addEventListener("input", (e) => {
      form.name = e.target.value;
    });
    nameInput.addEventListener("blur", () => {
      form.touched = true;
      this.render();
    });
    const nameFieldChildren = [el("label", { className: "field-label", text: "Run name" }), nameInput];
    const nameErr = form.touched ? this.newRunNameError() : null;
    if (nameErr) nameFieldChildren.push(el("div", { className: "field-error", text: nameErr }));
    if (form.submitError) {
      nameFieldChildren.push(el("div", { className: "field-error", text: form.submitError }));
    }
    this.mainEl.appendChild(
      el("div", {
        className: "form-card",
        children: [
          el("div", { className: "form-section-label", text: "Run" }),
          el("div", {
            className: "name-profile-grid",
            children: [
              el("div", { className: "form-field", children: nameFieldChildren }),
              this.buildProfilePicker(),
            ],
          }),
        ],
      })
    );

    const devicesGrid = single
      ? el("div", {
          className: "devices-grid single",
          children: [this.buildDeviceSubform("Device", form.old, form.touched)],
        })
      : el("div", {
          className: "devices-grid",
          children: [
            this.buildDeviceSubform("Old device", form.old, form.touched),
            this.buildDeviceSubform("New device", form.new, form.touched),
          ],
        });
    this.mainEl.appendChild(
      el("div", {
        className: "form-card",
        children: [
          el("div", { className: "form-section-label", text: single ? "Device" : "Devices" }),
          devicesGrid,
        ],
      })
    );

    if (!single) {
      this.mainEl.appendChild(
        this.buildMappingCard(form.mappings, {
          editable: true,
          touched: form.touched,
          dupOld: this.mappingDupErrors(form.mappings),
          onAdd: () => {
            form.mappings.push({ old: "", new: "" });
            this.render();
          },
          onRemove: (i) => {
            form.mappings.splice(i, 1);
            this.render();
          },
          onChangeOld: (i, v) => {
            form.mappings[i].old = v;
          },
          onChangeNew: (i, v) => {
            form.mappings[i].new = v;
          },
          onBlur: () => {
            form.touched = true;
            this.render();
          },
        })
      );
    }

    this.mainEl.appendChild(
      el("div", {
        className: "footer-actions",
        children: [
          el("button", {
            className: "btn btn-secondary",
            text: "Cancel",
            onClick: () => this.cancelNewRunForm(),
          }),
          el("button", {
            className: "btn btn-primary",
            text: form.submitting ? "Creating…" : "Create run",
            onClick: form.submitting ? null : () => this.submitNewRun(),
          }),
        ],
      })
    );
  }
```

Check how `el()` (top of `app.js`, line 7) applies `attrs` — it must call `setAttribute` for each key so that `disabled`, `role` and `aria-checked` land on the button. If `el()` only supports a fixed set, extend it there rather than working around it.

Add the CSS after `.devices-grid { ... }` in `style.css`:

```css
.devices-grid.single {
  grid-template-columns: 1fr;
  max-width: calc(50% - 10px);
}

.name-profile-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.type-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.type-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  text-align: left;
  padding: 14px 16px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: #fff;
  font: 400 13px 'IBM Plex Sans', sans-serif;
  color: #111827;
  cursor: pointer;
}
.type-card:hover:not(.disabled) { border-color: #9ca3af; }
.type-card.on {
  border-color: #3b82f6;
  background: #eff6ff;
  box-shadow: inset 0 0 0 1px #3b82f6;
}
.type-card.disabled {
  color: #9ca3af;
  background: #f9fafb;
  cursor: default;
}
.type-card-title { font-weight: 600; font-size: 13.5px; }
.type-card-desc { font-size: 12.5px; color: #6b7280; }
.type-card.disabled .type-card-desc { color: #9ca3af; }
.type-card-note {
  margin-top: 4px;
  font: 500 11px 'IBM Plex Mono', monospace;
  color: #9ca3af;
}
```

- [ ] **Step 5: Verify in the browser**

Run: `.venv/bin/mig-validate gui --run-root runs-example` (check `mig-validate gui --help` for the actual flag names if that fails) and open the printed URL. Then:

1. Click `+ New run`. Three cards render; Bulk is grey, shows `bulk · pripravuje se`, and clicking it changes nothing.
2. The preselected card matches the kind of the most recently created run (Two devices on a fresh `runs-example`).
3. Select Single device: one `Device` sub-form, no Port mapping card; Profile select shows `(default)` and is disabled.
4. Type node/host in the single form, switch to Two devices: the values appear under Old device. Type into New device, switch back to Single and back again: both sets survive.
5. Create a single run named `upg01`: lands on the run overview (Task 7 finishes that view).
6. Try `Create run` with an empty node: the `node is required` field error appears; with a duplicate name the 409 detail appears under Run name.

Also run: `node --test tests/js/` and `pyats-venv/bin/pytest -q` (unchanged, must stay green).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): New run s kartami typu (single/two devices/bulk placeholder) a profile pickerem"
```

---

### Task 7: Run view and capture form for a single run

**Files:**
- Modify: `migration_validator/gui/static/app.js` (`renderRunOverview` ~1297-1515, `buildPairingTable` ~1532, `captureFindOldDeviceNode` ~935, `GUIDE_TEXT.run` ~line 41)
- Modify: `migration_validator/gui/static/style.css` (`.pairing-header-row.cols-same` block ~line 354, `.run-header .subtitle` ~line 269)

**Interfaces:**
- Consumes: `detail.kind` (Task 4); evaluations from `GET /api/runs/{run}/evaluation` where single-run post evaluations carry `same_device: true` and rollback evaluations `same_device: false` (Task 2).
- Produces: `buildPairingTable(rows, opts)` with `opts.single` rendering a `Device / Pre / Post / Rollback` header (class `cols-single`); `captureFindOldDeviceNode()` returns the `single` device for a single run so the capture form's baseline notice works there.

- [ ] **Step 1: Header, table, results and footer by kind in `renderRunOverview`**

Apply these edits inside `renderRunOverview`:

After `const devices = detail.devices || {};` add and extend the device scan:

```js
    const single = detail.kind === "single";
    let oldDevice = null;
    let newDevice = null;
    let singleDevice = null;
    for (const [node, d] of Object.entries(devices)) {
      if (d.role === "old") oldDevice = { node, ...d };
      else if (d.role === "new") newDevice = { node, ...d };
      else if (d.role === "single") singleDevice = { node, ...d };
    }
```

Replace the `if (oldDevice && newDevice) { ... }` subtitle block with:

```js
    if (single && singleDevice) {
      const label = PLATFORM_LABEL[singleDevice.platform] || singleDevice.platform;
      header.appendChild(el("span", { className: "kind-tag", text: "single device" }));
      header.appendChild(
        el("span", {
          className: "subtitle",
          text: `${singleDevice.node} · ${singleDevice.host} (${label})`,
        })
      );
    } else if (oldDevice && newDevice) {
      const oldLabel = PLATFORM_LABEL[oldDevice.platform] || oldDevice.platform;
      const newLabel = PLATFORM_LABEL[newDevice.platform] || newDevice.platform;
      header.appendChild(
        el("span", {
          className: "subtitle",
          text: `${oldDevice.host} (${oldLabel}) → ${newDevice.host} (${newLabel})`,
        })
      );
    }
```

Replace `const pairEvaluations = evaluations.filter((ev) => !ev.same_device);` with:

```js
    // Single run: every evaluation (post vs own pre, rollback vs own pre) is
    // the main result set; there is no separate same-device section.
    const pairEvaluations = single ? evaluations : evaluations.filter((ev) => !ev.same_device);
```

Replace the captures-table block (`const allRows = ...` through the `else { ... }`) with:

```js
    const allRows = detail.rows || [];
    const mappingRows = allRows.filter((r) => r.old && r.new);
    const wholeRows = allRows.filter((r) => !r.old || !r.new);
    if (single) {
      if (allRows.length > 0) {
        this.mainEl.appendChild(el("div", { className: "subsection-title", text: "Captures" }));
        this.mainEl.appendChild(this.buildPairingTable(allRows, { single: true }));
      }
    } else if (mappingRows.length === 0) {
      this.mainEl.appendChild(
        el("div", {
          className: "notice notice-warn",
          text: "no port mapping — captures are per-device",
        })
      );
      if (wholeRows.length > 0) {
        this.mainEl.appendChild(el("div", { className: "subsection-title", text: "Captures" }));
        this.mainEl.appendChild(this.buildPairingTable(wholeRows));
      }
    } else {
      this.mainEl.appendChild(el("div", { className: "subsection-title", text: "Captures" }));
      this.mainEl.appendChild(this.buildPairingTable(allRows));
    }
```

Replace the same-device + results block:

```js
    const sameDevice = single ? null : this.buildSameDeviceSection();
    if (sameDevice) this.mainEl.appendChild(sameDevice);

    const entries = this.collectServiceEntries(pairEvaluations);
    if (entries.length > 0) {
      this.mainEl.appendChild(
        el("div", { className: "subsection-title", text: `Results — ${entries.length} služeb` })
      );
      this.mainEl.appendChild(this.buildResultsTable(entries, { singlePort: single }));
    }
```

In the `if (this.cache.evaluation) { ... }` aggregation block, change the two lines

```js
      const sameDeviceEvaluations = evaluations.filter((ev) => ev.same_device);
```
to
```js
      const sameDeviceEvaluations = single ? [] : evaluations.filter((ev) => ev.same_device);
```
(the `aggregate(sameDeviceEvaluations, ...)` call then contributes nothing for a single run and stays as is).

In the footer, build the button list conditionally:

```js
    const footerButtons = [];
    if (!single) {
      footerButtons.push(
        el("button", {
          className: "btn btn-secondary",
          text: "Edit mapping",
          onClick: () => this.openEditMapping(),
        })
      );
    }
    footerButtons.push(
      el("button", {
        className: "btn btn-secondary",
        text: "Export JSON",
        onClick: () => this.exportJson(),
      }),
      el("button", {
        className: "btn btn-primary-green",
        text: "Evaluate run",
        onClick: () => this.evaluateRun(),
      })
    );
    const footer = el("div", { className: "footer-actions", children: footerButtons });
    this.mainEl.appendChild(footer);
```

- [ ] **Step 2: `buildPairingTable` single layout**

Change the signature to `buildPairingTable(rows, opts)` and the header/row construction:

```js
  buildPairingTable(rows, opts) {
    const single = !!(opts && opts.single);
    const cols = single ? " cols-single" : "";
    const table = el("div", { className: "pairing-table" });
    const headers = single
      ? ["Device", "Pre", "Post", "Rollback"]
      : ["Old port", "New port", "Pre", "Post", "Rollback"];
    table.appendChild(
      el("div", {
        className: "pairing-header-row" + cols,
        children: headers.map((text) => el("span", { text })),
      })
    );

    const task = this.cache.captureProgress;
    const endpointText = (ep) => (ep ? `${ep.node}:${ep.port || "all"}` : "not paired");
    for (const row of rows) {
      const rowMatches = this.rowMatchesCapture(row, task);
      const portCells = single
        ? [el("span", { className: "port-cell", text: endpointText(row.old || row.new) })]
        : [
            el("span", {
              className: "port-cell" + (row.old ? "" : " unpaired"),
              text: endpointText(row.old),
            }),
            el("span", {
              className: "port-cell" + (row.new ? "" : " unpaired"),
              text: endpointText(row.new),
            }),
          ];

      const rowEl = el("div", {
        className: "pairing-row" + cols,
        children: [
          ...portCells,
          this.buildFlagCell(row, "pre", task),
          this.buildFlagCell(row, "post", task),
          this.buildFlagCell(row, "rollback", task),
        ],
      });
      table.appendChild(rowEl);
      // ... the existing row-note handling (running / failed / done with issues) stays unchanged
```

Keep the rest of the method (the `row-note` blocks and `return table;`) exactly as it is.

- [ ] **Step 3: Capture form knows the single device**

Replace `captureFindOldDeviceNode`:

```js
  captureFindOldDeviceNode() {
    // Baseline owner: the old box in a migration run, the box itself in a
    // single run (its whole-box pre is the baseline for post and rollback).
    const detail = this.cache.captureDetail;
    if (!detail) return null;
    const entries = Object.entries(detail.devices || {});
    const entry =
      entries.find(([, d]) => d.role === "old") ||
      entries.find(([, d]) => d.role === "single");
    return entry ? entry[0] : null;
  }
```

`presetCaptureFormDevice` and `selectCaptureFormDevice` already default a non-`new` role to phase `pre`, and `capturePortOptions` already falls back to the free port text when there are no mappings, so nothing else changes there.

- [ ] **Step 4: Guide copy and CSS**

Add to `GUIDE_TEXT.run.body` (as the second bullet):

```js
      "Single device run porovnává post (nebo rollback) snímek boxu s jeho vlastním pre snímkem — tabulka má jeden sloupec Device a žádné mapování portů.",
```

Add to `style.css`, next to the `.cols-same` rule:

```css
.pairing-header-row.cols-single,
.pairing-row.cols-single {
  grid-template-columns: 2.6fr 90px 90px 110px;
}

.kind-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid #c7d2fe;
  background: #eef2ff;
  color: #4338ca;
  font: 600 11px 'IBM Plex Sans', sans-serif;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  align-self: center;
}
```

- [ ] **Step 5: Verify in the browser**

With the GUI running against a scratch run root (copy `runs-example` into the scratchpad directory and start the GUI with that root so real runs stay untouched):

1. Open a migration run: header, table and footer look exactly as before (Old port / New port columns, Edit mapping present, Same device section only when such evaluations exist).
2. Create a single run `upg01` from Task 6. The overview shows the `SINGLE DEVICE` tag and `node · host (platform)` subtitle, no "no port mapping" notice, no Edit mapping button, no Captures table yet (no rows).
3. Open New capture from that run: the device select has one entry `PTX… (single)`, phases pre/post/rollback, free port text. Post phase without a pre shows the `no pre baseline recorded for <node>:all` warning; after a pre exists it shows `Baseline … found`.
4. To exercise results without a lab: copy the two snapshot files from an existing `runs-example` run into `upg01/`, add matching `pre`/`post` `captures` entries (device = the single node, `port: all`) to `upg01/run.yml`, reload. The Captures table has one `Device` row with ✓ pre/post; `Evaluate run` yields a `Results — N služeb` table with the single `Port` column; Nespárováno / Nezařazeno sections render; no "Same device — pre vs post" heading.

Run `pyats-venv/bin/pytest -q` and `node --test tests/js/` (unchanged, must stay green).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): run overview a capture form pro single run - jeden sloupec Device, bez mappingu"
```

---

### Task 8: Documentation of the new manifest fields

**Files:**
- Modify: `docs/cs/README.md:206-236` (run.yml example and the `role` paragraph)
- Modify: `docs/en/README.md:184-200`

- [ ] **Step 1: Czech README**

In the run.yml example add `kind: migration` directly under `schema_version: 1`. Replace the paragraph starting `` `role` je `old` / `new` / `l2-switch`. `` with:

```markdown
`kind` je `migration` (výchozí, když chybí) nebo `single`. Migrační run má jeden box role
`old` a jeden role `new`; `l2_switch` (EX mezi EVO a CPE) formát manifestu už nese, ale zapojí
ho až fáze 5. Run typu `single` má právě jedno zařízení role `single` a žádný
`interface_mapping` — post (i rollback) snímek se porovnává s vlastním pre snímkem boxu
(upgrade, rekonfigurace na místě). Volitelné `profile: <jméno>` říká, který profil z
`profiles/` run používá (chybí = serverový default); `group: <řetězec>` je rezervované pro
hromadné zakládání single runů, zatím ho nic nezapisuje. `interface_mapping` páruje
**logické jednotky** (`ge-0/0/0`), stejně jako `mapping.yml` výš.
```

- [ ] **Step 2: English README**

Add `kind: migration` under `schema_version: 1` in the example, and after the code block (before `Full flag tables, ...`) add:

```markdown
`kind` is `migration` (the default when missing) or `single`. A migration run has one box of
role `old` and one of role `new`. A `single` run has exactly one device of role `single` and no
`interface_mapping`; its post (and rollback) snapshot is compared against the box's own pre
snapshot (upgrade, in-place reconfiguration). Optional `profile: <name>` records which profile
from `profiles/` the run uses (missing = server default); `group: <string>` is reserved for bulk
creation of single runs and is not written by anything yet.
```

- [ ] **Step 3: Commit**

```bash
git add docs/cs/README.md docs/en/README.md
git commit -m "docs: run.yml kind/profile/group a role single"
```

---

## Finishing

After Task 8: run the full suite once more (`pyats-venv/bin/pytest -q` and `node --test tests/js/`), then use `superpowers:finishing-a-development-branch` to merge `gui-run-kinds` into `main` (the previous wave merged fast-forward and pushed). Known accepted limitations to carry into the memory note: the load-time rule deviation described under Global Constraints, and that the `profile` field is write-only until spec 3.
