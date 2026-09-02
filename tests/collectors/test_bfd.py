"""Testy collectoru BFD proti nahranemu XML z laborky.

Puvodne (pred 2026-09) obe nahravky nesly spolecny peer 152.11.13.2, Up na
obou stranach. Nahravka 2026-09-02 (task 5b, ideal pre-migration state z
Ansible commitu) ukazala, ze `bfd-liveness-detection` byl z BGP skupin/
sousedu na .4 odebran cele - sessions zustavaji v tabulce jako AdminDown/
bez klientu (Junos je nemaze hned), 198.11.13.2 a 2001:db8:11:13::b
(L3VPN-CPE13-NNI) uz z tabulky vypadly uplne.

Nahravka 2026-09-03 (task 5c, post-migration z .5 po presunu sluzeb) ukazala
totez o krok dal: uzivatel BFD odebral DEVICE-WIDE na OBOU routerech
zaroven. .5 (junos-evo) uz nema zadny konfigurovany bfd_peer a session
tabulka (tests/fixtures/rpc/junos-evo/bfd.xml) je uplne prazdna (0 session,
0 clients) - na rozdil od .4, kde jeste stale AdminDown zaznamy zustavaji.
Testy, ktere drive overovaly konkretni peer/stav proti nahravce z .5, uz
takovy scenar na zive laborce nemaji - prevedeny na synteticke XML (stejny
zpusob jako EMPTY_OUTPUT nize), aby o pokryti nerozhodoval stav laborky.

Prazdny vypis je platny stav a testuje se na syntetickem XML (EMPTY_OUTPUT),
ne na nahravce, aby o pokryti nerozhodoval stav laborky.
"""

from __future__ import annotations

import pytest
from lxml import etree

from migration_validator.collectors.bfd import BfdCollector

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize("platform", PLATFORMS)
def test_returns_mapping_keyed_by_neighbor(rpc_fixture, platform):
    """Collector vraci dict bez ohledu na to, kolik (pripadne zadnych) session laborka ma.

    Do 2026-09-03 (task 5c) obe nahravky nesly spolecny peer 152.11.13.2 a
    testovalo se tu i na to. Po tomto datu .5 (junos-evo) nema BFD zadne -
    tvrdit "152.11.13.2 in result" by uz neplatilo pro zadnou platformu
    stejne (na .4 je to jen stale AdminDown zaznam, na .5 nic). Obsah
    session konkretniho peera overuji dedikovane testy nize (synteticke pro
    .5, na realne nahravce pro .4).
    """
    result = BfdCollector().parse(rpc_fixture(platform, "bfd"), platform)
    assert isinstance(result, dict)


def test_evo_session_table_is_empty_after_bfd_removal(rpc_fixture):
    """Realny fakt z 2026-09-03: .5 uz nema jedinou BFD session vubec.

    Na rozdil od .4 (task 5b), kde stale AdminDown zaznamy v tabulce jeste
    zustavaji, .5 uz session table vyprazdnila uplne - collector musi umet
    vratit prazdny dict i z RPC odpovedi, ktera ma sessions=0/clients=0.
    """
    result = BfdCollector().parse(rpc_fixture("junos-evo", "bfd"), "junos-evo")

    assert result == {}


EMPTY_OUTPUT = """
<bfd-session-information style="detail">
  <sessions>0</sessions>
  <clients>0</clients>
  <cumulative-transmission-rate>0.0</cumulative-transmission-rate>
  <cumulative-reception-rate>0.0</cumulative-reception-rate>
</bfd-session-information>
"""


@pytest.mark.parametrize("platform", PLATFORMS)
def test_empty_output_is_a_valid_state(platform):
    """Nula session neni selhani collectoru - collector nema co interpretovat.

    XML je synteticke, ne nahravka. Drive se tenhle stav bral z `junos`
    fixture, ktera zadnou session nemela - jenze to znamenalo, ze stav
    laborky rozhodoval o tom, jestli je tenhle pripad vubec testovany.
    """
    result = BfdCollector().parse(etree.fromstring(EMPTY_OUTPUT.encode()), platform)

    assert result == {}


def test_up_and_down_sessions_are_recorded_verbatim():
    """Odpoved nese session verbatim vcetne stavu a rozhrani.

    Do 2026-09-03 (task 5c) toto overovala nahravka z .5 (peer 152.11.13.2
    Up, 198.11.13.2 Down). BFD odebrane device-wide z laborky vyprazdnilo
    .5 session tabulku uplne (viz modulovy docstring), takze zadna zive
    nahrana data uz tenhle mix Up/Down nenesou - synteticke XML, tvar
    prevzaty ze skutecne zaznamenane odpovedi.
    """
    xml = etree.fromstring(
        b"""
        <bfd-session-information style="detail">
          <bfd-session>
            <session-neighbor>152.11.13.2</session-neighbor>
            <session-state>Up</session-state>
            <session-interface>et-0/0/8.13</session-interface>
            <local-diagnostic>None</local-diagnostic>
            <remote-state>Up</remote-state>
          </bfd-session>
          <bfd-session>
            <session-neighbor>198.11.13.2</session-neighbor>
            <session-state>Down</session-state>
            <local-diagnostic>None</local-diagnostic>
            <remote-state>AdminDown</remote-state>
          </bfd-session>
        </bfd-session-information>
        """
    )

    result = BfdCollector().parse(xml, "junos-evo")

    assert result["152.11.13.2"]["state"] == "Up"
    assert result["152.11.13.2"]["interface"] == "et-0/0/8.13"
    assert result["198.11.13.2"]["state"] == "Down"
    assert result["198.11.13.2"]["remote_state"] == "AdminDown"


