import json
from pathlib import Path

from conftest import DUAL_RIB_PEER
from migration_validator import api
from migration_validator.models.result import Status
from migration_validator.reporting.json_report import to_json
from migration_validator.reporting.text_report import filter_result, render

NOW = "2026-07-24T11:40:02Z"

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEVICE_4 = str(FIXTURES / "172.20.20.4.yml")
DEVICE_5 = str(FIXTURES / "172.20.20.5.yml")


def test_full_migration_run_has_no_unexplained_fail_or_warn(synthetic_snapshot):
    """172.20.20.4/.5 uz nemodeluji cistou migraci beze zmen (AR-29).

    Laborka po regeneraci nese realne, ruzne stavy deaktivace mezi MX (.4,
    baseline) a PTX (.5, subject) - napr. CPE24 je na .4 deaktivovana a na
    .5 aktivni, MGMT-VLAN naopak. deactivation_state to spravne hlasi jako
    FAIL/WARN (AR-22/AR-23) a to je zdravy vysledek, ne regrese.

    Co porad musi platit: zadny JINY check nesmi na teto dvojici vratit
    FAIL nebo WARN. Kdyby to udelal, byl by to check, ktery je vzdy
    FAIL/WARN na zdrave sluzbe a prosel by tichem.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    unexpected = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id != "deactivation_state" and check.status in (Status.FAIL, Status.WARN)
    ]
    assert not unexpected, (
        "check jiny nez deactivation_state vratil FAIL/WARN na zdrave migraci: "
        f"{[(c.id, c.status, c.message) for c in unexpected]}"
    )

    # Smer musi odpovidat AR-22/AR-23: baseline aktivni -> subject
    # deaktivovana je FAIL "migrace nedokoncena"; baseline deaktivovana ->
    # subject aktivni je WARN "ted je aktivni". Prohozeny smer by tudy
    # projel jako "nejaky FAIL/WARN existuje", ale byl by obraceny.
    deactivation_checks = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "deactivation_state" and check.status is not Status.SKIP
    ]
    assert deactivation_checks, "fixture nema zadnou zmenu deaktivace k overeni"
    for check in deactivation_checks:
        if check.status is Status.FAIL:
            assert "migrace nedokoncena" in check.message
        else:
            # Dve ruzne cesty k WARN, a splacnout je dohromady by zakrylo
            # obracene poradi: "ted je aktivni" je zmena proti baselinu,
            # "baseline neni k porovnani" je nesparovana sluzba, ktera je
            # deaktivovana a porovnat se nema s cim. Od vlny 9 je i druha
            # z nich WARN, ne SKIP.
            assert (
                "ted je aktivni" in check.message
                or "baseline neni k porovnani" in check.message
            )

    assert result.summary["scopes_matched"] >= 5
    assert json.loads(to_json(result))["schema_version"] == 1


def test_evpn_checks_produce_real_verdicts_on_real_data(synthetic_snapshot):
    """Guards against the EVPN fact-schema silently miskeying into all-SKIP."""
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    all_checks = [check for scope in result.scopes for check in scope.checks]

    for check_id in ("evpn_vpws_status", "evpn_esi_status", "evpn_mac_count"):
        matching = [check for check in all_checks if check.id == check_id]
        assert any(check.status is not Status.SKIP for check in matching), (
            f"expected at least one non-SKIP result for {check_id!r}"
        )


def test_service_without_ipv6_has_no_ipv6_section(synthetic_snapshot):
    """Rozhodnuti R-1 (varianta c) na skutecnych datech, ne na fixture.

    Sluzba bez nakonfigurovane IPv6 nedostane v bloku ani sekci IPv6, ani
    radek o ni. Drive si SKIP z `nd_present`, ktery svou rodinu znackoval
    i u nenakonfigurovane rodiny, prazdnou sekci vynutil.

    Chytit to jde jen tudy: renderer dostane sekci jen tehdy, kdyz nejaky
    check nese family=6, takze rucne slozena fixture bez takoveho checku
    projde vzdycky - at uz je chovani checku jakekoliv.
    """
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")
    result = api.evaluate(new, now=NOW)

    ipv4_only = [
        scope
        for scope in result.scopes
        if scope.identity.get("ipv4") and not scope.identity.get("ipv6")
    ]
    assert ipv4_only, "fixture nema zadnou sluzbu jen s IPv4 - test by nic neoveril"

    for scope in ipv4_only:
        assert not [check for check in scope.checks if check.family == 6], (
            f"{scope.scope_id} nema IPv6, presto nese check s family=6"
        )

    rendered = render(result, detail=True)
    for scope in ipv4_only:
        # Popis muze byt v inventory null (napr. irb.4094 bez configurovaneho
        # description) - renderer pak pouzije scope_id jako zahlavi bloku,
        # viz reporting/view.py:151. Vyrez musi hledat totez.
        description = scope.identity.get("description") or scope.scope_id
        block = _block_of(rendered, description, scope.identity["service_type"])
        assert "-- IPv6" not in block


def _block_of(rendered: str, description: str, service_type: str) -> str:
    """Vyrizne blok jedne sluzby. Bloky oddeluji cary ze samych '='.

    Krajet podle `rendered.split(description)[-1]` nestaci: vratilo by to
    zbytek hlavickoveho radku posledniho vyskytu, tedy nikdy nic, co
    zacina '-- IPv6'. Takova kontrola by nemohla selhat, at se kod chova
    jakkoliv - a hollow test je presne to, co tenhle test opravoval.

    Samotny popis taky nestaci: v laborce nesou dve ruzne sluzby popis
    EVPN-VLAN-AWARE-INTERNET a lisi se az typem (E-LAN vs Internet).
    """
    lines = rendered.splitlines()
    starts = [i for i, line in enumerate(lines) if line and set(line) == {"="}]

    blocks = []
    for index in starts:
        header = lines[index + 1] if index + 1 < len(lines) else ""
        if description not in header or service_type not in header:
            continue
        # Blok ma dve cary '=': nad hlavickou a pod ni. Dalsi blok proto
        # zacina az tou treti - hledat od index+1 by useklo vyrez hned za
        # hlavickou a telo bloku by se vubec nemerilo.
        rest = [i for i in starts if i > index + 2]
        end = rest[0] if rest else lines.index("NESPAROVANO")
        blocks.append("\n".join(lines[index:end]))

    assert len(blocks) == 1, (
        f"ocekavan 1 blok pro {description!r}/{service_type!r}, je jich {len(blocks)}"
    )
    # Bez tohohle by prazdny nebo useknuty vyrez prosel jako "IPv6 tam neni".
    assert " STAV |" in blocks[0], "vyrez neobsahuje telo bloku"
    return blocks[0]


def test_every_row_has_a_label_and_a_value_on_real_data(synthetic_snapshot):
    """F-7 + F-10 na skutecnych datech, ne na rucni fixture.

    Radek reportu je podle AR-4 dvojice popisek + hodnota. Kdyz jedno z toho
    chybelo, renderer sahl po id checku resp. po cele vete - a obojim se
    ostry vypis skutecne rozjel. Fallbacky v rendereru zustaly jako
    pojistka, takze bez tohohle testu by je nova nedbala vetev zase tise
    zapnula.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    checks = [check for scope in result.scopes for check in scope.checks]
    assert checks

    for check in checks:
        assert check.label, f"{check.id} vratil radek bez popisku"
        assert check.value, f"{check.id} vratil radek bez hodnoty ({check.message})"
        assert check.value != check.message, (
            f"{check.id} ma ve sloupci hodnot celou vetu: {check.value!r}"
        )


