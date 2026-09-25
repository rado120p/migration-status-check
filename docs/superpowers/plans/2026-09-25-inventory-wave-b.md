# Host inventory — wave B (inventory, hostname filter, node picker) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New runs and bulk groups pick nodes from an Ansible-style inventory file (search fills node + host); an admin-controlled glob filter narrows what the search offers; manual entry stays available.

**Architecture:** Core modules `migration_validator/inventory.py` (parser + mtime-cached source) and `migration_validator/hostname_filter.py` (glob matching + YAML store) have no FastAPI import. `gui/inventory_routes.py` exposes search and filter endpoints on top of them; `create_app` receives an optional `InventorySource` and a `FilterStore`; `mig-validate gui` builds both from `settings.yml`. The frontend gets a pure-logic `static/node_picker.js` (node-tested) plus a picker component in `app.js` used by the new-run device sub-forms and the bulk table, and an admin-only Settings screen for the filter.

**Tech Stack:** Python 3.13, FastAPI 0.141, PyYAML, stdlib `fnmatch`, vanilla JS, pytest, `node --test tests/js/*.test.js`.

**Running tests:** `pyproject.toml` already sets `addopts = "-q"`; never add another `-q`. Use `/home/rado/Desktop/scripts/migration-status-check/.venv/bin/python -m pytest -p no:warnings [paths]` from the repository/worktree root. JS: `node --test tests/js/*.test.js` (Node 22 rejects a bare directory).

**Spec:** `docs/superpowers/specs/2026-09-25-user-roles-and-inventory-design.md` (sections "Closed decisions", "Permission matrix", "Wave B"). Wave A (accounts, login, `require()` 401/403, audit `record()`, `data-perm` hiding, `GET /api/me`) is already merged into the base of this branch.

## Global Constraints

- Inventory path: `inventory.path` in `config/settings.yml`; missing key → inventory disabled (forms manual-only); configured but unreadable → error state in the API, forms fall back to manual; the GUI still starts.
- Inventory line format: `NAME ansible_host=IP [other vars]`; skip blank, `#`/`;` comments, `[group]` headers and the bodies of `[x:vars]` / `[x:children]` sections; `ansible_host` quotes stripped; missing `ansible_host` → skipped + warning `line N: NAME has no ansible_host`; duplicate name same host → one entry; different host → first wins + warning `line N: NAME duplicates line M with a different host`. No range expansion, no YAML inventories.
- Filter file `config/hostname_filter.yml` (gitignored in wave A), `allow:` list; node visible when `fnmatch.fnmatchcase(node.lower(), pattern.lower())` for any pattern; empty/missing → everything visible; written atomically.
- Filter narrows the inventory search only — never existing runs, groups or manual entry.
- `GET /api/inventory?q=` (VIEW): case-insensitive substring on node name within the filtered set, sorted by name, ≤ 50 items, returns `{items, total, enabled, error}`; empty `q` → first 50 visible.
- `GET /api/inventory/filter` (VIEW) → `{allow, visible, total, warnings, enabled, error}`; `PUT /api/inventory/filter` (ADMIN) body `{allow: [...]}`: entries stripped, duplicates dropped, empty entry → 422; `?dry_run=true` validates and returns counts without saving or auditing.
- Platform is always picked by the user; the server never checks node/host against the inventory.
- GUI-visible text English; Python comments/messages Czech without diacritics.

## Review Focus

1. **Hand-edited inventory with Windows line endings or trailing spaces** (`MX-POP1 ansible_host=1.2.3.4\r`) must parse to host `1.2.3.4`, not `1.2.3.4\r` — pinned in Task 1.
2. **Inventory file replaced while the GUI runs** (atomic `mv` by Ansible tooling) must be picked up without restart — mtime/size cache test in Task 1.
3. **Malformed `hostname_filter.yml` edited by hand** (scalar `allow: MX-*`) must surface as an error in the API, not a 500 or a silently empty filter — pinned in Tasks 2 and 3.
4. **Viewer or operator calling `PUT /api/inventory/filter`** must get 403, including with `dry_run=true` — pinned in Task 3 (matrix rows).
5. **Picker re-render losing typed text** — the whole app re-renders `#main` on state changes; the picker must keep its query and dropdown local and only trigger a full render on pick/clear/manual toggle — pinned by keeping picker state in the device object (Task 5) and verified in the manual pass.

---

## File structure

| File | Responsibility |
|---|---|
| `migration_validator/auth.py` (modify) | `InventorySettings`, `load_inventory_settings()` |
| `migration_validator/inventory.py` (create) | `InventoryHost`, `Inventory`, `parse_inventory`, `InventorySource` |
| `migration_validator/hostname_filter.py` (create) | `DEFAULT_FILTER_FILE`, `normalize_patterns`, `visible`, `FilterStore` |
| `migration_validator/gui/inventory_routes.py` (create) | `/api/inventory`, `/api/inventory/filter` |
| `migration_validator/gui/app.py` (modify) | `create_app(inventory=…, hostname_filter=…)` |
| `migration_validator/cli.py` (modify) | `gui` wires inventory + filter store |
| `migration_validator/gui/static/node_picker.js` (create) | pure picker state helpers |
| `migration_validator/gui/static/app.js`, `index.html`, `style.css` (modify) | picker component, Settings screen |
| `tests/…` | per task |
| `config/settings-template.yml`, `docs/en/README.md`, `docs/cs/README.md` | config + docs |

