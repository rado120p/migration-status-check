"""Platformni get-config filtr.

MX (klasicky Junos) nema top-level hierarchii vlans, NETCONF server
takovy filtr odmitne s RpcError bad_element. Kazdy parser proto
deklaruje hierarchie, ktere jeho platforma umi obslouzit.
"""

from lxml import etree

from migration_validator.parsers import (
    JunosEvoAcxServiceParser,
    JunosServiceParser,
)
from migration_validator.parsers.core import retrieve_configuration


def test_mx_hierarchie_bez_vlans():
    hierarchies = JunosServiceParser.CONFIG_HIERARCHIES
    assert "vlans" not in hierarchies
    # switch-options je na MX platny top-level blok (overeno v provozu).
    assert "switch-options" in hierarchies
    assert "bridge-domains" in hierarchies


def test_evo_hierarchie_obsahuji_vlans_i_bridge_domains():
    hierarchies = JunosEvoAcxServiceParser.CONFIG_HIERARCHIES
    assert "vlans" in hierarchies
    assert "bridge-domains" in hierarchies


class _FakeRpc:
    def __init__(self):
        self.filter_xml = None

    def get_config(self, filter_xml, options):
        self.filter_xml = filter_xml
        return etree.XML(
            "<rpc-reply><data><configuration/></data></rpc-reply>"
        )


class _FakeDevice:
    def __init__(self):
        self.rpc = _FakeRpc()


def test_retrieve_configuration_sklada_filtr_z_hierarchii():
    device = _FakeDevice()

    retrieve_configuration(
        device,
        hierarchies=("interfaces", "bridge-domains"),
    )

    children = [
        child.tag for child in device.rpc.filter_xml
    ]
    assert children == ["interfaces", "bridge-domains"]
