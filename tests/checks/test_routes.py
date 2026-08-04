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

CONFIGURED_TWO_RIBS = [
    {"rib": "inet.0", "prefix": "198.62.1.0/29", "next_hop": ["152.11.13.2"]},
    {
        "rib": "L3VPN-CPE13-NNI.inet6.0",
        "prefix": "2001:eeee::/64",
        "next_hop": ["2001:db8:11:13::b"],
    },
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


def _installed_two_ribs():
    return {
        "inet.0": {
            "198.62.1.0/29": {
                "next_hop": ["152.11.13.2"],
                "via": ["et-0/0/8.13"],
                "active": True,
            }
        },
        "L3VPN-CPE13-NNI.inet6.0": {
            "2001:eeee::/64": {
                "next_hop": ["2001:db8:11:13::b"],
                "via": ["et-0/0/8.113"],
                "active": True,
            }
        },
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

    assert findings[0].label == "inet.0 198.62.1.0/29"


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


def test_inactive_route_with_silent_baseline_is_degraded():
    """Baseline, ktery o aktivite mlci, je nejednoznacnost - podle R-2 se
    nejednoznacnost na FAIL neeskaluje.

    Zabiji mutanta: navrat defaultu `baseline.get("active", True)`. S nim se
    mlceni precte jako "forwardovala" a check vyda BROKEN, tedy tvrdy FAIL
    za neco, co neni dolozene.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _installed_without_active())
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "neni aktivni"


def test_silent_baseline_has_its_own_message():
    """Tri stavy baseline musi dat tri zpravy, ne dve.

    Zabiji mutanta: slouceni obou DEGRADED vetvi do jedne zpravy. Pak by
    mlcici baseline dostal formulaci urcenou pro "baseline vubec neni" a
    operator by z radku nepoznal, ktery z tech dvou duvodu nastal.

    Porovnava se CELA zprava: obe vetve sdileji prefix "je v tabulce, ale
    neni aktivni", takze assert na podretezec by je nerozlisil.
    """
    silent = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _installed_without_active())
    )
    missing = StaticRouteStatusCheck().run(_ctx(_installed_inactive()))

    assert silent[0].message == (
        "inet.0 198.62.1.0/29: je v tabulce, ale neni aktivni; "
        "baseline aktivitu neuvadi"
    )
    assert missing[0].message == "inet.0 198.62.1.0/29: je v tabulce, ale neni aktivni"


def test_static_routes_from_different_ribs_share_one_group():
    """Zabiji mutanta, ktery skupinu odvodi z RIB misto konstanty.

    Assert porovnava skupinu s presnou konstantou "Staticke routy", takze
    mutanta `group = rib` chyti i s jedinou RIB. Dve RUZNE RIB tu jsou z
    jineho duvodu: modeluji skutecny pripad, ktery skupiny zavedly - dve
    routy z ruznych RIB musi skoncit v JEDNE skupine, deleni po RIB uz nese
    popisek radku. Sourozenec test_label_carries_rib_and_prefix hlida
    popisek, tenhle skupinu. Tvar fixture i pocet vysledku (2) overen proti
    kodu 2026-08-03.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_two_ribs(), scope=_scope(CONFIGURED_TWO_RIBS))
    )

    assert len(findings) == 2
    assert {f.group for f in findings} == {"Staticke routy"}
    assert {f.label for f in findings} == {
        "inet.0 198.62.1.0/29",
        "L3VPN-CPE13-NNI.inet6.0 2001:eeee::/64",
    }


def _baseline_with_active(value):
    """Baseline s podstrcenou hodnotou 'active'.

    Zadna z techto hodnot dnes nenastane - collectors/routes.py:84 vyrabi
    skutecny bool a YAML round-tripuje bool jako bool. Testy hlidaji, ze
    R-2 plati konstrukci, ne argumentem o nedosazitelnosti.
    """
    routes = _installed()
    routes["inet.0"]["198.62.1.0/29"]["active"] = value
    return routes


