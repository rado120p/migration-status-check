from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.scoping.mapping import Mapping, MappingRule, Selector
from migration_validator.scoping.matcher import match_scopes


def _scope(
    interface,
    description=None,
    service_type="Internet",
    subtype=None,
    routing_instance=None,
    addresses=(),
    addresses_v6=(),
    vlans=(),
    bridge_domains=(),
):
    label = description or interface
    return Scope(
        id=f"svc:{label}:{service_type}",
        kind="service",
        key=ScopeKey(description, service_type, subtype),
        selectors=Selectors(
            interfaces=[interface],
            routing_instances=[routing_instance] if routing_instance else [],
            local_ipv4=list(addresses),
            local_ipv6=list(addresses_v6),
            vlans=list(vlans),
            bridge_domains=list(bridge_domains),
        ),
    )


def test_matches_on_description_and_service_type():
    baseline = [_scope("ge-0/0/2.113", "L3VPN-CPE13-NNI", "IPVPN")]
    subject = [_scope("et-0/0/8.113", "L3VPN-CPE13-NNI", "IPVPN")]

    result = match_scopes(baseline, subject)

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.method == "description+service_type"
    assert pair.confidence == "high"
    assert pair.subject.selectors.interfaces == ["et-0/0/8.113"]
    assert result.unmatched_baseline == []
    assert result.unmatched_subject == []


def test_subtype_is_used_when_present():
    baseline = [_scope("ge-0/0/2.313", "EVPN-AWARE", "E-LAN", subtype="vlan-aware")]
    subject = [_scope("et-0/0/8.313", "EVPN-AWARE", "E-LAN", subtype="vlan-aware")]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "description+service_type+service_subtype"


def test_falls_back_to_routing_instance_when_description_missing():
    baseline = [_scope("ge-0/0/4.0", None, "IPVPN", routing_instance="L3VPN-CPE14-UNI")]
    subject = [_scope("et-0/0/10.0", None, "IPVPN", routing_instance="L3VPN-CPE14-UNI")]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "routing_instance+service_type"
    assert pair.confidence == "medium"


def test_falls_back_to_subnet():
    baseline = [_scope("ge-0/0/9.0", None, "Internet", addresses=["10.5.5.1/30"])]
    subject = [_scope("et-0/0/9.0", None, "Internet", addresses=["10.5.5.1/30"])]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "subnet+service_type"


def test_falls_back_to_subnet_on_ipv6():
    v6 = ["2001:db8:5:5::1/64"]
    baseline = [_scope("ge-0/0/9.0", None, "Internet", addresses_v6=v6)]
    subject = [_scope("et-0/0/9.0", None, "Internet", addresses_v6=v6)]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "subnet+service_type"


def test_subnet_rule_ignores_opposite_ends_of_p2p_link():
    baseline = [_scope("ge-0/0/1.0", None, "Core", addresses=["10.1.0.4/31"])]
    subject = [_scope("et-0/0/1.0", None, "Core", addresses=["10.1.0.5/31"])]

    result = match_scopes(baseline, subject)

    assert result.pairs == []
    assert len(result.unmatched_baseline) == 1
    assert len(result.unmatched_subject) == 1


def test_subnet_rule_ignores_opposite_ends_of_p2p_link_on_ipv6():
    baseline = [_scope("ge-0/0/1.0", None, "Core", addresses_v6=["2001:db8:6::/127"])]
    subject = [_scope("et-0/0/1.0", None, "Core", addresses_v6=["2001:db8:6::1/127"])]

    result = match_scopes(baseline, subject)

    assert result.pairs == []


def test_subnet_rule_pairs_same_address_on_p2p_prefix():
    baseline = [_scope("ge-0/0/1.0", None, "Core", addresses=["10.1.0.4/31"])]
    subject = [_scope("et-0/0/1.0", None, "Core", addresses=["10.1.0.4/31"])]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "subnet+service_type"


