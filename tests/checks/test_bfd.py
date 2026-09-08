"""Testy checku BFD.

Matice stavu odpovida tomu, co laborka ukazovala 2026-07-29:
152.11.13.2 Up, 198.11.13.2 Down, 198.11.14.2 nakonfigurovane pres skupinu
ale bez session (BGP Idle), a par peeru bez BFD vubec.
"""

from __future__ import annotations

from migration_validator.checks.base import CheckContext, run_check
from migration_validator.checks.bfd import BfdSessionStateCheck
from migration_validator.collectors.bfd import BfdCollector
from migration_validator.config import default_config
from migration_validator.models.result import Outcome, Status
from migration_validator.models.scope import Scope, ScopeKey, Selectors, device_scope

INTENT = [{"peer": "198.11.13.2", "minimum_interval": 3000, "multiplier": 3, "source": "neighbor"}]


def _scope(bfd_peers=None, bgp_neighbors=("198.11.13.2",)) -> Scope:
    return Scope(
        id="svc:CPE13:IPVPN",
        kind="service",
        key=ScopeKey(description="CPE13", service_type="IPVPN"),
        selectors=Selectors(
            bgp_neighbors=list(bgp_neighbors),
            bfd_peers=list(bfd_peers if bfd_peers is not None else INTENT),
        ),
    )


def _ctx(sessions, bgp_state="Established", baseline_sessions=None, scope=None):
    baseline = (
        {"bfd": baseline_sessions, "bgp": {}} if baseline_sessions is not None else None
    )
    return CheckContext(
        scope=scope or _scope(),
        subject={"bfd": sessions, "bgp": {"198.11.13.2": {"state": bgp_state}}},
        baseline=baseline,
        config=default_config(),
        # Pozitivni dukaz, ze baseline oblast byla zmerena (ctx.baseline_measured)
        # - bez nej by UNCHANGED nemohl vzniknout ani u testu, ktere baseline
        # predavaji.
        baseline_collectors=(
            {area: {"status": "ok"} for area in baseline} if baseline is not None else {}
        ),
    )


def test_session_up_passes():
    findings = BfdSessionStateCheck().run(_ctx({"198.11.13.2": {"state": "Up"}}))

    assert findings[0].outcome is Outcome.OK
    assert findings[0].value == "Up"
    assert findings[0].label == "BFD (198.11.13.2)"


def test_session_down_is_broken():
    findings = BfdSessionStateCheck().run(_ctx({"198.11.13.2": {"state": "Down"}}))

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "Down"


def test_session_down_message_states_expectation():
    """Hlaska ma rict, co se ocekavalo, ne jen aktualni stav (bod 3)."""
    f = BfdSessionStateCheck().run(_ctx({"198.11.13.2": {"state": "Down"}}))[0]

    assert f.message == "198.11.13.2: session Down, ocekavano Up"


def test_session_down_since_baseline_carries_previous_state():
    """ZMENA sloupec ma vypsat 'bylo Up' i u session, ktera od te doby spadla."""
    findings = BfdSessionStateCheck().run(
        _ctx({"198.11.13.2": {"state": "Down"}}, baseline_sessions={"198.11.13.2": {"state": "Up"}})
    )

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "Down"
    assert findings[0].baseline_value == "Up"


def test_missing_session_with_established_bgp_is_broken():
    """BGP bezi, BFD se nedomluvilo - realna degradace doby vypadku."""
    findings = BfdSessionStateCheck().run(_ctx({}))

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "bez session"
    assert findings[0].family == 4


def test_missing_session_with_idle_bgp_is_skipped():
    """BFD nemuze nabehnout, dokud nebezi BGP.

    Bez teto vetve by L3VPN-CPE14-UNI dostalo dva FAIL radky za jednu
    pricinu a v ostrem behu by se to opakovalo u kazde nedojete sluzby.
    """
    findings = BfdSessionStateCheck().run(_ctx({}, bgp_state="Idle"))

    assert findings[0].outcome is Outcome.SKIP
    assert findings[0].value == "BGP neni Established"
    assert findings[0].family == 4


def test_bfd_removed_since_baseline_is_broken():
    """Hlaska tvrdi jen o clenstvi ve sluzbe, ne o existenci na zarizeni.

    Peer, ktereho tato sluzba uz nenarokuje, muze mit na zarizeni dal
    zivou BFD session pod jinou sluzbou - engine.py:_unassigned_bfd_sessions
    by ji ukazal v NEZARAZENO. "v subjektu neni nakonfigurovane" by tam
    lhalo, proto formulace mluvi o clenstvi ("v baseline patril k teto
    sluzbe, v subjektu uz ne"), ne o existenci.
    """
    findings = BfdSessionStateCheck().run(
        _ctx({}, baseline_sessions={"198.11.13.2": {"state": "Up"}}, scope=_scope(bfd_peers=[]))
    )

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "v baseline patril k teto sluzbe, v subjektu uz ne"
    assert findings[0].message == "198.11.13.2: v baseline patril k teto sluzbe, v subjektu uz ne"
    # R-5: baseline_value ted pochazi ze stejne _session_value() jako value,
    # se STEJNYM configured/is_device jako ma subjekt (bfd_peers=[] =>
    # configured=False). Peer neni ve sluzbe (service scope, ne device), takze
    # se na baseline session aplikuje stejna PARSER_MISSED hlidka jako by
    # session prisla v subjektu - baseline_value uz nerika "Up", ale mluvi
    # slovnikem radku (i kdyz to na NOT_IN_SERVICE radku pusobi nezvykle).
    assert findings[0].baseline_value == "parser nenasel konfiguraci"
    assert findings[0].family == 4


