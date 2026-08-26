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
from migration_validator.checks.deactivation import deactivation_outcome
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


class _AppliesToCoreLoopback:
    """BGP checky meri i interni peery na lo0.0 (spec 2026-08-26).

    service_types | {"Core"} nestaci - Core transit zadne peery nema a
    dostal by prazdne SKIP/FAIL radky. Gate na subtype je proto v
    applies_to, ne v datech.
    """

    def applies_to(self, scope):
        if scope.service_type == "Core":
            return scope.service_subtype == "loopback"
        return super().applies_to(scope)


# Popisek radku je vzdycky "BGP status (adresa)", i kdyz ma sekce jedineho
# peera a adresa je tam potreti. Podminit ho poctem peeru v sekci se
# nabizelo - zmereno, ze v laborce je redundantni ve vsech sedmi sekcich -
# ale `label` je identifikator radku i ve strojovem JSON vystupu. Podmineny
# kvalifikator by znamenal, ze pribyti druheho souseda prejmenuje i radek
# toho prvniho, takze dva behy tehoz stavu by se v diffu nesparovaly.
#
# Na sdilene podsiti (/29, /24 na NNI nebo zakaznicka podsit se dvema CPE)
# je kvalifikator nutny: bez nej by dva radky "BGP status" vedle sebe
# nerekly, ktery soused je rozbity. Fixtures tenhle tvar nemodeluji, takze
# mereni ukazuje redundanci, ne cenu jejiho odstraneni.
@register
class BgpSessionStateCheck(_AppliesToCoreLoopback, Check):
    id = "bgp_session_state"
    title = "Stav BGP session"
    label = "BGP status"
    mode = Mode.BOTH
    requires = ("bgp",)
    service_types = CUSTOMER_SERVICE_TYPES
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        peers: dict[str, Any] = ctx.subject.get("bgp", {})
        baseline_peers = (ctx.baseline or {}).get("bgp", {})
        configured = list(ctx.scope.selectors.bgp_neighbors)
        inactive = [
            peer
            for peer in ctx.scope.selectors.bgp_neighbors_inactive
            if peer not in peers
        ]

        # Identita peera se bere ze ZAMERU, ne z mereni. Scope.select()
        # (models/scope.py:163) filtruje merena fakta podle clenstvi, takze
        # nakonfigurovany peer bez session se do `peers` nedostane a dostat
        # nemuze. Kdo by iteroval jen `peers`, napsal by vetev, ktera v
        # provozu nikdy nic nenajde.
        universe = (
            set(configured)
            | set(ctx.scope.selectors.bgp_neighbors_inactive)
            | set(peers)
            | set(baseline_peers)
        )

        # Poradi je soucast pozadavku: sluzba, jejiz jediny peer je
        # deaktivovany nebo bez session, nesmi dostat 'nema zadne BGP peery' -
        # to by tvrdilo, ze v konfiguraci zadny neni. Hlaska je pravdiva
        # teprve kdyz je prazdne cele sjednoceni.
        if not universe:
            return [Finding(Outcome.SKIP, "sluzba nema zadne BGP peery", value="zadny peer")]

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
                        label=f"BGP status ({peer})",
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
                    label=f"BGP status ({peer})",
                    family=peer_family(peer),
                    value=state,
                    baseline_value=baseline_state,
                    baseline={"state": baseline_state} if baseline_state else None,
                    subject=subject,
                )
            )

        # Deaktivovany peer, pro ktery presto prisla session, se sem
        # nedostane (filtr `peer not in peers` vys) a projde normalni vetvi -
        # je to rozpor konfigurace se stavem a ma byt videt.
        #
        # Radek 4 tabulky (aktivni ted, vypnuty v baselinu) se sem nedostane
        # taky: takovy peer je v `peers` nebo v `bgp_neighbors`, ne v
        # `inactive`. Nedostat se tam ma - znovuzapnuty peer zadny
        # deaktivovany prvek nenese a zlepseni neni varovani (R-2).
        baseline_inactive = (
            ctx.baseline_scope.selectors.bgp_neighbors_inactive
            if ctx.baseline_scope is not None
            else []
        )
        baseline_active = (
            ctx.baseline_scope.selectors.bgp_neighbors
            if ctx.baseline_scope is not None
            else []
        )
        for peer in sorted(inactive):
            if peer in baseline_inactive:
                baseline_off = True
            elif peer in baseline_active:
                baseline_off = False
            else:
                baseline_off = None
            outcome = deactivation_outcome(True, baseline_off)
            message = (
                f"peer {peer} v baseline bezel, ted je v konfiguraci "
                "deaktivovan - migrace nedokoncena"
                if outcome is Outcome.BROKEN
                else f"peer {peer} je v konfiguraci deaktivovan"
            )
            findings.append(
                Finding(
                    outcome,
                    message,
                    label=f"BGP status ({peer})",
                    family=peer_family(peer),
                    value="deaktivovan",
                )
            )

        # Peer, ktery ma byt a session pro nej neprisla. Zrcadli vetev
        # `if subject is None:` v checks/routes.py, ale jen tvarem, ne
        # hlaskou: routa v baseline byla a v subjektu neni, kdezto peer
        # muze na zarizeni dal bezet - jen ho tahle sluzba uz nenarokuje.
        # Deaktivovane peery uz vyresila smycka vys, proto se odectou.
        without_session = universe - set(peers) - set(ctx.scope.selectors.bgp_neighbors_inactive)
        for peer in sorted(without_session):
            in_config = peer in configured
            findings.append(
                Finding(
                    Outcome.BROKEN,
                    # Tvrzeni o CLENSTVI, ne o existenci. Peer, ktereho
                    # nenarokuje zadny subjektovy scope, muze mit na
                    # zarizeni zivou session - engine.py:_unassigned_bgp_peers
                    # ji ukaze v NEZARAZENO. Hlaska "v subjektu neni" tam
                    # tedy lhala. Nova formulace je pravdiva v obou
                    # pripadech, ktere sem spadnou (peer ze zarizeni zmizel
                    # i peer presel pod jinou sluzbu), takze se check nemusi
                    # ptat na nefiltrovana fakta, ktera nema.
                    f"{peer}: nakonfigurovan, ale session neexistuje"
                    if in_config
                    else f"{peer}: v baseline patril k teto sluzbe, v subjektu uz ne",
                    label=f"BGP status ({peer})",
                    family=peer_family(peer),
                    value="bez session" if in_config else "neni ve sluzbe",
                    baseline_value=(
                        str(baseline_peers[peer].get("state", "unknown"))
                        if peer in baseline_peers
                        else None
                    ),
                )
            )
        return findings


@register
class BgpPrefixCountsCheck(_AppliesToCoreLoopback, Check):
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
            return [
                Finding(
                    Outcome.SKIP,
                    "zadna namerena BGP session, neni co porovnat",
                    value="zadna session",
                )
            ]

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
    label = f"{key}-prefix-count"
    group = f"BGP {peer} / {rib_name}"
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
        group=group,
        family=family,
        value=str(subject),
        baseline_value=str(baseline),
        delta=delta,
        baseline={key: baseline},
        subject={key: subject},
        details=details,
    )
