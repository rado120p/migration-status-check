"""Checky statickych a agregatnich rout.

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
from dataclasses import replace
from typing import Any

from migration_validator.checks.base import Check, CheckContext, Mode
from migration_validator.checks.baseline import suffix, unchanged_or
from migration_validator.checks.deactivation import deactivation_outcome
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

MISSING_FROM_TABLE = "neni v tabulce"
MISSING_ENTIRELY = "chybi"
NOT_ACTIVE = "neni aktivni"
IN_TABLE = "v tabulce"


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


def _presence_text(data: dict[str, Any] | None) -> str | None:
    """Baseline hodnota agregatu v reci pritomnosti, ne next-hopu.

    Agregat next-hopy nema, takze _next_hop_text by vratil "-" a report
    by proti value "v tabulce" tiskl falesne "bylo -" na kazdem
    nezmenenem radku. None = v baseline zaznam neni (view.py pak resi
    "bez baseline" sam).
    """
    if data is None:
        return None
    return IN_TABLE if data.get("active", True) else NOT_ACTIVE


def _hop_text(hop: dict[str, Any]) -> str:
    interface = hop.get("interface")
    return f"{hop['to']} via {interface}" if interface else str(hop["to"])


def _presence_finding(
    identity: tuple[str, str],
    subject: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    deactivated: bool,
    baseline_deactivated: bool | None,
    label_prefix: str,
    group: str,
    value_ok: str,
    baseline_value: str | None,
    ctx: CheckContext,
) -> Finding:
    """Vetve sdilene StaticRouteStatusCheck a AggregateRouteStatusCheck.

    Kryje sjednoceni tri zdroju a chybejici/deaktivovanou routu -
    identicke pro obe rouceni, protoze ani jedno z toho nezavisi na
    next-hopu. Vraci vzdy Finding (ne None): rozliseni ZMENA vetve (was
    != now next-hop), ktera existuje jen u statik, dela volajici
    _finding sam PRED timhle volanim - tahle funkce uz jen skladá OK
    radek. Kdyby tu ZMENA zustala jako "vrat None a nech volajiciho
    dodelat", agregat (bez ZMENA vetve, next-hop nema) by mohl dostat
    None do sveho `list[Finding]` a spadnout do siroke `except Exception`
    v run_check pri sestavovani CheckResultu - proto je bezpecnejsi mit
    tu funkci totalni.

    `label_prefix` (check.label, napr. "Agregatni routa") se do popisku
    radku nepromita - `findings[0].label == "inet.0 prefix"` je zamrzly
    kontrakt statickeho checku (test_routes.py). Parametr drzi jen
    stejny volaci tvar z obou checku.
    """
    rib, prefix = identity
    label = f"{rib} {prefix}"
    family = prefix_family(prefix)
    # baseline_value dodava volajici, protoze musi mluvit stejnou reci
    # jako `value` sve strany: statika mnozinou next-hopu, agregat
    # pritomnosti ("v tabulce"). Kdyby se tu pocital z next-hopu pro oba,
    # nezmeneny agregat by dostal baseline_value "-" proti value
    # "v tabulce" a report by na kazdy radek tiskl falesne "bylo -"
    # (view.py tiskne sufix pri baseline_value != value).
    was = baseline_value

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
        # (AR-17). Rozliseni je podle toho, jestli je co srovnavat s
        # baselinem, ne podle scope: baseline zaznam existuje -> chybi
        # proti baselinu; baseline zaznam neni -> jen konfigurace tvrdi,
        # ze routa ma byt v tabulce, a neni (plati i v device scope, ktery
        # zamer nezna, ale tady jde jen o to, co rika samotna tabulka).
        if baseline is not None:
            value = MISSING_ENTIRELY
            message = f"{rib} {prefix}: v baseline byla, v subjektu neni"
            outcome = Outcome.BROKEN
        else:
            value = MISSING_FROM_TABLE
            # same=True: `baseline is None` tady znamena "v baseline
            # tabulce nebyla" - unchanged_or sam odmitne UNCHANGED, kdyz
            # baseline chybi nebo collector routes selhal.
            outcome = unchanged_or(Outcome.BROKEN, ctx, "routes", same=True)
            message = (
                f"{rib} {prefix}: nakonfigurovana, ale neni v routovaci tabulce"
                f"{suffix(outcome)}"
            )
        return Finding(
            outcome,
            message,
            label=label,
            group=group,
            family=family,
            value=value,
            baseline_value=(MISSING_FROM_TABLE if outcome is Outcome.UNCHANGED else was),
            baseline=baseline,
        )

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
        # podle R-2 se nejednoznacnost na FAIL neeskaluje.
        baseline_value = was
        if was_active is True:
            outcome = Outcome.BROKEN
            message = f"{rib} {prefix}: v baseline forwardovala, ted neni aktivni"
        elif baseline:
            outcome = Outcome.DEGRADED
            message = (
                f"{rib} {prefix}: je v tabulce, ale neni aktivni; "
                "baseline aktivitu neuvadi"
            )
            # Baseline zaznam existuje, ale klic "active" ne - fabulovat
            # z neho "bylo X" by predstiralo znalost, kterou mereni
            # nenese (R-2 pro baseline_value, ne jen pro outcome).
            baseline_value = None
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
            baseline_value=baseline_value,
            baseline=baseline,
            subject=subject,
        )

    if baseline is not None and baseline.get("active") is False:
        # Reaktivace: subjekt forwarduje, ale v baselinu nebyl aktivni.
        # Zlepseni je RECOVERED, ne varovani (R-2) - stejny princip jako
        # u deaktivace v deactivation.py, jen tady se to tyka aktivity,
        # ne konfiguracni deaktivace. baseline_value se tu nepocita z
        # `was` (next-hop text / pritomnost), protoze "co bylo" je tady
        # "nebylo aktivni", ne minula hodnota next-hopu/pritomnosti.
        return Finding(
            Outcome.RECOVERED,
            f"{rib} {prefix}: {value_ok} (v baseline nebyla aktivni)",
            label=label,
            group=group,
            family=family,
            value=value_ok,
            baseline_value=NOT_ACTIVE,
            baseline=baseline,
            subject=subject,
        )

    # Sem se dostane jen "routa je v tabulce a aktivni" - u statiky s
    # `was != now` uz vetev ZMENA odbavil volajici (_finding) pred timhle
    # volanim, takze tady zbyva jen OK.
    return Finding(
        Outcome.OK,
        f"{rib} {prefix}: {value_ok}",
        label=label,
        group=group,
        family=family,
        value=value_ok,
        baseline_value=was,
        baseline=baseline,
        subject=subject,
    )


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
                    subject=subject.get(identity),
                    baseline=baseline.get(identity),
                    deactivated=identity in deactivated,
                    baseline_deactivated=(
                        identity in baseline_deactivated
                        if identity in baseline_configured
                        else None
                    ),
                    hops=hops_by_identity.get(identity, ()),
                    baseline_hops=baseline_hops_by_identity.get(identity, ()),
                    ctx=ctx,
                )
            )
        return findings

    def _finding(
        self,
        identity: tuple[str, str],
        subject: dict[str, Any] | None,
        baseline: dict[str, Any] | None,
        deactivated: bool,
        baseline_deactivated: bool | None,
        hops: tuple[dict[str, Any], ...] | list[dict[str, Any]],
        baseline_hops: tuple[dict[str, Any], ...] | list[dict[str, Any]],
        ctx: CheckContext,
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

        now = _next_hop_text(subject)
        # Aktivni tabulka = subject existuje, ma klic "active" a je True.
        # Presne tenhle stav ma next-hop anotaci (viz nize) - vsechny ostatni
        # vetve (chybi/deaktivovana/skip/neaktivni, sdilene s agregatem pres
        # _presence_finding) ji nemaji, protoze next-hop tam nic nerika.
        reached_active_table = (
            subject is not None and "active" in subject and subject["active"]
        )

        # ZMENA se resi tady, ne v _presence_finding: ta funkce je totalni
        # (vzdy vraci Finding, nikdy None) prave proto, aby ji smel volat i
        # AggregateRouteStatusCheck, ktery zadnou ZMENA vetev nema (agregat
        # next-hop nenese, `was` a `now` mu vzdy vyjdou stejne).
        if reached_active_table and was is not None and was != now:
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

        presence = _presence_finding(
            identity,
            subject=subject,
            baseline=baseline,
            deactivated=deactivated,
            baseline_deactivated=baseline_deactivated,
            label_prefix=self.label,
            group=group,
            value_ok=now or "",
            baseline_value=was,
            ctx=ctx,
        )

        if not reached_active_table:
            return presence
        outcome, message = self._annotate_inactive_hops(
            presence.outcome, presence.message, inactive_hops, baseline_hops
        )
        return replace(presence, outcome=outcome, message=message)

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
        # RECOVERED sedi na stejnou urovni jako OK: deaktivovany next-hop
        # eskaluje z obou stejne, worse vyhrava (DEGRADED/BROKEN nad
        # RECOVERED stejne jako nad OK).
        rank = {
            Outcome.OK: 0,
            Outcome.RECOVERED: 0,
            Outcome.DEGRADED: 1,
            Outcome.BROKEN: 2,
        }
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


@register
class AggregateRouteStatusCheck(Check):
    """Agregat nema next-hop - porovnava se pritomnost a aktivita.

    Sdili se StaticRouteStatusCheck sjednoceni tri zdroju i vetve
    chybi/deaktivovana; nesdili porovnani next-hopu, protoze zadny neni.
    Zmizely zakaznicky agregat po migraci je signal vypadku - proto
    CRITICAL jako u statik.
    """

    id = "aggregate_route_status"
    title = "Stav agregatnich rout"
    label = "Agregatni routa"
    mode = Mode.BOTH
    requires = ("routes",)
    default_severity = Severity.CRITICAL

    def run(self, ctx: CheckContext) -> list[Finding]:
        selected = [
            route
            for route in ctx.scope.selectors.static_routes
            if route.get("route_type", "static") == "aggregate"
        ]
        configured = {
            (str(route.get("rib")), str(route.get("prefix"))) for route in selected
        }
        deactivated = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in selected
            if route.get("active", True) is False
        }
        baseline_routes = (
            ctx.baseline_scope.selectors.static_routes
            if ctx.baseline_scope is not None
            else []
        )
        baseline_selected = [
            route
            for route in baseline_routes
            if route.get("route_type", "static") == "aggregate"
        ]
        baseline_deactivated = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in baseline_selected
            if route.get("active", True) is False
        }
        baseline_configured = {
            (str(route.get("rib")), str(route.get("prefix")))
            for route in baseline_selected
        }
        subject = _flatten(ctx.subject.get("routes"), "aggregate")
        baseline = _flatten((ctx.baseline or {}).get("routes"), "aggregate")

        return [
            _presence_finding(
                identity,
                subject=subject.get(identity),
                baseline=baseline.get(identity),
                deactivated=identity in deactivated,
                baseline_deactivated=(
                    identity in baseline_deactivated
                    if identity in baseline_configured
                    else None
                ),
                label_prefix=self.label,
                group="Agregatni routy",
                value_ok=IN_TABLE,
                baseline_value=_presence_text(baseline.get(identity)),
                ctx=ctx,
            )
            for identity in sorted(configured | set(subject) | set(baseline))
        ]
