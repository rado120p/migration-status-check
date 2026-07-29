from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.bgp import (
    BgpPrefixCountsCheck,
    BgpSessionStateCheck,
    peer_family,
)
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


def _peer(
    state="Established",
    rib="inet.0",
    received=14,
    accepted=14,
    advertised=3,
    active=None,
    suppressed=0,
):
    if active is None:
        active = accepted
    return {
        "state": state,
        "routing_instance": "L3VPN-CPE13-NNI",
        "ribs": {
            rib: {
                "received": received,
                "accepted": accepted,
                "advertised": advertised,
                "active": active,
                "suppressed": suppressed,
            }
        },
    }


def _by_label(results, label):
    """Vybere finding podle labelu - po rozdeleni na RIB uz nejde spolehnout
    na to, ze hledany radek je na indexu 0."""
    matches = [result for result in results if result.label == label]
    assert len(matches) == 1, f"ocekavan 1 finding s labelem {label!r}, je jich {len(matches)}"
    return matches[0]


def test_session_findings_carry_peer_family():
    subject = {
        "bgp": {
            "152.11.13.2": _peer(),
            "2001:abcd:11:13::b": {
                "state": "Established",
                "routing_instance": None,
                "ribs": {"inet6.0": {"received": 0, "accepted": 0, "advertised": 1,
                                     "active": 0, "suppressed": 0}},
            },
        }
    }

    findings = run_check(BgpSessionStateCheck(), _ctx(subject))
    by_family = {finding.family for finding in findings}

    assert by_family == {4, 6}
    assert all(finding.label == "BGP status" for finding in findings)
    assert all(finding.value == "Established" for finding in findings)


def test_established_passes():
    result = run_check(BgpSessionStateCheck(), _ctx({"bgp": {"198.11.13.2": _peer()}}))[0]
    assert result.status is Status.PASS
    assert result.label == "BGP status"
    assert result.family == 4
    assert result.value == "Established"


def test_active_state_fails():
    ctx = _ctx({"bgp": {"198.11.13.2": _peer(state="Active")}})
    result = run_check(BgpSessionStateCheck(), ctx)[0]
    assert result.status is Status.FAIL
    assert "Active" in result.message


def test_no_bgp_peers_skips():
    result = run_check(BgpSessionStateCheck(), _ctx({"bgp": {}}))[0]
    assert result.status is Status.SKIP
    assert "BGP" in result.message


def test_state_change_to_established_is_pass_not_warn():
    """Rozhodnuti R-2: zlepseni neni varovani.

    Tahle vetev je dosazitelna jen kdyz je stav Established (horsi stavy
    odchazi drive), takze pokryva presne a pouze pripad, kdy se relace
    behem migrace zlepsila. WARN na zdrave sluzbe je falesny poplach -
    presne ten trvaly oranzovy svit, proti kteremu se rozhodovalo v Tasku 7.

    Zmena nezmizi: hlaska ji pojmenuje a sloupec ZMENA pise 'bylo Active',
    takze nezdrava baseline zustane videt.
    """
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(state="Established")}},
        baseline={"bgp": {"198.11.13.2": _peer(state="Active")}},
    )
    result = run_check(BgpSessionStateCheck(), ctx)[0]

    assert result.status is Status.PASS
    assert "Active" in result.message and "Established" in result.message
    assert result.baseline_value == "Active"


def test_changed_session_carries_baseline_value():
    """Regrese na live nalez: svc:et-0/0/10.0:IPVPN hlasilo v souhrnu 'stav
    se zmenil Connect -> Established', ale radek bloku ukazoval 'bez
    baseline' - protoze DEGRADED vetev nastavovala jen `baseline` (dict
    pro JSON), ne `baseline_value` (pole, ze ktereho report sklada sloupec
    ZMENA). Bez `baseline_value` ZMENA cte baseline_value is None jako
    'bez baseline', presestoze check presne vi, jaky stav byl predtim.
    """
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(state="Established")}},
        baseline={"bgp": {"198.11.13.2": _peer(state="Connect")}},
    )
    result = run_check(BgpSessionStateCheck(), ctx)[0]

    assert result.status is Status.PASS
    assert result.baseline_value == "Connect"


