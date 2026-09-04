# GUI run list and archiving Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sidebar run list with a searchable run combobox in the topbar, make "+ New run" a primary button, let users archive a run from the run overview, add a CLI purge for the archive, and put every write endpoint behind a permission seam that a future role model can plug into.

**Architecture:** Backend keeps the existing shape: `migration_validator/api.py` holds the reusable operations (`archive_run`, `list_archive`, `purge_archive`), `gui/app.py` routes are thin wrappers, a new `gui/authz.py` provides the `require(Permission)` FastAPI dependency with an "anonymous = admin" provider that tests override through `app.state.actor_provider`. Frontend stays vanilla JS: the pure `filterRuns` helper goes into `view.js` (tested with `node --test`), the combobox, the empty state and the archive modal go into `app.js`, markup into `index.html`, styles into `style.css`.

**Tech Stack:** Python 3.13, FastAPI + TestClient, pytest, PyYAML, vanilla JS + CSS (no build step), `node --test` (Node 22) for JS logic.

**Spec:** `docs/superpowers/specs/2026-09-04-gui-run-list-a-archivace-design.md`

## Global Constraints

- Tool strings (API `detail`, CLI output, exceptions) are Czech **without diacritics**, exactly as the rest of the code base (`run 'x' neexistuje`, `nedostatecne opravneni: vyzaduje admin`).
- GUI copy in `GUIDE_TEXT` and on screen follows the existing GUI convention (Czech with diacritics in the guide rail, short English labels on buttons: `Archive run`, `+ New run`).
- Run names match `^[a-z0-9_-]+$` (`api._RUN_NAME_RE`); archive lives in `run_root/.archive/<name>-<YYYYMMDDTHHMMSSZ>/`.
- `GET /api/runs` must never list a directory whose name starts with `.`.
- Permission levels: `view` < `operate` < `admin`. Reads need `view`, create run / update mapping / start capture need `operate`, archive needs `admin`.
- No new Python dependencies, no JS dependencies, no build step.
- Run the full suite before every commit: `pytest -q` (expect all green, ~1380 tests) and `node --test tests/js/`.
- Commit messages in the repo style: `feat(gui): ...`, `feat(api): ...`, `feat(cli): ...`, Czech without diacritics, ending with the Co-Authored-By and Claude-Session trailers given by the session.

---

## File structure

| File | Responsibility |
|---|---|
| `migration_validator/gui/authz.py` (new) | `Actor`, `Permission`, `current_actor`, `require()` |
| `migration_validator/gui/app.py` | wire `require()` onto every route; new `POST /api/runs/{run}/archive`; skip dotted dirs in `list_runs` |
| `migration_validator/gui/captures.py` | `CaptureManager.busy_run(run)` |
| `migration_validator/api.py` | `archive_run`, `ArchiveEntry`, `list_archive`, `purge_archive` |
| `migration_validator/cli.py` | `run purge` subcommand |
| `migration_validator/gui/static/view.js` | pure `filterRuns(runs, query)` |
| `migration_validator/gui/static/index.html` | topbar combobox markup, primary New run button, sidebar without runs |
| `migration_validator/gui/static/app.js` | combobox state and rendering, empty state, archive modal |
| `migration_validator/gui/static/style.css` | combobox, modal, danger button hover |
| `tests/gui/test_authz.py` (new) | seam tests |
| `tests/gui/test_write_routes.py` | archive route tests |
| `tests/gui/test_captures.py` | `busy_run` |
| `tests/test_api_runs.py` | `archive_run`, `list_archive`, `purge_archive` |
| `tests/test_cli.py` | `run purge` |
| `tests/js/view.test.js` | `filterRuns` |
| `docs/cs/README.md`, `docs/en/README.md` | archive + purge paragraph in section 3a |

---

### Task 1: Permission seam `gui/authz.py`

**Files:**
- Create: `migration_validator/gui/authz.py`
- Create: `tests/gui/test_authz.py`
- Modify: `migration_validator/gui/app.py` (every route signature)

**Interfaces:**
- Produces: `Actor(role: str)` frozen dataclass; `Permission` str-enum with `VIEW="view"`, `OPERATE="operate"`, `ADMIN="admin"`; `current_actor(request) -> Actor` reading `request.app.state.actor_provider` (callable `(Request) -> Actor`, default `anonymous_admin`); `require(permission: Permission)` returning a `fastapi.Depends` usable as a route parameter default. Later tasks use `require(Permission.ADMIN)` on the archive route.

- [ ] **Step 1: Write the failing tests**

```python
# tests/gui/test_authz.py
"""Permission seam - dnes je kazdy anonymni admin, testy prepinaji roli
pres app.state.actor_provider."""

from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor

OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos"}
NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"}


def _client(tmp_path, role=None):
    api.create_run("mig01", old_device=OLD, new_device=NEW, run_root=tmp_path)
    app = create_app(run_root=tmp_path)
    if role is not None:
        app.state.actor_provider = lambda request: Actor(role=role)
    return TestClient(app)


def test_anonymni_je_admin_a_muze_zapisovat(tmp_path):
    client = _client(tmp_path)
    resp = client.post("/api/runs", json={
        "name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": [],
    })
    assert resp.status_code == 201


def test_viewer_cte_ale_nezapisuje(tmp_path):
    client = _client(tmp_path, role="viewer")
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/runs/mig01").status_code == 200
    resp = client.post("/api/runs", json={
        "name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": [],
    })
    assert resp.status_code == 403
    assert resp.json()["detail"] == "nedostatecne opravneni: vyzaduje operate"


def test_operator_zaklada_run(tmp_path):
    client = _client(tmp_path, role="operator")
    resp = client.post("/api/runs", json={
        "name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": [],
    })
    assert resp.status_code == 201


def test_neznama_role_nema_nic(tmp_path):
    client = _client(tmp_path, role="guest")
    assert client.get("/api/runs").status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/gui/test_authz.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'migration_validator.gui.authz'`

- [ ] **Step 3: Write the seam**

