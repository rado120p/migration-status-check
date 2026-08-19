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

Check cte jen zaznamy s `protocol == "static"` (fakta) / `route_type ==
"static"` (zamer) - agregaty ma od 2026-08-19 QNH vlastni
`aggregate_route_status`. Klicovani `(rib, prefix)` se s per-hop zaznamy
nemeni: casteci deaktivace (nektere next-hopy vypnute, jine ne) zije uvnitr
jednoho zaznamu, ne jako druha identita.

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


def _flatten(
    routes: dict[str, Any] | None, protocol: str
) -> dict[tuple[str, str], dict[str, Any]]:
    # Chybejici klic 'protocol' je zaznam ze snapshotu pred schematem 10 -
    # tehdy se sbiraly jen statiky, takze default je 'static', ne chyba.
    return {
        (table, prefix): data
        for table, prefixes in (routes or {}).items()
        for prefix, data in prefixes.items()
        if str(data.get("protocol", "static")) == protocol
    }


def _hop_text(hop: dict[str, Any]) -> str:
    interface = hop.get("interface")
    return f"{hop['to']} via {interface}" if interface else str(hop["to"])


@register
class StaticRouteStatusCheck(Check):
    id = "static_route_status"
    title = "Stav statickych rout"
    label = "Staticka routa"
    mode = Mode.BOTH
    requires = ("routes",)
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        static_selectors = [
            route
            for route in ctx.scope.selectors.static_routes
            if route.get("route_type", "static") == "static"
        ]
        configured = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in static_selectors
        }
        # Chybejici klic 'active' znamena zamer od parseru pred vlnou 8.
        # Snapshot i inventory maji od te vlny schema 5, takze se takovy
        # zamer nenacte - default je tu jen proto, aby jednotkovy test
        # nemusel psat klic, ktery netestuje.
        #
        # Klicovani jen dvojici (rib, prefix) je bezpecne, ne opomenuti:
        # duplicitni identita s ruznymi priznaky by umlcela i tu aktivni
        # routu, ale parser dva zaznamy pro tentyz prefix nevydava. Od
        # 2026-08-19 QNH pro qualified next-hopy drzi jeden zaznam na
        # (rib, prefix) konstrukce `_static_routes_under` v parseru -
        # castecna deaktivace zije uvnitr `next_hops`, ne jako druha
        # identita.
        deactivated = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in static_selectors
            if route.get("active", True) is False
        }
        hops_by_identity = {
            (str(route.get("rib")), str(route.get("prefix"))): route.get("next_hops") or []
            for route in static_selectors
        }
        # Baseline ZAMER, ne baseline mereni. Priznak deaktivace je v
        # inventory, takze `ctx.baseline` (fakta) o nem nevi nic.
        # `ctx.baseline_scope` je None v behu bez baselinu i u nesparovane
        # sluzby - v obou pripadech je spravna odpoved "neni s cim
        # porovnat", ne "v baselinu byla aktivni".
        baseline_routes = [
            route
            for route in (
                ctx.baseline_scope.selectors.static_routes
                if ctx.baseline_scope is not None
                else []
            )
            if route.get("route_type", "static") == "static"
        ]
        baseline_deactivated = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in baseline_routes
            if route.get("active", True) is False
        }
        baseline_configured = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in baseline_routes
        }
        baseline_hops_by_identity = {
            (str(route.get("rib")), str(route.get("prefix"))): route.get("next_hops") or []
            for route in baseline_routes
        }
        subject = _flatten(ctx.subject.get("routes"), "static")
        baseline = _flatten((ctx.baseline or {}).get("routes"), "static")

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
                    hops=hops_by_identity.get(identity, ()),
                    baseline_hops=baseline_hops_by_identity.get(identity, ()),
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
        hops: tuple[dict[str, Any], ...] | list[dict[str, Any]],
        baseline_hops: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ) -> Finding:
        rib, prefix = identity
        label = f"{rib} {prefix}"
        group = "Staticke routy"
        family = prefix_family(prefix)
        was = _next_hop_text(baseline)

        inactive_hops = [h for h in hops if not h.get("active", True)]
        active_hops = [h for h in hops if h.get("active", True)]

        if hops and not active_hops and subject is None and not deactivated:
            # Zadny hop nema forwardovat - zamer je stejny jako u routy
            # deaktivovane cele (vetev nize), jen na urovni next-hopu.
            # Absence v tabulce je tu ocekavana, ne rozpor - nejde tedy o
            # BROKEN vetev 'neni v tabulce', ale o informacni stav pres
            # sdilenou deactivation_outcome semantiku.
            if baseline_hops:
                baseline_all_hops_off = all(
                    not h.get("active", True) for h in baseline_hops
                )
            else:
                baseline_all_hops_off = None
            outcome = deactivation_outcome(True, baseline_all_hops_off)
            message = (
                f"{rib} {prefix}: vsechny next-hopy jsou deaktivovane"
                + (" - migrace nedokoncena" if outcome is Outcome.BROKEN else "")
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
            outcome, message = self._annotate_inactive_hops(
                Outcome.DEGRADED,
                f"{rib} {prefix}: next-hop se zmenil {was} -> {now}",
                inactive_hops,
                baseline_hops,
            )
            return Finding(
                outcome,
                message,
                label=label,
                group=group,
                family=family,
                value=now,
                baseline_value=was,
                baseline=baseline,
                subject=subject,
            )

        outcome, message = self._annotate_inactive_hops(
            Outcome.OK,
            f"{rib} {prefix}: {now}",
            inactive_hops,
            baseline_hops,
        )
        return Finding(
            outcome,
            message,
            label=label,
            group=group,
            family=family,
            value=now,
            baseline_value=was,
            baseline=baseline,
            subject=subject,
        )

    @staticmethod
    def _annotate_inactive_hops(
        outcome: Outcome,
        message: str,
        inactive_hops: list[dict[str, Any]],
        baseline_hops: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    ) -> tuple[Outcome, str]:
        """Zdravy/zmeneny radek s deaktivovanym next-hopem se nesmi tvarit
        zdrave (R-2 to nezakazuje - deaktivovany prvek je nalez sam o sobe,
        stejne jako u cele routy). Kazdy deaktivovany next-hop eskaluje
        vysledek radku pres sdilenou `deactivation_outcome`; vyhrava
        nejhorsi (BROKEN > DEGRADED > puvodni vysledek), do zpravy se
        pripoji vypis deaktivovanych hopu a jedna souhrnna pripominka.
        """
        if not inactive_hops:
            return outcome, message

        baseline_hop_map = {
            (h.get("to"), h.get("interface")): h for h in baseline_hops
        }
        rank = {Outcome.OK: 0, Outcome.DEGRADED: 1, Outcome.BROKEN: 2}
        worst = outcome
        newly_deactivated = False
        same_as_baseline = False
        for hop in inactive_hops:
            baseline_hop = baseline_hop_map.get((hop.get("to"), hop.get("interface")))
            hop_baseline_off = (
                not baseline_hop.get("active", True) if baseline_hop is not None else None
            )
            hop_outcome = deactivation_outcome(True, hop_baseline_off)
            if hop_baseline_off is False:
                newly_deactivated = True
            elif hop_baseline_off is True:
                same_as_baseline = True
            if hop_outcome is not None and rank[hop_outcome] > rank[worst]:
                worst = hop_outcome

        message += "; deaktivovany next-hop: " + ", ".join(
            _hop_text(h) for h in inactive_hops
        )
        if worst is Outcome.BROKEN or newly_deactivated:
            message += " - migrace nedokoncena"
        elif same_as_baseline:
            message += " (stejne jako v baseline)"

        return worst, message
