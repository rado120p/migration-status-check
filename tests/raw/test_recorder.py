"""RecordingDevice - nahravani na hranici device.rpc (spec 2026-09-23)."""

import pytest
from lxml import etree

from migration_validator.raw.recorder import RecordingDevice, SessionRecording


class _Rpc:
    def get_interface_information(self, **kwargs):
        return etree.fromstring(
            "<interface-information><x>1</x></interface-information>"
        )

    def get_bgp_neighbor_information(self, **kwargs):
        raise RuntimeError("RPC selhalo")

    def get_config(self, **kwargs):
        return etree.fromstring(
            "<rpc-reply><configuration><interfaces/><firewall/></configuration></rpc-reply>"
        )

    def commit_check(self, **kwargs):
        return True


class _Device:
    def __init__(self):
        self.facts = {
            "hostname": "MX1", "model": "MX204", "version": "21.4R3",
            "serialnumber": "S1",
        }
        self.hostname = "172.20.20.4"
        self.rpc = _Rpc()


def _recorded():
    recording = SessionRecording()
    return RecordingDevice(_Device(), recording), recording


def test_records_calls_in_order_with_replies():
    device, recording = _recorded()
    reply = device.rpc.get_interface_information(terse=True)
    device.rpc.get_interface_information(extensive=True)

    assert reply.find("x").text == "1"
    assert [(c.seq, c.rpc, c.kwargs) for c in recording.calls] == [
        (1, "get_interface_information", {"terse": True}),
        (2, "get_interface_information", {"extensive": True}),
    ]
    assert b"<x>1</x>" in recording.calls[0].reply_xml


def test_records_error_and_reraises():
    device, recording = _recorded()
    with pytest.raises(RuntimeError, match="RPC selhalo"):
        device.rpc.get_bgp_neighbor_information()
    assert recording.calls[0].error == {"type": "RuntimeError", "message": "RPC selhalo"}
    assert recording.calls[0].reply_xml is None


def test_reply_is_serialized_before_caller_mutates_it():
    """retrieve_configuration odpoved orezava na miste - nahravka uz musi
    byt serializovana. Zabiji mutanta: recorder drzi referenci na element
    a serializuje az pri zapisu."""
    device, recording = _recorded()
    filter_xml = etree.Element("configuration")
    etree.SubElement(filter_xml, "interfaces")
    etree.SubElement(filter_xml, "firewall")

    reply = device.rpc.get_config(filter_xml=filter_xml, options={})
    root = reply.find(".//configuration")
    root.remove(root.find("firewall"))

    assert b"<firewall/>" in recording.calls[0].reply_xml
    assert recording.calls[0].filter == ["interfaces", "firewall"]
    assert recording.calls[0].kwargs == {"options": {}}


def test_non_xml_reply_is_recorded_as_value():
    device, recording = _recorded()
    assert device.rpc.commit_check() is True
    assert recording.calls[0].reply_value is True
    assert recording.calls[0].reply_xml is None


def test_facts_and_hostname_are_recorded_and_passed_through():
    device, recording = _recorded()
    assert recording.facts == {"hostname": "MX1", "model": "MX204", "version": "21.4R3"}
    assert recording.hostname == "172.20.20.4"
    assert device.facts["serialnumber"] == "S1"
    assert device.hostname == "172.20.20.4"


def test_private_attribute_is_not_an_rpc():
    device, _ = _recorded()
    with pytest.raises(AttributeError):
        device.rpc._private
