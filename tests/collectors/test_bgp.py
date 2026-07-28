import pytest
from lxml import etree

from migration_validator.collectors.bgp import BgpCollector, strip_port

PLATFORMS = ("junos", "junos-evo")
RIB_KEYS = {"received", "accepted", "advertised", "active", "suppressed"}


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
        assert set(data) == {"state", "peer_as", "routing_instance", "ribs"}
        for rib_counts in data["ribs"].values():
            assert set(rib_counts) == RIB_KEYS
            assert all(isinstance(value, int) for value in rib_counts.values())


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ribs_are_kept_apart(rpc_fixture, platform):
    peers = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    assert peers, "fixture nema zadneho peera"

    for peer, data in peers.items():
        assert "prefixes" not in data, f"{peer}: souctove pole prezilo"
        # Idle session nevymenila zadne trasy, takze RPC odpoved pro ni
        # neobsahuje zadnou bgp-rib - prazdne ribs tam jsou spravne.
        if data["state"] == "Established":
            assert data["ribs"], f"{peer}: zadna RIB"
        for rib_name, counts in data["ribs"].items():
            assert rib_name
            assert set(counts) == RIB_KEYS


@pytest.mark.parametrize("platform", PLATFORMS)
def test_known_rib_name_is_present(rpc_fixture, platform):
    peers = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    names = {rib for data in peers.values() for rib in data["ribs"]}

    assert "inet.0" in names


@pytest.mark.parametrize("platform", PLATFORMS)
def test_established_peers_are_reported(rpc_fixture, platform):
    """Laborka ma vsechny session nahore - stav se musi propsat doslova."""
    result = BgpCollector().parse(rpc_fixture(platform, "bgp"), platform)
    assert any(data["state"] == "Established" for data in result.values())


def test_ribs_are_not_summed_together():
    """Kdyby se countery zase scitaly pres RIB, inet.0 a inet6.0 by se slily
    do jednoho cisla a tenhle test by na rozdilne hodnoty nedokazal narazit."""
    xml = etree.fromstring(
        """
        <bgp-information>
          <bgp-peer>
            <peer-address>10.0.0.1+179</peer-address>
            <peer-state>Established</peer-state>
            <peer-as>65001</peer-as>
            <peer-cfg-rti>L3VPN-A</peer-cfg-rti>
            <bgp-rib>
              <name>inet.0</name>
              <received-prefix-count>10</received-prefix-count>
              <accepted-prefix-count>9</accepted-prefix-count>
              <advertised-prefix-count>2</advertised-prefix-count>
              <active-prefix-count>9</active-prefix-count>
              <suppressed-prefix-count>0</suppressed-prefix-count>
            </bgp-rib>
            <bgp-rib>
              <name>inet6.0</name>
              <received-prefix-count>4</received-prefix-count>
              <accepted-prefix-count>4</accepted-prefix-count>
              <advertised-prefix-count>1</advertised-prefix-count>
              <active-prefix-count>4</active-prefix-count>
              <suppressed-prefix-count>1</suppressed-prefix-count>
            </bgp-rib>
          </bgp-peer>
        </bgp-information>
        """
    )

    result = BgpCollector().parse(xml, "junos")

    assert result["10.0.0.1"]["ribs"] == {
        "inet.0": {
            "received": 10,
            "accepted": 9,
            "advertised": 2,
            "active": 9,
            "suppressed": 0,
        },
        "inet6.0": {
            "received": 4,
            "accepted": 4,
            "advertised": 1,
            "active": 4,
            "suppressed": 1,
        },
    }
    assert result["10.0.0.1"]["state"] == "Established"
    assert result["10.0.0.1"]["peer_as"] == 65001
    assert result["10.0.0.1"]["routing_instance"] == "L3VPN-A"


def test_rib_without_name_is_skipped():
    """bgp-rib bez <name> se nezapocitava - bez ni nejde radek priradit
    ke sprave rodine ani ke spravne RIB pri porovnani s baseline."""
    xml = etree.fromstring(
        """
        <bgp-information>
          <bgp-peer>
            <peer-address>10.0.0.4+179</peer-address>
            <peer-state>Established</peer-state>
            <bgp-rib>
              <received-prefix-count>10</received-prefix-count>
            </bgp-rib>
          </bgp-peer>
        </bgp-information>
        """
    )
    result = BgpCollector().parse(xml, "junos")
    assert result["10.0.0.4"]["ribs"] == {}


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
    assert result["10.0.0.2"]["ribs"] == {}


def test_collector_metadata():
    assert BgpCollector().name == "bgp"
