"""BGP checky.

Peer patri ke sluzbe pres bgp_neighbor z inventory - parser ho doplnuje na
zaklade shody se subnetem rozhrani, takze scope uz ma spravny seznam.

Pocty prefixu se porovnavaji s toleranci, ne 1:1. Presna shoda generuje
mnozstvi FAILu kvuli rozdilu nekolika rout, coz neni signifikantni.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.ifaces import percent_change
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

CUSTOMER_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})
ESTABLISHED = "Established"
PREFIX_KEYS = ("received", "accepted", "advertised")


@register
class BgpSessionStateCheck(Check):
    id = "bgp_session_state"
    title = "Stav BGP session"
    mode = Mode.BOTH
    requires = ("bgp",)
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        peers: dict[str, Any] = ctx.subject.get("bgp", {})
        if not peers:
            return [Finding(Outcome.SKIP, "sluzba nema zadne BGP peery")]

        baseline_peers = (ctx.baseline or {}).get("bgp", {})

        findings = []
        for peer in sorted(peers):
            state = str(peers[peer].get("state", "unknown"))
            subject = {"state": state}

            if state != ESTABLISHED:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{peer}: stav {state}, ocekavano {ESTABLISHED}",
                        label=peer,
                        subject=subject,
                    )
                )
                continue

            baseline_state = (
                str(baseline_peers[peer].get("state", "unknown"))
                if peer in baseline_peers
                else None
            )
            if baseline_state is not None and baseline_state != state:
                findings.append(
                    Finding(
                        Outcome.DEGRADED,
                        f"{peer}: stav se zmenil {baseline_state} -> {state}",
                        label=peer,
                        baseline={"state": baseline_state},
                        subject=subject,
                    )
                )
                continue

            findings.append(
                Finding(
                    Outcome.OK,
                    f"{peer}: {ESTABLISHED}",
                    label=peer,
                    baseline={"state": baseline_state} if baseline_state else None,
                    subject=subject,
                )
            )
        return findings


@register
class BgpPrefixCountsCheck(Check):
    id = "bgp_prefix_counts"
    title = "Pocty BGP prefixu"
    mode = Mode.COMPARE
    requires = ("bgp",)
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        peers: dict[str, Any] = ctx.subject.get("bgp", {})
        if not peers:
            return [Finding(Outcome.SKIP, "sluzba nema zadne BGP peery")]

        baseline_peers = (ctx.baseline or {}).get("bgp", {})
        tolerance = float(ctx.options(self.id)["tolerance_percent"])

        findings = []
        for peer in sorted(peers):
            if peer not in baseline_peers:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{peer}: peer neni v baseline snapshotu, nelze porovnat",
                        label=peer,
                    )
                )
                continue

            subject = _counts(peers[peer])
            baseline = _counts(baseline_peers[peer])
            details: dict[str, Any] = {"tolerance_percent": tolerance}
            drops = []

            for key in PREFIX_KEYS:
                change = percent_change(baseline[key], subject[key])
                if change is None:
                    continue
                details[f"{key}_change_percent"] = round(change, 1)
                if change < tolerance:
                    drops.append(f"{key} {baseline[key]} -> {subject[key]}")

            if drops:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{peer}: pokles prefixu ({'; '.join(drops)}), "
                        f"prah je {tolerance:.0f} %",
                        label=peer,
                        baseline=baseline,
                        subject=subject,
                        details=details,
                    )
                )
            else:
                findings.append(
                    Finding(
                        Outcome.OK,
                        f"{peer}: pocty prefixu v toleranci {tolerance:.0f} %",
                        label=peer,
                        baseline=baseline,
                        subject=subject,
                        details=details,
                    )
                )
        return findings


def _counts(peer: dict[str, Any]) -> dict[str, int]:
    prefixes = peer.get("prefixes", {})
    return {key: int(prefixes.get(key, 0)) for key in PREFIX_KEYS}
