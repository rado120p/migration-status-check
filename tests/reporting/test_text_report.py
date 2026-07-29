import json

from migration_validator.models.result import (
    CheckResult,
    MatchInfo,
    RunResult,
    ScopeResult,
    Severity,
    Status,
)
from migration_validator.reporting.json_report import to_json
from migration_validator.reporting.text_report import filter_result, render


def _legacy_check(check_id, status, message):
    return CheckResult(
        id=check_id,
        mode="both",
        status=status,
        severity=Severity.ADVISORY,
        message=message,
    )


def _legacy_result() -> RunResult:
    return RunResult(
        evaluated_at="2026-07-24T11:40:02Z",
        subject={"address": "172.20.20.5", "phase": "post-migration", "captured_at": "x"},
        baseline={"address": "172.20.20.4", "phase": "pre-migration", "captured_at": "y"},
        summary={
            "pass": 3, "warn": 1, "fail": 1, "skip": 0,
            "scopes_matched": 2, "unmatched_baseline": 1, "unmatched_subject": 1,
        },
        scopes=[
            ScopeResult(
                scope_id="svc:INTERNET-CPE13-NNI:Internet",
                key={"description": "INTERNET-CPE13-NNI", "service_type": "Internet"},
                status=Status.PASS,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[_legacy_check("interface_state", Status.PASS, "up/up")],
            ),
            ScopeResult(
                scope_id="svc:L3VPN-CPE13-NNI:IPVPN",
                key={"description": "L3VPN-CPE13-NNI", "service_type": "IPVPN"},
                status=Status.WARN,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[
                    _legacy_check(
                        "interface_traffic", Status.WARN, "provoz -72 % (410 -> 115 pps)"
                    ),
                    _legacy_check("interface_state", Status.PASS, "up/up"),
                ],
            ),
            ScopeResult(
                scope_id="svc:EVPN-VPWS-CPE13-NNI:E-Line",
                key={"description": "EVPN-VPWS-CPE13-NNI", "service_type": "E-Line"},
                status=Status.FAIL,
                match=MatchInfo(status="matched", method="description+service_type"),
                checks=[_legacy_check("evpn_vpws_status", Status.FAIL, "vpws-sid-pe-status: Down")],
            ),
        ],
        unmatched={
            "baseline": [
                {
                    "scope_id": "svc:L3VPN-CPE99-NNI:IPVPN",
                    "description": "L3VPN-CPE99-NNI",
                    "service_type": "IPVPN",
                    "reason": "zadny kandidat na subject",
                }
            ],
            "subject": [
                {
                    "scope_id": "svc:EVPN-VLAN-AWARE-INTERNET:E-LAN",
                    "description": "EVPN-VLAN-AWARE-INTERNET",
                    "service_type": "E-LAN",
                    "reason": "nova sluzba, chybi baseline",
                }
            ],
        },
        unassigned={"bgp_peers": []},
    )


def test_render_contains_header_with_both_devices():
    """Zjistuje i chybu, kterou puvodni test nechytil: adresa/faze subjektu
    tvrdil jen test_cli.py, ne tento soubor."""
    output = render(_legacy_result())
    assert "172.20.20.4" in output and "172.20.20.5" in output
    assert "pre-migration" in output and "post-migration" in output


def test_render_contains_summary_counts():
    output = render(_legacy_result())
    assert "3 PASS" in output
    assert "1 WARN" in output
    assert "1 FAIL" in output
    assert "Sparovano 2" in output


def test_render_lists_services_with_worst_check_message():
    """Musi tvrdit, ze zprava sedi na souhrnnem radku sluzby (NALEZ sloupec),
    ne jen ze se retezec objevi nekde ve vystupu - jinak by test prezil i
    smazani NALEZ sloupce, protoze WARN/FAIL sluzby se rozbaluji do bloku a
    _legacy_check nema `value`, takze se zprava echuje i jako hodnota radku
    uvnitr bloku.

    Souhrnny radek sluzby nezacina mezerou (na rozdil od radku bloku, ktere
    zacinaji " WARN " / " FAIL "), takze `startswith("WARN  ")` bez uvodni
    mezery vybere presne jen souhrnny radek.
    """
    output = render(_legacy_result())
    lines = output.splitlines()

    warn_row = next(
        line for line in lines
        if line.startswith("WARN  ") and "L3VPN-CPE13-NNI" in line
    )
    assert warn_row.endswith("provoz -72 % (410 -> 115 pps)")

    fail_row = next(
        line for line in lines
        if line.startswith("FAIL  ") and "EVPN-VPWS-CPE13-NNI" in line
    )
    assert fail_row.endswith("vpws-sid-pe-status: Down")


