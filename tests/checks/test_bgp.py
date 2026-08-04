from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.bgp import (
    PREFIX_KEYS,
    BgpPrefixCountsCheck,
    BgpSessionStateCheck,
    peer_family,
)
from migration_validator.config import CheckConfig, default_config
from migration_validator.models.result import Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors


def _scope_of(bgp_neighbors, bgp_neighbors_inactive):
    return Scope(
        id="svc:L3VPN-CPE13-NNI:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"],
            bgp_neighbors=list(bgp_neighbors),
            bgp_neighbors_inactive=list(bgp_neighbors_inactive),
        ),
    )


def _ctx(
    subject,
    baseline=None,
    config=None,
    bgp_neighbors=None,
    bgp_neighbors_inactive=None,
    baseline_neighbors=None,
    baseline_neighbors_inactive=None,
):
    """Baseline ZAMER se predava sem, ne prirazenim po konstrukci.

    CheckContext neni frozen, takze `ctx.baseline_scope = ...` by proslo, ale
    test, ktery si context prestavuje az po sestaveni, obchazi tvar, ktery
    engine skutecne stavi.
    """
    scope = _scope_of(
        ["198.11.13.2"] if bgp_neighbors is None else bgp_neighbors,
        bgp_neighbors_inactive or [],
    )
    baseline_scope = (
        _scope_of(baseline_neighbors or [], baseline_neighbors_inactive or [])
        if baseline_neighbors is not None or baseline_neighbors_inactive is not None
        else None
    )
    return CheckContext(
        scope=scope,
        subject=subject,
        baseline=baseline,
        baseline_scope=baseline_scope,
        config=config or default_config(),
        failed_collectors={},
    )


def _peer(
    state="Established",
    rib="inet.0",
    received=14,
    accepted=14,
    advertised=3,
    active=None,
    suppressed=0,
):
    if active is None:
        active = accepted
    return {
        "state": state,
        "routing_instance": "L3VPN-CPE13-NNI",
        "ribs": {
            rib: {
                "received": received,
                "accepted": accepted,
                "advertised": advertised,
                "active": active,
                "suppressed": suppressed,
            }
        },
    }


def _by_label(results, label):
    """Vybere finding podle labelu - po rozdeleni na RIB uz nejde spolehnout
    na to, ze hledany radek je na indexu 0."""
    matches = [result for result in results if result.label == label]
    assert len(matches) == 1, f"ocekavan 1 finding s labelem {label!r}, je jich {len(matches)}"
    return matches[0]


def test_session_findings_carry_peer_family():
    """Rodina se odvozuje z adresy peera, ne ze jmena RIB.

    Argument bgp_neighbors musi odpovidat peerum v subjektu: default
    `_ctx()` nese 198.11.13.2, ktery by od vlny 9 pridal radek
    "nakonfigurovan, ale session neexistuje" a do tohoto testu nepatri.
    """
    subject = {
        "bgp": {
            "152.11.13.2": _peer(),
            "2001:abcd:11:13::b": {
                "state": "Established",
                "routing_instance": None,
                "ribs": {"inet6.0": {"received": 0, "accepted": 0, "advertised": 1,
                                     "active": 0, "suppressed": 0}},
            },
        }
    }

    findings = run_check(
        BgpSessionStateCheck(),
        _ctx(subject, bgp_neighbors=["152.11.13.2", "2001:abcd:11:13::b"]),
    )
    by_family = {finding.family for finding in findings}

    assert by_family == {4, 6}
    assert {finding.label for finding in findings} == {
        "BGP status (152.11.13.2)",
        "BGP status (2001:abcd:11:13::b)",
    }
    assert all(finding.value == "Established" for finding in findings)


def test_established_passes():
    result = run_check(BgpSessionStateCheck(), _ctx({"bgp": {"198.11.13.2": _peer()}}))[0]
    assert result.status is Status.PASS
    assert result.label == "BGP status (198.11.13.2)"
    assert result.family == 4
    assert result.value == "Established"


