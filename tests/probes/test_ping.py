import ipaddress

import pytest
from lxml import etree

from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.probes.ping import (
    PingTarget,
    parse_ping_result,
    resolve_targets,
    run_ping,
    subnet_fallback,
)


def _scope(
    scope_id="svc:X:IPVPN",
    service_type="IPVPN",
    interfaces=("ge-0/0/2.113",),
    addresses=("198.11.13.1/30",),
    local_ipv6=(),
    virtual_gw_v4=(),
    virtual_gw_v6=(),
    routing_instance="L3VPN-CPE13-NNI",
    bgp_neighbors=(),
    bgp_neighbors_inactive=(),
):
    # local_ipv6/virtual_gw_v6 doplneny, aby na ne slo sahnout - drivejsi
    # verze tenhle helper rozdelila jen napul (IPv4 vetev), IPv6 byla skrz
    # nej nedosazitelna.
    return Scope(
        id=scope_id,
        kind="service",
        key=ScopeKey("X", service_type, None),
        selectors=Selectors(
            interfaces=list(interfaces),
            local_ipv4=list(addresses),
            local_ipv6=list(local_ipv6),
            virtual_gw_v4=list(virtual_gw_v4),
            virtual_gw_v6=list(virtual_gw_v6),
            routing_instances=[routing_instance] if routing_instance else [],
            bgp_neighbors=list(bgp_neighbors),
            bgp_neighbors_inactive=list(bgp_neighbors_inactive),
        ),
    )


@pytest.mark.parametrize(
    "address,expected",
    [
        ("198.11.13.1/30", "198.11.13.2"),
        ("198.11.13.2/30", "198.11.13.1"),
        ("10.1.2.1/31", "10.1.2.0"),
        ("10.1.2.0/31", "10.1.2.1"),
        ("152.11.14.1/29", "152.11.14.2"),
        ("150.0.0.11/32", None),
    ],
)
def test_subnet_fallback(address, expected):
    assert subnet_fallback([address], 4) == expected


def test_ipv6_fallback_only_on_point_to_point():
    """Do /64 se nestrili - zarucene neuspesny ping se cte jako nedostupne CPE."""
    assert subnet_fallback(["2001:abcd:11:13::a/127"], 6) == "2001:abcd:11:13::b"
    assert subnet_fallback(["2001:db8::2/64"], 6) is None


def test_subnet_fallback_excludes_virtual_gateway_v4():
    """Regrese na self-ping: irb.14 ma adresu .2/29 a VGW .1.

    Bez `owned` fallback vrati .1 (prvni kandidat po vynechani sitove .0),
    coz je presne adresa, kterou `source_address()` uz zvolila jako zdroj -
    ping sam na sebe. Overeno proti realne laborce (172.20.20.5, EVO).
    """
    without_fix = subnet_fallback(["152.11.14.2/29"], 4)
    assert without_fix == "152.11.14.1"  # bug: to je VGW

    with_fix = subnet_fallback(["152.11.14.2/29"], 4, owned=["152.11.14.1"])
    assert with_fix == "152.11.14.3"
    assert with_fix != "152.11.14.1"


def test_subnet_fallback_excludes_virtual_gateway_v6():
    """Stejna chyba je rodino-agnosticka - overeno i na IPv6 strane.

    /127 by fallback nikdy nespustil (jen 2 adresy, obe uz vyloucene), takze
    scenar potrebuje kratsi prefix - /126, ktery je jeste v ramci
    IPV6_FALLBACK_MIN_PREFIX. Vlastni adresa je na ::0 (site-adresa se v
    IPv6 nevylucuje - viz komentar u subnet_fallback), VGW na ::1 - presne
    tvar, ktery bez `owned` vrati VGW jako prvniho kandidata po vlastni
    adrese.
    """
    without_fix = subnet_fallback(["2001:db8:11:14::0/126"], 6)
    assert without_fix == "2001:db8:11:14::1"  # bug: to je VGW

    with_fix = subnet_fallback(
        ["2001:db8:11:14::0/126"], 6, owned=["2001:db8:11:14::1"]
    )
    assert with_fix == "2001:db8:11:14::2"
    assert with_fix != "2001:db8:11:14::1"


