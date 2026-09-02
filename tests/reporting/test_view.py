from migration_validator.models.result import (
    CheckResult,
    MatchInfo,
    ScopeResult,
    Severity,
    SKIPPED_BECAUSE,
    SKIP_DEACTIVATED,
    Status,
)
from migration_validator.reporting.view import build_view, change_text


def _check(check_id, *, family=None, label="X", value="v", status=Status.PASS,
           mode="state", baseline_value=None, delta=None, message="msg", address=None,
           group=None, skipped_because=None):
    details = {}
    if address:
        details["address"] = address
    if skipped_because:
        details[SKIPPED_BECAUSE] = skipped_because
    return CheckResult(
        id=check_id,
        mode=mode,
        status=status,
        severity=Severity.ADVISORY,
        message=message,
        label=label,
        group=group,
        family=family,
        value=value,
        baseline_value=baseline_value,
        delta=delta,
        details=details,
    )


_UNSET = object()


def _scope(checks, match=_UNSET, link=None) -> ScopeResult:
    return ScopeResult(
        scope_id="svc:INTERNET-CPE13-NNI:Internet",
        key={"description": "INTERNET-CPE13-NNI", "service_type": "Internet"},
        status=Status.WARN,
        match=(
            MatchInfo(
                status="matched",
                baseline_interfaces=["ge-0/0/2.13"],
                subject_interfaces=["et-0/0/8.13"],
            )
            if match is _UNSET
            else match
        ),
        checks=checks,
        identity={
            "description": "INTERNET-CPE13-NNI",
            "service_type": "Internet",
            "service_subtype": None,
            "routing_instance": None,
            "interfaces": ["et-0/0/8.13"],
            "ipv4": ["152.11.13.1/30"],
            "ipv6": ["2001:abcd:11:13::a/127"],
            "virtual_gw_v4": [],
            "virtual_gw_v6": [],
        },
        link=link,
    )


def test_sections_are_ordered_common_ipv4_ipv6():
    view = build_view(
        _scope(
            [
                _check("ping_reachability", family=6, label="Ping"),
                _check("interface_state", label="Interface admin status"),
                _check("arp_present", family=4, label="ARP"),
            ]
        )
    )

    assert [section.family for section in view.sections] == [None, 4, 6]


def test_section_carries_its_addresses():
    view = build_view(_scope([_check("arp_present", family=4, label="ARP")]))
    section = next(s for s in view.sections if s.family == 4)

    assert section.addresses == ["152.11.13.1/30"]


def test_empty_family_produces_no_section():
    """Sluzba bez IPv6 nesmi dostat prazdnou IPv6 sekci."""
    view = build_view(_scope([_check("arp_present", family=4, label="ARP")]))

    assert [section.family for section in view.sections] == [4]


def test_ports_come_from_match_info():
    view = build_view(_scope([_check("arp_present", family=4, label="ARP")]))

    assert view.baseline_interfaces == ["ge-0/0/2.13"]
    assert view.subject_interfaces == ["et-0/0/8.13"]


def test_port_falls_back_to_identity_when_there_is_no_match():
    """`evaluate --snapshot X` bez --baseline je podle AR-10 doporuceny
    zpusob, jak si prohlednout stav jednoho zarizeni. V nem je match None,
    takze sloupec NOVY PORT byl prazdny u kazde sluzby - prave v rezimu,
    ktery spec doporucuje. Rozhrani zna identity, ktera vznikla presne
    proto, aby renderer nemusel do scope.
    """
    view = build_view(_scope([_check("arp_present", family=4, label="ARP")], match=None))

    assert view.subject_interfaces == ["et-0/0/8.13"]
    assert view.baseline_interfaces == []


def test_single_address_stays_out_of_the_label():
    """Jedina adresa uz je v hlavicce sekce - v popisku by jen prekazela."""
    view = build_view(
        _scope([_check("arp_present", family=4, label="ARP", value="mac -> 152.11.13.2")])
    )
    section = next(s for s in view.sections if s.family == 4)

    assert section.rows[0].label == "ARP"


def test_second_address_of_the_same_family_enters_the_label():
    """Sluzba s vic rozsahy jedne rodiny - AR-5b, nejen dual-stack."""
    scope = _scope(
        [
            _check("arp_present", family=4, label="ARP", value="mac -> 152.11.13.2",
                   address="152.11.13.1/30"),
            _check("arp_present", family=4, label="ARP", value="mac -> 152.11.20.2",
                   address="152.11.20.1/29"),
        ]
    )
    scope.identity["ipv4"] = ["152.11.13.1/30", "152.11.20.1/29"]

    section = next(s for s in build_view(scope).sections if s.family == 4)

    assert section.addresses == ["152.11.13.1/30", "152.11.20.1/29"]
    assert [row.label for row in section.rows] == [
        "ARP (152.11.13.1/30)",
        "ARP (152.11.20.1/29)",
    ]