def test_active_state_fails():
    ctx = _ctx({"bgp": {"198.11.13.2": _peer(state="Active")}})
    result = run_check(BgpSessionStateCheck(), ctx)[0]
    assert result.status is Status.FAIL
    assert "Active" in result.message


def test_service_with_no_peers_at_all_skips():
    """Hlaska 'nema zadne BGP peery' plati, jen kdyz zadny peer opravdu neni.

    Puvodni verze tohoto testu mela pres default `_ctx()` ve scope
    nakonfigurovaneho peera a presto tvrdila, ze zadny neni - byla to prave
    ta lez, kvuli ktere bod 15 vznikl.
    """
    result = run_check(BgpSessionStateCheck(), _ctx({"bgp": {}}, bgp_neighbors=[]))[0]
    assert result.status is Status.SKIP
    assert "BGP" in result.message


def test_deactivated_peer_without_session_warns():
    """Deaktivovany peer bez session se v reportu objevi jako WARN.

    Bez teto vetve by z reportu zmizel uplne a operator by nepoznal, ze
    sluzba takoveho peera v konfiguraci vubec ma. Od vlny 9 uz to neni SKIP:
    deaktivovany prvek konfigurace je sam o sobe nalez.
    """
    ctx = _ctx(
        {"bgp": {"198.11.13.2": _peer(state="Established")}},
        bgp_neighbors=["198.11.13.2"],
        bgp_neighbors_inactive=["198.11.13.9"],
    )
    results = run_check(BgpSessionStateCheck(), ctx)
    by_label = {result.label: result for result in results}

    assert by_label["BGP status (198.11.13.9)"].status is Status.WARN
    assert by_label["BGP status (198.11.13.2)"].status is Status.PASS


def test_service_with_only_deactivated_peers_warns_per_peer():
    """Jediny peer sluzby je deaktivovany - nesmi to spadnout do 'zadny peer'.

    Zabiji mutanta: ponechany predcasny navrat `if not peers:`.
    """
    ctx = _ctx(
        {"bgp": {}},
        bgp_neighbors=[],
        bgp_neighbors_inactive=["198.11.13.9"],
    )
    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.WARN
    assert results[0].value == "deaktivovan"


