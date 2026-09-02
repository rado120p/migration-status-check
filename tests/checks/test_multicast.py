"""Checky multicastu (spec 2026-09-02). Idiom: realny Scope, CheckContext,
primy run(). Baseline scope nese jina jmena rozhrani (ge- vs et-), proto
se IGMP mnozina baseline cte pres ctx.baseline_scope."""

from __future__ import annotations

from migration_validator.checks.base import CheckContext
from migration_validator.checks.multicast import (
    NO_REPORT,
    RATE_UNAVAILABLE,
    IgmpMembershipReportCheck,
    format_uptime_hms,
    igmp_pairs,
    routes_for,
    sg_label,
    stream_rows,
)
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors

POST = "et-0/0/8.11"
PRE = "ge-0/0/2.11"
SG = ("10.11.11.1", "232.1.1.1")


def _scope(interface=POST, service_type="Internet", subtype="multicast", instances=(),
           static_routes=()):
    return Scope(
        id=f"svc:x:{service_type}", kind="service",
        key=ScopeKey("x", service_type, subtype),
        selectors=Selectors(
            interfaces=[interface], routing_instances=list(instances),
            static_routes=[dict(r) for r in static_routes],
        ),
    )


def _ctx(subject, baseline=None, scope=None, baseline_scope=None):
    return CheckContext(
        scope=scope or _scope(), subject=subject, baseline=baseline,
        config=default_config(), baseline_scope=baseline_scope,
    )


def _igmp(iface, *pairs):
    return {"igmp_group": {iface: [{"source": s, "group": g} for s, g in pairs]}}


# --- helpery --------------------------------------------------------------

def test_sg_label_and_asm():
    assert sg_label("10.11.11.1", "232.1.1.1") == "(10.11.11.1, 232.1.1.1)"
    assert sg_label(None, "239.1.1.1") == "(*, 239.1.1.1)"


def test_igmp_pairs_is_sorted_unique_and_scoped():
    facts = {"igmp_group": {
        POST: [{"source": "10.0.0.2", "group": "232.0.0.2"},
               {"source": "10.0.0.1", "group": "232.0.0.1"},
               {"source": "10.0.0.1", "group": "232.0.0.1"}],
        "irb.9": [{"source": "1.1.1.1", "group": "239.9.9.9"}],
    }}
    assert igmp_pairs(facts, _scope()) == [("10.0.0.1", "232.0.0.1"), ("10.0.0.2", "232.0.0.2")]
    assert igmp_pairs(None, _scope()) == []


def test_routes_for_exact_and_asm():
    table = {"10.0.0.1,232.0.0.1": {"a": 1}, "10.0.0.2,232.0.0.1": {"b": 2}}
    assert routes_for(table, "10.0.0.1", "232.0.0.1") == [("10.0.0.1,232.0.0.1", {"a": 1})]
    assert [k for k, _ in routes_for(table, None, "232.0.0.1")] == [
        "10.0.0.1,232.0.0.1", "10.0.0.2,232.0.0.1"]
    assert routes_for(table, "9.9.9.9", "232.0.0.1") == []


def test_format_uptime_hms():
    assert format_uptime_hms(3266) == "00:54:26"
    assert format_uptime_hms(93784) == "1d 02:03:04"
    assert format_uptime_hms(None) == "-"


def test_stream_rows_skip_when_rate_missing():
    """junos-evo: multicast-statistics-timed-out, absence neni nula."""
    for route in ({}, {"forwarding_rate_pps": None}):
        rate_row, uptime_row = stream_rows("(*, 232.1.1.1)", route, rate_label="Upstream")
        assert rate_row.outcome is Outcome.SKIP
        assert rate_row.value == RATE_UNAVAILABLE

    rate_row, _ = stream_rows(
        "(*, 232.1.1.1)", {"forwarding_rate_pps": 0}, rate_label="Upstream",
    )
    assert rate_row.outcome is Outcome.BROKEN
    assert rate_row.value == "0 pps"


# --- igmp_membership_report -----------------------------------------------

def test_igmp_report_pass_lists_pairs():
    (row,) = IgmpMembershipReportCheck().run(_ctx(_igmp(POST, SG)))
    assert row.outcome is Outcome.OK
    assert row.label == "IGMP membership report"
    assert row.value == "(10.11.11.1, 232.1.1.1)"


def test_igmp_report_missing_is_fail():
    (row,) = IgmpMembershipReportCheck().run(_ctx({"igmp_group": {}}))
    assert row.outcome is Outcome.BROKEN
    assert row.value == NO_REPORT


def test_igmp_report_same_set_against_baseline_is_pass():
    (row,) = IgmpMembershipReportCheck().run(_ctx(
        _igmp(POST, SG), baseline=_igmp(PRE, SG),
        baseline_scope=_scope(PRE),
    ))
    assert row.outcome is Outcome.OK
    assert row.baseline_value == "(10.11.11.1, 232.1.1.1)"


def test_igmp_report_changed_set_is_warn():
    """Mutant: nahrazeni DEGRADED -> OK ve vetvi 'mnozina se lisi' tenhle
    test polozi (overit spustenim v Tasku 11)."""
    (row,) = IgmpMembershipReportCheck().run(_ctx(
        _igmp(POST, SG), baseline=_igmp(PRE, SG, ("10.11.11.2", "232.1.1.2")),
        baseline_scope=_scope(PRE),
    ))
    assert row.outcome is Outcome.DEGRADED
    assert row.baseline_value == "(10.11.11.1, 232.1.1.1), (10.11.11.2, 232.1.1.2)"


def test_igmp_report_baseline_without_groups_is_not_compared():
    (row,) = IgmpMembershipReportCheck().run(_ctx(
        _igmp(POST, SG), baseline={"igmp_group": {}}, baseline_scope=_scope(PRE),
    ))
    assert row.outcome is Outcome.OK
    assert row.baseline_value is None


def test_igmp_check_applies_only_to_multicast_subtypes():
    check = IgmpMembershipReportCheck()
    assert check.applies_to(_scope())
    assert check.applies_to(_scope("irb.2", "IPVPN", "mvpn-igmp", ["RI"]))
    assert not check.applies_to(_scope("et-0/0/8.13", "Internet", None))
    assert not check.applies_to(_scope("irb.3", "IPVPN", None, ["RI"]))
    assert not check.applies_to(_scope("lo0.0", "Core", "loopback"))