def test_single_address_stays_unqualified_even_when_check_has_an_address():
    """Doplnek k testu vyse: rozliseni je pocet adres v identite, ne pritomnost
    check.details['address'] - jinak by 'kvalifikuj vzdy' proslo beze zmeny."""
    view = build_view(
        _scope(
            [
                _check("arp_present", family=4, label="ARP", value="mac -> 152.11.13.2",
                       address="152.11.13.1/30"),
            ]
        )
    )
    section = next(s for s in view.sections if s.family == 4)

    assert section.rows[0].label == "ARP"


def test_missing_value_does_not_pull_the_whole_message_into_the_column():
    """F-7: sloupec hodnot je podle AR-4 hodnota, ne veta.

    Kdyz `value` chybelo, renderer sahl po `check.message` - do sloupce pak
    padaly cele vety a nejdelsi z nich (RPC chyba od collectoru) roztahla
    blok na 270 znaku. Vetu ma nest sloupec NALEZ a strojovy vystup.

    Vlastni radky uz hodnotu dodavaji checky; tenhle fallback je posledni
    pojistka pro pripad, ze na ni nekdo zapomene.
    """
    row = build_view(
        _scope([_check("evpn_mac_count", value=None, message="chybi data z collectoru")])
    ).sections[0].rows[0]

    assert row.value == "-"


def test_state_check_never_says_missing_baseline():
    """STATE check nema baseline z definice - 'bez baseline' by bylo na vsem."""
    row = build_view(_scope([_check("arp_present", family=4, mode="state")])).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == ""


