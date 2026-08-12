import io
import json
import re

from migration_validator.models.result import (
    CheckResult,
    MatchInfo,
    RunResult,
    ScopeResult,
    Severity,
    Status,
)
from migration_validator.reporting.json_report import to_json
from migration_validator.reporting.text_report import _colorize, filter_result, render, use_color


class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_use_color_defaults_to_isatty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert use_color(stream=_Tty()) is True
    assert use_color(stream=io.StringIO()) is False


def test_use_color_respects_no_color_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert use_color(stream=_Tty()) is False


def test_use_color_empty_no_color_counts_as_unset(monkeypatch):
    # Konvence no-color.org: vypina jen NEPRAZDNA hodnota.
    monkeypatch.setenv("NO_COLOR", "")
    assert use_color(stream=_Tty()) is True


def test_use_color_force_on_beats_pipe_and_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert use_color(force_on=True, stream=io.StringIO()) is True


def test_use_color_force_off_beats_everything(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert use_color(force_on=True, force_off=True, stream=_Tty()) is False


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


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
            "pass": 3, "warn": 1, "fail": 1, "skip": 0, "info": 0,
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


def test_color_wraps_status_tokens():
    output = render(_legacy_result(), color=True)
    assert "\x1b[32mPASS\x1b[0m" in output
    assert "\x1b[33mWARN\x1b[0m" in output
    assert "\x1b[31mFAIL\x1b[0m" in output


def test_color_paints_names_in_counts_lines():
    # Radky Sluzby:/Checky: barvi nazev stavu, cislo pred nim ne.
    output = render(_legacy_result(), color=True)
    assert "1 \x1b[31mFAIL\x1b[0m" in output
    assert "\x1b[31m1" not in output


def test_colorize_uses_dim_for_skip_and_cyan_for_info():
    # Primy assert na byty: strip-equality test escape sekvence odstrani,
    # takze zamenu kodu SKIP/INFO by bez tohoto testu nic nechytilo.
    assert _colorize(Status.SKIP, "SKIP", True) == "\x1b[2mSKIP\x1b[0m"
    assert _colorize(Status.INFO, "INFO", True) == "\x1b[36mINFO\x1b[0m"


def test_color_off_emits_no_ansi():
    assert "\x1b[" not in render(_legacy_result())


def test_stripped_color_output_equals_plain_output():
    # Hlida, ze barveni nerozbiji zarovnani ani sirky ramecku: po
    # odstraneni ANSI sekvenci musi byt vystup znak po znaku stejny.
    result = _legacy_result()
    assert _strip_ansi(render(result, color=True)) == render(result)


def test_block_header_token_is_colored():
    # Blok zacina =, pak hlavicka s tokenem stavu - token musi byt obarven.
    output = render(_legacy_result(), color=True, detail=True)
    lines = output.splitlines()

    # Najdi prvni ramec (=)
    frame_idx = next(i for i, line in enumerate(lines) if line and set(line) == {"="})
    # Nasledujici radek je hlavicka s tokenem
    header = lines[frame_idx + 1]

    # Header obsahuje PASS (zelena) nebo WARN/FAIL (zluty/cerveny)
    has_colored_token = (
        "\x1b[32mPASS\x1b[0m" in header or
        "\x1b[33mWARN\x1b[0m" in header or
        "\x1b[31mFAIL\x1b[0m" in header
    )
    assert has_colored_token, f"hlavicka bloku ma neobarveny token: {header}"


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


def _counts_of(line: str) -> dict[str, int]:
    return {name: int(count) for count, name in re.findall(r"(\d+) (PASS|WARN|FAIL|SKIP)", line)}


def _line_starting(output: str, prefix: str) -> str:
    return next(line for line in output.splitlines() if line.strip().startswith(prefix))


def test_summary_names_its_two_units():
    """F-8: souhrn scital checky, tabulka hned pod nim ma radek na sluzbu.

    Dve ruzne jednotky nad sebou bez oznaceni znamenaji, ze si operator
    odnese cislo, na ktere se nedival - '83 PASS' nad tabulkou o 11 radcich.
    """
    output = render(_legacy_result())

    assert _counts_of(_line_starting(output, "Checky:")) == {
        "PASS": 3, "WARN": 1, "FAIL": 1, "SKIP": 0
    }
    assert _counts_of(_line_starting(output, "Sluzby:")) == {
        "PASS": 1, "WARN": 1, "FAIL": 1, "SKIP": 0
    }


def test_service_counts_agree_with_the_rows_of_the_table_below():
    """Bez tohohle by oznaceni jednotek bylo jen slovo navic: cislo na radku
    Sluzby musi sedet na pocet radku tabulky s tymz stavem."""
    output = render(_legacy_result())
    counts = _counts_of(_line_starting(output, "Sluzby:"))

    for name, count in counts.items():
        rows = [
            line for line in output.splitlines() if line.startswith(f"{name}  ")
        ]
        assert len(rows) == count, f"{name}: hlavicka rika {count}, tabulka ma {len(rows)}"


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


def test_filter_recomputes_the_counts_for_the_selection():
    """F-12: filtr menil tabulku, ale ne cisla nad ni - hlavicka pak rikala
    neco jineho nez telo. Rozhodnuto: cisla se prepocitaji za vyber."""
    filtered = filter_result(_legacy_result(), text="L3VPN")

    assert [scope.scope_id for scope in filtered.scopes] == ["svc:L3VPN-CPE13-NNI:IPVPN"]
    assert filtered.summary["pass"] == 1  # cely beh jich hlasi 3
    assert filtered.summary["warn"] == 1
    assert filtered.summary["fail"] == 0


def test_filter_leaves_the_unmatched_counts_alone():
    """NESPAROVANO je pojistka proti prehlednuti nezmigrovane sluzby a
    filtrovani se na nej nevztahuje - takze ani jeho cisla se neprepocitavaji.
    Prepocet obou by tise smazal presne to, co ma sekce ukazat."""
    filtered = filter_result(_legacy_result(), text="NEEXISTUJE")

    assert filtered.scopes == []
    assert filtered.summary["scopes_matched"] == 2
    assert filtered.summary["unmatched_baseline"] == 1
    assert filtered.summary["unmatched_subject"] == 1


def test_filter_records_what_it_hid():
    filtered = filter_result(_legacy_result(), statuses={Status.FAIL})

    assert filtered.filtered["statuses"] == ["FAIL"]
    assert filtered.filtered["scopes_shown"] == 1
    assert filtered.filtered["scopes_total"] == 3


def test_result_without_a_filter_carries_no_record():
    result = _legacy_result()

    assert filter_result(result).filtered is None


def test_filter_by_status_keeps_the_link_partner_too():
    """Dokumentace slibuje, ze --status ponecha i partnera vazby - jinak
    hlavicka FAIL bloku odkazuje 'blok vyse'/'blok nize' do prazdna, protoze
    partner byl smazan filtrem drive, nez se blok vubec vykresli."""
    pair = _linked_pair(l3_status=Status.PASS, l2_status=Status.FAIL)
    filtered = filter_result(_result(pair, baseline=False), statuses={Status.FAIL})

    assert {scope.scope_id for scope in filtered.scopes} == {
        "svc:L3VPN-CPE14-UNI:IPVPN",
        "svc:EVPN-VLAN-AWARE-CPE14:E-LAN",
    }

    output = render(filtered)
    assert "L2 cast: ae0.15 v EVPN-VLAN-AWARE-POP1 (blok nize)" in output
    assert "L3 cast: irb.15 v L3VPN-CPE14-UNI (blok vyse)" in output


def test_filter_matching_neither_side_of_a_pair_keeps_neither():
    pair = _linked_pair(l3_status=Status.PASS, l2_status=Status.PASS)
    filtered = filter_result(_result(pair, baseline=False), statuses={Status.FAIL})

    assert filtered.scopes == []


def test_filter_does_not_pull_in_unlinked_scopes():
    filtered = filter_result(_legacy_result(), statuses={Status.FAIL})

    assert {scope.scope_id for scope in filtered.scopes} == {
        "svc:EVPN-VPWS-CPE13-NNI:E-Line"
    }


def test_render_says_which_filter_ran_and_kolik_z_kolika():
    """Prepoctena cisla bez teto vety by byla druha podoba teze chyby:
    hlavicka by rikala 1 PASS, zatimco beh jich mel 3, a nic by to nepriznalo.
    """
    output = render(filter_result(_legacy_result(), statuses={Status.FAIL}))

    assert "filtr: status=FAIL -- 1 z 3 sluzeb" in output
    # Radek Sparovano a sekce NESPAROVANO se neprepocitavaji, takze se to
    # musi rict - jinak vedle sebe stoji dve cisla za jinou mnozinu.
    assert "NESPAROVANO" in _line_starting(output, "(pocty")


def test_filter_record_is_in_the_machine_output_too():
    """`evaluate --format json --status fail` zapisuje profiltrovany vysledek,
    takze bez zaznamu o filtru by JSON hlasil prepoctena cisla a nic by
    neprozradilo, ze nejde o cely beh."""
    filtered = json.loads(to_json(filter_result(_legacy_result(), text="L3VPN")))
    assert filtered["filtered"]["text"] == "L3VPN"
    assert filtered["summary"]["pass"] == 1

    whole = json.loads(to_json(_legacy_result()))
    assert "filtered" not in whole


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
        summary={"pass": 1, "warn": 1, "fail": 0, "skip": 0, "info": 0,
                 "scopes_matched": 1, "unmatched_baseline": 0, "unmatched_subject": 0},
        scopes=scopes,
    )


