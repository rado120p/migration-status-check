import pytest
from lxml import etree

from migration_validator.collectors.base import Collector, CollectorError
from migration_validator.collectors.registry import (
    all_collectors,
    collectors_for,
    register,
)


class FakeRpcMeta:
    def __init__(self, responses):
        self._responses = responses

    def __getattr__(self, name):
        if name not in self._responses:
            raise AttributeError(name)

        def call(**kwargs):
            return self._responses[name]

        return call


class FakeDevice:
    def __init__(self, responses):
        self.rpc = FakeRpcMeta(responses)


class DemoCollector(Collector):
    name = "demo"

    def rpc_name(self, platform):
        return "get_demo_information"

    def parse(self, xml, platform):
        return {node.get("id"): node.text for node in xml.findall("item")}


def test_collect_runs_rpc_and_parses():
    xml = etree.fromstring('<demo><item id="a">1</item><item id="b">2</item></demo>')
    device = FakeDevice({"get_demo_information": xml})

    assert DemoCollector().collect(device, "junos") == {"a": "1", "b": "2"}


def test_unsupported_platform_raises():
    class EvoOnly(DemoCollector):
        name = "evo_only"
        platforms = ("junos-evo",)

    collector = EvoOnly()
    assert collector.supports("junos") is False

    with pytest.raises(CollectorError, match="junos"):
        collector.collect(FakeDevice({}), "junos")


def test_missing_rpc_raises_collector_error():
    with pytest.raises(CollectorError, match="get_demo_information"):
        DemoCollector().collect(FakeDevice({}), "junos")


def test_registry_filters_by_platform():
    @register
    class OnlyEvo(DemoCollector):
        name = "only_evo"
        platforms = ("junos-evo",)

    @register
    class Both(DemoCollector):
        name = "both_platforms"

    names = {collector.name for collector in collectors_for("junos")}
    assert "both_platforms" in names
    assert "only_evo" not in names

    assert {collector.name for collector in all_collectors()} >= {
        "only_evo",
        "both_platforms",
    }


def test_duplicate_registration_is_rejected():
    @register
    class Unique(DemoCollector):
        name = "unique_collector"

    with pytest.raises(ValueError, match="unique_collector"):

        @register
        class Duplicate(DemoCollector):
            name = "unique_collector"