def test_peer_deactivated_in_subject_but_active_in_baseline_is_fail():
    """V baselinu peer bezel, ted je vypnuty - migrace nedokoncena."""
    ctx = _ctx(
        {"bgp": {}},
        baseline={"bgp": {}},
        bgp_neighbors=[],
        bgp_neighbors_inactive=["198.11.13.9"],
        baseline_neighbors=["198.11.13.9"],
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert "migrace nedokoncena" in results[0].message


def test_peer_deactivated_in_both_snapshots_is_warn():
    """Vypnuty i predtim - porad nalez, jen tissi.

    Zabiji mutanta: navrat Outcome.OK pro tuhle dvojici. S nim by sluzba s
    dlouhodobe vypnutym peerem byla PASS.
    """
    ctx = _ctx(
        {"bgp": {}},
        baseline={"bgp": {}},
        bgp_neighbors=[],
        bgp_neighbors_inactive=["198.11.13.9"],
        baseline_neighbors_inactive=["198.11.13.9"],
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.WARN


def test_deactivated_peer_with_live_session_is_reported_normally():
    """Deaktivovany peer, ktery presto bezi, neni SKIP - je to rozpor.

    Symetricke se statikami: konfigurace rika 'vypnuto', tabulka rika
    'bezi', a prave to ma byt videt.

    Subject se stavi pres `Scope.select()`, ne rucne. Rucne postaveny
    subject obchazi prave to misto, kde se session ztracela, takze by test
    prosel i nad rozbitou cestou.

    Zabiji mutanta: vyber `bgp` v `Scope.select()` filtrovany jen pres
    `bgp_neighbors` (bez `bgp_neighbors_inactive`). Session se pak ke checku
    nedostane, peer propadne vetvi `inactive` a dostane WARN 'deaktivovan'
    misto PASS - od vlny 9 uz tahle vetev nikdy nevydava SKIP.
    """
    scope = Scope(
        id="svc:L3VPN-CPE13-NNI:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"],
            bgp_neighbors=[],
            bgp_neighbors_inactive=["198.11.13.9"],
        ),
    )
    facts = {"bgp": {"198.11.13.9": _peer(state="Established")}}
    ctx = CheckContext(
        scope=scope,
        subject=scope.select(facts),
        baseline=None,
        config=default_config(),
        failed_collectors={},
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.PASS
    assert results[0].label == "BGP status (198.11.13.9)"
    assert results[0].value == "Established"


def test_peer_active_now_deactivated_in_baseline_with_live_session_gets_no_deactivation_row():
    """Znovuzapnuty peer neni varovani - je to zlepseni (R-2).

    Radek 4 tabulky se u podprvku neuplatnuje: peer, ktery je ted aktivni
    v konfiguraci, zadny deaktivovany prvek nenese. Jeho stav nese normalni
    stavovy radek (tady s bezici session), ne radek o deaktivaci.

    Zabiji mutanta: volani deactivation_outcome() i pro peera, ktery je
    aktivni v subjektu, ale deaktivovany v baselinu. S nim by kazdy
    znovuzapnuty peer pridal WARN navic ke svemu normalnimu radku.
    """
    ctx = _ctx(
        {"bgp": {"198.11.13.2": _peer(state="Established")}},
        baseline={"bgp": {}},
        bgp_neighbors=["198.11.13.2"],
        bgp_neighbors_inactive=[],
        baseline_neighbors=[],
        baseline_neighbors_inactive=["198.11.13.2"],
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.PASS
    assert "deaktivovan" not in results[0].message


def test_peer_active_now_deactivated_in_baseline_without_session_gets_no_deactivation_row():
    """Totez jako vyse, ale peer nema session vubec - jde do vetve 'bez
    session', ne do vetve deaktivace.

    Zabiji stejneho mutanta jako test vyse, ale pro peera bez zivych dat -
    kdyby se deactivation_outcome() volalo i pro takove peery s
    subject_off=False, dostal by druhy WARN radek navic k tomu, ktery uz
    vydava vetev 'bez session'.
    """
    ctx = _ctx(
        {"bgp": {}},
        baseline={"bgp": {}},
        bgp_neighbors=["198.11.13.2"],
        bgp_neighbors_inactive=[],
        baseline_neighbors=[],
        baseline_neighbors_inactive=["198.11.13.2"],
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert "deaktivovan" not in results[0].message


def test_state_change_to_established_is_pass_not_warn():
    """Rozhodnuti R-2: zlepseni neni varovani.

    Tahle vetev je dosazitelna jen kdyz je stav Established (horsi stavy
    odchazi drive), takze pokryva presne a pouze pripad, kdy se relace
    behem migrace zlepsila. WARN na zdrave sluzbe je falesny poplach -
    presne ten trvaly oranzovy svit, proti kteremu se rozhodovalo v Tasku 7.

    Zmena nezmizi: hlaska ji pojmenuje a sloupec ZMENA pise 'bylo Active',
    takze nezdrava baseline zustane videt.
    """
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(state="Established")}},
        baseline={"bgp": {"198.11.13.2": _peer(state="Active")}},
    )
    result = run_check(BgpSessionStateCheck(), ctx)[0]

    assert result.status is Status.PASS
    assert "Active" in result.message and "Established" in result.message
    assert result.baseline_value == "Active"


def test_changed_session_carries_baseline_value():
    """Regrese na live nalez: svc:et-0/0/10.0:IPVPN hlasilo v souhrnu 'stav
    se zmenil Connect -> Established', ale radek bloku ukazoval 'bez
    baseline' - protoze DEGRADED vetev nastavovala jen `baseline` (dict
    pro JSON), ne `baseline_value` (pole, ze ktereho report sklada sloupec
    ZMENA). Bez `baseline_value` ZMENA cte baseline_value is None jako
    'bez baseline', presestoze check presne vi, jaky stav byl predtim.
    """
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(state="Established")}},
        baseline={"bgp": {"198.11.13.2": _peer(state="Connect")}},
    )
    result = run_check(BgpSessionStateCheck(), ctx)[0]

    assert result.status is Status.PASS
    assert result.baseline_value == "Connect"