def _linked_pair(l3_status=Status.WARN, l2_status=Status.PASS):
    l3 = ScopeResult(
        scope_id="svc:L3VPN-CPE14-UNI:IPVPN",
        key={},
        status=l3_status,
        match=None,
        checks=[_check("interface_state", l3_status, "x", label="Interface admin status (irb.15)", value="Up")],
        identity={
            "description": "L3VPN-CPE14-UNI",
            "service_type": "IPVPN",
            "routing_instance": "L3VPN-CPE14-UNI",
            "interfaces": ["irb.15"],
            "ipv4": [], "ipv6": [], "virtual_gw_v4": [], "virtual_gw_v6": [],
        },
        link={
            "role": "l3",
            "peer_scope_id": "svc:EVPN-VLAN-AWARE-CPE14:E-LAN",
            "peer_interface": "ae0.15",
            "peer_instance": "EVPN-VLAN-AWARE-POP1",
        },
    )
    l2 = ScopeResult(
        scope_id="svc:EVPN-VLAN-AWARE-CPE14:E-LAN",
        key={},
        status=l2_status,
        match=None,
        checks=[_check("interface_state", l2_status, "x", label="Interface admin status (ae0.15)", value="Up")],
        identity={
            "description": "EVPN-VLAN-AWARE-CPE14",
            "service_type": "E-LAN",
            "routing_instance": "EVPN-VLAN-AWARE-POP1",
            "interfaces": ["ae0.15"],
            "ipv4": [], "ipv6": [], "virtual_gw_v4": [], "virtual_gw_v6": [],
        },
        link={
            "role": "l2",
            "peer_scope_id": "svc:L3VPN-CPE14-UNI:IPVPN",
            "peer_interface": "irb.15",
            "peer_instance": "L3VPN-CPE14-UNI",
        },
    )
    return [l3, l2]


