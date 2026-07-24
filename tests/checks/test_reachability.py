from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.reachability import ArpPresentCheck, PingReachabilityCheck
from migration_validator.config import default_config
from migration_validator.models.result import Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope


def _ctx(subject, service_type="IPVPN", scope=None):
    scope = scope or Scope(
        id="svc:X:" + service_type,
        kind="service",
        key=ScopeKey("X", service_type, None),
        selectors=Selectors(interfaces=["ge-0/0/2.113"]),
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=None,
        config=default_config(),
        failed_collectors={},
    )


def test_arp_present_passes_with_entries():
    ctx = _ctx({"arp": [{"ip": "198.11.13.2", "interface": "ge-0/0/2.113"}]})
    result = run_check(ArpPresentCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert "198.11.13.2" in result.message


def test_arp_empty_warns():
    result = run_check(ArpPresentCheck(), _ctx({"arp": []}))[0]
    assert result.status is Status.WARN


def test_arp_not_run_on_core_scope():
    assert run_check(ArpPresentCheck(), _ctx({"arp": []}, service_type="Core")) == []


def test_arp_skips_on_device_scope():
    ctx = _ctx({"arp": []}, scope=device_scope())
    result = run_check(ArpPresentCheck(), ctx)[0]
    assert result.status is Status.SKIP
    assert "inventory" in result.message


def test_ping_all_targets_reachable_passes():
    ctx = _ctx(
        {
            "ping": [
                {"target": "198.11.13.2", "sent": 5, "received": 5, "loss_percent": 0},
                {"target": "198.11.13.3", "sent": 5, "received": 5, "loss_percent": 0},
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.PASS
    assert result.details["reachable"] == 2
    assert result.details["total"] == 2


def test_ping_partial_success_is_warn_with_per_target_detail():
    ctx = _ctx(
        {
            "ping": [
                {"target": "198.11.13.2", "sent": 5, "received": 5, "loss_percent": 0},
                {"target": "198.11.13.3", "sent": 5, "received": 0, "loss_percent": 100},
                {"target": "198.11.13.4", "sent": 5, "received": 5, "loss_percent": 0},
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.WARN
    assert result.details["reachable"] == 2
    assert result.details["targets"]["198.11.13.3"]["received"] == 0
    assert "198.11.13.3" in result.message


def test_ping_no_target_reachable_warns_as_advisory():
    ctx = _ctx({"ping": [{"target": "198.11.13.2", "sent": 5, "received": 0}]})
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.status is Status.WARN


def test_ping_without_targets_skips():
    result = run_check(PingReachabilityCheck(), _ctx({"ping": []}))[0]
    assert result.status is Status.SKIP
    assert "cile" in result.message


def test_ping_records_fallback_resolution():
    ctx = _ctx(
        {
            "ping": [
                {
                    "target": "198.11.13.2",
                    "sent": 5,
                    "received": 5,
                    "resolved_from": "subnet-fallback",
                }
            ]
        }
    )
    result = run_check(PingReachabilityCheck(), ctx)[0]
    assert result.details["targets"]["198.11.13.2"]["resolved_from"] == "subnet-fallback"