def test_filter_by_text_matches_description():
    filtered = filter_result(_legacy_result(), text="L3VPN")
    assert [scope.scope_id for scope in filtered.scopes] == ["svc:L3VPN-CPE13-NNI:IPVPN"]


def test_filter_by_status_keeps_only_requested():
    filtered = filter_result(_legacy_result(), statuses={Status.FAIL, Status.WARN})
    assert {scope.status for scope in filtered.scopes} == {Status.WARN, Status.FAIL}


def test_filter_does_not_touch_unmatched():
    filtered = filter_result(_legacy_result(), text="NEEXISTUJE")
    assert filtered.scopes == []
    assert len(filtered.unmatched["baseline"]) == 1
    assert len(filtered.unmatched["subject"]) == 1


def test_to_json_is_valid_and_keeps_czech_characters():
    result = _legacy_result()
    result.scopes[0].checks[0].message = "rozhrani je v poradku"
    payload = json.loads(to_json(result))
    assert payload["schema_version"] == 1
    assert payload["scopes"][0]["checks"][0]["message"] == "rozhrani je v poradku"


def test_render_always_shows_unmatched_section():
    output = render(_legacy_result())
    assert "NESPAROVANO" in output
    assert "L3VPN-CPE99-NNI" in output
    assert "EVPN-VLAN-AWARE-INTERNET" in output


def test_unmatched_section_present_even_when_all_green():
    result = _legacy_result()
    for scope in result.scopes:
        scope.status = Status.PASS
    assert "NESPAROVANO" in render(result)


def test_unmatched_label_longer_than_32_chars_is_not_truncated():
    """Regrese: NESPAROVANO drivejsi sazelo popisek napevno na {:<32.32},
    stejna vada jako kdysi v souhrnne tabulce. Realny nazev sluzby z laborky
    (clab-pop-migration-MX1-POP1 ge-0/0/1, 37 znaku) je delsi nez 32 - orezany
    nazev je presne to, co ma NESPAROVANO zabranit prehlednout."""
    result = _legacy_result()
    long_name = "clab-pop-migration-MX1-POP1 ge-0/0/1"
    assert len(long_name) > 32
    result.unmatched["baseline"].append(
        {
            "scope_id": "svc:clab-pop-migration-MX1-POP1 ge-0/0/1:Core",
            "description": long_name,
            "service_type": "Core",
            "reason": "zadny kandidat na subject",
        }
    )

    output = render(result)

    assert long_name in output


def _check(check_id, status, message, *, label, value, family=None,
           mode="state", baseline_value=None, delta=None):
    return CheckResult(
        id=check_id, mode=mode, status=status, severity=Severity.ADVISORY,
        message=message, label=label, family=family, value=value,
        baseline_value=baseline_value, delta=delta,
    )


def _dual_stack_scope(status=Status.WARN) -> ScopeResult:
    return ScopeResult(
        scope_id="svc:INTERNET-CPE13-NNI:Internet",
        key={"description": "INTERNET-CPE13-NNI", "service_type": "Internet"},
        status=status,
        match=MatchInfo(
            status="matched",
            baseline_interfaces=["ge-0/0/2.13"],
            subject_interfaces=["et-0/0/8.13"],
        ),
        checks=[
            _check("interface_state", Status.PASS, "up", label="Interface admin status", value="Up"),
            _check("arp_present", Status.PASS, "arp ok", label="ARP", family=4,
                   value="0c:00:ef:5e:df:01 -> 152.11.13.2"),
            _check("nd_present", Status.PASS, "nd ok", label="ND", family=6,
                   value="0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b"),
            _check("interface_traffic", Status.WARN, "pokles", label="Interface traffic in",
                   value="460 pps", mode="both", baseline_value="520 pps", delta="-12 %"),
        ],
        identity={
            "description": "INTERNET-CPE13-NNI",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "ipv4": ["152.11.13.1/30"],
            "ipv6": ["2001:abcd:11:13::a/127"],
            "virtual_gw_v4": [],
            "virtual_gw_v6": [],
        },
    )