def test_subnet_fallback_without_owned_keeps_old_behaviour():
    """`owned` je volitelny - scopy bez VGW se chovaji jako drive."""
    assert subnet_fallback(["152.11.14.1/29"], 4) == "152.11.14.2"


def test_targets_come_from_arp():
    arp = [
        {"ip": "198.11.13.2", "interface": "ge-0/0/2.113"},
        {"ip": "198.11.13.3", "interface": "ge-0/0/2.113"},
        {"ip": "10.9.9.9", "interface": "ge-0/0/9.0"},
    ]
    targets = resolve_targets([_scope()], arp)

    assert [target.target for target in targets] == ["198.11.13.2", "198.11.13.3"]
    assert all(target.resolved_from == "arp" for target in targets)
    assert "source" not in targets[0].to_dict()
    assert all(target.routing_instance == "L3VPN-CPE13-NNI" for target in targets)


def test_local_learned_arp_entry_is_not_a_target():
    """EVPN VLAN-AWARE: zaznam nauceny pres .local..N je host za vzdalenym
    PE - pingat ho nema smysl (produkce 2026-08). Prefix, ne substring:
    learned_via 'ae0.14' projit musi."""
    arp = [
        {"ip": "198.11.13.2", "interface": "ge-0/0/2.113", "learned_via": ".local..9"},
        {"ip": "198.11.13.3", "interface": "ge-0/0/2.113", "learned_via": "ae0.14"},
        {"ip": "198.11.13.4", "interface": "ge-0/0/2.113", "learned_via": None},
    ]

    targets = resolve_targets([_scope()], arp)

    assert [t.target for t in targets] == ["198.11.13.3", "198.11.13.4"]


def test_local_learned_nd_entry_is_not_a_target():
    scope = _scope(
        interfaces=("et-0/0/8.13",), addresses=(), local_ipv6=("2001:abcd:11:13::a/64",)
    )
    nd = [
        {
            "ip": "2001:abcd:11:13::b",
            "mac": "0c:00:ef:5e:df:01",
            "interface": "et-0/0/8.13",
            "state": "reachable",
            "learned_via": ".local..7",
        },
        {
            "ip": "2001:abcd:11:13::c",
            "mac": "0c:00:ef:5e:df:02",
            "interface": "et-0/0/8.13",
            "state": "reachable",
            "learned_via": None,
        },
    ]

    targets = resolve_targets([scope], [], nd)

    assert [t.target for t in targets] == ["2001:abcd:11:13::c"]


def test_baseline_skips_local_learned_entries():
    """Baseline paruje jen podle subnetu - bez filtru by remote-PE hosty ze
    stareho boxu vratil presne pri cutoveru, kdy se baseline pouziva."""
    scope = _scope(interfaces=("irb.14",), addresses=("152.11.14.1/29",))
    baseline_arp = [
        {"ip": "152.11.14.4", "interface": "irb.14", "learned_via": ".local..5"},
        {"ip": "152.11.14.5", "interface": "irb.14", "learned_via": "ae0.14"},
    ]

    targets = resolve_targets([scope], [], baseline_arp=baseline_arp)

    assert [t.target for t in targets] == ["152.11.14.5"]
    assert targets[0].resolved_from == "baseline-arp"


def test_all_local_arp_suppresses_subnet_fallback():
    """Kdyz je jedinym dukazem .local zaznam, hosti ziji za vzdalenym PE -
    subnet-fallback by vyrobil cil, ktery nikdo nevlastni, a report by lhal
    cervenym radkem. Zadny cil je pravdivejsi (SKIP 'bez cile')."""
    arp = [{"ip": "198.11.13.2", "interface": "ge-0/0/2.113", "learned_via": ".local..9"}]

    targets = resolve_targets([_scope()], arp)

    assert targets == []


def test_all_local_baseline_suppresses_subnet_fallback():
    scope = _scope(interfaces=("irb.14",), addresses=("152.11.14.1/29",))
    baseline_arp = [
        {"ip": "152.11.14.4", "interface": "irb.14", "learned_via": ".local..5"}
    ]

    targets = resolve_targets([scope], [], baseline_arp=baseline_arp)

    assert targets == []


