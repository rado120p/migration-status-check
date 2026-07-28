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
    assert [r.status for r in results] == [Status.PASS, Status.PASS]


def test_interface_state_splits_admin_and_oper():
    """Distinct hodnoty pro admin/oper - zamena poradi by tichem prosla,
    kdyby oba stavy byly "up"."""
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"admin_status": "up", "oper_status": "up"}}})
    results = run_check(InterfaceStateCheck(), ctx)

    assert [r.label for r in results] == [
        "Interface admin status",
        "Interface operational status",
    ]
    assert all(r.value == "Up" for r in results)
    assert all(r.family is None for r in results)


def test_interface_state_down_fails():
    # admin up, oper down - jinak by prohozeni poli neslo poznat.
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"admin_status": "up", "oper_status": "down"}}})
    results = run_check(InterfaceStateCheck(), ctx)

    assert results[0].status is Status.PASS
    assert results[1].status is Status.FAIL
    assert "down" in results[1].message
    assert results[1].value == "Down"


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
    assert [r.status for r in results] == [Status.WARN, Status.WARN]
    assert [r.label for r in results] == ["Interface traffic in", "Interface traffic out"]
    assert all("0 pps" in r.message for r in results)


def test_traffic_state_mode_passes_when_flowing():
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"input_pps": 412, "output_pps": 388}}})
    results = run_check(InterfaceTrafficCheck(), ctx)
    assert [r.status for r in results] == [Status.PASS, Status.PASS]
    by_label = {r.label: r for r in results}
    assert by_label["Interface traffic in"].value == "412 pps"
    assert by_label["Interface traffic out"].value == "388 pps"


def test_traffic_compare_within_tolerance_passes():
    ctx = _ctx(
        subject={"interfaces": {"ge-0/0/2.113": {"input_pps": 398, "output_pps": 380}}},
        baseline={"interfaces": {"ge-0/0/2.113": {"input_pps": 412, "output_pps": 410}}},
    )
    results = run_check(InterfaceTrafficCheck(), ctx)
    assert [r.status for r in results] == [Status.PASS, Status.PASS]


def test_traffic_compare_below_tolerance_warns_and_reports_numbers():
    # in je v toleranci (-3 %), out ne (-72 %) - jinak by prohozeni smeru
    # neslo poznat.
    ctx = _ctx(
        subject={"interfaces": {"ge-0/0/2.113": {"input_pps": 398, "output_pps": 115}}},
        baseline={"interfaces": {"ge-0/0/2.113": {"input_pps": 412, "output_pps": 410}}},
    )
    by_label = {r.label: r for r in run_check(InterfaceTrafficCheck(), ctx)}

    incoming = by_label["Interface traffic in"]
    outgoing = by_label["Interface traffic out"]

    assert incoming.status is Status.PASS
    assert outgoing.status is Status.WARN
    assert "410" in outgoing.message and "115" in outgoing.message
    assert outgoing.value == "115 pps"
    assert outgoing.baseline_value == "410 pps"
    assert outgoing.delta == "-72 %"
    assert outgoing.baseline == {"output_pps": 410}
    assert outgoing.subject == {"output_pps": 115}
    assert outgoing.details["tolerance_percent"] == -60


def test_traffic_tolerance_is_configurable():
    config = CheckConfig({"interface_traffic": {"tolerance_percent": -80}})
    ctx = _ctx(
        subject={"interfaces": {"ge-0/0/2.113": {"input_pps": 400, "output_pps": 115}}},
        baseline={"interfaces": {"ge-0/0/2.113": {"input_pps": 412, "output_pps": 410}}},
        config=config,
    )
    results = run_check(InterfaceTrafficCheck(), ctx)
    assert [r.status for r in results] == [Status.PASS, Status.PASS]


def test_traffic_reports_distinct_value_baseline_and_delta_per_direction():
    # in a out maji rozdilne subject i baseline hodnoty, aby zamena smeru
    # nebo pole (value/baseline_value/delta) nemohla projit testem tise.
    ctx = _ctx(
        subject={"interfaces": {"ge-0/0/2.113": {"input_pps": 460, "output_pps": 300}}},
        baseline={"interfaces": {"ge-0/0/2.113": {"input_pps": 520, "output_pps": 200}}},
    )
    by_label = {r.label: r for r in run_check(InterfaceTrafficCheck(), ctx)}

    incoming = by_label["Interface traffic in"]
    outgoing = by_label["Interface traffic out"]

    assert incoming.value == "460 pps"
    assert incoming.baseline_value == "520 pps"
    assert incoming.delta == "-12 %"

    assert outgoing.value == "300 pps"
    assert outgoing.baseline_value == "200 pps"
    assert outgoing.delta == "+50 %"


def test_traffic_without_baseline_has_no_delta():
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"input_pps": 460, "output_pps": 300}}})
    by_label = {r.label: r for r in run_check(InterfaceTrafficCheck(), ctx)}

    incoming = by_label["Interface traffic in"]
    assert incoming.value == "460 pps"
    assert incoming.baseline_value is None
    assert incoming.delta is None


def test_traffic_skipped_on_internal_interface():
    ctx = _ctx(
        {"interfaces": {"lo0.0": {"input_pps": 0, "output_pps": 0}}},
        interfaces=("lo0.0",),
    )
    assert run_check(InterfaceTrafficCheck(), ctx)[0].status is Status.SKIP


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