---

### Task 1: Inventory settings and parser

**Files:**
- Modify: `migration_validator/auth.py`, `config/settings-template.yml`
- Create: `migration_validator/inventory.py`
- Test: `tests/test_auth.py` (append), `tests/test_inventory.py`

**Interfaces:**
- Produces: `auth.InventorySettings(path: Path | None = None)` frozen dataclass; `auth.load_inventory_settings(path: Path | None = None) -> InventorySettings`; `inventory.InventoryHost(node: str, host: str)` frozen dataclass; `inventory.Inventory(hosts: list[InventoryHost], warnings: list[str])` frozen dataclass; `inventory.parse_inventory(text: str) -> Inventory`; `inventory.InventorySource(path: Path)` with `.path` and `.load() -> Inventory` (raises `OSError` when unreadable).

- [ ] **Step 1: Failing settings tests** (append to `tests/test_auth.py`)

```python
from migration_validator.auth import InventorySettings, load_inventory_settings


def test_inventory_settings_default_disabled(tmp_path):
    assert load_inventory_settings(tmp_path / "missing.yml") == InventorySettings(path=None)
    path = tmp_path / "settings.yml"
    path.write_text("auth:\n  users_file: u.yml\n")
    assert load_inventory_settings(path).path is None


def test_inventory_settings_path(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("inventory:\n  path: ~/hosts\n")
    assert load_inventory_settings(path).path == Path("~/hosts").expanduser()


def test_inventory_settings_rejects_unknown_key_and_scalar(tmp_path):
    path = tmp_path / "settings.yml"
    path.write_text("inventory:\n  file: hosts\n")
    with pytest.raises(ValueError, match="file"):
        load_inventory_settings(path)
    path.write_text("inventory: /etc/ansible/hosts\n")
    with pytest.raises(ValueError, match="'inventory' musi byt mapping"):
        load_inventory_settings(path)
```

- [ ] **Step 2: Failing parser tests** — `tests/test_inventory.py`

```python
import os

import pytest

from migration_validator.inventory import Inventory, InventoryHost, InventorySource, parse_inventory


def test_basic_lines():
    inv = parse_inventory("MX-POP1 ansible_host=172.20.20.4\nPTX-POP1 ansible_host=172.20.20.5\n")
    assert inv.hosts == [InventoryHost("MX-POP1", "172.20.20.4"), InventoryHost("PTX-POP1", "172.20.20.5")]
    assert inv.warnings == []


def test_comments_groups_vars_and_children_skipped():
    text = (
        "# comment\n; other comment\n\n"
        "[pop1]\nMX-POP1 ansible_host=10.0.0.1 ansible_user=x\n"
        "[pop1:vars]\nansible_host=9.9.9.9\nntp=1.1.1.1\n"
        "[all:children]\npop1\n"
        "[pop2]\nPTX-POP2   ansible_host='10.0.0.2'\n"
    )
    inv = parse_inventory(text)
    assert inv.hosts == [InventoryHost("MX-POP1", "10.0.0.1"), InventoryHost("PTX-POP2", "10.0.0.2")]
    assert inv.warnings == []


def test_quotes_crlf_and_trailing_space():
    inv = parse_inventory('A1 ansible_host="10.0.0.1"\r\nB1 ansible_host=10.0.0.2   \r\n')
    assert [h.host for h in inv.hosts] == ["10.0.0.1", "10.0.0.2"]


def test_missing_ansible_host_warns():
    inv = parse_inventory("MX-POP1 ansible_host=10.0.0.1\nLONELY\nNOIP ansible_user=x\n")
    assert [h.node for h in inv.hosts] == ["MX-POP1"]
    assert inv.warnings == ["line 2: LONELY has no ansible_host", "line 3: NOIP has no ansible_host"]


def test_duplicates():
    inv = parse_inventory(
        "A1 ansible_host=10.0.0.1\nA1 ansible_host=10.0.0.1\nA1 ansible_host=10.0.0.9\n"
    )
    assert inv.hosts == [InventoryHost("A1", "10.0.0.1")]
    assert inv.warnings == ["line 3: A1 duplicates line 1 with a different host"]


def test_hosts_sorted_by_name():
    inv = parse_inventory("b ansible_host=2\nA ansible_host=1\nc ansible_host=3\n")
    assert [h.node for h in inv.hosts] == ["A", "b", "c"]


def test_source_reloads_on_change(tmp_path):
    path = tmp_path / "hosts"
    path.write_text("A1 ansible_host=10.0.0.1\n")
    source = InventorySource(path)
    assert [h.node for h in source.load().hosts] == ["A1"]
    replacement = tmp_path / "hosts.new"
    replacement.write_text("B1 ansible_host=10.0.0.2\n")
    os.replace(replacement, path)  # atomic swap, as Ansible tooling does
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert [h.node for h in source.load().hosts] == ["B1"]


def test_source_missing_file_raises(tmp_path):
    with pytest.raises(OSError):
        InventorySource(tmp_path / "nope").load()
```

Sorting rule: `sorted(hosts, key=lambda h: h.node.lower())`.

