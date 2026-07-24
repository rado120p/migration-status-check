# Migration Validator — Plán 1: offline vyhodnocovací cesta

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Postavit kompletní offline vrstvu validátoru — modely, scoping, párování služeb, checky, engine a reporting — tak, aby `mig-validate evaluate` fungoval nad snapshoty bez jediného přístupu na síť.

**Architecture:** Snapshot-centric model podle specu. Checky jsou čisté funkce `(facts, scope) → Finding`; framework z `Finding` a severity odvodí `Status`. Scope je čistě filtr nad device-scoped daty. Párování služeb běží nad scopy, ne nad checky. Vše v tomto plánu je testovatelné z fixtures.

**Tech Stack:** Python 3.13, dataclasses, PyYAML, pytest. Žádné pyATS, žádné JSNAPy.

**Spec:** `docs/superpowers/specs/2026-07-24-migration-validator-design.md`

**Navazuje:** Plán 2 (`capture`, collectory, probes) — implementuje se až po tomto plánu.

## Global Constraints

- **Python ≥ 3.11**, vývoj a testy běží pod 3.13.9 v `.venv` v kořeni repozitáře. Existující `pyats-venv/` se nemaže ani nepoužívá.
- **Balík se jmenuje `migration_validator`**, konzolový příkaz `mig-validate`.
- **Žádná závislost na pyATS, Genie ani JSNAPy.** Povolené runtime závislosti: `junos-eznc`, `PyYAML`, `lxml`.
- **Checky nikdy nesahají na síť.** Žádný import z `migration_validator.connection` v `migration_validator/checks/`.
- **Collectory nikdy neinterpretují.** V tomto plánu se collectory nepíšou, ale fact schéma je dané specem a nesmí obsahovat verdikty.
- **Chybějící data nikdy nedají PASS.** Nelze-li něco změřit, výsledek je `SKIP` s důvodem.
- **Všechny `message` řetězce jsou česky.**
- **`schema_version` je `1`** pro snapshot i result.
- **Tranzitní rozhraní:** `ge`, `xe`, `et`, `ae`. Jen na nich běží `interface_errors` a `interface_traffic`.
- **Management rozhraní** (`fxp`, `em`, `me`, `vme`, `bme`, `re0:mgmt-`, `re1:mgmt-`) se nikdy nestanou service scopem.
- **Migrované typy služeb:** `Internet`, `IPVPN`, `E-Line`, `E-LAN`, `Core`. `Layer1` scopem není.
- **Ping běží jen na `Internet` a `IPVPN`.**
- **Výchozí tolerance:** provoz `-60 %`, BGP prefixy `-10 %`.
- **Exit kódy:** `0` bez FAIL, `1` s FAIL, `2` chyba nástroje.
- **Commit po každém tasku.** Zprávy anglicky, prefix `feat:` / `test:` / `chore:`.

---

## Struktura souborů

| soubor | zodpovědnost |
|---|---|
| `pyproject.toml` | balík, závislosti, konzolový skript, pytest config |
| `migration_validator/models/result.py` | `Status`, `Severity`, `Outcome`, `Finding`, `CheckResult`, `ScopeResult`, `MatchInfo`, `RunResult` |
| `migration_validator/models/inventory.py` | `ServiceEntry`, `load_inventory()` |
| `migration_validator/models/scope.py` | `ScopeKey`, `Selectors`, `Scope` + `Scope.select()` |
| `migration_validator/models/snapshot.py` | `DeviceMeta`, `CaptureMeta`, `Snapshot` + JSON round-trip |
| `migration_validator/scoping/builder.py` | inventory → scopy (způsobilost, Layer1 jako rodič, mgmt vyloučení) |
| `migration_validator/scoping/mapping.py` | `mapping.yml` — `MappingRule`, `Selector`, `Mapping` |
| `migration_validator/scoping/matcher.py` | řetěz párovacích pravidel, nejednoznačnost, `MatchSet` |
| `migration_validator/checks/base.py` | `Mode`, `Check` ABC, `CheckContext`, odvození statusu, `CheckConfig` |
| `migration_validator/checks/registry.py` | registrace a výběr checků |
| `migration_validator/checks/ifaces.py` | `interface_state`, `interface_errors`, `interface_traffic` + klasifikace rozhraní |
| `migration_validator/checks/reachability.py` | `arp_present`, `ping_reachability` |
| `migration_validator/checks/bgp.py` | `bgp_session_state`, `bgp_prefix_counts` |
| `migration_validator/checks/evpn.py` | `evpn_vpws_status`, `evpn_esi_status`, `evpn_mac_count` |
| `migration_validator/engine.py` | orchestrace vyhodnocení, agregace, `unassigned` |
| `migration_validator/api.py` | `evaluate()`, `list_checks()` (`capture()` až v Plánu 2) |
| `migration_validator/reporting/json_report.py` | serializace `RunResult` |
| `migration_validator/reporting/text_report.py` | terminálová tabulka, filtrování |
| `migration_validator/cli.py` | `evaluate`, `match`, `checks` (`capture` až v Plánu 2) |

Testy zrcadlí strukturu v `tests/`, sdílené fixtures v `tests/conftest.py`.

---

### Task 1: Skeleton projektu a nástroje

**Files:**
- Create: `pyproject.toml`
- Create: `migration_validator/__init__.py`
- Create: `.gitignore`
- Test: `tests/test_package.py`

**Interfaces:**
- Consumes: nic
- Produces: importovatelný balík `migration_validator` s `__version__: str`; funkční `.venv/bin/pytest`

- [ ] **Step 1: Vytvoř `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
pyats-venv/
*.egg-info/
.pytest_cache/
runs/
```

- [ ] **Step 2: Vytvoř `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "migration-validator"
version = "0.1.0"
description = "Validace stavu sitovych sluzeb pri migraci Junos -> Junos EVO"
requires-python = ">=3.11"
dependencies = [
    "junos-eznc>=2.7",
    "PyYAML>=6.0",
    "lxml>=5.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
mig-validate = "migration_validator.cli:main"

[tool.setuptools.packages.find]
include = ["migration_validator*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 3: Vytvoř `migration_validator/__init__.py`**

```python
"""Validace stavu sitovych sluzeb pri migraci Junos -> Junos EVO."""

__version__ = "0.1.0"
```

- [ ] **Step 4: Napiš failing test**

Vytvoř `tests/test_package.py`:

```python
import migration_validator


def test_package_exposes_version():
    assert migration_validator.__version__ == "0.1.0"
```

- [ ] **Step 5: Vytvoř venv a nainstaluj**

```bash
python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[dev]"
```

Expected: instalace projde bez chyby. Ověř PyEZ:

```bash
.venv/bin/python -c "from jnpr.junos import Device; print('pyez ok')"
```

Expected: `pyez ok`

- [ ] **Step 6: Spusť test**

Run: `.venv/bin/pytest tests/test_package.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml migration_validator/__init__.py tests/test_package.py
git commit -m "chore: scaffold migration_validator package"
```

---

### Task 2: Model výsledků a odvození statusu

**Files:**
- Create: `migration_validator/models/__init__.py`
- Create: `migration_validator/models/result.py`
- Test: `tests/models/test_result.py`

**Interfaces:**
- Consumes: nic
- Produces:
  - `Status` (str Enum): `PASS`, `WARN`, `FAIL`, `SKIP`; property `rank: int`; classmethod `worst(statuses: Iterable[Status]) -> Status`
  - `Severity` (str Enum): `CRITICAL`, `ADVISORY`
  - `Outcome` (str Enum): `OK`, `DEGRADED`, `BROKEN`, `SKIP`
  - `derive_status(outcome: Outcome, severity: Severity) -> Status`
  - `Finding(outcome, message, label=None, baseline=None, subject=None, details=dict)`
  - `CheckResult(id, mode, status, severity, message, label=None, baseline=None, subject=None, details=dict)` + `to_dict()`
  - `MatchInfo(status, method=None, confidence=None, baseline_interfaces=list, subject_interfaces=list, reason=None)` + `to_dict()`
  - `ScopeResult(scope_id, key, status, match, checks)` + `to_dict()`
  - `RunResult(schema_version, evaluated_at, subject, baseline, summary, scopes, unmatched, unassigned)` + `to_dict()`

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/models/__init__.py` (prázdný) a `tests/models/test_result.py`:

```python
import pytest

from migration_validator.models.result import (
    Finding,
    Outcome,
    Severity,
    Status,
    derive_status,
)


@pytest.mark.parametrize(
    "outcome,severity,expected",
    [
        (Outcome.OK, Severity.CRITICAL, Status.PASS),
        (Outcome.OK, Severity.ADVISORY, Status.PASS),
        (Outcome.DEGRADED, Severity.CRITICAL, Status.WARN),
        (Outcome.DEGRADED, Severity.ADVISORY, Status.WARN),
        (Outcome.BROKEN, Severity.CRITICAL, Status.FAIL),
        (Outcome.BROKEN, Severity.ADVISORY, Status.WARN),
        (Outcome.SKIP, Severity.CRITICAL, Status.SKIP),
        (Outcome.SKIP, Severity.ADVISORY, Status.SKIP),
    ],
)
def test_derive_status(outcome, severity, expected):
    assert derive_status(outcome, severity) is expected


def test_degraded_is_warn_even_when_critical():
    """Castecny uspech je vzdy WARN - pravidlo je v jednom miste, ne v checcich."""
    assert derive_status(Outcome.DEGRADED, Severity.CRITICAL) is Status.WARN


def test_status_worst_ranks_skip_above_pass():
    assert Status.worst([Status.PASS, Status.SKIP]) is Status.SKIP
    assert Status.worst([Status.PASS, Status.WARN, Status.SKIP]) is Status.WARN
    assert Status.worst([Status.WARN, Status.FAIL]) is Status.FAIL
    assert Status.worst([]) is Status.SKIP


def test_finding_defaults():
    finding = Finding(outcome=Outcome.OK, message="vse ok")
    assert finding.label is None
    assert finding.details == {}
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/models/test_result.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.models'`

- [ ] **Step 3: Implementuj model**

Vytvoř `migration_validator/models/__init__.py` (prázdný) a `migration_validator/models/result.py`:

```python
"""Datove modely vysledku validace."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class Status(str, Enum):
    """Vysledny stav checku nebo scope."""

    PASS = "PASS"
    SKIP = "SKIP"
    WARN = "WARN"
    FAIL = "FAIL"

    @property
    def rank(self) -> int:
        return _STATUS_RANK[self]

    @classmethod
    def worst(cls, statuses: Iterable["Status"]) -> "Status":
        """Nejhorsi stav ze sady. Prazdna sada = SKIP (nic se nezmerilo)."""
        collected = list(statuses)
        if not collected:
            return cls.SKIP
        return max(collected, key=lambda status: status.rank)


_STATUS_RANK: dict[Status, int] = {
    Status.PASS: 0,
    Status.SKIP: 1,
    Status.WARN: 2,
    Status.FAIL: 3,
}


class Severity(str, Enum):
    CRITICAL = "critical"
    ADVISORY = "advisory"


class Outcome(str, Enum):
    """Co check namerí - status z toho odvodi framework."""

    OK = "ok"
    DEGRADED = "degraded"
    BROKEN = "broken"
    SKIP = "skip"


def derive_status(outcome: Outcome, severity: Severity) -> Status:
    """Prevede vysledek mereni na status podle severity.

    DEGRADED je vzdy WARN - castecny uspech nesmi byt tvrdy FAIL ani pri
    severity critical.
    """
    if outcome is Outcome.OK:
        return Status.PASS
    if outcome is Outcome.SKIP:
        return Status.SKIP
    if outcome is Outcome.DEGRADED:
        return Status.WARN
    return Status.FAIL if severity is Severity.CRITICAL else Status.WARN


@dataclass
class Finding:
    """Namereny vysledek jednoho checku pred odvozenim statusu."""

    outcome: Outcome
    message: str
    label: str | None = None
    baseline: dict[str, Any] | None = None
    subject: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    id: str
    mode: str
    status: Status
    severity: Severity
    message: str
    label: str | None = None
    baseline: dict[str, Any] | None = None
    subject: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "mode": self.mode,
            "status": self.status.value,
            "severity": self.severity.value,
            "message": self.message,
        }
        if self.label is not None:
            payload["label"] = self.label
        if self.baseline is not None:
            payload["baseline"] = self.baseline
        if self.subject is not None:
            payload["subject"] = self.subject
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass
class MatchInfo:
    status: str  # matched | unmatched
    method: str | None = None
    confidence: str | None = None
    baseline_interfaces: list[str] = field(default_factory=list)
    subject_interfaces: list[str] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status}
        for name in ("method", "confidence", "reason"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        payload["baseline_interfaces"] = list(self.baseline_interfaces)
        payload["subject_interfaces"] = list(self.subject_interfaces)
        return payload


@dataclass
class ScopeResult:
    scope_id: str
    key: dict[str, Any]
    status: Status
    match: MatchInfo | None
    checks: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "key": self.key,
            "status": self.status.value,
            "match": self.match.to_dict() if self.match else None,
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass
class RunResult:
    evaluated_at: str
    subject: dict[str, Any]
    baseline: dict[str, Any] | None
    summary: dict[str, Any]
    scopes: list[ScopeResult] = field(default_factory=list)
    unmatched: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"baseline": [], "subject": []}
    )
    unassigned: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"bgp_peers": []}
    )
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluated_at": self.evaluated_at,
            "subject": self.subject,
            "baseline": self.baseline,
            "summary": self.summary,
            "scopes": [scope.to_dict() for scope in self.scopes],
            "unmatched": self.unmatched,
            "unassigned": self.unassigned,
        }
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/models/test_result.py -v`
Expected: PASS, 12 testů

- [ ] **Step 5: Commit**

```bash
git add migration_validator/models tests/models
git commit -m "feat: add result model with status derivation"
```

---

### Task 3: Model inventory a načítání YAML

**Files:**
- Create: `migration_validator/models/inventory.py`
- Test: `tests/models/test_inventory.py`

**Interfaces:**
- Consumes: nic
- Produces:
  - `ServiceEntry` dataclass s poli: `interface: str`, `description: str | None`, `service_type: str`, `service_subtype: str | None`, `ip_address: list[str]`, `virtual_gw_ip_address: list[str]`, `routing_instance: str | None`, `active: bool`, `protocol: list[str]`, `bgp_neighbor: list[str]`, `bridge_domain: list[str]`, `customer_vlan: list[str]`
  - `ServiceEntry.physical_name: str` — část názvu před tečkou
  - `ServiceEntry.from_dict(data: dict) -> ServiceEntry`
  - `Inventory(device: str, entries: list[ServiceEntry])`
  - `load_inventory(path: str | Path) -> Inventory`

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/models/test_inventory.py`:

```python
import textwrap

import pytest

from migration_validator.models.inventory import ServiceEntry, load_inventory


def test_from_dict_fills_defaults_for_missing_keys():
    entry = ServiceEntry.from_dict({"interface": "ge-0/0/1.0", "service_type": "Internet"})
    assert entry.interface == "ge-0/0/1.0"
    assert entry.service_type == "Internet"
    assert entry.description is None
    assert entry.service_subtype is None
    assert entry.ip_address == []
    assert entry.bgp_neighbor == []
    assert entry.active is True


def test_from_dict_ignores_unknown_keys():
    entry = ServiceEntry.from_dict(
        {
            "interface": "ge-0/0/1.0",
            "service_type": "Internet",
            "detection_confidence": "medium",
            "detection_reason": ["cokoliv"],
        }
    )
    assert entry.interface == "ge-0/0/1.0"


@pytest.mark.parametrize(
    "interface,expected",
    [
        ("ge-0/0/2.113", "ge-0/0/2"),
        ("ge-0/0/2", "ge-0/0/2"),
        ("re0:mgmt-0.0", "re0:mgmt-0"),
        ("irb.14", "irb"),
    ],
)
def test_physical_name(interface, expected):
    entry = ServiceEntry.from_dict({"interface": interface, "service_type": "Internet"})
    assert entry.physical_name == expected


def test_load_inventory(tmp_path):
    path = tmp_path / "device.yml"
    path.write_text(
        textwrap.dedent(
            """\
            device: 172.20.20.4
            interfaces:
            - interface: ge-0/0/2.113
              description: L3VPN-CPE13-NNI
              service_type: IPVPN
              service_subtype: null
              ip_address:
              - 198.11.13.1/30
              virtual_gw_ip_address: []
              routing_instance: L3VPN-CPE13-NNI
              active: true
              protocol:
              - bgp
              bgp_neighbor:
              - 198.11.13.2
              bridge_domain: []
              customer_vlan:
              - '113'
            """
        ),
        encoding="utf-8",
    )

    inventory = load_inventory(path)

    assert inventory.device == "172.20.20.4"
    assert len(inventory.entries) == 1
    entry = inventory.entries[0]
    assert entry.description == "L3VPN-CPE13-NNI"
    assert entry.routing_instance == "L3VPN-CPE13-NNI"
    assert entry.bgp_neighbor == ["198.11.13.2"]
    assert entry.customer_vlan == ["113"]


def test_load_inventory_rejects_missing_interfaces_key(tmp_path):
    path = tmp_path / "bad.yml"
    path.write_text("device: 1.2.3.4\n", encoding="utf-8")

    with pytest.raises(ValueError, match="interfaces"):
        load_inventory(path)
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/models/test_inventory.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.models.inventory'`

- [ ] **Step 3: Implementuj model**

Vytvoř `migration_validator/models/inventory.py`:

```python
"""Model inventory sluzeb - vystup parseru konfigurace."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


@dataclass
class ServiceEntry:
    """Jeden zaznam z inventory - rozhrani a sluzba, ktera na nem bezi."""

    interface: str
    service_type: str
    description: str | None = None
    service_subtype: str | None = None
    ip_address: list[str] = field(default_factory=list)
    virtual_gw_ip_address: list[str] = field(default_factory=list)
    routing_instance: str | None = None
    active: bool = True
    protocol: list[str] = field(default_factory=list)
    bgp_neighbor: list[str] = field(default_factory=list)
    bridge_domain: list[str] = field(default_factory=list)
    customer_vlan: list[str] = field(default_factory=list)

    @property
    def physical_name(self) -> str:
        """Nazev fyzickeho rodice - cast pred teckou."""
        return self.interface.split(".", 1)[0]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServiceEntry":
        if "interface" not in data:
            raise ValueError("zaznam inventory nema klic 'interface'")
        if "service_type" not in data:
            raise ValueError(
                f"zaznam inventory pro {data['interface']} nema klic 'service_type'"
            )
        return cls(
            interface=str(data["interface"]),
            service_type=str(data["service_type"]),
            description=_as_optional_str(data.get("description")),
            service_subtype=_as_optional_str(data.get("service_subtype")),
            ip_address=_as_list(data.get("ip_address")),
            virtual_gw_ip_address=_as_list(data.get("virtual_gw_ip_address")),
            routing_instance=_as_optional_str(data.get("routing_instance")),
            active=bool(data.get("active", True)),
            protocol=_as_list(data.get("protocol")),
            bgp_neighbor=_as_list(data.get("bgp_neighbor")),
            bridge_domain=_as_list(data.get("bridge_domain")),
            customer_vlan=_as_list(data.get("customer_vlan")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface": self.interface,
            "description": self.description,
            "service_type": self.service_type,
            "service_subtype": self.service_subtype,
            "ip_address": list(self.ip_address),
            "virtual_gw_ip_address": list(self.virtual_gw_ip_address),
            "routing_instance": self.routing_instance,
            "active": self.active,
            "protocol": list(self.protocol),
            "bgp_neighbor": list(self.bgp_neighbor),
            "bridge_domain": list(self.bridge_domain),
            "customer_vlan": list(self.customer_vlan),
        }


@dataclass
class Inventory:
    device: str
    entries: list[ServiceEntry] = field(default_factory=list)


def load_inventory(path: str | Path) -> Inventory:
    """Nacte YAML vystup parseru konfigurace."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping, nalezeno {type(raw).__name__}")
    if "interfaces" not in raw:
        raise ValueError(f"{path}: chybi klic 'interfaces'")
    entries = [ServiceEntry.from_dict(item) for item in raw["interfaces"] or []]
    return Inventory(device=str(raw.get("device", "")), entries=entries)
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/models/test_inventory.py -v`
Expected: PASS, 9 testů

- [ ] **Step 5: Ověř proti reálným datům**

```bash
.venv/bin/python -c "
from migration_validator.models.inventory import load_inventory
for path in ('172.20.20.4.yml', '172.20.20.5.yml'):
    inv = load_inventory(path)
    print(path, inv.device, len(inv.entries), 'zaznamu')
"
```

