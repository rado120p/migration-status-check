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
from migration_validator.models.result import Outcome, Status
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


def _ctx(subject, baseline=None, interfaces=("ge-0/0/2.113",), config=None, link=None):
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
        link=link,
    )


def _service_ctx(subject, physical_interfaces, baseline=None, config=None, link=None):
    """Service scope s L1 rodicem - fyzicky port si drzi jeho L1 blok,
    tady zustavaji jen unity."""
    interfaces = sorted(subject.get("interfaces", {}))
    scope = Scope(
        id="svc:X:Internet",
        kind="service",
        key=ScopeKey("X", "Internet", None),
        selectors=Selectors(
            interfaces=interfaces, physical_interfaces=list(physical_interfaces)
        ),
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=baseline,
        config=config or default_config(),
        failed_collectors={},
        link=link,
    )


def _layer1_ctx(subject, baseline=None, config=None, link=None):
    interfaces = sorted(subject.get("interfaces", {}))
    scope = Scope(
        id="l1:ae0",
        kind="layer1",
        key=ScopeKey("EX1;ae0", "Layer1", "physical-port"),
        selectors=Selectors(interfaces=interfaces),
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=baseline,
        config=config or default_config(),
        failed_collectors={},
        link=link,
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
        "Interface admin status (ge-0/0/2.113)",
        "Interface operational status (ge-0/0/2.113)",
    ]
    assert all(r.value == "Up" for r in results)
    assert all(r.family is None for r in results)


def test_interface_state_rows_name_their_interface():
    """Kazdy scope drzi fyzicke i logicke rozhrani, takze kazdy blok ostreho
    reportu mel dva radky se stejnym popiskem, jinymi hodnotami a
    protichudnymi sloupci ZMENA - a nebylo poznat, ktere rozhrani je ktere.
    """
    ctx = _ctx(
        {
            "interfaces": {
                "ge-0/0/2": {"admin_status": "up", "oper_status": "up"},
                "ge-0/0/2.113": {"admin_status": "up", "oper_status": "down"},
            }
        }
    )
    labels = [r.label for r in run_check(InterfaceStateCheck(), ctx)]

    assert labels == [
        "Interface admin status (ge-0/0/2)",
        "Interface operational status (ge-0/0/2)",
        "Interface admin status (ge-0/0/2.113)",
        "Interface operational status (ge-0/0/2.113)",
    ]


def test_traffic_rows_name_their_interface():
    ctx = _ctx(
        {
            "interfaces": {
                "ge-0/0/2": {"input_pps": 10, "output_pps": 10},
                "ge-0/0/2.113": {"input_pps": 412, "output_pps": 388},
            }
        }
    )
    labels = [r.label for r in run_check(InterfaceTrafficCheck(), ctx)]

    assert labels == [
        "Interface traffic in (ge-0/0/2)",
        "Interface traffic out (ge-0/0/2)",
        "Interface traffic in (ge-0/0/2.113)",
        "Interface traffic out (ge-0/0/2.113)",
    ]


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
    # fyzicke rozhrani (bez tecky) - logicke unity nemohou mit chybove countery
    ctx = _ctx({"interfaces": {"ge-0/0/2": {"input_errors": 3, "output_errors": 0}}})
    results = run_check(InterfaceErrorsCheck(), ctx)
    assert results[0].status is Status.WARN
    assert "3" in results[0].message


def test_errors_rows_name_their_interface_the_same_way():
    """Modul si nesmi odporovat: kdyz stavove a datove radky nesou jmeno
    rozhrani v zavorce za popiskem, chybove countery to musi delat stejne.
    Holy nazev rozhrani jako popisek byl presne to, co AR-4 odstranovalo.
    Fyzicke rozhrani (bez tecky) - logicke unity nemohou mit chybove countery."""
    ctx = _ctx(
        {
            "interfaces": {
                "ge-0/0/2": {"input_errors": 0, "output_errors": 0},
                "xe-1/0/0": {"input_errors": 3, "output_errors": 0},
            }
        }
    )
    labels = [r.label for r in run_check(InterfaceErrorsCheck(), ctx)]

    assert labels == [
        "Interface errors (ge-0/0/2)",
        "Interface errors (xe-1/0/0)",
    ]


def test_errors_zero_passes():
    ctx = _ctx({"interfaces": {"ge-0/0/2": {"input_errors": 0, "output_errors": 0}}})
    assert run_check(InterfaceErrorsCheck(), ctx)[0].status is Status.PASS