def test_local_nd_does_not_suppress_ipv4_fallback():
    """Potlaceni je per rodina: .local dukaz v ND (IPv6) nesmi vypnout
    IPv4 subnet-fallback."""
    scope = _scope(addresses=("198.11.13.1/30",), local_ipv6=("2001:db8::/126",))
    nd = [
        {
            "ip": "2001:db8::2",
            "mac": "0c:00:ef:5e:df:01",
            "interface": "ge-0/0/2.113",
            "state": "reachable",
            "learned_via": ".local..3",
        }
    ]

    targets = resolve_targets([scope], [], nd)

    assert [(t.family, t.target) for t in targets] == [(4, "198.11.13.2")]


def test_foreign_subnet_local_baseline_does_not_suppress_fallback():
    """.local zaznam z ciziho subnetu neni dukaz o tomto scope - fallback bezi."""
    baseline_arp = [
        {"ip": "10.99.99.9", "interface": "irb.99", "learned_via": ".local..7"}
    ]

    targets = resolve_targets([_scope()], [], baseline_arp=baseline_arp)

    assert [t.target for t in targets] == ["198.11.13.2"]
    assert targets[0].resolved_from == "subnet-fallback"


def test_internet_service_has_no_routing_instance():
    targets = resolve_targets(
        [_scope(service_type="Internet", routing_instance=None)],
        [{"ip": "152.11.13.2", "interface": "ge-0/0/2.113"}],
    )
    assert targets[0].routing_instance is None


def test_empty_arp_falls_back_to_subnet():
    targets = resolve_targets([_scope()], [])
    assert len(targets) == 1
    assert targets[0].target == "198.11.13.2"
    assert targets[0].resolved_from == "subnet-fallback"


def test_irb_fallback_never_pings_own_virtual_gateway():
    """Regrese na live nalez: irb.14, 152.11.14.2/29, VGW 152.11.14.1.

    Bez opravy vraci ARP-prazdny scope fallback na 152.11.14.1 - presne tu
    adresu, kterou by scope pouzil jako VGW. Ping tak jde sam na sebe a
    sluzba dostane verdikt o nicem. Overeno proti 172.20.20.5.
    """
    scope = _scope(
        scope_id="svc:EVPN-VLAN-AWARE-INTERNET:Internet",
        service_type="Internet",
        interfaces=("irb.14",),
        addresses=("152.11.14.2/29",),
        virtual_gw_v4=("152.11.14.1",),
        routing_instance=None,
    )

    targets = resolve_targets([scope], [])

    assert len(targets) == 1
    assert targets[0].target == "152.11.14.3"
    assert targets[0].target != "152.11.14.1"


def test_no_resolved_target_is_ever_own_address():
    """Invariant, ne implementacni detail: ping na vlastni adresu je vzdy
    nesmysl - guard je own_addresses, ne source.

    Pinuje se pres vsechny cesty, ktere resolve_targets muze vzit - ARP,
    ND i subnet-fallback, s i bez virtual-gw - aby regrese kdekoliv v
    tomhle toku spolehlive spadla na tomhle testu.
    """
    scopes = [
        _scope(
            scope_id="svc:irb-v4:Internet",
            service_type="Internet",
            interfaces=("irb.14",),
            addresses=("152.11.14.2/29",),
            virtual_gw_v4=("152.11.14.1",),
            routing_instance=None,
        ),
        _scope(
            scope_id="svc:irb-v6:Internet",
            service_type="Internet",
            interfaces=("irb.15",),
            addresses=(),
            local_ipv6=("2001:db8:11:15::0/126",),
            virtual_gw_v6=("2001:db8:11:15::1",),
            routing_instance=None,
        ),
        _scope(),
    ]
    arp = [{"ip": "198.11.13.2", "interface": "ge-0/0/2.113"}]

    targets = resolve_targets(scopes, arp)

    own = {
        ipaddress.ip_address(address)
        for address in (
            "152.11.14.2",
            "152.11.14.1",
            "2001:db8:11:15::0",
            "2001:db8:11:15::1",
            "198.11.13.1",
        )
    }
    assert targets
    assert all(ipaddress.ip_address(target.target) not in own for target in targets)