Expected: obě cesty se načtou bez výjimky a vypíšou počet záznamů.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/models/inventory.py tests/models/test_inventory.py
git commit -m "feat: add inventory model and YAML loader"
```

---

### Task 4: Model scope a filtrování faktů

**Files:**
- Create: `migration_validator/models/scope.py`
- Test: `tests/models/test_scope.py`

**Interfaces:**
- Consumes: nic
- Produces:
  - `ScopeKey(description: str | None, service_type: str, service_subtype: str | None)` — frozen, hashovatelný; `to_dict()`
  - `Selectors(interfaces, physical_interfaces, routing_instances, bgp_neighbors, local_addresses, virtual_gw, vlans, bridge_domains)` — vše `list[str]`; `to_dict()`; `matches_interface(name: str) -> bool`
  - `Scope(id: str, kind: str, key: ScopeKey | None, selectors: Selectors)`; `to_dict()`; `from_dict()`
  - `Scope.is_device: bool`
  - `Scope.select(facts: dict, probes: dict | None = None) -> dict` — vrací dict s klíči `interfaces`, `arp`, `bgp`, `evpn_vpws`, `evpn_esi`, `evpn_mac`, `ping`
  - `device_scope() -> Scope`

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/models/test_scope.py`:

```python
from migration_validator.models.scope import (
    Scope,
    ScopeKey,
    Selectors,
    device_scope,
)

FACTS = {
    "interfaces": {
        "ge-0/0/2": {"oper_status": "up"},
        "ge-0/0/2.113": {"oper_status": "up", "input_pps": 10},
        "ge-0/0/9.0": {"oper_status": "down"},
    },
    "arp": [
        {"ip": "198.11.13.2", "interface": "ge-0/0/2.113"},
        {"ip": "10.9.9.2", "interface": "ge-0/0/9.0"},
    ],
    "bgp": {
        "198.11.13.2": {"state": "Established"},
        "10.9.9.2": {"state": "Active"},
    },
    "evpn_vpws": {"EVPN-VPWS-CPE13-NNI": {"status": "Up"}},
    "evpn_esi": {"00:11": {"status": "Up", "interface": "ge-0/0/2.113"}},
    "evpn_mac": {"L3VPN-CPE13-NNI": {"BD-313": 42}},
}

PROBES = {
    "ping": [
        {"scope_id": "svc:L3VPN-CPE13-NNI:IPVPN", "target": "198.11.13.2", "received": 5},
        {"scope_id": "svc:jiny:Internet", "target": "10.9.9.2", "received": 0},
    ]
}


def _service_scope() -> Scope:
    return Scope(
        id="svc:L3VPN-CPE13-NNI:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"],
            physical_interfaces=["ge-0/0/2"],
            routing_instances=["L3VPN-CPE13-NNI"],
            bgp_neighbors=["198.11.13.2"],
            local_addresses=["198.11.13.1/30"],
            virtual_gw=[],
            vlans=["113"],
            bridge_domains=[],
        ),
    )


def test_select_filters_interfaces_including_physical_parent():
    selected = _service_scope().select(FACTS, PROBES)
    assert set(selected["interfaces"]) == {"ge-0/0/2", "ge-0/0/2.113"}


def test_select_filters_arp_by_interface():
    selected = _service_scope().select(FACTS, PROBES)
    assert [entry["ip"] for entry in selected["arp"]] == ["198.11.13.2"]


def test_select_filters_bgp_by_neighbor():
    selected = _service_scope().select(FACTS, PROBES)
    assert list(selected["bgp"]) == ["198.11.13.2"]


def test_select_filters_evpn_by_routing_instance_and_interface():
    selected = _service_scope().select(FACTS, PROBES)
    assert list(selected["evpn_mac"]) == ["L3VPN-CPE13-NNI"]
    assert list(selected["evpn_esi"]) == ["00:11"]
    assert selected["evpn_vpws"] == {}


def test_select_filters_ping_by_scope_id():
    selected = _service_scope().select(FACTS, PROBES)
    assert [probe["target"] for probe in selected["ping"]] == ["198.11.13.2"]


def test_device_scope_selects_everything():
    selected = device_scope().select(FACTS, PROBES)
    assert selected["interfaces"] == FACTS["interfaces"]
    assert selected["arp"] == FACTS["arp"]
    assert selected["bgp"] == FACTS["bgp"]
    assert len(selected["ping"]) == 2


def test_device_scope_is_flagged():
    assert device_scope().is_device is True
    assert _service_scope().is_device is False


def test_select_tolerates_missing_fact_areas():
    selected = _service_scope().select({}, None)
    assert selected["interfaces"] == {}
    assert selected["arp"] == []
    assert selected["ping"] == []


def test_scope_round_trip():
    scope = _service_scope()
    assert Scope.from_dict(scope.to_dict()) == scope
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/models/test_scope.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.models.scope'`

- [ ] **Step 3: Implementuj model**

Vytvoř `migration_validator/models/scope.py`:

```python
"""Scope - filtr nad device-scoped fakty.

Scope neobsahuje zadna namerena data. Bez inventory existuje jediny device
scope s prazdnymi selektory, ktery propousti vse - diky tomu nemaji checky
zadnou vetev pro rezim bez inventory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEVICE_SCOPE_ID = "device"

FACT_AREAS = ("interfaces", "arp", "bgp", "evpn_vpws", "evpn_esi", "evpn_mac")


@dataclass(frozen=True)
class ScopeKey:
    description: str | None
    service_type: str
    service_subtype: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "service_type": self.service_type,
            "service_subtype": self.service_subtype,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScopeKey":
        return cls(
            description=data.get("description"),
            service_type=data["service_type"],
            service_subtype=data.get("service_subtype"),
        )


@dataclass
class Selectors:
    interfaces: list[str] = field(default_factory=list)
    physical_interfaces: list[str] = field(default_factory=list)
    routing_instances: list[str] = field(default_factory=list)
    bgp_neighbors: list[str] = field(default_factory=list)
    local_addresses: list[str] = field(default_factory=list)
    virtual_gw: list[str] = field(default_factory=list)
    vlans: list[str] = field(default_factory=list)
    bridge_domains: list[str] = field(default_factory=list)

    def matches_interface(self, name: str) -> bool:
        return name in self.interfaces or name in self.physical_interfaces

    def to_dict(self) -> dict[str, Any]:
        return {
            "interfaces": list(self.interfaces),
            "physical_interfaces": list(self.physical_interfaces),
            "routing_instances": list(self.routing_instances),
            "bgp_neighbors": list(self.bgp_neighbors),
            "local_addresses": list(self.local_addresses),
            "virtual_gw": list(self.virtual_gw),
            "vlans": list(self.vlans),
            "bridge_domains": list(self.bridge_domains),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Selectors":
        return cls(**{name: list(data.get(name, [])) for name in cls().to_dict()})


@dataclass
class Scope:
    id: str
    kind: str  # service | device
    key: ScopeKey | None
    selectors: Selectors

    @property
    def is_device(self) -> bool:
        return self.kind == "device"

    @property
    def service_type(self) -> str | None:
        return self.key.service_type if self.key else None

    def select(
        self, facts: dict[str, Any], probes: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Vybere z device-scoped faktu jen to, co patri tomuto scope."""
        probes = probes or {}
        pings = list(probes.get("ping", []))

        if self.is_device:
            selected: dict[str, Any] = {area: facts.get(area, _empty(area)) for area in FACT_AREAS}
            selected["ping"] = pings
            return selected

        interfaces = {
            name: data
            for name, data in (facts.get("interfaces") or {}).items()
            if self.selectors.matches_interface(name)
        }
        arp = [
            entry
            for entry in (facts.get("arp") or [])
            if self.selectors.matches_interface(str(entry.get("interface", "")))
        ]
        bgp = {
            peer: data
            for peer, data in (facts.get("bgp") or {}).items()
            if peer in self.selectors.bgp_neighbors
        }
        evpn_vpws = {
            name: data
            for name, data in (facts.get("evpn_vpws") or {}).items()
            if name in self.selectors.routing_instances
        }
        evpn_esi = {
            esi: data
            for esi, data in (facts.get("evpn_esi") or {}).items()
            if self.selectors.matches_interface(str(data.get("interface", "")))
        }
        evpn_mac = {
            name: data
            for name, data in (facts.get("evpn_mac") or {}).items()
            if name in self.selectors.routing_instances
        }
        ping = [probe for probe in pings if probe.get("scope_id") == self.id]

        return {
            "interfaces": interfaces,
            "arp": arp,
            "bgp": bgp,
            "evpn_vpws": evpn_vpws,
            "evpn_esi": evpn_esi,
            "evpn_mac": evpn_mac,
            "ping": ping,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "key": self.key.to_dict() if self.key else None,
            "selectors": self.selectors.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scope":
        key = data.get("key")
        return cls(
            id=data["id"],
            kind=data["kind"],
            key=ScopeKey.from_dict(key) if key else None,
            selectors=Selectors.from_dict(data.get("selectors", {})),
        )


def _empty(area: str) -> Any:
    return [] if area == "arp" else {}


def device_scope() -> Scope:
    """Jediny scope pro rezim bez inventory - propousti vsechna data."""
    return Scope(id=DEVICE_SCOPE_ID, kind="device", key=None, selectors=Selectors())
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/models/test_scope.py -v`
Expected: PASS, 9 testů

- [ ] **Step 5: Commit**

```bash
git add migration_validator/models/scope.py tests/models/test_scope.py
git commit -m "feat: add scope model with fact selection"
```

---

### Task 5: Model snapshotu a JSON round-trip

**Files:**
- Create: `migration_validator/models/snapshot.py`
- Test: `tests/models/test_snapshot.py`

**Interfaces:**
- Consumes: `Scope` (Task 4), `ServiceEntry` (Task 3)
- Produces:
  - `DeviceMeta(address, hostname=None, platform="junos", model=None, version=None, uptime_seconds=None)`
  - `CaptureMeta(started_at, finished_at=None, phase=None, collectors=dict)`
  - `CaptureMeta.failed_collectors() -> dict[str, str]` — jméno → chybová hláška
  - `Snapshot(device, capture, facts, probes, scopes=[], inventory=None, schema_version=1)`
  - `Snapshot.to_dict()`, `Snapshot.from_dict()`
  - `save_snapshot(snapshot, path)`, `load_snapshot(path) -> Snapshot`
  - `SnapshotVersionError(Exception)`
  - `SCHEMA_VERSION = 1`

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/models/test_snapshot.py`:

```python
import json

import pytest

from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import (
    CaptureMeta,
    DeviceMeta,
    Snapshot,
    SnapshotVersionError,
    load_snapshot,
    save_snapshot,
)


def _snapshot() -> Snapshot:
    return Snapshot(
        device=DeviceMeta(address="172.20.20.4", hostname="MX1-POP1", platform="junos"),
        capture=CaptureMeta(
            started_at="2026-07-24T09:12:03Z",
            finished_at="2026-07-24T09:12:41Z",
            phase="pre-migration",
            collectors={
                "interfaces": {"status": "ok"},
                "evpn_esi": {"status": "error", "message": "RpcError: syntax error"},
            },
        ),
        facts={"interfaces": {"ge-0/0/2.113": {"oper_status": "up"}}},
        probes={"ping": []},
        scopes=[
            Scope(
                id="svc:L3VPN-CPE13-NNI:IPVPN",
                kind="service",
                key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
                selectors=Selectors(interfaces=["ge-0/0/2.113"]),
            )
        ],
    )


def test_round_trip_through_dict():
    snapshot = _snapshot()
    assert Snapshot.from_dict(snapshot.to_dict()) == snapshot


def test_save_and_load(tmp_path):
    path = tmp_path / "snap.json"
    save_snapshot(_snapshot(), path)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["schema_version"] == 1
    assert written["device"]["platform"] == "junos"

    assert load_snapshot(path) == _snapshot()


def test_failed_collectors_lists_only_errors():
    failed = _snapshot().capture.failed_collectors()
    assert failed == {"evpn_esi": "RpcError: syntax error"}


def test_load_rejects_other_schema_version(tmp_path):
    path = tmp_path / "future.json"
    payload = _snapshot().to_dict()
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SnapshotVersionError, match="99"):
        load_snapshot(path)


def test_snapshot_without_inventory_has_empty_ping():
    snapshot = Snapshot(
        device=DeviceMeta(address="1.2.3.4"),
        capture=CaptureMeta(started_at="2026-07-24T09:00:00Z"),
        facts={},
        probes={"ping": []},
    )
    assert snapshot.inventory is None
    assert snapshot.probes["ping"] == []
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/models/test_snapshot.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.models.snapshot'`

- [ ] **Step 3: Implementuj model**

Vytvoř `migration_validator/models/snapshot.py`:

```python
"""Snapshot - zmrazeny stav zarizeni v jednom okamziku.

Snapshot je self-contained: evaluate k nemu nepotrebuje ani inventory,
ani pristup na sit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from migration_validator.models.inventory import ServiceEntry
from migration_validator.models.scope import Scope

SCHEMA_VERSION = 1


class SnapshotVersionError(Exception):
    """Snapshot ma jinou schema_version, nez nastroj umi zpracovat."""


@dataclass
class DeviceMeta:
    address: str
    hostname: str | None = None
    platform: str = "junos"  # junos | junos-evo
    model: str | None = None
    version: str | None = None
    uptime_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "hostname": self.hostname,
            "platform": self.platform,
            "model": self.model,
            "version": self.version,
            "uptime_seconds": self.uptime_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceMeta":
        return cls(
            address=data["address"],
            hostname=data.get("hostname"),
            platform=data.get("platform", "junos"),
            model=data.get("model"),
            version=data.get("version"),
            uptime_seconds=data.get("uptime_seconds"),
        )


@dataclass
class CaptureMeta:
    started_at: str
    finished_at: str | None = None
    phase: str | None = None
    collectors: dict[str, dict[str, Any]] = field(default_factory=dict)

    def failed_collectors(self) -> dict[str, str]:
        """Jmeno collectoru -> chybova hlaska, jen pro ty, ktere selhaly."""
        return {
            name: str(info.get("message", "neznama chyba"))
            for name, info in self.collectors.items()
            if info.get("status") != "ok"
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "phase": self.phase,
            "collectors": self.collectors,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CaptureMeta":
        return cls(
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            phase=data.get("phase"),
            collectors=data.get("collectors", {}),
        )


@dataclass
class Snapshot:
    device: DeviceMeta
    capture: CaptureMeta
    facts: dict[str, Any] = field(default_factory=dict)
    probes: dict[str, Any] = field(default_factory=lambda: {"ping": []})
    scopes: list[Scope] = field(default_factory=list)
    inventory: list[ServiceEntry] | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "device": self.device.to_dict(),
            "capture": self.capture.to_dict(),
            "inventory": (
                [entry.to_dict() for entry in self.inventory]
                if self.inventory is not None
                else None
            ),
            "scopes": [scope.to_dict() for scope in self.scopes],
            "facts": self.facts,
            "probes": self.probes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Snapshot":
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise SnapshotVersionError(
                f"snapshot ma schema_version {version}, nastroj umi {SCHEMA_VERSION}"
            )
        inventory = data.get("inventory")
        return cls(
            device=DeviceMeta.from_dict(data["device"]),
            capture=CaptureMeta.from_dict(data["capture"]),
            facts=data.get("facts", {}),
            probes=data.get("probes", {"ping": []}),
            scopes=[Scope.from_dict(item) for item in data.get("scopes", [])],
            inventory=(
                [ServiceEntry.from_dict(item) for item in inventory]
                if inventory is not None
                else None
            ),
            schema_version=version,
        )


def save_snapshot(snapshot: Snapshot, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_snapshot(path: str | Path) -> Snapshot:
    return Snapshot.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/models/test_snapshot.py -v`
Expected: PASS, 5 testů

- [ ] **Step 5: Commit**

```bash
git add migration_validator/models/snapshot.py tests/models/test_snapshot.py
git commit -m "feat: add snapshot model with JSON round-trip"
```

---

### Task 6: Stavba scopů z inventory

**Files:**
- Create: `migration_validator/scoping/__init__.py`
- Create: `migration_validator/scoping/builder.py`
- Test: `tests/scoping/test_builder.py`

**Interfaces:**
- Consumes: `Inventory`, `ServiceEntry` (Task 3), `Scope`, `ScopeKey`, `Selectors` (Task 4)
- Produces:
  - `MIGRATED_SERVICE_TYPES: frozenset[str]` = `{"Internet", "IPVPN", "E-Line", "E-LAN", "Core"}`
  - `MANAGEMENT_PREFIXES: tuple[str, ...]`
  - `is_management(interface: str) -> bool`
  - `build_scopes(inventory: Inventory) -> list[Scope]`

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/scoping/__init__.py` (prázdný) a `tests/scoping/test_builder.py`:

```python
import pytest

from migration_validator.models.inventory import Inventory, ServiceEntry
from migration_validator.scoping.builder import build_scopes, is_management


def _entry(**kwargs) -> ServiceEntry:
    return ServiceEntry.from_dict(kwargs)


@pytest.mark.parametrize(
    "interface,expected",
    [
        ("fxp0.0", True),
        ("em0", True),
        ("me0.0", True),
        ("vme.0", True),
        ("bme0", True),
        ("re0:mgmt-0.0", True),
        ("re1:mgmt-0.0", True),
        ("ge-0/0/2.113", False),
        ("et-0/0/8.13", False),
        ("ae0.14", False),
        ("irb.14", False),
        ("lo0.0", False),
    ],
)
def test_is_management(interface, expected):
    assert is_management(interface) is expected


def test_layer1_does_not_become_a_scope_but_feeds_physical_selector():
    inventory = Inventory(
        device="172.20.20.4",
        entries=[
            _entry(interface="ge-0/0/2", description="NNI1-TO-CPE1", service_type="Layer1"),
            _entry(
                interface="ge-0/0/2.113",
                description="L3VPN-CPE13-NNI",
                service_type="IPVPN",
                routing_instance="L3VPN-CPE13-NNI",
                bgp_neighbor=["198.11.13.2"],
                ip_address=["198.11.13.1/30"],
                customer_vlan=["113"],
            ),
        ],
    )

    scopes = build_scopes(inventory)

    assert len(scopes) == 1
    scope = scopes[0]
    assert scope.id == "svc:L3VPN-CPE13-NNI:IPVPN"
    assert scope.selectors.interfaces == ["ge-0/0/2.113"]
    assert scope.selectors.physical_interfaces == ["ge-0/0/2"]
    assert scope.selectors.bgp_neighbors == ["198.11.13.2"]
    assert scope.selectors.routing_instances == ["L3VPN-CPE13-NNI"]


def test_management_interfaces_are_excluded_even_when_typed_internet():
    inventory = Inventory(
        device="172.20.20.5",
        entries=[
            _entry(interface="fxp0.0", service_type="Internet", ip_address=["10.0.0.15/24"]),
            _entry(
                interface="re0:mgmt-0.0",
                service_type="Internet",
                ip_address=["172.20.20.5/24"],
            ),
        ],
    )

    assert build_scopes(inventory) == []


def test_core_and_loopback_produce_scopes():
    inventory = Inventory(
        device="172.20.20.4",
        entries=[
            _entry(interface="lo0.0", service_type="Core", ip_address=["150.0.0.11/32"]),
            _entry(
                interface="ge-0/0/0.0",
                description="clab-pop-migration-P1;et-0/0/0",
                service_type="Core",
                protocol=["inet", "iso", "mpls"],
            ),
        ],
    )

    scopes = build_scopes(inventory)

    assert [scope.key.service_type for scope in scopes] == ["Core", "Core"]
    assert scopes[0].id == "svc:lo0.0:Core"  # bez description se pouzije nazev rozhrani


def test_duplicate_key_gets_interface_suffix():
    inventory = Inventory(
        device="172.20.20.4",
        entries=[
            _entry(interface="ge-0/0/2.13", description="SAME", service_type="Internet"),
            _entry(interface="ge-0/0/3.13", description="SAME", service_type="Internet"),
        ],
    )

    scopes = build_scopes(inventory)

    assert [scope.id for scope in scopes] == [
        "svc:SAME:Internet:ge-0/0/2.13",
        "svc:SAME:Internet:ge-0/0/3.13",
    ]


def test_irb_virtual_gw_lands_in_selectors():
    inventory = Inventory(
        device="172.20.20.5",
        entries=[
            _entry(
                interface="irb.14",
                description="EVPN-VLAN-AWARE-INTERNET",
                service_type="Internet",
                ip_address=["152.11.14.2/29"],
                virtual_gw_ip_address=["152.11.14.1"],
                bgp_neighbor=["152.11.14.4"],
            )
        ],
    )

    scope = build_scopes(inventory)[0]

    assert scope.selectors.virtual_gw == ["152.11.14.1"]
    assert scope.selectors.local_addresses == ["152.11.14.2/29"]


def test_real_inventory_files_produce_expected_scope_counts():
    from migration_validator.models.inventory import load_inventory

    for path in ("172.20.20.4.yml", "172.20.20.5.yml"):
        scopes = build_scopes(load_inventory(path))
        types = {scope.key.service_type for scope in scopes}
        assert "Layer1" not in types
        assert not any(
            scope.selectors.interfaces[0].startswith(("fxp", "re0:mgmt"))
            for scope in scopes
        )
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/scoping/test_builder.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.scoping'`

