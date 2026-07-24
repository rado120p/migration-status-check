import pytest

from migration_validator import api
from migration_validator.models.result import Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot

NOW = "2026-07-24T11:40:02Z"


def _scope(scope_id, description, service_type, interface, peers=()):
    return Scope(
        id=scope_id,
        kind="service",
        key=ScopeKey(description, service_type, None),
        selectors=Selectors(interfaces=[interface], bgp_neighbors=list(peers)),
    )


def _snapshot(address, interface, scopes, *, pps=400, peers=None, phase="pre-migration"):
    facts = {
        "interfaces": {
            interface: {
                "admin_status": "up",
                "oper_status": "up",
                "input_pps": pps,
                "output_pps": pps,
                "input_errors": 0,
                "output_errors": 0,
            }
        },
        "arp": [{"ip": "198.11.13.2", "interface": interface}],
        "bgp": peers if peers is not None else {},
    }
    return Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(
            started_at=NOW,
            finished_at=NOW,
            phase=phase,
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts=facts,
        probes={"ping": [{"scope_id": scopes[0].id, "target": "198.11.13.2",
                          "sent": 5, "received": 5}]},
        scopes=scopes,
        inventory=[],
    )


def _old():
    scopes = [_scope("svc:L3VPN:IPVPN", "L3VPN", "IPVPN", "ge-0/0/2.113")]
    return _snapshot("172.20.20.4", "ge-0/0/2.113", scopes)


def _new(pps=400, extra_scope=False):
    scopes = [_scope("svc:L3VPN:IPVPN", "L3VPN", "IPVPN", "et-0/0/8.113")]
    if extra_scope:
        scopes.append(_scope("svc:NOVA:E-LAN", "NOVA", "E-LAN", "ae0.14"))
    snapshot = _snapshot(
        "172.20.20.5", "et-0/0/8.113", scopes, pps=pps, phase="post-migration"
    )
    if extra_scope:
        snapshot.facts["interfaces"]["ae0.14"] = {
            "admin_status": "up",
            "oper_status": "up",
            "input_pps": 10,
            "output_pps": 10,
        }
    return snapshot


def test_evaluate_without_baseline_runs_state_checks_only():
    result = api.evaluate(_old(), now=NOW)

    assert result.baseline is None
    assert len(result.scopes) == 1
    scope = result.scopes[0]
    assert scope.match is None
    compare_only = [c for c in scope.checks if c.id == "bgp_prefix_counts"]
    assert all(c.status is Status.SKIP for c in compare_only)


def test_healthy_scope_without_baseline_is_pass_not_skip():
    """Compare-only check dava SKIP, ale zdrava sluzba musi svitit zelene."""
    result = api.evaluate(_old(), now=NOW)

    scope = result.scopes[0]
    assert any(check.status is Status.SKIP for check in scope.checks)
    assert scope.status is Status.PASS


def test_scope_is_skip_only_when_everything_skipped():
    subject = _old()
    subject.capture.collectors = {
        name: {"status": "error", "message": "RpcError: timeout"}
        for name in ("interfaces", "arp", "bgp", "evpn_vpws", "evpn_esi", "evpn_mac")
    }
    subject.probes = {"ping": []}  # bez cilu -> ping_reachability tez SKIP

    result = api.evaluate(subject, now=NOW)

    assert result.scopes[0].status is Status.SKIP


def test_evaluate_with_baseline_matches_and_compares():
    result = api.evaluate(_new(), baseline=_old(), now=NOW)

    assert result.summary["scopes_matched"] == 1
    scope = result.scopes[0]
    assert scope.match.status == "matched"
    assert scope.match.method == "description+service_type"
    assert scope.match.baseline_interfaces == ["ge-0/0/2.113"]
    assert scope.match.subject_interfaces == ["et-0/0/8.113"]


def test_traffic_drop_surfaces_as_warn_in_summary():
    result = api.evaluate(_new(pps=100), baseline=_old(), now=NOW)

    traffic = [c for c in result.scopes[0].checks if c.id == "interface_traffic"]
    assert traffic[0].status is Status.WARN
    assert result.summary["warn"] >= 1


def test_unmatched_subject_scope_is_still_state_validated():
    result = api.evaluate(_new(extra_scope=True), baseline=_old(), now=NOW)

    assert result.summary["unmatched_subject"] == 1
    new_scope = next(s for s in result.scopes if s.scope_id == "svc:NOVA:E-LAN")
    assert new_scope.match.status == "unmatched"
    assert any(c.id == "interface_state" for c in new_scope.checks)
    assert result.unmatched["subject"][0]["scope_id"] == "svc:NOVA:E-LAN"


def test_unmatched_baseline_scope_is_reported_without_checks():
    old = _old()
    old.scopes.append(_scope("svc:ZMIZELA:Internet", "ZMIZELA", "Internet", "ge-0/0/6.0"))

    result = api.evaluate(_new(), baseline=old, now=NOW)

    assert result.summary["unmatched_baseline"] == 1
    assert result.unmatched["baseline"][0]["scope_id"] == "svc:ZMIZELA:Internet"
    assert all(s.scope_id != "svc:ZMIZELA:Internet" for s in result.scopes)


def test_unassigned_bgp_peers_are_reported():
    peers = {"10.9.9.9": {"state": "Established", "routing_instance": None}}
    subject = _new()
    subject.facts["bgp"] = peers

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["bgp_peers"][0]["peer"] == "10.9.9.9"


def test_failed_collector_produces_skip_not_pass():
    subject = _new()
    subject.capture.collectors["interfaces"] = {
        "status": "error",
        "message": "RpcError: timeout",
    }

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    state = [c for c in result.scopes[0].checks if c.id == "interface_state"]
    assert state[0].status is Status.SKIP
    assert "RpcError: timeout" in state[0].message


def test_snapshot_without_scopes_falls_back_to_device_scope():
    subject = _new()
    subject.scopes = []
    subject.inventory = None

    result = api.evaluate(subject, now=NOW)

    assert len(result.scopes) == 1
    assert result.scopes[0].scope_id == "device"


def test_summary_counts_every_check():
    result = api.evaluate(_new(), baseline=_old(), now=NOW)
    counted = sum(
        result.summary[key] for key in ("pass", "warn", "fail", "skip")
    )
    total = sum(len(scope.checks) for scope in result.scopes)
    assert counted == total


def test_list_checks_exposes_registry():
    described = api.list_checks()
    ids = {item["id"] for item in described}
    assert "interface_traffic" in ids
    assert "evpn_esi_status" in ids
    assert all("mode" in item and "default_severity" in item for item in described)


def test_result_is_json_serialisable():
    import json

    payload = api.evaluate(_new(), baseline=_old(), now=NOW).to_dict()
    assert json.loads(json.dumps(payload, ensure_ascii=False))["schema_version"] == 1
