"""Check statickych rout.

Iteruje pres sjednoceni tri zdroju (AR-14): konfigurace subjektu, mereni
subjektu a mereni baseline. Kazdy z nich zavira jednu diru:

- bez konfigurace by nesel poznat rozpor 'nakonfigurovano, neni v tabulce',
- bez mereni subjektu by v rezimu bez inventory nebylo co vypsat,
- bez mereni baseline by tise zmizelo vsechno, co migrace odstranila -
  routa vyrazena z konfigurace se do selektoru subjektu nedostane, takze
  by se scope na jeji chybeni nikdy nezeptal.

Identita routy je (RIB, prefix), next-hop je hodnota. Diky tomu se zmena
next-hopu cte jako zmenena routa - jeden radek se sloupcem ZMENA - ne jako
routa zmizela a jina pribyla.

Zapsany predpoklad: jmena RIB migraci prezijou. `_aligned_baseline_data`
v enginu preslovnuje mezi baseline a subjectem jen oblast `interfaces`;
`routes` jsou klicovane table -> prefix a preslovneni nedostanou. V laborce
to plati (L3VPN-CPE13-NNI.inet*.0 je na obou zarizenich stejne), ale sady
instanci se lisi - mgmt_junos je jen na jednom z nich. Kdyby budouci migrace
prejmenovala VRF, kazda routa v ni se precte jako chybejici + nova.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.deactivation import deactivation_outcome
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

MISSING_FROM_TABLE = "neni v tabulce"
MISSING_ENTIRELY = "chybi"
NOT_ACTIVE = "neni aktivni"


def prefix_family(prefix: str) -> int | None:
    """Rodina se odvozuje z prefixu, ne ze jmena RIB.

    Jmeno RIB rodinu obsahovat nemusi (bgp.l3vpn.0). Bez rodiny by radek
    spadl do bezhlavickove sekce nad IPv4 i IPv6 (FAMILY_ORDER).
    """
    try:
        return ipaddress.ip_network(prefix, strict=False).version
    except ValueError:
        return None


def _next_hop_text(data: dict[str, Any] | None) -> str | None:
    """Hodnotou je mnozina next-hopu, ne jejich poradi v XML (AR-12).

    Pri ECMP nese rt-entry vic <nh> a Junos jejich poradi negarantuje ani
    mezi platformami, ani mezi verzemi - a tenhle check prochazi presne tu
    hranici (junos -> junos-evo). Bez sorted() by dva snimky s tymiz
    next-hopy v jinem poradi daly falesny WARN 'next-hop se zmenil
    A, B -> B, A', protoze vetev ZMENA porovnava `was != now` jako stringy.
    Setrizeny vypis je navic deterministicky.
    """
    if data is None:
        return None
    next_hops = data.get("next_hop") or []
    return ", ".join(sorted(next_hops)) if next_hops else "-"


def _flatten(routes: dict[str, Any] | None) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (table, prefix): data
        for table, prefixes in (routes or {}).items()
        for prefix, data in prefixes.items()
    }


@register
class StaticRouteStatusCheck(Check):
    id = "static_route_status"
    title = "Stav statickych rout"
    label = "Staticka routa"
    mode = Mode.BOTH
    requires = ("routes",)
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        configured = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in ctx.scope.selectors.static_routes
        }
        # Chybejici klic 'active' znamena zamer od parseru pred vlnou 8.
        # Snapshot i inventory maji od te vlny schema 5, takze se takovy
        # zamer nenacte - default je tu jen proto, aby jednotkovy test
        # nemusel psat klic, ktery netestuje.
        #
        # Klicovani jen dvojici (rib, prefix) je bezpecne, ne opomenuti:
        # duplicitni identita s ruznymi priznaky by umlcela i tu aktivni
        # routu, ale parser dva zaznamy pro tentyz prefix nevydava. Zmereno
        # 2026-08-04 na zive laborce konfiguraci s holym next-hopem a dvema
        # qualified-next-hopy (jeden deaktivovany): parser vydal jediny
        # zaznam. `configured` je klicovana stejne - je to sdileny dusledek,
        # ne nekonzistence mezi dvema mnozinami.
        deactivated = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in ctx.scope.selectors.static_routes
            if route.get("active", True) is False
        }
        # Baseline ZAMER, ne baseline mereni. Priznak deaktivace je v
        # inventory, takze `ctx.baseline` (fakta) o nem nevi nic.
        # `ctx.baseline_scope` je None v behu bez baselinu i u nesparovane
        # sluzby - v obou pripadech je spravna odpoved "neni s cim
        # porovnat", ne "v baselinu byla aktivni".
        baseline_routes = (
            ctx.baseline_scope.selectors.static_routes
            if ctx.baseline_scope is not None
            else []
        )
        baseline_deactivated = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in baseline_routes
            if route.get("active", True) is False
        }
        baseline_configured = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in baseline_routes
        }
        subject = _flatten(ctx.subject.get("routes"))
        baseline = _flatten((ctx.baseline or {}).get("routes"))

        findings = []
        for identity in sorted(configured | set(subject) | set(baseline)):
            findings.append(
                self._finding(
                    identity,
                    configured=identity in configured,
                    subject=subject.get(identity),
                    baseline=baseline.get(identity),
                    is_device=ctx.scope.is_device,
                    deactivated=identity in deactivated,
                    baseline_deactivated=(
                        identity in baseline_deactivated
                        if identity in baseline_configured
                        else None
                    ),
                )
            )
        return findings

    def _finding(
        self,
        identity: tuple[str, str],
        configured: bool,
        subject: dict[str, Any] | None,
        baseline: dict[str, Any] | None,
        is_device: bool,
        deactivated: bool,
        baseline_deactivated: bool | None,
    ) -> Finding:
        rib, prefix = identity
        label = f"{rib} {prefix}"
        group = "Staticke routy"
        family = prefix_family(prefix)
        was = _next_hop_text(baseline)

        if deactivated and subject is None:
            # Radek 4 tabulky (aktivni ted, vypnuta v baselinu) se sem
            # nedostane a nedostat se nema: taková routa zadny deaktivovany
            # prvek v konfiguraci nenese a jeji stav nese normalni radek.
            # Zlepseni neni varovani (R-2).
            #
            # Kdyz deaktivovana routa v tabulce presto je, sem se nedostane
            # taky - to uz je skutecny rozpor konfigurace se stavem a chova
            # se jako dosud.
            outcome = deactivation_outcome(True, baseline_deactivated)
            message = (
                f"{rib} {prefix}: v baseline bezela, ted je v konfiguraci "
                "deaktivovana - migrace nedokoncena"
                if outcome is Outcome.BROKEN
                else f"{rib} {prefix}: routa je v konfiguraci deaktivovana"
            )
            return Finding(
                outcome,
                message,
                label=label,
                group=group,
                family=family,
                value="deaktivovana",
                baseline_value=was,
                baseline=baseline,
            )

        if subject is None:
            # Bez inventory neni zamer znam, takze se rozpor nehlasi
            # (AR-17). Sem se v device scope dostane jen routa, ktera byla
            # v baseline a v subjektu neni.
            #
            # `not is_device` je tady necinny: device_scope() ma vzdy prazdne
            # selektory, takze `configured` uz samo znamena ne-device. Drzi se
            # jako zapsany zamer AR-17, ne jako prace. Totez plati o obdobne
            # vetvi v bfd.py: device scope se nikdy nesparuje (device_scope()
            # ma key=None a klicovaci funkce v scoping/matcher.py na None
            # vraci prazdno), takze mu engine baseline vubec nepreda a vetev
            # je necinna i tam. Ani jednu nemazat - obe kryji AR-17 pro
            # pripad, ze by budouci format snapshotu device scope baseline
            # dal.
            value = MISSING_FROM_TABLE if configured and not is_device else MISSING_ENTIRELY
            message = (
                f"{rib} {prefix}: nakonfigurovana, ale neni v routovaci tabulce"
                if value == MISSING_FROM_TABLE
                else f"{rib} {prefix}: v baseline byla, v subjektu neni"
            )
            return Finding(
                Outcome.BROKEN,
                message,
                label=label,
                group=group,
                family=family,
                value=value,
                baseline_value=was,
                baseline=baseline,
            )

        now = _next_hop_text(subject)

        # Routa v tabulce bez hvezdicky forwarding nedela. Neni to totez co
        # "neni v tabulce": nedosazitelny next-hop routu z tabulky vyhodi
        # uplne, takze tenhle stav znamena, ze ji prebil jiny zdroj.
        # Default "aktivni" tady byl jedine misto v repu, kde by se regrese
        # collectoru precetla jako PASS misto jako chybejici kontrola.
        # `subject` vzdy pochazi z aktualniho collectoru (collectors/routes.py
        # vzdy nastavuje "active"), takze chybejici klic tady muze znamenat
        # jedine regresi collectoru - proto SKIP.
        if "active" not in subject:
            return Finding(
                Outcome.SKIP,
                f"{rib} {prefix}: mereni neobsahuje aktivitu routy",
                label=label,
                group=group,
                family=family,
                value="bez dat",
                baseline=baseline,
                subject=subject,
            )

        if not subject["active"]:
            was_active = baseline.get("active") if baseline else None

            if was_active is False:
                return Finding(
                    Outcome.OK,
                    f"{rib} {prefix}: neni aktivni, stejne jako v baseline",
                    label=label,
                    group=group,
                    family=family,
                    value=NOT_ACTIVE,
                    baseline_value=NOT_ACTIVE,
                    baseline=baseline,
                    subject=subject,
                )

            # Bez baseline neni z ceho poznat, ze neaktivni byla i predtim -
            # podle R-2 se nejednoznacnost na FAIL neeskaluje. Baseline, ktery
            # klic "active" nema, je tataz nejednoznacnost, jen z jineho
            # duvodu: neni to porucha mereni jako u `subject` vyse (ten vzdy
            # vyrabi aktualni collector), ale starsi artefakt, ktery o stavu
            # sveta mlci. Proto DEGRADED, ale s vlastni zpravou - jinak by
            # operator nepoznal, ktery z tech dvou duvodu nastal.

            # Symetricky s `was_active is False` vyse: pravdivostni test by
            # kazdou nebool hodnotu (napr. retezec "false") precetl jako
            # "forwardovala" a eskaloval nejednoznacnost na FAIL, coz R-2
            # zakazuje. Takova hodnota dnes nenastane - proto zprisneni, ne
            # oprava vady: R-2 ma platit konstrukci, ne argumentem o
            # nedosazitelnosti.
            if was_active is True:
                outcome = Outcome.BROKEN
                message = f"{rib} {prefix}: v baseline forwardovala, ted neni aktivni"
            elif baseline:
                outcome = Outcome.DEGRADED
                message = (
                    f"{rib} {prefix}: je v tabulce, ale neni aktivni; "
                    "baseline aktivitu neuvadi"
                )
            else:
                outcome = Outcome.DEGRADED
                message = f"{rib} {prefix}: je v tabulce, ale neni aktivni"
            return Finding(
                outcome,
                message,
                label=label,
                group=group,
                family=family,
                value=NOT_ACTIVE,
                baseline_value=was,
                baseline=baseline,
                subject=subject,
            )

        if was is not None and was != now:
            return Finding(
                Outcome.DEGRADED,
                f"{rib} {prefix}: next-hop se zmenil {was} -> {now}",
                label=label,
                group=group,
                family=family,
                value=now,
                baseline_value=was,
                baseline=baseline,
                subject=subject,
            )

        return Finding(
            Outcome.OK,
            f"{rib} {prefix}: {now}",
            label=label,
            group=group,
            family=family,
            value=now,
            baseline_value=was,
            baseline=baseline,
            subject=subject,
        )
