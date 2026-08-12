# L1 bloky, EVPN service-relevance a opticke testy - implementacni plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report seskupeny po Layer1 portech (L1 blok s optikou -> jeho sluzby), EVPN VLAN-AWARE bloky tisknou jen radky relevantni pro danou sluzbu, nove checky optickych urovni a alarmu.

**Architecture:** Layer1 zaznamy inventory se povysuji na plnohodnotne scopy (`kind="layer1"`); interface checky deli radky fyzicky port vs. unit podle druhu scopu; EVPN relevance je check-side filtr nad selektory scope; optika je novy collector + dva checky bezici jen na L1 scopech. Razeni bloku dela engine, renderer jen drzi L1 rodice u zobrazenych deti.

**Tech Stack:** Python 3.11+, lxml, PyEZ (jen collector), pytest. Zadne nove zavislosti.

**Spec:** `docs/superpowers/specs/2026-08-12-l1-bloky-a-service-relevance-design.md`

## Global Constraints

- Vsechny komentare, docstringy a hlasky reportu cesky bez diakritiky (konvence repa).
- Checky nikdy nesahaji na sit; collector nikdy neinterpretuje.
- Snapshot `SCHEMA_VERSION` 7 -> 8 (Task 11). Inventory `INVENTORY_SCHEMA_VERSION` zustava 5 - `lag_members` je aditivni pole s defaultem.
- Opticke RPC jmeno se NEHADA - overuje se na labu (`show interfaces diagnostics optics | display xml rpc`), viz Task 11 krok 1. Domaci pravidlo po minulem incidentu (collectors/evpn.py:6-14).
- Konstanta tolerance urovni: `tolerance_db: 2.0` v `config.DEFAULTS`, zadny dalsi knob.
- Alarm/warn flagy se porovnavaji case-insensitive (`off` vs `Off`).
- Schvalena rozhodnuti ze specu se nerelitiguji (sekce "Schvalena rozhodnuti").
- Testy: `python -m pytest <cesta> -v`. Pred kazdym commitem probehne cely balik `python -m pytest`.
- Commit message konvence: `feat:`/`fix:`/`test:` cesky, jako dosavadni historie.

## Poradi a zavislosti

Tasky 1-5 (EVPN relevance) jsou nezavisle na Taskach 6-10 (L1 scopy a razeni).
Tasky 11-13 (optika) zavisi na Taskach 6-7 (layer1 scope + gating).
Task 14 je zaverecna verifikace.

---

### Task 1: EVPN neighbor radky primo pod "EVPN neighbors"

**Files:**
- Modify: `migration_validator/checks/evpn.py` (metoda `_instance_findings`, radky ~498-531)
- Test: `tests/checks/test_evpn.py`

**Interfaces:**
- Consumes: `EvpnInstanceStatusCheck._instance_findings` (evpn.py:409)
- Produces: poradi emise findingu: `EVPN neighbors` (count) -> `EVPN neighbor` (adresy) -> ESI -> interface/IRB radky. Renderer poradi checku zachovava (view.py:220), zadna zmena jinde.

- [ ] **Step 1: Napis padajici test**

Do `tests/checks/test_evpn.py` (vyuzij existujici `_ctx`; instance data ve tvaru z collectoru):

```python
def _instance_subject(**overrides):
    data = {
        "local_interfaces": {"total": 1, "up": 1, "entries": [
            {"name": "ge-0/0/2.313", "status": "Up"},
        ]},
        "irb_interfaces": {"total": 0, "up": 0, "entries": []},
        "neighbors": {"total": 2, "addresses": ["150.0.0.2", "150.0.0.3"]},
        "esis": {},
    }
    data.update(overrides)
    return {"evpn_instance": {"EVPN-AWARE-CPE13": data}}


def test_neighbor_adresy_stoji_hned_pod_countem():
    findings = EvpnInstanceStatusCheck().run(_ctx(_instance_subject()))
    labels = [f.label for f in findings]
    count_idx = labels.index("EVPN neighbors")
    assert labels[count_idx + 1] == "EVPN neighbor"
    assert labels[count_idx + 2] == "EVPN neighbor"
```

- [ ] **Step 2: Over, ze test pada**

Run: `python -m pytest tests/checks/test_evpn.py::test_neighbor_adresy_stoji_hned_pod_countem -v`
Expected: FAIL - mezi countem a adresami dnes stoji ESI/interface/IRB radky.

- [ ] **Step 3: Presun emisni smycku**

V `_instance_findings` vyjmi smycku `for address in neighbors.get("addresses", []):` (evpn.py:522-530) a vloz ji hned za append `EVPN neighbors` count findingu (za radek ~496), pred `findings.extend(self._esi_findings(...))`.

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/checks/test_evpn.py -v`
Expected: PASS (vcetne stavajicich testu - zadny netestuje absolutni poradi neighbor adres na konci).

- [ ] **Step 5: Commit**

```bash
git add tests/checks/test_evpn.py migration_validator/checks/evpn.py
git commit -m "fix: EVPN neighbor adresy se tisknou hned pod radkem EVPN neighbors"
```

---

### Task 2: Zruseni RI-wide agregatu a service filtr EVPN/IRB interface radku

**Files:**
- Modify: `migration_validator/checks/evpn.py`
- Test: `tests/checks/test_evpn.py`

**Interfaces:**
- Consumes: `ctx.scope.selectors.interfaces`, `ctx.scope.selectors.vlans`, `ctx.link` (CheckContext, base.py:38-47)
- Produces: modulova funkce `_service_units(ctx) -> _ServiceUnits` s poli `interfaces: set[str]`, `irb: str | None`, `vlans: set[str]`, `active: bool`. Pouziva ji Task 3 (ESI filtr) i Task 4 (MAC filtr). Radky `EVPN local interfaces`, `EVPN local interfaces up`, `EVPN IRB interfaces`, `EVPN IRB interfaces up` PRESTAVAJI existovat. Radky `EVPN interface` / `IRB interface` jsou nove OK/BROKEN (ne INFO) a jen pro vlastni unity.

- [ ] **Step 1: Napis padajici testy**

```python
def _vlan_aware_ctx(subject, link=None):
    """Scope sluzby ae0.14 v RI se tremi sluzbami - vlan-aware vzor z labu."""
    scope = Scope(
        id="svc:SVC:E-LAN",
        kind="service",
        key=ScopeKey("SVC", "E-LAN", "vlan-aware"),
        selectors=Selectors(
            interfaces=["ae0.14"],
            routing_instances=["EVPN-VLAN-AWARE-POP1"],
            vlans=["14"],
        ),
    )
    return CheckContext(
        scope=scope, subject=subject, baseline=None,
        config=default_config(), failed_collectors={}, link=link,
    )


def _aware_subject():
    return {"evpn_instance": {"EVPN-VLAN-AWARE-POP1": {
        "local_interfaces": {"total": 4, "up": 4, "entries": [
            {"name": ".local..64", "status": "Up"},
            {"name": "ae0.14", "status": "Up"},
            {"name": "ae0.15", "status": "Up"},
            {"name": "ae0.4094", "status": "Down"},
        ]},
        "irb_interfaces": {"total": 3, "up": 3, "entries": [
            {"name": "irb.14", "status": "Up", "l3_context": "master"},
            {"name": "irb.15", "status": "Up", "l3_context": "L3VPN-CPE14"},
            {"name": "irb.4094", "status": "Up", "l3_context": "MGMT"},
        ]},
        "neighbors": {"total": 1, "addresses": ["150.0.0.2"]},
        "esis": {
            "00:11:12:13:14:00:14:00:00:00": "Resolved by IFL ae0.14",
            "00:11:12:13:14:00:15:00:00:00": "Resolved by IFL ae0.15",
        },
    }}}


LINK_L2 = {"role": "l2", "peer_scope_id": "svc:X:Internet",
           "peer_interface": "irb.14", "peer_instance": "inet.0"}


def test_agregatni_radky_se_netisknou():
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(_aware_subject()))
    labels = {f.label for f in findings}
    assert "EVPN local interfaces" not in labels
    assert "EVPN local interfaces up" not in labels
    assert "EVPN IRB interfaces" not in labels
    assert "EVPN IRB interfaces up" not in labels
    assert "EVPN neighbors" in labels  # count zustava


def test_evpn_interface_jen_vlastni_unit_a_je_posuzovany():
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(_aware_subject()))
    rows = [f for f in findings if f.label == "EVPN interface"]
    assert [r.value for r in rows] == ["ae0.14 Up"]
    assert rows[0].outcome is Outcome.OK


def test_evpn_interface_down_je_broken():
    subject = _aware_subject()
    subject["evpn_instance"]["EVPN-VLAN-AWARE-POP1"]["local_interfaces"]["entries"][1][
        "status"] = "Down"
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(subject))
    rows = [f for f in findings if f.label == "EVPN interface"]
    assert rows[0].outcome is Outcome.BROKEN


def test_irb_radek_jen_linkovany_unit():
    findings = EvpnInstanceStatusCheck().run(
        _vlan_aware_ctx(_aware_subject(), link=LINK_L2))
    rows = [f for f in findings if f.label == "IRB interface"]
    assert [r.value for r in rows] == ["irb.14 Up (master)"]
    assert rows[0].outcome is Outcome.OK


def test_bez_linku_zadny_irb_radek():
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(_aware_subject()))
    assert not [f for f in findings if f.label == "IRB interface"]


