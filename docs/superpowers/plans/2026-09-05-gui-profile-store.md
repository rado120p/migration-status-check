# GUI profile store and profile editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Named profiles in `profiles/<name>.yml` that the GUI can list, create, edit and delete; each run records which profile it uses (`profile:` in run.yml, already written by spec 2) and every evaluation/capture route resolves the profile per run; an editor page with a form for the `profile:` section.

**Architecture:** `migration_validator/profiles/store.py` owns the on-disk format (`document_to_yaml`, atomic `ProfileStore.save` validated through the existing `load_profile`). `migration_validator/profiles/catalogue.py` builds the defaults the form needs from the collector and check registries. `migration_validator/gui/profiles.py` resolves a run's profile (`profile_for_run`) and counts usage; `migration_validator/gui/profile_routes.py` is an `APIRouter` with all `/api/profiles` routes, included by `create_app`. The frontend stays vanilla JS: pure document helpers in `view.js` (tested with `node --test`), the editor page and pickers in `app.js`, styles in `style.css`.

**Tech Stack:** Python 3.13, FastAPI + TestClient, pytest, PyYAML, vanilla JS + CSS (no build step), `node --test` (Node 22) for JS logic.

**Spec:** `docs/superpowers/specs/2026-09-04-gui-profile-store-design.md`

## Global Constraints

- Tool strings (API `detail`, exceptions) are Czech **without diacritics**, exactly like the rest of the code base (`profil 'x' neexistuje`, `nedostatecne opravneni: vyzaduje admin`). GUI copy in `GUIDE_TEXT` is Czech with diacritics; button labels are short English (`Profiles`, `+ New profile`, `Duplicate`, `Delete`, `Discard`, `Save profile`). The usage counter reads exactly `pouziva <n> runu`, the empty chip list exactly `(vsechny)`, the default ping placeholder exactly `5 (default)`.
- Profile names match `^[a-z0-9_-]+$` (same rule as run names). The server default is shown as `(default)` everywhere and is read-only.
- Store root: `profiles/` next to `runs/`, flag `--profiles-root DIR` (default `profiles`), `create_app(run_root, profile_path, profiles_root)`.
- `--profile` keeps its meaning (server default profile). CLI capture/evaluate behaviour is unchanged by this wave.
- Permission levels: `/api/profiles` reads need `view`, writes (`POST`, `PUT`, `DELETE`, and `POST /api/profiles/preview`) need `admin`. Every `/api` route must carry `require(Permission.*)` (a test in `tests/gui/test_authz.py` enforces it).
- Document shape (JSON): `{"profile": {"collectors": [...]|null, "service_types": [...]|null, "ping_count": int|null}, "checks": {<check_id>: {...overrides}}}`. Null = "not set". `document_to_yaml` omits null keys and an empty `checks` section; it is the single serialiser for save and preview.
- Severity enum is only `critical` / `advisory`. Do not reopen archive-vs-delete, one-file-vs-two, or profile-location decisions.
- **Deliberate additions to the spec (small, needed by the form):** `GET /api/profiles` also returns `default_document` (the server default profile as a document) so `Duplicate` on `(default)` has something to copy; catalogue check entries carry `default_enabled` (from `config.DEFAULTS`, e.g. `traffic_ceased` is off by default) because "every check has `enabled`" is only useful if the form knows its default.
- **Deliberate deviation:** the store re-raises the loader's `ValueError` with the temporary file path replaced by the target path (`profiles/<name>.yml`), otherwise the GUI would show `.core-only.k3j2.tmp: neznamy collector ...`. The message text is otherwise the loader's, verbatim.
- No new Python dependencies, no JS dependencies, no build step.
- Test commands: `pyats-venv/bin/pytest -q` (main is at 1518 passed / 1 skipped) and `node --test tests/js/` (27 tests). Run both before every commit.
- Commit messages in repo style: `feat(profiles): ...`, `feat(api): ...`, `feat(gui): ...`, `docs: ...`, Czech without diacritics, ending with the `Co-Authored-By` and `Claude-Session` trailers given by the session.
- Work on branch `gui-profile-store` off `main` (create it in Task 1, Step 0).

---

## File structure

| File | Responsibility |
|---|---|
| `migration_validator/config.py` | `PING_COUNT_DEFAULT = 5` (shared by CLI, orchestrate and catalogue) |
| `migration_validator/profiles/__init__.py` | package marker |
| `migration_validator/profiles/store.py` | `PROFILE_NAME_RE`, `check_profile_name`, `document_to_yaml`, `document_from_profile`, `ProfileStore` |
| `migration_validator/profiles/catalogue.py` | `build_catalogue()` for `GET /api/profiles/catalogue` |
| `migration_validator/api.py` | `create_run(..., profiles_root=...)` validates `profile` against the store |
| `migration_validator/cli.py` | `gui --profiles-root` |
| `migration_validator/gui/profiles.py` | `profile_for_run`, `profile_usage` |
| `migration_validator/gui/profile_routes.py` | `build_profiles_router(store, run_root, profile_path)` with all `/api/profiles` routes |
| `migration_validator/gui/app.py` | `profiles_root` parameter, `profile_for_run` in evaluation/snapshot/capture routes, router included |
| `migration_validator/gui/static/view.js` | pure `emptyProfileDocument`, `normalizeProfileDocument`, `profileDirty`, `toggleListValue` |
| `migration_validator/gui/static/app.js` | `Profiles` button, profile picker in New run, profile link in run overview, editor page |
| `migration_validator/gui/static/index.html` | `btn-checks` → `btn-profiles` |
| `migration_validator/gui/static/style.css` | chip picker, editor toolbar, profile link |
| `tests/test_profile_store.py` | store unit tests |
| `tests/gui/test_profile_routes.py` | routes + catalogue |
| `tests/gui/test_evaluation_routes.py` | per-run profile resolution |
| `tests/test_api_runs.py`, `tests/test_cli.py` | `create_run` profile validation, CLI flag |
| `tests/js/view.test.js` | document helpers |
| `docs/cs/reference.md`, `docs/en/reference.md`, `docs/cs/README.md`, `docs/en/README.md` | profiles section |

---

### Task 1: Profile store module

**Files:**
- Modify: `migration_validator/config.py` (add `PING_COUNT_DEFAULT`)
- Create: `migration_validator/profiles/__init__.py`, `migration_validator/profiles/store.py`
- Test: `tests/test_profile_store.py`

**Interfaces:**
- Produces: `config.PING_COUNT_DEFAULT: int = 5`; in `profiles/store.py`: `PROFILE_NAME_RE`, `check_profile_name(name: str) -> None` (raises `ValueError`), `document_to_yaml(document: dict) -> str`, `document_from_profile(profile: Profile) -> dict`, `empty_document() -> dict`, `class ProfileStore(root: Path)` with `list() -> list[str]`, `path(name) -> Path`, `exists(name) -> bool`, `load(name) -> Profile` (raises `FileNotFoundError("profil '<name>' neexistuje (<path>)")`), `document(name) -> dict`, `save(name, document) -> Path`, `delete(name) -> None`.

- [ ] **Step 0: Create the branch**

```bash
git checkout -b gui-profile-store main
```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_profile_store.py`:

```python
"""Profile store - profiles/<name>.yml, validace pres load_profile, atomicky zapis."""

import pytest

from migration_validator.config import PING_COUNT_DEFAULT, Profile, CheckConfig
from migration_validator.profiles.store import (
    ProfileStore,
    check_profile_name,
    document_from_profile,
    document_to_yaml,
    empty_document,
)


def _doc(**profile):
    return {"profile": {"collectors": None, "service_types": None,
                        "ping_count": None, **profile}, "checks": {}}


def test_ping_count_default_je_pet():
    assert PING_COUNT_DEFAULT == 5


def test_empty_document_ma_plny_tvar():
    assert empty_document() == _doc()


def test_document_to_yaml_vynecha_null_a_prazdne_checks():
    text = document_to_yaml(_doc(collectors=["interfaces", "bgp"]))
    assert text == "profile:\n  collectors:\n  - interfaces\n  - bgp\n"


def test_document_to_yaml_prazdny_dokument_je_prazdny_text():
    assert document_to_yaml(empty_document()) == ""


def test_document_to_yaml_zachova_checks_a_vynecha_null_option():
    doc = _doc(ping_count=3)
    doc["checks"] = {
        "interface_traffic": {"tolerance_percent": -40, "require_nonzero": None},
        "traffic_ceased": {"enabled": None},
    }
    text = document_to_yaml(doc)
    assert text == (
        "profile:\n  ping_count: 3\n"
        "checks:\n  interface_traffic:\n    tolerance_percent: -40\n"
    )


def test_document_to_yaml_je_stabilni():
    doc = _doc(service_types=["IPVPN", "Internet"])
    doc["checks"] = {"bgp_prefix_counts": {"tolerance_percent": -5}}
    assert document_to_yaml(doc) == document_to_yaml(doc)


def test_check_profile_name():
    check_profile_name("core-only_2")
    with pytest.raises(ValueError, match="nevalidni jmeno profilu 'Core Only'"):
        check_profile_name("Core Only")
    with pytest.raises(ValueError, match="nevalidni jmeno profilu '..'"):
        check_profile_name("..")


