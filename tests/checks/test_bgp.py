from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.bgp import BgpPrefixCountsCheck, BgpSessionStateCheck
from migration_validator.config import CheckConfig, default_config
from migration_validator.models.result import Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _ctx(subject, baseline=None, config=None):
    scope = Scope(
        id="svc:L3VPN-CPE13-NNI:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"], bgp_neighbors=["198.11.13.2"]
        ),
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=baseline,
        config=config or default_config(),
        failed_collectors={},
    )


def _peer(state="Established", received=14, accepted=14, advertised=3):
    return {
        "state": state,
        "routing_instance": "L3VPN-CPE13-NNI",
        "prefixes": {
            "received": received,
            "accepted": accepted,
            "advertised": advertised,
        },
    }


def test_established_passes():
    result = run_check(BgpSessionStateCheck(), _ctx({"bgp": {"198.11.13.2": _peer()}}))[0]
    assert result.status is Status.PASS
    assert result.label == "198.11.13.2"


def test_active_state_fails():
    ctx = _ctx({"bgp": {"198.11.13.2": _peer(state="Active")}})
    result = run_check(BgpSessionStateCheck(), ctx)[0]
    assert result.status is Status.FAIL
    assert "Active" in result.message


def test_no_bgp_peers_skips():
    result = run_check(BgpSessionStateCheck(), _ctx({"bgp": {}}))[0]
    assert result.status is Status.SKIP
    assert "BGP" in result.message


def test_state_change_from_active_to_established_is_warn_not_fail():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(state="Established")}},
        baseline={"bgp": {"198.11.13.2": _peer(state="Active")}},
    )
    result = run_check(BgpSessionStateCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert "Active" in result.message and "Established" in result.message


def test_peer_missing_in_baseline_is_evaluated_as_state_only():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer()}},
        baseline={"bgp": {}},
    )
    assert run_check(BgpSessionStateCheck(), ctx)[0].status is Status.PASS


def test_prefix_counts_within_tolerance_pass():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=13, accepted=13)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    result = run_check(BgpPrefixCountsCheck(), ctx)[0]
    assert result.status is Status.PASS


def test_prefix_counts_below_tolerance_warn():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=5, accepted=5)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    result = run_check(BgpPrefixCountsCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert "received" in result.message
    assert result.baseline["received"] == 14
    assert result.subject["received"] == 5


def test_prefix_tolerance_is_configurable():
    config = CheckConfig({"bgp_prefix_counts": {"tolerance_percent": -90}})
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=5, accepted=5)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
        config=config,
    )
    assert run_check(BgpPrefixCountsCheck(), ctx)[0].status is Status.PASS


def test_prefix_counts_without_baseline_skips():
    result = run_check(BgpPrefixCountsCheck(), _ctx({"bgp": {"198.11.13.2": _peer()}}))[0]
    assert result.status is Status.SKIP
    assert "baseline" in result.message


def test_prefix_counts_peer_missing_in_baseline_skips_that_peer():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer()}},
        baseline={"bgp": {}},
    )
    result = run_check(BgpPrefixCountsCheck(), ctx)[0]
    assert result.status is Status.SKIP
    assert "198.11.13.2" in result.message


def test_prefix_growth_is_not_a_problem():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=40, accepted=40)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    assert run_check(BgpPrefixCountsCheck(), ctx)[0].status is Status.PASS