- [ ] **Step 3: Run and see failures.** `…pytest -p no:warnings tests/test_auth.py tests/test_inventory.py`

- [ ] **Step 4: Implement**

`auth.py` (next to `load_auth_settings`, reusing `_read_settings`):

```python
_KNOWN_INVENTORY_KEYS = frozenset({"path"})


@dataclass(frozen=True)
class InventorySettings:
    # None = inventar vypnuty, formulare jen s rucnim zadanim.
    path: Path | None = None


def load_inventory_settings(path: Path | None = None) -> InventorySettings:
    if path is None:
        path = DEFAULT_SETTINGS_PATH
    raw = _read_settings(path)
    if raw is None:
        return InventorySettings()
    block = raw.get("inventory") or {}
    if not isinstance(block, dict):
        raise ValueError(f"{path}: 'inventory' musi byt mapping")
    unknown = sorted(set(block) - _KNOWN_INVENTORY_KEYS)
    if unknown:
        raise ValueError(
            f"{path}: neznamy klic inventory.{', inventory.'.join(unknown)} "
            f"(zname: {', '.join(sorted(_KNOWN_INVENTORY_KEYS))})"
        )
    value = block.get("path")
    return InventorySettings(path=Path(str(value)).expanduser() if value else None)
```

`config/settings-template.yml` append:

```yaml
inventory:
  # Ansible INI inventory (NAME ansible_host=IP); without it the GUI offers manual entry only.
  path: /etc/ansible/hosts
```

`migration_validator/inventory.py`:

```python
"""Ansible INI inventar - jen jmeno uzlu a ansible_host.

Skupiny, :vars a :children se preskakuji; rozsahy (mx[01:10]) ani YAML
inventare se nepodporuji. Varovani nesou cislo radku, GUI je ukaze adminovi.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InventoryHost:
    node: str
    host: str


@dataclass(frozen=True)
class Inventory:
    hosts: list[InventoryHost]
    warnings: list[str]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_inventory(text: str) -> Inventory:
    found: dict[str, tuple[str, int]] = {}
    warnings: list[str] = []
    in_host_section = True
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            in_host_section = not (section.endswith(":vars") or section.endswith(":children"))
            continue
        if not in_host_section:
            continue
        tokens = line.split()
        node = tokens[0]
        host = None
        for token in tokens[1:]:
            key, sep, value = token.partition("=")
            if sep and key == "ansible_host":
                host = _strip_quotes(value)
        if not host:
            warnings.append(f"line {number}: {node} has no ansible_host")
            continue
        if node in found:
            first_host, first_line = found[node]
            if first_host != host:
                warnings.append(
                    f"line {number}: {node} duplicates line {first_line} with a different host"
                )
            continue
        found[node] = (host, number)
    hosts = sorted(
        (InventoryHost(node, host) for node, (host, _) in found.items()),
        key=lambda h: h.node.lower(),
    )
    return Inventory(hosts=hosts, warnings=warnings)


class InventorySource:
    """Soubor inventare s cache podle (mtime_ns, size) - zmena souboru
    se projevi bez restartu GUI."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._key: tuple[int, int] | None = None
        self._cached: Inventory | None = None

    def load(self) -> Inventory:
        st = self.path.stat()  # OSError kdyz soubor chybi / neni citelny
        key = (st.st_mtime_ns, st.st_size)
        if self._cached is None or key != self._key:
            text = self.path.read_text(encoding="utf-8", errors="replace")
            self._cached = parse_inventory(text)
            self._key = key
        return self._cached
```

- [ ] **Step 5: Run tests; full suite.** Expected PASS.

- [ ] **Step 6: Commit** — `feat(inventory): Ansible INI inventory parser and inventory.path setting`

---

### Task 2: Hostname filter core

**Files:**
- Create: `migration_validator/hostname_filter.py`
- Test: `tests/test_hostname_filter.py`

**Interfaces:**
- Produces: `DEFAULT_FILTER_FILE = Path("config") / "hostname_filter.yml"`; `normalize_patterns(raw: list) -> list[str]` (raises `ValueError`); `is_visible(node: str, patterns: list[str]) -> bool`; `FilterStore(path: Path)` with `.path`, `.load() -> list[str]` (missing file → `[]`, malformed → `ValueError`), `.save(patterns: list[str]) -> None` (atomic).

- [ ] **Step 1: Failing tests** — `tests/test_hostname_filter.py`