def test_traffic_drop_on_new_device_is_detected(synthetic_snapshot):
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration", pps=400)
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration", pps=50)

    result = api.evaluate(new, baseline=old, now=NOW)

    traffic = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "interface_traffic"
    ]
    assert any(check.status is Status.WARN for check in traffic)


def test_new_elan_service_shows_up_as_unmatched(synthetic_snapshot):
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    subject_ids = {item["scope_id"] for item in result.unmatched["subject"]}
    assert any("EVPN-VLAN-AWARE-INTERNET" in scope_id for scope_id in subject_ids)


def test_management_interfaces_never_appear(synthetic_snapshot):
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, now=NOW)
    rendered = render(result)

    assert "fxp0" not in rendered
    assert "mgmt" not in rendered


def test_single_snapshot_validation_skips_comparison_checks(synthetic_snapshot):
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")

    result = api.evaluate(old, now=NOW)

    prefix_checks = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "bgp_prefix_counts"
    ]
    assert prefix_checks
    assert all(check.status is Status.SKIP for check in prefix_checks)


def test_render_after_filter_still_shows_unmatched_section(synthetic_snapshot):
    """NESPAROVANO je pojistka: i kdyz filtr smaze vsechny scopy, sekce s
    nezmigrovanymi sluzbami se musi vykreslit dal - to je hlavni bod, proti
    kteremu tenhle task testuje.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)
    assert result.unmatched["subject"] or result.unmatched["baseline"]

    filtered = filter_result(result, text="no-such-scope-matches-this-needle")
    assert filtered.scopes == []

    rendered = render(filtered)

    assert "NESPAROVANO" in rendered
    unmatched_labels = {
        item.get("description") or item["scope_id"]
        for side in ("baseline", "subject")
        for item in filtered.unmatched[side]
    }
    assert any(label in rendered for label in unmatched_labels)


def _prefix_count_checks(result):
    """Vsechny vysledky checku bgp_prefix_counts napric scopy.

    Vraci dvojice (group, rib). SKIP vysledky bez details se vypousti -
    nesou (None, None) a o jmenu RIB netvrdi nic.
    """
    return {
        (check.group, check.details.get("rib"))
        for scope in result.scopes
        for check in scope.checks
        if check.id == "bgp_prefix_counts" and check.details.get("rib")
    }


def test_ipv6_peers_carry_inet6_rib(synthetic_snapshot):
    """Sdilene fixtures musi u IPv6 peera hlasit inet6.0, ne inet.0.

    Zabiji mutanta: `rib_name` napevno na "inet.0" v `_prefix_finding`
    (migration_validator/checks/bgp.py). S nim by kazdy peer bez ohledu na
    rodinu hlasil inet.0 a report by tvrdil neco, co na zarizeni neplati.

    Test tvrdi nad objekty (group, details["rib"]), ne nad vykreslenym
    textem - hledani retezce kdekoliv ve vystupu by nemerilo sekci.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    ipv6_ribs = {
        rib for group, rib in _prefix_count_checks(result) if ":" in group
    }
    assert ipv6_ribs, "fixture nema zadneho IPv6 BGP peera k overeni"
    assert ipv6_ribs == {"inet6.0"}


