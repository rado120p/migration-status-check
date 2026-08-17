"""Realna RPC odpoved nese junos namespace - parser ho musi zahladit.

Testovaci fixtures jinde jsou namespace-free, takze tohle je jedine
misto, ktere cvici strip_namespaces() na vstupu s xmlns. Bez stripu by
plain xpathy (./interfaces/interface) nenasly nic a parser by vratil
prazdny inventar.
"""

from lxml import etree

from migration_validator.parsers import JunosServiceParser
from migration_validator.parsers.core import find_configuration_root

NAMESPACED_RPC = """
<rpc-reply xmlns:junos="http://xml.juniper.net/junos/23.4R0/junos">
  <data>
    <configuration xmlns="http://xml.juniper.net/xnm/1.1/xnm"
                   junos:commit-seconds="1"
                   junos:commit-localtime="x"
                   junos:commit-user="ansible">
      <interfaces>
        <interface>
          <name>ge-0/0/0</name>
          <unit>
            <name>100</name>
            <description>ZAKAZNIK-INET</description>
            <family>
              <inet>
                <address>
                  <name>192.0.2.1/30</name>
                </address>
              </inet>
            </family>
          </unit>
        </interface>
      </interfaces>
    </configuration>
  </data>
</rpc-reply>
"""


def test_namespaced_config_parses_services():
    root = find_configuration_root(etree.fromstring(NAMESPACED_RPC))
    services = JunosServiceParser(root).parse()

    by_name = {service.interface: service for service in services}
    assert "ge-0/0/0.100" in by_name

    service = by_name["ge-0/0/0.100"]
    assert service.service_type == "Internet"
    assert service.ipv4_address == ["192.0.2.1/30"]


def test_inactive_attribute_survives_namespace_strip():
    xml = """
    <configuration xmlns="http://xml.juniper.net/xnm/1.1/xnm">
      <interfaces>
        <interface inactive="inactive">
          <name>ge-0/0/1</name>
          <unit>
            <name>200</name>
            <family><inet/></family>
          </unit>
        </interface>
      </interfaces>
    </configuration>
    """
    services = JunosServiceParser(etree.fromstring(xml)).parse()

    by_name = {service.interface: service for service in services}
    assert by_name["ge-0/0/1.200"].interface_active is False
