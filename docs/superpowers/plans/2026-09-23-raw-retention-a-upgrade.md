# Raw retention + `mig-validate upgrade` — implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Každý capture v runu uloží syrová data ze zařízení (konfiguraci s rozšířeným filtrem, všechny RPC odpovědi včetně pingů, facts) a `mig-validate upgrade` / tlačítka „Upgrade run“ a „Upgrade group“ z nich přegenerují inventory i snapshoty aktuální verzí nástroje.

**Architecture:** Obal kolem `device.rpc` (`RecordingDevice`) nahraje obě sessions capture (`--parse-services` i capture) do paměti; `capture_into_run` je pod zámkem runu zapíše vedle snapshotu/inventory jako `raw/<kmen>/` (session.json + gzip odpovědi). `upgrade` pustí nezměněný `generate_inventory` a `capture_device` proti `ReplayDevice`, který vrací nahrané odpovědi; co v nahrávce není, vyhodí `NotRecorded` (collector → status error, ping → sent=0 → SKIP). Výsledky jdou do `.upgrade-staging/`, výměna se zálohou do `backup/upgrade-<čas>/` proběhne pod `fcntl.flock` až po kontrole, že se run mezitím nezměnil.

**Tech Stack:** Python 3.13, lxml, PyYAML, FastAPI, pytest (`.venv/bin/pytest`), vanilla JS (`node --test tests/js/*.test.js`), laborka containerlab (MX1-POP1 `172.20.20.4` junos, PTX1-POP1 `172.20.20.5` junos-evo, user `admin`, heslo `$MIG_LAB_PASSWORD`).

**Spec:** `docs/superpowers/specs/2026-09-23-raw-retention-a-upgrade-design.md`

## Global Constraints

