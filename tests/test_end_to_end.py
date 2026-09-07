import json
import re
from pathlib import Path

from conftest import DUAL_RIB_PEER
from migration_validator import api
from migration_validator.engine import evaluate_snapshots
from migration_validator.models.result import Status
from migration_validator.reporting.json_report import to_json
from migration_validator.reporting.text_report import filter_result, render

NOW = "2026-07-24T11:40:02Z"

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEVICE_4 = str(FIXTURES / "172.20.20.4.yml")
DEVICE_5 = str(FIXTURES / "172.20.20.5.yml")


# Task 5 (regenerace 2026-09-02) zaznamenal realny rozdil MX (.4, baseline)
# vs. EVO (.5, subjekt): L3VPN-CPE13-NNI mela na MX pod BGP peerem
# 2001:db8:11:13::b nakonfigurovany bfd-liveness-detection, na EVO ne.
# Task 5b (2026-09-02, idealni pre-migracni stav .4) overil primo v `show
# configuration routing-instances`/`show configuration protocols bgp` na .4,
# ze bfd-liveness-detection byl mezitim (commit-user ansible) odebran z CELE
# BGP konfigurace na .4 - nejen z tohoto peera. Asymetrie tedy zmizela sama:
# .4 uz pro tohoto peera zadny zamer ani session nema (peer vypadl i z
# operacni tabulky), takze union treti zdroju (AR-14) neprodukuje zadny
# nalez a puvodni vyjimka by uz nic nefiltrovala (overeno: prazdny tuple
# nechava test zelenym). Mechanismus se necha na miste pro pripadne dalsi
# nalezy, ale ZADNA aktualni polozka neni potreba.
#
# Nevyresena otazka pro operatora laborky zustava: byla to zamerna
# normalizace (misto pridani BFD na EVO se odebralo z MX), nebo vedlejsi
# efekt pripravy multicast scenare? Viz task-5b-report.md.
KNOWN_LAB_ASYMMETRIES: tuple[tuple[str, str], ...] = ()


def _deactivate_only_in_subject(old, new) -> None:
    """Vyrobi jednu FAIL deaktivaci (baseline aktivni, subjekt vypnuty) na
    sluzbe sdilene obema snimky.

    Nazev se lisi od `_deactivate_shared_service` nize (ta vypina TUTEZ
    sluzbu na OBOU stranach - WARN "stejne jako v baseline"): stejne jmeno
    by v modulu prepsalo drivejsi definici a `test_full_migration_run_...`
    by tise volal jiny scenar, nez zamyslel.

    Puvodne test cilil na realnou asymetrii z laborky (CPE24/MGMT-VLAN mely
    kazde jinou stranu deaktivovanou). Regenerace 2026-09-02 to uz nenese -
    laborka byla mezitim pro multicast scenar sjednocena a obe sluzby jsou
    ted aktivni na obou zarizenich. AR-22/AR-23 smer testu je porad potreba
    overit na skutecne postavenem RunResultu (ne jen jednotkovym testem
    checku), takze scenar se vyrabi rucne stejnym zpusobem jako
    `_deactivate_shared_route` u statickych rout.
    """
    old_by_key = {
        (scope.key.description, scope.key.service_type): scope
        for scope in old.scopes
        if scope.key is not None and scope.key.description is not None
    }
    new_by_key = {
        (scope.key.description, scope.key.service_type): scope
        for scope in new.scopes
        if scope.key is not None and scope.key.description is not None
    }
    shared = sorted(set(old_by_key) & set(new_by_key))
    assert shared, "fixture nema zadnou sluzbu sdilenou obema snimky"

    key = shared[0]
    old_by_key[key].interface_active = True
    old_by_key[key].routing_instance_active = True
    new_by_key[key].interface_active = False