```python
import pytest
import yaml

from migration_validator.hostname_filter import FilterStore, is_visible, normalize_patterns


@pytest.mark.parametrize("node, patterns, expected", [
    ("MX-POP1", ["MX-*"], True),
    ("mx-pop1", ["MX-*"], True),          # case-insensitive
    ("SOMEMX-1", ["MX-*"], False),        # whole name, not contains
    ("PTX-POP2", ["MX-*", "PTX-*"], True),
    ("CORE1", ["*POP*"], False),
    ("ACX-POP9", ["*POP*"], True),
    ("ANY", [], True),                    # empty filter = everything
])
def test_is_visible(node, patterns, expected):
    assert is_visible(node, patterns) is expected


def test_normalize_strips_and_dedups():
    assert normalize_patterns([" MX-* ", "PTX-*", "MX-*"]) == ["MX-*", "PTX-*"]


@pytest.mark.parametrize("raw", [[""], ["  "], ["MX-*", ""], [1], [None]])
def test_normalize_rejects_empty_and_non_string(raw):
    with pytest.raises(ValueError):
        normalize_patterns(raw)


def test_store_round_trip(tmp_path):
    store = FilterStore(tmp_path / "config" / "hostname_filter.yml")
    assert store.load() == []
    store.save(["MX-*", "PTX-*"])
    assert yaml.safe_load(store.path.read_text()) == {"allow": ["MX-*", "PTX-*"]}
    assert store.load() == ["MX-*", "PTX-*"]
    assert sorted(p.name for p in store.path.parent.iterdir()) == ["hostname_filter.yml"]


@pytest.mark.parametrize("content", ["allow: MX-*\n", "- MX-*\n", "allow:\n  - ''\n"])
def test_store_malformed_raises(tmp_path, content):
    path = tmp_path / "hostname_filter.yml"
    path.write_text(content)
    with pytest.raises(ValueError, match="hostname_filter.yml"):
        FilterStore(path).load()


def test_store_empty_file(tmp_path):
    path = tmp_path / "hostname_filter.yml"
    path.write_text("")
    assert FilterStore(path).load() == []
```

- [ ] **Step 2: Run and see failures.**

- [ ] **Step 3: Implement `migration_validator/hostname_filter.py`**

```python
"""Filtr jmen uzlu pro vyhledavani v inventari (spec 2026-09-25, vlna B).

Glob (fnmatch) na cele jmeno, bez ohledu na velikost pismen. Filtr zuzuje
jen nabidku ve vyhledavani - existujici runy ani rucni zadani neomezuje.
"""

from __future__ import annotations

import fnmatch
import os
import tempfile
from pathlib import Path

import yaml

DEFAULT_FILTER_FILE = Path("config") / "hostname_filter.yml"


def normalize_patterns(raw: list) -> list[str]:
    patterns: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"prazdny nebo neplatny vzor: {entry!r}")
        pattern = entry.strip()
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def is_visible(node: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    name = node.lower()
    return any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in patterns)


class FilterStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[str]:
        if not self.path.exists():
            return []
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{self.path}: ocekavan mapping s klicem 'allow'")
        allow = raw.get("allow") or []
        if not isinstance(allow, list):
            raise ValueError(f"{self.path}: 'allow' musi byt seznam vzoru")
        try:
            return normalize_patterns(allow)
        except ValueError as error:
            raise ValueError(f"{self.path}: {error}") from error

    def save(self, patterns: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".hostname_filter-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                yaml.safe_dump({"allow": list(patterns)}, handle, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
```

- [ ] **Step 4: Run tests; full suite.** PASS.

- [ ] **Step 5: Commit** — `feat(inventory): hostname filter globs and filter store`

---

### Task 3: Inventory API routes

**Files:**
- Create: `migration_validator/gui/inventory_routes.py`
- Modify: `migration_validator/gui/app.py` (`create_app` params + `include_router`)
- Test: `tests/gui/test_inventory_routes.py`; add MATRIX rows in `tests/gui/test_permission_matrix.py`

**Interfaces:**
- Consumes: Task 1 `InventorySource`, `Inventory`; Task 2 `FilterStore`, `normalize_patterns`, `is_visible`, `DEFAULT_FILTER_FILE`; wave A `require`, `Permission`, `Actor`, `audit.record`.
- Produces: `build_inventory_router(inventory: InventorySource | None, filters: FilterStore) -> APIRouter`; `create_app(..., inventory: InventorySource | None = None, hostname_filter: FilterStore | None = None)` (None filter → `FilterStore(DEFAULT_FILTER_FILE)`); JSON shapes from Global Constraints; `SEARCH_LIMIT = 50`.

- [ ] **Step 1: Failing tests** — `tests/gui/test_inventory_routes.py`

