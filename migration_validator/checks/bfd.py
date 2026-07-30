"""Check stavu BFD session.

Iteruje pres sjednoceni tri zdroju (AR-14): zamer z konfigurace, session
v subjektu a session v baseline. Peer, ktery BFD nikdy nemel, radek
nedostane (R-1).

Vazba na stav BGP je zamerna, ne kosmeticka: BFD nemuze nabehnout, dokud
nebezi BGP, takze bez ni by sluzba se spadlym BGP dostala dva FAIL radky
za jednu pricinu. V ostrem behu by se to opakovalo u kazde nedojete
sluzby a operator by si zvykl vypis preskakovat.
"""

from __future__ import annotations

from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.bgp import ESTABLISHED, peer_family
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

NO_SESSION = "bez session"
BGP_NOT_UP = "BGP neni Established"
NO_INTENT = "bez konfigurace"

# Dve konstanty na jeden stav "session byla v baseline, v subjektu neni",
# stejne jako routes.py rozlisuje MISSING_FROM_TABLE a MISSING_ENTIRELY.
# REMOVED je tvrzeni o zameru ("odstraneno z konfigurace") a to jde rict jen
# v service scope. Device scope zadnou inventory nema, takze `configured` je
# tam vzdy False a o konfiguraci nejde tvrdit nic (AR-17) - zbyva ciste stav
# session. Rozdil musi byt v hodnote, ne jen v hlasce: do reportu jde sloupec
# s hodnotou (F-7/AR-4), hlaska se v textovem vypisu neobjevi.
REMOVED = "BFD odstraneno"
SESSION_GONE = "session zmizela"


@register
class BfdSessionStateCheck(Check):
    id = "bfd_session_state"
    title = "Stav BFD session"
    label = "BFD"
    mode = Mode.BOTH
    requires = ("bfd", "bgp")
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        intent = {
            str(item.get("peer")): item for item in ctx.scope.selectors.bfd_peers
        }
        sessions: dict[str, Any] = ctx.subject.get("bfd", {})
        baseline_sessions: dict[str, Any] = (ctx.baseline or {}).get("bfd", {})
        bgp: dict[str, Any] = ctx.subject.get("bgp", {})

        findings = []
        for peer in sorted(set(intent) | set(sessions) | set(baseline_sessions)):
            findings.append(
                self._finding(
                    peer,
                    configured=peer in intent,
                    session=sessions.get(peer),
                    baseline=baseline_sessions.get(peer),
                    bgp_state=str((bgp.get(peer) or {}).get("state", "")),
                    is_device=ctx.scope.is_device,
                )
            )
        return findings

    def _finding(
        self,
        peer: str,
        configured: bool,
        session: dict[str, Any] | None,
        baseline: dict[str, Any] | None,
        bgp_state: str,
        is_device: bool,
    ) -> Finding:
        label = f"{self.label} ({peer})"
        family = peer_family(peer)
        was = str(baseline.get("state")) if baseline else None

        if session is not None:
            state = str(session.get("state", "unknown"))

            # Bez inventory neni zamer znam, takze se nehlasi, ze
            # konfigurace chybi (AR-17).
            if not configured and not is_device:
                return Finding(
                    Outcome.DEGRADED,
                    f"{peer}: session existuje ({state}), v konfiguraci sluzby neni",
                    label=label,
                    family=family,
                    value=NO_INTENT,
                    baseline_value=was,
                    subject=session,
                )

            outcome = Outcome.OK if state == "Up" else Outcome.BROKEN
            return Finding(
                outcome,
                f"{peer}: session {state}",
                label=label,
                family=family,
                value=state,
                baseline_value=was,
                baseline=baseline,
                subject=session,
            )

        if not configured:
            if is_device:
                # Bez inventory se netvrdi, ze konfigurace chybi (AR-17) -
                # jen ze session, ktera v baseline byla, uz neexistuje.
                return Finding(
                    Outcome.BROKEN,
                    f"{peer}: session byla v baseline ({was}), v subjektu neexistuje",
                    label=label,
                    family=family,
                    value=SESSION_GONE,
                    baseline_value=was,
                    baseline=baseline,
                )

            # Session byla v baseline, v subjektu neni ani zamer.
            # Migrace nema tise shodit ze stolu ochranu, ktera tam byla.
            return Finding(
                Outcome.BROKEN,
                f"{peer}: BFD bylo v baseline ({was}), v subjektu neni nakonfigurovane",
                label=label,
                family=family,
                value=REMOVED,
                baseline_value=was,
                baseline=baseline,
            )

        if bgp_state != ESTABLISHED:
            return Finding(
                Outcome.SKIP,
                f"{peer}: BFD nakonfigurovano, ale BGP je {bgp_state or 'neznamy'}",
                label=label,
                family=family,
                value=BGP_NOT_UP,
                baseline_value=was,
            )

        return Finding(
            Outcome.BROKEN,
            f"{peer}: BFD nakonfigurovano, BGP bezi, ale session neexistuje",
            label=label,
            family=family,
            value=NO_SESSION,
            baseline_value=was,
        )