def test_full_migration_run_has_no_unexplained_fail_or_warn(synthetic_snapshot):
    """172.20.20.4/.5 uz nemodeluji cistou migraci beze zmen (AR-29).

    Puvodne 172.20.20.4/.5 nesly realnou asymetrii deaktivace (CPE24 na .4
    vypnuta a na .5 aktivni, MGMT-VLAN naopak). Regenerace 2026-09-02 uz
    tenhle stav v laborce nenajde - obe sluzby jsou na obou zarizenich
    aktivni (multicast lab setup je mezitim sjednotil) - takze AR-22/AR-23
    smer se overuje na scenari vyrobenem `_deactivate_only_in_subject`, ne
    na realnych datech - test tim overuje mechanismus checku, ne stav
    laborky, ktera uz tuhle asymetrii nenese.

    Co porad musi platit: zadny JINY check nesmi na teto dvojici vratit
    FAIL nebo WARN. Kdyby to udelal, byl by to check, ktery je vzdy
    FAIL/WARN na zdrave sluzbe a prosel by tichem.

    KNOWN_LAB_ASYMMETRIES je uzka vyjimka (check id + podretezec zpravy),
    ne plosne "bfd_session_state se ignoruje" - to by schovalo check, ktery
    by byl trvale FAIL/WARN na zdrave sluzbe. Aktualne je tuple prazdny -
    jediny drive zaznamenany rozdil (BFD na L3VPN-CPE13-NNI peerovi
    2001:db8:11:13::b) zmizel s tim, jak .4 prisel o BFD zamer cely (task
    5b, 2026-09-02) - viz komentar u konstanty vyse.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")
    _deactivate_only_in_subject(old, new)

    result = api.evaluate(new, baseline=old, now=NOW)

    unexpected = [
        check
        for scope in result.scopes
        for check in scope.checks
        if check.id != "deactivation_state" and check.status in (Status.FAIL, Status.WARN)
        and not any(
            check.id == known_id and known_substring in (check.message or "")
            for known_id, known_substring in KNOWN_LAB_ASYMMETRIES
        )
    ]
    assert not unexpected, (
        "check jiny nez deactivation_state (a mimo KNOWN_LAB_ASYMMETRIES) vratil "
        f"FAIL/WARN na zdrave migraci: {[(c.id, c.status, c.message) for c in unexpected]}"
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
    # Bloky chodi v parech ('=' nad hlavickou, '=' pod ni) - hlavicka ale
    # nema pevny pocet radku: IRB bez EVPN linku dostava navic radek
    # "L2: ..." (spec 2026-09-02, l2_interfaces v identite). Puvodni kod
    # pocital s presne jednim radkem hlavicky (index+2 jako dolni okraj) -
    # s poznamkovym radkem navic se dolni okraj posunul na index+3, ktery
    # "rest = [i for i in starts if i > index + 2]" omylem vzal za zacatek
    # DALSIHO bloku, takze se telo bloku usteklo hned za poznamkou.
    pairs = list(zip(starts[0::2], starts[1::2]))

    blocks = []
    for position, (top, bottom) in enumerate(pairs):
        header = "\n".join(lines[top + 1 : bottom])
        if description not in header or service_type not in header:
            continue
        end = pairs[position + 1][0] if position + 1 < len(pairs) else lines.index("NESPAROVANO")
        blocks.append("\n".join(lines[top:end]))

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


def _deactivate_shared_route(old, new) -> tuple[str, str]:
    """Vypne TUTEZ routu TEZE sluzby v obou snimcich a vrati jeji popis + typ.

    Naivni "prvni scope se statickou routou v kazdem snimku" je vada: vnitrni
    break opousti jen vnitrni smycku a poradi scopu se mezi .4 a .5 lisit
    muze, takze by se v kazdem snimku vypnula jina sluzba. Vysledek by byl
    "v baselinu bezela, ted je vypnuta" = FAIL, ne WARN, ktery tenhle test
    meri - a selhani by vypadalo jako vada implementace.

    Parovat se musi i konkretni routa, ne jen sluzba: dve ruzne routy tehoz
    prefixu neexistuji, ale poradi v seznamu garantovane neni.

    Vraci i service_type: laborka (po regeneraci 2026-09-02) ma pripady, kdy
    dve ruzne sluzby (napr. E-LAN a Internet strana teze EVPN-VLAN-AWARE
    komponenty) sdileji stejny popis. Popis samotny proto scope neurcuje
    jednoznacne - volajici musi filtrovat i podle typu, jinak muze vybrat
    scope, ktery deaktivovanou routu vubec nenese.

    Odchylka od bodu z brief: samotne nastaveni "active": False v selektoru
    nestaci. `routes.py:155` vyzaduje `deactivated and subject is None` -
    tedy ze routa navic chybi z namerene routovaci tabulky (`facts["routes"]`
    subjektu). `synthetic_snapshot` ale fakta pocita PRED touto mutaci a
    napevno je oznaci jako "active": True (tests/conftest.py:174-179), takze
    bez tohoto kroku by zadny check nikdy nehlasil zadny nalez a test by
    tise merilo nic. Route proto mizi z faktu subjektu ("new" snimek), presne
    jak by to udelal skutecny kolektor u vypnute konfigurace.

    Fakta baselinu ("old") se schvalne nechavaji netknuta - vetev, kterou
    tenhle test cili (`routes.py:155`), se rozhoduje jen podle `subject is
    None` a `baseline_deactivated` (zamer ze selektoru, uz nastaveny vyse),
    ne podle faktu baselinu. Ty ovlivnuji jen zobrazenou `baseline_value`.
    """
    by_key = {}
    for snapshot, side in ((old, "old"), (new, "new")):
        for scope in snapshot.scopes:
            if scope.key is None or scope.key.description is None:
                continue
            for route in scope.selectors.static_routes:
                key = (
                    scope.key.description,
                    scope.key.service_type,
                    str(route.get("rib")),
                    str(route.get("prefix")),
                )
                by_key.setdefault(key, {})[side] = route

    shared = sorted(key for key, sides in by_key.items() if len(sides) == 2)
    assert shared, "fixture nema zadnou statickou routu pritomnou v obou snimcich"

    target = shared[0]
    for route in by_key[target].values():
        route["active"] = False

    description, service_type, rib, prefix = target
    new.facts.get("routes", {}).get(rib, {}).pop(prefix, None)
    return description, service_type


def test_deactivated_route_is_visible_without_detail(synthetic_snapshot):
    """Bod 14: zdrava sluzba s deaktivovanou routou se rozbali i bez --detail.

    Test jde pres api.evaluate a render, ne nad rucne slozenym ServiceView.
    Test nad ServiceView by prosel i nad rozbitou cestou, protoze by
    obesel prave to misto, kde se stav rozhoduje: engine.py:145 filtruje
    SKIPy pred Status.worst(), takze dokud deaktivace vyrabela SKIP,
    sluzba zustala PASS a text_report.py:390 jeji blok nerozbalil.

    Zabiji mutanta: navrat Outcome.SKIP misto DEGRADED v routes.py. Sluzba
    by zustala PASS - test padne uz na `status is Status.WARN` a k asserci
    na obsah bloku se nedostane. Ze by se blok bez PASS na Status.WARN
    skutecne nerozbalil, overuje samostatne text_report.py:390 podminka
    `detail or view.status is not Status.PASS` - overeno primo, mutaci te
    podminky na `if detail`.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    target, service_type = _deactivate_shared_route(old, new)

    result = api.evaluate(new, baseline=old, now=NOW)
    rendered = render(result)

    affected = [
        scope
        for scope in result.scopes
        if scope.identity.get("description") == target
        and scope.identity.get("service_type") == service_type
    ]
    assert affected, f"sluzba {target} ve vysledku neni"
    assert affected[0].status is Status.WARN

    block = _block_of(rendered, target, service_type)
    assert "deaktivovana" in block