def test_broken_session_carries_baseline_value():
    """Druha polovina te same regrese, kterou resi test vyse - a ta horsi.

    Vetev pro spadlou relaci vytvarela Finding drive, nez se baseline stav
    vubec dohledal, takze report u nej psal 'bez baseline'. Sloupec ZMENA
    vznikl proto, aby byla regrese videt; zamlcoval ji presne u peeru, ktery
    spadl. Doloženo na runs/ipv6: peer 152.11.13.2 je Established v pre a
    Connect v post snapshotu.
    """
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(state="Connect")}},
        baseline={"bgp": {"198.11.13.2": _peer(state="Established")}},
    )
    result = run_check(BgpSessionStateCheck(), ctx)[0]

    assert result.status is Status.FAIL
    assert result.baseline_value == "Established"


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
    results = run_check(BgpPrefixCountsCheck(), ctx)
    result = _by_label(results, "BGP received-prefix-count")
    assert result.status is Status.PASS


def test_prefix_counts_below_tolerance_warn():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=5, accepted=5)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    result = _by_label(results, "BGP received-prefix-count")
    assert result.status is Status.WARN
    assert "received" in result.message
    assert result.baseline == {"received": 14}
    assert result.subject == {"received": 5}


def test_prefix_tolerance_is_configurable():
    config = CheckConfig({"bgp_prefix_counts": {"tolerance_percent": -90}})
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=5, accepted=5)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
        config=config,
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    result = _by_label(results, "BGP received-prefix-count")
    assert result.status is Status.PASS


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


def test_prefix_counts_rib_missing_in_baseline_skips_that_rib():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(rib="inet6.0")}},
        baseline={"bgp": {"198.11.13.2": _peer(rib="inet.0")}},
    )
    result = run_check(BgpPrefixCountsCheck(), ctx)[0]
    assert result.status is Status.SKIP
    assert "inet6.0" in result.message
    assert result.label == "BGP prefixy (inet6.0)"


def test_prefix_growth_is_not_a_problem():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=40, accepted=40)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    result = _by_label(results, "BGP received-prefix-count")
    assert result.status is Status.PASS


def test_prefix_counts_are_reported_per_rib_not_summed():
    """Kdyby se countery pri porovnani zase scitaly pres RIB, pokles 40
    prefixu v inet6.0 vyvazeny narustem 40 v inet.0 by presel jako OK -
    tenhle test na takovy revert musi spadnout."""
    ctx = _ctx(
        subject={
            "bgp": {
                "198.11.13.2": {
                    "state": "Established",
                    "routing_instance": None,
                    "ribs": {
                        "inet.0": {
                            "received": 54, "accepted": 54, "advertised": 3,
                            "active": 54, "suppressed": 0,
                        },
                        "inet6.0": {
                            "received": 0, "accepted": 0, "advertised": 1,
                            "active": 0, "suppressed": 0,
                        },
                    },
                }
            }
        },
        baseline={
            "bgp": {
                "198.11.13.2": {
                    "state": "Established",
                    "routing_instance": None,
                    "ribs": {
                        "inet.0": {
                            "received": 14, "accepted": 14, "advertised": 3,
                            "active": 14, "suppressed": 0,
                        },
                        "inet6.0": {
                            "received": 40, "accepted": 40, "advertised": 1,
                            "active": 40, "suppressed": 0,
                        },
                    },
                }
            }
        },
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    labels = {result.label: result for result in results}
    assert labels["BGP received-prefix-count"].status is Status.WARN
    assert "inet6.0" in labels["BGP received-prefix-count"].message


def test_prefix_finding_family_is_derived_from_peer_address_not_rib_name():
    ctx = _ctx(
        subject={
            "bgp": {
                "198.11.13.2": _peer(rib="inet.0"),
                "2001:db8:11:13::b": _peer(rib="inet6.0"),
            }
        },
        baseline={
            "bgp": {
                "198.11.13.2": _peer(rib="inet.0"),
                "2001:db8:11:13::b": _peer(rib="inet6.0"),
            }
        },
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    families = {result.family for result in results}
    assert families == {4, 6}


def test_peer_family_derived_from_address():
    assert peer_family("198.11.13.2") == 4
    assert peer_family("2001:db8:11:13::b") == 6
    assert peer_family("not-an-address") is None
