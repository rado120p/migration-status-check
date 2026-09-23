"""Spolecne tvary raw zaznamu (spec 2026-09-23)."""

from lxml import etree

from migration_validator.raw.calls import (
    NOT_RECORDED,
    NotRecorded,
    RecordedCall,
    call_key,
    canonical_kwargs,
    config_filter,
)


def _filter(*names):
    root = etree.Element("configuration")
    for name in names:
        etree.SubElement(root, name)
    return root


def test_canonical_kwargs_drops_config_filter_element():
    """Filtr get_config nese config_filter(); v kwargs by jako serializovany
    element rozbil parovani, jakmile se zmeni poradi hierarchii.
    Zabiji mutanta: canonical_kwargs necha filter_xml v kwargs."""
    kwargs = {
        "filter_xml": _filter("interfaces"),
        "options": {"database": "committed", "inherit": ""},
    }
    assert canonical_kwargs("get_config", kwargs) == {
        "options": {"database": "committed", "inherit": ""}
    }


def test_canonical_kwargs_serializes_other_elements():
    element = etree.fromstring("<x><y/></x>")
    assert canonical_kwargs("some_rpc", {"filter_xml": element}) == {
        "filter_xml": "<x><y/></x>"
    }


def test_config_filter_lists_top_level_hierarchies_in_order():
    kwargs = {"filter_xml": _filter("interfaces", "policy-options")}
    assert config_filter("get_config", kwargs) == ["interfaces", "policy-options"]
    assert config_filter("get_interface_information", {"terse": True}) is None
    assert config_filter("get_config", {}) is None


def test_call_key_ignores_kwarg_order():
    a = call_key("ping", {"host": "10.0.0.1", "rapid": True, "count": "5"})
    b = call_key("ping", {"count": "5", "host": "10.0.0.1", "rapid": True})
    assert a == b
    assert a != call_key("ping", {"host": "10.0.0.2", "rapid": True, "count": "5"})


def test_recorded_call_reply_parses_fresh_element_each_time():
    """Collector smi odpoved upravovat - druhe podani musi byt neporusene."""
    call = RecordedCall(seq=1, rpc="x", kwargs={}, reply_xml=b"<a><b>1</b></a>")
    first = call.reply()
    first.remove(first[0])
    assert call.reply()[0].text == "1"


def test_recorded_call_non_xml_reply_is_value():
    assert RecordedCall(seq=1, rpc="x", kwargs={}, reply_value=True).reply() is True


def test_not_recorded_carries_message():
    assert str(NotRecorded(f"{NOT_RECORDED}: ping")) == "neni v raw zaznamu: ping"
