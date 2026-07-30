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