def _counts_of_line(output: str, prefix: str) -> dict[str, int]:
    """Rozebere souhrnny radek na countery.

    Vlastni kopie helperu z tests/reporting/test_text_report.py - ten ho ma
    pro synteticke RunResulty a importovat ho sem by svazalo dva testovaci
    soubory kvuli dvema radkum.
    """
    line = next(l for l in output.splitlines() if l.strip().startswith(prefix))
    return {name: int(count) for count, name in re.findall(r"(\d+) (PASS|WARN|FAIL|SKIP)", line)}


def test_deactivated_element_moves_a_service_from_pass_to_warn(synthetic_snapshot):
    """Souhrn za sluzby musi deaktivovany prvek pocitat do WARN, ne do PASS.

    Nalez 3 specu vlny 9: tuhle vazbu nehlidal zadny test, takze zmena
    semantiky mohla countery rozpojit tise. Meri se na zrenderovanem radku
    'Sluzby:', ne na result.scopes - counter je vlastnost vystupu.

    Zabiji mutanta: navrat Outcome.SKIP v routes.py. Sluzba by zustala v
    PASS a souhrn by tvrdil, ze je vsechno v poradku.
    """
    def snapshots():
        return (
            synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration"),
            synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration"),
        )

    old, new = snapshots()
    baseline_run = api.evaluate(new, baseline=old, now=NOW)
    before = _counts_of_line(render(baseline_run), "Sluzby:")

    # Cerstve snimky, ne ty uz vyhodnocene - deaktivace se zapisuje do
    # zameru a sdileny objekt mezi dvema behy by meril poradi volani.
    old, new = snapshots()
    target, service_type = _deactivate_shared_route(old, new)

    # Cil MUSI byt PASS pred zmenou, jinak aserce nize nemeri nic: sluzba,
    # ktera uz WARN byla, se do counteru nepresune a rozdil vyjde nula.
    target_before = [
        scope for scope in baseline_run.scopes
        if scope.identity.get("description") == target
        and scope.identity.get("service_type") == service_type
    ]
    assert target_before, f"sluzba {target} ve vysledku neni"
    assert target_before[0].status is Status.PASS, (
        f"sluzba {target} nebyla pred zmenou PASS ({target_before[0].status}), "
        "test by presun counteru nemeril"
    )

    after = _counts_of_line(render(api.evaluate(new, baseline=old, now=NOW)), "Sluzby:")

    assert after["PASS"] == before["PASS"] - 1
    assert after["WARN"] == before["WARN"] + 1


