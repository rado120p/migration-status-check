# GUI bulk single-device runs (groups) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create N single-device runs from one device list (a *group*), capture a phase on all of them through a bounded pool, and show one table with every box's capture state and verdict.

**Architecture:** No new run kind: a group member is a `single` run named `<group>-<node.lower()>` with `group` set in run.yml. `api.py` gains pure group operations (create, add devices, list, archive) that validate everything before writing. `gui/captures.py` gets a `queued` state and a semaphore pool. `gui/groups.py` builds the group summary (verdict per run, cached by manifest and profile mtime). `gui/group_routes.py` is a router like `profile_routes.py`; the capture-launch code shared with `POST /api/captures` moves to `gui/capture_launch.py`. Frontend stays vanilla JS: pure helpers in `view.js` (tested with `node --test`), the group view, combobox grouping and the bulk row editor in `app.js`, styles in `style.css`.

**Tech Stack:** Python 3.13, FastAPI + TestClient, pytest, PyYAML, threading, vanilla JS + CSS (no build step), `node --test` (Node 22) for JS logic.

**Spec:** `docs/superpowers/specs/2026-09-06-gui-bulk-groups-design.md`

## Global Constraints

- Tool strings (API `detail`, exceptions) are Czech **without diacritics**, as everywhere in the code base (`run 'x' neexistuje`, `nedostatecne opravneni: vyzaduje operate`).
- GUI copy in `GUIDE_TEXT` is Czech with diacritics; button and card labels are short English (`Capture pre on all`, `Add devices`, `Archive group`).
- Group name and run names match `_RUN_NAME_RE` = `^[a-z0-9_-]+$`. Member run name is exactly `f"{group}-{node.lower()}"`.
- Known platforms for bulk devices: `junos`, `junos-evo` (the same two the device sub-form offers).
- `capture_pool` lives under `connection:` in `config/settings.yml`, default `10`, must be `>= 1`. It bounds all captures on the server.
- Capture task states: `queued`, `running`, `done`, `failed`. "Active" = queued or running.
- Permissions: read = `view`, create/add/capture = `operate`, archive = `admin`, all through `require(Permission.X)`.
- Error mapping: `GroupError` → 409 with `detail: {"message": str, "rows": [{"index": int|null, "message": str}]}`; unknown group → 404; settings error → 503; unknown phase → 422; write failure midway → 500 with the names already written.
- No new Python dependencies, no JS dependencies, no build step.
- Test commands: `pyats-venv/bin/pytest -q` (all green on main today) and `node --test tests/js/`. Run both before every commit.
- Commit messages in repo style (`feat(api): ...`, `feat(gui): ...`, Czech without diacritics) ending with the Co-Authored-By and Claude-Session trailers given by the session.
- Work on branch `gui-bulk-groups` off `main` (created in Task 1, Step 0).

---

## File structure

| File | Responsibility |
|---|---|
| `migration_validator/auth.py` | `capture_pool` in `ConnectionSettings` and `load_settings` |
| `migration_validator/gui/captures.py` | `queued` state, semaphore pool, `active_task(run)` |
| `migration_validator/api.py` | `GroupError`, `group_run_name`, `group_runs`, `list_groups`, `create_group`, `add_group_devices`, `archive_group` |
| `migration_validator/gui/capture_launch.py` | `launch_capture(...)` shared by the single capture route and the batch route |
| `migration_validator/gui/groups.py` | `run_verdict`, `SummaryCache`, `build_group_summary` |
| `migration_validator/gui/group_routes.py` | `/api/groups*` router |
| `migration_validator/gui/app.py` | wire router, `group` in run summary and detail, `capture_pool` into `CaptureManager`, use `launch_capture` |
| `migration_validator/cli.py` | `_cmd_gui` passes `capture_pool` from settings |
| `migration_validator/gui/static/view.js` | pure `comboEntries`, `groupRowOrder`, `sortGroupRows`, `phaseCell` |
| `migration_validator/gui/static/app.js` | combobox grouping, group view, batch polling, bulk form, add-devices dialog, guide text |
| `migration_validator/gui/static/style.css` | group table, phase cells, verdict rules, bulk row editor |
| `tests/test_auth.py` | `capture_pool` parsing |
| `tests/gui/test_captures.py` | queued state, pool, `active_task` |
| `tests/test_api_groups.py` | group operations |
| `tests/gui/test_groups_module.py` | verdict, cache, summary rows |
| `tests/gui/test_group_routes.py` | every `/api/groups*` route |
| `tests/gui/test_routes.py` | `group` in list and detail |
| `tests/js/view.test.js` | new pure helpers |
| `docs/cs/reference.md`, `docs/en/reference.md` | group section under "Run management" |

---

### Task 1: `capture_pool` setting

**Files:**
- Modify: `migration_validator/auth.py:22-36, 86-94`
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces: `ConnectionSettings.capture_pool: int` (default `DEFAULT_CAPTURE_POOL = 10`), settings key `connection.capture_pool`.

- [ ] **Step 0: Branch**

```bash
git checkout -b gui-bulk-groups main
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
def test_capture_pool_default_je_10(tmp_path):
    settings = load_settings(tmp_path / "settings.yml")
    assert settings.capture_pool == 10


def test_capture_pool_se_cte_ze_souboru(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  capture_pool: 3\n", encoding="utf-8")
    assert load_settings(path).capture_pool == 3


def test_capture_pool_pod_1_je_chyba(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("connection:\n  capture_pool: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="capture_pool musi byt >= 1"):
        load_settings(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/test_auth.py -q -k capture_pool`
Expected: 3 failed (`AttributeError: capture_pool` / unknown key error).

- [ ] **Step 3: Implement**

In `migration_validator/auth.py`:

```python
DEFAULT_CAPTURE_POOL = 10

_KNOWN_KEYS = frozenset(
    {"netconf_port", "timeout", "username", "ssh_key_paths", "password",
     "password_env", "capture_pool"}
)


@dataclass(frozen=True)
class ConnectionSettings:
    username: str = DEFAULT_USERNAME
    ssh_key_paths: tuple[str, ...] = ()
    netconf_port: int = DEFAULT_NETCONF_PORT
    timeout: int = DEFAULT_TIMEOUT
    password: str | None = None
    # Kolik captures smi bezet naraz (GUI, vsechny runy dohromady).
    capture_pool: int = DEFAULT_CAPTURE_POOL
```

In `load_settings`, before the final `return`:

```python
    capture_pool = DEFAULT_CAPTURE_POOL
    if connection.get("capture_pool") is not None:
        capture_pool = int(connection["capture_pool"])
        if capture_pool < 1:
            raise ValueError(f"{path}: capture_pool musi byt >= 1")
```

and add `capture_pool=capture_pool,` to the `ConnectionSettings(...)` constructor call.

- [ ] **Step 4: Run tests**

