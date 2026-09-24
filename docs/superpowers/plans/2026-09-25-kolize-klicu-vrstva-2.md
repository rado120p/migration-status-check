# Key collisions, layer 2 (schema 13 → 14) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-key the `bgp`, `bfd`, `evpn_esi` and `routes` facts as lists of self-identifying records, and filter IPv6 neighbours out of `pim_neighbor`. After this, two legitimate entries on one box never overwrite each other. The per-scope view that checks read keeps today's shape.

**Architecture:** Collectors emit a list of records per area. Each record carries its full identity: (instance, address, local_interface), (neighbor, interface), (instance, ESI), (rib, prefix, protocol). `Scope.select` filters records by ownership and folds them back into today's per-scope dict (`{address: record}`, `{esi: flat}`, and for routes `{protocol: {rib: {prefix: record}}}`). A key is unique within one scope by construction, because a scope has one interface and at most one RI. Where more than one record still passes ownership for one address (multihop BFD, link-local BGP), `select` returns an `*_ambiguous` count and leaves that address out of the view. The Layer 1 collision plumbing (`bgp_collisions` / `bfd_collisions`) is deleted and replaced by that ambiguity marker.

Each task converts one fact area end to end: collector, `Scope.select`, NEZARAZENO, checks, renderers, test builders. So the suite is green after every task.

**Tech Stack:** Python 3, pytest via `.venv/bin/python -m pytest`, lxml, vanilla JS GUI (tests via `node --test tests/js/*.test.js`).

**Spec:** `docs/superpowers/specs/2026-09-25-kolize-klicu-vrstva-2-design.md`

## Global Constraints

- `SCHEMA_VERSION = 14` (`migration_validator/models/snapshot.py`). The loader stays exact-match. No 13 → 14 shim.
- **No RPC and no RPC kwargs may change.** Replay `call_key` must keep matching recorded bundles, or `mig-validate upgrade` breaks. Every new field must come from XML the current RPCs already return.
- `LIST_AREAS` in `migration_validator/capture.py` = `{"arp", "nd", "bgp", "bfd", "evpn_esi", "routes"}` at the end of the wave. A failed collector writes `[]` for these.
- The per-scope view shape that checks read stays as it is: `bgp` → `{address: record}`, `bfd` → `{neighbor: record}`, `evpn_esi` → `{esi: {"resolved_status", "df_role", "interface", "status", "mode"}}`. Routes are the one exception: `routes` → `{protocol: {rib: {prefix: record}}}`.
- The per-scope view keys `bgp_collisions` / `bfd_collisions` disappear. The new keys are `bgp_ambiguous` / `bfd_ambiguous`: `{address: int_count}`, with count ≥ 2.
- Ambiguity values, verbatim: row value `neznamy (nejednoznacne: {n} session)`, and prefix-count baseline value `bez baseline (nejednoznacne)`.
- Row labels (`BGP status (addr)`, `BFD (addr)`, `BGP prefixy`, ESI labels, route labels) must not change. They are row ids in the JSON output.
- A PIM neighbour is dropped only when `ip-protocol-version` is exactly `6`. A missing element means v4.
- An ambiguous record is **owned**, so it must **not** appear in NEZARAZENO.
- Code comments follow repo style: Czech, **no diacritics**. Docs under `docs/` keep diacritics.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Work on branch `vrstva-2-kolize-klicu`. Don't merge and don't push.
- Baseline before the wave: `2060 passed, 1 skipped`, JS `86 pass`.
- A docstring or comment that claims "mutant X is killed by test Y" must be verified by actually running the mutant before it is kept or written (memory: such claims go stale).

## Review Focus

1. **The production case end to end.** customer-a and customer-b each have peer `192.168.1.2` in their own VRF, and customer-a's session is Idle. customer-b must be PASS, with no `BGP prefixy (customer-a.inet.0)` row and no BFD `v baseline patril k teto sluzbe`. customer-a must be FAIL for its own Idle session. Pinned in Task 6.
2. **Single-hop and multihop records to the same neighbour.** A scope with the single-hop session on its own interface must take the single-hop one. It must not report ambiguity, and it must not grab the multihop record. Pinned in Task 2.
3. **Static and aggregate on the same (rib, prefix), owned by one service.** Both checks must see their own record, and NEZARAZENO must not swallow the aggregate when only the static route is owned. Pinned in Task 4.
4. **Ambiguity in the baseline only.** The baseline has two multihop sessions on the address, and the subject has one. The subject row must never be UNCHANGED or RECOVERED, and its baseline value must say `neznamy (nejednoznacne: 2 session)`. Pinned in Task 2 (BFD) and Task 1 (BGP).
5. **An ESI shared by two instances, with one IFL Down.** The scope whose IFL is Down must FAIL. Today the Up copy of the ESI hides it. Pinned in Task 3.

---

### Task 1: BGP records, `bgp_ambiguous`, schema 14

**Files:**
- Modify: `migration_validator/models/snapshot.py:19-30` (comment + `SCHEMA_VERSION`)
- Modify: `migration_validator/capture.py:29` (`LIST_AREAS`)
- Modify: `migration_validator/collectors/bgp.py` (`parse`)
- Modify: `migration_validator/models/scope.py` (`owns_bgp_peer`, `select` BGP part, new `_by_key`)
- Modify: `migration_validator/engine.py:459-495` (`_unassigned_bgp_peers`)
- Modify: `migration_validator/checks/bgp.py` (collision → ambiguity)
- Modify: `migration_validator/reporting/text_report.py:417-418`, `migration_validator/gui/static/view.js:163-165` (BGP NEZARAZENO detail)
- Create: `tests/fact_records.py` (test builders, shared by later tasks)
- Modify: `tests/conftest.py:146-240` (`_facts_for` BGP part)
- Test: `tests/collectors/test_bgp.py`, `tests/models/test_scope.py`, `tests/test_engine.py`, `tests/checks/test_bgp.py`, `tests/reporting/…` (whichever file tests `_unassigned_row`), `tests/js/view.test.js`, `tests/models/test_snapshot.py` and any test that hard-codes schema 13

**Interfaces:**
- Produces `BgpCollector.parse(xml, platform) -> list[dict]`. Record keys, exactly: `address, routing_instance, local_interface, state, peer_as, ribs`.
- Produces `Scope.owns_bgp_peer(self, record: dict) -> bool`. It takes the record, which carries `address`, and there is no separate peer argument.
- Produces the module-level `models.scope._by_key(records: list[dict], key: str) -> tuple[dict[str, dict], dict[str, int]]`. It returns (unique view, ambiguous counts). Task 2 reuses it.
- Produces the per-scope view keys `bgp` (`{address: record}`) and `bgp_ambiguous` (`{address: int}`).
- Produces in `checks/bgp.py`: `ambiguous_value(count: int) -> str` and `NO_BASELINE_AMBIGUOUS = "bez baseline (nejednoznacne)"`. Task 2 imports `ambiguous_value`.
- `checks/bgp.py` **keeps** `ADDRESS_COLLISION` for now, because `checks/bfd.py` still imports it. Task 2 deletes it. `NO_BASELINE_COLLISION` is deleted here.
- Produces `tests/fact_records.py` with `bgp_record(...)` and `bgp_records(mapping)`.

- [ ] **Step 1: Test builders**

Create `tests/fact_records.py`:

```python
"""Stavitele zaznamu device faktu (schema 14) pro testy.

Device fakta bgp/bfd/evpn_esi/routes jsou od schematu 14 seznamy zaznamu,
ktere nesou celou svou identitu. Testy, ktere si fakta staveji rucne, je
maji stavet timhle modulem - tvar se pak meni na jednom miste.

`*_records(mapping)` prevadi stary tvar {klic: data} na seznam, aby prepis
existujiciho testu byl jen obaleni literalu.
"""

from __future__ import annotations

from typing import Any


def bgp_record(
    address: str,
    *,
    routing_instance: str | None = None,
    local_interface: str | None = None,
    state: str = "Established",
    peer_as: int | None = None,
    ribs: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    return {
        "address": address,
        "routing_instance": routing_instance,
        "local_interface": local_interface,
        "state": state,
        "peer_as": peer_as,
        "ribs": dict(ribs or {}),
    }


def bgp_records(mapping: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """{adresa: data} -> [zaznam]. Chybejici pole dostanou vychozi hodnotu."""
    return [
        {**bgp_record(address), **data, "address": address}
        for address, data in mapping.items()
    ]
```

- [ ] **Step 2: Failing collector tests**

In `tests/collectors/test_bgp.py`, rewrite the dict-based tests (`test_peer_schema`, `test_ribs_are_kept_apart`, `test_known_rib_name_is_present` and every other test there that does `.items()` / `.values()` on the result) to iterate the list. Then add:

