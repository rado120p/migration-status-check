# LAG porty a migrace po krocích — implementační plán

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port-po-portu migrace funguje i pro N:1 (staré UNI porty → sdílený
LAG): evaluace per mapping s filtrem přes baseline, `--parse-services` vždy
regeneruje inventory, pre snímek je chráněn `--overwrite`, ping cíle se
sjednocují ze všech mapovaných pre snímků.

**Architecture:** `plan_evaluations` iteruje `interface_mapping` (jeden
záznam = jedna evaluace s polem `step`), engine dostane `step` a potlačí
nespárované subject služby do `RunResult.excluded_services`, renderer
tiskne krok v hlavičce + souhrnný řádek. Žádné schema změny (run.yml 1,
snapshot 9, RunResult 1 — nové JSON klíče jsou aditivní).

**Tech Stack:** Python 3.13, pytest, PyYAML, dataclasses. Žádné nové
závislosti, žádné nové RPC.

**Spec:** `docs/superpowers/specs/2026-08-17-lag-port-migrace-po-krocich-design.md`

## Global Constraints

- Testy se pouští `.venv/bin/python -m pytest` z kořene repa; před
  odevzdáním úkolu musí být zelená celá sada (`.venv/bin/python -m pytest tests -q`).
- `migration_validator/**` je ASCII (žádná diakritika v kódu ani
  stringách); česky s diakritikou jsou jen docs a vyjmenované parser testy.
- run.yml schema zůstává 1, snapshot schema 9, RunResult `schema_version` 1.
  Nové JSON klíče (`step`, `excluded_services`) jsou aditivní a přidávají se
  jen když nejsou None (vzor `profile`/`filtered` v `models/result.py:249-254`).
- Nikdy neregenerovat `tests/fixtures/172.20.20.{4,5}.yml` z labu — drží
  záměrně starý commitnutý stav. Nové scénáře se staví v testech ručně.
- Commit messages česky bez diakritiky, prefixy `feat:`/`fix:`/`test:`/`docs:`,
  patička `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Docstring/komentář smí tvrdit chování mutantu jen pokud ho implementátor
  mutantem ověřil (viz pravidla projektu).

---

### Task 1: Plánování evaluací per mapping (`Evaluation.step`)

**Files:**
- Modify: `migration_validator/runs/pairing.py`
- Test: `tests/runs/test_pairing.py`

**Interfaces:**
- Consumes: `RunManifest`, `InterfaceMapping`, `MappingEndpoint`,
  `CaptureRecord` z `migration_validator/runs/manifest.py` (beze změn).
- Produces: `Evaluation` získává pole `step: InterfaceMapping | None = None`.
  `plan_evaluations(manifest, ports)` vrací pro post snímek s N mapovanými
  old porty N evaluací (každá se `step`); `ports` filtr se u evaluací se
  `step` vztahuje na **starý** port (`step.old.port`), jinak na
  `subject.port` jako dnes. Task 7 čte `evaluation.step`.

- [ ] **Step 1: Napiš failing testy**

Do `tests/runs/test_pairing.py` přidej (helper `_manifest` už existuje,
`InterfaceMapping`/`MappingEndpoint` jsou naimportované):

```python
def _manifest_lag():
    """Dva stare UNI porty mapovane na jeden novy LAG port."""
    manifest = _manifest()
    manifest.interface_mapping = [
        InterfaceMapping(
            old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/4"),
            new=MappingEndpoint(node="PTX1-POP1", port="ae0"),
        ),
        InterfaceMapping(
            old=MappingEndpoint(node="MX1-POP1", port="ge-0/0/5"),
            new=MappingEndpoint(node="PTX1-POP1", port="ae0"),
        ),
    ]
    return manifest


