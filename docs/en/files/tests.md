# `tests/` — the test suite

```bash
.venv/bin/pytest          # 305 passed, 1 skipped (~1.2 s)
```

**No test needs a network or a lab.** That is a direct dividend of the checks being pure
functions and of the raw RPC XML being recorded into fixtures. The only layer that would want
a real box is `connection/` — and it is deliberately thin enough to be tested through a
`FakeDevice`.

One test is permanently skipped: `test_esi_interface_matches_a_scope[junos]` — the MX
recording contains no ESI segment, so there is nothing to verify.

---

## Structure

| path | what it tests |
|---|---|
| `conftest.py` | the shared `synthetic_snapshot` fixture — builds a snapshot from the **real inventory** |
| `test_package.py` | the package version |
| `test_engine.py` | evaluation orchestration (13 tests) |
| `test_capture.py` | collection orchestration through `FakeDevice` |
| `test_cli.py` | subcommands, exit codes, error messages |
| `test_end_to_end.py` | a full run over the real inventory of both devices |
| `models/` | `inventory`, `scope`, `snapshot`, `result` |
| `connection/` | `detect_platform`, `device_meta`, PyEZ kwargs assembly |
| `collectors/` | per collector over recorded XML + **conformance tests** |
| `probes/` | target resolution and ping response parsing |
| `scoping/` | builder, matcher, mapping |
| `checks/` | per check over hand-written minimal fixtures |
| `reporting/` | the text report and filtering |
| `fixtures/` | the inventory of both devices + `rpc/{junos,junos-evo}/*.xml` |

---

## `fixtures/` — recorded data

```
tests/fixtures/
├── 172.20.20.4.yml            inventory from the MX
├── 172.20.20.5.yml            inventory from the PTX (EVO)
└── rpc/
    ├── junos/                 interfaces, arp, bgp, evpn_vpws, evpn_esi,
    │                          evpn_mac, evpn_mac.2
    └── junos-evo/             the same minus evpn_mac.2 (EVO has only one RPC)
```

Fixtures are recorded with
`mig-validate record --device <ip> --output-dir tests/fixtures/rpc`. Unfamiliar output from
production gets copied here and the regression test is done — it is the only sustainable way
to keep the collectors from rotting.

**Fixtures from both platforms are mandatory.** That enforces platform differences being
handled in the collector rather than leaking into the checks — the premise the whole
cross-device comparison rests on.

The file `evpn_mac.2.xml` exists only for `junos`, because MX needs two RPCs for the MAC
table. If `record` stored only the first, vlan-based instances would be missing.

---

## The conformance tests — the most important file

`tests/collectors/test_conformance.py` is the only test that **pins both halves of the
collector ↔ check seam against each other**:

1. it builds facts with the **real collectors** from **recorded lab XML** (no manual
   patching),
2. builds scopes from the **real inventory**,
3. runs it through the **real checks** via `api.evaluate()`,
4. asserts the checks did not return **all `SKIP`**.

Why this is needed: when a collector renames a key, the check does not find it and returns
`SKIP`. **And because `SKIP` is not `FAIL`, nothing else shows it** — the tool silently stops
checking an entire area. The other offline tests do not catch it, because their fixtures build
data from the same selectors as the scope, so they are consistent by construction.

The specific assertions:

| test | what it guards |
|---|---|
| `test_collector_keys_match_contract` | the `facts` keys match the collector names |
| `test_evpn_instances_are_keyed_by_routing_instance` | `evpn_vpws` and `evpn_mac` are keyed by instance name, not by interface |
| `test_esi_interface_matches_a_scope` | `evpn_esi.interface` is a name a scope actually matches |
| `test_interfaces_reach_their_scopes` | at least one scope sees at least one interface |
| `test_checks_produce_real_verdicts_not_all_skip` | over real data, at least one non-`SKIP` verdict appears |
| `test_specific_check_sees_data` | per area and platform — so a failure shows **which** seam came apart |

---

## Patterns that recur in the tests

**`FakeDevice` / `FakeRpc`** (`test_capture.py`, `collectors/test_base.py`,
`connection/test_junos.py`) — an object with `facts` and `rpc`, where `__getattr__` returns
prepared XML. It can also fail on demand
(`FakeDevice(failing=("get_bgp_neighbor_information",))`), so failed-collector behaviour is
testable without any mocking framework.

**`synthetic_snapshot`** (`conftest.py`) — builds a snapshot from the real inventory by
deriving facts from the scopes' selectors. The end-to-end tests use it: they exercise
orchestration and pairing, not parsing.

**Deterministic time** — `now=NOW` is passed everywhere so results are comparable.

---

## What each area guards (a selection)

| test | guards against |
|---|---|
| `test_capture.py::test_every_area_is_registered_for_both_platforms` | a collector forgotten in `all.py` would silently drop a whole area |
| `test_capture.py::test_failed_arp_collector_leaves_ping_empty` | without ARP, no bogus probe may be created (and `all()` over an empty list asserts nothing — the test handles that explicitly) |
| `test_capture.py::test_record_raw_writes_every_rpc_of_multi_rpc_collector` | a fixture from only the first RPC would be silently incomplete |
| `test_engine.py::test_healthy_scope_without_baseline_is_pass_not_skip` | a healthy service must not glow `SKIP` merely because of a compare-only check |
| `test_engine.py::test_failed_collector_produces_skip_not_pass` | missing data never yields a PASS |
| `test_engine.py::test_unmatched_subject_scope_is_still_state_validated` | a new service is still checked, it just has nothing to compare to |
| `test_end_to_end.py::test_management_interfaces_never_appear` | neither `fxp0` nor `mgmt` may appear in the output |
| `test_end_to_end.py::test_render_after_filter_still_shows_unmatched_section` | a filter must not hide `NESPAROVANO` |
| `test_end_to_end.py::test_evpn_checks_produce_real_verdicts_on_real_data` | the EVPN fact schema must not silently slide into all-`SKIP` |
| `scoping/test_matcher.py::test_ambiguity_never_guesses` | unpaired beats a silently wrong match |
| `scoping/test_matcher.py::test_ambiguous_under_one_key_is_not_paired_under_a_sibling_key` | a scope must not end up in both `pairs` and `unmatched` |
| `checks/test_base.py::test_missing_data_never_passes` | the overriding rule of the whole tool |
| `models/test_result.py::test_degraded_is_warn_even_when_critical` | "partial success = WARN" holds even at `critical` |
| the `cli` tests around codes 0/1/2 | *the tool failed* ≠ *a test failed* |

A note on `collectors/test_base.py`: it registers its own demo collectors into the **same
module-level registry**, which is why tests elsewhere compare the registry with `>=` rather
than `==`.