def test_compare_check_without_baseline_says_so():
    row = build_view(
        _scope([_check("bgp_prefix_counts", family=4, mode="compare")])
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == "bez baseline"


def test_both_check_without_baseline_says_so():
    """Rezim BOTH, ne COMPARE - a prave on to hlasi na spadle BGP relaci.

    Bez tehle fixture je pravidlo pokryte jen pro COMPARE: mutace vracejici
    pro BOTH prazdny retezec misto 'bez baseline' projde celou sadou.
    """
    row = build_view(
        _scope([_check("bgp_session_state", family=4, mode="both", value="Connect")])
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == "bez baseline"


def test_skip_row_does_not_repeat_missing_baseline():
    """SKIP uz duvod nese ve vlastni hlasce ('peer neni v baseline
    snapshotu'), takze 'bez baseline' ve sloupci ZMENA je druha kopie teze
    vety vedle sebe. V ostrem behu to bylo 14 z 18 vyskytu te hlasky.

    Testy nad timhle drzi opacnou stranu: PASS a BOTH radek bez baseline ji
    hlasit musi, jinak by zmizela i tam, kde je jedina.
    """
    row = build_view(
        _scope(
            [
                _check("bgp_prefix_counts", family=4, mode="compare",
                       status=Status.SKIP, value="bez baseline",
                       message="10.0.0.1: peer neni v baseline snapshotu, nelze porovnat"),
            ]
        )
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == ""


def test_identical_values_print_nothing():
    row = build_view(
        _scope([_check("interface_traffic", mode="both", value="460 pps",
                       baseline_value="460 pps")])
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == ""


def test_changed_value_prints_previous_and_delta():
    row = build_view(
        _scope([_check("interface_traffic", mode="both", value="460 pps",
                       baseline_value="520 pps", delta="-12 %")])
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == "bylo 520 pps   -12 %"


def test_changed_value_without_delta_omits_the_delta_part():
    """Compare check bez spocitane delty - jen 'bylo <hodnota>', ne 'bylo
    <hodnota>   None'."""
    row = build_view(
        _scope([_check("bgp_prefix_counts", family=4, mode="compare", value="460",
                       baseline_value="520")])
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=True) == "bylo 520"


def test_no_baseline_at_all_prints_nothing():
    row = build_view(
        _scope([_check("bgp_prefix_counts", family=4, mode="compare")])
    ).sections[0].rows[0]

    assert change_text(row, has_baseline=False) == ""


def test_ungrouped_rows_stand_before_groups():
    """Zabiji dva mutanty najednou.

    (1) `groups.sort(key=lambda g: g.title)` - skupiny by se seradily
    abecedne misto poradim vyskytu, takze 'Skupina A' by predbehla
    'Skupinu B'. (2) neseskupene radky pripojene do posledni skupiny misto
    do `section.rows`.

    NEhlida to, co se opravdu VYTISKNE - poradi v datove strukture umi byt
    spravne a renderer ho presto prohodi. To meri sourozenec
    test_rendered_block_puts_ungrouped_rows_above_the_first_group_header
    v tests/reporting/test_text_report.py; overeno mutantem M2, ktery
    tenhle test prezil.
    """
    view = build_view(
        _scope(
            [
                _check("a", family=4, label="b1", group="Skupina B"),
                _check("b", family=4, label="volny"),
                _check("c", family=4, label="a1", group="Skupina A"),
                _check("d", family=4, label="b2", group="Skupina B"),
            ]
        )
    )
    section = view.sections[0]
    assert [row.label for row in section.rows] == ["volny"]
    assert [(g.title, [r.label for r in g.rows]) for g in section.groups] == [
        ("Skupina B", ["b1", "b2"]),
        ("Skupina A", ["a1"]),
    ]


def test_all_rows_returns_grouped_rows_too():
    """Zabiji mutanta `return list(self.rows)` v all_rows().

    Sirky sloupcu se pocitaji prave z all_rows(); kdyby zapomnela radky ve
    skupinach, dlouha hodnota uvnitr skupiny by prerostla ramec bloku.
    """
    view = build_view(
        _scope(
            [
                _check("a", family=4, label="volny"),
                _check("b", family=4, label="ve skupine", group="S"),
            ]
        )
    )
    assert [row.label for row in view.sections[0].all_rows()] == ["volny", "ve skupine"]


def test_groups_do_not_cross_family_sections():
    """Zabiji mutanta, ktery skupiny sbira globalne misto po sekcich.

    Tataz skupina v IPv4 i IPv6 sekci musi dat dva samostatne nadpisy -
    jinak by radky jedne rodiny spadly pod nadpis v sekci te druhe.
    """
    view = build_view(
        _scope(
            [
                _check("a", family=4, label="v4", group="Staticke routy"),
                _check("b", family=6, label="v6", group="Staticke routy"),
            ]
        )
    )
    families = {section.family: section for section in view.sections}
    assert [r.label for r in families[4].groups[0].rows] == ["v4"]
    assert [r.label for r in families[6].groups[0].rows] == ["v6"]


def _deactivation_skip(label):
    return _check(
        f"check_{label}",
        label=label,
        status=Status.SKIP,
        value="interface deactivated",
        message="sluzba je v konfiguraci deaktivovana (interface deactivated)",
        skipped_because=SKIP_DEACTIVATED,
    )


def test_deactivation_skips_collapse_into_one_row():
    """Bez --detail se deaktivacni SKIPy slevaji do jednoho radku s poctem.

    Blok deaktivovane sluzby jich mel v laborce sedm az deset a vsechny
    rikaly doslova totez co radek Deaktivace nad nimi.
    """
    view = build_view(
        _scope(
            [
                _check("deactivation_state", label="Deaktivace", status=Status.WARN,
                       value="interface deactivated"),
                _deactivation_skip("BFD"),
                _deactivation_skip("Interface status"),
                _deactivation_skip("Staticka routa"),
            ]
        ),
        detail=False,
    )

    labels = [row.label for row in view.sections[0].rows]

    assert labels == ["Deaktivace", "Ostatni checky"]
    assert view.sections[0].rows[1].value == "3 preskoceno"
    assert view.sections[0].rows[1].status is Status.SKIP


def test_detail_keeps_every_deactivation_skip():
    """S --detail se nesleva nic - zasada 'detail rozbali vsechno'.

    Zabiji mutanta: slevani bez ohledu na priznak detail. S nim by
    --detail prestal byt uplnym vypisem a operator by se k jednotlivym
    preskocenym checkum nedostal nikde.
    """
    view = build_view(
        _scope(
            [
                _check("deactivation_state", label="Deaktivace", status=Status.WARN,
                       value="interface deactivated"),
                _deactivation_skip("BFD"),
                _deactivation_skip("Interface status"),
                _deactivation_skip("Staticka routa"),
            ]
        ),
        detail=True,
    )

    labels = [row.label for row in view.sections[0].rows]

    assert labels == ["Deaktivace", "BFD", "Interface status", "Staticka routa"]


def test_foreign_skip_is_not_collapsed():
    """SKIP z jineho duvodu zustava samostatne, i kdyz sedi mezi deaktivacnimi.

    Zmereno na bloku svc:et-0/0/10.0:Internet: mezi deviti deaktivacnimi
    SKIPy tam sedi 'BGP prefixy : bez baseline', coz je SKIP porovnavaciho
    checku bez baseline snapshotu. Slit ho dohromady by zahodilo informaci,
    kterou nic jineho nenese.

    Zabiji mutanta: slevani podle Status.SKIP misto podle znacky. Zmereno
    (2026-08-04, oprava vlny 10): pod timhle mutantem padne i
    tests/reporting/test_text_report.py::test_deactivated_service_shows_the_reason_in_the_report,
    jehoz scope nese jediny check `deactivation_state`, ktery je sam
    Status.SKIP bez znacky - implementace slevajici podle stavu spolkne
    prave ten radek, ktery ten test hlida. Tenhle test tedy neni jediny,
    ktery ten rozdil meri, ale je jediny v tomhle souboru.
    """
    view = build_view(
        _scope(
            [
                _check("deactivation_state", label="Deaktivace", status=Status.WARN,
                       value="interface deactivated"),
                _deactivation_skip("BFD"),
                _check("bgp_prefix_counts", label="BGP prefixy", status=Status.SKIP,
                       value="bez baseline", message="porovnavaci check bez baseline snapshotu"),
                _deactivation_skip("Staticka routa"),
            ]
        ),
        detail=False,
    )

    labels = [row.label for row in view.sections[0].rows]

    assert labels == ["Deaktivace", "BGP prefixy", "Ostatni checky"]
    assert view.sections[0].rows[2].value == "2 preskoceno"


def test_l3_link_note_points_below():
    scope = _scope(
        [_check("interface_state")],
        link={
            "role": "l3",
            "peers": [
                {
                    "scope_id": "svc:X:E-LAN",
                    "interface": "ae0.15",
                    "instance": "EVPN-VLAN-AWARE-POP1",
                }
            ],
        },
    )
    view = build_view(scope)
    assert view.link_role == "l3"
    assert view.link_note == "L2 cast: ae0.15 v EVPN-VLAN-AWARE-POP1 (blok nize)"
    assert "(L2 cast)" not in view.service_type


def test_irb_without_link_gets_l2_note_from_identity():
    scope = _scope([_check("interface_state")])
    scope.identity["l2_interfaces"] = ["ge-0/0/2.12"]
    view = build_view(scope)
    assert view.link_role is None
    assert view.link_note == "L2: ge-0/0/2.12"


def test_l3_link_note_lists_every_l2_peer():
    # N L2 : 1 L3 (lab BD-4094) - poznamka vyjmenuje vsechny L2 casti.
    scope = _scope(
        [_check("interface_state")],
        link={
            "role": "l3",
            "peers": [
                {
                    "scope_id": "svc:A:E-LAN",
                    "interface": "ge-0/0/2.4094",
                    "instance": "EVPN-VLAN-AWARE-POP1",
                },
                {
                    "scope_id": "svc:B:E-LAN",
                    "interface": "ge-0/0/6.4094",
                    "instance": "EVPN-VLAN-AWARE-POP1",
                },
            ],
        },
    )
    view = build_view(scope)
    assert view.link_note == (
        "L2 cast: ge-0/0/2.4094 v EVPN-VLAN-AWARE-POP1, "
        "ge-0/0/6.4094 v EVPN-VLAN-AWARE-POP1 (bloky nize)"
    )


def test_l2_link_note_points_above_and_marks_type():
    scope = _scope(
        [_check("interface_state")],
        link={
            "role": "l2",
            "peer_scope_id": "svc:X:IPVPN",
            "peer_interface": "irb.15",
            "peer_instance": "L3VPN-CPE14-UNI",
        },
    )
    view = build_view(scope)
    assert view.link_role == "l2"
    assert view.link_note == "L3 cast: irb.15 v L3VPN-CPE14-UNI (blok vyse)"
    assert view.service_type.endswith(" (L2 cast)")


def test_unexpected_link_role_produces_no_note():
    # Jen "l3"/"l2" jsou platne role; jina hodnota nesmi vyrobit odkaz, ktery
    # by tvrdil neco, co engine nikdy nenaplnil.
    scope = _scope(
        [_check("interface_state")],
        link={
            "role": "neco-jineho",
            "peer_scope_id": "svc:X:E-LAN",
            "peer_interface": "ae0.15",
            "peer_instance": "EVPN-VLAN-AWARE-POP1",
        },
    )
    view = build_view(scope)
    assert view.link_role == "neco-jineho"
    assert view.link_note is None
    assert "(L2 cast)" not in view.service_type


def test_no_link_leaves_view_unchanged():
    view = build_view(_scope([_check("interface_state")]))
    assert view.link_role is None
    assert view.link_note is None