@pytest.mark.parametrize("position", (0, 1, 2))
def test_arp_guard_drops_own_address_at_any_position(position):
    """Gratuitous ARP / duplicitni adresa: vlastni zdrojova adresa se muze
    objevit primo v ARP tabulce, ne jen dojit z owned/VGW smeru. `owned` na
    tohle nema dosah - ARP vetev vubec nevola subnet_fallback. Jedine, co
    tu self-ping brani, je own_addresses guard v resolve_targets.

    Vlastni adresa obchazi vsechny pozice v tabulce zamerne. Kdyz stala jen
    na indexu 0, prosel by mutant, ktery kontrolu own_addresses zkratkuje
    hned po prvnim prvku (napr. `if index > 0 or not _is_own(...)`) - test
    by pak dokazoval jen to, ze pojistka existuje, ne ze plati na kazdy
    zaznam. Parametrizace pres pozice 0, 1, 2 tomuhle brani.

    Sousedi po obou stranach jsou tam i proto, aby test nemohl projit diky
    tomu, ze prazdny seznam po filtru spustil `if addresses:` vetev jako
    celek - filtr musi vyradit presne jednu polozku a ostatni nechat byt.
    """
    neighbours = ["198.11.13.2", "198.11.13.3"]
    ips = list(neighbours)
    ips.insert(position, "198.11.13.1")  # vlastni adresa scope
    arp = [{"ip": ip, "interface": "ge-0/0/2.113"} for ip in ips]

    targets = resolve_targets([_scope(addresses=("198.11.13.1/24",))], arp)

    assert [t.target for t in targets] == neighbours


@pytest.mark.parametrize("position", (0, 1, 2))
def test_nd_guard_drops_own_address_at_any_position(position):
    """IPv6 obdoba: IRB muze odpovidat za svou vlastni adresu v ND (nebo jde
    o duplicate-address stav behem cutoveru) - vlastni adresa se objevi
    primo v ND tabulce. Zase mimo dosah `owned` (ND vetev take nevola
    subnet_fallback) - chrani jen own_addresses guard v resolve_targets.

    Adresa scope je /64, ne /127: pri /127 vratil subnet_fallback presne
    tehoz souseda, ktery se ocekaval z ND, takze i uplne smazana ND vetev
    prosla pres fallback. Na /64 fallback mlci (IPV6_FALLBACK_MIN_PREFIX je
    126), takze jediny mozny zdroj cile je ND - a `resolved_from` to rovnou
    tvrdi, aby to nezaviselo jen na te konstante.
    """
    scope = _scope(
        interfaces=("et-0/0/8.13",), addresses=(), local_ipv6=("2001:abcd:11:13::a/64",)
    )
    neighbours = ["2001:abcd:11:13::b", "2001:abcd:11:13::c"]
    ips = list(neighbours)
    ips.insert(position, "2001:abcd:11:13::a")  # vlastni adresa scope
    nd = [
        {
            "ip": ip,
            "mac": "0c:00:ef:5e:df:01",
            "interface": "et-0/0/8.13",
            "state": "reachable",
        }
        for ip in ips
    ]

    targets = resolve_targets([scope], [], nd)

    assert [t.target for t in targets] == neighbours
    assert all(t.resolved_from == "nd" for t in targets)


def test_fallback_owned_covers_self_ping_from_overlapping_local_ranges():
    """Fallback shape, kde `owned` (prazdny - zadny VGW) self-ping nechyti,
    ale volani subnet_fallback s owned=[*local, *owned] ano.

    Scope ma dve IPv4 adresy: "10.0.0.1/32" a "10.0.0.2/30" (zadny VGW).
    Prvni adresa je /32, subnet_fallback ji preskoci (network.prefixlen >=
    max_prefixlen). Padne to na druhou adresu, jejiz sit 10.0.0.0/30
    obsahuje 10.0.0.1 jako validniho kandidata - a ten je presne jedna z
    local adres scope. Puvodni `owned` o tomhle prekryvu nic nevi (neni to
    VGW), ale kdyz resolve_targets poslal do subnet_fallback i local adresy
    jako owned, fallback vrati zadny cil.
    """
    scope = _scope(addresses=("10.0.0.1/32", "10.0.0.2/30"), virtual_gw_v4=())

    targets = resolve_targets([scope], [])

    assert targets == []


def test_no_targets_for_core_or_elan_scopes():
    scopes = [
        _scope(scope_id="svc:C:Core", service_type="Core"),
        _scope(scope_id="svc:E:E-LAN", service_type="E-LAN"),
    ]
    assert resolve_targets(scopes, []) == []