def test_client_names_are_collected():
    """Klient rozlisi BGP session od te, kterou drzi jiny protokol.

    Synteticke XML od 2026-09-03 (task 5c) ze stejneho duvodu jako
    test_up_and_down_sessions_are_recorded_verbatim vyse - .5 uz zadnou
    session s klientem v realne nahravce nema.
    """
    xml = etree.fromstring(
        b"""
        <bfd-session-information style="detail">
          <bfd-session>
            <session-neighbor>152.11.13.2</session-neighbor>
            <session-state>Up</session-state>
            <session-interface>et-0/0/8.13</session-interface>
            <bfd-client>
              <client-name>BGP</client-name>
            </bfd-client>
            <local-diagnostic>None</local-diagnostic>
            <remote-state>Up</remote-state>
          </bfd-session>
        </bfd-session-information>
        """
    )

    result = BfdCollector().parse(xml, "junos-evo")

    assert result["152.11.13.2"]["clients"] == ["BGP"]


def test_mx_sessions_are_recorded_verbatim(rpc_fixture):
    """Odpoved z MX ma tentyz tvar jako z EVO - jen jina jmena rozhrani.

    Roadmapa vlny 3 predpokladala MX-specificke parsovani; nahravka z
    2026-07-31 ukazala, ze zadne neni. Tenhle test to drzi: kdyby MX odpoved
    vlastni tvar dostala, spadne tady, ne az v conformance.

    Hodnoty aktualizovany na nahravku 2026-09-02 (task 5b): `.4` ma teď
    `bfd-liveness-detection` odebrane z BGP konfigurace, takze 152.11.13.2
    je v tabulce jako AdminDown/bez klienta a 198.11.13.2 z tabulky uplne
    vypadl - misto neho se overuje 198.11.14.4 (L3VPN-CPE14-UNI), ktery ve
    stejne nahravce zustava jako AdminDown multi-hop session bez rozhrani.
    """
    result = BfdCollector().parse(rpc_fixture("junos", "bfd"), "junos")

    assert result["152.11.13.2"]["state"] == "AdminDown"
    assert result["152.11.13.2"]["interface"] == "ge-0/0/2.13"
    assert result["152.11.13.2"]["remote_state"] == "Down"
    assert result["198.11.14.4"]["state"] == "AdminDown"
    assert result["198.11.14.4"]["remote_state"] == "Up"


def test_mx_client_names_are_collected(rpc_fixture):
    """Bez klienta nejde odlisit session drzenou BGP od jine.

    Zabíjí mutanta: vypusteni smycky pres bfd-client, po nemz zustane
    `clients` prazdny seznam. Je to duvod, proc collector vubec pouziva
    detail variantu RPC.

    Nahravka z 2026-09-02 (task 5b) uz na `.4` zadnou session s klientem
    nema (bfd-liveness-detection byl z BGP konfigurace odebran cely) - proto
    synteticke XML misto nahravky, stejnym zpusobem jako EMPTY_OUTPUT vyse,
    aby o pokryti mutantu nerozhodoval stav laborky.
    """
    xml = etree.fromstring(
        b"""
        <bfd-session-information style="detail">
          <bfd-session>
            <session-neighbor>152.11.13.2</session-neighbor>
            <session-state>Up</session-state>
            <session-interface>ge-0/0/2.13</session-interface>
            <bfd-client>
              <client-name>BGP</client-name>
            </bfd-client>
            <local-diagnostic>None</local-diagnostic>
            <remote-state>Up</remote-state>
          </bfd-session>
        </bfd-session-information>
        """
    )

    result = BfdCollector().parse(xml, "junos")

    assert result["152.11.13.2"]["clients"] == ["BGP"]


def test_entries_have_expected_keys():
    """Kazda session ma presne tuto sadu klicu - zadny navic, zadny chybi.

    Do 2026-09-03 (task 5c) toto overovala nahravka z .5, ktera aspon jednu
    session mela. Po vyprazdneni .5 tabulky (viz modulovy docstring) by
    smycka pres `result.values()` nad realnou nahravkou proslo VAKUOVE -
    prazdny dict nema co iterovat. Synteticke XML drzi test na skutecnem
    obsahu, ne na tom, jestli laborka zrovna nejakou session ma.
    """
    xml = etree.fromstring(
        b"""
        <bfd-session-information style="detail">
          <bfd-session>
            <session-neighbor>152.11.13.2</session-neighbor>
            <session-state>Up</session-state>
            <session-interface>et-0/0/8.13</session-interface>
            <bfd-client>
              <client-name>BGP</client-name>
            </bfd-client>
            <local-diagnostic>None</local-diagnostic>
            <remote-state>Up</remote-state>
          </bfd-session>
        </bfd-session-information>
        """
    )

    result = BfdCollector().parse(xml, "junos-evo")

    assert result
    for data in result.values():
        assert set(data) == {
            "state",
            "interface",
            "remote_state",
            "local_diagnostic",
            "clients",
            "detection_time",
            "transmission_interval",
            "multiplier",
        }


def test_collector_passes_detail_flag():
    """Strucna varianta nema bfd-client ani remote-state."""
    assert BfdCollector().rpc_kwargs("junos") == {"detail": True}