def test_errors_skip_logical_units():
    ctx = _ctx({"interfaces": {
        "ge-0/0/4": {"input_errors": 0, "output_errors": 0},
        "ge-0/0/4.0": {"input_errors": 7, "output_errors": 0},
    }})
    findings = run_check(InterfaceErrorsCheck(), ctx)
    labels = [f.label for f in findings]
    assert any("ge-0/0/4)" in label for label in labels)
    assert not any("ge-0/0/4.0" in label for label in labels)


def test_errors_only_units_present_gives_skip_with_truthful_message():
    # scope muze nest jen unity (fyzicky rodic mimo inventory)
    # Chybove countery nese jen fyzicke rozhrani - ale unitami jde popsat
    # pravdu: "jsou jen unity", ne "neni tranzitni". ae0 JE tranzitni.
    ctx = _ctx({"interfaces": {"ae0.15": {"input_errors": 0}}})
    findings = run_check(InterfaceErrorsCheck(), ctx)
    assert len(findings) == 1
    assert findings[0].status is Status.SKIP
    assert "jen unity" in findings[0].value
    assert "chybove countery nese jen fyzicke rozhrani" in findings[0].message
    assert "ae0.15" in findings[0].message


def test_errors_no_transit_interfaces_uses_old_skip():
    # Pokud nejsou zadne tranzitni rozhrani - ani fyzicka ani unity -
    # pouzije se _no_transit_finding, stejne jako pred timto fixem.
    ctx = _ctx(
        {"interfaces": {"lo0.0": {"input_errors": 0}}},
        interfaces=("lo0.0",),
    )
    findings = run_check(InterfaceErrorsCheck(), ctx)
    assert len(findings) == 1
    assert findings[0].status is Status.SKIP
    assert "neni tranzitni rozhrani" in findings[0].message


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
    assert [r.label for r in results] == [
        "Interface traffic in (ge-0/0/2.113)",
        "Interface traffic out (ge-0/0/2.113)",
    ]
    assert all("0 pps" in r.message for r in results)


def test_traffic_state_mode_passes_when_flowing():
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"input_pps": 412, "output_pps": 388}}})
    results = run_check(InterfaceTrafficCheck(), ctx)
    assert [r.status for r in results] == [Status.PASS, Status.PASS]
    by_label = {r.label: r for r in results}
    assert by_label["Interface traffic in (ge-0/0/2.113)"].value == "412 pps"
    assert by_label["Interface traffic out (ge-0/0/2.113)"].value == "388 pps"


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

    incoming = by_label["Interface traffic in (ge-0/0/2.113)"]
    outgoing = by_label["Interface traffic out (ge-0/0/2.113)"]

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

    incoming = by_label["Interface traffic in (ge-0/0/2.113)"]
    outgoing = by_label["Interface traffic out (ge-0/0/2.113)"]

    assert incoming.value == "460 pps"
    assert incoming.baseline_value == "520 pps"
    assert incoming.delta == "-12 %"

    assert outgoing.value == "300 pps"
    assert outgoing.baseline_value == "200 pps"
    assert outgoing.delta == "+50 %"


def test_traffic_without_baseline_has_no_delta():
    ctx = _ctx({"interfaces": {"ge-0/0/2.113": {"input_pps": 460, "output_pps": 300}}})
    by_label = {r.label: r for r in run_check(InterfaceTrafficCheck(), ctx)}

    incoming = by_label["Interface traffic in (ge-0/0/2.113)"]
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