def test_link_notes_render_inside_block_frames():
    output = render(_result(_linked_pair(), baseline=False))
    assert " L2 cast: ae0.15 v EVPN-VLAN-AWARE-POP1 (blok nize)" in output
    assert " L3 cast: irb.15 v L3VPN-CPE14-UNI (blok vyse)" in output
    assert "E-LAN (L2 cast)" in output


def test_pass_l2_block_renders_when_l3_partner_renders():
    # L2 blok je PASS a bez --detail by se sam nevypsal; vazba ho vytahne
    output = render(_result(_linked_pair(l3_status=Status.WARN, l2_status=Status.PASS)))
    assert "L3 cast: irb.15" in output


def test_pass_l3_block_renders_when_l2_partner_renders():
    output = render(_result(_linked_pair(l3_status=Status.PASS, l2_status=Status.FAIL)))
    assert "L2 cast: ae0.15" in output


def test_both_pass_blocks_stay_collapsed_without_detail():
    # "L2 cast:" (s dvojteckou) je link_note z bloku - service_type suffix
    # "(L2 cast)" v souhrnne tabulce se vypisuje vzdy a s tim se nekrizi.
    output = render(_result(_linked_pair(l3_status=Status.PASS, l2_status=Status.PASS)))
    assert "L2 cast:" not in output


def test_link_note_counts_into_frame_width():
    # dlouhy nazev instance v odkazu nesmi prerust "=" ramec
    pair = _linked_pair()
    pair[0].link["peer_instance"] = "EVPN-VLAN-AWARE-VELMI-DLOUHE-JMENO-INSTANCE-POP1"
    output = render(_result(pair, baseline=False))
    _assert_frame_wraps_block(output)


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