def test_device_scope_produces_no_targets():
    from migration_validator.models.scope import device_scope

    assert resolve_targets([device_scope()], [{"ip": "1.2.3.4", "interface": "ge-0/0/0"}]) == []


def test_ipv6_targets_come_from_nd():
    scope = _scope(
        interfaces=("et-0/0/8.13",), addresses=(), local_ipv6=("2001:abcd:11:13::a/127",)
    )
    nd = [
        {
            "ip": "2001:abcd:11:13::b",
            "mac": "0c:00:ef:5e:df:01",
            "interface": "et-0/0/8.13",
            "state": "reachable",
        }
    ]

    targets = resolve_targets([scope], [], nd)

    assert [t.target for t in targets] == ["2001:abcd:11:13::b"]
    assert targets[0].family == 6
    assert targets[0].resolved_from == "nd"
    # Bez toho by prosel i regres, ktery interface pripoji vzdy - normalni
    # (ne link-local) cil by pak byl pripnuty na jednu linku misto smerovani.
    assert targets[0].interface is None


def test_unusable_nd_entries_are_not_targets():
    """Zaznam bez MAC nebo v nedokoncenem stavu neni cil - strilet na nej nema smysl.

    Kazdy prvek pravidla ma vlastni zaznam, aby test poznal, kdyby
    _usable_nd testovala jen nektery z nich - tedy i `unreachable` zvlast
    od `incomplete`. Oba nesou platnou MAC: v nahranem fixture z laborky
    (rpc/junos/nd.xml) ma jediny `unreachable` zaznam zaroven mac "none",
    takze ho odmitne uz prvni podminka a ta stavova zustane nedotcena.
    Prave tahle polovina pravidla pritom na realnych datech sepne.

    Posledni, plne pouzitelny zaznam dokazuje, ze filtr neodmita vsechno
    paplosne.
    """
    scope = _scope(interfaces=("et-0/0/8.13",), addresses=(), local_ipv6=("2001:db8::2/64",))
    nd = [
        {
            "ip": "2001:db8::1",
            "mac": "none",
            "interface": "et-0/0/8.13",
            "state": "reachable",
        },
        {
            "ip": "2001:db8::4",
            "mac": "0c:00:ef:5e:df:01",
            "interface": "et-0/0/8.13",
            "state": "incomplete",
        },
        {
            "ip": "2001:db8::5",
            "mac": "0c:00:ef:5e:df:03",
            "interface": "et-0/0/8.13",
            "state": "unreachable",
        },
        {
            "ip": "2001:db8::3",
            "mac": "0c:00:ef:5e:df:02",
            "interface": "et-0/0/8.13",
            "state": "reachable",
        },
    ]

    targets = resolve_targets([scope], [], nd)

    assert [t.target for t in targets] == ["2001:db8::3"]


def test_link_local_target_carries_interface():
    scope = _scope(interfaces=("et-0/0/8.13",), addresses=(), local_ipv6=("fe80::1/64",))
    nd = [
        {
            "ip": "fe80::c66b:b8ff:fe48:0",
            "mac": "c4:6b:b8:48:00:00",
            "interface": "et-0/0/8.13",
            "state": "stale",
        }
    ]

    targets = resolve_targets([scope], [], nd)

    assert targets[0].interface == "et-0/0/8.13"


def test_link_local_ignored_when_not_configured():
    """Bez nakonfigurovane link-local adresy se link-local soused ignoruje.

    Cil misto toho vznikne z /127 fallbacku na stejnem scope - je to p2p
    linka, fallback tam neni nahodny strel jako u /64.
    """
    scope = _scope(
        interfaces=("et-0/0/8.13",), addresses=(), local_ipv6=("2001:abcd:11:13::a/127",)
    )
    nd = [
        {
            "ip": "fe80::c66b:b8ff:fe48:0",
            "mac": "c4:6b:b8:48:00:00",
            "interface": "et-0/0/8.13",
            "state": "stale",
        }
    ]

    targets = resolve_targets([scope], [], nd)

    assert [t.resolved_from for t in targets] == ["subnet-fallback"]
    assert not any(t.target.startswith("fe80:") for t in targets)