def test_nonbool_truthy_active_does_not_escalate_to_fail():
    """Nejednoznacnost se podle R-2 na FAIL neeskaluje ani u nebool hodnot.

    Zabiji mutanta: navrat `if was_active is True:` na `if was_active:`.
    S nim je "false" jako neprazdny retezec pravdivy a check vyda BROKEN,
    tedy tvrdy FAIL za hodnotu, ktera ve skutecnosti tvrdi opak.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _baseline_with_active("false"))
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.DEGRADED


def test_falsy_nonbool_active_does_not_escalate_either():
    """Nula neaktivitu uvadi, takze uz vubec nesmi skoncit jako FAIL.

    Zabiji mutanta: `if was_active is not False:`, tedy blizky preklep
    vlastni opravy. S nim by 0 spadla do BROKEN.

    Navrat zmeny (`if was_active:`) tenhle test NEZABIJE - zmereno, 0 dava
    DEGRADED pred zmenou i po ni. Test tedy nechrani zmenu samotnou, ale
    jeji okoli; pojmenovava se to takhle schvalne, aby nikdo netvrdil vic,
    nez co mereni unese.
    """
    findings = StaticRouteStatusCheck().run(
        _ctx(_installed_inactive(), _baseline_with_active(0))
    )

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.DEGRADED


def test_deactivated_route_warns_on_own_row_and_sibling_stays_ok():
    """Deaktivovana routa varuje na svem radku a sourozence nestrhne.

    Od vlny 9 uz nedava SKIP: deaktivovany prvek konfigurace je sam o sobe
    nalez, takze sluzba s nim nesmi byt PASS. Co plati dal, je granularita -
    varovani je na jednom radku a zdrava routa vedle zustava OK.

    Treti routa (aktivni, ale chybejici v tabulce) je tu schvalne: bez ni
    by mutant `if route.get("active", True) is False:` -> `if True:` prosel
    beze zmeny vysledku - sourozenec s daty v subjektu skonci OK porad,
    protoze vetev deaktivace pro nej neni dosazitelna (subject neni None).
    Treti routa ma subject None, takze mutant, ktery oznaci za deaktivovanou
    i tuhle aktivni routu, by ji misto BROKEN vratil chybne jako DEGRADED -
    a tenhle test to zachyti.
    """
    scope = _scope(
        static_routes=[
            {"rib": "inet.0", "prefix": "10.0.0.0/8", "next_hop": ["1.1.1.1"],
             "active": False},
            {"rib": "inet.0", "prefix": "10.1.0.0/16", "next_hop": ["2.2.2.2"],
             "active": True},
            {"rib": "inet.0", "prefix": "10.2.0.0/16", "next_hop": ["3.3.3.3"],
             "active": True},
        ]
    )
    subject_routes = {
        "inet.0": {
            "10.1.0.0/16": {
                "next_hop": ["2.2.2.2"],
                "via": ["et-0/0/8.13"],
                "active": True,
            }
        }
    }
    findings = StaticRouteStatusCheck().run(
        _ctx(subject_routes, scope=scope)
    )
    by_label = {finding.label: finding for finding in findings}

    assert by_label["inet.0 10.0.0.0/8"].outcome is Outcome.DEGRADED
    assert by_label["inet.0 10.0.0.0/8"].value == "deaktivovana"
    assert by_label["inet.0 10.1.0.0/16"].outcome is Outcome.OK
    assert by_label["inet.0 10.2.0.0/16"].outcome is Outcome.BROKEN


def _scope_with_baseline_routes(subject_routes, baseline_routes):
    """Dva scopy - zamer subjektu a zamer baselinu.

    Baseline zamer nese `ctx.baseline_scope`, ne `ctx.baseline`: v `baseline`
    jsou namerena fakta, priznak deaktivace je v inventory. Test, ktery by
    priznak hledal ve faktech, by meril neco jineho, nez check cte.
    """
    return _scope(subject_routes), _scope(baseline_routes)


def test_route_deactivated_in_subject_but_active_in_baseline_is_broken():
    """V baselinu routa bezela, ted je vypnuta - migrace nedokoncena.

    Tohle je jediny radek tabulky, ktery u routy dava FAIL. Bez nej by
    nedokoncena migrace podprvku vypadala stejne jako vedomy dlouhodoby
    stav.
    """
    route = {"rib": "inet.0", "prefix": "10.0.0.0/8", "next_hop": ["1.1.1.1"]}
    scope, baseline_scope = _scope_with_baseline_routes(
        [{**route, "active": False}], [{**route, "active": True}]
    )
    ctx = CheckContext(
        scope=scope,
        subject={"routes": {}},
        baseline={"routes": {}},
        baseline_scope=baseline_scope,
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)
    by_label = {finding.label: finding for finding in findings}

    assert by_label["inet.0 10.0.0.0/8"].outcome is Outcome.BROKEN
    assert "v baseline bezela" in by_label["inet.0 10.0.0.0/8"].message


def test_route_deactivated_in_both_snapshots_is_degraded():
    """Vypnuta i predtim - porad nalez, jen tissi.

    Zabiji mutanta: navrat Outcome.OK pro tuhle dvojici. S nim by sluzba s
    dlouhodobe vypnutou routou byla PASS a jeji blok by se nerozbalil.
    """
    route = {"rib": "inet.0", "prefix": "10.0.0.0/8", "next_hop": ["1.1.1.1"]}
    scope, baseline_scope = _scope_with_baseline_routes(
        [{**route, "active": False}], [{**route, "active": False}]
    )
    ctx = CheckContext(
        scope=scope,
        subject={"routes": {}},
        baseline={"routes": {}},
        baseline_scope=baseline_scope,
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert findings[0].outcome is Outcome.DEGRADED


def test_route_active_now_deactivated_in_baseline_gets_no_deactivation_row():
    """Znovuzapnuta routa neni varovani - je to zlepseni (R-2).

    Radek 4 tabulky se u podprvku neuplatnuje: routa, ktera je ted aktivni,
    zadny deaktivovany prvek v konfiguraci nema. Jeji stav nese normalni
    stavovy radek, ne radek o deaktivaci.

    Zabiji mutanta: volani deactivation_outcome() i pro routu s
    `active is not False`. S nim by kazda znovuzapnuta routa pridala WARN na
    zdravou sluzbu.
    """
    route = {"rib": "inet.0", "prefix": "10.0.0.0/8", "next_hop": ["1.1.1.1"]}
    scope, baseline_scope = _scope_with_baseline_routes(
        [{**route, "active": True}], [{**route, "active": False}]
    )
    subject_routes = {
        "inet.0": {"10.0.0.0/8": {"next_hop": ["1.1.1.1"], "via": ["et-0/0/8.13"], "active": True}}
    }
    ctx = CheckContext(
        scope=scope,
        subject={"routes": subject_routes},
        baseline={"routes": subject_routes},
        baseline_scope=baseline_scope,
        config=default_config(),
    )

    findings = StaticRouteStatusCheck().run(ctx)

    assert findings[0].outcome is Outcome.OK
    assert "deaktivovan" not in findings[0].message


def test_deactivated_route_still_in_the_table_is_not_hidden_by_deactivation_branch():
    """Deaktivovana routa, ktera v tabulce presto je, nespadne do vetve
    deaktivace - jde normalni cestou a nese normalni stavovy radek.

    Konfigurace rika 'vypnuto', tabulka rika 'nainstalovana' - to je
    skutecny rozpor zameru se stavem a ma zustat viditelny se svym
    obvyklym stavem, ne za cenu radku 'routa je v konfiguraci deaktivovana'.
    Zabiji mutanta, ktery druhy konjunkt vetve deaktivace vypusti:
    `if deactivated and subject is None:` -> `if deactivated:`. Bez teto
    routy (deaktivovana, ale s datmi v subjektu) by takovy mutant prosel
    beze zmeny vysledku testu - s ni vetev deaktivace vrati DEGRADED misto
    OK, ktere normalni cesta vydava.
    """
    scope = _scope(
        static_routes=[
            {"rib": "inet.0", "prefix": "10.3.0.0/16", "next_hop": ["4.4.4.4"],
             "active": False},
        ]
    )
    subject_routes = {
        "inet.0": {
            "10.3.0.0/16": {
                "next_hop": ["4.4.4.4"],
                "via": ["et-0/0/8.13"],
                "active": True,
            }
        }
    }
    findings = StaticRouteStatusCheck().run(_ctx(subject_routes, scope=scope))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.outcome is Outcome.OK
    assert finding.value == "4.4.4.4"