def test_each_block_is_measured_against_its_own_frame():
    """Dva bloky ruznych sirek v jednom vystupu.

    Kazdy blok si sirku pocita sam, takze uzsi z nich musi sedet na svuj
    vlastni ramec - ne na ten sirsi vedle nej.
    """
    output = render(_result([_dual_stack_scope(), _many_ranges_scope()]))

    widths = {len(line) for line in output.splitlines() if line and set(line) == {"="}}
    assert len(widths) > 1, "oba bloky vysly stejne siroke - test by nic neoveril"

    _assert_frame_wraps_block(output)


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

    # Kazdy radek se meri proti ramecku SVEHO bloku. Porovnavat vsechno
    # proti prvnimu ramecku by u jednoscopoveho vystupu davalo stejny
    # vysledek, ale u dvou sluzeb by uzsi blok tise merilo proti sirsimu
    # ramecku toho druheho.
    frame = None
    measured = 0
    for line in lines[start:end]:
        if line and set(line) == {"="}:
            frame = len(line)
            continue
        if not line.strip():
            continue
        assert frame is not None, "radek bloku pred jeho ramcem"
        measured += 1
        assert len(line) <= frame, (
            f"radek {len(line)} znaku prerusta ramec {frame} znaku:\n{line}"
        )

    assert measured, "blok nema zadny radek"


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


def test_deactivated_service_shows_the_reason_in_the_report():
    """Duvod deaktivace musi byt videt, ne jen ulozeny v CheckResult.

    Bez toho by operator videl SKIP bez vysvetleni - tedy presne ten stav,
    kvuli kteremu se cela vlna dela. deactivation_state ma family=None, takze
    radek spada do bezhlavickove sekce; tenhle test hlida, ze tam opravdu
    dojde a nese hodnotu.

    Zabiji mutanta: vynechani `value` z Findingu v checks/deactivation.py.
    """
    result = RunResult(
        evaluated_at="2026-07-30T12:00:00Z",
        subject={"address": "172.20.20.5", "phase": "post-migration", "captured_at": "x"},
        baseline=None,
        summary={
            "pass": 0, "warn": 0, "fail": 0, "skip": 1, "info": 0,
            "scopes_matched": 0, "unmatched_baseline": 0, "unmatched_subject": 0,
        },
        scopes=[
            ScopeResult(
                scope_id="svc:L3VPN-CPE14-UNI:IPVPN",
                key={"description": "L3VPN-CPE14-UNI", "service_type": "IPVPN"},
                status=Status.SKIP,
                match=None,
                checks=[
                    CheckResult(
                        id="deactivation_state",
                        mode="both",
                        status=Status.SKIP,
                        severity=Severity.CRITICAL,
                        message="sluzba je v konfiguraci deaktivovana, "
                        "baseline neni k porovnani",
                        label="Deaktivace",
                        value="interface deactivated",
                    )
                ],
            )
        ],
    )

    text = render(result)

    assert "L3VPN-CPE14-UNI" in text
    assert "sluzba je v konfiguraci deaktivovana" in text, (
        "radek checku se do textoveho reportu vubec nedostal"
    )
    assert "interface deactivated" in text, (
        "duvod deaktivace se do textoveho reportu nedostal - operator vidi "
        "SKIP bez vysvetleni"
    )


def test_group_reaches_the_json_report():
    """Zabiji mutanta, ktery `group` do to_dict() nezapise.

    Strojovy vystup ma nest tutez informaci jako text. Sourozenec
    test_group_travels_from_finding_to_check_result hlida cestu k
    CheckResultu, tenhle az serializaci.
    """
    result = _legacy_result()
    result.scopes[0].checks[0].group = "BGP 198.11.13.2 / inet.0"
    payload = json.loads(to_json(result))
    assert payload["scopes"][0]["checks"][0]["group"] == "BGP 198.11.13.2 / inet.0"


def test_json_report_omits_group_when_there_is_none():
    """Zabiji mutanta, ktery `group` zapise vzdy, i kdyz je None.

    Nefiltrovany beh bez skupin ma zustat presne tim tvarem, ktery uz cte
    okoli - stejne pravidlo, jake plati pro `filtered` a pro `details`.
    """
    payload = json.loads(to_json(_legacy_result()))
    assert "group" not in payload["scopes"][0]["checks"][0]


def _grouped_check(check_id, *, label, group=None):
    return CheckResult(
        id=check_id,
        mode="state",
        status=Status.PASS,
        severity=Severity.ADVISORY,
        message="msg",
        label=label,
        group=group,
        family=4,
        value="v",
    )


def _grouped_result(checks) -> RunResult:
    return RunResult(
        evaluated_at="2026-07-24T11:40:02Z",
        subject={"address": "172.20.20.5", "phase": "post-migration"},
        baseline={"address": "172.20.20.4", "phase": "pre-migration"},
        summary={
            "pass": 1, "warn": 0, "fail": 0, "skip": 0, "info": 0,
            "scopes_matched": 1, "unmatched_baseline": 0, "unmatched_subject": 0,
        },
        scopes=[
            ScopeResult(
                scope_id="svc:X:Internet",
                key={"description": "X", "service_type": "Internet"},
                status=Status.PASS,
                match=MatchInfo(
                    status="matched",
                    baseline_interfaces=["ge-0/0/2.13"],
                    subject_interfaces=["et-0/0/8.13"],
                ),
                checks=checks,
                identity={
                    "description": "X",
                    "service_type": "Internet",
                    "routing_instance": None,
                    "interfaces": ["et-0/0/8.13"],
                    "ipv4": ["152.11.13.1/30"],
                    "ipv6": [],
                    "virtual_gw_v4": [],
                    "virtual_gw_v6": [],
                },
            )
        ],
    )