```python
import logging

import pytest
from fastapi.testclient import TestClient

from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor
from migration_validator.hostname_filter import FilterStore
from migration_validator.inventory import InventorySource

HOSTS = "\n".join(
    [f"MX-POP{i} ansible_host=10.0.1.{i}" for i in range(1, 61)]
    + ["PTX-POP1 ansible_host=10.0.2.1", "CORE1 ansible_host=10.0.3.1", "BROKEN"]
) + "\n"


def _client(tmp_path, role="admin", hosts=HOSTS, inventory=True):
    path = tmp_path / "hosts"
    path.write_text(hosts)
    app = create_app(
        run_root=tmp_path / "runs", profiles_root=tmp_path / "profiles",
        inventory=InventorySource(path) if inventory else None,
        hostname_filter=FilterStore(tmp_path / "hostname_filter.yml"),
    )
    app.state.actor_provider = lambda request: Actor(role=role, username="u")
    return TestClient(app), tmp_path


def test_search_limit_total_and_sort(tmp_path):
    client, _ = _client(tmp_path)
    body = client.get("/api/inventory").json()
    assert body["enabled"] is True and body["error"] is None
    assert body["total"] == 62
    assert len(body["items"]) == 50
    assert body["items"][0] == {"node": "CORE1", "host": "10.0.3.1"}


def test_search_substring_case_insensitive(tmp_path):
    client, _ = _client(tmp_path)
    body = client.get("/api/inventory", params={"q": "ptx"}).json()
    assert body["items"] == [{"node": "PTX-POP1", "host": "10.0.2.1"}]
    assert body["total"] == 1


def test_filter_narrows_search(tmp_path):
    client, root = _client(tmp_path)
    assert client.put("/api/inventory/filter", json={"allow": ["PTX-*", "CORE*"]}).status_code == 200
    body = client.get("/api/inventory").json()
    assert [i["node"] for i in body["items"]] == ["CORE1", "PTX-POP1"]


def test_filter_get_shape_and_warnings(tmp_path):
    client, _ = _client(tmp_path)
    body = client.get("/api/inventory/filter").json()
    assert body == {"allow": [], "visible": 62, "total": 62,
                    "warnings": ["line 63: BROKEN has no ansible_host"],
                    "enabled": True, "error": None}


def test_filter_put_normalizes_and_counts(tmp_path, caplog):
    client, root = _client(tmp_path)
    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        body = client.put("/api/inventory/filter", json={"allow": [" MX-* ", "MX-*"]}).json()
    assert body["allow"] == ["MX-*"] and body["visible"] == 60 and body["total"] == 62
    assert FilterStore(root / "hostname_filter.yml").load() == ["MX-*"]
    assert "action=filter-edit" in caplog.text


def test_filter_put_dry_run_does_not_save_or_audit(tmp_path, caplog):
    client, root = _client(tmp_path)
    with caplog.at_level(logging.INFO, logger="migration_validator.gui.audit"):
        body = client.put("/api/inventory/filter", params={"dry_run": "true"}, json={"allow": ["CORE*"]}).json()
    assert body["visible"] == 1 and body["allow"] == ["CORE*"]
    assert not (root / "hostname_filter.yml").exists()
    assert "filter-edit" not in caplog.text


@pytest.mark.parametrize("allow", [[""], ["MX-*", "  "]])
def test_filter_put_rejects_empty_entry(tmp_path, allow):
    client, _ = _client(tmp_path)
    assert client.put("/api/inventory/filter", json={"allow": allow}).status_code == 422


@pytest.mark.parametrize("role", ["viewer", "operator"])
@pytest.mark.parametrize("dry_run", ["false", "true"])
def test_filter_put_is_admin_only(tmp_path, role, dry_run):
    client, _ = _client(tmp_path, role=role)
    resp = client.put("/api/inventory/filter", params={"dry_run": dry_run}, json={"allow": ["MX-*"]})
    assert resp.status_code == 403


def test_inventory_disabled(tmp_path):
    client, _ = _client(tmp_path, inventory=False)
    assert client.get("/api/inventory").json() == {"items": [], "total": 0, "enabled": False, "error": None}
    body = client.get("/api/inventory/filter").json()
    assert body["enabled"] is False and body["total"] == 0 and body["visible"] == 0


def test_inventory_unreadable_is_error_state(tmp_path):
    client, root = _client(tmp_path)
    (root / "hosts").unlink()
    body = client.get("/api/inventory").json()
    assert body["enabled"] is True and body["items"] == [] and "hosts" in body["error"]


def test_malformed_filter_file_is_error_state(tmp_path):
    client, root = _client(tmp_path)
    (root / "hostname_filter.yml").write_text("allow: MX-*\n")
    search = client.get("/api/inventory").json()
    assert search["items"] == [] and "hostname_filter.yml" in search["error"]
    state = client.get("/api/inventory/filter").json()
    assert "hostname_filter.yml" in state["error"]
    # saving a valid filter repairs it
    assert client.put("/api/inventory/filter", json={"allow": ["MX-*"]}).status_code == 200
    assert client.get("/api/inventory").json()["error"] is None
```

Add to `MATRIX` in `tests/gui/test_permission_matrix.py`:

```python
    ("GET", "/api/inventory", "viewer"),
    ("GET", "/api/inventory/filter", "viewer"),
    ("PUT", "/api/inventory/filter", "admin"),
```

and `BODIES[("PUT", "/api/inventory/filter")] = {"allow": []}`.

- [ ] **Step 2: Run and see failures.**

- [ ] **Step 3: Implement `gui/inventory_routes.py`**

