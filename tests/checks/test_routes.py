"""Testy checku statickych rout.

Check na XML nesaha - fakta se skladaji rucne, protoze prave kombinace
'nakonfigurovano ale nenainstalovano' se z jedne nahravky vytahnout neda.
"""

from __future__ import annotations

from migration_validator.checks.base import CheckContext
from migration_validator.checks.routes import StaticRouteStatusCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
from migration_validator.models.scope import Scope, ScopeKey, Selectors

CONFIGURED = [
    {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": ["152.11.13.2"]},
]


def _scope(static_routes=None) -> Scope:
    return Scope(
        id="svc:CPE13:Internet",
        kind="service",
        key=ScopeKey(description="CPE13", service_type="Internet"),
        selectors=Selectors(static_routes=list(static_routes or [])),
    )


def _ctx(subject_routes, baseline_routes=None, scope=None) -> CheckContext:
    return CheckContext(
        scope=scope or _scope(CONFIGURED),
        subject={"routes": subject_routes},
        baseline={"routes": baseline_routes} if baseline_routes is not None else None,
        config=default_config(),
    )


def _installed(next_hop="152.11.13.2"):
    return {
        "inet.0": {
            "198.62.1.0/29": {
                "next_hop": [next_hop],
                "via": ["et-0/0/8.13"],
                "active": True,
            }
        }
    }


def _installed_inactive(next_hop="152.11.13.2"):
    """Routa v tabulce je, ale hvezdicku nema - prebil ji jiny zdroj."""
    routes = _installed(next_hop)
    routes["inet.0"]["198.62.1.0/29"]["active"] = False
    return routes


def test_inactive_route_that_was_inactive_before_passes():
    """Stav se nezmenil, takze to neni nalez.

    Zabiji mutanta: vypusteni vetve `was_active is False`, tedy hlaseni
    nalezu i u routy, ktera nebyla aktivni uz v baseline.

    Vetev bez baseline (DEGRADED miste BROKEN) tenhle test nehlida - ta ma
    vlastni test test_inactive_route_without_baseline_is_degraded. Overeno
    mutaci 2026-07-31: `outcome = Outcome.BROKEN` natvrdo tenhle test prezije
    a shodi az ten druhy.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _installed_inactive())
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK


def test_route_that_stopped_being_active_is_broken():
    """Na starem zarizeni forwardovala, na novem uz ne.

    Zabiji mutanta: vynechani cteni klice "active" - bez nej je next-hop
    stejny, takze by check vratil OK.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _installed())
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "neni aktivni"


def test_inactive_route_without_baseline_is_degraded():
    """Bez baseline neni z ceho poznat, ze neaktivni byla i predtim.

    FAIL by tvrdil, ze se neco zhorsilo, a to doloneno neni - podle R-2 se
    nejednoznacnost na FAIL neeskaluje. SKIP by naopak znamenal, ze
    `evaluate --snapshot X` bez --baseline o neaktivni route mlci uplne.

    Zabiji mutanta: BROKEN misto DEGRADED v teto vetvi.
    """
    findings = StaticRouteStatusCheck().run(_ctx(_installed_inactive()))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "neni aktivni"


def test_route_that_became_active_passes():
    """Zlepseni neni nalez (R-2).

    Zabiji mutanta: porovnani na nerovnost misto na smer zmeny.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed(), _installed_inactive())
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK


def test_installed_route_passes():
    findings = StaticRouteStatusCheck().run(_ctx(_installed(), _installed()))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "152.11.13.2"


def test_configured_but_not_installed_is_broken():
    """Presne to, co laborka delala 2026-07-29: 5 statik v konfiguraci, 0 v tabulce."""
    findings = StaticRouteStatusCheck().run(_ctx({}, _installed()))

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "neni v tabulce"
    assert findings[0].baseline_value == "152.11.13.2"


def test_changed_next_hop_is_degraded_and_carries_the_old_value():
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed("152.11.13.9"), _installed("152.11.13.2"))
    )

    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "152.11.13.9"
    assert findings[0].baseline_value == "152.11.13.2"


def test_ecmp_next_hops_in_a_different_order_are_not_a_change():
    """AR-12: pri ECMP je hodnotou mnozina next-hopu, ne jejich poradi v XML.

    Junos poradi <nh> negarantuje ani mezi platformami, ani mezi verzemi -
    a check prochazi presne tu hranici (junos -> junos-evo). Bez setrizeni by
    tenhle par dal WARN 'next-hop se zmenil 152.11.13.9, 152.11.13.2 ->
    152.11.13.2, 152.11.13.9', tedy hlaseni o zmene, ktera nenastala.

    Fakta se skladaji rucne, protoze zadna nahravka multi-nh routu nema -
    kazde rt-entry v obou captureech nese presne jedno <to>. Editovat kvuli
    tomu fixture nelze, jsou to doslovne odposlechy z laborky.
    """
    def _ecmp(next_hops, vias):
        return {
            "inet.0": {
                "198.62.1.0/29": {
                    "next_hop": list(next_hops),
                    "via": list(vias),
                    "active": True,
                }
            }
        }

    findings = StaticRouteStatusCheck().run(
        _ctx(
            _ecmp(("152.11.13.9", "152.11.13.2"), ("et-0/0/8.14", "et-0/0/8.13")),
            _ecmp(("152.11.13.2", "152.11.13.9"), ("et-0/0/8.13", "et-0/0/8.14")),
        )
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "152.11.13.2, 152.11.13.9"
    assert findings[0].baseline_value == "152.11.13.2, 152.11.13.9"


def test_route_that_vanished_from_config_and_table_is_broken():
    """Routa vyrazena z konfigurace se do selektoru subjektu nedostane.

    Bez tretiho zdroje sjednoceni (mereni baseline) by se scope na jeji
    chybeni nikdy nezeptal a zmizela by beze stopy.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx({}, _installed(), scope=_scope([]))
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "chybi"