def test_subnet_rule_pairs_different_addresses_in_wide_subnet():
    baseline = [_scope("ge-0/0/9.0", None, "Internet", addresses=["192.0.2.1/24"])]
    subject = [_scope("et-0/0/9.0", None, "Internet", addresses=["192.0.2.2/24"])]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "subnet+service_type"


def test_falls_back_to_vlan():
    baseline = [_scope("ge-0/0/9.7", None, "Internet", vlans=["7"])]
    subject = [_scope("et-0/0/9.7", None, "Internet", vlans=["7"])]

    pair = match_scopes(baseline, subject).pairs[0]

    assert pair.method == "vlan+service_type"
    assert pair.confidence == "low"


def test_ambiguity_never_guesses():
    baseline = [_scope("ge-0/0/2.13", "SAME", "Internet")]
    subject = [
        _scope("et-0/0/8.13", "SAME", "Internet"),
        _scope("et-0/0/9.13", "SAME", "Internet"),
    ]

    result = match_scopes(baseline, subject)

    assert result.pairs == []
    assert len(result.unmatched_baseline) == 1
    assert "ambiguous" in result.unmatched_baseline[0].reason
    assert len(result.unmatched_subject) == 2


def test_unmatched_reasons_are_distinct():
    baseline = [_scope("ge-0/0/2.13", "ONLY-OLD", "Internet")]
    subject = [_scope("et-0/0/8.14", "ONLY-NEW", "E-LAN", subtype="vlan-aware")]

    result = match_scopes(baseline, subject)

    assert result.pairs == []
    assert result.unmatched_baseline[0].reason == "zadny kandidat na subject"
    assert result.unmatched_subject[0].reason == "nova sluzba, chybi baseline"


def test_manual_mapping_wins_over_automatic_rules():
    baseline = [_scope("ge-0/0/5.0", "EVPN-VLAN-AWARE-INTERNET", "Internet")]
    subject = [
        _scope("ae0.14", "EVPN-VLAN-AWARE-INTERNET", "E-LAN", subtype="vlan-aware"),
    ]
    mapping = Mapping(
        mappings=[
            MappingRule(
                baseline=Selector(
                    description="EVPN-VLAN-AWARE-INTERNET", service_type="Internet"
                ),
                subject=Selector(
                    description="EVPN-VLAN-AWARE-INTERNET", service_type="E-LAN"
                ),
            )
        ]
    )

    result = match_scopes(baseline, subject, mapping)

    assert len(result.pairs) == 1
    assert result.pairs[0].method == "manual"
    assert result.pairs[0].confidence == "manual"


def test_ignored_scopes_are_dropped_from_both_sides():
    baseline = [
        _scope("ge-0/0/2.13", "KEEP", "Internet"),
        _scope("ge-0/0/7.0", "DROP", "Internet"),
    ]
    subject = [
        _scope("et-0/0/8.13", "KEEP", "Internet"),
        _scope("et-0/0/7.0", "DROP", "Internet"),
    ]
    mapping = Mapping(ignore=[Selector(description="DROP")])

    result = match_scopes(baseline, subject, mapping)

    assert len(result.pairs) == 1
    assert result.pairs[0].baseline.key.description == "KEEP"
    assert result.unmatched_baseline == []
    assert result.unmatched_subject == []


def test_ambiguous_under_one_key_is_not_paired_under_a_sibling_key():
    """Vicehodnotove selektory: scope zahozeny jako nejednoznacny pod jednim
    klicem se nesmi sparovat pod jinym klicem tehoz pravidla."""
    baseline = [_scope("ge-0/0/9.7", None, "Internet", vlans=["7", "8"])]
    subject = [
        _scope("et-0/0/1.7", None, "Internet", vlans=["7"]),
        _scope("et-0/0/2.7", None, "Internet", vlans=["7"]),
        _scope("et-0/0/3.8", None, "Internet", vlans=["8"]),
    ]

    result = match_scopes(baseline, subject)

    paired_ids = {pair.baseline.id for pair in result.pairs}
    unmatched_ids = {item.scope.id for item in result.unmatched_baseline}
    assert not (paired_ids & unmatched_ids), "scope je zaroven sparovany i nesparovany"
    assert result.pairs == []
    assert "ambiguous" in result.unmatched_baseline[0].reason