_LONG_GROUP = "BGP 2001:db8:11:13::b / VELMI-DLOUHE-JMENO-ROUTING-INSTANCE.inet6.0"


def test_rendered_block_puts_ungrouped_rows_above_the_first_group_header():
    """Zabiji mutanta M2: renderer tiskne neseskupene radky az ZA skupinami.

    Sesterny test test_ungrouped_rows_stand_before_groups ve
    test_view.py tohohle mutanta PREZIL - poradi v datove strukture zustane
    spravne, prohodi se az sazba. Zmereno na prototypu 2026-08-03.
    """
    result = _grouped_result(
        [
            _grouped_check("a", label="b1", group="Skupina B"),
            _grouped_check("b", label="volny"),
        ]
    )
    lines = render(result, detail=True).splitlines()
    free = next(i for i, line in enumerate(lines) if "volny" in line)
    header = next(i for i, line in enumerate(lines) if line.strip() == "-- Skupina B")
    grouped = next(i for i, line in enumerate(lines) if "b1" in line)
    assert free < header < grouped


def test_long_group_title_widens_the_block_frame():
    """Zabiji mutanta M3: nadpisy skupin vypadnou z max() ve vypoctu sirky.

    Nadpis je schvalne DELSI nez cela tabulka sloupcu - s kratkym nadpisem
    by test prosel i tehdy, kdyby se do sirky nezapocitaval, protoze ramec
    uz je siroky z jinych duvodu. Ctvrty vyskyt tehoz tvaru chyby; tri
    predchozi jsou popsane v komentari text_report.py:140.
    """
    result = _grouped_result([_grouped_check("a", label="x", group=_LONG_GROUP)])
    lines = render(result, detail=True).splitlines()
    frame = [line for line in lines if line and set(line) == {"="}]
    assert frame, "blok nema ramec"
    assert len(frame[0]) >= len(f"   -- {_LONG_GROUP}")


def test_group_header_is_not_padded_with_dashes():
    """Zabiji mutanta, ktery nadpis skupiny doplni pomlckami jako sekci.

    Dve urovne nadpisu maji zustat rozlisitelne: sekce rodiny drzi caru pres
    celou sirku, skupina ne.
    """
    result = _grouped_result([_grouped_check("a", label="x", group="S")])
    lines = render(result, detail=True).splitlines()
    header = next(line for line in lines if line.strip().startswith("-- S"))
    assert header == "   -- S"


def test_group_title_does_not_widen_the_check_column():
    """Zabiji mutanta, ktery nadpis skupiny primicha do label_width sloupce CHECK.

    Sesterny test test_long_group_title_widens_the_block_frame hlida SIRKU
    RAMCE bloku (radek '='...), do ktere nadpis skupiny vstupovat MA (AR-38).
    Tenhle hlida neco jineho: pozici ':' ve sloupci CHECK u datovych radku,
    kam nadpis skupiny vstupovat NEMA - jinak by dlouhy nazev peeru/RIB
    roztahl sloupec s popisky u vsech radku bloku, i tech s kratkym labelem.
    """
    result = _grouped_result([_grouped_check("a", label="x", group=_LONG_GROUP)])
    lines = render(result, detail=True).splitlines()
    data_row = next(line for line in lines if line.startswith(" PASS |"))
    expected_label_width = max(len("x"), len("CHECK"))
    assert data_row.index(":") == 9 + expected_label_width


def test_two_groups_in_one_section_keep_first_occurrence_order():
    """Zabiji mutanta, ktery renderer seradi skupiny v sekci abecedne.

    Sesterny test test_ungrouped_rows_stand_before_groups (ve view testech)
    hlida poradi na urovni DAT (Section.groups) mezi neseskupenymi radky a
    prvni skupinou. Tenhle hlida SAZBU renderu, kdyz je v jedne sekci
    skupin vic - podle AR-37 je poradi skupin poradim prvniho vyskytu, ne
    abecedni. Fixture jde schvalne proti abecede (Zebra pred Alfa) - s
    poradim, ktere abecede odpovida, by mutant prosel i beze zmeny.
    """
    result = _grouped_result(
        [
            _grouped_check("a", label="z1", group="Zebra"),
            _grouped_check("b", label="a1", group="Alfa"),
        ]
    )
    lines = render(result, detail=True).splitlines()
    zebra_idx = next(i for i, line in enumerate(lines) if line.strip() == "-- Zebra")
    alfa_idx = next(i for i, line in enumerate(lines) if line.strip() == "-- Alfa")
    assert zebra_idx < alfa_idx


