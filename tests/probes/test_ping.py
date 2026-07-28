import pytest
from lxml import etree

from migration_validator.models.scope import Scope, ScopeKey, Selectors
from migration_validator.probes.ping import (
    parse_ping_result,
    resolve_targets,
    source_address,
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
        ),
    )


def test_source_is_interface_address():
    assert source_address(_scope(), 4) == "198.11.13.1"


def test_source_prefers_virtual_gw_on_irb():
    scope = _scope(
        interfaces=("irb.14",),
        addresses=("152.11.14.2/29",),
        virtual_gw_v4=("152.11.14.1",),
    )
    assert source_address(scope, 4) == "152.11.14.1"


def test_source_none_when_no_address():
    assert source_address(_scope(addresses=()), 4) is None


def test_source_follows_target_family():
    scope = _scope(
        addresses=("152.11.13.1/30",),
        local_ipv6=("2001:abcd:11:13::a/127",),
    )

    assert source_address(scope, 4) == "152.11.13.1"
    assert source_address(scope, 6) == "2001:abcd:11:13::a"


def test_virtual_gateway_wins_over_interface_address():
    scope = _scope(
        addresses=("152.11.14.2/29",),
        virtual_gw_v4=("152.11.14.1",),
    )

    assert source_address(scope, 4) == "152.11.14.1"


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


def test_targets_come_from_arp():
    arp = [
        {"ip": "198.11.13.2", "interface": "ge-0/0/2.113"},
        {"ip": "198.11.13.3", "interface": "ge-0/0/2.113"},
        {"ip": "10.9.9.9", "interface": "ge-0/0/9.0"},
    ]
    targets = resolve_targets([_scope()], arp)

    assert [target.target for target in targets] == ["198.11.13.2", "198.11.13.3"]
    assert all(target.resolved_from == "arp" for target in targets)
    assert all(target.source == "198.11.13.1" for target in targets)
    assert all(target.routing_instance == "L3VPN-CPE13-NNI" for target in targets)


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


def test_incomplete_nd_entry_is_not_a_target():
    """Zaznam bez MAC neni cil - strilet na nej nema smysl."""
    scope = _scope(interfaces=("et-0/0/8.13",), addresses=(), local_ipv6=("2001:db8::2/64",))
    nd = [
        {
            "ip": "2001:db8::1",
            "mac": "none",
            "interface": "et-0/0/8.13",
            "state": "unreachable",
        }
    ]

    assert resolve_targets([scope], [], nd) == []


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