```python
def _peer(address, instance=None, local_if=None, state="Established"):
    rti = f"<peer-cfg-rti>{instance}</peer-cfg-rti>" if instance else ""
    lif = f"<local-interface-name>{local_if}</local-interface-name>" if local_if else ""
    return (
        f"<bgp-peer><peer-address>{address}+179</peer-address>"
        f"<peer-state>{state}</peer-state><peer-as>65001</peer-as>{rti}{lif}</bgp-peer>"
    )


def test_same_address_in_two_instances_keeps_both():
    xml = etree.fromstring(
        "<bgp-information>"
        + _peer("192.168.1.2", "customer-a", "ae0.100", "Idle")
        + _peer("192.168.1.2", "customer-b", "ae0.200")
        + "</bgp-information>"
    )
    records = BgpCollector().parse(xml, "junos")
    assert [(r["address"], r["routing_instance"], r["state"]) for r in records] == [
        ("192.168.1.2", "customer-a", "Idle"),
        ("192.168.1.2", "customer-b", "Established"),
    ]


def test_record_carries_local_interface():
    xml = etree.fromstring(
        "<bgp-information>" + _peer("fe80::2", "CUST", "ae0.100") + "</bgp-information>"
    )
    (record,) = BgpCollector().parse(xml, "junos")
    assert record["local_interface"] == "ae0.100"


def test_master_instance_is_none():
    xml = etree.fromstring(
        "<bgp-information>" + _peer("10.0.0.1", "master") + "</bgp-information>"
    )
    (record,) = BgpCollector().parse(xml, "junos")
    assert record["routing_instance"] is None
    assert record["local_interface"] is None


@pytest.mark.parametrize("platform", PLATFORMS)
def test_fixture_records_carry_local_interface_key(rpc_fixture, platform):
    records = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    assert records
    assert all(set(r) == {"address", "routing_instance", "local_interface",
                          "state", "peer_as", "ribs"} for r in records)
    assert any(r["local_interface"] for r in records)
```

Run: `.venv/bin/python -m pytest tests/collectors/test_bgp.py -q`
Expected: FAIL, because `parse` returns a dict.

- [ ] **Step 3: Collector**

In `migration_validator/collectors/bgp.py`, change `parse` to return a list:

```python
    def parse(self, xml: etree._Element, platform: str) -> list[dict[str, Any]]:
        # Seznam, ne slovnik podle adresy: dve VRF se stejnou p2p podsiti
        # maji peera na stejne adrese (ostry beh MX -> ACX 2026-09-23) a
        # klic adresou jednu session tise zahodil. Identitu nese zaznam:
        # (routing_instance, address, local_interface) - local_interface
        # rozlisi i dva link-local sousedy v jedne RI (schema 14).
        peers: list[dict[str, Any]] = []

        for node in xml.iter("bgp-peer"):
            ...  # dnesni cteni address / ribs / instance / peer_as beze zmeny
            peers.append({
                "address": address,
                "routing_instance": instance,
                "local_interface": _text(node, "local-interface-name"),
                "state": _text(node, "peer-state") or "unknown",
                "peer_as": int(peer_as) if peer_as and peer_as.isdigit() else None,
                "ribs": ribs,
            })

        return peers
```

Also update the module docstring: add a paragraph saying the facts are a list of records since schema 14, and why.

Run: `.venv/bin/python -m pytest tests/collectors/test_bgp.py -q`
Expected: PASS.

- [ ] **Step 4: Schema bump and LIST_AREAS**

`migration_validator/models/snapshot.py`: add under the `# 13:` line:

```python
# 14: device fakta bgp/bfd/evpn_esi/routes jsou seznamy zaznamu s celou
#     identitou (kolize klicu, spec 2026-09-25): bgp (instance, adresa,
#     local_interface), bfd (soused, rozhrani, multihop), evpn_esi
#     (instance, ESI, per-IFL stav), routes (rib, prefix, protokol).
#     pim_neighbor nese jen IPv4 sousedy.
SCHEMA_VERSION = 14
```

`migration_validator/capture.py:29`: `LIST_AREAS = frozenset({"arp", "nd", "bgp"})`. Later tasks add `bfd`, `evpn_esi` and `routes`.

Update every test that hard-codes schema `13`. Find them with `grep -rn "13" tests/models/test_snapshot.py tests/test_cli.py tests/test_end_to_end.py tests/test_engine.py tests/gui tests/runs | grep -i schema`. Also check that the `upgrade` tests (`tests/raw/test_upgrade.py`) still describe the right "stale version" situation.

- [ ] **Step 5: Failing Scope tests**

In `tests/models/test_scope.py`:
- Convert every hand-built `facts = {"bgp": {...}}` to `bgp_records({...})` (import from `fact_records`).
- Delete or rewrite the Layer 1 `bgp_collisions` tests (grep `collision`). A foreign-instance record on our address must now just be absent from `bgp` and must **not** appear anywhere as a collision or ambiguity.

Add:

```python
from fact_records import bgp_record


def _ipvpn(ri, peer="192.168.1.2", iface="ae0.100"):
    return Scope(
        id=f"svc:{ri}", kind="service",
        key=ScopeKey(description=ri, service_type="IPVPN"),
        selectors=Selectors(interfaces=[iface], routing_instances=[ri],
                            bgp_neighbors=[peer]),
    )


def test_bgp_same_address_two_vrfs_each_scope_gets_its_own():
    facts = {"bgp": [
        bgp_record("192.168.1.2", routing_instance="customer-a", state="Idle"),
        bgp_record("192.168.1.2", routing_instance="customer-b"),
    ]}
    a = _ipvpn("customer-a").select(facts)
    b = _ipvpn("customer-b", iface="ae0.200").select(facts)
    assert a["bgp"]["192.168.1.2"]["state"] == "Idle"
    assert b["bgp"]["192.168.1.2"]["state"] == "Established"
    assert a["bgp_ambiguous"] == {} and b["bgp_ambiguous"] == {}
    assert "bgp_collisions" not in a


def test_bgp_two_link_local_records_in_one_ri_are_ambiguous():
    facts = {"bgp": [
        bgp_record("fe80::2", routing_instance="CUST", local_interface="ae0.100"),
        bgp_record("fe80::2", routing_instance="CUST", local_interface="ae0.200"),
    ]}
    view = _ipvpn("CUST", peer="fe80::2").select(facts)
    assert view["bgp"] == {}
    assert view["bgp_ambiguous"] == {"fe80::2": 2}
```

Run: `.venv/bin/python -m pytest tests/models/test_scope.py -q`
Expected: FAIL.

- [ ] **Step 6: Scope**

In `migration_validator/models/scope.py`, add the module-level helper (above `class Selectors`):

```python
def _by_key(
    records: list[dict[str, Any]], key: str
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Vybrane zaznamy -> (pohled {klic: zaznam}, {klic: pocet} nejednoznacnych).

    Uvnitr jednoho scopu je klic jednoznacny z konstrukce (jedno rozhrani,
    nejvys jedna RI). Kdyz ownership presto projde vic zaznamu na jeden
    klic (multihop BFD bez VRF, dva link-local BGP sousedi), nevybira se
    nahodne - klic v pohledu chybi a check rekne 'nejednoznacne'.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get(key)), []).append(record)
    view = {k: group[0] for k, group in grouped.items() if len(group) == 1}
    ambiguous = {k: len(group) for k, group in grouped.items() if len(group) > 1}
    return view, ambiguous
```

Replace `owns_bgp_peer`. Keep the docstring's reasoning about deactivated peers, and rewrite the part about "klicovana jen adresou":

```python
    def owns_bgp_peer(self, record: dict[str, Any]) -> bool:
        peer = str(record.get("address"))
        if (
            peer not in self.selectors.bgp_neighbors
            and peer not in self.selectors.bgp_neighbors_inactive
        ):
            return False
        return record.get("routing_instance") == self.bgp_instance
```

In `select`, replace the `bgp_facts` / `bgp` / `bgp_collisions` block with:

```python
        bgp, bgp_ambiguous = _by_key(
            [r for r in (facts.get("bgp") or []) if self.owns_bgp_peer(r)],
            "address",
        )
```

In the returned dict, replace `"bgp_collisions": bgp_collisions` with `"bgp_ambiguous": bgp_ambiguous`. Leave `bfd_collisions` alone; Task 2 handles it.

Note: `bfd_collisions` currently checks `peer in self.selectors.bgp_neighbors` on the old BFD dict, which is fine. Don't touch the BFD code in this task.

Run: `.venv/bin/python -m pytest tests/models/test_scope.py -q`
Expected: PASS.

- [ ] **Step 7: NEZARAZENO BGP**

In `migration_validator/engine.py`, rewrite `_unassigned_bgp_peers` over records. Keep its comments, and update the one about "porovnani jen adresou":

```python
    def _on_port(record: dict[str, Any]) -> bool:
        if port is None:
            return True
        instance = record.get("routing_instance")
        if instance:
            return instance in _scope_instances(scopes)
        return _peer_in_scope_subnets(
            str(record.get("address")),
            [scope for scope in scopes if scope.bgp_instance is None],
        )

    records = sorted(
        subject.facts.get("bgp") or [],
        key=lambda r: (
            str(r.get("address")),
            r.get("routing_instance") or "",
            r.get("local_interface") or "",
        ),
    )
    return [
        {
            "peer": record.get("address"),
            "routing_instance": record.get("routing_instance"),
            "local_interface": record.get("local_interface"),
            "snapshot": "subject",
        }
        for record in records
        if not any(scope.owns_bgp_peer(record) for scope in scopes)
        and _on_port(record)
    ]
```

In `tests/test_engine.py`, convert the hand-built BGP facts to `bgp_records(...)`, and adjust any expected NEZARAZENO BGP entry to include `"local_interface": None`. Add:

```python
def test_unassigned_bgp_foreign_vrf_record_on_our_address_is_listed():
    # customer-b ma sluzbu, customer-a ne: session customer-a na stejne
    # adrese nikomu nepatri a musi byt v NEZARAZENO.
    ...  # subject snapshot se scopem IPVPN customer-b (peer 192.168.1.2),
         # fakta bgp_records dvou zaznamu (customer-a, customer-b)
    assert [(e["peer"], e["routing_instance"]) for e in result.unassigned["bgp_peers"]] == [
        ("192.168.1.2", "customer-a")
    ]
```