- [ ] **Step 3: Implementuj builder**

Vytvoř `migration_validator/scoping/__init__.py` (prázdný) a `migration_validator/scoping/builder.py`:

```python
"""Stavba scopu z inventory.

Zpusobilost pro scope je jina vrstva nez klasifikace rozhrani v checcich:
tady se rozhoduje "je to vubec migrovana sluzba", tam "ma smysl na tom merit".
"""

from __future__ import annotations

from collections import Counter

from migration_validator.models.inventory import Inventory, ServiceEntry
from migration_validator.models.scope import Scope, ScopeKey, Selectors

MIGRATED_SERVICE_TYPES = frozenset({"Internet", "IPVPN", "E-Line", "E-LAN", "Core"})

MANAGEMENT_PREFIXES = ("fxp", "em", "me", "vme", "bme", "re0:mgmt-", "re1:mgmt-")


def is_management(interface: str) -> bool:
    """Management rozhrani se nikdy nestane service scopem."""
    physical = interface.split(".", 1)[0]
    return physical.startswith(MANAGEMENT_PREFIXES)


def _key(entry: ServiceEntry) -> ScopeKey:
    return ScopeKey(
        description=entry.description,
        service_type=entry.service_type,
        service_subtype=entry.service_subtype,
    )


def _label(entry: ServiceEntry) -> str:
    return entry.description or entry.interface


def _is_eligible(entry: ServiceEntry) -> bool:
    return entry.service_type in MIGRATED_SERVICE_TYPES and not is_management(entry.interface)


def build_scopes(inventory: Inventory) -> list[Scope]:
    """Vytvori jeden scope pro kazdy zaznam migrovaneho typu sluzby.

    Zaznamy Layer1 se scopem nestanou - slouzi jen jako potvrzeni, ze
    rodicovske fyzicke rozhrani v inventory existuje, a doplni se do
    selektoru logicke jednotky.
    """
    physical_names = {
        entry.interface for entry in inventory.entries if entry.service_type == "Layer1"
    }
    eligible = [entry for entry in inventory.entries if _is_eligible(entry)]

    key_counts = Counter(_key(entry) for entry in eligible)

    scopes: list[Scope] = []
    for entry in eligible:
        key = _key(entry)
        scope_id = f"svc:{_label(entry)}:{entry.service_type}"
        if key_counts[key] > 1:
            scope_id = f"{scope_id}:{entry.interface}"

        parents = [entry.physical_name] if entry.physical_name in physical_names else []

        scopes.append(
            Scope(
                id=scope_id,
                kind="service",
                key=key,
                selectors=Selectors(
                    interfaces=[entry.interface],
                    physical_interfaces=parents,
                    routing_instances=(
                        [entry.routing_instance] if entry.routing_instance else []
                    ),
                    bgp_neighbors=list(entry.bgp_neighbor),
                    local_addresses=list(entry.ip_address),
                    virtual_gw=list(entry.virtual_gw_ip_address),
                    vlans=list(entry.customer_vlan),
                    bridge_domains=list(entry.bridge_domain),
                ),
            )
        )
    return scopes
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/scoping/test_builder.py -v`
Expected: PASS, 18 testů

- [ ] **Step 5: Commit**

```bash
git add migration_validator/scoping tests/scoping
git commit -m "feat: build service scopes from inventory with eligibility rules"
```

---

### Task 7: Model `mapping.yml`

**Files:**
- Create: `migration_validator/scoping/mapping.py`
- Test: `tests/scoping/test_mapping.py`

**Interfaces:**
- Consumes: `Scope` (Task 4)
- Produces:
  - `Selector(description=None, service_type=None, interface=None)` — frozen; `matches(scope: Scope) -> bool`; `from_dict()`
  - `MappingRule(baseline: Selector, subject: Selector, note: str | None = None)`
  - `Mapping(mappings: list[MappingRule], ignore: list[Selector])`; `is_ignored(scope) -> bool`
  - `load_mapping(path) -> Mapping`
  - `empty_mapping() -> Mapping`

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/scoping/test_mapping.py`:

```python
import textwrap

import pytest

from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.scoping.mapping import (
    Selector,
    empty_mapping,
    load_mapping,
)


def _scope(description, service_type, interface) -> Scope:
    return Scope(
        id=f"svc:{description}:{service_type}",
        kind="service",
        key=ScopeKey(description, service_type, None),
        selectors=Selectors(interfaces=[interface]),
    )


def test_selector_matches_on_description_and_type():
    selector = Selector(description="EVPN-VLAN-AWARE-INTERNET", service_type="Internet")
    assert selector.matches(_scope("EVPN-VLAN-AWARE-INTERNET", "Internet", "ge-0/0/5.0"))
    assert not selector.matches(_scope("EVPN-VLAN-AWARE-INTERNET", "E-LAN", "ae0.14"))


def test_selector_matches_on_interface_alone():
    selector = Selector(interface="ge-0/0/4.0")
    assert selector.matches(_scope(None, "IPVPN", "ge-0/0/4.0"))
    assert not selector.matches(_scope(None, "IPVPN", "ge-0/0/5.0"))


def test_empty_selector_is_rejected():
    with pytest.raises(ValueError, match="prazdny selektor"):
        Selector.from_dict({})


def test_load_mapping(tmp_path):
    path = tmp_path / "mapping.yml"
    path.write_text(
        textwrap.dedent(
            """\
            mappings:
              - baseline: {description: EVPN-VLAN-AWARE-INTERNET, service_type: Internet}
                subject:  {description: EVPN-VLAN-AWARE-INTERNET, service_type: E-LAN}
                note: "sluzba restrukturalizovana"
              - baseline: {interface: "ge-0/0/4.0"}
                subject:  {interface: "et-0/0/10.0"}
            ignore:
              - {interface: "ge-0/0/7.0"}
            """
        ),
        encoding="utf-8",
    )

    mapping = load_mapping(path)

    assert len(mapping.mappings) == 2
    assert mapping.mappings[0].note == "sluzba restrukturalizovana"
    assert mapping.mappings[1].subject.interface == "et-0/0/10.0"
    assert mapping.is_ignored(_scope(None, "Internet", "ge-0/0/7.0")) is True
    assert mapping.is_ignored(_scope(None, "Internet", "ge-0/0/8.0")) is False


def test_empty_mapping_ignores_nothing():
    mapping = empty_mapping()
    assert mapping.mappings == []
    assert mapping.is_ignored(_scope("X", "Internet", "ge-0/0/1.0")) is False
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/scoping/test_mapping.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.scoping.mapping'`

- [ ] **Step 3: Implementuj mapping**

Vytvoř `migration_validator/scoping/mapping.py`:

```python
"""Rucni mapovani a ignorovani sluzeb - mapping.yml.

Management rozhrani se sem psat nemusi, ta jsou vyloucena uz ve builderu.
Tenhle soubor je pro pripady specificke pro danou migraci.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from migration_validator.models.scope import Scope


@dataclass(frozen=True)
class Selector:
    description: str | None = None
    service_type: str | None = None
    interface: str | None = None

    def matches(self, scope: Scope) -> bool:
        if self.description is not None:
            if scope.key is None or scope.key.description != self.description:
                return False
        if self.service_type is not None:
            if scope.key is None or scope.key.service_type != self.service_type:
                return False
        if self.interface is not None:
            if self.interface not in scope.selectors.interfaces:
                return False
        return True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Selector":
        selector = cls(
            description=data.get("description"),
            service_type=data.get("service_type"),
            interface=data.get("interface"),
        )
        if (
            selector.description is None
            and selector.service_type is None
            and selector.interface is None
        ):
            raise ValueError(
                "prazdny selektor v mapping.yml - uved description, service_type nebo interface"
            )
        return selector


@dataclass
class MappingRule:
    baseline: Selector
    subject: Selector
    note: str | None = None


@dataclass
class Mapping:
    mappings: list[MappingRule] = field(default_factory=list)
    ignore: list[Selector] = field(default_factory=list)

    def is_ignored(self, scope: Scope) -> bool:
        return any(selector.matches(scope) for selector in self.ignore)


def empty_mapping() -> Mapping:
    return Mapping()


def load_mapping(path: str | Path) -> Mapping:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping, nalezeno {type(raw).__name__}")

    rules = []
    for item in raw.get("mappings") or []:
        if "baseline" not in item or "subject" not in item:
            raise ValueError(f"{path}: pravidlo mapovani musi mit 'baseline' i 'subject'")
        rules.append(
            MappingRule(
                baseline=Selector.from_dict(item["baseline"]),
                subject=Selector.from_dict(item["subject"]),
                note=item.get("note"),
            )
        )

    ignore = [Selector.from_dict(item) for item in raw.get("ignore") or []]
    return Mapping(mappings=rules, ignore=ignore)
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/scoping/test_mapping.py -v`
Expected: PASS, 5 testů

- [ ] **Step 5: Commit**

```bash
git add migration_validator/scoping/mapping.py tests/scoping/test_mapping.py
git commit -m "feat: add mapping.yml model for manual service mapping"
```

---

### Task 8: Párování scopů mezi zařízeními

**Files:**
- Create: `migration_validator/scoping/matcher.py`
- Test: `tests/scoping/test_matcher.py`

**Interfaces:**
- Consumes: `Scope` (Task 4), `Mapping`, `empty_mapping` (Task 7)
- Produces:
  - `MatchedPair(baseline: Scope, subject: Scope, method: str, confidence: str)`
  - `UnmatchedScope(scope: Scope, reason: str)`
  - `MatchSet(pairs, unmatched_baseline, unmatched_subject)`
  - `match_scopes(baseline: list[Scope], subject: list[Scope], mapping: Mapping | None = None) -> MatchSet`

**Pravidla (pořadí a hlášená metoda):**

| metoda | confidence | podmínka |
|---|---|---|
| `manual` | `manual` | pravidlo z `mapping.yml` |
| `description+service_type+service_subtype` | `high` | description i subtype vyplněné |
| `description+service_type` | `high` | description vyplněná |
| `routing_instance+service_type` | `medium` | routing instance vyplněná |
| `subnet+service_type` | `medium` | síťová adresa z `local_addresses` |
| `vlan+service_type` | `low` | `vlans` neprázdné |

Pár vzniká **jen když daný klíč odpovídá právě jednomu scope na každé straně.** Jinak jde o nejednoznačnost a oba scopy se z dalších pravidel vyřadí.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/scoping/test_matcher.py`:

```python
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.scoping.mapping import Mapping, MappingRule, Selector
from migration_validator.scoping.matcher import match_scopes


def _scope(
    interface,
    description=None,
    service_type="Internet",
    subtype=None,
    routing_instance=None,
    addresses=(),
    vlans=(),
):
    label = description or interface
    return Scope(
        id=f"svc:{label}:{service_type}",
        kind="service",
        key=ScopeKey(description, service_type, subtype),
        selectors=Selectors(
            interfaces=[interface],
            routing_instances=[routing_instance] if routing_instance else [],
            local_addresses=list(addresses),
            vlans=list(vlans),
        ),
    )


def test_matches_on_description_and_service_type():
    baseline = [_scope("ge-0/0/2.113", "L3VPN-CPE13-NNI", "IPVPN")]
    subject = [_scope("et-0/0/8.113", "L3VPN-CPE13-NNI", "IPVPN")]

    result = match_scopes(baseline, subject)

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.method == "description+service_type"
    assert pair.confidence == "high"
    assert pair.subject.selectors.interfaces == ["et-0/0/8.113"]
    assert result.unmatched_baseline == []
    assert result.unmatched_subject == []


def test_subtype_is_used_when_present():
    baseline = [_scope("ge-0/0/2.313", "EVPN-AWARE", "E-LAN", subtype="vlan-aware")]
    subject = [_scope("et-0/0/8.313", "EVPN-AWARE", "E-LAN", subtype="vlan-aware")]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "description+service_type+service_subtype"


def test_falls_back_to_routing_instance_when_description_missing():
    baseline = [_scope("ge-0/0/4.0", None, "IPVPN", routing_instance="L3VPN-CPE14-UNI")]
    subject = [_scope("et-0/0/10.0", None, "IPVPN", routing_instance="L3VPN-CPE14-UNI")]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "routing_instance+service_type"
    assert pair.confidence == "medium"


def test_falls_back_to_subnet():
    baseline = [_scope("ge-0/0/9.0", None, "Internet", addresses=["10.5.5.1/30"])]
    subject = [_scope("et-0/0/9.0", None, "Internet", addresses=["10.5.5.1/30"])]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "subnet+service_type"


def test_falls_back_to_vlan():
    baseline = [_scope("ge-0/0/9.7", None, "Internet", vlans=["7"])]
    subject = [_scope("et-0/0/9.7", None, "Internet", vlans=["7"])]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "vlan+service_type"
    assert pair.confidence == "low"


def test_ambiguity_never_guesses():
    baseline = [_scope("ge-0/0/2.13", "SAME", "Internet")]
    subject = [
        _scope("et-0/0/8.13", "SAME", "Internet"),
        _scope("et-0/0/9.13", "SAME", "Internet"),
    ]

    result = match_scopes(baseline, subject)

    assert result.pairs == []
    assert len(result.unmatched_baseline) == 1
    assert "ambiguous" in result.unmatched_baseline[0].reason
    assert len(result.unmatched_subject) == 2


def test_unmatched_reasons_are_distinct():
    baseline = [_scope("ge-0/0/2.13", "ONLY-OLD", "Internet")]
    subject = [_scope("et-0/0/8.14", "ONLY-NEW", "E-LAN", subtype="vlan-aware")]

    result = match_scopes(baseline, subject)

    assert result.pairs == []
    assert result.unmatched_baseline[0].reason == "zadny kandidat na subject"
    assert result.unmatched_subject[0].reason == "nova sluzba, chybi baseline"


def test_manual_mapping_wins_over_automatic_rules():
    baseline = [_scope("ge-0/0/5.0", "EVPN-VLAN-AWARE-INTERNET", "Internet")]
    subject = [
        _scope("ae0.14", "EVPN-VLAN-AWARE-INTERNET", "E-LAN", subtype="vlan-aware"),
    ]
    mapping = Mapping(
        mappings=[
            MappingRule(
                baseline=Selector(
                    description="EVPN-VLAN-AWARE-INTERNET", service_type="Internet"
                ),
                subject=Selector(
                    description="EVPN-VLAN-AWARE-INTERNET", service_type="E-LAN"
                ),
            )
        ]
    )

    result = match_scopes(baseline, subject, mapping)

    assert len(result.pairs) == 1
    assert result.pairs[0].method == "manual"
    assert result.pairs[0].confidence == "manual"


def test_ignored_scopes_are_dropped_from_both_sides():
    baseline = [
        _scope("ge-0/0/2.13", "KEEP", "Internet"),
        _scope("ge-0/0/7.0", "DROP", "Internet"),
    ]
    subject = [
        _scope("et-0/0/8.13", "KEEP", "Internet"),
        _scope("et-0/0/7.0", "DROP", "Internet"),
    ]
    mapping = Mapping(ignore=[Selector(description="DROP")])

    result = match_scopes(baseline, subject, mapping)

    assert len(result.pairs) == 1
    assert result.pairs[0].baseline.key.description == "KEEP"
    assert result.unmatched_baseline == []
    assert result.unmatched_subject == []


def test_ambiguous_under_one_key_is_not_paired_under_a_sibling_key():
    """Vicehodnotove selektory: scope zahozeny jako nejednoznacny pod jednim
    klicem se nesmi sparovat pod jinym klicem tehoz pravidla."""
    baseline = [_scope("ge-0/0/9.7", None, "Internet", vlans=["7", "8"])]
    subject = [
        _scope("et-0/0/1.7", None, "Internet", vlans=["7"]),
        _scope("et-0/0/2.7", None, "Internet", vlans=["7"]),
        _scope("et-0/0/3.8", None, "Internet", vlans=["8"]),
    ]

    result = match_scopes(baseline, subject)

    paired_ids = {pair.baseline.id for pair in result.pairs}
    unmatched_ids = {item.scope.id for item in result.unmatched_baseline}
    assert not (paired_ids & unmatched_ids), "scope je zaroven sparovany i nesparovany"
    assert result.pairs == []
    assert "ambiguous" in result.unmatched_baseline[0].reason


def test_different_service_type_never_matches_automatically():
    baseline = [_scope("ge-0/0/5.0", "SAME-NAME", "Internet")]
    subject = [_scope("ae0.14", "SAME-NAME", "E-LAN", subtype="vlan-aware")]

    result = match_scopes(baseline, subject)

    assert result.pairs == []
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/scoping/test_matcher.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.scoping.matcher'`

- [ ] **Step 3: Implementuj matcher**

Vytvoř `migration_validator/scoping/matcher.py`:

```python
"""Parovani scopu mezi baseline a subject snapshotem.

Klicove pravidlo: pri nejednoznacnosti se nikdy nehada. Tichy spatny match
by u migrace znamenal zelenou na rozbite sluzbe.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Callable, Hashable

from migration_validator.models.scope import Scope
from migration_validator.scoping.mapping import Mapping, empty_mapping

REASON_NO_CANDIDATE = "zadny kandidat na subject"
REASON_NEW_SERVICE = "nova sluzba, chybi baseline"


@dataclass
class MatchedPair:
    baseline: Scope
    subject: Scope
    method: str
    confidence: str


@dataclass
class UnmatchedScope:
    scope: Scope
    reason: str


@dataclass
class MatchSet:
    pairs: list[MatchedPair] = field(default_factory=list)
    unmatched_baseline: list[UnmatchedScope] = field(default_factory=list)
    unmatched_subject: list[UnmatchedScope] = field(default_factory=list)


KeyFn = Callable[[Scope], list[Hashable]]


def _keys_description_subtype(scope: Scope) -> list[Hashable]:
    key = scope.key
    if key is None or key.description is None or key.service_subtype is None:
        return []
    return [(key.description, key.service_type, key.service_subtype)]


def _keys_description(scope: Scope) -> list[Hashable]:
    key = scope.key
    if key is None or key.description is None:
        return []
    return [(key.description, key.service_type)]


def _keys_routing_instance(scope: Scope) -> list[Hashable]:
    key = scope.key
    if key is None:
        return []
    return [
        (instance, key.service_type) for instance in scope.selectors.routing_instances
    ]


def _keys_subnet(scope: Scope) -> list[Hashable]:
    key = scope.key
    if key is None:
        return []
    keys: list[Hashable] = []
    for address in scope.selectors.local_addresses:
        try:
            network = ipaddress.ip_interface(address).network
        except ValueError:
            continue
        keys.append((str(network), key.service_type))
    return keys


def _keys_vlan(scope: Scope) -> list[Hashable]:
    key = scope.key
    if key is None:
        return []
    return [(vlan, key.service_type) for vlan in scope.selectors.vlans]


RULES: list[tuple[str, str, KeyFn]] = [
    ("description+service_type+service_subtype", "high", _keys_description_subtype),
    ("description+service_type", "high", _keys_description),
    ("routing_instance+service_type", "medium", _keys_routing_instance),
    ("subnet+service_type", "medium", _keys_subnet),
    ("vlan+service_type", "low", _keys_vlan),
]


def _index(scopes: list[Scope], key_fn: KeyFn) -> dict[Hashable, list[Scope]]:
    index: dict[Hashable, list[Scope]] = {}
    for scope in scopes:
        for key in key_fn(scope):
            index.setdefault(key, []).append(scope)
    return index


def _ambiguity_reason(candidates: list[Scope]) -> str:
    ids = ", ".join(scope.id for scope in candidates)
    return f"ambiguous: {len(candidates)} kandidatu ({ids})"


def _apply_manual(
    mapping: Mapping,
    baseline: list[Scope],
    subject: list[Scope],
    result: MatchSet,
) -> tuple[list[Scope], list[Scope]]:
    remaining_baseline = list(baseline)
    remaining_subject = list(subject)

    for rule in mapping.mappings:
        b_hits = [scope for scope in remaining_baseline if rule.baseline.matches(scope)]
        s_hits = [scope for scope in remaining_subject if rule.subject.matches(scope)]
        if len(b_hits) == 1 and len(s_hits) == 1:
            result.pairs.append(
                MatchedPair(
                    baseline=b_hits[0], subject=s_hits[0], method="manual", confidence="manual"
                )
            )
            remaining_baseline.remove(b_hits[0])
            remaining_subject.remove(s_hits[0])
            continue
        if len(b_hits) > 1:
            for scope in b_hits:
                result.unmatched_baseline.append(
                    UnmatchedScope(scope, _ambiguity_reason(b_hits))
                )
                remaining_baseline.remove(scope)
        if len(s_hits) > 1:
            for scope in s_hits:
                result.unmatched_subject.append(
                    UnmatchedScope(scope, _ambiguity_reason(s_hits))
                )
                remaining_subject.remove(scope)

    return remaining_baseline, remaining_subject


def match_scopes(
    baseline: list[Scope],
    subject: list[Scope],
    mapping: Mapping | None = None,
) -> MatchSet:
    mapping = mapping or empty_mapping()

    remaining_baseline = [scope for scope in baseline if not mapping.is_ignored(scope)]
    remaining_subject = [scope for scope in subject if not mapping.is_ignored(scope)]

    result = MatchSet()
    remaining_baseline, remaining_subject = _apply_manual(
        mapping, remaining_baseline, remaining_subject, result
    )

    for method, confidence, key_fn in RULES:
        baseline_index = _index(remaining_baseline, key_fn)
        subject_index = _index(remaining_subject, key_fn)

        paired: set[int] = set()
        dropped: set[int] = set()

        for key, b_hits in baseline_index.items():
            s_hits = subject_index.get(key)
            if not s_hits:
                continue
            if len(b_hits) == 1 and len(s_hits) == 1:
                # Kontroluje se paired I dropped: subnet a vlan pravidla generuji
                # vic klicu na scope, takze scope zahozeny jako nejednoznacny pod
                # jednim klicem by se pod jinym klicem tehoz pravidla jinak
                # sparoval - a skoncil by zaroven v pairs i v unmatched.
                if (
                    id(b_hits[0]) in paired
                    or id(s_hits[0]) in paired
                    or id(b_hits[0]) in dropped
                    or id(s_hits[0]) in dropped
                ):
                    continue
                result.pairs.append(
                    MatchedPair(
                        baseline=b_hits[0],
                        subject=s_hits[0],
                        method=method,
                        confidence=confidence,
                    )
                )
                paired.update({id(b_hits[0]), id(s_hits[0])})
                continue

            reason = _ambiguity_reason(s_hits if len(s_hits) > 1 else b_hits)
            for scope in b_hits:
                if id(scope) not in dropped and id(scope) not in paired:
                    result.unmatched_baseline.append(UnmatchedScope(scope, reason))
                    dropped.add(id(scope))
            for scope in s_hits:
                if id(scope) not in dropped and id(scope) not in paired:
                    result.unmatched_subject.append(UnmatchedScope(scope, reason))
                    dropped.add(id(scope))

        remaining_baseline = [
            scope
            for scope in remaining_baseline
            if id(scope) not in paired and id(scope) not in dropped
        ]
        remaining_subject = [
            scope
            for scope in remaining_subject
            if id(scope) not in paired and id(scope) not in dropped
        ]

    result.unmatched_baseline.extend(
        UnmatchedScope(scope, REASON_NO_CANDIDATE) for scope in remaining_baseline
    )
    result.unmatched_subject.extend(
        UnmatchedScope(scope, REASON_NEW_SERVICE) for scope in remaining_subject
    )
    return result
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/scoping/test_matcher.py -v`
Expected: PASS, 10 testů