def test_different_service_type_never_matches_automatically():
    baseline = [_scope("ge-0/0/5.0", "SAME-NAME", "Internet")]
    subject = [_scope("ae0.14", "SAME-NAME", "E-LAN", subtype="vlan-aware")]

    result = match_scopes(baseline, subject)

    assert result.pairs == []


def test_deactivation_does_not_block_pairing():
    """Sluzba deaktivovana na jedne strane se porad musi sparovat.

    Docasne deaktivovana sluzba se porad migruje, takze deaktivace nesmi
    rozhodovat o tom, jestli se najde protejsek - jinak by zmizela do
    NESPAROVANO a operator by prisel prave o ten radek, kvuli kteremu se
    priznak zavadi.

    Zabiji mutanta: doplneni podminky na priznak do klicovaci funkce nebo do
    filtru kandidatu v matcher.py. Dnes tam neni; tenhle test hlida, aby se
    tam nedostala.
    """
    baseline = [_scope("ge-0/0/2.113", "L3VPN-CPE13-NNI", "IPVPN")]
    subject = [_scope("et-0/0/8.113", "L3VPN-CPE13-NNI", "IPVPN")]
    baseline[0].interface_active = False

    result = match_scopes(baseline, subject)

    assert len(result.pairs) == 1
    assert result.pairs[0].method == "description+service_type"
    assert not result.unmatched_baseline
    assert not result.unmatched_subject


def test_local_scope_pairs_across_platforms_ignoring_domain_names():
    """Local E-LAN scope (globalni bridge-domain/vlan): domenove jmeno neni
    parovaci klic - MX BD-* a PTX VL-* jmena tehoz portu se paruji podle
    description+service_type+service_subtype, ne podle bridge_domains."""
    baseline = [
        _scope(
            "ge-0/0/2.12",
            "NGMVPN-IGMP-RECEIVER",
            "E-LAN",
            subtype="local",
            vlans=["12"],
            bridge_domains=["BD-NGMVPN-IGMP-RECEIVER"],
        )
    ]
    subject = [
        _scope(
            "et-0/0/8.12",
            "NGMVPN-IGMP-RECEIVER",
            "E-LAN",
            subtype="local",
            vlans=["12"],
            bridge_domains=["VL-NGMVPN-IGMP-RECEIVER"],
        )
    ]

    result = match_scopes(baseline, subject)

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.method == "description+service_type+service_subtype"
    assert pair.baseline.selectors.bridge_domains == ["BD-NGMVPN-IGMP-RECEIVER"]
    assert pair.subject.selectors.bridge_domains == ["VL-NGMVPN-IGMP-RECEIVER"]
    assert pair.subject.selectors.interfaces == ["et-0/0/8.12"]