def test_peer_moved_out_of_service_is_not_claimed_to_be_missing(synthetic_snapshot):
    """Bod 19 + oprava vlny 10, nalez 1: peer se zivou session nesmi byt
    hlasen jako 'v subjektu neni' - ani BGP checkem, ani BFD checkem.

    Blok sluzby a NEZARAZENO jsou dve nezavisle cesty. Blok jde pres
    ctx.baseline, coz jsou fakta profiltrovana BASELINE selektory, takze
    peera vidi. NEZARAZENO jde pres surova subject.facts['bgp']/['bfd'] a
    mnozinu assigned jen ze SUBJEKTOVYCH scopu, takze ho vidi taky. Dokud
    blok tvrdil 'v subjektu neni', rekly ty dve sekce o jednom peeru dve
    neslucitelne veci.

    Test modeluje UPLNY presun peera ze sluzby - peer mizi z
    target.selectors.bgp_neighbors I z target.selectors.bfd_peers, ne jen
    z prvniho z nich. Duvod: all_checks() radi podle `id` a
    "bfd_session_state" je pred "bgp_session_state" abecedne
    (bf < bg), takze _worst_message() vezme do souhrnneho radku hlasku z
    BFD checku, kdyz jsou oba checky na stejne nejhorsim stavu. Kdyby test
    modeloval presun jen v BGP a bfd_peers nechal na miste, BFD check by
    zustal v jine vetvi (BGP neni Established / bez session) a souhrnny
    radek by nesl bud BGP hlasku, nebo BFD hlasku z jine (spravne) vetve -
    v obou pripadech by test tu vadu neuvidel, protoze puvodni vadna BFD
    hlaska ("BFD bylo v baseline (Up), v subjektu neni nakonfigurovane")
    by se do vystupu vubec nedostala.

    Test asertuje OBE sekce v jednom behu. Kdyby asertoval jen blok, prosel
    by i nad implementaci, ktera peera z NEZARAZENO vyhodi - a to je jina
    varianta, kterou uzivatel vedome odmitl.

    Zabiji mutanta: navrat hlasky 'v baseline byl, v subjektu neni' (BGP)
    i navrat hlasky 'BFD bylo v baseline (...), v subjektu neni
    nakonfigurovane' (BFD).

    Aserce `peer in new.facts['bgp']` nize je POJISTKA PROTI VAKUOVOSTI a
    nesmi se odstranit. _facts_for() (tests/conftest.py) odvozuje
    facts['bgp'] (a facts['bfd']) ZE SELEKTORU, takze kdyby nekdo odebrani
    peera presunul pred stavbu snimku, zadna session by pro nej nevznikla -
    peer by se do NEZARAZENO nedostal a obe aserce nize by prosly, aniz by
    cokoli dokazaly. Tahle jedina aserce ten presun odhali.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    # Nahravka 2026-09-02 (task 5b, idealni pre-migracni stav .4) ukazala,
    # ze bfd-liveness-detection byl z BGP konfigurace na .4 odebran cely -
    # 172.20.20.4.yml uz pro tohoto peera nema zadny bfd_peers zamer, takze
    # _facts_for() (conftest.py) uz pro nej v old.facts["bfd"] nic
    # nesyntetizuje. Test overuje blok postaveny nad BASELINE session (viz
    # docstring), takze mu jedna Up session v baseline musi zustat - dopsana
    # rucne, protoze uz ji nejde odvodit ze skutecne .4 inventory.
    old.facts["bfd"]["152.11.13.2"] = {
        "state": "Up",
        "interface": "ge-0/0/2.13",
        "remote_state": "Up",
        "local_diagnostic": "None",
        "clients": ["BGP"],
        "detection_time": "9.000",
        "transmission_interval": "3.000",
        "multiplier": 3,
    }

    # Nahravka 2026-09-03 (task 5c, post-migration z .5) ukazala totez o
    # krok dal: bfd-liveness-detection byl device-wide odebran i na .5
    # (uzivatelske rozhodnuti odebrat BFD z OBOU routeru zaroven, viz task
    # 5c report) - 172.20.20.5.yml uz pro tohoto peera taky nema zadny
    # bfd_peers zamer, takze _facts_for() uz pro nej v new.facts["bfd"] nic
    # nesyntetizuje. Test presouva peera pryc ze sluzby AZ NAD HOTOVYM
    # SNIMKEM (viz docstring), takze potrebuje odkud ho odebrat - dopsana
    # rucne, protoze uz ji nejde odvodit ze skutecne .5 inventory.
    new.facts["bfd"]["152.11.13.2"] = {
        "state": "Up",
        "interface": "et-0/0/8.13",
        "remote_state": "Up",
        "local_diagnostic": "None",
        "clients": ["BGP"],
        "detection_time": "9.000",
        "transmission_interval": "3.000",
        "multiplier": 3,
    }

    # Selektor se meni AZ NAD HOTOVYM SNIMKEM - viz docstring.
    target = next(
        scope for scope in new.scopes
        if scope.id == "svc:INTERNET-CPE13-NNI:Internet"
    )
    peer = "152.11.13.2"
    assert peer in new.facts["bgp"], "fixture nema session peera, test by byl vakuovy"
    assert peer in new.facts["bfd"], "fixture nema BFD session peera, test by byl vakuovy"
    target.selectors.bgp_neighbors = [
        neighbor for neighbor in target.selectors.bgp_neighbors if neighbor != peer
    ]
    target.selectors.bfd_peers = [
        b for b in target.selectors.bfd_peers if b.get("peer") != peer
    ]

    result = api.evaluate(new, baseline=old, now=NOW)
    rendered = render(result)

    # Cela hlaska je v souhrnne tabulce ve sloupci NALEZ, ne v bloku: blok
    # tiskne status/label/value/change (_block v text_report.py), message
    # nikdy. Zmereno.
    summary_row = next(
        line
        for line in rendered.splitlines()
        if line.startswith("FAIL") and "INTERNET-CPE13-NNI" in line
    )
    assert "v baseline patril k teto sluzbe, v subjektu uz ne" in summary_row
    assert "v subjektu neni" not in rendered
    # Puvodni (opravena) BFD hlaska tvrdila o zarizeni to, co check vi jen
    # o sluzbe - nesmi se do souhrnneho radku vratit.
    assert "BFD bylo v baseline" not in rendered
    assert "neni nakonfigurovane" not in rendered

    # V bloku je videt `value`, a ta se meni taky - u obou checku.
    block = _block_of(rendered, "INTERNET-CPE13-NNI", "Internet")
    assert "BGP status (152.11.13.2)" in block
    assert "BFD (152.11.13.2)" in block
    assert block.count("v baseline patril k teto sluzbe, v subjektu uz ne") == 2

    # Hledat uvnitr sekce, ne kdekoli ve vystupu: NEZARAZENO sdili
    # formatovaci literaly se sousednimi sekcemi, takze `x in rendered` by
    # proslo i kdyby se sekce vubec nevytiskla (pravidlo vlny 5).
    unassigned = rendered.split("NEZARAZENO")[-1]
    assert peer in unassigned


def _deactivate_shared_service(old, new):
    """Vypne tutez sluzbu v obou snimcich a vrati jeji description.

    Parovani podle DVOJICE (description, service_type): samotny description
    nestaci, fixtures nesou EVPN-VLAN-AWARE-INTERNET dvakrat - jednou jako
    Internet a jednou jako E-LAN. Podle samotneho description by se v kazdem
    snimku mohla vypnout jina a test by meril nesparovanou sluzbu.
    """
    target = ("EVPN-VLAN-AWARE-CPE13-NNI", "E-LAN")
    hit = 0
    for snapshot in (old, new):
        for scope in snapshot.scopes:
            if scope.kind != "service":
                continue
            if (scope.key.description, scope.key.service_type) == target:
                scope.interface_active = False
                hit += 1
    assert hit == 2, "sluzba neni v obou snimcich, test by meril nesparovanou"
    return target[0]


def test_deactivated_service_block_has_one_skip_row_without_detail(synthetic_snapshot):
    """Bod 18: blok deaktivovane sluzby prestane tisknout N stejnych radku.

    Test jde pres api.evaluate a render, ne nad rucne slozenym ServiceView -
    slevani se overuje na skutecne zrenderovanem vystupu produkcni cesty,
    ne na rucne poskladanem ServiceView.

    Zmereno: mutant `build_view(scope)` bez `detail` v text_report.py tenhle
    test nezabiji (defaultni `detail=False` da stejny vysledek jako
    predavany `detail=False`). Ten mutant zabiji sousedni
    `test_detail_expands_the_deactivated_service_block`, ktery pouziva
    `detail=True`.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    target = _deactivate_shared_service(old, new)
    result = api.evaluate(new, baseline=old, now=NOW)

    block = _block_of(render(result), target, "E-LAN")

    assert "Ostatni checky" in block
    assert "preskoceno" in block
    # Sedm deaktivacnich SKIPu se slilo, radek Deaktivace zustava.
    assert block.count("interface deactivated") == 1
    assert "Deaktivace" in block