def test_fallback_bez_selektoru_tiskne_vse():
    ctx = _vlan_aware_ctx(_aware_subject())
    ctx.scope.selectors.interfaces = []
    findings = EvpnInstanceStatusCheck().run(ctx)
    rows = [f for f in findings if f.label == "EVPN interface"]
    assert len(rows) == 4  # filtr vypnuty, vsechno jako drive
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/checks/test_evpn.py -v -k "agregatni or evpn_interface or irb_radek or bez_linku or fallback"`
Expected: FAIL.

- [ ] **Step 3: Implementace**

Do `migration_validator/checks/evpn.py` pridej (nad `EvpnInstanceStatusCheck`):

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class _ServiceUnits:
    """Identita sluzby pro relevance filtr vlan-aware bloku.

    active=False vypina filtrovani - scope bez interface selektoru nema
    podle ceho vybirat a radsi vypise vsechno nez nic.
    """

    interfaces: frozenset[str]
    irb: str | None
    vlans: frozenset[str]

    @property
    def active(self) -> bool:
        return bool(self.interfaces)


def _service_units(ctx: CheckContext) -> _ServiceUnits:
    interfaces = frozenset(ctx.scope.selectors.interfaces)
    irb = None
    if ctx.link and ctx.link.get("role") == "l2":
        irb = ctx.link.get("peer_interface")
    vlans = frozenset(ctx.scope.selectors.vlans)
    if not vlans:
        # sluzba bez customer_vlan - unit cislo je stejna informace
        vlans = frozenset(
            name.split(".", 1)[1] for name in interfaces if "." in name
        )
    return _ServiceUnits(interfaces=interfaces, irb=irb, vlans=vlans)
```

V `run()` spocitej `units = _service_units(ctx)` a predej do `_instance_findings(name, data, baseline, many, units)`. V `_instance_findings`:

1. Smaz ctyri agregatni findingy: `EVPN local interfaces`, `EVPN local interfaces up`, `EVPN IRB interfaces`, `EVPN IRB interfaces up` (evpn.py:422-478) - bez podminky, i pro fallback.
2. `EVPN interface` smycka - filtr + verdikt:

```python
        for entry in local.get("entries", []):
            if units.active and entry["name"] not in units.interfaces:
                continue
            up = _is_up(str(entry["status"]))
            findings.append(
                Finding(
                    Outcome.OK if up else Outcome.BROKEN,
                    f"{instance}: interface {entry['name']} {entry['status']}"
                    + ("" if up else f", ocekavano {UP}"),
                    label=label("EVPN interface"),
                    value=f"{entry['name']} {entry['status']}",
                )
            )
```

3. `IRB interface` smycka - jen linkovany unit, stejne povyseni na OK/BROKEN. Pri aktivnim filtru a `units.irb is None` se IRB radky netisknou (cizi IRB nejsou nase sluzba):

```python
        for entry in irb.get("entries", []):
            if units.active and entry["name"] != units.irb:
                continue
            ...  # stejny Outcome.OK/BROKEN vzor jako u EVPN interface,
                 # value vcetne l3_context zavorky jako dnes
```

Pozn.: `.local..64` zadny filtr zvlast neresi - vypadne pres `not in units.interfaces`.

- [ ] **Step 4: Oprav dotcene stavajici testy**

Run: `python -m pytest tests/checks/test_evpn.py tests/test_end_to_end.py -v`
Testy assertujici agregatni radky nebo INFO outcome interface radku uprav podle noveho chovani (radek neexistuje / outcome OK). E2E invarianty musi projit beze zmen.

- [ ] **Step 5: Commit**

```bash
git add tests/checks/test_evpn.py migration_validator/checks/evpn.py
git commit -m "feat: vlan-aware blok tiskne jen vlastni EVPN/IRB interfacy, agregaty zruseny"
```

---

### Task 3: ESI listing filtr podle IFL sluzby

**Files:**
- Modify: `migration_validator/checks/evpn.py` (`_esi_findings`, radky ~533-584)
- Test: `tests/checks/test_evpn.py`

**Interfaces:**
- Consumes: `_ServiceUnits` z Tasku 2; `_esi_findings(instance, data, baseline, label)` dostava novy parametr `units`.
- Produces: `ESI {esi}` radky jen pro ESI, jejichz status jmenuje IFL sluzby. Pri aktivnim filtru a zadnem relevantnim ESI vraci `_esi_findings` prazdny seznam (zdravi vlastniho ESI nese DF radek checku `evpn_esi_status`, jehoz data uz jsou interface-filtrovana - scope.py:176-179).

- [ ] **Step 1: Napis padajici testy**

```python
def test_esi_filtr_drzi_jen_vlastni_ifl():
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(_aware_subject()))
    esi_rows = [f for f in findings if f.label.startswith("ESI ")]
    assert [f.label for f in esi_rows] == ["ESI 00:11:12:13:14:00:14:00:00:00"]
    assert esi_rows[0].value == "Resolved by IFL ae0.14"


def test_esi_unresolved_bez_ifl_se_pri_filtru_netiskne():
    subject = _aware_subject()
    subject["evpn_instance"]["EVPN-VLAN-AWARE-POP1"]["esis"] = {
        "00:11:12:13:14:00:15:00:00:00": "Unresolved",
    }
    findings = EvpnInstanceStatusCheck().run(_vlan_aware_ctx(subject))
    assert not [f for f in findings if f.label.startswith("ESI ")]


def test_esi_fallback_bez_selektoru_tiskne_vse():
    ctx = _vlan_aware_ctx(_aware_subject())
    ctx.scope.selectors.interfaces = []
    findings = EvpnInstanceStatusCheck().run(ctx)
    esi_rows = [f for f in findings if f.label.startswith("ESI ")]
    assert len(esi_rows) == 2
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/checks/test_evpn.py -v -k esi_filtr` (a dalsi dva)
Expected: FAIL.

- [ ] **Step 3: Implementace**

Nad `EvpnInstanceStatusCheck` pridej:

```python
import re

_IFL_RE = re.compile(r"by IFL (\S+)")


def _esi_is_relevant(status: str | None, units: _ServiceUnits) -> bool:
    """ESI patri sluzbe, kdyz jeho status jmenuje jeji IFL.

    Unresolved status IFL nenese, takze pri aktivnim filtru vypadne -
    vlastni ESI sluzby posuzuje DF radek evpn_esi_status (interface-
    filtrovany), tenhle listing je jen instancni kontext.
    """
    if not status:
        return False
    match = _IFL_RE.search(status)
    return bool(match) and match.group(1) in units.interfaces
```

V `_esi_findings` (predanym `units`): pri `units.active` filtruj iteraci
`sorted(set(esis) | set(baseline_esis))` na
`_esi_is_relevant(esis.get(esi), units)`. Vetev `status is None`
("v baseline bylo, ted chybi") pri aktivnim filtru vypada s ostatnimi -
baseline IFL nese stare jmeno rozhrani, proti novym selektorum nikdy
nesedi (zamer, viz spec kap. 2). SKIP radek "zadne ESI ve vypisu" zustava
jen pro pripad `not esis and not baseline_esis`; kdyz filtr vyradi vse,
vrat `[]`.

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/checks/test_evpn.py tests/test_end_to_end.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/checks/test_evpn.py migration_validator/checks/evpn.py
git commit -m "feat: ESI listing v bloku sluzby jen pro vlastni IFL"
```

---

### Task 4: MAC count filtr podle VLAN a unitu sluzby

**Files:**
- Modify: `migration_validator/checks/evpn.py` (`EvpnMacCountCheck.run`, radky ~597-673)
- Test: `tests/checks/test_evpn.py`

**Interfaces:**
- Consumes: `_service_units(ctx)` z Tasku 2.
- Produces: `VL-x MAC count` radky jen pro `units.vlans`; `... Interface y MAC count` radky jen pro klice v `units.interfaces` (klic je normalizovany tvar `ae0.14`, collectors/evpn.py:365).

- [ ] **Step 1: Napis padajici testy**

```python
def _mac_ctx(subject):
    return _vlan_aware_ctx(subject)  # stejny scope ae0.14 / vlan 14


def _mac_subject():
    return {"evpn_mac": {"EVPN-VLAN-AWARE-POP1": {
        "vlans": {
            "14": {"domain": "VL-14", "count": 8},
            "15": {"domain": "VL-15", "count": 8},
        },
        "interfaces": {
            "ae0.14": {"name": "ae0.14:14", "domain": "VL-14", "count": 6},
            "ae0.15": {"name": "ae0.15:15", "domain": "VL-15", "count": 6},
        },
    }}}


def test_mac_count_jen_vlastni_vlan_a_unit():
    findings = EvpnMacCountCheck().run(_mac_ctx(_mac_subject()))
    labels = [f.label for f in findings]
    assert "VL-14 MAC count" in labels
    assert "VL-15 MAC count" not in labels
    assert "VL-14 Interface ae0.14:14 MAC count" in labels
    assert "VL-15 Interface ae0.15:15 MAC count" not in labels


def test_mac_count_fallback_bez_selektoru():
    ctx = _mac_ctx(_mac_subject())
    ctx.scope.selectors.interfaces = []
    ctx.scope.selectors.vlans = []
    findings = EvpnMacCountCheck().run(ctx)
    assert len([f for f in findings if f.label.endswith("MAC count")]) == 4
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/checks/test_evpn.py -v -k mac_count_jen`
Expected: FAIL.

- [ ] **Step 3: Implementace**

V `EvpnMacCountCheck.run` spocitej `units = _service_units(ctx)` a:

- ve VLAN smycce (evpn.py:622) preskoc `if units.active and units.vlans and vlan not in units.vlans: continue`,
- v per-interface smycce (evpn.py:654) preskoc `if units.active and key not in units.interfaces: continue`.

Union s baseline zustava - mrtva domena VLASTNI vlan se dal hlasi (vlan id migraci prezije).

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/checks/test_evpn.py tests/test_end_to_end.py -v`
Expected: PASS. Stavajici mac testy pouzivaji scope s `interfaces=["ge-0/0/2.313"]` - kdyz assertuji radky jinych vlan/rozhrani, uprav fixture selektory tak, aby zamer testu zustal.