```python
"""Vyhledavani v inventari a filtr jmen (spec 2026-09-25, vlna B)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from migration_validator.gui.audit import record
from migration_validator.gui.authz import Actor, Permission, require
from migration_validator.hostname_filter import FilterStore, is_visible, normalize_patterns
from migration_validator.inventory import InventoryHost, InventorySource

SEARCH_LIMIT = 50


class FilterBody(BaseModel):
    allow: list[str]


def build_inventory_router(inventory: InventorySource | None, filters: FilterStore) -> APIRouter:
    router = APIRouter(prefix="/api/inventory")

    def _hosts() -> tuple[list[InventoryHost], list[str], str | None]:
        """(hosts, warnings, error). Chyba = nectitelny inventar."""
        if inventory is None:
            return [], [], None
        try:
            loaded = inventory.load()
        except OSError as error:
            return [], [], f"inventory {inventory.path}: {error.strerror or error}"
        return loaded.hosts, loaded.warnings, None

    def _state(allow: list[str]) -> dict:
        hosts, warnings, error = _hosts()
        return {
            "allow": allow,
            "visible": sum(1 for h in hosts if is_visible(h.node, allow)),
            "total": len(hosts),
            "warnings": warnings,
            "enabled": inventory is not None,
            "error": error,
        }

    @router.get("")
    def search(q: str = "", actor: Actor = require(Permission.VIEW)) -> dict:
        hosts, _, error = _hosts()
        if error is None:
            try:
                allow = filters.load()
            except ValueError as filter_error:
                error = str(filter_error)
        if error is not None:
            return {"items": [], "total": 0, "enabled": inventory is not None, "error": error}
        needle = q.strip().lower()
        matches = [h for h in hosts if is_visible(h.node, allow) and needle in h.node.lower()]
        return {
            "items": [{"node": h.node, "host": h.host} for h in matches[:SEARCH_LIMIT]],
            "total": len(matches),
            "enabled": inventory is not None,
            "error": None,
        }

    @router.get("/filter")
    def get_filter(actor: Actor = require(Permission.VIEW)) -> dict:
        try:
            allow = filters.load()
        except ValueError as error:
            state = _state([])
            state["error"] = str(error)
            return state
        return _state(allow)

    @router.put("/filter")
    def put_filter(body: FilterBody, dry_run: bool = False, actor: Actor = require(Permission.ADMIN)) -> dict:
        try:
            allow = normalize_patterns(body.allow)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if not dry_run:
            filters.save(allow)
            record(actor.username, "filter-edit", target="hostname_filter", allow=",".join(allow))
        return _state(allow)

    return router
```

`app.py` `create_app`: add params `inventory: InventorySource | None = None`, `hostname_filter: FilterStore | None = None`; after the groups router: `app.include_router(build_inventory_router(inventory, hostname_filter or FilterStore(DEFAULT_FILTER_FILE)))`.

- [ ] **Step 4: Run tests; full suite (matrix coverage test must pass with the new rows).**

- [ ] **Step 5: Commit** — `feat(gui): inventory search and hostname filter API`

---

### Task 4: `mig-validate gui` wires inventory and filter

**Files:**
- Modify: `migration_validator/cli.py` (`_cmd_gui`)
- Test: `tests/test_cli_gui.py` (append)

**Interfaces:**
- Consumes: `load_inventory_settings`, `InventorySource`, `FilterStore`, `DEFAULT_FILTER_FILE`, `create_app(inventory=…, hostname_filter=…)`.