- [ ] **Step 5: Ověř proti reálným datům**

```bash
.venv/bin/python -c "
from migration_validator.models.inventory import load_inventory
from migration_validator.scoping.builder import build_scopes
from migration_validator.scoping.matcher import match_scopes

old = build_scopes(load_inventory('172.20.20.4.yml'))
new = build_scopes(load_inventory('172.20.20.5.yml'))
result = match_scopes(old, new)

for pair in result.pairs:
    print(f'{pair.confidence:8} {pair.method:45} {pair.baseline.id}')
print('--- nesparovano baseline:', [u.scope.id for u in result.unmatched_baseline])
print('--- nesparovano subject :', [u.scope.id for u in result.unmatched_subject])
"
```

Expected: spáruje se mimo jiné `L3VPN-CPE13-NNI`, `INTERNET-CPE13-NNI`, `EVPN-VPWS-CPE13-NNI`, `EVPN-VLAN-AWARE-CPE13-NNI`, `EVPN-VLAN-BASED-CPE13-NNI` a přes `routing_instance` také `L3VPN-CPE14-UNI`. V `unmatched.subject` zůstane `ae0.14` (E-LAN, na starém boxu neexistovala).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/scoping/matcher.py tests/scoping/test_matcher.py
git commit -m "feat: match services across devices with fallback chain"
```

---

### Task 9: Konfigurace, základ checků a registry

**Files:**
- Create: `migration_validator/config.py`
- Create: `migration_validator/checks/__init__.py`
- Create: `migration_validator/checks/base.py`
- Create: `migration_validator/checks/registry.py`
- Test: `tests/checks/test_base.py`

**Interfaces:**
- Consumes: `Severity`, `Outcome`, `Finding`, `CheckResult`, `Status`, `derive_status` (Task 2), `Scope` (Task 4)
- Produces:
  - `config.CheckConfig` — `options(check_id) -> dict`, `severity(check_id, default) -> Severity`, `enabled(check_id) -> bool`
  - `config.default_config() -> CheckConfig`, `config.load_config(path) -> CheckConfig`, `config.DEFAULTS: dict`
  - `checks.base.Mode` (str Enum): `STATE`, `COMPARE`, `BOTH`
  - `checks.base.CheckContext(scope, subject, baseline, config, failed_collectors)` + `has_baseline: bool`
  - `checks.base.Check` ABC s atributy `id`, `title`, `mode`, `requires`, `requires_inventory`, `service_types`, `default_severity`; metody `applies_to(scope) -> bool`, `run(ctx) -> list[Finding]`, `describe() -> dict`
  - `checks.base.run_check(check: Check, ctx: CheckContext) -> list[CheckResult]`
  - `checks.registry.register(cls)`, `all_checks() -> list[Check]`, `get_check(id) -> Check`, `checks_for(scope, config) -> list[Check]`

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/checks/__init__.py` (prázdný) a `tests/checks/test_base.py`:

```python
import pytest

from migration_validator.config import CheckConfig, default_config
from migration_validator.checks.base import Check, CheckContext, Mode, run_check
from migration_validator.models.result import Finding, Outcome, Severity, Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope


class DummyCheck(Check):
    id = "dummy"
    title = "Dummy"
    mode = Mode.STATE
    requires = ("interfaces",)
    default_severity = Severity.CRITICAL

    def run(self, ctx):
        return [Finding(outcome=Outcome.BROKEN, message="rozbito")]


class CompareCheck(DummyCheck):
    id = "dummy_compare"
    mode = Mode.COMPARE


class InventoryCheck(DummyCheck):
    id = "dummy_inventory"
    requires_inventory = True


class TypedCheck(DummyCheck):
    id = "dummy_typed"
    service_types = frozenset({"IPVPN"})


class ExplodingCheck(DummyCheck):
    id = "dummy_boom"

    def run(self, ctx):
        raise RuntimeError("neco se pokazilo")


def _scope(service_type="Internet") -> Scope:
    return Scope(
        id=f"svc:X:{service_type}",
        kind="service",
        key=ScopeKey("X", service_type, None),
        selectors=Selectors(interfaces=["ge-0/0/1.0"]),
    )


def _ctx(**kwargs) -> CheckContext:
    defaults = dict(
        scope=_scope(),
        subject={"interfaces": {"ge-0/0/1.0": {}}},
        baseline=None,
        config=default_config(),
        failed_collectors={},
    )
    defaults.update(kwargs)
    return CheckContext(**defaults)


def test_broken_critical_is_fail():
    results = run_check(DummyCheck(), _ctx())
    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert results[0].id == "dummy"


def test_severity_override_from_config_turns_fail_into_warn():
    config = CheckConfig({"dummy": {"severity": "advisory"}})
    results = run_check(DummyCheck(), _ctx(config=config))
    assert results[0].status is Status.WARN


def test_disabled_check_produces_no_results():
    config = CheckConfig({"dummy": {"enabled": False}})
    assert run_check(DummyCheck(), _ctx(config=config)) == []


def test_compare_check_without_baseline_skips():
    results = run_check(CompareCheck(), _ctx())
    assert results[0].status is Status.SKIP
    assert "baseline" in results[0].message


def test_check_requiring_inventory_skips_on_device_scope():
    results = run_check(InventoryCheck(), _ctx(scope=device_scope()))
    assert results[0].status is Status.SKIP
    assert "inventory" in results[0].message


def test_failed_collector_skips_with_original_error():
    results = run_check(
        DummyCheck(), _ctx(failed_collectors={"interfaces": "RpcError: syntax error"})
    )
    assert results[0].status is Status.SKIP
    assert "RpcError: syntax error" in results[0].message


def test_check_not_applicable_to_service_type_produces_no_results():
    assert run_check(TypedCheck(), _ctx(scope=_scope("Internet"))) == []
    assert run_check(TypedCheck(), _ctx(scope=_scope("IPVPN"))) != []


def test_check_applies_to_device_scope_regardless_of_service_types():
    assert TypedCheck().applies_to(device_scope()) is True


def test_exception_in_check_becomes_skip_not_crash():
    results = run_check(ExplodingCheck(), _ctx())
    assert results[0].status is Status.SKIP
    assert "neco se pokazilo" in results[0].message


def test_missing_data_never_passes():
    """Kdyz collector selhal, vysledek nesmi byt PASS."""
    results = run_check(DummyCheck(), _ctx(failed_collectors={"interfaces": "timeout"}))
    assert results[0].status is not Status.PASS
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/checks/test_base.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.config'`

- [ ] **Step 3: Implementuj konfiguraci**

Vytvoř `migration_validator/config.py`:

```python
"""Konfigurace checku - tolerance a severity nejsou nikdy zadratovane."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from migration_validator.models.result import Severity

DEFAULTS: dict[str, dict[str, Any]] = {
    "interface_traffic": {"tolerance_percent": -60, "require_nonzero": True},
    "bgp_prefix_counts": {"tolerance_percent": -10},
    "evpn_mac_count": {"tolerance_percent": -60},
    "ping_reachability": {"count": 5},
    "traffic_ceased": {"enabled": False},
}


@dataclass
class CheckConfig:
    raw: dict[str, dict[str, Any]] = field(default_factory=dict)

    def options(self, check_id: str) -> dict[str, Any]:
        merged = dict(DEFAULTS.get(check_id, {}))
        merged.update(self.raw.get(check_id, {}))
        return merged

    def severity(self, check_id: str, default: Severity) -> Severity:
        value = self.options(check_id).get("severity")
        return Severity(value) if value else default

    def enabled(self, check_id: str) -> bool:
        return bool(self.options(check_id).get("enabled", True))


def default_config() -> CheckConfig:
    return CheckConfig()


def load_config(path: str | Path) -> CheckConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: ocekavan YAML mapping, nalezeno {type(raw).__name__}")
    return CheckConfig(raw.get("checks") or {})
```

- [ ] **Step 4: Implementuj základ checků**

Vytvoř `migration_validator/checks/__init__.py` (prázdný) a `migration_validator/checks/base.py`:

```python
"""Zaklad checku.

Check nevraci status primo - vraci Finding a status z nej odvodi framework.
Diky tomu je pravidlo "castecny uspech = WARN" na jednom miste a autor
noveho checku ho nemuze omylem porusit.

Checky nikdy nesahaji na sit. Tento modul nesmi importovat nic z
migration_validator.connection.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

from migration_validator.config import CheckConfig
from migration_validator.models.result import (
    CheckResult,
    Finding,
    Outcome,
    Severity,
    derive_status,
)
from migration_validator.models.scope import Scope


class Mode(str, Enum):
    STATE = "state"
    COMPARE = "compare"
    BOTH = "both"


@dataclass
class CheckContext:
    scope: Scope
    subject: dict[str, Any]
    baseline: dict[str, Any] | None
    config: CheckConfig
    failed_collectors: dict[str, str] = field(default_factory=dict)

    @property
    def has_baseline(self) -> bool:
        return self.baseline is not None

    def options(self, check_id: str) -> dict[str, Any]:
        return self.config.options(check_id)


class Check(ABC):
    id: ClassVar[str]
    title: ClassVar[str]
    mode: ClassVar[Mode] = Mode.STATE
    requires: ClassVar[tuple[str, ...]] = ()
    requires_inventory: ClassVar[bool] = False
    service_types: ClassVar[frozenset[str] | None] = None
    default_severity: ClassVar[Severity] = Severity.ADVISORY

    def applies_to(self, scope: Scope) -> bool:
        """Device scope dostane vsechny checky - filtrovat nema podle ceho."""
        if scope.is_device:
            return True
        if self.service_types is None:
            return True
        return scope.service_type in self.service_types

    @abstractmethod
    def run(self, ctx: CheckContext) -> list[Finding]:
        """Vrati namerene vysledky. Status odvodi run_check()."""

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "mode": self.mode.value,
            "requires": list(self.requires),
            "requires_inventory": self.requires_inventory,
            "service_types": (
                sorted(self.service_types) if self.service_types else None
            ),
            "default_severity": self.default_severity.value,
        }


def _skip(check: Check, severity: Severity, message: str) -> list[CheckResult]:
    return [
        CheckResult(
            id=check.id,
            mode=check.mode.value,
            status=derive_status(Outcome.SKIP, severity),
            severity=severity,
            message=message,
        )
    ]


def run_check(check: Check, ctx: CheckContext) -> list[CheckResult]:
    """Spusti check a prevede jeho Findings na CheckResults."""
    if not ctx.config.enabled(check.id):
        return []
    if not check.applies_to(ctx.scope):
        return []

    severity = ctx.config.severity(check.id, check.default_severity)

    if check.requires_inventory and ctx.scope.is_device:
        return _skip(check, severity, "check vyzaduje inventory, snapshot ji neobsahuje")

    if check.mode is Mode.COMPARE and not ctx.has_baseline:
        return _skip(check, severity, "porovnavaci check bez baseline snapshotu")

    for area in check.requires:
        if area in ctx.failed_collectors:
            return _skip(
                check,
                severity,
                f"chybi data z collectoru '{area}': {ctx.failed_collectors[area]}",
            )

    try:
        findings = check.run(ctx)
    except Exception as error:  # noqa: BLE001 - jeden rozbity check nesmi zabit cely beh
        return _skip(check, severity, f"check selhal: {error}")

    return [
        CheckResult(
            id=check.id,
            mode=check.mode.value,
            status=derive_status(finding.outcome, severity),
            severity=severity,
            message=finding.message,
            label=finding.label,
            baseline=finding.baseline,
            subject=finding.subject,
            details=finding.details,
        )
        for finding in findings
    ]
```

- [ ] **Step 5: Implementuj registry**

Vytvoř `migration_validator/checks/registry.py`:

```python
"""Registr checku - jediny zdroj pravdy pro CLI i GUI."""

from __future__ import annotations

from migration_validator.checks.base import Check
from migration_validator.config import CheckConfig
from migration_validator.models.scope import Scope

_REGISTRY: dict[str, Check] = {}


def register(cls: type[Check]) -> type[Check]:
    """Dekorator pro registraci checku."""
    if cls.id in _REGISTRY:
        raise ValueError(f"check '{cls.id}' je uz registrovany")
    _REGISTRY[cls.id] = cls()
    return cls


def all_checks() -> list[Check]:
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def get_check(check_id: str) -> Check:
    if check_id not in _REGISTRY:
        raise KeyError(f"neznamy check '{check_id}'")
    return _REGISTRY[check_id]


def checks_for(scope: Scope, config: CheckConfig) -> list[Check]:
    return [
        check
        for check in all_checks()
        if config.enabled(check.id) and check.applies_to(scope)
    ]
```

- [ ] **Step 6: Spusť test**

Run: `.venv/bin/pytest tests/checks/test_base.py -v`
Expected: PASS, 10 testů

- [ ] **Step 7: Commit**

```bash
git add migration_validator/config.py migration_validator/checks tests/checks
git commit -m "feat: add check base, config and registry"
```

---

### Task 10: Interface checky a klasifikace rozhraní

**Files:**
- Create: `migration_validator/checks/ifaces.py`
- Test: `tests/checks/test_ifaces.py`

**Interfaces:**
- Consumes: `Check`, `CheckContext`, `Mode`, `run_check` (Task 9), `register` (Task 9)
- Produces:
  - `TRANSIT_PREFIXES: tuple[str, ...]` = `("ge", "xe", "et", "ae")`
  - `INTERNAL_PREFIXES: tuple[str, ...]` — dokumentační seznam ze specu
  - `is_transit(interface: str) -> bool`
  - `InterfaceStateCheck` (`id="interface_state"`, `Mode.STATE`, `Severity.CRITICAL`)
  - `InterfaceErrorsCheck` (`id="interface_errors"`, `Mode.STATE`, `Severity.ADVISORY`)
  - `InterfaceTrafficCheck` (`id="interface_traffic"`, `Mode.BOTH`, `Severity.ADVISORY`)
  - `percent_change(old: float, new: float) -> float | None`

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/checks/test_ifaces.py`:

```python
import pytest

from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.ifaces import (
    InterfaceErrorsCheck,
    InterfaceStateCheck,
    InterfaceTrafficCheck,
    is_transit,
    percent_change,
)
from migration_validator.config import CheckConfig, default_config
from migration_validator.models.result import Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors


@pytest.mark.parametrize(
    "interface,expected",
    [
        ("ge-0/0/2.113", True),
        ("xe-1/0/0", True),
        ("et-0/0/8.13", True),
        ("ae0.14", True),
        ("lo0.0", False),
        ("irb.14", False),
        ("fxp0.0", False),
        ("re0:mgmt-0.0", False),
        ("gre-0/0/0", False),
        ("esi", False),
        ("vtep.1", False),
    ],
)
def test_is_transit(interface, expected):
    assert is_transit(interface) is expected


def _ctx(subject, baseline=None, interfaces=("ge-0/0/2.113",), config=None):
    scope = Scope(
        id="svc:X:Internet",
        kind="service",
        key=ScopeKey("X", "Internet", None),
        selectors=Selectors(interfaces=list(interfaces)),
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=baseline,
        config=config or default_config(),
        failed_collectors={},
    )


def test_interface_state_up_passes():
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"admin_status": "up", "oper_status": "up"}}})
    results = run_check(InterfaceStateCheck(), ctx)
    assert [r.status for r in results] == [Status.PASS]


def test_interface_state_down_fails():
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"admin_status": "up", "oper_status": "down"}}})
    results = run_check(InterfaceStateCheck(), ctx)
    assert results[0].status is Status.FAIL
    assert "down" in results[0].message


def test_interface_state_without_data_skips():
    results = run_check(InterfaceStateCheck(), _ctx({"interfaces": {}}))
    assert results[0].status is Status.SKIP


def test_errors_skipped_on_internal_interface():
    ctx = _ctx(
        {"interfaces": {"irb.14": {"input_errors": 0, "output_errors": 0}}},
        interfaces=("irb.14",),
    )
    results = run_check(InterfaceErrorsCheck(), ctx)
    assert results[0].status is Status.SKIP
    assert "tranzitni" in results[0].message


def test_errors_present_warns_on_transit_interface():
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"input_errors": 3, "output_errors": 0}}})
    results = run_check(InterfaceErrorsCheck(), ctx)
    assert results[0].status is Status.WARN
    assert "3" in results[0].message


def test_errors_zero_passes():
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"input_errors": 0, "output_errors": 0}}})
    assert run_check(InterfaceErrorsCheck(), ctx)[0].status is Status.PASS


@pytest.mark.parametrize(
    "old,new,expected",
    [(100, 40, -60.0), (100, 100, 0.0), (100, 150, 50.0), (0, 10, None)],
)
def test_percent_change(old, new, expected):
    assert percent_change(old, new) == expected


def test_traffic_state_mode_requires_nonzero():
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"input_pps": 0, "output_pps": 0}}})
    results = run_check(InterfaceTrafficCheck(), ctx)
    assert results[0].status is Status.WARN
    assert "netece" in results[0].message


def test_traffic_state_mode_passes_when_flowing():
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"input_pps": 412, "output_pps": 388}}})
    assert run_check(InterfaceTrafficCheck(), ctx)[0].status is Status.PASS


def test_traffic_compare_within_tolerance_passes():
    ctx = _ctx(
        subject={"interfaces": {"ge-0/0/2.113": {"input_pps": 398, "output_pps": 380}}},
        baseline={"interfaces": {"ge-0/0/2.113": {"input_pps": 412, "output_pps": 410}}},
    )
    assert run_check(InterfaceTrafficCheck(), ctx)[0].status is Status.PASS


