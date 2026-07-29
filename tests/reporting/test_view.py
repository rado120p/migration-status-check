from migration_validator.models.result import CheckResult, MatchInfo, ScopeResult, Severity, Status
from migration_validator.reporting.view import build_view, change_text


def _check(check_id, *, family=None, label="X", value="v", status=Status.PASS,
           mode="state", baseline_value=None, delta=None, message="msg", address=None):
    return CheckResult(
        id=check_id,
        mode=mode,
        status=status,
        severity=Severity.ADVISORY,
        message=message,
        label=label,
        family=family,
        value=value,
        baseline_value=baseline_value,
        delta=delta,
        details={"address": address} if address else {},
    )


_UNSET = object()


def _scope(checks, match=_UNSET) -> ScopeResult:
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