def test_detail_expands_the_deactivated_service_block(synthetic_snapshot):
    """S --detail ma tyz blok vsechny puvodni radky.

    Zabiji mutanta: slevani bez ohledu na priznak detail.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    target = _deactivate_shared_service(old, new)
    result = api.evaluate(new, baseline=old, now=NOW)

    block = _block_of(render(result, detail=True), target, "E-LAN")

    assert "Ostatni checky" not in block
    assert block.count("interface deactivated") > 1


def test_json_report_keeps_every_check_regardless_of_detail(synthetic_snapshot):
    """Slevani je vlastnost textoveho reportu, ne vysledku behu.

    RunResult.to_dict() stavi vystup z CheckResultu, ne z ServiceView.
    Kdyby slevani proteklo do nej, strojovy konzument by o preskocenych
    checkach prisel a nic by mu to nereklo.

    Tvrzeni o konkretnim mutantovi (presun slevani do engine.py) neni
    overene spustenim - je to viceradkove presunuti kodu, ne jednoradkovy
    sed. Test hlida strukturalni fakt: `Ostatni checky` se v JSON labelech
    neobjevi a vsech devet deaktivacnich SKIPu (Agregatni routa, BFD,
    EVPN ESI status, EVPN instance, EVPN MAC count, Interface errors,
    Interface status, Interface traffic, Staticka routa - zmereno na tomto
    snimku), ktere se v textovem reportu slevaji do jedineho radku, je
    v JSON pritomno jednotlive.

    Oprava vlny 10, nalez 3: JSON take musi nest znacku `skipped_because`
    (klic SKIPPED_BECAUSE v CheckResult.details) - spec ji chtel propsat
    vedome a plan slibil test, ktery to zafixuje. Bez teto aserce by
    slouceni znacky do to_dict() proslo bez povsimnuti.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    target = _deactivate_shared_service(old, new)
    result = api.evaluate(new, baseline=old, now=NOW)

    payload = result.to_dict()
    scope = next(
        item for item in payload["scopes"]
        if item.get("identity", {}).get("description") == target
        and item.get("identity", {}).get("service_type") == "E-LAN"
    )
    labels = [check.get("label") for check in scope["checks"]]

    assert "Ostatni checky" not in labels

    deactivation_check = next(
        check for check in scope["checks"] if check.get("id") == "deactivation_state"
    )
    assert deactivation_check.get("details", {}).get("skipped_because") is None
    skipped_labels = {
        check.get("label") for check in scope["checks"]
        if check.get("details", {}).get("skipped_because") == "service_deactivated"
    }
    assert skipped_labels == {
        "Agregatni routa",
        "BFD",
        "EVPN ESI status",
        "EVPN instance",
        "EVPN MAC count",
        "Interface errors",
        "Interface status",
        "Interface traffic",
        "Staticka routa",
    }