def test_traffic_compare_below_tolerance_warns_and_reports_numbers():
    ctx = _ctx(
        subject={"interfaces": {"ge-0/0/2.113": {"input_pps": 398, "output_pps": 115}}},
        baseline={"interfaces": {"ge-0/0/2.113": {"input_pps": 412, "output_pps": 410}}},
    )
    result = run_check(InterfaceTrafficCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert "410" in result.message and "115" in result.message
    assert result.baseline == {"input_pps": 412, "output_pps": 410}
    assert result.subject == {"input_pps": 398, "output_pps": 115}
    assert result.details["tolerance_percent"] == -60


def test_traffic_tolerance_is_configurable():
    config = CheckConfig({"interface_traffic": {"tolerance_percent": -80}})
    ctx = _ctx(
        subject={"interfaces": {"ge-0/0/2.113": {"input_pps": 400, "output_pps": 115}}},
        baseline={"interfaces": {"ge-0/0/2.113": {"input_pps": 412, "output_pps": 410}}},
        config=config,
    )
    assert run_check(InterfaceTrafficCheck(), ctx)[0].status is Status.PASS


def test_traffic_skipped_on_internal_interface():
    ctx = _ctx(
        {"interfaces": {"lo0.0": {"input_pps": 0, "output_pps": 0}}},
        interfaces=("lo0.0",),
    )
    assert run_check(InterfaceTrafficCheck(), ctx)[0].status is Status.SKIP
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/checks/test_ifaces.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.checks.ifaces'`

- [ ] **Step 3: Implementuj checky**

Vytvoř `migration_validator/checks/ifaces.py`:

```python
"""Checky nad rozhranimi.

Counter-based checky bezi jen na tranzitnich rozhranich. Na internich davaji
SKIP, ne WARN - SKIP znamena "tenhle test sem nepatri", WARN "neco je spatne".
Kdyby interni rozhrani trvale svitila oranzove, operator si zvykne vystup
preskakovat.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

TRANSIT_PREFIXES = ("ge", "xe", "et", "ae")

INTERNAL_PREFIXES = (
    "fxp", "em", "me", "bme", "cbp", "pip", "tap", "jsrv", "esi", "vtep", "pp0",
    "lc-", "demux", "lsi", "mtun", "pime", "pimd", "gre", "ipip", "dsc", "pfe",
    "pfh", "vcp", "sxe", "vme", "fti", "lo0", "re0", "irb",
)


def is_transit(interface: str) -> bool:
    """Nese rozhrani zakaznicky provoz? Allowlist ge/xe/et/ae."""
    physical = interface.split(".", 1)[0]
    return physical.startswith(TRANSIT_PREFIXES)


def percent_change(old: float, new: float) -> float | None:
    """Zmena v procentech. None kdyz baseline byla nulova (delit nulou nelze)."""
    if not old:
        return None
    return (new - old) / old * 100.0


def _transit_interfaces(ctx: CheckContext) -> list[str]:
    return sorted(name for name in ctx.subject.get("interfaces", {}) if is_transit(name))


def _no_transit_finding(ctx: CheckContext) -> Finding:
    names = sorted(ctx.subject.get("interfaces", {}))
    listed = ", ".join(names) if names else "zadne rozhrani"
    return Finding(
        outcome=Outcome.SKIP,
        message=f"neni tranzitni rozhrani ({listed}), counter check se preskakuje",
    )


@register
class InterfaceStateCheck(Check):
    id = "interface_state"
    title = "Stav rozhrani"
    mode = Mode.STATE
    requires = ("interfaces",)
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        interfaces: dict[str, Any] = ctx.subject.get("interfaces", {})
        if not interfaces:
            return [Finding(Outcome.SKIP, "pro tento scope nejsou data o rozhranich")]

        findings = []
        for name in sorted(interfaces):
            data = interfaces[name]
            admin = str(data.get("admin_status", "unknown"))
            oper = str(data.get("oper_status", "unknown"))
            state = {"admin_status": admin, "oper_status": oper}
            if admin == "up" and oper == "up":
                findings.append(
                    Finding(Outcome.OK, f"{name}: up/up", label=name, subject=state)
                )
            else:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: admin {admin}, oper {oper}",
                        label=name,
                        subject=state,
                    )
                )
        return findings


@register
class InterfaceErrorsCheck(Check):
    id = "interface_errors"
    title = "Chybove countery rozhrani"
    mode = Mode.STATE
    requires = ("interfaces",)
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        names = _transit_interfaces(ctx)
        if not names:
            return [_no_transit_finding(ctx)]

        findings = []
        for name in names:
            data = ctx.subject["interfaces"][name]
            counters = {
                key: int(data.get(key, 0))
                for key in ("input_errors", "output_errors", "framing_errors")
                if key in data
            }
            total = sum(counters.values())
            if total == 0:
                findings.append(
                    Finding(Outcome.OK, f"{name}: bez chyb", label=name, subject=counters)
                )
            else:
                detail = ", ".join(f"{key}={value}" for key, value in counters.items() if value)
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: chybove countery nenulove ({detail})",
                        label=name,
                        subject=counters,
                    )
                )
        return findings


@register
class InterfaceTrafficCheck(Check):
    id = "interface_traffic"
    title = "Datovost rozhrani"
    mode = Mode.BOTH
    requires = ("interfaces",)
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        names = _transit_interfaces(ctx)
        if not names:
            return [_no_transit_finding(ctx)]

        options = ctx.options(self.id)
        tolerance = float(options["tolerance_percent"])
        require_nonzero = bool(options["require_nonzero"])

        findings = []
        for name in names:
            subject = _rates(ctx.subject["interfaces"][name])
            baseline_data = (ctx.baseline or {}).get("interfaces", {}).get(name)

            if baseline_data is None:
                findings.append(_state_finding(name, subject, require_nonzero))
                continue

            baseline = _rates(baseline_data)
            findings.append(_compare_finding(name, baseline, subject, tolerance))
        return findings


def _rates(data: dict[str, Any]) -> dict[str, int]:
    return {
        "input_pps": int(data.get("input_pps", 0)),
        "output_pps": int(data.get("output_pps", 0)),
    }


def _state_finding(name: str, subject: dict[str, int], require_nonzero: bool) -> Finding:
    if require_nonzero and (subject["input_pps"] == 0 or subject["output_pps"] == 0):
        return Finding(
            Outcome.BROKEN,
            f"{name}: provoz netece (in {subject['input_pps']} pps, "
            f"out {subject['output_pps']} pps)",
            label=name,
            subject=subject,
        )
    return Finding(
        Outcome.OK,
        f"{name}: provoz tece (in {subject['input_pps']} pps, "
        f"out {subject['output_pps']} pps)",
        label=name,
        subject=subject,
    )


def _compare_finding(
    name: str, baseline: dict[str, int], subject: dict[str, int], tolerance: float
) -> Finding:
    details = {"tolerance_percent": tolerance}
    drops = []
    for key in ("input_pps", "output_pps"):
        change = percent_change(baseline[key], subject[key])
        if change is None:
            continue
        details[f"{key}_change_percent"] = round(change, 1)
        if change < tolerance:
            drops.append(
                f"{key} kleslo o {abs(round(change))} % "
                f"({baseline[key]} -> {subject[key]})"
            )

    if drops:
        return Finding(
            Outcome.BROKEN,
            f"{name}: " + "; ".join(drops) + f", prah je {tolerance:.0f} %",
            label=name,
            baseline=baseline,
            subject=subject,
            details=details,
        )
    return Finding(
        Outcome.OK,
        f"{name}: provoz v toleranci {tolerance:.0f} %",
        label=name,
        baseline=baseline,
        subject=subject,
        details=details,
    )
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/checks/test_ifaces.py -v`
Expected: PASS, 24 testů

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/ifaces.py tests/checks/test_ifaces.py
git commit -m "feat: add interface state, errors and traffic checks"
```

---

### Task 11: ARP a ping checky

**Files:**
- Create: `migration_validator/checks/reachability.py`
- Test: `tests/checks/test_reachability.py`

**Interfaces:**
- Consumes: `Check`, `CheckContext`, `Mode`, `run_check`, `register` (Task 9)
- Produces:
  - `ArpPresentCheck` (`id="arp_present"`, `Mode.STATE`, `Severity.ADVISORY`, `service_types={"Internet","IPVPN"}`, `requires=("arp",)`, `requires_inventory=True`)
  - `PingReachabilityCheck` (`id="ping_reachability"`, `Mode.STATE`, `Severity.ADVISORY`, `service_types={"Internet","IPVPN"}`, `requires=("ping",)`, `requires_inventory=True`)

**Sémantika pingu:** všechny cíle odpověděly → `OK`; část odpověděla → `DEGRADED` (vždy WARN); žádný → `BROKEN`; žádné cíle ve snapshotu → `SKIP`.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/checks/test_reachability.py`:

```python
from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.reachability import ArpPresentCheck, PingReachabilityCheck
from migration_validator.config import default_config
from migration_validator.models.result import Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope


def _ctx(subject, service_type="IPVPN", scope=None):
    scope = scope or Scope(
        id="svc:X:" + service_type,
        kind="service",
        key=ScopeKey("X", service_type, None),
        selectors=Selectors(interfaces=["ge-0/0/2.113"]),
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=None,
        config=default_config(),
        failed_collectors={},
    )


def test_arp_present_passes_with_entries():
    ctx = _ctx({"arp": [{"ip": "198.11.13.2", "interface": "ge-0/0/2.113"}]})
    result = run_check(ArpPresentCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert "198.11.13.2" in result.message


def test_arp_empty_warns():
    result = run_check(ArpPresentCheck(), _ctx({"arp": []}))[0]
    assert result.status is Status.WARN


def test_arp_not_run_on_core_scope():
    assert run_check(ArpPresentCheck(), _ctx({"arp": []}, service_type="Core")) == []


def test_arp_skips_on_device_scope():
    ctx = _ctx({"arp": []}, scope=device_scope())
    result = run_check(ArpPresentCheck(), ctx)[0]
    assert result.status is Status.SKIP
    assert "inventory" in result.message


def test_ping_all_targets_reachable_passes():
    ctx = _ctx(
        {
            "ping": [
                {"target": "198.11.13.2", "sent": 5, "received": 5, "loss_percent": 0},
                {"target": "198.11.13.3", "sent": 5, "received": 5, "loss_percent": 0},
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert result.details["reachable"] == 2
    assert result.details["total"] == 2


def test_ping_partial_success_is_warn_with_per_target_detail():
    ctx = _ctx(
        {
            "ping": [
                {"target": "198.11.13.2", "sent": 5, "received": 5, "loss_percent": 0},
                {"target": "198.11.13.3", "sent": 5, "received": 0, "loss_percent": 100},
                {"target": "198.11.13.4", "sent": 5, "received": 5, "loss_percent": 0},
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert result.details["reachable"] == 2
    assert result.details["targets"]["198.11.13.3"]["received"] == 0
    assert "198.11.13.3" in result.message


def test_ping_no_target_reachable_warns_as_advisory():
    ctx = _ctx({"ping": [{"target": "198.11.13.2", "sent": 5, "received": 0}]})
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.WARN


def test_ping_without_targets_skips():
    result = run_check(PingReachabilityCheck(), _ctx({"ping": []}))[0]
    assert result.status is Status.SKIP
    assert "cile" in result.message


def test_ping_records_fallback_resolution():
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "198.11.13.2",
                    "sent": 5,
                    "received": 5,
                    "resolved_from": "subnet-fallback",
                }
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.details["targets"]["198.11.13.2"]["resolved_from"] == "subnet-fallback"
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/checks/test_reachability.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.checks.reachability'`

- [ ] **Step 3: Implementuj checky**

Vytvoř `migration_validator/checks/reachability.py`:

```python
"""ARP a ping checky.

Oba jsou vedome best-effort - CPE muze byt vypnute nebo blokovat ICMP -
proto default severity advisory. Cile pingu se resolvuji uz pri capture
(ARP -> ping), tady se ctou hotove vysledky ze snapshotu.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

CUSTOMER_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})


@register
class ArpPresentCheck(Check):
    id = "arp_present"
    title = "Existence ARP zaznamu"
    mode = Mode.STATE
    requires = ("arp",)
    requires_inventory = True
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        entries: list[dict[str, Any]] = ctx.subject.get("arp", [])
        addresses = [str(entry.get("ip")) for entry in entries if entry.get("ip")]

        if not addresses:
            return [
                Finding(
                    Outcome.BROKEN,
                    "na rozhranich sluzby neni zadny ARP zaznam",
                    subject={"count": 0, "addresses": []},
                )
            ]

        return [
            Finding(
                Outcome.OK,
                f"nalezeno {len(addresses)} ARP zaznamu: {', '.join(addresses)}",
                subject={"count": len(addresses), "addresses": addresses},
            )
        ]


@register
class PingReachabilityCheck(Check):
    id = "ping_reachability"
    title = "Dosazitelnost CPE pingem"
    mode = Mode.STATE
    requires = ("ping",)
    requires_inventory = True
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        probes: list[dict[str, Any]] = ctx.subject.get("ping", [])
        if not probes:
            return [
                Finding(
                    Outcome.SKIP,
                    "pro tento scope nejsou ve snapshotu zadne cile pingu",
                )
            ]

        targets: dict[str, dict[str, Any]] = {}
        unreachable: list[str] = []
        for probe in probes:
            target = str(probe.get("target"))
            received = int(probe.get("received", 0))
            targets[target] = {
                "sent": int(probe.get("sent", 0)),
                "received": received,
                "loss_percent": probe.get("loss_percent"),
                "resolved_from": probe.get("resolved_from"),
                "rtt_avg_ms": probe.get("rtt_avg_ms"),
            }
            if received == 0:
                unreachable.append(target)

        total = len(targets)
        reachable = total - len(unreachable)
        details = {"total": total, "reachable": reachable, "targets": targets}

        if reachable == total:
            return [
                Finding(
                    Outcome.OK,
                    f"vsech {total} cilu odpovedelo",
                    subject={"reachable": reachable, "total": total},
                    details=details,
                )
            ]

        if reachable == 0:
            return [
                Finding(
                    Outcome.BROKEN,
                    f"zadny z {total} cilu neodpovedel ({', '.join(unreachable)})",
                    subject={"reachable": 0, "total": total},
                    details=details,
                )
            ]

        return [
            Finding(
                Outcome.DEGRADED,
                f"odpovedelo {reachable} z {total} cilu, "
                f"neodpovedelo: {', '.join(unreachable)}",
                subject={"reachable": reachable, "total": total},
                details=details,
            )
        ]
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/checks/test_reachability.py -v`
Expected: PASS, 9 testů

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/reachability.py tests/checks/test_reachability.py
git commit -m "feat: add ARP and ping reachability checks"
```

---

### Task 12: BGP checky

**Files:**
- Create: `migration_validator/checks/bgp.py`
- Test: `tests/checks/test_bgp.py`

**Interfaces:**
- Consumes: `Check`, `CheckContext`, `Mode`, `register` (Task 9), `percent_change` (Task 10)
- Produces:
  - `BgpSessionStateCheck` (`id="bgp_session_state"`, `Mode.BOTH`, `Severity.CRITICAL`, `service_types={"Internet","IPVPN"}`, `requires=("bgp",)`)
  - `BgpPrefixCountsCheck` (`id="bgp_prefix_counts"`, `Mode.COMPARE`, `Severity.ADVISORY`, `service_types={"Internet","IPVPN"}`, `requires=("bgp",)`)

**Sémantika stavu:** subject není `Established` → `BROKEN` (FAIL). Subject je `Established`, ale baseline měl jiný stav → `DEGRADED` (WARN) — změna se nahlásí, ale zlepšení není FAIL. Oboje `Established` → `OK`.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/checks/test_bgp.py`:

```python
from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.bgp import BgpPrefixCountsCheck, BgpSessionStateCheck
from migration_validator.config import CheckConfig, default_config
from migration_validator.models.result import Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _ctx(subject, baseline=None, config=None):
    scope = Scope(
        id="svc:L3VPN-CPE13-NNI:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"], bgp_neighbors=["198.11.13.2"]
        ),
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=baseline,
        config=config or default_config(),
        failed_collectors={},
    )


def _peer(state="Established", received=14, accepted=14, advertised=3):
    return {
        "state": state,
        "routing_instance": "L3VPN-CPE13-NNI",
        "prefixes": {
            "received": received,
            "accepted": accepted,
            "advertised": advertised,
        },
    }


def test_established_passes():
    result = run_check(BgpSessionStateCheck(), _ctx({"bgp": {"198.11.13.2": _peer()}}))[0]
    assert result.status is Status.PASS
    assert result.label == "198.11.13.2"


def test_active_state_fails():
    ctx = _ctx({"bgp": {"198.11.13.2": _peer(state="Active")}})
    result = run_check(BgpSessionStateCheck(), ctx)[0]
    assert result.status is Status.FAIL
    assert "Active" in result.message


def test_no_bgp_peers_skips():
    result = run_check(BgpSessionStateCheck(), _ctx({"bgp": {}}))[0]
    assert result.status is Status.SKIP
    assert "BGP" in result.message


def test_state_change_from_active_to_established_is_warn_not_fail():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(state="Established")}},
        baseline={"bgp": {"198.11.13.2": _peer(state="Active")}},
    )
    result = run_check(BgpSessionStateCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert "Active" in result.message and "Established" in result.message


def test_peer_missing_in_baseline_is_evaluated_as_state_only():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer()}},
        baseline={"bgp": {}},
    )
    assert run_check(BgpSessionStateCheck(), ctx)[0].status is Status.PASS


def test_prefix_counts_within_tolerance_pass():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=13, accepted=13)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    result = run_check(BgpPrefixCountsCheck(), ctx)[0]
    assert result.status is Status.PASS


def test_prefix_counts_below_tolerance_warn():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=5, accepted=5)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    result = run_check(BgpPrefixCountsCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert "received" in result.message
    assert result.baseline["received"] == 14
    assert result.subject["received"] == 5


def test_prefix_tolerance_is_configurable():
    config = CheckConfig({"bgp_prefix_counts": {"tolerance_percent": -90}})
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=5, accepted=5)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
        config=config,
    )
    assert run_check(BgpPrefixCountsCheck(), ctx)[0].status is Status.PASS


def test_prefix_counts_without_baseline_skips():
    result = run_check(BgpPrefixCountsCheck(), _ctx({"bgp": {"198.11.13.2": _peer()}}))[0]
    assert result.status is Status.SKIP
    assert "baseline" in result.message


def test_prefix_counts_peer_missing_in_baseline_skips_that_peer():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer()}},
        baseline={"bgp": {}},
    )
    result = run_check(BgpPrefixCountsCheck(), ctx)[0]
    assert result.status is Status.SKIP
    assert "198.11.13.2" in result.message


def test_prefix_growth_is_not_a_problem():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=40, accepted=40)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    assert run_check(BgpPrefixCountsCheck(), ctx)[0].status is Status.PASS
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/checks/test_bgp.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.checks.bgp'`

- [ ] **Step 3: Implementuj checky**

Vytvoř `migration_validator/checks/bgp.py`:

```python
"""BGP checky.

Peer patri ke sluzbe pres bgp_neighbor z inventory - parser ho doplnuje na
zaklade shody se subnetem rozhrani, takze scope uz ma spravny seznam.

Pocty prefixu se porovnavaji s toleranci, ne 1:1. Presna shoda generuje
mnozstvi FAILu kvuli rozdilu nekolika rout, coz neni signifikantni.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.ifaces import percent_change
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

CUSTOMER_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})
ESTABLISHED = "Established"
PREFIX_KEYS = ("received", "accepted", "advertised")


@register
class BgpSessionStateCheck(Check):
    id = "bgp_session_state"
    title = "Stav BGP session"
    mode = Mode.BOTH
    requires = ("bgp",)
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        peers: dict[str, Any] = ctx.subject.get("bgp", {})
        if not peers:
            return [Finding(Outcome.SKIP, "sluzba nema zadne BGP peery")]

        baseline_peers = (ctx.baseline or {}).get("bgp", {})

        findings = []
        for peer in sorted(peers):
            state = str(peers[peer].get("state", "unknown"))
            subject = {"state": state}

            if state != ESTABLISHED:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{peer}: stav {state}, ocekavano {ESTABLISHED}",
                        label=peer,
                        subject=subject,
                    )
                )
                continue

            baseline_state = (
                str(baseline_peers[peer].get("state", "unknown"))
                if peer in baseline_peers
                else None
            )
            if baseline_state is not None and baseline_state != state:
                findings.append(
                    Finding(
                        Outcome.DEGRADED,
                        f"{peer}: stav se zmenil {baseline_state} -> {state}",
                        label=peer,
                        baseline={"state": baseline_state},
                        subject=subject,
                    )
                )
                continue

            findings.append(
                Finding(
                    Outcome.OK,
                    f"{peer}: {ESTABLISHED}",
                    label=peer,
                    baseline={"state": baseline_state} if baseline_state else None,
                    subject=subject,
                )
            )
        return findings


@register
class BgpPrefixCountsCheck(Check):
    id = "bgp_prefix_counts"
    title = "Pocty BGP prefixu"
    mode = Mode.COMPARE
    requires = ("bgp",)
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        peers: dict[str, Any] = ctx.subject.get("bgp", {})
        if not peers:
            return [Finding(Outcome.SKIP, "sluzba nema zadne BGP peery")]

        baseline_peers = (ctx.baseline or {}).get("bgp", {})
        tolerance = float(ctx.options(self.id)["tolerance_percent"])

        findings = []
        for peer in sorted(peers):
            if peer not in baseline_peers:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{peer}: peer neni v baseline snapshotu, nelze porovnat",
                        label=peer,
                    )
                )
                continue

            subject = _counts(peers[peer])
            baseline = _counts(baseline_peers[peer])
            details: dict[str, Any] = {"tolerance_percent": tolerance}
            drops = []

            for key in PREFIX_KEYS:
                change = percent_change(baseline[key], subject[key])
                if change is None:
                    continue
                details[f"{key}_change_percent"] = round(change, 1)
                if change < tolerance:
                    drops.append(f"{key} {baseline[key]} -> {subject[key]}")

            if drops:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{peer}: pokles prefixu ({'; '.join(drops)}), "
                        f"prah je {tolerance:.0f} %",
                        label=peer,
                        baseline=baseline,
                        subject=subject,
                        details=details,
                    )
                )
            else:
                findings.append(
                    Finding(
                        Outcome.OK,
                        f"{peer}: pocty prefixu v toleranci {tolerance:.0f} %",
                        label=peer,
                        baseline=baseline,
                        subject=subject,
                        details=details,
                    )
                )
        return findings


def _counts(peer: dict[str, Any]) -> dict[str, int]:
    prefixes = peer.get("prefixes", {})
    return {key: int(prefixes.get(key, 0)) for key in PREFIX_KEYS}
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/checks/test_bgp.py -v`
Expected: PASS, 11 testů

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/bgp.py tests/checks/test_bgp.py
git commit -m "feat: add BGP session state and prefix count checks"
```

---

### Task 13: EVPN checky

**Files:**
- Create: `migration_validator/checks/evpn.py`
- Test: `tests/checks/test_evpn.py`

**Interfaces:**
- Consumes: `Check`, `CheckContext`, `Mode`, `register` (Task 9), `percent_change` (Task 10)
- Produces:
  - `EvpnVpwsStatusCheck` (`id="evpn_vpws_status"`, `Mode.BOTH`, `Severity.CRITICAL`, `service_types={"E-Line"}`, `requires=("evpn_vpws",)`)
  - `EvpnEsiStatusCheck` (`id="evpn_esi_status"`, `Mode.BOTH`, `Severity.CRITICAL`, `service_types={"E-LAN"}`, `requires=("evpn_esi",)`)
  - `EvpnMacCountCheck` (`id="evpn_mac_count"`, `Mode.BOTH`, `Severity.ADVISORY`, `service_types={"E-LAN"}`, `requires=("evpn_mac",)`)

**Tvar faktů (produkuje Plán 2, tady se jen konzumuje):**

```python
facts["evpn_vpws"] = {"<instance>": {"local_sid": int, "remote_sid": int, "status": str}}
facts["evpn_esi"]  = {"<esi>": {"status": str, "df_role": str, "interface": str}}
facts["evpn_mac"]  = {"<instance>": {"<bridge_domain>": int}}
```

U `vlan-based` instance nemá vlastní bridge domain — collector v Plánu 2 v takovém případě použije jediný klíč `"-"` s počtem MAC celé instance, takže check nepotřebuje větev na subtype.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/checks/test_evpn.py`:

```python
from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.evpn import (
    EvpnEsiStatusCheck,
    EvpnMacCountCheck,
    EvpnVpwsStatusCheck,
)
from migration_validator.config import default_config
from migration_validator.models.result import Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _ctx(subject, baseline=None, service_type="E-LAN", subtype="vlan-aware"):
    scope = Scope(
        id=f"svc:SVC:{service_type}",
        kind="service",
        key=ScopeKey("SVC", service_type, subtype),
        selectors=Selectors(
            interfaces=["ge-0/0/2.313"], routing_instances=["EVPN-AWARE-CPE13"]
        ),
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=baseline,
        config=default_config(),
        failed_collectors={},
    )


def _vpws_ctx(subject, baseline=None):
    return _ctx(subject, baseline, service_type="E-Line", subtype="vpws")


def test_vpws_up_with_matching_sids_passes():
    ctx = _vpws_ctx(
        {"evpn_vpws": {"VPWS": {"local_sid": 213, "remote_sid": 213, "status": "Up"}}}
    )
    result = run_check(EvpnVpwsStatusCheck(), ctx)[0]
    assert result.status is Status.PASS


def test_vpws_down_fails():
    ctx = _vpws_ctx(
        {"evpn_vpws": {"VPWS": {"local_sid": 213, "remote_sid": 213, "status": "Down"}}}
    )
    result = run_check(EvpnVpwsStatusCheck(), ctx)[0]
    assert result.status is Status.FAIL
    assert "Down" in result.message


def test_vpws_sid_mismatch_fails():
    ctx = _vpws_ctx(
        {"evpn_vpws": {"VPWS": {"local_sid": 213, "remote_sid": 999, "status": "Up"}}}
    )
    result = run_check(EvpnVpwsStatusCheck(), ctx)[0]
    assert result.status is Status.FAIL
    assert "213" in result.message and "999" in result.message


def test_vpws_missing_data_skips():
    result = run_check(EvpnVpwsStatusCheck(), _vpws_ctx({"evpn_vpws": {}}))[0]
    assert result.status is Status.SKIP


def test_vpws_not_run_on_elan_scope():
    assert run_check(EvpnVpwsStatusCheck(), _ctx({"evpn_vpws": {}})) == []


def test_esi_up_passes_and_reports_df_role():
    ctx = _ctx({"evpn_esi": {"00:11": {"status": "Up", "df_role": "DF", "interface": "ae0"}}})
    result = run_check(EvpnEsiStatusCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert result.subject["df_role"] == "DF"


def test_esi_down_fails():
    ctx = _ctx({"evpn_esi": {"00:11": {"status": "Down", "df_role": "-", "interface": "ae0"}}})
    assert run_check(EvpnEsiStatusCheck(), ctx)[0].status is Status.FAIL


def test_esi_missing_data_skips():
    assert run_check(EvpnEsiStatusCheck(), _ctx({"evpn_esi": {}}))[0].status is Status.SKIP


def test_mac_count_nonzero_passes_per_bridge_domain():
    ctx = _ctx({"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 42, "BD-314": 7}}})
    results = run_check(EvpnMacCountCheck(), ctx)
    assert {r.label for r in results} == {"EVPN-AWARE-CPE13/BD-313", "EVPN-AWARE-CPE13/BD-314"}
    assert all(r.status is Status.PASS for r in results)


def test_mac_count_zero_warns():
    ctx = _ctx({"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 0}}})
    result = run_check(EvpnMacCountCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert "0" in result.message


def test_mac_count_drop_beyond_tolerance_warns():
    ctx = _ctx(
        subject={"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 11}}},
        baseline={"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 42}}},
    )
    result = run_check(EvpnMacCountCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert "42" in result.message and "11" in result.message


def test_mac_count_within_tolerance_passes():
    ctx = _ctx(
        subject={"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 40}}},
        baseline={"evpn_mac": {"EVPN-AWARE-CPE13": {"BD-313": 42}}},
    )
    assert run_check(EvpnMacCountCheck(), ctx)[0].status is Status.PASS


def test_mac_count_vlan_based_uses_single_placeholder_domain():
    ctx = _ctx(
        {"evpn_mac": {"EVPN-BASED-CPE13": {"-": 12}}},
        service_type="E-LAN",
        subtype="vlan-based",
    )
    result = run_check(EvpnMacCountCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert result.label == "EVPN-BASED-CPE13"
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/checks/test_evpn.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.checks.evpn'`

- [ ] **Step 3: Implementuj checky**

Vytvoř `migration_validator/checks/evpn.py`:

```python
"""EVPN checky pro E-Line (vpws) a E-LAN (vlan-aware, vlan-based).

Platformni rozdil MX (virtual-switch/bridge-domain) vs EVO (mac-vrf/VLAN)
resi collector - sem uz prichazi jednotne schema, takze tady neni zadna
vetev na platformu.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.ifaces import percent_change
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

UP = "Up"
NO_DOMAIN = "-"


@register
class EvpnVpwsStatusCheck(Check):
    id = "evpn_vpws_status"
    title = "Stav EVPN-VPWS"
    mode = Mode.BOTH
    requires = ("evpn_vpws",)
    service_types = frozenset({"E-Line"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_vpws", {})
        if not instances:
            return [Finding(Outcome.SKIP, "pro tento scope nejsou data evpn-vpws")]

        baseline_instances = (ctx.baseline or {}).get("evpn_vpws", {})

        findings = []
        for name in sorted(instances):
            data = instances[name]
            status = str(data.get("status", "unknown"))
            local = data.get("local_sid")
            remote = data.get("remote_sid")
            subject = {"status": status, "local_sid": local, "remote_sid": remote}
            baseline = baseline_instances.get(name)

            if status != UP:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: vpws-sid-pe-status {status}, ocekavano {UP}",
                        label=name,
                        baseline=baseline,
                        subject=subject,
                    )
                )
                continue

            if local != remote:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: local SID {local} neodpovida remote SID {remote}",
                        label=name,
                        baseline=baseline,
                        subject=subject,
                    )
                )
                continue

            findings.append(
                Finding(
                    Outcome.OK,
                    f"{name}: {UP}, SID {local}",
                    label=name,
                    baseline=baseline,
                    subject=subject,
                )
            )
        return findings


@register
class EvpnEsiStatusCheck(Check):
    id = "evpn_esi_status"
    title = "Stav EVPN ESI"
    mode = Mode.BOTH
    requires = ("evpn_esi",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        entries: dict[str, Any] = ctx.subject.get("evpn_esi", {})
        if not entries:
            return [Finding(Outcome.SKIP, "pro tento scope nejsou data evpn-esi")]

        baseline_entries = (ctx.baseline or {}).get("evpn_esi", {})

        findings = []
        for esi in sorted(entries):
            data = entries[esi]
            status = str(data.get("status", "unknown"))
            subject = {
                "status": status,
                "df_role": data.get("df_role"),
                "interface": data.get("interface"),
            }
            baseline = baseline_entries.get(esi)
            outcome = Outcome.OK if status == UP else Outcome.BROKEN
            message = (
                f"{esi}: {status}, DF role {subject['df_role']}"
                if outcome is Outcome.OK
                else f"{esi}: evpn-esi-status {status}, ocekavano {UP}"
            )
            findings.append(
                Finding(outcome, message, label=esi, baseline=baseline, subject=subject)
            )
        return findings


@register
class EvpnMacCountCheck(Check):
    id = "evpn_mac_count"
    title = "Pocet MAC adres"
    mode = Mode.BOTH
    requires = ("evpn_mac",)
    service_types = frozenset({"E-LAN"})
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        instances: dict[str, Any] = ctx.subject.get("evpn_mac", {})
        if not instances:
            return [Finding(Outcome.SKIP, "pro tento scope nejsou data o MAC adresach")]

        baseline_instances = (ctx.baseline or {}).get("evpn_mac", {})
        tolerance = float(ctx.options(self.id)["tolerance_percent"])

        findings = []
        for instance in sorted(instances):
            domains = instances[instance]
            for domain in sorted(domains):
                count = int(domains[domain])
                label = instance if domain == NO_DOMAIN else f"{instance}/{domain}"
                baseline_count = (
                    baseline_instances.get(instance, {}).get(domain)
                    if instance in baseline_instances
                    else None
                )

                if baseline_count is None:
                    findings.append(_mac_state_finding(label, count))
                    continue

                findings.append(
                    _mac_compare_finding(label, int(baseline_count), count, tolerance)
                )
        return findings


def _mac_state_finding(label: str, count: int) -> Finding:
    if count == 0:
        return Finding(
            Outcome.BROKEN,
            f"{label}: 0 naucenych MAC adres",
            label=label,
            subject={"mac_count": 0},
        )
    return Finding(
        Outcome.OK,
        f"{label}: {count} naucenych MAC adres",
        label=label,
        subject={"mac_count": count},
    )


def _mac_compare_finding(
    label: str, baseline: int, subject: int, tolerance: float
) -> Finding:
    change = percent_change(baseline, subject)
    details: dict[str, Any] = {"tolerance_percent": tolerance}
    if change is not None:
        details["change_percent"] = round(change, 1)

    if change is not None and change < tolerance:
        return Finding(
            Outcome.BROKEN,
            f"{label}: pocet MAC klesl {baseline} -> {subject}, "
            f"prah je {tolerance:.0f} %",
            label=label,
            baseline={"mac_count": baseline},
            subject={"mac_count": subject},
            details=details,
        )
    if subject == 0:
        return Finding(
            Outcome.BROKEN,
            f"{label}: 0 naucenych MAC adres (baseline {baseline})",
            label=label,
            baseline={"mac_count": baseline},
            subject={"mac_count": 0},
            details=details,
        )
    return Finding(
        Outcome.OK,
        f"{label}: {subject} MAC adres, v toleranci {tolerance:.0f} %",
        label=label,
        baseline={"mac_count": baseline},
        subject={"mac_count": subject},
        details=details,
    )
```

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/checks/test_evpn.py -v`
Expected: PASS, 13 testů

- [ ] **Step 5: Ověř, že registry vidí všech osm checků**

```bash
.venv/bin/python -c "
import migration_validator.checks.ifaces
import migration_validator.checks.reachability
import migration_validator.checks.bgp
import migration_validator.checks.evpn
from migration_validator.checks.registry import all_checks
for check in all_checks():
    print(f'{check.id:24} {check.mode.value:8} {check.default_severity.value}')
"
```

Expected: 10 řádků — `arp_present`, `bgp_prefix_counts`, `bgp_session_state`, `evpn_esi_status`, `evpn_mac_count`, `evpn_vpws_status`, `interface_errors`, `interface_state`, `interface_traffic`, `ping_reachability`.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/checks/evpn.py tests/checks/test_evpn.py
git commit -m "feat: add EVPN VPWS, ESI and MAC count checks"
```

---

### Task 14: Vyhodnocovací engine a API

**Files:**
- Create: `migration_validator/checks/all.py`
- Create: `migration_validator/engine.py`
- Create: `migration_validator/api.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `Snapshot` (Task 5), `device_scope` (Task 4), `match_scopes`, `MatchSet` (Task 8), `checks_for`, `run_check`, `CheckContext` (Task 9), všechny checky (Tasks 10–13)
- Produces:
  - `checks.all.load_all() -> None` — importuje všechny moduly s checky, aby se zaregistrovaly
  - `engine.evaluate_snapshots(subject, baseline, mapping, config, now) -> RunResult`
  - `api.evaluate(snapshot, *, baseline=None, mapping=None, config=None, now=None) -> RunResult`
  - `api.list_checks() -> list[dict]`

**Pravidla enginu:**
- Bez baseline: žádné párování, `match` je `None`, běží jen stavové checky (porovnávací dají `SKIP`).
- S baseline: spárované dvojice dostanou subject i baseline data. Nespárované subject scopy se **stále validují stavovými checky** — nová služba si validaci zaslouží — a mají `match.status = "unmatched"`.
- Nespárované baseline scopy se jen zapíšou do `unmatched.baseline`, checky se pro ně nespouští (zařízení už tu službu nemá).
- `unassigned.bgp_peers` = peeři v subject faktech, které nepokrývá žádný subject scope.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/test_engine.py`:

```python
import pytest

from migration_validator import api
from migration_validator.models.result import Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot

NOW = "2026-07-24T11:40:02Z"


def _scope(scope_id, description, service_type, interface, peers=()):
    return Scope(
        id=scope_id,
        kind="service",
        key=ScopeKey(description, service_type, None),
        selectors=Selectors(interfaces=[interface], bgp_neighbors=list(peers)),
    )


def _snapshot(address, interface, scopes, *, pps=400, peers=None, phase="pre-migration"):
    facts = {
        "interfaces": {
            interface: {
                "admin_status": "up",
                "oper_status": "up",
                "input_pps": pps,
                "output_pps": pps,
                "input_errors": 0,
                "output_errors": 0,
            }
        },
        "arp": [{"ip": "198.11.13.2", "interface": interface}],
        "bgp": peers if peers is not None else {},
    }
    return Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(
            started_at=NOW,
            finished_at=NOW,
            phase=phase,
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts=facts,
        probes={"ping": [{"scope_id": scopes[0].id, "target": "198.11.13.2",
                          "sent": 5, "received": 5}]},
        scopes=scopes,
        inventory=[],
    )


def _old():
    scopes = [_scope("svc:L3VPN:IPVPN", "L3VPN", "IPVPN", "ge-0/0/2.113")]
    return _snapshot("172.20.20.4", "ge-0/0/2.113", scopes)


def _new(pps=400, extra_scope=False):
    scopes = [_scope("svc:L3VPN:IPVPN", "L3VPN", "IPVPN", "et-0/0/8.113")]
    if extra_scope:
        scopes.append(_scope("svc:NOVA:E-LAN", "NOVA", "E-LAN", "ae0.14"))
    snapshot = _snapshot(
        "172.20.20.5", "et-0/0/8.113", scopes, pps=pps, phase="post-migration"
    )
    if extra_scope:
        snapshot.facts["interfaces"]["ae0.14"] = {
            "admin_status": "up",
            "oper_status": "up",
            "input_pps": 10,
            "output_pps": 10,
        }
    return snapshot


def test_evaluate_without_baseline_runs_state_checks_only():
    result = api.evaluate(_old(), now=NOW)

    assert result.baseline is None
    assert len(result.scopes) == 1
    scope = result.scopes[0]
    assert scope.match is None
    compare_only = [c for c in scope.checks if c.id == "bgp_prefix_counts"]
    assert all(c.status is Status.SKIP for c in compare_only)


def test_healthy_scope_without_baseline_is_pass_not_skip():
    """Compare-only check dava SKIP, ale zdrava sluzba musi svitit zelene."""
    result = api.evaluate(_old(), now=NOW)

    scope = result.scopes[0]
    assert any(check.status is Status.SKIP for check in scope.checks)
    assert scope.status is Status.PASS


def test_scope_is_skip_only_when_everything_skipped():
    subject = _old()
    subject.capture.collectors = {
        name: {"status": "error", "message": "RpcError: timeout"}
        for name in ("interfaces", "arp", "bgp", "evpn_vpws", "evpn_esi", "evpn_mac")
    }
    subject.probes = {"ping": []}  # bez cilu -> ping_reachability tez SKIP

    result = api.evaluate(subject, now=NOW)

    assert result.scopes[0].status is Status.SKIP


def test_evaluate_with_baseline_matches_and_compares():
    result = api.evaluate(_new(), baseline=_old(), now=NOW)

    assert result.summary["scopes_matched"] == 1
    scope = result.scopes[0]
    assert scope.match.status == "matched"
    assert scope.match.method == "description+service_type"
    assert scope.match.baseline_interfaces == ["ge-0/0/2.113"]
    assert scope.match.subject_interfaces == ["et-0/0/8.113"]


def test_traffic_drop_surfaces_as_warn_in_summary():
    result = api.evaluate(_new(pps=100), baseline=_old(), now=NOW)

    traffic = [c for c in result.scopes[0].checks if c.id == "interface_traffic"]
    assert traffic[0].status is Status.WARN
    assert result.summary["warn"] >= 1


def test_unmatched_subject_scope_is_still_state_validated():
    result = api.evaluate(_new(extra_scope=True), baseline=_old(), now=NOW)

    assert result.summary["unmatched_subject"] == 1
    new_scope = next(s for s in result.scopes if s.scope_id == "svc:NOVA:E-LAN")
    assert new_scope.match.status == "unmatched"
    assert any(c.id == "interface_state" for c in new_scope.checks)
    assert result.unmatched["subject"][0]["scope_id"] == "svc:NOVA:E-LAN"


def test_unmatched_baseline_scope_is_reported_without_checks():
    old = _old()
    old.scopes.append(_scope("svc:ZMIZELA:Internet", "ZMIZELA", "Internet", "ge-0/0/6.0"))

    result = api.evaluate(_new(), baseline=old, now=NOW)

    assert result.summary["unmatched_baseline"] == 1
    assert result.unmatched["baseline"][0]["scope_id"] == "svc:ZMIZELA:Internet"
    assert all(s.scope_id != "svc:ZMIZELA:Internet" for s in result.scopes)


def test_unassigned_bgp_peers_are_reported():
    peers = {"10.9.9.9": {"state": "Established", "routing_instance": None}}
    subject = _new()
    subject.facts["bgp"] = peers

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["bgp_peers"][0]["peer"] == "10.9.9.9"


def test_failed_collector_produces_skip_not_pass():
    subject = _new()
    subject.capture.collectors["interfaces"] = {
        "status": "error",
        "message": "RpcError: timeout",
    }

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    state = [c for c in result.scopes[0].checks if c.id == "interface_state"]
    assert state[0].status is Status.SKIP
    assert "RpcError: timeout" in state[0].message


def test_snapshot_without_scopes_falls_back_to_device_scope():
    subject = _new()
    subject.scopes = []
    subject.inventory = None

    result = api.evaluate(subject, now=NOW)

    assert len(result.scopes) == 1
    assert result.scopes[0].scope_id == "device"


def test_summary_counts_every_check():
    result = api.evaluate(_new(), baseline=_old(), now=NOW)
    counted = sum(
        result.summary[key] for key in ("pass", "warn", "fail", "skip")
    )
    total = sum(len(scope.checks) for scope in result.scopes)
    assert counted == total


def test_list_checks_exposes_registry():
    described = api.list_checks()
    ids = {item["id"] for item in described}
    assert "interface_traffic" in ids
    assert "evpn_esi_status" in ids
    assert all("mode" in item and "default_severity" in item for item in described)


def test_result_is_json_serialisable():
    import json

    payload = api.evaluate(_new(), baseline=_old(), now=NOW).to_dict()
    assert json.loads(json.dumps(payload, ensure_ascii=False))["schema_version"] == 1
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/test_engine.py -v`
Expected: FAIL s `ImportError: cannot import name 'api'`

- [ ] **Step 3: Implementuj načtení všech checků**

Vytvoř `migration_validator/checks/all.py`:

```python
"""Import vsech modulu s checky, aby se zaregistrovaly do registry.

Je to samostatny modul (ne __init__.py), aby nevznikl cyklicky import:
checks.ifaces importuje checks.base, takze __init__ nesmi importovat ifaces.
"""

from __future__ import annotations

from migration_validator.checks import bgp, evpn, ifaces, reachability  # noqa: F401

_LOADED = True


def load_all() -> None:
    """Idempotentni - import na urovni modulu uz probehl."""
    assert _LOADED
```

- [ ] **Step 4: Implementuj engine**

Vytvoř `migration_validator/engine.py`:

```python
"""Orchestrace vyhodnoceni snapshotu.

Engine nesaha na sit. Vsechna data pochazeji ze snapshotu.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from migration_validator.checks import all as _all_checks  # noqa: F401  (registrace)
from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.registry import all_checks
from migration_validator.config import CheckConfig, default_config
from migration_validator.models.result import (
    MatchInfo,
    RunResult,
    ScopeResult,
    Status,
)
from migration_validator.models.scope import Scope, device_scope
from migration_validator.models.snapshot import Snapshot
from migration_validator.scoping.mapping import Mapping, empty_mapping
from migration_validator.scoping.matcher import MatchedPair, match_scopes


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot_meta(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "address": snapshot.device.address,
        "phase": snapshot.capture.phase,
        "captured_at": snapshot.capture.finished_at or snapshot.capture.started_at,
    }


def _scopes_of(snapshot: Snapshot) -> list[Scope]:
    return snapshot.scopes if snapshot.scopes else [device_scope()]


def _unmatched_entry(scope: Scope, reason: str) -> dict[str, Any]:
    return {
        "scope_id": scope.id,
        "description": scope.key.description if scope.key else None,
        "service_type": scope.key.service_type if scope.key else None,
        "reason": reason,
    }


def _run_scope(
    scope: Scope,
    subject: Snapshot,
    baseline_scope: Scope | None,
    baseline: Snapshot | None,
    config: CheckConfig,
    match: MatchInfo | None,
) -> ScopeResult:
    subject_data = scope.select(subject.facts, subject.probes)
    baseline_data = (
        baseline_scope.select(baseline.facts, baseline.probes)
        if baseline_scope is not None and baseline is not None
        else None
    )
    ctx = CheckContext(
        scope=scope,
        subject=subject_data,
        baseline=baseline_data,
        config=config,
        failed_collectors=subject.capture.failed_collectors(),
    )

    results = []
    for check in all_checks():
        results.extend(run_check(check, ctx))

    # SKIP vyhrava jen kdyz neni co lepsiho hlasit. Bez teto podminky by
    # zdrava IPVPN sluzba v rezimu bez baseline svitila SKIP jen proto, ze
    # bgp_prefix_counts je compare-only - a operator by prisel o zeleny
    # signal prave u sluzeb s nejvic kontrolami.
    reported = [result.status for result in results if result.status is not Status.SKIP]
    status = Status.worst(reported) if reported else Status.SKIP

    return ScopeResult(
        scope_id=scope.id,
        key=scope.key.to_dict() if scope.key else {},
        status=status,
        match=match,
        checks=results,
    )


def _match_info(pair: MatchedPair) -> MatchInfo:
    return MatchInfo(
        status="matched",
        method=pair.method,
        confidence=pair.confidence,
        baseline_interfaces=list(pair.baseline.selectors.interfaces),
        subject_interfaces=list(pair.subject.selectors.interfaces),
    )


def _unassigned_bgp_peers(subject: Snapshot, scopes: list[Scope]) -> list[dict[str, Any]]:
    assigned = {peer for scope in scopes for peer in scope.selectors.bgp_neighbors}
    if any(scope.is_device for scope in scopes):
        return []
    return [
        {
            "peer": peer,
            "routing_instance": data.get("routing_instance"),
            "snapshot": "subject",
        }
        for peer, data in sorted((subject.facts.get("bgp") or {}).items())
        if peer not in assigned
    ]


def evaluate_snapshots(
    subject: Snapshot,
    baseline: Snapshot | None = None,
    mapping: Mapping | None = None,
    config: CheckConfig | None = None,
    now: str | None = None,
) -> RunResult:
    config = config or default_config()
    mapping = mapping or empty_mapping()

    subject_scopes = _scopes_of(subject)
    scope_results: list[ScopeResult] = []
    unmatched: dict[str, list[dict[str, Any]]] = {"baseline": [], "subject": []}
    matched_count = 0

    if baseline is None:
        for scope in subject_scopes:
            scope_results.append(_run_scope(scope, subject, None, None, config, None))
    else:
        matches = match_scopes(_scopes_of(baseline), subject_scopes, mapping)
        matched_count = len(matches.pairs)

        for pair in matches.pairs:
            scope_results.append(
                _run_scope(
                    pair.subject, subject, pair.baseline, baseline, config, _match_info(pair)
                )
            )

        for item in matches.unmatched_subject:
            scope_results.append(
                _run_scope(
                    item.scope,
                    subject,
                    None,
                    None,
                    config,
                    MatchInfo(
                        status="unmatched",
                        reason=item.reason,
                        subject_interfaces=list(item.scope.selectors.interfaces),
                    ),
                )
            )
            unmatched["subject"].append(_unmatched_entry(item.scope, item.reason))

        for item in matches.unmatched_baseline:
            unmatched["baseline"].append(_unmatched_entry(item.scope, item.reason))

    summary = {
        "pass": 0,
        "warn": 0,
        "fail": 0,
        "skip": 0,
        "scopes_matched": matched_count,
        "unmatched_baseline": len(unmatched["baseline"]),
        "unmatched_subject": len(unmatched["subject"]),
    }
    for scope_result in scope_results:
        for check in scope_result.checks:
            summary[check.status.value.lower()] += 1

    return RunResult(
        evaluated_at=now or _now(),
        subject=_snapshot_meta(subject),
        baseline=_snapshot_meta(baseline) if baseline else None,
        summary=summary,
        scopes=scope_results,
        unmatched=unmatched,
        unassigned={"bgp_peers": _unassigned_bgp_peers(subject, subject_scopes)},
    )
```

- [ ] **Step 5: Implementuj API**

Vytvoř `migration_validator/api.py`:

```python
"""Programove API - jediny sev, ktery bude volat GUI.

CLI je tenky obal nad timto modulem, ne alternativni implementace.
capture() doplni Plan 2.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.all import load_all
from migration_validator.checks.registry import all_checks
from migration_validator.config import CheckConfig
from migration_validator.engine import evaluate_snapshots
from migration_validator.models.result import RunResult
from migration_validator.models.snapshot import Snapshot
from migration_validator.scoping.mapping import Mapping


def evaluate(
    snapshot: Snapshot,
    *,
    baseline: Snapshot | None = None,
    mapping: Mapping | None = None,
    config: CheckConfig | None = None,
    now: str | None = None,
) -> RunResult:
    """Vyhodnoti snapshot, volitelne proti baseline snapshotu."""
    load_all()
    return evaluate_snapshots(
        subject=snapshot, baseline=baseline, mapping=mapping, config=config, now=now
    )


def list_checks() -> list[dict[str, Any]]:
    """Popis vsech registrovanych checku - pro CLI i GUI."""
    load_all()
    return [check.describe() for check in all_checks()]
```

- [ ] **Step 6: Spusť test**

Run: `.venv/bin/pytest tests/test_engine.py -v`
Expected: PASS, 13 testů

- [ ] **Step 7: Spusť celou sadu**

Run: `.venv/bin/pytest -q`
Expected: všechny testy PASS

- [ ] **Step 8: Commit**

```bash
git add migration_validator/checks/all.py migration_validator/engine.py \
        migration_validator/api.py tests/test_engine.py
git commit -m "feat: add evaluation engine and public API"
```

---

### Task 15: Reporting — JSON a terminálová tabulka

**Files:**
- Create: `migration_validator/reporting/__init__.py`
- Create: `migration_validator/reporting/json_report.py`
- Create: `migration_validator/reporting/text_report.py`
- Test: `tests/reporting/test_text_report.py`

**Interfaces:**
- Consumes: `RunResult`, `ScopeResult`, `Status` (Task 2)
- Produces:
  - `json_report.to_json(result: RunResult, *, indent: int = 2) -> str`
  - `json_report.write_json(result: RunResult, path: str | Path) -> None`
  - `text_report.filter_result(result, *, text: str | None = None, statuses: set[Status] | None = None) -> RunResult`
  - `text_report.render(result: RunResult) -> str`

**Pravidlo:** sekce `NESPAROVANO` se vypisuje **vždy**, i když je všechno ostatní zelené — je to hlavní pojistka proti tichému přehlédnutí nezmigrované služby. Filtrování se na ni nevztahuje.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/reporting/__init__.py` (prázdný) a `tests/reporting/test_text_report.py`:

```python
import json

from migration_validator.models.result import (
    CheckResult,
    MatchInfo,
    RunResult,
    ScopeResult,
    Severity,
    Status,
)
from migration_validator.reporting.json_report import to_json
from migration_validator.reporting.text_report import filter_result, render


def _check(check_id, status, message):
    return CheckResult(
        id=check_id,
        mode="both",
        status=status,
        severity=Severity.ADVISORY,
        message=message,
    )


def _result() -> RunResult:
    return RunResult(
        evaluated_at="2026-07-24T11:40:02Z",
        subject={"address": "172.20.20.5", "phase": "post-migration", "captured_at": "x"},
        baseline={"address": "172.20.20.4", "phase": "pre-migration", "captured_at": "y"},
        summary={
            "pass": 3, "warn": 1, "fail": 1, "skip": 0,
            "scopes_matched": 2, "unmatched_baseline": 1, "unmatched_subject": 1,
        },
        scopes=[
            ScopeResult(
                scope_id="svc:INTERNET-CPE13-NNI:Internet",
                key={"description": "INTERNET-CPE13-NNI", "service_type": "Internet"},
                status=Status.PASS,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[_check("interface_state", Status.PASS, "up/up")],
            ),
            ScopeResult(
                scope_id="svc:L3VPN-CPE13-NNI:IPVPN",
                key={"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN"},
                status=Status.WARN,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[
                    _check("interface_traffic", Status.WARN, "provoz -72 % (410 -> 115 pps)"),
                    _check("interface_state", Status.PASS, "up/up"),
                ],
            ),
            ScopeResult(
                scope_id="svc:EVPN-VPWS-CPE13-NNI:E-Line",
                key={"description": "EVPN-VPWS-CPE13-NNI", "service_type": "E-Line"},
                status=Status.FAIL,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[_check("evpn_vpws_status", Status.FAIL, "vpws-sid-pe-status: Down")],
            ),
        ],
        unmatched={
            "baseline": [
                {
                    "scope_id": "svc:L3VPN-CPE99-NNI:IPVPN",
                    "description": "L3VPN-CPE99-NNI",
                    "service_type": "IPVPN",
                    "reason": "zadny kandidat na subject",
                }
            ],
            "subject": [
                {
                    "scope_id": "svc:EVPN-VLAN-AWARE-INTERNET:E-LAN",
                    "description": "EVPN-VLAN-AWARE-INTERNET",
                    "service_type": "E-LAN",
                    "reason": "nova sluzba, chybi baseline",
                }
            ],
        },
        unassigned={"bgp_peers": []},
    )


def test_render_contains_header_with_both_devices():
    output = render(_result())
    assert "172.20.20.4" in output and "172.20.20.5" in output
    assert "pre-migration" in output and "post-migration" in output


def test_render_contains_summary_counts():
    output = render(_result())
    assert "3 PASS" in output
    assert "1 WARN" in output
    assert "1 FAIL" in output
    assert "Sparovano 2" in output


def test_render_lists_services_with_worst_check_message():
    output = render(_result())
    assert "L3VPN-CPE13-NNI" in output
    assert "provoz -72 %" in output
    assert "vpws-sid-pe-status: Down" in output


def test_render_always_shows_unmatched_section():
    output = render(_result())
    assert "NESPAROVANO" in output
    assert "L3VPN-CPE99-NNI" in output
    assert "EVPN-VLAN-AWARE-INTERNET" in output


def test_unmatched_section_present_even_when_all_green():
    result = _result()
    for scope in result.scopes:
        scope.status = Status.PASS
    assert "NESPAROVANO" in render(result)


def test_filter_by_text_matches_description():
    filtered = filter_result(_result(), text="L3VPN")
    assert [scope.scope_id for scope in filtered.scopes] == ["svc:L3VPN-CPE13-NNI:IPVPN"]


def test_filter_by_status_keeps_only_requested():
    filtered = filter_result(_result(), statuses={Status.FAIL, Status.WARN})
    assert {scope.status for scope in filtered.scopes} == {Status.WARN, Status.FAIL}


def test_filter_does_not_touch_unmatched():
    filtered = filter_result(_result(), text="NEEXISTUJE")
    assert filtered.scopes == []
    assert len(filtered.unmatched["baseline"]) == 1
    assert len(filtered.unmatched["subject"]) == 1


def test_to_json_is_valid_and_keeps_czech_characters():
    result = _result()
    result.scopes[0].checks[0].message = "rozhrani je v poradku"
    payload = json.loads(to_json(result))
    assert payload["schema_version"] == 1
    assert payload["scopes"][0]["checks"][0]["message"] == "rozhrani je v poradku"
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/reporting/test_text_report.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.reporting'`

- [ ] **Step 3: Implementuj JSON report**

Vytvoř `migration_validator/reporting/__init__.py` (prázdný) a `migration_validator/reporting/json_report.py`:

```python
"""Serializace vysledku do JSON."""

from __future__ import annotations

import json
from pathlib import Path

from migration_validator.models.result import RunResult


def to_json(result: RunResult, *, indent: int = 2) -> str:
    return json.dumps(result.to_dict(), indent=indent, ensure_ascii=False)


def write_json(result: RunResult, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_json(result) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Implementuj textový report**

Vytvoř `migration_validator/reporting/text_report.py`:

```python
"""Terminalovy vystup.

Sekce NESPAROVANO se vypisuje vzdy, i kdyz je vsechno ostatni zelene, a
filtrovani se na ni nevztahuje - je to hlavni pojistka proti prehlednuti
nezmigrovane sluzby.
"""

from __future__ import annotations

from dataclasses import replace

from migration_validator.models.result import RunResult, ScopeResult, Status

SYMBOL = {
    Status.PASS: "OK ",
    Status.WARN: "WARN",
    Status.FAIL: "FAIL",
    Status.SKIP: "SKIP",
}

_STATUS_ORDER = (Status.FAIL, Status.WARN, Status.SKIP, Status.PASS)


def filter_result(
    result: RunResult,
    *,
    text: str | None = None,
    statuses: set[Status] | None = None,
) -> RunResult:
    """Vrati kopii vysledku s profiltrovanymi scopy. Unmatched zustava cely."""
    scopes = list(result.scopes)

    if text:
        needle = text.lower()
        scopes = [
            scope
            for scope in scopes
            if needle in scope.scope_id.lower()
            or needle in str(scope.key.get("description", "")).lower()
        ]

    if statuses:
        scopes = [scope for scope in scopes if scope.status in statuses]

    return replace(result, scopes=scopes)


def _worst_message(scope: ScopeResult) -> str:
    if scope.status is Status.PASS:
        return ""
    for status in _STATUS_ORDER:
        for check in scope.checks:
            if check.status is status and status is not Status.PASS:
                return check.message
    return ""


def render(result: RunResult) -> str:
    lines: list[str] = []

    subject = result.subject
    baseline = result.baseline
    if baseline:
        lines.append(
            f"Migrace: {baseline['address']} ({baseline['phase']}) -> "
            f"{subject['address']} ({subject['phase']})"
        )
    else:
        lines.append(f"Validace: {subject['address']} ({subject['phase']})")
    lines.append("")

    summary = result.summary
    lines.append(
        f"  {summary['pass']} PASS   {summary['warn']} WARN   "
        f"{summary['fail']} FAIL   {summary['skip']} SKIP"
    )
    lines.append(
        f"  Sparovano {summary['scopes_matched']} sluzeb, "
        f"{summary['unmatched_baseline']} nesparovana v baseline, "
        f"{summary['unmatched_subject']} nesparovane v subject"
    )
    lines.append("")

    lines.append(f"{'SLUZBA':<32} {'TYP':<10} {'STAV':<5} DETAIL")
    for scope in result.scopes:
        description = scope.key.get("description") or scope.scope_id
        service_type = scope.key.get("service_type") or "-"
        lines.append(
            f"{description:<32.32} {service_type:<10.10} "
            f"{SYMBOL[scope.status]:<5} {_worst_message(scope)}"
        )

    lines.append("")
    lines.append("NESPAROVANO")
    if not result.unmatched["baseline"] and not result.unmatched["subject"]:
        lines.append("  (nic)")
    for side in ("baseline", "subject"):
        for item in result.unmatched[side]:
            label = item.get("description") or item["scope_id"]
            service_type = item.get("service_type") or "-"
            lines.append(f"  {side:<9} {label:<32.32} ({service_type})  {item['reason']}")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 5: Spusť test**

Run: `.venv/bin/pytest tests/reporting/test_text_report.py -v`
Expected: PASS, 9 testů

- [ ] **Step 6: Commit**

```bash
git add migration_validator/reporting tests/reporting
git commit -m "feat: add JSON and text reporting with filtering"
```

---

### Task 16: CLI

**Files:**
- Create: `migration_validator/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `api.evaluate`, `api.list_checks` (Task 14), `load_snapshot`, `SnapshotVersionError` (Task 5), `load_mapping` (Task 7), `load_config` (Task 9), reporting (Task 15), `build_scopes` (Task 6), `match_scopes` (Task 8)
- Produces:
  - `main(argv: list[str] | None = None) -> int`
  - Podpříkazy `evaluate`, `match`, `checks`

**Exit kódy:** `0` bez FAIL, `1` s aspoň jedním FAIL, `2` chyba nástroje. `--warn-as-error` povýší WARN na exit `1`.

- [ ] **Step 1: Napiš failing test**

Vytvoř `tests/test_cli.py`:

```python
import json

import pytest

from migration_validator.cli import main
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import (
    CaptureMeta,
    DeviceMeta,
    Snapshot,
    save_snapshot,
)

NOW = "2026-07-24T11:40:02Z"


def _write(tmp_path, name, address, interface, *, oper="up", pps=400):
    scope = Scope(
        id="svc:L3VPN:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN", "IPVPN", None),
        selectors=Selectors(interfaces=[interface]),
    )
    snapshot = Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(
            started_at=NOW, finished_at=NOW, phase=name,
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts={
            "interfaces": {
                interface: {
                    "admin_status": "up", "oper_status": oper,
                    "input_pps": pps, "output_pps": pps,
                    "input_errors": 0, "output_errors": 0,
                }
            }
        },
        probes={"ping": []},
        scopes=[scope],
        inventory=[],
    )
    path = tmp_path / f"{name}.json"
    save_snapshot(snapshot, path)
    return path