- Snapshot `SCHEMA_VERSION` zůstává **13**, inventory `INVENTORY_SCHEMA_VERSION` **10**. Raw formát `raw_format: 1`.
- `RAW_EXTRA_HIERARCHIES = ("policy-options", "firewall", "class-of-service")` — přesně tahle n-tice v `migration_validator/parsers/core.py`.
- Adresář raw má stejný kmen jako jeho soubor: `snapshot_<X>.json` → `raw/<X>/`, `inventory_<Y>.yml` → `raw/inventory_<Y>/`. V bundlu capture je `inventory/` (kopie raw inventory) nebo `inventory/inventory.yml` (kopie YAML, když inventory raw nemá).
- Zámek runu je `fcntl.flock` na `runs/<run>/.lock` (ne `lockf`/`fcntl.fcntl` — ty se uvnitř jednoho procesu nevylučují a GUI má capture i upgrade v jednom procesu). Pod zámkem jen zápis na disk, nikdy NETCONF session.
- Upgrade (GUI) vyžaduje `Permission.ADMIN`. Návratové kódy CLI `upgrade`: `0` vše přegenerováno, `1` (`EXIT_FAILED_CHECKS`) některé capture nejdou, `2` (`EXIT_TOOL_ERROR`) upgrade některého runu spadl.
- Řetězce z backendu (CLI výstup, `reason`, `detail`, varování) jsou ASCII bez diakritiky jako zbytek nástroje (`pregenerovano - beze zmeny`, `nelze - bez raw zaznamu (zachyceno pred zavedenim)`). Popisky v GUI jsou anglicky (`Upgrade run`, `outdated`, `no raw`), vysvětlující texty v modalech česky s diakritikou jako u „Archive run“. Příklad reportu ve specu s diakritikou je ilustrace, závazná je tahle ASCII podoba.
- `NotRecorded` se nikdy nesmí proměnit ve změřený výsledek: collector → status `error`, ping → `sent=0` + `error="neni v raw zaznamu"`.
- RPC na capture cestě se volají jen keyword argumenty (`device.rpc.get_config(filter_xml=..., options=...)`, `getattr(device.rpc, name)(**kwargs)`, `device.rpc.ping(**kwargs)`, `device.rpc.get_instance_information(brief=True)` — ověřeno grepem 2026-09-23). `RecordingDevice` a `ReplayDevice` přijímají jen `**kwargs`; nové RPC volání s pozičním argumentem je chyba.
- Collectory (`collectors/base.py`, `interfaces.py`, `routes.py`, `evpn.py`, `multicast.py`) i `run_ping` chytají `Exception` a nevětví se podle typu výjimky PyEZ. Replay nahraných chyb na tom stojí — nepřidávat `except RpcError` do capture cesty.
- Testy: `.venv/bin/pytest -q -p no:warnings` (výchozí stav 1929 passed, 1 skipped) a `node --test tests/js/*.test.js` (77 pass). Před každým commitem musí projít celá Python sada; JS sada po každém tasku, který sahá na `static/`.
- Každé tvrzení o zabitém mutantu (docstring testu, commit message) se ověřuje **spuštěním** mutanta, ne úsudkem — a nahlas i tehdy, když je oprava mimo rozsah tvého tasku.
- Heslo laborky: `eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"` na začátku každého příkazu, který mluví s laborkou (v `~/.bashrc` je pod guardem pro neinteraktivní shell). V `EnterWorktree` session guard `eval` odmítne — pak heslo přečti v Pythonu z řádku `export MIG_LAB_PASSWORD=` v `~/.bashrc`.
- Commit messages končí:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```

## Review Focus

1. **Box bez konfigurace pod některou žádanou hierarchií** (žádný `firewall`, `switch-options`): Junos ji v odpovědi vynechá; upgrade stejnou verzí musí hlásit „beze změny“, ne „nelze“. → Task 3 `test_requested_but_empty_hierarchy_is_not_an_error`, Task 14 lab dry-run.
2. **GUI capture doběhne během upgradu téhož runu (jeden proces):** čerstvý snapshot musí přežít a nesmí skončit jen v záloze. → Task 7 `test_capture_write_waits_for_run_lock`, Task 9 `test_run_changed_during_upgrade_aborts`, `test_swap_waits_for_capture_lock`.
3. **Pád po zápisu nového snapshotu/inventory, ale před zápisem jeho raw:** vedle nového souboru nesmí zůstat raw předchozího capture. → Task 2 `test_failed_write_leaves_no_old_session`, Task 7 `test_raw_write_failure_is_warning_and_leaves_no_stale_raw`, `test_inventory_raw_failure_leaves_no_stale_raw`.
4. **Post, jehož baseline pre byla přepsána nebo nejde přegenerovat:** post se přesto přegeneruje, report to poznamená a rozdíly v pingu jsou SKIP, nikdy BROKEN. → Task 6 `test_replay_unrecorded_ping_target_is_skip_not_broken`, Task 8 `test_missing_baseline_is_noted_and_post_still_regenerates`.
5. **Run se snapshotem poškozeným nebo zachyceným před zavedením raw:** detail runu, souhrn skupiny i upgrade ho nahlásí a nespadnou. → Task 8 `test_capture_without_raw_is_reported_and_untouched`, Task 11 `test_snapshot_list_marks_corrupt_snapshot_outdated`, `test_group_summary_row_reports_outdated_run`.

---

## Mapa souborů

| Soubor | Změna |
|---|---|
| `migration_validator/raw/__init__.py` | nový balíček |
| `migration_validator/raw/calls.py` | nový: `NotRecorded`, `NOT_RECORDED`, `canonical_kwargs`, `config_filter`, `call_key`, `RecordedCall` (list modul, jen lxml) |
| `migration_validator/raw/recorder.py` | nový: `SessionRecording`, `RecordingDevice`, `FACT_KEYS` |
| `migration_validator/raw/bundle.py` | nový: `Session`, `RAW_FORMAT`, `write_session`, `read_session`, `remove_session`, `has_session`, `tool_info`, `RawFormatError` |
| `migration_validator/raw/replay.py` | nový: `ReplayDevice`, `RecordedRpcError`, `recorded_error` |
| `migration_validator/raw/upgrade.py` | nový: `upgrade_run`, `UpgradeReport`, `UpgradeItem`, `regenerate_inventory`, `render_report` |
| `migration_validator/parsers/__init__.py` | `parser_hierarchies()` |
| `migration_validator/parsers/core.py` | `RAW_EXTRA_HIERARCHIES`, rozšířený filtr + ořez v `retrieve_configuration` |
| `migration_validator/probes/ping.py` | `run_ping` chytá `NotRecorded` před catch-all |
| `migration_validator/checks/reachability.py` | `sent == 0` připojí `error` k textu |
| `migration_validator/capture.py` | `finished_at`, pryč `record_raw` a `_record` |
| `migration_validator/api.py` | `capture(recorder=)` místo `record_raw`; `upgrade_run()` |
| `migration_validator/runs/store.py` | `raw_name()`, `RunStore.raw_dir()`, `RunStore.lock()` |
| `migration_validator/runs/orchestrate.py` | nahrávání obou sessions, životní cyklus raw, zámek, manifest znovu pod zámkem |
| `migration_validator/cli.py` | pryč `--record-raw`; nový podpříkaz `upgrade` |
| `migration_validator/models/snapshot.py` | rada v `SnapshotVersionError`, `peek_schema_version()` |
| `migration_validator/models/inventory.py` | `InventoryVersionError(ValueError)` s radou |
| `migration_validator/gui/app.py` | handler 422 `schema_outdated`, `POST /api/runs/{run}/upgrade`, `snapshot_list(manifest, store)` |
| `migration_validator/gui/group_routes.py` | `POST /api/groups/{group}/upgrade` |
| `migration_validator/gui/groups.py` | řádek souhrnu se zastaralými snímky místo pádu |
| `migration_validator/gui/serializers.py` | `schema_version`, `outdated`, `has_raw` |
| `migration_validator/gui/captures.py` | `RunBusy(DeviceBusy)`, `maintenance()`, kontrola ve `start()` |
| `migration_validator/gui/static/view.js`, `app.js`, `style.css` | helpery, modaly Upgrade run/group, štítky, notice s tlačítkem |
| `tests/raw_run.py` | nový helper: testovací run se skutečnými raw bundly (`from raw_run import ...`) |
| `tests/raw/…` | nové testy balíčku `raw` |
| `tests/fixtures/raw/junos/`, `tests/fixtures/raw/junos-evo/` | laboratorní bundly (Task 14) |
| `docs/cs/README.md`, `docs/en/README.md`, `docs/cs/files/*.md`, `docs/cs/architecture.md`, `docs/en/architecture.md` | raw retention, `upgrade`, pryč `--record-raw` |

Pořadí tasků je závazné: každý staví na rozhraních předchozích.

---

### Task 1: `raw/calls.py` — společné tvary a `NotRecorded`

**Files:**
- Create: `migration_validator/raw/__init__.py`, `migration_validator/raw/calls.py`
- Create: `tests/raw/__init__.py`, `tests/raw/test_calls.py`

**Interfaces:**
- Consumes: nic.
- Produces:
  - `NOT_RECORDED: str = "neni v raw zaznamu"`
  - `class NotRecorded(Exception)` — zpráva je celý text (`NotRecorded("neni v raw zaznamu: get_x")`).
  - `canonical_kwargs(rpc: str, kwargs: dict) -> dict` — seřazené klíče; u `get_config` vynechá `filter_xml`; jiné `etree._Element` hodnoty serializuje na `str`.
  - `config_filter(rpc: str, kwargs: dict) -> list[str] | None` — lokální jména dětí `filter_xml` u `get_config`, jinak `None`.
  - `call_key(rpc: str, kwargs: dict) -> str` — klíč pro párování (kwargs už kanonické).
  - `@dataclass RecordedCall(seq: int, rpc: str, kwargs: dict, filter: list[str] | None = None, reply_xml: bytes | None = None, reply_value: Any = None, error: dict[str, str] | None = None)` s metodou `reply() -> Any` (čerstvě naparsovaný element při každém volání, jinak `reply_value`).

- [ ] **Step 1: Write the failing test**

`tests/raw/__init__.py` prázdný. `tests/raw/test_calls.py`:

```python
"""Spolecne tvary raw zaznamu (spec 2026-09-23)."""

from lxml import etree

from migration_validator.raw.calls import (
    NOT_RECORDED,
    NotRecorded,
    RecordedCall,
    call_key,
    canonical_kwargs,
    config_filter,
)


def _filter(*names):
    root = etree.Element("configuration")
    for name in names:
        etree.SubElement(root, name)
    return root


def test_canonical_kwargs_drops_config_filter_element():
    """Filtr get_config nese config_filter(); v kwargs by jako serializovany
    element rozbil parovani, jakmile se zmeni poradi hierarchii.
    Zabiji mutanta: canonical_kwargs necha filter_xml v kwargs."""
    kwargs = {
        "filter_xml": _filter("interfaces"),
        "options": {"database": "committed", "inherit": ""},
    }
    assert canonical_kwargs("get_config", kwargs) == {
        "options": {"database": "committed", "inherit": ""}
    }


def test_canonical_kwargs_serializes_other_elements():
    element = etree.fromstring("<x><y/></x>")
    assert canonical_kwargs("some_rpc", {"filter_xml": element}) == {
        "filter_xml": "<x><y/></x>"
    }


def test_config_filter_lists_top_level_hierarchies_in_order():
    kwargs = {"filter_xml": _filter("interfaces", "policy-options")}
    assert config_filter("get_config", kwargs) == ["interfaces", "policy-options"]
    assert config_filter("get_interface_information", {"terse": True}) is None
    assert config_filter("get_config", {}) is None


def test_call_key_ignores_kwarg_order():
    a = call_key("ping", {"host": "10.0.0.1", "rapid": True, "count": "5"})
    b = call_key("ping", {"count": "5", "host": "10.0.0.1", "rapid": True})
    assert a == b
    assert a != call_key("ping", {"host": "10.0.0.2", "rapid": True, "count": "5"})


def test_recorded_call_reply_parses_fresh_element_each_time():
    """Collector smi odpoved upravovat - druhe podani musi byt neporusene."""
    call = RecordedCall(seq=1, rpc="x", kwargs={}, reply_xml=b"<a><b>1</b></a>")
    first = call.reply()
    first.remove(first[0])
    assert call.reply()[0].text == "1"


def test_recorded_call_non_xml_reply_is_value():
    assert RecordedCall(seq=1, rpc="x", kwargs={}, reply_value=True).reply() is True


def test_not_recorded_carries_message():
    assert str(NotRecorded(f"{NOT_RECORDED}: ping")) == "neni v raw zaznamu: ping"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/raw/test_calls.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'migration_validator.raw'`

- [ ] **Step 3: Write minimal implementation**

`migration_validator/raw/__init__.py`:

```python
"""Raw retention + replay (spec 2026-09-23): nahravani NETCONF sessions
capture a jejich prehravani pro `mig-validate upgrade`."""
```

`migration_validator/raw/calls.py`:

```python
"""Spolecne tvary raw zaznamu - list modul bez tezkych importu.

Pouziva ho recorder, bundle, replay i probes/ping (NotRecorded). Nesmi
importovat parsery ani capture - ping by jinak tahal cely parser a hrozil
by cyklicky import.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from lxml import etree

NOT_RECORDED = "neni v raw zaznamu"

# Produkcni `interfaces extensive` muze mit obri textove uzly.
_PARSER = etree.XMLParser(huge_tree=True)


class NotRecorded(Exception):
    """Volani neni v raw zaznamu. Nikdy se nevydava za vysledek: collector
    z nej udela status error (checky SKIP), ping sent=0 (SKIP)."""


def canonical_kwargs(rpc: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """JSON-bezpecne argumenty pro parovani nahravky s replayem.

    `filter_xml` u get_config se vynecha - nese ho config_filter() jako
    seznam hierarchii. Ostatni lxml elementy se serializuji na text.
    """
    out: dict[str, Any] = {}
    for key in sorted(kwargs):
        value = kwargs[key]
        if rpc == "get_config" and key == "filter_xml":
            continue
        if isinstance(value, etree._Element):
            value = etree.tostring(value, encoding="unicode")
        out[key] = value
    return out


def config_filter(rpc: str, kwargs: dict[str, Any]) -> list[str] | None:
    """Top-level hierarchie filtru get_config v poradi, jinak None.

    Junos prazdnou hierarchii v odpovedi vynecha - rozlisit "zadano
    a prazdne" od "nikdy nezadano" jde jen podle filtru.
    """
    if rpc != "get_config":
        return None
    element = kwargs.get("filter_xml")
    if element is None:
        return None
    return [
        etree.QName(child).localname
        for child in element
        if isinstance(child.tag, str)
    ]


def call_key(rpc: str, kwargs: dict[str, Any]) -> str:
    """Klic pro parovani volani; kwargs uz musi byt kanonicke."""
    return f"{rpc} {json.dumps(kwargs, sort_keys=True, default=str)}"


@dataclass
class RecordedCall:
    seq: int
    rpc: str
    kwargs: dict[str, Any]
    filter: list[str] | None = None
    reply_xml: bytes | None = None
    reply_value: Any = None
    error: dict[str, str] | None = None

    def reply(self) -> Any:
        """Cerstve naparsovana odpoved - kazde podani je nezavisla kopie."""
        if self.reply_xml is not None:
            return etree.fromstring(self.reply_xml, _PARSER)
        return self.reply_value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/raw/test_calls.py -q -p no:warnings`
Expected: PASS (7 passed)

- [ ] **Step 5: Run the mutant**

Dočasně smaž v `canonical_kwargs` řádky `if rpc == "get_config" and key == "filter_xml": continue`, spusť `test_canonical_kwargs_drops_config_filter_element` → musí FAIL. Vrať změnu.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/raw/__init__.py migration_validator/raw/calls.py tests/raw/__init__.py tests/raw/test_calls.py
git commit -m "feat(raw): shared call shapes and NotRecorded for raw retention

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `raw/recorder.py` + `raw/bundle.py` — nahrávání a zápis na disk

**Files:**
- Create: `migration_validator/raw/recorder.py`, `migration_validator/raw/bundle.py`
- Test: `tests/raw/test_recorder.py`, `tests/raw/test_bundle.py`

**Interfaces:**
- Consumes: `RecordedCall`, `canonical_kwargs`, `config_filter` (Task 1).
- Produces:
  - `FACT_KEYS = ("hostname", "model", "version")` — jen facts, které nástroj čte (`detect_platform`, `device_meta`); jiné se nesahají, aby PyEZ nespouštěl další fact RPC.
  - `class SessionRecording` s atributy `calls: list[RecordedCall]`, `facts: dict`, `hostname: str | None`.
  - `class RecordingDevice(device, recording)` — atributy `rpc` (nahrávající), `facts` a `hostname` (průchozí z obaleného zařízení), `recording`.
  - `RAW_FORMAT = 1`, `SESSION_FILE = "session.json"`, `INVENTORY_DIR = "inventory"`, `INVENTORY_YAML = "inventory.yml"`.
  - `class RawFormatError(ValueError)`.
  - `@dataclass Session(kind: str, address: str | None, hostname: str | None, facts: dict, calls: list[RecordedCall], started_at: str | None = None, finished_at: str | None = None, params: dict | None = None, inventory: dict | None = None, baselines: list[str] | None = None, port_filter: str | None = None, tool: dict | None = None, raw_format: int = RAW_FORMAT)`.
  - `write_session(session: Session, target: Path, *, inventory_raw: Path | None = None, inventory_yaml: Path | None = None) -> None` — nejdřív smaže starý `target`, píše do `.tmp-<jméno>` vedle a přejmenuje.
  - `read_session(path: Path) -> Session` (`RawFormatError` pro chybějící/poškozený/neznámý formát).
  - `remove_session(target: Path) -> None`, `has_session(path: Path) -> bool`, `tool_info() -> dict` (`{"version", "commit"}`), `reply_file_name(call) -> str` (`"0001-get_config.xml.gz"`).

- [ ] **Step 1: Write the failing tests**

`tests/raw/test_recorder.py`:

```python
"""RecordingDevice - nahravani na hranici device.rpc (spec 2026-09-23)."""

import pytest
from lxml import etree

from migration_validator.raw.recorder import RecordingDevice, SessionRecording


class _Rpc:
    def get_interface_information(self, **kwargs):
        return etree.fromstring(
            "<interface-information><x>1</x></interface-information>"
        )

    def get_bgp_neighbor_information(self, **kwargs):
        raise RuntimeError("RPC selhalo")

    def get_config(self, **kwargs):
        return etree.fromstring(
            "<rpc-reply><configuration><interfaces/><firewall/></configuration></rpc-reply>"
        )

    def commit_check(self, **kwargs):
        return True


class _Device:
    def __init__(self):
        self.facts = {
            "hostname": "MX1", "model": "MX204", "version": "21.4R3",
            "serialnumber": "S1",
        }
        self.hostname = "172.20.20.4"
        self.rpc = _Rpc()


def _recorded():
    recording = SessionRecording()
    return RecordingDevice(_Device(), recording), recording


def test_records_calls_in_order_with_replies():
    device, recording = _recorded()
    reply = device.rpc.get_interface_information(terse=True)
    device.rpc.get_interface_information(extensive=True)

    assert reply.find("x").text == "1"
    assert [(c.seq, c.rpc, c.kwargs) for c in recording.calls] == [
        (1, "get_interface_information", {"terse": True}),
        (2, "get_interface_information", {"extensive": True}),
    ]
    assert b"<x>1</x>" in recording.calls[0].reply_xml


def test_records_error_and_reraises():
    device, recording = _recorded()
    with pytest.raises(RuntimeError, match="RPC selhalo"):
        device.rpc.get_bgp_neighbor_information()
    assert recording.calls[0].error == {"type": "RuntimeError", "message": "RPC selhalo"}
    assert recording.calls[0].reply_xml is None


def test_reply_is_serialized_before_caller_mutates_it():
    """retrieve_configuration odpoved orezava na miste - nahravka uz musi
    byt serializovana. Zabiji mutanta: recorder drzi referenci na element
    a serializuje az pri zapisu."""
    device, recording = _recorded()
    filter_xml = etree.Element("configuration")
    etree.SubElement(filter_xml, "interfaces")
    etree.SubElement(filter_xml, "firewall")

    reply = device.rpc.get_config(filter_xml=filter_xml, options={})
    root = reply.find(".//configuration")
    root.remove(root.find("firewall"))

    assert b"<firewall/>" in recording.calls[0].reply_xml
    assert recording.calls[0].filter == ["interfaces", "firewall"]
    assert recording.calls[0].kwargs == {"options": {}}


def test_non_xml_reply_is_recorded_as_value():
    device, recording = _recorded()
    assert device.rpc.commit_check() is True
    assert recording.calls[0].reply_value is True
    assert recording.calls[0].reply_xml is None


def test_facts_and_hostname_are_recorded_and_passed_through():
    device, recording = _recorded()
    assert recording.facts == {"hostname": "MX1", "model": "MX204", "version": "21.4R3"}
    assert recording.hostname == "172.20.20.4"
    assert device.facts["serialnumber"] == "S1"
    assert device.hostname == "172.20.20.4"


def test_private_attribute_is_not_an_rpc():
    device, _ = _recorded()
    with pytest.raises(AttributeError):
        device.rpc._private
```

`tests/raw/test_bundle.py`:

```python
"""Raw bundle na disku (spec 2026-09-23, sekce 1)."""

import json

import pytest

from migration_validator import __version__
from migration_validator.raw.bundle import (
    RawFormatError,
    Session,
    has_session,
    read_session,
    tool_info,
    write_session,
)
from migration_validator.raw.calls import RecordedCall


def _session(**overrides):
    calls = [
        RecordedCall(
            seq=1, rpc="get_config", kwargs={"options": {"database": "committed"}},
            filter=["interfaces"], reply_xml=b"<configuration><interfaces/></configuration>",
        ),
        RecordedCall(
            seq=2, rpc="get_bgp_neighbor_information", kwargs={},
            error={"type": "RpcError", "message": "boom"},
        ),
        RecordedCall(seq=3, rpc="commit_check", kwargs={}, reply_value=True),
    ]
    fields = dict(
        kind="capture", address="172.20.20.4", hostname="172.20.20.4",
        facts={"hostname": "MX1", "model": "MX204", "version": "21.4R3"},
        calls=calls, started_at="2026-09-23T10:00:00Z",
        finished_at="2026-09-23T10:01:00Z",
        params={
            "phase": "pre", "port": None, "collectors": None, "ping_count": 5,
            "service_types": None, "profile_name": None,
        },
        inventory={"file": "inventory_MX1_all.yml", "raw": True},
        baselines=[], tool={"version": "0.1.0", "commit": None},
    )
    fields.update(overrides)
    return Session(**fields)


def test_round_trip(tmp_path):
    target = tmp_path / "raw" / "pre_MX1_all"
    write_session(_session(), target)

    assert read_session(target) == _session()
    assert (target / "0001-get_config.xml.gz").is_file()
    data = json.loads((target / "session.json").read_text())
    assert data["raw_format"] == 1
    assert data["calls"][1] == {
        "seq": 2, "rpc": "get_bgp_neighbor_information", "kwargs": {},
        "error": {"type": "RpcError", "message": "boom"},
    }
    assert data["calls"][2]["value"] is True


def test_write_replaces_old_session_completely(tmp_path):
    target = tmp_path / "raw" / "pre_MX1_all"
    write_session(_session(), target)
    (target / "stale.txt").write_text("x")

    write_session(_session(calls=[]), target)

    assert not (target / "stale.txt").exists()
    assert read_session(target).calls == []


def test_failed_write_leaves_no_old_session(tmp_path, monkeypatch):
    """Selhani zapisu nesmi nechat stary raw vedle noveho snimku - upgrade
    by jinak pres novy snapshot pregeneroval predchozi capture.
    Zabiji mutanta: write_session maze stary target az po uspesnem zapisu."""
    target = tmp_path / "raw" / "pre_MX1_all"
    write_session(_session(), target)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("migration_validator.raw.bundle.gzip.open", boom)
    with pytest.raises(OSError, match="disk full"):
        write_session(_session(), target)

    assert not target.exists()
    assert [p.name for p in target.parent.iterdir()] == []


def test_copies_inventory_raw_into_capture_bundle(tmp_path):
    inventory_raw = tmp_path / "raw" / "inventory_MX1_ge_0_0_2"
    write_session(
        _session(kind="inventory", params=None, inventory=None, baselines=None,
                 port_filter="ge-0/0/2"),
        inventory_raw,
    )
    target = tmp_path / "raw" / "pre_MX1_ge_0_0_2"
    write_session(_session(), target, inventory_raw=inventory_raw)

    assert read_session(target / "inventory").port_filter == "ge-0/0/2"


def test_copies_inventory_yaml_when_inventory_has_no_raw(tmp_path):
    yaml_path = tmp_path / "inv.yml"
    yaml_path.write_text("schema_version: 10\n")
    target = tmp_path / "raw" / "pre_MX1_all"

    write_session(
        _session(inventory={"file": "inv.yml", "raw": False}), target,
        inventory_yaml=yaml_path,
    )

    assert (target / "inventory" / "inventory.yml").read_text() == "schema_version: 10\n"


def test_unknown_raw_format_is_rejected(tmp_path):
    target = tmp_path / "raw" / "pre_MX1_all"
    write_session(_session(), target)
    data = json.loads((target / "session.json").read_text())
    data["raw_format"] = 99
    (target / "session.json").write_text(json.dumps(data))

    with pytest.raises(RawFormatError, match="raw_format 99"):
        read_session(target)


def test_corrupt_session_is_raw_format_error(tmp_path):
    target = tmp_path / "raw" / "pre_MX1_all"
    target.mkdir(parents=True)
    (target / "session.json").write_text("{")

    with pytest.raises(RawFormatError):
        read_session(target)


def test_has_session(tmp_path):
    target = tmp_path / "raw" / "pre_MX1_all"
    assert not has_session(target)
    write_session(_session(), target)
    assert has_session(target)


def test_tool_info_has_version_and_commit_key():
    info = tool_info()
    assert info["version"] == __version__
    assert "commit" in info
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/raw/test_recorder.py tests/raw/test_bundle.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'migration_validator.raw.recorder'`

- [ ] **Step 3: Write `raw/recorder.py`**

```python
"""Nahravani NETCONF session na hranici device.rpc (spec 2026-09-23).

Obal zapise kazde volani v poradi: jmeno RPC, kanonicke argumenty, a bud
odpoved (serializovanou hned pri volani - volajici ji smi upravit), nebo
chybu (jmeno tridy + text). Zaznam zije v pameti; na disk ho zapisuje
raw.bundle az po ulozeni snapshotu/inventory.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.raw.calls import RecordedCall, canonical_kwargs, config_filter

# Jen facts, ktere nastroj cte (detect_platform, device_meta). Cteni dalsich
# by na PyEZ spoustelo dalsi fact RPC.
FACT_KEYS = ("hostname", "model", "version")


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class SessionRecording:
    """Volani jedne session v poradi + facts a hostname zarizeni."""

    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self.facts: dict[str, Any] = {}
        self.hostname: str | None = None


class RecordingDevice:
    """Obal kolem PyEZ Device: `rpc` nahrava, `facts`/`hostname` prochazi."""

    def __init__(self, device: Any, recording: SessionRecording) -> None:
        self._device = device
        self.recording = recording
        facts = getattr(device, "facts", {}) or {}
        recording.facts = {key: jsonable(facts.get(key)) for key in FACT_KEYS}
        recording.hostname = jsonable(getattr(device, "hostname", None))
        self.rpc = _RecordingRpc(device.rpc, recording)

    @property
    def facts(self) -> Any:
        return getattr(self._device, "facts", {})

    @property
    def hostname(self) -> Any:
        return getattr(self._device, "hostname", None)


class _RecordingRpc:
    def __init__(self, rpc: Any, recording: SessionRecording) -> None:
        self._rpc = rpc
        self._recording = recording

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        target = getattr(self._rpc, name)

        def call(**kwargs: Any) -> Any:
            entry = RecordedCall(
                seq=len(self._recording.calls) + 1,
                rpc=name,
                kwargs=canonical_kwargs(name, kwargs),
                filter=config_filter(name, kwargs),
            )
            try:
                reply = target(**kwargs)
            except Exception as error:  # noqa: BLE001 - nahraje se a propadne dal
                entry.error = {"type": type(error).__name__, "message": str(error)}
                self._recording.calls.append(entry)
                raise
            if isinstance(reply, etree._Element):
                entry.reply_xml = etree.tostring(reply)
            else:
                entry.reply_value = jsonable(reply)
            self._recording.calls.append(entry)
            return reply

        return call
```

- [ ] **Step 4: Write `raw/bundle.py`**

```python
"""Raw bundle na disku: adresar session se session.json a gzip odpovedmi.

Raw format je jedina vec, ktera se nikdy nesmi sama potrebovat upgradovat:
kazda budouci verze nastroje musi precist kazdy starsi raw_format; zmena
formatu = ctecka umi obe verze, stare bundly se neprevadeji. Bundle proto
nese jen data ze zarizeni (odpovedi, chyby, facts) a vstupy capture, nic
odvozeneho (spec 2026-09-23, sekce 1).
"""

from __future__ import annotations

import functools
import gzip
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from migration_validator import __version__
from migration_validator.raw.calls import RecordedCall

RAW_FORMAT = 1
SESSION_FILE = "session.json"
INVENTORY_DIR = "inventory"
INVENTORY_YAML = "inventory.yml"


class RawFormatError(ValueError):
    """session.json chybi, je poskozeny nebo ma neznamy raw_format."""


@dataclass
class Session:
    kind: str  # capture | inventory
    address: str | None
    hostname: str | None
    facts: dict[str, Any]
    calls: list[RecordedCall]
    started_at: str | None = None
    finished_at: str | None = None
    params: dict[str, Any] | None = None
    inventory: dict[str, Any] | None = None
    baselines: list[str] | None = None
    port_filter: str | None = None
    tool: dict[str, Any] | None = None
    raw_format: int = RAW_FORMAT


@functools.lru_cache(maxsize=1)
def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


def tool_info() -> dict[str, Any]:
    """Verze nastroje pro session.json (__version__ je staticke 0.1.0)."""
    return {"version": __version__, "commit": _git_commit()}


def reply_file_name(call: RecordedCall) -> str:
    return f"{call.seq:04d}-{call.rpc}.xml.gz"


def has_session(path: Path) -> bool:
    return (Path(path) / SESSION_FILE).is_file()


def remove_session(target: Path) -> None:
    target = Path(target)
    if target.exists():
        shutil.rmtree(target)


def _call_to_json(call: RecordedCall, directory: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"seq": call.seq, "rpc": call.rpc, "kwargs": call.kwargs}
    if call.filter is not None:
        entry["filter"] = call.filter
    if call.error is not None:
        entry["error"] = call.error
    elif call.reply_xml is not None:
        name = reply_file_name(call)
        with gzip.open(directory / name, "wb") as handle:
            handle.write(call.reply_xml)
        entry["reply"] = name
    else:
        entry["value"] = call.reply_value
    return entry


def write_session(
    session: Session,
    target: Path,
    *,
    inventory_raw: Path | None = None,
    inventory_yaml: Path | None = None,
) -> None:
    """Zapise session do `target`.

    Stary target se smaze jako prvni: selhani zapisu nesmi nechat raw
    predchoziho capture vedle noveho souboru. Zapis jde do sourozence
    `.tmp-<jmeno>` a na misto se prejmenuje. `inventory_raw` se zkopiruje
    jako `inventory/`, jinak `inventory_yaml` jako `inventory/inventory.yml`.
    """
    target = Path(target)
    remove_session(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".tmp-{target.name}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        data = {
            "raw_format": session.raw_format,
            "kind": session.kind,
            "tool": session.tool,
            "address": session.address,
            "hostname": session.hostname,
            "facts": session.facts,
            "started_at": session.started_at,
            "finished_at": session.finished_at,
            "params": session.params,
            "inventory": session.inventory,
            "baselines": session.baselines,
            "port_filter": session.port_filter,
            "calls": [_call_to_json(call, tmp) for call in session.calls],
        }
        (tmp / SESSION_FILE).write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if inventory_raw is not None:
            shutil.copytree(inventory_raw, tmp / INVENTORY_DIR)
        elif inventory_yaml is not None:
            (tmp / INVENTORY_DIR).mkdir()
            shutil.copy2(inventory_yaml, tmp / INVENTORY_DIR / INVENTORY_YAML)
        os.replace(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _call_from_json(entry: dict[str, Any], path: Path) -> RecordedCall:
    reply_xml = None
    if "reply" in entry:
        with gzip.open(path / entry["reply"], "rb") as handle:
            reply_xml = handle.read()
    return RecordedCall(
        seq=entry["seq"],
        rpc=entry["rpc"],
        kwargs=entry.get("kwargs") or {},
        filter=entry.get("filter"),
        reply_xml=reply_xml,
        reply_value=entry.get("value"),
        error=entry.get("error"),
    )


def read_session(path: Path) -> Session:
    path = Path(path)
    try:
        data = json.loads((path / SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RawFormatError(f"{path}: session.json nejde precist ({error})") from error
    raw_format = data.get("raw_format") if isinstance(data, dict) else None
    if raw_format != RAW_FORMAT:
        raise RawFormatError(f"{path}: neznamy raw_format {raw_format!r}")
    try:
        return Session(
            kind=data["kind"],
            address=data.get("address"),
            hostname=data.get("hostname"),
            facts=data.get("facts") or {},
            calls=[_call_from_json(entry, path) for entry in data["calls"]],
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            params=data.get("params"),
            inventory=data.get("inventory"),
            baselines=data.get("baselines"),
            port_filter=data.get("port_filter"),
            tool=data.get("tool"),
            raw_format=raw_format,
        )
    except (KeyError, TypeError, OSError) as error:
        raise RawFormatError(
            f"{path}: poskozeny raw zaznam ({type(error).__name__}: {error})"
        ) from error
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/raw -q -p no:warnings`
Expected: PASS

- [ ] **Step 6: Run the mutants**

1. Lazy serializace: v `RecordedCall` (Task 1) dočasně přejmenuj pole `reply_xml` na `_reply_element: Any = None` a přidej property `reply_xml`, která vrací `etree.tostring(self._reply_element)` (nebo `None`); v `_RecordingRpc.call` ulož `entry._reply_element = reply` místo serializace. Typy sedí (property vrací bytes), takže `test_reply_is_serialized_before_caller_mutates_it` musí FAIL na chybějícím `<firewall/>` — ne na `TypeError`. Pokud spadne na čemkoliv jiném než na tom assertu, mutant je špatně postavený; oprav ho, ne test. Vrať.
2. Ve `write_session` přesuň `remove_session(target)` těsně před `os.replace(tmp, target)` → `test_failed_write_leaves_no_old_session` musí FAIL. Vrať.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/raw/recorder.py migration_validator/raw/bundle.py tests/raw/test_recorder.py tests/raw/test_bundle.py
git commit -m "feat(raw): record NETCONF sessions and store them as raw bundles

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `raw/replay.py` — `ReplayDevice`

**Files:**
- Create: `migration_validator/raw/replay.py`
- Modify: `migration_validator/parsers/__init__.py` (přidat `parser_hierarchies`)
- Test: `tests/raw/test_replay.py`

**Interfaces:**
- Consumes: `Session` (Task 2), `RecordedCall`, `NotRecorded`, `NOT_RECORDED`, `call_key`, `canonical_kwargs`, `config_filter` (Task 1), `find_configuration_root` z `parsers/core.py`.
- Produces:
  - `parser_hierarchies() -> frozenset[str]` v `migration_validator/parsers/__init__.py` — sjednocení `CONFIG_HIERARCHIES` všech platformních parserů.
  - `class RecordedRpcError(Exception)`; `recorded_error(error: dict) -> Exception` (dynamická podtřída se jménem nahrané třídy).
  - `class ReplayDevice(session: Session, *, required_config: frozenset[str] | None = None)` — atributy `facts`, `hostname`, `rpc`. `required_config` default = `parser_hierarchies()`.

- [ ] **Step 1: Write the failing test**

`tests/raw/test_replay.py`:

```python
"""ReplayDevice - prehravani nahrane session (spec 2026-09-23, sekce 2)."""

import pytest
from lxml import etree

from migration_validator.connection.junos import detect_platform
from migration_validator.parsers import parser_hierarchies
from migration_validator.raw.bundle import Session
from migration_validator.raw.calls import NotRecorded, RecordedCall
from migration_validator.raw.replay import RecordedRpcError, ReplayDevice


def _session(calls):
    return Session(
        kind="capture", address="172.20.20.4", hostname="172.20.20.4",
        facts={"hostname": "MX1", "model": "MX204", "version": "21.4R3"},
        calls=calls,
    )


def _xml(seq, rpc, kwargs, body, filter=None):
    return RecordedCall(seq=seq, rpc=rpc, kwargs=kwargs, filter=filter, reply_xml=body.encode())


def test_serves_recorded_reply_by_rpc_and_kwargs():
    device = ReplayDevice(_session([
        _xml(1, "get_interface_information", {"terse": True}, "<t/>"),
        _xml(2, "get_interface_information", {"extensive": True}, "<e/>"),
    ]))
    assert device.rpc.get_interface_information(extensive=True).tag == "e"
    assert device.rpc.get_interface_information(terse=True).tag == "t"


def test_repeated_call_is_served_in_order_and_last_repeats():
    rpc = ReplayDevice(_session([
        _xml(1, "get_instance_information", {"brief": True}, "<a/>"),
        _xml(2, "get_instance_information", {"brief": True}, "<b/>"),
    ])).rpc
    assert [rpc.get_instance_information(brief=True).tag for _ in range(3)] == ["a", "b", "b"]


def test_unrecorded_call_raises_not_recorded():
    with pytest.raises(NotRecorded, match="neni v raw zaznamu: get_bgp_neighbor_information"):
        ReplayDevice(_session([])).rpc.get_bgp_neighbor_information()


def test_changed_kwargs_are_not_recorded():
    device = ReplayDevice(_session([
        _xml(1, "ping", {"count": "5", "host": "10.0.0.1", "rapid": True}, "<ping-results/>"),
    ]))
    assert device.rpc.ping(host="10.0.0.1", count="5", rapid=True).tag == "ping-results"
    with pytest.raises(NotRecorded):
        device.rpc.ping(host="10.0.0.2", count="5", rapid=True)


def test_recorded_error_keeps_class_name_and_message():
    """Text statusu collectoru ("RpcError: timeout") musi po replayi vyjit
    stejne jako pri zivem capture."""
    call = RecordedCall(
        seq=1, rpc="get_bfd_session_information", kwargs={},
        error={"type": "RpcError", "message": "timeout"},
    )
    with pytest.raises(RecordedRpcError) as info:
        ReplayDevice(_session([call])).rpc.get_bfd_session_information()
    assert type(info.value).__name__ == "RpcError"
    assert str(info.value) == "timeout"


def test_facts_and_hostname_come_from_session():
    device = ReplayDevice(_session([]))
    assert device.facts["model"] == "MX204"
    assert device.hostname == "172.20.20.4"
    assert detect_platform(device) == "junos"


CONFIG = (
    "<rpc-reply><data><configuration>"
    "<interfaces><interface><name>ge-0/0/0</name></interface></interfaces>"
    "<class-of-service><interfaces><interface><name>ge-0/0/9</name></interface>"
    "</interfaces></class-of-service>"
    "</configuration></data></rpc-reply>"
)
RECORDED_FILTER = ["interfaces", "routing-options", "firewall", "class-of-service"]


def _filter(*names):
    root = etree.Element("configuration")
    for name in names:
        etree.SubElement(root, name)
    return root


def _config_device(required):
    return ReplayDevice(
        _session([_xml(1, "get_config", {"options": {}}, CONFIG, filter=RECORDED_FILTER)]),
        required_config=frozenset(required),
    )


def _children(reply):
    return [child.tag for child in reply.find(".//configuration")]


def test_get_config_is_cut_to_requested_hierarchies():
    device = _config_device({"interfaces", "routing-options"})
    reply = device.rpc.get_config(filter_xml=_filter("interfaces"), options={})
    assert _children(reply) == ["interfaces"]


def test_requested_but_empty_hierarchy_is_not_an_error():
    """Junos prazdnou hierarchii v odpovedi vynecha: routing-options byla
    ve filtru zadana, jen v konfiguraci nic nema. Zabiji mutanta, ktery
    chybejici hierarchii hleda v odpovedi misto v nahranem filtru."""
    device = _config_device({"interfaces", "routing-options"})
    reply = device.rpc.get_config(
        filter_xml=_filter("interfaces", "routing-options"), options={}
    )
    assert _children(reply) == ["interfaces"]


def test_hierarchy_outside_recorded_filter_is_not_recorded():
    device = _config_device({"interfaces", "chassis"})
    with pytest.raises(NotRecorded, match="konfigurace nema hierarchii chassis"):
        device.rpc.get_config(filter_xml=_filter("interfaces", "chassis"), options={})


def test_optional_hierarchy_outside_filter_is_tolerated():
    """Hierarchie, kterou zadny parser nepotrebuje (budouci RAW_EXTRA),
    starsi capture neblokuje."""
    device = _config_device({"interfaces"})
    reply = device.rpc.get_config(filter_xml=_filter("interfaces", "services"), options={})
    assert _children(reply) == ["interfaces"]


def test_default_required_config_is_union_of_parser_hierarchies():
    hierarchies = parser_hierarchies()
    assert {"interfaces", "routing-instances", "protocols", "bridge-domains", "vlans"} <= hierarchies
    assert "policy-options" not in hierarchies


def test_private_attribute_is_not_an_rpc():
    with pytest.raises(AttributeError):
        ReplayDevice(_session([])).rpc._private
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/raw/test_replay.py -q -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'parser_hierarchies'`

- [ ] **Step 3: Add `parser_hierarchies` to `migration_validator/parsers/__init__.py`**

Na konec souboru:

```python
def parser_hierarchies() -> frozenset[str]:
    """Hierarchie, ktere potrebuje aspon jeden parser. Replay podle toho
    rozlisuje povinnou hierarchii (chybi -> capture nejde pregenerovat) od
    hierarchie, ktera se jen nahrava (RAW_EXTRA_HIERARCHIES)."""
    return frozenset(
        hierarchy
        for parser_cls in _PLATFORM_PARSERS.values()
        for hierarchy in parser_cls.CONFIG_HIERARCHIES
    )
```

- [ ] **Step 4: Write `raw/replay.py`**

```python
"""ReplayDevice - vraci nahrane odpovedi misto zarizeni (spec 2026-09-23).

Co v nahravce neni, se nikdy nevydava za vysledek: vyhodi se NotRecorded
(collector -> status error, ping -> sent=0). Nahrana chyba se vyhodi znovu
se stejnym jmenem tridy a textem.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.parsers import parser_hierarchies
from migration_validator.parsers.core import find_configuration_root
from migration_validator.raw.bundle import Session
from migration_validator.raw.calls import (
    NOT_RECORDED,
    NotRecorded,
    RecordedCall,
    call_key,
    canonical_kwargs,
    config_filter,
)


class RecordedRpcError(Exception):
    """Zaklad pro nahrane chyby; podtrida nese jmeno puvodni tridy."""


def recorded_error(error: dict[str, str]) -> Exception:
    cls = type(str(error.get("type") or "RpcError"), (RecordedRpcError,), {})
    return cls(error.get("message", ""))


def _answer(entry: RecordedCall) -> Any:
    if entry.error is not None:
        raise recorded_error(entry.error)
    return entry.reply()


class ReplayDevice:
    def __init__(
        self, session: Session, *, required_config: frozenset[str] | None = None
    ) -> None:
        self.facts = dict(session.facts)
        self.hostname = session.hostname
        self.rpc = _ReplayRpc(
            session.calls,
            required_config if required_config is not None else parser_hierarchies(),
        )


class _ReplayRpc:
    def __init__(self, calls: list[RecordedCall], required: frozenset[str]) -> None:
        self._by_key: dict[str, list[RecordedCall]] = {}
        self._configs: list[RecordedCall] = []
        for call in calls:
            if call.rpc == "get_config":
                self._configs.append(call)
            else:
                self._by_key.setdefault(call_key(call.rpc, call.kwargs), []).append(call)
        self._served: dict[str, int] = {}
        self._required = required

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def call(**kwargs: Any) -> Any:
            if name == "get_config":
                return self._config(kwargs)
            key = call_key(name, canonical_kwargs(name, kwargs))
            entries = self._by_key.get(key)
            if not entries:
                raise NotRecorded(f"{NOT_RECORDED}: {name}")
            index = self._served.get(key, 0)
            self._served[key] = index + 1
            return _answer(entries[min(index, len(entries) - 1)])

        return call

    def _config(self, kwargs: dict[str, Any]) -> Any:
        requested = config_filter("get_config", kwargs)
        if requested is None or not self._configs:
            raise NotRecorded(f"{NOT_RECORDED}: get_config")
        entry = self._configs[0]
        recorded = set(entry.filter or ())
        # Rozhoduje nahrany filtr, ne odpoved - Junos prazdnou hierarchii
        # vynecha, takze chybejici v odpovedi muze byt jen prazdna.
        missing = [h for h in requested if h in self._required and h not in recorded]
        if missing:
            raise NotRecorded(f"konfigurace nema hierarchii {', '.join(missing)}")
        reply = _answer(entry)
        root = find_configuration_root(reply)
        keep = set(requested)
        for child in list(root):
            if isinstance(child.tag, str) and etree.QName(child).localname not in keep:
                root.remove(child)
        return reply
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/raw -q -p no:warnings`
Expected: PASS

- [ ] **Step 6: Run the mutant**

V `_config` nahraď kontrolu `h not in recorded` kontrolou proti odpovědi (`h not in {etree.QName(c).localname for c in find_configuration_root(entry.reply()) if isinstance(c.tag, str)}`) → `test_requested_but_empty_hierarchy_is_not_an_error` musí FAIL. Vrať.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/raw/replay.py migration_validator/parsers/__init__.py tests/raw/test_replay.py
git commit -m "feat(raw): replay device serving recorded replies, NotRecorded on miss

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Ping — nenahraný cíl je „neodeslán“, ne „neodpověděl“

**Files:**
- Modify: `migration_validator/probes/ping.py` (`run_ping`, ř. ~475-515)
- Modify: `migration_validator/checks/reachability.py` (`_ping_findings`, větev `if sent == 0:`, ř. ~496)
- Test: `tests/probes/test_ping.py`, `tests/checks/test_reachability.py`

**Interfaces:**
- Consumes: `NotRecorded`, `NOT_RECORDED` (Task 1).
- Produces: probe záznam `{"sent": 0, "received": 0, "loss_percent": None, "rtt_avg_ms": None, "error": "neni v raw zaznamu", ...PingTarget.to_dict()}`; SKIP zpráva `"<cil>: ping neodeslan (<error>)"`, když má probe `error`.

- [ ] **Step 1: Write the failing tests**

Na konec `tests/probes/test_ping.py`:

```python
class _NotRecordedRpc:
    def ping(self, **kwargs):
        from migration_validator.raw.calls import NotRecorded

        raise NotRecorded("neni v raw zaznamu: ping")


class _NotRecordedDevice:
    rpc = _NotRecordedRpc()


def test_run_ping_not_recorded_is_not_sent_not_unreachable():
    """Replay miss nesmi skoncit v catch-all (sent=count, received=0 =
    vymysleny BROKEN "neodpovedel"). Zabiji mutanta: odstraneny
    `except NotRecorded` v run_ping."""
    target = PingTarget("svc:X:IPVPN", "198.11.13.2", None, "arp", 4)

    record = run_ping(_NotRecordedDevice(), target, count=5)

    assert record["sent"] == 0
    assert record["received"] == 0
    assert record["loss_percent"] is None
    assert record["error"] == "neni v raw zaznamu"
    assert record["target"] == "198.11.13.2"
```

Do `tests/checks/test_reachability.py` hned za `test_ping_zero_sent_is_skip`:

```python
def test_ping_not_recorded_names_the_reason():
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "10.0.0.2", "family": 4, "sent": 0, "received": 0,
                    "error": "neni v raw zaznamu",
                }
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.SKIP
    assert result.message == "10.0.0.2: ping neodeslan (neni v raw zaznamu)"
    assert result.value == "10.0.0.2 neodeslan"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/probes/test_ping.py tests/checks/test_reachability.py -q -p no:warnings -k "not_recorded"`
Expected: FAIL — `record["sent"] == 5` a `message == "10.0.0.2: ping neodeslan"`.

- [ ] **Step 3: Implement**

`migration_validator/probes/ping.py` — import nahoře:

```python
from migration_validator.raw.calls import NOT_RECORDED, NotRecorded
```

V `run_ping` před stávající `except Exception as error:` vlož:

```python
    except NotRecorded:
        # Replay (mig-validate upgrade): cil v puvodnim capture nahrany neni.
        # sent=0 = "ping neodeslan" (SKIP); catch-all nize by zapsal
        # sent=count/received=0, tedy vymysleny BROKEN.
        record.update(
            {
                "sent": 0,
                "received": 0,
                "loss_percent": None,
                "rtt_avg_ms": None,
                "error": NOT_RECORDED,
            }
        )
```

`migration_validator/checks/reachability.py` ve větvi `if sent == 0:` nahraď `f"{target}: ping neodeslan",` za:

```python
            reason = probe.get("error")
            message = f"{target}: ping neodeslan" + (f" ({reason})" if reason else "")
```

a do `Finding(` předej `message` místo původního f-stringu (ostatní argumenty beze změny).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/probes tests/checks -q -p no:warnings`
Expected: PASS

- [ ] **Step 5: Run the mutant**

Smaž blok `except NotRecorded:` → `test_run_ping_not_recorded_is_not_sent_not_unreachable` musí FAIL (`sent == 5`). Vrať.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/probes/ping.py migration_validator/checks/reachability.py tests/probes/test_ping.py tests/checks/test_reachability.py
git commit -m "fix(ping): an unrecorded replay target is not sent, never unreachable

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `retrieve_configuration` — rozšířený filtr, parser dostane jen své hierarchie

**Files:**
- Modify: `migration_validator/parsers/core.py:2028-2048` (`retrieve_configuration`) + konstanta `RAW_EXTRA_HIERARCHIES` těsně nad funkcí
- Modify: `tests/parsers/test_config_filter.py`
- Test: `tests/raw/test_inventory_round_trip.py` (nový)

**Interfaces:**
- Consumes: `RecordingDevice`, `SessionRecording` (Task 2), `write_session`, `read_session`, `Session` (Task 2), `ReplayDevice` (Task 3), `generate_inventory` (`runs/services.py`).
- Produces: `RAW_EXTRA_HIERARCHIES = ("policy-options", "firewall", "class-of-service")` v `parsers/core.py`; `retrieve_configuration(device, hierarchies)` posílá filtr `hierarchies + extra (bez duplicit)` a vrací kořen `configuration` jen s `hierarchies`.

- [ ] **Step 1: Update and extend `tests/parsers/test_config_filter.py`**

V `test_retrieve_configuration_sklada_filtr_z_hierarchii` změň očekávání (záměrně — spec 2026-09-23):

```python
    assert children == [
        "interfaces", "bridge-domains", "policy-options", "firewall", "class-of-service",
    ]
```

Na konec souboru přidej:

```python
def test_retrieve_configuration_nezdvojuje_hierarchii_z_extra():
    device = _FakeDevice()

    retrieve_configuration(device, hierarchies=("interfaces", "firewall"))

    children = [child.tag for child in device.rpc.filter_xml]
    assert children == ["interfaces", "firewall", "policy-options", "class-of-service"]


COS_CONFIG = (
    "<rpc-reply><data><configuration>"
    "<interfaces><interface><name>ge-0/0/0</name></interface></interfaces>"
    "<firewall><family><inet><filter><name>F</name></filter></inet></family></firewall>"
    "<class-of-service><interfaces><interface><name>ge-0/0/9</name></interface>"
    "</interfaces></class-of-service>"
    "</configuration></data></rpc-reply>"
)


class _CosRpc:
    def get_config(self, filter_xml, options):
        return etree.XML(COS_CONFIG)


class _CosDevice:
    def __init__(self):
        self.facts = {"hostname": "MX1", "model": "MX204", "version": "21.4R3"}
        self.hostname = "172.20.20.4"
        self.rpc = _CosRpc()


def test_parser_dostane_jen_sve_hierarchie():
    """class-of-service interfaces interface ma stejna jmena elementu jako
    top-level interfaces - parser ji nesmi videt. Zabiji mutanta: orez
    v retrieve_configuration vynechan."""
    root = retrieve_configuration(_CosDevice(), hierarchies=("interfaces",))
    assert [child.tag for child in root] == ["interfaces"]


def test_nahravka_nese_extra_hierarchie_i_po_orezu():
    from migration_validator.raw.recorder import RecordingDevice, SessionRecording

    recording = SessionRecording()
    retrieve_configuration(RecordingDevice(_CosDevice(), recording), hierarchies=("interfaces",))

    assert b"class-of-service" in recording.calls[0].reply_xml
    assert b"<firewall>" in recording.calls[0].reply_xml
    assert recording.calls[0].filter == [
        "interfaces", "policy-options", "firewall", "class-of-service",
    ]
```

`tests/raw/test_inventory_round_trip.py`:

```python
"""Inventory z nahrane konfigurace vyjde pri replayi stejne (spec 2026-09-23)."""

import yaml
from lxml import etree

from migration_validator.raw.bundle import Session, read_session, write_session
from migration_validator.raw.recorder import RecordingDevice, SessionRecording
from migration_validator.raw.replay import ReplayDevice
from migration_validator.runs.services import generate_inventory

CONFIG = """
<rpc-reply><data><configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/0</name>
      <unit>
        <name>100</name>
        <description>INTERNET-CPE100-NNI</description>
        <family><inet><address><name>152.11.100.1/30</name></address></inet></family>
      </unit>
    </interface>
    <interface>
      <name>ge-0/0/1</name>
      <unit>
        <name>200</name>
        <description>INTERNET-CPE200-NNI</description>
        <family><inet><address><name>152.11.200.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <firewall><family><inet><filter><name>F</name></filter></inet></family></firewall>
</configuration></data></rpc-reply>
"""


class _Rpc:
    def get_config(self, filter_xml, options):
        return etree.fromstring(CONFIG)


class _Device:
    facts = {"hostname": "MX1", "model": "MX204", "version": "21.4R3"}
    hostname = "172.20.20.4"
    rpc = _Rpc()


def test_inventory_replays_identically(tmp_path):
    recording = SessionRecording()
    live = tmp_path / "live.yml"
    generate_inventory(RecordingDevice(_Device(), recording), "junos", live, "ge-0/0/0")
    raw_dir = tmp_path / "raw" / "inventory_MX1_ge_0_0_0"
    write_session(
        Session(
            kind="inventory", address="172.20.20.4", hostname=recording.hostname,
            facts=recording.facts, calls=recording.calls, port_filter="ge-0/0/0",
        ),
        raw_dir,
    )

    session = read_session(raw_dir)
    replayed = tmp_path / "replayed.yml"
    generate_inventory(ReplayDevice(session), "junos", replayed, session.port_filter)

    assert yaml.safe_load(replayed.read_text()) == yaml.safe_load(live.read_text())
    assert "ge-0/0/1" not in replayed.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/parsers/test_config_filter.py tests/raw/test_inventory_round_trip.py -q -p no:warnings`
Expected: FAIL — filtr bez extra hierarchií; parser vidí `class-of-service`.

- [ ] **Step 3: Implement in `migration_validator/parsers/core.py`**

Nad `retrieve_configuration` přidej:

```python
# Hierarchie navic, ktere se jen nahravaji do raw zaznamu (spec 2026-09-23):
# budouci parser z nich muze starsi capture pregenerovat. Parser je
# nedostane - retrieve_configuration je z odpovedi odstrihne.
RAW_EXTRA_HIERARCHIES = ("policy-options", "firewall", "class-of-service")
```

Tělo `retrieve_configuration` nahraď:

```python
    """
    Načte konfiguraci potřebnou pro klasifikaci služeb.

    Filtr se skládá z hierarchií dané platformy (CONFIG_HIERARCHIES
    parseru) plus RAW_EXTRA_HIERARCHIES - NETCONF server odmítne filtr
    s hierarchií, kterou schéma platformy nezná (např. vlans na MX).
    Nahrávka (RecordingDevice) dostane celou odpověď; parser jen své
    hierarchie - class-of-service interfaces interface má stejná jména
    elementů jako top-level interfaces.
    """

    config_filter = etree.Element("configuration")

    extra = tuple(h for h in RAW_EXTRA_HIERARCHIES if h not in hierarchies)
    for hierarchy in (*hierarchies, *extra):
        etree.SubElement(config_filter, hierarchy)

    response = device.rpc.get_config(
        filter_xml=config_filter, options={"database": "committed", "inherit": ""}
    )

    root = find_configuration_root(response)
    wanted = set(hierarchies)
    for child in list(root):
        if isinstance(child.tag, str) and etree.QName(child).localname not in wanted:
            root.remove(child)
    return root
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q -p no:warnings`
Expected: PASS (celá sada — `tests/runs/test_services.py` monkeypatchuje `retrieve_configuration`, nic jiného se nemění)

- [ ] **Step 5: Run the mutant**

Smaž cyklus `for child in list(root): ...` → `test_parser_dostane_jen_sve_hierarchie` musí FAIL. Vrať.

- [ ] **Step 6: Lab check — both platforms accept the extended filter**

Jediný neověřený vnější fakt vlny: NETCONF odmítne hierarchii, kterou schéma platformy nezná (`vlans` na MX, `b01078f`). Ověř hned, ne až v Tasku 14 — Tasky 6-13 na tom stojí. Jen čtení:

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
for host in 172.20.20.4 172.20.20.5; do
.venv/bin/python - "$host" <<'PY'
import os, sys
from jnpr.junos import Device
from migration_validator.connection.junos import detect_platform
from migration_validator.parsers import parser_for_platform
from migration_validator.parsers.core import retrieve_configuration
from migration_validator.raw.recorder import RecordingDevice, SessionRecording

host = sys.argv[1]
with Device(host=host, user="admin", passwd=os.environ["MIG_LAB_PASSWORD"], port=830) as dev:
    recording = SessionRecording()
    device = RecordingDevice(dev, recording)
    platform = detect_platform(device)
    root = retrieve_configuration(device, parser_for_platform(platform).CONFIG_HIERARCHIES)
    call = recording.calls[0]
    print(host, platform, "error:", call.error, "filter:", call.filter)
    print("  parser vidi:", sorted({child.tag for child in root}))
    for name in ("policy-options", "firewall", "class-of-service"):
        print("  nahravka", name, f"<{name}".encode() in (call.reply_xml or b""))
PY
done
```

Expected: pro oba hosty `error: None`, filtr končí `policy-options, firewall, class-of-service`, „parser vidi“ neobsahuje žádnou z nich. `False` u nahrávky znamená jen prázdnou hierarchii v laborce (v pořádku). Pokud NETCONF filtr odmítne (`RpcError`), **STOP** — nahlas přesnou chybu; filtr sám neupravuj (rozsah konfigurace je rozhodnutí specu). Výstup vlož do reportu tasku.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/parsers/core.py tests/parsers/test_config_filter.py tests/raw/test_inventory_round_trip.py
git commit -m "feat(parsers): fetch policy-options, firewall, class-of-service for raw retention

The parser still sees only its own hierarchies.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `capture_device(finished_at=)`, `api.capture(recorder=)`, pryč `--record-raw`

**Files:**
- Modify: `migration_validator/capture.py` (smazat `_record` a import `etree`; signatura `capture_device`)
- Modify: `migration_validator/api.py:37-73` (`capture`)
- Modify: `migration_validator/runs/orchestrate.py` (parametr `record_raw` z `capture_into_run` a z volání `api.capture`)
- Modify: `migration_validator/cli.py` (`capture.add_argument("--record-raw")` a oba `record_raw=args.record_raw`)
- Modify: `tests/test_capture.py` (smazat testy `--record-raw`)
- Test: `tests/raw/test_capture_round_trip.py` (nový)

**Interfaces:**
- Consumes: `RecordingDevice`, `SessionRecording`, `Session`, `write_session`, `read_session` (Task 2), `ReplayDevice` (Task 3), ping `NotRecorded` (Task 4).
- Produces:
  - `capture_device(device, address, *, inventory=None, collector_names=None, phase=None, ping_count=DEFAULT_COUNT, now=None, finished_at=None, baselines=None, service_types=None, on_progress=None, profile_name=None) -> Snapshot` — `finished_at` přebije `now` pro `CaptureMeta.finished_at`.
  - `api.capture(host, *, inventory=None, options=None, collectors=None, phase=None, ping_count=5, baselines=None, service_types=None, on_progress=None, profile_name=None, recorder: SessionRecording | None = None) -> Snapshot`.
  - `capture_into_run(...)` už nemá `record_raw`.

- [ ] **Step 1: Write the failing test**

`tests/raw/test_capture_round_trip.py`:

```python
"""Zivy capture pres RecordingDevice a jeho replay dají stejny snapshot
(spec 2026-09-23, sekce 6 - hlavni pojistka, ze replay neodboci)."""

from pathlib import Path

from lxml import etree

from migration_validator import api
from migration_validator.capture import capture_device
from migration_validator.models.inventory import load_inventory
from migration_validator.models.result import Status
from migration_validator.raw.bundle import Session, read_session, write_session
from migration_validator.raw.recorder import RecordingDevice, SessionRecording
from migration_validator.raw.replay import ReplayDevice

NOW = "2026-09-23T10:00:00Z"
DONE = "2026-09-23T10:02:00Z"
INVENTORY_4 = Path(__file__).resolve().parents[1] / "fixtures" / "172.20.20.4.yml"
COLLECTORS = ["interfaces", "arp", "bgp", "evpn_vpws", "evpn_instance", "evpn_mac"]

INTERFACES_XML = """
<interface-information>
  <physical-interface>
    <name>ge-0/0/2</name>
    <admin-status>up</admin-status>
    <oper-status>up</oper-status>
    <logical-interface>
      <name>ge-0/0/2.113</name>
      <oper-status>up</oper-status>
      <transit-traffic-statistics>
        <input-pps>412</input-pps>
        <output-pps>388</output-pps>
      </transit-traffic-statistics>
    </logical-interface>
  </physical-interface>
</interface-information>
"""

ARP_XML = """
<arp-table-information>
  <arp-table-entry>
    <ip-address>198.11.13.2</ip-address>
    <mac-address>00:11:22:33:44:55</mac-address>
    <interface-name>ge-0/0/2.113</interface-name>
  </arp-table-entry>
</arp-table-information>
"""

PING_XML = """
<ping-results>
  <probe-results-summary>
    <probes-sent>5</probes-sent>
    <responses-received>5</responses-received>
    <packet-loss>0</packet-loss>
    <rtt-average>1240</rtt-average>
  </probe-results-summary>
  <ping-success/>
</ping-results>
"""

RESPONSES = {
    "get_interface_information": INTERFACES_XML,
    "get_arp_table_information": ARP_XML,
    "get_bgp_neighbor_information": "<bgp-information/>",
    "get_evpn_vpws_information": "<evpn-vpws-information/>",
    "get_evpn_instance_information": "<evpn-instance-information/>",
    "get_bridge_mac_table": "<l2ald-rtb-macdb/>",
    "get_evpn_mac_table": "<l2ald-rtb-macdb/>",
}


class _Rpc:
    def __init__(self, failing=()):
        self.failing = set(failing)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.failing:
            def boom(**kwargs):
                raise RuntimeError(f"RPC {name} selhalo")
            return boom
        if name == "ping":
            return lambda **kwargs: etree.fromstring(PING_XML)
        if name not in RESPONSES:
            raise AttributeError(name)
        return lambda **kwargs: etree.fromstring(RESPONSES[name])


class _Device:
    def __init__(self, failing=()):
        self.facts = {"hostname": "MX1-POP1", "model": "MX204", "version": "21.4R3-S4"}
        self.hostname = "172.20.20.4"
        self.rpc = _Rpc(failing)


def _live(tmp_path, failing=()):
    recording = SessionRecording()
    inventory = load_inventory(INVENTORY_4)
    snapshot = capture_device(
        RecordingDevice(_Device(failing), recording), "172.20.20.4",
        inventory=inventory, collector_names=COLLECTORS, phase="pre",
        now=NOW, finished_at=DONE,
    )
    write_session(
        Session(
            kind="capture", address="172.20.20.4", hostname=recording.hostname,
            facts=recording.facts, calls=recording.calls,
            started_at=snapshot.capture.started_at,
            finished_at=snapshot.capture.finished_at,
        ),
        tmp_path / "raw",
    )
    return snapshot, read_session(tmp_path / "raw"), inventory


def _replay(session, inventory, collectors=COLLECTORS, **kwargs):
    return capture_device(
        ReplayDevice(session), session.address, inventory=inventory,
        collector_names=collectors, phase="pre", now=session.started_at,
        finished_at=session.finished_at, **kwargs,
    )


def test_finished_at_is_kept_separately():
    snapshot = capture_device(_Device(), "172.20.20.4", collector_names=["arp"], now=NOW, finished_at=DONE)
    assert (snapshot.capture.started_at, snapshot.capture.finished_at) == (NOW, DONE)


def test_replay_reproduces_live_snapshot(tmp_path):
    live, session, inventory = _live(tmp_path, failing=("get_bgp_neighbor_information",))

    assert live.capture.collectors["bgp"]["status"] == "error"
    assert live.probes["ping"]
    assert _replay(session, inventory).to_dict() == live.to_dict()


def test_replay_new_collector_is_error_not_silent(tmp_path):
    """Collector, ktery v nahravce neni, je status error 'neni v raw
    zaznamu' - jeho checky pak SKIP, ne tichy PASS."""
    _, session, inventory = _live(tmp_path)

    replayed = _replay(session, inventory, collectors=COLLECTORS + ["bfd"])

    assert replayed.capture.collectors["bfd"]["status"] == "error"
    assert "neni v raw zaznamu" in replayed.capture.collectors["bfd"]["message"]


def test_replay_unrecorded_ping_target_is_skip_not_broken(tmp_path):
    """Jine argumenty pingu nez pri nahravani (tady ping_count) = kazdy cil
    je miss. Vysledek je sent=0 a check SKIP, nikdy BROKEN."""
    _, session, inventory = _live(tmp_path)

    replayed = _replay(session, inventory, ping_count=3)

    assert replayed.probes["ping"]
    assert all(
        probe["sent"] == 0 and probe["error"] == "neni v raw zaznamu"
        for probe in replayed.probes["ping"]
    )
    result = api.evaluate(replayed, now=NOW)
    ping_checks = [
        check for scope in result.scopes for check in scope.checks
        if check.id == "ping_reachability"
    ]
    assert ping_checks
    assert all(check.status is Status.SKIP for check in ping_checks)
```

`RESPONSES` musí pokrýt každé RPC collectorů z `COLLECTORS` na platformě `junos` — `_Rpc` na neznámé jméno vyhodí `AttributeError` a to se nenahraje (PyEZ tohle nikdy nedělá, je to artefakt fake zařízení), takže by živý a přehraný capture měly jiný text chyby. Když round-trip test spadne na rozdílu ve `capture.collectors[...]["message"]`, doplň chybějící RPC do `RESPONSES` jako prázdný element.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/raw/test_capture_round_trip.py -q -p no:warnings`
Expected: FAIL — `TypeError: capture_device() got an unexpected keyword argument 'finished_at'`

- [ ] **Step 3: Change `migration_validator/capture.py`**

- Smaž funkci `_record` celou a `from lxml import etree` (jinde se v souboru nepoužívá).
- V signatuře `capture_device` smaž `record_raw: str | Path | None = None,` a za `now: str | None = None,` přidej `finished_at: str | None = None,`.
- Ve smyčce collectorů smaž `if record_raw is not None: _record(...)`.
- V `CaptureMeta(...)` změň `finished_at=now or _timestamp(),` na `finished_at=finished_at or now or _timestamp(),`.
- Pokud po smazání zůstane nepoužitý import `Path`, smaž ho.

- [ ] **Step 4: Change `migration_validator/api.py` `capture`**

```python
from migration_validator.raw.recorder import RecordingDevice, SessionRecording
```

Signatura: smaž `record_raw: str | None = None,`, na konec přidej `recorder: SessionRecording | None = None,`. Docstring doplň odstavcem:

```python
    `recorder` - kdyz je zadany, vsechna RPC session se nahravaji do nej
    (raw retention, spec 2026-09-23); zapis na disk dela volajici.
```

Tělo:

```python
    options = options or ConnectionOptions(host=host)
    with connect(options) as device:
        if recorder is not None:
            device = RecordingDevice(device, recorder)
        return capture_device(
            device,
            address=host,
            inventory=inventory,
            collector_names=collectors,
            phase=phase,
            ping_count=ping_count,
            baselines=baselines,
            service_types=service_types,
            on_progress=on_progress,
            profile_name=profile_name,
        )
```

- [ ] **Step 5: Remove `record_raw` from `orchestrate.py` and `cli.py`**

- `runs/orchestrate.py`: v `capture_into_run` smaž parametr `record_raw: str | None = None,` a argument `record_raw=record_raw,` ve volání `api.capture`.
- `cli.py`: smaž řádek `capture.add_argument("--record-raw")` a oba argumenty `record_raw=args.record_raw,` (`_cmd_capture` mimo run i `_capture_into_run`).

- [ ] **Step 6: Remove the `--record-raw` tests from `tests/test_capture.py`**

- Import `from migration_validator.capture import _record, capture_device` → `from migration_validator.capture import capture_device`.
- Smaž: `test_record_raw_writes_every_rpc_of_multi_rpc_collector`, třídy `_TwoCallCollector`, `_TwoCallRpc`, `_TwoCallDevice`, `test_record_uses_record_calls_not_rpc_calls`, `_BoomRecordCallsCollector`, `_BoomDevice`, `test_record_does_not_raise_when_record_calls_fails`, `test_record_raw_continues_after_one_collector_boom`, `test_record_raw_writes_xml`. `test_collector_subset_can_be_selected`, `test_snapshot_round_trips_to_disk` a `test_unknown_collector_name_is_rejected` zůstávají.
- Smaž importy, které tím zůstaly nepoužité (typicky `Collector`).

Úplnost nahrávky multi-RPC collectorů teď drží `RecordingDevice` (nahraje, co collector opravdu zavolá) a `test_replay_reproduces_live_snapshot` (interfaces extensive+terse, evpn_mac bridge+evpn na MX).

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest -q -p no:warnings`
Expected: PASS. Zkontroluj `grep -rn "record_raw\|--record-raw" migration_validator tests` → žádný výskyt.

- [ ] **Step 8: Run the mutant**

V `ReplayDevice._ReplayRpc.__getattr__` vrať při miss místo `raise NotRecorded(...)` prázdný element `etree.Element("empty")` → `test_replay_new_collector_is_error_not_silent` musí FAIL. Vrať.

- [ ] **Step 9: Commit**

```bash
git add migration_validator/capture.py migration_validator/api.py migration_validator/runs/orchestrate.py migration_validator/cli.py tests/test_capture.py tests/raw/test_capture_round_trip.py
git commit -m "feat(capture): recorder seam in api.capture, drop --record-raw

--record-raw called every RPC twice and never kept the config; the
recorder sees exactly the calls a collector makes.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `capture_into_run` — nahrání obou sessions, životní cyklus raw, zámek runu

**Files:**
- Modify: `migration_validator/runs/store.py`
- Modify: `migration_validator/runs/orchestrate.py` (`_parse_services`, `capture_into_run`)
- Test: `tests/runs/test_store.py`, `tests/runs/test_orchestrate.py`

**Interfaces:**
- Consumes: `SessionRecording`, `RecordingDevice`, `Session`, `write_session`, `remove_session`, `has_session`, `tool_info`, `read_session` (Task 2), `api.capture(recorder=)` (Task 6), `RecordedCall` (Task 1).
- Produces:
  - `raw_name(file_name: str) -> str` v `runs/store.py` (`"snapshot_post_X.json"` → `"post_X"`, `"inventory_Y.yml"` → `"inventory_Y"`).
  - `RunStore.raw_dir(file_name: str) -> Path` (`<run>/raw/<raw_name>`).
  - `RunStore.lock()` — context manager, exkluzivní `fcntl.flock` na `<run>/.lock`.
  - `RAW_WRITE_WARNING` v `orchestrate.py`: `"raw zaznam {name} se nepodarilo ulozit - po upgradu nastroje nepujde pregenerovat ({error})"`.
  - Bundle capture: `session.params = {"phase", "port", "collectors", "ping_count", "service_types", "profile_name"}`, `session.inventory = {"file": <jméno souboru>, "raw": bool}`, `session.baselines = [<jména snapshotů>]`, `session.started_at == CaptureRecord.taken`.

- [ ] **Step 1: Write the failing store tests**

Na konec `tests/runs/test_store.py`:

```python
import threading
import time

from migration_validator.runs.store import RunStore, raw_name


def test_raw_name_and_dir(tmp_path):
    store = RunStore(tmp_path, "mig01")
    assert raw_name("snapshot_post_PTX1_et_0_0_8.json") == "post_PTX1_et_0_0_8"
    assert raw_name("inventory_PTX1_all.yml") == "inventory_PTX1_all"
    assert store.raw_dir("snapshot_pre_MX1_all.json") == tmp_path / "mig01" / "raw" / "pre_MX1_all"


def test_lock_excludes_second_holder_in_same_process(tmp_path):
    """GUI ma capture i upgrade v jednom procesu - zamek se musi vylucovat
    i mezi vlakny. Zabiji mutanta: fcntl.lockf misto fcntl.flock (POSIX
    record lock se uvnitr procesu nevylucuje)."""
    store = RunStore(tmp_path, "mig01")
    order = []

    def other():
        with store.lock():
            order.append("other")

    with store.lock():
        thread = threading.Thread(target=other)
        thread.start()
        time.sleep(0.2)
        order.append("main")
    thread.join(5)

    assert order == ["main", "other"]
```

(Importy přesuň nahoru k ostatním, pokud tam už nejsou.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/runs/test_store.py -q -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'raw_name'`

- [ ] **Step 3: Implement in `migration_validator/runs/store.py`**

```python
import fcntl
from contextlib import contextmanager
from typing import Iterator

RAW_DIR = "raw"
LOCK_FILE = ".lock"


def raw_name(file_name: str) -> str:
    """Jmeno raw adresare k souboru runu (spec 2026-09-23):
    snapshot_<X>.json -> <X>, inventory_<Y>.yml -> inventory_<Y>."""
    return Path(file_name).stem.removeprefix("snapshot_")
```

Do třídy `RunStore`:

```python
    def raw_dir(self, file_name: str) -> Path:
        """Raw adresar snapshotu nebo inventory (stejny kmen jako soubor)."""
        return self.dir / RAW_DIR / raw_name(file_name)

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Exkluzivni zamek runu - fcntl.flock na runs/<run>/.lock.

        flock, ne lockf: GUI bezi capture i upgrade v jednom procesu a jen
        flock na samostatnych open() se vylucuje i uvnitr procesu. Pri padu
        procesu se uvolni sam. Drzi se jen kolem zapisu na disk, nikdy kolem
        NETCONF session.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / LOCK_FILE, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
```

Run: `.venv/bin/pytest tests/runs/test_store.py -q -p no:warnings` → PASS. Mutant: `fcntl.flock` → `fcntl.lockf` v obou voláních → `test_lock_excludes_second_holder_in_same_process` musí FAIL. Vrať.

- [ ] **Step 4: Write the failing orchestrate tests**

Na konec `tests/runs/test_orchestrate.py`:

```python
import threading
import time
from contextlib import contextmanager

from lxml import etree

from migration_validator import api as mig_api
from migration_validator.raw.bundle import has_session, read_session
from migration_validator.raw.calls import RecordedCall
from migration_validator.runs.manifest import CaptureRecord

INVENTORY = "tests/fixtures/172.20.20.4.yml"


def _fake_capture_recording(monkeypatch, rpc="get_arp_table_information", during=None):
    """api.capture, ktery naplni recorder jako zivy capture. `during` se
    zavola uprostred capture (simulace souběžného zapisu do runu)."""

    def fake(host, **kwargs):
        recorder = kwargs.get("recorder")
        if recorder is not None:
            recorder.facts = {"hostname": "R1", "model": "MX204", "version": "21.4R3"}
            recorder.hostname = host
            recorder.calls.append(
                RecordedCall(seq=1, rpc=rpc, kwargs={}, reply_xml=b"<x/>")
            )
        if during is not None:
            during()
        return _snapshot(host=host, phase=kwargs.get("phase"))

    monkeypatch.setattr("migration_validator.runs.orchestrate.api.capture", fake)


def _capture(store, phase="pre", host="172.20.20.4", port=None, **kwargs):
    kwargs.setdefault("inventory", INVENTORY)
    return capture_into_run(
        store, host=host, phase=phase, port=port,
        options=ConnectionOptions(host=host), profile=default_profile(),
        overwrite=True, **kwargs,
    )


def test_capture_writes_raw_bundle_next_to_snapshot(tmp_path, monkeypatch):
    _fake_capture_recording(monkeypatch)
    store = RunStore(root=tmp_path, name="mig01")

    outcome = _capture(store)

    record = load_manifest(store.manifest_path).captures[0]
    raw_dir = store.raw_dir(outcome.snapshot_path.name)
    session = read_session(raw_dir)
    assert session.kind == "capture"
    assert session.started_at == record.taken
    assert session.params == {
        "phase": "pre", "port": None, "collectors": None, "ping_count": 5,
        "service_types": None, "profile_name": None,
    }
    assert session.inventory == {"file": "172.20.20.4.yml", "raw": False}
    assert (raw_dir / "inventory" / "inventory.yml").is_file()
    assert [call.rpc for call in session.calls] == ["get_arp_table_information"]


def test_second_capture_replaces_raw(tmp_path, monkeypatch):
    store = RunStore(root=tmp_path, name="mig01")
    _fake_capture_recording(monkeypatch, rpc="get_arp_table_information")
    _capture(store)
    _fake_capture_recording(monkeypatch, rpc="get_nd_information")
    outcome = _capture(store)

    session = read_session(store.raw_dir(outcome.snapshot_path.name))
    assert [call.rpc for call in session.calls] == ["get_nd_information"]


def test_raw_write_failure_is_warning_and_leaves_no_stale_raw(tmp_path, monkeypatch):
    """Zabiji mutanta: capture_into_run nesmaze stary raw pred save_snapshot
    (po selhanem zapisu by vedle noveho snimku zustal raw predchoziho)."""
    store = RunStore(root=tmp_path, name="mig01")
    _fake_capture_recording(monkeypatch)
    first = _capture(store)
    raw_dir = store.raw_dir(first.snapshot_path.name)
    assert has_session(raw_dir)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("migration_validator.runs.orchestrate.write_session", boom)
    outcome = _capture(store)

    assert any("nepujde pregenerovat" in warning for warning in outcome.warnings)
    assert not raw_dir.exists()
    assert outcome.snapshot_path.exists()
    assert len(load_manifest(store.manifest_path).captures) == 1


def test_post_records_baselines_used(tmp_path, monkeypatch):
    _fake_capture_recording(monkeypatch)
    mig_api.create_run(
        "mig01", kind="migration",
        devices=[
            {"node": "MX1", "host": "10.0.0.1", "platform": "junos", "role": "old"},
            {"node": "PTX1", "host": "10.0.0.2", "platform": "junos-evo", "role": "new"},
        ],
        mappings=[("ge-0/0/1", "et-0/0/1")], run_root=tmp_path,
    )
    store = RunStore(root=tmp_path, name="mig01")
    pre = _capture(store, host="10.0.0.1", port="ge-0/0/1")

    post = _capture(store, phase="post", host="10.0.0.2", port="et-0/0/1")

    session = read_session(store.raw_dir(post.snapshot_path.name))
    assert session.baselines == [pre.snapshot_path.name]


CONFIG = """
<rpc-reply><data><configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/0</name>
      <unit>
        <name>100</name>
        <description>INTERNET-CPE100-NNI</description>
        <family><inet><address><name>152.11.100.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
</configuration></data></rpc-reply>
"""


class _ConfigRpc:
    def get_config(self, filter_xml, options):
        return etree.fromstring(CONFIG)


class _ConfigDevice:
    facts = {"hostname": "R1", "model": "MX204", "version": "21.4R3"}
    hostname = "172.20.20.4"
    rpc = _ConfigRpc()


def _fake_connect(monkeypatch):
    @contextmanager
    def fake(options):
        yield _ConfigDevice()

    monkeypatch.setattr("migration_validator.runs.orchestrate.connect", fake)


def test_parse_services_pins_inventory_raw_into_capture(tmp_path, monkeypatch):
    _fake_connect(monkeypatch)
    _fake_capture_recording(monkeypatch)
    store = RunStore(root=tmp_path, name="mig01")

    outcome = _capture(store, port="ge-0/0/0", inventory=None, parse_services=True)

    inventory_name = store.inventory_path("172.20.20.4", "ge-0/0/0").name
    inventory_session = read_session(store.raw_dir(inventory_name))
    assert inventory_session.kind == "inventory"
    assert inventory_session.port_filter == "ge-0/0/0"
    assert inventory_session.calls[0].rpc == "get_config"
    capture_session = read_session(store.raw_dir(outcome.snapshot_path.name))
    assert capture_session.inventory == {"file": inventory_name, "raw": True}
    assert read_session(store.raw_dir(outcome.snapshot_path.name) / "inventory").port_filter == "ge-0/0/0"
    assert not list(store.dir.glob(".tmp-*"))


def test_inventory_raw_failure_leaves_no_stale_raw(tmp_path, monkeypatch):
    _fake_connect(monkeypatch)
    _fake_capture_recording(monkeypatch)
    store = RunStore(root=tmp_path, name="mig01")
    _capture(store, port="ge-0/0/0", inventory=None, parse_services=True)
    inventory_raw = store.raw_dir(store.inventory_path("172.20.20.4", "ge-0/0/0").name)
    assert has_session(inventory_raw)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("migration_validator.runs.orchestrate.write_session", boom)
    outcome = _capture(store, port="ge-0/0/0", inventory=None, parse_services=True)

    assert not inventory_raw.exists()
    assert any("nepujde pregenerovat" in warning for warning in outcome.warnings)


def test_capture_write_waits_for_run_lock(tmp_path, monkeypatch):
    """Zapis capture (snapshot + raw + run.yml) jde pod zamkem runu - upgrade
    ho pri vymene nesmi prerusit ani prepsat."""
    _fake_capture_recording(monkeypatch)
    store = RunStore(root=tmp_path, name="mig01")
    snapshot_path = store.snapshot_path("pre", "172.20.20.4", None)

    with store.lock():
        thread = threading.Thread(target=lambda: _capture(store))
        thread.start()
        time.sleep(0.3)
        assert not snapshot_path.exists()
    thread.join(5)

    assert snapshot_path.exists()


def test_manifest_is_reloaded_under_lock(tmp_path, monkeypatch):
    """Capture trva minuty; druhe zarizeni runu mezitim zapise svuj zaznam.
    Zabiji mutanta: capture_into_run uklada manifest nacteny na zacatku."""
    store = RunStore(root=tmp_path, name="mig01")

    def other_capture():
        manifest = store.load()
        manifest.record_capture(CaptureRecord(
            phase="pre", device="OTHER", port=None,
            snapshot="snapshot_pre_OTHER_all.json", taken=NOW,
        ))
        store.save(manifest)

    _fake_capture_recording(monkeypatch, during=other_capture)
    _capture(store)

    devices = {record.device for record in load_manifest(store.manifest_path).captures}
    assert devices == {"OTHER", "172.20.20.4"}
```

- [ ] **Step 5: Run to verify they fail**

Run: `.venv/bin/pytest tests/runs/test_orchestrate.py -q -p no:warnings`
Expected: FAIL — žádný raw adresář (`RawFormatError`), manifest bez `OTHER` atd.

- [ ] **Step 6: Implement in `migration_validator/runs/orchestrate.py`**

Importy navíc:

```python
import os
from datetime import datetime, timezone

from migration_validator.raw.bundle import (
    Session,
    has_session,
    remove_session,
    tool_info,
    write_session,
)
from migration_validator.raw.recorder import RecordingDevice, SessionRecording
```

Konstanty a helpery (pod `_PHASE_TO_ROLE`):

```python
RAW_WRITE_WARNING = (
    "raw zaznam {name} se nepodarilo ulozit - po upgradu nastroje nepujde "
    "pregenerovat ({error})"
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_raw(session: Session, target: Path, warnings: list[str], **copy) -> None:
    """Zapis raw je best effort: selhani = varovani, capture plati dal
    (spec 2026-09-23). write_session maze stary zaznam jako prvni, takze po
    selhani vedle noveho souboru nikdy nezustane raw predchoziho capture."""
    try:
        write_session(session, target, **copy)
    except OSError as error:
        warnings.append(RAW_WRITE_WARNING.format(name=target.name, error=error))
```

`_parse_services` nahraď (nový první parametr `store`):

```python
def _parse_services(
    store: RunStore,
    options: ConnectionOptions,
    port: str | None,
    inventory_path: Path,
    warnings: list[str],
) -> None:
    """Vyrobi inventory pro parse_services v samostatnem kratkem spojeni.

    Existujici soubor se pregeneruje - konfigurace noveho boxu se meni
    kazdou vlnou a flag je explicitni umysl (revize faze 4, viz spec
    2026-08-17). Session se nahrava (raw retention, spec 2026-09-23);
    inventory vznika v docasnem souboru a na misto jde az pod zamkem runu
    spolu s raw - zamek nikdy nedrzi NETCONF session.
    """
    previous = _inventory_interfaces(inventory_path)
    scratch = inventory_path.with_name(f".tmp-{inventory_path.name}")
    recording = SessionRecording()
    started_at = _timestamp()
    try:
        with connect(options) as device:
            recorded = RecordingDevice(device, recording)
            platform = detect_platform(recorded)
            generate_inventory(recorded, platform, scratch, port)
        session = Session(
            kind="inventory",
            address=options.host,
            hostname=recording.hostname,
            facts=recording.facts,
            calls=list(recording.calls),
            started_at=started_at,
            port_filter=port,
            tool=tool_info(),
        )
        raw_dir = store.raw_dir(inventory_path.name)
        with store.lock():
            # Stary raw pryc driv, nez se nahradi YAML - jinak by vedle nove
            # inventory zustal raw predchozi.
            remove_session(raw_dir)
            os.replace(scratch, inventory_path)
            _write_raw(session, raw_dir, warnings)
    finally:
        scratch.unlink(missing_ok=True)

    current = _inventory_interfaces(inventory_path) or set()
    if previous is None:
        warnings.append(f"inventory vyrobena: {inventory_path} ({len(current)} sluzeb)")
    else:
        added = len(current - previous)
        removed = len(previous - current)
        warnings.append(
            f"inventory pregenerovana: {inventory_path} "
            f"({len(current)} sluzeb, +{added} nove, -{removed} odebrane)"
        )
```

V `capture_into_run`:

1. Volání `_parse_services(options, port, inventory_path, warnings)` → `_parse_services(store, options, port, inventory_path, warnings)`.
2. Za `baselines: list[Snapshot] = []` přidej `baseline_names: list[str] = []` a ve smyčce `for record in records:` přidej `baseline_names.append(record.snapshot)` vedle `baselines.append(...)`.
3. Od `collectors = collectors if ...` do konce funkce nahraď:

```python
    collectors = collectors if collectors is not None else profile.collectors
    ping_count = ping_count if ping_count is not None else (profile.ping_count or PING_COUNT_DEFAULT)
    service_types = service_types if service_types is not None else profile.service_types

    recording = SessionRecording()
    snapshot = api.capture(
        host,
        inventory=str(inventory_path),
        options=options,
        collectors=collectors,
        phase=phase,
        ping_count=ping_count,
        baselines=baselines or None,
        service_types=service_types,
        on_progress=on_progress,
        profile_name=profile.name or None,
        recorder=recording,
    )

    snapshot_path = store.snapshot_path(phase, node, port)
    raw_dir = store.raw_dir(snapshot_path.name)
    inventory_raw = None
    if inventory_path.resolve().parent == store.dir.resolve():
        candidate = store.raw_dir(inventory_path.name)
        if has_session(candidate):
            inventory_raw = candidate
    session = Session(
        kind="capture",
        address=host,
        hostname=recording.hostname,
        facts=recording.facts,
        calls=list(recording.calls),
        started_at=snapshot.capture.started_at,
        finished_at=snapshot.capture.finished_at,
        params={
            "phase": phase,
            "port": port,
            "collectors": collectors,
            "ping_count": ping_count,
            "service_types": service_types,
            "profile_name": profile.name or None,
        },
        inventory={"file": inventory_path.name, "raw": inventory_raw is not None},
        baselines=baseline_names,
        tool=tool_info(),
    )

    with store.lock():
        # Manifest znovu pod zamkem: capture trva minuty a do runu mezitim
        # mohl zapsat capture druheho zarizeni.
        manifest = store.load()
        if node not in manifest.devices:
            manifest.devices[node] = RunDevice(
                host=host,
                platform=snapshot.device.platform,
                role=_PHASE_TO_ROLE[phase],
            )
        remove_session(raw_dir)
        save_snapshot(snapshot, snapshot_path)
        _write_raw(
            session,
            raw_dir,
            warnings,
            inventory_raw=inventory_raw,
            inventory_yaml=None if inventory_raw is not None else inventory_path,
        )
        manifest.record_capture(
            CaptureRecord(
                phase=phase,
                device=node,
                port=port,
                snapshot=store.snapshot_name(phase, node, port),
                taken=snapshot.capture.started_at,
            )
        )
        store.save(manifest)

    failed = snapshot.capture.failed_collectors()

    return CaptureOutcome(
        snapshot_path=snapshot_path,
        failed_collectors=failed,
        warnings=warnings,
    )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest -q -p no:warnings`
Expected: PASS (celá sada — `tests/test_cli.py` capture testy monkeypatchují `api.capture` a nově zapíšou i raw)

- [ ] **Step 8: Run the mutants**

1. V `capture_into_run` smaž `remove_session(raw_dir)` → `test_raw_write_failure_is_warning_and_leaves_no_stale_raw` musí FAIL. Vrať.
2. Pod zámkem nahraď `manifest = store.load()` použitím manifestu z začátku funkce → `test_manifest_is_reloaded_under_lock` musí FAIL. Vrať.

- [ ] **Step 9: Commit**

```bash
git add migration_validator/runs/store.py migration_validator/runs/orchestrate.py tests/runs/test_store.py tests/runs/test_orchestrate.py
git commit -m "feat(runs): keep raw bundles for every capture, write under a run lock

Both sessions are recorded. The old raw goes before the new file lands,
and run.yml is reloaded under the lock so a concurrent capture survives.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `raw/upgrade.py` — přegenerování, report, staging, záloha

**Files:**
- Create: `migration_validator/raw/upgrade.py`
- Create: `tests/raw_run.py` (helper; importuje se `from raw_run import ...` — `tests/` je na `sys.path`, ověřeno)
- Test: `tests/raw/test_upgrade.py`

**Interfaces:**
- Consumes: vše z Tasků 1-7; `capture_device(finished_at=)`, `generate_inventory`, `detect_platform`, `load_inventory`, `load_snapshot`, `save_snapshot`, `all_collectors`, `RunStore.raw_dir`.
- Produces (v `migration_validator/raw/upgrade.py`):
  - konstanty `UNCHANGED = "unchanged"`, `CHANGED = "changed"`, `NOT_REGENERABLE = "not_regenerable"`, `NO_RAW = "bez raw zaznamu (zachyceno pred zavedenim)"`, `RAW_MISMATCH = "raw nepatri k tomuto snimku"`, `INVENTORY_NO_RAW = "inventory bez raw zaznamu a nejde nacist"`, `STAGING_DIR = ".upgrade-staging"`, `BACKUP_DIR = "backup"`.
  - `@dataclass UpgradeItem(kind, file, phase=None, device=None, port=None, result=UNCHANGED, reason=None, notes=[])` s `refuse(reason)`.
  - `@dataclass UpgradeReport(run, dry_run, items=[], backup=None, error=None)` s `exit_code` (0/1/2) a `to_dict()` (`{"run", "dry_run", "backup", "items": [...], "error"}`).
  - `upgrade_run(store: RunStore, *, dry_run: bool = False, now: datetime | None = None) -> UpgradeReport`.
  - `regenerate_inventory(session: Session, target: Path) -> None`.
  - `render_report(report: UpgradeReport) -> list[str]`.
- `tests/raw_run.py` produkuje: `PRE_AT`, `POST_AT`, `COLLECTORS`, `FakeDevice`, `record_inventory(store, node, port=None) -> Path`, `record_capture(store, *, phase, node, started_at, port=None, baselines=(), collectors=COLLECTORS, role=None) -> str`, `build_run(root, name="mig01", *, group=None) -> RunStore`, `arp_with_note(monkeypatch)`.

- [ ] **Step 1: Write the helper `tests/raw_run.py`**

```python
"""Testovaci run se skutecnymi raw bundly pro testy upgradu (spec 2026-09-23).

Jde skutecnou cestou recorder -> bundle: capture_device nad RecordingDevice,
write_session, save_snapshot, zaznam v run.yml. Importuje se jako
`from raw_run import ...` (tests/ je na sys.path).
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from migration_validator.capture import capture_device
from migration_validator.models.inventory import load_inventory
from migration_validator.models.snapshot import load_snapshot, save_snapshot
from migration_validator.raw.bundle import Session, tool_info, write_session
from migration_validator.raw.recorder import RecordingDevice, SessionRecording
from migration_validator.runs.manifest import CaptureRecord, RunDevice
from migration_validator.runs.services import generate_inventory
from migration_validator.runs.store import RunStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INVENTORY_4 = FIXTURES / "172.20.20.4.yml"
PRE_AT = "2026-09-23T08:00:00Z"
POST_AT = "2026-09-23T09:00:00Z"
COLLECTORS = ["interfaces", "arp", "bgp", "evpn_vpws", "evpn_instance", "evpn_mac"]

INTERFACES_XML = """
<interface-information>
  <physical-interface>
    <name>ge-0/0/2</name>
    <admin-status>up</admin-status>
    <oper-status>up</oper-status>
    <logical-interface>
      <name>ge-0/0/2.113</name>
      <oper-status>up</oper-status>
    </logical-interface>
  </physical-interface>
</interface-information>
"""

ARP_XML = """
<arp-table-information>
  <arp-table-entry>
    <ip-address>198.11.13.2</ip-address>
    <mac-address>00:11:22:33:44:55</mac-address>
    <interface-name>ge-0/0/2.113</interface-name>
  </arp-table-entry>
</arp-table-information>
"""

PING_XML = """
<ping-results>
  <probe-results-summary>
    <probes-sent>5</probes-sent>
    <responses-received>5</responses-received>
    <packet-loss>0</packet-loss>
    <rtt-average>1240</rtt-average>
  </probe-results-summary>
</ping-results>
"""

CONFIG_XML = """
<rpc-reply><data><configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/0</name>
      <unit>
        <name>100</name>
        <description>INTERNET-CPE100-NNI</description>
        <family><inet><address><name>152.11.100.1/30</name></address></inet></family>
      </unit>
    </interface>
  </interfaces>
  <firewall><family><inet><filter><name>F</name></filter></inet></family></firewall>
</configuration></data></rpc-reply>
"""

RESPONSES = {
    "get_interface_information": INTERFACES_XML,
    "get_arp_table_information": ARP_XML,
    "get_bgp_neighbor_information": "<bgp-information/>",
    "get_evpn_vpws_information": "<evpn-vpws-information/>",
    "get_evpn_instance_information": "<evpn-instance-information/>",
    "get_bridge_mac_table": "<l2ald-rtb-macdb/>",
    "get_evpn_mac_table": "<l2ald-rtb-macdb/>",
    "ping": PING_XML,
    "get_config": CONFIG_XML,
}


class FakeRpc:
    def __getattr__(self, name):
        if name.startswith("_") or name not in RESPONSES:
            raise AttributeError(name)
        return lambda **kwargs: etree.fromstring(RESPONSES[name])


class FakeDevice:
    def __init__(self):
        self.facts = {"hostname": "MX1-POP1", "model": "MX204", "version": "21.4R3-S4"}
        self.hostname = "172.20.20.4"
        self.rpc = FakeRpc()


def record_inventory(store: RunStore, node: str, port: str | None = None) -> Path:
    recording = SessionRecording()
    path = store.inventory_path(node, port)
    generate_inventory(RecordingDevice(FakeDevice(), recording), "junos", path, port)
    write_session(
        Session(
            kind="inventory", address="172.20.20.4", hostname=recording.hostname,
            facts=recording.facts, calls=list(recording.calls), started_at=PRE_AT,
            port_filter=port, tool=tool_info(),
        ),
        store.raw_dir(path.name),
    )
    return path


def record_capture(
    store: RunStore, *, phase: str, node: str, started_at: str,
    port: str | None = None, baselines=(), collectors=COLLECTORS, role: str | None = None,
) -> str:
    recording = SessionRecording()
    snapshot = capture_device(
        RecordingDevice(FakeDevice(), recording), "172.20.20.4",
        inventory=load_inventory(INVENTORY_4), collector_names=list(collectors),
        phase=phase, now=started_at,
        baselines=[load_snapshot(store.dir / name) for name in baselines] or None,
    )
    name = store.snapshot_name(phase, node, port)
    save_snapshot(snapshot, store.dir / name)
    write_session(
        Session(
            kind="capture", address="172.20.20.4", hostname=recording.hostname,
            facts=recording.facts, calls=list(recording.calls),
            started_at=snapshot.capture.started_at,
            finished_at=snapshot.capture.finished_at,
            params={
                "phase": phase, "port": port, "collectors": list(collectors),
                "ping_count": 5, "service_types": None, "profile_name": None,
            },
            inventory={"file": INVENTORY_4.name, "raw": False},
            baselines=list(baselines), tool=tool_info(),
        ),
        store.raw_dir(name),
        inventory_yaml=INVENTORY_4,
    )
    manifest = store.load()
    manifest.devices.setdefault(node, RunDevice(
        host=f"10.0.0.{len(manifest.devices) + 1}", platform="junos",
        role=role or ("new" if phase == "post" else "old"),
    ))
    manifest.record_capture(CaptureRecord(
        phase=phase, device=node, port=port, snapshot=name,
        taken=snapshot.capture.started_at,
    ))
    store.save(manifest)
    return name


def build_run(root, name: str = "mig01", *, group: str | None = None) -> RunStore:
    """Run: pre MX1 (celobox), post PTX1 s baseline pre, inventory MX1 s raw."""
    store = RunStore(Path(root), name)
    pre = record_capture(store, phase="pre", node="MX1", started_at=PRE_AT)
    record_capture(store, phase="post", node="PTX1", started_at=POST_AT, baselines=[pre])
    record_inventory(store, "MX1")
    if group is not None:
        manifest = store.load()
        manifest.group = group
        store.save(manifest)
    return store


def arp_with_note(monkeypatch) -> None:
    """Simulace opravy parsovani: ArpCollector pridava pole -> fakta se
    zmeni, snapshot pregenerovany novou verzi se lisi."""
    from migration_validator.collectors.arp import ArpCollector

    original = ArpCollector.parse

    def parse(self, xml, platform):
        return [dict(entry, note="v2") for entry in original(self, xml, platform)]

    monkeypatch.setattr(ArpCollector, "parse", parse)
```

- [ ] **Step 2: Write the failing tests `tests/raw/test_upgrade.py`**

```python
"""mig-validate upgrade - pregenerovani runu z raw zaznamu (spec 2026-09-23, sekce 3)."""

import json
from datetime import datetime, timezone

from raw_run import PRE_AT, arp_with_note, build_run

from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot, save_snapshot
from migration_validator.runs.manifest import CaptureRecord
from migration_validator.raw.upgrade import (
    CHANGED,
    NO_RAW,
    NOT_REGENERABLE,
    RAW_MISMATCH,
    UNCHANGED,
    UpgradeItem,
    UpgradeReport,
    render_report,
    upgrade_run,
)

NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)


def _results(report):
    return {item.file: (item.result, item.reason) for item in report.items}


def _bytes(store):
    return {
        path.name: path.read_bytes()
        for path in sorted(store.dir.iterdir())
        if path.is_file() and path.name != ".lock"
    }


def test_same_version_upgrade_is_unchanged(tmp_path):
    store = build_run(tmp_path)

    report = upgrade_run(store, dry_run=True)

    assert _results(report) == {
        "inventory_MX1_all.yml": (UNCHANGED, None),
        "snapshot_pre_MX1_all.json": (UNCHANGED, None),
        "snapshot_post_PTX1_all.json": (UNCHANGED, None),
    }
    assert report.exit_code == 0
    assert report.error is None


def test_capture_without_raw_is_reported_and_untouched(tmp_path):
    store = build_run(tmp_path)
    old = Snapshot(device=DeviceMeta(address="10.0.0.9"), capture=CaptureMeta(started_at=PRE_AT, phase="pre"))
    save_snapshot(old, store.dir / "snapshot_pre_OLD_all.json")
    manifest = store.load()
    manifest.record_capture(CaptureRecord(
        phase="pre", device="OLD", port=None,
        snapshot="snapshot_pre_OLD_all.json", taken=PRE_AT,
    ))
    store.save(manifest)
    before = (store.dir / "snapshot_pre_OLD_all.json").read_bytes()

    report = upgrade_run(store, now=NOW)

    assert _results(report)["snapshot_pre_OLD_all.json"] == (NOT_REGENERABLE, NO_RAW)
    assert report.exit_code == 1
    assert (store.dir / "snapshot_pre_OLD_all.json").read_bytes() == before


def test_raw_that_does_not_belong_to_snapshot_is_refused(tmp_path):
    store = build_run(tmp_path)
    manifest = store.load()
    manifest.captures[0].taken = "2026-09-23T07:59:59Z"
    store.save(manifest)

    report = upgrade_run(store, dry_run=True)

    assert _results(report)["snapshot_pre_MX1_all.json"] == (NOT_REGENERABLE, RAW_MISMATCH)


def test_changed_parse_regenerates_and_backs_up(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    before = (store.dir / "snapshot_pre_MX1_all.json").read_bytes()
    arp_with_note(monkeypatch)

    report = upgrade_run(store, now=NOW)

    assert _results(report)["snapshot_pre_MX1_all.json"] == (CHANGED, None)
    assert report.backup == "backup/upgrade-20260923T120000Z"
    backup = store.dir / report.backup
    assert (backup / "snapshot_pre_MX1_all.json").read_bytes() == before
    data = json.loads((store.dir / "snapshot_pre_MX1_all.json").read_text())
    assert all(entry["note"] == "v2" for entry in data["facts"]["arp"])
    assert not (store.dir / ".upgrade-staging").exists()


def test_post_uses_regenerated_pre_as_baseline(tmp_path, monkeypatch):
    """Zabiji mutanta: post dostane baseline nactenou z disku (stara verze)
    misto pregenerovane pre ze stejneho behu."""
    store = build_run(tmp_path)
    arp_with_note(monkeypatch)
    seen = {}

    from migration_validator.raw import upgrade as upgrade_module

    real = upgrade_module.capture_device

    def spy(*args, **kwargs):
        if kwargs.get("phase") == "post":
            seen["baselines"] = kwargs.get("baselines")
        return real(*args, **kwargs)

    monkeypatch.setattr(upgrade_module, "capture_device", spy)
    upgrade_run(store, dry_run=True)

    assert seen["baselines"]
    assert all(entry.get("note") == "v2" for entry in seen["baselines"][0].facts["arp"])


def test_missing_baseline_is_noted_and_post_still_regenerates(tmp_path):
    store = build_run(tmp_path)
    (store.dir / "snapshot_pre_MX1_all.json").write_text("{")
    import shutil

    shutil.rmtree(store.raw_dir("snapshot_pre_MX1_all.json"))

    report = upgrade_run(store, dry_run=True)

    post = next(item for item in report.items if item.file == "snapshot_post_PTX1_all.json")
    assert post.result != NOT_REGENERABLE
    assert any("nejde pregenerovat ani nacist" in note for note in post.notes)


def test_unknown_recorded_collector_is_dropped_with_note(tmp_path):
    store = build_run(tmp_path)
    raw = store.raw_dir("snapshot_pre_MX1_all.json") / "session.json"
    data = json.loads(raw.read_text())
    data["params"]["collectors"].append("gone_collector")
    raw.write_text(json.dumps(data))

    report = upgrade_run(store, dry_run=True)

    pre = next(item for item in report.items if item.file == "snapshot_pre_MX1_all.json")
    assert pre.result == UNCHANGED
    assert pre.notes == ["collector gone_collector v teto verzi neexistuje"]


def test_inventory_without_raw_is_listed(tmp_path):
    store = build_run(tmp_path)
    (store.dir / "inventory_OLD_all.yml").write_text("schema_version: 9\n")

    report = upgrade_run(store, dry_run=True)

    assert _results(report)["inventory_OLD_all.yml"] == (NOT_REGENERABLE, NO_RAW)


def test_config_without_needed_hierarchy_is_not_regenerable(tmp_path):
    store = build_run(tmp_path)
    raw = store.raw_dir("inventory_MX1_all.yml") / "session.json"
    data = json.loads(raw.read_text())
    data["calls"][0]["filter"].remove("protocols")
    raw.write_text(json.dumps(data))

    report = upgrade_run(store, dry_run=True)

    assert _results(report)["inventory_MX1_all.yml"] == (
        NOT_REGENERABLE, "konfigurace nema hierarchii protocols",
    )


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    before = _bytes(store)
    arp_with_note(monkeypatch)

    report = upgrade_run(store, dry_run=True, now=NOW)

    assert CHANGED in {item.result for item in report.items}
    assert report.backup is None
    assert _bytes(store) == before
    assert not (store.dir / "backup").exists()
    assert not (store.dir / ".upgrade-staging").exists()


def test_crash_during_replay_leaves_run_untouched(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    before = _bytes(store)
    arp_with_note(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("bug v nastroji")

    monkeypatch.setattr("migration_validator.raw.upgrade.capture_device", boom)
    report = upgrade_run(store, now=NOW)

    assert report.error == "replay selhal - RuntimeError: bug v nastroji"
    assert report.exit_code == 2
    assert _bytes(store) == before
    assert not (store.dir / "backup").exists()
    assert not (store.dir / ".upgrade-staging").exists()


def test_render_report_lines():
    report = UpgradeReport(run="mig01", dry_run=True, items=[
        UpgradeItem(kind="capture", file="snapshot_post_PTX1_all.json", phase="post",
                    device="PTX1", port=None, result=CHANGED),
        UpgradeItem(kind="capture", file="snapshot_pre_MX1_ge_0_0_2.json", phase="pre",
                    device="MX1", port="ge-0/0/2", result=NOT_REGENERABLE, reason=NO_RAW,
                    notes=["collector x v teto verzi neexistuje"]),
        UpgradeItem(kind="inventory", file="inventory_MX1_all.yml"),
    ])

    lines = render_report(report)

    assert lines[0] == "run mig01 (dry-run)"
    assert "post     PTX1 all" in lines[1]
    assert lines[1].endswith("pregenerovano - zmeneno")
    assert lines[2].endswith("nelze - bez raw zaznamu (zachyceno pred zavedenim)")
    assert lines[3] == "    ! collector x v teto verzi neexistuje"
    assert "inventory inventory_MX1_all.yml" in lines[4]
    assert lines[4].endswith("pregenerovano - beze zmeny")


def test_report_to_dict_shape():
    report = UpgradeReport(run="mig01", dry_run=False, backup="backup/x", items=[
        UpgradeItem(kind="inventory", file="inventory_MX1_all.yml"),
    ])
    assert report.to_dict() == {
        "run": "mig01", "dry_run": False, "backup": "backup/x", "error": None,
        "items": [{
            "kind": "inventory", "file": "inventory_MX1_all.yml", "phase": None,
            "device": None, "port": None, "result": "unchanged", "reason": None,
            "notes": [],
        }],
    }
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/pytest tests/raw/test_upgrade.py -q -p no:warnings`
Expected: FAIL — `ModuleNotFoundError: No module named 'migration_validator.raw.upgrade'`

- [ ] **Step 4: Write `migration_validator/raw/upgrade.py`**

```python
"""mig-validate upgrade - pregeneruje inventory a snimky runu z raw zaznamu
aktualni verzi nastroje (spec 2026-09-23, sekce 3).

Poradi: inventory soubory, pak pre/rollback capture, pak post (baseline =
pregenerovane pre podle session.json, ne dnesni mapping). Vse jde do
.upgrade-staging/; pad replaye (chyba nastroje) nic nezmeni. Capture, ktere
pregenerovat nejde, zustavaji beze zmeny a upgrade ostatnich nezastavi.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

import migration_validator.collectors.all  # noqa: F401  (registrace)
from migration_validator.capture import capture_device
from migration_validator.collectors.registry import all_collectors
from migration_validator.connection.junos import detect_platform
from migration_validator.models.inventory import Inventory, load_inventory
from migration_validator.models.snapshot import (
    Snapshot,
    SnapshotVersionError,
    load_snapshot,
    save_snapshot,
)
from migration_validator.probes.ping import DEFAULT_COUNT
from migration_validator.raw.bundle import (
    INVENTORY_DIR,
    INVENTORY_YAML,
    RawFormatError,
    Session,
    has_session,
    read_session,
)
from migration_validator.raw.calls import NotRecorded
from migration_validator.raw.replay import ReplayDevice
from migration_validator.runs.manifest import CaptureRecord, RunManifest
from migration_validator.runs.services import generate_inventory
from migration_validator.runs.store import RunStore

STAGING_DIR = ".upgrade-staging"
BACKUP_DIR = "backup"

UNCHANGED = "unchanged"
CHANGED = "changed"
NOT_REGENERABLE = "not_regenerable"

NO_RAW = "bez raw zaznamu (zachyceno pred zavedenim)"
RAW_MISMATCH = "raw nepatri k tomuto snimku"
INVENTORY_NO_RAW = "inventory bez raw zaznamu a nejde nacist"

_RESULT_TEXT = {
    UNCHANGED: "pregenerovano - beze zmeny",
    CHANGED: "pregenerovano - zmeneno",
    NOT_REGENERABLE: "nelze",
}


@dataclass
class UpgradeItem:
    kind: str  # capture | inventory
    file: str
    phase: str | None = None
    device: str | None = None
    port: str | None = None
    result: str = UNCHANGED
    reason: str | None = None
    notes: list[str] = field(default_factory=list)

    def refuse(self, reason: str) -> None:
        self.result = NOT_REGENERABLE
        self.reason = reason


@dataclass
class UpgradeReport:
    run: str
    dry_run: bool
    items: list[UpgradeItem] = field(default_factory=list)
    backup: str | None = None
    error: str | None = None

    @property
    def exit_code(self) -> int:
        """0 vse pregenerovano, 1 nektere capture nejdou, 2 upgrade runu spadl."""
        if self.error is not None:
            return 2
        if any(item.result == NOT_REGENERABLE for item in self.items):
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "dry_run": self.dry_run,
            "backup": self.backup,
            "items": [asdict(item) for item in self.items],
            "error": self.error,
        }


class _Unreadable(Exception):
    """Inventory bez raw, kterou aktualni verze nenacte."""


def upgrade_run(
    store: RunStore,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> UpgradeReport:
    report = UpgradeReport(run=store.name, dry_run=dry_run)
    manifest = store.load()
    staging = store.dir / STAGING_DIR
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        try:
            staged = _regenerate(store, manifest, staging, report)
        except Exception as error:  # noqa: BLE001 - chyba nastroje, run zustava beze zmeny
            report.error = f"replay selhal - {type(error).__name__}: {error}"
            return report
        if dry_run or not staged:
            return report
        report.backup = _swap(store, staging, staged, now)
        return report
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def regenerate_inventory(session: Session, target: Path) -> None:
    """Inventory ze session --parse-services aktualnim parserem, se stejnym
    port filtrem jako puvodne."""
    device = ReplayDevice(session)
    generate_inventory(device, detect_platform(device), target, session.port_filter)


def _regenerate(
    store: RunStore, manifest: RunManifest, staging: Path, report: UpgradeReport
) -> list[str]:
    """Pregeneruje vse do stagingu. Vraci jmena souboru, ktere se zmenily."""
    staged: list[str] = []

    for path in sorted(store.dir.glob("inventory_*.yml")):
        item = UpgradeItem(kind="inventory", file=path.name)
        report.items.append(item)
        raw_dir = store.raw_dir(path.name)
        if not has_session(raw_dir):
            item.refuse(NO_RAW)
            continue
        target = staging / path.name
        try:
            regenerate_inventory(read_session(raw_dir), target)
        except (NotRecorded, RawFormatError) as error:
            item.refuse(str(error))
            continue
        item.result = _compare(path, target, yaml.safe_load)
        if item.result == CHANGED:
            staged.append(path.name)

    regenerated: dict[str, Snapshot] = {}
    ordered = [r for r in manifest.captures if r.phase != "post"] + [
        r for r in manifest.captures if r.phase == "post"
    ]
    for record in ordered:
        item = UpgradeItem(
            kind="capture", file=record.snapshot, phase=record.phase,
            device=record.device, port=record.port,
        )
        report.items.append(item)
        snapshot = _replay_capture(store, record, staging, regenerated, item)
        if snapshot is None:
            continue
        regenerated[record.snapshot] = snapshot
        target = staging / record.snapshot
        save_snapshot(snapshot, target)
        item.result = _compare(store.dir / record.snapshot, target, json.loads)
        if item.result == CHANGED:
            staged.append(record.snapshot)
    return staged


def _replay_capture(
    store: RunStore,
    record: CaptureRecord,
    staging: Path,
    regenerated: dict[str, Snapshot],
    item: UpgradeItem,
) -> Snapshot | None:
    raw_dir = store.raw_dir(record.snapshot)
    if not has_session(raw_dir):
        item.refuse(NO_RAW)
        return None
    try:
        session = read_session(raw_dir)
    except RawFormatError as error:
        item.refuse(str(error))
        return None
    if session.started_at != record.taken:
        item.refuse(RAW_MISMATCH)
        return None
    scratch = staging / ".inventory" / f"{Path(record.snapshot).stem}.yml"
    try:
        inventory = _capture_inventory(raw_dir, scratch)
    except (NotRecorded, RawFormatError) as error:
        item.refuse(str(error))
        return None
    except _Unreadable:
        item.refuse(INVENTORY_NO_RAW)
        return None
    params = session.params or {}
    return capture_device(
        ReplayDevice(session),
        session.address or record.device,
        inventory=inventory,
        collector_names=_known_collectors(params.get("collectors"), item),
        phase=params.get("phase", record.phase),
        ping_count=params.get("ping_count", DEFAULT_COUNT),
        now=session.started_at,
        finished_at=session.finished_at,
        baselines=_baselines(store, session.baselines or [], regenerated, item) or None,
        service_types=params.get("service_types"),
        profile_name=params.get("profile_name"),
    )


def _capture_inventory(raw_dir: Path, scratch: Path) -> Inventory | None:
    """Inventory, se kterou capture bezel: z kopie raw inventory, jinak
    z kopie YAML (jen kdyz ji aktualni verze nacte)."""
    inventory_dir = raw_dir / INVENTORY_DIR
    if has_session(inventory_dir):
        regenerate_inventory(read_session(inventory_dir), scratch)
        return load_inventory(scratch)
    copy = inventory_dir / INVENTORY_YAML
    if copy.is_file():
        try:
            return load_inventory(copy)
        except ValueError as error:
            raise _Unreadable(str(error)) from error
    return None


def _known_collectors(names: list[str] | None, item: UpgradeItem) -> list[str] | None:
    """None = vsechny collectory aktualni verze. Jmeno, ktere tahle verze
    nezna (prejmenovany/odstraneny collector), se vypusti s poznamkou -
    _select_collectors by na nem jinak spadl."""
    if names is None:
        return None
    known = {collector.name for collector in all_collectors()}
    for name in names:
        if name not in known:
            item.notes.append(f"collector {name} v teto verzi neexistuje")
    return [name for name in names if name in known]


def _baselines(
    store: RunStore,
    names: list[str],
    regenerated: dict[str, Snapshot],
    item: UpgradeItem,
) -> list[Snapshot]:
    result: list[Snapshot] = []
    for name in names:
        if name in regenerated:
            result.append(regenerated[name])
            continue
        try:
            result.append(load_snapshot(store.dir / name))
        except (OSError, ValueError, KeyError, TypeError, SnapshotVersionError):
            item.notes.append(
                f"baseline {name} nejde pregenerovat ani nacist - ping cile bez ni"
            )
    return result


def _compare(old: Path, new: Path, load: Callable[[str], Any]) -> str:
    try:
        before = load(old.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return CHANGED
    return UNCHANGED if before == load(new.read_text(encoding="utf-8")) else CHANGED


def _swap(store: RunStore, staging: Path, staged: list[str], now: datetime | None) -> str:
    """Nahrazovane soubory do backup/upgrade-<cas>/, nove na jejich misto.
    Rada prejmenovani, ne atomicka operace - pri padu uprostred je vse
    puvodni v zaloze."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup = store.dir / BACKUP_DIR / f"upgrade-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for name in staged:
        current = store.dir / name
        if current.exists():
            os.replace(current, backup / name)
        os.replace(staging / name, current)
    return backup.relative_to(store.dir).as_posix()


def render_report(report: UpgradeReport) -> list[str]:
    lines = [f"run {report.run}" + (" (dry-run)" if report.dry_run else "")]
    for item in report.items:
        if item.kind == "capture":
            label = f"{item.phase or '?':<8} {item.device} {item.port or 'all'}"
        else:
            label = f"inventory {item.file}"
        text = _RESULT_TEXT[item.result]
        if item.reason:
            text = f"{text} - {item.reason}"
        lines.append(f"  {label:<44} {text}")
        lines.extend(f"    ! {note}" for note in item.notes)
    if report.error is not None:
        lines.append(f"  CHYBA: {report.error} - run zustal beze zmeny")
    elif report.backup is not None:
        lines.append(f"  zaloha: {report.backup}")
    return lines
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/raw -q -p no:warnings`
Expected: PASS. Pokud `test_same_version_upgrade_is_unchanged` hlásí `changed`, je to skutečná odchylka replaye od živého capture — **STOP**, použij superpowers:systematic-debugging (porovnej `json.loads` obou snapshotů klíč po klíči) a nahlas ji; test neupravuj.

- [ ] **Step 6: Run the mutants**

1. V `_regenerate` nahraď výpočet `ordered` za `ordered = list(reversed(manifest.captures))` (post se přehraje před pre, baseline přijde ze staré verze na disku) → `test_post_uses_regenerated_pre_as_baseline` musí FAIL. Vrať.
2. V `_baselines` vynech větev `if name in regenerated` → `test_post_uses_regenerated_pre_as_baseline` musí FAIL. Vrať.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/raw/upgrade.py tests/raw_run.py tests/raw/test_upgrade.py
git commit -m "feat(raw): regenerate a run's inventories and snapshots from raw bundles

Staging, backup of replaced files, per-capture report; captures without
raw are listed and left alone.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Upgrade — kontrola změny runu, zámek při výměně, posun mtime `run.yml`

**Files:**
- Modify: `migration_validator/raw/upgrade.py` (`upgrade_run`, nový `_fingerprint`, konstanta `RUN_CHANGED`)
- Test: `tests/raw/test_upgrade.py`

**Interfaces:**
- Consumes: `RunStore.lock()` (Task 7), `upgrade_run` (Task 8).
- Produces: `upgrade_run(store, *, dry_run=False, now=None, before_swap: Callable[[], None] | None = None)`; `RUN_CHANGED = "run se behem upgradu zmenil - spust upgrade znovu"`. `before_swap` je testovací šev: volá se po replayi, těsně před zámkem.

- [ ] **Step 1: Write the failing tests**

Na konec `tests/raw/test_upgrade.py`:

```python
import os
import threading
import time

from migration_validator.raw.upgrade import RUN_CHANGED


def test_run_changed_during_upgrade_aborts(tmp_path, monkeypatch):
    """Capture, ktery do runu zapise mezi replayem a vymenou, musi prezit -
    v zaloze by nebyl. Zabiji mutanta: vymena bez kontroly fingerprintu."""
    store = build_run(tmp_path)
    arp_with_note(monkeypatch)
    post = store.dir / "snapshot_post_PTX1_all.json"

    def fresh_capture():
        post.write_text('{"fresh": true}\n')

    report = upgrade_run(store, now=NOW, before_swap=fresh_capture)

    assert report.error == RUN_CHANGED
    assert report.exit_code == 2
    assert post.read_text() == '{"fresh": true}\n'
    assert not (store.dir / "backup").exists()


def test_swap_waits_for_capture_lock(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    arp_with_note(monkeypatch)
    result = {}

    with store.lock():
        thread = threading.Thread(target=lambda: result.update(report=upgrade_run(store, now=NOW)))
        thread.start()
        time.sleep(0.5)
        assert not (store.dir / "backup").exists()
    thread.join(10)

    assert result["report"].backup == "backup/upgrade-20260923T120000Z"


def test_apply_bumps_run_yml_mtime_only_when_something_changed(tmp_path, monkeypatch):
    """SummaryCache GUI je klicovana mtime run.yml - bez posunu by souhrn
    skupiny ukazoval stary verdikt."""
    store = build_run(tmp_path)
    old = 1_000_000_000
    os.utime(store.manifest_path, (old, old))

    upgrade_run(store, now=NOW)
    assert store.manifest_path.stat().st_mtime == old

    arp_with_note(monkeypatch)
    upgrade_run(store, now=NOW)
    assert store.manifest_path.stat().st_mtime > old


def test_stale_staging_is_removed(tmp_path):
    store = build_run(tmp_path)
    (store.dir / ".upgrade-staging").mkdir()
    (store.dir / ".upgrade-staging" / "junk").write_text("x")

    upgrade_run(store, dry_run=True)

    assert not (store.dir / ".upgrade-staging").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/raw/test_upgrade.py -q -p no:warnings`
Expected: FAIL — `TypeError: upgrade_run() got an unexpected keyword argument 'before_swap'`, `ImportError: RUN_CHANGED`.

- [ ] **Step 3: Implement**

Konstanta pod `INVENTORY_NO_RAW`:

```python
RUN_CHANGED = "run se behem upgradu zmenil - spust upgrade znovu"
```

Nová funkce:

```python
def _fingerprint(store: RunStore) -> dict[str, tuple[int, int]]:
    """mtime_ns + velikost vseho, co upgrade cte nebo nahrazuje. Zmena mezi
    ctenim a vymenou = do runu mezitim zapsal capture."""
    entries: dict[str, tuple[int, int]] = {}
    for pattern in ("run.yml", "snapshot_*.json", "inventory_*.yml", "raw/*/session.json"):
        for path in store.dir.glob(pattern):
            stat = path.stat()
            entries[path.relative_to(store.dir).as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return entries
```

`upgrade_run` nahraď:

```python
def upgrade_run(
    store: RunStore,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    before_swap: Callable[[], None] | None = None,
) -> UpgradeReport:
    """Replay (kroky 1-4) bezi bez zamku - trva sekundy, capture minuty a
    nema cekat. Vymena bezi pod RunStore.lock() a jen kdyz se run od
    zacatku nezmenil (souběh s capture zachyti fingerprint)."""
    report = UpgradeReport(run=store.name, dry_run=dry_run)
    manifest = store.load()
    fingerprint = _fingerprint(store)
    staging = store.dir / STAGING_DIR
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        try:
            staged = _regenerate(store, manifest, staging, report)
        except Exception as error:  # noqa: BLE001 - chyba nastroje, run zustava beze zmeny
            report.error = f"replay selhal - {type(error).__name__}: {error}"
            return report
        if dry_run or not staged:
            return report
        if before_swap is not None:
            before_swap()
        with store.lock():
            if _fingerprint(store) != fingerprint:
                report.error = RUN_CHANGED
                return report
            report.backup = _swap(store, staging, staged, now)
            # SummaryCache GUI je klicovana mtime run.yml - bez posunu by
            # souhrn skupiny ukazoval stary verdikt az do restartu GUI.
            os.utime(store.manifest_path)
        return report
    finally:
        shutil.rmtree(staging, ignore_errors=True)
```

Poznámka: `staging` samotný pod `store.dir` do fingerprintu nepatří (vzory ho nechytí).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q -p no:warnings`
Expected: PASS

- [ ] **Step 5: Run the mutants**

1. Smaž `if _fingerprint(store) != fingerprint: ...` → `test_run_changed_during_upgrade_aborts` musí FAIL. Vrať.
2. Smaž `with store.lock():` (tělo odsaď ven) → `test_swap_waits_for_capture_lock` musí FAIL. Vrať.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/raw/upgrade.py tests/raw/test_upgrade.py
git commit -m "fix(raw): swap under the run lock only if the run did not change

A capture landing between replay and swap now aborts the upgrade instead
of being overwritten; run.yml mtime moves so the GUI summary refreshes.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `api.upgrade_run` + CLI `mig-validate upgrade`

**Files:**
- Modify: `migration_validator/api.py` (nová `upgrade_run`)
- Modify: `migration_validator/cli.py` (`_cmd_upgrade`, parser `upgrade`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `raw.upgrade.upgrade_run`, `render_report`, `UpgradeReport` (Tasky 8-9), `api.group_runs`.
- Produces:
  - `api.upgrade_run(name: str, *, run_root: str | Path = Path("runs"), dry_run: bool = False) -> UpgradeReport` (`FileNotFoundError`, když run neexistuje).
  - `mig-validate upgrade [RUN ...] [--group NAME] [--dry-run] [--run-root DIR]`; návratový kód = nejhorší `exit_code` přes runy.

- [ ] **Step 1: Write the failing tests**

Na konec `tests/test_cli.py`:

```python
def test_upgrade_dry_run_prints_report(tmp_path, capsys):
    from raw_run import build_run

    build_run(tmp_path)

    code = main(["upgrade", "mig01", "--run-root", str(tmp_path), "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0] == "run mig01 (dry-run)"
    assert "pregenerovano - beze zmeny" in out


def test_upgrade_not_regenerable_exits_1(tmp_path, capsys):
    from raw_run import build_run

    store = build_run(tmp_path)
    import shutil

    shutil.rmtree(store.raw_dir("snapshot_pre_MX1_all.json"))

    code = main(["upgrade", "mig01", "--run-root", str(tmp_path), "--dry-run"])

    assert code == 1
    assert "nelze - bez raw zaznamu" in capsys.readouterr().out


def test_upgrade_group_expands_members(tmp_path, capsys):
    from raw_run import build_run

    build_run(tmp_path, "pop1-mx1", group="pop1")
    build_run(tmp_path, "pop1-mx2", group="pop1")

    code = main(["upgrade", "--group", "pop1", "--run-root", str(tmp_path), "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    assert "run pop1-mx1 (dry-run)" in out
    assert "run pop1-mx2 (dry-run)" in out


def test_upgrade_without_runs_is_tool_error(tmp_path, capsys):
    assert main(["upgrade", "--run-root", str(tmp_path)]) == 2
    assert "zadej aspon jeden run" in capsys.readouterr().err


def test_upgrade_unknown_run_is_tool_error(tmp_path, capsys):
    assert main(["upgrade", "nope", "--run-root", str(tmp_path)]) == 2
    assert "neexistuje" in capsys.readouterr().err


def test_capture_has_no_record_raw_flag():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["capture", "--device", "x", "--record-raw", "d"])
```

(Pokud `main` nebo `build_parser` v souboru ještě nejsou importované, přidej `from migration_validator.cli import build_parser, main`.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -q -p no:warnings -k "upgrade or record_raw_flag"`
Expected: FAIL — `argparse` nezná `upgrade` (SystemExit 2 místo očekávaného výstupu).

- [ ] **Step 3: Implement `api.upgrade_run`**

V `migration_validator/api.py` import:

```python
from migration_validator.raw import upgrade as raw_upgrade
```

a funkce (za `archive_run`):

```python
def upgrade_run(
    name: str,
    *,
    run_root: str | Path = Path("runs"),
    dry_run: bool = False,
) -> "raw_upgrade.UpgradeReport":
    """Pregeneruje inventory a snimky runu z raw zaznamu aktualni verzi
    nastroje (spec 2026-09-23). FileNotFoundError, kdyz run neexistuje."""
    store = RunStore(Path(run_root), name)
    if not store.manifest_path.is_file():
        raise FileNotFoundError(f"run '{name}' neexistuje")
    return raw_upgrade.upgrade_run(store, dry_run=dry_run)
```

- [ ] **Step 4: Implement the CLI**

V `migration_validator/cli.py`:

```python
from migration_validator.raw.upgrade import render_report
```

```python
def _cmd_upgrade(args: argparse.Namespace) -> int:
    names = list(args.runs)
    if args.group:
        members = api.group_runs(args.run_root).get(args.group)
        if not members:
            raise ToolError(f"skupina '{args.group}' neexistuje")
        names += [member for member in members if member not in names]
    if not names:
        raise ToolError("zadej aspon jeden run nebo --group")

    worst = EXIT_OK
    for name in names:
        try:
            report = api.upgrade_run(name, run_root=args.run_root, dry_run=args.dry_run)
        except FileNotFoundError as error:
            raise ToolError(str(error)) from error
        for line in render_report(report):
            print(line)
        worst = max(worst, report.exit_code)
    return worst
```

V `build_parser` (za `record`):

```python
    upgrade = sub.add_parser(
        "upgrade",
        help="pregeneruje inventory a snimky runu z raw zaznamu aktualni verzi nastroje",
    )
    upgrade.add_argument("runs", nargs="*", metavar="RUN", help="nazvy runu")
    upgrade.add_argument("--group", help="vsechny runy skupiny")
    upgrade.add_argument(
        "--dry-run", action="store_true", help="jen report, v runu nic nemen"
    )
    upgrade.add_argument(
        "--run-root", type=Path, default=Path("runs"), help="koren run adresaru"
    )
    upgrade.set_defaults(func=_cmd_upgrade)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest -q -p no:warnings`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add migration_validator/api.py migration_validator/cli.py tests/test_cli.py
git commit -m "feat(cli): mig-validate upgrade [RUN...] [--group] [--dry-run]

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Chyby verze schématu — rada, 422 místo 500, štítky v detailu runu

**Files:**
- Modify: `migration_validator/models/snapshot.py` (`SnapshotVersionError` text, `peek_schema_version`)
- Modify: `migration_validator/models/inventory.py` (`InventoryVersionError`)
- Modify: `migration_validator/gui/app.py` (exception handler, `_detail`)
- Modify: `migration_validator/gui/serializers.py` (`snapshot_list`)
- Modify: `migration_validator/gui/groups.py` (`_member_row`)
- Test: `tests/models/test_snapshot.py`, `tests/models/test_inventory.py`, `tests/gui/test_serializers.py`, `tests/gui/test_evaluation_routes.py`, `tests/gui/test_groups_module.py`

**Interfaces:**
- Consumes: `has_session` (Task 2), `RunStore.raw_dir` (Task 7), `raw_run.build_run` (Task 8).
- Produces:
  - `SnapshotVersionError` text: `"snapshot ma schema_version {v}, nastroj umi 13 - pregeneruj run: mig-validate upgrade <run>"`.
  - `peek_schema_version(path: str | Path) -> int | None`.
  - `class InventoryVersionError(ValueError)`; text končí `"- vygeneruj ji znovu parserem (capture --parse-services) nebo pregeneruj run: mig-validate upgrade <run>"`.
  - GUI: `SnapshotVersionError` kdekoliv v routě → HTTP 422 `{"detail": <str>, "code": "schema_outdated"}`.
  - `snapshot_list(manifest, store: RunStore | None = None)` — se `store` přidá `schema_version`, `outdated` (`schema_version != SCHEMA_VERSION`), `has_raw`.
  - `OUTDATED_ROW_ERROR = "zastarale snimky - spust Upgrade group"` v `gui/groups.py`.

- [ ] **Step 1: Write the failing tests**

`tests/models/test_snapshot.py` (na konec):

```python
def test_version_error_names_the_fix(tmp_path):
    import json

    import pytest

    from migration_validator.models.snapshot import (
        CaptureMeta, DeviceMeta, Snapshot, SnapshotVersionError, load_snapshot, save_snapshot,
    )

    path = tmp_path / "s.json"
    save_snapshot(Snapshot(device=DeviceMeta(address="x"), capture=CaptureMeta(started_at="t")), path)
    data = json.loads(path.read_text())
    data["schema_version"] = 12
    path.write_text(json.dumps(data))

    with pytest.raises(SnapshotVersionError, match="mig-validate upgrade"):
        load_snapshot(path)


def test_peek_schema_version(tmp_path):
    from migration_validator.models.snapshot import (
        SCHEMA_VERSION, CaptureMeta, DeviceMeta, Snapshot, peek_schema_version, save_snapshot,
    )

    path = tmp_path / "s.json"
    save_snapshot(Snapshot(device=DeviceMeta(address="x"), capture=CaptureMeta(started_at="t")), path)
    assert peek_schema_version(path) == SCHEMA_VERSION
    (tmp_path / "bad.json").write_text("{")
    assert peek_schema_version(tmp_path / "bad.json") is None
    assert peek_schema_version(tmp_path / "missing.json") is None
```

`tests/models/test_inventory.py` (na konec):

```python
def test_inventory_version_error_is_value_error_with_fix(tmp_path):
    import pytest

    from migration_validator.models.inventory import InventoryVersionError, load_inventory

    path = tmp_path / "inv.yml"
    path.write_text("schema_version: 9\ndevice: r1\ninterfaces: []\n", encoding="utf-8")

    with pytest.raises(InventoryVersionError, match="mig-validate upgrade") as info:
        load_inventory(path)
    assert isinstance(info.value, ValueError)
```

`tests/gui/test_serializers.py` (na konec):

```python
def test_snapshot_list_with_store_adds_schema_and_raw(tmp_path):
    from raw_run import build_run

    from migration_validator.gui.serializers import snapshot_list
    from migration_validator.models.snapshot import SCHEMA_VERSION

    store = build_run(tmp_path)
    rows = snapshot_list(store.load(), store)

    assert {row["file"]: (row["schema_version"], row["outdated"], row["has_raw"]) for row in rows} == {
        "snapshot_pre_MX1_all.json": (SCHEMA_VERSION, False, True),
        "snapshot_post_PTX1_all.json": (SCHEMA_VERSION, False, True),
    }


def test_snapshot_list_marks_corrupt_snapshot_outdated(tmp_path):
    import shutil

    from raw_run import build_run

    from migration_validator.gui.serializers import snapshot_list

    store = build_run(tmp_path)
    (store.dir / "snapshot_pre_MX1_all.json").write_text("{")
    shutil.rmtree(store.raw_dir("snapshot_pre_MX1_all.json"))

    row = next(r for r in snapshot_list(store.load(), store) if r["file"] == "snapshot_pre_MX1_all.json")

    assert (row["schema_version"], row["outdated"], row["has_raw"]) == (None, True, False)
```

`tests/gui/test_evaluation_routes.py` (na konec):

```python
def _outdated_run(tmp_path):
    import json

    from fastapi.testclient import TestClient
    from raw_run import build_run

    from migration_validator.gui.app import create_app

    store = build_run(tmp_path)
    path = store.dir / "snapshot_post_PTX1_all.json"
    data = json.loads(path.read_text())
    data["schema_version"] = 12
    path.write_text(json.dumps(data))
    return TestClient(create_app(run_root=tmp_path))


def test_run_evaluation_outdated_snapshot_is_422_schema_outdated(tmp_path):
    client = _outdated_run(tmp_path)

    res = client.get("/api/runs/mig01/evaluation")

    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "schema_outdated"
    assert isinstance(body["detail"], str)
    assert "mig-validate upgrade" in body["detail"]


def test_snapshot_evaluation_outdated_snapshot_is_422(tmp_path):
    client = _outdated_run(tmp_path)

    res = client.get("/api/runs/mig01/snapshots/snapshot_post_PTX1_all.json/evaluation")

    assert res.status_code == 422
    assert res.json()["code"] == "schema_outdated"


def test_run_detail_lists_schema_and_raw(tmp_path):
    client = _outdated_run(tmp_path)

    snapshots = client.get("/api/runs/mig01").json()["snapshots"]

    post = next(s for s in snapshots if s["file"] == "snapshot_post_PTX1_all.json")
    assert (post["schema_version"], post["outdated"], post["has_raw"]) == (12, True, True)
```

`tests/gui/test_groups_module.py` (na konec):

```python
def test_group_summary_row_reports_outdated_run(tmp_path):
    import json

    from raw_run import build_run

    from migration_validator.gui.captures import CaptureManager
    from migration_validator.gui.groups import OUTDATED_ROW_ERROR, SummaryCache, build_group_summary
    from migration_validator.profiles.store import ProfileStore

    bad = build_run(tmp_path, "pop1-mx1", group="pop1")
    build_run(tmp_path, "pop1-mx2", group="pop1")
    path = bad.dir / "snapshot_post_PTX1_all.json"
    data = json.loads(path.read_text())
    data["schema_version"] = 12
    path.write_text(json.dumps(data))

    summary = build_group_summary(
        "pop1", run_root=tmp_path, profiles=ProfileStore(tmp_path / "profiles"),
        default_path=None, manager=CaptureManager(), cache=SummaryCache(),
    )

    rows = {row["run"]: row for row in summary["runs"]}
    assert rows["pop1-mx1"]["error"] == OUTDATED_ROW_ERROR
    assert rows["pop1-mx2"]["error"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/models tests/gui -q -p no:warnings`
Expected: FAIL — chybí `peek_schema_version`, `InventoryVersionError`, 500 místo 422, `KeyError: schema_version`, `SnapshotVersionError` propadne ze souhrnu skupiny.

- [ ] **Step 3: Implement the models**

`models/snapshot.py`:

```python
import re
```

V `Snapshot.from_dict` text chyby:

```python
            raise SnapshotVersionError(
                f"snapshot ma schema_version {version}, nastroj umi {SCHEMA_VERSION}"
                " - pregeneruj run: mig-validate upgrade <run>"
            )
```

Na konec souboru:

```python
_SCHEMA_RE = re.compile(r'"schema_version"\s*:\s*(\d+)')


def peek_schema_version(path: str | Path) -> int | None:
    """schema_version bez plneho nacteni - save_snapshot ji zapisuje jako
    prvni klic. None, kdyz soubor chybi nebo ji na zacatku nenese
    (poskozeny soubor); GUI z toho udela stitek 'outdated'."""
    try:
        with open(path, encoding="utf-8") as handle:
            head = handle.read(512)
    except OSError:
        return None
    match = _SCHEMA_RE.search(head)
    return int(match.group(1)) if match else None
```

`models/inventory.py` — nad `load_inventory`:

```python
class InventoryVersionError(ValueError):
    """Inventory ma jinou schema_version, nez nastroj umi."""
```

a v `load_inventory`:

```python
        raise InventoryVersionError(
            f"{path}: inventory ma schema_version {version}, nastroj umi "
            f"{INVENTORY_SCHEMA_VERSION} - vygeneruj ji znovu parserem "
            "(capture --parse-services) nebo pregeneruj run: mig-validate upgrade <run>"
        )
```

- [ ] **Step 4: Implement the GUI side**

`gui/serializers.py`:

```python
from migration_validator.models.snapshot import SCHEMA_VERSION, peek_schema_version
from migration_validator.raw.bundle import has_session
from migration_validator.runs.store import RunStore


def snapshot_list(manifest: RunManifest, store: RunStore | None = None) -> list[dict]:
    rows = []
    for record in manifest.captures:
        row = {
            "file": record.snapshot,
            "phase": record.phase,
            "device": record.device,
            "port": record.port,
            "taken": record.taken,
        }
        if store is not None:
            version = peek_schema_version(store.dir / record.snapshot)
            row["schema_version"] = version
            row["outdated"] = version != SCHEMA_VERSION
            row["has_raw"] = has_session(store.raw_dir(record.snapshot))
        rows.append(row)
    return rows
```

`gui/app.py`:

```python
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from migration_validator.models.snapshot import SnapshotVersionError, load_snapshot
```

V `create_app` hned za `app = FastAPI(...)`:

```python
    @app.exception_handler(SnapshotVersionError)
    async def schema_outdated(request: Request, error: SnapshotVersionError) -> JSONResponse:
        # detail zustava retezec - app.js vsude cte body.detail jako text;
        # jen notice s tlacitkem Upgrade run se ridi podle code.
        return JSONResponse(
            status_code=422,
            content={"detail": str(error), "code": "schema_outdated"},
        )
```

V `_detail`: `"snapshots": snapshot_list(manifest),` → `"snapshots": snapshot_list(manifest, store),`.

`gui/groups.py`:

```python
from migration_validator.models.snapshot import SnapshotVersionError, load_snapshot

OUTDATED_ROW_ERROR = "zastarale snimky - spust Upgrade group"
```

V `_member_row` v `try` okolo `run_verdict` přidej před `except (ValueError, OSError)`:

```python
    except SnapshotVersionError:
        row["error"] = OUTDATED_ROW_ERROR
        return row
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest -q -p no:warnings`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add migration_validator/models/snapshot.py migration_validator/models/inventory.py migration_validator/gui/app.py migration_validator/gui/serializers.py migration_validator/gui/groups.py tests/models tests/gui
git commit -m "fix(gui): outdated snapshots answer 422 schema_outdated, not 500

Run detail lists schema_version/outdated/has_raw per snapshot; the group
summary shows an outdated run as a row error instead of failing.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: GUI endpointy upgradu a zámek runu v `CaptureManager`

**Files:**
- Modify: `migration_validator/gui/captures.py`
- Modify: `migration_validator/gui/app.py` (`POST /api/runs/{run}/upgrade`)
- Modify: `migration_validator/gui/group_routes.py` (`POST /api/groups/{group}/upgrade`)
- Test: `tests/gui/test_captures.py`, `tests/gui/test_upgrade_routes.py` (nový)

**Interfaces:**
- Consumes: `api.upgrade_run` (Task 10), `raw_run.build_run`, `raw_run.arp_with_note` (Task 8).
- Produces:
  - `class RunBusy(DeviceBusy)`; `CaptureManager.maintenance(run: str)` — context manager; `start()` odmítne run v údržbě (`RunBusy`).
  - `POST /api/runs/{run}/upgrade?dry_run=<bool>` → `UpgradeReport.to_dict()`; 404 neznámý run, 409 běžící capture / souběžný upgrade, 403 bez `ADMIN`.
  - `POST /api/groups/{group}/upgrade?dry_run=<bool>` → `{"group": str, "runs": [UpgradeReport.to_dict() | {"run", "dry_run", "backup": None, "items": [], "error": str}]}`; 404, 409 (běžící capture v kterémkoliv runu), 403.

- [ ] **Step 1: Write the failing manager tests**

Na konec `tests/gui/test_captures.py`:

```python
from migration_validator.gui.captures import RunBusy


def _blocking(event):
    def fn(on_progress):
        event.wait(5)
        return object()
    return fn


def test_maintenance_refuses_run_with_active_capture():
    manager = CaptureManager()
    release = threading.Event()
    task = manager.start(_blocking(release), run="mig01", device="MX1", port=None, phase="pre")
    try:
        with pytest.raises(RunBusy, match="bezici capture"):
            with manager.maintenance("mig01"):
                pass
    finally:
        release.set()
        _wait_done(manager, task.id)


def test_start_refuses_run_in_maintenance():
    manager = CaptureManager()
    with manager.maintenance("mig01"):
        with pytest.raises(RunBusy, match="se upgraduje"):
            manager.start(_ok_fn, run="mig01", device="MX1", port=None, phase="pre")
        with pytest.raises(RunBusy, match="uz upgraduje"):
            with manager.maintenance("mig01"):
                pass
    task = manager.start(_ok_fn, run="mig01", device="MX1", port=None, phase="pre")
    assert _wait_done(manager, task.id).state == "done"


def test_run_busy_is_device_busy_for_existing_handlers():
    assert issubclass(RunBusy, DeviceBusy)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/gui/test_captures.py -q -p no:warnings`
Expected: FAIL — `ImportError: cannot import name 'RunBusy'`

- [ ] **Step 3: Implement in `gui/captures.py`**

```python
from contextlib import contextmanager
from typing import Any, Callable, Iterator


class RunBusy(DeviceBusy):
    """Run je v udrzbe (upgrade) nebo na nem ceka/bezi capture. Podtrida
    DeviceBusy, aby existujici handlery (409) fungovaly beze zmeny."""
```

V `CaptureManager.__init__` přidej `self._maintenance: set[str] = set()`.

`active_task` rozděl:

```python
    def active_task(self, run: str) -> CaptureTask | None:
        with self._lock:
            return self._active_unlocked(run)

    def _active_unlocked(self, run: str) -> CaptureTask | None:
        for task in self._tasks.values():
            if task.run == run and task.state in ACTIVE_STATES:
                return task
        return None

    @contextmanager
    def maintenance(self, run: str) -> Iterator[None]:
        """Zamek runu v GUI pro upgrade. Spravnost drzi flock na disku
        (RunStore.lock); tohle jen vrati srozumitelnou 409 driv, nez se
        capture vubec zaradi do fronty. Kontrola i nastaveni jsou pod
        jednim zamkem manageru - zadny zavod."""
        with self._lock:
            if run in self._maintenance:
                raise RunBusy(f"run {run} se uz upgraduje")
            if self._active_unlocked(run) is not None:
                raise RunBusy(f"run {run} ma bezici capture")
            self._maintenance.add(run)
        try:
            yield
        finally:
            with self._lock:
                self._maintenance.discard(run)
```

Ve `start()` hned na začátku bloku `with self._lock:`:

```python
            if run in self._maintenance:
                raise RunBusy(f"run {run} se upgraduje")
```

Run: `.venv/bin/pytest tests/gui/test_captures.py -q -p no:warnings` → PASS.

- [ ] **Step 4: Write the failing route tests `tests/gui/test_upgrade_routes.py`**

```python
"""POST /api/runs/{run}/upgrade a /api/groups/{group}/upgrade (spec 2026-09-23, sekce 4)."""

import threading

from fastapi.testclient import TestClient
from raw_run import arp_with_note, build_run

from migration_validator import api
from migration_validator.gui.app import create_app
from migration_validator.gui.authz import Actor


def _client(tmp_path):
    app = create_app(run_root=tmp_path)
    return app, TestClient(app)


def test_dry_run_returns_report_and_changes_nothing(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    before = (store.dir / "snapshot_pre_MX1_all.json").read_bytes()
    arp_with_note(monkeypatch)
    _, client = _client(tmp_path)

    res = client.post("/api/runs/mig01/upgrade?dry_run=true")

    assert res.status_code == 200
    body = res.json()
    assert body["dry_run"] is True
    assert body["backup"] is None
    assert {item["file"]: item["result"] for item in body["items"]}["snapshot_pre_MX1_all.json"] == "changed"
    assert (store.dir / "snapshot_pre_MX1_all.json").read_bytes() == before


def test_apply_replaces_and_reports_backup(tmp_path, monkeypatch):
    store = build_run(tmp_path)
    arp_with_note(monkeypatch)
    _, client = _client(tmp_path)

    body = client.post("/api/runs/mig01/upgrade").json()

    assert body["dry_run"] is False
    assert body["backup"].startswith("backup/upgrade-")
    assert (store.dir / body["backup"] / "snapshot_pre_MX1_all.json").is_file()


def test_upgrade_refused_while_capture_runs(tmp_path):
    build_run(tmp_path)
    app, client = _client(tmp_path)
    release = threading.Event()
    app.state.captures.start(
        lambda on_progress: release.wait(5), run="mig01", device="MX1", port=None, phase="pre",
    )
    try:
        res = client.post("/api/runs/mig01/upgrade?dry_run=true")
    finally:
        release.set()

    assert res.status_code == 409
    assert "bezici capture" in res.json()["detail"]


def test_upgrade_unknown_run_is_404(tmp_path):
    _, client = _client(tmp_path)
    assert client.post("/api/runs/nope/upgrade").status_code == 404


def test_upgrade_needs_admin(tmp_path):
    build_run(tmp_path)
    app, client = _client(tmp_path)
    app.state.actor_provider = lambda request: Actor(role="operator")

    assert client.post("/api/runs/mig01/upgrade?dry_run=true").status_code == 403
    assert client.post("/api/groups/pop1/upgrade?dry_run=true").status_code == 403


def test_group_upgrade_continues_after_one_run_fails(tmp_path, monkeypatch):
    build_run(tmp_path, "pop1-mx1", group="pop1")
    build_run(tmp_path, "pop1-mx2", group="pop1")
    real = api.upgrade_run

    def flaky(name, **kwargs):
        if name == "pop1-mx1":
            raise OSError("disk")
        return real(name, **kwargs)

    monkeypatch.setattr("migration_validator.gui.group_routes.api.upgrade_run", flaky)
    _, client = _client(tmp_path)

    body = client.post("/api/groups/pop1/upgrade?dry_run=true").json()

    runs = {entry["run"]: entry for entry in body["runs"]}
    assert body["group"] == "pop1"
    assert runs["pop1-mx1"]["error"] == "OSError: disk"
    assert runs["pop1-mx1"]["items"] == []
    assert runs["pop1-mx2"]["error"] is None
    assert runs["pop1-mx2"]["items"]


def test_group_upgrade_refused_while_any_capture_runs(tmp_path):
    build_run(tmp_path, "pop1-mx1", group="pop1")
    build_run(tmp_path, "pop1-mx2", group="pop1")
    app, client = _client(tmp_path)
    release = threading.Event()
    app.state.captures.start(
        lambda on_progress: release.wait(5), run="pop1-mx2", device="MX1", port=None, phase="pre",
    )
    try:
        res = client.post("/api/groups/pop1/upgrade?dry_run=true")
    finally:
        release.set()

    assert res.status_code == 409


def test_group_upgrade_unknown_group_is_404(tmp_path):
    _, client = _client(tmp_path)
    assert client.post("/api/groups/nope/upgrade").status_code == 404
```

- [ ] **Step 5: Run to verify they fail**

Run: `.venv/bin/pytest tests/gui/test_upgrade_routes.py -q -p no:warnings`
Expected: FAIL — 404/405 (routy neexistují).

- [ ] **Step 6: Implement the routes**

`gui/app.py` — import `from migration_validator.gui.captures import CaptureManager, DeviceBusy, RunBusy` a za `archive_run`:

```python
    @app.post("/api/runs/{run}/upgrade")
    def upgrade_run(run: str, dry_run: bool = False, actor: Actor = require(Permission.ADMIN)) -> dict:
        """Pregeneruje run z raw zaznamu (spec 2026-09-23). Synchronni -
        replay je offline a kratky, fronta captures se nepouziva."""
        _require_store(run)
        try:
            with manager.maintenance(run):
                report = api.upgrade_run(run, run_root=run_root, dry_run=dry_run)
        except RunBusy as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return report.to_dict()
```

`gui/group_routes.py` — import `from migration_validator.gui.captures import CaptureManager, DeviceBusy, RunBusy` a za `archive_group`:

```python
    @router.post("/{group}/upgrade")
    def upgrade_group(group: str, dry_run: bool = False, actor: Actor = require(Permission.ADMIN)) -> dict:
        """Upgrade vsech runu skupiny postupne; selhani jednoho runu
        ostatni nezastavi (spec 2026-09-23, sekce 4)."""
        members = api.group_runs(run_root).get(group)
        if not members:
            raise HTTPException(status_code=404, detail=f"skupina '{group}' neexistuje")
        if any(manager.busy_run(name) for name in members):
            raise HTTPException(status_code=409, detail=f"skupina '{group}' ma bezici capture")
        runs: list[dict[str, Any]] = []
        for name in members:
            try:
                with manager.maintenance(name):
                    runs.append(api.upgrade_run(name, run_root=run_root, dry_run=dry_run).to_dict())
            except Exception as error:  # noqa: BLE001 - jeden run nesmi zastavit skupinu
                runs.append({
                    "run": name, "dry_run": dry_run, "backup": None, "items": [],
                    "error": f"{type(error).__name__}: {error}",
                })
        return {"group": group, "runs": runs}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest -q -p no:warnings`
Expected: PASS

- [ ] **Step 8: Run the mutant**

Ve `start()` smaž kontrolu `if run in self._maintenance` → `test_start_refuses_run_in_maintenance` musí FAIL. Vrať.

- [ ] **Step 9: Commit**

```bash
git add migration_validator/gui/captures.py migration_validator/gui/app.py migration_validator/gui/group_routes.py tests/gui/test_captures.py tests/gui/test_upgrade_routes.py
git commit -m "feat(gui): upgrade run and upgrade group endpoints with a run maintenance lock

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Frontend — modaly Upgrade run/group, štítky snímků, notice se zastaralými snímky

**Files:**
- Modify: `migration_validator/gui/static/view.js` (helpery + export)
- Modify: `migration_validator/gui/static/app.js`
- Modify: `migration_validator/gui/static/style.css`
- Test: `tests/js/view.test.js`

**Interfaces:**
- Consumes: odpovědi z Tasku 12 (`UpgradeReport.to_dict()`, `{"group", "runs"}`), `snapshots[*].outdated/has_raw` a 422 `code: "schema_outdated"` z Tasku 11.
- Produces (v `MigView`): `upgradeItemRow(item) -> {where, result, cls, reason, notes}`, `upgradeCounts(report) -> {unchanged, changed, not_regenerable}`, `snapshotBadges(snap) -> [{cls, text}]`, `errorDetail(body, fallback) -> string`.

- [ ] **Step 1: Write the failing JS tests**

Na konec `tests/js/view.test.js`:

```js
test("upgradeItemRow: capture row names phase, device and port; whole box is 'all'", () => {
  const row = MigView.upgradeItemRow({
    kind: "capture", phase: "post", device: "PTX1", port: null,
    result: "changed", reason: null, notes: [],
  });
  assert.strictEqual(row.where, "post PTX1 all");
  assert.strictEqual(row.result, "regenerated – changed");
  assert.strictEqual(row.cls, "info");
});

test("upgradeItemRow: not regenerable carries reason and warn class", () => {
  const row = MigView.upgradeItemRow({
    kind: "capture", phase: "pre", device: "MX1", port: "ge-0/0/2",
    result: "not_regenerable", reason: "bez raw zaznamu (zachyceno pred zavedenim)",
    notes: ["collector x v teto verzi neexistuje"],
  });
  assert.strictEqual(row.where, "pre MX1 ge-0/0/2");
  assert.strictEqual(row.result, "cannot regenerate");
  assert.strictEqual(row.cls, "warn");
  assert.strictEqual(row.reason, "bez raw zaznamu (zachyceno pred zavedenim)");
  assert.deepStrictEqual(row.notes, ["collector x v teto verzi neexistuje"]);
});

test("upgradeItemRow: inventory row names the file, unchanged is pass", () => {
  const row = MigView.upgradeItemRow({ kind: "inventory", file: "inventory_MX1_all.yml", result: "unchanged" });
  assert.strictEqual(row.where, "inventory inventory_MX1_all.yml");
  assert.strictEqual(row.result, "regenerated – unchanged");
  assert.strictEqual(row.cls, "pass");
  assert.deepStrictEqual(row.notes, []);
});

test("upgradeCounts counts results; empty report is zeros", () => {
  const report = { items: [{ result: "changed" }, { result: "changed" }, { result: "not_regenerable" }] };
  assert.deepStrictEqual(MigView.upgradeCounts(report), { unchanged: 0, changed: 2, not_regenerable: 1 });
  assert.deepStrictEqual(MigView.upgradeCounts(null), { unchanged: 0, changed: 0, not_regenerable: 0 });
});

test("snapshotBadges: outdated and no raw are separate; current with raw has none", () => {
  assert.deepStrictEqual(MigView.snapshotBadges({ outdated: true, has_raw: false }), [
    { cls: "warn", text: "outdated" },
    { cls: "neutral", text: "no raw" },
  ]);
  assert.deepStrictEqual(MigView.snapshotBadges({ outdated: false, has_raw: true }), []);
  assert.deepStrictEqual(MigView.snapshotBadges({}), []);
});

test("errorDetail: string detail, object detail.message, fallback", () => {
  assert.strictEqual(MigView.errorDetail({ detail: "run ma bezici capture" }, "x"), "run ma bezici capture");
  assert.strictEqual(MigView.errorDetail({ detail: { message: "skupina chybi" } }, "x"), "skupina chybi");
  assert.strictEqual(MigView.errorDetail({}, "upgrade selhal (500)"), "upgrade selhal (500)");
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `node --test tests/js/*.test.js`
Expected: FAIL — `MigView.upgradeItemRow is not a function`.

- [ ] **Step 3: Implement the helpers in `view.js`**

Nad `const MigView = {`:

```js
/* Upgrade run/group report (spec 2026-09-23). Reason a poznamky prichazi
   z backendu jako ASCII text, popisky vysledku jsou anglicky. */
const UPGRADE_RESULT_LABEL = {
  unchanged: "regenerated – unchanged",
  changed: "regenerated – changed",
  not_regenerable: "cannot regenerate",
};

function upgradeItemRow(item) {
  const where = item.kind === "capture"
    ? `${item.phase} ${item.device} ${item.port || "all"}`
    : `inventory ${item.file}`;
  let cls = "pass";
  if (item.result === "not_regenerable") cls = "warn";
  else if (item.result === "changed") cls = "info";
  return {
    where,
    result: UPGRADE_RESULT_LABEL[item.result] || item.result,
    cls,
    reason: item.reason || "",
    notes: item.notes || [],
  };
}

function upgradeCounts(report) {
  const counts = { unchanged: 0, changed: 0, not_regenerable: 0 };
  for (const item of (report && report.items) || []) {
    if (item.result in counts) counts[item.result] += 1;
  }
  return counts;
}

function snapshotBadges(snap) {
  const badges = [];
  if (snap.outdated) badges.push({ cls: "warn", text: "outdated" });
  if (snap.has_raw === false) badges.push({ cls: "neutral", text: "no raw" });
  return badges;
}

function errorDetail(body, fallback) {
  const detail = body && body.detail;
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return fallback;
}
```

Do objektu `MigView` přidej `upgradeItemRow, upgradeCounts, snapshotBadges, errorDetail,`.

Run: `node --test tests/js/*.test.js` → PASS (83).

- [ ] **Step 4: Wire `app.js` — state, error code, header buttons, badges, notice**

1. V `this.state = {` přidej `upgradeModal: null,` a `upgradeGroupModal: null,`.
2. V `loadRun()` ve větvi `!res.ok` evaluace:

```js
        this.cache.evaluationError = {
          status: res.status,
          detail: body.detail || `evaluation selhala (${res.status})`,
          code: body.code || null,
        };
```

3. V hlavičce runu před tlačítko „Archive run“ vlož:

```js
    header.appendChild(
      el("button", {
        className: "btn btn-secondary run-header-upgrade",
        text: "Upgrade run",
        onClick: () => this.openUpgradeModal(),
      })
    );
```

4. Notice chyby evaluace nahraď:

```js
    if (this.cache.evaluationError) {
      const error = this.cache.evaluationError;
      const children = [document.createTextNode(error.detail)];
      if (error.code === "schema_outdated") {
        children.push(
          el("button", {
            className: "btn btn-secondary notice-action",
            text: "Upgrade run",
            onClick: () => this.openUpgradeModal(),
          })
        );
      }
      this.mainEl.appendChild(el("div", { className: "notice notice-warn", children }));
    }
```

5. V hlavičce skupiny před „Archive group“ vlož:

```js
        el("button", {
          className: "btn btn-secondary run-header-upgrade",
          text: "Upgrade group",
          onClick: () => this.openUpgradeGroupModal(),
        }),
```

6. V `renderSidebar()` v `snap-row-top` mezi `snap-row-label` a `phase-badge` vlož:

```js
                ...MigView.snapshotBadges(snap).map((badge) =>
                  el("span", { className: "badge-pill snap-badge " + badge.cls, text: badge.text })
                ),
```

7. V `renderModal()` hned za `clear(root);`:

```js
    if (this.state.upgradeModal) {
      this.renderUpgradeModal(root);
      return;
    }
    if (this.state.upgradeGroupModal) {
      this.renderUpgradeGroupModal(root);
      return;
    }
```

8. Do nápovědy `run.body` přidej: `"Upgrade run přegeneruje inventory a snímky z raw záznamu aktuální verzí nástroje (po upgradu nástroje nebo opravě parsování). Náhled ukáže, co se změní; změněné soubory jdou do backup/. Snímek se štítkem no raw přegenerovat nejde.",` a do `group.body`: `"Upgrade group udělá totéž pro všechny runy skupiny; run, který selže, ostatní nezastaví. Se spuštěným capture odmítne.",`.

- [ ] **Step 5: Add the modal methods to `app.js`** (za blok „archive run modal“)

```js
  // -- upgrade run / group modal ---------------------------------------------

  async openUpgradeModal() {
    if (!this.leaveGuard()) return;
    this.state.upgradeModal = { phase: "loading", report: null, error: null };
    this.render();
    await this.runUpgrade(true);
  }

  closeUpgradeModal() {
    const done = this.state.upgradeModal && this.state.upgradeModal.phase === "done";
    this.state.upgradeModal = null;
    if (done) {
      this.loadRun().then(() => this.render());
    } else {
      this.render();
    }
  }

  async runUpgrade(dryRun) {
    const modal = this.state.upgradeModal;
    const run = this.state.run;
    if (!modal || !run) return;
    modal.phase = dryRun ? "loading" : "applying";
    modal.error = null;
    this.render();
    try {
      const res = await fetch(`/api/runs/${run}/upgrade?dry_run=${dryRun}`, { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        modal.report = body;
        modal.phase = dryRun ? "preview" : "done";
      } else {
        modal.error = MigView.errorDetail(body, `upgrade selhal (${res.status})`);
        modal.phase = dryRun ? "error" : "preview";
      }
    } catch (err) {
      modal.error = String(err);
      modal.phase = dryRun ? "error" : "preview";
    }
    this.render();
  }

  async openUpgradeGroupModal() {
    if (!this.leaveGuard()) return;
    this.state.upgradeGroupModal = { phase: "loading", reports: null, error: null };
    this.render();
    await this.runGroupUpgrade(true);
  }

  closeUpgradeGroupModal() {
    const done = this.state.upgradeGroupModal && this.state.upgradeGroupModal.phase === "done";
    this.state.upgradeGroupModal = null;
    if (done) {
      this.loadGroupSummary().then(() => this.render());
    } else {
      this.render();
    }
  }

  async runGroupUpgrade(dryRun) {
    const modal = this.state.upgradeGroupModal;
    const name = this.state.group;
    if (!modal || !name) return;
    modal.phase = dryRun ? "loading" : "applying";
    modal.error = null;
    this.render();
    try {
      const res = await fetch(
        `/api/groups/${encodeURIComponent(name)}/upgrade?dry_run=${dryRun}`,
        { method: "POST" }
      );
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        modal.reports = body.runs || [];
        modal.phase = dryRun ? "preview" : "done";
      } else {
        modal.error = MigView.errorDetail(body, `upgrade selhal (${res.status})`);
        modal.phase = dryRun ? "error" : "preview";
      }
    } catch (err) {
      modal.error = String(err);
      modal.phase = dryRun ? "error" : "preview";
    }
    this.render();
  }

  buildUpgradeReport(report) {
    const counts = MigView.upgradeCounts(report);
    const rows = (report.items || []).map((item) => {
      const row = MigView.upgradeItemRow(item);
      const detail = [row.reason, ...row.notes].filter(Boolean).join(" · ");
      return el("tr", {
        children: [
          el("td", { className: "mono", text: row.where }),
          el("td", { children: [el("span", { className: "badge-pill " + row.cls, text: row.result })] }),
          el("td", { className: "upgrade-detail", text: detail }),
        ],
      });
    });
    const children = [
      el("p", {
        text: `${counts.changed} changed · ${counts.unchanged} unchanged · ${counts.not_regenerable} cannot regenerate`,
      }),
      el("table", { className: "upgrade-report", children: [el("tbody", { children: rows })] }),
    ];
    if (report.error) {
      children.push(el("div", { className: "notice notice-fail", text: `Run nezměněn: ${report.error}` }));
    } else if (report.backup) {
      children.push(
        el("p", {
          children: [document.createTextNode("Záloha: "), el("span", { className: "mono", text: report.backup })],
        })
      );
    }
    return el("div", { children });
  }

  buildUpgradeFooter(modal, canApply, onApply, onClose) {
    const busy = modal.phase === "loading" || modal.phase === "applying";
    const actions = [
      el("button", {
        className: "btn btn-secondary",
        text: modal.phase === "done" ? "Close" : "Cancel",
        onClick: busy ? null : onClose,
      }),
    ];
    if (modal.phase !== "done") {
      actions.push(
        el("button", {
          className: "btn btn-primary",
          text: modal.phase === "applying" ? "Applying…" : "Apply",
          attrs: canApply ? {} : { disabled: "disabled" },
          onClick: canApply ? onApply : null,
        })
      );
    }
    return el("div", { className: "footer-actions", children: actions });
  }

  mountUpgradeModal(root, modal, children, onClose) {
    const busy = modal.phase === "loading" || modal.phase === "applying";
    root.appendChild(
      el("div", {
        className: "modal-backdrop",
        onClick: (e) => {
          if (e.target.classList.contains("modal-backdrop") && !busy) onClose();
        },
        children: [el("div", { className: "modal modal-wide", children })],
      })
    );
  }

  renderUpgradeModal(root) {
    const modal = this.state.upgradeModal;
    const children = [
      el("h3", { text: "Upgrade run" }),
      el("p", {
        children: [
          document.createTextNode("Přegeneruje inventory a snímky runu "),
          el("span", { className: "mono", text: this.state.run || "" }),
          document.createTextNode(
            " z raw záznamu aktuální verzí nástroje. Změněné soubory se před přepsáním přesunou do backup/."
          ),
        ],
      }),
    ];
    if (modal.phase === "loading") children.push(el("p", { text: "Náhled (dry run)…" }));
    if (modal.phase === "applying") children.push(el("p", { text: "Upgraduju…" }));
    if (modal.report && modal.phase !== "loading") children.push(this.buildUpgradeReport(modal.report));
    if (modal.error) children.push(el("div", { className: "field-error", text: modal.error }));
    const canApply = modal.phase === "preview" && modal.report && !modal.report.error
      && MigView.upgradeCounts(modal.report).changed > 0;
    const close = () => this.closeUpgradeModal();
    children.push(this.buildUpgradeFooter(modal, canApply, () => this.runUpgrade(false), close));
    this.mountUpgradeModal(root, modal, children, close);
  }

  renderUpgradeGroupModal(root) {
    const modal = this.state.upgradeGroupModal;
    const children = [
      el("h3", { text: "Upgrade group" }),
      el("p", {
        children: [
          document.createTextNode("Přegeneruje všechny runy skupiny "),
          el("span", { className: "mono", text: this.state.group || "" }),
          document.createTextNode(" z raw záznamu. Run, který selže, ostatní nezastaví."),
        ],
      }),
    ];
    if (modal.phase === "loading") children.push(el("p", { text: "Náhled (dry run)…" }));
    if (modal.phase === "applying") children.push(el("p", { text: "Upgraduju…" }));
    if (modal.reports && modal.phase !== "loading") {
      for (const report of modal.reports) {
        children.push(el("h4", { className: "upgrade-report-run mono", text: report.run }));
        children.push(this.buildUpgradeReport(report));
      }
    }
    if (modal.error) children.push(el("div", { className: "field-error", text: modal.error }));
    const canApply = modal.phase === "preview" && (modal.reports || []).some(
      (report) => !report.error && MigView.upgradeCounts(report).changed > 0
    );
    const close = () => this.closeUpgradeGroupModal();
    children.push(this.buildUpgradeFooter(modal, canApply, () => this.runGroupUpgrade(false), close));
    this.mountUpgradeModal(root, modal, children, close);
  }
```

- [ ] **Step 6: CSS in `style.css`**

Vedle `.run-header-archive`:

```css
.run-header-upgrade { margin-left: auto; align-self: center; }
.run-header-upgrade + .run-header-archive { margin-left: 8px; }
```

Za `.snap-row-top`:

```css
.snap-row-top .snap-row-label { margin-right: auto; }
.snap-badge { font-size: 10.5px; padding: 1px 5px; }
```

Za `.modal-wide`:

```css
.upgrade-report { width: 100%; border-collapse: collapse; font-size: 13px; margin: 0 0 12px; }
.upgrade-report td { padding: 4px 8px; border-bottom: 1px solid #f3f4f6; vertical-align: top; }
.upgrade-detail { color: #6b7280; }
.upgrade-report-run { margin: 12px 0 6px; font-size: 14px; }
.notice-action { margin-left: 12px; }
```

- [ ] **Step 7: Verify**

Run: `node --test tests/js/*.test.js` → PASS; `.venv/bin/pytest -q -p no:warnings tests/gui` → PASS.

Smoke v prohlížeči: `.venv/bin/python -c "from raw_run import build_run; build_run('<scratch>/runs')"` spusť s `PYTHONPATH=tests`, pak `.venv/bin/mig-validate gui --run-root <scratch>/runs --port 8399`; otevři run `mig01`, ověř tlačítko „Upgrade run“ vedle „Archive run“, modal s náhledem (3× unchanged, Apply zakázané), štítky v sidebaru se neukazují (aktuální schéma s raw). Pak v `snapshot_post_PTX1_all.json` přepiš `"schema_version": 13` na `12`, obnov stránku: štítek `outdated` u post snímku a notice s tlačítkem „Upgrade run“. Pokud je k dispozici claude-in-chrome, udělej screenshoty a přilož je k reportu tasku; jinak popiš, co jsi ověřil.

- [ ] **Step 8: Commit**

```bash
git add migration_validator/gui/static/view.js migration_validator/gui/static/app.js migration_validator/gui/static/style.css tests/js/view.test.js
git commit -m "feat(gui): Upgrade run and Upgrade group modals, outdated/no raw badges

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Laborka — rozšířený filtr na obou platformách, bundly jako fixtures, akceptace

**Files:**
- Create: `tests/fixtures/raw/junos/…`, `tests/fixtures/raw/junos-evo/…` (zkopírované bundly)
- Create: `tests/raw/test_lab_bundles.py`

**Interfaces:**
- Consumes: celý capture + upgrade (Tasky 1-10).
- Produces: laboratorní bundly (pre, bez baseline) pro MX a EVO; test, že replay udělá přesně nahranou sekvenci volání.

- [ ] **Step 1: Capture z laborky do scratch run rootu**

```bash
eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"
RR=$(mktemp -d)
echo "$RR"
.venv/bin/mig-validate capture --run lab-raw --run-root "$RR" --device 172.20.20.4 --phase pre --parse-services --username admin --password "$MIG_LAB_PASSWORD"
.venv/bin/mig-validate capture --run lab-raw --run-root "$RR" --device 172.20.20.5 --phase post --parse-services --username admin --password "$MIG_LAB_PASSWORD"
.venv/bin/mig-validate capture --run lab-raw-evo --run-root "$RR" --device 172.20.20.5 --phase pre --parse-services --username admin --password "$MIG_LAB_PASSWORD"
```

Expected: tři `snapshot ulozen: …`, žádné varování `raw zaznam … se nepodarilo ulozit`. Pokud `--parse-services` na kterékoliv platformě selže na `get_config` (NETCONF odmítne `policy-options`/`firewall`/`class-of-service`), **STOP** — nahlas přesnou chybu, filtr sám neupravuj (je to rozhodnutí specu).

- [ ] **Step 2: Ověř, že konfigurace nese rozšířené hierarchie**

```bash
for d in "$RR"/lab-raw/raw/inventory_* "$RR"/lab-raw-evo/raw/inventory_*; do
  echo "$d"; python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['calls'][0]['filter'])" "$d/session.json"
  zcat "$d"/0001-get_config.xml.gz | grep -oE "<(policy-options|firewall|class-of-service)[ >]" | sort | uniq -c
done
```

Expected: filtr končí `policy-options, firewall, class-of-service`; počty zapiš do reportu (prázdná hierarchie je v pořádku — laborka ji nemusí mít nakonfigurovanou).

- [ ] **Step 3: Akceptace — upgrade stejnou verzí je beze změny**

```bash
.venv/bin/mig-validate upgrade lab-raw lab-raw-evo --run-root "$RR" --dry-run; echo "exit=$?"
.venv/bin/mig-validate upgrade lab-raw lab-raw-evo --run-root "$RR"; echo "exit=$?"
ls "$RR"/lab-raw "$RR"/lab-raw-evo
du -sh "$RR"/lab-raw/raw/* "$RR"/lab-raw-evo/raw/*
```

Expected: všechny řádky `pregenerovano - beze zmeny`, `exit=0` v obou bězích, žádný adresář `backup/`. Velikosti (gzip) zapiš do reportu. Pokud se objeví `pregenerovano - zmeneno` nebo `nelze`, **STOP** — replay se odchyluje od živého capture; použij superpowers:systematic-debugging (diff `json.loads` živého a přegenerovaného snapshotu z `--dry-run` nelze vidět — pusť `upgrade_run` z Pythonu s vlastním stagingem nebo porovnej `capture_device(ReplayDevice(...))` se souborem) a nahlas nález.

- [ ] **Step 4: Zkopíruj bundly do fixtures**

```bash
mkdir -p tests/fixtures/raw
cp -r "$RR"/lab-raw/raw/pre_172.20.20.4_all tests/fixtures/raw/junos
cp -r "$RR"/lab-raw-evo/raw/pre_172.20.20.5_all tests/fixtures/raw/junos-evo
ls tests/fixtures/raw/junos tests/fixtures/raw/junos-evo | head
```

(Jméno uzlu v novém runu je adresa hostu — ověř skutečná jména adresářů v `"$RR"/*/raw/` a použij je.)

Kontrola citlivých dat — fixtures jdou do pushovaného repa a konfigurace nese `protocols`:

```bash
zcat tests/fixtures/raw/*/inventory/*.xml.gz | grep -nE '\$9\$|authentication-key|secret|encrypted-password'
```

Expected: žádný výstup. Pokud cokoliv najde, **STOP** — necommituj a zeptej se uživatele (možnosti: vymazat hodnoty v nahrávce, nebo fixture konfigurace necommitovat).

- [ ] **Step 5: Write the test `tests/raw/test_lab_bundles.py`**

```python
"""Laboratorni bundly (MX1-POP1, PTX1-POP1, nahrano 2026-09-23 planem
raw retention, Task 14): replay musi udelat presne tataz volani jako zivy
capture - zadny miss, zadne volani navic.

Kdyz collector zmeni RPC nebo jeho argumenty, test spadne. Pak se bundly
nahraji z laborky znovu (Task 14 planu 2026-09-23-raw-retention-a-upgrade)
- jinak by upgrade starsich capture tise hlasil 'neni v raw zaznamu'.
"""

from pathlib import Path

import pytest

from migration_validator.capture import capture_device
from migration_validator.connection.junos import detect_platform
from migration_validator.models.inventory import load_inventory
from migration_validator.raw.bundle import read_session
from migration_validator.raw.recorder import RecordingDevice, SessionRecording
from migration_validator.raw.replay import ReplayDevice
from migration_validator.runs.services import generate_inventory

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "raw"


def _shape(calls):
    return [
        (call.rpc, call.kwargs, call.filter, call.error["type"] if call.error else None)
        for call in calls
    ]


@pytest.mark.parametrize("platform", ["junos", "junos-evo"])
def test_replay_makes_exactly_the_recorded_calls(platform, tmp_path):
    bundle = FIXTURES / platform
    session = read_session(bundle)
    inventory_session = read_session(bundle / "inventory")

    inventory_recording = SessionRecording()
    inventory_device = RecordingDevice(ReplayDevice(inventory_session), inventory_recording)
    inventory_path = tmp_path / "inventory.yml"
    generate_inventory(
        inventory_device, detect_platform(inventory_device), inventory_path,
        inventory_session.port_filter,
    )
    assert _shape(inventory_recording.calls) == _shape(inventory_session.calls)

    recording = SessionRecording()
    params = session.params
    capture_device(
        RecordingDevice(ReplayDevice(session), recording),
        session.address,
        inventory=load_inventory(inventory_path),
        collector_names=params["collectors"],
        phase=params["phase"],
        ping_count=params["ping_count"],
        now=session.started_at,
        finished_at=session.finished_at,
        service_types=params["service_types"],
        profile_name=params["profile_name"],
    )
    assert _shape(recording.calls) == _shape(session.calls)
    assert all(call.error is None or call.error["type"] != "NotRecorded" for call in recording.calls)
```

- [ ] **Step 6: Run and check the mutant**

Run: `.venv/bin/pytest tests/raw/test_lab_bundles.py -q -p no:warnings` → PASS (2 passed).
Mutant: v `ArpCollector.rpc_kwargs` změň `{"no_resolve": True}` na `{}` → test musí FAIL pro obě platformy. Vrať.

- [ ] **Step 7: Full suite + commit**

Run: `.venv/bin/pytest -q -p no:warnings` → PASS.

```bash
git add tests/fixtures/raw tests/raw/test_lab_bundles.py
git commit -m "test(raw): lab raw bundles for MX and EVO, replay must make the recorded calls

Lab acceptance 2026-09-23: extended get_config filter accepted on both
platforms; same-version upgrade reports every capture unchanged.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

V reportu tasku uveď: velikosti bundlů (Step 3), počty extra hierarchií (Step 2), výstup `upgrade`.

---

### Task 15: Dokumentace

**Files:**
- Modify: `docs/cs/README.md`, `docs/en/README.md`
- Modify: `docs/cs/files/collectors.md`, `docs/cs/files/tests.md`, `docs/cs/architecture.md`, `docs/en/architecture.md` (a další soubory, které najde grep níže)

**Interfaces:**
- Consumes: hotové chování z Tasků 1-14.
- Produces: dokumentace bez `--record-raw`, se sekcí o raw retention a `upgrade`.

- [ ] **Step 1: Najdi všechny zmínky**

Run: `grep -rn "record-raw\|record_raw" docs/cs docs/en README.md`
Expected (výchozí stav): `docs/cs/README.md:115,597-598`, `docs/en/README.md:114,495`, `docs/cs/files/collectors.md:49,240`, `docs/cs/files/tests.md:113`, `docs/cs/architecture.md:258`, `docs/en/architecture.md:257`.

- [ ] **Step 2: Oprav zmínky**

- Tabulka přepínačů `capture` (cs 115, en 114): řádek `--record-raw DIR` smaž.
- Odstavec „Totéž se dá udělat mimochodem při běžném sběru: `capture --record-raw DIR`…“ (cs 597-598, en ~495): nahraď větou „Capture do runu si syrové odpovědi ukládá sám (`raw/`, viz Raw záznam a upgrade runu); `record` zůstává pro testovací fixtures.“ / „A capture into a run keeps the raw replies itself (`raw/`, see Raw recording and run upgrade); `record` stays for test fixtures.“
- `collectors.md:49`, `architecture.md` (cs 258, en 257): „`record` a `--record-raw` uloží jen první“ → „`record` uloží jen první“ (raw retention nahrává, co collector opravdu zavolá, takže se ho věta netýká).
- `collectors.md:240`: „`record` i `--record-raw` z ní nahrají **obě** odpovědi“ → „`record` z ní nahraje **obě** odpovědi“.
- `tests.md:113`: řádek `test_capture.py::test_record_raw_writes_every_rpc_of_multi_rpc_collector` nahraď řádkem `raw/test_capture_round_trip.py::test_replay_reproduces_live_snapshot | replay musí dát tentýž snapshot jako živý capture (multi-RPC collectory, chyby, pingy)` a přidej `raw/test_lab_bundles.py::test_replay_makes_exactly_the_recorded_calls | laboratorní bundly: replay dělá přesně nahraná volání; při změně RPC collectoru nahrát znovu z laborky`.

- [ ] **Step 3: Nová sekce v `docs/cs/README.md`**

V „### Struktura `runs/<nazev>/`“ doplň do výpisu:

```
  raw/                      syrové odpovědi zařízení per capture/inventory (raw retention)
  backup/upgrade-<čas>/     soubory nahrazené příkazem upgrade
  .lock                     zámek runu (zápis capture, výměna při upgradu)
```

Za „### Archivace a úklid runů“ vlož:

```markdown
### Raw záznam a upgrade runu (`upgrade`)

Každý capture do runu uloží vedle snapshotu syrová data ze zařízení do
`raw/<kmen snapshotu>/`: `session.json` (vstupy capture, facts, seznam volání)
a odpovědi všech RPC včetně pingů jako `NNNN-<rpc>.xml.gz`. `--parse-services`
stejně uloží konfiguraci, ze které vznikla inventory (`raw/inventory_<…>/`), a každý
capture si kopii přibalí do `inventory/`. Konfigurace se stahuje s hierarchiemi
parseru plus `policy-options`, `firewall` a `class-of-service` — parser je nevidí,
jsou tu pro budoucí verze. **Pozor:** v raw je konfigurace včetně `$9$` klíčů pod
`protocols`; run adresáře ber jako citlivá data.

Když nová verze nástroje změní schéma snímků (nebo opraví parsování),
`upgrade` z raw záznamu přegeneruje inventory i snímky aktuální verzí:

```bash
.venv/bin/mig-validate upgrade migration-01 --dry-run   # jen report
.venv/bin/mig-validate upgrade migration-01             # přegeneruje, změněné soubory → backup/
.venv/bin/mig-validate upgrade --group pop1             # všechny runy skupiny
```

Report má řádek na soubor: `pregenerovano - beze zmeny`, `pregenerovano - zmeneno`
nebo `nelze - <důvod>` (snímek bez raw záznamu, konfigurace bez potřebné hierarchie,
raw nepatří ke snímku). Co přegenerovat nejde, zůstává beze změny. Co v raw záznamu
není (nový collector, nový ping cíl), se nikdy nevydává za výsledek: collector má
status error a jeho checky SKIP, ping „neodeslan (neni v raw zaznamu)“.

Návratový kód: 0 vše přegenerováno, 1 některé soubory nejdou, 2 upgrade runu
spadl a run zůstal beze změny. V GUI stejné dělají tlačítka **Upgrade run**
a **Upgrade group** (náhled, pak Apply); snímky mají štítky `outdated` a `no raw`.

Snímky zachycené před zavedením raw záznamu (2026-09-23) přegenerovat nejde;
po příštím bumpu schématu je nová verze nenačte.
```

- [ ] **Step 4: Totéž anglicky v `docs/en/README.md`**

Stejná místa (struktura runu, sekce za „Archiving and cleaning up runs“), text:

```markdown
### Raw recording and run upgrade (`upgrade`)

Every capture into a run stores the device's raw data next to the snapshot in
`raw/<snapshot stem>/`: `session.json` (capture inputs, facts, list of calls) and
every RPC reply including pings as `NNNN-<rpc>.xml.gz`. `--parse-services` likewise
stores the configuration the inventory was built from (`raw/inventory_<…>/`), and
each capture bundles a copy under `inventory/`. The configuration is fetched with
the parser hierarchies plus `policy-options`, `firewall` and `class-of-service` —
the parser does not see them, they are there for future versions. **Note:** the raw
configuration includes `$9$` keys under `protocols`; treat run directories as
sensitive.

When a new tool version changes the snapshot schema (or fixes parsing), `upgrade`
regenerates inventories and snapshots from the raw recording with the current version:

```bash
.venv/bin/mig-validate upgrade migration-01 --dry-run   # report only
.venv/bin/mig-validate upgrade migration-01             # regenerate, replaced files → backup/
.venv/bin/mig-validate upgrade --group pop1             # every run of the group
```

The report has one line per file: `pregenerovano - beze zmeny` (unchanged),
`pregenerovano - zmeneno` (changed) or `nelze - <reason>` (no raw recording, config
without a needed hierarchy, raw does not belong to the snapshot). Whatever cannot be
regenerated stays untouched. What the recording lacks (a new collector, a new ping
target) is never passed off as a result: the collector gets status error and its
checks SKIP, the ping reads "neodeslan (neni v raw zaznamu)".

Exit code: 0 everything regenerated, 1 some files cannot be, 2 the run's upgrade
failed and the run was left unchanged. In the GUI the **Upgrade run** and **Upgrade
group** buttons do the same (preview, then Apply); snapshots carry `outdated` and
`no raw` badges.

Snapshots captured before raw recording existed (2026-09-23) cannot be regenerated;
after the next schema bump the new version will not load them.
```

- [ ] **Step 5: Verify and commit**

Run: `grep -rn "record-raw\|record_raw" docs/cs docs/en README.md` → žádný výskyt. `.venv/bin/pytest -q -p no:warnings` → PASS.

```bash
git add docs/cs docs/en README.md
git commit -m "docs: raw recording and mig-validate upgrade, drop --record-raw

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

## Po dokončení

- Celá sada: `.venv/bin/pytest -q -p no:warnings` a `node --test tests/js/*.test.js` zelené.
- Finální review celé větve, pak superpowers:finishing-a-development-branch.
- Po merge restartovat laboratorní GUI (běží ze starého kódu).
- Follow-up `docs/superpowers/followup-2026-09-23-kolize-adres-peeru-vrstva-2.md`: vrstva 2 teď může bumpnout schéma — snímky s raw přegeneruje `upgrade` novým collectorem.