def test_targets_are_ordered_ipv4_before_ipv6():
    scope = _scope(
        interfaces=("et-0/0/8.13",),
        addresses=("152.11.13.1/30",),
        local_ipv6=("2001:abcd:11:13::a/127",),
    )
    arp = [{"ip": "152.11.13.2", "interface": "et-0/0/8.13"}]
    nd = [
        {
            "ip": "2001:abcd:11:13::b",
            "mac": "0c:00:ef:5e:df:01",
            "interface": "et-0/0/8.13",
            "state": "reachable",
        }
    ]

    targets = resolve_targets([scope], arp, nd)

    assert [t.family for t in targets] == [4, 6]


def test_baseline_arp_wins_over_own_arp():
    scope = _scope(addresses=("192.0.2.1/24",))
    baseline_arp = [{"ip": "192.0.2.50", "interface": "ge-0/0/0.100"}]

    targets = resolve_targets([scope], [], baseline_arp=baseline_arp)

    assert [t.target for t in targets] == ["192.0.2.50"]
    assert targets[0].resolved_from == "baseline-arp"
    assert targets[0].interface is None

    own_arp = [{"ip": "192.0.2.60", "interface": "ge-0/0/2.113"}]
    targets_with_own = resolve_targets([scope], own_arp, baseline_arp=baseline_arp)

    assert [t.target for t in targets_with_own] == ["192.0.2.50"]
    assert targets_with_own[0].resolved_from == "baseline-arp"


def test_baseline_filters_by_scope_subnet():
    scope = _scope(addresses=("192.0.2.1/24",))
    baseline_arp = [{"ip": "10.9.9.9", "interface": "ge-0/0/0.100"}]
    own_arp = [{"ip": "192.0.2.60", "interface": "ge-0/0/2.113"}]

    targets = resolve_targets([scope], own_arp, baseline_arp=baseline_arp)

    assert [t.target for t in targets] == ["192.0.2.60"]
    assert targets[0].resolved_from == "arp"


def test_baseline_excludes_own_and_vgw():
    """Filtr musi vyradit presne source a VGW a nechat platneho souseda byt.

    Bez toho by test prosel i kdyby _baseline_addresses vzdy vratila prazdny
    seznam - viz vzor v test_arp_guard_drops_own_address_at_any_position.
    """
    scope = _scope(
        interfaces=("irb.14",),
        addresses=("152.11.14.2/29",),
        virtual_gw_v4=("152.11.14.1",),
        routing_instance=None,
    )
    baseline_arp = [
        {"ip": "152.11.14.2", "interface": "ge-0/0/0.14"},  # source (local_ipv4)
        {"ip": "152.11.14.1", "interface": "ge-0/0/0.14"},  # VGW
        {"ip": "152.11.14.4", "interface": "ge-0/0/0.14"},  # platny soused
    ]

    targets = resolve_targets([scope], [], baseline_arp=baseline_arp)

    assert [t.target for t in targets] == ["152.11.14.4"]
    assert targets[0].resolved_from == "baseline-arp"


def test_baseline_nd_respects_usable_and_link_local():
    scope = _scope(
        interfaces=("et-0/0/8.13",), addresses=(), local_ipv6=("2001:abcd:11:13::a/64",)
    )
    baseline_nd = [
        {
            "ip": "2001:abcd:11:13::b",
            "mac": "0c:00:ef:5e:df:03",
            "interface": "ge-0/0/8.13",
            "state": "unreachable",
        },
        {
            "ip": "fe80::c66b:b8ff:fe48:0",
            "mac": "c4:6b:b8:48:00:00",
            "interface": "ge-0/0/8.13",
            "state": "stale",
        },
        {
            "ip": "2001:abcd:11:13::c",
            "mac": "0c:00:ef:5e:df:01",
            "interface": "ge-0/0/8.13",
            "state": "reachable",
        },
    ]

    targets = resolve_targets([scope], [], baseline_nd=baseline_nd)

    assert [t.target for t in targets] == ["2001:abcd:11:13::c"]
    assert targets[0].resolved_from == "baseline-nd"


def test_baseline_nd_never_uses_link_local_even_when_scope_has_it():
    """Link-local baseline soused se nepouzije, i kdyz scope ma fe80 v local_ipv6.

    Bez explicitniho vyloucni link-local by tenhle zaznam prosel filtrem
    prislusnosti do site (fe80::/64 obsahuje fe80::1/64) a skoncil by jako
    cil - fe80 je ale per-link, na jinem boxu neplati.
    """
    scope = _scope(interfaces=("et-0/0/8.13",), addresses=(), local_ipv6=("fe80::1/64",))
    baseline_nd = [
        {
            "ip": "fe80::c66b:b8ff:fe48:0",
            "mac": "c4:6b:b8:48:00:00",
            "interface": "ge-0/0/8.13",
            "state": "stale",
        }
    ]

    targets = resolve_targets([scope], [], baseline_nd=baseline_nd)

    assert targets == []