def _ceased_ctx(subject_pps, baseline_pps, config=None, link=None, interfaces=("ge-0/0/2.113",)):
    from migration_validator.config import CheckConfig

    config = config or CheckConfig({"traffic_ceased": {"enabled": True}})
    interface = interfaces[0] if interfaces else "ge-0/0/2.113"
    return _ctx(
        subject={
            "interfaces": {
                interface: {"input_pps": subject_pps, "output_pps": subject_pps}
            }
        },
        baseline={
            "interfaces": {
                interface: {"input_pps": baseline_pps, "output_pps": baseline_pps}
            }
        },
        config=config,
        link=link,
        interfaces=interfaces,
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


def test_traffic_ceased_rows_name_their_interface_the_same_way():
    """Posledni check v modulu, ktery jeste pouzival holy nazev rozhrani
    jako popisek. Vychozi je vypnuty, takze do ostreho reportu nikdy
    neprosakoval - o to snadneji by v nem zustal nesourody."""
    labels = [
        run_check(TrafficCeasedCheck(), ctx)[0].label
        for ctx in (_ceased_ctx(0, 400), _ceased_ctx(400, 400), _ceased_ctx(0, 0))
    ]

    assert labels == ["Interface traffic ceased (ge-0/0/2.113)"] * 3


def test_traffic_ceased_row_carries_the_previous_traffic():
    """Compare check bez `baseline_value` znamena 'bez baseline' ve sloupci
    ZMENA - a tenhle check bez baseline vubec nebezi, takze by to byla
    hlaska, ktera nemuze byt pravda. Tataz trida chyby jako F-15."""
    result = run_check(TrafficCeasedCheck(), _ceased_ctx(0, 400))[0]

    assert result.value == "0 pps"
    assert result.baseline_value == "400 pps"


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


L3_LINK = {
    "role": "l3",
    "peer_scope_id": "svc:EVPN-VLAN-AWARE-CPE14:E-LAN",
    "peer_interface": "ae0.15",
    "peer_instance": "EVPN-VLAN-AWARE-POP1",
}


def test_errors_on_linked_l3_scope_point_to_l2_block():
    ctx = _ctx(
        {"interfaces": {"irb.15": {"admin_status": "up", "oper_status": "up"}}},
        interfaces=("irb.15",),
        link=L3_LINK,
    )
    findings = InterfaceErrorsCheck().run(ctx)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.outcome is Outcome.INFO
    assert finding.label == "Interface errors / traffic"
    assert finding.value == "mereno na L2 (ae0.15) - viz blok nize"


def test_traffic_on_linked_l3_scope_emits_nothing():
    ctx = _ctx(
        {"interfaces": {"irb.15": {"admin_status": "up", "oper_status": "up"}}},
        interfaces=("irb.15",),
        link=L3_LINK,
    )
    assert InterfaceTrafficCheck().run(ctx) == []


def test_linked_l3_scope_with_transit_keeps_measuring():
    # ochrana: kdyby L3 scope tranzit mel, vazba mereni nesmi vypnout
    ctx = _ctx(
        {
            "interfaces": {
                "ge-0/0/4.0": {
                    "admin_status": "up",
                    "oper_status": "up",
                    "input_pps": 5,
                    "output_pps": 5,
                }
            }
        },
        interfaces=("ge-0/0/4.0",),
        link=L3_LINK,
    )
    findings = InterfaceTrafficCheck().run(ctx)
    assert len(findings) == 2


def test_l2_side_of_link_measures_as_before():
    l2_link = {
        "role": "l2",
        "peer_scope_id": "svc:X:IPVPN",
        "peer_interface": "irb.15",
        "peer_instance": "L3VPN-CPE14-UNI",
    }
    ctx = _ctx(
        {
            "interfaces": {
                "ae0.15": {
                    "admin_status": "up",
                    "oper_status": "up",
                    "input_pps": 5,
                    "output_pps": 5,
                }
            }
        },
        interfaces=("ae0.15",),
        link=l2_link,
    )
    assert len(InterfaceTrafficCheck().run(ctx)) == 2


def test_traffic_ceased_with_l3_link_emits_nothing():
    ctx = _ceased_ctx(0, 400, link=L3_LINK, interfaces=("irb.15",))
    assert TrafficCeasedCheck().run(ctx) == []


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


def test_service_scope_bez_unitu_netiskne_bez_dat():
    """Prazdny scope_interfaces() neni prazdna cela area - service scope
    s L1 rodicem, jehoz unit v datech chybi, ma zustat ticho, ne vratit
    falesne 'bez dat' (to je vyhrazeno pro scope bez zadnych dat vubec)."""
    ctx = _service_ctx({"interfaces": {"ae0": IFACES["ae0"]}}, physical_interfaces=["ae0"])
    assert InterfaceStateCheck().run(ctx) == []


def test_parentless_service_beze_zmeny():
    ctx = _service_ctx({"interfaces": {"ae0.14": IFACES["ae0.14"]}},
                       physical_interfaces=[])
    rows = InterfaceErrorsCheck().run(ctx)
    assert rows[0].outcome is Outcome.SKIP  # "jen unity" jako dnes
