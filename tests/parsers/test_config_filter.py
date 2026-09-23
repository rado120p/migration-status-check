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
    assert children == [
        "interfaces", "bridge-domains", "policy-options", "firewall", "class-of-service",
    ]


def test_retrieve_configuration_nezdvojuje_hierarchii_z_extra():
    device = _FakeDevice()

    retrieve_configuration(device, hierarchies=("interfaces", "firewall"))

    children = [child.tag for child in device.rpc.filter_xml]
    assert children == ["interfaces", "firewall", "policy-options", "class-of-service"]


COS_CONFIG = (
    "<rpc-reply><data><configuration>"
    "<interfaces><interface><name>ge-0/0/0</name></interface></interfaces>"
    "<firewall><family><inet><filter><name>F</name></filter></inet></family></firewall>"
    "<class-of-service><interfaces><interface><name>ge-0/0/9</name></interface>"
    "</interfaces></class-of-service>"
    "</configuration></data></rpc-reply>"
)


class _CosRpc:
    def get_config(self, filter_xml, options):
        return etree.XML(COS_CONFIG)


class _CosDevice:
    def __init__(self):
        self.facts = {"hostname": "MX1", "model": "MX204", "version": "21.4R3"}
        self.hostname = "172.20.20.4"
        self.rpc = _CosRpc()


def test_parser_dostane_jen_sve_hierarchie():
    """class-of-service interfaces interface ma stejna jmena elementu jako
    top-level interfaces - parser ji nesmi videt. Zabiji mutanta: orez
    v retrieve_configuration vynechan."""
    root = retrieve_configuration(_CosDevice(), hierarchies=("interfaces",))
    assert [child.tag for child in root] == ["interfaces"]


def test_nahravka_nese_extra_hierarchie_i_po_orezu():
    from migration_validator.raw.recorder import RecordingDevice, SessionRecording

    recording = SessionRecording()
    retrieve_configuration(RecordingDevice(_CosDevice(), recording), hierarchies=("interfaces",))

    assert b"class-of-service" in recording.calls[0].reply_xml
    assert b"<firewall>" in recording.calls[0].reply_xml
    assert recording.calls[0].filter == [
        "interfaces", "policy-options", "firewall", "class-of-service",
    ]
