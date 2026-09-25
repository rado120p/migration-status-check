# EVPN-VPWS local-switch + AC filter (E2) and matcher fall-through (E5) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the E-Line check see only the scope's own attachment circuit (AC), recognise local-switched EVPN-VPWS from the operational data, and stop the matcher from permanently dropping scopes that are ambiguous under one rule.

**Architecture:** The `evpn_vpws` collector gains two fields: `pseudowire_status` per AC, and `local_interface` per SID (the partner AC of a local switch). That changes the fact shape, so it is schema 14 → 15. `Scope.select` narrows each VPWS instance to the scope's own ACs. The check pairs baseline ACs by (local SID, remote SID) and gains two rows: PW status, and local switch. The matcher keeps ambiguous scopes in the pool for later rules. It builds the unmatched list at the end, and a scope keeps the "ambiguous" reason only while one of its rivals is also unpaired. In a step run, only `nova sluzba` scopes are excluded. `mapping.yml` selectors gain `routing_instance`.

**Tech Stack:** Python 3, pytest via `.venv/bin/python -m pytest`, lxml. JS tests via `node --test tests/js/*.test.js` (untouched by this wave, but must stay green).

**Spec:** `docs/superpowers/specs/2026-09-25-vpws-local-switch-a-matcher-design.md`

## Global Constraints

- `SCHEMA_VERSION = 15` (`migration_validator/models/snapshot.py`). The loader stays exact-match. No 14 → 15 shim.
- **No RPC and no RPC kwargs change.** The replay `call_key` must keep matching recorded bundles, or `mig-validate upgrade` breaks. Both new fields come from `get_evpn_vpws_information`, which is already collected.
- Existing row labels must not change, because they are row ids in the JSON output. The two new labels, verbatim:
  - `EVPN VPWS pseudowire status`
  - `EVPN VPWS SID remote local switch`
- Verbatim values:
  - PW-up value: `CCC-Up`
  - missing-AC row value: `Chybi`
  - shape-change baseline text: `local switch (<partner name> <partner status>)`
  - local-switch row value: `<partner name> <partner status>`
- The PW row is emitted **only** when `pseudowire_status` is not `None` (MX never reports it). No SKIP row.
- The unmatched reason strings stay as they are: `zadny kandidat na subject`, `nova sluzba, chybi baseline`, `ambiguous: N kandidatu (...)`.
- Code comments follow repo style: Czech, **no diacritics**. Docs under `docs/` keep diacritics.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Work on branch `vlna-e2-e5-2026-09-25`. Don't merge and don't push.
- Baseline before the wave: `2103 passed, 1 skipped`; JS `pass 88`.
- A docstring or comment that claims "mutant X is killed by test Y" must be verified by running the mutant before it is written or kept (memory: such claims go stale).

## Review Focus

1. **The real lab pair, MX → EVO, through collector + select + check.** For ge-0/0/2.211 → et-0/0/8.211, both ends are Up. Expected: no FAIL, a local switch row PASS with baseline `ge-0/0/2.212 Up`, and no `remote peer chybi`. Pinned in Task 4 (`test_vpws_local_switch_lab_captures_mx_to_evo`).
2. **Baseline local-switched, subject has no peer and no partner.** Today `same=True` turns this into UNCHANGED. It must be FAIL, with baseline `local switch (...)`. Pinned in Task 4.
3. **Ambiguity resolved by a later rule in a step run.** A foreign-wave VRF-b scope must stay excluded (reason `nova sluzba`), not show up in NESPAROVANO. Pinned in Task 5 (matcher) and Task 6 (engine).
4. **Remote PW names such as `ae0.224` / `ge-0/0/3.0` must survive the AC filter.** A filter bug would turn a healthy PW into BROKEN `ve vypisu instance chybi`. Pinned in Task 2, and checked on real data in Task 9.
5. **Unknown SIDs.** Two ACs whose SID values are `None` must never be paired by SID, because `(None, None)` is not an identity. Pinned in Task 3.

---

### Task 1: Collector fields + schema 15

**Files:**
- Create: `tests/fixtures/cases/evpn_vpws_local_switch_evo.xml`, `tests/fixtures/cases/evpn_vpws_local_switch_mx.xml`
- Modify: `migration_validator/collectors/evpn.py:28-89` (`EvpnVpwsCollector`)
- Modify: `migration_validator/models/snapshot.py:35` (+ comment block above it)
- Modify: `tests/collectors/test_evpn.py:22-67`
- Modify: `tests/models/test_snapshot.py:96`
- Modify: `tests/conftest.py:473-495` (E-Line builder)

**Interfaces:**
- Produces the per-AC dict: `{"name", "status", "mode", "pseudowire_status": str | None, "local_sid": SID, "remote_sid": SID}`.
- Produces `SID = {"value": int | None, "peers": list[peer], "local_interface": {"name": str, "status": str} | None}`.
- A partner status missing from the XML → `"unknown"`.

- [ ] **Step 1: Create the case fixtures from the lab captures**

The case fixtures must carry no default `xmlns`, because the collector uses un-namespaced `iter()` (see `tests/fixtures/cases/evpn_esi_two_instances.xml`). Strip it with sed and add a note to the header comment:

```bash
cd /home/rado/Desktop/scripts/migration-status-check
sed -E 's/ xmlns="[^"]*"//g' docs/superpowers/lab-captures/2026-09-25/ptx1-pop1_evpn_vpws_local_switch.xml \
  | sed -E '0,/-->/s/-->/\n     Kopie pro testy: odstraneny default namespace atributy (xmlns=...), jinak doslovne. -->/' \
  > tests/fixtures/cases/evpn_vpws_local_switch_evo.xml
sed -E 's/ xmlns="[^"]*"//g' docs/superpowers/lab-captures/2026-09-25/mx1-pop1_evpn_vpws_local_switch.xml \
  | sed -E '0,/-->/s/-->/\n     Kopie pro testy: odstraneny default namespace atributy (xmlns=...), jinak doslovne. -->/' \
  > tests/fixtures/cases/evpn_vpws_local_switch_mx.xml
head -5 tests/fixtures/cases/evpn_vpws_local_switch_evo.xml tests/fixtures/cases/evpn_vpws_local_switch_mx.xml
grep -c 'xmlns="' tests/fixtures/cases/evpn_vpws_local_switch_*.xml
```

Expected: both headers are valid XML comments, and the `grep -c` prints `0` for both files. Only `xmlns:junos` stays, on `<rpc-reply>`. Check that both parse:

```bash
.venv/bin/python -c "from lxml import etree; [etree.parse(f'tests/fixtures/cases/evpn_vpws_local_switch_{p}.xml') for p in ('evo','mx')]; print('ok')"
```

- [ ] **Step 2: Write the failing collector tests**

In `tests/collectors/test_evpn.py`, update `test_vpws_instances_carry_interface_list` (line 23). The key sets become:

```python
            assert set(iface) == {
                "name", "status", "mode", "pseudowire_status", "local_sid", "remote_sid",
            }
            for sid in (iface["local_sid"], iface["remote_sid"]):
                assert set(sid) == {"value", "peers", "local_interface"}
```

Update the two asserts at the end of `test_vpws_empty_pe_table_gives_empty_peers`:

```python
    assert iface["local_sid"] == {"value": 1000, "peers": [], "local_interface": None}
    assert iface["remote_sid"] == {"value": 2000, "peers": [], "local_interface": None}
    assert iface["pseudowire_status"] is None
```

Add these tests after `test_vpws_empty_pe_table_gives_empty_peers`:

