from migration_validator.checks.base import CheckContext
from migration_validator.checks.optics import OpticalAlarmsCheck, OpticalLevelsCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope


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


# Nepripojeny port hlasi rx/tx jako -inf (skutecne chovani krabice). _fmt
# musi vytisknout citelny Junos-styl token, ne "-inf dBm" z f-stringu, a
# _level_finding nesmi z nekonecna spocitat delta/DEGRADED - verdikt o
# nepripojenem portu nese alarms check (flagy On), ne aritmetika levels
# checku.
def test_levels_nekonecny_rx_se_formatuje_jako_inf_token_bez_delty():
    lane = _lane(rx=float("-inf"), tx=-2.0)
    findings = OpticalLevelsCheck().run(
        _ctx({"optics": {"ae0": {"lanes": [lane]}}}))
    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "RX -Inf dBm / TX -2.00 dBm"


def test_levels_prechod_z_konecne_na_nekonecnou_hodnotu_nedava_rx_deltu():
    findings = OpticalLevelsCheck().run(_ctx(
        {"optics": {"ae0": {"lanes": [_lane(rx=float("-inf"), tx=-2.5)]}}},
        baseline={"optics": {"ae0": {"lanes": [_lane(rx=-5.0, tx=-2.0)]}}}))
    finding = findings[0]
    assert finding.outcome is Outcome.OK
    assert "RX" not in (finding.delta or "")
    assert "TX -0.5 dB" in finding.delta


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
