"""Orchestrace vyhodnoceni snapshotu.

Engine nesaha na sit. Vsechna data pochazeji ze snapshotu.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from migration_validator.checks import all as _all_checks  # noqa: F401  (registrace)
from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.registry import all_checks
from migration_validator.config import CheckConfig, default_config
from migration_validator.models.result import (
    MatchInfo,
    RunResult,
    ScopeResult,
    Status,
    count_statuses,
)
from migration_validator.models.scope import LAYER1_SERVICE_TYPE, Scope, device_scope
from migration_validator.models.snapshot import Snapshot
from migration_validator.scoping.linker import ScopeLink, link_scopes
from migration_validator.scoping.mapping import Mapping, empty_mapping
from migration_validator.scoping.matcher import MatchedPair, match_scopes


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot_meta(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "address": snapshot.device.address,
        "phase": snapshot.capture.phase,
        "captured_at": snapshot.capture.finished_at or snapshot.capture.started_at,
    }


def _scopes_of(snapshot: Snapshot) -> list[Scope]:
    return snapshot.scopes if snapshot.scopes else [device_scope()]


def _unmatched_entry(scope: Scope, reason: str) -> dict[str, Any]:
    return {
        "scope_id": scope.id,
        "description": scope.key.description if scope.key else None,
        "service_type": scope.key.service_type if scope.key else None,
        "reason": reason,
    }


def _aligned_baseline_data(
    baseline_scope: Scope, scope: Scope, baseline: Snapshot
) -> dict[str, Any]:
    """Vybere baseline data a preslovnuje klice interfaces na jmena subjektu.

    Baseline scope selektuje podle sveho vlastniho jmena rozhrani (napr.
    ge-0/0/2.113 na MX). Checky (Task 10, napr. interface_traffic) ale
    hledaji baseline hodnotu pod stejnym klicem, jaky ma SUBJECT rozhrani
    (et-0/0/8.113 na EVO) - to je bezny dusledek migrace na jiny hardware.
    Bez preslovnovani by srovnani tise spadlo do stavoveho rezimu (baseline
    nenalezena) a ztratil by se signal o poklesu provozu presne u sluzeb,
    kde se rozhrani prejmenovalo. Presmerovani je poziciove: kazda sluzba
    ma v selektoru prave jedno rozhrani (scoping/builder.py), takze zip
    dvou jednoprvkovych seznamu je jednoznacny.

    Preslovnuji se obe skupiny selektoru. Scope nese vedle logickeho
    rozhrani i to fyzicke a migrace prejmenovava obe (ge-0/0/2 -> et-0/0/8);
    kdyz se preslovnovalo jen logicke, fyzicke svou baseline nenaslo a kazda
    migrovana sluzba vypsala dva trvale radky 'bez baseline'.

    Stejny osud potka evpn_mac: instance nese vedle poctu MAC na VLAN i
    per-interface rozpad (Task 2/3) a ten je klicovany jmenem rozhrani,
    presne jako fakta v oblasti interfaces. Bez preklicovani by check z
    Tasku 3 hledal par pod jmenem subjektu (et-0/0/8.313), ale baseline by
    ho porad mel ulozeny pod starym jmenem (ge-0/0/2.313) - par by se
    nikdy nenasel.

    Oblast optics je klicovana fyzickym portem, ktery muze byt i clen LAGu
    (lag_members) - proto se do rename pozicne pricitaji i cleny, ne jen
    physical_interfaces. Bez toho by port v LAGu po migraci na jiny hardware
    nikdy nenasel svou baseline optiku.
    """
    data = baseline_scope.select(baseline.facts, baseline.probes)
    selectors = baseline_scope.selectors
    rename = dict(zip(selectors.interfaces, scope.selectors.interfaces))
    rename.update(
        zip(selectors.physical_interfaces, scope.selectors.physical_interfaces)
    )
    rename.update(zip(selectors.lag_members, scope.selectors.lag_members))
    if rename:
        data["interfaces"] = {
            rename.get(name, name): iface_data
            for name, iface_data in data.get("interfaces", {}).items()
        }
    if rename and data.get("evpn_mac"):
        # Stejny pozicni princip jako u oblasti interfaces: per-interface
        # MAC pocty se paruji pres dvojici stary <-> novy port, ne pres
        # jmeno, ktere se migraci zmenilo.
        data["evpn_mac"] = {
            instance: {
                **instance_data,
                "interfaces": {
                    rename.get(key, key): entry
                    for key, entry in instance_data.get("interfaces", {}).items()
                },
            }
            for instance, instance_data in data["evpn_mac"].items()
        }
    if rename and data.get("optics"):
        data["optics"] = {
            rename.get(name, name): optics_data
            for name, optics_data in data["optics"].items()
        }
    return data


def _identity(scope: Scope) -> dict[str, Any]:
    """Vse, co report o sluzbe vypisuje - jinak by to zustalo ve scope.

    Renderer nema pristup ke scopum, jen k vysledku, takze bez tohoto by
    sloupce s adresami, virtual gateway a routing-instanci nemel odkud vzit.
    """
    key = scope.key
    selectors = scope.selectors
    return {
        "description": key.description if key else None,
        "service_type": key.service_type if key else None,
        "service_subtype": key.service_subtype if key else None,
        "routing_instance": (
            selectors.routing_instances[0] if selectors.routing_instances else None
        ),
        # Bez baseline neni MatchInfo, ze ktere renderer bere porty - a rezim
        # `evaluate --snapshot X` bez --baseline je podle AR-10 doporuceny
        # zpusob, jak si prohlednout stav jednoho zarizeni. Bez tohohle pole
        # by v nem byl sloupec s portem prazdny u kazde sluzby.
        "interfaces": list(selectors.interfaces),
        "physical_interfaces": list(selectors.physical_interfaces),
        "ipv4": list(selectors.local_ipv4),
        "ipv6": list(selectors.local_ipv6),
        "virtual_gw_v4": list(selectors.virtual_gw_v4),
        "virtual_gw_v6": list(selectors.virtual_gw_v6),
    }


def _link_payloads(links: list[ScopeLink]) -> dict[str, dict[str, Any]]:
    """Slovnik scope_id -> vazba, jak ji ctou checky a renderer.

    U L2 strany se "master" prepisuje na "inet.0" - hlavicka bloku ma
    ukazovat routing tabulku, ne interni oznaceni z RPC vypisu.
    """
    payloads: dict[str, dict[str, Any]] = {}
    for link in links:
        l3_instance = "inet.0" if link.l3_context == "master" else link.l3_context
        payloads[link.l3_scope_id] = {
            "role": "l3",
            "peer_scope_id": link.l2_scope_id,
            "peer_interface": link.l2_interface,
            "peer_instance": link.l2_instance,
        }
        payloads[link.l2_scope_id] = {
            "role": "l2",
            "peer_scope_id": link.l3_scope_id,
            "peer_interface": link.irb_interface,
            "peer_instance": l3_instance,
        }
    return payloads


def _reorder_linked(results: list[ScopeResult]) -> list[ScopeResult]:
    """L2 blok patri hned za svuj L3 blok - jinak razeni z matcheru."""
    l2_after: dict[str, ScopeResult] = {}
    l2_ids: set[str] = set()
    ids = {result.scope_id for result in results}
    for result in results:
        link = result.link
        if link and link["role"] == "l2" and link["peer_scope_id"] in ids:
            l2_after[link["peer_scope_id"]] = result
            l2_ids.add(result.scope_id)
    ordered: list[ScopeResult] = []
    for result in results:
        if result.scope_id in l2_ids:
            continue
        ordered.append(result)
        partner = l2_after.get(result.scope_id)
        if partner is not None:
            ordered.append(partner)
    return ordered


def _natural_key(name: str) -> list:
    return [int(part) if part.isdigit() else part
            for part in re.split(r"(\d+)", name)]


def _is_l1(result: ScopeResult) -> bool:
    return (result.key or {}).get("service_type") == LAYER1_SERVICE_TYPE


def _parent_port(result: ScopeResult, by_id: dict[str, ScopeResult]) -> str | None:
    """L1 rodic bloku. L3 clen paru dedi rodice sve L2 casti - par ma stat
    pod portem, na kterem sluzba fyzicky bezi (spec kap. 1)."""
    link = result.link
    if link and link["role"] == "l3":
        peer = by_id.get(link["peer_scope_id"])
        if peer is not None:
            result = peer
    parents = (result.identity or {}).get("physical_interfaces") or []
    return parents[0] if parents else None


def _group_by_layer1(results: list[ScopeResult]) -> list[ScopeResult]:
    """Poradi bloku: L1 port -> jeho sluzby, porty prirozene razene,
    sluzby bez L1 rodice na konci. Vstup uz prosel _reorder_linked,
    takze relativni poradi sluzeb (vcetne L3+L2 sousednosti) se drzi."""
    by_id = {result.scope_id: result for result in results}
    l1_by_port = {
        result.identity.get("interfaces", ["?"])[0]: result
        for result in results
        if _is_l1(result)
    }
    services = [result for result in results if not _is_l1(result)]
    ordered: list[ScopeResult] = []
    for port in sorted(l1_by_port, key=_natural_key):
        ordered.append(l1_by_port[port])
        ordered.extend(
            result for result in services
            if _parent_port(result, by_id) == port
        )
    ordered.extend(
        result for result in services
        if _parent_port(result, by_id) not in l1_by_port
    )
    return ordered


def _l1_baseline(
    l1_scope: Scope,
    pairs: list[MatchedPair],
    baseline_l1: list[Scope],
) -> Scope | None:
    """Baseline protejsek L1 portu odvozeny z jeho sparovanych deti.

    Jmeno portu se migraci meni (ge-0/0/5 -> ae0), primy match nejde.
    Remiza hlasu = zadny par - spatny odkaz je horsi nez zadny (stejne
    pravidlo jako matcher)."""
    port = l1_scope.selectors.interfaces[0]
    votes: Counter[str] = Counter()
    for pair in pairs:
        if pair.subject.selectors.physical_interfaces == [port]:
            parents = pair.baseline.selectors.physical_interfaces
            if parents:
                votes[parents[0]] += 1
    if not votes:
        return None
    (top, top_count), *rest = votes.most_common()
    if rest and rest[0][1] == top_count:
        return None
    return next(
        (scope for scope in baseline_l1 if scope.selectors.interfaces == [top]),
        None,
    )


def _run_scope(
    scope: Scope,
    subject: Snapshot,
    baseline_scope: Scope | None,
    baseline: Snapshot | None,
    config: CheckConfig,
    match: MatchInfo | None,
    link: dict[str, Any] | None = None,
) -> ScopeResult:
    subject_data = scope.select(subject.facts, subject.probes)
    baseline_data = (
        _aligned_baseline_data(baseline_scope, scope, baseline)
        if baseline_scope is not None and baseline is not None
        else None
    )
    ctx = CheckContext(
        scope=scope,
        subject=subject_data,
        baseline=baseline_data,
        config=config,
        failed_collectors=subject.capture.failed_collectors(),
        baseline_scope=baseline_scope,
        link=link,
    )

    results = []
    for check in all_checks():
        results.extend(run_check(check, ctx))

    # SKIP vyhrava jen kdyz neni co lepsiho hlasit. Bez teto podminky by
    # zdrava IPVPN sluzba v rezimu bez baseline svitila SKIP jen proto, ze
    # bgp_prefix_counts je compare-only - a operator by prisel o zeleny
    # signal prave u sluzeb s nejvic kontrolami.
    reported = [result.status for result in results if result.status is not Status.SKIP]
    status = Status.worst(reported) if reported else Status.SKIP

    return ScopeResult(
        scope_id=scope.id,
        key=scope.key.to_dict() if scope.key else {},
        status=status,
        match=match,
        checks=results,
        identity=_identity(scope),
        link=link,
    )


def _match_info(pair: MatchedPair) -> MatchInfo:
    return MatchInfo(
        status="matched",
        method=pair.method,
        confidence=pair.confidence,
        baseline_interfaces=list(pair.baseline.selectors.interfaces),
        subject_interfaces=list(pair.subject.selectors.interfaces),
    )


def _unassigned_bgp_peers(subject: Snapshot, scopes: list[Scope]) -> list[dict[str, Any]]:
    # Deaktivovany peer je porad peer sve sluzby. Kdyz pro nej presto prijde
    # session, je to nalez o teto sluzbe - do NEZARAZENO patri jen peer,
    # ktery ke zadne sluzbe nesedi.
    assigned = {
        peer
        for scope in scopes
        for peer in (
            *scope.selectors.bgp_neighbors,
            *scope.selectors.bgp_neighbors_inactive,
        )
    }
    if any(scope.is_device for scope in scopes):
        return []
    return [
        {
            "peer": peer,
            "routing_instance": data.get("routing_instance"),
            "snapshot": "subject",
        }
        for peer, data in sorted((subject.facts.get("bgp") or {}).items())
        if peer not in assigned
    ]


def _unassigned_static_routes(
    subject: Snapshot, scopes: list[Scope]
) -> list[dict[str, Any]]:
    """Routy z tabulky, ktere si nenarokuje zadny scope.

    Sem spadne statika v management instanci - fxp0.0 se scopem nikdy
    nestane, takze routa nema ke ktere sluzbe patrit. A taky routa, kterou
    parser neumel precist: kdyz konfiguracni tvar nezname, do selektoru se
    nedostane, ale v tabulce ji videt je. Je to tedy i pojistka proti
    mezeram v parsovani.
    """
    assigned = {
        (str(route.get("rib")), str(route.get("prefix")))
        for scope in scopes
        for route in scope.selectors.static_routes
    }
    if any(scope.is_device for scope in scopes):
        return []
    return [
        {
            "rib": table,
            "prefix": prefix,
            "next_hop": data.get("next_hop", []),
            "via": data.get("via", []),
            "snapshot": "subject",
        }
        for table, prefixes in sorted((subject.facts.get("routes") or {}).items())
        for prefix, data in sorted(prefixes.items())
        if (table, prefix) not in assigned
    ]


def _unassigned_bfd_sessions(
    subject: Snapshot, scopes: list[Scope]
) -> list[dict[str, Any]]:
    """Session peeru, ktery neni v zadnem bgp_neighbors.

    Napriklad BFD drzene jinym klientem nez BGP - parser takovy zamer
    necte, takze by session jinak nikde nefigurovala.
    """
    # Zamerna asymetrie proti _unassigned_bgp_peers: tam se
    # bgp_neighbors_inactive do `assigned` pricita, tady ne.
    #
    # U BGP dostane deaktivovany peer se zivou session skutecny nalez u sve
    # sluzby (Scope.select ho vybere, checks/bgp.py ho vypise normalni
    # vetvi), takze by se v NEZARAZENO objevil podruhe a vztah ke sluzbe by
    # se zahodil. U BFD zadny takovy nalez nevznika: Scope.select session
    # deaktivovaneho peera do sluzby zamerne nevybira, protoze checks/bfd.py
    # o deaktivaci nevi a napsal by k ni nepravdive 'v konfiguraci sluzby
    # neni' (BFD se na teto vlne zamerne nemenilo). NEZARAZENO je tedy
    # jedine misto, kde takova session muze zustat videt; pricist inactive
    # by znamenalo, ze zmizi uplne.
    assigned = {peer for scope in scopes for peer in scope.selectors.bgp_neighbors}
    if any(scope.is_device for scope in scopes):
        return []
    return [
        {
            "peer": peer,
            "interface": data.get("interface"),
            "state": data.get("state"),
            "snapshot": "subject",
        }
        for peer, data in sorted((subject.facts.get("bfd") or {}).items())
        if peer not in assigned
    ]


def evaluate_snapshots(
    subject: Snapshot,
    baseline: Snapshot | None = None,
    mapping: Mapping | None = None,
    config: CheckConfig | None = None,
    now: str | None = None,
    service_types: list[str] | None = None,
    profile_name: str | None = None,
) -> RunResult:
    config = config or default_config()
    mapping = mapping or empty_mapping()

    def _in_profile(scope: Scope) -> bool:
        # Filtr je jen na service typy: device a layer1 scopy jsou
        # infrastruktura, ne sluzba, a v reportu zustavaji vzdy.
        if service_types is None or scope.kind != "service":
            return True
        return scope.service_type in set(service_types)

    subject_scopes = _scopes_of(subject)
    subject_l1 = [s for s in subject_scopes if s.kind == "layer1"]
    subject_services = [s for s in subject_scopes if s.kind != "layer1"]
    link_payloads = _link_payloads(
        link_scopes(subject_scopes, subject.facts.get("evpn_instance") or {})
    )
    scope_results: list[ScopeResult] = []
    unmatched: dict[str, list[dict[str, Any]]] = {"baseline": [], "subject": []}
    matched_count = 0
    skipped_total = 0

    if baseline is None:
        for scope in subject_scopes:
            if not _in_profile(scope):
                skipped_total += 1
                continue
            scope_results.append(
                _run_scope(
                    scope, subject, None, None, config, None, link=link_payloads.get(scope.id)
                )
            )
    else:
        baseline_scopes = _scopes_of(baseline)
        baseline_l1 = [s for s in baseline_scopes if s.kind == "layer1"]
        baseline_services = [s for s in baseline_scopes if s.kind != "layer1"]
        matches = match_scopes(baseline_services, subject_services, mapping)
        matched_count = len(matches.pairs)

        for pair in matches.pairs:
            if not _in_profile(pair.subject):
                skipped_total += 1
                continue
            scope_results.append(
                _run_scope(
                    pair.subject,
                    subject,
                    pair.baseline,
                    baseline,
                    config,
                    _match_info(pair),
                    link=link_payloads.get(pair.subject.id),
                )
            )

        for item in matches.unmatched_subject:
            # `unmatched["subject"]` seznam se nefiltruje - NESPAROVANO je
            # pojistka proti prehlednuti a filtr ji smi zuzit jen v tom, co
            # jde do check smycky (scope_results), ne co je videt v sekci.
            if not _in_profile(item.scope):
                skipped_total += 1
            else:
                scope_results.append(
                    _run_scope(
                        item.scope,
                        subject,
                        None,
                        None,
                        config,
                        MatchInfo(
                            status="unmatched",
                            reason=item.reason,
                            subject_interfaces=list(item.scope.selectors.interfaces),
                        ),
                        link=link_payloads.get(item.scope.id),
                    )
                )
            unmatched["subject"].append(_unmatched_entry(item.scope, item.reason))

        for item in matches.unmatched_baseline:
            unmatched["baseline"].append(_unmatched_entry(item.scope, item.reason))

        for l1_scope in subject_l1:
            baseline_scope = _l1_baseline(l1_scope, matches.pairs, baseline_l1)
            match = (
                MatchInfo(
                    status="matched",
                    method="layer1-children",
                    confidence="medium",
                    baseline_interfaces=list(baseline_scope.selectors.interfaces),
                    subject_interfaces=list(l1_scope.selectors.interfaces),
                )
                if baseline_scope is not None
                else None
            )
            scope_results.append(
                _run_scope(l1_scope, subject, baseline_scope, baseline, config, match)
            )

    scope_results = _group_by_layer1(_reorder_linked(scope_results))

    summary = {
        **count_statuses(
            check.status for scope_result in scope_results for check in scope_result.checks
        ),
        "scopes_matched": matched_count,
        "unmatched_baseline": len(unmatched["baseline"]),
        "unmatched_subject": len(unmatched["subject"]),
    }

    filtered = None
    if service_types is not None:
        filtered = {
            "service_types": list(service_types),
            "scopes_shown": len(scope_results),
            "scopes_total": len(scope_results) + skipped_total,
        }
        if profile_name:
            filtered["profile"] = profile_name

    return RunResult(
        evaluated_at=now or _now(),
        subject=_snapshot_meta(subject),
        baseline=_snapshot_meta(baseline) if baseline else None,
        summary=summary,
        scopes=scope_results,
        unmatched=unmatched,
        unassigned={
            "bgp_peers": _unassigned_bgp_peers(subject, subject_scopes),
            "static_routes": _unassigned_static_routes(subject, subject_scopes),
            "bfd_sessions": _unassigned_bfd_sessions(subject, subject_scopes),
        },
        filtered=filtered,
    )