def test_dual_rib_peer_yields_two_distinguishable_blocks(synthetic_snapshot):
    """Motivujici scenar AR-36 musi byt na sdilenych fixtures k videni.

    Zabiji tehoz mutanta: `rib_name` napevno na "inet.0". S nim by obe RIB
    tehoz peera splynuly do jedine skupiny a osm nerozlisitelnych radku,
    kvuli kterym vlna 5 report prepsala, by se vratilo.

    Zabiji i mutanta `_SECOND_RIB_COUNTERS = dict(_RIB_COUNTERS)` v
    `tests/conftest.py`: samotna mnozina jmen RIB {"inet.0", "inet6.0"}
    je jen hlavicka. Bez porovnani hodnot by dva bloky tehoz peera
    mohly nest stejne countery a byly by rozlisitelne jen hlavickou -
    presne to, co komentar nad `_SECOND_RIB_COUNTERS` popisuje jako vadu.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    result = api.evaluate(new, baseline=old, now=NOW)

    dual_rib_checks = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id == "bgp_prefix_counts"
        and check.group
        and check.group.startswith(f"BGP {DUAL_RIB_PEER} / ")
    ]

    ribs = {
        check.details.get("rib")
        for check in dual_rib_checks
        if check.details.get("rib")
    }
    assert ribs == {"inet.0", "inet6.0"}

    values_by_rib = {
        rib: {
            (check.label, check.value)
            for check in dual_rib_checks
            if check.details.get("rib") == rib
        }
        for rib in ribs
    }
    assert values_by_rib["inet.0"] != values_by_rib["inet6.0"], (
        "oba bloky DUAL_RIB_PEER nesou stejne countery - lisi se jen hlavickou"
    )
