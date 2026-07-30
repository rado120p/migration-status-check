"""Testy checku BFD.

Matice stavu odpovida tomu, co laborka ukazovala 2026-07-29:
152.11.13.2 Up, 198.11.13.2 Down, 198.11.14.2 nakonfigurovane pres skupinu
ale bez session (BGP Idle), a par peeru bez BFD vubec.
"""

from __future__ import annotations

from migration_validator.checks.base import CheckContext
from migration_validator.checks.bfd import BfdSessionStateCheck
from migration_validator.config import default_config
from migration_validator.models.result import Outcome
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
    return CheckContext(
        scope=scope or _scope(),
        subject={"bfd": sessions, "bgp": {"198.11.13.2": {"state": bgp_state}}},
        baseline=(
            {"bfd": baseline_sessions, "bgp": {}} if baseline_sessions is not None else None
        ),
        config=default_config(),
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
    findings = BfdSessionStateCheck().run(
        _ctx({}, baseline_sessions={"198.11.13.2": {"state": "Up"}}, scope=_scope(bfd_peers=[]))
    )

    assert findings[0].outcome is Outcome.BROKEN
    assert findings[0].value == "BFD odstraneno"
    assert findings[0].baseline_value == "Up"
    assert findings[0].family == 4


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
    assert findings[0].value == "BFD odstraneno"


def test_session_without_intent_is_degraded():
    """Detektor diry v pruchodu hierarchii BFD.

    Kdyby check iteroval jen pres zamer, chyba v dedeni ze skupiny by se
    schovala pred vystupem nastroje.
    """
    findings = BfdSessionStateCheck().run(
        _ctx({"198.11.13.2": {"state": "Up"}}, scope=_scope(bfd_peers=[]))
    )

    assert findings[0].outcome is Outcome.DEGRADED
    assert findings[0].value == "bez konfigurace"
    assert findings[0].family == 4


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
    prave tudy se do vetve REMOVED chodi. `BFD odstraneno` i jeho hlaska
    ale mluvi o konfiguraci, kterou nastroj v tomhle rezimu nevidi.
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


def test_device_scope_reports_state_without_intent():
    """Bez inventory se nehlasi ani 'bez session', ani 'bez konfigurace'."""
    ctx = CheckContext(
        scope=device_scope(),
        subject={"bfd": {"152.11.13.2": {"state": "Up"}}, "bgp": {}},
        baseline=None,
        config=default_config(),
    )

    findings = BfdSessionStateCheck().run(ctx)

    assert len(findings) == 1
    assert findings[0].outcome is Outcome.OK