def test_post_on_shared_lag_yields_one_evaluation_per_mapping():
    manifest = _manifest_lag()
    pre4 = CaptureRecord("pre", "MX1-POP1", "ge-0/0/4", "pre4.json", "T1")
    pre5 = CaptureRecord("pre", "MX1-POP1", "ge-0/0/5", "pre5.json", "T2")
    post = CaptureRecord("post", "PTX1-POP1", "ae0", "post.json", "T3")
    manifest.captures = [pre4, pre5, post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 2
    assert [e.subject for e in evaluations] == [post, post]
    assert {e.baseline for e in evaluations} == {pre4, pre5}
    steps = {(e.step.old.port, e.step.new.port) for e in evaluations}
    assert steps == {("ge-0/0/4", "ae0"), ("ge-0/0/5", "ae0")}
    assert all(e.reason is None for e in evaluations)


def test_mapped_step_without_per_port_pre_falls_back_to_whole_box():
    manifest = _manifest_lag()
    pre_all = CaptureRecord("pre", "MX1-POP1", None, "pre_all.json", "T1")
    post = CaptureRecord("post", "PTX1-POP1", "ae0", "post.json", "T2")
    manifest.captures = [pre_all, post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 2
    assert all(e.baseline == pre_all for e in evaluations)
    assert all(e.step is not None for e in evaluations)


def test_mapped_step_without_any_pre_has_reason():
    manifest = _manifest_lag()
    post = CaptureRecord("post", "PTX1-POP1", "ae0", "post.json", "T1")
    manifest.captures = [post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 2
    assert all(e.baseline is None for e in evaluations)
    assert "ge-0/0/4" in evaluations[0].reason
    assert "ge-0/0/5" in evaluations[1].reason


def test_ports_filter_selects_by_old_port_of_step():
    manifest = _manifest_lag()
    pre4 = CaptureRecord("pre", "MX1-POP1", "ge-0/0/4", "pre4.json", "T1")
    pre5 = CaptureRecord("pre", "MX1-POP1", "ge-0/0/5", "pre5.json", "T2")
    post = CaptureRecord("post", "PTX1-POP1", "ae0", "post.json", "T3")
    manifest.captures = [pre4, pre5, post]

    evaluations = plan_evaluations(manifest, ports=["ge-0/0/4"])

    assert len(evaluations) == 1
    assert evaluations[0].baseline == pre4


def test_unmapped_post_keeps_todays_fallback():
    # zadny mapping -> celoboxovy fallback, step zustava None (dnesni chovani)
    manifest = _manifest_lag()
    manifest.interface_mapping = []
    pre_all = CaptureRecord("pre", "MX1-POP1", None, "pre_all.json", "T1")
    post = CaptureRecord("post", "PTX1-POP1", "et-0/0/0", "post.json", "T2")
    manifest.captures = [pre_all, post]

    evaluations = plan_evaluations(manifest)

    assert len(evaluations) == 1
    assert evaluations[0].baseline == pre_all
    assert evaluations[0].step is None
```

- [ ] **Step 2: Ověř, že testy padají**

Run: `.venv/bin/python -m pytest tests/runs/test_pairing.py -v`
Expected: nové testy FAIL (`Evaluation` nemá `step`, počty evaluací nesedí);
staré testy PASS.

- [ ] **Step 3: Implementuj v `runs/pairing.py`**

```python
from migration_validator.runs.manifest import (
    CaptureRecord,
    InterfaceMapping,
    RunManifest,
)


@dataclass
class Evaluation:
    subject: CaptureRecord
    baseline: CaptureRecord | None
    reason: str | None = None
    # Migracni krok (zaznam interface_mapping), ktery evaluaci vyrobil.
    # None = celoboxova nebo nemapovana evaluace - chovani beze zmeny.
    step: InterfaceMapping | None = None
```

`_plan_post` vrací nově `list[Evaluation]`:

```python
def _plan_post(manifest: RunManifest, subject: CaptureRecord) -> list[Evaluation]:
    steps = [
        mapping
        for mapping in manifest.interface_mapping
        if subject.port is not None
        and mapping.new.node == subject.device
        and mapping.new.port == subject.port
    ]

    if not steps:
        baseline = find_pre_baseline(manifest, subject.device, subject.port)
        if baseline is None:
            return [
                Evaluation(
                    subject=subject,
                    baseline=None,
                    reason="chybi pre snimek stareho boxu",
                )
            ]
        return [Evaluation(subject=subject, baseline=baseline)]

    evaluations: list[Evaluation] = []
    for mapping in steps:
        baseline = manifest.find_capture(
            "pre", mapping.old.node, mapping.old.port
        ) or manifest.find_capture("pre", mapping.old.node, None)
        if baseline is None:
            evaluations.append(
                Evaluation(
                    subject=subject,
                    baseline=None,
                    reason=(
                        "chybi pre snimek "
                        f"{mapping.old.node}:{mapping.old.port}"
                    ),
                    step=mapping,
                )
            )
        else:
            evaluations.append(
                Evaluation(subject=subject, baseline=baseline, step=mapping)
            )
    return evaluations
```

`plan_evaluations` filtruje až hotové evaluace (u kroku podle starého portu):

```python
def _filter_port(evaluation: Evaluation) -> str | None:
    if evaluation.step is not None:
        return evaluation.step.old.port
    return evaluation.subject.port


def plan_evaluations(
    manifest: RunManifest, ports: list[str] | None = None
) -> list[Evaluation]:
    """Naplanuje evaluace pro capturey s fazi post/rollback.

    pre captury samy o sobe evaluaci netvori - jsou jen baseline zdroj.
    Post snimek portu s vice mapovanymi old porty vyrobi evaluaci na kazdy
    mapping (migracni krok); `ports` filtr se u kroku vztahuje na stary port.
    """
    evaluations: list[Evaluation] = []
    for capture in manifest.captures:
        if capture.phase not in ("post", "rollback"):
            continue
        if capture.phase == "post":
            evaluations.extend(_plan_post(manifest, capture))
        else:
            evaluations.append(_plan_rollback(manifest, capture))
    return [
        evaluation
        for evaluation in evaluations
        if _passes_port_filter(_filter_port(evaluation), ports)
    ]
```

Existující test `test_ports_filter_keeps_only_matching_and_drops_whole_box`
filtruje 1:1 mapovaný post podle **nového** portu — po změně krok filtruje
starým portem, takže test uprav: filtruj starým portem z mappingu (přečti si
ho a zachovej jeho záměr: celoboxový snímek `--port` filtr zahazuje).

- [ ] **Step 4: Ověř, že testy prochází**

Run: `.venv/bin/python -m pytest tests/runs/ tests/test_cli.py -q`
Expected: PASS (test_cli.py kryje `evaluate --run` end-to-end — 1:1 případy
musí projít beze změny chování).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/runs/pairing.py tests/runs/test_pairing.py
git commit -m "feat: plan_evaluations vyrabi evaluaci per mapping (N:1 UNI->LAG)"
```

---

### Task 2: Ping cíle ze sjednocení mapovaných pre snímků

**Files:**
- Modify: `migration_validator/runs/manifest.py` (helper `mapped_olds`)
- Modify: `migration_validator/capture.py:75-132`
- Modify: `migration_validator/api.py:22-56` (parametr `baselines`)
- Modify: `migration_validator/cli.py:464-473` (blok baseline v `_capture_into_run`)
- Test: `tests/runs/test_manifest.py`, `tests/test_capture.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `RunManifest.find_capture`, `resolve_targets(scopes, arp, nd, *,
  baseline_arp, baseline_nd)` z `probes/ping.py` (beze změny).
- Produces: `RunManifest.mapped_olds(new_node: str, new_port: str) ->
  list[MappingEndpoint]`; `api.capture(..., baselines: list[Snapshot] | None
  = None)` a `capture_device(..., baselines=...)` — parametr `baseline`
  (jednotné číslo) ZANIKÁ; jediný volající je `cli._capture_into_run`
  a testy.

- [ ] **Step 1: Napiš failing testy**

`tests/runs/test_manifest.py` — přidej:

```python
def test_mapped_olds_returns_all_old_endpoints_of_new_port():
    manifest = RunManifest()
    manifest.interface_mapping = [
        InterfaceMapping(
            old=MappingEndpoint(node="MX1", port="ge-0/0/4"),
            new=MappingEndpoint(node="PTX1", port="ae0"),
        ),
        InterfaceMapping(
            old=MappingEndpoint(node="MX1", port="ge-0/0/5"),
            new=MappingEndpoint(node="PTX1", port="ae0"),
        ),
        InterfaceMapping(
            old=MappingEndpoint(node="MX1", port="ge-0/0/6"),
            new=MappingEndpoint(node="PTX1", port="ae1"),
        ),
    ]

    olds = manifest.mapped_olds("PTX1", "ae0")

    assert [o.port for o in olds] == ["ge-0/0/4", "ge-0/0/5"]
    assert manifest.mapped_olds("PTX1", "et-9/9/9") == []
```

`tests/test_capture.py` — najdi existující test baseline pingů (grep
`baseline` v souboru; mockuje `run_ping` a staví baseline Snapshot
s `facts={"arp": [...], "nd": [...]}`). Přidej vedle něj test sjednocení
podle stejného vzoru:

```python
def test_ping_baselines_merge_and_dedup_by_ip():
    """Dva pre snimky: spolecna IP se pinguje jednou, unikatni z obou."""
    # Postav baseline_a a baseline_b stejne jako sousedni baseline test:
    # baseline_a facts["arp"] = [{"ip": "10.0.0.2", ...}, {"ip": "10.0.0.3", ...}]
    # baseline_b facts["arp"] = [{"ip": "10.0.0.3", ...}, {"ip": "10.0.0.4", ...}]
    # Zavolej capture_device(..., baselines=[baseline_a, baseline_b])
    # s mocknutym run_ping, ktery zaznamenava cile.
    # Assert: cilove IP jsou presne {"10.0.0.2", "10.0.0.3", "10.0.0.4"}
    # a "10.0.0.3" je v seznamu prave jednou.
```

`tests/test_cli.py` — harness je `main([...])` + `_fake_capture(monkeypatch)`
(vrací dict `calls` se kwargs posledního volání `api.capture`). Vzor:
`test_capture_run_post_with_pre_snapshot_passes_baseline` (řádek ~354) —
ten zároveň uprav na `baselines` (kwarg `baseline` zaniká). Nový test:

```python
def test_capture_run_post_on_lag_passes_union_of_mapped_pre_snapshots(
    tmp_path, monkeypatch
):
    """run.yml se 2 mappingy ge-0/0/4,ge-0/0/5 -> ae0 a obema pre snimky:
    post capture ae0 dostane baselines=[pre4, pre5] (poradi mappingu)."""
    # Priprava: postav run adresar jako sousedni testy (main capture --phase
    # pre --port ge-0/0/4 --maps-to ..., pak ge-0/0/5), pote post na ae0.
    # Assert: calls["baselines"] ma delku 2 a adresy/faze odpovidaji obema
    # pre snimkum; pri jedinem mappingu ma delku 1 (dnesni chovani).
```

- [ ] **Step 2: Ověř, že testy padají**

Run: `.venv/bin/python -m pytest tests/runs/test_manifest.py tests/test_capture.py -v`
Expected: FAIL (`mapped_olds` neexistuje, `baselines` parametr neexistuje).

- [ ] **Step 3: Implementuj**

`manifest.py` — do `RunManifest` vedle `paired_old`:

```python
def mapped_olds(self, new_node: str, new_port: str) -> list[MappingEndpoint]:
    """Vsechny stare endpointy mapovane na dany novy port (N:1 u LAGu)."""
    return [
        mapping.old
        for mapping in self.interface_mapping
        if mapping.new.node == new_node and mapping.new.port == new_port
    ]
```

`capture.py` — parametr `baseline: Snapshot | None` nahraď
`baselines: list[Snapshot] | None = None` a merge s dedupem podle IP:

```python
def _merged_baseline_entries(
    snapshots: list[Snapshot], area: str
) -> list[dict[str, Any]]:
    """Sjednoceni ARP/ND zaznamu vice pre snimku, dedup podle IP.

    Prvni vyskyt vyhrava - poradi snimku urcuje cli (poradi mappingu).
    """
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        for entry in snapshot.facts.get(area, []):
            ip = entry.get("ip")
            if ip is None or ip in seen:
                continue
            seen.add(ip)
            merged.append(entry)
    return merged
```

a v těle (místo řádků 123–124):

```python
        baseline_arp = (
            _merged_baseline_entries(baselines, "arp") if baselines else None
        )
        baseline_nd = (
            _merged_baseline_entries(baselines, "nd") if baselines else None
        )
```

`api.py` — `capture(..., baselines: list[Snapshot] | None = None)`, forward
do `capture_device(..., baselines=baselines)`; docstring uprav: "`baselines`
jsou pre snimky starych boxu mapovanych na tento port - ping cile pro
--phase post se prednostne odvozuji ze sjednoceni jejich ARP/ND".

`cli.py` — blok `if phase == "post":` (dnes řádky 464–473) nahraď:

```python
    baselines: list[Snapshot] = []
    if phase == "post":
        seen_snapshots: set[str] = set()
        records = []
        for old in manifest.mapped_olds(node, args.port) if args.port else []:
            record = manifest.find_capture(
                "pre", old.node, old.port
            ) or manifest.find_capture("pre", old.node, None)
            if record is not None and record.snapshot not in seen_snapshots:
                seen_snapshots.add(record.snapshot)
                records.append(record)
        if not records:
            fallback = find_pre_baseline(manifest, node, args.port)
            if fallback is not None:
                records.append(fallback)
        for record in records:
            baselines.append(_load_snapshot(str(store.dir / record.snapshot)))
        if not baselines:
            print(
                "pre snimek nenalezen, ping cile z vlastni ARP",
                file=sys.stderr,
            )
```

a `api.capture(..., baseline=baseline, ...)` změň na
`api.capture(..., baselines=baselines or None, ...)`. Zkontroluj typ
`Snapshot` v importech cli (už tam je přes `_load_snapshot`? — pokud ne,
importuj jen pro typ, nebo anotaci vynech, soubor typy nevynucuje).

- [ ] **Step 4: Ověř, že testy prochází**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS (grep `baseline=` v tests/test_capture.py a tests/test_cli.py —
všechna volání se starým parametrem uprav na `baselines=[...]`).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/runs/manifest.py migration_validator/capture.py \
  migration_validator/api.py migration_validator/cli.py \
  tests/runs/test_manifest.py tests/test_capture.py tests/test_cli.py
git commit -m "feat: ping cile ze sjednoceni ARP/ND vsech mapovanych pre snimku"
```

---

### Task 3: `--parse-services` vždy regeneruje inventory

**Files:**
- Modify: `migration_validator/cli.py:411-425` (`_parse_services_into`),
  `migration_validator/cli.py:444-462` (výběr inventory v `_capture_into_run`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `generate_inventory(device, platform, output_path, port)` z
  `runs/services.py` (beze změny); inventory YAML má tvar
  `{"schema_version": ..., "device": ..., "interfaces": [...]}` a služby
  nesou klíč `interface`.
- Produces: `_parse_services_into(args, inventory_path)` přepisuje existující
  soubor a tiskne deltu. Chování bez flagu se nemění.

- [ ] **Step 1: Napiš failing test**

Do `tests/test_cli.py`. Vzor je
`test_capture_run_parse_services_generates_inventory` (řádek ~519):
`_fake_capture(monkeypatch)` + mock
`monkeypatch.setattr("migration_validator.cli.generate_inventory", fake)` —
fake zapisuje YAML `{"schema_version": 5, "device": ..., "interfaces": [...]}`
do `output_path`. Nový test:

```python
def test_parse_services_regenerates_existing_inventory_and_prints_delta(
    tmp_path, monkeypatch, capsys
):
    """--parse-services prepise existujici per-port inventory a vypise deltu."""
    _fake_capture(monkeypatch)
    # Predpriprav soubor vlny 1: runs adresar mig01, inventory pro ae0
    # s jednou sluzbou {"interface": "ae0.15", ...}.
    # fake_generate_inventory zapise dve sluzby: ae0.15 a ae0.16.
    # Spust: main(["capture", "--run", "mig01", "--run-root", str(tmp_path),
    #   "--device", "172.20.20.5", "--phase", "post", "--port", "ae0",
    #   "--parse-services"])
    # Assert: code == 0; soubor ma 2 interfaces; vystup obsahuje
    # "inventory pregenerovana" a "+1 nove, -0 odebrane";
    # "generovani se preskakuje" se nikde nevyskytuje.
```

Existující `test_capture_run_parse_services_skips_existing_inventory`
(řádek ~582, s `fail_generate_inventory`) smaž — zamyká chování, které
tímto taskem záměrně zaniká (spec: revize fáze-4 rozhodnutí).

- [ ] **Step 2: Ověř, že test padá**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v -k parse_services`
Expected: nový test FAIL (soubor se nepřegeneroval, stará hláška).

- [ ] **Step 3: Implementuj v cli.py**

`_parse_services_into` — před generováním načti předchozí stav, po něm
vypiš deltu:

```python
def _inventory_interfaces(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        entry.get("interface")
        for entry in raw.get("interfaces") or []
        if entry.get("interface")
    }


def _parse_services_into(args: argparse.Namespace, inventory_path: Path) -> None:
    """Vyrobi inventory pro --parse-services v samostatnem kratkem spojeni.

    Existujici soubor se pregeneruje - konfigurace noveho boxu se meni
    kazdou vlnou a flag je explicitni umysl (revize faze 4, viz spec
    2026-08-17). api.capture se nemeni - konfigurace pro inventory se
    stahne pred snapshotem.
    """
    previous = _inventory_interfaces(inventory_path)
    ...  # stavajici telo (connect + generate_inventory) beze zmeny
    current = _inventory_interfaces(inventory_path) or set()
    if previous is None:
        print(f"inventory vyrobena: {inventory_path} ({len(current)} sluzeb)")
    else:
        added = len(current - previous)
        removed = len(previous - current)
        print(
            f"inventory pregenerovana: {inventory_path} "
            f"({len(current)} sluzeb, +{added} nove, -{removed} odebrane)"
        )
```

(`yaml` je v cli.py potřeba naimportovat, pokud ještě není.)

Výběr inventory v `_capture_into_run` (dnes řádky 444–462) přepiš:

```python
    if args.inventory:
        inventory_path = Path(args.inventory)
    elif args.parse_services:
        inventory_path = store.inventory_path(node, args.port)
        _parse_services_into(args, inventory_path)
    else:
        inventory_path = store.inventory_path(node, args.port)
        if not inventory_path.exists():
            inventory_path = store.inventory_path(node, None)
        if not inventory_path.exists():
            raise ToolError("inventory nenalezena - spust s --parse-services")
```

Původní hláška „inventory jiz existuje, generovani se preskakuje" zaniká.

- [ ] **Step 4: Ověř, že testy prochází**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/cli.py tests/test_cli.py
git commit -m "feat: --parse-services vzdy pregeneruje inventory a vypise deltu"
```

---

### Task 4: Pojistka proti přepsání pre snímku (`--overwrite`)

**Files:**
- Modify: `migration_validator/cli.py` (`_capture_into_run` + argparse
  definice capture subcommandu, dnes kolem řádku 641)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `manifest.find_capture("pre", node, port)`, `ToolError`.
- Produces: `capture --run --phase pre` na node/port s existujícím pre
  záznamem = ToolError; `--overwrite` (store_true) ho povolí. Jen pro pre.

- [ ] **Step 1: Napiš failing test**

```python
def test_pre_capture_refuses_overwrite_without_flag(tmp_path, monkeypatch, capsys):
    """Druhy pre capture stejneho node/port konci chybou s navodem;
    s --overwrite probehne. post se prepisuje bez flagu (dnesni chovani)."""
    _fake_capture(monkeypatch)
    args = [
        "capture", "--run", "mig01", "--run-root", str(tmp_path),
        "--device", "172.20.20.4", "--phase", "pre",
        "--inventory", "tests/fixtures/172.20.20.4.yml",
    ]
    assert main(args) == 0                       # vlna 1: pre vznikne
    assert main(args) == 2                       # opakovani: ToolError
    err = capsys.readouterr().err
    assert "pre snimek uz existuje" in err
    assert "snapshot_pre_172.20.20.4_all.json" in err
    assert main(args + ["--overwrite"]) == 0     # explicitni prepis projde
```

Druhý test stejným vzorem: `--phase post` dvakrát po sobě bez flagu → oba
běhy `== 0` (post guard nemá).

- [ ] **Step 2: Ověř, že test padá**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v -k overwrite`
Expected: FAIL (flag neexistuje / žádná chyba se nevyhodí).

- [ ] **Step 3: Implementuj**

V `_capture_into_run` hned po `node = manifest.node_for_host(...)`
(před výběrem inventory — guard musí padnout dřív, než se otevře spojení):

```python
    if phase == "pre" and not args.overwrite:
        existing = manifest.find_capture("pre", node, args.port)
        if existing is not None:
            raise ToolError(
                f"pre snimek uz existuje: {existing.snapshot}; "
                "prepis povol s --overwrite"
            )
```

argparse (u capture subcommandu):

```python
    capture.add_argument(
        "--overwrite",
        action="store_true",
        help="povol prepsani existujiciho pre snimku v run adresari",
    )
```

- [ ] **Step 4: Ověř, že testy prochází**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/cli.py tests/test_cli.py
git commit -m "feat: pre snimek chranen proti prepsani, povoluje --overwrite"
```

---

### Task 5: Filtr přes baseline v enginu + `RunResult.step`/`excluded_services`

**Files:**
- Modify: `migration_validator/engine.py:418-564` (`evaluate_snapshots`)
- Modify: `migration_validator/models/result.py:208-256` (`RunResult`)
- Test: `tests/test_engine.py`, `tests/models/` (to_dict test vedle
  existujících RunResult testů — najdi grep `to_dict`)

**Interfaces:**
- Consumes: `match_scopes` (MatchSet s pairs/unmatched_*), `link_scopes`
  payloady (`link_payloads.get(scope.id)` vrací dict s `peer_scope_id`),
  `_unmatched_entry(scope, reason)`.
- Produces: `evaluate_snapshots(..., step: dict[str, Any] | None = None)`;
  tvar `step = {"old": {"node": str, "port": str}, "new": {"node": str,
  "port": str}}`. `RunResult.step: dict | None = None`,
  `RunResult.excluded_services: list[dict] | None = None` (položky
  `{"scope_id", "description", "service_type", "reason"}` — přesně tvar
  `_unmatched_entry`, engine.py:47-53).
  Task 6 (renderer) a Task 7 (api/cli) na tyhle názvy spoléhají.

- [ ] **Step 1: Napiš failing testy**

Do `tests/test_engine.py` (helpery `_scope`, `_snapshot` v souboru existují;
STEP konstanta nová):

```python
STEP = {
    "old": {"node": "MX1-POP1", "port": "ge-0/0/4"},
    "new": {"node": "PTX1-POP1", "port": "ae0"},
}


def test_step_filters_unmatched_subject_into_excluded_services():
    """Sluzby cizich vln na LAGu nejdou pres checky, skonci v excluded."""
    baseline = _snapshot(
        "172.20.20.4",
        "ge-0/0/4.0",
        [_scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ge-0/0/4.0")],
    )
    subject = _snapshot(
        "172.20.20.5",
        "ae0.15",
        [
            _scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ae0.15"),
            _scope("svc:VLNA2:Internet", "VLNA2", "Internet", "ae0.16"),
        ],
        phase="post-migration",
    )

    result = evaluate_snapshots(subject, baseline, now=NOW, step=STEP)

    shown_ids = {scope.scope_id for scope in result.scopes}
    assert "svc:VLNA1:Internet" in shown_ids
    assert "svc:VLNA2:Internet" not in shown_ids
    assert result.step == STEP
    assert result.excluded_services == [
        {
            "scope_id": "svc:VLNA2:Internet",
            "description": "VLNA2",
            "service_type": "Internet",
            "reason": "nova sluzba, chybi baseline",
        }
    ]


def test_step_keeps_unmatched_baseline_visible():
    """Chybejici sluzba stareho portu je hlavni nalez - filtr ji nesmi vzit."""
    baseline = _snapshot(
        "172.20.20.4",
        "ge-0/0/4.0",
        [
            _scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ge-0/0/4.0"),
            _scope("svc:ZTRACENA:Internet", "ZTRACENA", "Internet", "ge-0/0/4.1"),
        ],
    )
    subject = _snapshot(
        "172.20.20.5",
        "ae0.15",
        [_scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ae0.15")],
        phase="post-migration",
    )

    result = evaluate_snapshots(subject, baseline, now=NOW, step=STEP)

    assert [e["description"] for e in result.unmatched["baseline"]] == ["ZTRACENA"]


def test_step_none_changes_nothing():
    """Bez step je vysledek bit-po-bitu dnesni (klice v JSON chybi)."""
    baseline = _snapshot(
        "172.20.20.4", "ge-0/0/4.0",
        [_scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ge-0/0/4.0")],
    )
    subject = _snapshot(
        "172.20.20.5", "ae0.15",
        [
            _scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ae0.15"),
            _scope("svc:VLNA2:Internet", "VLNA2", "Internet", "ae0.16"),
        ],
        phase="post-migration",
    )

    result = evaluate_snapshots(subject, baseline, now=NOW)

    shown_ids = {scope.scope_id for scope in result.scopes}
    assert "svc:VLNA2:Internet" in shown_ids  # dnesni NESPAROVANO
    payload = result.to_dict()
    assert "step" not in payload
    assert "excluded_services" not in payload


def test_step_pulls_linked_partner_of_matched_scope():
    """Napul sparovany L2/L3 par: nesparovany partner se pritahne."""
    # Subject = _linked_snapshot() (irb.15 IPVPN + ae0.15 E-LAN v jedne
    # instanci, helper v tomto souboru). Baseline nese JEN L3 polovinu
    # (description "L3VPN-CPE14-UNI") -> L2 "EVPN-VLAN-AWARE-CPE14" se
    # nesparuje, ale link na sparovany irb.15 ho musi udrzet v reportu.
    subject = _linked_snapshot()
    baseline = _snapshot(
        "172.20.20.4",
        "ge-0/0/4.0",
        [_scope("svc:L3VPN-CPE14-UNI:IPVPN", "L3VPN-CPE14-UNI", "IPVPN", "ge-0/0/4.0")],
    )

    result = evaluate_snapshots(subject, baseline, now=NOW, step=STEP)

    shown_ids = {scope.scope_id for scope in result.scopes}
    assert "svc:EVPN-VLAN-AWARE-CPE14:E-LAN" in shown_ids
    assert "svc:OTHER:Internet" not in shown_ids
    excluded_ids = {e["scope_id"] for e in result.excluded_services}
    assert excluded_ids == {"svc:OTHER:Internet"}
```

Pozn.: `_scope` v test_engine.py dává `service_subtype=None` a description
je unikátní → párování jde pravidlem `description+service_type`. U
`_linked_snapshot` má L3 scope stejné id jako baseline scope — párování jde
přes description, id se nepoužívá.

- [ ] **Step 2: Ověř, že testy padají**

Run: `.venv/bin/python -m pytest tests/test_engine.py -v -k step`
Expected: FAIL (`evaluate_snapshots` nezná `step`).

- [ ] **Step 3: Implementuj**

`models/result.py` — do `RunResult` za `profile`:

```python
    # Migracni krok (old/new endpoint), pod kterym vysledek vznikl -
    # aditivni klic, stejne pravidlo jako `profile`.
    step: dict[str, Any] | None = None
    # Sluzby subjektu potlacene filtrem pres baseline (cizi vlny na LAGu).
    # Plni se jen se `step` - i prazdny seznam rika "filtr bezel".
    excluded_services: list[dict[str, Any]] | None = None
```

a v `to_dict()` za blok `profile`:

```python
        if self.step is not None:
            payload["step"] = self.step
        if self.excluded_services is not None:
            payload["excluded_services"] = self.excluded_services
```

`engine.py` — signatura:

```python
def evaluate_snapshots(
    subject: Snapshot,
    baseline: Snapshot | None = None,
    mapping: Mapping | None = None,
    config: CheckConfig | None = None,
    now: str | None = None,
    service_types: list[str] | None = None,
    profile_name: str | None = None,
    step: dict[str, Any] | None = None,
) -> RunResult:
```

Ve větvi `baseline is not None` (dnes od řádku 462): před smyčkou přes
`matches.unmatched_subject` spočti spárované subject id a připrav seznam:

```python
        excluded: list[dict[str, Any]] = []
        matched_subject_ids = {pair.subject.id for pair in matches.pairs}
```

a smyčku uprav — potlačení má přednost před dnešní profilovou větví,
device scopy a přitahovaní partneři filtru nepodléhají:

```python
        for item in matches.unmatched_subject:
            link = link_payloads.get(item.scope.id)
            partner_matched = bool(
                link and link.get("peer_scope_id") in matched_subject_ids
            )
            if (
                step is not None
                and item.scope.kind == "service"
                and not partner_matched
            ):
                # Cizi vlna na sdilenem portu: mimo tento migracni krok.
                # Nejde pres checky ani do NESPAROVANO - jen do JSON.
                excluded.append(_unmatched_entry(item.scope, item.reason))
                continue
            ...  # stavajici telo smycky beze zmeny (vcetne unmatched["subject"].append)
```

Ve větvi `baseline is None` se `step` ignoruje (evaluace kroku vždy baseline
má; kroky bez baseline plánovač vyřazuje s reason). Na konci:

```python
    return RunResult(
        ...,
        filtered=filtered,
        profile=profile_name,
        step=step,
        excluded_services=excluded if step is not None else None,
    )
```

(`excluded` inicializuj na `[]` i mimo baseline větev, ať jméno existuje.)

- [ ] **Step 4: Ověř, že testy prochází**

Run: `.venv/bin/python -m pytest tests/test_engine.py tests/models tests/reporting -q`
Expected: PASS.

- [ ] **Step 5: Mutant na filtr**

Dočasně zakomentuj `continue` v nové větvi (potlačení vypnuto) a spusť
`.venv/bin/python -m pytest tests/test_engine.py -k step`. Očekávání:
`test_step_filters_unmatched_subject_into_excluded_services` FAIL na
`shown_ids`. Vrať kód zpět, spusť znovu, PASS. (Mutant běž nad už
zkompletovaným krokem 3 — žádné jiné rozpracované změny v pracovní kopii.)

- [ ] **Step 6: Commit**

```bash
git add migration_validator/engine.py migration_validator/models/result.py tests/test_engine.py
git commit -m "feat: engine filtr pres baseline - step a excluded_services"
```

---

### Task 6: Renderer — krok v hlavičce a souhrnný řádek

**Files:**
- Modify: `migration_validator/reporting/text_report.py:436-470` (`render`)
- Test: `tests/reporting/` (soubor s render testy — grep `def render\|profil`
  v tests/reporting, přidej vedle testů hlavičky profilu)

**Interfaces:**
- Consumes: `RunResult.step` a `RunResult.excluded_services` z Tasku 5
  (tvary tamtéž). `filter_result` používá `dataclasses.replace`, nová pole
  projdou beze změny — jen to pokryj testem.
- Produces: textový výstup; JSON cesta (`to_json`) žádnou změnu nepotřebuje
  (to_dict už klíče nese).

- [ ] **Step 1: Napiš failing testy**

Do `tests/reporting/test_text_report.py`. Minimální `RunResult` postav
podle helperu na řádku ~527 (subject/baseline dicty s address/phase/
captured_at, summary s pěti countery + scopes_matched/unmatched_*):

```python
STEP = {
    "old": {"node": "MX1", "port": "ge-0/0/4"},
    "new": {"node": "PTX1", "port": "ae0"},
}
EXCLUDED = [
    {
        "scope_id": "svc:X:Internet",
        "description": "X",
        "service_type": "Internet",
        "reason": "nova sluzba, chybi baseline",
    }
]


def _step_result(*, step=None, excluded=None):
    return RunResult(
        evaluated_at="2026-08-17T11:40:02Z",
        subject={"address": "172.20.20.5", "phase": "post-migration", "captured_at": "x"},
        baseline={"address": "172.20.20.4", "phase": "pre-migration", "captured_at": "y"},
        summary={"pass": 0, "warn": 0, "fail": 0, "skip": 0, "info": 0,
                 "scopes_matched": 0, "unmatched_baseline": 0, "unmatched_subject": 0},
        scopes=[],
        step=step,
        excluded_services=excluded,
    )


def test_render_header_carries_step():
    """Hlavicka nese [krok old -> new] a souhrny radek potlacenych sluzeb."""
    out = render(_step_result(step=STEP, excluded=EXCLUDED))
    assert "[krok MX1:ge-0/0/4 -> PTX1:ae0]" in out
    assert (
        "Dalsi sluzby na ae0 mimo tento krok: 1 "
        "(nesparovano s baseline ge-0/0/4)" in out
    )


def test_render_no_step_no_step_lines():
    out = render(_step_result())
    assert "[krok" not in out
    assert "mimo tento krok" not in out


def test_render_step_without_excluded_has_no_summary_line():
    out = render(_step_result(step=STEP, excluded=[]))
    assert "[krok" in out
    assert "mimo tento krok" not in out


def test_filter_result_preserves_step_and_excluded():
    result = _step_result(step=STEP, excluded=EXCLUDED)
    shown = filter_result(result, text=None, statuses=None)
    assert shown.step == STEP
    assert shown.excluded_services == EXCLUDED
```

- [ ] **Step 2: Ověř, že testy padají**

Run: `.venv/bin/python -m pytest tests/reporting -v -k step`
Expected: render testy FAIL; `test_filter_result_preserves...` může projít
rovnou (replace pole zachová) — to je v pořádku, je to regresní zámek.

- [ ] **Step 3: Implementuj v `render()`**

Za blok `if result.profile:` (dnes řádky 451–452):

```python
    if result.step:
        step_old = result.step["old"]
        step_new = result.step["new"]
        header += (
            f" [krok {step_old['node']}:{step_old['port']}"
            f" -> {step_new['node']}:{step_new['port']}]"
        )
```

Za řádek `Sparovano ...` (dnes řádky 465–469):

```python
    if result.step and result.excluded_services:
        lines.append(
            f"  Dalsi sluzby na {result.step['new']['port']} mimo tento krok: "
            f"{len(result.excluded_services)} "
            f"(nesparovano s baseline {result.step['old']['port']})"
        )
```

„X z Y sluzeb" počítání se nemění — potlačené scopy v `result.scopes`
nejsou, čísla sedí sama od sebe.

- [ ] **Step 4: Ověř, že testy prochází**

Run: `.venv/bin/python -m pytest tests/reporting -q`
Expected: PASS (včetně strip-equality testu šířek — nové řádky jsou čistý
text bez barev, invariant drží).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/reporting/text_report.py tests/reporting
git commit -m "feat: report nese migracni krok a pocet sluzeb mimo krok"
```

---

### Task 7: Propojení — `evaluate --run` předává krok, api.evaluate, status N:1

**Files:**
- Modify: `migration_validator/api.py:58-78` (`evaluate` — param `step`)
- Modify: `migration_validator/cli.py:160-190` (`_evaluate_run`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Evaluation.step: InterfaceMapping | None` (Task 1),
  `evaluate_snapshots(..., step=...)` (Task 5).
- Produces: `api.evaluate(..., step: dict[str, Any] | None = None)`.
  Konec pipeline — nic dalšího na tom nestaví.

- [ ] **Step 1: Napiš failing test**

Do `tests/test_cli.py`. Vzor je `test_evaluate_run_pairs_and_exit_code`
(řádek ~672): staví run adresář ručně — `save_manifest` s captures +
`save_snapshot` soubory na disku, pak `main(["evaluate", "--run", ...])`
s `capsys`. Nový test:

```python
def test_evaluate_run_emits_one_report_per_step_with_header(tmp_path, capsys):
    """run.yml se 2 mappingy na ae0 + pre4/pre5/post snimky na disku:
    evaluate --run vypise 2 reporty, kazdy s [krok ...] sve dvojice."""
    # Manifest: devices MX1-POP1 (old) + PTX1-POP1 (new); interface_mapping
    # ge-0/0/4->ae0 a ge-0/0/5->ae0; captures pre4, pre5, post (ae0);
    # snapshoty vyrob helperem _snapshot() a uloz pod jmeny z manifestu.
    code = main(["evaluate", "--run", "mig01", "--run-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "[krok MX1-POP1:ge-0/0/4 -> PTX1-POP1:ae0]" in out
    assert "[krok MX1-POP1:ge-0/0/5 -> PTX1-POP1:ae0]" in out
    assert out.count("=== ") == 2
```

Druhý test stejnou přípravou s `--format json`: každý vypsaný objekt nese
`"step"` a `"excluded_services"`. Třetí test: `status --run` se 2 mappingy
na tentýž nový port tiskne 2 řádky (regresní zámek N:1 — `_status_rows`
už iteruje mappingy, test jen zamyká, že to tak zůstane).

- [ ] **Step 2: Ověř, že test padá**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v -k "step or status"`
Expected: evaluate test FAIL (krok se nikam nepředává); status test může
projít rovnou (dnešní kód N:1 řádkuje) — regresní zámek.

- [ ] **Step 3: Implementuj**

`api.py` — `evaluate(..., step: dict[str, Any] | None = None)` a forward
`step=step` do `evaluate_snapshots`.

`cli.py` `_evaluate_run` — ve smyčce před voláním `api.evaluate`:

```python
        step_payload = None
        step_label = ""
        if evaluation.step is not None:
            step_payload = {
                "old": {
                    "node": evaluation.step.old.node,
                    "port": evaluation.step.old.port,
                },
                "new": {
                    "node": evaluation.step.new.node,
                    "port": evaluation.step.new.port,
                },
            }
            step_label = (
                f" [krok {evaluation.step.old.node}:{evaluation.step.old.port}"
                f" -> {evaluation.step.new.node}:{evaluation.step.new.port}]"
            )

        print(f"=== {evaluation.subject.snapshot} vs {baseline_label}{step_label} ===")

        result = api.evaluate(
            subject,
            baseline=baseline,
            mapping=mapping,
            config=profile.checks,
            service_types=service_types,
            profile_name=profile.name or None,
            step=step_payload,
        )
```

(původní `print(f"=== ...")` řádek nahraď — oddělovací hlavička teď nese
krok, aby se dva reporty téhož post snímku daly rozlišit i v `--format json`
výstupu, kde `render` hlavička není.)

- [ ] **Step 4: Ověř, že testy prochází**

Run: `.venv/bin/python -m pytest tests -q`
Expected: celá sada PASS.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/api.py migration_validator/cli.py tests/test_cli.py
git commit -m "feat: evaluate --run vyhodnocuje po migracnich krocich"
```

---

### Task 8: Dokumentace

**Files:**
- Modify: `docs/cs/README.md` (kapitola „3a. Run management"),
  `docs/cs/reference.md` (sekce 8), `docs/en/README.md`, `docs/en/reference.md`
- Modify: `docs/cs/files/top-level.md`, `docs/en/files/top-level.md`
  (cli změny), `docs/cs/files/reporting.md`, `docs/en/files/reporting.md`
  (step v hlavičce/JSON)

**Interfaces:**
- Consumes: hotové chování Tasků 1–7.
- Produces: dokumentace; nic na ní nestaví.

- [ ] **Step 1: Aktualizuj cs dokumentaci**

Do run-management kapitol doplň (formulace zarovnej s okolním textem):

- N:1 mapování: víc `--maps-to` old portů na jeden nový LAG port; evaluate
  vyrobí report per krok; `--port` u `evaluate --run` filtruje starým portem.
- Filtr přes baseline: co znamená řádek „Dalsi sluzby na ... mimo tento
  krok" a JSON klíče `step`/`excluded_services` (aditivní, jen s krokem).
- `--parse-services` vždy regeneruje (a deltu tiskne); `--overwrite` pro
  pre snímky.
- Ping cíle při post na LAGu = sjednocení ARP/ND všech mapovaných pre
  snímků.

- [ ] **Step 2: Aktualizuj en zrcadlo**

Stejné body v `docs/en/**` — drž strukturu cs verze.

- [ ] **Step 3: Ověř konzistenci s kódem**

Projdi hotové CLI hlášky (`grep -n "mimo tento krok\|pregenerovana\|--overwrite" migration_validator/`)
a zkontroluj, že docs citují přesné znění. Spusť celou sadu:
`.venv/bin/python -m pytest tests -q` → PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/cs docs/en
git commit -m "docs: run management po migracnich krocich (N:1 LAG)"
```

---

## Poznámky pro implementátory

- Lab smoke (volitelný, po dokončení): heslo viz memory
  [[lab-password-not-in-shell-env]]; pre na vMX `172.20.20.4` UNI portu,
  post na PTX EVO `172.20.20.5` LAGu, dvě vlny po sobě. Není součástí
  žádného tasku — testovací jistota stojí na pytest sadě.
- Deferred: hláška „neznama faze 'None'" (fáze 4 minor) se tímto plánem
  neřeší; „_status_rows řadí l2-switch do OLD sloupce" zůstává na fázi 5.
