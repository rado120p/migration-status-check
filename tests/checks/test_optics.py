from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.optics import OpticalAlarmsCheck, OpticalLevelsCheck
from migration_validator.config import default_config
from migration_validator.models.result import NOT_COMPARED, UNCHANGED_SINCE_BASELINE, Outcome, Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope


def _lane(rx=-5.0, tx=-2.0, lane=0, alarms=None, warnings=None):
    return {"lane": lane, "rx_power_dbm": rx, "tx_power_dbm": tx,
            "temperature_c": 30.0, "alarms": alarms or {}, "warnings": warnings or {}}


def _ctx(subject, baseline=None, port="ae0", members=()):
    scope = Scope(id=f"l1:{port}", kind="layer1",
                  key=ScopeKey(f"L1;{port}", "Layer1", "physical-port"),
                  selectors=Selectors(interfaces=[port],
                                      lag_members=list(members)))
    return CheckContext(
        scope=scope, subject=subject, baseline=baseline,
        config=default_config(), failed_collectors={},
        # Pozitivni dukaz, ze baseline oblast byla zmerena (ctx.baseline_measured)
        # - bez nej by UNCHANGED nemohl vzniknout ani u testu, ktere baseline
        # predavaji.
        baseline_collectors=(
            {area: {"status": "ok"} for area in baseline} if baseline is not None else {}
        ),
    )


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


def test_alarm_message_says_aktivni():
    lane = _lane(alarms={"rx_los": True})
    findings = OpticalAlarmsCheck().run(
        _ctx({"optics": {"ae0": {"lanes": [lane]}}}))
    f = [x for x in findings if x.outcome is Outcome.BROKEN][0]
    assert f.message == "ae0: rx_los je aktivni"


def test_flag_off_se_nevypisuje():
    lane = _lane(alarms={"laser-rx-power-low-alarm": False})
    findings = OpticalAlarmsCheck().run(
        _ctx({"optics": {"ae0": {"lanes": [lane]}}}))
    assert [f.value for f in findings] == ["bez alarmu"]


# Nepripojeny port hlasi rx/tx jako -inf (skutecne chovani krabice). _fmt
# musi vytisknout citelny Junos-styl token, ne "-inf dBm" z f-stringu, a
# _level_finding nesmi z nekonecna spocitat delta/DEGRADED - delta se
# pocita jen z konecnych hodnot. Nekonecno samo je ale BROKEN: port bez
# svetla neni "uroven v toleranci" (pozadavek z lab testovani 2026-08-13).
def test_levels_nekonecny_rx_je_broken_s_inf_tokenem():
    lane = _lane(rx=float("-inf"), tx=-2.0)
    findings = OpticalLevelsCheck().run(
        _ctx({"optics": {"ae0": {"lanes": [lane]}}}))
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "RX -Inf dBm / TX -2.00 dBm"


def test_levels_nekonecno_je_fail_i_pres_run_check():
    lane = _lane(rx=float("-inf"), tx=float("-inf"))
    results = run_check(OpticalLevelsCheck(),
                        _ctx({"optics": {"ae0": {"lanes": [lane]}}}))
    assert results[0].status is Status.FAIL


def test_levels_prechod_z_konecne_na_nekonecnou_hodnotu_nedava_rx_deltu():
    findings = OpticalLevelsCheck().run(_ctx(
        {"optics": {"ae0": {"lanes": [_lane(rx=float("-inf"), tx=-2.5)]}}},
        baseline={"optics": {"ae0": {"lanes": [_lane(rx=-5.0, tx=-2.0)]}}}))
    finding = findings[0]
    assert finding.outcome is Outcome.BROKEN
    assert "RX" not in (finding.delta or "")
    assert "TX -0.5 dB" in finding.delta


# SKIP "bez optiky" na LAG rodici stal v reportu bez jmena rozhrani a
# vedle lane radku clenu nebylo poznat, ke komu patri (lab 2026-08-13).
def test_skip_bez_optiky_nese_jmeno_rozhrani_v_labelu():
    findings = OpticalLevelsCheck().run(_ctx(
        {"optics": {"et-0/0/5": {"lanes": [_lane()]}}},
        port="ae0", members=["et-0/0/5"]))
    skip = next(f for f in findings if f.outcome is Outcome.SKIP)
    assert skip.label == "Interface optical levels (ae0)"

    alarm_findings = OpticalAlarmsCheck().run(_ctx(
        {"optics": {"et-0/0/5": {"lanes": [_lane()]}}},
        port="ae0", members=["et-0/0/5"]))
    alarm_skip = next(f for f in alarm_findings if f.outcome is Outcome.SKIP)
    assert alarm_skip.label == "Interface optical alarms (ae0)"