def test_evaluate_text_output(tmp_path, capsys):
    old = _write(tmp_path, "pre", "172.20.20.4", "ge-0/0/2.113")
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113")

    code = main(["evaluate", "--snapshot", str(new), "--baseline", str(old)])

    assert code == 0
    output = capsys.readouterr().out
    assert "172.20.20.4" in output
    assert "NESPAROVANO" in output


def test_evaluate_json_output_to_file(tmp_path, capsys):
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113")
    target = tmp_path / "result.json"

    code = main(["evaluate", "--snapshot", str(new), "--format", "json",
                 "--output", str(target)])

    assert code == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["baseline"] is None


def test_exit_code_1_when_fail_present(tmp_path):
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113", oper="down")
    assert main(["evaluate", "--snapshot", str(new)]) == 1


def test_warn_does_not_change_exit_code(tmp_path):
    old = _write(tmp_path, "pre", "172.20.20.4", "ge-0/0/2.113", pps=400)
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113", pps=50)
    assert main(["evaluate", "--snapshot", str(new), "--baseline", str(old)]) == 0


def test_warn_as_error_flag(tmp_path):
    old = _write(tmp_path, "pre", "172.20.20.4", "ge-0/0/2.113", pps=400)
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113", pps=50)
    code = main(
        ["evaluate", "--snapshot", str(new), "--baseline", str(old), "--warn-as-error"]
    )
    assert code == 1


