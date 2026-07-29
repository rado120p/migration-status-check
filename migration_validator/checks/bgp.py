"""BGP checky.

Peer patri ke sluzbe pres bgp_neighbor z inventory - parser ho doplnuje na
zaklade shody se subnetem rozhrani, takze scope uz ma spravny seznam.

Pocty prefixu se porovnavaji s toleranci, ne 1:1. Presna shoda generuje
mnozstvi FAILu kvuli rozdilu nekolika rout, coz neni signifikantni.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.ifaces import percent_change
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

CUSTOMER_SERVICE_TYPES = frozenset({"Internet", "IPVPN"})
ESTABLISHED = "Established"
# Countery, ktere se dostanou do reportu - jeden radek na counter.
# `suppressed` tu chybi zamerne (rozhodnuti 2026-07-29): damping se v
# tomhle nasazeni nepouziva, takze radek by byl vzdy nulovy. Odpada s nim
# i to, ze se u nej porovnani cetlo obracene - u potlacenych rout je
# pokles zlepseni, ne regrese. Collector ho sbira dal, aby snapshot
# zustal vernym zaznamem zarizeni.
PREFIX_KEYS = ("active", "received", "accepted", "advertised")


def peer_family(peer: str) -> int | None:
    """Rodina se odvozuje z adresy peeru, ne ze jmena RIB.

    Jmeno RIB rodinu nemusi obsahovat vubec (bgp.l3vpn.0). Zname omezeni:
    peer s IPv4 adresou nesouci IPv6 RIB se cely zaradi do sekce IPv4.
    """
    try:
        return ipaddress.ip_address(peer).version
    except ValueError:
        return None


@register
class BgpSessionStateCheck(Check):
    id = "bgp_session_state"
    title = "Stav BGP session"
    label = "BGP status"
    mode = Mode.BOTH
    requires = ("bgp",)
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        peers: dict[str, Any] = ctx.subject.get("bgp", {})
        if not peers:
            return [Finding(Outcome.SKIP, "sluzba nema zadne BGP peery", value="zadny peer")]

        baseline_peers = (ctx.baseline or {}).get("bgp", {})

        findings = []
        for peer in sorted(peers):
            state = str(peers[peer].get("state", "unknown"))
            subject = {"state": state}

            # Dohledava se pred vetvenim, ne uvnitr vetve pro Established:
            # spadla relace je prave ten pripad, kvuli kteremu sloupec ZMENA
            # vznikl, a kdyz se baseline hledal az za jejim continue, report
            # u ni psal 'bez baseline', prestoze check predchozi stav znal.
            baseline_state = (
                str(baseline_peers[peer].get("state", "unknown"))
                if peer in baseline_peers
                else None
            )

            if state != ESTABLISHED:
                findings.append(
                    Finding(
                        Outcome.BROKEN,
                        f"{peer}: stav {state}, ocekavano {ESTABLISHED}",
                        label="BGP status",
                        family=peer_family(peer),
                        value=state,
                        baseline_value=baseline_state,
                        baseline={"state": baseline_state} if baseline_state else None,
                        subject=subject,
                    )
                )
                continue

            # Sem se dojde jen se stavem Established - horsi stavy odesly
            # vetvi vyse. Zmena proti baseline tedy znamena, ze se relace
            # behem migrace ZLEPSILA, a zlepseni neni varovani (R-2):
            # oranzovy radek na zdrave sluzbe je falesny poplach a operator
            # si zvykne vypis preskakovat. Zmena nezmizi - pojmenuje ji
            # hlaska a sloupec ZMENA pise, jaky byl stav predtim.
            changed = baseline_state is not None and baseline_state != state
            findings.append(
                Finding(
                    Outcome.OK,
                    (
                        f"{peer}: stav se zmenil {baseline_state} -> {state}"
                        if changed
                        else f"{peer}: {ESTABLISHED}"
                    ),
                    label="BGP status",
                    family=peer_family(peer),
                    value=state,
                    baseline_value=baseline_state,
                    baseline={"state": baseline_state} if baseline_state else None,
                    subject=subject,
                )
            )
        return findings


@register
class BgpPrefixCountsCheck(Check):
    id = "bgp_prefix_counts"
    title = "Pocty BGP prefixu"
    label = "BGP prefixy"
    mode = Mode.COMPARE
    requires = ("bgp",)
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.ADVISORY

    def run(self, ctx: CheckContext) -> list[Finding]:
        peers: dict[str, Any] = ctx.subject.get("bgp", {})
        if not peers:
            return [Finding(Outcome.SKIP, "sluzba nema zadne BGP peery", value="zadny peer")]

        baseline_peers = (ctx.baseline or {}).get("bgp", {})
        tolerance = float(ctx.options(self.id)["tolerance_percent"])

        findings = []
        for peer in sorted(peers):
            family = peer_family(peer)
            if peer not in baseline_peers:
                findings.append(
                    Finding(
                        Outcome.SKIP,
                        f"{peer}: peer neni v baseline snapshotu, nelze porovnat",
                        label="BGP prefixy",
                        family=family,
                        value="bez baseline",
                    )
                )
                continue

            subject_ribs = peers[peer].get("ribs", {})
            baseline_ribs = baseline_peers[peer].get("ribs", {})

            for rib_name in sorted(subject_ribs):
                subject = subject_ribs[rib_name]
                baseline = baseline_ribs.get(rib_name)
                if baseline is None:
                    findings.append(
                        Finding(
                            Outcome.SKIP,
                            f"{peer}/{rib_name}: RIB neni v baseline, nelze porovnat",
                            label=f"BGP prefixy ({rib_name})",
                            family=family,
                            value="bez baseline",
                        )
                    )
                    continue

                for key in PREFIX_KEYS:
                    findings.append(
                        _prefix_finding(
                            peer, rib_name, key, baseline[key], subject[key],
                            tolerance, family,
                        )
                    )
        return findings


def _prefix_finding(
    peer: str,
    rib_name: str,
    key: str,
    baseline: int,
    subject: int,
    tolerance: float,
    family: int | None,
) -> Finding:
    """Jeden radek na counter - report je vypisuje jednotlive."""
    label = f"BGP {key}-prefix-count"
    change = percent_change(baseline, subject)
    delta = None
    if subject != baseline:
        delta = f"{subject - baseline:+d}"

    details = {"rib": rib_name, "tolerance_percent": tolerance}
    if change is not None:
        details["change_percent"] = round(change, 1)

    outcome = Outcome.OK
    message = f"{peer}/{rib_name}: {key} {subject}"
    if change is not None and change < tolerance:
        outcome = Outcome.BROKEN
        message = (
            f"{peer}/{rib_name}: pokles {key} {baseline} -> {subject}, "
            f"prah je {tolerance:.0f} %"
        )

    return Finding(
        outcome,
        message,
        label=label,
        family=family,
        value=str(subject),
        baseline_value=str(baseline),
        delta=delta,
        baseline={key: baseline},
        subject={key: subject},
        details=details,
    )