def test_unmatched_service_type_column_is_padded():
    """Zabiji mutanta M7: sloupec (TYP) se nedoplnuje na sirku.

    Puvodni F-14 z 2026-07-28. Test se diva na POZICI sloupce s duvodem, ne
    na pritomnost mezer - dva ruzne dlouhe typy sluzby ('Core', 'Internet')
    musi dat duvod ve stejnem sloupci.
    """
    result = _grouped_result([_grouped_check("a", label="x")])
    result.unmatched = {
        "baseline": [
            {
                "scope_id": "s1",
                "description": "clab-pop-migration-P1;et-0/0/0",
                "service_type": "Core",
                "reason": "zadny kandidat na subject",
            }
        ],
        "subject": [
            {
                "scope_id": "s2",
                "description": "svc:et-0/0/10.0:Internet",
                "service_type": "Internet",
                "reason": "nova sluzba, chybi baseline",
            }
        ],
    }
    lines = render(result).splitlines()
    rows = [
        line for line in lines
        if line.startswith("  baseline") or line.startswith("  subject")
    ]
    assert len(rows) == 2
    starts = {
        line.index("zadny") if "zadny" in line else line.index("nova") for line in rows
    }
    assert len(starts) == 1, f"sloupec s duvodem nestoji v jedne linii: {rows}"


def _unassigned_result():
    result = _grouped_result([_grouped_check("a", label="x")])
    result.unassigned = {
        "bgp_peers": [
            {"peer": "10.9.9.9", "routing_instance": "MGMT", "snapshot": "subject"}
        ],
        "static_routes": [
            {
                "rib": "inet.0",
                "prefix": "10.0.0.0/8",
                "next_hop": ["172.20.20.1"],
                "via": [],
                "snapshot": "subject",
            },
            {
                "rib": "inet.0",
                "prefix": "10.1.0.0/16",
                "next_hop": [],
                "via": ["et-0/0/8.13"],
                "snapshot": "subject",
            },
        ],
        "bfd_sessions": [
            {
                "peer": "10.9.9.9",
                "interface": "et-0/0/2",
                "state": "Up",
                "snapshot": "subject",
            }
        ],
    }
    return result


def test_unassigned_objects_reach_the_text_report():
    """Zabiji mutanta, ktery `unassigned` necha jen v JSON.

    Do vlny 5 se retezec 'unassigned' v reporting/ nevyskytoval ani jednou,
    takze pojistka proti mezeram v parsovani byla videt jen strojove.

    `via 10.1.0.0/16` ma zaroven pokryt vetev `_unassigned_row` pro
    prazdny next_hop - bez ni by mutace textu 'via ' na cokoliv jineho
    prosla, protoze fixture do teto ulohy mela `via` vzdy prazdne.
    """
    out = render(_unassigned_result())
    assert "NEZARAZENO" in out
    assert "10.9.9.9" in out and "MGMT" in out
    assert "inet.0 10.0.0.0/8" in out and "172.20.20.1" in out
    assert "et-0/0/2" in out
    assert "via et-0/0/8.13" in out
    assert "-> et-0/0/8.13" not in out


def test_unassigned_section_is_printed_even_when_empty():
    """Zabiji mutanta, ktery `(nic)` z prazdne vetve `_unassigned_lines` vynecha.

    Hleda se schvalne az za nadpisem 'NEZARAZENO (jen subject)', ne kdekoliv
    ve vystupu - sekce NESPAROVANO hned nad ni tiskne pri prazdnem
    `unmatched` tentyz retezec '(nic)', takze `"(nic)" in out` by prosel i
    kdyby _unassigned_lines svou prazdnou vetev vubec nevytiskla. Tohle
    nehlida sourozenec test_unassigned_detail_column_stands_in_one_line -
    ten bezi jen nad naplnenou sekci.
    """
    out = render(_grouped_result([_grouped_check("a", label="x")]))
    lines = out.splitlines()
    start = lines.index("NEZARAZENO (jen subject)")
    assert lines[start + 1] == "  (nic)"


