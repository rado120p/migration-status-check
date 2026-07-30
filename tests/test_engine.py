import pytest

from migration_validator import api
from migration_validator.engine import _identity
from migration_validator.models.result import Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot

NOW = "2026-07-24T11:40:02Z"


def _scope(scope_id, description, service_type, interface, peers=(), physical=()):
    return Scope(
        id=scope_id,
        kind="service",
        key=ScopeKey(description, service_type, None),
        selectors=Selectors(
            interfaces=[interface],
            physical_interfaces=list(physical),
            bgp_neighbors=list(peers),
        ),
    )


def _snapshot(
    address, interface, scopes, *, pps=400, peers=None, phase="pre-migration", physical=None
):
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
        "nd": [
            {
                "ip": "2001:db8:11:13::2",
                "mac": "0c:00:ef:5e:df:01",
                "interface": interface,
                "state": "reachable",
            }
        ],
        "bgp": peers if peers is not None else {},
    }
    if physical:
        facts["interfaces"][physical] = {
            "admin_status": "up",
            "oper_status": "up",
            "input_pps": pps,
            "output_pps": pps,
            "input_errors": 0,
            "output_errors": 0,
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
                          "family": 4, "sent": 5, "received": 5}]},
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
        for name in ("interfaces", "arp", "nd", "bgp", "evpn_vpws", "evpn_esi", "evpn_mac")
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


def test_physical_interface_finds_its_baseline_after_rename():
    """Migrace prejmenovava i fyzicke rozhrani, ne jen logicke.

    _aligned_baseline_data preslovnovalo jen selectors.interfaces, takze
    fyzicke rozhrani svou baseline nikdy nenaslo a kazda migrovana sluzba
    vypsala dva trvale radky 'WARN 0 pps | bez baseline'. V ostrem behu to
    bylo 16 z 36 varovani - presne ten trvaly oranzovy svit, kvuli kteremu
    counter checky na internich rozhranich davaji SKIP misto WARN.
    """
    baseline = _snapshot(
        "172.20.20.4",
        "ge-0/0/2.113",
        [_scope("svc:L3VPN:IPVPN", "L3VPN", "IPVPN", "ge-0/0/2.113",
                physical=["ge-0/0/2"])],
        physical="ge-0/0/2",
    )
    subject = _snapshot(
        "172.20.20.5",
        "et-0/0/8.113",
        [_scope("svc:L3VPN:IPVPN", "L3VPN", "IPVPN", "et-0/0/8.113",
                physical=["et-0/0/8"])],
        phase="post-migration",
        physical="et-0/0/8",
    )

    result = api.evaluate(subject, baseline=baseline, now=NOW)

    physical = [
        check
        for check in result.scopes[0].checks
        if check.id == "interface_traffic" and check.message.startswith("et-0/0/8:")
    ]
    assert len(physical) == 2, "fyzicke rozhrani ma mit radek pro oba smery provozu"
    assert [check.baseline_value for check in physical] == ["400 pps", "400 pps"]


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


MGMT_ROUTE = {
    "mgmt_junos.inet.0": {
        "0.0.0.0/0": {"next_hop": ["10.0.0.2"], "via": ["fxp0.0"], "active": True}
    }
}

SERVICE_ROUTE = {
    "inet.0": {
        "198.62.1.0/29": {
            "next_hop": ["152.11.13.2"],
            "via": ["et-0/0/8.13"],
            "active": True,
        }
    }
}


def test_management_static_route_lands_in_unassigned():
    """Statika v mgmt_junos padne na fxp0.0, ze ktere se scope nikdy nestane.

    Do inventory se nedostane. Kdyby ji nezachytil unassigned, zmizela by
    z vystupu uplne.
    """
    subject = _new()
    subject.facts["routes"] = MGMT_ROUTE

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["static_routes"] == [
        {
            "rib": "mgmt_junos.inet.0",
            "prefix": "0.0.0.0/0",
            "next_hop": ["10.0.0.2"],
            "via": ["fxp0.0"],
            "snapshot": "subject",
        }
    ]