def test_peers_of_one_service_carry_different_prefix_counts(synthetic_snapshot):
    """Dva peery jedne sluzby musi mit ruzne countery.

    Sdilene fixtures davaly kazdemu peerovi 14/14/14/3, takze zamena peeru
    v kodu by na reportu nebyla videt vubec. Zmereno pri psani planu vlny
    10: rozruzneni counteru neshodilo ani jeden z 676 testu, tedy jejich
    hodnoty nehlidal nikdo. Bez teto aserce by se fixtures mohly kdykoli
    vratit k uniformnim cislum a nic by to nevytklo.

    Zabiji mutanta: `_ribs_for` vracejici pro kazdeho peera tataz cisla
    (napr. natvrdo 14/14/14/3) misto volani `_counters_for(peer)`.
    """
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    service = next(
        scope for scope in new.scopes
        if scope.id == "svc:L3VPN-CPE13-NNI:IPVPN"
    )
    peers = service.selectors.bgp_neighbors
    assert len(peers) >= 2, "sluzba uz nema dva peery, test by byl vakuovy"

    received = [
        counters["received"]
        for peer in peers
        for counters in new.facts["bgp"][peer]["ribs"].values()
    ]

    assert len(set(received)) == len(received), f"countery se opakuji: {received}"


def test_prefix_counts_match_between_baseline_and_subject(synthetic_snapshot):
    """Rozruznene countery musi byt v obou snimcich stejne.

    Kdyby se lisily mezi snimky, bgp_prefix_counts by zacal hlasit rozdil
    u kazde zdrave sluzby a fixtures by prestaly byt zdravou vychozi sadou.

    Zabiji mutanta: countery odvozene z neceho, co se mezi snimky .4 a .5
    lisi pro TOTEZ peera (napr. z poradi peeru v ramci snimku), coz by dalo
    jina cisla pro stejnou adresu v .4 a v .5.

    Zmereno (oprava vlny 10, nalez 4): odvozeni ze `len(peer)` misto ze
    souctu ordinalu adresy tenhle test nezabije (685 passed) - `len(peer)`
    je porad funkce SAME adresy, takze je mezi snimky shodna. Docstring
    puvodne tvrdil sirsi vec ("cehokoli jineho nez z adresy peera"), coz
    tenhle test nehlida.
    """
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")

    shared = set(old.facts["bgp"]) & set(new.facts["bgp"])
    assert shared, "snimky nemaji spolecneho peera, test by byl vakuovy"

    for peer in sorted(shared):
        assert old.facts["bgp"][peer]["ribs"] == new.facts["bgp"][peer]["ribs"], peer


