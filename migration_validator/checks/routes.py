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
from migration_validator.checks.registry import register
from migration_validator.models.result import Finding, Outcome, Severity

MISSING_FROM_TABLE = "neni v tabulce"
MISSING_ENTIRELY = "chybi"


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
    ) -> Finding:
        rib, prefix = identity
        label = f"{self.label} ({rib} {prefix})"
        family = prefix_family(prefix)
        was = _next_hop_text(baseline)

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
                family=family,
                value=value,
                baseline_value=was,
                baseline=baseline,
            )

        now = _next_hop_text(subject)

        if was is not None and was != now:
            return Finding(
                Outcome.DEGRADED,
                f"{rib} {prefix}: next-hop se zmenil {was} -> {now}",
                label=label,
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
            family=family,
            value=now,
            baseline_value=was,
            baseline=baseline,
            subject=subject,
        )
