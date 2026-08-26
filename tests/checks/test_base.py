import pytest

from migration_validator.config import CheckConfig, default_config
from migration_validator.checks.base import Check, CheckContext, Mode, run_check
from migration_validator.checks.ifaces import InterfaceStateCheck
from migration_validator.models.result import Finding, Outcome, Severity, Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope


class DummyCheck(Check):
    id = "dummy"
    title = "Dummy"
    label = "Dummy radek"
    mode = Mode.STATE
    requires = ("interfaces",)
    default_severity = Severity.CRITICAL

    def run(self, ctx):
        return [Finding(outcome=Outcome.BROKEN, message="rozbito", value="rozbito")]


class CompareCheck(DummyCheck):
    id = "dummy_compare"
    mode = Mode.COMPARE


class InventoryCheck(DummyCheck):
    id = "dummy_inventory"
    requires_inventory = True


class TypedCheck(DummyCheck):
    id = "dummy_typed"
    service_types = frozenset({"IPVPN"})


class ExplodingCheck(DummyCheck):
    id = "dummy_boom"

    def run(self, ctx):
        raise RuntimeError("neco se pokazilo")


def _scope(service_type="Internet") -> Scope:
    return Scope(
        id=f"svc:X:{service_type}",
        kind="service",
        key=ScopeKey("X", service_type, None),
        selectors=Selectors(interfaces=["ge-0/0/1.0"]),
    )


def _layer1_scope() -> Scope:
    return Scope(
        id="l1:ae0",
        kind="layer1",
        key=ScopeKey("EX1;ae0", "Layer1", "physical-port"),
        selectors=Selectors(interfaces=["ae0"]),
    )


def _ctx(**kwargs) -> CheckContext:
    defaults = dict(
        scope=_scope(),
        subject={"interfaces": {"ge-0/0/1.0": {}}},
        baseline=None,
        config=default_config(),
        failed_collectors={},
    )
    defaults.update(kwargs)
    return CheckContext(**defaults)


def test_broken_critical_is_fail():
    results = run_check(DummyCheck(), _ctx())
    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert results[0].id == "dummy"


def test_severity_override_from_config_turns_fail_into_warn():
    config = CheckConfig({"dummy": {"severity": "advisory"}})
    results = run_check(DummyCheck(), _ctx(config=config))
    assert results[0].status is Status.WARN


def test_disabled_check_produces_no_results():
    config = CheckConfig({"dummy": {"enabled": False}})
    assert run_check(DummyCheck(), _ctx(config=config)) == []


def test_compare_check_without_baseline_skips():
    results = run_check(CompareCheck(), _ctx())
    assert results[0].status is Status.SKIP
    assert "baseline" in results[0].message


def test_check_requiring_inventory_skips_on_device_scope():
    results = run_check(InventoryCheck(), _ctx(scope=device_scope()))
    assert results[0].status is Status.SKIP
    assert "inventory" in results[0].message


def test_failed_collector_skips_with_original_error():
    results = run_check(
        DummyCheck(), _ctx(failed_collectors={"interfaces": "RpcError: syntax error"})
    )
    assert results[0].status is Status.SKIP
    assert "RpcError: syntax error" in results[0].message


def test_check_not_applicable_to_service_type_produces_no_results():
    assert run_check(TypedCheck(), _ctx(scope=_scope("Internet"))) == []
    assert run_check(TypedCheck(), _ctx(scope=_scope("IPVPN"))) != []


def test_check_applies_to_device_scope_regardless_of_service_types():
    assert TypedCheck().applies_to(device_scope()) is True


def test_layer1_scope_pousti_jen_layer1_checky():
    class ServiceOnly(DummyCheck):
        service_types = None

    class ForPort(DummyCheck):
        layer1 = True

    assert not ServiceOnly().applies_to(_layer1_scope())
    assert ForPort().applies_to(_layer1_scope())
    assert ServiceOnly().applies_to(_scope())  # chovani sluzeb beze zmeny


def _core_scope(service_subtype) -> Scope:
    return Scope(
        id=f"svc:core:{service_subtype}",
        kind="service",
        key=ScopeKey("core", "Core", service_subtype),
        selectors=Selectors(interfaces=["ge-0/0/0.0"]),
    )


def test_check_with_service_subtypes_gates_on_subtype():
    class TransitOnly(DummyCheck):
        service_types = frozenset({"Core"})
        service_subtypes = frozenset({"transit"})

    check = TransitOnly()
    assert check.applies_to(_core_scope("transit")) is True
    assert check.applies_to(_core_scope("loopback")) is False
    assert check.applies_to(_scope("Internet")) is False
    assert check.applies_to(device_scope()) is True


def test_exception_in_check_becomes_skip_not_crash():
    results = run_check(ExplodingCheck(), _ctx())
    assert results[0].status is Status.SKIP
    assert "neco se pokazilo" in results[0].message


def test_missing_data_never_passes():
    """Kdyz collector selhal, vysledek nesmi byt PASS."""
    results = run_check(DummyCheck(), _ctx(failed_collectors={"interfaces": "timeout"}))
    assert results[0].status is not Status.PASS


class _PresentationCheck(Check):
    # Neregistruje se @register - je jen pro tento test, registrace by ho
    # pustila do vsech ostatnich behu.
    id = "presentation_probe"
    title = "Testovaci check"
    mode = Mode.STATE

    def run(self, ctx):
        return [
            Finding(
                Outcome.OK,
                "hotovo",
                label="Neco",
                family=6,
                value="Up",
                baseline_value="Down",
                delta="zmena",
            )
        ]