def test_l2_l3_link_renders_paired_blocks(synthetic_snapshot):
    snapshot = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post")
    snapshot.facts["evpn_instance"] = {
        "EVPN-VLAN-AWARE-POP1": {
            "local_interfaces": {
                "total": 1,
                "up": 1,
                "entries": [{"name": "ae0.15", "status": "Up"}],
            },
            "irb_interfaces": {
                "total": 1,
                "up": 1,
                "entries": [
                    {"name": "irb.15", "status": "Up", "l3_context": "L3VPN-CPE14-UNI"}
                ],
            },
            "neighbors": {"total": 1, "addresses": ["10.255.0.1"]},
            "esis": {},
        }
    }
    result = evaluate_snapshots(snapshot)

    by_id = {scope.scope_id: scope for scope in result.scopes}
    l3_scope = next(
        scope for scope in result.scopes
        if scope.link and scope.link["role"] == "l3"
    )
    l2_scope = by_id[l3_scope.link["peers"][0]["scope_id"]]
    assert "irb.15" in l3_scope.identity["interfaces"]
    assert l2_scope.identity["interfaces"] == ["ae0.15"]

    # L2 blok hned za L3 blokem
    ids = [scope.scope_id for scope in result.scopes]
    assert ids.index(l2_scope.scope_id) == ids.index(l3_scope.scope_id) + 1

    # errors/traffic v L3 bloku odkazuji na L2
    labels = {check.label: check for check in l3_scope.checks}
    pointer = labels["Interface errors / traffic"]
    assert pointer.value == "mereno na L2 (ae0.15) - viz blok nize"
    assert not any(
        check.id == "interface_traffic" for check in l3_scope.checks
    )

    output = render(result, detail=True)
    assert "L2 cast: ae0.15 v EVPN-VLAN-AWARE-POP1 (blok nize)" in output
    assert "L3 cast: irb.15 v L3VPN-CPE14-UNI (blok vyse)" in output