```python
# migration_validator/gui/authz.py
"""Permission seam GUI - dnes je kazdy anonymni admin.

Budouci model roli (admin / operator / viewer) vymeni jen actor provider
v app.state; routy deklaruji, jake opravneni potrebuji, a nemeni se."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from fastapi import Depends, HTTPException, Request


class Permission(str, Enum):
    VIEW = "view"
    OPERATE = "operate"
    ADMIN = "admin"


@dataclass(frozen=True)
class Actor:
    role: str


_ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
_NEEDED_RANK = {Permission.VIEW: 0, Permission.OPERATE: 1, Permission.ADMIN: 2}

ActorProvider = Callable[[Request], Actor]


def anonymous_admin(request: Request) -> Actor:
    return Actor(role="admin")


def current_actor(request: Request) -> Actor:
    provider: ActorProvider = getattr(
        request.app.state, "actor_provider", anonymous_admin
    )
    return provider(request)


def allows(actor: Actor, permission: Permission) -> bool:
    return _ROLE_RANK.get(actor.role, -1) >= _NEEDED_RANK[permission]


def require(permission: Permission):
    """Pouziti: `def route(actor: Actor = require(Permission.ADMIN))`."""

    def dependency(actor: Actor = Depends(current_actor)) -> Actor:
        if not allows(actor, permission):
            raise HTTPException(
                status_code=403,
                detail=f"nedostatecne opravneni: vyzaduje {permission.value}",
            )
        return actor

    return Depends(dependency)
```

- [ ] **Step 4: Wire the seam onto every existing route in `app.py`**

Add the import next to the other gui imports:

```python
from migration_validator.gui.authz import Actor, Permission, anonymous_admin, require
```

In `create_app`, right after `app.state.captures = manager`:

```python
    app.state.actor_provider = anonymous_admin
```

Add an `actor` parameter to every route. Reads get `VIEW`, writes get `OPERATE`:

```python
    @app.get("/api/checks")
    def list_checks(actor: Actor = require(Permission.VIEW)) -> dict:

    @app.get("/api/meta")
    def meta(actor: Actor = require(Permission.VIEW)) -> dict:

    @app.get("/api/runs")
    def list_runs(actor: Actor = require(Permission.VIEW)) -> dict:

    @app.get("/api/runs/{run}")
    def run_detail(run: str, actor: Actor = require(Permission.VIEW)) -> dict:

    @app.post("/api/runs", status_code=201)
    def create_run(body: CreateRunBody, actor: Actor = require(Permission.OPERATE)) -> dict:

    @app.put("/api/runs/{run}/mapping")
    def update_mapping(run: str, body: MappingBody, actor: Actor = require(Permission.OPERATE)) -> dict:

    @app.get("/api/runs/{run}/evaluation")
    def run_evaluation(run: str, ports: str | None = None, actor: Actor = require(Permission.VIEW)) -> dict:

    @app.get("/api/runs/{run}/snapshots/{file}/evaluation")
    def snapshot_evaluation(run: str, file: str, baseline: str | None = None, actor: Actor = require(Permission.VIEW)) -> dict:

    @app.post("/api/captures", status_code=202)
    def start_capture(body: CaptureBody, actor: Actor = require(Permission.OPERATE)) -> dict:

    @app.get("/api/captures/{task_id}")
    def capture_status(task_id: str, actor: Actor = require(Permission.VIEW)) -> dict:
```

The `index` route and the static mount stay unguarded (the page shell is not data).

- [ ] **Step 5: Run the tests**

Run: `pytest tests/gui -v`
Expected: all PASS, including the four new tests.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui/authz.py migration_validator/gui/app.py tests/gui/test_authz.py
git commit -m "feat(gui): permission seam authz.py - view/operate/admin, anonymni je admin"
```

---

### Task 2: `CaptureManager.busy_run` and `api.archive_run`

**Files:**
- Modify: `migration_validator/gui/captures.py` (class `CaptureManager`, after `get`)
- Modify: `migration_validator/api.py` (after `create_run`)
- Modify: `migration_validator/gui/app.py` (`list_runs`)
- Test: `tests/gui/test_captures.py`, `tests/test_api_runs.py`, `tests/gui/test_routes.py`

**Interfaces:**
- Produces: `CaptureManager.busy_run(run: str) -> bool` (True while any task of that run has `state == "running"`); `api.archive_run(name: str, *, run_root: str | Path = Path("runs"), now: datetime | None = None) -> Path` returning the archive directory, raising `ValueError` for a bad name and `FileNotFoundError` for a missing run.

- [ ] **Step 1: Write the failing tests**

Append to `tests/gui/test_captures.py`:

```python
def test_busy_run_vidi_jen_bezici_task_daneho_runu():
    manager = CaptureManager()
    gate = threading.Event()

    def blocking(on_progress):
        gate.wait(5)
        return object()

    task = manager.start(blocking, run="mig01", device="MX1", port=None, phase="pre")
    assert manager.busy_run("mig01") is True
    assert manager.busy_run("mig02") is False
    gate.set()
    _wait_done(manager, task.id)
    assert manager.busy_run("mig01") is False
```

Add `import threading` at the top of that test file.

Append to `tests/test_api_runs.py`:

```python
from datetime import datetime, timezone
from pathlib import Path


def test_archive_run_presune_adresar_do_archive(tmp_path):
    api.create_run("mig02", old_device=OLD, new_device=NEW, run_root=tmp_path)
    (tmp_path / "mig02" / "snapshot_pre_MX1_all.json").write_text("{}")
    when = datetime(2026, 9, 4, 10, 30, 0, tzinfo=timezone.utc)
    target = api.archive_run("mig02", run_root=tmp_path, now=when)
    assert target == tmp_path / ".archive" / "mig02-20260904T103000Z"
    assert (target / "run.yml").exists()
    assert (target / "snapshot_pre_MX1_all.json").exists()
    assert not (tmp_path / "mig02").exists()


def test_archive_run_neexistujici_run(tmp_path):
    with pytest.raises(FileNotFoundError, match="run 'nope' neexistuje"):
        api.archive_run("nope", run_root=tmp_path)


def test_archive_run_nevalidni_jmeno(tmp_path):
    with pytest.raises(ValueError, match="nevalidni jmeno runu"):
        api.archive_run("../etc", run_root=tmp_path)