```python
def test_vpws_local_switch_evo_carries_partner_and_pw_status():
    """PTX1-POP1 2026-09-25: lokalne prepnuty EVPN-VPWS - remote SID misto PE
    nese partnersky AC, pseudowire-status je CCC-Up."""
    result = EvpnVpwsCollector().parse(
        _case("evpn_vpws_local_switch_evo.xml"), "junos-evo"
    )
    acs = result["EVPN-VPWS-LOCAL"]["interfaces"]
    assert [ac["name"] for ac in acs] == ["et-0/0/8.211", "et-0/0/8.212"]
    assert [ac["pseudowire_status"] for ac in acs] == ["CCC-Up", "CCC-Up"]
    assert acs[0]["local_sid"] == {"value": 100, "peers": [], "local_interface": None}
    assert acs[0]["remote_sid"] == {
        "value": 200,
        "peers": [],
        "local_interface": {"name": "et-0/0/8.212", "status": "Up"},
    }
    assert acs[1]["remote_sid"]["local_interface"] == {"name": "et-0/0/8.211", "status": "Up"}


def test_vpws_local_switch_mx_has_partner_but_no_pw_status():
    """MX1-POP1 2026-09-25: partner stejne jako EVO, pseudowire-status MX
    nevypisuje vubec."""
    result = EvpnVpwsCollector().parse(_case("evpn_vpws_local_switch_mx.xml"), "junos")
    acs = result["EVPN-VPWS-LOCAL"]["interfaces"]
    assert [ac["name"] for ac in acs] == ["ge-0/0/2.211", "ge-0/0/2.212"]
    assert [ac["pseudowire_status"] for ac in acs] == [None, None]
    assert acs[0]["remote_sid"]["local_interface"] == {"name": "ge-0/0/2.212", "status": "Up"}
    assert acs[1]["remote_sid"]["local_interface"] == {"name": "ge-0/0/2.211", "status": "Up"}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_vpws_remote_pw_has_no_local_interface(rpc_fixture, platform):
    result = EvpnVpwsCollector().parse(rpc_fixture(platform, "evpn_vpws"), platform)
    for data in result.values():
        for ac in data["interfaces"]:
            assert ac["remote_sid"]["local_interface"] is None
            assert ac["local_sid"]["local_interface"] is None
            expected_pw = "CCC-Up" if platform == "junos-evo" else None
            assert ac["pseudowire_status"] == expected_pw


def test_vpws_partner_without_status_is_unknown():
    xml = etree.fromstring(
        """<evpn-vpws-information><evpn-vpws-instance>
        <evpn-vpws-instance-name>X</evpn-vpws-instance-name>
        <evpn-vpws-interface-status-table><evpn-vpws-interface>
          <evpn-vpws-interface-name>ge-0/0/2.211</evpn-vpws-interface-name>
          <evpn-vpws-interface-status>Up</evpn-vpws-interface-status>
          <evpn-vpws-service-id-remote-status-table><evpn-vpws-sid-remote>
            <evpn-vpws-sid-remote-value>200</evpn-vpws-sid-remote-value>
            <evpn-vpws-sid-local-interface-name>ge-0/0/2.212</evpn-vpws-sid-local-interface-name>
            <evpn-vpws-sid-pe-status-table/>
          </evpn-vpws-sid-remote></evpn-vpws-service-id-remote-status-table>
        </evpn-vpws-interface></evpn-vpws-interface-status-table>
        </evpn-vpws-instance></evpn-vpws-information>"""
    )
    iface = EvpnVpwsCollector().parse(xml, "junos")["X"]["interfaces"][0]
    assert iface["remote_sid"]["local_interface"] == {"name": "ge-0/0/2.212", "status": "unknown"}
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `.venv/bin/python -m pytest tests/collectors/test_evpn.py -q -p no:warnings`
Expected: FAIL. There are key-set mismatches and a `KeyError: 'pseudowire_status'` / `'local_interface'`.

- [ ] **Step 4: Implement the collector**

In `migration_validator/collectors/evpn.py`, update the `EvpnVpwsCollector` docstring by appending:

```
    Schema 15 (spec 2026-09-25 vpws-local-switch-a-matcher): kazde AC nese
    pseudowire_status (jen EVO - MX element nevypisuje ani u vzdaleneho PW,
    None = nezmereno) a kazdy SID local_interface - partnersky AC lokalne
    prepnuteho EVPN-VPWS, ktery Junos v remote SID vypisuje misto PE.
```

In `_interface`, add after `"mode"`:

```python
            "pseudowire_status": _text(iface, "evpn-vpws-pseudowire-status"),
```

Replace `_sid` with:

```python
    @staticmethod
    def _sid(iface: etree._Element, path: str, value_tag: str) -> dict[str, Any]:
        sid = iface.find(path)
        if sid is None:
            return {"value": None, "peers": [], "local_interface": None}
        peers = [
            {
                "esi": _text(peer, "evpn-vpws-sid-interface-esi"),
                "ipaddr": _text(peer, "evpn-vpws-sid-pe-ipaddr"),
                "mode": _text(peer, "evpn-vpws-sid-pe-mode"),
                "role": _text(peer, "evpn-vpws-sid-pe-role"),
                "status": _text(peer, "evpn-vpws-sid-pe-status"),
            }
            for peer in sid.iter("evpn-vpws-sid-pe-info")
        ]
        return {
            "value": _int(sid, value_tag),
            "peers": peers,
            "local_interface": _local_interface(sid),
        }
```

Add a module-level helper right above `@register class EvpnVpwsCollector`:

```python
def _local_interface(sid: etree._Element) -> dict[str, str] | None:
    """Partnersky AC lokalne prepnuteho EVPN-VPWS, jinak None.

    Remote SID blok lokalne prepnute instance ma prazdnou
    sid-pe-status-table a misto PE nese jmeno a stav AC na tomtez boxu -
    MX 24.2 i EVO 25.2 stejne (lab 2026-09-25,
    docs/superpowers/lab-captures/2026-09-25/).
    """
    name = _text(sid, "evpn-vpws-sid-local-interface-name")
    if not name:
        return None
    return {
        "name": name,
        "status": _text(sid, "evpn-vpws-sid-local-interface-status") or "unknown",
    }
```

- [ ] **Step 5: Bump the schema**

In `migration_validator/models/snapshot.py`, add below the `# 14:` comment block:

```python
# 15: evpn_vpws AC nese pseudowire_status (jen EVO) a kazdy SID
#     local_interface - partnersky AC lokalne prepnuteho EVPN-VPWS
#     (spec 2026-09-25 vpws-local-switch-a-matcher).
SCHEMA_VERSION = 15
```

In `tests/models/test_snapshot.py:96`, change it to `assert SCHEMA_VERSION == 15`.

- [ ] **Step 6: Update the conftest E-Line builder to the new shape**

In `tests/conftest.py` (E-Line block around line 473), add `"pseudowire_status": None,` after `"mode": "single-homed",`. Add `"local_interface": None` to both SID dicts, so they become `{"value": 1000, "peers": [], "local_interface": None}` and `{"value": 2000, "peers": [...], "local_interface": None}`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: all pass. The count is 2103 + 5 new = `2108 passed, 1 skipped`. If any test still compares a hard-coded schema number 14, fix it to use `SCHEMA_VERSION` or 15.

- [ ] **Step 8: Commit**

```bash
git add migration_validator/collectors/evpn.py migration_validator/models/snapshot.py \
  tests/collectors/test_evpn.py tests/models/test_snapshot.py tests/conftest.py \
  tests/fixtures/cases/evpn_vpws_local_switch_evo.xml tests/fixtures/cases/evpn_vpws_local_switch_mx.xml
git commit -m "feat(evpn): collect VPWS local-switch partner and pseudowire status (schema 15)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `Scope.select` keeps only the scope's own ACs (E2)

**Files:**
- Modify: `migration_validator/models/scope.py:325-329`
- Modify: `tests/conftest.py:473` (builder appends instead of overwriting)
- Test: `tests/models/test_scope.py`, `tests/parsers/test_vpws_local_switch.py` (create)

**Interfaces:**
- Consumes the AC dict from Task 1.
- Produces the per-scope view `evpn_vpws: {instance: {**instance_data, "interfaces": [own ACs]}}`. An instance of the scope whose AC is absent stays in the view with `"interfaces": []`. An instance absent from the facts is not in the view.

- [ ] **Step 1: Write the failing select tests**

Append to `tests/models/test_scope.py`:

```python
def _eline(iface, ri="EVPN-VPWS-LOCAL"):
    return Scope(
        id=f"svc:{iface}", kind="service",
        key=ScopeKey(description=iface, service_type="E-Line", service_subtype="vpws"),
        selectors=Selectors(interfaces=[iface], physical_interfaces=["et-0/0/8"],
                            routing_instances=[ri]),
    )


def _ac(name, status="Up"):
    return {
        "name": name, "status": status, "mode": "single-homed", "pseudowire_status": None,
        "local_sid": {"value": 100, "peers": [], "local_interface": None},
        "remote_sid": {"value": 200, "peers": [], "local_interface": None},
    }


def _vpws_facts():
    return {"evpn_vpws": {"EVPN-VPWS-LOCAL": {"interfaces": [
        _ac("et-0/0/8.211"), _ac("et-0/0/8.212", status="Down"),
    ]}}}


def test_select_vpws_keeps_only_own_ac():
    """Review E2: scope .211 nesmi videt AC .212 tehoz VPWS instance -
    Down .212 by jinak rozsvitil FAIL na zdrave sluzbe."""
    facts = _vpws_facts()
    for iface in ("et-0/0/8.211", "et-0/0/8.212"):
        acs = _eline(iface).select(facts)["evpn_vpws"]["EVPN-VPWS-LOCAL"]["interfaces"]
        assert [ac["name"] for ac in acs] == [iface]
    # physical_interfaces ["et-0/0/8"] nesmi vybrat unity
    assert len(facts["evpn_vpws"]["EVPN-VPWS-LOCAL"]["interfaces"]) == 2, "select zmenil fakta"


def test_select_vpws_scope_whose_ac_is_missing_gets_empty_list():
    selected = _eline("et-0/0/8.213").select(_vpws_facts())
    assert selected["evpn_vpws"] == {"EVPN-VPWS-LOCAL": {"interfaces": []}}


def test_select_vpws_real_remote_pw_names_survive_filter():
    """ae0.224 / ge-0/0/3.0 z labu: filtr podle jmena IFL musi sedet."""
    for iface in ("ae0.224", "ge-0/0/3.0"):
        scope = Scope(
            id=f"svc:{iface}", kind="service",
            key=ScopeKey(description=iface, service_type="E-Line", service_subtype="vpws"),
            selectors=Selectors(interfaces=[iface], physical_interfaces=[iface.split(".")[0]],
                                routing_instances=["EVPN-VPWS-CPE23-UNI"]),
        )
        facts = {"evpn_vpws": {"EVPN-VPWS-CPE23-UNI": {"interfaces": [_ac(iface)]}}}
        acs = scope.select(facts)["evpn_vpws"]["EVPN-VPWS-CPE23-UNI"]["interfaces"]
        assert [ac["name"] for ac in acs] == [iface]


def test_select_vpws_other_instance_not_selected():
    assert _eline("et-0/0/8.211", ri="OTHER").select(_vpws_facts())["evpn_vpws"] == {}