def test_peer_only_in_baseline_value_is_full_sentence():
    """Hodnota v tabulce je uplna veta, ne kratka znacka (bod 4).

    Puvodni hodnota 'neni ve sluzbe' rikala, ze peer neni ve sluzbe, ne ze
    v baseline byl a uz neni - v tabulkovem sloupci bez kontextu hlasky to
    ctenari nedavalo smysl.
    """
    f = BfdSessionStateCheck().run(
        _ctx({}, baseline_sessions={"198.11.13.2": {"state": "Up"}}, scope=_scope(bfd_peers=[], bgp_neighbors=()))
    )[0]

    assert f.value == "v baseline patril k teto sluzbe, v subjektu uz ne"
    # Stejny duvod jako v test_bfd_removed_since_baseline_is_broken - R-5
    # sjednocuje baseline_value pres _session_value() s configured/is_device
    # subjektu.
    assert f.baseline_value == "parser nenasel konfiguraci"


def test_bfd_removed_since_baseline_is_broken_even_when_bgp_is_gone():
    """Poradi vetvi 'not configured' pred 'bgp_state != ESTABLISHED' je zamerne.

    Peer zcela odstraneny z migrace - zamer pryc, session pryc, BGP taky
    pryc. Kdyby se vetve prohodily, tenhle pripad by spadl do SKIP s
    hlaskou 'BGP neni Established', coz je fakticky spatne (BFD pro
    tohoto peera vubec nakonfigurovane neni) a schova to skutecnou
    regresi (BFD ochrana zmizela od baseline) za neviditelny SKIP radek.
    `test_bfd_removed_since_baseline_is_broken` tohle nezachyti, protoze
    dedi vychozi bgp_state="Established" z _ctx - jedinou hodnotu, pri
    ktere obe poradi vetvi davaji stejny vysledek.
    """
    findings = BfdSessionStateCheck().run(
        _ctx(
            {},
            bgp_state="Idle",
            baseline_sessions={"198.11.13.2": {"state": "Up"}},
            scope=_scope(bfd_peers=[]),
        )
    )

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "v baseline patril k teto sluzbe, v subjektu uz ne"


def test_session_without_intent_is_degraded():
    """Detektor diry v pruchodu hierarchii BFD.

    Kdyby check iteroval jen pres zamer, chyba v dedeni ze skupiny by se
    schovala pred vystupem nastroje.
    """
    findings = BfdSessionStateCheck().run(
        _ctx({"198.11.13.2": {"state": "Up"}}, scope=_scope(bfd_peers=[]))
    )

    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "parser nenasel konfiguraci"
    assert findings[0].family == 4


def test_unconfigured_session_blames_parser():
    """Hlaska ma rict, ze problem je v parseru, ne v konfiguraci sluzby
    (bod 5) - session existuje, parser ji jen nedohledal."""
    f = BfdSessionStateCheck().run(
        _ctx({"198.11.13.2": {"state": "Up"}}, scope=_scope(bfd_peers=[]))
    )[0]

    assert f.outcome is Outcome.DEGRADED
    assert f.message == "198.11.13.2: BFD session existuje (Up), ale parser ji nenasel v konfiguraci sluzby"
    assert f.value == "parser nenasel konfiguraci"


def test_service_without_bfd_gets_no_row():
    """R-1: bez konfigurace, bez session a bez session v baseline - zadny radek."""
    findings = BfdSessionStateCheck().run(_ctx({}, scope=_scope(bfd_peers=[])))

    assert findings == []


def test_family_comes_from_peer_address():
    findings = BfdSessionStateCheck().run(
        _ctx(
            {"2001:db8:11:14::b": {"state": "Up"}},
            scope=_scope(
                bfd_peers=[{"peer": "2001:db8:11:14::b", "source": "group"}],
                bgp_neighbors=("2001:db8:11:14::b",),
            ),
        )
    )

    assert findings[0].family == 6


