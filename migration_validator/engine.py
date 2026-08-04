"""Orchestrace vyhodnoceni snapshotu.

Engine nesaha na sit. Vsechna data pochazeji ze snapshotu.
"""

from __future__ import annotations

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
from migration_validator.models.scope import Scope, device_scope
from migration_validator.models.snapshot import Snapshot
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
    """
    data = baseline_scope.select(baseline.facts, baseline.probes)
    selectors = baseline_scope.selectors
    rename = dict(zip(selectors.interfaces, scope.selectors.interfaces))
    rename.update(
        zip(selectors.physical_interfaces, scope.selectors.physical_interfaces)
    )
    if rename:
        data["interfaces"] = {
            rename.get(name, name): iface_data
            for name, iface_data in data.get("interfaces", {}).items()
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
        "ipv4": list(selectors.local_ipv4),
        "ipv6": list(selectors.local_ipv6),
        "virtual_gw_v4": list(selectors.virtual_gw_v4),
        "virtual_gw_v6": list(selectors.virtual_gw_v6),
    }


def _run_scope(
    scope: Scope,
    subject: Snapshot,
    baseline_scope: Scope | None,
    baseline: Snapshot | None,
    config: CheckConfig,
    match: MatchInfo | None,
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
) -> RunResult:
    config = config or default_config()
    mapping = mapping or empty_mapping()

    subject_scopes = _scopes_of(subject)
    scope_results: list[ScopeResult] = []
    unmatched: dict[str, list[dict[str, Any]]] = {"baseline": [], "subject": []}
    matched_count = 0

    if baseline is None:
        for scope in subject_scopes:
            scope_results.append(_run_scope(scope, subject, None, None, config, None))
    else:
        matches = match_scopes(_scopes_of(baseline), subject_scopes, mapping)
        matched_count = len(matches.pairs)

        for pair in matches.pairs:
            scope_results.append(
                _run_scope(
                    pair.subject, subject, pair.baseline, baseline, config, _match_info(pair)
                )
            )

        for item in matches.unmatched_subject:
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
                )
            )
            unmatched["subject"].append(_unmatched_entry(item.scope, item.reason))

        for item in matches.unmatched_baseline:
            unmatched["baseline"].append(_unmatched_entry(item.scope, item.reason))

    summary = {
        **count_statuses(
            check.status for scope_result in scope_results for check in scope_result.checks
        ),
        "scopes_matched": matched_count,
        "unmatched_baseline": len(unmatched["baseline"]),
        "unmatched_subject": len(unmatched["subject"]),
    }

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
    )