```

- [ ] **Step 2: Write the parser guard test**

Create `tests/parsers/test_vpws_local_switch.py`:

```python
"""Lokalne prepnuty EVPN-VPWS (lab 2026-09-25): obe AC jedne evpn-vpws
instance jsou dve E-Line sluzby ve stejne RI. Inventory tedy da dva scopy
a E2 filtr v Scope.select je musi rozdelit - bez subtypu local-switch
(spec 2026-09-25 vpws-local-switch-a-matcher).
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.parsers.evo import JunosEvoAcxServiceParser
from migration_validator.parsers.mx import JunosServiceParser

PARSERS = (
    pytest.param(JunosEvoAcxServiceParser, id="evo"),
    pytest.param(JunosServiceParser, id="mx"),
)

CONFIG = """
<configuration>
  <interfaces>
    <interface>
      <name>et-0/0/8</name>
      <flexible-vlan-tagging/>
      <encapsulation>flexible-ethernet-services</encapsulation>
      <unit><name>211</name><encapsulation>vlan-ccc</encapsulation><vlan-id>211</vlan-id></unit>
      <unit><name>212</name><encapsulation>vlan-ccc</encapsulation><vlan-id>212</vlan-id></unit>
    </interface>
  </interfaces>
  <routing-instances>
    <instance>
      <name>EVPN-VPWS-LOCAL</name>
      <instance-type>evpn-vpws</instance-type>
      <protocols><evpn>
        <interface><name>et-0/0/8.211</name>
          <vpws-service-id><local>100</local><remote>200</remote></vpws-service-id></interface>
        <interface><name>et-0/0/8.212</name>
          <vpws-service-id><local>200</local><remote>100</remote></vpws-service-id></interface>
      </evpn></protocols>
      <interface><name>et-0/0/8.211</name></interface>
      <interface><name>et-0/0/8.212</name></interface>
      <route-distinguisher><rd-type>65000:211</rd-type></route-distinguisher>
      <vrf-target><community>target:65000:211</community></vrf-target>
    </instance>
  </routing-instances>