def test_unassigned_detail_column_stands_in_one_line():
    """Zabiji mutanta, ktery `identity_width` z formatovani vypusti.

    Sourozenec test_unassigned_objects_reach_the_text_report hlida, ze se
    data vypisou; tenhle, ze stoji ve sloupcich. Ctyri objekty ve fixture
    maji ruzne dlouhou identitu ('10.9.9.9' vs 'inet.0 10.0.0.0/8' vs
    'inet.0 10.1.0.0/16'), takze bez doplneni identity_width by podrobnost
    skoncila ve ctyrech ruznych sloupcich - tataz vada, jakou AR-40 opravuje
    v NESPAROVANO.
    """
    lines = render(_unassigned_result()).splitlines()
    start = lines.index("NEZARAZENO (jen subject)")
    rows = [line for line in lines[start + 1 :] if line.startswith("  ")]
    assert len(rows) == 4
    starts = {
        line.index("RI ") if "RI " in line else
        line.index("-> ") if "-> " in line else
        line.index("via ") if "via " in line else
        line.index("et-0/0/2")
        for line in rows
    }
    assert len(starts) == 1, f"sloupec s podrobnosti nestoji v jedne linii: {rows}"


def test_unassigned_survives_a_filter_that_hides_every_scope():
    """Zabiji mutanta M8: filtr se pusti i na NEZARAZENO.

    Je to pojistka, ne data - stejne jako NESPAROVANO, ktere filter_result
    schvalne neprepocitava. Objekty bez sluzby navic zadny status nemaji,
    takze --status fail by je schoval vzdycky.
    """
    out = render(filter_result(_unassigned_result(), statuses={Status.FAIL}))
    assert "10.9.9.9" in out, "filtr smazal pojistku"


def _section(output: str, title: str) -> str:
    """Vyrizne z outputu radky mezi hlavickou skupiny/sekce `title` a dalsi
    hlavickou. Hlavicka skupiny je '   -- {title}' bez pridavku (viz
    text_report.py:_group_header); hlavicka sekce rodiny ma za titulem jeste
    adresy, proto se dalsi hranice hleda obecne jako radek zacinajici '--'.
    """
    lines = output.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(f"-- {title}"))
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if lines[i].strip().startswith("--") or lines[i] == ""
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_deactivated_route_renders_as_skip_in_its_section():
    """Report musi SKIP ukazat, ne ho jen mit v datech.

    Hleda se uvnitr sekce statickych rout, ne kdekoliv ve vystupu: retezec
    'deaktivovana' se objevi i u deaktivovane sluzby, takze `in output` by
    prosel i kdyby se radek routy vubec nevykreslil.
    """
    result = _grouped_result(
        [
            CheckResult(
                id="static_route_status",
                mode="both",
                status=Status.SKIP,
                severity=Severity.CRITICAL,
                message="inet.0 10.0.0.0/8: routa je v konfiguraci deaktivovana",
                label="inet.0 10.0.0.0/8",
                group="Staticke routy",
                family=4,
                value="deaktivovana",
            ),
            CheckResult(
                id="static_route_status",
                mode="both",
                status=Status.PASS,
                severity=Severity.CRITICAL,
                message="inet.0 10.1.0.0/16: 2.2.2.2",
                label="inet.0 10.1.0.0/16",
                group="Staticke routy",
                family=4,
                value="2.2.2.2",
            ),
        ]
    )
    output = render(result, detail=True)
    section = _section(output, "Staticke routy")

    assert "SKIP" in section
    assert "deaktivovana" in section


def test_deactivated_peer_renders_as_skip_in_its_family_section():
    """Radek deaktivovaneho peera se musi vykreslit, ne jen existovat v datech."""
    result = _grouped_result(
        [
            CheckResult(
                id="bgp_session_state",
                mode="both",
                status=Status.SKIP,
                severity=Severity.CRITICAL,
                message="peer 198.11.13.9 je v konfiguraci deaktivovan",
                label="BGP status (198.11.13.9)",
                family=4,
                value="deaktivovan",
            ),
            CheckResult(
                id="bgp_session_state",
                mode="both",
                status=Status.PASS,
                severity=Severity.CRITICAL,
                message="198.11.13.2: Established",
                label="BGP status (198.11.13.2)",
                family=4,
                value="Established",
            ),
        ]
    )
    output = render(result, detail=True)
    section = _section(output, "IPv4")

    assert "SKIP" in section
    assert "deaktivovan" in section