def test_sluzba_stoji_za_svym_l1_blokem(synthetic_snapshot):
    old = synthetic_snapshot(DEVICE_4, "172.20.20.4", "pre-migration")
    new = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")
    evaluated = api.evaluate(new, baseline=old, now=NOW)

    order = [(r.scope_id, (r.key or {}).get("service_type"),
              (r.identity or {}).get("physical_interfaces") or [None],
              (r.identity or {}).get("interfaces") or [None])
             for r in evaluated.scopes]
    last_l1_port = None
    l1_ports = {ifaces[0] for _, t, _, ifaces in order if t == "Layer1"}
    for scope_id, service_type, parents, ifaces in order:
        if service_type == "Layer1":
            last_l1_port = ifaces[0]
        elif parents[0] in l1_ports:
            assert parents[0] == last_l1_port, (
                f"{scope_id}: rodic {parents[0]}, ale posledni L1 blok {last_l1_port}")


def test_synthetic_mvpn_scope_has_pim_join(synthetic_snapshot):
    """Zdravy MVPN receiver ma join s IRB jako downstream (spec 2026-09-07);
    bez nej by pim_join check zil jen z INFO zrcadla a sender vetev
    forwarding checku by synteza nikdy neprosla."""
    snapshot = synthetic_snapshot(DEVICE_5, "172.20.20.5", "post-migration")
    table = snapshot.facts["pim_join"]["MULTICAST-STREAM-B-MUX1-RECEIVER"]
    (join,) = table.values()
    assert join["downstream_interfaces"] == ["irb.2"]
    assert join["upstream_interface"] == "Through BGP"