</configuration>
"""


@pytest.mark.parametrize("parser_class", PARSERS)
def test_local_switched_vpws_gives_two_eline_services(parser_class):
    services = parser_class(etree.fromstring(CONFIG)).parse()
    eline = {s.interface: s for s in services if s.service_type == "E-Line"}
    assert set(eline) == {"et-0/0/8.211", "et-0/0/8.212"}
    assert {s.service_subtype for s in eline.values()} == {"vpws"}
    assert {s.routing_instance for s in eline.values()} == {"EVPN-VPWS-LOCAL"}
```

This test passes already (verified while writing the plan). It guards the assumption behind the E2 filter.

- [ ] **Step 3: Run the select tests and confirm they fail**

Run: `.venv/bin/python -m pytest tests/models/test_scope.py tests/parsers/test_vpws_local_switch.py -q -p no:warnings`
Expected: `test_select_vpws_keeps_only_own_ac` and `test_select_vpws_scope_whose_ac_is_missing_gets_empty_list` FAIL, because the whole instance is selected. The parser tests PASS.

- [ ] **Step 4: Implement the filter**

In `migration_validator/models/scope.py`, replace the `evpn_vpws = {...}` comprehension (around line 325) with:

```python
        # E2 (review 2026-09-24): VPWS fakta jsou klicovana instanci, ale
        # scope je per AC - lokalne prepnuty EVPN-VPWS ma v jedne instanci
        # obe AC (dve sluzby). Scope vidi jen sva AC; instance, ve ktere
        # jeho AC ve vypisu chybi, zustava s prazdnym seznamem, aby check
        # rozlisil "AC chybi" (BROKEN) od "instance chybi" (SKIP).
        evpn_vpws = {
            name: {
                **data,
                "interfaces": [
                    ac
                    for ac in data.get("interfaces") or []
                    if self.selectors.matches_interface(str(ac.get("name") or ""))
                ],
            }
            for name, data in (facts.get("evpn_vpws") or {}).items()
            if name in self.selectors.routing_instances
        }
```

- [ ] **Step 5: Make the conftest builder append ACs**

Two E-Line scopes sharing an instance would overwrite each other in the conftest builder. With the filter in place, the first scope would then get an empty list and report BROKEN. In `tests/conftest.py` (E-Line block), replace `evpn_vpws[instance] = {"interfaces": [ {AC} ]}` with an append:

```python
            evpn_vpws.setdefault(instance, {"interfaces": []})["interfaces"].append(
                {
                    ...the same AC dict as before...
                }
            )
```

Keep the AC dict content identical to Task 1's version.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: all pass (`2114 passed, 1 skipped`: 2108 + 4 select + 2 parser). Pay particular attention to `tests/collectors/test_conformance.py::test_vpws_check_really_reads_the_sid_pe_status_table` and `test_specific_check_sees_data[junos-evo-evpn_vpws_status]`. They run real inventory (`ae0.224`, `et-0/0/8.213`) through the filter.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/models/scope.py tests/models/test_scope.py \
  tests/parsers/test_vpws_local_switch.py tests/conftest.py
git commit -m "fix(scope): VPWS view keeps only the scope's own AC (E2)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Check — SID pairing, missing AC, PW status row

**Files:**
- Modify: `migration_validator/checks/evpn.py:35-136` (`_find_baseline_peer` docstring, `EvpnVpwsStatusCheck` docstring, `run`, `_interface_findings`)
- Test: `tests/checks/test_evpn.py:42-57` (helpers) and new tests after line 318

**Interfaces:**
- Consumes the per-scope view from Task 2. The check still accepts any number of ACs per instance, because check tests pass facts directly.
- Produces `_pair_baseline_acs(acs: list[dict], baseline_acs: list[dict]) -> list[dict | None]` (module level, same length as `acs`), and the constant `PW_UP = "CCC-Up"`.

- [ ] **Step 1: Extend the test helpers**

In `tests/checks/test_evpn.py`, replace `_vpws_subject` (line 42) with:

```python
def _vpws_ac(*, name="ge-0/0/2.213", status="Up", mode="single-homed", local_value=1000,
             remote_value=2000, local_peers=(), remote_peers=(), partner=None, pw=None):
    return {
        "name": name, "status": status, "mode": mode, "pseudowire_status": pw,
        "local_sid": {"value": local_value, "peers": list(local_peers), "local_interface": None},
        "remote_sid": {"value": remote_value, "peers": list(remote_peers),
                       "local_interface": partner},
    }


def _vpws_facts(*acs, instance="EVPN-VPWS-X"):
    return {"evpn_vpws": {instance: {"interfaces": list(acs)}}}


def _vpws_subject(*, status="Up", mode="single-homed", iface_name="ge-0/0/2.213",
                  local_peers=(), remote_peers=(), remote_value=2000,
                  instance="EVPN-VPWS-X", pw=None, partner=None):
    return _vpws_facts(
        _vpws_ac(name=iface_name, status=status, mode=mode, remote_value=remote_value,
                 local_peers=local_peers, remote_peers=remote_peers, pw=pw, partner=partner),
        instance=instance,
    )
```

Rename `test_vpws_baseline_matches_by_position_despite_renamed_interface` (line 198) to `test_vpws_baseline_pairs_by_sid_despite_renamed_interface`. Replace its comment with `# Jmeno rozhrani se migraci meni (ge-0/0/3.0 -> ae0.224), SID ne.` The body stays as it is.

- [ ] **Step 2: Write the failing tests**

Append after `test_vpws_missing_remote_peer_in_both_is_unchanged_with_sentinel_baseline`:

```python
def test_vpws_baseline_pairs_by_sid_not_position():
    """Review E2: baseline AC v jinem poradi - parovani podle (local, remote) SID."""
    a = _vpws_ac(name="ge-0/0/2.100", local_value=100, remote_value=200, remote_peers=[PEER_OK])
    b = _vpws_ac(name="ge-0/0/2.101", local_value=101, remote_value=201, status="Down",
                 remote_peers=[PEER_OK])
    subject = _vpws_facts({**a, "name": "et-0/0/8.100"}, {**b, "name": "et-0/0/8.101", "status": "Up"})
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, _vpws_facts(b, a)))
    assert _by_label(findings, "EVPN VPWS local interface status (et-0/0/8.100)").baseline_value == "Up"
    assert _by_label(findings, "EVPN VPWS local interface status (et-0/0/8.101)").baseline_value == "Down"


def test_vpws_down_ac_never_unchanged_against_other_acs_baseline():
    """Pozicne by Down .100 dostal baseline Down .101 -> falesne UNCHANGED."""
    base_a = _vpws_ac(name="ge-0/0/2.100", local_value=100, remote_value=200, remote_peers=[PEER_OK])
    base_b = _vpws_ac(name="ge-0/0/2.101", local_value=101, remote_value=201, status="Down",
                      remote_peers=[PEER_OK])
    subject = _vpws_facts(
        _vpws_ac(name="et-0/0/8.100", local_value=100, remote_value=200, status="Down",
                 remote_peers=[PEER_OK]),
        _vpws_ac(name="et-0/0/8.101", local_value=101, remote_value=201, status="Down",
                 remote_peers=[PEER_OK]),
    )
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=_vpws_facts(base_b, base_a)))
    assert _by_label(rows, "EVPN VPWS local interface status (et-0/0/8.100)").status is Status.FAIL
    assert _by_label(rows, "EVPN VPWS local interface status (et-0/0/8.101)").status is Status.PASS


def test_vpws_single_ac_with_changed_sid_still_pairs():
    baseline = _vpws_subject(remote_peers=[PEER_OK], remote_value=2000)
    subject = _vpws_subject(remote_peers=[PEER_OK], remote_value=2001, iface_name="et-0/0/8.213")
    row = _by_label(EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline)),
                    "EVPN VPWS SID remote value")
    assert row.value == "SID 2001" and row.baseline_value == "SID 2000"


def test_vpws_two_acs_without_sid_match_get_no_baseline():
    baseline = _vpws_facts(_vpws_ac(name="a.1", local_value=1, remote_value=2),
                           _vpws_ac(name="a.2", local_value=3, remote_value=4))
    subject = _vpws_facts(_vpws_ac(name="b.1", local_value=5, remote_value=6),
                          _vpws_ac(name="b.2", local_value=7, remote_value=8))
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline))
    assert _by_label(findings, "EVPN VPWS local interface status (b.1)").baseline_value is None
    assert _by_label(findings, "EVPN VPWS local interface status (b.2)").baseline_value is None


def test_vpws_unknown_sids_never_pair_by_sid():
    """(None, None) neni identita - dve AC bez SID se podle SID neparuji."""
    baseline = _vpws_facts(_vpws_ac(name="a.1", local_value=None, remote_value=None),
                           _vpws_ac(name="a.2", local_value=None, remote_value=None, status="Down"))
    subject = _vpws_facts(_vpws_ac(name="b.1", local_value=None, remote_value=None),
                          _vpws_ac(name="b.2", local_value=None, remote_value=None))
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline))
    for iface in ("b.1", "b.2"):
        assert _by_label(findings, f"EVPN VPWS local interface status ({iface})").baseline_value is None


def test_vpws_ac_missing_from_instance_output_is_broken():
    """Instance je, AC scopu ve vypisu neni -> BROKEN, ne SKIP."""
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(_vpws_facts()))
    row = _by_label(findings, "EVPN VPWS local interface status")
    assert row.outcome is Outcome.BROKEN
    assert row.value == "Chybi"
    assert row.message == "EVPN-VPWS-X: AC ge-0/0/2.313 ve vypisu instance chybi"


def test_vpws_ac_missing_in_both_is_unchanged():
    empty = _vpws_facts()
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(empty, baseline=empty))
    row = _by_label(rows, "EVPN VPWS local interface status")
    assert row.status is Status.PASS and row.baseline_value == "Chybi"


def test_vpws_ac_missing_but_present_in_baseline_shows_previous_status():
    findings = EvpnVpwsStatusCheck().run(
        _vpws_ctx(_vpws_facts(), _vpws_subject(remote_peers=[PEER_OK]))
    )
    row = _by_label(findings, "EVPN VPWS local interface status")
    assert row.outcome is Outcome.BROKEN and row.baseline_value == "Up"


def test_vpws_pw_status_ccc_up_is_ok_and_follows_interface_row():
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK], pw="CCC-Up"))
    assert [f.label for f in findings[:2]] == [
        "EVPN VPWS local interface status", "EVPN VPWS pseudowire status",
    ]
    row = _by_label(findings, "EVPN VPWS pseudowire status")
    assert row.outcome is Outcome.OK and row.value == "CCC-Up"


def test_vpws_pw_status_other_than_ccc_up_is_broken():
    row = _by_label(run_findings(_vpws_subject(remote_peers=[PEER_OK], pw="CCC-Down")),
                    "EVPN VPWS pseudowire status")
    assert row.outcome is Outcome.BROKEN
    assert row.message == "EVPN-VPWS-X: pseudowire CCC-Down, ocekavano CCC-Up"


def test_vpws_pw_status_absent_emits_no_row():
    """MX pseudowire-status nevypisuje - zadny radek, ani SKIP."""
    findings = run_findings(_vpws_subject(remote_peers=[PEER_OK]))
    assert not any(f.label == "EVPN VPWS pseudowire status" for f in findings)


def test_vpws_pw_status_against_mx_baseline_has_no_baseline_value():
    baseline = _vpws_subject(remote_peers=[PEER_OK])
    subject = _vpws_subject(remote_peers=[PEER_OK], pw="CCC-Down", iface_name="et-0/0/8.213")
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=baseline))
    row = _by_label(rows, "EVPN VPWS pseudowire status")
    assert row.status is Status.FAIL and row.baseline_value is None


def test_vpws_pw_status_same_broken_in_both_is_unchanged():
    subject = _vpws_subject(remote_peers=[PEER_OK], pw="CCC-Down")
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=subject))
    assert _by_label(rows, "EVPN VPWS pseudowire status").status is Status.PASS


def test_vpws_review_case_down_ac_does_not_leak_into_other_scope():
    """Review E2 end to end: .101 Down, scope .100 zustane cisty."""
    facts = _vpws_facts(
        _vpws_ac(name="ge-0/0/1.100", local_value=100, remote_value=200, remote_peers=[PEER_OK]),
        _vpws_ac(name="ge-0/0/1.101", local_value=101, remote_value=201, status="Down",
                 remote_peers=[PEER_OK]),
        instance="VPWS",
    )
    scope = Scope(
        id="svc:ge-0/0/1.100", kind="service",
        key=ScopeKey("ge-0/0/1.100", "E-Line", "vpws"),
        selectors=Selectors(interfaces=["ge-0/0/1.100"], physical_interfaces=["ge-0/0/1"],
                            routing_instances=["VPWS"]),
    )
    ctx = CheckContext(scope=scope, subject=scope.select(facts), baseline=None,
                       config=default_config(), failed_collectors={}, baseline_collectors={})
    rows = run_check(EvpnVpwsStatusCheck(), ctx)
    assert all(row.status is not Status.FAIL for row in rows)
    assert not any("ge-0/0/1.101" in (row.label or "") for row in rows)
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `.venv/bin/python -m pytest tests/checks/test_evpn.py -q -p no:warnings -k vpws`
Expected: the new tests FAIL. Pairing is positional, there's no `Chybi` row, and there's no PW row. `test_vpws_review_case_down_ac_does_not_leak_into_other_scope` already passes thanks to Task 2, and that's fine.

- [ ] **Step 4: Implement**

In `migration_validator/checks/evpn.py`:

(a) Below `UP = "Up"`, add `PW_UP = "CCC-Up"`.

(b) In `_find_baseline_peer`'s docstring, replace `v jiz pozicne sparovanem SID` with `v SID baseline AC sparovaneho podle (local, remote) SID`.

(c) Below `_find_baseline_peer`, add:

```python
def _sid_key(ac: dict[str, Any]) -> tuple[Any, Any] | None:
    local = (ac.get("local_sid") or {}).get("value")
    remote = (ac.get("remote_sid") or {}).get("value")
    if local is None or remote is None:
        return None
    return (local, remote)


def _pair_baseline_acs(
    acs: list[dict[str, Any]], baseline_acs: list[dict[str, Any]]
) -> list[dict[str, Any] | None]:
    """Baseline AC ke kazdemu AC subjektu podle (local SID, remote SID).

    Jmeno IFL se migraci meni (ge-0/0/2.211 -> et-0/0/8.211), SID ne.
    Drivejsi pozicni parovani dalo pri jinem poradi/poctu AC cizi baseline
    a Down AC mohlo vyjit UNCHANGED (review 2026-09-24, E2). Dvojice je
    jednoznacna i u zrcadlenych SID lokalniho prepnuti (100/200 vs
    200/100). Kdyz SID nesedi a obe strany maji prave jedno AC, sparuji se
    ta dve - zmenu SID ukaze ZMENA na radku SID value. Jinak AC baseline
    nema. Neznamy SID (None) neni identita a podle SID se neparuje.
    """
    by_key: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for ac in baseline_acs:
        key = _sid_key(ac)
        if key is not None:
            by_key.setdefault(key, []).append(ac)
    paired: list[dict[str, Any] | None] = []
    for ac in acs:
        key = _sid_key(ac)
        hits = by_key.get(key, []) if key is not None else []
        paired.append(hits[0] if len(hits) == 1 else None)
    if len(acs) == 1 and len(baseline_acs) == 1 and paired[0] is None:
        paired[0] = baseline_acs[0]
    return paired
```

(d) In `EvpnVpwsStatusCheck`'s docstring, replace the sentence `baseline_value se dopocitava pozicnim parovanim rozhrani a peeru podle ipaddr (viz _find_baseline_peer)` with `baseline_value se dopocitava parovanim AC podle (local, remote) SID (_pair_baseline_acs) a peeru podle ipaddr (_find_baseline_peer)`.

(e) Replace the loop in `run` (from `findings: list[Finding] = []` to `return findings`) with:

```python
        findings: list[Finding] = []
        for name in sorted(instances):
            interfaces = instances[name].get("interfaces", [])
            baseline_instance = baseline_instances.get(name)
            baseline_interfaces = (baseline_instance or {}).get("interfaces", [])
            if not interfaces:
                findings.append(self._missing_ac_finding(name, baseline_instance, ctx))
                continue
            many = len(interfaces) > 1
            paired = _pair_baseline_acs(interfaces, baseline_interfaces)
            for iface, baseline_iface in zip(interfaces, paired):
                findings.extend(
                    self._interface_findings(name, iface, baseline_iface, many, ctx)
                )
        return findings

    def _missing_ac_finding(
        self,
        instance: str,
        baseline_instance: dict[str, Any] | None,
        ctx: CheckContext,
    ) -> Finding:
        # Scope.select zuzil instanci na AC scopu (E2) a zadne nezbylo:
        # inventory rika, ze AC v instanci je, operacni vypis ho nezna.
        # BROKEN, ne SKIP - data instance mame, jen v nich AC chybi.
        acs = ", ".join(ctx.scope.selectors.interfaces) or "?"
        baseline_acs = (baseline_instance or {}).get("interfaces", [])
        same = baseline_instance is not None and not baseline_acs
        outcome = unchanged_or(Outcome.BROKEN, ctx, "evpn_vpws", same=same)
        if same:
            baseline_value = "Chybi"
        elif len(baseline_acs) == 1:
            baseline_value = str(baseline_acs[0].get("status", UNKNOWN))
        else:
            baseline_value = None
        return Finding(
            outcome,
            f"{instance}: AC {acs} ve vypisu instance chybi" + suffix(outcome),
            label="EVPN VPWS local interface status",
            value="Chybi",
            baseline_value=baseline_value,
        )
```

Delete the old positional-pairing comment (`# Pozicni parovani rozhrani: ...`) along with the loop it described.

(f) In `_interface_findings`, right after the `findings.append(...)` of the local interface status row, before the two `_sid_findings` calls:

```python
        pw = iface.get("pseudowire_status")
        if pw is not None:
            findings.append(self._pw_finding(instance, pw, baseline_iface, label, ctx))
```

Add the method after `_interface_findings`:

```python
    def _pw_finding(
        self,
        instance: str,
        pw: str,
        baseline_iface: dict[str, Any] | None,
        label,
        ctx: CheckContext,
    ) -> Finding:
        # Radek jen kdyz Junos element vypsal (EVO). MX ho nema ani
        # u vzdaleneho PW - zadny radek misto SKIP, stav se nefabuluje.
        baseline_pw = (
            baseline_iface.get("pseudowire_status") if baseline_iface is not None else None
        )
        up = pw == PW_UP
        outcome = (
            Outcome.OK if up
            else unchanged_or(Outcome.BROKEN, ctx, "evpn_vpws", same=baseline_pw == pw)
        )
        return Finding(
            outcome,
            f"{instance}: pseudowire {pw}"
            + ("" if up else f", ocekavano {PW_UP}")
            + suffix(outcome),
            label=label("EVPN VPWS pseudowire status"),
            value=pw,
            baseline_value=baseline_pw,
        )
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/checks/test_evpn.py -q -p no:warnings`
Expected: all pass.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: all pass (2114 + 14 = `2128 passed, 1 skipped`). The conformance test on junos-evo now also produces a PW row (`CCC-Up`, OK) for the fixture services, which is expected.

- [ ] **Step 7: Commit**

```bash
git add migration_validator/checks/evpn.py tests/checks/test_evpn.py
git commit -m "fix(evpn): pair VPWS baseline AC by SID, report missing AC and PW status

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Check — local switch row and shape changes

**Files:**
- Modify: `migration_validator/checks/evpn.py` (`_sid_findings`, plus a new `_local_switch_finding` and a module helper `_local_switch_text`)
- Test: `tests/checks/test_evpn.py`

**Interfaces:**
- Consumes `remote_sid["local_interface"]` (Task 1), `_vpws_ac` / `_vpws_facts` (Task 3 test helpers), and `Scope.select` (Task 2).
- Produces the row `EVPN VPWS SID remote local switch`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/checks/test_evpn.py`:

```python
PARTNER_UP = {"name": "et-0/0/8.212", "status": "Up"}
BASE_PARTNER_UP = {"name": "ge-0/0/2.212", "status": "Up"}
REMOTE_PE_LABELS = ("EVPN VPWS SID remote PE", "EVPN VPWS SID remote status")


def test_vpws_local_switch_row_replaces_pe_rows():
    findings = run_findings(_vpws_subject(partner=PARTNER_UP, pw="CCC-Up"))
    row = _by_label(findings, "EVPN VPWS SID remote local switch")
    assert row.outcome is Outcome.OK
    assert row.value == "et-0/0/8.212 Up"
    assert row.message == "EVPN-VPWS-X: lokalni prepnuti na et-0/0/8.212, stav Up"
    assert not any(f.label in REMOTE_PE_LABELS for f in findings)


def test_vpws_local_switch_partner_down_is_broken():
    findings = run_findings(_vpws_subject(partner={"name": "et-0/0/8.212", "status": "Down"}))
    row = _by_label(findings, "EVPN VPWS SID remote local switch")
    assert row.outcome is Outcome.BROKEN
    assert row.message == "EVPN-VPWS-X: lokalni prepnuti na et-0/0/8.212, stav Down, ocekavano Up"


def test_vpws_local_switch_partner_down_in_both_is_unchanged_despite_rename():
    baseline = _vpws_subject(partner={"name": "ge-0/0/2.212", "status": "Down"})
    subject = _vpws_subject(partner={"name": "et-0/0/8.212", "status": "Down"})
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=baseline))
    row = _by_label(rows, "EVPN VPWS SID remote local switch")
    assert row.status is Status.PASS and row.details[UNCHANGED_SINCE_BASELINE] is True
    assert row.baseline_value == "ge-0/0/2.212 Down"


def test_vpws_local_switch_partner_down_but_up_in_baseline_fails():
    baseline = _vpws_subject(partner=BASE_PARTNER_UP)
    subject = _vpws_subject(partner={"name": "et-0/0/8.212", "status": "Down"})
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=baseline))
    assert _by_label(rows, "EVPN VPWS SID remote local switch").status is Status.FAIL


def test_vpws_local_switch_partner_unknown_in_both_stays_fail():
    unknown = {"name": "ge-0/0/2.212", "status": "unknown"}
    subject = _vpws_subject(partner=unknown)
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=subject))
    assert _by_label(rows, "EVPN VPWS SID remote local switch").status is Status.FAIL


def test_vpws_local_switch_in_baseline_remote_pw_in_subject_shows_shape_change():
    """Step presunul jedno AC local-switch paru: na novem boxu je vzdaleny PW."""
    baseline = _vpws_subject(iface_name="ge-0/0/2.211", partner=BASE_PARTNER_UP)
    subject = _vpws_subject(iface_name="et-0/0/8.211", remote_peers=[PEER_OK])
    findings = EvpnVpwsStatusCheck().run(_vpws_ctx(subject, baseline))
    for label in REMOTE_PE_LABELS:
        assert _by_label(findings, label).baseline_value == "local switch (ge-0/0/2.212 Up)"


def test_vpws_local_switch_baseline_and_subject_without_peer_is_broken_not_unchanged():
    """Dnes: baseline bez peeru -> same=True -> UNCHANGED. Baseline ale byla
    lokalne prepnuta a fungovala - subjekt bez PE i partnera je FAIL."""
    baseline = _vpws_subject(partner=BASE_PARTNER_UP)
    subject = _vpws_subject(remote_peers=())
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=baseline))
    for label in REMOTE_PE_LABELS:
        row = _by_label(rows, label)
        assert row.status is Status.FAIL
        assert row.baseline_value == "local switch (ge-0/0/2.212 Up)"


def test_vpws_remote_pw_in_baseline_local_switch_in_subject_shows_pe_as_baseline():
    baseline = _vpws_subject(remote_peers=[PEER_OK])
    subject = _vpws_subject(partner={"name": "et-0/0/8.212", "status": "Down"})
    rows = run_check(EvpnVpwsStatusCheck(), _vpws_ctx(subject, baseline=baseline))
    row = _by_label(rows, "EVPN VPWS SID remote local switch")
    assert row.status is Status.FAIL and row.baseline_value == "150.0.0.14 Resolved"


def test_vpws_local_switch_lab_captures_mx_to_evo():
    """Lab 2026-09-25: MX1 ge-0/0/2.211 -> PTX1 et-0/0/8.211, oba konce Up.
    Collector + Scope.select + check nad skutecnymi zaznamy."""
    from pathlib import Path

    from lxml import etree

    from migration_validator.collectors.evpn import EvpnVpwsCollector

    cases = Path(__file__).resolve().parents[1] / "fixtures" / "cases"

    def facts(name, platform):
        root = etree.parse(str(cases / name)).getroot()
        return {"evpn_vpws": EvpnVpwsCollector().parse(root, platform)}

    def scope(iface):
        return Scope(
            id=f"svc:{iface}", kind="service",
            key=ScopeKey("EVPN-VPWS-LOCAL", "E-Line", "vpws"),
            selectors=Selectors(interfaces=[iface], physical_interfaces=[iface.split(".")[0]],
                                routing_instances=["EVPN-VPWS-LOCAL"]),
        )

    subject_scope = scope("et-0/0/8.211")
    baseline_view = scope("ge-0/0/2.211").select(
        facts("evpn_vpws_local_switch_mx.xml", "junos"))
    subject_view = subject_scope.select(facts("evpn_vpws_local_switch_evo.xml", "junos-evo"))
    ctx = CheckContext(
        scope=subject_scope, subject=subject_view, baseline=baseline_view,
        config=default_config(), failed_collectors={},
        baseline_collectors={"evpn_vpws": {"status": "ok"}},
    )
    rows = run_check(EvpnVpwsStatusCheck(), ctx)
    assert not any(row.status is Status.FAIL for row in rows)
    switch = _by_label(rows, "EVPN VPWS SID remote local switch")
    assert switch.status is Status.PASS
    assert switch.value == "et-0/0/8.212 Up" and switch.baseline_value == "ge-0/0/2.212 Up"
    assert _by_label(rows, "EVPN VPWS pseudowire status").baseline_value is None
    assert not any(row.label in REMOTE_PE_LABELS for row in rows)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/python -m pytest tests/checks/test_evpn.py -q -p no:warnings -k local_switch`
Expected: FAIL. There's no local-switch row, and the PE rows still report `remote peer chybi`.

- [ ] **Step 3: Implement**

In `migration_validator/checks/evpn.py`, add a module helper below `_pair_baseline_acs`:

```python
def _local_switch_text(partner: dict[str, Any]) -> str:
    return f"local switch ({partner.get('name') or '?'} {partner.get('status') or UNKNOWN})"
```

In `_sid_findings`, right after the `findings = [Finding(Outcome.INFO, ... value row ...)]` list, insert:

```python
        # Lokalne prepnuty EVPN-VPWS (schema 15): remote SID nema PE, ale
        # partnersky AC na tomtez boxu - radky PE/status se nahrazuji
        # jednim radkem se stavem partnera.
        partner = sid.get("local_interface")
        if side == "remote" and partner is not None:
            findings.append(
                self._local_switch_finding(instance, partner, baseline_sid, label, ctx)
            )
            return findings
        baseline_partner = (
            (baseline_sid or {}).get("local_interface") if side == "remote" else None
        )
        # Baseline byla lokalne prepnuta, subjekt ma PE (nebo nic): radky
        # PE/status nesou jako baseline tvar baseline, nikdy UNCHANGED.
        switch_text = _local_switch_text(baseline_partner) if baseline_partner else None
```

In the `if not peers:` / `if side == "remote":` branch:

- Change `same = baseline_iface is not None and not baseline_peers` to:

```python
                same = (
                    baseline_iface is not None
                    and not baseline_peers
                    and baseline_partner is None
                )
```

- In both `Finding`s of that branch, the innermost `else None` (the case where `first_baseline_peer` is falsy) becomes `else switch_text`.
  - PE row: `str(first_baseline_peer.get("ipaddr") or "?") if first_baseline_peer else switch_text`
  - status row: `str(first_baseline_peer.get("status") or "Unresolved / Chybi") if first_baseline_peer else switch_text`

In the `for peer in peers:` loop, in the PE row and status row `baseline_value`s, replace the trailing `else None` with `else switch_text`. Leave the INFO rows (mode/ESI/role) as they are.

Add the method after `_sid_findings`:

```python
    def _local_switch_finding(
        self,
        instance: str,
        partner: dict[str, Any],
        baseline_sid: dict[str, Any] | None,
        label,
        ctx: CheckContext,
    ) -> Finding:
        # Stav partnera je stav druheho konce okruhu. Porovnava se jen stav,
        # ne jmeno - partnersky IFL se migraci prejmenuje
        # (ge-0/0/2.212 -> et-0/0/8.212).
        name = partner.get("name") or "?"
        status = str(partner.get("status") or UNKNOWN)
        baseline_partner = (baseline_sid or {}).get("local_interface")
        baseline_peers = (baseline_sid or {}).get("peers") or []
        if baseline_partner is not None:
            baseline_status = str(baseline_partner.get("status") or UNKNOWN)
            baseline_value = f"{baseline_partner.get('name') or '?'} {baseline_status}"
            same = status != UNKNOWN and baseline_status == status
        elif baseline_peers:
            # Baseline byl vzdaleny PW - tvar se zmenil, UNCHANGED nikdy.
            first = baseline_peers[0]
            baseline_value = (
                f"{first.get('ipaddr') or '?'} {first.get('status') or 'Unresolved / Chybi'}"
            )
            same = False
        else:
            baseline_value = None
            same = False
        up = _is_up(status)
        outcome = (
            Outcome.OK if up
            else unchanged_or(Outcome.BROKEN, ctx, "evpn_vpws", same=same)
        )
        return Finding(
            outcome,
            f"{instance}: lokalni prepnuti na {name}, stav {status}"
            + ("" if up else f", ocekavano {UP}")
            + suffix(outcome),
            label=label("EVPN VPWS SID remote local switch"),
            value=f"{name} {status}",
            baseline_value=baseline_value,
            subject={"interface": name, "status": status},
        )
```

Also update the `EvpnVpwsStatusCheck` docstring with one more sentence: `Lokalne prepnuty EVPN-VPWS (remote SID nese partnersky AC misto PE) ma misto radku remote PE / remote status jeden radek 'EVPN VPWS SID remote local switch' se stavem partnera (schema 15).`

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/checks/test_evpn.py -q -p no:warnings`
Expected: all pass, including the existing `test_vpws_missing_remote_peer_*` tests. `switch_text` is `None` there, because their baselines have no partner.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: `2137 passed, 1 skipped` (2128 + 9).

- [ ] **Step 6: Commit**

```bash
git add migration_validator/checks/evpn.py tests/checks/test_evpn.py
git commit -m "feat(evpn): local-switched EVPN-VPWS row instead of missing remote PE

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Matcher — ambiguity falls through, reason only while unresolved (E5)

**Files:**
- Modify: `migration_validator/scoping/matcher.py:153-221` (`match_scopes`)
- Test: `tests/scoping/test_matcher.py`

**Interfaces:**
- `match_scopes(baseline, subject, mapping=None) -> MatchSet` keeps its signature.
- Unmatched entries are now built after all rules. Order: manual-ambiguity entries first (from `_apply_manual`, unchanged), then the remaining scopes in input order.
- The constants `REASON_NO_CANDIDATE` / `REASON_NEW_SERVICE` stay importable (Task 6 imports `REASON_NEW_SERVICE`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/scoping/test_matcher.py`:

```python
def _ifaces(pairs):
    return {(p.baseline.selectors.interfaces[0], p.subject.selectors.interfaces[0]) for p in pairs}


def test_ambiguous_scopes_fall_through_to_routing_instance():
    """Review E5: popis "CPE" ve dvou VRF, stejna /30 na obou boxech -
    dnes 0 paru, pravidlo routing_instance je rozlisi."""
    baseline = [
        _scope("ge-0/0/2.100", "CPE", "IPVPN", routing_instance="customer-a",
               addresses=["192.168.1.1/30"]),
        _scope("ge-0/0/2.200", "CPE", "IPVPN", routing_instance="customer-b",
               addresses=["192.168.1.1/30"]),
    ]
    subject = [
        _scope("et-0/0/8.100", "CPE", "IPVPN", routing_instance="customer-a",
               addresses=["192.168.1.1/30"]),
        _scope("et-0/0/8.200", "CPE", "IPVPN", routing_instance="customer-b",
               addresses=["192.168.1.1/30"]),
    ]

    result = match_scopes(baseline, subject)

    assert _ifaces(result.pairs) == {("ge-0/0/2.100", "et-0/0/8.100"),
                                     ("ge-0/0/2.200", "et-0/0/8.200")}
    assert {p.method for p in result.pairs} == {"routing_instance+service_type"}
    assert {p.confidence for p in result.pairs} == {"medium"}
    assert result.unmatched_baseline == [] and result.unmatched_subject == []


def test_ambiguity_resolved_by_later_rule_leaves_leftover_as_new_service():
    """Step beh: stary port ma CPE ve VRF-a, novy box CPE ve VRF-a (tento
    krok) i VRF-b (drivejsi vlna). VRF-b souperil jen se sparovanou
    baseline -> nova sluzba, ne ambiguous."""
    baseline = [_scope("ge-0/0/2.100", "CPE", "IPVPN", routing_instance="VRF-a")]
    subject = [
        _scope("et-0/0/8.100", "CPE", "IPVPN", routing_instance="VRF-a"),
        _scope("et-0/0/8.200", "CPE", "IPVPN", routing_instance="VRF-b"),
    ]

    result = match_scopes(baseline, subject)

    assert _ifaces(result.pairs) == {("ge-0/0/2.100", "et-0/0/8.100")}
    assert [(u.scope.selectors.interfaces, u.reason) for u in result.unmatched_subject] == [
        (["et-0/0/8.200"], "nova sluzba, chybi baseline"),
    ]


def test_ambiguity_unresolved_keeps_ambiguous_reason_on_both_sides():
    baseline = [_scope("ge-0/0/2.13", "SAME", "Internet")]
    subject = [_scope("et-0/0/8.13", "SAME", "Internet"), _scope("et-0/0/9.13", "SAME", "Internet")]

    result = match_scopes(baseline, subject)

    assert result.pairs == []
    assert all("ambiguous" in u.reason for u in result.unmatched_baseline + result.unmatched_subject)


def test_local_switch_pair_resolved_by_vlan():
    """Lab 2026-09-25: EVPN-VPWS-LOCAL - na PTX obe AC stejny popis,
    stejna RI, rozlisi je az vlan."""
    baseline = [
        _scope("ge-0/0/2.211", "EVPN-VPWS-LOCAL-CPE1", "E-Line", "vpws",
               routing_instance="EVPN-VPWS-LOCAL", vlans=["211"]),
        _scope("ge-0/0/2.212", "EVPN-VPWS-LOCAL-CPE2", "E-Line", "vpws",
               routing_instance="EVPN-VPWS-LOCAL", vlans=["212"]),
    ]
    subject = [
        _scope("et-0/0/8.211", "EVPN-VPWS-LOCAL", "E-Line", "vpws",
               routing_instance="EVPN-VPWS-LOCAL", vlans=["211"]),
        _scope("et-0/0/8.212", "EVPN-VPWS-LOCAL", "E-Line", "vpws",
               routing_instance="EVPN-VPWS-LOCAL", vlans=["212"]),
    ]

    result = match_scopes(baseline, subject)

    assert _ifaces(result.pairs) == {("ge-0/0/2.211", "et-0/0/8.211"),
                                     ("ge-0/0/2.212", "et-0/0/8.212")}
    assert {p.method for p in result.pairs} == {"vlan+service_type"}
    assert result.unmatched_baseline == [] and result.unmatched_subject == []
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/python -m pytest tests/scoping/test_matcher.py -q -p no:warnings`
Expected: `test_ambiguous_scopes_fall_through_to_routing_instance`, `test_ambiguity_resolved_by_later_rule_leaves_leftover_as_new_service` and `test_local_switch_pair_resolved_by_vlan` FAIL. `test_ambiguity_unresolved_keeps_ambiguous_reason_on_both_sides` passes already.

- [ ] **Step 3: Implement**

Replace the body of `match_scopes` from `for method, confidence, key_fn in RULES:` to the end of the function with:

```python
    # E5 (review 2026-09-24): nejednoznacnost scope z poolu nevyradi -
    # pozdejsi pravidlo (routing_instance, subnet, vlan) ho muze rozlisit.
    # Pro kazdy scope se pamatuje prvni (nejsilnejsi) nejednoznacnost:
    # duvod a scopy druhe strany, se kterymi pod tim klicem souperil.
    ambiguity: dict[int, tuple[str, list[Scope]]] = {}

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
                # vic klicu na scope, takze scope nejednoznacny pod jednim
                # klicem se pod jinym klicem tehoz pravidla nesmi sparovat.
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
                if id(scope) not in paired:
                    dropped.add(id(scope))
                    ambiguity.setdefault(id(scope), (reason, s_hits))
            for scope in s_hits:
                if id(scope) not in paired:
                    dropped.add(id(scope))
                    ambiguity.setdefault(id(scope), (reason, b_hits))

        # `dropped` plati jen uvnitr pravidla - do dalsiho jde vse nesparovane.
        remaining_baseline = [scope for scope in remaining_baseline if id(scope) not in paired]
        remaining_subject = [scope for scope in remaining_subject if id(scope) not in paired]

    paired_ids = {id(pair.baseline) for pair in result.pairs} | {
        id(pair.subject) for pair in result.pairs
    }

    def _reason(scope: Scope, fallback: str) -> str:
        # Duvod "ambiguous" jen dokud nejednoznacnost trva - aspon jeden
        # souper z druhe strany zustal nesparovany. Kdyz je pozdejsi
        # pravidlo vsechny rozdelilo jinam, scope protejsek nema (step beh:
        # scope cizi vlny musi zustat "nova sluzba" a byt vyloucen).
        entry = ambiguity.get(id(scope))
        if entry is not None and any(id(rival) not in paired_ids for rival in entry[1]):
            return entry[0]
        return fallback

    result.unmatched_baseline.extend(
        UnmatchedScope(scope, _reason(scope, REASON_NO_CANDIDATE))
        for scope in remaining_baseline
    )
    result.unmatched_subject.extend(
        UnmatchedScope(scope, _reason(scope, REASON_NEW_SERVICE))
        for scope in remaining_subject
    )
    return result
```

Update the module docstring by appending one paragraph:

```
Nejednoznacny scope z poolu nevypada (E5, spec 2026-09-25): pozdejsi
pravidlo ho smi sparovat, pokud tam je shoda 1:1. Duvod "ambiguous" nese
jen dokud nejednoznacnost trva.
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `.venv/bin/python -m pytest tests/scoping/test_matcher.py -q -p no:warnings`
Expected: all pass, including `test_ambiguous_under_one_key_is_not_paired_under_a_sibling_key` and `test_ambiguity_never_guesses`.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: `2141 passed, 1 skipped`. If a test elsewhere asserts the **order** of `unmatched_*` entries (they used to be emitted mid-loop), check whether the new order (input order) is still correct for what that test pins, and adjust the assertion to be order-independent **only** if the order carries no meaning there. Report any such change in the commit message.

- [ ] **Step 6: Commit**

```bash
git add migration_validator/scoping/matcher.py tests/scoping/test_matcher.py
git commit -m "fix(matcher): ambiguous scopes fall through to later rules (E5)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Engine — step run excludes only `nova sluzba` (E5)

**Files:**
- Modify: `migration_validator/engine.py:30` (import) and `:696-707`
- Test: `tests/test_engine.py` (after `test_step_pulls_linked_partner_of_matched_scope`)

**Interfaces:**
- Consumes `REASON_NEW_SERVICE` from `migration_validator.scoping.matcher`, and the Task 5 reasons.

- [ ] **Step 1: Write the failing test and the guard test**

```python
def test_step_keeps_ambiguous_subject_scope_visible():
    """E5: scope nesparovany kvuli nejednoznacnosti muze patrit tomuto
    kroku - pres checky a do NESPAROVANO, ne do excluded_services."""
    baseline = _snapshot(
        "172.20.20.4", "ge-0/0/4.0",
        [_scope("svc:DUP:Internet", "DUP", "Internet", "ge-0/0/4.0")],
    )
    subject = _snapshot(
        "172.20.20.5", "ae0.15",
        [
            _scope("svc:DUP:Internet#1", "DUP", "Internet", "ae0.15"),
            _scope("svc:DUP:Internet#2", "DUP", "Internet", "ae0.16"),
        ],
        phase="post-migration",
    )

    result = evaluate_snapshots(subject, baseline, now=NOW, step=STEP)

    shown_ids = {scope.scope_id for scope in result.scopes}
    assert {"svc:DUP:Internet#1", "svc:DUP:Internet#2"} <= shown_ids
    assert result.excluded_services == []
    assert [e["scope_id"] for e in result.unmatched["subject"]] == [
        "svc:DUP:Internet#1", "svc:DUP:Internet#2",
    ]
    assert all("ambiguous" in e["reason"] for e in result.unmatched["subject"])


def test_step_excludes_foreign_wave_scope_after_ambiguity_is_resolved():
    """Rozhodnuti 2026-09-25: VRF-b (drivejsi vlna) souperil jen se
    sparovanou baseline -> nova sluzba -> vyloucen."""
    baseline = _snapshot(
        "172.20.20.4", "ge-0/0/4.0",
        [_scope("svc:CPE:IPVPN", "CPE", "IPVPN", "ge-0/0/4.0", routing_instances=["VRF-a"])],
    )
    subject = _snapshot(
        "172.20.20.5", "ae0.15",
        [
            _scope("svc:CPE:IPVPN#a", "CPE", "IPVPN", "ae0.15", routing_instances=["VRF-a"]),
            _scope("svc:CPE:IPVPN#b", "CPE", "IPVPN", "ae0.16", routing_instances=["VRF-b"]),
        ],
        phase="post-migration",
    )

    result = evaluate_snapshots(subject, baseline, now=NOW, step=STEP)

    shown_ids = {scope.scope_id for scope in result.scopes}
    assert "svc:CPE:IPVPN#a" in shown_ids
    assert "svc:CPE:IPVPN#b" not in shown_ids
    assert [(e["scope_id"], e["reason"]) for e in result.excluded_services] == [
        ("svc:CPE:IPVPN#b", "nova sluzba, chybi baseline"),
    ]
```

- [ ] **Step 2: Run the tests and confirm the first fails**

Run: `.venv/bin/python -m pytest tests/test_engine.py -q -p no:warnings -k "step_keeps_ambiguous or foreign_wave"`
Expected: `test_step_keeps_ambiguous_subject_scope_visible` FAILS, because both scopes are in `excluded_services`. `test_step_excludes_foreign_wave_scope_after_ambiguity_is_resolved` PASSES, thanks to Task 5.

- [ ] **Step 3: Implement**

`engine.py:30`: `from migration_validator.scoping.matcher import REASON_NEW_SERVICE, MatchedPair, match_scopes`

In the step condition (~line 699), add the reason check and extend the comment:

```python
            if (
                step is not None
                and item.scope.kind == "service"
                and not partner_matched
                and item.reason == REASON_NEW_SERVICE
            ):
                # Cizi vlna na sdilenem portu: mimo tento migracni krok.
                # Nejde pres checky ani do NESPAROVANO - jen do JSON. Jen
                # "nova sluzba": scope nesparovany kvuli trvajici
                # nejednoznacnosti muze patrit tomuto kroku (E5).
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: `2143 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/engine.py tests/test_engine.py
git commit -m "fix(engine): step run keeps still-ambiguous scopes in NESPAROVANO (E5)

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `mapping.yml` selector `routing_instance`

**Files:**
- Modify: `migration_validator/scoping/mapping.py:18-51` (`Selector`)
- Test: `tests/scoping/test_mapping.py`, `tests/scoping/test_matcher.py`

**Interfaces:**
- Produces `Selector(description=None, service_type=None, interface=None, routing_instance=None)`. `routing_instance` matches when `routing_instance in scope.selectors.routing_instances`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/scoping/test_mapping.py`:

```python
def _vrf_scope(description, routing_instance, interface) -> Scope:
    return Scope(
        id=f"svc:{description}:IPVPN:{interface}",
        kind="service",
        key=ScopeKey(description, "IPVPN", None),
        selectors=Selectors(interfaces=[interface], routing_instances=[routing_instance]),
    )


def test_selector_matches_on_routing_instance():
    selector = Selector(description="CPE", routing_instance="customer-a")
    assert selector.matches(_vrf_scope("CPE", "customer-a", "ge-0/0/2.100"))
    assert not selector.matches(_vrf_scope("CPE", "customer-b", "ge-0/0/2.200"))


def test_routing_instance_alone_is_a_valid_selector():
    assert Selector.from_dict({"routing_instance": "customer-a"}).routing_instance == "customer-a"


def test_load_mapping_reads_routing_instance(tmp_path):
    path = tmp_path / "mapping.yml"
    path.write_text(
        textwrap.dedent(
            """\
            mappings:
              - baseline: {description: CPE, routing_instance: customer-a}
                subject:  {description: CPE, routing_instance: customer-a}
            ignore:
              - {routing_instance: VRF-x}
            """
        ),
        encoding="utf-8",
    )

    mapping = load_mapping(path)

    assert mapping.mappings[0].baseline.routing_instance == "customer-a"
    assert mapping.is_ignored(_vrf_scope("ANY", "VRF-x", "ge-0/0/9.0")) is True
    assert mapping.is_ignored(_vrf_scope("ANY", "VRF-y", "ge-0/0/9.0")) is False
```

Append to `tests/scoping/test_matcher.py`:

```python
def test_manual_mapping_by_routing_instance_splits_same_description():
    baseline = [
        _scope("ge-0/0/2.100", "CPE", "IPVPN", routing_instance="customer-a"),
        _scope("ge-0/0/2.200", "CPE", "IPVPN", routing_instance="customer-b"),
    ]
    subject = [
        _scope("et-0/0/8.100", "CPE", "IPVPN", routing_instance="customer-a"),
        _scope("et-0/0/8.200", "CPE", "IPVPN", routing_instance="customer-b"),
    ]
    mapping = Mapping(mappings=[
        MappingRule(
            baseline=Selector(description="CPE", routing_instance="customer-a"),
            subject=Selector(description="CPE", routing_instance="customer-a"),
        ),
    ])

    result = match_scopes(baseline, subject, mapping)

    manual = [p for p in result.pairs if p.method == "manual"]
    assert _ifaces(manual) == {("ge-0/0/2.100", "et-0/0/8.100")}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/bin/python -m pytest tests/scoping/test_mapping.py tests/scoping/test_matcher.py -q -p no:warnings`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'routing_instance'`.

- [ ] **Step 3: Implement**

In `migration_validator/scoping/mapping.py`, update `Selector`:

```python
@dataclass(frozen=True)
class Selector:
    description: str | None = None
    service_type: str | None = None
    interface: str | None = None
    routing_instance: str | None = None

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
        if self.routing_instance is not None:
            # Rozlisi sluzby se stejnym popisem v ruznych VRF (E5).
            if self.routing_instance not in scope.selectors.routing_instances:
                return False
        return True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Selector":
        selector = cls(
            description=data.get("description"),
            service_type=data.get("service_type"),
            interface=data.get("interface"),
            routing_instance=data.get("routing_instance"),
        )
        if (
            selector.description is None
            and selector.service_type is None
            and selector.interface is None
            and selector.routing_instance is None
        ):
            raise ValueError(
                "prazdny selektor v mapping.yml - uved description, service_type, "
                "interface nebo routing_instance"
            )
        return selector
```

`test_empty_selector_is_rejected` matches `prazdny selektor`, so it still passes.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: `2147 passed, 1 skipped`.

- [ ] **Step 5: Commit**

```bash
git add migration_validator/scoping/mapping.py tests/scoping/test_mapping.py tests/scoping/test_matcher.py
git commit -m "feat(mapping): routing_instance selector in mapping.yml

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `docs/cs` update

**Files (modify; Czech with diacritics):**
- `docs/cs/files/collectors.md` (§ `EvpnVpwsCollector`, around line 179)
- `docs/cs/files/models.md` (line 174, `evpn_vpws` select row; line 226)
- `docs/cs/files/checks.md` (§ `evpn_vpws_status`, around line 415)
- `docs/cs/files/scoping.md` (lines 95-117 matcher ambiguity; § `mapping.py`)
- `docs/cs/architecture.md` (line 106 `evpn_vpws` row, lines 112 and 230)
- `docs/cs/reference.md` (line 25 check row; § 3 `mapping.yml` 219-272; example `schema_version` at line 312 → 15; example `evpn_vpws` at line 353)

- [ ] **Step 1: Find every stale passage**

Run: `grep -n -i "vpws\|pozičn\|nejednozna\|ambiguous\|schema 14\|schema_version\": 14\|prazdny selektor\|selektor" docs/cs/*.md docs/cs/files/*.md`

- [ ] **Step 2: Update each passage to state the following facts (as of schema 15)**

- `evpn_vpws` fact shape: `{routing_instance: {interfaces: [{name, status, mode, pseudowire_status, local_sid, remote_sid}]}}`. SID = `{value, peers, local_interface}`. `local_interface` = `{name, status}` of the partner AC of a local switch (both MX and EVO report it), otherwise `null`. `pseudowire_status` is reported only by EVO (`CCC-Up`); on MX it is `null`.
- `Scope.select`: VPWS instance by RI, `interfaces` narrowed to the scope's ACs. An instance without the scope's AC stays with an empty list.
- Check `evpn_vpws_status`:
  - baseline AC pairing by (local SID, remote SID), with a single-AC fallback
  - new row `EVPN VPWS pseudowire status`, only when present
  - local switch → row `EVPN VPWS SID remote local switch` instead of PE/status
  - shape change → baseline `local switch (...)`, never UNCHANGED
  - missing AC → BROKEN `Chybi`
- Matcher:
  - ambiguity doesn't remove a scope from the pool; later rules can pair it
  - the `ambiguous` reason stays only while a rival is unpaired
  - multi-key guard within a rule
  - step run excludes only `nova sluzba`
- `mapping.yml`: `routing_instance` field in `mappings` and `ignore`, plus the new text of the empty-selector error.
- The example `schema_version` is 15.

Leave historical notes (e.g. "`runs/mig01` ... `schema_version: 12`") alone. They describe the past.

- [ ] **Step 3: Check for leftovers**

Run: `grep -n -i "pozičn" docs/cs/files/checks.md docs/cs/architecture.md docs/cs/reference.md | grep -i vpws`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add docs/cs
git commit -m "docs(cs): schema 15 evpn_vpws, VPWS local switch, matcher fall-through, mapping routing_instance

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Mutants, lab acceptance on `test-no-inventory` (controller)

This task runs real data and compares reports. The controller does it, not a subagent. `runs/` is git-ignored, and nothing here is committed except an optional note.

- [ ] **Step 1: Mutants**

Run `grep -rn -i "mutant" tests/checks/test_evpn.py tests/scoping/test_matcher.py tests/test_engine.py tests/models/test_scope.py tests/collectors/test_evpn.py`. For every docstring that claims a mutant is killed, apply the mutant by hand, run the named test, confirm it fails, and revert. Fix or delete any claim that no longer holds.

Also run these three mutants (do not commit them):
- `_pair_baseline_acs` with the `_sid_key` None guard removed → `test_vpws_unknown_sids_never_pair_by_sid` must fail.
- `_reason` always returning `entry[0]` when there is an entry → `test_ambiguity_resolved_by_later_rule_leaves_leftover_as_new_service` must fail.
- In `Scope.select`, the AC filter replaced with `data` → `test_select_vpws_keeps_only_own_ac` must fail.

- [ ] **Step 2: "Before" report from `main` (schema 14)**

```bash
S=<scratchpad>   # session scratchpad directory
cd /home/rado/Desktop/scripts/migration-status-check
git worktree add $S/main-wt main
.venv/bin/python -c "import sys; sys.path.insert(0, '$S/main-wt'); import migration_validator; print(migration_validator.__file__)"
# expect a path under $S/main-wt
.venv/bin/python -c "import sys; sys.path.insert(0, '$S/main-wt'); from migration_validator.cli import main; sys.exit(main())" \
  evaluate --run test-no-inventory --run-root runs --format text --detail --output $S/before.txt
```

- [ ] **Step 3: "After" report on an upgraded copy**

```bash
mkdir -p $S/runs && cp -r runs/test-no-inventory $S/runs/
.venv/bin/mig-validate upgrade test-no-inventory --run-root $S/runs
.venv/bin/mig-validate evaluate --run test-no-inventory --run-root $S/runs --format text --detail --output $S/after.txt
diff $S/before.txt $S/after.txt
```

- [ ] **Step 4: Account for every diff line**

Expected differences (spec §6, acceptance):
- **E5:** EVPN-VPWS-LOCAL scopes are paired: ge-0/0/2.211 ↔ et-0/0/8.211 and ge-0/0/2.212 ↔ et-0/0/8.212, method `vlan+service_type`. They are gone from NESPAROVANO (4 fewer entries). MGMT E-LAN scopes stay in NESPAROVANO as ambiguous.
- **EVPN-VPWS-LOCAL .211 and .212:** a `EVPN VPWS SID remote local switch` row PASS with baseline `ge-0/0/2.21x Up`, and a `EVPN VPWS pseudowire status` row `CCC-Up`. There is no `remote peer chybi` row.
- **Remote PWs (.213, ae0.224):** only a new `EVPN VPWS pseudowire status` row `CCC-Up`. There must **not** be any `ve vypisu instance chybi` row.

Any other difference is a finding. Stop and report it to the user.

- [ ] **Step 5: Clean up and hand over**

```bash
git worktree remove $S/main-wt
```

Report the diff summary to the user. The real `mig-validate upgrade test-no-inventory` on `runs/` and the lab-GUI restart are the user's decision (spec §6).