class _UnlabelledCheck(Check):
    id = "unlabelled_probe"
    title = "Testovaci check bez popisku ve findingu"
    label = "Popisek checku"
    mode = Mode.STATE

    def run(self, ctx):
        return [Finding(Outcome.OK, "hotovo", value="Up")]


def test_finding_without_label_borrows_the_one_from_the_check():
    """F-10: popisek radku nesmi spadnout na id checku.

    Doplnuje se v run_check, ne v rendereru: renderer vidi jen CheckResult,
    takze by nemel odkud vzit nic lepsiho nez to id - a `SKIP |
    evpn_esi_status` mezi hezkymi popisky je presne to, co se v ostrem behu
    tisklo.
    """
    ctx = CheckContext(
        scope=device_scope(), subject={}, baseline=None, config=default_config()
    )

    result = run_check(_UnlabelledCheck(), ctx)[0]

    assert result.label == "Popisek checku"


def test_failed_collector_skip_is_a_row_not_a_veta():
    """F-7/F-10: radky, ktere vyrobi framework mimo check, mely prazdny
    popisek i hodnotu, takze do reportu spadlo id checku a cela veta.

    U selhaneho collectoru to byla veta o RPC chybe dlouha 190 znaku, ktera
    v ostrem behu roztahla cely blok na 270 znaku sirky. Duvod nemizi -
    zustava v message, kterou tiskne sloupec NALEZ a strojovy vystup.
    """
    result = run_check(
        DummyCheck(), _ctx(failed_collectors={"interfaces": "RpcError: syntax error"})
    )[0]

    assert result.status is Status.SKIP
    assert result.label == "Dummy radek"
    assert result.value == "collector selhal"
    assert "RpcError: syntax error" in result.message


def test_missing_inventory_skip_is_a_row_not_a_veta():
    result = run_check(InventoryCheck(), _ctx(scope=device_scope()))[0]

    assert result.label == "Dummy radek"
    assert result.value == "bez inventory"


def test_compare_check_without_baseline_skips_with_a_short_value():
    result = run_check(CompareCheck(), _ctx())[0]

    assert result.label == "Dummy radek"
    assert result.value == "bez baseline"


def test_exploded_check_skips_with_a_short_value():
    result = run_check(ExplodingCheck(), _ctx())[0]

    assert result.value == "check selhal"
    assert "neco se pokazilo" in result.message


def test_every_registered_check_has_a_row_label():
    """Bez popisku by check tise vypisoval radky pod svym id - a prave to
    je F-10. Kdyz ho zavede uz trida, nemuze na nej novy check zapomenout
    jen v nekterych vetvich."""
    from migration_validator.checks import all as _all  # noqa: F401  (registrace)
    from migration_validator.checks.registry import all_checks

    checks = all_checks()
    assert checks
    for check in checks:
        assert getattr(check, "label", None), f"{check.id} nema label"


def test_run_check_propagates_presentation_fields():
    ctx = CheckContext(
        scope=device_scope(),
        subject={},
        baseline=None,
        config=default_config(),
    )

    result = run_check(_PresentationCheck(), ctx)[0]

    assert result.family == 6
    assert result.value == "Up"
    assert result.baseline_value == "Down"
    assert result.delta == "zmena"


def test_deactivated_scope_skips_every_check():
    """Deaktivovana sluzba nevyrabi FAILy - jen rekne, ze je deaktivovana.

    Zabiji mutanta: vynechani nove zkratky z run_check. Bez ni by sluzba na
    deaktivovanem rozhrani hlasila FAIL na vsem (ping down, BGP down) bez
    jakehokoli vysvetleni - presne to, co v laborce dnes dela ge-0/0/4.
    """
    scope = Scope(
        id="svc:CPE14:IPVPN",
        kind="service",
        key=ScopeKey(description="CPE14", service_type="IPVPN"),
        selectors=Selectors(interfaces=["ge-0/0/4.0"]),
        interface_active=False,
    )
    ctx = CheckContext(
        scope=scope,
        subject={"interfaces": {}},
        baseline=None,
        config=default_config(),
    )

    results = run_check(InterfaceStateCheck(), ctx)

    assert results
    assert all(result.status is Status.SKIP for result in results)
    assert results[0].value == "interface deactivated"


def test_live_scope_still_runs_its_checks():
    """Protejsek - bez nej by slo zkratku napsat tak, ze SKIPuje vzdycky.

    Zabiji mutanta: podminka zmenena na `if True`.
    """
    scope = Scope(
        id="svc:CPE13:IPVPN",
        kind="service",
        key=ScopeKey("CPE13", "IPVPN", None),
        selectors=Selectors(interfaces=["ge-0/0/2.113"]),
    )
    ctx = CheckContext(
        scope=scope,
        subject={
            "interfaces": {
                "ge-0/0/2.113": {"admin_status": "up", "oper_status": "up"}
            }
        },
        baseline=None,
        config=default_config(),
    )

    results = run_check(InterfaceStateCheck(), ctx)

    assert results
    assert all(result.status is Status.PASS for result in results)


def test_group_travels_from_finding_to_check_result():
    """Zabiji mutanta, ktery v run_check() `group=finding.group` vypusti.

    Bez tohohle by skupina koncila u Findingu a renderer by ji nikdy
    nevidel - vsechny radky by spadly mezi neseskupene a nadpisy by nikdy
    nevznikly.
    """

    class _Grouped(Check):
        id = "grouped_probe"
        title = "Zkouska skupiny"
        label = "Zkouska"

        def run(self, ctx):
            return [
                Finding(Outcome.OK, "s", label="a", group="Skupina"),
                Finding(Outcome.OK, "b", label="b"),
            ]

    results = run_check(_Grouped(), _ctx())
    assert [r.group for r in results] == ["Skupina", None]