Build the snapshot with the helper the neighbouring NEZARAZENO tests in `tests/test_engine.py` already use (grep `_unassigned_bgp_peers` / `unassigned["bgp_peers"]`), and write the elided lines in that helper's style.

Run: `.venv/bin/python -m pytest tests/test_engine.py -q`
Expected: PASS.

- [ ] **Step 8: checks/bgp.py collision → ambiguity**

Replace the constants block (`ADDRESS_COLLISION` stays, `NO_BASELINE_COLLISION` goes):

```python
# Adresa, na kterou ve scopu projde vic nez jeden zaznam (dva link-local
# sousedi v jedne RI, multihop BFD bez VRF - Scope.select -> *_ambiguous).
# Je to 'nevime', ne 'neni': jako value u SKIP radku, jako baseline_value
# tam, kde nejednoznacna byla baseline. Sdilena s bfd.py.
def ambiguous_value(count: int) -> str:
    return f"neznamy (nejednoznacne: {count} session)"


# Hodnota SKIP radku prefixu, kdyz baseline na adrese nese vic session.
NO_BASELINE_AMBIGUOUS = "bez baseline (nejednoznacne)"
# Docasne - checks/bfd.py ho importuje do Tasku 2 teto vlny.
ADDRESS_COLLISION = "neznamy (kolize adresy)"
```

In `BgpSessionStateCheck.run`:
- `collisions` → `ambiguous: dict[str, int] = ctx.subject.get("bgp_ambiguous", {})`
- `baseline_collisions` → `baseline_ambiguous: dict[str, int] = (ctx.baseline or {}).get("bgp_ambiguous", {})`
- `was = ambiguous_value(baseline_ambiguous[peer]) if baseline_state is None and peer in baseline_ambiguous else baseline_state`
- In the `without_session` loop, `baseline_value = … else ambiguous_value(baseline_ambiguous[peer]) if peer in baseline_ambiguous else None`.
- The SKIP branch becomes:

```python
            if peer in ambiguous:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{peer}: stav nelze urcit - na adrese je {ambiguous[peer]} BGP "
                        f"session v instanci {ctx.scope.bgp_instance or 'master'} "
                        "(lisi se local-interface)",
                        label=f"BGP status ({peer})",
                        family=peer_family(peer),
                        value=ambiguous_value(ambiguous[peer]),
                        baseline_value=baseline_value,
                    )
                )
                continue
```
- `same=peer not in baseline_peers and peer not in baseline_ambiguous`.

In `BgpPrefixCountsCheck.run`, do the same rename:
- `if not peers and not ambiguous:`
- the `set(ambiguous) - set(peers)` SKIP row takes the same message as above, `value=ambiguous_value(n)`;
- the baseline branch becomes `peer not in baseline_peers and peer in baseline_ambiguous` → message `f"{peer}: v baseline je na adrese {n} session, nelze porovnat"`, `value=NO_BASELINE_AMBIGUOUS`.

Update the comments that mention collisions or overwriting, including the module docstring line "Namerena polozka se k nemu prida jen ze spravne instance (Scope.owns_bgp_peer)", which stays true.

In `tests/checks/test_bgp.py`, rewrite the collision tests (grep `collision`, `kolize`) into ambiguity tests that build `bgp_ambiguous` directly in the subject/baseline view. At minimum:

```python
def test_session_ambiguous_subject_is_skip_not_missing():
    # subject: {"bgp": {}, "bgp_ambiguous": {"fe80::2": 2}}, scope bgp_neighbors=["fe80::2"]
    # -> jediny radek BGP status (fe80::2), Outcome.SKIP, value "neznamy (nejednoznacne: 2 session)"


def test_session_ambiguous_baseline_never_unchanged():
    # subject: peer Idle v "bgp"; baseline: {"bgp": {}, "bgp_ambiguous": {peer: 2}}
    # -> Outcome.BROKEN (ne UNCHANGED), baseline_value "neznamy (nejednoznacne: 2 session)"


def test_prefix_counts_ambiguous_baseline_is_skip():
    # subject: peer s ribs; baseline_ambiguous {peer: 2}
    # -> SKIP, value "bez baseline (nejednoznacne)"
```

Write them with the context helpers already used in `tests/checks/test_bgp.py` (grep `def _ctx` / `CheckContext(`). The comments above give the exact input and the asserts.

Run: `.venv/bin/python -m pytest tests/checks/test_bgp.py -q`
Expected: PASS.

- [ ] **Step 9: NEZARAZENO renderer (text + GUI)**

`migration_validator/reporting/text_report.py`, `_unassigned_row`:

```python
    if kind == "bgp_peers":
        detail = f"RI {item.get('routing_instance') or '-'}"
        # Dva link-local peery se stejnou adresou by jinak vypadaly stejne.
        if item.get("local_interface"):
            detail += f"   {item['local_interface']}"
        return item["peer"], detail
```

`migration_validator/gui/static/view.js`, `unassignedRow`:

```js
  if (kind === "bgp_peers") {
    const detail = `RI ${item.routing_instance || "-"}`;
    return {
      identity: item.peer,
      detail: item.local_interface ? `${detail}   ${item.local_interface}` : detail,
    };
  }
```

Add one test each: to the Python test file that covers `_unassigned_row` (grep `_unassigned_row` in `tests/`) and to `tests/js/view.test.js` (grep `unassignedRow`). Each checks that a `local_interface` shows up in the detail and that its absence leaves `RI x` unchanged.

- [ ] **Step 10: Shared synthetic facts**

In `tests/conftest.py` `_facts_for`: `bgp = []`, and inside the loop:

```python
            bgp.append({
                "address": peer,
                # Presne pravidlo vlastnictvi (Scope.bgp_instance): Internet
                # ve virtual-routeru ma peery v masteru.
                "routing_instance": scope.bgp_instance,
                "local_interface": interface,
                "state": "Established",
                "peer_as": None,
                "ribs": _ribs_for(peer, family),
            })
```

`routing_instance` is `scope.bgp_instance`, not `routing_instances[0]`. Today's dict used `routing_instances[0]`. If the suite changes behaviour because of this (an Internet-in-VR fixture scope), keep `bgp_instance`, which is what production collects, and fix the expectation.

Watch for the multiple-scopes-same-peer case: the old dict deduped by address. If two scopes share a peer (e.g. an IPVPN and its mvpn subtype on one unit), append only once per `(address, routing_instance)`. Keep a `seen_bgp: set[tuple]` and skip duplicates.

Also `tests/collectors/test_conformance.py`: grep `"bgp"` and adapt.