def test_save_a_load_roundtrip(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    doc = _doc(collectors=["interfaces"], service_types=["IPVPN"], ping_count=3)
    doc["checks"] = {"interface_optics_levels": {"enabled": False}}
    path = store.save("core-only", doc)
    assert path == tmp_path / "profiles" / "core-only.yml"
    assert path.read_text(encoding="utf-8") == document_to_yaml(doc)
    profile = store.load("core-only")
    assert profile.collectors == ["interfaces"]
    assert profile.service_types == ["IPVPN"]
    assert profile.ping_count == 3
    assert profile.name == "core-only.yml"
    assert not profile.checks.enabled("interface_optics_levels")
    assert store.document("core-only") == doc
    assert store.list() == ["core-only"]
    assert store.exists("core-only") and not store.exists("jiny")


def test_list_ignoruje_cizi_soubory(tmp_path):
    store = ProfileStore(tmp_path)
    store.save("b", empty_document())
    store.save("a", empty_document())
    (tmp_path / "Poznamky.yml").write_text("", encoding="utf-8")
    (tmp_path / ".a.x.tmp").write_text("", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("", encoding="utf-8")
    assert store.list() == ["a", "b"]


def test_save_nevalidni_dokument_nezanecha_temp_a_puvodni_soubor_zustane(tmp_path):
    store = ProfileStore(tmp_path)
    store.save("core-only", _doc(ping_count=3))
    before = store.path("core-only").read_text(encoding="utf-8")
    with pytest.raises(ValueError) as info:
        store.save("core-only", _doc(collectors=["iface"]))
    message = str(info.value)
    assert "neznamy collector 'iface'" in message
    assert message.startswith(str(store.path("core-only")))
    assert ".tmp" not in message
    assert store.path("core-only").read_text(encoding="utf-8") == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["core-only.yml"]


def test_save_neznamy_klic_v_profile_je_chyba(tmp_path):
    store = ProfileStore(tmp_path)
    doc = _doc()
    doc["profile"]["servicetypes"] = ["IPVPN"]
    with pytest.raises(ValueError, match="neznamy klic servicetypes"):
        store.save("x", doc)
    assert list(tmp_path.iterdir()) == []


def test_save_odmitne_spatne_jmeno_bez_zapisu(tmp_path):
    store = ProfileStore(tmp_path)
    with pytest.raises(ValueError, match="nevalidni jmeno profilu"):
        store.save("Core Only", empty_document())
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_load_neexistujici_profil(tmp_path):
    store = ProfileStore(tmp_path)
    with pytest.raises(FileNotFoundError, match="profil 'neni' neexistuje"):
        store.load("neni")


def test_delete(tmp_path):
    store = ProfileStore(tmp_path)
    store.save("a", empty_document())
    store.delete("a")
    assert store.list() == []
    with pytest.raises(FileNotFoundError, match="profil 'a' neexistuje"):
        store.delete("a")


def test_document_from_profile():
    profile = Profile(
        checks=CheckConfig({"bgp_prefix_counts": {"tolerance_percent": -5}}),
        collectors=["bgp"], service_types=None, ping_count=None, name="x.yml",
    )
    doc = document_from_profile(profile)
    assert doc == {
        "profile": {"collectors": ["bgp"], "service_types": None, "ping_count": None},
        "checks": {"bgp_prefix_counts": {"tolerance_percent": -5}},
    }
    assert doc["checks"] is not profile.checks.raw
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/test_profile_store.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'migration_validator.profiles'` (and `ImportError` for `PING_COUNT_DEFAULT`).

- [ ] **Step 3: Implement**

In `migration_validator/config.py`, right after the `DEFAULTS` dict add:

```python
# Vychozi pocet pingu, kdyz ho nezada ani CLI ani profil. Sdileny s
# runs/orchestrate a s katalogem profilu (GUI formular ukazuje defaulty).
PING_COUNT_DEFAULT = 5
```

Replace the two literal `5` fallbacks: in `migration_validator/cli.py` the two lines `ping_count = _pick(args.ping_count, profile.ping_count, 5)` become `ping_count = _pick(args.ping_count, profile.ping_count, PING_COUNT_DEFAULT)` (extend the existing `from migration_validator.config import Profile, default_profile, load_profile` import with `PING_COUNT_DEFAULT`), and in `migration_validator/runs/orchestrate.py` line 183 `(profile.ping_count or 5)` becomes `(profile.ping_count or PING_COUNT_DEFAULT)` (add the import next to the existing `Profile` import there).

Create `migration_validator/profiles/__init__.py`:

```python
"""Profile store - pojmenovane profily v profiles/<name>.yml (GUI vlna 2026-09, spec 3)."""
```

Create `migration_validator/profiles/store.py`:

```python
"""Profile store - profiles/<name>.yml, jeden soubor na profil.

Ulozeni validuje pres existujici load_profile (jediny zdroj pravdy pro
format profilu): dokument se vypise do docasneho souboru ve stejnem
adresari, nacte se, a teprve pak se prejmenuje pres cil. Nevalidni
dokument tedy nikdy neprepise platny soubor a nenecha za sebou temp.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from migration_validator.config import Profile, load_profile

PROFILE_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


def check_profile_name(name: str) -> None:
    if not PROFILE_NAME_RE.match(name):
        raise ValueError(
            f"nevalidni jmeno profilu '{name}' - povolene znaky: a-z 0-9 _ -"
        )


def empty_document() -> dict[str, Any]:
    """Plny tvar dokumentu s null = "nenastaveno"."""
    return {
        "profile": {"collectors": None, "service_types": None, "ping_count": None},
        "checks": {},
    }


def document_from_profile(profile: Profile) -> dict[str, Any]:
    return {
        "profile": {
            "collectors": list(profile.collectors) if profile.collectors is not None else None,
            "service_types": list(profile.service_types)
            if profile.service_types is not None else None,
            "ping_count": profile.ping_count,
        },
        "checks": {
            check_id: dict(overrides)
            for check_id, overrides in profile.checks.raw.items()
        },
    }


def document_to_yaml(document: dict[str, Any]) -> str:
    """Jediny serializer - save i preview. Null klice a prazdna sekce
    checks se vynechaji, soubor tak nese jen to, co je nastavene.
    Nezname klice zustavaji (save je nechava spadnout v load_profile)."""
    section = {
        key: value
        for key, value in (document.get("profile") or {}).items()
        if value is not None
    }
    checks: dict[str, dict[str, Any]] = {}
    for check_id, overrides in (document.get("checks") or {}).items():
        kept = {k: v for k, v in (overrides or {}).items() if v is not None}
        if kept:
            checks[check_id] = kept
    data: dict[str, Any] = {}
    if section:
        data["profile"] = section
    if checks:
        data["checks"] = checks
    if not data:
        return ""
    return yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, default_flow_style=False
    )


@dataclass(frozen=True)
class ProfileStore:
    root: Path

    def path(self, name: str) -> Path:
        check_profile_name(name)
        return self.root / f"{name}.yml"

    def list(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            entry.stem
            for entry in self.root.glob("*.yml")
            if entry.is_file() and PROFILE_NAME_RE.match(entry.stem)
        )

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def load(self, name: str) -> Profile:
        path = self.path(name)
        if not path.is_file():
            raise FileNotFoundError(f"profil '{name}' neexistuje ({path})")
        return load_profile(path)

    def document(self, name: str) -> dict[str, Any]:
        return document_from_profile(self.load(name))

    def save(self, name: str, document: dict[str, Any]) -> Path:
        path = self.path(name)
        self.root.mkdir(parents=True, exist_ok=True)
        text = document_to_yaml(document)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.root, prefix=f".{name}.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            try:
                load_profile(tmp)
            except ValueError as error:
                # Hlaska loaderu beze zmeny, jen s cilovou cestou misto temp.
                raise ValueError(str(error).replace(str(tmp), str(path))) from error
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
        return path

    def delete(self, name: str) -> None:
        path = self.path(name)
        if not path.is_file():
            raise FileNotFoundError(f"profil '{name}' neexistuje ({path})")
        path.unlink()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pyats-venv/bin/pytest tests/test_profile_store.py tests/test_profile.py tests/test_cli.py tests/runs -q`
Expected: all PASS. Then `pyats-venv/bin/pytest -q` — all green.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/config.py migration_validator/cli.py migration_validator/runs/orchestrate.py migration_validator/profiles tests/test_profile_store.py
git commit -m "feat(profiles): ProfileStore - profiles/<name>.yml, validace pres load_profile, atomicky zapis"
```

---

### Task 2: `create_run` validates the profile, `--profiles-root` flag, `create_app(profiles_root=...)`

**Files:**
- Modify: `migration_validator/api.py` (`create_run`)
- Modify: `migration_validator/cli.py` (`_cmd_gui`, `gui` parser)
- Modify: `migration_validator/gui/app.py` (`create_app` signature, `app.state.profiles`, `create_run` route)
- Test: `tests/test_api_runs.py`, `tests/test_cli.py`, `tests/gui/test_write_routes.py`

**Interfaces:**
- Consumes: `ProfileStore`, `check_profile_name` from Task 1.
- Produces: `api.create_run(name, *, kind, devices, mappings=None, profile=None, run_root=Path("runs"), profiles_root=Path("profiles"))`; `create_app(run_root=Path("runs"), profile_path=None, profiles_root=Path("profiles"))` with `app.state.profiles: ProfileStore`; CLI `gui --profiles-root DIR`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_api_runs.py` replace `test_create_run_profil_zatim_nelze` with:

```python
def test_create_run_s_neexistujicim_profilem_je_chyba(tmp_path):
    with pytest.raises(ValueError, match="profil 'core-only' neexistuje"):
        api.create_run(
            "p", kind="single", devices=[SINGLE], profile="core-only",
            run_root=tmp_path / "runs", profiles_root=tmp_path / "profiles",
        )
    assert not (tmp_path / "runs" / "p").exists()


def test_create_run_s_existujicim_profilem(tmp_path):
    from migration_validator.profiles.store import ProfileStore, empty_document
    ProfileStore(tmp_path / "profiles").save("core-only", empty_document())
    manifest = api.create_run(
        "p", kind="single", devices=[SINGLE], profile="core-only",
        run_root=tmp_path / "runs", profiles_root=tmp_path / "profiles",
    )
    assert manifest.profile == "core-only"
    assert "profile: core-only" in (tmp_path / "runs" / "p" / "run.yml").read_text()


def test_create_run_s_nevalidnim_jmenem_profilu(tmp_path):
    with pytest.raises(ValueError, match="nevalidni jmeno profilu '../x'"):
        api.create_run(
            "p", kind="single", devices=[SINGLE], profile="../x",
            run_root=tmp_path / "runs", profiles_root=tmp_path / "profiles",
        )
```

In `tests/test_cli.py` extend `test_gui_subcommand_parsuje`:

```python
def test_gui_subcommand_parsuje():
    from pathlib import Path
    from migration_validator.cli import build_parser
    args = build_parser().parse_args(["gui", "--port", "9999"])
    assert args.gui_port == 9999
    assert args.host == "127.0.0.1"
    assert args.profiles_root == Path("profiles")
    args = build_parser().parse_args(["gui", "--profiles-root", "/tmp/p"])
    assert args.profiles_root == Path("/tmp/p")
```

Append to `tests/gui/test_write_routes.py`:

```python
def test_post_runs_s_profilem_ze_store(tmp_path):
    from migration_validator.profiles.store import ProfileStore, empty_document
    ProfileStore(tmp_path / "profiles").save("core-only", empty_document())
    client = TestClient(create_app(run_root=tmp_path / "runs", profiles_root=tmp_path / "profiles"))
    resp = client.post("/api/runs", json={
        "name": "upg01", "kind": "single", "profile": "core-only",
        "devices": [SINGLE], "mappings": [],
    })
    assert resp.status_code == 201
    assert resp.json()["profile"] == "core-only"


def test_post_runs_s_neznamym_profilem_je_409(tmp_path):
    client = TestClient(create_app(run_root=tmp_path / "runs", profiles_root=tmp_path / "profiles"))
    resp = client.post("/api/runs", json={
        "name": "upg01", "kind": "single", "profile": "neni",
        "devices": [SINGLE], "mappings": [],
    })
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith("profil 'neni' neexistuje")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/test_api_runs.py tests/test_cli.py::test_gui_subcommand_parsuje tests/gui/test_write_routes.py -q`
Expected: the new tests FAIL (`TypeError: create_run() got an unexpected keyword argument 'profiles_root'`, `AttributeError: 'Namespace' object has no attribute 'profiles_root'`, `TypeError: create_app() got an unexpected keyword argument 'profiles_root'`).

- [ ] **Step 3: Implement**

`migration_validator/api.py`: add `from migration_validator.profiles.store import ProfileStore` to the imports; change the `create_run` signature and body:

```python
def create_run(
    name: str,
    *,
    kind: str,
    devices: list[dict[str, str]],
    mappings: list[tuple[str, str]] | None = None,
    profile: str | None = None,
    run_root: str | Path = Path("runs"),
    profiles_root: str | Path = Path("profiles"),
) -> RunManifest:
    """Zalozi runs/<name>/run.yml - schopnost, kterou CLI nema (run.yml
    se dosud psal rucne).

    kind "single": prave jedno zarizeni role single, zadny mapping.
    kind "migration": prave jeden old a jeden new; mapping je volitelny,
    bez nej vznika sekvencni run s volnym capture formularem.
    `profile` je jmeno z profile store (profiles/<name>.yml); None =
    serverovy default. Neexistujici profil je chyba, run se nezalozi."""
```

Replace the block

```python
    if profile is not None:
        raise ValueError(
            f"profil '{profile}' neexistuje - profile store zatim neni k dispozici"
        )
```

with

```python
    if profile is not None:
        profiles = ProfileStore(Path(profiles_root))
        if not profiles.exists(profile):  # ValueError pri nevalidnim jmenu
            raise ValueError(f"profil '{profile}' neexistuje ({profiles.path(profile)})")
```

`migration_validator/cli.py`: in the `gui` parser add after `--run-root`:

```python
    gui.add_argument(
        "--profiles-root", type=Path, default=Path("profiles"),
        help="adresar pojmenovanych profilu (profiles/<name>.yml)",
    )
```

and in `_cmd_gui`: `app = create_app(run_root=args.run_root, profile_path=args.profile, profiles_root=args.profiles_root)`.

`migration_validator/gui/app.py`: add `from migration_validator.profiles.store import ProfileStore`; change `create_app`:

```python
def create_app(
    run_root: Path = Path("runs"),
    profile_path: str | None = None,
    profiles_root: Path = Path("profiles"),
) -> FastAPI:
    app = FastAPI(title="mig-validate")
    app.state.run_root = run_root
    app.state.profile_path = profile_path
    profiles = ProfileStore(Path(profiles_root))
    app.state.profiles = profiles
```

and in the `create_run` route pass `profiles_root=profiles.root` to `api.create_run`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pyats-venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/api.py migration_validator/cli.py migration_validator/gui/app.py tests/test_api_runs.py tests/test_cli.py tests/gui/test_write_routes.py
git commit -m "feat(api): create_run overuje profil proti profile store, gui --profiles-root"
```

---

### Task 3: Per-run profile resolution in the evaluation, snapshot and capture routes

**Files:**
- Create: `migration_validator/gui/profiles.py`
- Modify: `migration_validator/gui/app.py` (three routes)
- Test: `tests/gui/test_evaluation_routes.py`, `tests/gui/test_profiles_module.py`

**Interfaces:**
- Consumes: `ProfileStore.load`, `document_from_profile` (Task 1).
- Produces: in `gui/profiles.py`: `profile_for_run(manifest: RunManifest, manifest_path: Path, *, store: ProfileStore, default_path: str | None) -> Profile` (raises `ValueError("profil '<name>' neexistuje (<manifest_path>)")`), `server_default_profile(default_path: str | None) -> Profile`, `profile_usage(run_root: Path) -> dict[str, int]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/gui/test_profiles_module.py`:

```python
"""profile_for_run / profile_usage - reseni profilu per run."""

from pathlib import Path

import pytest

from migration_validator.gui.profiles import (
    profile_for_run,
    profile_usage,
    server_default_profile,
)
from migration_validator.profiles.store import ProfileStore, empty_document
from migration_validator.runs.manifest import RunDevice, RunManifest


def _manifest(profile=None):
    return RunManifest(
        devices={"PTX1": RunDevice(host="10.0.0.2", platform="junos-evo", role="single")},
        kind="single", profile=profile,
    )


def test_bez_profilu_vraci_serverovy_default(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    profile = profile_for_run(_manifest(), Path("runs/x/run.yml"), store=store, default_path=None)
    assert profile.name == "" and profile.collectors is None


def test_bez_profilu_vraci_soubor_z_profile_flagu(tmp_path):
    default = tmp_path / "core.yml"
    default.write_text("profile:\n  ping_count: 3\n", encoding="utf-8")
    store = ProfileStore(tmp_path / "profiles")
    profile = profile_for_run(
        _manifest(), Path("runs/x/run.yml"), store=store, default_path=str(default)
    )
    assert profile.ping_count == 3 and profile.name == "core.yml"
    assert server_default_profile(str(default)).ping_count == 3
    assert server_default_profile(None).ping_count is None


def test_profil_ze_store(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    doc = empty_document()
    doc["profile"]["ping_count"] = 2
    store.save("core-only", doc)
    profile = profile_for_run(
        _manifest("core-only"), Path("runs/x/run.yml"), store=store, default_path=None
    )
    assert profile.ping_count == 2 and profile.name == "core-only.yml"


def test_chybejici_profil_je_chyba_bez_fallbacku(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    with pytest.raises(ValueError) as info:
        profile_for_run(
            _manifest("neni"), Path("runs/x/run.yml"), store=store, default_path=None
        )
    assert str(info.value) == "profil 'neni' neexistuje (runs/x/run.yml)"


def test_profile_usage_pocita_manifesty(tmp_path):
    for run, profile in (("a", "core-only"), ("b", "core-only"), ("c", None), ("d", "jiny")):
        (tmp_path / run).mkdir()
        text = "schema_version: 1\nkind: single\ndevices: {}\n"
        if profile:
            text += f"profile: {profile}\n"
        (tmp_path / run / "run.yml").write_text(text, encoding="utf-8")
    (tmp_path / ".archive").mkdir()
    (tmp_path / ".archive" / "z-20260101T000000Z").mkdir()
    (tmp_path / ".archive" / "z-20260101T000000Z" / "run.yml").write_text(
        "profile: core-only\n", encoding="utf-8"
    )
    (tmp_path / "e").mkdir()  # bez run.yml
    assert profile_usage(tmp_path) == {"core-only": 2, "jiny": 1}
    assert profile_usage(tmp_path / "neexistuje") == {}
```

Append to `tests/gui/test_evaluation_routes.py`:

```python
# -- profil per run (spec 3) -------------------------------------------------

def _set_run_profile(tmp_path, name):
    store = RunStore(tmp_path, "mig01")
    manifest = store.load()
    manifest.profile = name
    store.save(manifest)


def _check_ids(payload):
    return {
        check["id"]
        for ev in payload["evaluations"]
        for scope in ev["result"]["scopes"]
        for check in scope["checks"]
    }


def test_run_s_chybejicim_profilem_je_422(run_se_snimky_client, tmp_path):
    _set_run_profile(tmp_path, "neni")
    resp = run_se_snimky_client.get("/api/runs/mig01/evaluation")
    assert resp.status_code == 422
    assert resp.json()["detail"] == f"profil 'neni' neexistuje ({tmp_path / 'mig01' / 'run.yml'})"
    file = next((tmp_path / "mig01").glob("snapshot_post_*.json")).name
    resp = run_se_snimky_client.get(f"/api/runs/mig01/snapshots/{file}/evaluation")
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("profil 'neni' neexistuje")


def test_run_s_profilem_pouzije_jeho_checky(run_se_snimky_client, tmp_path):
    from migration_validator.profiles.store import ProfileStore, empty_document
    assert "interface_state" in _check_ids(
        run_se_snimky_client.get("/api/runs/mig01/evaluation").json()
    )
    doc = empty_document()
    doc["checks"] = {"interface_state": {"enabled": False}}
    # create_app(run_root=tmp_path) -> profiles_root je Path("profiles") relativni
    # k cwd; fixture nema store, proto se app staví znovu s explicitnim rootem.
    ProfileStore(tmp_path / "profiles").save("bez-state", doc)
    _set_run_profile(tmp_path, "bez-state")
    client = TestClient(create_app(run_root=tmp_path, profiles_root=tmp_path / "profiles"))
    payload = client.get("/api/runs/mig01/evaluation").json()
    ids = _check_ids(payload)
    assert "interface_state" not in ids
    assert "interface_traffic" in ids
    assert payload["evaluations"][0]["result"]["profile"] == "bez-state.yml"
```

(`result["profile"]` is the `profile_name` passed to `api.evaluate`; `RunResult.to_dict` in `migration_validator/models/result.py` adds the key whenever a profile name is set, so it proves the evaluation ran under the named profile.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/gui/test_profiles_module.py tests/gui/test_evaluation_routes.py -q`
Expected: `ModuleNotFoundError: No module named 'migration_validator.gui.profiles'`; the two route tests fail (200 instead of 422, `interface_state` present).

- [ ] **Step 3: Implement**

Create `migration_validator/gui/profiles.py`:

```python
"""Ktery profil run pouziva.

run.yml `profile: <name>` ukazuje do profile store; None = serverovy
default (`--profile` flag, nebo vestaveny prazdny profil). Chybejici profil
je chyba - zadny tichy fallback na default, run by se vyhodnotil jinak,
nez si operator myslel.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from migration_validator.config import Profile, default_profile, load_profile
from migration_validator.profiles.store import ProfileStore
from migration_validator.runs.manifest import RunManifest


def server_default_profile(default_path: str | None) -> Profile:
    return load_profile(default_path) if default_path else default_profile()


def profile_for_run(
    manifest: RunManifest,
    manifest_path: Path,
    *,
    store: ProfileStore,
    default_path: str | None,
) -> Profile:
    if manifest.profile is None:
        return server_default_profile(default_path)
    try:
        return store.load(manifest.profile)
    except FileNotFoundError as error:
        raise ValueError(
            f"profil '{manifest.profile}' neexistuje ({manifest_path})"
        ) from error


def profile_usage(run_root: Path) -> dict[str, int]:
    """Kolik run.yml pod run_root odkazuje na ktery profil. Cte jen klic
    `profile` primo z YAMLu, aby rozbity manifest nezastavil vypis;
    teckovane adresare (archiv) se preskakuji jako v GET /api/runs."""
    counts: dict[str, int] = {}
    if not run_root.is_dir():
        return counts
    for entry in sorted(run_root.iterdir()):
        if entry.name.startswith("."):
            continue
        manifest_path = entry / "run.yml"
        if not manifest_path.is_file():
            continue
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        name = raw.get("profile") if isinstance(raw, dict) else None
        if name:
            counts[str(name)] = counts.get(str(name), 0) + 1
    return counts
```

In `migration_validator/gui/app.py`:

- import: `from migration_validator.gui.profiles import profile_for_run, server_default_profile`; drop `default_profile, load_profile` from the `migration_validator.config` import if nothing else uses them after the edits below (the `/api/meta` route uses them — replace its line with `profile = server_default_profile(profile_path)`).
- add a helper inside `create_app` next to `_require_store`:

```python
    def _profile_for(store: RunStore, manifest: RunManifest):
        try:
            return profile_for_run(
                manifest, store.manifest_path,
                store=profiles, default_path=profile_path,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
```

- `run_evaluation`: replace `profile = load_profile(profile_path) if profile_path else default_profile()` with `profile = _profile_for(store, manifest)`.
- `snapshot_evaluation`: the manifest is only loaded when `baseline` is given; move `manifest = store.load()` to right after `path = _require_snapshot_path(store, file)` (unconditionally) and replace the profile line with `profile = _profile_for(store, manifest)`.
- `start_capture`: inside the existing `try:` replace `profile = (load_profile(profile_path) if profile_path else default_profile())` with `profile = profile_for_run(manifest, store.manifest_path, store=profiles, default_path=profile_path)` — the surrounding `except ValueError` already maps to 503; change that mapping so a missing profile is 422 and a settings problem stays 503:

```python
        try:
            settings = load_settings()
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        profile = _profile_for(store, manifest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pyats-venv/bin/pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/profiles.py migration_validator/gui/app.py tests/gui/test_profiles_module.py tests/gui/test_evaluation_routes.py
git commit -m "feat(gui): profile_for_run - evaluace a capture berou profil z run.yml, chybejici profil je 422"
```

---

### Task 4: Catalogue

**Files:**
- Create: `migration_validator/profiles/catalogue.py`
- Create: `migration_validator/gui/profile_routes.py` (router skeleton with the catalogue route only; Task 5 adds the rest)
- Modify: `migration_validator/gui/app.py` (include router)
- Test: `tests/gui/test_profile_routes.py`

**Interfaces:**
- Produces: `build_catalogue() -> dict` with keys `collectors`, `service_types`, `ping_count_default`, `checks` (list of `{"id","title","group","default_severity","default_enabled","options": {name: {"type","default"}}}`); `build_profiles_router(store: ProfileStore, run_root: Path, profile_path: str | None) -> APIRouter`; `GET /api/profiles/catalogue`.

- [ ] **Step 1: Write the failing tests**

Create `tests/gui/test_profile_routes.py`:

```python
"""/api/profiles - store CRUD, katalog, used_by, opravneni."""

from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.config import DEFAULTS, PING_COUNT_DEFAULT
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor
from migration_validator.profiles.catalogue import build_catalogue
from migration_validator.profiles.store import ProfileStore, empty_document

SINGLE = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "single"}


def _client(tmp_path, role=None, profile_path=None):
    app = create_app(
        run_root=tmp_path / "runs", profile_path=profile_path,
        profiles_root=tmp_path / "profiles",
    )
    if role is not None:
        app.state.actor_provider = lambda request: Actor(role=role)
    return TestClient(app)


# -- katalog ----------------------------------------------------------------

def test_katalog_ma_kazdy_check_jednou_s_default_severity():
    catalogue = build_catalogue()
    ids = [c["id"] for c in catalogue["checks"]]
    assert len(ids) == len(set(ids))
    assert ids == [c["id"] for c in api.list_checks()]  # poradi registru
    by_id = {c["id"]: c for c in catalogue["checks"]}
    assert set(by_id) == {c["id"] for c in api.list_checks()}
    for described in api.list_checks():
        entry = by_id[described["id"]]
        assert entry["default_severity"] == described["default_severity"]
        assert entry["title"] == described["title"]
        assert entry["group"] == (described["requires"][0] if described["requires"] else "general")


def test_katalog_options_z_defaults_bez_severity_a_enabled():
    by_id = {c["id"]: c for c in build_catalogue()["checks"]}
    assert by_id["interface_traffic"]["options"] == {
        "tolerance_percent": {"type": "number", "default": -60},
        "require_nonzero": {"type": "boolean", "default": True},
    }
    assert by_id["interface_optics_levels"]["options"]["tolerance_db"] == {
        "type": "number", "default": 2.0,
    }
    assert by_id["traffic_ceased"]["options"] == {
        "max_residual_pps": {"type": "number", "default": 1},
    }
    assert by_id["traffic_ceased"]["default_enabled"] is False
    assert by_id["interface_state"]["default_enabled"] is True
    assert by_id["interface_state"]["options"] == {}
    assert by_id["deactivation_state"]["group"] == "general"


def test_kazdy_klic_v_defaults_je_registrovany_check():
    ids = {c["id"] for c in api.list_checks()}
    assert set(DEFAULTS) <= ids


def test_katalog_collectors_service_types_ping():
    catalogue = build_catalogue()
    assert "interfaces" in catalogue["collectors"]["junos"]
    assert "interfaces" in catalogue["collectors"]["junos-evo"]
    assert catalogue["service_types"] == ["Core", "E-LAN", "E-Line", "IPVPN", "Internet"]
    assert catalogue["ping_count_default"] == PING_COUNT_DEFAULT == 5


def test_get_catalogue_route(tmp_path):
    client = _client(tmp_path, role="viewer")
    resp = client.get("/api/profiles/catalogue")
    assert resp.status_code == 200
    assert resp.json()["checks"][0]["id"] == api.list_checks()[0]["id"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/gui/test_profile_routes.py -q`
Expected: `ModuleNotFoundError: No module named 'migration_validator.profiles.catalogue'`.

- [ ] **Step 3: Implement**

Create `migration_validator/profiles/catalogue.py`:

```python
"""Katalog pro formular profilu - defaulty bez zadratovani v GUI.

Collectory z registru collectoru (jako /api/meta), service typy z
scoping.builder, ping z config.PING_COUNT_DEFAULT, checky z registru
sloucene s config.DEFAULTS. Typ option se odvozuje z Python typu defaultu.
"""

from __future__ import annotations

from typing import Any

from migration_validator import api
from migration_validator.config import DEFAULTS, PING_COUNT_DEFAULT
from migration_validator.scoping.builder import MIGRATED_SERVICE_TYPES

_PLATFORMS = ("junos", "junos-evo")
_NOT_OPTIONS = frozenset({"severity", "enabled"})


def option_type(value: Any) -> str:
    # bool je podtrida int - musi byt prvni.
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _check_entry(described: dict[str, Any]) -> dict[str, Any]:
    defaults = DEFAULTS.get(described["id"], {})
    requires = described["requires"]
    return {
        "id": described["id"],
        "title": described["title"],
        "group": requires[0] if requires else "general",
        "default_severity": described["default_severity"],
        "default_enabled": bool(defaults.get("enabled", True)),
        "options": {
            name: {"type": option_type(value), "default": value}
            for name, value in defaults.items()
            if name not in _NOT_OPTIONS
        },
    }


def build_catalogue() -> dict[str, Any]:
    import migration_validator.collectors.all  # noqa: F401  (registrace)
    from migration_validator.collectors.registry import collectors_for

    return {
        "collectors": {
            platform: [c.name for c in collectors_for(platform)]
            for platform in _PLATFORMS
        },
        "service_types": sorted(MIGRATED_SERVICE_TYPES),
        "ping_count_default": PING_COUNT_DEFAULT,
        "checks": [_check_entry(described) for described in api.list_checks()],
    }
```

Create `migration_validator/gui/profile_routes.py`:

```python
"""Routes /api/profiles - tenke obaly nad profiles.store a profiles.catalogue.

Cteni vyzaduje view, zapis admin (spec 3). Router se sklada v create_app,
aby dostal store, run_root a serverovy default profil."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from migration_validator.gui.authz import Actor, Permission, require
from migration_validator.profiles.catalogue import build_catalogue
from migration_validator.profiles.store import ProfileStore


def build_profiles_router(
    store: ProfileStore, run_root: Path, profile_path: str | None
) -> APIRouter:
    router = APIRouter(prefix="/api/profiles")

    # Staticke cesty (catalogue, preview) musi byt registrovane pred /{name}.
    @router.get("/catalogue")
    def catalogue(actor: Actor = require(Permission.VIEW)) -> dict:
        return build_catalogue()

    return router
```

In `migration_validator/gui/app.py`: import `from migration_validator.gui.profile_routes import build_profiles_router` and, right after `app.state.actor_provider = anonymous_admin`, add `app.include_router(build_profiles_router(profiles, run_root, profile_path))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pyats-venv/bin/pytest tests/gui -q` (includes `test_kazda_api_routa_ma_authz_zavislost`, which must still pass for the router's routes).
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/profiles/catalogue.py migration_validator/gui/profile_routes.py migration_validator/gui/app.py tests/gui/test_profile_routes.py
git commit -m "feat(profiles): katalog pro formular - collectory, service typy, ping default, checky s defaulty"
```

---

### Task 5: Profile CRUD routes

**Files:**
- Modify: `migration_validator/gui/profile_routes.py`
- Test: `tests/gui/test_profile_routes.py`

**Interfaces:**
- Consumes: `ProfileStore`, `document_to_yaml`, `document_from_profile`, `empty_document` (Task 1); `server_default_profile`, `profile_usage` (Task 3).
- Produces: `GET /api/profiles` → `{"default": <basename of --profile file>|null, "default_document": {...}, "profiles": [{"name","used_by"}]}`; `GET /api/profiles/{name}` → `{"name","document"}`; `POST /api/profiles` `{"name","document"}` → 201 `{"name","document"}`; `PUT /api/profiles/{name}` `{"document"}` → 200 `{"name","document"}`; `DELETE /api/profiles/{name}` → 204; `POST /api/profiles/preview` `{"document"}` → `{"yaml"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/gui/test_profile_routes.py`:

```python
# -- CRUD -------------------------------------------------------------------

def _doc(**profile):
    doc = empty_document()
    doc["profile"].update(profile)
    return doc


def test_list_prazdny_store_a_default(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/profiles").json() == {
        "default": None, "default_document": empty_document(), "profiles": [],
    }


def test_list_default_z_profile_flagu(tmp_path):
    default = tmp_path / "core.yml"
    default.write_text("profile:\n  ping_count: 3\n", encoding="utf-8")
    client = _client(tmp_path, profile_path=str(default))
    body = client.get("/api/profiles").json()
    assert body["default"] == "core.yml"
    assert body["default_document"]["profile"]["ping_count"] == 3


def test_post_get_put_delete(tmp_path):
    client = _client(tmp_path)
    doc = _doc(collectors=["interfaces", "bgp"], ping_count=3)
    resp = client.post("/api/profiles", json={"name": "core-only", "document": doc})
    assert resp.status_code == 201
    assert resp.json() == {"name": "core-only", "document": doc}
    assert (tmp_path / "profiles" / "core-only.yml").is_file()

    assert client.get("/api/profiles/core-only").json() == {"name": "core-only", "document": doc}
    assert client.get("/api/profiles").json()["profiles"] == [{"name": "core-only", "used_by": 0}]

    doc2 = _doc(service_types=["IPVPN"])
    doc2["checks"] = {"interface_optics_levels": {"enabled": False}}
    resp = client.put("/api/profiles/core-only", json={"document": doc2})
    assert resp.status_code == 200
    assert resp.json()["document"] == doc2
    assert client.get("/api/profiles/core-only").json()["document"] == doc2

    assert client.delete("/api/profiles/core-only").status_code == 204
    assert client.get("/api/profiles/core-only").status_code == 404
    assert client.get("/api/profiles").json()["profiles"] == []


def test_post_existujici_je_409(tmp_path):
    client = _client(tmp_path)
    client.post("/api/profiles", json={"name": "a", "document": empty_document()})
    resp = client.post("/api/profiles", json={"name": "a", "document": empty_document()})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "profil 'a' uz existuje"


def test_post_nevalidni_jmeno_je_422(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/profiles", json={"name": "Core Only", "document": empty_document()})
    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("nevalidni jmeno profilu 'Core Only'")


def test_post_neznamy_collector_je_422_s_hlaskou_loaderu(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/profiles", json={"name": "x", "document": _doc(collectors=["iface"])})
    assert resp.status_code == 422
    assert "neznamy collector 'iface'" in resp.json()["detail"]
    assert not (tmp_path / "profiles" / "x.yml").exists()


def test_put_neexistujici_je_404(tmp_path):
    client = _client(tmp_path)
    resp = client.put("/api/profiles/neni", json={"document": empty_document()})
    assert resp.status_code == 404
    assert resp.json()["detail"].startswith("profil 'neni' neexistuje")
    assert client.delete("/api/profiles/neni").status_code == 404


def test_used_by_a_delete_odmitnut_dokud_je_pouzity(tmp_path):
    client = _client(tmp_path)
    client.post("/api/profiles", json={"name": "core-only", "document": empty_document()})
    for run in ("a", "b"):
        api.create_run(
            run, kind="single", devices=[{**SINGLE, "node": run}], profile="core-only",
            run_root=tmp_path / "runs", profiles_root=tmp_path / "profiles",
        )
    assert client.get("/api/profiles").json()["profiles"] == [{"name": "core-only", "used_by": 2}]
    resp = client.delete("/api/profiles/core-only")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "profil pouziva 2 runu"
    assert (tmp_path / "profiles" / "core-only.yml").is_file()


def test_preview_je_shodny_se_souborem(tmp_path):
    client = _client(tmp_path)
    doc = _doc(collectors=["interfaces"], ping_count=None)
    doc["checks"] = {"interface_traffic": {"tolerance_percent": -40}}
    preview = client.post("/api/profiles/preview", json={"document": doc}).json()["yaml"]
    client.post("/api/profiles", json={"name": "p", "document": doc})
    assert preview == (tmp_path / "profiles" / "p.yml").read_text(encoding="utf-8")
    assert preview == "profile:\n  collectors:\n  - interfaces\nchecks:\n  interface_traffic:\n    tolerance_percent: -40\n"


def test_get_rozbity_soubor_je_422(tmp_path):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "bad.yml").write_text("cheks: {}\n", encoding="utf-8")
    client = _client(tmp_path)
    resp = client.get("/api/profiles/bad")
    assert resp.status_code == 422
    assert "neznamy klic cheks" in resp.json()["detail"]


def test_viewer_cte_ale_nezapisuje(tmp_path):
    admin = _client(tmp_path)
    admin.post("/api/profiles", json={"name": "a", "document": empty_document()})
    viewer = _client(tmp_path, role="viewer")
    assert viewer.get("/api/profiles").status_code == 200
    assert viewer.get("/api/profiles/a").status_code == 200
    assert viewer.get("/api/profiles/catalogue").status_code == 200
    for resp in (
        viewer.post("/api/profiles", json={"name": "b", "document": empty_document()}),
        viewer.put("/api/profiles/a", json={"document": empty_document()}),
        viewer.delete("/api/profiles/a"),
        viewer.post("/api/profiles/preview", json={"document": empty_document()}),
    ):
        assert resp.status_code == 403
        assert resp.json()["detail"] == "nedostatecne opravneni: vyzaduje admin"


def test_operator_take_nezapisuje(tmp_path):
    operator = _client(tmp_path, role="operator")
    resp = operator.post("/api/profiles", json={"name": "b", "document": empty_document()})
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/gui/test_profile_routes.py -q`
Expected: the new tests fail with 404/405 responses.

- [ ] **Step 3: Implement**

Replace `migration_validator/gui/profile_routes.py` with:

```python
"""Routes /api/profiles - tenke obaly nad profiles.store a profiles.catalogue.

Cteni vyzaduje view, zapis admin (spec 3). Router se sklada v create_app,
aby dostal store, run_root a serverovy default profil.

Mapovani chyb: nevalidni jmeno / nevalidni dokument (ValueError z loaderu)
-> 422 s hlaskou beze zmeny; chybejici profil -> 404; existujici pri POST a
pouzivany pri DELETE -> 409; I/O chyba store -> 500 s hlaskou OS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from migration_validator.gui.authz import Actor, Permission, require
from migration_validator.gui.profiles import profile_usage, server_default_profile
from migration_validator.profiles.catalogue import build_catalogue
from migration_validator.profiles.store import (
    ProfileStore,
    check_profile_name,
    document_from_profile,
    document_to_yaml,
)


class ProfileBody(BaseModel):
    name: str
    document: dict[str, Any]


class DocumentBody(BaseModel):
    document: dict[str, Any]


def build_profiles_router(
    store: ProfileStore, run_root: Path, profile_path: str | None
) -> APIRouter:
    router = APIRouter(prefix="/api/profiles")

    def _name_or_422(name: str) -> None:
        try:
            check_profile_name(name)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def _document_or_error(name: str) -> dict[str, Any]:
        _name_or_422(name)
        try:
            return store.document(name)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except OSError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    def _save_or_error(name: str, document: dict[str, Any]) -> dict[str, Any]:
        try:
            store.save(name, document)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except OSError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return {"name": name, "document": _document_or_error(name)}

    # Staticke cesty (catalogue, preview) musi byt registrovane pred /{name}.
    @router.get("/catalogue")
    def catalogue(actor: Actor = require(Permission.VIEW)) -> dict:
        return build_catalogue()

    @router.post("/preview")
    def preview(body: DocumentBody, actor: Actor = require(Permission.ADMIN)) -> dict:
        return {"yaml": document_to_yaml(body.document)}

    @router.get("")
    def list_profiles(actor: Actor = require(Permission.VIEW)) -> dict:
        try:
            default = server_default_profile(profile_path)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        usage = profile_usage(run_root)
        return {
            "default": Path(profile_path).name if profile_path else None,
            "default_document": document_from_profile(default),
            "profiles": [
                {"name": name, "used_by": usage.get(name, 0)} for name in store.list()
            ],
        }

    @router.post("", status_code=201)
    def create_profile(body: ProfileBody, actor: Actor = require(Permission.ADMIN)) -> dict:
        _name_or_422(body.name)
        if store.exists(body.name):
            raise HTTPException(status_code=409, detail=f"profil '{body.name}' uz existuje")
        return _save_or_error(body.name, body.document)

    @router.get("/{name}")
    def get_profile(name: str, actor: Actor = require(Permission.VIEW)) -> dict:
        return {"name": name, "document": _document_or_error(name)}

    @router.put("/{name}")
    def update_profile(
        name: str, body: DocumentBody, actor: Actor = require(Permission.ADMIN)
    ) -> dict:
        _name_or_422(name)
        if not store.exists(name):
            raise HTTPException(
                status_code=404, detail=f"profil '{name}' neexistuje ({store.path(name)})"
            )
        return _save_or_error(name, body.document)

    @router.delete("/{name}", status_code=204)
    def delete_profile(name: str, actor: Actor = require(Permission.ADMIN)) -> Response:
        _name_or_422(name)
        if not store.exists(name):
            raise HTTPException(
                status_code=404, detail=f"profil '{name}' neexistuje ({store.path(name)})"
            )
        used = profile_usage(run_root).get(name, 0)
        if used:
            raise HTTPException(status_code=409, detail=f"profil pouziva {used} runu")
        try:
            store.delete(name)
        except OSError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return Response(status_code=204)

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pyats-venv/bin/pytest -q`
Expected: all green (including the authz route-guard test).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/profile_routes.py tests/gui/test_profile_routes.py
git commit -m "feat(gui): /api/profiles - list s used_by, get/post/put/delete, preview; zapis jen admin"
```

---

### Task 6: Frontend plumbing — document helpers, `Profiles` button, profile picker in New run, profile link in run overview

**Files:**
- Modify: `migration_validator/gui/static/view.js` (append helpers before the `MigView` export block)
- Modify: `migration_validator/gui/static/index.html:32` (`btn-checks` → `btn-profiles`)
- Modify: `migration_validator/gui/static/app.js` (`GUIDE_TEXT`, constructor, `loadProfiles`, `goToProfiles` stub, `buildProfilePicker`, `submitNewRun`, `renderRunOverview` header, `render` switch)
- Modify: `migration_validator/gui/static/style.css`
- Test: `tests/js/view.test.js`

**Interfaces:**
- Produces in `view.js` (all attached to `MigView`): `emptyProfileDocument() -> doc`, `normalizeProfileDocument(doc) -> doc` (same rule as Python `document_to_yaml`: drops null values, empty check overrides, empty sections), `profileDirty(doc, savedDoc) -> boolean`, `toggleListValue(list|null, value) -> list|null` (adds when absent, removes when present, returns `null` when the result is empty).
- Produces in `app.js`: `this.cache.profiles` (`GET /api/profiles` body), `async loadProfiles()`, `this.state.newRunForm.profile: string` (`""` = default), `goToProfiles(name|null)` (Task 7 fills the editor; in this task it sets `view = "profiles"` and renders the existing registry table).

- [ ] **Step 1: Write the failing JS tests**

Append to `tests/js/view.test.js`:

```js
test("emptyProfileDocument: full shape with nulls", () => {
  assert.deepStrictEqual(MigView.emptyProfileDocument(), {
    profile: { collectors: null, service_types: null, ping_count: null },
    checks: {},
  });
});

test("normalizeProfileDocument: drops nulls, empty overrides, empty sections", () => {
  const doc = {
    profile: { collectors: ["bgp"], service_types: null, ping_count: null },
    checks: { a: { x: 1, y: null }, b: { z: null }, c: {} },
  };
  assert.deepStrictEqual(MigView.normalizeProfileDocument(doc), {
    profile: { collectors: ["bgp"] },
    checks: { a: { x: 1 } },
  });
  assert.deepStrictEqual(MigView.normalizeProfileDocument(MigView.emptyProfileDocument()), {});
  assert.deepStrictEqual(MigView.normalizeProfileDocument({ profile: { collectors: [] } }), {});
});

test("profileDirty: equal after normalisation is clean", () => {
  const a = { profile: { collectors: null, ping_count: 5 }, checks: {} };
  const b = { profile: { ping_count: 5 }, checks: { x: {} } };
  assert.strictEqual(MigView.profileDirty(a, b), false);
  assert.strictEqual(MigView.profileDirty(a, { profile: { ping_count: 3 }, checks: {} }), true);
  assert.strictEqual(MigView.profileDirty({ profile: { collectors: ["a", "b"] } }, { profile: { collectors: ["b", "a"] } }), true);
});

test("toggleListValue: add, remove, null when empty", () => {
  assert.deepStrictEqual(MigView.toggleListValue(null, "bgp"), ["bgp"]);
  assert.deepStrictEqual(MigView.toggleListValue(["bgp"], "arp"), ["bgp", "arp"]);
  assert.deepStrictEqual(MigView.toggleListValue(["bgp", "arp"], "bgp"), ["arp"]);
  assert.strictEqual(MigView.toggleListValue(["bgp"], "bgp"), null);
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `node --test tests/js/`
Expected: 4 new failures (`MigView.emptyProfileDocument is not a function`, ...).

- [ ] **Step 3: Implement the helpers**

In `migration_validator/gui/static/view.js`, before the line that builds the `MigView` object (the block ending with `if (typeof module !== "undefined" && module.exports) module.exports = MigView;`), add:

```js
/* Profile document helpers (spec 3). The normalisation mirrors
   profiles/store.py document_to_yaml: null = "not set" and is dropped, an
   override dict with nothing left is dropped, an empty section is dropped. */
function emptyProfileDocument() {
  return { profile: { collectors: null, service_types: null, ping_count: null }, checks: {} };
}

function normalizeProfileDocument(doc) {
  const out = {};
  const section = {};
  for (const [key, value] of Object.entries((doc && doc.profile) || {})) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    section[key] = value;
  }
  if (Object.keys(section).length) out.profile = section;
  const checks = {};
  for (const [id, overrides] of Object.entries((doc && doc.checks) || {})) {
    const kept = {};
    for (const [key, value] of Object.entries(overrides || {})) {
      if (value !== null && value !== undefined) kept[key] = value;
    }
    if (Object.keys(kept).length) checks[id] = kept;
  }
  if (Object.keys(checks).length) out.checks = checks;
  return out;
}

function profileDirty(doc, savedDoc) {
  return JSON.stringify(normalizeProfileDocument(doc)) !== JSON.stringify(normalizeProfileDocument(savedDoc));
}

function toggleListValue(list, value) {
  const current = Array.isArray(list) ? list : [];
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
  return next.length ? next : null;
}
```

and add `emptyProfileDocument, normalizeProfileDocument, profileDirty, toggleListValue` to the `MigView` object. (Note: `normalizeProfileDocument` drops an empty array too, because the store treats `[]` and `null` alike for the chip pickers — an empty list means "all".)

Run `node --test tests/js/` — 31 tests pass.

- [ ] **Step 4: Rename the topbar button and wire the picker, link and view**

`index.html`: replace `<button class="btn btn-secondary" id="btn-checks">Checks</button>` with `<button class="btn btn-secondary" id="btn-profiles">Profiles</button>`.

`app.js`:

1. `GUIDE_TEXT`: rename the `checks` entry to `profiles`:

```js
  profiles: {
    title: "Profiles",
    body: [
      "Profil říká, co run měří: které collectory se sbírají, které typy služeb se hodnotí, kolik pingů se posílá a jak jsou nastavené checky. Ukládá se do profiles/<název>.yml, run si ho vybírá při založení.",
      "(default) je serverový profil z --profile (nebo vestavěný prázdný) a v GUI se needituje — Duplicate z něj udělá pojmenovanou kopii.",
      "Prázdný výběr collectorů nebo typů služeb znamená všechny. Profil, který používá nějaký run, nejde smazat.",
    ],
  },
```

and in the `newrun` entry replace the last paragraph `"Profil zatím zůstává serverový default; výběr profilu přinese další vlna."` with `"Profil vyber ze seznamu profiles/ — (default) je serverový profil. Profily spravuješ tlačítkem Profiles v horní liště."`.

2. Constructor: `this.state.view` stays `"run"`; rename `this.btnChecksEl = document.getElementById("btn-checks")` to `this.btnProfilesEl = document.getElementById("btn-profiles")` and its listener to `this.btnProfilesEl.addEventListener("click", () => this.goToProfiles(null));`. Add to `this.cache`: `profiles: null, profilesError: null`.

3. Add next to `loadMeta`:

```js
  async loadProfiles() {
    this.cache.profiles = null;
    this.cache.profilesError = null;
    try {
      const res = await fetch("/api/profiles");
      if (res.ok) {
        this.cache.profiles = await res.json();
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.profilesError = {
          status: res.status,
          detail: body.detail || `profily se nepodarilo nacist (${res.status})`,
        };
      }
    } catch (err) {
      this.cache.profilesError = { status: 0, detail: String(err) };
    }
  }
```

4. Replace `goToChecks` with a stub that Task 7 extends:

```js
  async goToProfiles(name) {
    this.state.view = "profiles";
    this.state.selectedSnapshot = null;
    await Promise.all([this.loadProfiles(), this.loadChecks()]);
    this.render();
  }
```

5. `render()`: `this.btnProfilesEl.classList.toggle("btn-toggle-active", this.state.view === "profiles");` and the switch case `case "profiles": this.renderProfilesView(); break;` (drop `case "checks"`). Rename `renderChecksView` to `renderProfilesView` and its breadcrumb label to `"profiles"`; the body stays (the registry table) until Task 7 replaces it.

6. `openNewRunForm`: add `profile: ""` to the form state and load the store before rendering:

```js
  async openNewRunForm() {
    this.state.view = "newrun";
    this.state.selectedSnapshot = null;
    this.state.newRunForm = {
      name: "",
      kind: MigView.defaultRunKind(this.cache.runs),
      profile: "",
      ...
    };
    this.render();
    await this.loadProfiles();
    this.render();
  }
```

7. `buildProfilePicker(form)`:

```js
  buildProfilePicker(form) {
    const store = this.cache.profiles;
    const select = el("select", { className: "form-select mono" });
    select.appendChild(el("option", { text: "(default)", attrs: { value: "" } }));
    for (const entry of (store && store.profiles) || []) {
      select.appendChild(el("option", { text: entry.name, attrs: { value: entry.name } }));
    }
    select.value = form.profile || "";
    select.addEventListener("change", (e) => {
      form.profile = e.target.value;
    });
    const children = [select];
    if (this.cache.profilesError) {
      children.push(el("div", { className: "field-error", text: this.cache.profilesError.detail }));
    }
    return this.buildCaptureField("Profile", el("div", { children }));
  }
```

Update the single call site in `renderNewRunForm` to `this.buildProfilePicker(form)`, and in `submitNewRun` send `profile: form.profile || null`.

8. `renderRunOverview`: after the `header` element is created (before the `if (single && singleDevice)` branch), append the profile line to the header:

```js
    const profileName = detail.profile || null;
    header.appendChild(
      el("button", {
        className: "profile-link mono",
        text: profileName ? `profile: ${profileName}` : "profile: (default)",
        attrs: { type: "button", title: "otevřít profil" },
        onClick: () => this.goToProfiles(profileName),
      })
    );
```

`style.css`: add after `.run-header .subtitle { ... }`:

```css
.profile-link {
  border: 0;
  background: none;
  padding: 0;
  font-size: 12.5px;
  color: #1a56db;
  cursor: pointer;
  align-self: baseline;
}
.profile-link:hover { text-decoration: underline; }
```

- [ ] **Step 5: Verify**

`node --test tests/js/` (31 pass) and `pyats-venv/bin/pytest tests/gui -q` (green). Then start the GUI against a scratch copy of `runs-example` (`.venv/bin/mig-validate gui --run-root <scratch>/runs --profiles-root <scratch>/profiles`) and check:

1. Topbar shows `Profiles`; clicking it shows the registry table under the `profiles` breadcrumb.
2. New run: the Profile select is enabled and lists `(default)`; after creating `profiles/core-only.yml` via `curl -X POST localhost:8321/api/profiles -H 'content-type: application/json' -d '{"name":"core-only","document":{"profile":{},"checks":{}}}'` and reopening the form it lists `core-only`; a run created with it shows `profile: core-only` under the title and `run.yml` contains `profile: core-only`.
3. Clicking the profile link opens the Profiles view.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui/static tests/js/view.test.js
git commit -m "feat(gui): Profiles tlacitko, profile picker v New run ze store, odkaz na profil v prehledu runu"
```

---

### Task 7: Profile editor page

**Files:**
- Modify: `migration_validator/gui/static/app.js` (editor state, `goToProfiles`, `renderProfilesView`, toolbar actions, form builders, save/discard, unsaved-changes guard)
- Modify: `migration_validator/gui/static/style.css`

**Interfaces:**
- Consumes: `GET /api/profiles` (`default`, `default_document`, `profiles[{name, used_by}]`), `GET /api/profiles/catalogue`, `GET/POST/PUT/DELETE /api/profiles[/{name}]` (Task 5); `MigView.emptyProfileDocument/normalizeProfileDocument/profileDirty/toggleListValue` (Task 6).
- Produces: `this.state.profileEditor = { name: string|null, doc, saved, saving, error, loadError }` (`name === null` is `(default)`); `this.cache.catalogue`; `leaveGuard()` used by every navigation entry point.

- [ ] **Step 1: State, loading and the guard**

In the constructor add `profileEditor: null` to `this.state` and `catalogue: null, catalogueError: null` to `this.cache`. Add:

```js
  async loadCatalogue() {
    if (this.cache.catalogue) return;
    try {
      const res = await fetch("/api/profiles/catalogue");
      if (res.ok) {
        this.cache.catalogue = await res.json();
        this.cache.catalogueError = null;
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.catalogueError = body.detail || `katalog se nepodarilo nacist (${res.status})`;
      }
    } catch (err) {
      this.cache.catalogueError = String(err);
    }
  }

  editorDirty() {
    const editor = this.state.profileEditor;
    return !!editor && editor.name !== null && MigView.profileDirty(editor.doc, editor.saved);
  }

  // Every navigation away from the editor goes through here (spec §5:
  // "navigating away with unsaved changes asks for confirmation").
  leaveGuard() {
    if (!this.editorDirty()) return true;
    return window.confirm("Profil má neuložené změny. Zahodit je?");
  }

  async goToProfiles(name) {
    if (this.state.view === "profiles" && !this.leaveGuard()) return;
    this.state.view = "profiles";
    this.state.selectedSnapshot = null;
    this.state.profileEditor = { name, doc: null, saved: null, saving: false, error: null, loadError: null };
    this.render();
    await Promise.all([this.loadProfiles(), this.loadCatalogue(), this.loadChecks()]);
    await this.loadEditorDocument();
    this.render();
  }

  async loadEditorDocument() {
    const editor = this.state.profileEditor;
    if (!editor) return;
    const store = this.cache.profiles;
    if (editor.name === null) {
      const doc = (store && store.default_document) || MigView.emptyProfileDocument();
      editor.doc = JSON.parse(JSON.stringify(doc));
      editor.saved = JSON.parse(JSON.stringify(doc));
      return;
    }
    try {
      const res = await fetch(`/api/profiles/${editor.name}`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        editor.loadError = body.detail || `profil se nepodarilo nacist (${res.status})`;
        return;
      }
      const { document } = await res.json();
      editor.doc = document;
      editor.saved = JSON.parse(JSON.stringify(document));
    } catch (err) {
      editor.loadError = String(err);
    }
  }
```

Call `leaveGuard()` at the top of `selectRun` (before `if (this.state.run === name) return;` — `if (!this.leaveGuard()) return;`), `openNewRunForm`, `openCaptureForm`, `backToRun`, and `openArchiveModal`. Add in the constructor:

```js
    window.addEventListener("beforeunload", (e) => {
      if (this.editorDirty()) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
```

Also make `renderGuide`, `renderSidebar` etc. unaffected: the sidebar keeps showing the current run's snapshots while the editor is open (same as the old checks view).

- [ ] **Step 2: Toolbar actions**

```js
  async createProfileFromDocument(name, doc) {
    const res = await fetch("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, document: doc }),
    });
    if (res.status === 201) return null;
    const body = await res.json().catch(() => ({}));
    return body.detail || `profil se nepodarilo vytvorit (${res.status})`;
  }

  async newProfile(fromDoc) {
    if (!this.leaveGuard()) return;
    const name = window.prompt("Název nového profilu (a-z 0-9 _ -):", "");
    if (name === null) return;
    const trimmed = name.trim();
    if (!/^[a-z0-9_-]+$/.test(trimmed)) {
      this.state.profileEditor.error = `nevalidni jmeno profilu '${trimmed}' - povolene znaky: a-z 0-9 _ -`;
      this.render();
      return;
    }
    const doc = fromDoc ? MigView.normalizeProfileDocument(fromDoc) : MigView.emptyProfileDocument();
    const error = await this.createProfileFromDocument(trimmed, doc);
    if (error) {
      this.state.profileEditor.error = error;
      this.render();
      return;
    }
    this.state.profileEditor = null; // no guard prompt on the way in
    await this.goToProfiles(trimmed);
  }

  async deleteProfile() {
    const editor = this.state.profileEditor;
    if (!editor || editor.name === null) return;
    if (!window.confirm(`Smazat profil ${editor.name}? Soubor profiles/${editor.name}.yml zmizí.`)) return;
    try {
      const res = await fetch(`/api/profiles/${editor.name}`, { method: "DELETE" });
      if (res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        editor.error = body.detail || `profil se nepodarilo smazat (${res.status})`;
        this.render();
        return;
      }
    } catch (err) {
      editor.error = String(err);
      this.render();
      return;
    }
    this.state.profileEditor = null;
    await this.goToProfiles(null);
  }

  async saveProfile() {
    const editor = this.state.profileEditor;
    if (!editor || editor.name === null || editor.saving) return;
    editor.saving = true;
    editor.error = null;
    this.render();
    try {
      const res = await fetch(`/api/profiles/${editor.name}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document: editor.doc }),
      });
      if (res.ok) {
        const { document } = await res.json();
        editor.doc = document;
        editor.saved = JSON.parse(JSON.stringify(document));
        await this.loadProfiles();
      } else {
        const body = await res.json().catch(() => ({}));
        editor.error = body.detail || `profil se nepodarilo ulozit (${res.status})`;
      }
    } catch (err) {
      editor.error = String(err);
    }
    editor.saving = false;
    this.render();
  }

  discardProfile() {
    const editor = this.state.profileEditor;
    if (!editor || !editor.saved) return;
    editor.doc = JSON.parse(JSON.stringify(editor.saved));
    editor.error = null;
    this.render();
  }
