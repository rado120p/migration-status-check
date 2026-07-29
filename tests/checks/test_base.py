import pytest

from migration_validator.config import CheckConfig, default_config
from migration_validator.checks.base import Check, CheckContext, Mode, run_check
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