def _many_ranges_scope() -> ScopeResult:
    """Sluzba se ctyrmi IPv6 rozsahy - hlavicka sekce prerusta tabulku.

    Radky jsou zamerne kratke, aby o sirce bloku rozhodovala prave ta
    hlavicka a nic jineho.
    """
    return ScopeResult(
        scope_id="svc:MANY:Internet",
        key={"description": "MANY", "service_type": "Internet"},
        status=Status.WARN,
        match=MatchInfo(
            status="matched",
            baseline_interfaces=["ge-0/0/2.13"],
            subject_interfaces=["et-0/0/8.13"],
        ),
        checks=[
            _check("nd_present", Status.PASS, "nd ok", label="ND", family=6, value="ok"),
        ],
        identity={
            "description": "MANY",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "ipv4": [],
            "ipv6": [
                "2001:abcd:11:13::a/127",
                "2001:abcd:11:14::a/127",
                "2001:abcd:11:15::a/127",
                "2001:abcd:11:16::a/127",
            ],
            "virtual_gw_v4": [],
            "virtual_gw_v6": [],
        },
    )


def _result(scopes, *, baseline=True) -> RunResult:
    return RunResult(
        evaluated_at="2026-07-28T11:40:02Z",
        subject={"address": "172.20.20.5", "phase": "post-migration", "captured_at": "x"},
        baseline=(
            {"address": "172.20.20.4", "phase": "pre-migration", "captured_at": "y"}
            if baseline
            else None
        ),
        summary={"pass": 1, "warn": 1, "fail": 0, "skip": 0,
                 "scopes_matched": 1, "unmatched_baseline": 0, "unmatched_subject": 0},
        scopes=scopes,
    )


def test_ipv4_section_comes_before_ipv6():
    output = render(_result([_dual_stack_scope()]))

    assert output.index("-- IPv4") < output.index("-- IPv6")


def test_section_header_carries_the_address():
    output = render(_result([_dual_stack_scope()]))

    assert "-- IPv4  152.11.13.1/30" in output
    assert "-- IPv6  2001:abcd:11:13::a/127" in output


def test_long_ipv6_value_is_not_truncated():
    """Orezana IPv6 adresa je horsi nez zadna."""
    output = render(_result([_dual_stack_scope()]))

    assert "0c:00:ef:5e:df:01 -> 2001:abcd:11:13::b" in output


def test_columns_hold_the_line_across_sections():
    """Sirky se pocitaji z celeho bloku, ne z kazde sekce zvlast."""
    output = render(_result([_dual_stack_scope()]))
    data_lines = [
        line for line in output.splitlines()
        if line.startswith((" PASS |", " WARN |", " FAIL |", " SKIP |"))
    ]
    positions = {line.index(" : ") for line in data_lines}

    assert len(positions) == 1, f"sloupec s hodnotou se lame: {positions}"


def test_passing_service_is_collapsed_by_default():
    output = render(_result([_dual_stack_scope(status=Status.PASS)]))

    assert "-- IPv4" not in output


def test_failing_service_is_expanded_by_default():
    output = render(_result([_dual_stack_scope(status=Status.FAIL)]))

    assert "-- IPv4" in output


def test_detail_expands_passing_service():
    output = render(_result([_dual_stack_scope(status=Status.PASS)]), detail=True)

    assert "-- IPv4" in output


def test_pass_subcheck_is_shown_inside_a_non_pass_block():
    """Headline chovani tohoto tasku: blok WARN/FAIL sluzby ukazuje VSECHNY
    radky bloku, ne jen ten, ktery zpusobil WARN/FAIL - jinak by PASS radek
    (Interface admin status) v ramci WARN sluzby uvnitr bloku chybel."""
    output = render(_result([_dual_stack_scope(status=Status.WARN)]))
    lines = output.splitlines()

    assert any(line.startswith(" PASS | Interface admin status") for line in lines), (
        "PASS radek (Interface admin status) chybi uvnitr WARN bloku"
    )


def test_change_column_is_absent_without_baseline():
    output = render(_result([_dual_stack_scope()], baseline=False))

    assert "ZMENA" not in output


def test_change_column_shows_previous_value():
    output = render(_result([_dual_stack_scope()]))

    assert "bylo 520 pps" in output


def test_ports_are_in_the_summary_row():
    output = render(_result([_dual_stack_scope()]))

    assert "ge-0/0/2.13" in output
    assert "et-0/0/8.13" in output