def test_missing_snapshot_file_is_tool_error(tmp_path, capsys):
    code = main(["evaluate", "--snapshot", str(tmp_path / "nope.json")])
    assert code == 2
    assert "nope.json" in capsys.readouterr().err


def test_wrong_schema_version_is_tool_error(tmp_path, capsys):
    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")

    code = main(["evaluate", "--snapshot", str(path)])

    assert code == 2
    assert "99" in capsys.readouterr().err


def test_checks_subcommand_lists_registry(capsys):
    assert main(["checks"]) == 0
    output = capsys.readouterr().out
    assert "interface_traffic" in output
    assert "evpn_esi_status" in output


def test_checks_subcommand_json(capsys):
    assert main(["checks", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(item["id"] == "ping_reachability" for item in payload)


def test_match_subcommand_reports_pairs_and_unmatched(tmp_path, capsys):
    old = _write(tmp_path, "pre", "172.20.20.4", "ge-0/0/2.113")
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113")

    code = main(["match", "--baseline", str(old), "--subject", str(new)])

    assert code == 0
    output = capsys.readouterr().out
    assert "description+service_type" in output
    assert "svc:L3VPN:IPVPN" in output


def test_status_filter(tmp_path, capsys):
    new = _write(tmp_path, "post", "172.20.20.5", "et-0/0/8.113", oper="down")

    main(["evaluate", "--snapshot", str(new), "--status", "fail"])

    output = capsys.readouterr().out
    assert "L3VPN" in output
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL s `ModuleNotFoundError: No module named 'migration_validator.cli'`

- [ ] **Step 3: Implementuj CLI**

Vytvoř `migration_validator/cli.py`:

```python
"""CLI - tenky obal nad api.py.

Cokoliv umi CLI, umi i GUI, protoze jdou stejnou cestou.
capture podprikaz doplni Plan 2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from migration_validator import api
from migration_validator.config import default_config, load_config
from migration_validator.models.result import Status
from migration_validator.models.snapshot import (
    Snapshot,
    SnapshotVersionError,
    load_snapshot,
)
from migration_validator.reporting.json_report import to_json, write_json
from migration_validator.reporting.text_report import filter_result, render
from migration_validator.scoping.mapping import empty_mapping, load_mapping
from migration_validator.scoping.matcher import match_scopes

EXIT_OK = 0
EXIT_FAILED_CHECKS = 1
EXIT_TOOL_ERROR = 2


class ToolError(Exception):
    """Nastroj selhal - jina vec nez selhany test."""


def _load_snapshot(path: str) -> Snapshot:
    try:
        return load_snapshot(path)
    except FileNotFoundError as error:
        raise ToolError(f"snapshot nenalezen: {path}") from error
    except SnapshotVersionError as error:
        raise ToolError(str(error)) from error
    except json.JSONDecodeError as error:
        raise ToolError(f"{path}: nevalidni JSON ({error})") from error


def _parse_statuses(value: str | None) -> set[Status] | None:
    if not value:
        return None
    return {Status(item.strip().upper()) for item in value.split(",") if item.strip()}


def _cmd_evaluate(args: argparse.Namespace) -> int:
    subject = _load_snapshot(args.snapshot)
    baseline = _load_snapshot(args.baseline) if args.baseline else None
    mapping = load_mapping(args.mapping) if args.mapping else empty_mapping()
    config = load_config(args.config) if args.config else default_config()

    result = api.evaluate(subject, baseline=baseline, mapping=mapping, config=config)

    shown = filter_result(result, text=args.filter, statuses=_parse_statuses(args.status))

    if args.format == "json":
        if args.output:
            write_json(shown, args.output)
        else:
            print(to_json(shown))
    else:
        print(render(shown), end="")
        if args.output:
            write_json(result, args.output)

    if result.summary["fail"]:
        return EXIT_FAILED_CHECKS
    if args.warn_as_error and result.summary["warn"]:
        return EXIT_FAILED_CHECKS
    return EXIT_OK


def _cmd_match(args: argparse.Namespace) -> int:
    baseline = _load_snapshot(args.baseline)
    subject = _load_snapshot(args.subject)
    mapping = load_mapping(args.mapping) if args.mapping else empty_mapping()

    matches = match_scopes(baseline.scopes, subject.scopes, mapping)

    print(f"SPAROVANO ({len(matches.pairs)})")
    for pair in matches.pairs:
        print(f"  {pair.confidence:<8} {pair.method:<45} {pair.baseline.id}")
        print(f"           -> {pair.subject.id}")

    print(f"\nNESPAROVANO baseline ({len(matches.unmatched_baseline)})")
    for item in matches.unmatched_baseline:
        print(f"  {item.scope.id:<50} {item.reason}")

    print(f"\nNESPAROVANO subject ({len(matches.unmatched_subject)})")
    for item in matches.unmatched_subject:
        print(f"  {item.scope.id:<50} {item.reason}")

    return EXIT_OK


def _cmd_checks(args: argparse.Namespace) -> int:
    described = api.list_checks()
    if args.format == "json":
        print(json.dumps(described, indent=2, ensure_ascii=False))
        return EXIT_OK

    print(f"{'ID':<24} {'MODE':<8} {'SEVERITY':<9} TYPY SLUZEB")
    for item in described:
        types = ", ".join(item["service_types"]) if item["service_types"] else "vsechny"
        print(
            f"{item['id']:<24} {item['mode']:<8} "
            f"{item['default_severity']:<9} {types}"
        )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mig-validate",
        description="Validace stavu sitovych sluzeb pri migraci Junos -> Junos EVO",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate = sub.add_parser("evaluate", help="vyhodnoti snapshot, volitelne proti baseline")
    evaluate.add_argument("--snapshot", required=True)
    evaluate.add_argument("--baseline")
    evaluate.add_argument("--mapping")
    evaluate.add_argument("--config")
    evaluate.add_argument("--format", choices=("text", "json"), default="text")
    evaluate.add_argument("--output")
    evaluate.add_argument("--filter", help="podretezec v description nebo scope id")
    evaluate.add_argument("--status", help="carkou oddeleny seznam: pass,warn,fail,skip")
    evaluate.add_argument("--warn-as-error", action="store_true")
    evaluate.set_defaults(func=_cmd_evaluate)

    match = sub.add_parser("match", help="jen parovani sluzeb, pro ladeni mapping.yml")
    match.add_argument("--baseline", required=True)
    match.add_argument("--subject", required=True)
    match.add_argument("--mapping")
    match.set_defaults(func=_cmd_match)

    checks = sub.add_parser("checks", help="vypise registrovane checky")
    checks.add_argument("--format", choices=("text", "json"), default="text")
    checks.set_defaults(func=_cmd_checks)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ToolError as error:
        print(f"chyba: {error}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    except (OSError, ValueError) as error:
        print(f"chyba: {error}", file=sys.stderr)
        return EXIT_TOOL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
```

> **Poznámka k `--output` v textovém režimu:** do souboru se zapisuje **nefiltrovaný** `result`, zatímco na terminál jde filtrovaný pohled. Je to záměr — artefakt na disku má být kompletní, filtr je jen pohled operátora.

- [ ] **Step 4: Spusť test**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS, 11 testů

- [ ] **Step 5: Ověř konzolový skript**

```bash
.venv/bin/mig-validate checks
```

Expected: tabulka s deseti checky.

- [ ] **Step 6: Spusť celou sadu**

Run: `.venv/bin/pytest -q`
Expected: všechny testy PASS

- [ ] **Step 7: Commit**

```bash
git add migration_validator/cli.py tests/test_cli.py
git commit -m "feat: add CLI with evaluate, match and checks subcommands"
```

---

### Task 17: End-to-end test nad reálnými inventory soubory

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_end_to_end.py`
- Test: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: vše z Tasků 1–16
- Produces: fixture `synthetic_snapshot(inventory_path, address, phase, **overrides) -> Snapshot`, která staví snapshot ze skutečné inventory a syntetických faktů

Tenhle task nezavádí nový kód v balíku — je to pojistka, že celý řetěz `inventory → scopy → párování → checky → report` drží pohromadě na reálných datech, ne jen na minimálních fixtures.

- [ ] **Step 1: Napiš fixture**

Vytvoř `tests/conftest.py`:

```python
"""Sdilene fixtures - stavba snapshotu ze skutecne inventory."""

from __future__ import annotations

import pytest

from migration_validator.models.inventory import load_inventory
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot
from migration_validator.scoping.builder import build_scopes

NOW = "2026-07-24T09:12:41Z"


def _facts_for(scopes, pps: int) -> dict:
    interfaces = {}
    arp = []
    bgp = {}
    evpn_vpws = {}
    evpn_esi = {}
    evpn_mac = {}

    for scope in scopes:
        for name in scope.selectors.interfaces + scope.selectors.physical_interfaces:
            interfaces[name] = {
                "admin_status": "up",
                "oper_status": "up",
                "input_pps": pps,
                "output_pps": pps,
                "input_errors": 0,
                "output_errors": 0,
            }
        for peer in scope.selectors.bgp_neighbors:
            arp.append({"ip": peer, "interface": scope.selectors.interfaces[0]})
            bgp[peer] = {
                "state": "Established",
                "routing_instance": (
                    scope.selectors.routing_instances[0]
                    if scope.selectors.routing_instances
                    else None
                ),
                "prefixes": {"received": 14, "accepted": 14, "advertised": 3},
            }
        service_type = scope.key.service_type
        instance = (
            scope.selectors.routing_instances[0]
            if scope.selectors.routing_instances
            else None
        )
        if service_type == "E-Line" and instance:
            evpn_vpws[instance] = {"local_sid": 213, "remote_sid": 213, "status": "Up"}
        if service_type == "E-LAN" and instance:
            evpn_esi[f"esi-{instance}"] = {
                "status": "Up",
                "df_role": "DF",
                "interface": scope.selectors.interfaces[0],
            }
            domains = scope.selectors.bridge_domains or ["-"]
            evpn_mac[instance] = {domain: 42 for domain in domains}

    return {
        "interfaces": interfaces,
        "arp": arp,
        "bgp": bgp,
        "evpn_vpws": evpn_vpws,
        "evpn_esi": evpn_esi,
        "evpn_mac": evpn_mac,
    }


@pytest.fixture
def synthetic_snapshot():
    def build(inventory_path: str, address: str, phase: str, *, pps: int = 400) -> Snapshot:
        inventory = load_inventory(inventory_path)
        scopes = build_scopes(inventory)
        pings = [
            {
                "scope_id": scope.id,
                "target": scope.selectors.bgp_neighbors[0],
                "source": scope.selectors.local_addresses[0].split("/")[0],
                "sent": 5,
                "received": 5,
                "loss_percent": 0,
                "resolved_from": "arp",
            }
            for scope in scopes
            if scope.selectors.bgp_neighbors and scope.selectors.local_addresses
        ]
        return Snapshot(
            device=DeviceMeta(address=address),
            capture=CaptureMeta(
                started_at=NOW,
                finished_at=NOW,
                phase=phase,
                collectors={
                    name: {"status": "ok"}
                    for name in ("interfaces", "arp", "bgp", "evpn_vpws", "evpn_esi", "evpn_mac")
                },
            ),
            facts=_facts_for(scopes, pps),
            probes={"ping": pings},
            scopes=scopes,
            inventory=inventory.entries,
        )

    return build
```

- [ ] **Step 2: Napiš end-to-end test**

Vytvoř `tests/test_end_to_end.py`:

```python
import json

from migration_validator import api
from migration_validator.models.result import Status
from migration_validator.reporting.json_report import to_json
from migration_validator.reporting.text_report import render

NOW = "2026-07-24T11:40:02Z"


def test_full_migration_run_is_green(synthetic_snapshot):
    old = synthetic_snapshot("172.20.20.4.yml", "172.20.20.4", "pre-migration")
    new = synthetic_snapshot("172.20.20.5.yml", "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    assert result.summary["fail"] == 0
    assert result.summary["scopes_matched"] >= 5
    assert json.loads(to_json(result))["schema_version"] == 1


def test_traffic_drop_on_new_device_is_detected(synthetic_snapshot):
    old = synthetic_snapshot("172.20.20.4.yml", "172.20.20.4", "pre-migration", pps=400)
    new = synthetic_snapshot("172.20.20.5.yml", "172.20.20.5", "post-migration", pps=50)

    result = api.evaluate(new, baseline=old, now=NOW)

    traffic = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "interface_traffic"
    ]
    assert any(check.status is Status.WARN for check in traffic)


def test_new_elan_service_shows_up_as_unmatched(synthetic_snapshot):
    old = synthetic_snapshot("172.20.20.4.yml", "172.20.20.4", "pre-migration")
    new = synthetic_snapshot("172.20.20.5.yml", "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    subject_ids = {item["scope_id"] for item in result.unmatched["subject"]}
    assert any("EVPN-VLAN-AWARE-INTERNET" in scope_id for scope_id in subject_ids)


def test_management_interfaces_never_appear(synthetic_snapshot):
    new = synthetic_snapshot("172.20.20.5.yml", "172.20.20.5", "post-migration")

    result = api.evaluate(new, now=NOW)
    rendered = render(result)

    assert "fxp0" not in rendered
    assert "mgmt" not in rendered


def test_single_snapshot_validation_skips_comparison_checks(synthetic_snapshot):
    old = synthetic_snapshot("172.20.20.4.yml", "172.20.20.4", "pre-migration")

    result = api.evaluate(old, now=NOW)

    prefix_checks = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "bgp_prefix_counts"
    ]
    assert prefix_checks
    assert all(check.status is Status.SKIP for check in prefix_checks)
```

- [ ] **Step 3: Spusť test**

Run: `.venv/bin/pytest tests/test_end_to_end.py -v`
Expected: PASS, 5 testů

- [ ] **Step 4: Spusť celou sadu a zkontroluj čas**

Run: `.venv/bin/pytest -q`
Expected: všechny testy PASS, běh pod 5 sekund (žádný test nesahá na síť)

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_end_to_end.py
git commit -m "test: add end-to-end run over real inventory files"
```

---

### Task 18: Volitelný check `traffic_ceased`

**Files:**
- Modify: `migration_validator/checks/ifaces.py` (přidat třídu na konec souboru)
- Modify: `tests/checks/test_ifaces.py` (přidat testy na konec souboru)

**Interfaces:**
- Consumes: `Check`, `CheckContext`, `Mode`, `register` (Task 9), `_rates`, `percent_change` (Task 10)
- Produces: `TrafficCeasedCheck` (`id="traffic_ceased"`, `Mode.COMPARE`, `Severity.ADVISORY`, `requires=("interfaces",)`, **default vypnutý** přes `DEFAULTS` v `config.py`)

**K čemu to je:** ověří, že na **starém** rozhraní provoz po migraci klesl k nule. Chytá případ „zapomnělo se to vypnout / provoz teče dvakrát". Použití: třetí `capture` starého boxu **po** migraci, pak `evaluate` s tímto jedním checkem — subject je starý box po migraci, baseline starý box před migrací.

Konfigurace už na check odkazuje (`DEFAULTS["traffic_ceased"] = {"enabled": False}`), takže tenhle task jen doplňuje implementaci.

- [ ] **Step 1: Napiš failing test**

Přidej na konec `tests/checks/test_ifaces.py`:

```python
from migration_validator.checks.ifaces import TrafficCeasedCheck


def _ceased_ctx(subject_pps, baseline_pps, config=None):
    from migration_validator.config import CheckConfig

    config = config or CheckConfig({"traffic_ceased": {"enabled": True}})
    return _ctx(
        subject={
            "interfaces": {
                "ge-0/0/2.113": {"input_pps": subject_pps, "output_pps": subject_pps}
            }
        },
        baseline={
            "interfaces": {
                "ge-0/0/2.113": {"input_pps": baseline_pps, "output_pps": baseline_pps}
            }
        },
        config=config,
    )


def test_traffic_ceased_is_disabled_by_default():
    ctx = _ctx(
        subject={"interfaces": {"ge-0/0/2.113": {"input_pps": 400, "output_pps": 400}}},
        baseline={"interfaces": {"ge-0/0/2.113": {"input_pps": 400, "output_pps": 400}}},
    )
    assert run_check(TrafficCeasedCheck(), ctx) == []


def test_traffic_ceased_passes_when_old_port_went_quiet():
    result = run_check(TrafficCeasedCheck(), _ceased_ctx(0, 400))[0]
    assert result.status is Status.PASS


def test_traffic_ceased_warns_when_old_port_still_carries_traffic():
    result = run_check(TrafficCeasedCheck(), _ceased_ctx(380, 400))[0]
    assert result.status is Status.WARN
    assert "380" in result.message


def test_traffic_ceased_tolerates_residual_pps():
    result = run_check(TrafficCeasedCheck(), _ceased_ctx(1, 400))[0]
    assert result.status is Status.PASS


def test_traffic_ceased_residual_threshold_is_configurable():
    from migration_validator.config import CheckConfig

    config = CheckConfig(
        {"traffic_ceased": {"enabled": True, "max_residual_pps": 500}}
    )
    result = run_check(TrafficCeasedCheck(), _ceased_ctx(380, 400, config))[0]
    assert result.status is Status.PASS


def test_traffic_ceased_skips_when_baseline_had_no_traffic():
    result = run_check(TrafficCeasedCheck(), _ceased_ctx(0, 0))[0]
    assert result.status is Status.SKIP
    assert "baseline" in result.message
```

- [ ] **Step 2: Spusť test a ověř, že selže**

Run: `.venv/bin/pytest tests/checks/test_ifaces.py -k ceased -v`
Expected: FAIL s `ImportError: cannot import name 'TrafficCeasedCheck'`

- [ ] **Step 3: Doplň výchozí hodnotu do konfigurace**

V `migration_validator/config.py` nahraď řádek

```python
    "traffic_ceased": {"enabled": False},
```

za

```python
    "traffic_ceased": {"enabled": False, "max_residual_pps": 1},
```

- [ ] **Step 4: Implementuj check**

Přidej na konec `migration_validator/checks/ifaces.py`:

```python
@register
class TrafficCeasedCheck(Check):
    """Overi, ze na starem rozhrani provoz po migraci klesl k nule.

    Chyta zapomenute vypnuti a duplicitni forwarding. Default vypnuty -
    vyzaduje treti capture stareho boxu po migraci.
    """

    id = "traffic_ceased"
    title = "Utichnuti stareho rozhrani"
    mode = Mode.COMPARE
    requires = ("interfaces",)
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        names = _transit_interfaces(ctx)
        if not names:
            return [_no_transit_finding(ctx)]

        threshold = int(ctx.options(self.id)["max_residual_pps"])

        findings = []
        for name in names:
            subject = _rates(ctx.subject["interfaces"][name])
            baseline_data = (ctx.baseline or {}).get("interfaces", {}).get(name)
            if baseline_data is None:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{name}: rozhrani neni v baseline snapshotu",
                        label=name,
                    )
                )
                continue

            baseline = _rates(baseline_data)
            if baseline["input_pps"] == 0 and baseline["output_pps"] == 0:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{name}: v baseline zadny provoz, utichnuti nelze overit",
                        label=name,
                        baseline=baseline,
                        subject=subject,
                    )
                )
                continue

            residual = max(subject["input_pps"], subject["output_pps"])
            details = {"max_residual_pps": threshold, "residual_pps": residual}

            if residual > threshold:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{name}: stare rozhrani stale nese provoz "
                        f"({residual} pps, prah {threshold} pps)",
                        label=name,
                        baseline=baseline,
                        subject=subject,
                        details=details,
                    )
                )
            else:
                findings.append(
                    Finding(
                        Outcome.OK,
                        f"{name}: provoz utichl ({residual} pps)",
                        label=name,
                        baseline=baseline,
                        subject=subject,
                        details=details,
                    )
                )
        return findings
```

- [ ] **Step 5: Spusť testy**

Run: `.venv/bin/pytest tests/checks/test_ifaces.py -v`
Expected: PASS, 30 testů

- [ ] **Step 6: Oprav počty v ostatních testech**

Registry teď má 11 checků. Spusť celou sadu:

Run: `.venv/bin/pytest -q`
Expected: všechny testy PASS. Pokud některý test kontroluje počet checků, uprav ho na 11.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/checks/ifaces.py migration_validator/config.py \
        tests/checks/test_ifaces.py
git commit -m "feat: add optional traffic_ceased check for old interface"
```

---

## Hotovo po Plánu 1

Po dokončení funguje:

```bash
.venv/bin/mig-validate checks
.venv/bin/mig-validate evaluate --snapshot post.json --baseline pre.json
.venv/bin/mig-validate match --baseline pre.json --subject post.json
```

Chybí `capture` — snapshoty zatím nikdo nevyrábí ze skutečného zařízení. To řeší **Plán 2**.