def test_two_local_scopes_pair_independently_never_cross():
    """Dve local sluzby na jednom boxu s ruznymi descriptions se paruji
    kazda se svym protejskem, nikdy navzajem."""
    baseline = [
        _scope(
            "ge-0/0/2.12",
            "NGMVPN-IGMP-RECEIVER",
            "E-LAN",
            subtype="local",
            vlans=["12"],
            bridge_domains=["BD-NGMVPN-IGMP-RECEIVER"],
        ),
        _scope(
            "ge-0/0/2.10",
            "NGMVPN-PIM-RECEIVER",
            "E-LAN",
            subtype="local",
            vlans=["10"],
            bridge_domains=["BD-NGMVPN-PIM-RECEIVER"],
        ),
    ]
    subject = [
        _scope(
            "et-0/0/8.10",
            "NGMVPN-PIM-RECEIVER",
            "E-LAN",
            subtype="local",
            vlans=["10"],
            bridge_domains=["VL-NGMVPN-PIM-RECEIVER"],
        ),
        _scope(
            "et-0/0/8.12",
            "NGMVPN-IGMP-RECEIVER",
            "E-LAN",
            subtype="local",
            vlans=["12"],
            bridge_domains=["VL-NGMVPN-IGMP-RECEIVER"],
        ),
    ]

    result = match_scopes(baseline, subject)

    assert len(result.pairs) == 2
    by_description = {
        pair.baseline.key.description: pair for pair in result.pairs
    }
    igmp = by_description["NGMVPN-IGMP-RECEIVER"]
    pim = by_description["NGMVPN-PIM-RECEIVER"]
    assert igmp.subject.key.description == "NGMVPN-IGMP-RECEIVER"
    assert pim.subject.key.description == "NGMVPN-PIM-RECEIVER"
    assert result.unmatched_baseline == []
    assert result.unmatched_subject == []


def test_pairing_works_when_both_sides_are_deactivated():
    """Sluzba deaktivovana na obou zarizenich se taky musi sparovat.

    Bez tohoto testu by slo AR-24 splnit podminkou "sparuj, jen kdyz se
    priznaky lisi" - tedy poloviately.
    """
    baseline = [_scope("ge-0/0/2.113", "L3VPN-CPE13-NNI", "IPVPN")]
    subject = [_scope("et-0/0/8.113", "L3VPN-CPE13-NNI", "IPVPN")]
    baseline[0].interface_active = False
    subject[0].interface_active = False

    result = match_scopes(baseline, subject)

    assert len(result.pairs) == 1


def _ifaces(pairs):
    return {(p.baseline.selectors.interfaces[0], p.subject.selectors.interfaces[0]) for p in pairs}


def test_ambiguous_scopes_fall_through_to_routing_instance():
    """Review E5: popis "CPE" ve dvou VRF, stejna /30 na obou boxech -
    dnes 0 paru, pravidlo routing_instance je rozlisi."""
    baseline = [
        _scope("ge-0/0/2.100", "CPE", "IPVPN", routing_instance="customer-a",
               addresses=["192.168.1.1/30"]),
        _scope("ge-0/0/2.200", "CPE", "IPVPN", routing_instance="customer-b",
               addresses=["192.168.1.1/30"]),
    ]
    subject = [
        _scope("et-0/0/8.100", "CPE", "IPVPN", routing_instance="customer-a",
               addresses=["192.168.1.1/30"]),
        _scope("et-0/0/8.200", "CPE", "IPVPN", routing_instance="customer-b",
               addresses=["192.168.1.1/30"]),
    ]

    result = match_scopes(baseline, subject)

    assert _ifaces(result.pairs) == {("ge-0/0/2.100", "et-0/0/8.100"),
                                     ("ge-0/0/2.200", "et-0/0/8.200")}
    assert {p.method for p in result.pairs} == {"routing_instance+service_type"}
    assert {p.confidence for p in result.pairs} == {"medium"}
    assert result.unmatched_baseline == [] and result.unmatched_subject == []


def test_ambiguity_resolved_by_later_rule_leaves_leftover_as_new_service():
    """Step beh: stary port ma CPE ve VRF-a, novy box CPE ve VRF-a (tento
    krok) i VRF-b (drivejsi vlna). VRF-b souperil jen se sparovanou
    baseline -> nova sluzba, ne ambiguous."""
    baseline = [_scope("ge-0/0/2.100", "CPE", "IPVPN", routing_instance="VRF-a")]
    subject = [
        _scope("et-0/0/8.100", "CPE", "IPVPN", routing_instance="VRF-a"),
        _scope("et-0/0/8.200", "CPE", "IPVPN", routing_instance="VRF-b"),
    ]

    result = match_scopes(baseline, subject)

    assert _ifaces(result.pairs) == {("ge-0/0/2.100", "et-0/0/8.100")}
    assert [(u.scope.selectors.interfaces, u.reason) for u in result.unmatched_subject] == [
        (["et-0/0/8.200"], "nova sluzba, chybi baseline"),
    ]