- [ ] **Step 5: Commit**

```bash
git add tests/checks/test_evpn.py migration_validator/checks/evpn.py
git commit -m "feat: MAC count radky jen pro VLAN a unit sluzby"
```

---

### Task 5: Selectors.lag_members + ServiceEntry.lag_members (modely)

**Files:**
- Modify: `migration_validator/models/scope.py` (Selectors, radky 52-95)
- Modify: `migration_validator/models/inventory.py` (ServiceEntry)
- Test: `tests/models/` (pridat asserty do existujicich testu modelu; kdyz test soubor pro scope/inventory neexistuje, vytvor `tests/models/test_lag_members.py`)

**Interfaces:**
- Produces: `Selectors.lag_members: list[str]` (default `[]`, serializace v `to_dict`/`from_dict`); `ServiceEntry.lag_members: list[str]` (default `[]`, cte se v `from_dict` pres `_as_list`, vydava v `to_dict`). Inventory schema_version zustava 5 - stare YAML bez pole se nacita dal.

- [ ] **Step 1: Napis padajici test**

```python
# tests/models/test_lag_members.py
from migration_validator.models.inventory import ServiceEntry
from migration_validator.models.scope import Selectors


def test_selectors_lag_members_roundtrip():
    selectors = Selectors(interfaces=["ae0"], lag_members=["et-0/0/5", "et-0/0/6"])
    assert Selectors.from_dict(selectors.to_dict()).lag_members == [
        "et-0/0/5", "et-0/0/6"]


def test_selectors_stary_dict_bez_pole_se_nacte():
    assert Selectors.from_dict({"interfaces": ["ae0"]}).lag_members == []


def test_service_entry_lag_members_roundtrip():
    entry = ServiceEntry.from_dict({
        "interface": "ae0", "service_type": "Layer1",
        "lag_members": ["et-0/0/5"],
    })
    assert entry.lag_members == ["et-0/0/5"]
    assert entry.to_dict()["lag_members"] == ["et-0/0/5"]


def test_service_entry_bez_pole_ma_prazdny_seznam():
    entry = ServiceEntry.from_dict({"interface": "ae0", "service_type": "Layer1"})
    assert entry.lag_members == []
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/models/test_lag_members.py -v`
Expected: FAIL (TypeError / KeyError).

- [ ] **Step 3: Implementace**