def test_route_claimed_by_a_scope_is_not_unassigned():
    subject = _new()
    subject.facts["routes"] = SERVICE_ROUTE
    subject.scopes[0].selectors.static_routes = [
        {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": ["152.11.13.2"]}
    ]

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["static_routes"] == []


def test_unassigned_static_route_claim_must_match_rib_not_just_prefix():
    """Klic je (rib, prefix) - shoda jen na prefixu nestaci.

    0.0.0.0/0 casto existuje soucasne v inet.0 i v mgmt_junos.inet.0. Kdyby
    se assigned mnozina klicovala jen prefixem, claim v inet.0 by tise
    schoval statiku ve mgmt_junos.inet.0.
    """
    subject = _new()
    subject.facts["routes"] = {
        "inet.0": {
            "0.0.0.0/0": {
                "next_hop": ["152.11.13.1"],
                "via": ["et-0/0/8.13"],
                "active": True,
            }
        },
        "mgmt_junos.inet.0": {
            "0.0.0.0/0": {"next_hop": ["10.0.0.2"], "via": ["fxp0.0"], "active": True}
        },
    }
    subject.scopes[0].selectors.static_routes = [
        {"rib": "inet.0", "prefix": "0.0.0.0/0", "next_hop": ["152.11.13.1"]}
    ]

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["static_routes"] == [
        {
            "rib": "mgmt_junos.inet.0",
            "prefix": "0.0.0.0/0",
            "next_hop": ["10.0.0.2"],
            "via": ["fxp0.0"],
            "snapshot": "subject",
        }
    ]


def test_bfd_session_of_unknown_peer_lands_in_unassigned():
    subject = _new()
    subject.facts["bfd"] = {"10.1.1.1": {"state": "Up", "interface": "et-0/0/9.0"}}

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["bfd_sessions"] == [
        {
            "peer": "10.1.1.1",
            "interface": "et-0/0/9.0",
            "state": "Up",
            "snapshot": "subject",
        }
    ]


def test_bfd_session_of_known_peer_is_not_unassigned():
    subject = _new()
    subject.facts["bfd"] = {"10.1.1.1": {"state": "Up", "interface": "et-0/0/9.0"}}
    subject.scopes[0].selectors.bgp_neighbors = ["10.1.1.1"]

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["bfd_sessions"] == []


def test_unassigned_bfd_session_ignores_intent_not_in_bgp_neighbors():
    """Assigned mnozina se klicuje bgp_neighbors, ne bfd_peers zamerem.

    Chyti implementaci, ktera by do "assigned" sjednotila i zamer
    (`{str(b.get("peer")) for scope in scopes for b in
    scope.selectors.bfd_peers}`) - presne anti-vzor, ktery AR-14 a komentar
    u `Scope.select()` (`models/scope.py:177-181`) zakazuji. Peer je
    v zameru (`bfd_peers`), ale nikdy se nedostal do `bgp_neighbors` -
    to je zrovna ten pripad meznery v parsovani, kvuli ktere `unassigned`
    existuje. Kdyby se zamer sjednotil do "assigned", session by se tise
    ztratila misto aby upozornila na rozpor.
    """
    subject = _new()
    subject.facts["bfd"] = {"10.1.1.1": {"state": "Up", "interface": "et-0/0/9.0"}}
    subject.scopes[0].selectors.bfd_peers = [{"peer": "10.1.1.1"}]

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["bfd_sessions"] == [
        {
            "peer": "10.1.1.1",
            "interface": "et-0/0/9.0",
            "state": "Up",
            "snapshot": "subject",
        }
    ]


def test_device_scope_reports_nothing_as_unassigned():
    """Device scope propousti vsechno, takze nic neprirazene byt nemuze."""
    subject = _new()
    subject.facts["routes"] = MGMT_ROUTE
    subject.facts["bfd"] = {"10.1.1.1": {"state": "Up", "interface": "et-0/0/9.0"}}
    subject.scopes = []

    result = api.evaluate(subject, now=NOW)

    assert result.unassigned["static_routes"] == []
    assert result.unassigned["bfd_sessions"] == []


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


def test_identity_maps_each_address_field_to_its_own_key():
    """Distinct hodnoty pro kazde pole - zamena v4/v6 by tichem prosla, kdyby
    hodnoty byly stejne."""
    scope = Scope(
        id="svc:X:Internet",
        kind="service",
        key=ScopeKey("X", "Internet", "residential"),
        selectors=Selectors(
            interfaces=["et-0/0/8.13"],
            routing_instances=["VRF-X"],
            local_ipv4=["192.0.2.1/30"],
            local_ipv6=["2001:db8::1/64"],
            virtual_gw_v4=["192.0.2.2"],
            virtual_gw_v6=["2001:db8::2"],
        ),
    )

    identity = _identity(scope)

    assert identity["description"] == "X"
    assert identity["service_type"] == "Internet"
    assert identity["service_subtype"] == "residential"
    assert identity["routing_instance"] == "VRF-X"
    assert identity["interfaces"] == ["et-0/0/8.13"]
    assert identity["ipv4"] == ["192.0.2.1/30"]
    assert identity["ipv6"] == ["2001:db8::1/64"]
    assert identity["virtual_gw_v4"] == ["192.0.2.2"]
    assert identity["virtual_gw_v6"] == ["2001:db8::2"]


def test_identity_on_device_scope_is_empty_not_crashing():
    identity = _identity(device_scope())

    assert identity == {
        "description": None,
        "service_type": None,
        "service_subtype": None,
        "routing_instance": None,
        "interfaces": [],
        "ipv4": [],
        "ipv6": [],
        "virtual_gw_v4": [],
        "virtual_gw_v6": [],
    }


def test_identity_without_routing_instance_is_none_not_indexerror():
    scope = Scope(
        id="svc:Y:Internet",
        kind="service",
        key=ScopeKey("Y", "Internet", None),
        selectors=Selectors(),
    )

    assert _identity(scope)["routing_instance"] is None


def _deactivated(snapshot):
    """Oznaci vsechny service scopy snimku za deaktivovane."""
    for scope in snapshot.scopes:
        scope.interface_active = False
    return snapshot


def test_service_deactivated_on_both_sides_is_pass():
    """Deaktivovano na obou stranach = PASS, ne SKIP - stav se nezmenil.

    Ostatni checky SKIPnou (AR-22), projde jen OK z deactivation_state,
    a Status.worst z jedine ne-SKIP hodnoty da PASS.

    Zabiji mutanta: vyjmuti deactivation_state ze zkratky v run_check. Pak by
    SKIPl i on, `reported` by byl prazdny a sluzba by spadla do SKIP - tedy
    "nic se nezmerilo" misto "je to v poradku".
    """
    result = api.evaluate(
        _deactivated(_new()), baseline=_deactivated(_old()), now=NOW
    )

    assert result.scopes
    assert result.scopes[0].status is Status.PASS


def test_service_deactivated_only_in_subject_is_fail():
    """Bezela na starem zarizeni, na novem je deaktivovana - migrace nedokoncena.

    Zabiji mutanta: zamena BROKEN za OK v teto vetvi deactivation.py. Bez
    tohoto testu by "deaktivovano az ted" bylo k nerozeznani od
    "deaktivovano i drive".
    """
    result = api.evaluate(_deactivated(_new()), baseline=_old(), now=NOW)

    assert result.scopes
    assert result.scopes[0].status is Status.FAIL


def test_service_reactivated_after_migration_is_warn():
    """V baseline deaktivovana, ted nahozena - zmena proti baseline."""
    result = api.evaluate(_new(), baseline=_deactivated(_old()), now=NOW)

    assert result.scopes
    assert result.scopes[0].status is Status.WARN
