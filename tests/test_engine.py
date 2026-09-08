from pathlib import Path

import pytest

from migration_validator import api
from migration_validator.checks import base as check_base
from migration_validator.engine import (
    _aligned_baseline_data,
    _group_by_layer1,
    _identity,
    _unassigned_bfd_sessions,
    _unassigned_bgp_peers,
    evaluate_snapshots,
)
from migration_validator.models.result import Finding, Outcome, ScopeResult, Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope
from migration_validator.models.snapshot import CaptureMeta, DeviceMeta, Snapshot

NOW = "2026-07-24T11:40:02Z"

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEVICE_4 = str(FIXTURES / "172.20.20.4.yml")


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

    Task 8 presunulo radky fyzickeho portu z bloku sluzby do L1 bloku -
    preslovnovani se tedy overuje na scopu l1:ae0-ekvivalentu, ne uz na
    service scopu, ktery uz fyzicky port vubec netiskne.
    """
    l1_key = ("Optika CPE14", "Layer1")

    def _l1_scope(scope_id, physical):
        return Scope(
            id=scope_id,
            kind="layer1",
            key=ScopeKey(l1_key[0], l1_key[1], None),
            selectors=Selectors(interfaces=[physical]),
        )

    baseline = _snapshot(
        "172.20.20.4",
        "ge-0/0/2.113",
        [
            _scope("svc:L3VPN:IPVPN", "L3VPN", "IPVPN", "ge-0/0/2.113",
                   physical=["ge-0/0/2"]),
            _l1_scope("l1:ge-0/0/2", "ge-0/0/2"),
        ],
        physical="ge-0/0/2",
    )
    subject = _snapshot(
        "172.20.20.5",
        "et-0/0/8.113",
        [
            _scope("svc:L3VPN:IPVPN", "L3VPN", "IPVPN", "et-0/0/8.113",
                   physical=["et-0/0/8"]),
            _l1_scope("l1:et-0/0/8", "et-0/0/8"),
        ],
        phase="post-migration",
        physical="et-0/0/8",
    )

    result = api.evaluate(subject, baseline=baseline, now=NOW)

    physical = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "interface_traffic" and check.message.startswith("et-0/0/8:")
    ]
    assert len(physical) == 2, "fyzicke rozhrani ma mit radek pro oba smery provozu"
    assert [check.baseline_value for check in physical] == ["400 pps", "400 pps"]


def test_aligned_baseline_renames_evpn_mac_interface_keys():
    """Preklicovani rename mapy musi platit i pro evpn_mac interfaces.

    Jmena rozhrani se migraci meni (ge-0/0/2.313 -> et-0/0/8.313); bez
    preklicovani by per-interface MAC pocty nikdy nenasly baseline pod
    klicem subjektu (Task 3 check hleda prave pod nim).
    """
    baseline_scope = Scope(
        id="svc:EVPN-X:E-LAN",
        kind="service",
        key=ScopeKey("EVPN-X", "E-LAN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.313"],
            physical_interfaces=["ge-0/0/2"],
            routing_instances=["EVPN-X"],
        ),
    )
    subject_scope = Scope(
        id="svc:EVPN-X:E-LAN",
        kind="service",
        key=ScopeKey("EVPN-X", "E-LAN", None),
        selectors=Selectors(
            interfaces=["et-0/0/8.313"],
            physical_interfaces=["et-0/0/8"],
            routing_instances=["EVPN-X"],
        ),
    )
    baseline = Snapshot(
        device=DeviceMeta(address="172.20.20.4"),
        capture=CaptureMeta(started_at=NOW, phase="pre-migration"),
        facts={
            "evpn_mac": {
                "EVPN-X": {
                    "vlans": {"313": {"count": 2, "domain": "BD-313"}},
                    "interfaces": {
                        "ge-0/0/2.313": {
                            "count": 2,
                            "name": "ge-0/0/2.313:313",
                            "domain": "BD-313",
                        }
                    },
                }
            }
        },
        scopes=[baseline_scope],
    )

    data = _aligned_baseline_data(baseline_scope, subject_scope, baseline)

    assert set(data["evpn_mac"]["EVPN-X"]["interfaces"]) == {"et-0/0/8.313"}


def test_aligned_baseline_renames_evpn_instance_nested_entry_names():
    """R-6: evpn_instance nese jmeno rozhrani zanorene v
    local_interfaces.entries[].name a irb_interfaces.entries[].name, ne
    v klici slovniku - preklicovani proto potrebuje vlastni vetev.

    Zaznam bez jmena v rename mape (irb.10) zustava beze zmeny - stav se
    nefabuluje.
    """
    baseline_scope = Scope(
        id="svc:EVPN-X:E-LAN",
        kind="service",
        key=ScopeKey("EVPN-X", "E-LAN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/1.0"],
            routing_instances=["EVPN-X"],
        ),
    )
    subject_scope = Scope(
        id="svc:EVPN-X:E-LAN",
        kind="service",
        key=ScopeKey("EVPN-X", "E-LAN", None),
        selectors=Selectors(
            interfaces=["et-0/0/1.0"],
            routing_instances=["EVPN-X"],
        ),
    )
    baseline = Snapshot(
        device=DeviceMeta(address="172.20.20.4"),
        capture=CaptureMeta(started_at=NOW, phase="pre-migration"),
        facts={
            "evpn_instance": {
                "EVPN-X": {
                    "local_interfaces": {
                        "total": 1,
                        "up": 1,
                        "entries": [{"name": "ge-0/0/1.0", "status": "Up"}],
                    },
                    "irb_interfaces": {
                        "total": 1,
                        "up": 1,
                        "entries": [
                            {"name": "irb.10", "status": "Up", "l3_context": "default"}
                        ],
                    },
                }
            }
        },
        scopes=[baseline_scope],
    )

    data = _aligned_baseline_data(baseline_scope, subject_scope, baseline)

    instance = data["evpn_instance"]["EVPN-X"]
    assert instance["local_interfaces"]["entries"] == [
        {"name": "et-0/0/1.0", "status": "Up"}
    ]
    assert instance["irb_interfaces"]["entries"] == [
        {"name": "irb.10", "status": "Up", "l3_context": "default"}
    ]


def test_aligned_baseline_renames_bfd_session_interface_field():
    """R-6: BFD session na Core transit scopu se vybira podle rozhrani
    (Scope.select), ne podle peeru - preklicovani musi prejmenovat pole
    "interface" v zaznamu session, jinak by check po migraci na jiny
    hardware session nenasel pod jmenem subjektu.
    """
    baseline_scope = Scope(
        id="core:Core:transit",
        kind="service",
        key=ScopeKey("Core", "Core", "transit"),
        selectors=Selectors(interfaces=["ge-0/0/1.0"]),
    )
    subject_scope = Scope(
        id="core:Core:transit",
        kind="service",
        key=ScopeKey("Core", "Core", "transit"),
        selectors=Selectors(interfaces=["et-0/0/1.0"]),
    )
    baseline = Snapshot(
        device=DeviceMeta(address="172.20.20.4"),
        capture=CaptureMeta(started_at=NOW, phase="pre-migration"),
        facts={
            "bfd": {
                "198.11.13.2": {
                    "state": "Up",
                    "interface": "ge-0/0/1.0",
                }
            }
        },
        scopes=[baseline_scope],
    )

    data = _aligned_baseline_data(baseline_scope, subject_scope, baseline)

    assert data["bfd"]["198.11.13.2"]["interface"] == "et-0/0/1.0"


def test_aligned_baseline_renames_optics_keys_including_lag_members():
    """Optika je klicovana fyzickym portem (i clenem LAGu) - preklicovani
    z Tasku 11 musi platit i pro ni, jinak by po migraci na jiny hardware
    (ge-0/0/2 -> et-0/0/8, ae0 s clenem ge-0/0/3 -> ae0 s clenem et-0/0/9)
    optika navzdy hlasila 'bez baseline'.
    """
    baseline_scope = Scope(
        id="l1:ge-0/0/2",
        kind="layer1",
        key=ScopeKey("Optika CPE14", "Layer1", None),
        selectors=Selectors(
            physical_interfaces=["ge-0/0/2"],
            lag_members=["ge-0/0/3"],
        ),
    )
    subject_scope = Scope(
        id="l1:et-0/0/8",
        kind="layer1",
        key=ScopeKey("Optika CPE14", "Layer1", None),
        selectors=Selectors(
            physical_interfaces=["et-0/0/8"],
            lag_members=["et-0/0/9"],
        ),
    )
    baseline = Snapshot(
        device=DeviceMeta(address="172.20.20.4"),
        capture=CaptureMeta(started_at=NOW, phase="pre-migration"),
        facts={
            "optics": {
                "ge-0/0/2": {"lanes": [{"lane": 0, "rx_power_dbm": -3.0}]},
                "ge-0/0/3": {"lanes": [{"lane": 0, "rx_power_dbm": -4.0}]},
            }
        },
        scopes=[baseline_scope],
    )

    data = _aligned_baseline_data(baseline_scope, subject_scope, baseline)

    assert set(data["optics"]) == {"et-0/0/8", "et-0/0/9"}


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


def _multi_service_snapshot(phase="pre-migration"):
    """Snapshot se dvema sluzbami ruznych typu (IPVPN + Internet) a L1
    blokem - pro overeni, ze service_types filtr sahne jen na sluzby."""
    ipvpn = _scope("svc:L3VPN:IPVPN", "L3VPN", "IPVPN", "ge-0/0/2.113")
    internet = _scope("svc:NET:Internet", "NET", "Internet", "ge-0/0/3.0")
    l1 = Scope(
        id="l1:ge-0/0/4",
        kind="layer1",
        key=ScopeKey("Optika X", "Layer1", None),
        selectors=Selectors(interfaces=["ge-0/0/4"]),
    )
    snapshot = _snapshot(
        "172.20.20.4", "ge-0/0/2.113", [ipvpn, internet, l1], phase=phase
    )
    snapshot.facts["interfaces"]["ge-0/0/3.0"] = {
        "admin_status": "up",
        "oper_status": "up",
        "input_pps": 100,
        "output_pps": 100,
        "input_errors": 0,
        "output_errors": 0,
    }
    snapshot.facts["interfaces"]["ge-0/0/4"] = {
        "admin_status": "up",
        "oper_status": "up",
        "input_pps": 0,
        "output_pps": 0,
        "input_errors": 0,
        "output_errors": 0,
    }
    return snapshot


def test_service_types_filtruje_check_smycku():
    subject = _multi_service_snapshot()

    result = evaluate_snapshots(
        subject, service_types=["IPVPN"], profile_name="core-only.yml", now=NOW
    )

    typy = {
        scope.key.get("service_type")
        for scope in result.scopes
        if scope.key and scope.key.get("service_type")
    }
    assert "Internet" not in typy
    assert result.filtered["service_types"] == ["IPVPN"]
    assert result.filtered["profile"] == "core-only.yml"
    assert result.filtered["scopes_total"] > result.filtered["scopes_shown"]


def test_device_a_layer1_scopy_filtru_nepodlehaji():
    subject = _multi_service_snapshot()

    result = evaluate_snapshots(subject, service_types=["IPVPN"], now=NOW)

    ids = {scope.scope_id for scope in result.scopes}
    # L1 blok zustava v reportu i kdyz jeho service_type neni v `IPVPN` -
    # filtr je jen na sluzby, ne na infrastrukturu.
    assert "l1:ge-0/0/4" in ids


def test_nesparovane_se_nefiltruji():
    baseline = _old()
    baseline.scopes.append(_scope("svc:ZMIZELA:Internet", "ZMIZELA", "Internet", "ge-0/0/6.0"))

    result = evaluate_snapshots(
        _new(), baseline=baseline, service_types=["IPVPN"], now=NOW
    )

    assert any("Internet" in str(item) for item in result.unmatched["baseline"])


def test_nesparovany_subject_mimo_profil_zustava_v_nesparovano():
    """NESPAROVANY subject scope, ktery filtr vyradi z check smycky, musi
    porad zustat v NESPAROVANO - i tam se pocita do skipped_total."""
    result = evaluate_snapshots(
        _new(extra_scope=True), baseline=_old(), service_types=["IPVPN"], now=NOW
    )

    assert result.unmatched["subject"][0]["scope_id"] == "svc:NOVA:E-LAN"
    assert result.summary["unmatched_subject"] == 1
    assert all(s.scope_id != "svc:NOVA:E-LAN" for s in result.scopes)
    assert result.filtered["scopes_total"] == result.filtered["scopes_shown"] + 1


def test_bez_filtru_zadny_marker():
    result = evaluate_snapshots(_old(), now=NOW)

    assert result.filtered is None


def test_profile_name_se_propisuje_i_bez_service_types_filtru():
    """Jmeno profilu ma byt v result.profile vzdy, kdyz je predano - i kdyz
    profil nema service_types, takze zadny filtr sluzeb nebezi."""
    result = evaluate_snapshots(_old(), profile_name="core-only.yml", now=NOW)

    assert result.profile == "core-only.yml"
    assert result.filtered is None


def test_bez_profile_name_je_profile_none():
    result = evaluate_snapshots(_old(), now=NOW)

    assert result.profile is None


def test_prazdny_seznam_service_types_odfiltruje_vsechny_sluzby_ale_marker_zustava():
    """service_types=[] predane primo (ne pres CLI) musi vyfiltrovat vsechny
    sluzbove scopy a stale vyprodukovat `filtered` znacku - dokumentuje to
    API chovani, CLI samo prazdny seznam odmita drive."""
    subject = _multi_service_snapshot()

    result = evaluate_snapshots(subject, service_types=[], now=NOW)

    service_scopes = [scope for scope in result.scopes if scope.scope_id.startswith("svc:")]
    assert service_scopes == []
    assert result.filtered is not None
    assert result.filtered["service_types"] == []


def test_unassigned_bgp_peers_are_reported():
    peers = {"10.9.9.9": {"state": "Established", "routing_instance": None}}
    subject = _new()
    subject.facts["bgp"] = peers

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["bgp_peers"][0]["peer"] == "10.9.9.9"


def test_inactive_peer_is_assigned_not_unassigned():
    """Ziva session deaktivovaneho peera patri sve sluzbe, ne do NEZARAZENO.

    Deaktivovany peer je porad peer teto sluzby - kdyz pro nej presto prijde
    session, je to nalez o teto sluzbe. Spadnout do NEZARAZENO by ten vztah
    zahodilo.

    Zabiji mutanta: `assigned` postavene jen z `bgp_neighbors`.
    """
    scope = Scope(
        id="s1",
        kind="service",
        key=ScopeKey(None, "Internet"),
        selectors=Selectors(bgp_neighbors_inactive=["198.11.13.9"]),
    )
    snapshot = Snapshot(
        device=DeviceMeta(address="172.20.20.4"),
        capture=CaptureMeta(
            started_at=NOW,
            finished_at=NOW,
            phase="pre-migration",
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts={"bgp": {"198.11.13.9": {"state": "Established"}}},
        probes={},
        scopes=[scope],
        inventory=[],
    )

    assert _unassigned_bgp_peers(snapshot, [scope]) == []


def test_inactive_peer_bfd_session_stays_visible_in_unassigned():
    """Ziva BFD session deaktivovaneho peera musi zustat v NEZARAZENO.

    Zamerna asymetrie proti BGP: u BGP dostane deaktivovany peer se zivou
    session skutecny nalez u sve sluzby, takze do NEZARAZENO nepatri. U BFD
    zadny takovy check neni - checks/bfd.py iteruje zamer bfd_peers, ktery
    je pro deaktivovaneho peera prazdny. Kdyby se session zaroven povazovala
    za zarazenou, nevykreslila by se nikde.

    Zabiji mutanta: `assigned` v `_unassigned_bfd_sessions` postavene jako
    sjednoceni `bgp_neighbors` a `bgp_neighbors_inactive` (tj. symetricky
    s `_unassigned_bgp_peers`).
    """
    scope = Scope(
        id="s1",
        kind="service",
        key=ScopeKey(None, "Internet"),
        selectors=Selectors(bgp_neighbors_inactive=["198.11.13.9"]),
    )
    snapshot = Snapshot(
        device=DeviceMeta(address="172.20.20.4"),
        capture=CaptureMeta(
            started_at=NOW,
            finished_at=NOW,
            phase="pre-migration",
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts={"bfd": {"198.11.13.9": {"state": "Up", "interface": "et-0/0/9.0"}}},
        probes={},
        scopes=[scope],
        inventory=[],
    )

    assert _unassigned_bfd_sessions(snapshot, [scope]) == [
        {
            "peer": "198.11.13.9",
            "interface": "et-0/0/9.0",
            "state": "Up",
            "snapshot": "subject",
        }
    ]


def test_core_transit_bfd_session_claimed_by_interface_is_not_unassigned():
    """BFD session Core transit sluzby, ktera nema BGP peery, se do NEZARAZENO
    nesmi dostat podruhe - Scope.select ji uz zarazuje podle rozhrani
    (models/scope.py), takze `_unassigned_bfd_sessions` musi pouzit stejne
    pravidlo, jinak by se v reportu objevila dvakrat."""
    scope = Scope(
        id="svc:core-transit:Core",
        kind="service",
        key=ScopeKey(None, "Core", "transit"),
        selectors=Selectors(interfaces=["ge-0/0/0.0"], physical_interfaces=["ge-0/0/0"]),
    )
    snapshot = Snapshot(
        device=DeviceMeta(address="172.20.20.4"),
        capture=CaptureMeta(
            started_at=NOW,
            finished_at=NOW,
            phase="pre-migration",
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts={"bfd": {"10.0.0.1": {"state": "Up", "interface": "ge-0/0/0.0"}}},
        probes={},
        scopes=[scope],
        inventory=[],
    )

    assert _unassigned_bfd_sessions(snapshot, [scope]) == []


def test_core_loopback_ibgp_bfd_session_stays_visible_in_unassigned():
    """iBGP BFD na lo0.0 je vedome odlozene rozhodnuti (2026-08-26, nalez
    finalniho review): `bfd_session_state` uz na Core-loopback scope nebezi
    (checks/bfd.py) a `bfd_transit_state` mu taky nepatri (jen transit) -
    zadny check session interniho peera nemeri. Kdyby ji `assigned` presto
    pohltilo (protoze peer je v Core-loopback bgp_neighbors), zmizela by
    z reportu uplne. Musi tedy zustat v NEZARAZENO stejne jako pred touto
    vlnou."""
    scope = Scope(
        id="svc:core-loopback:Core",
        kind="service",
        key=ScopeKey(None, "Core", "loopback"),
        selectors=Selectors(bgp_neighbors=["150.0.0.1"]),
    )
    snapshot = Snapshot(
        device=DeviceMeta(address="172.20.20.4"),
        capture=CaptureMeta(
            started_at=NOW,
            finished_at=NOW,
            phase="pre-migration",
            collectors={"interfaces": {"status": "ok"}},
        ),
        facts={"bfd": {"150.0.0.1": {"state": "Up", "interface": "lo0.0"}}},
        probes={},
        scopes=[scope],
        inventory=[],
    )

    assert _unassigned_bfd_sessions(snapshot, [scope]) == [
        {
            "peer": "150.0.0.1",
            "interface": "lo0.0",
            "state": "Up",
            "snapshot": "subject",
        }
    ]


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
            "protocol": "static",
            "snapshot": "subject",
        }
    ]


def test_nezarazeny_aggregate_nese_protokol():
    """Agregat bez odpovidajici sluzby (VRF bez sluzeb, box bez Core lo0.0)
    spadne do NEZARAZENO stejne jako statika - a nese protocol, aby report
    poznal, ze jde o agregat, ne o klasickou statiku."""
    subject = _new()
    subject.facts["routes"] = {
        "inet6.0": {
            "2001:abcd::/32": {
                "next_hop": [],
                "via": [],
                "active": True,
                "protocol": "aggregate",
            }
        }
    }

    result = api.evaluate(subject, baseline=_old(), now=NOW)

    assert result.unassigned["static_routes"] == [
        {
            "rib": "inet6.0",
            "prefix": "2001:abcd::/32",
            "next_hop": [],
            "via": [],
            "protocol": "aggregate",
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
            "protocol": "static",
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
    u vyberu `bfd` v `Scope.select()` zakazuji. Peer je
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
        "physical_interfaces": [],
        "ipv4": [],
        "ipv6": [],
        "virtual_gw_v4": [],
        "virtual_gw_v6": [],
        "l2_interfaces": [],
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


def test_service_deactivated_on_both_sides_is_warn():
    """Deaktivovano na obou stranach = WARN, ne PASS a ne SKIP.

    Konfigurace by deaktivovane prvky bezne obsahovat nemela, takze
    "nezmenilo se to" neni duvod mlcet - je to duvod hlasit potise
    (rozhodnuti uzivatele z 2026-08-04).

    Zabiji mutanta: navrat Outcome.OK v teto vetvi deactivation.py. S nim by
    Status.worst z jedine ne-SKIP hodnoty dal PASS a sluzba by ve strucnem
    vypisu zmizela mezi zdravymi.
    """
    result = api.evaluate(
        _deactivated(_new()), baseline=_deactivated(_old()), now=NOW
    )

    assert result.scopes
    assert result.scopes[0].status is Status.WARN


def test_service_deactivated_only_in_subject_is_fail():
    """Bezela na starem zarizeni, na novem je deaktivovana - migrace nedokoncena.

    Zabiji mutanta: zamena BROKEN za OK v teto vetvi deactivation.py. Bez
    tohoto testu by "deaktivovano az ted" bylo k nerozeznani od
    "deaktivovano i drive".
    """
    result = api.evaluate(_deactivated(_new()), baseline=_old(), now=NOW)

    assert result.scopes
    assert result.scopes[0].status is Status.FAIL


def test_service_reactivated_after_migration_is_recovered():
    """V baseline deaktivovana, ted nahozena - zlepseni je RECOVERED, ne
    varovani (R-2), oprava opravneho kola 2026-09-03.
    """
    result = api.evaluate(_new(), baseline=_deactivated(_old()), now=NOW)

    assert result.scopes
    assert result.scopes[0].status is Status.RECV


def test_route_without_active_key_does_not_mask_healthy_siblings(synthetic_snapshot):
    """SKIP na jedne route nesmi stahnout cely scope a zakryt PASSy sourozencu.

    Roadmapa vlny 5 vedla tenhle stav jako vadu (bod 4), protoze
    _STATUS_RANK ma SKIP nad PASS. Merenim se ukazalo, ze vada neexistuje:
    engine.py:145 SKIPy z hlasovani Status.worst() vyfiltruje driv, nez se
    hlasuje. Chybel jen test, ktery to tvrdi.

    Zabiji mutanta: vypusteni `if result.status is not Status.SKIP` z
    engine.py:145. Zmereno (vlna 9, fix round 1): tenhle test ho zabije
    primo (asserty nize) a `test_healthy_scope_without_baseline_is_pass_not_skip`
    ho zabiji taky, pres jiny scenar - compare-only check bez baseline.
    Sluzba deaktivovana na obou stranach uz od vlny 9 nekryje: DEGRADED
    (WARN) vyhrava v `Status.worst()` nad SKIP i bez filtru, takze
    `test_service_deactivated_on_both_sides_is_warn` na tenhle mutant
    nezavisi.
    """
    subject = synthetic_snapshot(DEVICE_4, "172.20.20.4", "post-migration")
    del subject.facts["routes"]["inet.0"]["198.62.1.0/29"]["active"]

    result = api.evaluate(subject, now=NOW)

    scope = next(
        s for s in result.scopes if s.scope_id == "svc:INTERNET-CPE13-NNI:Internet"
    )
    statuses = [c.status for c in scope.checks if c.id == "static_route_status"]

    # Bez tehle dvojice by test prosel i tehdy, kdyby se SKIP vubec nevyrobil
    # nebo kdyby scope nemel zadneho zdraveho sourozence - tedy kdyby merit
    # nebylo co.
    assert statuses.count(Status.SKIP) == 1
    assert Status.PASS in statuses

    assert scope.status is Status.PASS


def _linked_snapshot():
    """Snapshot se dvojici irb.15 (IPVPN) + ae0.15 (E-LAN) v jedne instanci."""
    l3 = Scope(
        id="svc:L3VPN-CPE14-UNI:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN-CPE14-UNI", "IPVPN", None),
        selectors=Selectors(
            interfaces=["irb.15"], routing_instances=["L3VPN-CPE14-UNI"]
        ),
    )
    l2 = Scope(
        id="svc:EVPN-VLAN-AWARE-CPE14:E-LAN",
        kind="service",
        key=ScopeKey("EVPN-VLAN-AWARE-CPE14", "E-LAN", "vlan-aware"),
        selectors=Selectors(
            interfaces=["ae0.15"],
            routing_instances=["EVPN-VLAN-AWARE-POP1"],
            vlans=["15"],
        ),
    )
    # dalsi nesouvisejici scope, aby bylo videt razeni
    other = Scope(
        id="svc:OTHER:Internet",
        kind="service",
        key=ScopeKey("OTHER", "Internet", None),
        selectors=Selectors(interfaces=["ge-0/0/1.0"]),
    )
    facts = {
        "interfaces": {
            "irb.15": {"admin_status": "up", "oper_status": "up"},
            "ae0.15": {
                "admin_status": "up",
                "oper_status": "up",
                "input_pps": 10,
                "output_pps": 10,
            },
            "ge-0/0/1.0": {
                "admin_status": "up",
                "oper_status": "up",
                "input_pps": 10,
                "output_pps": 10,
            },
        },
        "evpn_instance": {
            "EVPN-VLAN-AWARE-POP1": {
                "local_interfaces": {
                    "total": 1,
                    "up": 1,
                    "entries": [{"name": "ae0.15", "status": "Up"}],
                },
                "irb_interfaces": {
                    "total": 1,
                    "up": 1,
                    "entries": [
                        {
                            "name": "irb.15",
                            "status": "Up",
                            "l3_context": "L3VPN-CPE14-UNI",
                        }
                    ],
                },
                "neighbors": {"total": 1, "addresses": ["10.0.0.1"]},
                "esis": {},
            }
        },
    }
    return Snapshot(
        device=DeviceMeta(address="172.20.20.4"),
        capture=CaptureMeta(started_at=NOW, finished_at=NOW, phase="pre-migration"),
        facts=facts,
        scopes=[l3, other, l2],
        inventory=[],
    )


def test_linked_scopes_carry_link_payload():
    result = evaluate_snapshots(_linked_snapshot())
    by_id = {scope.scope_id: scope for scope in result.scopes}
    l3 = by_id["svc:L3VPN-CPE14-UNI:IPVPN"]
    l2 = by_id["svc:EVPN-VLAN-AWARE-CPE14:E-LAN"]
    assert l3.link == {
        "role": "l3",
        "peers": [
            {
                "scope_id": l2.scope_id,
                "interface": "ae0.15",
                "instance": "EVPN-VLAN-AWARE-POP1",
            }
        ],
    }
    assert l2.link == {
        "role": "l2",
        "peer_scope_id": l3.scope_id,
        "peer_interface": "irb.15",
        "peer_instance": "L3VPN-CPE14-UNI",
    }
    assert by_id["svc:OTHER:Internet"].link is None


def _fanout_snapshot():
    """_linked_snapshot + druhy L2 scope v teze bridge domain (lab BD-4094)."""
    snapshot = _linked_snapshot()
    second = Scope(
        id="svc:MGMT-VLAN:E-LAN",
        kind="service",
        key=ScopeKey("MGMT-VLAN", "E-LAN", "vlan-aware"),
        selectors=Selectors(
            interfaces=["ae1.15"],
            routing_instances=["EVPN-VLAN-AWARE-POP1"],
            vlans=["15"],
        ),
    )
    snapshot.scopes.append(second)
    snapshot.facts["interfaces"]["ae1.15"] = {
        "admin_status": "up",
        "oper_status": "up",
        "input_pps": 10,
        "output_pps": 10,
    }
    instance = snapshot.facts["evpn_instance"]["EVPN-VLAN-AWARE-POP1"]
    instance["local_interfaces"]["total"] = 2
    instance["local_interfaces"]["up"] = 2
    instance["local_interfaces"]["entries"].append(
        {"name": "ae1.15", "status": "Up"}
    )
    return snapshot


def test_l3_scope_links_every_l2_scope_of_its_bridge_domain():
    result = evaluate_snapshots(_fanout_snapshot())
    by_id = {scope.scope_id: scope for scope in result.scopes}
    l3 = by_id["svc:L3VPN-CPE14-UNI:IPVPN"]

    assert l3.link == {
        "role": "l3",
        "peers": [
            {
                "scope_id": "svc:EVPN-VLAN-AWARE-CPE14:E-LAN",
                "interface": "ae0.15",
                "instance": "EVPN-VLAN-AWARE-POP1",
            },
            {
                "scope_id": "svc:MGMT-VLAN:E-LAN",
                "interface": "ae1.15",
                "instance": "EVPN-VLAN-AWARE-POP1",
            },
        ],
    }
    for l2_id in ("svc:EVPN-VLAN-AWARE-CPE14:E-LAN", "svc:MGMT-VLAN:E-LAN"):
        assert by_id[l2_id].link == {
            "role": "l2",
            "peer_scope_id": l3.scope_id,
            "peer_interface": "irb.15",
            "peer_instance": "L3VPN-CPE14-UNI",
        }


def test_all_linked_l2_blocks_follow_their_l3_scope():
    result = evaluate_snapshots(_fanout_snapshot())
    ids = [scope.scope_id for scope in result.scopes]
    l3_index = ids.index("svc:L3VPN-CPE14-UNI:IPVPN")
    assert ids[l3_index + 1 : l3_index + 3] == [
        "svc:EVPN-VLAN-AWARE-CPE14:E-LAN",
        "svc:MGMT-VLAN:E-LAN",
    ]


def test_linked_l2_scope_follows_its_l3_scope():
    result = evaluate_snapshots(_linked_snapshot())
    ids = [scope.scope_id for scope in result.scopes]
    l3_index = ids.index("svc:L3VPN-CPE14-UNI:IPVPN")
    assert ids[l3_index + 1] == "svc:EVPN-VLAN-AWARE-CPE14:E-LAN"


def test_link_serialized_only_when_present():
    result = evaluate_snapshots(_linked_snapshot())
    payloads = {scope["scope_id"]: scope for scope in result.to_dict()["scopes"]}
    assert payloads["svc:L3VPN-CPE14-UNI:IPVPN"]["link"]["role"] == "l3"
    assert "link" not in payloads["svc:OTHER:Internet"]


def test_master_context_link_shows_inet0():
    snapshot = _linked_snapshot()
    # prepni kontext na master a odeber RI z L3 scopu
    facts_irb = snapshot.facts["evpn_instance"]["EVPN-VLAN-AWARE-POP1"][
        "irb_interfaces"
    ]["entries"][0]
    facts_irb["l3_context"] = "master"
    for scope in snapshot.scopes:
        if scope.id == "svc:L3VPN-CPE14-UNI:IPVPN":
            scope.selectors.routing_instances = []
            scope.key = ScopeKey("L3VPN-CPE14-UNI", "Internet", None)
    result = evaluate_snapshots(snapshot)
    by_id = {scope.scope_id: scope for scope in result.scopes}
    assert by_id["svc:EVPN-VLAN-AWARE-CPE14:E-LAN"].link["peer_instance"] == "inet.0"


def _snapshot_with(scopes, *, address="172.20.20.5", phase="pre-migration"):
    return Snapshot(
        device=DeviceMeta(address=address),
        capture=CaptureMeta(started_at=NOW, finished_at=NOW, phase=phase),
        facts={},
        scopes=scopes,
    )


def _l1(port, members=()):
    return Scope(id=f"l1:{port}", kind="layer1",
                 key=ScopeKey(f"L1;{port}", "Layer1", "physical-port"),
                 selectors=Selectors(interfaces=[port],
                                     lag_members=list(members)))


def _svc(name, unit, parent, service_type="Internet"):
    return Scope(id=f"svc:{name}:{service_type}", kind="service",
                 key=ScopeKey(name, service_type),
                 selectors=Selectors(interfaces=[unit],
                                     physical_interfaces=[parent]))


def test_bloky_se_skupinuji_po_portech_prirozene_razene():
    scopes = [
        _svc("S-B", "ge-0/0/10.0", "ge-0/0/10"),
        _svc("S-A", "ge-0/0/2.0", "ge-0/0/2"),
        _svc("S-LO", "lo0.0", "lo0"),  # bez L1 scopu
        _l1("ge-0/0/10"),
        _l1("ge-0/0/2"),
    ]
    result = evaluate_snapshots(_snapshot_with(scopes))
    ids = [r.scope_id for r in result.scopes]
    assert ids == ["l1:ge-0/0/2", "svc:S-A:Internet",
                   "l1:ge-0/0/10", "svc:S-B:Internet",
                   "svc:S-LO:Internet"]


def _result(scope_id, service_type, interfaces=(), parents=(), link=None):
    """Hotovy ScopeResult pro unit test razeni - engine se neobchazi,
    _group_by_layer1 je cista funkce nad vysledky."""
    return ScopeResult(
        scope_id=scope_id, key={"service_type": service_type},
        status=Status.PASS, match=None, checks=[],
        identity={"interfaces": list(interfaces),
                  "physical_interfaces": list(parents)},
        link=link,
    )


def test_l3_l2_par_drzi_pohromade_pod_portem_l2_rozhrani():
    # L3 blok (irb.14, bez fyzickeho rodice) dedi rodice sve L2 casti:
    # par stoji pod l1:ae0, L3 pred L2 (vstup uz prosel _reorder_linked).
    l3 = _result("svc:INET:Internet", "Internet", interfaces=["irb.14"],
                 link={"role": "l3", "peers": [{"scope_id": "svc:ELAN:E-LAN",
                       "interface": "ae0.14", "instance": "POP1"}]})
    l2 = _result("svc:ELAN:E-LAN", "E-LAN", interfaces=["ae0.14"],
                 parents=["ae0"],
                 link={"role": "l2", "peer_scope_id": "svc:INET:Internet",
                       "peer_interface": "irb.14", "peer_instance": "inet.0"})
    l1 = _result("l1:ae0", "Layer1", interfaces=["ae0"])
    ordered = _group_by_layer1([l3, l2, l1])
    assert [r.scope_id for r in ordered] == [
        "l1:ae0", "svc:INET:Internet", "svc:ELAN:E-LAN"]


def test_l1_baseline_z_deti():
    # baseline: sluzba INET na ge-0/0/5.0 (rodic ge-0/0/5) + l1:ge-0/0/5
    # subject:  sluzba INET na ae0.14 (rodic ae0) + l1:ae0
    # sluzby sdili description "INET" -> match pres description,
    # L1 par se pak odvodi z deti. Oba snapshoty stejnou tovarnou.
    baseline_snapshot = _snapshot_with(
        [_svc("INET", "ge-0/0/5.0", "ge-0/0/5"), _l1("ge-0/0/5")])
    subject_snapshot = _snapshot_with(
        [_svc("INET", "ae0.14", "ae0"), _l1("ae0")])
    result = evaluate_snapshots(subject_snapshot, baseline_snapshot)
    l1 = next(r for r in result.scopes if r.scope_id == "l1:ae0")
    assert l1.match is not None and l1.match.method == "layer1-children"
    assert l1.match.baseline_interfaces == ["ge-0/0/5"]
    assert not any(item["scope_id"].startswith("l1:")
                   for side in ("baseline", "subject")
                   for item in result.unmatched[side])


def test_identity_nese_physical_interfaces():
    result = evaluate_snapshots(_snapshot_with([_svc("S", "ae0.14", "ae0"), _l1("ae0")]))
    svc = next(r for r in result.scopes if r.scope_id.startswith("svc:"))
    assert svc.identity["physical_interfaces"] == ["ae0"]


STEP = {
    "old": {"node": "MX1-POP1", "port": "ge-0/0/4"},
    "new": {"node": "PTX1-POP1", "port": "ae0"},
}


def test_step_filters_unmatched_subject_into_excluded_services():
    """Sluzby cizich vln na LAGu nejdou pres checky, skonci v excluded."""
    baseline = _snapshot(
        "172.20.20.4",
        "ge-0/0/4.0",
        [_scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ge-0/0/4.0")],
    )
    subject = _snapshot(
        "172.20.20.5",
        "ae0.15",
        [
            _scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ae0.15"),
            _scope("svc:VLNA2:Internet", "VLNA2", "Internet", "ae0.16"),
        ],
        phase="post-migration",
    )

    result = evaluate_snapshots(subject, baseline, now=NOW, step=STEP)

    shown_ids = {scope.scope_id for scope in result.scopes}
    assert "svc:VLNA1:Internet" in shown_ids
    assert "svc:VLNA2:Internet" not in shown_ids
    assert result.step == STEP
    assert result.excluded_services == [
        {
            "scope_id": "svc:VLNA2:Internet",
            "description": "VLNA2",
            "service_type": "Internet",
            "reason": "nova sluzba, chybi baseline",
        }
    ]


def test_step_keeps_unmatched_baseline_visible():
    """Chybejici sluzba stareho portu je hlavni nalez - filtr ji nesmi vzit."""
    baseline = _snapshot(
        "172.20.20.4",
        "ge-0/0/4.0",
        [
            _scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ge-0/0/4.0"),
            _scope("svc:ZTRACENA:Internet", "ZTRACENA", "Internet", "ge-0/0/4.1"),
        ],
    )
    subject = _snapshot(
        "172.20.20.5",
        "ae0.15",
        [_scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ae0.15")],
        phase="post-migration",
    )

    result = evaluate_snapshots(subject, baseline, now=NOW, step=STEP)

    assert [e["description"] for e in result.unmatched["baseline"]] == ["ZTRACENA"]


def test_step_none_changes_nothing():
    """Bez step je vysledek bit-po-bitu dnesni (klice v JSON chybi)."""
    baseline = _snapshot(
        "172.20.20.4", "ge-0/0/4.0",
        [_scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ge-0/0/4.0")],
    )
    subject = _snapshot(
        "172.20.20.5", "ae0.15",
        [
            _scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ae0.15"),
            _scope("svc:VLNA2:Internet", "VLNA2", "Internet", "ae0.16"),
        ],
        phase="post-migration",
    )

    result = evaluate_snapshots(subject, baseline, now=NOW)

    shown_ids = {scope.scope_id for scope in result.scopes}
    assert "svc:VLNA2:Internet" in shown_ids  # dnesni NESPAROVANO
    payload = result.to_dict()
    assert "step" not in payload
    assert "excluded_services" not in payload


def test_step_pulls_linked_partner_of_matched_scope():
    """Napul sparovany L2/L3 par: nesparovany partner se pritahne."""
    # Subject = _linked_snapshot() (irb.15 IPVPN + ae0.15 E-LAN v jedne
    # instanci, helper v tomto souboru). Baseline nese JEN L3 polovinu
    # (description "L3VPN-CPE14-UNI") -> L2 "EVPN-VLAN-AWARE-CPE14" se
    # nesparuje, ale link na sparovany irb.15 ho musi udrzet v reportu.
    subject = _linked_snapshot()
    baseline = _snapshot(
        "172.20.20.4",
        "ge-0/0/4.0",
        [_scope("svc:L3VPN-CPE14-UNI:IPVPN", "L3VPN-CPE14-UNI", "IPVPN", "ge-0/0/4.0")],
    )

    result = evaluate_snapshots(subject, baseline, now=NOW, step=STEP)

    shown_ids = {scope.scope_id for scope in result.scopes}
    assert "svc:EVPN-VLAN-AWARE-CPE14:E-LAN" in shown_ids
    assert "svc:OTHER:Internet" not in shown_ids
    excluded_ids = {e["scope_id"] for e in result.excluded_services}
    assert excluded_ids == {"svc:OTHER:Internet"}


def test_step_without_baseline_has_no_excluded_services():
    """Krok bez pre snimku nema baseline, filtr pres baseline neprobehl."""
    subject = _snapshot(
        "172.20.20.5",
        "ae0.15",
        [_scope("svc:VLNA1:Internet", "VLNA1", "Internet", "ae0.15")],
        phase="post-migration",
    )

    result = evaluate_snapshots(subject, None, now=NOW, step=STEP)

    assert result.step == STEP
    assert result.excluded_services is None
    payload = result.to_dict()
    assert "step" in payload
    assert "excluded_services" not in payload


def test_summary_counts_unchanged_rows(monkeypatch):
    class Unchanged(check_base.Check):
        id = "unchanged_dummy"
        title = "t"
        label = "Dummy"
        mode = check_base.Mode.BOTH

        def run(self, ctx):
            return [Finding(Outcome.UNCHANGED, "x", value="Down", baseline_value="Down")]

    monkeypatch.setattr("migration_validator.engine.all_checks", lambda: [Unchanged()])
    result = api.evaluate(_new(), baseline=_old(), now=NOW)
    assert result.summary["pass_unchanged"] == 1
    assert result.summary["pass"] == 1


def test_engine_passes_baseline_collectors_to_checks(monkeypatch):
    seen = {}

    class Probe(check_base.Check):
        id = "probe_dummy"
        title = "t"
        label = "Probe"
        mode = check_base.Mode.BOTH

        def run(self, ctx):
            seen["collectors"] = dict(ctx.baseline_collectors)
            seen["measured_arp"] = ctx.baseline_measured("arp")
            seen["measured_nd"] = ctx.baseline_measured("nd")
            return [Finding(Outcome.OK, "x", value="v", baseline_value="v")]

    monkeypatch.setattr("migration_validator.engine.all_checks", lambda: [Probe()])
    baseline = _old()
    baseline.capture.collectors["arp"] = {"status": "error", "message": "RpcError: timeout"}
    baseline.capture.collectors["nd"] = {"status": "ok"}
    api.evaluate(_new(), baseline=baseline, now=NOW)
    assert seen["collectors"]["arp"] == {"status": "error", "message": "RpcError: timeout"}
    assert seen["collectors"]["nd"] == {"status": "ok"}
    # selhany collector v baseline - neni to dukaz zmereni
    assert seen["measured_arp"] is False
    # zaznam se statusem ok - pozitivni dukaz zmereni
    assert seen["measured_nd"] is True


def test_aligned_baseline_renames_protocol_areas_and_interface_fields(monkeypatch):
    old = _scope("svc:CORE:Core", "CORE", "Core", "ge-0/0/1.0")
    new = _scope("svc:CORE:Core", "CORE", "Core", "et-0/0/1.0")
    baseline = _snapshot("172.20.20.4", "ge-0/0/1.0", [old])
    baseline.facts.update({
        "isis_adjacency": {"ge-0/0/1.0": {"state": "Up"}},
        "isis_interface": {"ge-0/0/1.0": {"levels": {}}},
        "ldp_neighbor": {"ge-0/0/1.0": {"uptime_seconds": 5}},
        "pim_neighbor": {"ge-0/0/1.0": {"uptime_seconds": 5}},
        "mpls_interface": {"ge-0/0/1.0": {"state": "Up"}},
        "igmp_group": {"ge-0/0/1.0": [{"source": "10.0.0.1", "group": "232.1.1.1"}]},
        "bfd": {"10.1.0.5": {"state": "Up", "interface": "ge-0/0/1.0"}},
        "evpn_esi": {"00:11": {"interface": "ge-0/0/1.0", "status": "Resolved"}},
    })
    baseline.capture.collectors.update(
        {name: {"status": "ok"} for name in ("isis_adjacency", "isis_interface",
         "ldp_neighbor", "pim_neighbor", "mpls_interface", "igmp_group", "bfd", "evpn_esi")}
    )

    # Scope.select() filtruje arp/nd/bfd/evpn_esi podle shody rozhrani, takze
    # zaznam bez klice "interface" by pres nej nikdy neprosel - domonkeypatchujeme
    # select() tak, aby takovy zaznam vratil, a overime, ze rename blok v
    # _aligned_baseline_data ho nechava beze zmeny (interface se nedofabrikuje).
    original_select = old.select

    def _select_with_bare_arp_entry(facts, probes=None):
        data = original_select(facts, probes)
        data["arp"] = data["arp"] + [{"ip": "198.11.13.9", "no_interface_here": True}]
        return data

    monkeypatch.setattr(old, "select", _select_with_bare_arp_entry)

    data = _aligned_baseline_data(old, new, baseline)

    for area in ("isis_adjacency", "isis_interface", "ldp_neighbor",
                 "pim_neighbor", "mpls_interface", "igmp_group"):
        assert list(data[area]) == ["et-0/0/1.0"], area
    assert data["arp"][0]["interface"] == "et-0/0/1.0"
    assert data["nd"][0]["interface"] == "et-0/0/1.0"
    assert data["evpn_esi"]["00:11"]["interface"] == "et-0/0/1.0"
    # entry bez klice "interface" zustane beze zmeny, "interface" se nedofabrikuje
    assert data["arp"][1] == {"ip": "198.11.13.9", "no_interface_here": True}
    assert "interface" not in data["arp"][1]