```

Append to `tests/gui/test_routes.py`:

```python
def test_list_runs_preskoci_teckovane_adresare(tmp_path):
    from migration_validator import api
    from migration_validator.gui.app import create_app
    from fastapi.testclient import TestClient
    OLD = {"node": "MX1", "host": "10.0.0.1", "platform": "junos"}
    NEW = {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"}
    api.create_run("mig01", old_device=OLD, new_device=NEW, run_root=tmp_path)
    api.archive_run("mig01", run_root=tmp_path)
    api.create_run("mig02", old_device=OLD, new_device=NEW, run_root=tmp_path)
    client = TestClient(create_app(run_root=tmp_path))
    names = [r["name"] for r in client.get("/api/runs").json()["runs"]]
    assert names == ["mig02"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/gui/test_captures.py::test_busy_run_vidi_jen_bezici_task_daneho_runu tests/test_api_runs.py tests/gui/test_routes.py -v`
Expected: FAIL with `AttributeError: 'CaptureManager' object has no attribute 'busy_run'` and `AttributeError: module 'migration_validator.api' has no attribute 'archive_run'`.

- [ ] **Step 3: Implement `busy_run`**

In `migration_validator/gui/captures.py`, inside `CaptureManager` after `get`:

```python
    def busy_run(self, run: str) -> bool:
        """True, dokud na runu bezi aspon jeden capture - archivace ceka."""
        with self._lock:
            return any(
                task.run == run and task.state == "running"
                for task in self._tasks.values()
            )
```

- [ ] **Step 4: Implement `archive_run`**

In `migration_validator/api.py`, add imports at the top (keep existing ones):

```python
from datetime import datetime, timezone
```

After `create_run`:

```python
ARCHIVE_DIR = ".archive"


def archive_run(
    name: str,
    *,
    run_root: str | Path = Path("runs"),
    now: datetime | None = None,
) -> Path:
    """Presune runs/<name>/ do runs/.archive/<name>-<UTC stamp>/.

    Rename na stejnem filesystemu je atomicky - run bud zmizi cely, nebo
    vubec. Teckovany adresar list_runs preskakuje."""
    if not _RUN_NAME_RE.match(name):
        raise ValueError(
            f"nevalidni jmeno runu '{name}' - povolene znaky: a-z 0-9 _ -"
        )
    store = RunStore(Path(run_root), name)
    if not store.manifest_path.exists():
        raise FileNotFoundError(f"run '{name}' neexistuje ({store.dir})")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    archive = Path(run_root) / ARCHIVE_DIR
    archive.mkdir(exist_ok=True)
    target = archive / f"{name}-{stamp}"
    store.dir.rename(target)
    return target
```

- [ ] **Step 5: Skip dotted entries in `list_runs`**

In `migration_validator/gui/app.py`, `list_runs`:

```python
            for entry in sorted(run_root.iterdir()):
                if entry.name.startswith("."):
                    continue
                store = RunStore(run_root, entry.name)
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/gui tests/test_api_runs.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/gui/captures.py migration_validator/api.py migration_validator/gui/app.py tests/gui/test_captures.py tests/test_api_runs.py tests/gui/test_routes.py
git commit -m "feat(api): archive_run presouva run do runs/.archive, busy_run na CaptureManageru"
```

---

### Task 3: Route `POST /api/runs/{run}/archive`

**Files:**
- Modify: `migration_validator/gui/app.py` (after `update_mapping`)
- Test: `tests/gui/test_write_routes.py`

**Interfaces:**
- Consumes: `api.archive_run`, `manager.busy_run`, `require(Permission.ADMIN)` from Tasks 1 and 2.
- Produces: `POST /api/runs/{run}/archive` → 200 `{"archived_to": "<dir name>"}`; 404 `run '<x>' neexistuje`; 409 `run ma bezici capture`; 403 for non-admin.

- [ ] **Step 1: Write the failing tests**

Append to `tests/gui/test_write_routes.py`:

```python
def test_post_archive_presune_run_a_zmizi_ze_seznamu(tmp_path):
    client = _client(tmp_path)
    client.post("/api/runs", json={
        "name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": [],
    })
    resp = client.post("/api/runs/mig02/archive")
    assert resp.status_code == 200
    archived = resp.json()["archived_to"]
    assert archived.startswith("mig02-")
    assert (tmp_path / ".archive" / archived / "run.yml").exists()
    assert not (tmp_path / "mig02").exists()
    assert client.get("/api/runs").json()["runs"] == []


def test_post_archive_podruhe_je_404(tmp_path):
    client = _client(tmp_path)
    client.post("/api/runs", json={
        "name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": [],
    })
    assert client.post("/api/runs/mig02/archive").status_code == 200
    resp = client.post("/api/runs/mig02/archive")
    assert resp.status_code == 404
    assert "neexistuje" in resp.json()["detail"]


def test_post_archive_nevalidni_jmeno_je_404(tmp_path):
    client = _client(tmp_path)
    assert client.post("/api/runs/Mig.02/archive").status_code == 404


def test_post_archive_s_bezicim_capture_je_409(tmp_path):
    from migration_validator.gui.captures import CaptureTask
    app = create_app(run_root=tmp_path)
    client = TestClient(app)
    client.post("/api/runs", json={
        "name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": [],
    })
    manager = app.state.captures
    manager._tasks["fake"] = CaptureTask(
        id="fake", run="mig02", device="MX1", port=None, phase="pre",
    )
    resp = client.post("/api/runs/mig02/archive")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "run ma bezici capture"
    assert (tmp_path / "mig02" / "run.yml").exists()


def test_post_archive_vyzaduje_admina(tmp_path):
    from migration_validator.gui.authz import Actor
    app = create_app(run_root=tmp_path)
    client = TestClient(app)
    client.post("/api/runs", json={
        "name": "mig02", "old_device": OLD, "new_device": NEW, "mappings": [],
    })
    app.state.actor_provider = lambda request: Actor(role="operator")
    resp = client.post("/api/runs/mig02/archive")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "nedostatecne opravneni: vyzaduje admin"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/gui/test_write_routes.py -v -k archive`
Expected: FAIL, all five with status 404 or 405 from FastAPI (route does not exist).

- [ ] **Step 3: Add the route**

In `migration_validator/gui/app.py`, after `update_mapping`:

```python
    @app.post("/api/runs/{run}/archive")
    def archive_run(run: str, actor: Actor = require(Permission.ADMIN)) -> dict:
        if manager.busy_run(run):
            raise HTTPException(status_code=409, detail="run ma bezici capture")
        try:
            target = api.archive_run(run, run_root=run_root)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"archived_to": target.name}
```

The busy check runs first so a bad name on a busy run still reports the honest reason; ordering between 404 and 409 for a non-existent run does not matter because `busy_run` is False for a run that never had a task.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/gui -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/app.py tests/gui/test_write_routes.py
git commit -m "feat(gui): POST /api/runs/{run}/archive - admin, 409 pri bezicim capture"
```

---

### Task 4: CLI `run purge` and archive helpers

**Files:**
- Modify: `migration_validator/api.py` (after `archive_run`)
- Modify: `migration_validator/cli.py` (new `_cmd_run_purge`, parser in `build_parser`)
- Modify: `docs/cs/README.md` (section `## 3a. Run management`), `docs/en/README.md` (section `## 3a. Run management`)
- Test: `tests/test_api_runs.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `api.ArchiveEntry(name: str, archived: datetime, snapshots: int, path: Path)`; `api.list_archive(run_root) -> list[ArchiveEntry]` sorted by `archived`; `api.purge_archive(run_root, *, older_than_days: int, now: datetime | None = None) -> list[ArchiveEntry]` deleting and returning the removed entries. CLI: `mig-validate run purge [--run-root DIR] [--older-than DAYS] [--dry-run] [--yes]`.

- [ ] **Step 1: Write the failing API tests**

Append to `tests/test_api_runs.py`:

```python
def _archived(tmp_path, name, when):
    api.create_run(name, old_device=OLD, new_device=NEW, run_root=tmp_path)
    (tmp_path / name / "snapshot_pre_MX1_all.json").write_text("{}")
    return api.archive_run(name, run_root=tmp_path, now=when)


def test_list_archive_cte_jmeno_cas_a_pocet_snimku(tmp_path):
    _archived(tmp_path, "mig02", datetime(2026, 9, 1, tzinfo=timezone.utc))
    _archived(tmp_path, "mig01", datetime(2026, 8, 1, tzinfo=timezone.utc))
    entries = api.list_archive(tmp_path)
    assert [e.name for e in entries] == ["mig01", "mig02"]
    assert entries[0].archived == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert entries[0].snapshots == 1
    assert entries[0].path == tmp_path / ".archive" / "mig01-20260801T000000Z"


def test_list_archive_bez_archivu_je_prazdny(tmp_path):
    assert api.list_archive(tmp_path) == []


def test_purge_archive_maze_jen_starsi(tmp_path):
    _archived(tmp_path, "mig01", datetime(2026, 8, 1, tzinfo=timezone.utc))
    keep = _archived(tmp_path, "mig02", datetime(2026, 9, 3, tzinfo=timezone.utc))
    removed = api.purge_archive(
        tmp_path, older_than_days=7,
        now=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    assert [e.name for e in removed] == ["mig01"]
    assert not (tmp_path / ".archive" / "mig01-20260801T000000Z").exists()
    assert keep.exists()
```

- [ ] **Step 2: Write the failing CLI tests**

Append to `tests/test_cli.py` (the module already imports `main`; the device dicts are defined inline below):

```python
def test_run_purge_bez_older_than_jen_vypise(tmp_path, capsys):
    from datetime import datetime, timezone
    from migration_validator import api
    api.create_run("mig01", old_device={"node": "MX1", "host": "10.0.0.1", "platform": "junos"},
                   new_device={"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"},
                   run_root=tmp_path)
    api.archive_run("mig01", run_root=tmp_path,
                    now=datetime(2026, 8, 1, tzinfo=timezone.utc))
    code = main(["run", "purge", "--run-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "mig01" in out and "2026-08-01" in out
    assert (tmp_path / ".archive" / "mig01-20260801T000000Z").exists()


def test_run_purge_dry_run_nemaze(tmp_path, capsys):
    from datetime import datetime, timezone
    from migration_validator import api
    api.create_run("mig01", old_device={"node": "MX1", "host": "10.0.0.1", "platform": "junos"},
                   new_device={"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"},
                   run_root=tmp_path)
    api.archive_run("mig01", run_root=tmp_path,
                    now=datetime(2026, 8, 1, tzinfo=timezone.utc))
    code = main(["run", "purge", "--run-root", str(tmp_path),
                 "--older-than", "0", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "smazal by" in out
    assert (tmp_path / ".archive" / "mig01-20260801T000000Z").exists()


def test_run_purge_yes_smaze(tmp_path, capsys):
    from datetime import datetime, timezone
    from migration_validator import api
    api.create_run("mig01", old_device={"node": "MX1", "host": "10.0.0.1", "platform": "junos"},
                   new_device={"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo"},
                   run_root=tmp_path)
    api.archive_run("mig01", run_root=tmp_path,
                    now=datetime(2026, 8, 1, tzinfo=timezone.utc))
    code = main(["run", "purge", "--run-root", str(tmp_path),
                 "--older-than", "0", "--yes"])
    out = capsys.readouterr().out
    assert code == 0
    assert "smazano 1" in out
    assert not (tmp_path / ".archive" / "mig01-20260801T000000Z").exists()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_api_runs.py tests/test_cli.py -v -k "archive or purge"`
Expected: FAIL with `AttributeError: module 'migration_validator.api' has no attribute 'list_archive'` and `SystemExit: 2` from argparse (`invalid choice: 'run'`).

- [ ] **Step 4: Implement `list_archive` and `purge_archive`**

In `migration_validator/api.py`, add imports (`shutil`, `dataclass`, `timedelta`):

```python
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
```

After `archive_run`:

```python
_ARCHIVE_STAMP_RE = re.compile(r"^(?P<name>[a-z0-9_-]+)-(?P<stamp>\d{8}T\d{6}Z)$")


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    archived: datetime
    snapshots: int
    path: Path


def list_archive(run_root: str | Path = Path("runs")) -> list[ArchiveEntry]:
    """Polozky runs/.archive/ serazene od nejstarsi. Adresare, ktere
    nevypadaji jako <name>-<stamp>, se preskakuji - nikdy se nemazou."""
    archive = Path(run_root) / ARCHIVE_DIR
    if not archive.is_dir():
        return []
    entries: list[ArchiveEntry] = []
    for entry in archive.iterdir():
        match = _ARCHIVE_STAMP_RE.match(entry.name)
        if not entry.is_dir() or match is None:
            continue
        archived = datetime.strptime(
            match.group("stamp"), "%Y%m%dT%H%M%SZ"
        ).replace(tzinfo=timezone.utc)
        snapshots = len(list(entry.glob("snapshot_*.json")))
        entries.append(ArchiveEntry(
            name=match.group("name"), archived=archived,
            snapshots=snapshots, path=entry,
        ))
    return sorted(entries, key=lambda e: (e.archived, e.name))


def purge_archive(
    run_root: str | Path = Path("runs"),
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> list[ArchiveEntry]:
    """Smaze archivovane runy starsi nez older_than_days. Vraci smazane."""
    threshold = (now or datetime.now(timezone.utc)) - timedelta(days=older_than_days)
    removed: list[ArchiveEntry] = []
    for entry in list_archive(run_root):
        if entry.archived <= threshold:
            shutil.rmtree(entry.path)
            removed.append(entry)
    return removed
```

- [ ] **Step 5: Implement the CLI subcommand**

In `migration_validator/cli.py`, add after `_cmd_gui`:

```python
def _cmd_run_purge(args: argparse.Namespace) -> int:
    entries = api.list_archive(args.run_root)
    if not entries:
        print("archiv je prazdny")
        return EXIT_OK
    print(f"{'RUN':<24} {'ARCHIVOVANO':<20} SNIMKU")
    for entry in entries:
        print(
            f"{entry.name:<24} "
            f"{entry.archived.strftime('%Y-%m-%d %H:%M:%SZ'):<20} "
            f"{entry.snapshots}"
        )
    if args.older_than is None:
        return EXIT_OK

    from datetime import datetime, timedelta, timezone

    threshold = datetime.now(timezone.utc) - timedelta(days=args.older_than)
    candidates = [e for e in entries if e.archived <= threshold]
    if not candidates:
        print(f"nic starsiho nez {args.older_than} dni")
        return EXIT_OK
    names = ", ".join(e.path.name for e in candidates)
    if args.dry_run:
        print(f"smazal by {len(candidates)}: {names}")
        return EXIT_OK
    if not args.yes:
        answer = input(f"smazat {len(candidates)} ({names})? [y/N] ")
        if answer.strip().lower() != "y":
            print("zruseno")
            return EXIT_OK
    removed = api.purge_archive(args.run_root, older_than_days=args.older_than)
    print(f"smazano {len(removed)}")
    return EXIT_OK
```

In `build_parser`, before `return parser`:

```python
    run = sub.add_parser("run", help="sprava run adresaru")
    run_sub = run.add_subparsers(dest="run_command", required=True)
    purge = run_sub.add_parser(
        "purge", help="vypise archivovane runy, s --older-than je smaze"
    )
    purge.add_argument(
        "--run-root", type=Path, default=Path("runs"), help="koren run adresaru"
    )
    purge.add_argument(
        "--older-than", type=int, default=None, metavar="DNI",
        help="smaze archivovane runy starsi nez DNI (bez flagu jen vypis)",
    )
    purge.add_argument("--dry-run", action="store_true", help="jen vypise, co by smazal")
    purge.add_argument("--yes", action="store_true", help="bez potvrzeni")
    purge.set_defaults(func=_cmd_run_purge)
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_api_runs.py tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 7: Document archive and purge**

In `docs/cs/README.md`, at the end of the `### Přehled runu (`status`)` subsection (before `## 4. Jak číst výstup`), add:

```markdown
### Archivace a úklid runů

GUI umí run archivovat (tlačítko *Archive run* v přehledu runu): adresář se
přesune do `runs/.archive/<nazev>-<UTC čas>/`, ze seznamu zmizí, data zůstanou.
Archiv se čistí z příkazové řádky:

```bash
mig-validate run purge                       # jen výpis archivu
mig-validate run purge --older-than 30       # smaže starší než 30 dní, ptá se
mig-validate run purge --older-than 30 --yes # bez dotazu
```

Obnova = ruční přesun adresáře zpět do `runs/` a přejmenování na původní název.
```

In `docs/en/README.md`, at the end of section `## 3a. Run management` (before `## 4. Reading the output`), add:

```markdown
### Archiving and purging runs

The GUI can archive a run (*Archive run* on the run overview): the directory
moves to `runs/.archive/<name>-<UTC time>/`, disappears from the list and keeps
its data. The archive is cleaned from the shell:

```bash
mig-validate run purge                       # list the archive only
mig-validate run purge --older-than 30       # delete entries older than 30 days, asks first
mig-validate run purge --older-than 30 --yes # no prompt
```

Restore = move the directory back into `runs/` and rename it to the original name.
```

- [ ] **Step 8: Run the full suite and commit**

Run: `pytest -q`
Expected: all PASS.

```bash
git add migration_validator/api.py migration_validator/cli.py tests/test_api_runs.py tests/test_cli.py docs/cs/README.md docs/en/README.md
git commit -m "feat(cli): run purge - vypis a mazani runs/.archive podle stari"
```

---

### Task 5: Pure `filterRuns` in `view.js`

**Files:**
- Modify: `migration_validator/gui/static/view.js` (before `const MigView = {`)
- Test: `tests/js/view.test.js`

**Interfaces:**
- Produces: `MigView.filterRuns(runs, query)` where `runs` is the array from `GET /api/runs` (`{name, devices: {node: {...}}, snapshots, mapped_ports}`) and `query` a string. Returns a new array preserving order.

- [ ] **Step 1: Write the failing tests**

Append to `tests/js/view.test.js`:

```js
const RUNS = [
  { name: "mig01", devices: { "MX1-POP1": {}, "PTX1-POP1": {} }, snapshots: 2 },
  { name: "upgrade-ptx-2026", devices: { "PTX3-POP2": {} }, snapshots: 1 },
  { name: "mig02-pop3", devices: { "MX9-POP3": {}, "PTX9-POP3": {} }, snapshots: 6 },
];

test("filterRuns: empty or blank query returns all runs in order", () => {
  assert.deepStrictEqual(MigView.filterRuns(RUNS, ""), RUNS);
  assert.deepStrictEqual(MigView.filterRuns(RUNS, "   "), RUNS);
});

test("filterRuns: matches run name case-insensitively", () => {
  const out = MigView.filterRuns(RUNS, "MIG0");
  assert.deepStrictEqual(out.map((r) => r.name), ["mig01", "mig02-pop3"]);
});

test("filterRuns: matches device node names", () => {
  const out = MigView.filterRuns(RUNS, "ptx3");
  assert.deepStrictEqual(out.map((r) => r.name), ["upgrade-ptx-2026"]);
});

test("filterRuns: no match returns empty list", () => {
  assert.deepStrictEqual(MigView.filterRuns(RUNS, "nope"), []);
});

test("filterRuns: tolerates runs without devices", () => {
  const out = MigView.filterRuns([{ name: "bare" }], "bare");
  assert.strictEqual(out.length, 1);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node --test tests/js/`
Expected: 5 failures with `TypeError: MigView.filterRuns is not a function`.

- [ ] **Step 3: Implement**

In `view.js`, before `const MigView = {`:

```js
/* Run combobox filter: case-insensitive substring on run name and device
   node names. Empty query returns the input array unchanged. */
function filterRuns(runs, query) {
  const needle = (query || "").trim().toLowerCase();
  if (!needle) return runs;
  return runs.filter((run) => {
    if ((run.name || "").toLowerCase().includes(needle)) return true;
    const nodes = Object.keys(run.devices || {});
    return nodes.some((node) => node.toLowerCase().includes(needle));
  });
}
```

Add `filterRuns,` to the `MigView` object.

- [ ] **Step 4: Run the tests**

Run: `node --test tests/js/`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/static/view.js tests/js/view.test.js
git commit -m "feat(gui): filterRuns - ciste filtrovani runu podle jmena a nodu"
```

---

### Task 6: Topbar run combobox and sidebar without runs

**Files:**
- Modify: `migration_validator/gui/static/index.html`
- Modify: `migration_validator/gui/static/app.js` (constructor, `boot`, `render`, `renderSidebar`, `selectRun`, `submitNewRun`, `cancelNewRunForm`; new `renderRunCombo`, `openRunCombo`, `closeRunCombo`, `onComboKey`)
- Modify: `migration_validator/gui/static/style.css`

**Interfaces:**
- Consumes: `MigView.filterRuns` from Task 5; `this.cache.runs` and `selectRun(name)` already in `app.js`.
- Produces: `this.state.combo = {open: bool, query: string, index: number}`; `renderRunCombo()` called from `render()`; `this.btnNewRunEl` (topbar primary button) used by Task 7.

- [ ] **Step 1: Replace the topbar and sidebar markup in `index.html`**

Replace the whole `<div class="topbar">…</div>` with:

```html
    <div class="topbar">
      <div class="brand">
        <div class="brand-mark">mv</div>
        <span class="brand-name">mig-validate</span>
      </div>
      <div class="run-combo" id="run-combo">
        <button class="run-combo-toggle" id="run-combo-toggle" type="button" aria-haspopup="listbox" aria-expanded="false">
          <span class="run-combo-label">Run:</span>
          <span class="mono run-combo-current" id="run-combo-current">—</span>
          <span class="run-combo-caret">▾</span>
        </button>
        <div class="run-combo-panel" id="run-combo-panel" hidden>
          <input class="form-input mono run-combo-filter" id="run-combo-filter" type="text" placeholder="filter runs…" autocomplete="off">
          <div class="run-combo-list" id="run-combo-list" role="listbox"></div>
          <div class="run-combo-foot">
            <a href="#" id="run-combo-new">+ New run</a>
          </div>
        </div>
      </div>
      <div class="topbar-spacer"></div>
      <div class="profile">
        <span>Profile:</span><span class="mono profile-value" id="profile-name">…</span>
      </div>
      <button class="btn btn-secondary" id="btn-checks">Checks</button>
      <button class="btn btn-primary" id="btn-new-run">+ New run</button>
      <button class="btn btn-secondary" id="btn-new-capture">New capture</button>
    </div>
```

In the sidebar, delete these three lines:

```html
        <div class="sidebar-section-header">
          <span>Runs</span>
          <a href="#" class="new-run-link" id="btn-new-run">+ New run</a>
        </div>
        <div id="sidebar-runs"></div>
```

and change the Snapshots header so it is the first section:

```html
        <div class="sidebar-section-header">Snapshots</div>
```

- [ ] **Step 2: Add combobox state and element refs in the `App` constructor**

In `this.state = {…}` add:

```js
      combo: { open: false, query: "", index: 0 },
```

Replace `this.sidebarRunsEl = document.getElementById("sidebar-runs");` with:

```js
    this.comboEl = document.getElementById("run-combo");
    this.comboToggleEl = document.getElementById("run-combo-toggle");
    this.comboCurrentEl = document.getElementById("run-combo-current");
    this.comboPanelEl = document.getElementById("run-combo-panel");
    this.comboFilterEl = document.getElementById("run-combo-filter");
    this.comboListEl = document.getElementById("run-combo-list");
    this.btnNewRunEl = document.getElementById("btn-new-run");
```

Replace the `const newRunLink = …` block with:

```js
    this.btnNewRunEl.addEventListener("click", () => this.openNewRunForm());
    document.getElementById("run-combo-new").addEventListener("click", (e) => {
      e.preventDefault();
      this.closeRunCombo();
      this.openNewRunForm();
    });
    this.comboToggleEl.addEventListener("click", () => {
      if (this.state.combo.open) this.closeRunCombo();
      else this.openRunCombo();
    });
    this.comboFilterEl.addEventListener("input", (e) => {
      this.state.combo.query = e.target.value;
      this.state.combo.index = 0;
      this.renderRunCombo();
    });
    this.comboFilterEl.addEventListener("keydown", (e) => this.onComboKey(e));
    document.addEventListener("mousedown", (e) => {
      if (this.state.combo.open && !this.comboEl.contains(e.target)) this.closeRunCombo();
    });
```

- [ ] **Step 3: Add the combobox methods (after `updateProfileBadge`)**

```js
  // -- run combobox (topbar) ----------------------------------------------

  openRunCombo() {
    this.state.combo = { open: true, query: "", index: 0 };
    this.comboFilterEl.value = "";
    this.renderRunCombo();
    this.comboFilterEl.focus();
  }

  closeRunCombo() {
    if (!this.state.combo.open) return;
    this.state.combo.open = false;
    this.renderRunCombo();
  }

  comboMatches() {
    return MigView.filterRuns(this.cache.runs, this.state.combo.query);
  }

  onComboKey(e) {
    const matches = this.comboMatches();
    const combo = this.state.combo;
    if (e.key === "Escape") {
      e.preventDefault();
      this.closeRunCombo();
      this.comboToggleEl.focus();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      combo.index = Math.min(combo.index + 1, Math.max(matches.length - 1, 0));
      this.renderRunCombo();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      combo.index = Math.max(combo.index - 1, 0);
      this.renderRunCombo();
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = matches[combo.index];
      if (pick) {
        this.closeRunCombo();
        this.selectRun(pick.name);
      }
    }
  }

  renderRunCombo() {
    const combo = this.state.combo;
    this.comboCurrentEl.textContent = this.state.run || "—";
    this.comboToggleEl.setAttribute("aria-expanded", combo.open ? "true" : "false");
    this.comboPanelEl.hidden = !combo.open;
    if (!combo.open) return;

    clear(this.comboListEl);
    const matches = this.comboMatches();
    if (matches.length === 0) {
      const text = this.cache.runs.length === 0 ? "no runs yet" : "no run matches";
      this.comboListEl.appendChild(el("div", { className: "run-combo-empty", text }));
      return;
    }
    matches.forEach((run, i) => {
      const active = run.name === this.state.run;
      const focused = i === combo.index;
      this.comboListEl.appendChild(
        el("div", {
          className:
            "run-combo-row" + (active ? " active" : "") + (focused ? " focused" : ""),
          attrs: { role: "option", "aria-selected": active ? "true" : "false" },
          onClick: () => {
            this.closeRunCombo();
            this.selectRun(run.name);
          },
          children: [
            el("span", { className: "mono run-combo-name", text: run.name }),
            el("span", {
              className: "run-combo-sub",
              text: `${run.snapshots} snapshot${run.snapshots === 1 ? "" : "s"}`,
            }),
          ],
        })
      );
    });
  }
```

- [ ] **Step 4: Remove the sidebar run list and hook the combobox into `render`**

In `renderSidebar()`, delete everything from `clear(this.sidebarRunsEl);` down to (and including) the closing `}` of the `for (const run of this.cache.runs) {…}` loop, so the method starts with `clear(this.sidebarSnapshotsEl);`.

In `render()`, replace `this.renderSidebar();` with:

```js
    this.renderSidebar();
    this.renderRunCombo();
```

- [ ] **Step 5: Styles**

Append to `style.css` after the `.profile-value` rule:

```css
/* Run combobox (topbar) */
.run-combo { position: relative; }

.run-combo-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  background: #fff;
  font: 500 13px 'IBM Plex Sans', sans-serif;
  color: #6b7280;
  cursor: pointer;
  min-width: 180px;
}
.run-combo-toggle:hover { border-color: #9ca3af; }
.run-combo-toggle[aria-expanded="true"] { border-color: #1a56db; }
.run-combo-current { color: #111827; font-weight: 600; }
.run-combo-caret { margin-left: auto; color: #9ca3af; font-size: 11px; }

.run-combo-panel {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  width: 320px;
  background: #fff;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  box-shadow: 0 10px 24px rgba(17, 24, 39, 0.12);
  padding: 8px;
  z-index: 20;
}
.run-combo-filter { width: 100%; box-sizing: border-box; margin-bottom: 6px; }
.run-combo-list { max-height: 320px; overflow-y: auto; }

.run-combo-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  padding: 7px 10px;
  border-radius: 6px;
  cursor: pointer;
}
.run-combo-row:hover, .run-combo-row.focused { background: #f3f4f6; }
.run-combo-row.active { background: #eef2ff; }
.run-combo-name { font-size: 13px; font-weight: 500; color: #374151; }
.run-combo-row.active .run-combo-name { color: #1a56db; font-weight: 600; }
.run-combo-sub { font-size: 12px; color: #9ca3af; white-space: nowrap; }
.run-combo-empty { padding: 10px; font-size: 12px; color: #9ca3af; }

.run-combo-foot {
  border-top: 1px solid #e5e7eb;
  margin-top: 6px;
  padding: 8px 10px 2px;
  font-size: 13px;
  font-weight: 600;
}
.run-combo-foot a { color: #1a56db; text-decoration: none; }
.run-combo-foot a:hover { text-decoration: underline; }
```

Delete the now-unused rules `.new-run-link`, `.new-run-link:hover`, and every `.run-card*` rule (lines starting with `.run-card` in `style.css`).

- [ ] **Step 6: Manual check in the browser**

Run: `mig-validate gui --run-root runs` and open `http://127.0.0.1:8321/` (default `--port` of the `gui` subcommand).
Expected: no sidebar run cards; the topbar shows `Run: mig01`; clicking opens the panel with the filter focused; typing `ptx` narrows the list; Enter selects; Escape closes; clicking outside closes; `+ New run` in the panel footer and in the topbar both open the form. Run `node --test tests/js/` and `pytest tests/gui -q` again (unchanged, must stay green).

- [ ] **Step 7: Commit**

```bash
git add migration_validator/gui/static/index.html migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): run combobox v topbaru s filtrem, sidebar bez seznamu runu"
```

---

### Task 7: Prominent New run and the empty state

**Files:**
- Modify: `migration_validator/gui/static/app.js` (`GUIDE_TEXT`, `renderEmptyState`, `render`)
- Modify: `migration_validator/gui/static/style.css`

**Interfaces:**
- Consumes: `this.btnNewRunEl` from Task 6.
- Produces: `GUIDE_TEXT.empty`; `.empty-state` shows the hint and one primary button.

- [ ] **Step 1: Guide copy for the empty state**

In `GUIDE_TEXT`, add before the `snapshot` entry:

```js
  empty: {
    title: "Zatím žádný run",
    body: [
      "Run je vstupní bod nástroje: adresář runs/<název>/ s run.yml, do kterého se ukládají snímky zařízení a párování portů.",
      "Založ první run tlačítkem + New run — pak můžeš sbírat snapshoty (New capture) a vyhodnocovat.",
    ],
  },
```

- [ ] **Step 2: Empty state body**

Replace `renderEmptyState()` with:

```js
  renderEmptyState() {
    clear(this.mainEl);
    this.mainEl.appendChild(
      el("div", {
        className: "empty-state",
        children: [
          el("h1", { className: "empty-state-title", text: "Zatím žádný run" }),
          el("p", {
            className: "empty-state-hint",
            text:
              "Run je vstupní bod nástroje — bez něj nejde sbírat snapshoty ani vyhodnocovat. " +
              "Založ první run: pojmenuj ho a vyplň zařízení.",
          }),
          el("button", {
            className: "btn btn-primary",
            text: "+ New run",
            onClick: () => this.openNewRunForm(),
          }),
        ],
      })
    );
  }
```

- [ ] **Step 3: Highlight the topbar button while in the empty state and disable New capture**

In `render()`, right after `this.btnChecksEl.classList.toggle(...)`:

```js
    const noRuns = this.cache.runs.length === 0;
    this.btnNewRunEl.classList.toggle("btn-pulse", noRuns);
    document.getElementById("btn-new-capture").disabled = noRuns;
```

- [ ] **Step 4: Styles**

Append to `style.css`:

```css
.empty-state-title { font-size: 20px; font-weight: 600; color: #111827; margin: 0; }
.empty-state-hint { max-width: 460px; text-align: center; line-height: 1.5; margin: 0; }

.btn:disabled { opacity: 0.5; cursor: default; }

@keyframes btn-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(26, 86, 219, 0.45); }
  50% { box-shadow: 0 0 0 6px rgba(26, 86, 219, 0); }
}
.btn-pulse { animation: btn-pulse 1.6s ease-out infinite; }
```

- [ ] **Step 5: Manual check**

Run: `mig-validate gui --run-root /tmp/empty-runs` (a directory with no runs, create it with `mkdir -p`).
Expected: main area shows the Czech hint and one `+ New run` button; the topbar `+ New run` pulses; `New capture` is disabled; the combobox reads `Run: —` and its panel says `no runs yet`. Create a run and verify the pulse stops and New capture is enabled.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): + New run jako primarni tlacitko, empty state s napovedou"
```

---

### Task 8: Archive run button and confirm modal

**Files:**
- Modify: `migration_validator/gui/static/app.js` (`GUIDE_TEXT.run`, state, `renderRunOverview` header, new `openArchiveModal`, `closeArchiveModal`, `confirmArchive`, `renderModal`, `render`)
- Modify: `migration_validator/gui/static/style.css`

**Interfaces:**
- Consumes: `POST /api/runs/{run}/archive` from Task 3; `this.cache.runs`, `selectRun`, `loadRun` from `app.js`.
- Produces: `this.state.archiveModal = null | {submitting: bool, error: string | null}`; `renderModal()` mounts `#modal-root` on `document.body`.

- [ ] **Step 1: State and guide copy**

In the constructor `this.state` add:

```js
      archiveModal: null,
```

In `GUIDE_TEXT.run.body`, append one paragraph:

```js
      "Archive run přesune adresář runu do runs/.archive/ — ze seznamu zmizí, snímky zůstanou. Archiv se čistí přes mig-validate run purge.",
```

- [ ] **Step 2: Button in the run overview header**

In `renderRunOverview()`, right after `this.mainEl.appendChild(header);` add:

```js
    header.appendChild(
      el("button", {
        className: "btn btn-danger-secondary run-header-archive",
        text: "Archive run",
        onClick: () => this.openArchiveModal(),
      })
    );
```

- [ ] **Step 3: Modal methods (after the combobox methods)**

```js
  // -- archive run modal ------------------------------------------------------

  openArchiveModal() {
    this.state.archiveModal = { submitting: false, error: null };
    this.render();
  }

  closeArchiveModal() {
    this.state.archiveModal = null;
    this.render();
  }

  async confirmArchive() {
    const modal = this.state.archiveModal;
    const run = this.state.run;
    if (!modal || !run || modal.submitting) return;
    modal.submitting = true;
    modal.error = null;
    this.render();
    try {
      const res = await fetch(`/api/runs/${run}/archive`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        modal.error = body.detail || `archivace selhala (${res.status})`;
        modal.submitting = false;
        this.render();
        return;
      }
      this.cache.runs = (await (await fetch("/api/runs")).json()).runs;
      this.state.archiveModal = null;
      this.state.run = null;
      this.cache.detail = null;
      this.cache.evaluation = null;
      this.state.selectedSnapshot = null;
      this.state.openResults = {};
      if (this.cache.runs.length === 0) {
        this.state.view = "empty";
        this.render();
      } else {
        await this.selectRun(this.cache.runs[0].name);
      }
    } catch (err) {
      modal.error = String(err);
      modal.submitting = false;
      this.render();
    }
  }

  renderModal() {
    let root = document.getElementById("modal-root");
    if (!root) {
      root = el("div", { attrs: { id: "modal-root" } });
      document.body.appendChild(root);
    }
    clear(root);
    const modal = this.state.archiveModal;
    if (!modal) return;
    const detail = this.cache.detail || {};
    const count = (detail.snapshots || []).length;
    const children = [
      el("h3", { text: "Archive run" }),
      el("p", {
        children: [
          document.createTextNode("Run "),
          el("span", { className: "mono", text: this.state.run || "" }),
          document.createTextNode(
            ` (${count} snapshot${count === 1 ? "" : "s"}) se přesune do runs/.archive/ a zmizí ze seznamu. Data zůstanou na disku.`
          ),
        ],
      }),
    ];
    if (modal.error) children.push(el("div", { className: "field-error", text: modal.error }));
    children.push(
      el("div", {
        className: "footer-actions",
        children: [
          el("button", {
            className: "btn btn-secondary",
            text: "Cancel",
            onClick: modal.submitting ? null : () => this.closeArchiveModal(),
          }),
          el("button", {
            className: "btn btn-danger",
            text: modal.submitting ? "Archiving…" : "Archive",
            onClick: modal.submitting ? null : () => this.confirmArchive(),
          }),
        ],
      })
    );
    root.appendChild(
      el("div", {
        className: "modal-backdrop",
        onClick: (e) => {
          if (e.target.classList.contains("modal-backdrop") && !modal.submitting) {
            this.closeArchiveModal();
          }
        },
        children: [el("div", { className: "modal", children })],
      })
    );
  }
```

- [ ] **Step 4: Hook into `render`**

In `render()`, after `this.renderRunCombo();` add:

```js
    this.renderModal();
```

- [ ] **Step 5: Styles**

Append to `style.css`:

```css
.run-header-archive { margin-left: auto; align-self: center; }

.btn-danger {
  border: none;
  background: #b91c1c;
  color: #fff;
}
.btn-danger-secondary:hover { background: #fef2f2; }

/* Modal */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(17, 24, 39, 0.45);
  display: grid;
  place-items: center;
  z-index: 50;
}
.modal {
  width: 440px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 10px;
  padding: 20px 22px;
  box-shadow: 0 20px 48px rgba(17, 24, 39, 0.25);
}
.modal h3 { margin: 0 0 10px; font-size: 16px; color: #111827; }
.modal p { margin: 0 0 14px; font-size: 13px; line-height: 1.5; color: #374151; }
```

`.run-header` is already `display: flex` with `flex-wrap: wrap` (style.css line ~241), so `margin-left: auto` pushes the button to the right of the title row.

- [ ] **Step 6: Manual check**

Run the GUI against a copy of `runs/` (`cp -r runs /tmp/runs-copy && mig-validate gui --run-root /tmp/runs-copy`).
Expected: `Archive run` shows on the run overview; the modal names the run and the snapshot count; Cancel and clicking the backdrop close it; Archive moves the folder to `/tmp/runs-copy/.archive/mig01-<stamp>/`, the combobox then shows the next run or the empty state. Start a capture on a device and press Archive while it runs: the modal shows `run ma bezici capture` and the run stays.

- [ ] **Step 7: Full suite and commit**

Run: `pytest -q && node --test tests/js/`
Expected: all PASS.

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): Archive run s potvrzovacim modalem, po archivaci prepne na dalsi run"
```

---

## Self-review

**Spec coverage**
- §Cross-cutting: seam module, roles, 403 message → Task 1. Run kind / profile fields belong to specs 2 and 3, not this plan.
- §1 combobox: markup, filter, keyboard, outside click, data source, `filterRuns` tested → Tasks 5 and 6.
- §2 New run entry point: primary button, empty state with hint and button, guide rail → Task 7.
- §3 archive: button, modal with name and count, route with 404/409/admin, `.archive` layout, `busy_run`, dotted dirs skipped, `api.archive_run` reusable, GUI reselects → Tasks 2, 3, 8.
- §4 purge CLI with list / `--older-than` / `--dry-run` / confirmation / `--yes` → Task 4.
- §Error handling: rename failure surfaces as 500 through FastAPI's default `OSError` handling, nothing extra needed; combobox load failure: `boot()` already throws on a failed fetch and the existing error path applies. Modal shows the `detail` text.
- §Testing: every listed test file has a task. Docs paragraph added in Task 4.

**Placeholder scan**: none.

**Type consistency**: `require(Permission.X)` used as a parameter default in Tasks 1 and 3; `busy_run(run) -> bool` in Tasks 2 and 3; `archive_run(name, *, run_root, now)` in Tasks 2, 3, 4; `ArchiveEntry` fields `name/archived/snapshots/path` in Task 4 API and CLI; `MigView.filterRuns(runs, query)` in Tasks 5 and 6; `this.state.combo` and `this.btnNewRunEl` in Tasks 6, 7; `this.state.archiveModal` in Task 8.
