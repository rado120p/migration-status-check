"""Parovani scopu mezi baseline a subject snapshotem.

Klicove pravidlo: pri nejednoznacnosti se nikdy nehada. Tichy spatny match
by u migrace znamenal zelenou na rozbite sluzbe.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Callable, Hashable

from migration_validator.models.scope import Scope
from migration_validator.scoping.mapping import Mapping, empty_mapping

REASON_NO_CANDIDATE = "zadny kandidat na subject"
REASON_NEW_SERVICE = "nova sluzba, chybi baseline"


@dataclass
class MatchedPair:
    baseline: Scope
    subject: Scope
    method: str
    confidence: str


@dataclass
class UnmatchedScope:
    scope: Scope
    reason: str


@dataclass
class MatchSet:
    pairs: list[MatchedPair] = field(default_factory=list)
    unmatched_baseline: list[UnmatchedScope] = field(default_factory=list)
    unmatched_subject: list[UnmatchedScope] = field(default_factory=list)


KeyFn = Callable[[Scope], list[Hashable]]


def _keys_description_subtype(scope: Scope) -> list[Hashable]:
    key = scope.key
    if key is None or key.description is None or key.service_subtype is None:
        return []
    return [(key.description, key.service_type, key.service_subtype)]


def _keys_description(scope: Scope) -> list[Hashable]:
    key = scope.key
    if key is None or key.description is None:
        return []
    return [(key.description, key.service_type)]


def _keys_routing_instance(scope: Scope) -> list[Hashable]:
    key = scope.key
    if key is None:
        return []
    return [
        (instance, key.service_type) for instance in scope.selectors.routing_instances
    ]


def _keys_subnet(scope: Scope) -> list[Hashable]:
    key = scope.key
    if key is None:
        return []
    keys: list[Hashable] = []
    for address in scope.selectors.local_addresses:
        try:
            network = ipaddress.ip_interface(address).network
        except ValueError:
            continue
        keys.append((str(network), key.service_type))
    return keys


def _keys_vlan(scope: Scope) -> list[Hashable]:
    key = scope.key
    if key is None:
        return []
    return [(vlan, key.service_type) for vlan in scope.selectors.vlans]


RULES: list[tuple[str, str, KeyFn]] = [
    ("description+service_type+service_subtype", "high", _keys_description_subtype),
    ("description+service_type", "high", _keys_description),
    ("routing_instance+service_type", "medium", _keys_routing_instance),
    ("subnet+service_type", "medium", _keys_subnet),
    ("vlan+service_type", "low", _keys_vlan),
]


def _index(scopes: list[Scope], key_fn: KeyFn) -> dict[Hashable, list[Scope]]:
    index: dict[Hashable, list[Scope]] = {}
    for scope in scopes:
        for key in key_fn(scope):
            index.setdefault(key, []).append(scope)
    return index


def _ambiguity_reason(candidates: list[Scope]) -> str:
    ids = ", ".join(scope.id for scope in candidates)
    return f"ambiguous: {len(candidates)} kandidatu ({ids})"


def _apply_manual(
    mapping: Mapping,
    baseline: list[Scope],
    subject: list[Scope],
    result: MatchSet,
) -> tuple[list[Scope], list[Scope]]:
    remaining_baseline = list(baseline)
    remaining_subject = list(subject)

    for rule in mapping.mappings:
        b_hits = [scope for scope in remaining_baseline if rule.baseline.matches(scope)]
        s_hits = [scope for scope in remaining_subject if rule.subject.matches(scope)]
        if len(b_hits) == 1 and len(s_hits) == 1:
            result.pairs.append(
                MatchedPair(
                    baseline=b_hits[0], subject=s_hits[0], method="manual", confidence="manual"
                )
            )
            remaining_baseline.remove(b_hits[0])
            remaining_subject.remove(s_hits[0])
            continue
        if len(b_hits) > 1:
            for scope in b_hits:
                result.unmatched_baseline.append(
                    UnmatchedScope(scope, _ambiguity_reason(b_hits))
                )
                remaining_baseline.remove(scope)
        if len(s_hits) > 1:
            for scope in s_hits:
                result.unmatched_subject.append(
                    UnmatchedScope(scope, _ambiguity_reason(s_hits))
                )
                remaining_subject.remove(scope)

    return remaining_baseline, remaining_subject


def match_scopes(
    baseline: list[Scope],
    subject: list[Scope],
    mapping: Mapping | None = None,
) -> MatchSet:
    mapping = mapping or empty_mapping()

    remaining_baseline = [scope for scope in baseline if not mapping.is_ignored(scope)]
    remaining_subject = [scope for scope in subject if not mapping.is_ignored(scope)]

    result = MatchSet()
    remaining_baseline, remaining_subject = _apply_manual(
        mapping, remaining_baseline, remaining_subject, result
    )

    for method, confidence, key_fn in RULES:
        baseline_index = _index(remaining_baseline, key_fn)
        subject_index = _index(remaining_subject, key_fn)

        paired: set[int] = set()
        dropped: set[int] = set()

        for key, b_hits in baseline_index.items():
            s_hits = subject_index.get(key)
            if not s_hits:
                continue
            if len(b_hits) == 1 and len(s_hits) == 1:
                if id(b_hits[0]) in paired or id(s_hits[0]) in paired:
                    continue
                result.pairs.append(
                    MatchedPair(
                        baseline=b_hits[0],
                        subject=s_hits[0],
                        method=method,
                        confidence=confidence,
                    )
                )
                paired.update({id(b_hits[0]), id(s_hits[0])})
                continue

            reason = _ambiguity_reason(s_hits if len(s_hits) > 1 else b_hits)
            for scope in b_hits:
                if id(scope) not in dropped and id(scope) not in paired:
                    result.unmatched_baseline.append(UnmatchedScope(scope, reason))
                    dropped.add(id(scope))
            for scope in s_hits:
                if id(scope) not in dropped and id(scope) not in paired:
                    result.unmatched_subject.append(UnmatchedScope(scope, reason))
                    dropped.add(id(scope))

        remaining_baseline = [
            scope
            for scope in remaining_baseline
            if id(scope) not in paired and id(scope) not in dropped
        ]
        remaining_subject = [
            scope
            for scope in remaining_subject
            if id(scope) not in paired and id(scope) not in dropped
        ]

    result.unmatched_baseline.extend(
        UnmatchedScope(scope, REASON_NO_CANDIDATE) for scope in remaining_baseline
    )
    result.unmatched_subject.extend(
        UnmatchedScope(scope, REASON_NEW_SERVICE) for scope in remaining_subject
    )
    return result