def _long_named_scope() -> ScopeResult:
    """Realny tvar z laborky: nazev sluzby i RI delsi nez stare napevno dane
    sirky sloupcu (SLUZBA 26, RI 17) - presne to, co na 172.20.20.5
    rozjelo zarovnani souhrnne tabulky."""
    return ScopeResult(
        scope_id="svc:clab-pop-migration-MX1-POP1 ge-0/0/1:Core",
        key={
            "description": "clab-pop-migration-MX1-POP1 ge-0/0/1",  # 37 znaku
            "service_type": "Core",
        },
        status=Status.WARN,
        match=MatchInfo(
            status="matched",
            baseline_interfaces=["ge-0/0/1.0"],
            subject_interfaces=["et-0/0/1.0"],
        ),
        checks=[
            _check(
                "interface_traffic", Status.WARN, "et-0/0/1: input_pps 0 pps",
                label="Interface traffic in", value="0 pps", mode="both",
            ),
        ],
        identity={
            "description": "clab-pop-migration-MX1-POP1 ge-0/0/1",
            "service_type": "Core",
            "service_subtype": None,
            "routing_instance": "EVPN-VLAN-AWARE-EX-POP1",  # 23 znaku
            "ipv4": [],
            "ipv6": [],
            "virtual_gw_v4": [],
            "virtual_gw_v6": [],
        },
    )


def test_summary_table_columns_stay_aligned_for_long_real_names():
    """Regrese na live nalez: souhrnna tabulka mela napevno dane sirky
    sloupcu (SLUZBA:<26, RI:<17), ktere realna data z laborky prekrocila -
    dlouhy nazev sluzby/RI se nalepil na dalsi sloupec bez mezery a vsechno
    napravo se posunulo. Sirky se ted pocitaji z dat, stejne jako v _block().
    """
    long_scope = _long_named_scope()
    output = render(_result([_dual_stack_scope(), long_scope]))
    lines = output.splitlines()

    header = next(line for line in lines if line.startswith("STAV "))
    long_row = next(
        line for line in lines if "clab-pop-migration-MX1-POP1 ge-0/0/1" in line
    )
    short_row = next(
        line for line in lines if "INTERNET-CPE13-NNI" in line and line.startswith("WARN")
    )

    # Nic se neorizne - cely nazev i RI musi byt ve vystupu cele.
    assert "clab-pop-migration-MX1-POP1 ge-0/0/1" in long_row
    assert "EVPN-VLAN-AWARE-EX-POP1" in long_row

    # TYP sloupec zacina na stejne pozici na vsech radcich vcetne hlavicky -
    # to je presne to, co pri napevno dane sirce prestalo platit.
    type_col = header.index("TYP")
    assert long_row[type_col:type_col + 4].strip() == "Core"
    assert short_row[type_col:type_col + 8].strip() == "Internet"


def _vgw_scope() -> ScopeResult:
    """EVPN-VLAN-AWARE-INTERNET z laborky: irb.14 ma virtual gateway."""
    return ScopeResult(
        scope_id="svc:EVPN-VLAN-AWARE-INTERNET:Internet",
        key={"description": "EVPN-VLAN-AWARE-INTERNET", "service_type": "Internet"},
        status=Status.FAIL,
        match=MatchInfo(
            status="matched",
            baseline_interfaces=["ge-0/0/5.0"],
            subject_interfaces=["irb.14"],
        ),
        checks=[
            _check("arp_present", Status.FAIL, "zadny zaznam", label="ARP", family=4,
                   value="zadny zaznam"),
        ],
        identity={
            "description": "EVPN-VLAN-AWARE-INTERNET",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "ipv4": ["152.11.14.2/29"],
            "ipv6": [],
            "virtual_gw_v4": ["152.11.14.1"],
            "virtual_gw_v6": [],
        },
    )


def test_virtual_gateway_is_shown_in_the_section_header():
    output = render(_result([_vgw_scope()]))

    assert "-- IPv4  152.11.14.2/29   VGW 152.11.14.1" in output


def test_renderer_omits_a_family_with_no_rows():
    """Renderer sam o rodinach nerozhoduje - vykresli sekci prave tehdy,
    kdyz do ni nejaky radek patri.

    Drive se tenhle test jmenoval po sluzbe bez IPv6 a tvrdil, ze takova
    sluzba IPv6 sekci nedostane. Prochazel ale jen proto, ze jeho fixture
    zadny check s family=6 neobsahovala - o chovani cele retezec checky ->
    view -> sazba nerikal nic. To, co slibovalo jeho jmeno, overuje
    test_service_without_ipv6_has_no_ipv6_section v tests/test_end_to_end.py
    na skutecnych datech.
    """
    output = render(_result([_vgw_scope()]))

    assert "-- IPv4  152.11.14.2/29" in output
    assert "-- IPv6" not in output


