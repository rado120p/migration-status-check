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
from migration_validator.checks.baseline import UNKNOWN, suffix, unchanged_or
from migration_validator.checks.bgp import ESTABLISHED, NOT_IN_SERVICE, peer_family
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

NO_SESSION = "bez session"
BGP_NOT_UP = "BGP neni Established"
PARSER_MISSED = "parser nenasel konfiguraci"

# NOT_IN_SERVICE je tvrzeni o CLENSTVI ve sluzbe, ne o existenci na zarizeni,
# a to jde rict jen v service scope. Peer, ktereho uz tato sluzba nenarokuje,
# muze na zarizeni dal bezet pod jinou sluzbou - engine.py:_unassigned_bfd_sessions
# by ho ukazal v NEZARAZENO. Device scope zadnou inventory
# nema, takze `configured` je tam vzdy False a o konfiguraci nejde tvrdit nic
# (AR-17) - zbyva ciste stav session. Rozdil musi byt v hodnote, ne jen v
# hlasce: do reportu jde sloupec s hodnotou (F-7/AR-4), hlaska se v textovem
# vypisu neobjevi. Konstanta je sdilena s bgp.py - stejny konstrukt, stejna
# formulace.
SESSION_GONE = "session zmizela"


@register
class BfdSessionStateCheck(Check):
    id = "bfd_session_state"
    title = "Stav BFD session"
    label = "BFD"
    mode = Mode.BOTH
    requires = ("bfd", "bgp")
    default_severity = Severity.CRITICAL

    def applies_to(self, scope) -> bool:
        if scope.service_type == "Core":
            # Transit meri bfd_transit_state (session se paruje podle
            # rozhrani, ne podle peer adresy ze zamerove konfigurace).
            #
            # iBGP BFD na lo0.0 je vedome odlozene rozhodnuti (2026-08-26):
            # Scope.select ted tahne interni peery i do Core-loopback scope,
            # ale zadny check jejich session nemeri. Kdyby tu tenhle check
            # bezel dal, kazda takova session (zamer v konfiguraci existuje)
            # by dostala nepravdive DEGRADED "parser nenasel konfiguraci" - loopback BFD
            # zamer se totiz z inventory neparsuje. Radeji zadny check nez
            # lhavy.
            return False
        return super().applies_to(scope)

    def run(self, ctx: CheckContext) -> list[Finding]:
        # Zaznam bez `peer` se preskoci. str(item.get("peer")) by z nej udelal
        # doslovny string "None", tedy radek "BFD (None)" s family=None, ktery
        # spadne do bezhlavickove sekce reportu. Parser peer vzdy vyplni,
        # takze je to dosazitelne jen rucne upravenou inventory.
        intent = {
            str(item["peer"]): item
            for item in ctx.scope.selectors.bfd_peers
            if item.get("peer")
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
                    ctx=ctx,
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
        ctx: CheckContext,
    ) -> Finding:
        label = f"{self.label} ({peer})"
        family = peer_family(peer)
        # Stejna funkce pro subjekt i baseline (se stejnym configured/is_device
        # subjektu), takze baseline_value mluvi slovnikem radku (R-5). Plati
        # jen pro vetve, ktere se MOHOU stat UNCHANGED (porovnavaji se) -
        # SESSION_GONE/NOT_IN_SERVICE nize maji vlastni `was_measured`.
        was = (
            _session_value(baseline, configured, is_device, bgp_state)
            if ctx.has_baseline
            else None
        )
        # SESSION_GONE/NOT_IN_SERVICE jsou z definice "v baseline byla, ted
        # neni" - nikdy UNCHANGED, takze baseline_value tam nema mluvit
        # slovnikem SUBJEKTOVEHO configured/is_device (`_session_value` by na
        # NOT_IN_SERVICE radku vratila PARSER_MISSED, i kdyz baseline session
        # realne mela stav). Poctivy baseline_value je to, co baseline
        # skutecne zmerila - surovy stav session.
        was_measured = str(baseline.get("state")) if baseline else None

        if session is not None:
            # Surovy stav ze session, ne slovnik `value` - hlaska "existuje
            # (Up)" ma mluvit o skutecnem stavu i ve vetvi PARSER_MISSED,
            # kde _session_value uz vraci znacku, ne stav.
            raw_state = str(session.get("state", UNKNOWN))
            state = _session_value(session, configured, is_device, bgp_state)

            # Bez inventory neni zamer znam, takze se nehlasi, ze
            # konfigurace chybi (AR-17).
            if not configured and not is_device:
                return Finding(
                    Outcome.DEGRADED,
                    f"{peer}: BFD session existuje ({raw_state}), ale parser ji nenasel "
                    "v konfiguraci sluzby",
                    label=label,
                    family=family,
                    value=state,
                    baseline_value=was,
                    subject=session,
                )

            outcome = Outcome.OK if state == "Up" else Outcome.BROKEN
            if outcome is Outcome.BROKEN:
                same = (
                    state != UNKNOWN
                    and baseline is not None
                    and str(baseline.get("state")) == state
                )
                outcome = unchanged_or(outcome, ctx, "bfd", same=same)
            message = (
                f"{peer}: session {state}"
                if outcome is Outcome.OK
                else f"{peer}: session {state}, ocekavano Up{suffix(outcome)}"
            )
            return Finding(
                outcome,
                message,
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
                # jen ze session, ktera v baseline byla, uz neexistuje. Tato
                # vetev je z definice "v baseline byla" - nikdy UNCHANGED.
                return Finding(
                    Outcome.BROKEN,
                    f"{peer}: session byla v baseline ({was_measured}), v subjektu neexistuje",
                    label=label,
                    family=family,
                    value=SESSION_GONE,
                    baseline_value=was_measured,
                    baseline=baseline,
                )

            # Session byla v baseline, v subjektu neni ani zamer. Tvrzeni o
            # CLENSTVI, ne o existenci: peer, ktereho nenarokuje zadny
            # subjektovy scope, muze mit na zarizeni zivou BFD session -
            # engine.py:_unassigned_bfd_sessions ji ukaze v NEZARAZENO.
            # Hlaska "v subjektu neni nakonfigurovane" by tam lhala. Nova
            # formulace je pravdiva v obou pripadech, ktere sem spadaji
            # (BFD ze zarizeni zmizelo i BFD preslo pod jinou sluzbu).
            # Migrace nema tise shodit ze stolu ochranu, ktera tam byla.
            # Tato vetev je take z definice "v baseline byla" - nikdy
            # UNCHANGED.
            return Finding(
                Outcome.BROKEN,
                f"{peer}: v baseline patril k teto sluzbe, v subjektu uz ne",
                label=label,
                family=family,
                value=NOT_IN_SERVICE,
                baseline_value=was_measured,
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

        outcome = unchanged_or(Outcome.BROKEN, ctx, "bfd", same=baseline is None)
        return Finding(
            outcome,
            f"{peer}: BFD nakonfigurovano, BGP bezi, ale session neexistuje{suffix(outcome)}",
            label=label,
            family=family,
            value=NO_SESSION,
            baseline_value=was,
        )


def _session_value(
    session: dict[str, Any] | None,
    configured: bool,
    is_device: bool,
    bgp_state: str,
) -> str:
    """Vraci presne to, co dnes konci ve `value` prislusne vetve `_finding`.
    Pouziva se pro subjekt i pro baseline session (se stejnym configured/
    is_device subjektu), takze baseline_value mluvi stejnym slovnikem jako
    value (R-5)."""
    if session is not None:
        state = str(session.get("state", UNKNOWN))
        if not configured and not is_device:
            return PARSER_MISSED
        return state
    if not configured:
        return SESSION_GONE if is_device else NOT_IN_SERVICE
    if bgp_state != ESTABLISHED:
        return BGP_NOT_UP
    return NO_SESSION