def test_new_route_passes():
    findings = StaticRouteStatusCheck().run(_ctx(_installed(), {}))

    assert findings[0].outcome is Outcome.OK
    assert findings[0].baseline_value is None


def test_family_comes_from_the_prefix_not_the_rib_name():
    """Bez rodiny by radek spadl do bezhlavickove sekce nad IPv4 i IPv6."""
    ctx = _ctx(
        {
            "inet6.0": {
                "2001:aaaa::/64": {
                    "next_hop": ["2001:abcd:11:13::b"],
                    "via": ["et-0/0/8.13"],
                    "active": True,
                }
            }
        },
        scope=_scope(
            [{"rib": "inet6.0", "prefix": "2001:aaaa::/64", "next_hop": []}]
        ),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert findings[0].family == 6


def test_family_survives_a_rib_name_that_does_not_say_the_family():
    """RIB jmeno nemusi rodinu vubec obsahovat (napr. VRF bez '.inet6.').

    Kdyby check cetl rodinu ze jmena RIB misto z prefixu, tenhle prefix by
    se pri hledani '6' ve jmene minul a spadl by do IPv4 nebo bezhlavickove
    sekce.
    """
    ctx = _ctx(
        {
            "L3VPN-CPE13-NNI": {
                "2001:aaaa::/64": {
                    "next_hop": ["2001:abcd:11:13::b"],
                    "via": ["et-0/0/8.13"],
                    "active": True,
                }
            }
        },
        scope=_scope(
            [{"rib": "L3VPN-CPE13-NNI", "prefix": "2001:aaaa::/64", "next_hop": []}]
        ),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert findings[0].family == 6


def test_label_carries_rib_and_prefix():
    """Identita jde do popisku, next-hop je hodnota - jinak by ZMENA vypsala par dvakrat."""
    findings = StaticRouteStatusCheck().run(_ctx(_installed()))

    assert findings[0].label == "Staticka routa (inet.0 198.62.1.0/29)"


def test_service_without_static_routes_gets_no_row():
    """R-1: chybejici konfigurace se v bloku neprojevi vubec."""
    findings = StaticRouteStatusCheck().run(_ctx({}, {}, scope=_scope([])))

    assert findings == []


def test_device_scope_does_not_claim_a_route_is_missing_from_the_table():
    """Device scope nezna zamer, takze 'neni v tabulce' rict nesmi (AR-17).

    Zabiji mutanta: odebrani 'and not is_device' z checks/routes.py:130.
    Aby ta vetev mela co rozhodovat, musi byt 'configured' pravda - proto se
    tu stavi scope s kind="device" A NEPRAZDNYMI static_routes. Puvodni test
    pouzival device_scope(), ktery ma vzdy prazdne selektory, takze
    'configured' bylo vzdy False a cela podminka nepravda bez ohledu na
    is_device.
    """
    scope = Scope(
        id="dev:172.20.20.4",
        kind="device",
        key=None,
        selectors=Selectors(static_routes=list(CONFIGURED)),
    )
    ctx = CheckContext(
        scope=scope,
        subject={"routes": {}},
        baseline={"routes": _installed()},
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "chybi", (
        "device scope ohlasil 'neni v tabulce' - to tvrdi, ze zna zamer, "
        "a ten v nem znat neni"
    )


def test_service_scope_does_claim_a_route_is_missing_from_the_table():
    """Protejsek predchoziho testu - bez nej by 'chybi' slo vratit vzdycky.

    Zabiji mutanta: zamena cele podminky na 'False' v checks/routes.py:130.
    """
    ctx = _ctx({}, _installed())

    findings = StaticRouteStatusCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "neni v tabulce"


def _installed_without_active():
    """Mereni bez klice 'active' - tvar, ktery by vyrobila regrese collectoru."""
    routes = _installed()
    del routes["inet.0"]["198.62.1.0/29"]["active"]
    return routes


def test_route_without_active_key_is_skipped():
    """Chybejici klic je chybejici kontrola, ne PASS.

    Zabíjí mutanta: `subject.get("active", True)`, tedy default "aktivni".
    S nim by check vydal OK a regrese collectoru by se cetla jako uspech.
    """
    findings = StaticRouteStatusCheck().run(_ctx(_installed_without_active()))

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.SKIP