Run: `pyats-venv/bin/pytest tests/test_auth.py -q`
Expected: all pass (the "neznamy klic" test still passes since its unknown key is not `capture_pool`).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/auth.py tests/test_auth.py
git commit -m "feat(auth): capture_pool v settings.yml - limit soubeznych captures"
```

---

### Task 2: CaptureManager queued state and pool

**Files:**
- Modify: `migration_validator/gui/captures.py`
- Modify: `migration_validator/gui/app.py:87-98` (constructor arg)
- Modify: `migration_validator/cli.py:482-495`
- Test: `tests/gui/test_captures.py`

**Interfaces:**
- Produces: `CaptureManager(pool: int = 10)`, `ACTIVE_STATES = ("queued", "running")`, `CaptureManager.active_task(run: str) -> CaptureTask | None`, `CaptureTask.state` starts as `"queued"`.
- `create_app(run_root, profile_path, profiles_root, capture_pool: int = DEFAULT_CAPTURE_POOL)`.

- [ ] **Step 1: Update the wait helper and write failing tests**

In `tests/gui/test_captures.py` change both helpers to treat queued as not finished:

```python
def _wait_done(manager, task_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = manager.get(task_id)
        if task.state in ("done", "failed"):
            return task
        time.sleep(0.01)
    raise AssertionError("capture nedobehl")


def _wait_done_route(client, task_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/api/captures/{task_id}").json()
        if task["state"] in ("done", "failed"):
            return task
        time.sleep(0.01)
    raise AssertionError("capture nedobehl")
```

Append:

```python
def _wait_state(manager, task_id, state, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager.get(task_id).state == state:
            return
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} nedosel do stavu {state}")


def test_pool_drzi_treti_capture_ve_fronte():
    manager = CaptureManager(pool=2)
    gate = threading.Event()

    def blocking(on_progress):
        gate.wait(5)
        return object()

    a = manager.start(blocking, run="g-a", device="A", port=None, phase="pre")
    b = manager.start(blocking, run="g-b", device="B", port=None, phase="pre")
    c = manager.start(blocking, run="g-c", device="C", port=None, phase="pre")
    _wait_state(manager, a.id, "running")
    _wait_state(manager, b.id, "running")
    time.sleep(0.05)
    assert manager.get(c.id).state == "queued"
    gate.set()
    for task in (a, b, c):
        assert _wait_done(manager, task.id).state == "done"


def test_queued_zarizeni_je_busy():
    manager = CaptureManager(pool=1)
    gate = threading.Event()

    def blocking(on_progress):
        gate.wait(5)
        return object()

    manager.start(blocking, run="g-a", device="A", port=None, phase="pre")
    queued = manager.start(blocking, run="g-b", device="B", port=None, phase="pre")
    assert manager.get(queued.id).state == "queued"
    with pytest.raises(DeviceBusy):
        manager.start(blocking, run="g-b", device="B", port=None, phase="post")
    assert manager.busy_run("g-b") is True
    gate.set()


def test_active_task_vraci_queued_nebo_running():
    manager = CaptureManager(pool=1)
    gate = threading.Event()

    def blocking(on_progress):
        gate.wait(5)
        return object()

    running = manager.start(blocking, run="g-a", device="A", port=None, phase="pre")
    queued = manager.start(blocking, run="g-b", device="B", port=None, phase="pre")
    _wait_state(manager, running.id, "running")
    assert manager.active_task("g-a").id == running.id
    assert manager.active_task("g-b").id == queued.id
    assert manager.active_task("g-c") is None
    gate.set()
    _wait_done(manager, queued.id)
    assert manager.active_task("g-a") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/gui/test_captures.py -q`
Expected: the three new tests fail (`TypeError: unexpected keyword 'pool'`).

- [ ] **Step 3: Implement**

Replace the class body in `migration_validator/gui/captures.py`:

```python
ACTIVE_STATES = ("queued", "running")


@dataclass
class CaptureTask:
    id: str
    run: str
    device: str
    port: str | None
    phase: str
    state: str = "queued"  # queued | running | done | failed
    steps: list[dict] = field(default_factory=list)
    error: str | None = None
    failed_collectors: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        ...  # unchanged


class CaptureManager:
    """Pool omezuje pocet soucasne bezicich captures (settings.yml
    connection.capture_pool). Task ceka jako `queued`, dokud se neuvolni
    slot; busy kontrola zarizeni i runu pocita queued i running."""

    def __init__(self, pool: int = 10) -> None:
        self._tasks: dict[str, CaptureTask] = {}
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(pool)

    def get(self, task_id: str) -> CaptureTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def busy_run(self, run: str) -> bool:
        """True, dokud na runu ceka nebo bezi aspon jeden capture."""
        return self.active_task(run) is not None

    def active_task(self, run: str) -> CaptureTask | None:
        with self._lock:
            for task in self._tasks.values():
                if task.run == run and task.state in ACTIVE_STATES:
                    return task
        return None

    def start(self, fn, *, run, device, port, phase) -> CaptureTask:
        with self._lock:
            for task in self._tasks.values():
                if task.device == device and task.state in ACTIVE_STATES:
                    raise DeviceBusy(
                        f"na zarizeni {device} uz bezi capture ({task.id})"
                    )
            task = CaptureTask(
                id=uuid.uuid4().hex[:12], run=run, device=device,
                port=port, phase=phase,
            )
            self._tasks[task.id] = task

        def on_progress(step, status, message):
            ...  # unchanged

        def worker() -> None:
            with self._slots:
                with self._lock:
                    task.state = "running"
                try:
                    outcome = fn(on_progress)
                    failed = getattr(outcome, "failed_collectors", None)
                    if failed:
                        task.failed_collectors = failed
                    warns = getattr(outcome, "warnings", None)
                    if warns:
                        task.warnings = warns
                    task.state = "done"
                except Exception as error:  # noqa: BLE001 - stav musi byt failed vzdy
                    task.state = "failed"
                    task.error = str(error)

        threading.Thread(target=worker, daemon=True).start()
        return task
```

`create_app` in `gui/app.py`: add parameter `capture_pool: int = DEFAULT_CAPTURE_POOL` (import `DEFAULT_CAPTURE_POOL` from `migration_validator.auth`) and build `manager = CaptureManager(pool=capture_pool)`.

`cli.py` `_cmd_gui`: after the import, read the pool once:

```python
    from migration_validator.auth import load_settings
    from migration_validator.gui.app import create_app

    app = create_app(
        run_root=args.run_root, profile_path=args.profile,
        profiles_root=args.profiles_root,
        capture_pool=load_settings().capture_pool,
    )
```

`load_settings` raises `ValueError` on a broken settings file; `cli.main` already turns `ValueError` into `EXIT_TOOL_ERROR` (check `main()` at `cli.py:726`; if it only catches `ToolError`, wrap: `except ValueError as error: raise ToolError(str(error)) from error`).

- [ ] **Step 4: Run tests**

Run: `pyats-venv/bin/pytest tests/gui/test_captures.py tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/captures.py migration_validator/gui/app.py migration_validator/cli.py tests/gui/test_captures.py
git commit -m "feat(gui): CaptureManager s poolem - stav queued, active_task, capture_pool z settings"
```

---

### Task 3: Group operations in `api.py`

**Files:**
- Modify: `migration_validator/api.py` (after `create_run`, before `ARCHIVE_DIR`)
- Test: `tests/test_api_groups.py` (create)

**Interfaces:**
- Produces:
  - `class GroupError(ValueError)` with `.rows: list[tuple[int | None, str]]`; `str()` joins messages with `"; "`.
  - `class GroupWriteError(OSError)` with `.written: list[str]` and `.cause: str`.
  - `KNOWN_PLATFORMS = frozenset({"junos", "junos-evo"})`.
  - `group_run_name(group: str, node: str) -> str`.
  - `group_runs(run_root) -> dict[str, list[str]]` (group → sorted member run names; tolerant of broken run.yml).
  - `list_groups(run_root) -> list[dict]` with keys `name, runs, profile, created`.
  - `create_group(group, devices, *, profile=None, run_root, profiles_root) -> list[str]` (run names).
  - `add_group_devices(group, devices, *, run_root) -> list[str]`.
  - `archive_group(group, *, run_root, now=None) -> list[str]` (archive dir names).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_groups.py`:

```python
"""Testy pro skupiny (bulk single runy) v api.py."""

from datetime import datetime, timezone

import pytest
import yaml

from migration_validator import api

DEVICES = [
    {"node": "PTX1-POP1", "host": "172.20.20.5", "platform": "junos-evo"},
    {"node": "MX2-POP1", "host": "172.20.20.7", "platform": "junos"},
]


def test_group_run_name_je_group_a_node_malymi():
    assert api.group_run_name("pop1", "PTX1-POP1") == "pop1-ptx1-pop1"


def test_create_group_zapise_n_single_runu(tmp_path):
    names = api.create_group("pop1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    assert names == ["pop1-ptx1-pop1", "pop1-mx2-pop1"]
    raw = yaml.safe_load((tmp_path / "pop1-ptx1-pop1" / "run.yml").read_text())
    assert raw["kind"] == "single"
    assert raw["group"] == "pop1"
    assert raw["devices"] == {
        "PTX1-POP1": {"host": "172.20.20.5", "platform": "junos-evo", "role": "single"}
    }
    assert "profile" not in raw or raw["profile"] is None


def test_create_group_nevalidni_jmeno_je_radek_none(tmp_path):
    with pytest.raises(api.GroupError) as excinfo:
        api.create_group("Pop 1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    assert excinfo.value.rows == [
        (None, "nevalidni jmeno skupiny 'Pop 1' - povolene znaky: a-z 0-9 _ -")
    ]
    assert list(tmp_path.iterdir()) == []


def test_create_group_prazdny_seznam(tmp_path):
    with pytest.raises(api.GroupError) as excinfo:
        api.create_group("pop1", [], run_root=tmp_path, profiles_root=tmp_path / "p")
    assert excinfo.value.rows == [(None, "seznam zarizeni je prazdny")]


def test_create_group_existujici_skupina(tmp_path):
    api.create_group("pop1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    with pytest.raises(api.GroupError) as excinfo:
        api.create_group("pop1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    assert excinfo.value.rows == [(None, "skupina 'pop1' uz existuje")]


def test_create_group_neexistujici_profil(tmp_path):
    with pytest.raises(api.GroupError) as excinfo:
        api.create_group("pop1", DEVICES, profile="nope", run_root=tmp_path,
                         profiles_root=tmp_path / "p")
    assert excinfo.value.rows[0][0] is None
    assert "profil 'nope' neexistuje" in excinfo.value.rows[0][1]


def test_create_group_radkove_chyby_maji_index_a_nic_se_nezapise(tmp_path):
    (tmp_path / "pop1-ex9").mkdir()
    (tmp_path / "pop1-ex9" / "run.yml").write_text("devices: {}\n", encoding="utf-8")
    devices = [
        {"node": "PTX1", "host": "", "platform": "junos-evo"},
        {"node": "MX2", "host": "10.0.0.2", "platform": "ios"},
        {"node": "ptx1", "host": "10.0.0.3", "platform": "junos"},
        {"node": "EX9", "host": "10.0.0.4", "platform": "junos"},
    ]
    with pytest.raises(api.GroupError) as excinfo:
        api.create_group("pop1", devices, run_root=tmp_path, profiles_root=tmp_path / "p")
    assert excinfo.value.rows == [
        (0, "zarizeni 'PTX1': chybi 'host'"),
        (1, "zarizeni 'MX2': neznama platforma 'ios', ocekavano junos nebo junos-evo"),
        (2, "zarizeni 'ptx1' je uvedeno dvakrat (radek 1)"),
        (3, "run 'pop1-ex9' uz existuje"),
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pop1-ex9"]


def test_group_runs_a_list_groups(tmp_path):
    api.create_group("pop1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    api.create_run("solo", kind="single", devices=[
        {"node": "X", "host": "1.1.1.1", "platform": "junos", "role": "single"}
    ], run_root=tmp_path)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "run.yml").write_text("[not a mapping", encoding="utf-8")
    assert api.group_runs(tmp_path) == {"pop1": ["pop1-mx2-pop1", "pop1-ptx1-pop1"]}
    groups = api.list_groups(tmp_path)
    assert len(groups) == 1
    assert groups[0]["name"] == "pop1"
    assert groups[0]["runs"] == 2
    assert groups[0]["profile"] is None
    assert groups[0]["created"].endswith("Z")


def test_add_group_devices_dedi_profil_a_odmita_duplicitu(tmp_path):
    from migration_validator.profiles.store import ProfileStore, empty_document
    ProfileStore(tmp_path / "p").save("core-only", empty_document())
    api.create_group("pop1", DEVICES, profile="core-only", run_root=tmp_path,
                     profiles_root=tmp_path / "p")
    names = api.add_group_devices(
        "pop1", [{"node": "EX1", "host": "10.0.0.9", "platform": "junos"}], run_root=tmp_path,
    )
    assert names == ["pop1-ex1"]
    raw = yaml.safe_load((tmp_path / "pop1-ex1" / "run.yml").read_text())
    assert raw["profile"] == "core-only"
    assert raw["group"] == "pop1"
    with pytest.raises(api.GroupError) as excinfo:
        api.add_group_devices(
            "pop1", [{"node": "ex1", "host": "10.0.0.9", "platform": "junos"}], run_root=tmp_path,
        )
    assert excinfo.value.rows == [(0, "run 'pop1-ex1' uz existuje")]


def test_add_group_devices_neznama_skupina(tmp_path):
    with pytest.raises(FileNotFoundError, match="skupina 'pop1' neexistuje"):
        api.add_group_devices("pop1", DEVICES, run_root=tmp_path)


def test_archive_group_presune_vsechny_cleny(tmp_path):
    api.create_group("pop1", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    now = datetime(2026, 9, 6, 8, 0, 0, tzinfo=timezone.utc)
    archived = api.archive_group("pop1", run_root=tmp_path, now=now)
    assert archived == [
        "pop1-mx2-pop1-20260906T080000Z", "pop1-ptx1-pop1-20260906T080000Z",
    ]
    assert api.group_runs(tmp_path) == {}
    assert (tmp_path / ".archive" / archived[0] / "run.yml").exists()


def test_archive_group_neznama_skupina(tmp_path):
    with pytest.raises(FileNotFoundError, match="skupina 'pop1' neexistuje"):
        api.archive_group("pop1", run_root=tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/test_api_groups.py -q`
Expected: all fail with `AttributeError` on `api.group_run_name` etc.

- [ ] **Step 3: Implement**

Add to `migration_validator/api.py` after `create_run` (imports: `import yaml` at the top next to the others):

```python
# --- skupiny (bulk single runy) --------------------------------------------

KNOWN_PLATFORMS = frozenset({"junos", "junos-evo"})


class GroupError(ValueError):
    """Validace skupiny selhala. `rows` = (index zarizeni | None, hlaska);
    None = chyba cele skupiny (jmeno, profil, prazdny seznam)."""

    def __init__(self, rows: list[tuple[int | None, str]]) -> None:
        self.rows = rows
        super().__init__("; ".join(message for _, message in rows))


class GroupWriteError(OSError):
    """Zapis run.yml selhal uprostred - `written` uz na disku je."""

    def __init__(self, written: list[str], cause: str) -> None:
        self.written = written
        self.cause = cause
        super().__init__(f"zapis skupiny selhal po {len(written)} runech: {cause}")


def group_run_name(group: str, node: str) -> str:
    return f"{group}-{node.lower()}"


def _raw_manifest(path: Path) -> dict[str, Any] | None:
    """Klice z run.yml bez validace - rozbity manifest vrati None."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return raw if isinstance(raw, dict) else None


def group_runs(run_root: str | Path = Path("runs")) -> dict[str, list[str]]:
    """group -> serazena jmena clenu. Teckovane adresare (archiv) se
    preskakuji, rozbity run.yml take (summary ho ukaze jako error radek,
    ale az kdyz uz skupinu zname z jinych clenu)."""
    root = Path(run_root)
    groups: dict[str, list[str]] = {}
    if not root.is_dir():
        return groups
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("."):
            continue
        raw = _raw_manifest(entry / "run.yml")
        if raw is None or not raw.get("group"):
            continue
        groups.setdefault(str(raw["group"]), []).append(entry.name)
    return groups


def _iso_mtime(path: Path) -> str:
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_groups(run_root: str | Path = Path("runs")) -> list[dict[str, Any]]:
    root = Path(run_root)
    out = []
    for name, members in sorted(group_runs(root).items()):
        manifests = [_raw_manifest(root / m / "run.yml") or {} for m in members]
        profile = next((m.get("profile") for m in manifests if m.get("profile")), None)
        created = min(_iso_mtime(root / m / "run.yml") for m in members)
        out.append({"name": name, "runs": len(members), "profile": profile, "created": created})
    return out


def _group_device(index: int, data: dict[str, str]) -> tuple[str, RunDevice]:
    label = data.get("node") or "?"
    for key in ("node", "host", "platform"):
        if not (data.get(key) or "").strip():
            raise GroupError([(index, f"zarizeni '{label}': chybi '{key}'")])
    platform = data["platform"].strip()
    if platform not in KNOWN_PLATFORMS:
        raise GroupError([(
            index,
            f"zarizeni '{label}': neznama platforma '{platform}', "
            "ocekavano junos nebo junos-evo",
        )])
    return data["node"].strip(), RunDevice(host=data["host"].strip(), platform=platform, role="single")


def _validate_group_devices(
    group: str, devices: list[dict[str, str]], run_root: Path
) -> list[tuple[str, str, RunDevice]]:
    """Vrati (run name, node, device) pro kazde zarizeni, nebo GroupError
    se vsemi radkovymi chybami najednou - formular je ukaze u radku."""
    rows: list[tuple[int | None, str]] = []
    # Prvni vyskyt kazdeho node (bez ohledu na velikost pismen) se urci
    # dopredu, aby duplicita platila i vuci radku, ktery sam neprosel.
    first: dict[str, int] = {}
    for index, data in enumerate(devices):
        key = (data.get("node") or "").strip().lower()
        if key:
            first.setdefault(key, index)
    planned: list[tuple[str, str, RunDevice]] = []
    for index, data in enumerate(devices):
        try:
            node, device = _group_device(index, data)
        except GroupError as error:
            rows.extend(error.rows)
            continue
        if first[node.lower()] != index:
            rows.append((index, f"zarizeni '{node}' je uvedeno dvakrat (radek {first[node.lower()] + 1})"))
            continue
        name = group_run_name(group, node)
        if RunStore(run_root, name).dir.exists():
            rows.append((index, f"run '{name}' uz existuje"))
            continue
        planned.append((name, node, device))
    if rows:
        raise GroupError(rows)
    return planned


def _write_group_members(
    group: str, planned: list[tuple[str, str, RunDevice]], *, profile: str | None, run_root: Path
) -> list[str]:
    written: list[str] = []
    for name, node, device in planned:
        manifest = RunManifest(
            devices={node: device}, kind="single", profile=profile, group=group,
        )
        try:
            RunStore(run_root, name).save(manifest)
        except OSError as error:
            raise GroupWriteError(written, str(error)) from error
        written.append(name)
    return written


def create_group(
    group: str,
    devices: list[dict[str, str]],
    *,
    profile: str | None = None,
    run_root: str | Path = Path("runs"),
    profiles_root: str | Path = Path("profiles"),
) -> list[str]:
    """Zalozi N single runu `<group>-<node>` se spolecnym `group`. Validuje
    vsechno dopredu; pri chybe se nezapise nic."""
    root = Path(run_root)
    if not _RUN_NAME_RE.match(group):
        raise GroupError([(None, f"nevalidni jmeno skupiny '{group}' - povolene znaky: a-z 0-9 _ -")])
    if not devices:
        raise GroupError([(None, "seznam zarizeni je prazdny")])
    if group in group_runs(root):
        raise GroupError([(None, f"skupina '{group}' uz existuje")])
    if profile is not None:
        profiles = ProfileStore(Path(profiles_root))
        if not profiles.exists(profile):
            raise GroupError([(None, f"profil '{profile}' neexistuje ({profiles.path(profile)})")])
    planned = _validate_group_devices(group, devices, root)
    return _write_group_members(group, planned, profile=profile, run_root=root)


def add_group_devices(
    group: str,
    devices: list[dict[str, str]],
    *,
    run_root: str | Path = Path("runs"),
) -> list[str]:
    """Prida cleny do existujici skupiny; profil dedi po skupine."""
    root = Path(run_root)
    members = group_runs(root).get(group)
    if not members:
        raise FileNotFoundError(f"skupina '{group}' neexistuje")
    if not devices:
        raise GroupError([(None, "seznam zarizeni je prazdny")])
    manifests = [_raw_manifest(root / m / "run.yml") or {} for m in members]
    profile = next((m.get("profile") for m in manifests if m.get("profile")), None)
    planned = _validate_group_devices(group, devices, root)
    return _write_group_members(group, planned, profile=profile, run_root=root)


def archive_group(
    group: str,
    *,
    run_root: str | Path = Path("runs"),
    now: datetime | None = None,
) -> list[str]:
    """Archivuje vsechny cleny skupiny (stejne razitko). Vraci jmena
    archivnich adresaru v poradi clenu."""
    root = Path(run_root)
    members = group_runs(root).get(group)
    if not members:
        raise FileNotFoundError(f"skupina '{group}' neexistuje")
    stamp_now = now or datetime.now(timezone.utc)
    archived: list[str] = []
    for name in members:
        try:
            archived.append(archive_run(name, run_root=root, now=stamp_now).name)
        except OSError as error:
            raise GroupWriteError(archived, str(error)) from error
    return archived
```

Note `_iso_mtime` duplicates `gui/app.py::_created_iso`; replace `_created_iso` in `app.py` with `api._iso_mtime` later in Task 5 to keep one copy (the function is small; the plan does that in Task 5 Step 3).

- [ ] **Step 4: Run tests**

Run: `pyats-venv/bin/pytest tests/test_api_groups.py tests/test_api_runs.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/api.py tests/test_api_groups.py
git commit -m "feat(api): skupiny - create_group, add_group_devices, list_groups, archive_group s radkovymi chybami"
```

---

### Task 4: Shared capture launch + group summary module

**Files:**
- Create: `migration_validator/gui/capture_launch.py`
- Create: `migration_validator/gui/groups.py`
- Modify: `migration_validator/gui/app.py:315-358` (use `launch_capture`)
- Test: `tests/gui/test_groups_module.py` (create)

**Interfaces:**
- Produces:
  - `launch_capture(manager, *, store, manifest, node, phase, port, parse_services, profile, settings) -> CaptureTask` (raises `DeviceBusy`, `KeyError` when node not in manifest).
  - `PHASES = ("pre", "post", "rollback")`.
  - `run_verdict(store, manifest, profile) -> tuple[str | None, dict, dict]` (verdict, service counts, check counts). Raises `ValueError("chybejici soubory snimku: ...")` when files are missing.
  - `class SummaryCache` with `get(run, key)`, `put(run, key, value)`, `retain(runs)`.
  - `build_group_summary(group, *, run_root, profiles, default_path, manager, cache) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/gui/test_groups_module.py`:

```python
"""Souhrn skupiny - verdikt runu, cache, radky."""

from migration_validator import api
from migration_validator.gui.captures import CaptureManager
from migration_validator.gui.groups import SummaryCache, build_group_summary, run_verdict
from migration_validator.gui.profiles import server_default_profile
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot, save_snapshot
from migration_validator.profiles.store import ProfileStore
from migration_validator.runs.manifest import CaptureRecord
from migration_validator.runs.store import RunStore

NOW = "2026-09-06T08:14:02Z"
DEVICES = [
    {"node": "PTX1", "host": "10.0.0.1", "platform": "junos-evo"},
    {"node": "MX2", "host": "10.0.0.2", "platform": "junos"},
]


def _write_snapshot(store, phase, node, address, *, oper="up"):
    scope = Scope(
        id="svc:L3VPN:IPVPN", kind="service",
        key=ScopeKey("L3VPN", "IPVPN", None), selectors=Selectors(interfaces=["et-0/0/1.113"]),
    )
    snapshot = Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(started_at=NOW, finished_at=NOW, phase=phase,
                            collectors={"interfaces": {"status": "ok"}}),
        facts={"interfaces": {"et-0/0/1.113": {
            "admin_status": "up", "oper_status": oper,
            "input_pps": 400, "output_pps": 400, "input_errors": 0, "output_errors": 0,
        }}},
        probes={"ping": []}, scopes=[scope], inventory=[],
    )
    path = store.snapshot_path(phase, node, None)
    save_snapshot(snapshot, path)
    return path


def _capture(store, phase, node, address, **kw):
    manifest = store.load()
    path = _write_snapshot(store, phase, node, address, **kw)
    manifest.record_capture(CaptureRecord(phase, node, None, path.name, NOW))
    store.save(manifest)


def _group(tmp_path):
    api.create_group("g", DEVICES, run_root=tmp_path, profiles_root=tmp_path / "p")
    return RunStore(tmp_path, "g-ptx1"), RunStore(tmp_path, "g-mx2")


def test_run_verdict_bez_post_je_none(tmp_path):
    ptx, _ = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    verdict, services, checks = run_verdict(ptx, ptx.load(), server_default_profile(None))
    assert verdict is None
    assert services["pass"] == 0 and checks["pass"] == 0


def test_run_verdict_pre_a_post_je_pass(tmp_path):
    ptx, _ = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1")
    verdict, services, checks = run_verdict(ptx, ptx.load(), server_default_profile(None))
    assert verdict == "PASS"
    assert services["pass"] == 1
    assert checks["pass"] >= 1


def test_run_verdict_down_rozhrani_je_fail(tmp_path):
    ptx, _ = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1", oper="down")
    verdict, services, _ = run_verdict(ptx, ptx.load(), server_default_profile(None))
    assert verdict == "FAIL"
    assert services["fail"] == 1


def _summary(tmp_path, cache=None, manager=None):
    return build_group_summary(
        "g", run_root=tmp_path, profiles=ProfileStore(tmp_path / "p"), default_path=None,
        manager=manager or CaptureManager(), cache=cache or SummaryCache(),
    )


def test_summary_radky_a_verdikty(tmp_path):
    ptx, mx = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1", oper="down")
    _capture(mx, "pre", "MX2", "10.0.0.2")
    summary = _summary(tmp_path)
    assert summary["name"] == "g"
    assert summary["profile"] is None
    rows = {row["run"]: row for row in summary["runs"]}
    assert rows["g-ptx1"]["node"] == "PTX1"
    assert rows["g-ptx1"]["phases"] == {"pre": NOW, "post": NOW, "rollback": None}
    assert rows["g-ptx1"]["verdict"] == "FAIL"
    assert rows["g-ptx1"]["active_task"] is None
    assert rows["g-ptx1"]["error"] is None
    assert rows["g-mx2"]["verdict"] is None
    assert rows["g-mx2"]["phases"]["post"] is None
    assert summary["verdicts"] == {"FAIL": 1, "none": 1}


def test_summary_neznama_skupina_je_none(tmp_path):
    assert _summary(tmp_path) is None


def test_summary_rozbity_manifest_je_error_radek(tmp_path):
    ptx, mx = _group(tmp_path)
    mx.manifest_path.write_text("kind: bulk\ngroup: g\ndevices: {}\n", encoding="utf-8")
    summary = _summary(tmp_path)
    rows = {row["run"]: row for row in summary["runs"]}
    assert "neznamy kind 'bulk'" in rows["g-mx2"]["error"]
    assert rows["g-mx2"]["verdict"] is None
    assert rows["g-ptx1"]["error"] is None
    assert summary["verdicts"] == {"none": 1, "error": 1}


def test_summary_chybejici_snimek_je_error_radek(tmp_path):
    ptx, _ = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1")
    next(ptx.dir.glob("snapshot_pre_*.json")).unlink()
    rows = {row["run"]: row for row in _summary(tmp_path)["runs"]}
    assert rows["g-ptx1"]["error"].startswith("chybejici soubory snimku")


def test_summary_active_task_z_manageru(tmp_path):
    import threading
    _group(tmp_path)
    manager = CaptureManager(pool=1)
    gate = threading.Event()
    task = manager.start(lambda on_progress: gate.wait(5), run="g-ptx1", device="PTX1",
                         port=None, phase="post")
    rows = {row["run"]: row for row in _summary(tmp_path, manager=manager)["runs"]}
    assert rows["g-ptx1"]["active_task"]["id"] == task.id
    assert rows["g-ptx1"]["active_task"]["phase"] == "post"
    assert rows["g-ptx1"]["active_task"]["state"] in ("queued", "running")
    gate.set()


def test_summary_cache_nevyhodnocuje_dvakrat(tmp_path, monkeypatch):
    ptx, _ = _group(tmp_path)
    _capture(ptx, "pre", "PTX1", "10.0.0.1")
    _capture(ptx, "post", "PTX1", "10.0.0.1")
    calls = []
    real = api.evaluate

    def spy(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(api, "evaluate", spy)
    cache = SummaryCache()
    _summary(tmp_path, cache=cache)
    _summary(tmp_path, cache=cache)
    assert len(calls) == 1
    # nova capture posune mtime run.yml -> prepocet
    import os, time
    stamp = time.time() + 5
    os.utime(ptx.manifest_path, (stamp, stamp))
    _summary(tmp_path, cache=cache)
    assert len(calls) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/gui/test_groups_module.py -q`
Expected: `ModuleNotFoundError: migration_validator.gui.groups`.

- [ ] **Step 3: Implement `capture_launch.py`**

```python
"""Spusteni capture do runu pres CaptureManager - spolecne pro
POST /api/captures a davkovy capture skupiny."""

from __future__ import annotations

from migration_validator.auth import ConnectionSettings
from migration_validator.config import Profile
from migration_validator.connection.junos import ConnectionOptions
from migration_validator.gui.captures import CaptureManager, CaptureTask
from migration_validator.runs.manifest import RunManifest
from migration_validator.runs.orchestrate import capture_into_run
from migration_validator.runs.store import RunStore


def launch_capture(
    manager: CaptureManager,
    *,
    store: RunStore,
    manifest: RunManifest,
    node: str,
    phase: str,
    port: str | None,
    parse_services: bool,
    profile: Profile,
    settings: ConnectionSettings,
) -> CaptureTask:
    """Zaradi capture zarizeni `node` do manageru. KeyError, kdyz node
    v runu neni; DeviceBusy propada z manageru."""
    device = manifest.devices[node]
    options = ConnectionOptions(
        host=device.host,
        username=settings.username,
        ssh_key_paths=settings.ssh_key_paths,
        password=settings.password,
        port=settings.netconf_port,
        timeout=settings.timeout,
    )

    def fn(on_progress):
        return capture_into_run(
            store,
            host=device.host,
            phase=phase,
            port=port,
            options=options,
            profile=profile,
            parse_services=parse_services,
            overwrite=True,  # GUI resi prepis potvrzenim ve formulari
            on_progress=on_progress,
        )

    return manager.start(fn, run=store.name, device=node, port=port, phase=phase)
```

Then rewrite `start_capture` in `gui/app.py` to use it (keeps behaviour, drops the duplicated closure):

```python
    @app.post("/api/captures", status_code=202)
    def start_capture(body: CaptureBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        store = _require_store(body.run)
        manifest = store.load()
        if body.device not in manifest.devices:
            raise HTTPException(
                status_code=404, detail=f"zarizeni '{body.device}' neni v runu"
            )
        try:
            settings = load_settings()
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        profile = _profile_for(store, manifest)
        try:
            task = launch_capture(
                manager, store=store, manifest=manifest, node=body.device,
                phase=body.phase, port=body.port, parse_services=body.parse_services,
                profile=profile, settings=settings,
            )
        except DeviceBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"id": task.id}
```

Remove the now-unused imports from `app.py` (`ConnectionOptions`, `capture_into_run`) and add `from migration_validator.gui.capture_launch import launch_capture`.

- [ ] **Step 4: Implement `groups.py`**

```python
"""Souhrn skupiny (bulk single runy): jeden radek na clena s fazemi,
bezicim taskem a verdiktem. Verdikt = nejhorsi Status pres evaluace runu
(stejna cesta jako GET /api/runs/{run}/evaluation), cachovany podle mtime
run.yml a souboru profilu."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from migration_validator import api
from migration_validator.config import Profile
from migration_validator.gui.captures import CaptureManager
from migration_validator.gui.profiles import profile_for_run
from migration_validator.models.result import Status, count_statuses
from migration_validator.models.snapshot import load_snapshot
from migration_validator.profiles.store import ProfileStore
from migration_validator.runs.manifest import RunManifest
from migration_validator.runs.pairing import plan_evaluations
from migration_validator.runs.store import RunStore

PHASES = ("pre", "post", "rollback")

Counts = dict[str, int]
Verdict = tuple[str | None, Counts, Counts]


def _zero() -> Counts:
    return count_statuses([])


def _add(into: Counts, counts: dict[str, Any]) -> None:
    for key in into:
        into[key] += int(counts.get(key, 0))


def run_verdict(store: RunStore, manifest: RunManifest, profile: Profile) -> Verdict:
    """(verdikt, pocty sluzeb, pocty checku). Verdikt None = zadna evaluace
    (jen pre). ValueError pri chybejicich souborech snimku."""
    missing = store.missing_snapshots(manifest)
    if missing:
        raise ValueError("chybejici soubory snimku: " + ", ".join(sorted(missing)))
    services, checks = _zero(), _zero()
    worst: list[Status] = []
    for evaluation in plan_evaluations(manifest):
        subject = load_snapshot(str(store.dir / evaluation.subject.snapshot))
        baseline = None
        if evaluation.baseline is not None:
            baseline = load_snapshot(str(store.dir / evaluation.baseline.snapshot))
        result = api.evaluate(
            subject, baseline=baseline, config=profile.checks,
            service_types=profile.service_types, profile_name=profile.name or None,
        )
        statuses = [scope.status for scope in result.scopes]
        _add(services, count_statuses(statuses))
        _add(checks, result.summary)
        worst.append(Status.worst(statuses))
    if not worst:
        return None, services, checks
    return Status.worst(worst).value, services, checks


class SummaryCache:
    """run -> (klic, verdikt). Klic = (mtime run.yml, mtime profilu)."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[tuple[int, int], Verdict]] = {}

    def get(self, run: str, key: tuple[int, int]) -> Verdict | None:
        entry = self._entries.get(run)
        return entry[1] if entry and entry[0] == key else None

    def put(self, run: str, key: tuple[int, int], value: Verdict) -> None:
        self._entries[run] = (key, value)

    def retain(self, runs: set[str]) -> None:
        for name in list(self._entries):
            if name not in runs:
                del self._entries[name]


def _profile_mtime(manifest: RunManifest, profiles: ProfileStore, default_path: str | None) -> int:
    path = profiles.path(manifest.profile) if manifest.profile else (
        Path(default_path) if default_path else None
    )
    try:
        return path.stat().st_mtime_ns if path else 0
    except OSError:
        return 0


def _member_row(
    store: RunStore, *, profiles: ProfileStore, default_path: str | None,
    manager: CaptureManager, cache: SummaryCache,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "run": store.name, "node": None, "host": None, "platform": None,
        "phases": {phase: None for phase in PHASES}, "active_task": None,
        "verdict": None, "services": _zero(), "checks": _zero(), "error": None,
    }
    task = manager.active_task(store.name)
    if task is not None:
        row["active_task"] = {"id": task.id, "phase": task.phase, "state": task.state}
    try:
        manifest = store.load()
    except (ValueError, OSError) as error:
        row["error"] = str(error)
        return row
    node, device = next(iter(manifest.devices.items()), (None, None))
    if device is not None:
        row.update({"node": node, "host": device.host, "platform": device.platform})
        for phase in PHASES:
            record = manifest.find_capture(phase, node, None)
            row["phases"][phase] = record.taken if record else None
    try:
        profile = profile_for_run(manifest, store.manifest_path, store=profiles, default_path=default_path)
        key = (store.manifest_path.stat().st_mtime_ns, _profile_mtime(manifest, profiles, default_path))
        verdict = cache.get(store.name, key)
        if verdict is None:
            verdict = run_verdict(store, manifest, profile)
            cache.put(store.name, key, verdict)
    except (ValueError, OSError) as error:
        row["error"] = str(error)
        return row
    row["verdict"], row["services"], row["checks"] = verdict
    return row


def build_group_summary(
    group: str, *, run_root: Path, profiles: ProfileStore, default_path: str | None,
    manager: CaptureManager, cache: SummaryCache,
) -> dict[str, Any] | None:
    members = api.group_runs(run_root).get(group)
    if not members:
        return None
    cache.retain(set(members))
    rows = [
        _member_row(RunStore(run_root, name), profiles=profiles, default_path=default_path,
                    manager=manager, cache=cache)
        for name in members
    ]
    verdicts: dict[str, int] = {}
    for row in rows:
        key = "error" if row["error"] else (row["verdict"] or "none")
        verdicts[key] = verdicts.get(key, 0) + 1
    header = next((g for g in api.list_groups(run_root) if g["name"] == group), {})
    return {
        "name": group,
        "profile": header.get("profile"),
        "created": header.get("created"),
        "runs": rows,
        "verdicts": verdicts,
    }
```

`Status.worst([])` returns `SKIP`, so a run whose evaluation matched no scope reports verdict `SKIP`; the GUI treats it like `no post` colour-wise but keeps the label. Verdict keys in `verdicts` are the `Status` values plus `none` and `error`.

- [ ] **Step 5: Run tests**

Run: `pyats-venv/bin/pytest tests/gui -q`
Expected: all pass (including the existing capture route tests through the refactor).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/gui/capture_launch.py migration_validator/gui/groups.py migration_validator/gui/app.py tests/gui/test_groups_module.py
git commit -m "feat(gui): souhrn skupiny - verdikt runu, cache podle mtime, sdilene spusteni capture"
```

---

### Task 5: `/api/groups*` router and `group` in run payloads

**Files:**
- Create: `migration_validator/gui/group_routes.py`
- Modify: `migration_validator/gui/app.py` (wire router, `group` in `_run_summary` and `_detail`, replace `_created_iso` with `api._iso_mtime`)
- Test: `tests/gui/test_group_routes.py` (create), `tests/gui/test_routes.py` (append)

**Interfaces:**
- Produces: `build_groups_router(*, run_root, profiles, profile_path, manager, cache) -> APIRouter` mounted at `/api/groups`.
- Request bodies: `GroupDeviceBody(node, host, platform)`, `CreateGroupBody(group, profile=None, devices, capture_pre=False)`, `AddDevicesBody(devices)`, `GroupCaptureBody(phase)`.
- Consumes: Task 3 api functions, Task 4 `launch_capture`, `build_group_summary`, `SummaryCache`.

- [ ] **Step 1: Write the failing route tests**

Create `tests/gui/test_group_routes.py`:

```python
"""Routes /api/groups - bulk single runy."""

import threading
import time

from fastapi.testclient import TestClient

from migration_validator import api
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor
from migration_validator.gui.captures import CaptureManager

DEVICES = [
    {"node": "PTX1", "host": "10.0.0.1", "platform": "junos-evo"},
    {"node": "MX2", "host": "10.0.0.2", "platform": "junos"},
]


def _client(tmp_path, role=None, pool=10):
    app = create_app(run_root=tmp_path, profiles_root=tmp_path / "p", capture_pool=pool)
    if role is not None:
        app.state.actor_provider = lambda request: Actor(role=role)
    return TestClient(app)


def _body(**over):
    body = {"group": "pop1", "profile": None, "devices": DEVICES, "capture_pre": False}
    body.update(over)
    return body


def test_post_groups_zalozi_skupinu_a_vrati_souhrn(tmp_path):
    resp = _client(tmp_path).post("/api/groups", json=_body())
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "pop1"
    assert [row["run"] for row in data["runs"]] == ["pop1-mx2", "pop1-ptx1"]
    assert data["batch"] is None
    assert (tmp_path / "pop1-ptx1" / "run.yml").exists()


def test_post_groups_radkove_chyby_409(tmp_path):
    devices = [DEVICES[0], {"node": "ptx1", "host": "1.1.1.1", "platform": "junos"}]
    resp = _client(tmp_path).post("/api/groups", json=_body(devices=devices))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["rows"] == [
        {"index": 1, "message": "zarizeni 'ptx1' je uvedeno dvakrat (radek 1)"}
    ]
    assert "dvakrat" in detail["message"]
    assert not (tmp_path / "pop1-ptx1").exists()


def test_post_groups_chyba_skupiny_ma_index_null(tmp_path):
    resp = _client(tmp_path).post("/api/groups", json=_body(group="Pop 1"))
    assert resp.status_code == 409
    assert resp.json()["detail"]["rows"][0]["index"] is None


def test_post_groups_vyzaduje_operate(tmp_path):
    resp = _client(tmp_path, role="viewer").post("/api/groups", json=_body())
    assert resp.status_code == 403


def test_get_groups_seznam(tmp_path):
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    data = client.get("/api/groups").json()
    assert data["groups"][0]["name"] == "pop1"
    assert data["groups"][0]["runs"] == 2


def test_get_group_404(tmp_path):
    assert _client(tmp_path).get("/api/groups/neni").status_code == 404


def test_get_runs_nese_group(tmp_path):
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    runs = {r["name"]: r for r in client.get("/api/runs").json()["runs"]}
    assert runs["pop1-ptx1"]["group"] == "pop1"
    assert client.get("/api/runs/pop1-ptx1").json()["group"] == "pop1"


def test_post_devices_prida_cleny(tmp_path):
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    resp = client.post("/api/groups/pop1/devices", json={
        "devices": [{"node": "EX1", "host": "10.0.0.9", "platform": "junos"}],
    })
    assert resp.status_code == 201
    assert [row["run"] for row in resp.json()["runs"]] == ["pop1-ex1", "pop1-mx2", "pop1-ptx1"]


def test_post_devices_neznama_skupina_404(tmp_path):
    resp = _client(tmp_path).post("/api/groups/neni/devices", json={"devices": DEVICES})
    assert resp.status_code == 404


def _blocking_capture(monkeypatch, gate):
    """Nahradi capture_into_run necim, co ceka na gate a nic nezapisuje."""
    import migration_validator.gui.capture_launch as launch

    def fake(store, **kwargs):
        gate.wait(5)
        return object()

    monkeypatch.setattr(launch, "capture_into_run", fake)


def test_post_captures_spusti_task_pro_kazdeho_clena(tmp_path, monkeypatch):
    gate = threading.Event()
    _blocking_capture(monkeypatch, gate)
    client = _client(tmp_path, pool=1)
    client.post("/api/groups", json=_body())
    resp = client.post("/api/groups/pop1/captures", json={"phase": "pre"})
    assert resp.status_code == 202
    data = resp.json()
    assert len(data["batch"]) == 12
    assert [t["run"] for t in data["tasks"]] == ["pop1-mx2", "pop1-ptx1"]
    assert all(t["task_id"] and t["error"] is None for t in data["tasks"])
    states = {client.get(f"/api/captures/{t['task_id']}").json()["state"] for t in data["tasks"]}
    assert states <= {"queued", "running"}
    summary = client.get("/api/groups/pop1").json()
    assert all(row["active_task"] is not None for row in summary["runs"])
    gate.set()


def test_post_captures_obsazene_zarizeni_je_radkova_chyba(tmp_path, monkeypatch):
    gate = threading.Event()
    _blocking_capture(monkeypatch, gate)
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    client.post("/api/captures", json={"run": "pop1-ptx1", "device": "PTX1", "phase": "pre"})
    resp = client.post("/api/groups/pop1/captures", json={"phase": "pre"})
    assert resp.status_code == 202
    tasks = {t["run"]: t for t in resp.json()["tasks"]}
    assert tasks["pop1-ptx1"]["task_id"] is None
    assert "uz bezi capture" in tasks["pop1-ptx1"]["error"]
    assert tasks["pop1-mx2"]["task_id"] is not None
    gate.set()


def test_post_captures_neznama_faze_422(tmp_path):
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    resp = client.post("/api/groups/pop1/captures", json={"phase": "during"})
    assert resp.status_code == 422
    assert "neznama faze" in resp.json()["detail"]


def test_post_groups_capture_pre_vrati_batch(tmp_path, monkeypatch):
    gate = threading.Event()
    _blocking_capture(monkeypatch, gate)
    resp = _client(tmp_path).post("/api/groups", json=_body(capture_pre=True))
    assert resp.status_code == 201
    batch = resp.json()["batch"]
    assert len(batch["tasks"]) == 2
    gate.set()


def test_archive_group_odmitne_beh_a_pak_archivuje(tmp_path, monkeypatch):
    gate = threading.Event()
    _blocking_capture(monkeypatch, gate)
    client = _client(tmp_path)
    client.post("/api/groups", json=_body())
    batch = client.post("/api/groups/pop1/captures", json={"phase": "pre"}).json()
    resp = client.post("/api/groups/pop1/archive")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "skupina 'pop1' ma bezici capture"
    gate.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        states = {client.get(f"/api/captures/{t['task_id']}").json()["state"] for t in batch["tasks"]}
        if states <= {"done", "failed"}:
            break
        time.sleep(0.01)
    resp = client.post("/api/groups/pop1/archive")
    assert resp.status_code == 200
    assert len(resp.json()["archived"]) == 2
    assert client.get("/api/groups/pop1").status_code == 404


def test_archive_group_vyzaduje_admin(tmp_path):
    client = _client(tmp_path, role="operator")
    assert client.post("/api/groups/pop1/archive").status_code == 403
```

Append to `tests/gui/test_routes.py`:

```python
def test_runs_bez_skupiny_maji_group_null(client):
    assert client.get("/api/runs").json()["runs"][0]["group"] is None
    assert client.get("/api/runs/mig01").json()["group"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pyats-venv/bin/pytest tests/gui/test_group_routes.py tests/gui/test_routes.py -q`
Expected: 404s and KeyErrors on `group`.

- [ ] **Step 3: Implement the router**

Create `migration_validator/gui/group_routes.py`:

```python
"""Routes /api/groups - bulk single runy (spec 2026-09-06).

Cteni view, zakladani a capture operate, archivace admin. Chyby validace
(GroupError) jdou jako 409 s detail {"message", "rows": [{"index",
"message"}]}, aby je formular ukazal u radku; neznama skupina 404; zapis,
ktery selhal uprostred, 500 s jiz zapsanymi runy."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from migration_validator import api
from migration_validator.auth import load_settings
from migration_validator.gui.authz import Actor, Permission, require
from migration_validator.gui.capture_launch import launch_capture
from migration_validator.gui.captures import CaptureManager, DeviceBusy
from migration_validator.gui.groups import PHASES, SummaryCache, build_group_summary
from migration_validator.gui.profiles import profile_for_run
from migration_validator.profiles.store import ProfileStore
from migration_validator.runs.store import RunStore


class GroupDeviceBody(BaseModel):
    node: str
    host: str
    platform: str


class CreateGroupBody(BaseModel):
    group: str
    profile: str | None = None
    devices: list[GroupDeviceBody]
    capture_pre: bool = False


class AddDevicesBody(BaseModel):
    devices: list[GroupDeviceBody]


class GroupCaptureBody(BaseModel):
    phase: str


def _group_error(error: api.GroupError) -> HTTPException:
    return HTTPException(status_code=409, detail={
        "message": str(error),
        "rows": [{"index": index, "message": message} for index, message in error.rows],
    })


def _write_error(error: api.GroupWriteError) -> HTTPException:
    return HTTPException(status_code=500, detail={
        "message": str(error), "written": error.written,
    })


def build_groups_router(
    *, run_root: Path, profiles: ProfileStore, profile_path: str | None,
    manager: CaptureManager, cache: SummaryCache,
) -> APIRouter:
    router = APIRouter(prefix="/api/groups")

    def _summary_or_404(group: str) -> dict[str, Any]:
        summary = build_group_summary(
            group, run_root=run_root, profiles=profiles, default_path=profile_path,
            manager=manager, cache=cache,
        )
        if summary is None:
            raise HTTPException(status_code=404, detail=f"skupina '{group}' neexistuje")
        return summary

    def _start_batch(group: str, phase: str) -> dict[str, Any]:
        members = api.group_runs(run_root).get(group)
        if not members:
            raise HTTPException(status_code=404, detail=f"skupina '{group}' neexistuje")
        try:
            settings = load_settings()
        except ValueError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        tasks = []
        for name in members:
            store = RunStore(run_root, name)
            entry: dict[str, Any] = {"run": name, "device": None, "task_id": None, "error": None}
            try:
                manifest = store.load()
                node = next(iter(manifest.devices))
                entry["device"] = node
                profile = profile_for_run(
                    manifest, store.manifest_path, store=profiles, default_path=profile_path,
                )
                task = launch_capture(
                    manager, store=store, manifest=manifest, node=node, phase=phase,
                    port=None, parse_services=False, profile=profile, settings=settings,
                )
                entry["task_id"] = task.id
            except (ValueError, OSError, StopIteration, DeviceBusy) as error:
                entry["error"] = str(error) or "run bez zarizeni"
            tasks.append(entry)
        return {"batch": uuid.uuid4().hex[:12], "tasks": tasks}

    @router.get("")
    def list_groups(actor: Actor = require(Permission.VIEW)) -> dict:
        return {"groups": api.list_groups(run_root)}

    @router.post("", status_code=201)
    def create_group(body: CreateGroupBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        try:
            api.create_group(
                body.group, [d.model_dump() for d in body.devices],
                profile=body.profile, run_root=run_root, profiles_root=profiles.root,
            )
        except api.GroupError as error:
            raise _group_error(error) from error
        except api.GroupWriteError as error:
            raise _write_error(error) from error
        batch = _start_batch(body.group, "pre") if body.capture_pre else None
        return {**_summary_or_404(body.group), "batch": batch}

    @router.get("/{group}")
    def group_detail(group: str, actor: Actor = require(Permission.VIEW)) -> dict:
        return _summary_or_404(group)

    @router.post("/{group}/devices", status_code=201)
    def add_devices(group: str, body: AddDevicesBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        try:
            api.add_group_devices(group, [d.model_dump() for d in body.devices], run_root=run_root)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except api.GroupError as error:
            raise _group_error(error) from error
        except api.GroupWriteError as error:
            raise _write_error(error) from error
        return _summary_or_404(group)

    @router.post("/{group}/captures", status_code=202)
    def group_capture(group: str, body: GroupCaptureBody, actor: Actor = require(Permission.OPERATE)) -> dict:
        if body.phase not in PHASES:
            raise HTTPException(
                status_code=422,
                detail=f"neznama faze '{body.phase}', ocekavano pre, post nebo rollback",
            )
        return _start_batch(group, body.phase)

    @router.post("/{group}/archive")
    def archive_group(group: str, actor: Actor = require(Permission.ADMIN)) -> dict:
        members = api.group_runs(run_root).get(group)
        if not members:
            raise HTTPException(status_code=404, detail=f"skupina '{group}' neexistuje")
        if any(manager.busy_run(name) for name in members):
            raise HTTPException(status_code=409, detail=f"skupina '{group}' ma bezici capture")
        try:
            archived = api.archive_group(group, run_root=run_root)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except api.GroupWriteError as error:
            raise _write_error(error) from error
        return {"archived": archived}

    return router
```

The `ProfileStore` needs a `.root` attribute; `app.py` already passes `profiles.root` to `create_run`, so it exists.

In `gui/app.py`:
- `from migration_validator.gui.group_routes import build_groups_router` and `from migration_validator.gui.groups import SummaryCache`.
- In `create_app`, after `manager = CaptureManager(pool=capture_pool)`: `cache = SummaryCache()`, `app.state.group_cache = cache`, and `app.include_router(build_groups_router(run_root=run_root, profiles=profiles, profile_path=profile_path, manager=manager, cache=cache))`.
- `_run_summary`: add `"group": manifest.group,` and replace `_created_iso(store.manifest_path)` with `api._iso_mtime(store.manifest_path)`; delete `_created_iso` and its unused `datetime` imports.
- `_detail`: add `"group": manifest.group,`.

- [ ] **Step 4: Run tests**

Run: `pyats-venv/bin/pytest tests/gui -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/group_routes.py migration_validator/gui/app.py tests/gui/test_group_routes.py tests/gui/test_routes.py
git commit -m "feat(gui): /api/groups - zalozeni, pridani zarizeni, davkovy capture, souhrn, archivace; group v run payloadech"
```

---

### Task 6: Pure JS helpers in `view.js`

**Files:**
- Modify: `migration_validator/gui/static/view.js` (before `const MigView = {`)
- Test: `tests/js/view.test.js` (append)

**Interfaces:**
- Produces (exported on `MigView`):
  - `comboEntries(runs, query)` → array of `{type: "group", name, count}` and `{type: "run", run, grouped: bool}`.
  - `VERDICT_ORDER = ["FAIL", "WARN", "RECV", "PASS", "SKIP", "INFO"]`.
  - `groupRowOrder(rows)` → new array sorted worst-first, error rows last, ties by node.
  - `sortGroupRows(rows, key, dir)` → sorted copy; `key` null = `groupRowOrder`; keys `node|host|platform|pre|post|rollback|verdict`; `dir` `"asc"|"desc"`.
  - `phaseCell(row, phase, taskState)` → `{kind: "time"|"none"|"queued"|"running"|"error", text, title}`; `taskState` is `{state, error, phase} | null` from the client's polling map.

- [ ] **Step 1: Write the failing tests**

Append to `tests/js/view.test.js`:

```js
const RUNS = [
  { name: "solo", kind: "single", group: null, devices: { X: {} } },
  { name: "pop1-mx2", kind: "single", group: "pop1", devices: { MX2: {} } },
  { name: "pop1-ptx1", kind: "single", group: "pop1", devices: { PTX1: {} } },
  { name: "mig01", kind: "migration", group: null, devices: { MX1: {}, PTX9: {} } },
];

test("comboEntries: groups first with members indented, ungrouped after", () => {
  const entries = MigView.comboEntries(RUNS, "");
  assert.deepStrictEqual(entries.map((e) => e.type === "group" ? `G:${e.name}:${e.count}` : `${e.grouped ? "  " : ""}${e.run.name}`), [
    "G:pop1:2", "  pop1-mx2", "  pop1-ptx1", "mig01", "solo",
  ]);
});

test("comboEntries: query on group name keeps the whole group; query on a node keeps its header", () => {
  const byGroup = MigView.comboEntries(RUNS, "POP1");
  assert.deepStrictEqual(byGroup.map((e) => e.type === "group" ? e.name : e.run.name), ["pop1", "pop1-mx2", "pop1-ptx1"]);
  const byNode = MigView.comboEntries(RUNS, "ptx1");
  assert.deepStrictEqual(byNode.map((e) => e.type === "group" ? e.name : e.run.name), ["pop1", "pop1-ptx1"]);
  const solo = MigView.comboEntries(RUNS, "solo");
  assert.deepStrictEqual(solo.map((e) => e.run.name), ["solo"]);
});

const ROWS = [
  { run: "g-a", node: "A", host: "1", platform: "junos", verdict: "PASS", error: null, phases: { pre: "t", post: "t", rollback: null } },
  { run: "g-b", node: "B", host: "2", platform: "junos", verdict: null, error: null, phases: { pre: "t", post: null, rollback: null } },
  { run: "g-c", node: "C", host: "3", platform: "junos-evo", verdict: "FAIL", error: null, phases: { pre: "t", post: "t", rollback: null } },
  { run: "g-d", node: "D", host: "4", platform: "junos", verdict: null, error: "rozbity", phases: { pre: null, post: null, rollback: null } },
  { run: "g-e", node: "E", host: "5", platform: "junos", verdict: "WARN", error: null, phases: { pre: "t", post: "t", rollback: null } },
];

test("groupRowOrder: worst first, no verdict after, error rows last", () => {
  assert.deepStrictEqual(MigView.groupRowOrder(ROWS).map((r) => r.node), ["C", "E", "A", "B", "D"]);
});

test("sortGroupRows: by column with direction; null key = worst-first", () => {
  assert.deepStrictEqual(MigView.sortGroupRows(ROWS, "node", "desc").map((r) => r.node), ["E", "D", "C", "B", "A"]);
  assert.deepStrictEqual(MigView.sortGroupRows(ROWS, "platform", "asc").map((r) => r.node), ["A", "B", "D", "E", "C"]);
  assert.deepStrictEqual(MigView.sortGroupRows(ROWS, "post", "asc").map((r) => r.node), ["B", "D", "A", "C", "E"]);
  assert.deepStrictEqual(MigView.sortGroupRows(ROWS, null, "asc").map((r) => r.node), ["C", "E", "A", "B", "D"]);
});

test("phaseCell: time, none, queued, running, error", () => {
  const row = { phases: { pre: "2026-09-06T08:14:02Z", post: null, rollback: null } };
  assert.deepStrictEqual(MigView.phaseCell(row, "pre", null), { kind: "time", text: "08:14", title: "2026-09-06T08:14:02Z" });
  assert.deepStrictEqual(MigView.phaseCell(row, "post", null), { kind: "none", text: "—", title: "" });
  assert.deepStrictEqual(MigView.phaseCell(row, "post", { phase: "post", state: "queued", error: null }), { kind: "queued", text: "queued", title: "" });
  assert.deepStrictEqual(MigView.phaseCell(row, "post", { phase: "post", state: "running", error: null }), { kind: "running", text: "running…", title: "" });
  assert.deepStrictEqual(MigView.phaseCell(row, "post", { phase: "post", state: "failed", error: "auth" }), { kind: "error", text: "error", title: "auth" });
  // task on another phase does not touch this cell
  assert.strictEqual(MigView.phaseCell(row, "pre", { phase: "post", state: "running", error: null }).kind, "time");
  // done task shows the time again (summary refetched by then)
  assert.strictEqual(MigView.phaseCell(row, "pre", { phase: "pre", state: "done", error: null }).kind, "time");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/js/`
Expected: the new tests fail with `TypeError: MigView.comboEntries is not a function`.

- [ ] **Step 3: Implement**

Insert before `const MigView = {` in `view.js`:

```js
/* Run combobox entries: group headers first (sorted by name) with their
   members indented, then ungrouped runs. A query matching a group name keeps
   the whole group; otherwise filterRuns decides per run and a matching
   member keeps its header. */
function comboEntries(runs, query) {
  const needle = (query || "").trim().toLowerCase();
  const groups = new Map();
  const loose = [];
  for (const run of runs || []) {
    if (run.group) {
      if (!groups.has(run.group)) groups.set(run.group, []);
      groups.get(run.group).push(run);
    } else {
      loose.push(run);
    }
  }
  const out = [];
  for (const name of [...groups.keys()].sort()) {
    const members = groups.get(name).slice().sort((a, b) => a.name.localeCompare(b.name));
    const kept = needle && !name.toLowerCase().includes(needle) ? filterRuns(members, needle) : members;
    if (!kept.length) continue;
    out.push({ type: "group", name, count: members.length });
    for (const run of kept) out.push({ type: "run", run, grouped: true });
  }
  for (const run of filterRuns(loose.slice().sort((a, b) => a.name.localeCompare(b.name)), needle)) {
    out.push({ type: "run", run, grouped: false });
  }
  return out;
}

const VERDICT_ORDER = ["FAIL", "WARN", "RECV", "PASS", "SKIP", "INFO"];

function verdictRank(row) {
  if (row.error) return VERDICT_ORDER.length + 1;
  if (!row.verdict) return VERDICT_ORDER.length;
  const index = VERDICT_ORDER.indexOf(row.verdict);
  return index === -1 ? VERDICT_ORDER.length : index;
}

function groupRowOrder(rows) {
  return rows.slice().sort((a, b) => verdictRank(a) - verdictRank(b) || (a.node || "").localeCompare(b.node || ""));
}

function sortGroupRows(rows, key, dir) {
  if (!key) return groupRowOrder(rows);
  const sign = dir === "desc" ? -1 : 1;
  const value = (row) => {
    if (key === "verdict") return verdictRank(row);
    if (key === "pre" || key === "post" || key === "rollback") return (row.phases && row.phases[key]) || "";
    return row[key] || "";
  };
  return rows.slice().sort((a, b) => {
    const va = value(a), vb = value(b);
    const cmp = typeof va === "number" ? va - vb : String(va).localeCompare(String(vb));
    return sign * cmp || (a.node || "").localeCompare(b.node || "");
  });
}

/* Phase cell of the group table. taskState = {phase, state, error} from the
   client's polling map for that run, or null. */
function phaseCell(row, phase, taskState) {
  if (taskState && taskState.phase === phase) {
    if (taskState.state === "queued") return { kind: "queued", text: "queued", title: "" };
    if (taskState.state === "running") return { kind: "running", text: "running…", title: "" };
    if (taskState.state === "failed") return { kind: "error", text: "error", title: taskState.error || "" };
  }
  const taken = row.phases && row.phases[phase];
  if (!taken) return { kind: "none", text: "—", title: "" };
  const match = /T(\d{2}:\d{2})/.exec(taken);
  return { kind: "time", text: match ? match[1] : taken, title: taken };
}
```

Add `comboEntries, VERDICT_ORDER, groupRowOrder, sortGroupRows, phaseCell,` to the `MigView` object.

- [ ] **Step 4: Run tests**

Run: `node --test tests/js/`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/gui/static/view.js tests/js/view.test.js
git commit -m "feat(gui): view.js - comboEntries, razeni radku skupiny, phaseCell"
```

---

### Task 7: Group view, combobox grouping, batch polling

**Files:**
- Modify: `migration_validator/gui/static/app.js` (state, `GUIDE_TEXT`, `renderRunCombo`, `render()`, new group methods, run header link, archive modal)
- Modify: `migration_validator/gui/static/style.css`

**Interfaces:**
- Consumes: Task 5 endpoints, Task 6 helpers.
- Produces: `state.view === "group"`, `state.group`, `cache.groupSummary`, `cache.groupError`, `cache.groupTasks` (`run → {task_id, phase, state, error}`), `state.groupSort = {key, dir}`, methods `openGroup(name)`, `loadGroupSummary()`, `renderGroupView()`, `startGroupCapture(phase)`, `startRowCapture(run, node, phase)`, `syncGroupPolling()`, `pollGroupTasks()`, `openArchiveGroupModal()`, `confirmArchiveGroup()`.

No automated UI test exists for `app.js`; verification is the manual checklist in Step 5. Keep every pure decision in `view.js` (already done in Task 6).

- [ ] **Step 1: State, guide text, combobox**

In the `App` constructor add to `state`: `group: null, groupSort: { key: null, dir: "asc" }, archiveGroupModal: null, addDevicesModal: null` and to `cache`: `groupSummary: null, groupError: null, groupTasks: {}`. Add field `this.groupPollTimer = null`.

Add to `GUIDE_TEXT`:

```js
  group: {
    title: "Group",
    body: [
      "Skupina = N single-device runů se stejným group v run.yml, jeden na box. Každý řádek je samostatný run — open ho otevře jako každý jiný single run.",
      "Capture pre/post/rollback on all zařadí capture každého boxu do fronty; naráz jich běží nejvýš capture_pool (settings.yml, výchozí 10). Obsazený box se přeskočí a hlásí chybu v buňce fáze, ostatní pokračují.",
      "Verdict = nejhorší stav vyhodnocení runu (post nebo rollback proti vlastnímu pre). Řádky jsou seřazené od nejhoršího; kliknutím na hlavičku přeřadíš.",
      "Capture ▾ u řádku spustí capture jen pro ten box — třeba když post jednoho boxu selhal. Add devices přidá další boxy do skupiny se stejným profilem.",
      "Archive group archivuje všechny runy skupiny najednou; se spuštěným capture odmítne.",
    ],
  },
```

Replace the body of `renderRunCombo` after `clear(this.comboListEl);` so it walks `MigView.comboEntries(this.cache.runs, combo.query)`:

```js
    const entries = MigView.comboEntries(this.cache.runs, combo.query);
    if (entries.length === 0) { /* unchanged empty-state branch */ }
    entries.forEach((entry, i) => {
      const focused = i === combo.index;
      if (entry.type === "group") {
        const active = this.state.view === "group" && this.state.group === entry.name;
        this.comboListEl.appendChild(
          el("div", {
            className: "run-combo-row run-combo-group" + (active ? " active" : "") + (focused ? " focused" : ""),
            attrs: { role: "option", "aria-selected": active ? "true" : "false" },
            onClick: () => { this.closeRunCombo(); this.openGroup(entry.name); },
            children: [
              el("span", { className: "mono run-combo-name", text: entry.name }),
              el("span", { className: "kind-tag", text: `group · ${entry.count}` }),
            ],
          })
        );
        return;
      }
      const run = entry.run;
      const active = run.name === this.state.run && this.state.view !== "group";
      this.comboListEl.appendChild(
        el("div", {
          className: "run-combo-row" + (entry.grouped ? " grouped" : "") + (active ? " active" : "") + (focused ? " focused" : ""),
          /* rest identical to the existing row: onClick selectRun, name, existing meta children */
        })
      );
    });
```

`comboMatches()` becomes `return MigView.comboEntries(this.cache.runs, this.state.combo.query);` and the Enter branch of `onComboKey` picks `pick.type === "group" ? this.openGroup(pick.name) : this.selectRun(pick.run.name)`.

`renderRunCombo`'s closed label: when `this.state.view === "group"` show `this.state.group` with a `group` prefix, i.e. `this.comboCurrentEl.textContent = this.state.view === "group" ? `${this.state.group} (group)` : (this.state.run || …)`.

- [ ] **Step 2: Group view methods**

Add to `App` (after the `selectRun` block):

```js
  // -- group view ----------------------------------------------------------

  async openGroup(name) {
    if (!this.leaveGuard()) return;
    this.state.view = "group";
    this.state.group = name;
    this.state.selectedSnapshot = null;
    this.state.groupSort = { key: null, dir: "asc" };
    await this.loadGroupSummary();
    this.render();
  }

  async loadGroupSummary() {
    this.cache.groupSummary = null;
    this.cache.groupError = null;
    try {
      const res = await fetch(`/api/groups/${encodeURIComponent(this.state.group)}`);
      if (res.ok) {
        this.cache.groupSummary = await res.json();
      } else {
        const body = await res.json().catch(() => ({}));
        this.cache.groupError = body.detail || `skupinu se nepodarilo nacist (${res.status})`;
      }
    } catch (err) {
      this.cache.groupError = String(err);
    }
  }

  /* Record batch/task ids to poll. Rows with a server-side error (busy
     device) get a synthetic failed state so the cell shows the message. */
  trackBatch(batch, phase) {
    for (const entry of (batch && batch.tasks) || []) {
      this.cache.groupTasks[entry.run] = entry.task_id
        ? { task_id: entry.task_id, phase, state: "queued", error: null }
        : { task_id: null, phase, state: "failed", error: entry.error || "capture se nespustil" };
    }
  }

  async startGroupCapture(phase) {
    const summary = this.cache.groupSummary;
    if (!summary || this.groupBatchActive()) return;
    try {
      const res = await fetch(`/api/groups/${encodeURIComponent(summary.name)}/captures`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phase }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.status !== 202) {
        this.cache.groupError = body.detail || `capture se nepodarilo spustit (${res.status})`;
      } else {
        this.trackBatch(body, phase);
      }
    } catch (err) {
      this.cache.groupError = String(err);
    }
    this.render();
  }

  async startRowCapture(run, node, phase) {
    try {
      const res = await fetch("/api/captures", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run, device: node, port: null, phase, parse_services: false }),
      });
      const body = await res.json().catch(() => ({}));
      this.cache.groupTasks[run] = res.status === 202
        ? { task_id: body.id, phase, state: "queued", error: null }
        : { task_id: null, phase, state: "failed", error: body.detail || `capture se nespustil (${res.status})` };
    } catch (err) {
      this.cache.groupTasks[run] = { task_id: null, phase, state: "failed", error: String(err) };
    }
    this.render();
  }

  groupBatchActive() {
    return Object.values(this.cache.groupTasks).some((t) => t.task_id && (t.state === "queued" || t.state === "running"));
  }

  syncGroupPolling() {
    const shouldPoll = this.state.view === "group" && this.groupBatchActive();
    if (shouldPoll && !this.groupPollTimer) {
      this.groupPollTimer = setInterval(() => this.pollGroupTasks(), 2000);
      this.pollGroupTasks();
    } else if (!shouldPoll && this.groupPollTimer) {
      clearInterval(this.groupPollTimer);
      this.groupPollTimer = null;
    }
  }

  async pollGroupTasks() {
    const pending = Object.entries(this.cache.groupTasks).filter(
      ([, t]) => t.task_id && (t.state === "queued" || t.state === "running")
    );
    if (!pending.length) { this.syncGroupPolling(); return; }
    let changed = false;
    for (const [run, tracked] of pending) {
      try {
        const res = await fetch(`/api/captures/${tracked.task_id}`);
        if (!res.ok) { tracked.state = "failed"; tracked.error = `task ${res.status}`; changed = true; continue; }
        const task = await res.json();
        if (task.state !== tracked.state) {
          tracked.state = task.state;
          tracked.error = task.error || null;
          changed = true;
        }
      } catch (err) {
        // network hiccup - retry on the next tick
      }
    }
    if (changed && !this.groupBatchActive()) {
      await this.loadGroupSummary();
    }
    if (changed) this.render();
  }
```

Hook into `render()`: add `case "group": this.renderGroupView(); break;` and call `this.syncGroupPolling();` next to `this.syncCapturePolling();`. Also make `document.getElementById("btn-new-capture").disabled = noRuns || this.state.view === "group";` (the single capture form has no group context).

Leaving the group view: `selectRun`, `openNewRunForm`, `goToProfiles` already set `state.view`, so `syncGroupPolling` stops the timer on the next render. Returning to the group calls `openGroup` which refetches; `cache.groupTasks` is kept so an in-flight batch keeps polling.

- [ ] **Step 3: Render the group view**

```js
  renderGroupView() {
    clear(this.mainEl);
    const summary = this.cache.groupSummary;
    if (this.cache.groupError && !summary) {
      this.mainEl.appendChild(el("div", { className: "notice notice-warn", text: this.cache.groupError }));
      return;
    }
    if (!summary) return;
    const tasks = this.cache.groupTasks;
    const batchActive = this.groupBatchActive();

    const header = el("div", {
      className: "run-header",
      children: [
        el("h1", { children: [document.createTextNode("Group "), el("span", { className: "mono", text: summary.name })] }),
        el("span", { className: "kind-tag", text: `group · ${summary.runs.length} runs` }),
        el("button", {
          className: "profile-link mono",
          text: summary.profile ? `profile: ${summary.profile}` : "profile: (default)",
          attrs: { type: "button" },
          onClick: () => this.goToProfiles(summary.profile || null),
        }),
        el("span", { className: "subtitle", text: summary.created ? `created ${summary.created}` : "" }),
        el("button", {
          className: "btn btn-danger-secondary run-header-archive",
          text: "Archive group",
          onClick: () => this.openArchiveGroupModal(),
        }),
      ],
    });
    this.mainEl.appendChild(header);

    const captureBtn = (label, phase, primary) => el("button", {
      className: "btn " + (primary ? "btn-primary" : "btn-secondary"),
      text: label,
      attrs: batchActive ? { disabled: "disabled" } : {},
      onClick: batchActive ? null : () => this.startGroupCapture(phase),
    });
    const tracked = Object.values(tasks).filter((t) => t.task_id);
    const done = tracked.filter((t) => t.state === "done" || t.state === "failed").length;
    const phaseRunning = (tracked.find((t) => t.state === "queued" || t.state === "running") || {}).phase;
    const bar = el("div", {
      className: "group-actions",
      children: [
        captureBtn("Capture pre on all", "pre", true),
        captureBtn("Capture post on all", "post", false),
        captureBtn("Capture rollback on all", "rollback", false),
        el("button", { className: "btn btn-secondary", text: "+ Add devices", onClick: () => this.openAddDevicesModal() }),
        batchActive ? el("span", { className: "group-progress-text", text: `${phaseRunning} capture running · ${done}/${tracked.length} done` }) : null,
        this.buildVerdictStrip(summary.verdicts),
      ],
    });
    this.mainEl.appendChild(bar);
    if (batchActive) {
      this.mainEl.appendChild(el("div", { className: "group-progress", children: [
        el("div", { style: { width: `${tracked.length ? Math.round((100 * done) / tracked.length) : 0}%` } }),
      ] }));
    }
    if (this.cache.groupError) {
      this.mainEl.appendChild(el("div", { className: "notice notice-warn", text: this.cache.groupError }));
    }
    this.mainEl.appendChild(this.buildGroupTable(summary, tasks));
  }

  buildVerdictStrip(verdicts) {
    const order = ["PASS", "RECV", "WARN", "FAIL", "SKIP", "INFO"];
    const children = [];
    for (const key of order) {
      if (verdicts[key]) children.push(el("span", { className: "pill pill-" + key.toLowerCase(), text: `${key} ${verdicts[key]}` }));
    }
    if (verdicts.none) children.push(el("span", { className: "pill pill-none", text: `no post ${verdicts.none}` }));
    if (verdicts.error) children.push(el("span", { className: "pill pill-fail", text: `error ${verdicts.error}` }));
    return el("div", { className: "verdict-strip", children });
  }

  buildGroupTable(summary, tasks) {
    const sort = this.state.groupSort;
    const columns = [
      ["node", "Device"], ["host", "Host"], ["platform", "Platform"],
      ["pre", "Pre"], ["post", "Post"], ["rollback", "Rollback"],
      ["verdict", "Verdict"], [null, "Services"], [null, "Checks"], [null, ""],
    ];
    const head = el("div", { className: "group-row group-head" });
    for (const [key, label] of columns) {
      const active = key && sort.key === key;
      head.appendChild(el("span", {
        className: "group-th" + (key ? " sortable" : "") + (active ? " active" : ""),
        text: label + (active ? (sort.dir === "asc" ? " ▲" : " ▼") : ""),
        onClick: key ? () => {
          this.state.groupSort = active ? { key, dir: sort.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" };
          this.render();
        } : null,
      }));
    }
    const table = el("div", { className: "group-table", children: [head] });
    const rows = MigView.sortGroupRows(summary.runs, sort.key, sort.dir);
    for (const row of rows) table.appendChild(this.buildGroupRow(row, tasks[row.run] || null));
    return table;
  }

  buildGroupRow(row, taskState) {
    const tone = row.error ? "error" : (row.verdict || "none").toLowerCase();
    const counts = (c) => el("span", { className: "count-pills", children:
      ["pass", "recv", "warn", "fail"].filter((k) => c && c[k]).map((k) => el("span", { className: "pill pill-" + k, text: String(c[k]) })),
    });
    const phase = (name) => {
      const cell = MigView.phaseCell(row, name, taskState);
      return el("span", { className: "phase-cell phase-" + cell.kind, text: cell.text, attrs: cell.title ? { title: cell.title } : {} });
    };
    const menu = el("select", { className: "form-select row-capture", children: [
      el("option", { text: "capture ▾", attrs: { value: "" } }),
      ...["pre", "post", "rollback"].map((p) => el("option", { text: p, attrs: { value: p } })),
    ] });
    menu.addEventListener("change", (e) => {
      const p = e.target.value;
      e.target.value = "";
      if (p && row.node) this.startRowCapture(row.run, row.node, p);
    });
    return el("div", {
      className: "group-row tone-" + tone,
      children: [
        el("span", { className: "mono", text: row.node || row.run }),
        el("span", { className: "mono", text: row.host || "" }),
        el("span", { text: row.platform || "" }),
        phase("pre"), phase("post"), phase("rollback"),
        row.error
          ? el("span", { className: "verdict verdict-error", text: "error", attrs: { title: row.error } })
          : el("span", { className: "verdict verdict-" + (row.verdict || "none").toLowerCase(), text: row.verdict || "no post" }),
        counts(row.services), counts(row.checks),
        el("span", { className: "row-actions", children: [
          el("a", { className: "crumb-link", text: "open ›", attrs: { href: "#" }, onClick: (e) => { e.preventDefault(); this.selectRun(row.run); } }),
          menu,
        ] }),
      ],
    });
  }
```

- [ ] **Step 4: Archive group modal, run header link**

Add `openArchiveGroupModal()` / `closeArchiveGroupModal()` / `confirmArchiveGroup()` mirroring the run versions (`this.state.archiveGroupModal = { submitting, error }`; POST `/api/groups/${name}/archive`; on success refetch `/api/runs`, set `state.view` to `empty` or `selectRun(runs[0].name)`). Extend `renderModal()` so it renders `archiveGroupModal` when set: title `Archive group`, text `Skupina <name> (<n> runů) se přesune do runs/.archive/ a zmizí ze seznamu. Data zůstanou na disku.`, buttons Cancel / Archive.

In `renderRunOverview` header, after the `single device` tag: if `detail.group`, append

```js
      header.appendChild(el("button", {
        className: "profile-link mono", text: `group: ${detail.group}`,
        attrs: { type: "button", title: "otevřít skupinu" },
        onClick: () => this.openGroup(detail.group),
      }));
```

and in the breadcrumb of the run view (if one exists at the top of `renderRunOverview`; otherwise skip) prefix `group ›`.

- [ ] **Step 5: CSS**

Append to `style.css`:

```css
/* -- group view ---------------------------------------------------------- */
.group-actions { display: flex; align-items: center; gap: 8px; margin: 12px 0 6px; flex-wrap: wrap; }
.group-progress-text { font-size: 12px; color: #6b7280; margin-left: 4px; }
.group-progress { height: 4px; background: #e5e7eb; border-radius: 2px; margin-bottom: 10px; }
.group-progress > div { height: 4px; background: #1a56db; border-radius: 2px; transition: width 0.3s; }
.verdict-strip { display: flex; gap: 6px; margin-left: auto; }
.pill { padding: 1px 7px; border-radius: 10px; font: 600 11px 'IBM Plex Mono', monospace; }
.pill-pass { background: #dcfce7; color: #166534; }
.pill-recv { background: #e0f2fe; color: #075985; }
.pill-warn { background: #fef3c7; color: #92400e; }
.pill-fail { background: #fee2e2; color: #991b1b; }
.pill-skip, .pill-info, .pill-none { background: #f3f4f6; color: #6b7280; font-weight: 400; }
.count-pills { display: flex; gap: 4px; }
.group-table { background: #fff; border: 1px solid #e2e5ea; border-radius: 8px; overflow: hidden; }
.group-row {
  display: grid;
  grid-template-columns: 1.4fr 1.2fr 90px 78px 78px 78px 90px 1fr 1fr 150px;
  gap: 0 10px; padding: 8px 14px; align-items: center;
  border-bottom: 1px solid #f1f3f6; font-size: 13px;
}
.group-head { background: #f9fafb; border-bottom: 1px solid #e2e5ea; font: 600 11px 'IBM Plex Sans', sans-serif; letter-spacing: 0.06em; color: #6b7280; text-transform: uppercase; }
.group-th.sortable { cursor: pointer; }
.group-th.active { color: #1a56db; }
.group-row.tone-fail { box-shadow: inset 3px 0 0 #dc2626; }
.group-row.tone-warn { box-shadow: inset 3px 0 0 #d97706; }
.group-row.tone-recv { box-shadow: inset 3px 0 0 #0e7490; }
.group-row.tone-pass { box-shadow: inset 3px 0 0 #16a34a; }
.group-row.tone-none, .group-row.tone-skip, .group-row.tone-info { box-shadow: inset 3px 0 0 #d1d5db; }
.group-row.tone-error { box-shadow: inset 3px 0 0 #9ca3af; background: #fafafa; }
.phase-cell { display: inline-block; min-width: 54px; text-align: center; padding: 2px 6px; border-radius: 3px; font: 11px 'IBM Plex Mono', monospace; }
.phase-time { background: #dcfce7; color: #166534; }
.phase-none { background: #f3f4f6; color: #9ca3af; }
.phase-queued { background: #e0e7ff; color: #4338ca; }
.phase-running { background: #fef3c7; color: #92400e; }
.phase-error { background: #fee2e2; color: #991b1b; cursor: help; }
.verdict { font: 700 11px 'IBM Plex Mono', monospace; padding: 2px 8px; border-radius: 3px; }
.verdict-pass { background: #dcfce7; color: #166534; }
.verdict-recv { background: #e0f2fe; color: #075985; }
.verdict-warn { background: #fef3c7; color: #92400e; }
.verdict-fail { background: #fee2e2; color: #991b1b; }
.verdict-none, .verdict-skip, .verdict-info { background: #f3f4f6; color: #9ca3af; font-weight: 400; }
.verdict-error { background: #f3f4f6; color: #6b7280; cursor: help; }
.row-actions { display: flex; gap: 8px; align-items: center; justify-self: end; }
.row-capture { font-size: 11px; padding: 2px 4px; width: auto; }
.run-combo-row.grouped { padding-left: 28px; }
.run-combo-group .kind-tag { margin-left: 8px; }
```

- [ ] **Step 6: Manual check**

Run `pyats-venv/bin/mig-validate gui --run-root <tmp>` against a temp run root, create a group with `curl`:

```bash
curl -s -X POST localhost:8000/api/groups -H 'Content-Type: application/json' \
  -d '{"group":"g1","devices":[{"node":"A","host":"127.0.0.1","platform":"junos"},{"node":"B","host":"127.0.0.2","platform":"junos"}]}'
```

Then in the browser: the combobox lists `g1` with `group · 2` and two indented members; clicking `g1` shows the header, action bar, `no post 2` pill, two rows sorted by node; `Capture pre on all` turns cells to `queued`/`running…` then `error` (no box at 127.0.0.x, message on hover) and the progress line counts to `2/2`; `open ›` opens the member run whose header shows `group: g1` linking back; `Archive group` moves both runs away and the combobox shows no group.

- [ ] **Step 7: Run all tests and commit**

Run: `pyats-venv/bin/pytest -q && node --test tests/js/`

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): group view - tabulka clenu, davkovy capture s pollingem, capture u radku, archivace skupiny, skupiny v comboboxu"
```

---

### Task 8: Bulk form (row editor) and Add devices dialog

**Files:**
- Modify: `migration_validator/gui/static/app.js` (`RUN_TYPES`, `GUIDE_TEXT.newrun`, `openNewRunForm`, `setNewRunKind`, `submitNewRun`, `renderNewRunForm`, new `buildBulkDeviceRows`, `openAddDevicesModal`, `renderModal`)
- Modify: `migration_validator/gui/static/style.css`

**Interfaces:**
- Consumes: `POST /api/groups`, `POST /api/groups/{group}/devices` (Task 5), `trackBatch` and `openGroup` (Task 7).
- Produces: `newRunForm.bulk = { group, devices: [{node, host, platform}], capturePre: true, rowErrors: {index: message} }`, `state.addDevicesModal = { devices, submitting, error, rowErrors }`.

- [ ] **Step 1: Enable the card, extend the form state**

`RUN_TYPES`: drop `disabled: true` and `note` from the bulk entry; keep the comment accurate (`/* New run type cards. */`). In `GUIDE_TEXT.newrun` replace `Bulk se připravuje.` with `Bulk = víc single-device runů z jednoho seznamu boxů (skupina), třeba celý POP před upgradem.` and append a paragraph: `U Bulk zadej jméno skupiny a tabulku boxů (node, host, platforma); každý box dostane run <skupina>-<node>. Chyba na řádku zablokuje Create, nic se nezaloží napůl. Zaškrtnuté "start pre capture on all" spustí pre capture hned po založení.`

In `openNewRunForm` add to the form object:

```js
      bulk: { group: "", devices: [{ node: "", host: "", platform: "junos" }], capturePre: true, rowErrors: {} },
```

- [ ] **Step 2: Row editor builder**

```js
  /* Bulk device table: one buildDeviceSubform-compatible object per row,
     rendered inline. rowErrors = {index: message} from the server. */
  buildBulkDeviceRows(devices, touched, rowErrors, onChange) {
    const table = el("div", { className: "bulk-table" });
    table.appendChild(el("div", { className: "bulk-row bulk-head", children: [
      el("span", { text: "Node" }), el("span", { text: "Host" }), el("span", { text: "Platform" }), el("span", { text: "" }),
    ] }));
    const seen = new Map();
    devices.forEach((device, i) => {
      const key = device.node.trim().toLowerCase();
      const dup = key && seen.has(key);
      if (key && !dup) seen.set(key, i);
      const field = (prop, mono) => {
        const input = el("input", { className: "form-input" + (mono ? " mono" : ""), attrs: { type: "text" } });
        input.value = device[prop];
        input.addEventListener("input", (e) => { device[prop] = e.target.value; onChange(); });
        input.addEventListener("blur", () => this.render());
        return input;
      };
      const platform = el("select", { className: "form-select", children: ["junos", "junos-evo"].map((p) =>
        el("option", { text: p, attrs: p === device.platform ? { value: p, selected: "selected" } : { value: p } })
      ) });
      platform.addEventListener("change", (e) => { device.platform = e.target.value; onChange(); });
      const errors = [];
      if (touched && !device.node.trim()) errors.push("node is required");
      if (touched && !device.host.trim()) errors.push("host is required");
      if (dup) errors.push(`duplicate node (row ${seen.get(key) + 1})`);
      if (rowErrors[i]) errors.push(rowErrors[i]);
      table.appendChild(el("div", { className: "bulk-row" + (errors.length ? " has-error" : ""), children: [
        field("node", true), field("host", false), platform,
        el("button", { className: "btn btn-secondary bulk-remove", text: "×", attrs: { type: "button", title: "remove row" },
          onClick: () => { devices.splice(i, 1); onChange(); this.render(); } }),
        errors.length ? el("div", { className: "field-error bulk-row-error", text: errors.join(" · ") }) : null,
      ] }));
    });
    table.appendChild(el("button", { className: "btn btn-secondary", text: "+ add device", attrs: { type: "button" },
      onClick: () => { devices.push({ node: "", host: "", platform: "junos" }); this.render(); } }));
    return table;
  }

  bulkRowsValid(devices) {
    const seen = new Set();
    for (const d of devices) {
      const key = d.node.trim().toLowerCase();
      if (!key || !d.host.trim() || seen.has(key)) return false;
      seen.add(key);
    }
    return devices.length > 0;
  }
```

- [ ] **Step 3: Render and submit the bulk branch**

In `renderNewRunForm`, after the type cards card: `if (form.kind === "bulk") { this.renderBulkForm(form); return; }` with

```js
  renderBulkForm(form) {
    const bulk = form.bulk;
    const groupInput = el("input", { className: "form-input mono", attrs: { type: "text", placeholder: "e.g. pop1-upgrade-2026-09" } });
    groupInput.value = bulk.group;
    groupInput.addEventListener("input", (e) => { bulk.group = e.target.value; });
    groupInput.addEventListener("blur", () => { form.touched = true; this.render(); });
    const groupErr = form.touched ? this.bulkGroupError() : null;
    const groupField = [el("label", { className: "field-label", text: "Group name" }), groupInput];
    if (groupErr) groupField.push(el("div", { className: "field-error", text: groupErr }));
    if (form.submitError) groupField.push(el("div", { className: "field-error", text: form.submitError }));
    this.mainEl.appendChild(el("div", { className: "form-card", children: [
      el("div", { className: "form-section-label", text: "Group" }),
      el("div", { className: "name-profile-grid", children: [
        el("div", { className: "form-field", children: groupField }),
        this.buildProfilePicker(form),
      ] }),
    ] }));
    this.mainEl.appendChild(el("div", { className: "form-card", children: [
      el("div", { className: "form-section-label", text: "Devices" }),
      this.buildBulkDeviceRows(bulk.devices, form.touched, bulk.rowErrors, () => { bulk.rowErrors = {}; }),
    ] }));
    const valid = !this.bulkGroupError() && this.bulkRowsValid(bulk.devices) && !Object.keys(bulk.rowErrors).length;
    const checkbox = el("input", { attrs: { type: "checkbox" } });
    checkbox.checked = bulk.capturePre;
    checkbox.addEventListener("change", (e) => { bulk.capturePre = e.target.checked; });
    this.mainEl.appendChild(el("div", { className: "footer-actions", children: [
      el("button", { className: "btn btn-secondary", text: "Cancel", onClick: () => this.cancelNewRunForm() }),
      el("button", {
        className: "btn btn-primary", text: form.submitting ? "Creating…" : `Create ${bulk.devices.length} run${bulk.devices.length === 1 ? "" : "s"}`,
        attrs: valid && !form.submitting ? {} : { disabled: "disabled" },
        onClick: valid && !form.submitting ? () => this.submitBulk() : null,
      }),
      el("label", { className: "bulk-capture-pre", children: [checkbox, document.createTextNode(" start pre capture on all after creating")] }),
    ] }));
  }

  bulkGroupError() {
    const name = (this.state.newRunForm.bulk.group || "").trim();
    if (!name) return "group name is required";
    if (!/^[a-z0-9_-]+$/.test(name)) return "only a-z 0-9 _ - allowed";
    return null;
  }

  async submitBulk() {
    const form = this.state.newRunForm;
    const bulk = form.bulk;
    form.touched = true;
    form.submitting = true;
    form.submitError = null;
    this.render();
    try {
      const res = await fetch("/api/groups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          group: bulk.group.trim(),
          profile: form.profile || null,
          devices: bulk.devices.map((d) => ({ node: d.node.trim(), host: d.host.trim(), platform: d.platform })),
          capture_pre: bulk.capturePre,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.status === 201) {
        this.cache.runs = (await (await fetch("/api/runs")).json()).runs;
        this.state.newRunForm = null;
        this.cache.groupTasks = {};
        if (body.batch) this.trackBatch(body.batch, "pre");
        await this.openGroup(body.name);
        return;
      }
      this.applyGroupErrors(body, res.status, (msg) => { form.submitError = msg; }, bulk);
    } catch (err) {
      form.submitError = String(err);
    }
    form.submitting = false;
    this.render();
  }

  /* 409 detail is {message, rows:[{index, message}]}; index null goes to
     the form-level error, others to their row. Any other status is a plain
     detail string. */
  applyGroupErrors(body, status, setFormError, target) {
    const detail = body.detail;
    if (detail && typeof detail === "object" && Array.isArray(detail.rows)) {
      target.rowErrors = {};
      const general = [];
      for (const row of detail.rows) {
        if (row.index === null || row.index === undefined) general.push(row.message);
        else target.rowErrors[row.index] = row.message;
      }
      setFormError(general.length ? general.join("; ") : null);
      return;
    }
    setFormError((typeof detail === "string" && detail) || (detail && detail.message) || `skupinu se nepodarilo zalozit (${status})`);
  }
```

`setNewRunKind` needs no change (the bulk type is no longer disabled).

- [ ] **Step 4: Add devices dialog**

```js
  openAddDevicesModal() {
    this.state.addDevicesModal = {
      devices: [{ node: "", host: "", platform: "junos" }], submitting: false, error: null, rowErrors: {}, touched: false,
    };
    this.render();
  }

  async confirmAddDevices() {
    const modal = this.state.addDevicesModal;
    const summary = this.cache.groupSummary;
    if (!modal || !summary || modal.submitting) return;
    modal.touched = true;
    if (!this.bulkRowsValid(modal.devices)) { this.render(); return; }
    modal.submitting = true;
    modal.error = null;
    this.render();
    try {
      const res = await fetch(`/api/groups/${encodeURIComponent(summary.name)}/devices`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ devices: modal.devices.map((d) => ({ node: d.node.trim(), host: d.host.trim(), platform: d.platform })) }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.status === 201) {
        this.cache.runs = (await (await fetch("/api/runs")).json()).runs;
        this.state.addDevicesModal = null;
        this.cache.groupSummary = body;
        this.render();
        return;
      }
      this.applyGroupErrors(body, res.status, (msg) => { modal.error = msg; }, modal);
    } catch (err) {
      modal.error = String(err);
    }
    modal.submitting = false;
    this.render();
  }
```

In `renderModal()`, render `state.addDevicesModal` when set: `h3 Add devices`, the `buildBulkDeviceRows(modal.devices, modal.touched, modal.rowErrors, () => { modal.rowErrors = {}; })` table, `modal.error` as `.field-error`, footer Cancel (`this.state.addDevicesModal = null; this.render()`) and `Add` (`confirmAddDevices`). Give the modal `className: "modal modal-wide"`.

- [ ] **Step 5: CSS**

```css
/* -- bulk row editor ------------------------------------------------------ */
.bulk-table { display: flex; flex-direction: column; gap: 6px; }
.bulk-row { display: grid; grid-template-columns: 1.2fr 1.2fr 130px 36px; gap: 8px; align-items: center; }
.bulk-head { font: 600 11px 'IBM Plex Sans', sans-serif; letter-spacing: 0.06em; color: #6b7280; text-transform: uppercase; }
.bulk-row.has-error .form-input { border-color: #dc2626; }
.bulk-row-error { grid-column: 1 / -1; }
.bulk-remove { padding: 2px 8px; }
.bulk-capture-pre { margin-left: auto; font-size: 12.5px; color: #374151; display: flex; align-items: center; gap: 6px; }
.modal-wide { width: min(760px, 92vw); }
```

- [ ] **Step 6: Manual check**

With the GUI running: `+ New run` → the Bulk card is selectable; the row editor starts with one row; `+ add device` adds rows; a duplicate node shows `duplicate node (row 1)` on the later row and `Create 2 runs` is disabled; a valid two-row form with the checkbox on creates the group, lands on the group view and cells go `queued` → `running…` → `error` (unreachable hosts). Submit a group name that already exists → form-level error under Group name. `+ Add devices` on the group view adds a third row to the table after `Add`; re-adding the same node shows `run 'g1-a' uz existuje` on the dialog row.

- [ ] **Step 7: Run all tests and commit**

Run: `pyats-venv/bin/pytest -q && node --test tests/js/`

```bash
git add migration_validator/gui/static/app.js migration_validator/gui/static/style.css
git commit -m "feat(gui): New run Bulk - radkovy editor boxu, zalozeni skupiny s pre capture, dialog Add devices"
```

---

### Task 9: Docs and lab pass

**Files:**
- Modify: `docs/cs/reference.md` (section 8, the `group` row of the `run.yml` table and a new `### Skupiny (bulk, GUI)` subsection after `### Profily`)
- Modify: `docs/en/reference.md` (same places, English)
- Modify: `README.md` only if it lists GUI features (grep `Profiles` in it; add one bullet next to it if so)

- [ ] **Step 1: Update the `group` row in the run.yml table (cs)**

Replace the `group` row with:

```
| `group` | jméno skupiny | bulk: N `single` runů `<skupina>-<node>` se stejným `group`; zapisuje GUI (`POST /api/groups`), CLI ho jen zachová |
```

- [ ] **Step 2: Add the subsection (cs)**

After the `### Profily (...)` block:

```markdown
### Skupiny (bulk, GUI)

Skupina je N `single` runů založených z jednoho seznamu boxů: run `<skupina>-<node malými>`,
`kind: single`, jedno zařízení role `single`, společný `profile` a `group`. Skupina existuje,
dokud má aspoň jednoho nearchivovaného člena — žádný soubor navíc. Jméno skupiny i boxů
podléhá `^[a-z0-9_-]+$` (node se před použitím v názvu runu převede na malá písmena).

API (`/api/groups`): `GET` seznam (`name`, `runs`, `profile`, `created`), `POST`
(`{"group","profile","devices":[{node,host,platform}],"capture_pre"}`) založí všechny runy
najednou — validace proběhne celá dopředu a `409` nese `detail.rows` s `index` řádku
(`null` = chyba skupiny: jméno, profil, prázdný seznam, existující skupina), nic se nezaloží
napůl. `POST /{group}/devices` přidá boxy se stejným profilem. `POST /{group}/captures`
(`{"phase"}`) zařadí capture každého člena do `CaptureManager`; obsazený box je řádková
chyba v odpovědi (`task_id: null, error`), ne selhání dávky. `GET /{group}` vrací řádek na
člena: fáze (`taken` celoboxové capture), `active_task`, `verdict` (nejhorší `Status` přes
evaluace runu, `null` bez post/rollback), počty služeb a checků, `error` (rozbitý run.yml,
chybějící snímek, chybějící profil — ostatní řádky se vykreslí). Verdikty se cachují podle
mtime `run.yml` a souboru profilu. `POST /{group}/archive` (admin) archivuje všechny členy,
`409 skupina '<g>' ma bezici capture` dokud některý má queued/running task.

`connection.capture_pool` v `config/settings.yml` (výchozí `10`, min `1`) omezuje počet
současně běžících captures na celém serveru; ostatní čekají ve stavu `queued`.
```

- [ ] **Step 3: English mirror**

Apply the same two edits to `docs/en/reference.md` (`## 8. Run management` at line ~741, `### Profiles` at ~783) in English.

- [ ] **Step 4: Commit**

```bash
git add docs/cs/reference.md docs/en/reference.md README.md
git commit -m "docs: skupiny (bulk single runy), /api/groups, capture_pool"
```

- [ ] **Step 5: Lab pass (user-driven, after merge candidate is ready)**

Load the lab password per the memory note (`eval` of the `~/.bashrc` export), set `capture_pool: 2` in `config/settings.yml`, start the GUI against the lab run root and:

1. Create a 3-box group from the Bulk form with pre capture on. Two cells go `running…` while the third sits `queued`; all three end with a time.
2. Open one member with `open ›`, run a post capture from its own capture form, go back via `group: <name>`; the row shows the post time and a verdict.
3. `Capture post on all`; while it runs `Archive group` returns the 409 text in the modal.
4. From the row menu re-capture post on one box; the summary refreshes with a new time.
5. `Add devices` with one more box; `Archive group` once idle; the combobox no longer lists the group.

Record anything off as follow-up fixes on the branch before finishing with `superpowers:finishing-a-development-branch`.

---

## Self-review notes

- Spec §1 model/naming → Task 3; `capture_pool` → Tasks 1–2. §2 queued/pool → Task 2. §3 every endpoint → Task 5 (`GET /api/groups`, `POST`, `/devices`, `/captures`, `GET /{group}`, `/archive`), summary shape and cache → Task 4, `group` in run payloads → Task 5. §4 bulk form → Task 8. §5 group view, polling, row actions, add devices, navigation, guide → Tasks 7–8. Error handling → Tasks 3–5. Testing list → Tasks 1–6 plus manual checks; docs are the extra Task 9.
- Deviation noted: the spec says "settings.yml key `capture_pool`"; it lives under `connection:` to match every other key, documented in Task 9.
- `SKIP`/`INFO` verdicts can occur (empty scope list); the JS treats them like `none` visually and keeps the label.
