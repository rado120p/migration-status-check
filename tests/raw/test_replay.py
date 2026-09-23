"""ReplayDevice - prehravani nahrane session (spec 2026-09-23, sekce 2)."""

import pytest
from lxml import etree

from migration_validator.connection.junos import detect_platform
from migration_validator.parsers import parser_hierarchies
from migration_validator.raw.bundle import Session
from migration_validator.raw.calls import NotRecorded, RecordedCall
from migration_validator.raw.replay import RecordedRpcError, ReplayDevice


def _session(calls):
    return Session(
        kind="capture", address="172.20.20.4", hostname="172.20.20.4",
        facts={"hostname": "MX1", "model": "MX204", "version": "21.4R3"},
        calls=calls,
    )


def _xml(seq, rpc, kwargs, body, filter=None):
    return RecordedCall(seq=seq, rpc=rpc, kwargs=kwargs, filter=filter, reply_xml=body.encode())


def test_serves_recorded_reply_by_rpc_and_kwargs():
    device = ReplayDevice(_session([
        _xml(1, "get_interface_information", {"terse": True}, "<t/>"),
        _xml(2, "get_interface_information", {"extensive": True}, "<e/>"),
    ]))
    assert device.rpc.get_interface_information(extensive=True).tag == "e"
    assert device.rpc.get_interface_information(terse=True).tag == "t"


def test_repeated_call_is_served_in_order_and_last_repeats():
    rpc = ReplayDevice(_session([
        _xml(1, "get_instance_information", {"brief": True}, "<a/>"),
        _xml(2, "get_instance_information", {"brief": True}, "<b/>"),
    ])).rpc
    assert [rpc.get_instance_information(brief=True).tag for _ in range(3)] == ["a", "b", "b"]


def test_unrecorded_call_raises_not_recorded():
    with pytest.raises(NotRecorded, match="neni v raw zaznamu: get_bgp_neighbor_information"):
        ReplayDevice(_session([])).rpc.get_bgp_neighbor_information()


def test_changed_kwargs_are_not_recorded():
    device = ReplayDevice(_session([
        _xml(1, "ping", {"count": "5", "host": "10.0.0.1", "rapid": True}, "<ping-results/>"),
    ]))
    assert device.rpc.ping(host="10.0.0.1", count="5", rapid=True).tag == "ping-results"
    with pytest.raises(NotRecorded):
        device.rpc.ping(host="10.0.0.2", count="5", rapid=True)


def test_recorded_error_keeps_class_name_and_message():
    """Text statusu collectoru ("RpcError: timeout") musi po replayi vyjit
    stejne jako pri zivem capture."""
    call = RecordedCall(
        seq=1, rpc="get_bfd_session_information", kwargs={},
        error={"type": "RpcError", "message": "timeout"},
    )
    with pytest.raises(RecordedRpcError) as info:
        ReplayDevice(_session([call])).rpc.get_bfd_session_information()
    assert type(info.value).__name__ == "RpcError"
    assert str(info.value) == "timeout"


def test_facts_and_hostname_come_from_session():
    device = ReplayDevice(_session([]))
    assert device.facts["model"] == "MX204"
    assert device.hostname == "172.20.20.4"
    assert detect_platform(device) == "junos"


CONFIG = (
    "<rpc-reply><data><configuration>"
    "<interfaces><interface><name>ge-0/0/0</name></interface></interfaces>"
    "<class-of-service><interfaces><interface><name>ge-0/0/9</name></interface>"
    "</interfaces></class-of-service>"
    "</configuration></data></rpc-reply>"
)
RECORDED_FILTER = ["interfaces", "routing-options", "firewall", "class-of-service"]


def _filter(*names):
    root = etree.Element("configuration")
    for name in names:
        etree.SubElement(root, name)
    return root


def _config_device(required):
    return ReplayDevice(
        _session([_xml(1, "get_config", {"options": {}}, CONFIG, filter=RECORDED_FILTER)]),
        required_config=frozenset(required),
    )


def _children(reply):
    return [child.tag for child in reply.find(".//configuration")]


def test_get_config_is_cut_to_requested_hierarchies():
    device = _config_device({"interfaces", "routing-options"})
    reply = device.rpc.get_config(filter_xml=_filter("interfaces"), options={})
    assert _children(reply) == ["interfaces"]


def test_requested_but_empty_hierarchy_is_not_an_error():
    """Junos prazdnou hierarchii v odpovedi vynecha: routing-options byla
    ve filtru zadana, jen v konfiguraci nic nema. Zabiji mutanta, ktery
    chybejici hierarchii hleda v odpovedi misto v nahranem filtru."""
    device = _config_device({"interfaces", "routing-options"})
    reply = device.rpc.get_config(
        filter_xml=_filter("interfaces", "routing-options"), options={}
    )
    assert _children(reply) == ["interfaces"]


def test_hierarchy_outside_recorded_filter_is_not_recorded():
    device = _config_device({"interfaces", "chassis"})
    with pytest.raises(NotRecorded, match="konfigurace nema hierarchii chassis"):
        device.rpc.get_config(filter_xml=_filter("interfaces", "chassis"), options={})


def test_optional_hierarchy_outside_filter_is_tolerated():
    """Hierarchie, kterou zadny parser nepotrebuje (budouci RAW_EXTRA),
    starsi capture neblokuje."""
    device = _config_device({"interfaces"})
    reply = device.rpc.get_config(filter_xml=_filter("interfaces", "services"), options={})
    assert _children(reply) == ["interfaces"]


def test_default_required_config_is_union_of_parser_hierarchies():
    hierarchies = parser_hierarchies()
    assert {"interfaces", "routing-instances", "protocols", "bridge-domains", "vlans"} <= hierarchies
    assert "policy-options" not in hierarchies


def test_private_attribute_is_not_an_rpc():
    with pytest.raises(AttributeError):
        ReplayDevice(_session([])).rpc._private