def test_ambiguity_unresolved_keeps_ambiguous_reason_on_both_sides():
    baseline = [_scope("ge-0/0/2.13", "SAME", "Internet")]
    subject = [_scope("et-0/0/8.13", "SAME", "Internet"), _scope("et-0/0/9.13", "SAME", "Internet")]

    result = match_scopes(baseline, subject)

    assert result.pairs == []
    # all() na prazdnem seznamu je vzdy True - delka se overuje explicitne,
    # aby test necekal falesne "passed" na chybejicich zaznamech.
    assert len(result.unmatched_baseline) == 1 and len(result.unmatched_subject) == 2
    assert all("ambiguous" in u.reason for u in result.unmatched_baseline + result.unmatched_subject)


def test_local_switch_pair_resolved_by_vlan():
    """Lab 2026-09-25: EVPN-VPWS-LOCAL - na PTX obe AC stejny popis,
    stejna RI, rozlisi je az vlan."""
    baseline = [
        _scope("ge-0/0/2.211", "EVPN-VPWS-LOCAL-CPE1", "E-Line", "vpws",
               routing_instance="EVPN-VPWS-LOCAL", vlans=["211"]),
        _scope("ge-0/0/2.212", "EVPN-VPWS-LOCAL-CPE2", "E-Line", "vpws",
               routing_instance="EVPN-VPWS-LOCAL", vlans=["212"]),
    ]
    subject = [
        _scope("et-0/0/8.211", "EVPN-VPWS-LOCAL", "E-Line", "vpws",
               routing_instance="EVPN-VPWS-LOCAL", vlans=["211"]),
        _scope("et-0/0/8.212", "EVPN-VPWS-LOCAL", "E-Line", "vpws",
               routing_instance="EVPN-VPWS-LOCAL", vlans=["212"]),
    ]

    result = match_scopes(baseline, subject)

    assert _ifaces(result.pairs) == {("ge-0/0/2.211", "et-0/0/8.211"),
                                     ("ge-0/0/2.212", "et-0/0/8.212")}
    assert {p.method for p in result.pairs} == {"vlan+service_type"}
    assert result.unmatched_baseline == [] and result.unmatched_subject == []


def test_ambiguous_conflict_under_subnet_rule_is_order_independent():
    """F1 (fix round 1 review): stejna data, jiny vysledek podle poradi
    zpracovani klicu = hadani. B ma dve /30 adresy N1+N2, B2 jen N2,
    S ma taky N1+N2 - bez ohledu na poradi adres/poradi baseline scope
    musi vyjit 0 paru a vsechny tri "ambiguous"."""
    n1 = "192.168.1.1/30"
    n2 = "192.168.2.1/30"

    for b_addresses in ([n1, n2], [n2, n1]):
        for baseline_order in ("b_first", "b2_first"):
            b = _scope("ge-0/0/2.1", None, "Internet", addresses=list(b_addresses))
            b2 = _scope("ge-0/0/2.2", None, "Internet", addresses=[n2])
            s = _scope("et-0/0/8.1", None, "Internet", addresses=[n1, n2])
            baseline = [b, b2] if baseline_order == "b_first" else [b2, b]
            subject = [s]

            result = match_scopes(baseline, subject)

            assert result.pairs == [], (b_addresses, baseline_order)
            assert len(result.unmatched_baseline) == 2, (b_addresses, baseline_order)
            assert len(result.unmatched_subject) == 1, (b_addresses, baseline_order)
            assert all(
                "ambiguous" in u.reason
                for u in result.unmatched_baseline + result.unmatched_subject
            ), (b_addresses, baseline_order)


