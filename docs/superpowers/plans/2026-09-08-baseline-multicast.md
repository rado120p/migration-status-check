# Baseline porovnání — vlna 3: multicast — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chybějící S,G a sender bez receiveru jsou WARN, ne FAIL; c-multicast a core inet.2 řádky nesou správné baseline hodnoty nebo značku „neporovnává se", takže „bez baseline" a „bylo (S,G)" z produkčního reportu zmizí.

**Architecture:** Změny jen v `checks/multicast.py`. `stream_rows()` dostane baseline routu pro forwarding rate; `_summary()` rozlišuje tvrdé a měkké selhání; `multicast_forwarding_status` přejde na BOTH s `compared=False` na neporovnávaných řádcích.

**Tech Stack:** Python 3.13, pytest (`pyats-venv/bin/python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-09-08-baseline-porovnani-a-multicast-opravy-design.md` (R-9)

## Global Constraints

- Větev `baseline-porovnani-2026-09-08`, po vlnách 1 a 2 (`Finding.compared`, `unchanged_or`, `ctx.baseline_measured`).
- Přesné texty (bez diakritiky):
  - měkký souhrn: `{n}/{total} S,G bez streamu` (value), message `{n} z {total} S,G bez streamu`
  - provider tunnel value: `{tunnel} (PE {pe})`, baseline při změně PE: `PE {was_pe}`
- Rozhodnutí 2026-09-07 (role, sender site DEGRADED, kaskáda SKIP) se nemění.
- Suite zelená po každém tasku: `pyats-venv/bin/python -m pytest -q`.

---

## Task 1: `multicast_forwarding_status` — WARN pro chybějící S,G a sender bez downstreamu, BOTH + značky

**Files:**
- Modify: `migration_validator/checks/multicast.py:352-360` (`_summary`), `:363-451` (check + `_stream`)
- Test: `tests/checks/test_multicast.py`

**Interfaces:**
- Produces: `_summary(ctx, label, total, hard, soft, soft_unchanged) -> Finding`; `MulticastForwardingStatusCheck.mode = Mode.BOTH`; `_stream(...)` vrací řádky s `compared=False` (Stream, Upstream, Forwarding-rate, Route uptime).

- [ ] **Step 1: Failing testy** (helpery `_ctx`, `_scope`, `_igmp`, `_pim`, `_join`, `_facts(iface, instance, pairs, routes)`, `_route(upstream, downstream, pps, uptime)`, `_by_label(findings, group)`, `POST`, `PRE`, `SG` už v souboru jsou — nepřidávej nové se stejnými jmény)

```python
from migration_validator.models.result import NOT_COMPARED, UNCHANGED_SINCE_BASELINE

KEY = f"{SG[0]},{SG[1]}"


def test_sg_missing_from_table_is_warn_and_summary_is_soft():
    rows = run_check(MulticastForwardingStatusCheck(), _ctx({**_facts(routes={}), **_pim()}))
    summary, stream = rows[0], rows[1]
    assert stream.status is Status.WARN and stream.value == "S,G neni v multicast tabulce"
    assert summary.status is Status.WARN and summary.value == "1/1 S,G bez streamu"


def test_sg_missing_in_both_is_unchanged_pass():
    subject = {**_facts(routes={}), **_pim()}
    baseline = {**_facts(iface=PRE, routes={}), **_pim()}
    rows = run_check(MulticastForwardingStatusCheck(),
                     _ctx(subject, baseline=baseline, scope=_scope(POST), baseline_scope=_scope(PRE)))
    summary, stream = rows[0], rows[1]
    assert stream.status is Status.PASS and stream.details[UNCHANGED_SINCE_BASELINE] is True
    assert stream.baseline_value == stream.value
    assert summary.status is Status.PASS


def test_sender_without_downstream_is_warn():
    subject = {**_pim("master", _join(upstream=POST, downstream=[])),
               "igmp_group": {}, "multicast_route": {"master": {KEY: _route(upstream=POST, downstream=[])}}}
    rows = run_check(MulticastForwardingStatusCheck(), _ctx(subject, scope=_scope(POST, protocols=("pim",))))
    assert _by_label(rows, sg_label(*SG))["Stream"].status is Status.WARN
    assert rows[0].status is Status.WARN and rows[0].value == "1/1 S,G bez streamu"


def test_receiver_stream_not_forwarded_is_still_fail():
    subject = {**_facts(routes={KEY: _route(downstream=["other.0"])}), **_pim()}
    rows = run_check(MulticastForwardingStatusCheck(), _ctx(subject))
    assert rows[0].status is Status.FAIL and rows[0].value == "1/1 S,G nefunguje"


def test_forwarding_rows_are_not_compared_except_rate_with_baseline():
    subject = {**_facts(routes={KEY: _route(pps=9)}), **_pim()}
    baseline = {**_facts(iface=PRE, routes={KEY: _route(downstream=[PRE], pps=6)}), **_pim()}
    rows = run_check(MulticastForwardingStatusCheck(),
                     _ctx(subject, baseline=baseline, scope=_scope(POST), baseline_scope=_scope(PRE)))
    uncompared = {r.label for r in rows if r.details.get(NOT_COMPARED) is False}
    assert uncompared == {"Multicast forwarding status", "Stream", "Upstream interface", "Route uptime"}
    rate = _by_label(rows, sg_label(*SG))["Forwarding-rate"]
    assert rate.baseline_value == "6 pps" and NOT_COMPARED not in rate.details
```

- [ ] **Step 2: Ověř pád** — `pyats-venv/bin/python -m pytest tests/checks/test_multicast.py -q -k "soft or unchanged or sender_without or not_compared"` → FAIL.

- [ ] **Step 3: Implementace**

```python
def _summary(ctx: CheckContext, label: str, total: int, hard: int, soft: int, soft_unchanged: int) -> Finding:
    """Tvrde selhani (stream se na receiver neposila, upstream ze spatne
    role) = BROKEN. Jen mekke (S,G neni v tabulce, sender bez receiveru,
    rozhodnuti 2026-09-08) = DEGRADED; kdyz vsechna mekka byla i v baseline,
    je souhrn UNCHANGED jako jeho radky."""
    if hard:
        return Finding(
            Outcome.BROKEN, f"{hard} z {total} S,G nefunguje",
            label=label, value=f"{hard}/{total} S,G nefunguje", compared=False,
        )
    if soft:
        outcome = unchanged_or(Outcome.DEGRADED, ctx, "multicast_route", same=soft == soft_unchanged)
        return Finding(
            outcome, f"{soft} z {total} S,G bez streamu{suffix(outcome)}",
            label=label, value=f"{soft}/{total} S,G bez streamu", compared=False,
        )
    return Finding(Outcome.OK, f"{total} S,G funguje", label=label, value=f"{total} S,G", compared=False)
```

Check: `mode = Mode.BOTH`; v `run`:

```python
        table = multicast_table(ctx.subject)
        baseline_table = multicast_table(ctx.baseline) if ctx.has_baseline else {}
        ...
        hard = soft = soft_unchanged = 0
        for source, group, roles in pairs:
            matches = routes_for(table, source, group)
            if not matches:
                sg = sg_label(source, group)
                was = routes_for(baseline_table, source, group)
                outcome = unchanged_or(Outcome.DEGRADED, ctx, "multicast_route", same=not was)
                rows.append(Finding(
                    outcome, f"{sg}: S,G neni v multicast tabulce{suffix(outcome)}",
                    label="Stream", group=sg, value="S,G neni v multicast tabulce",
                    baseline_value=("S,G neni v multicast tabulce" if outcome is Outcome.UNCHANGED
                                    else _labels_of(was) or None),
                ))
                soft += 1
                soft_unchanged += outcome is Outcome.UNCHANGED
                continue
            pair_hard = pair_soft = pair_unchanged = False
            for key, route in matches:
                sg = sg_label(key.split(",", 1)[0], group)
                stream = self._stream(ctx, sg, iface, route, roles, baseline_table.get(key))
                outcomes = {f.outcome for f in stream}
                pair_hard |= Outcome.BROKEN in outcomes
                pair_soft |= Outcome.DEGRADED in outcomes
                pair_unchanged |= Outcome.UNCHANGED in outcomes
                rows.extend(stream)
            if pair_hard:
                hard += 1
            elif pair_soft or pair_unchanged:
                soft += 1
                soft_unchanged += pair_unchanged and not pair_soft
        return [_summary(ctx, self.label, len(pairs), hard, soft, soft_unchanged), *rows]
```

`_stream(self, ctx, sg, iface, route, roles, baseline_route)` (subtype se čte z `ctx.scope.service_subtype` uvnitř):

```python
        was_iface = (ctx.baseline_scope or ctx.scope).selectors.interfaces[0]
        was_down = (baseline_route or {}).get("downstream_interfaces") or []
        ...
        if as_sender:
            stream_outcome = Outcome.OK if sender_ok else unchanged_or(
                Outcome.DEGRADED, ctx, "multicast_route",
                same=baseline_route is not None and not was_down,
            )
            # message/value jako dnes + suffix(stream_outcome); compared=False
            ...
        else:
            stream_outcome = Outcome.OK if receiver_ok else unchanged_or(
                Outcome.BROKEN, ctx, "multicast_route",
                same=baseline_route is not None and was_iface not in was_down,
            )
            ...
```

Upstream řádky obou větví `compared=False` beze změny verdiktu; `stream_rows(sg, route, rate_label="Forwarding-rate", baseline_route=baseline_route)` (parametr přidej už teď s defaultem `None`, Task 3 ho dotáhne pro rate).

`_labels_of` přijímá `list[tuple[str, dict]]` — `routes_for` vrací stejný tvar.

Import `unchanged_or, suffix` z `checks.baseline`.

- [ ] **Step 4: Testy** `tests/checks/test_multicast.py tests/test_end_to_end.py -q` → PASS (opravit existující testy tvrdící FAIL pro chybějící S,G / sender bez downstreamu).

- [ ] **Step 5: Docs** `docs/cs/reference.md` řádek `multicast_forwarding_status`: mode `both`; „S,G není v tabulce = WARN; sender bez downstreamu = WARN; receiver bez streamu = FAIL". **Step 6: Commit** `feat(multicast): chybejici S,G a sender bez receiveru jsou WARN, forwarding radky compared=False`.

---

## Task 2: `mvpn_cmulticast_status` — baseline hodnoty a provider tunnel

**Files:**
- Modify: `migration_validator/checks/multicast.py:617-686`
- Test: `tests/checks/test_multicast.py`

- [ ] **Step 1: Failing testy** (v souboru už jsou testy c-multicast se strukturou `mvpn_instance`; drž jejich tvar záznamu `{"source_prefix", "group_prefix", "provider_tunnel_id", "sender_pe"}`)

```python
def _mvpn(instance, *entries):
    return {"mvpn_instance": {instance: {"c_multicast": list(entries)}}}


ENTRY = {"source_prefix": "10.12.12.1/32", "group_prefix": "239.1.1.1/32",
         "provider_tunnel_id": "0x1:150.0.0.13:0:1", "sender_pe": "150.0.0.13"}


def _mvpn_scope(iface=POST):
    return _scope(iface, service_type="IPVPN", subtype="mvpn", instances=("VRF",), protocols=("igmp",))


def test_cmulticast_ok_row_has_baseline_value_in_same_shape():
    pair = ("10.12.12.1", "239.1.1.1")
    subject = {**_igmp(POST, pair), **_pim("VRF"), **_mvpn("VRF", ENTRY)}
    baseline = {**_igmp(PRE, pair), **_pim("VRF"), **_mvpn("VRF", ENTRY)}
    rows = run_check(MvpnCmulticastStatusCheck(),
                     _ctx(subject, baseline=baseline, scope=_mvpn_scope(), baseline_scope=_mvpn_scope(PRE)))
    entry = [r for r in rows if r.label == "C-Multicast status"][0]
    assert entry.status is Status.PASS and entry.baseline_value == entry.value == "10.12.12.1/32:239.1.1.1/32"


def test_provider_tunnel_same_pe_is_not_compared_even_if_tunnel_id_changed():
    pair = ("10.12.12.1", "239.1.1.1")
    changed = {**ENTRY, "provider_tunnel_id": "0x1:150.0.0.13:0:9"}
    subject = {**_igmp(POST, pair), **_pim("VRF"), **_mvpn("VRF", changed)}
    baseline = {**_igmp(PRE, pair), **_pim("VRF"), **_mvpn("VRF", ENTRY)}
    rows = run_check(MvpnCmulticastStatusCheck(),
                     _ctx(subject, baseline=baseline, scope=_mvpn_scope(), baseline_scope=_mvpn_scope(PRE)))
    tunnel = [r for r in rows if r.label == "Provider tunnel"][0]
    assert tunnel.value == "0x1:150.0.0.13:0:9 (PE 150.0.0.13)"
    assert tunnel.details[NOT_COMPARED] is False and tunnel.status is Status.PASS


def test_provider_tunnel_changed_pe_is_warn_with_baseline_pe():
    pair = ("10.12.12.1", "239.1.1.1")
    moved = {**ENTRY, "sender_pe": "150.0.0.14"}
    subject = {**_igmp(POST, pair), **_pim("VRF"), **_mvpn("VRF", moved)}
    baseline = {**_igmp(PRE, pair), **_pim("VRF"), **_mvpn("VRF", ENTRY)}
    rows = run_check(MvpnCmulticastStatusCheck(),
                     _ctx(subject, baseline=baseline, scope=_mvpn_scope(), baseline_scope=_mvpn_scope(PRE)))
    tunnel = [r for r in rows if r.label == "Provider tunnel"][0]
    assert tunnel.status is Status.WARN and tunnel.baseline_value == "PE 150.0.0.13"


def test_cmulticast_entry_missing_in_both_is_unchanged():
    pair = ("10.12.12.1", "239.1.1.1")
    subject = {**_igmp(POST, pair), **_pim("VRF"), **_mvpn("VRF")}
    baseline = {**_igmp(PRE, pair), **_pim("VRF"), **_mvpn("VRF")}
    rows = run_check(MvpnCmulticastStatusCheck(),
                     _ctx(subject, baseline=baseline, scope=_mvpn_scope(), baseline_scope=_mvpn_scope(PRE)))
    [row] = rows
    assert row.status is Status.PASS and row.baseline_value == "chybi c-multicast zaznam"
```

- [ ] **Step 2: Ověř pád.**

- [ ] **Step 3: Implementace**

```python
        for source, group, _roles in pairs:
            sg = sg_label(source, group)
            entry = _cmulticast_entry(entries, source, group)
            was = _cmulticast_entry(baseline_entries, source, group) if ctx.has_baseline else None
            if entry is None:
                outcome = unchanged_or(Outcome.BROKEN, ctx, "mvpn_instance", same=was is None)
                findings.append(Finding(
                    outcome, f"{sg}: chybi c-multicast zaznam{suffix(outcome)}",
                    label=self.label, group=sg, value="chybi c-multicast zaznam",
                    baseline_value=("chybi c-multicast zaznam" if outcome is Outcome.UNCHANGED
                                    else _entry_value(was) if was else None),
                ))
                continue
            findings.append(Finding(
                Outcome.OK, f"{sg}: c-multicast {_entry_value(entry)}",
                label=self.label, group=sg, value=_entry_value(entry),
                baseline_value=_entry_value(was) if was else None,
            ))
            findings.append(self._tunnel_row(sg, entry, was))
```

s `def _entry_value(entry): return f"{entry['source_prefix']}:{entry['group_prefix']}"`. Instance chybí ve výpisu (`:640-644`): `baseline_value=None`, `compared=False` (tvrzení o instanci, ne o streamu).

`_tunnel_row`:

```python
        tunnel = entry.get("provider_tunnel_id") or "-"
        pe = entry.get("sender_pe")
        was_pe = was.get("sender_pe") if was else None
        value = f"{tunnel} (PE {pe or '-'})"
        if not pe:
            return Finding(Outcome.BROKEN, f"{sg}: provider tunnel {tunnel} - bez provider tunelu",
                           label="Provider tunnel", group=sg, value=value, compared=False)
        if was_pe and was_pe != pe:
            return Finding(Outcome.DEGRADED,
                           f"{sg}: provider tunnel {tunnel} - sender PE se zmenil {was_pe} -> {pe}",
                           label="Provider tunnel", group=sg, value=value, baseline_value=f"PE {was_pe}")
        # Tunnel id se pri re-signalizaci LSP meni bez zmeny sluzby
        # (rozhodnuti 2026-09-02) - porovnava se jen sender PE, a ten sedi.
        return Finding(Outcome.OK, f"{sg}: provider tunnel {tunnel}",
                       label="Provider tunnel", group=sg, value=value, compared=False)
```

- [ ] **Step 4: Testy** `tests/checks/test_multicast.py -q` → PASS (existující test na `baseline_value=was_tunnel` upravit). **Step 5: Commit** `feat(multicast): c-multicast baseline hodnoty, provider tunnel porovnava jen PE`.

---

## Task 3: `core_multicast_forwarding` — hodnoty, značky, rate z baseline

**Files:**
- Modify: `migration_validator/checks/multicast.py:214-241` (`stream_rows`), `:500-604` (check + `_stream`)
- Test: `tests/checks/test_multicast.py`

**Interfaces:**
- Produces: `stream_rows(sg, route, *, rate_label, baseline_route=None)`; rate řádek `baseline_value = f"{pps} pps"` z `baseline_route["forwarding_rate_pps"]` když není None, jinak `compared=False`; uptime řádek `compared=False`.

- [ ] **Step 1: Failing testy** (helpery `_core_scope(static_routes=INET2)`, `_core_facts(routes, via, inet2_present)`, `PREFIX = "10.11.11.1/32"`, `_route(...)`, `KEY` z Task 1 už existují)

```python
def test_core_exists_row_values_use_same_labels_so_zmena_is_blank():
    facts = _core_facts()
    rows = run_check(CoreMulticastForwardingCheck(), _ctx(facts, baseline=facts, scope=_core_scope()))
    exists = [r for r in rows if r.value.startswith("(10.11.11.1")][0]
    assert exists.value == "(10.11.11.1, 232.1.1.1)" == exists.baseline_value
    assert rows[0].details[NOT_COMPARED] is False


def test_core_upstream_downstream_uptime_not_compared_and_rate_has_baseline():
    subject = _core_facts(routes={KEY: _route(pps=9)})
    baseline = _core_facts(routes={KEY: _route(pps=6)})
    rows = run_check(CoreMulticastForwardingCheck(), _ctx(subject, baseline=baseline, scope=_core_scope()))
    by = _by_label(rows, sg_label(*SG))
    for label in ("Upstream interface", "Downstream interfaces", "Route uptime"):
        assert by[label].details[NOT_COMPARED] is False, label
    assert by["Forwarding rate packets"].baseline_value == "6 pps"


def test_core_rate_without_baseline_pps_is_not_compared():
    subject = _core_facts(routes={KEY: _route(pps=9)})
    baseline = _core_facts(routes={KEY: _route(pps=None)})
    rows = run_check(CoreMulticastForwardingCheck(), _ctx(subject, baseline=baseline, scope=_core_scope()))
    assert _by_label(rows, sg_label(*SG))["Forwarding rate packets"].details[NOT_COMPARED] is False


def test_core_missing_sg_in_both_is_unchanged_including_summary():
    facts = _core_facts(routes={})
    rows = run_check(CoreMulticastForwardingCheck(), _ctx(facts, baseline=facts, scope=_core_scope()))
    summary = rows[0]
    missing = [r for r in rows if r.value == "Neexistuje S,G"][0]
    assert missing.status is Status.PASS and missing.baseline_value == "Neexistuje S,G"
    assert summary.status is Status.PASS and summary.details[UNCHANGED_SINCE_BASELINE] is True


def test_core_upstream_among_two_vias_passes():
    """inet.2 statika s next-hop + qualified-next-hop (MX1-POP1 2026-09-08):
    collector od vlny 1 vraci obe via, upstream je jedno z nich."""
    facts = _core_facts(via=("et-0/0/0.0", "et-0/0/1.0"))
    rows = run_check(CoreMulticastForwardingCheck(), _ctx(facts, scope=_core_scope()))
    assert _by_label(rows, sg_label(*SG))["Upstream interface"].status is Status.PASS
```

- [ ] **Step 2: Ověř pád.**

- [ ] **Step 3: Implementace**

`stream_rows`:

```python
def stream_rows(sg, route, *, rate_label, baseline_route=None):
    raw_pps = route.get("forwarding_rate_pps")
    was_pps = (baseline_route or {}).get("forwarding_rate_pps")
    if raw_pps is None:
        rate_row = Finding(Outcome.SKIP, ..., compared=False)
    else:
        pps = int(raw_pps)
        rate_row = Finding(
            Outcome.OK if pps > 0 else Outcome.BROKEN,
            f"{sg}: forwarding rate {pps} pps",
            label=rate_label, group=sg, value=f"{pps} pps",
            baseline_value=f"{int(was_pps)} pps" if was_pps is not None else None,
            compared=was_pps is not None,
        )
    return [rate_row, Finding(Outcome.INFO, ..., label="Route uptime", group=sg, value=..., compared=False)]
```

Check `run`:

```python
            if not streams:
                outcome = unchanged_or(Outcome.BROKEN, ctx, "multicast_route", same=baseline_assigned is not None and not was)
                findings.append(Finding(
                    outcome, f"neexistuje S,G se zdrojem v {prefix}{suffix(outcome)}",
                    label=self.label, value="Neexistuje S,G",
                    baseline_value=("Neexistuje S,G" if outcome is Outcome.UNCHANGED else was_value),
                ))
                continue
            changed = bool(was) and {k for k, _ in was} != {k for k, _ in streams}
            findings.append(Finding(
                Outcome.DEGRADED if changed else Outcome.OK,
                f"existuje S,G se zdrojem v {prefix}: {_labels_of(streams)}" + (...),
                label=self.label, value=_labels_of(streams), baseline_value=was_value,
            ))
            baseline_by_key = dict(was)
            for key, route in streams:
                findings.extend(self._stream(sg_label(*key.split(",", 1)), route, vias, baseline_by_key.get(key)))
```

Souhrnný řádek (`insert(0, ...)`): `compared=False`; když všechny chybějící prefixy chyběly i v baseline, je souhrn UNCHANGED, ne BROKEN:

```python
        missing = [p for p in prefixes if not assigned.get(p)]
        missing_before = [
            p for p in missing
            if baseline_assigned is not None and not baseline_assigned.get(p)
        ]
        outcome = unchanged_or(
            Outcome.BROKEN if missing else Outcome.OK, ctx, "multicast_route",
            same=bool(missing) and missing == missing_before,
        )
        findings.insert(0, Finding(
            outcome,
            (f"{len(missing)} z {len(prefixes)} inet.2 prefixu bez streamu{suffix(outcome)}"
             if missing else f"{len(prefixes)} inet.2 prefixu se streamem"),
            label=self.label,
            value=(f"{len(missing)}/{len(prefixes)} bez streamu" if missing
                   else f"{len(prefixes)} inet.2 prefixu"),
            compared=False,
        ))
``` `_stream(sg, route, vias, baseline_route)`: upstream řádek (obě větve) i downstream řádek `compared=False`; `stream_rows(..., baseline_route=baseline_route)`.

Pozn. k prefixu v labelu: hodnota „Existuje S,G pro X" zaniká — prefix je v message; pokud má být vidět ve sloupci, dej ho do `group=f"inet.2 {prefix}"` (renderer skupinu vypíše jako nadpis). Rozhodni při implementaci podle výstupu `render(detail=True)`; obě varianty splňují R-5.

- [ ] **Step 4: Testy** `tests/checks/test_multicast.py tests/test_end_to_end.py -q` → PASS.

- [ ] **Step 5: Docs** `docs/cs/reference.md` řádek `core_multicast_forwarding`: „množina (S,G) proti baseline; upstream/downstream/uptime se neporovnávají (ZMENA prázdná); rate ukazuje `bylo N pps`". **Step 6: Commit** `feat(multicast): core inet.2 radky - shodny slovnik, compared=False, rate z baseline`.

---

## Task 4: Ověření v laborce a uzavření větve

- [ ] **Step 1:** `pyats-venv/bin/python -m pytest -q` a `node --test tests/js/*.test.js` zelené.
- [ ] **Step 2:** Laborka (`eval "$(grep '^export MIG_LAB_PASSWORD=' ~/.bashrc)"`): capture pre+post na MX1-POP1 (`172.20.20.4`) a PTX1-POP1 (`172.20.20.5`), `evaluate` s `--detail`. Zkontroluj: Core lo0.0 blok bez „bez baseline" a bez „bylo (S,G)"; `Upstream interface` PASS s dvěma via; Internet multicast blok bez ARP/ND/Ping; MVPN blok c-multicast bez „bez baseline"; souhrn vypíše `pass_unchanged` řádek jen když existují zděděné chyby.
- [ ] **Step 3:** Zapiš do spec sekce „Ověření" (datum, co sedělo, co ne). Commit `docs(spec): overeni vlny 3 v laborce`.
- [ ] **Step 4:** `superpowers:finishing-a-development-branch` — merge do `main`, push, restart lab GUI (viz memory `gui-bulk-groups-wave-2026-09-06`).
