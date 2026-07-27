import pytest
from lxml import etree

from migration_validator.collectors.bgp import BgpCollector, strip_port

PLATFORMS = ("junos", "junos-evo")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("198.11.13.2+179", "198.11.13.2"),
        ("198.11.13.2", "198.11.13.2"),
        ("2001:db8:11:13::b+51234", "2001:db8:11:13::b"),
        ("  10.0.0.1  ", "10.0.0.1"),
    ],
)
def test_strip_port(raw, expected):
    assert strip_port(raw) == expected


@pytest.mark.parametrize("platform", PLATFORMS)
def test_parses_peers(rpc_fixture, platform):
    result = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    assert result, "parser nenasel zadneho peera"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_peer_schema(rpc_fixture, platform):
    result = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    for peer, data in result.items():
        assert "+" not in peer, f"adresa peera nese port: {peer}"
        assert set(data) == {"state", "peer_as", "routing_instance", "prefixes"}
        assert set(data["prefixes"]) == {"received", "accepted", "advertised"}
        assert all(isinstance(value, int) for value in data["prefixes"].values())


@pytest.mark.parametrize("platform", PLATFORMS)
def test_established_peers_are_reported(rpc_fixture, platform):
    """Laborka ma vsechny session nahore - stav se musi propsat doslova."""
    result = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    assert any(data["state"] == "Established" for data in result.values())


def test_prefix_counts_are_summed_across_ribs():
    xml = etree.fromstring(
        """
        <bgp-information>
          <bgp-peer>
            <peer-address>10.0.0.1+179</peer-address>
            <peer-state>Established</peer-state>
            <peer-as>65001</peer-as>
            <peer-cfg-rti>L3VPN-A</peer-cfg-rti>
            <bgp-rib>
              <received-prefix-count>10</received-prefix-count>
              <accepted-prefix-count>9</accepted-prefix-count>
              <advertised-prefix-count>2</advertised-prefix-count>
            </bgp-rib>
            <bgp-rib>
              <received-prefix-count>4</received-prefix-count>
              <accepted-prefix-count>4</accepted-prefix-count>
              <advertised-prefix-count>1</advertised-prefix-count>
            </bgp-rib>
          </bgp-peer>
        </bgp-information>
        """
    )

    result = BgpCollector().parse(xml, "junos")

    assert result["10.0.0.1"]["prefixes"] == {
        "received": 14,
        "accepted": 13,
        "advertised": 3,
    }
    assert result["10.0.0.1"]["state"] == "Established"
    assert result["10.0.0.1"]["peer_as"] == 65001
    assert result["10.0.0.1"]["routing_instance"] == "L3VPN-A"


def test_master_instance_is_normalised_to_none():
    """peer-cfg-rti 'master' neni zakaznicka VRF - check ji nesmi videt jako instanci."""
    xml = etree.fromstring(
        """
        <bgp-information>
          <bgp-peer>
            <peer-address>10.0.0.3+179</peer-address>
            <peer-state>Established</peer-state>
            <peer-cfg-rti>master</peer-cfg-rti>
          </bgp-peer>
        </bgp-information>
        """
    )
    assert BgpCollector().parse(xml, "junos")["10.0.0.3"]["routing_instance"] is None


def test_missing_routing_instance_becomes_none():
    xml = etree.fromstring(
        """
        <bgp-information>
          <bgp-peer>
            <peer-address>10.0.0.2</peer-address>
            <peer-state>Active</peer-state>
          </bgp-peer>
        </bgp-information>
        """
    )
    result = BgpCollector().parse(xml, "junos")
    assert result["10.0.0.2"]["routing_instance"] is None
    assert result["10.0.0.2"]["prefixes"] == {
        "received": 0,
        "accepted": 0,
        "advertised": 0,
    }


def test_collector_metadata():
    assert BgpCollector().name == "bgp"