def test_broken_session_carries_baseline_value():
    """Druha polovina te same regrese, kterou resi test vyse - a ta horsi.

    Vetev pro spadlou relaci vytvarela Finding drive, nez se baseline stav
    vubec dohledal, takze report u nej psal 'bez baseline'. Sloupec ZMENA
    vznikl proto, aby byla regrese videt; zamlcoval ji presne u peeru, ktery
    spadl. Doloženo na runs/ipv6: peer 152.11.13.2 je Established v pre a
    Connect v post snapshotu.
    """
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(state="Connect")}},
        baseline={"bgp": {"198.11.13.2": _peer(state="Established")}},
    )
    result = run_check(BgpSessionStateCheck(), ctx)[0]

    assert result.status is Status.FAIL
    assert result.baseline_value == "Established"


def test_peer_missing_in_baseline_is_evaluated_as_state_only():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer()}},
        baseline={"bgp": {}},
    )
    assert run_check(BgpSessionStateCheck(), ctx)[0].status is Status.PASS


def test_prefix_counts_within_tolerance_pass():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=13, accepted=13)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    result = _by_label(results, "received-prefix-count")
    assert result.status is Status.PASS


def test_prefix_counts_below_tolerance_warn():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=5, accepted=5)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    result = _by_label(results, "received-prefix-count")
    assert result.status is Status.WARN
    assert "received" in result.message
    assert result.baseline == {"received": 14}
    assert result.subject == {"received": 5}


def test_prefix_tolerance_is_configurable():
    config = CheckConfig({"bgp_prefix_counts": {"tolerance_percent": -90}})
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=5, accepted=5)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
        config=config,
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    result = _by_label(results, "received-prefix-count")
    assert result.status is Status.PASS


def test_prefix_counts_without_baseline_skips():
    result = run_check(BgpPrefixCountsCheck(), _ctx({"bgp": {"198.11.13.2": _peer()}}))[0]
    assert result.status is Status.SKIP
    assert "baseline" in result.message


def test_prefix_counts_peer_missing_in_baseline_skips_that_peer():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer()}},
        baseline={"bgp": {}},
    )
    result = run_check(BgpPrefixCountsCheck(), ctx)[0]
    assert result.status is Status.SKIP
    assert "198.11.13.2" in result.message


def test_prefix_counts_rib_missing_in_baseline_skips_that_rib():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(rib="inet6.0")}},
        baseline={"bgp": {"198.11.13.2": _peer(rib="inet.0")}},
    )
    result = run_check(BgpPrefixCountsCheck(), ctx)[0]
    assert result.status is Status.SKIP
    assert "inet6.0" in result.message
    assert result.label == "BGP prefixy (inet6.0)"


def test_prefix_growth_is_not_a_problem():
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer(received=40, accepted=40)}},
        baseline={"bgp": {"198.11.13.2": _peer(received=14, accepted=14)}},
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    result = _by_label(results, "received-prefix-count")
    assert result.status is Status.PASS