- [ ] **Step 1: Failing tests** (reuse the file's `settings`, `captured`, `_add_admin` fixtures/helpers)

```python
def test_gui_without_inventory_setting_passes_none(settings, captured, tmp_path):
    _add_admin(tmp_path)
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path)]) == EXIT_OK
    client_app = captured["app"]
    from fastapi.testclient import TestClient
    from migration_validator.gui.authz import Actor
    client_app.state.actor_provider = lambda request: Actor(role="admin")
    assert TestClient(client_app).get("/api/inventory").json()["enabled"] is False


def test_gui_with_inventory_setting(tmp_path, captured):
    _add_admin(tmp_path)
    hosts = tmp_path / "hosts"
    hosts.write_text("MX-POP1 ansible_host=10.0.0.1\n")
    settings = tmp_path / "settings.yml"
    settings.write_text(f"auth:\n  users_file: {tmp_path / 'users.yml'}\ninventory:\n  path: {hosts}\n")
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path)]) == EXIT_OK
    from fastapi.testclient import TestClient
    from migration_validator.gui.authz import Actor
    app = captured["app"]
    app.state.actor_provider = lambda request: Actor(role="admin")
    body = TestClient(app).get("/api/inventory").json()
    assert body["items"] == [{"node": "MX-POP1", "host": "10.0.0.1"}]


def test_gui_starts_even_if_inventory_missing(tmp_path, captured):
    _add_admin(tmp_path)
    settings = tmp_path / "settings.yml"
    settings.write_text(f"auth:\n  users_file: {tmp_path / 'users.yml'}\ninventory:\n  path: {tmp_path / 'nope'}\n")
    assert main(["gui", "--settings", str(settings), "--run-root", str(tmp_path)]) == EXIT_OK
```

(Move the local imports to the file top if the file already imports them.)

- [ ] **Step 2: Implement** in `_cmd_gui`, next to the users store:

```python
    from migration_validator.auth import load_inventory_settings
    from migration_validator.hostname_filter import DEFAULT_FILTER_FILE, FilterStore
    from migration_validator.inventory import InventorySource

    inventory_path = load_inventory_settings(args.settings).path
    app = create_app(
        ...,
        users=users,
        inventory=InventorySource(inventory_path) if inventory_path else None,
        hostname_filter=FilterStore(DEFAULT_FILTER_FILE),
    )
```

- [ ] **Step 3: Run tests; full suite.** PASS.

- [ ] **Step 4: Commit** — `feat(cli): gui loads inventory.path and the hostname filter`

---

### Task 5: Node picker (pure logic + new-run and bulk forms)

**Files:**
- Create: `migration_validator/gui/static/node_picker.js`, `tests/js/node_picker.test.js`
- Modify: `migration_validator/gui/static/index.html` (script tag before `app.js`), `app.js`, `style.css`

**Interfaces:**
- Consumes: `GET /api/inventory?q=` → `{items, total, enabled, error}`; `GET /api/inventory/filter` → `{enabled, error, …}`.
- Produces (JS, `MigPicker` global / CommonJS export):
  - `pickerMode(device, inventoryAvailable) -> "pick" | "manual"` — `"manual"` when inventory unavailable or `device.manual === true`.
  - `applyPick(device, item)` — sets `node`, `host`, `picked = true`, `manual = false`.
  - `clearPick(device)` — `node = ""`, `host = ""`, `picked = false`.
  - `setManual(device, manual)` — sets `manual`; always clears node/host/picked (spec: unticking clears; ticking starts from empty inputs as today).
  - `moveHighlight(index, delta, length) -> number` — wraps within `[0, length)`, `-1` when `length === 0`.
  - `moreLabel(total, shown) -> string | null` — `"N more — refine the search"` when `total > shown`, else `null`.
  - `itemLabel(item) -> string` — `"MX-POP1 · 172.20.20.4"`.

- [ ] **Step 1: Failing JS tests** — `tests/js/node_picker.test.js`

```js
const test = require("node:test");
const assert = require("node:assert");
const P = require("../../migration_validator/gui/static/node_picker.js");

test("pickerMode: manual when inventory unavailable or device.manual", () => {
  assert.strictEqual(P.pickerMode({}, false), "manual");
  assert.strictEqual(P.pickerMode({ manual: true }, true), "manual");
  assert.strictEqual(P.pickerMode({}, true), "pick");
});

test("applyPick / clearPick", () => {
  const d = { node: "", host: "", platform: "junos" };
  P.applyPick(d, { node: "MX-POP1", host: "10.0.0.1" });
  assert.deepStrictEqual(d, { node: "MX-POP1", host: "10.0.0.1", platform: "junos", picked: true, manual: false });
  P.clearPick(d);
  assert.strictEqual(d.node, ""); assert.strictEqual(d.host, ""); assert.strictEqual(d.picked, false);
  assert.strictEqual(d.platform, "junos");
});

test("setManual clears both ways", () => {
  const d = { node: "MX-POP1", host: "10.0.0.1", picked: true };
  P.setManual(d, true);
  assert.deepStrictEqual([d.node, d.host, d.picked, d.manual], ["", "", false, true]);
  d.node = "typed"; d.host = "1.1.1.1";
  P.setManual(d, false);
  assert.deepStrictEqual([d.node, d.host, d.manual], ["", "", false]);
});

test("moveHighlight wraps and handles empty", () => {
  assert.strictEqual(P.moveHighlight(-1, 1, 3), 0);
  assert.strictEqual(P.moveHighlight(2, 1, 3), 0);
  assert.strictEqual(P.moveHighlight(0, -1, 3), 2);
  assert.strictEqual(P.moveHighlight(1, 1, 0), -1);
});

test("labels", () => {
  assert.strictEqual(P.moreLabel(120, 50), "70 more — refine the search");
  assert.strictEqual(P.moreLabel(50, 50), null);
  assert.strictEqual(P.itemLabel({ node: "MX-POP1", host: "172.20.20.4" }), "MX-POP1 · 172.20.20.4");
});
```

- [ ] **Step 2: Run** `node --test tests/js/*.test.js` → fails (module missing).

- [ ] **Step 3: Implement `static/node_picker.js`** following the module pattern of `profile_diff.js` (plain functions, `const MigPicker = {...}`, `if (typeof module !== "undefined" && module.exports) module.exports = MigPicker;`). Run JS tests → PASS.

- [ ] **Step 4: Picker component in `app.js`**

Add `<script src="/static/node_picker.js"></script>` before `app.js` in `index.html`.

Inventory availability: on `openNewRunForm()` and `openAddDevicesModal()`, fetch `GET /api/inventory/filter` once and store `this.cache.inventory = { enabled, error }` (on fetch failure: `{ enabled: false, error: "…" }`). `inventoryAvailable = enabled && !error`.

New method `buildNodePicker(device, { onPicked, focusKey })` returns an element:

- Mode `"manual"` (per `MigPicker.pickerMode`) → return `null`; callers render today's node/host inputs unchanged.
- Picked (`device.picked`) → a read-only row: node (mono) · host, and a `×` button → `MigPicker.clearPick(device); this.render()`.
- Otherwise → a wrapper with a search `<input>` (placeholder `Search inventory…`, `data-focus-key` = `focusKey`) and a dropdown `<div class="picker-dropdown" role="listbox">`. **Typing does not call `this.render()`**: the input handler debounces 200 ms, fetches `/api/inventory?q=…`, and rebuilds only the dropdown element's children (item rows using `MigPicker.itemLabel`, highlighted row, trailing `MigPicker.moreLabel` note; an `error` from the response shows as a note). Keep `device.query` updated so a full re-render restores the text. ArrowUp/ArrowDown move the highlight (`MigPicker.moveHighlight`), Enter picks the highlighted item, Esc closes the dropdown; mousedown on an item picks it. Picking → `MigPicker.applyPick(device, item); onPicked(); this.render()`. Ignore responses that arrive after a newer query was sent (keep a per-picker request counter).
- A `Manual` checkbox next to the field (hidden when inventory unavailable) → `MigPicker.setManual(device, checked); this.render()`.

`buildDeviceSubform(title, device, touched)`: when the picker is not `null`, replace the Node and Host fields with the picker (label "Node"); keep the "node is required" error under it when `touched && !device.node`. Platform select unchanged. When the picker is `null`, render exactly today's fields.

`buildBulkDeviceRows(devices, …)`: per row, the node and host cells become one picker cell spanning both columns when the row is in pick mode, with the per-row `Manual` checkbox; manual rows render today's two inputs. Duplicate/missing-host validation (`bulkRowsValid`, row errors) is unchanged. New rows pushed by "+ add device" start as `{ node: "", host: "", platform: "junos" }` (pick mode by default when inventory is available).

Styles in `style.css` for `.picker`, `.picker-dropdown` (absolute, max-height with scroll, same border colour/hex values as `.form-input`), `.picker-item`, `.picker-item.active`, `.picker-picked`, `.picker-manual`. Reuse existing hex colours and classes (`style.css` has no CSS custom properties).

- [ ] **Step 5: Verify** — `node --check migration_validator/gui/static/app.js`, `node --test tests/js/*.test.js`, full pytest suite.

- [ ] **Step 6: Commit** — `feat(gui): inventory node picker in new-run and bulk forms`

---

### Task 6: Admin Settings screen, docs

**Files:**
- Modify: `migration_validator/gui/static/index.html`, `app.js`, `style.css`, `docs/en/README.md`, `docs/cs/README.md`

**Interfaces:**
- Consumes: `GET/PUT /api/inventory/filter` (+ `?dry_run=true`), `data-perm="admin"` CSS rule from wave A (`body:not([data-role="admin"]) [data-perm="admin"] { display:none }`).

Ruling recorded in the plan: the spec says "sidebar entry Settings"; the sidebar is the per-run snapshot list, so Settings is a topbar button next to **Profiles** instead (same toggle pattern as Profiles).

- [ ] **Step 1: Topbar button** — in `index.html`, after `#btn-profiles`: `<button class="btn btn-secondary" id="btn-settings" data-perm="admin">Settings</button>`. In `app.js` wire it like `btnProfilesEl` (click → `this.goToSettings()`; `render()` toggles `btn-toggle-active` when `state.view === "settings"`), add `case "settings": this.renderSettingsView(); break;` to the render switch, and a `GUIDE_TEXT.settings` entry (Czech, like the others): what the filter does (glob, whole name, case-insensitive; empty = everything; only narrows the inventory search, never existing runs or manual entry).

- [ ] **Step 2: `goToSettings()` / `renderSettingsView()`**

State `this.state.settings = { text, saved, counts, warnings, enabled, error, saving, saveError, dirty }`:

- Load `GET /api/inventory/filter`; `text = allow.join("\n")`.
- Render: `h1` "Settings", a form-card "Hostname filter" with a monospace `<textarea>` (one pattern per line), a line `N of M nodes visible` (or "Inventory not configured — set inventory.path in config/settings.yml" when `!enabled`, or the `error` text), an "unsaved changes" marker when `dirty`, the warnings list (if any) under a "Inventory warnings" label, and a **Save** button (`data-perm="admin"`).
- Typing updates `text`, sets `dirty`, and after 300 ms debounce sends `PUT /api/inventory/filter?dry_run=true` with the non-empty trimmed lines; the response updates only the count line (no full render, keep caret). Blank lines are dropped client-side before sending; a 422 shows inline.
- Save → `PUT /api/inventory/filter` → on 200 update state from the response, clear `dirty`, render; on error show `saveError`.
- `leaveGuard()` should treat unsaved filter changes like the profile editor does (grep `leaveGuard` and extend it for `view === "settings" && state.settings.dirty`).

- [ ] **Step 3: Docs** — `docs/en/README.md` and `docs/cs/README.md` (Czech with diacritics), next to the wave A "Accounts and login" section, add "Host inventory":
  - `inventory.path` in `config/settings.yml` (Ansible INI: `NAME ansible_host=IP`; groups/`:vars`/`:children` skipped; no ranges/YAML; warnings shown on the Settings screen; file changes picked up without restart).
  - New run / bulk: search fills node + host; **Manual** checkbox for free entry; platform always chosen by hand.
  - Hostname filter: admin-only Settings screen; globs on the whole name, case-insensitive (`MX-*`, `*POP1*`); empty = all; only narrows the search; stored in `config/hostname_filter.yml` (gitignored).
  - Add `inventory.path` to the settings reference where `auth.users_file` is documented.

- [ ] **Step 4: Verify** — `node --check …/app.js`, `node --test tests/js/*.test.js`, full pytest suite.

- [ ] **Step 5: Commit** — `feat(gui): admin hostname filter settings screen; inventory docs`

---

## Manual verification (controller, after Task 6)

Start the GUI with a temp settings file pointing at an inventory of ~60 hosts, log in as admin: new run → type `ptx` → pick → node/host filled read-only; `×` clears; Manual toggles to free inputs; bulk table rows independent; Settings → type `MX-*` → live count changes without saving → Save → search only offers MX-*; log in as operator → no Settings button; `PUT /api/inventory/filter` as operator via curl → 403.