- [ ] **Step 11: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3` and `node --test tests/js/*.test.js 2>&1 | grep -E "^# (pass|fail)"`
Expected: all pass. Any remaining failure is an unconverted `"bgp": {…}` device fact: wrap it in `bgp_records(...)`. Don't touch per-scope views in check tests (`ctx.subject["bgp"]` stays a dict).

```bash
git add -A migration_validator tests
git commit -m "feat(bgp): facts as records keyed by (instance, address, local_interface); schema 14

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: BFD records, multihop ownership, `bfd_ambiguous`

**Files:**
- Modify: `migration_validator/collectors/bfd.py`
- Modify: `migration_validator/capture.py:29` (add `"bfd"`)
- Modify: `migration_validator/models/scope.py` (`owns_bfd_session`, `select` BFD part)
- Modify: `migration_validator/engine.py:545-598` (`_unassigned_bfd_sessions`). `_RENAMED_FIELD_DICT_AREAS` stays as it is, because the view is still a dict (see Step 6).
- Modify: `migration_validator/checks/bfd.py`, `migration_validator/checks/bgp.py` (delete `ADDRESS_COLLISION`)
- Modify: `migration_validator/reporting/text_report.py` / `gui/static/view.js` (BFD NEZARAZENO detail)
- Modify: `tests/fact_records.py`, `tests/conftest.py` (`_facts_for` BFD, including transit)
- Test: `tests/collectors/test_bfd.py`, `tests/models/test_scope.py`, `tests/models/test_scope_core.py`, `tests/test_engine.py`, `tests/checks/test_bfd.py`, `tests/checks/test_core_protocols.py`, `tests/js/view.test.js`

**Interfaces:**
- Consumes `models.scope._by_key` and `checks.bgp.ambiguous_value` from Task 1.
- Produces `BfdCollector.parse(...) -> list[dict]`. Record keys, exactly: `neighbor, interface, multihop, state, remote_state, local_diagnostic, clients, detection_time, transmission_interval, multiplier`.
- Produces `Scope.owns_bfd_session(self, record: dict, records: list[dict]) -> bool`, where `records` is every BFD record on the box. The multihop rule needs them.
- Produces the per-scope view keys `bfd` (`{neighbor: record}`) and `bfd_ambiguous` (`{neighbor: int}`). `bfd_collisions` is gone.
- Produces `tests/fact_records.py`: `bfd_record(...)`, `bfd_records(mapping)`.

- [ ] **Step 1: Builders**

Append to `tests/fact_records.py`:

```python
def bfd_record(
    neighbor: str,
    *,
    interface: str | None = None,
    multihop: bool | None = None,
    state: str = "Up",
    clients: list[str] | None = None,
    multiplier: int | None = 3,
) -> dict[str, Any]:
    return {
        "neighbor": neighbor,
        "interface": interface,
        "multihop": (interface is None) if multihop is None else multihop,
        "state": state,
        "remote_state": "Up",
        "local_diagnostic": "None",
        "clients": list(clients if clients is not None else ["BGP"]),
        "detection_time": "0.900",
        "transmission_interval": "0.300",
        "multiplier": multiplier,
    }


def bfd_records(mapping: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """{soused: data} -> [zaznam]."""
    return [
        {**bfd_record(neighbor, interface=data.get("interface")), **data,
         "neighbor": neighbor,
         "multihop": data.get("multihop", data.get("interface") is None)}
        for neighbor, data in mapping.items()
    ]
```

- [ ] **Step 2: Failing collector tests**

Rewrite `tests/collectors/test_bfd.py` from dict to list (e.g. `test_returns_mapping_keyed_by_neighbor` becomes `test_returns_list_of_records`, and `test_entries_have_expected_keys` gets the new key set). Add:

```python
def _session(neighbor, interface="", session_type="Single hop BFD", state="Up"):
    return (
        f"<bfd-session><session-neighbor>{neighbor}</session-neighbor>"
        f"<session-state>{state}</session-state>"
        f"<session-interface>{interface}</session-interface>"
        f"<session-type>{session_type}</session-type></bfd-session>"
    )


def test_same_neighbor_two_interfaces_keeps_both():
    xml = etree.fromstring(
        "<bfd-session-information>"
        + _session("192.168.1.2", "ae0.100", state="Down")
        + _session("192.168.1.2", "ae0.200")
        + "</bfd-session-information>"
    )
    records = BfdCollector().parse(xml, "junos")
    assert [(r["neighbor"], r["interface"], r["state"]) for r in records] == [
        ("192.168.1.2", "ae0.100", "Down"),
        ("192.168.1.2", "ae0.200", "Up"),
    ]


def test_two_multihop_sessions_same_neighbor_keeps_both():
    xml = etree.fromstring(
        "<bfd-session-information>"
        + _session("198.11.14.4", session_type="Multi hop BFD")
        + _session("198.11.14.4", session_type="Multi hop BFD", state="Down")
        + "</bfd-session-information>"
    )
    records = BfdCollector().parse(xml, "junos")
    assert len(records) == 2
    assert all(r["interface"] is None and r["multihop"] for r in records)


def test_multihop_without_session_type_falls_back_to_empty_interface():
    xml = etree.fromstring(
        "<bfd-session-information><bfd-session>"
        "<session-neighbor>10.9.9.9</session-neighbor><session-state>Up</session-state>"
        "<session-interface/></bfd-session></bfd-session-information>"
    )
    (record,) = BfdCollector().parse(xml, "junos")
    assert record["interface"] is None and record["multihop"] is True


def test_mx_fixture_multihop_session_is_flagged(rpc_fixture):
    records = BfdCollector().parse(rpc_fixture("junos", "bfd"), "junos")
    (mh,) = [r for r in records if r["neighbor"] == "198.11.14.4"]
    assert mh["multihop"] is True and mh["interface"] is None
```

Run: `.venv/bin/python -m pytest tests/collectors/test_bfd.py -q`
Expected: FAIL.

- [ ] **Step 3: Collector**

```python
    def parse(self, xml: etree._Element, platform: str) -> list[dict[str, Any]]:
        # Seznam, ne slovnik podle souseda (schema 14): single-hop session
        # dvou VRF na stejnou adresu se lisi rozhranim, multihop session
        # rozhrani ani VRF nenese (extensive taky ne - overeno v laborce
        # 2026-09-24), takze dve takove se nerozlisi vubec a obe musi
        # prezit, aby vyhodnoceni mohlo rict 'nejednoznacne'.
        sessions: list[dict[str, Any]] = []
        for node in xml.iter("bfd-session"):
            neighbor = _text(node, "session-neighbor")
            if not neighbor:
                continue
            clients = [...]  # beze zmeny
            interface = _text(node, "session-interface") or None
            session_type = _text(node, "session-type")
            multihop = (
                "multi hop" in session_type.lower()
                if session_type
                else interface is None
            )
            sessions.append({
                "neighbor": neighbor,
                "interface": interface,
                "multihop": multihop,
                ...  # zbyla pole beze zmeny
            })
        return sessions
```

Check that `_text` returns `None` for an empty element. If it returns `""`, the `or None` above covers it.

`capture.py`: `LIST_AREAS = frozenset({"arp", "nd", "bgp", "bfd"})`.

Run: `.venv/bin/python -m pytest tests/collectors/test_bfd.py -q`
Expected: PASS.

- [ ] **Step 4: Failing Scope tests**

In `tests/models/test_scope.py` and `tests/models/test_scope_core.py`, convert BFD device facts to `bfd_records(...)` and delete or rewrite the `bfd_collisions` tests. Add:

```python
from fact_records import bfd_record


def test_bfd_same_neighbor_two_interfaces_each_scope_gets_its_own():
    facts = {"bfd": [
        bfd_record("192.168.1.2", interface="ae0.100", state="Down"),
        bfd_record("192.168.1.2", interface="ae0.200"),
    ]}
    a = _ipvpn("customer-a").select(facts)
    b = _ipvpn("customer-b", iface="ae0.200").select(facts)
    assert a["bfd"]["192.168.1.2"]["state"] == "Down"
    assert b["bfd"]["192.168.1.2"]["state"] == "Up"
    assert a["bfd_ambiguous"] == {} and "bfd_collisions" not in a


def test_bfd_two_multihop_same_neighbor_is_ambiguous():
    facts = {"bfd": [bfd_record("198.11.14.4"), bfd_record("198.11.14.4", state="Down")]}
    view = _ipvpn("CUST", peer="198.11.14.4").select(facts)
    assert view["bfd"] == {}
    assert view["bfd_ambiguous"] == {"198.11.14.4": 2}


def test_bfd_single_hop_on_own_interface_wins_over_multihop():
    facts = {"bfd": [
        bfd_record("192.168.1.2", interface="ae0.100"),
        bfd_record("192.168.1.2"),  # multihop jine VRF
    ]}
    view = _ipvpn("customer-a").select(facts)
    assert view["bfd"]["192.168.1.2"]["interface"] == "ae0.100"
    assert view["bfd_ambiguous"] == {}


def test_bfd_single_multihop_is_owned_by_address():
    facts = {"bfd": [bfd_record("198.11.14.4")]}
    view = _ipvpn("CUST", peer="198.11.14.4").select(facts)
    assert view["bfd"]["198.11.14.4"]["multihop"] is True
```

(`_ipvpn` is the helper added in Task 1.)

Run: `.venv/bin/python -m pytest tests/models -q`
Expected: FAIL.

- [ ] **Step 5: Scope**

Replace `owns_bfd_session`. Keep the docstring's AR-14 reasoning and the `bgp_neighbors_inactive` asymmetry, and add the multihop rule:

```python
    def owns_bfd_session(
        self, record: dict[str, Any], records: list[dict[str, Any]]
    ) -> bool:
        neighbor = str(record.get("neighbor"))
        interface = record.get("interface")
        if interface:
            if neighbor in self.selectors.bgp_neighbors and self.selectors.matches_interface(
                str(interface)
            ):
                return True
            # Transit Core nema BFD zamery ani peery v konfiguraci sluzby -
            # session na nej patri podle rozhrani (spec 2026-08-26).
            return (
                self.service_type == "Core"
                and self.service_subtype == "transit"
                and self.selectors.matches_interface(str(interface))
            )
        # Multihop: rozhrani ani VRF nenese, zbyva adresa. Peer na vlastni
        # /30 je single-hop - kdyz scope takovou session na svem rozhrani
        # ma, multihop zaznam jine VRF na stejnou adresu mu nepatri.
        if neighbor not in self.selectors.bgp_neighbors:
            return False
        return not any(
            other.get("interface")
            and str(other.get("neighbor")) == neighbor
            and self.selectors.matches_interface(str(other["interface"]))
            for other in records
        )
```

In `select`, replace the `bfd_facts` / `bfd` / `bfd_collisions` block:

```python
        bfd_facts = list(facts.get("bfd") or [])
        bfd, bfd_ambiguous = _by_key(
            [r for r in bfd_facts if self.owns_bfd_session(r, bfd_facts)],
            "neighbor",
        )
```

In the returned dict, `"bfd_collisions"` becomes `"bfd_ambiguous": bfd_ambiguous`.

Run: `.venv/bin/python -m pytest tests/models -q`
Expected: PASS.

- [ ] **Step 6: NEZARAZENO BFD and baseline rename**

`_unassigned_bfd_sessions` over records. The long comment block stays. Replace its final paragraph about "tentyz predikat" with a note that the multihop rule needs all records:

```python
    records = list(subject.facts.get("bfd") or [])
    return [
        {
            "peer": record.get("neighbor"),
            "interface": record.get("interface"),
            "multihop": bool(record.get("multihop")),
            "state": record.get("state"),
            "snapshot": "subject",
        }
        for record in sorted(
            records,
            key=lambda r: (str(r.get("neighbor")), r.get("interface") or ""),
        )
        if not any(scope.owns_bfd_session(record, records) for scope in owners)
        and (port is None or _interface_on_port(str(record.get("interface") or ""), port, scopes))
    ]
```

`_aligned_baseline_data`: the view is still `{neighbor: record-with-interface}`, so the `_RENAMED_FIELD_DICT_AREAS` loop keeps working. A multihop record has `interface: None`. The existing loop does `rename.get(entry["interface"], entry["interface"])`, which returns `None` unchanged, so no change is needed. Verify that with a test in Step 8. Update the docstring phrase "bfd (klicovano peerem)" only if it became inaccurate (it didn't).

In `tests/test_engine.py`, convert the BFD facts to `bfd_records(...)`. Expected unassigned BFD entries gain `"multihop": False` (or `True` for multihop). Add:

```python
def test_unassigned_bfd_ambiguous_multihop_is_not_listed():
    # scope IPVPN s peerem 198.11.14.4, dva multihop zaznamy na tu adresu
    # -> oba vlastni scope (nejednoznacne), NEZARAZENO je nevypise
    ...
    assert result.unassigned["bfd_sessions"] == []
```

Write the elided setup with the helper the neighbouring NEZARAZENO tests use.

- [ ] **Step 7: checks/bfd.py**

- Imports: drop `ADDRESS_COLLISION` and import `ambiguous_value` from `checks.bgp`.
- `run`: `collisions` → `ambiguous: dict[str, int] = ctx.subject.get("bfd_ambiguous", {})`, `baseline_ambiguous` likewise. Pass `ambiguous=ambiguous.get(peer)` (`int | None`) and `baseline_ambiguous=baseline_ambiguous.get(peer)` into `_finding`. Keep the comment that an ambiguous address doesn't start a row by itself: the iteration set stays `intent | sessions | baseline_sessions`.
- `_finding` parameters: `ambiguous: int | None = None, baseline_ambiguous: int | None = None`.
- `was = ambiguous_value(baseline_ambiguous) if baseline is None and baseline_ambiguous else _session_value(...) if ctx.has_baseline else None`
- The collision branch becomes:

```python
        if ambiguous is not None:
            # Multihop session na adresu peera je vic a RPC nenese VRF -
            # kterou z nich sluzba ma, nejde rict. 'Session neexistuje' by
            # byla fabulace. Pred BGP vetvi: BGP polozka muze byt jednoznacna.
            return Finding(
                Outcome.SKIP,
                f"{peer}: stav BFD nelze urcit - multihop session na tuto adresu "
                f"je {ambiguous}x a RPC nenese VRF",
                label=label,
                family=family,
                value=ambiguous_value(ambiguous),
                baseline_value=was,
            )
```
- The last branch: `same=baseline is None and not baseline_ambiguous`.

`checks/bgp.py`: delete `ADDRESS_COLLISION` and its "Docasne" comment. `grep -rn ADDRESS_COLLISION migration_validator tests` must return nothing.

In `tests/checks/test_bfd.py`, rewrite the collision tests into ambiguity tests:

```python
def test_bfd_ambiguous_subject_is_skip():
    # scope bfd_peers=[{"peer": "198.11.14.4"}], bgp_neighbors=[...];
    # subject {"bfd": {}, "bfd_ambiguous": {"198.11.14.4": 2},
    #          "bgp": {"198.11.14.4": {"state": "Established"}}}
    # -> radek BFD (198.11.14.4) SKIP, value "neznamy (nejednoznacne: 2 session)"


def test_bfd_ambiguous_baseline_never_unchanged():
    # subject: session Down v "bfd"; baseline {"bfd": {}, "bfd_ambiguous": {peer: 2}}
    # -> Outcome.BROKEN, baseline_value "neznamy (nejednoznacne: 2 session)"


def test_bfd_ambiguous_without_intent_gets_no_row():
    # scope bez bfd_peers, subject bfd_ambiguous {peer: 2} -> findings == []
```

Write them with the file's existing context helpers.

- [ ] **Step 8: Transit BFD and baseline rename of a multihop record**

`bfd_transit_state` (`checks/core_protocols.py:473-545`) reads the view and needs no code change. In `tests/checks/test_core_protocols.py` and `tests/models/test_scope_core.py`, convert any device-level BFD facts to `bfd_records`. Add a test to `tests/test_engine.py` (next to the existing `_aligned_baseline_data` / R-6 tests):

```python
def test_aligned_baseline_keeps_multihop_bfd_interface_none():
    # baseline scope ge-0/0/2.100 -> subject scope et-0/0/8.100,
    # baseline fakta bfd_records multihop 198.11.14.4 (interface None)
    # -> aligned["bfd"]["198.11.14.4"]["interface"] is None (zadny KeyError, zadny rename)
```

- [ ] **Step 9: NEZARAZENO BFD renderer**

`text_report._unassigned_row`, last line:

```python
    interface = item.get("interface") or ("multihop" if item.get("multihop") else "-")
    return item["peer"], f"{interface}   {item.get('state') or '-'}"
```

`view.js`:

```js
  const iface = item.interface || (item.multihop ? "multihop" : "-");
  return { identity: item.peer, detail: `${iface}   ${item.state || "-"}` };
```

Add one Python test and one JS test that a multihop item renders `multihop   Up`.

- [ ] **Step 10: Shared synthetic facts**

In `tests/conftest.py` `_facts_for`, set `bfd = []`. The service BFD loop and the transit loop append records built like `bfd_record(...)` (inline dicts with the full key set, `multihop: False`). Dedupe on `(neighbor, interface)` the same way BGP was deduped in Task 1.

- [ ] **Step 11: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3` and `node --test tests/js/*.test.js 2>&1 | grep -E "^# (pass|fail)"`
Expected: all pass. `grep -rn "bfd_collisions\|bgp_collisions\|ADDRESS_COLLISION\|NO_BASELINE_COLLISION\|kolize adresy" migration_validator tests` → no hits.

```bash
git add -A migration_validator tests
git commit -m "feat(bfd): facts as records keyed by (neighbor, interface); multihop ambiguity replaces collisions

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: EVPN ESI records with per-IFL state

**Files:**
- Modify: `migration_validator/collectors/evpn.py:90-139` (`EvpnEsiCollector`)
- Modify: `migration_validator/capture.py:29` (add `"evpn_esi"`)
- Modify: `migration_validator/models/scope.py` (`select` ESI part)
- Modify: `migration_validator/checks/evpn.py:325-440` (`subject` of the local-status row + comments)
- Create: `tests/fixtures/cases/evpn_esi_two_instances.xml`, `tests/fixtures/cases/evpn_esi_two_ifls_one_instance.xml` (copies of the lab captures)
- Modify: `tests/fact_records.py`, `tests/conftest.py` (`_facts_for` ESI)
- Test: `tests/collectors/test_evpn.py`, `tests/models/test_scope.py`, `tests/test_engine.py`, `tests/checks/test_evpn.py`, `tests/models/test_snapshot.py`

**Interfaces:**
- Produces `EvpnEsiCollector.parse(...) -> list[dict]`. Record keys, exactly: `instance, esi, resolved_status, df_role, interfaces`. `interfaces` is `{ifl: {"status": str, "mode": str | None}}`.
- Produces the per-scope view `evpn_esi`: `{esi: {"resolved_status", "df_role", "interface", "status", "mode"}}`.
- Produces `tests/fact_records.py`: `esi_record(instance, esi, interfaces, *, resolved_status=..., df_role=...)`.

- [ ] **Step 1: Fixtures and builder**

```bash
cp docs/superpowers/lab-captures/2026-09-24/ptx1-pop1_evpn_esi_two_instances.xml tests/fixtures/cases/evpn_esi_two_instances.xml
cp docs/superpowers/lab-captures/2026-09-24/ptx1-pop1_evpn_esi_two_ifls_one_instance.xml tests/fixtures/cases/evpn_esi_two_ifls_one_instance.xml
```

Append to `tests/fact_records.py`:

```python
def esi_record(
    instance: str,
    esi: str,
    interfaces: dict[str, str],
    *,
    resolved_status: str | None = None,
    df_role: str | None = "10.0.0.1",
    mode: str = "all-active",
) -> dict[str, Any]:
    """interfaces: {ifl: stav}."""
    first = next(iter(interfaces), None)
    return {
        "instance": instance,
        "esi": esi,
        "resolved_status": resolved_status or (f"Resolved by IFL {first}" if first else None),
        "df_role": df_role,
        "interfaces": {
            name: {"status": status, "mode": mode} for name, status in interfaces.items()
        },
    }
```

- [ ] **Step 2: Failing collector tests**

In `tests/collectors/test_evpn.py`, rewrite `test_esi_schema`, `test_esi_emits_logical_unit_as_interface`, `test_auto_generated_esi_are_ignored` and `test_auto_generated_esi_does_not_hide_real_esi` for the list shape. The synthetic XML in `test_esi_emits_logical_unit_as_interface` must now wrap `evpn-esi` in an `evpn-instance` with an `evpn-interface-status-table`. Add:

```python
CASES = Path(__file__).resolve().parents[1] / "fixtures" / "cases"


def _case(name):
    return etree.parse(str(CASES / name)).getroot()


def test_esi_same_value_in_two_instances_keeps_both():
    records = EvpnEsiCollector().parse(_case("evpn_esi_two_instances.xml"), "junos-evo")
    by_instance = {r["instance"]: r for r in records if r["esi"] == "00:11:12:13:14:00:00:00:00:00"}
    assert set(by_instance) == {"EVPN-VLAN-AWARE-4093", "EVPN-VLAN-AWARE-POP1"}
    assert set(by_instance["EVPN-VLAN-AWARE-4093"]["interfaces"]) == {"ae0.4093"}
    assert set(by_instance["EVPN-VLAN-AWARE-POP1"]["interfaces"]) == {"ae0.4094"}


def test_esi_two_ifls_one_instance_lists_both():
    records = EvpnEsiCollector().parse(_case("evpn_esi_two_ifls_one_instance.xml"), "junos-evo")
    (record,) = [r for r in records if r["esi"] == "00:11:12:13:14:00:00:00:00:00"]
    assert record["interfaces"] == {
        "ae0.4093": {"status": "Up", "mode": "all-active"},
        "ae0.4094": {"status": "Up", "mode": "all-active"},
    }
    assert record["resolved_status"] == "Resolved by IFL ae0.4093"


def test_esi_single_homed_ifls_are_not_in_segment():
    records = EvpnEsiCollector().parse(_case("evpn_esi_two_ifls_one_instance.xml"), "junos-evo")
    for record in records:
        assert "et-0/0/8.4094" not in record["interfaces"]
        assert not record["esi"].startswith("05:")
```

(Check the `(record,)` unpack and the instance names against the actual capture content before relying on them. The capture comments say which instances and IFLs are present.)

Run: `.venv/bin/python -m pytest tests/collectors/test_evpn.py -q`
Expected: FAIL.

- [ ] **Step 3: Collector**

Update the `EvpnEsiCollector` docstring (the interface now comes from the per-instance status table, and the ESI block lists only one local IFL even when `num-local-intf` is 2, per the lab capture of 2026-09-24). Then:

```python
    def parse(self, xml: etree._Element, platform: str) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for instance_node in xml.iter("evpn-instance"):
            instance = _text(instance_node, "evpn-instance-name")
            if not instance:
                continue
            # Per-IFL stav nese jen tabulka instance: ESI blok vypisuje
            # jediny lokalni IFL i pri evpn-esi-num-local-intf = 2 (PTX
            # laborka 2026-09-24). Stejne ESI muze byt ve vic instancich
            # (per-port ESI na AE, units v ruznych EVI) - klic je proto
            # (instance, ESI), ne ESI.
            by_esi: dict[str, dict[str, dict[str, Any]]] = {}
            for iface in instance_node.iter("evpn-interface"):
                name = _text(iface, "evpn-interface-name")
                esi_value = _text(iface, "evpn-interface-esi")
                if not name or not esi_value:
                    continue
                by_esi.setdefault(esi_value, {})[name] = {
                    "status": _text(iface, "evpn-interface-status") or "unknown",
                    "mode": _text(iface, "evpn-interface-mode"),
                }
            for node in instance_node.iter("evpn-esi"):
                esi = _text(node, "evpn-esi-value")
                if not esi or esi.startswith("05:"):
                    continue
                df = node.find("evpn-esi-df-information")
                segments.append({
                    "instance": instance,
                    "esi": esi,
                    "resolved_status": _text(node, "evpn-esi-status"),
                    "df_role": _text(df, "esi-designated-forwarder"),
                    "interfaces": by_esi.get(esi, {}),
                })
        return segments
```

Keep the existing comments about `05:` ESIs, `resolved_status` and `df_role`, moved next to the new lines. Make sure `instance_node.iter("evpn-esi")` doesn't pick up elements nested deeper under a different instance. Instances are siblings, so `iter` from the instance node is safe. Check that `iter("evpn-interface")` doesn't also match `irb-interface` or `evpn-interface-status-table` (lxml `iter(tag)` matches exact tag names only, so it's fine).

`capture.py`: add `"evpn_esi"` to `LIST_AREAS`.

Run: `.venv/bin/python -m pytest tests/collectors/test_evpn.py -q`
Expected: PASS.

- [ ] **Step 4: Failing Scope test and Scope**

In `tests/models/test_scope.py`, convert the ESI facts and add:

```python
from fact_records import esi_record

ESI = "00:11:12:13:14:00:00:00:00:00"


def _elan(ri, iface):
    return Scope(
        id=f"svc:{iface}", kind="service",
        key=ScopeKey(description=iface, service_type="E-LAN"),
        selectors=Selectors(interfaces=[iface], physical_interfaces=["ae0"],
                            routing_instances=[ri]),
    )


def test_esi_shared_by_two_instances_each_scope_gets_its_ifl():
    facts = {"evpn_esi": [
        esi_record("EVI-A", ESI, {"ae0.4093": "Down"}),
        esi_record("EVI-B", ESI, {"ae0.4094": "Up"}),
    ]}
    a = _elan("EVI-A", "ae0.4093").select(facts)["evpn_esi"]
    b = _elan("EVI-B", "ae0.4094").select(facts)["evpn_esi"]
    assert a[ESI]["status"] == "Down" and a[ESI]["interface"] == "ae0.4093"
    assert b[ESI]["status"] == "Up" and b[ESI]["interface"] == "ae0.4094"


def test_esi_two_ifls_one_instance_scope_sees_only_own_ifl():
    facts = {"evpn_esi": [esi_record("EVI", ESI, {"ae0.4093": "Up", "ae0.4094": "Down"})]}
    view = _elan("EVI", "ae0.4093").select(facts)["evpn_esi"]
    assert view == {ESI: {
        "resolved_status": "Resolved by IFL ae0.4093", "df_role": "10.0.0.1",
        "interface": "ae0.4093", "status": "Up", "mode": "all-active",
    }}


def test_esi_of_other_instance_is_not_selected():
    facts = {"evpn_esi": [esi_record("EVI-B", ESI, {"ae0.4093": "Up"})]}
    assert _elan("EVI-A", "ae0.4093").select(facts)["evpn_esi"] == {}
```

Then in `Scope.select`, replace the `evpn_esi` block:

```python
        # Klic (instance, ESI) na zarizeni, ESI ve scopu: scope ma jednu RI
        # a jedno rozhrani, takze ESI je v nem jednoznacne. Stav je stav
        # IFL scopu z per-IFL tabulky, ne 'Up/Forwarding' IFL, ktery Junos
        # v ESI bloku nahodou vypsal (schema 14).
        evpn_esi: dict[str, dict[str, Any]] = {}
        for record in facts.get("evpn_esi") or []:
            if record.get("instance") not in self.selectors.routing_instances:
                continue
            for name, state in sorted((record.get("interfaces") or {}).items()):
                if self.selectors.matches_interface(name):
                    evpn_esi[str(record.get("esi"))] = {
                        "resolved_status": record.get("resolved_status"),
                        "df_role": record.get("df_role"),
                        "interface": name,
                        "status": state.get("status"),
                        "mode": state.get("mode"),
                    }
                    break
```

Run: `.venv/bin/python -m pytest tests/models/test_scope.py -q`
Expected: PASS.

- [ ] **Step 5: ESI check**

`checks/evpn.py` `_esi_block`: the local-status row's `subject` becomes `{"status": status, "interface": data.get("interface"), "mode": data.get("mode")}`. Update the comments that talk about `Up/Forwarding` from the ESI block. `_is_up`'s docstring stays valid, since it accepts both forms.

Add to `tests/checks/test_evpn.py`:

```python
def test_esi_status_row_uses_per_ifl_status_and_carries_mode():
    # subject view {"evpn_esi": {ESI: {"resolved_status": "Resolved by IFL ae0.4093",
    #   "df_role": "10.0.0.1", "interface": "ae0.4093", "status": "Down", "mode": "all-active"}}}
    # -> radek "ESI Local interface status": Outcome BROKEN (bez baseline), value "Down",
    #    subject == {"status": "Down", "interface": "ae0.4093", "mode": "all-active"}
```

Write it with the existing ESI check test helpers in that file. Convert any other ESI test that builds **device** facts. Per-scope view dicts in check tests stay as they are, except that `status` values of `Up/Forwarding` may stay (the check accepts both).

- [ ] **Step 6: Engine and synthetic facts**

- `tests/test_engine.py`: convert the ESI device facts to `esi_record(...)`. The `_aligned_baseline_data` rename of `evpn_esi` still works on the view (`interface` field), so there's no code change. Make sure the existing R-6 ESI rename test still passes.
- `tests/conftest.py` `_facts_for`: `evpn_esi = []`, and the E-LAN branch appends:

```python
                evpn_esi.append({
                    "instance": instance,
                    "esi": f"esi-{instance}",
                    "resolved_status": None,
                    "df_role": "DF",
                    "interfaces": {
                        scope.selectors.interfaces[0]: {"status": "Up", "mode": "all-active"}
                    },
                })
```

If two E-LAN scopes share an instance, append the IFL into the existing record's `interfaces` rather than creating a duplicate (instance, ESI) record.
- `tests/models/test_snapshot.py`: convert the ESI literal.

- [ ] **Step 7: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: all pass.

```bash
git add -A migration_validator tests
git commit -m "feat(evpn): ESI facts keyed by (instance, ESI) with per-IFL state from the interface table

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Route records keyed by (rib, prefix, protocol)

**Files:**
- Modify: `migration_validator/collectors/routes.py`
- Modify: `migration_validator/capture.py:29` (add `"routes"`)
- Modify: `migration_validator/models/scope.py` (`select` routes part)
- Modify: `migration_validator/engine.py:498-542` (`_unassigned_static_routes`)
- Modify: `migration_validator/checks/routes.py:77-87` (`_flatten`) and the module docstring lines 17-25
- Modify: `migration_validator/checks/multicast.py:602`
- Modify: `tests/fact_records.py`, `tests/conftest.py` (`_facts_for` routes)
- Test: `tests/collectors/test_routes.py`, `tests/models/test_scope.py`, `tests/test_engine.py`, `tests/checks/test_routes.py`, `tests/checks/test_multicast.py`

**Interfaces:**
- Produces `RoutesCollector.parse(...) -> list[dict]` and `collect(...) -> list[dict]`. Record keys, exactly: `rib, prefix, protocol, next_hop, via, active`.
- Produces the per-scope view `routes`: `{protocol: {rib: {prefix: record}}}`.
- Produces `checks.routes._flatten(routes, protocol) -> dict[tuple[str, str], dict]`, same signature, new input shape.
- Produces `tests/fact_records.py`: `route_record(rib, prefix, *, protocol="static", next_hop=(), via=(), active=True)` and `route_records(mapping)`.

- [ ] **Step 1: Builders**

```python
def route_record(
    rib: str,
    prefix: str,
    *,
    protocol: str = "static",
    next_hop: list[str] | tuple[str, ...] = (),
    via: list[str] | tuple[str, ...] = (),
    active: bool = True,
) -> dict[str, Any]:
    return {
        "rib": rib, "prefix": prefix, "protocol": protocol,
        "next_hop": list(next_hop), "via": list(via), "active": active,
    }


def route_records(mapping: dict[str, dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """{rib: {prefix: data}} -> [zaznam]; chybejici protocol = static."""
    return [
        {**route_record(rib, prefix), **data, "rib": rib, "prefix": prefix,
         "protocol": data.get("protocol", "static")}
        for rib, prefixes in mapping.items()
        for prefix, data in prefixes.items()
    ]
```

- [ ] **Step 2: Failing collector tests**

Rewrite the dict-based tests in `tests/collectors/test_routes.py` (`test_returns_mapping_of_tables`, `test_entries_have_expected_keys`, `test_table_name_carries_rib_and_family`, `test_management_routes_are_collected_not_filtered`, `test_empty_tables_are_dropped`, `test_collect_merguje_oba_pruchody_a_druhy_jen_pridava`, `test_two_rt_entries_of_one_prefix_are_merged`, `test_single_entry_route_shape_is_unchanged`, and the `test_parse_*` ones) to iterate records. `test_empty_tables_are_dropped` becomes "no record from an empty table". Replace the "second pass only adds" test with:

```python
def test_collect_keeps_static_and_aggregate_on_same_prefix():
    static_xml = etree.fromstring(
        "<route-information><route-table><table-name>CUST.inet.0</table-name>"
        "<rt><rt-destination>10.1.0.0/24</rt-destination><rt-entry>"
        "<active-tag>*</active-tag><protocol-name>Static</protocol-name>"
        "<nh><to>192.168.1.2</to><via>ae0.100</via></nh></rt-entry></rt>"
        "</route-table></route-information>"
    )
    aggregate_xml = etree.fromstring(
        "<route-information><route-table><table-name>CUST.inet.0</table-name>"
        "<rt><rt-destination>10.1.0.0/24</rt-destination><rt-entry>"
        "<protocol-name>Aggregate</protocol-name><nh-type>Reject</nh-type></rt-entry></rt>"
        "</route-table></route-information>"
    )
    device = FakeDevice(...)  # stejny fake jako dnesni test_collect_* testy
    records = RoutesCollector().collect(device, "junos")
    assert sorted((r["rib"], r["prefix"], r["protocol"], r["active"]) for r in records) == [
        ("CUST.inet.0", "10.1.0.0/24", "aggregate", False),
        ("CUST.inet.0", "10.1.0.0/24", "static", True),
    ]
```

Use the fake-device pattern that the existing `test_collect_*` tests in that file use for `FakeDevice(...)`. It returns `static_xml` for `protocol="static"` and `aggregate_xml` for `protocol="aggregate"`.

Run: `.venv/bin/python -m pytest tests/collectors/test_routes.py -q`
Expected: FAIL.

- [ ] **Step 3: Collector**

- Module docstring: replace the paragraph claiming that a prefix can't be static and aggregate at once, and that the second pass "only adds". The new text: the user confirmed (2026-09-24) that static and aggregate can share a (rib, prefix), so each protocol is its own record and the two passes just concatenate.
- `parse` returns `list[dict]`. For each table and prefix, group the `rt-entry` elements **by protocol**, and for each protocol append `{"rib": name, "prefix": prefix, **_merge_entries(entries_of_protocol)}`. `_merge_entries` already returns `protocol`. Drop the "empty tables" comment's dict wording and keep its point: no records from empty tables.
- `collect`: `records: list[dict] = []`, and `records.extend(parsed)` per pass. The `setdefault` block and its comment go away. Return `records`.
- `capture.py`: add `"routes"` to `LIST_AREAS`.

Run: `.venv/bin/python -m pytest tests/collectors/test_routes.py -q`
Expected: PASS.

- [ ] **Step 4: Failing Scope and check tests, then Scope, `_flatten`, multicast**

`tests/models/test_scope.py`:

```python
from fact_records import route_record


def test_routes_view_is_keyed_by_protocol_and_selector_route_type():
    scope = Scope(
        id="svc:x", kind="service", key=ScopeKey(description="x", service_type="IPVPN"),
        selectors=Selectors(interfaces=["ae0.100"], routing_instances=["CUST"], static_routes=[
            {"rib": "CUST.inet.0", "prefix": "10.1.0.0/24", "route_type": "static", "next_hops": []},
        ]),
    )
    facts = {"routes": [
        route_record("CUST.inet.0", "10.1.0.0/24", protocol="static", via=["ae0.100"]),
        route_record("CUST.inet.0", "10.1.0.0/24", protocol="aggregate", active=False),
    ]}
    view = scope.select(facts)["routes"]
    assert set(view) == {"static"}
    assert view["static"]["CUST.inet.0"]["10.1.0.0/24"]["via"] == ["ae0.100"]
```

Also add a second test with both selectors (static + aggregate on the same prefix) and assert `set(view) == {"static", "aggregate"}`.

`Scope.select` routes block:

```python
        # Selektor (rib, prefix, route_type): static a aggregate muzou sdilet
        # (rib, prefix) (overeno 2026-09-24), takze bez protokolu by statika
        # vybrala i agregat. Chybejici route_type = static (dnesni default).
        wanted_routes = {
            (str(route.get("rib")), str(route.get("prefix")), str(route.get("route_type", "static")))
            for route in self.selectors.static_routes
        }
        routes: dict[str, dict[str, dict[str, Any]]] = {}
        for record in facts.get("routes") or []:
            key = (str(record.get("rib")), str(record.get("prefix")), str(record.get("protocol")))
            if key in wanted_routes:
                routes.setdefault(key[2], {}).setdefault(key[0], {})[key[1]] = record
```

`checks/routes.py`:

```python
def _flatten(
    routes: dict[str, Any] | None, protocol: str
) -> dict[tuple[str, str], dict[str, Any]]:
    # Pohled scopu je {protokol: {rib: {prefix: zaznam}}} (schema 14) -
    # statika a agregat na stejnem (rib, prefix) jsou dva zaznamy.
    return {
        (table, prefix): data
        for table, prefixes in ((routes or {}).get(protocol) or {}).items()
        for prefix, data in prefixes.items()
    }
```

Update the module docstring lines 17-25 (the "`routes` jsou klicovane table -> prefix" sentence).

`checks/multicast.py:602`: `inet2 = ((ctx.subject.get("routes") or {}).get("static") or {}).get("inet.2") or {}`.

Convert `tests/checks/test_routes.py` and `tests/checks/test_multicast.py`: every per-scope `"routes": {rib: {prefix: {..., "protocol": p}}}` view becomes `{p: {rib: {prefix: {...}}}}`. Add to `tests/checks/test_routes.py`:

```python
def test_static_and_aggregate_on_same_prefix_each_check_sees_its_own():
    # scope static_routes = [static 10.1.0.0/24 next_hop 192.168.1.2, aggregate 10.1.0.0/24]
    # subject view {"routes": {"static": {"CUST.inet.0": {"10.1.0.0/24": {... active True}}},
    #                          "aggregate": {"CUST.inet.0": {"10.1.0.0/24": {... active False}}}}}
    # -> static check: OK radek pro prefix; aggregate check: radek pro prefix
    #    s hodnotou pritomnosti "v tabulce" (ne "chybi")
```

Write it with the existing helpers in that file.

- [ ] **Step 5: NEZARAZENO routes**

`engine._unassigned_static_routes`:

```python
    assigned = {
        (str(route.get("rib")), str(route.get("prefix")), str(route.get("route_type", "static")))
        for scope in scopes
        for route in scope.selectors.static_routes
    }

    def _on_port(record: dict[str, Any]) -> bool:
        if port is None:
            return True
        via = [str(name) for name in record.get("via", [])]
        if via:
            return any(_interface_on_port(name, port, scopes) for name in via)
        instance = _rib_instance(str(record.get("rib")))
        return instance is not None and instance in _scope_instances(scopes)

    records = sorted(
        subject.facts.get("routes") or [],
        key=lambda r: (str(r.get("rib")), str(r.get("prefix")), str(r.get("protocol"))),
    )
    return [
        {
            "rib": record.get("rib"),
            "prefix": record.get("prefix"),
            "next_hop": record.get("next_hop", []),
            "via": record.get("via", []),
            "protocol": str(record.get("protocol", "static")),
            "snapshot": "subject",
        }
        for record in records
        if (str(record.get("rib")), str(record.get("prefix")), str(record.get("protocol", "static")))
        not in assigned
        and _on_port(record)
    ]
```

Add a sentence to the docstring: an owned static route on the same prefix doesn't hide an unowned aggregate.

In `tests/test_engine.py`, convert the route facts to `route_records(...)` and add:

```python
def test_unassigned_aggregate_on_prefix_of_owned_static_is_listed():
    # scope vlastni jen static CUST.inet.0 10.1.0.0/24; fakta maji static i aggregate
    # -> unassigned["static_routes"] == [ {... "protocol": "aggregate" ...} ]
```

Write the setup with the helper the neighbouring NEZARAZENO route tests use.

- [ ] **Step 6: Shared synthetic facts, full suite, commit**

`tests/conftest.py` `_facts_for`: `routes = []`, and append one record per selector:

```python
        for route in scope.selectors.static_routes:
            routes.append({
                "rib": str(route["rib"]),
                "prefix": str(route["prefix"]),
                "protocol": str(route.get("route_type", "static")),
                "next_hop": [hop["to"] for hop in route.get("next_hops") or [] if hop["active"]],
                "via": list(scope.selectors.interfaces[:1]),
                "active": True,
            })
```

Dedupe on `(rib, prefix, protocol)`. The old dict dedupe was last-wins per (rib, prefix); keep the **first** occurrence and note that in a comment.

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: all pass.

```bash
git add -A migration_validator tests
git commit -m "feat(routes): route facts keyed by (rib, prefix, protocol); static and aggregate coexist

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: PIM neighbours, IPv4 only

**Files:**
- Modify: `migration_validator/collectors/pim.py:1-50`
- Create: `tests/fixtures/cases/pim_neighbors_dual_stack.xml` (copy of the lab capture)
- Test: `tests/collectors/test_pim.py`

**Interfaces:**
- `PimNeighborCollector.parse` keeps the shape `{interface: {"neighbor_address", "uptime_seconds"}}`.

- [ ] **Step 1: Fixture and failing tests**

```bash
cp docs/superpowers/lab-captures/2026-09-24/ptx1-pop1_pim_neighbors_basic.xml tests/fixtures/cases/pim_neighbors_dual_stack.xml
```

Add to `tests/collectors/test_pim.py`:

```python
from pathlib import Path

CASES = Path(__file__).resolve().parents[1] / "fixtures" / "cases"


def test_dual_stack_v6_neighbor_does_not_overwrite_v4():
    xml = etree.parse(str(CASES / "pim_neighbors_dual_stack.xml")).getroot()
    result = PimNeighborCollector().parse(xml, "junos-evo")
    assert result["et-0/0/1.0"] == {"neighbor_address": "10.1.0.4", "uptime_seconds": 87773}
    assert result["et-0/0/0.0"]["neighbor_address"] == "10.1.1.2"


def test_neighbor_without_ip_protocol_version_counts_as_v4():
    xml = etree.fromstring(
        "<pim-neighbors-information><pim-interface><pim-neighbor>"
        "<pim-interface-name>et-0/0/2.0</pim-interface-name>"
        "<pim-neighbor-address>10.9.0.2</pim-neighbor-address>"
        "</pim-neighbor></pim-interface></pim-neighbors-information>"
    )
    assert PimNeighborCollector().parse(xml, "junos")["et-0/0/2.0"]["neighbor_address"] == "10.9.0.2"


def test_only_v6_neighbor_gives_no_key():
    xml = etree.fromstring(
        "<pim-neighbors-information><pim-interface><pim-neighbor>"
        "<pim-interface-name>et-0/0/2.0</pim-interface-name>"
        "<ip-protocol-version>6</ip-protocol-version>"
        "<pim-neighbor-address>fe80::1</pim-neighbor-address>"
        "</pim-neighbor></pim-interface></pim-neighbors-information>"
    )
    assert PimNeighborCollector().parse(xml, "junos") == {}
```

(If the existing tests use namespaced XML, `{*}` iteration in the collector handles both.)

Run: `.venv/bin/python -m pytest tests/collectors/test_pim.py -q`
Expected: the dual-stack test FAILs.

- [ ] **Step 2: Collector**

Iterate the neighbour nodes, not "first neighbour per `pim-interface`":

```python
    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        neighbors: dict[str, dict[str, Any]] = {}
        for interface_node in xml.iter("{*}pim-interface"):
            interface = _localname_text(interface_node, "pim-interface-name")
            if not interface:
                continue
            for neighbor_node in interface_node.iter("{*}pim-neighbor"):
                # Kazda rodina ma vlastni pim-interface se stejnym jmenem,
                # v6 az za v4 (PTX laborka 2026-09-24) - bez filtru v6 soused
                # prepsal v4. IPv6 PIM se nevyhodnocuje; chybejici verze = v4.
                if _localname_text(neighbor_node, "ip-protocol-version") == "6":
                    continue
                if interface in neighbors:
                    break
                uptime = next(neighbor_node.iter("{*}pim-neighbor-uptime"), None)
                neighbors[interface] = {
                    "neighbor_address": _localname_text(neighbor_node, "pim-neighbor-address"),
                    "uptime_seconds": _seconds_attr(uptime),
                }
                break
        return neighbors
```

Update the module docstring: add the dual-stack paragraph, and keep "first v4 neighbour on an interface wins" (LAN is out of scope). Check `_localname_text`'s semantics. It searches descendants by local name, so calling it on `neighbor_node` for `ip-protocol-version` is correct, because the element sits inside `<pim-neighbor>`.

Run: `.venv/bin/python -m pytest tests/collectors/test_pim.py -q`
Expected: PASS.

- [ ] **Step 3: Full suite, then commit**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3`
Expected: all pass.

```bash
git add -A migration_validator tests
git commit -m "fix(pim): IPv6 neighbour no longer overwrites IPv4 on a dual-stack interface

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: End-to-end production scenario, docs, acceptance

**Files:**
- Test: `tests/test_end_to_end.py` (or `tests/test_engine.py` if the e2e file only drives inventories from fixtures; see Step 1)
- Modify: `docs/superpowers/followup-2026-09-23-kolize-adres-peeru-vrstva-2.md` (status header)
- Modify: `docs/superpowers/review-2026-09-24-kolize-klicu.md` (status of F1, F2, F3, F5, F8)
- Modify: `migration_validator/models/scope.py` class docstrings and `engine.py` comments that still mention collisions (grep `kolize`, `klicovan`, `prepsal`)

**Interfaces:**
- Consumes everything from Tasks 1–5.

- [ ] **Step 1: The production MX → ACX scenario, end to end**

Build two snapshots (baseline MX, subject ACX) through `evaluate_snapshots`, using the helper in `tests/test_engine.py` that builds a snapshot with explicit scopes and facts (grep `def _snapshot` / `Snapshot(`):

- Scopes, in both snapshots:
  - IPVPN `customer-a`: interface `ae0.100` (baseline) / `et-0/0/1.100` (subject), RI `customer-a`, `bgp_neighbors=["192.168.1.2"]`, `bfd_peers=[{"peer": "192.168.1.2", "multiplier": 3}]`.
  - IPVPN `customer-b`: the same shape, with `ae0.200` / `et-0/0/1.200` and RI `customer-b`.
- Baseline facts: `bgp_records` for both VRFs, both `Established`, each with a RIB `customer-X.inet.0` (counts 3/3/3/5). `bfd_record`s on `ae0.100` and `ae0.200`, both Up. Collector status `ok` for `bgp` and `bfd` in `capture.collectors`.
- Subject facts: the same, except that customer-a's BGP is `Idle` with no RIBs and its BFD is `Down`.

```python
    b = next(r for r in result.scopes if "customer-b" in r.scope_id)
    a = next(r for r in result.scopes if "customer-a" in r.scope_id)
    labels_b = [f.label for f in b.findings]
    assert b.status is Status.PASS
    assert not any("customer-a" in (f.label or "") for f in b.findings)
    assert not any(f.value == "v baseline patril k teto sluzbe, v subjektu uz ne" for f in b.findings)
    assert a.status is Status.FAIL
    assert any(f.label == "BGP status (192.168.1.2)" and f.value == "Idle" for f in a.findings)
    assert result.unassigned["bgp_peers"] == [] and result.unassigned["bfd_sessions"] == []
```

Adapt the attribute names (`scope_id`, `status`, `findings`, `Status`) to the actual `RunResult` / `ScopeResult` API used by the neighbouring tests. The asserts above are the requirement.

Run: `.venv/bin/python -m pytest tests/test_engine.py -q -k customer`
Expected: PASS. It must pass without any further production change. If it doesn't, it has found a real gap: fix it in the task that owns the code, not by weakening the test.

- [ ] **Step 2: Stale wording sweep**

`grep -rn "kolize\|collision\|klicovan.* jen adresou\|prepsal\|last-write" migration_validator` and fix every comment or docstring that describes Layer 1 behaviour as current. `grep -rn "13" migration_validator/models/snapshot.py` should only show the history comment.

- [ ] **Step 3: Docs**

- Follow-up note, first paragraph: "**Stav: vrstva 2 implementovaná na větvi `vrstva-2-kolize-klicu` (2026-09-25), spec `docs/superpowers/specs/2026-09-25-kolize-klicu-vrstva-2-design.md`.**"
- Review doc: under the summary table, add a line saying which of F1, F2, F3, F5 and F8 layer 2 resolved, and that F4, F6 and F7 are out of scope by the user's decision.

- [ ] **Step 4: Full verification**

Run: `.venv/bin/python -m pytest -q 2>&1 | tail -3` and `node --test tests/js/*.test.js 2>&1 | grep -E "^# (pass|fail)"`
Expected: all pass. Record the counts in the commit message body.

- [ ] **Step 5: Commit**

```bash
git add -A migration_validator tests docs
git commit -m "test: production MX->ACX shared-peer scenario end to end; docs for layer 2

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Lab acceptance (controller, not a subagent)**

1. Copy `runs/test-no-inventory` to the scratchpad. Run `mig-validate upgrade` on the copy (`.venv/bin/mig-validate upgrade <copy>`; check `--help` for the exact form). Evaluate with the pre-wave tool (`git stash`/worktree at `a5c0c6d`) and with the branch, and diff the text reports. Expected differences: the PIM row on et-0/0/1.0 (v4 address and uptime) and the ESI local status values (`Up` instead of `Up/Forwarding`). Anything else is a finding, to be investigated before finishing.
2. Replay the raw test bundles (`tests/fixtures/raw/junos`, `junos-evo`) through the upgrade path. `tests/raw/test_lab_bundles.py` does this already, so it's covered by the suite.
3. Tell the user to restart the lab GUI after merge.