def test_info_row_carries_info_token():
    """INFO radek nese token INFO ve sloupci STAV.

    Revize puvodniho rozhodnuti (prazdny symbol): 47 INFO radku v ostrem
    behu bylo bez tokenu k nerozeznani od pokracovacich radku a nemely se
    cim obarvit. Token se tiskne a barvi cyan stejne jako ostatni stavy;
    zarovnani sloupcu musi zustat stejne jako u ctyrznakovych tokenu.
    """
    result = _grouped_result(
        [
            CheckResult(
                id="interface_state",
                mode="state",
                status=Status.PASS,
                severity=Severity.ADVISORY,
                message="up/up",
                label="Interface admin status",
                family=4,
                value="Up",
            ),
            CheckResult(
                id="evpn_vpws_sid_local",
                mode="state",
                status=Status.INFO,
                severity=Severity.ADVISORY,
                message="EVPN VPWS SID local value",
                label="EVPN VPWS SID local value",
                family=4,
                value="10001",
            ),
        ]
    )
    rendered = render(result, detail=True)

    assert "EVPN VPWS SID local value" in rendered
    assert "Interface admin status" in rendered

    lines = rendered.splitlines()
    pass_line = next(l for l in lines if "Interface admin status" in l)
    info_line = next(l for l in lines if "SID local value" in l)

    # INFO radek ma token ve sloupci STAV
    assert info_line.lstrip().startswith("INFO")

    # Hranicni pozice | musi byt na stejnem miste u obou radku -
    # to je jedinym zpusobem, jak overit ze se zarovnani nezhroutilo.
    assert pass_line.index(" | ") == info_line.index(" | ")

    # S barvami je token cyan; po stripu ANSI je vystup identicky.
    colored = render(result, detail=True, color=True)
    assert "\x1b[36mINFO\x1b[0m" in colored
    assert _strip_ansi(colored) == rendered


def _run_result(scopes) -> RunResult:
    return RunResult(
        evaluated_at="2026-08-12T00:00:00Z",
        subject={"address": "172.20.20.5", "phase": "post-migration", "captured_at": "x"},
        baseline={"address": "172.20.20.4", "phase": "pre-migration", "captured_at": "y"},
        summary={"pass": 0, "warn": 0, "fail": 0, "skip": 0, "info": 0,
                 "scopes_matched": len(scopes), "unmatched_baseline": 0, "unmatched_subject": 0},
        scopes=scopes,
    )


def _l1_result(port, *, status) -> ScopeResult:
    return ScopeResult(
        scope_id=f"l1:{port}",
        key={"service_type": "Layer1"},
        status=status,
        match=None,
        checks=[_check("optics_present", status, "optika ok", label="Optika", value="ok")],
        identity={"service_type": "Layer1", "interfaces": [port], "physical_interfaces": []},
    )


def _svc_result(name, *, parent, status) -> ScopeResult:
    return ScopeResult(
        scope_id=f"svc:{name}",
        key={"description": name, "service_type": "IPVPN"},
        status=status,
        match=None,
        checks=[_check("interface_state", status, "x", label="Interface admin status", value="Up")],
        identity={
            "description": name,
            "service_type": "IPVPN",
            "interfaces": [],
            "physical_interfaces": [parent],
        },
    )


def test_filter_drzi_l1_rodice_vybrane_sluzby():
    result = _run_result([_l1_result("ae0", status=Status.PASS),
                          _svc_result("S-A", parent="ae0", status=Status.FAIL)])
    filtered = filter_result(result, text="S-A")
    ids = [scope.scope_id for scope in filtered.scopes]
    assert "l1:ae0" in ids  # rodic jede s vybranym ditetem


def test_fail_sluzba_rozbali_i_pass_l1_blok():
    # "Layer1" samo o sobe je slaby signal - vypisuje se i v souhrnne
    # tabulce za KAZDY scope bez ohledu na shown, takze by prosel i bez
    # opravy. "Optika" je label checku, ktery existuje jen uvnitr
    # vypsaneho bloku - to uz je dukaz, ze se blok l1:ae0 skutecne
    # rozbalil, i kdyz je sam PASS a detail=False.
    result = _run_result([_l1_result("ae0", status=Status.PASS),
                          _svc_result("S-A", parent="ae0", status=Status.FAIL)])
    text = render(result)
    assert "Optika" in text


def test_pass_l1_blok_zustava_sbaleny_bez_rozbalene_sluzby():
    # Negativni kontrola k testu vyse: kdyz je L1 i jeho dite PASS, nic
    # dite nerozbaluje a bez --detail zustava blok sbaleny - label checku
    # se nevytiskne. Kdyby _l1_parent_ids pridavala rodice vzdycky (ne jen
    # k rozbalenym detem), tenhle test by to chytil.
    result = _run_result([_l1_result("ae0", status=Status.PASS),
                          _svc_result("S-A", parent="ae0", status=Status.PASS)])
    text = render(result)
    assert "Optika" not in text