`Selectors`: pridej `lag_members: list[str] = field(default_factory=list)` a do `to_dict` polozku `"lag_members": list(self.lag_members)` (`from_dict` si ji vezme sam - iteruje pres `cls().to_dict()`). `ServiceEntry`: pridej pole, do `from_dict` `lag_members=_as_list(data.get("lag_members"))`, do `to_dict` `"lag_members": list(self.lag_members)`.

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/models/test_lag_members.py tests/ -x -q`
Expected: PASS cely balik.

- [ ] **Step 5: Commit**

```bash
git add tests/models/test_lag_members.py migration_validator/models/scope.py migration_validator/models/inventory.py
git commit -m "feat: lag_members v Selectors a ServiceEntry"
```

---

### Task 6: Parser cte 802.3ad clenstvi do Layer1 zaznamu bundlu

**Files:**
- Modify: `migration_validator/parsers/core.py` (`InterfaceConfig` :129, `_build_interface_config` :1148+, `InterfaceService` :162, `parse()` :402+, `clean_service_dict` ordered_keys :2213+)
- Test: `tests/parsers/` (pridej do existujiciho test souboru parseru; vzor si vezmi z okolnich testu, ktere krmi parser XML konfiguraci)

**Interfaces:**
- Consumes: konfiguracni XML `<interfaces><interface><name>et-0/0/5</name><gigether-options><ieee-802-3ad><bundle>ae0</bundle></ieee-802-3ad></gigether-options>...`. Element pro ruzne typy portu: `gigether-options`, `ether-options` nebo `aggregated-ether-options` neni relevantni - clenstvi je vzdy `*[local-name()='ieee-802-3ad']/*[local-name()='bundle']`.
- Produces: `InterfaceService.lag_members: list[str]` naplneny na zaznamu BUNDLU (`interface: ae0, service_type: Layer1`), serazeny podle jmena clena; YAML klic `lag_members` v ordered_keys hned za `customer_vlan`.

- [ ] **Step 1: Napis padajici test**

Pouzij stejny vzor stavby parseru jako sousedni testy v `tests/parsers/` (XML string -> parser -> `parse()` -> seznam `InterfaceService`). Minimalni konfigurace: `ae0` (fyzicky, bez unit), `et-0/0/5` a `et-0/0/6` s `ieee-802-3ad/bundle=ae0`:

```python
def test_lag_clenstvi_se_pripne_na_bundle():
    services = _parse(CONFIG_S_LAGEM)  # helper dle okolnich testu
    bundle = next(s for s in services if s.interface == "ae0")
    assert bundle.service_type == "Layer1"
    assert bundle.lag_members == ["et-0/0/5", "et-0/0/6"]
    members = [s for s in services if s.interface.startswith("et-0/0/")]
    assert all(s.lag_members == [] for s in members)
```

- [ ] **Step 2: Over, ze test pada**

Run: `python -m pytest tests/parsers/ -v -k lag_clenstvi`
Expected: FAIL.

- [ ] **Step 3: Implementace**

1. `InterfaceConfig` dostava `bundle: str | None = None`. V `_build_interface_config` (jen pro fyzicke rozhrani; na unit node xpath nic nenajde, coz nevadi):

```python
        bundle = first_text(
            node,
            ".//*[local-name()='ieee-802-3ad']"
            "/*[local-name()='bundle']/text()",
        )
```

2. `InterfaceService` dostava `lag_members: list[str] = field(default_factory=list)` (za `customer_vlan`).
3. V `parse()` po sestaveni seznamu sluzeb postav reverzni mapu a pripni ji na bundle zaznamy:

```python
        members_by_bundle: dict[str, list[str]] = {}
        for config in interface_configs:
            if config.bundle:
                members_by_bundle.setdefault(config.bundle, []).append(config.name)
        for service in services:
            if service.service_type == "Layer1":
                service.lag_members = sorted(
                    members_by_bundle.get(service.interface, [])
                )
```

4. `clean_service_dict`: pridej `"lag_members"` do `ordered_keys` za `"customer_vlan"`.

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/parsers/ tests/ -q`
Expected: PASS.

- [ ] **Step 5: Regeneruj lab inventory a commit**

Fixtures `tests/fixtures/172.20.20.4.yml` / `.5.yml` (a kopie v rootu) doplni pole az pri pristi regeneraci parserem na labu - nacitaji se dal (default `[]`), takze regenerace neni podminkou tasku; poznamku o ni nech v commit message.

```bash
git add tests/parsers/ migration_validator/parsers/core.py
git commit -m "feat: parser cte 802.3ad clenstvi do lag_members Layer1 zaznamu (inventory regenerovat pri pristi navsteve labu)"
```

---

### Task 7: Builder stavi Layer1 scopy

**Files:**
- Modify: `migration_validator/scoping/builder.py`
- Test: `tests/scoping/test_builder.py`

**Interfaces:**
- Consumes: `ServiceEntry.lag_members` (Task 5/6), `Selectors.lag_members` (Task 5).
- Produces: pro kazdy Layer1 zaznam, jehoz port je `physical_name` aspon jedne eligible sluzby, scope `Scope(id=f"l1:{port}", kind="layer1", key=ScopeKey(entry.description, "Layer1", entry.service_subtype), selectors=Selectors(interfaces=[port], lag_members=entry.lag_members))`. Layer1 scopy stoji v seznamu ZA service scopy (poradi bloku resi engine, Task 9). Tag `physical_interfaces` na service scopech beze zmeny.

- [ ] **Step 1: Napis padajici testy**

Do `tests/scoping/test_builder.py` (pouzij vzor stavby Inventory z okolnich testu):

```python
def test_layer1_port_se_sluzbou_dostane_scope():
    inventory = Inventory(device="dev", entries=[
        ServiceEntry(interface="ae0", service_type="Layer1",
                     description="EX1;ae0", service_subtype="physical-port",
                     lag_members=["et-0/0/5"]),
        ServiceEntry(interface="ae0.14", service_type="Internet",
                     description="INET"),
    ])
    scopes = build_scopes(inventory)
    l1 = [s for s in scopes if s.kind == "layer1"]
    assert len(l1) == 1
    assert l1[0].id == "l1:ae0"
    assert l1[0].key.service_type == "Layer1"
    assert l1[0].selectors.interfaces == ["ae0"]
    assert l1[0].selectors.lag_members == ["et-0/0/5"]


def test_layer1_port_bez_sluzeb_scope_nedostane():
    inventory = Inventory(device="dev", entries=[
        ServiceEntry(interface="ge-0/0/9", service_type="Layer1"),
    ])
    assert not [s for s in build_scopes(inventory) if s.kind == "layer1"]


def test_management_layer1_scope_nedostane():
    inventory = Inventory(device="dev", entries=[
        ServiceEntry(interface="fxp0", service_type="Layer1"),
        ServiceEntry(interface="fxp0.0", service_type="Internet"),
    ])
    assert not [s for s in build_scopes(inventory) if s.kind == "layer1"]
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/scoping/test_builder.py -v -k layer1`
Expected: FAIL.

- [ ] **Step 3: Implementace**

Na konec `build_scopes` (za smycku sluzeb, pred `return`):

```python
    LAYER1 = "Layer1"
    children = {entry.physical_name for entry in eligible}
    for entry in inventory.entries:
        if entry.service_type != LAYER1 or is_management(entry.interface):
            continue
        if entry.interface not in children:
            # port bez migrovane sluzby nema v reportu co rict
            continue
        scopes.append(
            Scope(
                id=f"l1:{entry.interface}",
                kind="layer1",
                key=ScopeKey(
                    description=entry.description,
                    service_type=LAYER1,
                    service_subtype=entry.service_subtype,
                ),
                selectors=Selectors(
                    interfaces=[entry.interface],
                    lag_members=list(entry.lag_members),
                ),
                routing_instance_active=entry.routing_instance_active,
                interface_active=entry.interface_active,
            )
        )
```

Docstring `build_scopes` uprav - veta "Zaznamy Layer1 se scopem nestanou" uz neplati.

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/scoping/ tests/ -q`
Expected: PASS. Pozor: synteticky snapshot v `tests/conftest.py` stavi scopy pres `build_scopes`, takze L1 scopy se objevi v e2e - kdyz nejaky e2e invariant spadne (napr. pocty scopu), uprav ho vedome, ne slepe.

- [ ] **Step 5: Commit**

```bash
git add tests/scoping/test_builder.py migration_validator/scoping/builder.py
git commit -m "feat: Layer1 zaznam s detmi se stava scopem kind=layer1"
```

---

### Task 8: Gating checku - layer1 flag a deleni radku fyzicky port vs unit

**Files:**
- Modify: `migration_validator/checks/base.py` (`Check.applies_to`, radky 72-78)
- Modify: `migration_validator/checks/ifaces.py` (vsechny ctyri checky)
- Modify: `migration_validator/checks/deactivation.py` (flag `layer1 = True`)
- Test: `tests/checks/test_base.py`, `tests/checks/test_ifaces.py`

**Interfaces:**
- Produces: `Check.layer1: ClassVar[bool] = False`; `applies_to` na layer1 scopu pousti jen checky s `layer1 = True`. `layer1 = True` maji: `InterfaceStateCheck`, `InterfaceErrorsCheck`, `InterfaceTrafficCheck`, `TrafficCeasedCheck`, `DeactivationStateCheck` (checks/deactivation.py:54) a pozdeji opticke checky (Task 12). Nova helper funkce `scope_interfaces(ctx) -> list[str]` v ifaces.py, pouzita vsemi interface checky.

- [ ] **Step 1: Napis padajici testy**

```python
# tests/checks/test_base.py - pridej:
def _layer1_scope():
    return Scope(
        id="l1:ae0", kind="layer1",
        key=ScopeKey("EX1;ae0", "Layer1", "physical-port"),
        selectors=Selectors(interfaces=["ae0"]),
    )


def test_layer1_scope_pousti_jen_layer1_checky():
    class ServiceOnly(DummyCheck):  # DummyCheck vzor z okolnich testu
        service_types = None

    class ForPort(DummyCheck):
        layer1 = True

    assert not ServiceOnly().applies_to(_layer1_scope())
    assert ForPort().applies_to(_layer1_scope())
    assert ServiceOnly().applies_to(_service_scope())  # chovani sluzeb beze zmeny
```

```python
# tests/checks/test_ifaces.py - pridej (vyuzij existujici ctx helper; scope
# sluzby dostava selectors.physical_interfaces=["ae0"], layer1 scope kind="layer1"):
IFACES = {
    "ae0": {"admin_status": "up", "oper_status": "up", "input_errors": 0,
            "output_errors": 0, "framing_errors": 0, "input_pps": 2, "output_pps": 2},
    "ae0.14": {"admin_status": "up", "oper_status": "up", "input_errors": 0,
               "output_errors": 0, "framing_errors": 0, "input_pps": 1, "output_pps": 1},
}


def test_service_scope_s_l1_rodicem_tiskne_jen_unity():
    ctx = _service_ctx({"interfaces": IFACES}, physical_interfaces=["ae0"])
    labels = [f.label for f in InterfaceStateCheck().run(ctx)]
    assert labels == ["Interface admin status (ae0.14)",
                      "Interface operational status (ae0.14)"]


def test_layer1_scope_tiskne_jen_fyzicky_port_bez_kvalifikatoru():
    ctx = _layer1_ctx({"interfaces": {"ae0": IFACES["ae0"]}})
    labels = [f.label for f in InterfaceStateCheck().run(ctx)]
    assert labels == ["Interface admin status", "Interface operational status"]


def test_errors_v_service_scopu_s_l1_rodicem_zadny_radek():
    ctx = _service_ctx({"interfaces": IFACES}, physical_interfaces=["ae0"])
    assert InterfaceErrorsCheck().run(ctx) == []


def test_errors_v_layer1_scopu_bez_kvalifikatoru():
    ctx = _layer1_ctx({"interfaces": {"ae0": IFACES["ae0"]}})
    rows = InterfaceErrorsCheck().run(ctx)
    assert [f.label for f in rows] == ["Interface errors"]


def test_traffic_v_service_scopu_jen_unit_v_l1_jen_port():
    service = InterfaceTrafficCheck().run(
        _service_ctx({"interfaces": IFACES}, physical_interfaces=["ae0"]))
    assert {f.label for f in service} == {
        "Interface traffic in (ae0.14)", "Interface traffic out (ae0.14)"}
    l1 = InterfaceTrafficCheck().run(_layer1_ctx({"interfaces": {"ae0": IFACES["ae0"]}}))
    assert {f.label for f in l1} == {"Interface traffic in", "Interface traffic out"}


def test_parentless_service_beze_zmeny():
    ctx = _service_ctx({"interfaces": {"ae0.14": IFACES["ae0.14"]}},
                       physical_interfaces=[])
    rows = InterfaceErrorsCheck().run(ctx)
    assert rows[0].outcome is Outcome.SKIP  # "jen unity" jako dnes
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/checks/test_base.py tests/checks/test_ifaces.py -v`
Expected: nove testy FAIL.

- [ ] **Step 3: Implementace**

`checks/base.py`:

```python
LAYER1_SERVICE_TYPE = "Layer1"


class Check(ABC):
    ...
    # Bezi check i na Layer1 scopu (fyzicky port)? Vychozi ne - vetsina
    # checku meri sluzbu, ne port, a SKIP radky by L1 blok jen zaplevelily.
    layer1: ClassVar[bool] = False

    def applies_to(self, scope: Scope) -> bool:
        if scope.is_device:
            return True
        if scope.kind == "layer1":
            return self.layer1
        if self.service_types is None:
            return True
        return scope.service_type in self.service_types
```

`checks/ifaces.py` - nova sdilena funkce + upravy:

```python
def scope_interfaces(ctx: CheckContext) -> list[str]:
    """Rozhrani, jejichz radky patri do tohoto scopu.

    Layer1 scope nese fyzicky port; service scope s L1 rodicem jen unity
    (radky portu ma jeho L1 blok - deduplikace ze specu); sluzba bez L1
    rodice vse jako drive.
    """
    names = sorted(ctx.subject.get("interfaces", {}))
    if ctx.scope.kind == "layer1":
        return [name for name in names if is_physical(name)]
    if ctx.scope.selectors.physical_interfaces:
        return [name for name in names if not is_physical(name)]
    return names
```

- `InterfaceStateCheck.run`: iteruj `scope_interfaces(ctx)` misto `sorted(interfaces)`; label `label if ctx.scope.kind == "layer1" else qualified(label, name)`. Kdyz je vysledny seznam prazdny, vrat dosavadni SKIP "bez dat" jen kdyz je prazdna cela area (jinak nic - unit radky ma druhy blok).
- `InterfaceErrorsCheck.run`: INFO vetev `_l3_link_without_transit` zustava prvni. Pak: `if ctx.scope.kind != "layer1" and ctx.scope.selectors.physical_interfaces: return []`. Zbytek pres `scope_interfaces(ctx)` + `is_transit`; label bez kvalifikatoru na layer1 scopu.
- `InterfaceTrafficCheck.run` a `TrafficCeasedCheck.run`: `names = [n for n in scope_interfaces(ctx) if is_transit(n)]`; label bez kvalifikatoru na layer1 scopu (u `_traffic_finding` predej hotovy label misto `qualified` uvnitr - uprav signaturu, at kvalifikaci resi volajici).
- `layer1 = True` na vsech ctyrech checkech a na `DeactivationStateCheck` v `checks/deactivation.py`.

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/checks/ tests/test_end_to_end.py -q`
Expected: PASS. E2E: bloky sluzeb ztrati radky fyzickych portu - invarianty "kazdy radek ma label a hodnotu" musi projit; asserty na konkretni radky uprav.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/ tests/checks/
git commit -m "feat: interface checky deli radky mezi L1 blok a bloky sluzeb"
```

---

### Task 9: Engine - identity, parovani L1 pres deti, razeni po portech

**Files:**
- Modify: `migration_validator/engine.py` (`_identity` :109, `evaluate_snapshots` :330, nove funkce vedle `_reorder_linked` :160)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: Layer1 scopy z builderu (Task 7), `kind == "layer1"`.
- Produces: `_identity` navic vraci `"physical_interfaces": list(selectors.physical_interfaces)`. `evaluate_snapshots` vyjima layer1 scopy z `match_scopes` a paruje je pres deti (`_l1_baseline`, method `"layer1-children"`, confidence `"medium"`); baseline-only L1 scopy se do NESPAROVANO nepisou (infrastruktura, ne sluzba). Vystupni poradi: `_group_by_layer1(_reorder_linked(results))` - L1 blok, jeho deti (L3+L2 sousednost zachovana), dalsi port v prirozenem razeni, sluzby bez L1 rodice na konci.

- [ ] **Step 1: Napis padajici testy**

Do `tests/test_engine.py`. Snapshot se scopy stav existujicim vzorem z tohoto souboru (najdi tovarnu, ktera vola `Snapshot(...)` s `facts={}` a predava `scopes=` - kdyz tam neni, zkopiruj vzor z `tests/conftest.py`); scopy samotne staci rucne:

```python
def _l1(port, members=()):
    return Scope(id=f"l1:{port}", kind="layer1",
                 key=ScopeKey(f"L1;{port}", "Layer1", "physical-port"),
                 selectors=Selectors(interfaces=[port],
                                     lag_members=list(members)))


def _svc(name, unit, parent, service_type="Internet"):
    return Scope(id=f"svc:{name}:{service_type}", kind="service",
                 key=ScopeKey(name, service_type),
                 selectors=Selectors(interfaces=[unit],
                                     physical_interfaces=[parent]))


def test_bloky_se_skupinuji_po_portech_prirozene_razene():
    scopes = [
        _svc("S-B", "ge-0/0/10.0", "ge-0/0/10"),
        _svc("S-A", "ge-0/0/2.0", "ge-0/0/2"),
        _svc("S-LO", "lo0.0", "lo0"),  # bez L1 scopu
        _l1("ge-0/0/10"),
        _l1("ge-0/0/2"),
    ]
    result = evaluate_snapshots(_snapshot_with(scopes))
    ids = [r.scope_id for r in result.scopes]
    assert ids == ["l1:ge-0/0/2", "svc:S-A:Internet",
                   "l1:ge-0/0/10", "svc:S-B:Internet",
                   "svc:S-LO:Internet"]


def _result(scope_id, service_type, interfaces=(), parents=(), link=None):
    """Hotovy ScopeResult pro unit test razeni - engine se neobchazi,
    _group_by_layer1 je cista funkce nad vysledky."""
    return ScopeResult(
        scope_id=scope_id, key={"service_type": service_type},
        status=Status.PASS, match=None, checks=[],
        identity={"interfaces": list(interfaces),
                  "physical_interfaces": list(parents)},
        link=link,
    )


def test_l3_l2_par_drzi_pohromade_pod_portem_l2_rozhrani():
    # L3 blok (irb.14, bez fyzickeho rodice) dedi rodice sve L2 casti:
    # par stoji pod l1:ae0, L3 pred L2 (vstup uz prosel _reorder_linked).
    l3 = _result("svc:INET:Internet", "Internet", interfaces=["irb.14"],
                 link={"role": "l3", "peer_scope_id": "svc:ELAN:E-LAN",
                       "peer_interface": "ae0.14", "peer_instance": "POP1"})
    l2 = _result("svc:ELAN:E-LAN", "E-LAN", interfaces=["ae0.14"],
                 parents=["ae0"],
                 link={"role": "l2", "peer_scope_id": "svc:INET:Internet",
                       "peer_interface": "irb.14", "peer_instance": "inet.0"})
    l1 = _result("l1:ae0", "Layer1", interfaces=["ae0"])
    ordered = _group_by_layer1([l3, l2, l1])
    assert [r.scope_id for r in ordered] == [
        "l1:ae0", "svc:INET:Internet", "svc:ELAN:E-LAN"]


def test_l1_baseline_z_deti():
    # baseline: sluzba INET na ge-0/0/5.0 (rodic ge-0/0/5) + l1:ge-0/0/5
    # subject:  sluzba INET na ae0.14 (rodic ae0) + l1:ae0
    # sluzby sdili description "INET" -> match pres description,
    # L1 par se pak odvodi z deti. Oba snapshoty stejnou tovarnou.
    baseline_snapshot = _snapshot_with(
        [_svc("INET", "ge-0/0/5.0", "ge-0/0/5"), _l1("ge-0/0/5")])
    subject_snapshot = _snapshot_with(
        [_svc("INET", "ae0.14", "ae0"), _l1("ae0")])
    result = evaluate_snapshots(subject_snapshot, baseline_snapshot)
    l1 = next(r for r in result.scopes if r.scope_id == "l1:ae0")
    assert l1.match is not None and l1.match.method == "layer1-children"
    assert l1.match.baseline_interfaces == ["ge-0/0/5"]
    assert not any(item["scope_id"].startswith("l1:")
                   for side in ("baseline", "subject")
                   for item in result.unmatched[side])


def test_identity_nese_physical_interfaces():
    result = evaluate_snapshots(_snapshot_with([_svc("S", "ae0.14", "ae0"), _l1("ae0")]))
    svc = next(r for r in result.scopes if r.scope_id.startswith("svc:"))
    assert svc.identity["physical_interfaces"] == ["ae0"]
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/test_engine.py -v -k "skupinuji or l1_baseline or physical_interfaces"`
Expected: FAIL.

- [ ] **Step 3: Implementace**

1. `_identity`: pridej `"physical_interfaces": list(selectors.physical_interfaces),` za `"interfaces"`.

2. Nove funkce vedle `_reorder_linked`:

```python
import re
from collections import Counter

LAYER1_TYPE = "Layer1"


def _natural_key(name: str) -> list:
    return [int(part) if part.isdigit() else part
            for part in re.split(r"(\d+)", name)]


def _is_l1(result: ScopeResult) -> bool:
    return (result.key or {}).get("service_type") == LAYER1_TYPE


def _parent_port(result: ScopeResult, by_id: dict[str, ScopeResult]) -> str | None:
    """L1 rodic bloku. L3 clen paru dedi rodice sve L2 casti - par ma stat
    pod portem, na kterem sluzba fyzicky bezi (spec kap. 1)."""
    link = result.link
    if link and link["role"] == "l3":
        peer = by_id.get(link["peer_scope_id"])
        if peer is not None:
            result = peer
    parents = (result.identity or {}).get("physical_interfaces") or []
    return parents[0] if parents else None


def _group_by_layer1(results: list[ScopeResult]) -> list[ScopeResult]:
    """Poradi bloku: L1 port -> jeho sluzby, porty prirozene razene,
    sluzby bez L1 rodice na konci. Vstup uz prosel _reorder_linked,
    takze relativni poradi sluzeb (vcetne L3+L2 sousednosti) se drzi."""
    by_id = {result.scope_id: result for result in results}
    l1_by_port = {
        result.identity.get("interfaces", ["?"])[0]: result
        for result in results
        if _is_l1(result)
    }
    services = [result for result in results if not _is_l1(result)]
    ordered: list[ScopeResult] = []
    for port in sorted(l1_by_port, key=_natural_key):
        ordered.append(l1_by_port[port])
        ordered.extend(
            result for result in services
            if _parent_port(result, by_id) == port
        )
    ordered.extend(
        result for result in services
        if _parent_port(result, by_id) not in l1_by_port
    )
    return ordered
```

3. Parovani L1 pres deti:

```python
def _l1_baseline(
    l1_scope: Scope,
    pairs: list[MatchedPair],
    baseline_l1: list[Scope],
) -> Scope | None:
    """Baseline protejsek L1 portu odvozeny z jeho sparovanych deti.

    Jmeno portu se migraci meni (ge-0/0/5 -> ae0), primy match nejde.
    Remiza hlasu = zadny par - spatny odkaz je horsi nez zadny (stejne
    pravidlo jako matcher)."""
    port = l1_scope.selectors.interfaces[0]
    votes: Counter[str] = Counter()
    for pair in pairs:
        if pair.subject.selectors.physical_interfaces == [port]:
            parents = pair.baseline.selectors.physical_interfaces
            if parents:
                votes[parents[0]] += 1
    if not votes:
        return None
    (top, top_count), *rest = votes.most_common()
    if rest and rest[0][1] == top_count:
        return None
    return next(
        (scope for scope in baseline_l1 if scope.selectors.interfaces == [top]),
        None,
    )
```

4. `evaluate_snapshots`: rozdel scopy a L1 zprac zvlast:

```python
    subject_scopes = _scopes_of(subject)
    subject_l1 = [s for s in subject_scopes if s.kind == "layer1"]
    subject_services = [s for s in subject_scopes if s.kind != "layer1"]
```

- Vetev bez baseline: `_run_scope` pro services i l1 (baseline None), jako dnes.
- Vetev s baseline: `baseline_l1` / `baseline_services` stejnym filtrem; `match_scopes(baseline_services, subject_services, mapping)`. Po smycce pairs/unmatched pridej:

```python
        for l1_scope in subject_l1:
            baseline_scope = _l1_baseline(l1_scope, matches.pairs, baseline_l1)
            match = (
                MatchInfo(
                    status="matched",
                    method="layer1-children",
                    confidence="medium",
                    baseline_interfaces=list(baseline_scope.selectors.interfaces),
                    subject_interfaces=list(l1_scope.selectors.interfaces),
                )
                if baseline_scope is not None
                else None
            )
            scope_results.append(
                _run_scope(l1_scope, subject, baseline_scope, baseline, config, match)
            )
```

Baseline-only L1 scopy do `unmatched["baseline"]` nepridavej. Pozn.: `_unassigned_*` funkce dal dostavaji vsechny scopy (`subject_scopes`) - selektory L1 scopu nic nenarokuji, chovani se nemeni.

5. Zaver: `scope_results = _group_by_layer1(_reorder_linked(scope_results))`.

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/test_engine.py tests/test_end_to_end.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_engine.py migration_validator/engine.py
git commit -m "feat: report razeny po L1 portech, L1 baseline parovani pres deti"
```

---

### Task 10: Renderer drzi L1 rodice u zobrazenych deti + e2e invariant

**Files:**
- Modify: `migration_validator/reporting/text_report.py` (`filter_result` :107-153, `shown` set :471-479)
- Test: `tests/reporting/test_text_report.py`, `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `ScopeResult.identity["physical_interfaces"]` (Task 9), `key["service_type"] == "Layer1"`.
- Produces: helper `_l1_parent_ids(all_scopes, kept_ids) -> set[str]` v text_report.py; pouzity ve `filter_result` (za krokem s partnery) i pri stavbe `shown`. Hlavicka L1 bloku zadnou novou sazbu nema - jede pres existujici `_block` (TYP sloupec nese "Layer1").

- [ ] **Step 1: Napis padajici testy**

```python
# tests/reporting/test_text_report.py - vzor stavby RunResult/ScopeResult
# viz okolni testy; L1 scope: key {"service_type": "Layer1"},
# identity {"interfaces": ["ae0"], "physical_interfaces": []}.

def test_filter_drzi_l1_rodice_vybrane_sluzby():
    result = _run_result([_l1_result("ae0", status=Status.PASS),
                          _svc_result("S-A", parent="ae0", status=Status.FAIL)])
    filtered = filter_result(result, text="S-A")
    ids = [scope.scope_id for scope in filtered.scopes]
    assert "l1:ae0" in ids  # rodic jede s vybranym ditetem


def test_fail_sluzba_rozbali_i_pass_l1_blok():
    result = _run_result([_l1_result("ae0", status=Status.PASS),
                          _svc_result("S-A", parent="ae0", status=Status.FAIL)])
    text = render(result)
    # blok l1:ae0 se vytiskl, i kdyz je PASS a detail=False
    assert "Layer1" in text
```

Do `tests/test_end_to_end.py` pridej invariant:

```python
def test_sluzba_stoji_za_svym_l1_blokem(evaluated):  # fixture dle okolnich testu
    order = [(r.scope_id, (r.key or {}).get("service_type"),
              (r.identity or {}).get("physical_interfaces") or [None],
              (r.identity or {}).get("interfaces") or [None])
             for r in evaluated.scopes]
    last_l1_port = None
    l1_ports = {ifaces[0] for _, t, _, ifaces in order if t == "Layer1"}
    for scope_id, service_type, parents, ifaces in order:
        if service_type == "Layer1":
            last_l1_port = ifaces[0]
        elif parents[0] in l1_ports:
            assert parents[0] == last_l1_port, (
                f"{scope_id}: rodic {parents[0]}, ale posledni L1 blok {last_l1_port}")
```

(Pozn. pro L3+L2 pary: L3 blok ma `physical_interfaces` prazdne, takze ho invariant preskoci - jeho umisteni kryje test z Tasku 9.)

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/reporting/test_text_report.py -v -k l1`
Expected: FAIL.

- [ ] **Step 3: Implementace**

```python
def _l1_parent_ids(all_scopes, kept_ids):
    """L1 rodice zobrazenych sluzeb - rodic jde s ditetem, aby seskupeni
    po portech nezustalo bez hlavicky portu."""
    l1_by_port = {
        (scope.identity or {}).get("interfaces", ["?"])[0]: scope.scope_id
        for scope in all_scopes
        if (scope.key or {}).get("service_type") == "Layer1"
    }
    parents = set()
    for scope in all_scopes:
        if scope.scope_id not in kept_ids:
            continue
        for port in (scope.identity or {}).get("physical_interfaces") or []:
            if port in l1_by_port:
                parents.add(l1_by_port[port])
    return parents
```

Ve `filter_result` za blokem s partnery (radek ~138): `kept_ids |= _l1_parent_ids(result.scopes, kept_ids)` a znovu postav `scopes` ze `result.scopes` podle rozsirene mnoziny (stejny vzor jako u partneru). Pri stavbe `shown` (radek ~479): `shown |= _l1_parent_ids([scope for scope, _ in views], shown)` - pozor, tady iterujes dvojice `(scope, view)`, helper dostava jen scopy.

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/reporting/ tests/test_end_to_end.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/reporting/test_text_report.py tests/test_end_to_end.py migration_validator/reporting/text_report.py
git commit -m "feat: L1 rodic se zobrazuje spolu se svymi sluzbami"
```

---

### Task 11: Opticky collector + area optics + schema 8

**Files:**
- Create: `migration_validator/collectors/optics.py`
- Modify: `migration_validator/collectors/all.py` (import), `migration_validator/models/scope.py` (FACT_AREAS, `select`), `migration_validator/models/snapshot.py` (SCHEMA_VERSION 8), `tests/conftest.py` (COLLECTOR_NAMES + opticka fakta), `migration_validator/engine.py` (`_aligned_baseline_data` - preslovnuti optics)
- Test: `tests/collectors/test_optics.py`

**Interfaces:**
- Produces: area `optics: dict[str, {"lanes": [Lane]}]`, kde `Lane = {"lane": int | None, "rx_power_dbm": float | None, "tx_power_dbm": float | None, "temperature_c": float | None, "alarms": dict[str, bool], "warnings": dict[str, bool]}` (True = flag zvednuty; klice jen pro tagy v XML pritomne). RX se sleva z `laser-rx-optical-power-dbm` NEBO `rx-signal-avg-optical-power-dbm` do `rx_power_dbm` uz pri parsovani. Task 12 na tomto tvaru stavi.

- [ ] **Step 1 (KROK NULA - lab): Over RPC jmeno a nahraj XML**

Na labu (heslo: `eval` bloku pod neinteraktivnim guardem v `~/.bashrc`, viz memory `lab-password-not-in-shell-env`):

1. Na PTX/EVO spust `show interfaces diagnostics optics | display xml rpc` a zapis skutecne RPC jmeno (ocekavane `get-interface-optics-diagnostics-information` -> PyEZ `get_interface_optics_diagnostics_information`; kdyz se lisi, pouzij skutecne a oprav nasledujici kod).
2. Nahraj raw XML odpoved do `tests/fixtures/rpc/junos-evo/optics.xml` (pres `mig-validate record` / `--record-raw`, stejne jako ostatni fixtures).
3. vMX optiku nevraci - pro `junos` zadna fixture nebude; `rpc_fixture` test preskoci (tests/collectors/conftest.py:13) a MX tvary kryji synteticke XML v unit testech nize.

- [ ] **Step 2: Napis padajici testy**

```python
# tests/collectors/test_optics.py
from lxml import etree

from migration_validator.collectors.optics import OpticsCollector

MX_S_LANES = etree.fromstring("""
<interface-information>
  <physical-interface>
    <name>et-0/0/5</name>
    <optics-diagnostics>
      <optics-diagnostics-lane-values>
        <lane-index>0</lane-index>
        <laser-rx-optical-power-dbm>-5.23</laser-rx-optical-power-dbm>
        <laser-output-power-dbm>-2.10</laser-output-power-dbm>
        <laser-rx-power-high-alarm>off</laser-rx-power-high-alarm>
        <laser-rx-power-low-alarm>on</laser-rx-power-low-alarm>
        <laser-rx-power-low-warn>off</laser-rx-power-low-warn>
      </optics-diagnostics-lane-values>
    </optics-diagnostics>
  </physical-interface>
</interface-information>
""")

MX_BEZ_LANES_KOHERENTNI = etree.fromstring("""
<interface-information>
  <physical-interface>
    <name>et-0/1/0</name>
    <optics-diagnostics>
      <rx-signal-avg-optical-power-dbm>-7.80</rx-signal-avg-optical-power-dbm>
      <laser-output-power-dbm>0.51</laser-output-power-dbm>
      <laser-rx-power-high-alarm>Off</laser-rx-power-high-alarm>
    </optics-diagnostics>
  </physical-interface>
</interface-information>
""")


def test_lane_varianta_a_zvednuty_alarm():
    result = OpticsCollector().parse(MX_S_LANES, "junos")
    lane = result["et-0/0/5"]["lanes"][0]
    assert lane["lane"] == 0
    assert lane["rx_power_dbm"] == -5.23
    assert lane["tx_power_dbm"] == -2.10
    assert lane["alarms"]["laser-rx-power-low-alarm"] is True
    assert lane["alarms"]["laser-rx-power-high-alarm"] is False
    assert lane["warnings"]["laser-rx-power-low-warn"] is False


def test_bez_lane_koherentni_rx_se_sleva_a_Off_je_off():
    result = OpticsCollector().parse(MX_BEZ_LANES_KOHERENTNI, "junos")
    lane = result["et-0/1/0"]["lanes"][0]
    assert lane["lane"] is None
    assert lane["rx_power_dbm"] == -7.80  # slouceno z rx-signal-avg
    assert lane["alarms"]["laser-rx-power-high-alarm"] is False  # "Off"


def test_port_bez_optiky_v_area_neni():
    xml = etree.fromstring(
        "<interface-information><physical-interface>"
        "<name>ge-0/0/0</name></physical-interface></interface-information>")
    assert OpticsCollector().parse(xml, "junos") == {}


def test_realne_evo_xml(rpc_fixture):
    xml = rpc_fixture("junos-evo", "optics")  # skip, dokud fixture neni
    result = OpticsCollector().parse(xml, "junos-evo")
    assert result  # aspon jeden port s lanes
    for data in result.values():
        assert all("rx_power_dbm" in lane for lane in data["lanes"])
```

- [ ] **Step 3: Over, ze testy padaji**

Run: `python -m pytest tests/collectors/test_optics.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 4: Implementace collectoru**

```python
# migration_validator/collectors/optics.py
"""Sber optickych urovni a alarmu - show interfaces diagnostics optics.

RPC jmeno overeno na labu pres '| display xml rpc' (krok nula planu) -
NEHADAT, viz collectors/evpn.py.

Dve podoby vypisu: moduly s lanes (optics-diagnostics-lane-values, ACX/EVO
vzdy, MX nekdy) a bez lanes (jen optics-diagnostics, MX). Bez-lane modul se
uklada jako jedna lane s lane=None. RX vykon ma na MX dve jmena
(laser-rx-optical-power-dbm vs rx-signal-avg-optical-power-dbm u
koherentnich modulu) - slevaji se tady, downstream vidi jen rx_power_dbm.
Flagy se porovnavaji case-insensitive ('off' MX vs 'Off' ACX/EVO).
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector
from migration_validator.collectors.registry import register

ALARM_TAGS = (
    "laser-rx-power-high-alarm",
    "laser-rx-power-low-alarm",
    "rx-loss-of-signal-alarm",
    "laser-tx-power-high-alarm",
    "laser-tx-power-low-alarm",
    "tx-loss-of-signal-functionality-alarm",
    "tx-laser-disabled-alarm",
    "laser-temp-high-alarm",
    "laser-temp-low-alarm",
)

WARN_TAGS = (
    "laser-rx-power-high-warn",
    "laser-rx-power-low-warn",
    "laser-tx-power-high-warn",
    "laser-tx-power-low-warn",
    "laser-temp-high-warn",
    "laser-temp-low-warn",
)


def _text(node: etree._Element, path: str) -> str | None:
    found = node.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip()


def _float(node: etree._Element, path: str) -> float | None:
    value = _text(node, path)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _flags(node: etree._Element, tags: tuple[str, ...]) -> dict[str, bool]:
    """True = flag zvednuty. Tag, ktery v XML neni, se neuklada - absence
    neni totez co 'off' a check ji nema co posuzovat."""
    result: dict[str, bool] = {}
    for tag in tags:
        value = _text(node, tag)
        if value is not None:
            result[tag] = value.lower() != "off"
    return result


def _lane(node: etree._Element, lane_index: str | None) -> dict[str, Any]:
    rx = _float(node, "laser-rx-optical-power-dbm")
    if rx is None:
        rx = _float(node, "rx-signal-avg-optical-power-dbm")
    return {
        "lane": int(lane_index) if lane_index is not None else None,
        "rx_power_dbm": rx,
        "tx_power_dbm": _float(node, "laser-output-power-dbm"),
        "temperature_c": _float(node, "laser-temperature"),
        "alarms": _flags(node, ALARM_TAGS),
        "warnings": _flags(node, WARN_TAGS),
    }


@register
class OpticsCollector(Collector):
    name = "optics"

    def rpc_name(self, platform: str) -> str:
        return "get_interface_optics_diagnostics_information"

    def parse(
        self, xml: etree._Element, platform: str
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for physical in xml.iter("physical-interface"):
            name = _text(physical, "name")
            diagnostics = physical.find("optics-diagnostics")
            if not name or diagnostics is None:
                continue
            lane_nodes = diagnostics.findall("optics-diagnostics-lane-values")
            if lane_nodes:
                lanes = [
                    _lane(node, _text(node, "lane-index")) for node in lane_nodes
                ]
            else:
                lanes = [_lane(diagnostics, None)]
            result[name] = {"lanes": lanes}
        return result
```

Import do `collectors/all.py` (vzor ostatnich).

- [ ] **Step 5: Area, schema, conftest, preslovnuti**

1. `models/scope.py`: `FACT_AREAS` + `"optics"` (na konec). V `select` pridej pred `return`:

```python
        optics = {
            name: data
            for name, data in (facts.get("optics") or {}).items()
            if self.selectors.matches_interface(name)
            or name in self.selectors.lag_members
        }
```

a `"optics": optics,` do vraceneho dictu.
2. `models/snapshot.py`: `SCHEMA_VERSION = 8` (from_dict stare snapshoty odmitne - zamer; `runs/mig01` je nutne presnimat, viz Task 14).
3. `tests/conftest.py`: `COLLECTOR_NAMES` + `"optics"` a do stavby faktu synteticke zdrave optiky pro fyzicke tranzitni porty (tam, kde se stavi area interfaces):

```python
    "optics": {
        port: {"lanes": [{"lane": 0, "rx_power_dbm": -5.0, "tx_power_dbm": -2.0,
                          "temperature_c": 30.0, "alarms": {}, "warnings": {}}]}
        for port in physical_ports  # promenna dle realneho tvaru fixture
    },
```

4. `engine._aligned_baseline_data`: za `rename.update(zip(...physical_interfaces...))` pridej `rename.update(zip(selectors.lag_members, scope.selectors.lag_members))` (pozicni princip jako u interfaces - clenove jsou serazeni, viz docstring) a preslovnuj oblast:

```python
    if rename and data.get("optics"):
        data["optics"] = {
            rename.get(name, name): optics_data
            for name, optics_data in data["optics"].items()
        }
```

- [ ] **Step 6: Over, ze testy prochazi**

Run: `python -m pytest tests/collectors/test_optics.py tests/ -q`
Expected: PASS (EVO test SKIP, dokud neni fixture z labu; po nahrani PASS).

- [ ] **Step 7: Commit**

```bash
git add migration_validator/collectors/ migration_validator/models/ migration_validator/engine.py tests/
git commit -m "feat: collector optics - urovne, alarmy, sliti RX variant, schema 8"
```

---

### Task 12: Checky Interface optical levels / alarms

**Files:**
- Create: `migration_validator/checks/optics.py`
- Modify: `migration_validator/checks/all.py` (import; overit jmeno souboru s registracnimi importy - vzor `checks/evpn.py` je importovan odtud), `migration_validator/config.py` (DEFAULTS)
- Test: `tests/checks/test_optics.py`

**Interfaces:**
- Consumes: area `optics` (Task 11), `ctx.scope.selectors.interfaces[0]` (port L1 scopu), `ctx.scope.selectors.lag_members`, `Check.layer1` flag (Task 8).
- Produces: check `interface_optics_levels` (label `Interface optical levels`, Mode.BOTH, ADVISORY, options `{"tolerance_db": 2.0}`), check `interface_optics_alarms` (label `Interface optical alarms`, Mode.STATE, CRITICAL). Oba `layer1 = True`, `service_types = frozenset()` (nikdy na service scopu).

- [ ] **Step 1: Napis padajici testy**

```python
# tests/checks/test_optics.py
from migration_validator.checks.base import CheckContext
from migration_validator.checks.optics import OpticalAlarmsCheck, OpticalLevelsCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _lane(rx=-5.0, tx=-2.0, lane=0, alarms=None, warnings=None):
    return {"lane": lane, "rx_power_dbm": rx, "tx_power_dbm": tx,
            "temperature_c": 30.0, "alarms": alarms or {}, "warnings": warnings or {}}


def _ctx(subject, baseline=None, port="ae0", members=()):
    scope = Scope(id=f"l1:{port}", kind="layer1",
                  key=ScopeKey(f"L1;{port}", "Layer1", "physical-port"),
                  selectors=Selectors(interfaces=[port],
                                      lag_members=list(members)))
    return CheckContext(scope=scope, subject=subject, baseline=baseline,
                        config=default_config(), failed_collectors={})


def test_levels_bez_baseline_informativni():
    findings = OpticalLevelsCheck().run(
        _ctx({"optics": {"ae0": {"lanes": [_lane()]}}}))
    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
    assert findings[0].label == "Interface optical levels (lane 0)"
    assert findings[0].value == "RX -5.00 dBm / TX -2.00 dBm"


def test_levels_delta_pres_toleranci_je_degraded():
    findings = OpticalLevelsCheck().run(_ctx(
        {"optics": {"ae0": {"lanes": [_lane(rx=-8.1)]}}},
        baseline={"optics": {"ae0": {"lanes": [_lane(rx=-5.0)]}}}))
    assert findings[0].outcome is Outcome.DEGRADED
    assert "RX -3.1 dB" in findings[0].delta


def test_levels_delta_v_toleranci_je_ok():
    findings = OpticalLevelsCheck().run(_ctx(
        {"optics": {"ae0": {"lanes": [_lane(rx=-6.0)]}}},
        baseline={"optics": {"ae0": {"lanes": [_lane(rx=-5.0)]}}}))
    assert findings[0].outcome is Outcome.OK


def test_levels_clen_lagu_nese_jmeno_v_labelu():
    findings = OpticalLevelsCheck().run(_ctx(
        {"optics": {"et-0/0/5": {"lanes": [_lane()]}}},
        port="ae0", members=["et-0/0/5"]))
    labels = [f.label for f in findings]
    assert "Interface optical levels (et-0/0/5 lane 0)" in labels
    # samotny ae0 optiku nema -> SKIP radek "bez optiky"
    skip = next(f for f in findings if f.outcome is Outcome.SKIP)
    assert skip.value == "bez optiky"


def test_alarms_ticho_je_jeden_pass_radek():
    findings = OpticalAlarmsCheck().run(
        _ctx({"optics": {"ae0": {"lanes": [_lane()]}}}))
    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
    assert findings[0].label == "Interface optical alarms"
    assert findings[0].value == "bez alarmu"


def test_alarms_zvednuty_alarm_je_broken_warn_degraded():
    lane = _lane(alarms={"laser-rx-power-low-alarm": True},
                 warnings={"laser-tx-power-low-warn": True})
    findings = OpticalAlarmsCheck().run(
        _ctx({"optics": {"ae0": {"lanes": [lane]}}}))
    by_value = {f.value: f.outcome for f in findings}
    assert by_value["laser-rx-power-low-alarm"] is Outcome.BROKEN
    assert by_value["laser-tx-power-low-warn"] is Outcome.DEGRADED
    assert "bez alarmu" not in by_value


def test_flag_off_se_nevypisuje():
    lane = _lane(alarms={"laser-rx-power-low-alarm": False})
    findings = OpticalAlarmsCheck().run(
        _ctx({"optics": {"ae0": {"lanes": [lane]}}}))
    assert [f.value for f in findings] == ["bez alarmu"]
```

- [ ] **Step 2: Over, ze testy padaji**

Run: `python -m pytest tests/checks/test_optics.py -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implementace**

```python
# migration_validator/checks/optics.py
"""Opticke urovne a alarmy - jen Layer1 scopy.

Urovne se posuzuji deltou proti baseline (prahy modulu uz vyhodnotil box
sam - to jsou alarm/warn flagy). Alarm radky se tisknou JEN zvednute;
tichy port ma jeden souhrnny radek, stejny vzor jako Interface errors.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity


def _optics_label(base: str, name: str, lane: int | None, port: str) -> str:
    parts = []
    if name != port:
        parts.append(name)  # clen LAGu - jmeno je pointa radku
    if lane is not None:
        parts.append(f"lane {lane}")
    return f"{base} ({' '.join(parts)})" if parts else base


def _ports(ctx: CheckContext) -> list[str]:
    port = ctx.scope.selectors.interfaces[0] if ctx.scope.selectors.interfaces else None
    return [name for name in [port, *sorted(ctx.scope.selectors.lag_members)] if name]


def _fmt(power: float | None) -> str:
    return f"{power:.2f} dBm" if power is not None else "?"


@register
class OpticalLevelsCheck(Check):
    id = "interface_optics_levels"
    title = "Opticke urovne"
    label = "Interface optical levels"
    mode = Mode.BOTH
    requires = ("optics",)
    service_types = frozenset()  # nikdy na service scopu
    layer1 = True
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        optics: dict[str, Any] = ctx.subject.get("optics", {})
        baseline_optics: dict[str, Any] = (ctx.baseline or {}).get("optics", {})
        tolerance = float(ctx.options(self.id)["tolerance_db"])
        port = ctx.scope.selectors.interfaces[0]

        findings: list[Finding] = []
        for name in _ports(ctx):
            data = optics.get(name)
            if data is None:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{name}: rozhrani nevraci opticka data",
                        label=_optics_label(self.label, name, None, port),
                        value="bez optiky",
                    )
                )
                continue
            baseline_lanes = {
                lane.get("lane"): lane
                for lane in baseline_optics.get(name, {}).get("lanes", [])
            }
            for lane in data["lanes"]:
                findings.append(
                    _level_finding(
                        _optics_label(self.label, name, lane["lane"], port),
                        name,
                        lane,
                        baseline_lanes.get(lane["lane"]),
                        tolerance,
                    )
                )
        return findings


def _level_finding(
    label: str,
    name: str,
    lane: dict[str, Any],
    baseline_lane: dict[str, Any] | None,
    tolerance: float,
) -> Finding:
    value = f"RX {_fmt(lane['rx_power_dbm'])} / TX {_fmt(lane['tx_power_dbm'])}"
    if baseline_lane is None:
        return Finding(
            Outcome.OK, f"{name}: {value}", label=label, value=value,
            subject={"rx_power_dbm": lane["rx_power_dbm"],
                     "tx_power_dbm": lane["tx_power_dbm"]},
        )

    deltas = []
    degraded = False
    for key, tag in (("rx_power_dbm", "RX"), ("tx_power_dbm", "TX")):
        now, before = lane.get(key), baseline_lane.get(key)
        if now is None or before is None:
            continue
        diff = now - before
        deltas.append(f"{tag} {diff:+.1f} dB")
        if abs(diff) > tolerance:
            degraded = True

    baseline_value = (
        f"RX {_fmt(baseline_lane['rx_power_dbm'])}"
        f" / TX {_fmt(baseline_lane['tx_power_dbm'])}"
    )
    message = (
        f"{name}: uroven se posunula o vic nez {tolerance:.1f} dB"
        f" ({', '.join(deltas)})"
        if degraded
        else f"{name}: urovne v toleranci {tolerance:.1f} dB"
    )
    return Finding(
        Outcome.DEGRADED if degraded else Outcome.OK,
        message,
        label=label,
        value=value,
        baseline_value=baseline_value,
        delta=", ".join(deltas) or None,
        details={"tolerance_db": tolerance},
    )


@register
class OpticalAlarmsCheck(Check):
    id = "interface_optics_alarms"
    title = "Opticke alarmy"
    label = "Interface optical alarms"
    mode = Mode.STATE
    requires = ("optics",)
    service_types = frozenset()
    layer1 = True
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        optics: dict[str, Any] = ctx.subject.get("optics", {})
        port = ctx.scope.selectors.interfaces[0]

        findings: list[Finding] = []
        for name in _ports(ctx):
            data = optics.get(name)
            if data is None:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{name}: rozhrani nevraci opticka data",
                        label=_optics_label(self.label, name, None, port),
                        value="bez optiky",
                    )
                )
                continue
            raised: list[tuple[int | None, str, Outcome]] = []
            for lane in data["lanes"]:
                for tag, is_on in lane["alarms"].items():
                    if is_on:
                        raised.append((lane["lane"], tag, Outcome.BROKEN))
                for tag, is_on in lane["warnings"].items():
                    if is_on:
                        raised.append((lane["lane"], tag, Outcome.DEGRADED))
            if not raised:
                findings.append(
                    Finding(
                        Outcome.OK,
                        f"{name}: bez optickych alarmu",
                        label=_optics_label(self.label, name, None, port),
                        value="bez alarmu",
                    )
                )
                continue
            for lane_no, tag, outcome in raised:
                findings.append(
                    Finding(
                        outcome,
                        f"{name}: {tag} je zvednuty",
                        label=_optics_label(self.label, name, lane_no, port),
                        value=tag,
                    )
                )
        return findings
```

`config.py` DEFAULTS: `"interface_optics_levels": {"tolerance_db": 2.0},`. Registracni import pridej tam, kde jsou ostatni checky (soubor, ktery importuje `checks.evpn` - over `migration_validator/checks/all.py`).

- [ ] **Step 4: Over, ze testy prochazi**

Run: `python -m pytest tests/checks/test_optics.py tests/ -q`
Expected: PASS cely balik (synteticke optiky v conftestu z Tasku 11 drzi e2e zelene).

- [ ] **Step 5: Commit**

```bash
git add migration_validator/checks/ migration_validator/config.py tests/checks/test_optics.py
git commit -m "feat: checky Interface optical levels a alarms na L1 scopech"
```

---

### Task 13: Konformance a registrace collectoru

**Files:**
- Test: `tests/collectors/test_conformance.py` (over, ze novy collector prochazi spolecnymi konformancnimi testy - registrace, rpc_names, platforms)
- Modify: pripadne `migration_validator/capture.py` nic - collectory se berou z registru (`collectors_for(platform)`), zadna zmena.

- [ ] **Step 1: Spust konformancni testy**

Run: `python -m pytest tests/collectors/test_conformance.py -v`
Expected: PASS - kdyz FAIL (napr. chybejici fixture konvence), dopln podle hlasky.

- [ ] **Step 2: Commit (jen kdyz vznikly zmeny)**

```bash
git add tests/ migration_validator/
git commit -m "test: konformance optics collectoru"
```

---

### Task 14: Zaverecna verifikace a lab

- [ ] **Step 1: Cely balik testu**

Run: `python -m pytest`
Expected: vse PASS (EVO optics fixture test SKIP, dokud neni nahrana).

- [ ] **Step 2: Lab overeni (vyzaduje pristup, viz memory o hesle)**

1. Regeneruj inventory parserem (`lag_members` v YAML, viz Task 6) a nahraj optics XML fixture (Task 11 krok 1) - pak `python -m pytest tests/collectors/test_optics.py -v` bez SKIPu.
2. Presnimej `runs/mig01` (schema 8) a spust `evaluate` - vizualne zkontroluj: L1 bloky s optikou, seskupeni po portech, vlan-aware blok bez cizich radku, neighbor adresy pod countem.
3. Overeni RPC jmena na PTX/EVO je soucast Tasku 11 - tady jen potvrd, ze capture bezi bez failed_collectors.

- [ ] **Step 3: Mutanty a docstringy**

Dle memory `mutant-nad-nekonzistentni-fixtures` a `tvrzeni-o-mutantovi-zastarava`: kdyz jsi menil fixtures, spust znovu mutanty, ktere se o ne opiraji, a over mutant-kill docstringy spustenim mutanta.

- [ ] **Step 4: Zaverecny commit / review**

```bash
git add -A && git status  # nic nesmi zbyt
python -m pytest -q
```

Pouzij superpowers:requesting-code-review pred uzavrenim vetve.