```

- [ ] **Step 3: Form builders**

```js
  // Chip picker: chosen values as chips with ×, a native <select> as the
  // "+ add" menu (no positioning code). Empty = all, shown as "(vsechny)".
  buildChipPicker(label, values, options, onChange, readonly) {
    const chips = el("div", { className: "chips" });
    const chosen = Array.isArray(values) ? values : [];
    if (chosen.length === 0) chips.appendChild(el("span", { className: "chip-empty", text: "(vsechny)" }));
    for (const value of chosen) {
      const chip = el("span", { className: "chip mono", text: value });
      if (!readonly) {
        chip.appendChild(
          el("button", {
            className: "chip-x",
            text: "×",
            attrs: { type: "button", title: `odebrat ${value}` },
            onClick: () => onChange(MigView.toggleListValue(chosen, value)),
          })
        );
      }
      chips.appendChild(chip);
    }
    if (!readonly) {
      const add = el("select", { className: "chip-add mono" });
      add.appendChild(el("option", { text: "+ add", attrs: { value: "" } }));
      for (const option of options.filter((o) => !chosen.includes(o))) {
        add.appendChild(el("option", { text: option, attrs: { value: option } }));
      }
      add.addEventListener("change", (e) => {
        if (e.target.value) onChange(MigView.toggleListValue(chosen, e.target.value));
      });
      chips.appendChild(add);
    }
    return el("div", {
      className: "form-field",
      children: [
        el("label", { className: "field-label", children: [
          document.createTextNode(label + " "),
          el("span", { className: "field-hint", text: "(empty = all)" }),
        ] }),
        chips,
      ],
    });
  }

  buildProfileSectionForm(editor, readonly) {
    const catalogue = this.cache.catalogue || { collectors: {}, service_types: [], ping_count_default: 5 };
    const collectorOptions = [...new Set(Object.values(catalogue.collectors).flat())].sort();
    const section = editor.doc.profile || (editor.doc.profile = { collectors: null, service_types: null, ping_count: null });
    const set = (key, value) => {
      section[key] = value;
      this.render();
    };

    const ping = el("input", {
      className: "form-input mono ping-input",
      attrs: { type: "number", min: "1", step: "1", placeholder: `${catalogue.ping_count_default} (default)` },
    });
    ping.value = section.ping_count === null || section.ping_count === undefined ? "" : String(section.ping_count);
    if (readonly) ping.setAttribute("disabled", "disabled");
    ping.addEventListener("change", (e) => {
      const raw = e.target.value.trim();
      set("ping_count", raw === "" ? null : Number(raw));
    });

    return el("div", {
      className: "form-card",
      children: [
        el("div", { className: "form-section-label", text: "Profile" }),
        el("div", {
          className: "profile-grid",
          children: [
            this.buildChipPicker("Collectors", section.collectors, collectorOptions,
              (v) => set("collectors", v), readonly),
            this.buildChipPicker("Service types", section.service_types, catalogue.service_types,
              (v) => set("service_types", v), readonly),
          ],
        }),
        this.buildCaptureField("Ping count", ping),
      ],
    });
  }

  buildProfileToolbar(editor) {
    const store = this.cache.profiles || { default: null, profiles: [] };
    const select = el("select", { className: "form-select mono" });
    select.appendChild(el("option", { text: "(default)", attrs: { value: "" } }));
    for (const entry of store.profiles) {
      select.appendChild(el("option", { text: entry.name, attrs: { value: entry.name } }));
    }
    select.value = editor.name || "";
    select.addEventListener("change", (e) => {
      const next = e.target.value || null;
      if (!this.leaveGuard()) {
        e.target.value = editor.name || "";
        return;
      }
      this.state.profileEditor = null;
      this.goToProfiles(next);
    });
    const current = store.profiles.find((p) => p.name === editor.name);
    const usedBy = current ? current.used_by : 0;
    const isDefault = editor.name === null;
    const deleteBtn = el("button", {
      className: "btn btn-danger-secondary",
      text: "Delete",
      attrs: { type: "button" },
      onClick: () => this.deleteProfile(),
    });
    if (isDefault || usedBy > 0) {
      deleteBtn.setAttribute("disabled", "disabled");
      deleteBtn.setAttribute("title", isDefault ? "(default) se nemaže" : `pouziva ${usedBy} runu`);
    }
    return el("div", {
      className: "profile-toolbar",
      children: [
        el("span", { className: "field-label", text: "Profile" }),
        select,
        isDefault ? el("span", { className: "kind-tag", text: "read-only" }) : null,
        el("button", { className: "btn btn-secondary", text: "+ New profile", attrs: { type: "button" },
          onClick: () => this.newProfile(null) }),
        el("button", { className: "btn btn-secondary", text: "Duplicate", attrs: { type: "button" },
          onClick: () => this.newProfile(editor.doc) }),
        deleteBtn,
        el("span", { className: "profile-usage", text: isDefault ? "" : `pouziva ${usedBy} runu` }),
      ],
    });
  }