def test_no_baseline_keeps_today_behavior():
    scope = _scope()
    arp = [
        {"ip": "198.11.13.2", "interface": "ge-0/0/2.113"},
        {"ip": "198.11.13.3", "interface": "ge-0/0/2.113"},
    ]

    assert resolve_targets([scope], arp) == resolve_targets(
        [scope], arp, baseline_arp=None, baseline_nd=None
    )


def test_bgp_neighbor_wins_over_baseline_and_arp():
    """Nakonfigurovany BGP soused je nejpresnejsi cil - adresa CPE primo z konfigurace."""
    scope = _scope(addresses=("198.11.13.1/29",), bgp_neighbors=("198.11.13.2",))
    arp = [{"ip": "198.11.13.3", "interface": "ge-0/0/2.113"}]
    baseline_arp = [{"ip": "198.11.13.4", "interface": "ge-0/0/0.100"}]

    targets = resolve_targets([scope], arp, baseline_arp=baseline_arp)

    assert [t.target for t in targets] == ["198.11.13.2"]
    assert targets[0].resolved_from == "bgp"


def test_bgp_neighbors_split_by_family():
    scope = _scope(
        addresses=("198.11.13.1/30",),
        local_ipv6=("2001:abcd:11:13::a/127",),
        bgp_neighbors=("198.11.13.2", "2001:abcd:11:13::b"),
    )

    targets = resolve_targets([scope], [])

    assert [(t.target, t.family) for t in targets] == [
        ("198.11.13.2", 4),
        ("2001:abcd:11:13::b", 6),
    ]
    assert all(t.resolved_from == "bgp" for t in targets)


def test_inactive_bgp_neighbor_is_not_a_target():
    """Deaktivovana session je vypnuty zamer - FAIL pingu by lhal o nedostupnem CPE."""
    scope = _scope(bgp_neighbors_inactive=("198.11.13.2",))
    arp = [{"ip": "198.11.13.2", "interface": "ge-0/0/2.113"}]

    targets = resolve_targets([scope], arp)

    assert [t.resolved_from for t in targets] == ["arp"]


def test_bgp_neighbor_guard_drops_own_addresses():
    """Vlastni adresy (source i VGW) nesmi projit ani z BGP selektoru.

    Kdyby filtr nefungoval, vratily by se tri cile - platny soused musi
    projit, aby test nemohl projit s tierem, ktery vraci prazdny seznam.
    """
    scope = _scope(
        interfaces=("irb.14",),
        addresses=("152.11.14.2/29",),
        virtual_gw_v4=("152.11.14.1",),
        bgp_neighbors=("152.11.14.2", "152.11.14.1", "152.11.14.4"),
        routing_instance=None,
    )

    targets = resolve_targets([scope], [])

    assert [t.target for t in targets] == ["152.11.14.4"]
    assert targets[0].resolved_from == "bgp"


def test_link_local_bgp_neighbor_is_skipped():
    """fe80 soused bez rozhrani se pingnout neda - selektor rozhrani nenese.

    Selekce musi propadnout na dalsi tier (tady ND), ne vyrobit cil,
    ktery Junos odmitne.
    """
    scope = _scope(
        interfaces=("et-0/0/8.13",),
        addresses=(),
        local_ipv6=("2001:abcd:11:13::a/64",),
        bgp_neighbors=("fe80::c66b:b8ff:fe48:0",),
    )
    nd = [
        {
            "ip": "2001:abcd:11:13::b",
            "mac": "0c:00:ef:5e:df:01",
            "interface": "et-0/0/8.13",
            "state": "reachable",
        }
    ]

    targets = resolve_targets([scope], [], nd)

    assert [(t.target, t.resolved_from) for t in targets] == [("2001:abcd:11:13::b", "nd")]


def test_scope_without_bgp_neighbors_behaves_as_before():
    scope = _scope()
    arp = [{"ip": "198.11.13.2", "interface": "ge-0/0/2.113"}]

    targets = resolve_targets([scope], arp)

    assert [t.resolved_from for t in targets] == ["arp"]