def test_prefix_counts_are_reported_per_rib_not_summed():
    """Kdyby se countery pri porovnani zase scitaly pres RIB, pokles 40
    prefixu v inet6.0 vyvazeny narustem 40 v inet.0 by presel jako OK -
    tenhle test na takovy revert musi spadnout."""
    ctx = _ctx(
        subject={
            "bgp": {
                "198.11.13.2": {
                    "state": "Established",
                    "routing_instance": None,
                    "ribs": {
                        "inet.0": {
                            "received": 54, "accepted": 54, "advertised": 3,
                            "active": 54, "suppressed": 0,
                        },
                        "inet6.0": {
                            "received": 0, "accepted": 0, "advertised": 1,
                            "active": 0, "suppressed": 0,
                        },
                    },
                }
            }
        },
        baseline={
            "bgp": {
                "198.11.13.2": {
                    "state": "Established",
                    "routing_instance": None,
                    "ribs": {
                        "inet.0": {
                            "received": 14, "accepted": 14, "advertised": 3,
                            "active": 14, "suppressed": 0,
                        },
                        "inet6.0": {
                            "received": 40, "accepted": 40, "advertised": 1,
                            "active": 40, "suppressed": 0,
                        },
                    },
                }
            }
        },
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    labels = {result.label: result for result in results}
    assert labels["received-prefix-count"].status is Status.WARN
    assert "inet6.0" in labels["received-prefix-count"].message


def test_prefix_finding_family_is_derived_from_peer_address_not_rib_name():
    ctx = _ctx(
        subject={
            "bgp": {
                "198.11.13.2": _peer(rib="inet.0"),
                "2001:db8:11:13::b": _peer(rib="inet6.0"),
            }
        },
        baseline={
            "bgp": {
                "198.11.13.2": _peer(rib="inet.0"),
                "2001:db8:11:13::b": _peer(rib="inet6.0"),
            }
        },
    )
    results = run_check(BgpPrefixCountsCheck(), ctx)
    families = {result.family for result in results}
    assert families == {4, 6}


def test_report_covers_exactly_these_prefix_counters():
    """Pripina PREFIX_KEYS, protoze jejich zmena je jinak tichá.

    Overeno mutaci: vyhozeni 'active' i 'suppressed' z PREFIX_KEYS proslo
    celou sadou - oba countery byly zafixovane jen na urovni collectoru,
    takze o tom, co se doopravdy dostane do reportu, netvrdil nic zadny
    test. Ubrany counter znamena mlcky nesledovanou regresi, pridany zase
    radek navic v kazdem bloku; obojí ma byt vedome rozhodnuti.

    'suppressed' tu chybi zamerne (rozhodnuti 2026-07-29): v produkci se
    damping v tomhle nasazeni nepouziva, takze radek nic nerika. Navic se
    u nej porovnani cetlo obracene - pokles potlacenych rout je zlepseni,
    ne regrese - a vynechanim odpada i potreba to resit.
    """
    assert PREFIX_KEYS == ("active", "received", "accepted", "advertised")


def test_prefix_counts_produce_a_row_per_counter():
    """Druha polovina te same pojistky: pripnuty seznam musi opravdu
    ridit, kolik radku check vyrobi."""
    ctx = _ctx(
        subject={"bgp": {"198.11.13.2": _peer()}},
        baseline={"bgp": {"198.11.13.2": _peer()}},
    )
    rows = [
        result
        for result in run_check(BgpPrefixCountsCheck(), ctx)
        if result.label.endswith("-prefix-count")
    ]

    assert sorted(r.label for r in rows) == sorted(
        f"{key}-prefix-count" for key in PREFIX_KEYS
    )


def test_peer_family_derived_from_address():
    assert peer_family("198.11.13.2") == 4
    assert peer_family("2001:db8:11:13::b") == 6
    assert peer_family("not-an-address") is None


def test_bgp_group_carries_peer_and_rib():
    """Zabiji mutanta M1: `group = f"BGP {peer}"` bez jmena RIB.

    Assert porovnava presne retezce vcetne jmena RIB, takze mutanta chyti i
    s jedinou RIB. Dve RIB tu jsou z jineho duvodu - modeluji skutecny
    problem, ktery report pred touto vlnou mel: peer se dvema RIB davajici
    osm nerozlisitelnych radku bez skupiny. Tvar fixture i pocet vysledku
    (8) je overeny proti skutecnemu kodu 2026-08-03.
    """
    peer = _peer()
    peer["ribs"]["bgp.l3vpn.0"] = {
        "received": 9, "accepted": 9, "advertised": 2, "active": 9, "suppressed": 0,
    }
    facts = {"bgp": {"198.11.13.2": peer}}
    results = run_check(BgpPrefixCountsCheck(), _ctx(facts, baseline=facts))

    assert len(results) == 8
    assert {r.group for r in results} == {
        "BGP 198.11.13.2 / inet.0",
        "BGP 198.11.13.2 / bgp.l3vpn.0",
    }
    assert {r.label for r in results} == {
        "active-prefix-count",
        "received-prefix-count",
        "accepted-prefix-count",
        "advertised-prefix-count",
    }


def test_configured_active_peer_without_session_is_fail():
    """Nakonfigurovany peer, ktery nenavazal session, musi byt videt.

    Dnes zmizi beze stopy nebo vyrobi lzivou hlasku 'sluzba nema zadne BGP
    peery'. Zrcadli chovani checks/routes.py: 'nakonfigurovana, ale neni v
    routovaci tabulce' je BROKEN.

    Zabiji mutanta: iterace jen pres `peers` misto pres sjednoceni.
    """
    ctx = _ctx({"bgp": {}}, bgp_neighbors=["198.11.13.2"])

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert "session neexistuje" in results[0].message


def test_configured_peer_without_session_does_not_hide_behind_a_sibling():
    """Sourozenec se session nesmi bezsessioveho peera prekryt.

    Bez tohoto testu by mutant, ktery novou vetev spusti jen kdyz je `peers`
    prazdne, prosel: test vys ma `peers` prazdne, takze by ho nechytil.
    """
    ctx = _ctx(
        {"bgp": {"198.11.13.9": _peer(state="Established")}},
        bgp_neighbors=["198.11.13.2", "198.11.13.9"],
    )

    results = run_check(BgpSessionStateCheck(), ctx)
    by_label = {result.label: result for result in results}

    assert by_label["BGP status (198.11.13.2)"].status is Status.FAIL
    assert by_label["BGP status (198.11.13.9)"].status is Status.PASS


def test_peer_measured_only_in_baseline_is_fail():
    """Peer, ktery v baselinu bezel a v subjektu neni ani v konfiguraci.

    Zrcadli routes.py: 'v baseline byla, v subjektu neni'.
    """
    ctx = _ctx(
        {"bgp": {}},
        baseline={"bgp": {"198.11.13.5": _peer(state="Established")}},
        bgp_neighbors=[],
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert "v baseline byl, v subjektu neni" in results[0].message


def test_configured_peer_without_session_takes_identity_from_selectors():
    """Subject se stavi pres Scope.select(), ne rucne.

    Scope.select filtruje MERENI podle ZAMERU, takze nakonfigurovany peer
    bez session se do ctx.subject nedostane a dostat nemuze. Test nad rucne
    slozenym subjektem by prosel i nad implementaci, ktera identitu bere z
    ctx.subject - a ta by v provozu nenasla nikdy nic.

    Zabiji mutanta: identita brana z `ctx.subject["bgp"]` misto ze
    selektoru.
    """
    scope = Scope(
        id="svc:L3VPN-CPE13-NNI:IPVPN",
        kind="service",
        key=ScopeKey("L3VPN-CPE13-NNI", "IPVPN", None),
        selectors=Selectors(
            interfaces=["ge-0/0/2.113"],
            bgp_neighbors=["198.11.13.2"],
        ),
    )
    # Fakta zarizeni nesou session UPLNE JINEHO peera - Scope.select ji
    # odfiltruje a subject vyjde prazdny, presne jako v provozu.
    facts = {"bgp": {"203.0.113.7": _peer(state="Established")}}
    ctx = CheckContext(
        scope=scope,
        subject=scope.select(facts),
        baseline=None,
        config=default_config(),
        failed_collectors={},
    )

    results = run_check(BgpSessionStateCheck(), ctx)

    assert len(results) == 1
    assert results[0].label == "BGP status (198.11.13.2)"
    assert results[0].status is Status.FAIL


def test_bgp_status_label_carries_the_peer():
    """Zabiji mutanta, ktery peera z popisku BGP status vypusti.

    Dva peery tehoz rodiny v jedne sluzbe by daly dva nerozlisitelne radky.
    Skupinu tenhle radek NEDOSTAVA schvalne - je jeden na peera a RIB se ho
    netyka, takze by nadpis stal nad jedinym radkem.
    """
    facts = {"bgp": {"198.11.13.2": _peer(), "198.11.13.6": _peer()}}
    results = run_check(BgpSessionStateCheck(), _ctx(facts, baseline=facts))

    assert len(results) == 2
    assert {r.label for r in results} == {
        "BGP status (198.11.13.2)",
        "BGP status (198.11.13.6)",
    }
    assert {r.group for r in results} == {None}