```

- [ ] **Step 4: The page**

Replace `renderProfilesView` (the renamed registry view) with:

```js
  renderProfilesView() {
    clear(this.mainEl);
    this.mainEl.appendChild(this.buildBreadcrumb("profiles", false));
    const editor = this.state.profileEditor;
    if (!editor) return;
    this.mainEl.appendChild(
      el("div", {
        className: "run-header",
        children: [
          el("h1", { text: "Profiles" }),
          el("span", { className: "subtitle", text: "profiles/<name>.yml · run picks one" }),
        ],
      })
    );
    if (this.cache.profilesError) {
      this.mainEl.appendChild(el("div", { className: "notice notice-warn", text: this.cache.profilesError.detail }));
    }
    if (this.cache.catalogueError) {
      this.mainEl.appendChild(el("div", { className: "notice notice-warn", text: this.cache.catalogueError }));
    }
    this.mainEl.appendChild(this.buildProfileToolbar(editor));
    if (editor.loadError) {
      this.mainEl.appendChild(el("div", { className: "notice notice-warn", text: editor.loadError }));
      return;
    }
    if (!editor.doc) return; // still loading

    const readonly = editor.name === null;
    this.mainEl.appendChild(this.buildProfileSectionForm(editor, readonly));

    // Spec 4 replaces this with the editable checks table; until then the
    // registry listing stays here, read-only.
    this.mainEl.appendChild(this.buildChecksRegistry());

    const footerChildren = [];
    if (editor.error) footerChildren.push(el("div", { className: "field-error", text: editor.error }));
    if (!readonly) {
      const dirty = this.editorDirty();
      footerChildren.push(
        el("div", {
          className: "footer-actions",
          children: [
            el("button", { className: "btn btn-secondary", text: "Discard", attrs: { type: "button" },
              onClick: dirty && !editor.saving ? () => this.discardProfile() : null }),
            el("button", { className: "btn btn-primary", text: editor.saving ? "Saving…" : "Save profile",
              attrs: { type: "button" }, onClick: dirty && !editor.saving ? () => this.saveProfile() : null }),
          ],
        })
      );
      if (!dirty) footerChildren[footerChildren.length - 1].querySelectorAll("button").forEach((b) => b.setAttribute("disabled", "disabled"));
    }
    this.mainEl.appendChild(el("div", { className: "profile-footer", children: footerChildren }));
  }