def test_device_scope_never_claims_anything_about_the_configuration():
    """AR-17: bez inventory nejde tvrdit, ze BFD neni nakonfigurovane.

    Device scope ma vzdy prazdne selektory, takze `configured` je False -
    prave tudy se do vetve SESSION_GONE chodi. Hlaska pro NOT_IN_SERVICE
    ("v baseline patril k teto sluzbe, v subjektu uz ne") tvrdi clenstvi
    ve sluzbe - to device scope bez inventory nevi o nic vic nez o
    konfiguraci, takze ji tu vydat nesmi stejne jako puvodni "v subjektu
    neni nakonfigurovane".
    Sloupec s hodnotou je to, co jde do reportu (F-7/AR-4), takze stav-only
    musi byt hodnota, ne jen hlaska.

    Toto je test, jehoz absence tu vadu propustila: routes.py stejny rozdil
    resi dvema konstantami, bfd.py mel jen jednu.
    """
    ctx = CheckContext(
        scope=device_scope(),
        subject={"bfd": {}, "bgp": {}},
        baseline={"bfd": {"152.11.13.2": {"state": "Up"}}, "bgp": {}},
        config=default_config(),
    )

    findings = BfdSessionStateCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "session zmizela"
    assert findings[0].baseline_value == "Up"
    assert "konfigurac" not in findings[0].message
    assert "nakonfigurovan" not in findings[0].message


def test_intent_entry_without_peer_gets_no_row():
    """Zaznam bez `peer` se preskoci, misto aby vyrobil radek 'BFD (None)'.

    str(item.get("peer")) davalo doslovny string "None" - radek s family=None,
    ktery spadne do bezhlavickove sekce reportu. Dosazitelne jen rucne
    upravenou inventory, protoze parser peer vzdy vyplni.
    """
    findings = BfdSessionStateCheck().run(
        _ctx({}, scope=_scope(bfd_peers=[{"minimum_interval": 3000, "source": "group"}]))
    )

    assert findings == []


def test_check_reads_the_area_named_by_its_collector():
    """Sev collector -> check: oblast se jmenuje podle collectoru, ne literalem.

    Conformance test to na MX pokryt neumi - fixture pro junos ma nula session
    (overeno 2026-07-30 i na surovem capturu z laborky), takze "aspon jeden
    ne-SKIP" by na nem selhalo z legitimniho duvodu. Klic ale na platforme
    nezavisi, takze staci overit ho jednou - a proti jmenu collectoru, ne
    proti retezci "bfd" napsanemu podruhe.

    Druhou polovinu sevu drzi test_collector_keys_match_contract, ktery tvrdi,
    ze klice faktu odpovidaji jmenum collectoru.

    Zabiji mutanta: ctx.subject.get("bfd") -> ctx.subject.get("bfd_x")
    v checks/bfd.py. S nim je session neviditelna, zamer zustava, a check
    misto OK vrati SKIP 'BGP neni Established'.
    """
    area = BfdCollector().name
    scope = Scope(
        id="svc:CPE13:IPVPN",
        kind="service",
        key=ScopeKey("CPE13", "IPVPN", None),
        selectors=Selectors(bfd_peers=[{"peer": "198.11.13.2"}]),
    )
    ctx = CheckContext(
        scope=scope,
        subject={area: {"198.11.13.2": {"state": "Up"}}, "bgp": {}},
        baseline=None,
        config=default_config(),
    )

    findings = BfdSessionStateCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK, (
        "check nevidi oblast, kterou BfdCollector vydava - klic se rozesel "
        "mezi collectorem a checkem"
    )


def test_session_down_in_both_is_unchanged():
    """Shodny spatny stav v subjektu i zmerene baseline je PASS se znackou."""
    down = {"198.11.13.2": {"state": "Down"}}
    [row] = run_check(BfdSessionStateCheck(), _ctx(down, baseline_sessions=down))
    assert row.status is Status.PASS and row.value == "Down" == row.baseline_value


def test_missing_session_in_both_with_established_bgp_is_unchanged():
    """Peer bez session ted i v baseline (BGP up v obou) je take UNCHANGED."""
    [row] = run_check(BfdSessionStateCheck(), _ctx({}, baseline_sessions={}))
    assert row.status is Status.PASS and row.baseline_value == "bez session"


def test_unconfigured_session_baseline_value_is_same_sentinel():
    """PARSER_MISSED zustava nalez (WARN), ne UNCHANGED - mimo zamer se
    porovnavat nema, i kdyz baseline i subjekt maji stejnou session."""
    session = {"10.0.0.9": {"state": "Up"}}
    [row] = run_check(BfdSessionStateCheck(), _ctx(session, baseline_sessions=session,
                                                   scope=_scope(bfd_peers=[])))
    assert row.status is Status.WARN                      # zustava (mimo zamer = nalez)
    assert row.baseline_value == "parser nenasel konfiguraci"


def test_device_scope_reports_state_without_intent():
    """Bez inventory se nehlasi ani 'bez session', ani 'parser nenasel konfiguraci'."""
    ctx = CheckContext(
        scope=device_scope(),
        subject={"bfd": {"152.11.13.2": {"state": "Up"}}, "bgp": {}},
        baseline=None,
        config=default_config(),
    )

    findings = BfdSessionStateCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