def test_parse_ping_result():
    xml = etree.fromstring(
        """
        <ping-results>
          <probe-results-summary>
            <probes-sent>5</probes-sent>
            <responses-received>4</responses-received>
            <packet-loss>20</packet-loss>
            <rtt-average>1240</rtt-average>
          </probe-results-summary>
        </ping-results>
        """
    )
    result = parse_ping_result(xml)

    assert result["sent"] == 5
    assert result["received"] == 4
    assert result["loss_percent"] == 20
    assert result["rtt_avg_ms"] == pytest.approx(1.24)


def test_parse_ping_result_records_internal_error():
    """'bind: Can't assign requested address' - ping vubec neodesel.

    Tahle odpoved nema probe-results-summary. Bez zaznamu duvodu by vysledek
    vypadal jako radne merenych nula paketu.
    """
    xml = etree.fromstring(
        """
        <ping-results>
          <ping-failure>internal error</ping-failure>
          <ping-error-message>bind: Can't assign requested address</ping-error-message>
        </ping-results>
        """
    )
    result = parse_ping_result(xml)

    assert result["sent"] == 0
    assert result["received"] == 0
    assert result["loss_percent"] == 100
    assert "bind" in result["error"]


def test_parse_ping_result_no_response_is_a_measurement_not_an_error():
    """'no response' ma platne summary - je to vysledek, ne porucha nastroje."""
    xml = etree.fromstring(
        """
        <ping-results>
          <ping-warning-message>sendto: No route to host</ping-warning-message>
          <probe-results-summary>
            <probes-sent>3</probes-sent>
            <responses-received>0</responses-received>
            <packet-loss>100</packet-loss>
          </probe-results-summary>
          <ping-failure>no response</ping-failure>
        </ping-results>
        """
    )
    result = parse_ping_result(xml)

    assert result["sent"] == 3
    assert result["loss_percent"] == 100
    assert result["error"] == "no response"


def test_parse_ping_result_strips_whitespace_values():
    """Junos obaluje hodnoty novymi radky."""
    xml = etree.fromstring(
        """
        <ping-results>
          <probe-results-summary>
            <probes-sent>
3
</probes-sent>
            <responses-received>
3
</responses-received>
            <packet-loss>
0
</packet-loss>
            <rtt-average>
22302
</rtt-average>
          </probe-results-summary>
          <ping-success/>
        </ping-results>
        """
    )
    result = parse_ping_result(xml)

    assert result == {
        "sent": 3,
        "received": 3,
        "loss_percent": 0,
        "rtt_avg_ms": pytest.approx(22.302),
    }


def test_parse_ping_result_handles_total_loss():
    xml = etree.fromstring(
        """
        <ping-results>
          <probe-results-summary>
            <probes-sent>5</probes-sent>
            <responses-received>0</responses-received>
            <packet-loss>100</packet-loss>
          </probe-results-summary>
        </ping-results>
        """
    )
    result = parse_ping_result(xml)
    assert result["received"] == 0
    assert result["rtt_avg_ms"] is None


class _RpcRecorder:
    def __init__(self):
        self.kwargs = None

    def ping(self, **kwargs):
        self.kwargs = kwargs
        return etree.fromstring(
            "<ping-results><probe-results-summary>"
            "<probes-sent>5</probes-sent><responses-received>5</responses-received>"
            "<packet-loss>0</packet-loss><rtt-average>2100</rtt-average>"
            "</probe-results-summary></ping-results>"
        )


class _RpcDevice:
    def __init__(self):
        self.rpc = _RpcRecorder()


def test_run_ping_never_sets_source():
    """Router voli egress adresu sam - explicitni source u multi-range irb
    miril mimo subnet cile a ping padal (produkce 2026-08)."""
    device = _RpcDevice()
    target = PingTarget("svc:X:IPVPN", "198.11.13.2", "L3VPN-CPE13-NNI", "arp", 4)

    record = run_ping(device, target)

    assert "source" not in device.rpc.kwargs
    assert device.rpc.kwargs["host"] == "198.11.13.2"
    assert device.rpc.kwargs["routing_instance"] == "L3VPN-CPE13-NNI"
    assert "source" not in record