def test_block_frame_agrees_with_its_widest_line():
    """Presne to selhani, ktere AR-5 resi: ramec kratsi nez hlavicka.

    Hlavicka bloku (SYMBOL, description, service_type, porty, RI) nese
    volny text bez horni meze delky, takze musi byt v `body` zahrnuta -
    puvodni filtr ji vynechaval (" STAV |" apod. nesedi na format hlavicky
    " PASS  <description> ...") a test tak nemohl chybu vubec zachytit.
    """
    _assert_frame_wraps_block(render(_result([_dual_stack_scope()])))


def test_block_frame_agrees_with_its_widest_line_when_family_has_many_ranges():
    """Sluzba s vice rozsahy v rodine - presne ten pripad, kvuli kteremu
    vzniklo AR-5b, a na kterem ramec praskl potreti.

    Hlavicka sekce se doplnovala NA sirku bloku, ale jeji vlastni delka se
    do te sirky nikdy nezapocitala. U jedne adresy na rodinu je hlavicka
    kratsi nez tabulka a nepozna se to; u ctyr rozsahu prerostla ramec o
    desitky znaku.
    """
    _assert_frame_wraps_block(render(_result([_many_ranges_scope()])))


def _assert_frame_wraps_block(output: str) -> None:
    """Zadny radek bloku nesmi prerust jeho ramec.

    Meri se VSECHNY neprazdne radky bloku, ne vyjmenovane prefixy. Puvodni
    allowlist (" STAV |", " PASS |" a spol.) hlavicky sekci zacinajici
    " -- " z mereni vylucoval, takze test napsany na tohle selhani byl
    vuci nemu slepy - a stejne slepy by byl vuci kazdemu novemu druhu
    radku, ktery renderer pribude.
    """
    lines = output.splitlines()

    start = next(i for i, line in enumerate(lines) if line and set(line) == {"="})
    end = lines.index("NESPAROVANO")
    block = [line for line in lines[start:end] if line.strip()]

    frame = [line for line in block if set(line) == {"="}]
    assert frame, "blok nema ramec"
    assert len(block) > len(frame), "blok nema zadny radek"

    widest = max(block, key=len)
    assert len(widest) <= len(frame[0]), (
        f"radek {len(widest)} znaku prerusta ramec {len(frame[0])} znaku:\n{widest}"
    )


def _long_description_scope() -> ScopeResult:
    """Description a RI delsi nez cela sloupcova tabulka - dukaz na AR-5."""
    return ScopeResult(
        scope_id="svc:LONG:Internet",
        key={"description": "LONG", "service_type": "Internet"},
        status=Status.FAIL,
        match=MatchInfo(
            status="matched",
            baseline_interfaces=["ge-0/0/5.0"],
            subject_interfaces=["irb.14"],
        ),
        checks=[
            _check("arp_present", Status.FAIL, "zadny zaznam", label="ARP", family=4,
                   value="x"),
        ],
        identity={
            "description": "VELMI-DLOUHY-NAZEV-SLUZBY-KTERY-JE-DELSI-NEZ-CELA-TABULKA-SLOUPCU",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": "STEJNE-VELMI-DLOUHY-NAZEV-ROUTING-INSTANCE-Z-LABORATORE",
            "ipv4": ["152.11.14.2/29"],
            "ipv6": [],
            "virtual_gw_v4": [],
            "virtual_gw_v6": [],
        },
    )


def test_frame_covers_a_header_line_longer_than_the_column_table():
    """Sirka bloku se pocita i z hlavickoveho radku, ne jen ze sloupcu.

    Description i routing_instance jsou tu zamerne delsi, nez cely sloupcovy
    blok (STAV/CHECK/HODNOTA/ZMENA) kdy vysazen sam - kdyby `width` bral v
    uvahu jen ten, ramec by byl kratsi nez hlavicka.
    """
    output = render(_result([_long_description_scope()]))
    lines = output.splitlines()

    frame_index = next(i for i, line in enumerate(lines) if line and set(line) == {"="})
    frame_line = lines[frame_index]
    header = lines[frame_index + 1]
    assert "VELMI-DLOUHY-NAZEV-SLUZBY" in header, "spatny radek - to neni hlavicka bloku"

    assert len(header) <= len(frame_line), (
        f"hlavicka bloku ({len(header)}) prerustla ramec ({len(frame_line)})"
    )