def test_conflicting_candidates_under_two_keys_of_one_rule_is_ambiguous():
    """F1: B ma vlany 10 a 20, S1 jen 10, S2 jen 20 (zadny popis/RI/subnet
    ktery by je odlisil) - kazdy klic sam o sobe da jednoznacny par, ale B
    ma dva ruzne kandidaty napric klici, takze nesmi hadat."""
    for b_vlans in (["10", "20"], ["20", "10"]):
        baseline = [_scope("ge-0/0/2.1", None, "Internet", vlans=b_vlans)]
        subject = [
            _scope("et-0/0/8.1", None, "Internet", vlans=["10"]),
            _scope("et-0/0/8.2", None, "Internet", vlans=["20"]),
        ]

        result = match_scopes(baseline, subject)

        assert result.pairs == [], b_vlans
        assert len(result.unmatched_baseline) == 1, b_vlans
        assert "ambiguous" in result.unmatched_baseline[0].reason, b_vlans


def test_reason_falls_back_only_when_every_rival_is_paired():
    """F2: pin any() vs all() v _reason. B "CPE" (bez RI) souperi s S1 i S2
    o popis; B' "OTHER"/RI=a se pozdeji jednoznacne sparuje s S1/RI=a.
    B ma porad nesparovaneho soupere S2 -> zustava "ambiguous". S2 ma
    jedineho souperem B, ktery taky zustava nesparovany -> taky
    "ambiguous". S all() by misto toho B dostalo "zadny kandidat na
    subject" (viz report - overeno mutantem)."""
    baseline = [
        _scope("ge-0/0/2.1", "CPE", "Internet"),
        _scope("ge-0/0/2.2", "OTHER", "Internet", routing_instance="a"),
    ]
    subject = [
        _scope("et-0/0/8.1", "CPE", "Internet", routing_instance="a"),
        _scope("et-0/0/8.2", "CPE", "Internet"),
    ]

    result = match_scopes(baseline, subject)

    assert _ifaces(result.pairs) == {("ge-0/0/2.2", "et-0/0/8.1")}
    b_reason = next(
        u.reason for u in result.unmatched_baseline if u.scope.selectors.interfaces == ["ge-0/0/2.1"]
    )
    s_reason = next(
        u.reason for u in result.unmatched_subject if u.scope.selectors.interfaces == ["et-0/0/8.2"]
    )
    assert b_reason.startswith("ambiguous: 2 kandidatu")
    assert "ambiguous" in s_reason


def test_later_rule_ambiguity_survives_earlier_rule_resolution():
    """F3: prvni (nejsilnejsi) nejednoznacnost scope Sx se popisem vyresi
    (B1 si vezme vlan-shoda), ale Sx porad souperi s jeste nesparovanym
    B2 pod RI pravidlem - musi zustat "ambiguous", ne skoncit jako "nova
    sluzba" jen proto, ze jeho PRVNI souper uz je sparovany jinam."""
    baseline = [
        _scope("ge-0/0/2.1", "CPE", "Internet", routing_instance="a", vlans=["10"]),
        _scope("ge-0/0/2.2", "OTHER", "Internet", routing_instance="a", vlans=["20"]),
    ]
    subject = [
        _scope("et-0/0/8.1", "CPE", "Internet", routing_instance="a", vlans=["10"]),
        _scope("et-0/0/8.2", "CPE", "Internet", routing_instance="a", vlans=["30"]),
    ]

    result = match_scopes(baseline, subject)

    assert _ifaces(result.pairs) == {("ge-0/0/2.1", "et-0/0/8.1")}
    sx_reason = next(
        u.reason for u in result.unmatched_subject if u.scope.selectors.interfaces == ["et-0/0/8.2"]
    )
    b2_reason = next(
        u.reason for u in result.unmatched_baseline if u.scope.selectors.interfaces == ["ge-0/0/2.2"]
    )
    assert "ambiguous" in sx_reason
    assert "ambiguous" in b2_reason