```

Turn the old registry body into `buildChecksRegistry()` returning a node: take everything in the old `renderChecksView` after the breadcrumb (the `run-header` with `Registered checks` and the `checks-registry-table`), wrap it in `el("div", { className: "form-card", children: [...] })`, and handle `!this.cache.checks` by returning the warning notice (or an empty `div`) instead of the table.

`style.css` additions:

```css
/* Profile editor (spec 3) */
.profile-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: 14px 0;
}
.profile-toolbar .form-select { min-width: 200px; }
.profile-usage { margin-left: auto; font-size: 12.5px; color: #6b7280; }
.profile-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.field-hint { font-weight: 400; color: #9ca3af; text-transform: none; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-height: 34px; }
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px 2px 8px;
  border-radius: 4px;
  background: #eef2ff;
  color: #4338ca;
  font-size: 12px;
}
.chip-x {
  border: 0;
  background: none;
  color: #9ca3af;
  cursor: pointer;
  font-size: 13px;
  padding: 0 4px;
}
.chip-x:hover { color: #b91c1c; }
.chip-empty { color: #9ca3af; font-style: italic; font-size: 12.5px; }
.chip-add {
  border: 1px dashed #c7d2fe;
  border-radius: 4px;
  background: #fff;
  color: #4338ca;
  font-size: 12px;
  padding: 2px 6px;
}
.ping-input { width: 140px; }
.ping-input::placeholder { color: #9ca3af; font-style: italic; }
.profile-footer { display: flex; flex-direction: column; gap: 8px; margin-top: 14px; }
.footer-actions button:disabled { opacity: 0.5; cursor: default; }
```

- [ ] **Step 5: Verify in the browser**

`pyats-venv/bin/pytest -q` and `node --test tests/js/` stay green (no logic moved out of `app.js` is tested by node). Then, against the scratch root from Task 6:

1. `Profiles` opens the editor on `(default)`: picker at `(default)`, `read-only` tag, Delete disabled (`(default) se nemaže`), the form controls disabled, no Save/Discard.
2. `+ New profile` → name `core-only` → the picker switches to it, `pouziva 0 runu`, empty form (`(vsechny)` twice, ping placeholder `5 (default)`).
3. Add collectors `interfaces` and `bgp` via `+ add`, service type `IPVPN`, ping `3` → Save enables; Save writes `profiles/core-only.yml` with exactly `profile:\n  collectors:\n  - interfaces\n  - bgp\n  service_types:\n  - IPVPN\n  ping_count: 3\n`; Save/Discard disable again.
4. Remove a chip, then Discard → chip returns. Change something and click a run in the combobox → confirm dialog appears; Cancel keeps the editor.
5. Create a run using `core-only` (New run picker) → toolbar shows `pouziva 1 runu`, Delete disabled with that tooltip. Archive the run → Delete enabled → deleting removes the file and lands on `(default)`.
6. `Duplicate` on `(default)` with `--profile some.yml` started → the new profile carries that file's values.
7. Hand-edit `profiles/core-only.yml` to `profile: {collectors: [iface]}`, reopen → a 422 notice with `neznamy collector 'iface'` under the toolbar; Save of a bad ping (`0`) shows the loader error under the buttons.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui/static
git commit -m "feat(gui): editor profilu - picker, new/duplicate/delete, formular sekce profile, save/discard s guardem"
```

---

### Task 8: Documentation

**Files:**
- Modify: `docs/cs/reference.md` (section `## 8. Run management`, after the `### Subcommand \`status\`` block), `docs/en/reference.md` (same place)
- Modify: `docs/cs/README.md` (near line 380, the GUI archive paragraph), `docs/en/README.md` (near line 250)

- [ ] **Step 1: Czech reference**

Add before `### Pravidla párování \`evaluate --run\`` in `docs/cs/reference.md`:

```markdown
### Profily (`profiles/`, GUI)

Vedle `runs/` leží `profiles/<jméno>.yml` — jeden soubor na profil, stejný formát jako
`--profile` (sekce `profile:` a `checks:`). Jméno odpovídá `^[a-z0-9_-]+$`. Run si profil
vybírá při založení (`profile:` v `run.yml`); bez něj platí **serverový default** — soubor
z `mig-validate gui --profile`, nebo vestavěný prázdný profil, v GUI zobrazený jako
`(default)`. Run, jehož profil už neexistuje, se nevyhodnotí: `422 profil '<jméno>'
neexistuje (runs/<run>/run.yml)`, žádný tichý fallback.

| přepínač `gui` | výchozí | poznámka |
|---|---|---|
| `--profiles-root` | `profiles` | adresář pojmenovaných profilů |
| `--profile` | — | serverový default profil (runy s `profile: null`), stejně jako u CLI |

API (`/api/profiles`): `GET` seznam s `used_by` (kolik `run.yml` profil odkazuje) a
`default_document`, `GET /catalogue` (collectory, typy služeb, `ping_count_default`, checky
s defaulty pro formulář), `GET/PUT/DELETE /{name}`, `POST` (`{"name","document"}`), `POST
/preview` (`{"document"}` → `{"yaml"}`). Zápis vyžaduje roli `admin`. Uložení validuje přes
`load_profile` na dočasném souboru a teprve pak přejmenuje přes cíl; chyba loaderu se vrací
beze změny jako `422`. Soubor nese jen nastavené klíče (`null` a prázdná sekce `checks` se
vynechají); `POST /preview` vrací byte-shodný text. Profil, na který ukazuje aspoň jeden run,
nejde smazat (`409 profil pouziva <n> runu`).
```

- [ ] **Step 2: English reference**

Add before `### \`evaluate --run\` pairing rules` (the English counterpart of the pairing section) in `docs/en/reference.md`:

```markdown
### Profiles (`profiles/`, GUI)

Next to `runs/` lives `profiles/<name>.yml` — one file per profile, the same format as
`--profile` (`profile:` and `checks:` sections). Names match `^[a-z0-9_-]+$`. A run picks its
profile when it is created (`profile:` in `run.yml`); without one the **server default**
applies — the file given to `mig-validate gui --profile`, or the built-in empty profile,
shown in the GUI as `(default)`. A run whose profile no longer exists is not evaluated:
`422 profil '<name>' neexistuje (runs/<run>/run.yml)`, no silent fallback.

| `gui` flag | default | note |
|---|---|---|
| `--profiles-root` | `profiles` | directory of named profiles |
| `--profile` | — | server default profile (runs with `profile: null`), as in the CLI |

API (`/api/profiles`): `GET` list with `used_by` (how many `run.yml` reference the profile)
and `default_document`, `GET /catalogue` (collectors, service types, `ping_count_default`,
checks with defaults for the form), `GET/PUT/DELETE /{name}`, `POST` (`{"name","document"}`),
`POST /preview` (`{"document"}` → `{"yaml"}`). Writes need the `admin` role. Saving validates
through `load_profile` on a temporary file and only then renames it over the target; the
loader's error comes back unchanged as `422`. The file carries only the keys that are set
(`null` and an empty `checks` section are omitted); `POST /preview` returns the byte-identical
text. A profile referenced by at least one run cannot be deleted (`409 profil pouziva <n> runu`).
```

- [ ] **Step 3: READMEs**

`docs/cs/README.md`, right after the paragraph starting `GUI umí run archivovat` (around line 380), add:

```markdown
Profily spravuje GUI tlačítkem *Profiles*: pojmenované soubory `profiles/<jméno>.yml` se
zakládají, duplikují a mažou v editoru, sekce `profile:` (collectory, typy služeb, ping
count) má formulář s viditelnými defaulty. Run si profil vybírá v *New run*; `(default)` je
serverový profil z `--profile` a v GUI se needituje.
```

`docs/en/README.md`, right after the paragraph starting `The GUI can archive a run` (around line 250), add:

```markdown
Profiles are managed in the GUI under *Profiles*: named files `profiles/<name>.yml` are
created, duplicated and deleted in the editor, and the `profile:` section (collectors,
service types, ping count) has a form that shows the defaults. A run picks its profile in
*New run*; `(default)` is the server profile from `--profile` and is read-only in the GUI.
```

- [ ] **Step 4: Commit**

```bash
git add docs/cs/reference.md docs/en/reference.md docs/cs/README.md docs/en/README.md
git commit -m "docs: profiles/ store, gui --profiles-root a /api/profiles"
```

---

## Finishing

After Task 8: run the full suite once more (`pyats-venv/bin/pytest -q` and `node --test tests/js/`), then use `superpowers:finishing-a-development-branch` to merge `gui-profile-store` into `main` (previous waves merged fast-forward and pushed). Carry into the memory note: the two deliberate additions (`default_document`, `default_enabled`), the temp-path rewrite in the store's error message, and that the registry table on the Profiles page is the placeholder spec 4 replaces with the editable checks table.