def test_levels_dark_in_both_is_unchanged():
    """Shodne nekonecno v subjektu i zmerene baseline je PASS se znackou."""
    lane = _lane(rx=float("-inf"))
    ctx = _ctx({"optics": {"ae0": {"lanes": [lane]}}}, baseline={"optics": {"ae0": {"lanes": [lane]}}})
    [row] = run_check(OpticalLevelsCheck(), ctx)
    assert row.status is Status.PASS and row.details[UNCHANGED_SINCE_BASELINE] is True


def test_levels_missing_baseline_lane_row_is_not_compared():
    """Baseline lane chybi (jina inventory lanes) - radek se z definice
    neporovnava, ZMENA sloupec zustava prazdny, ne 'bez baseline'."""
    ctx = _ctx({"optics": {"ae0": {"lanes": [_lane()]}}}, baseline={"optics": {}})
    [row] = run_check(OpticalLevelsCheck(), ctx)
    assert row.details[NOT_COMPARED] is False


def test_alarm_raised_in_both_is_unchanged():
    """Shodne zvedly alarm v subjektu i zmerene baseline je PASS se znackou."""
    lane = _lane(alarms={"rx-loss-of-signal": True})
    ctx = _ctx({"optics": {"ae0": {"lanes": [lane]}}}, baseline={"optics": {"ae0": {"lanes": [lane]}}})
    [row] = run_check(OpticalAlarmsCheck(), ctx)
    assert row.status is Status.PASS and row.value == "rx-loss-of-signal" == row.baseline_value


def test_new_alarm_with_measured_baseline_carries_baseline_summary():
    """Nove zvednuty alarm (nebyl v baseline) nedostava baseline_value=None,
    kdyz baseline pro port opticka data mela - jinak by renderer tiskl
    "bez baseline", i kdyz baseline byla zmerena (jen bez tohoto alarmu)."""
    now_lane = _lane(alarms={"rx-loss-of-signal": True})
    baseline_lane = _lane()  # bez alarmu/warningu
    ctx = _ctx(
        {"optics": {"ae0": {"lanes": [now_lane]}}},
        baseline={"optics": {"ae0": {"lanes": [baseline_lane]}}},
    )
    [row] = run_check(OpticalAlarmsCheck(), ctx)
    assert row.status is Status.FAIL
    assert row.baseline_value == "bez alarmu"


def test_alarm_escalated_from_warning_in_baseline_is_not_unchanged():
    """Stejny tag, ale warning v baseline a alarm ted, neni "stejny stav".

    Klic same= musi nest Outcome (BROKEN/DEGRADED), ne jen (lane, tag) -
    jinak by eskalace severity zmizela za UNCHANGED PASSem.
    """
    now_lane = _lane(alarms={"rx-loss-of-signal": True})
    baseline_lane = _lane(warnings={"rx-loss-of-signal": True})
    ctx = _ctx(
        {"optics": {"ae0": {"lanes": [now_lane]}}},
        baseline={"optics": {"ae0": {"lanes": [baseline_lane]}}},
    )
    [row] = run_check(OpticalAlarmsCheck(), ctx)
    assert row.status is Status.FAIL
    assert row.value == "rx-loss-of-signal"
    assert row.details.get(UNCHANGED_SINCE_BASELINE) is not True


# Check.applies_to() pousti device scope na VSECHNY checky bez ohledu na
# service_types/layer1 (base.py: `if scope.is_device: return True` je prvni
# vetev). device_scope() ma prazdny selectors.interfaces - kdyby run()
# indexoval [0] bez ochrany, spadl by na IndexError, ktery run_check()
# schova do SKIPu "check selhal" (viz evaluate_snapshots, kdyz snapshot
# nema scopy). Check tedy musi na device scope vratit prazdny seznam, ne
# spadnout.
def test_device_scope_bez_portu_nespada():
    ctx = CheckContext(scope=device_scope(), subject={"optics": {}}, baseline=None,
                        config=default_config(), failed_collectors={})
    assert OpticalLevelsCheck().run(ctx) == []
    assert OpticalAlarmsCheck().run(ctx) == []
