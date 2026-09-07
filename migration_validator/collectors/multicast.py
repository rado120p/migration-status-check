"""Sber multicast stavu: IGMP skupiny, multicast routy, MVPN instance
(spec 2026-09-02). Tri collectory v jednom modulu - sdileji helpery.

Zadny z nich neinterpretuje: `local` u IGMP se zahazuje jen proto, ze to
neni rozhrani (nikdy nemuze byt sluzbou), ne kvuli verdiktu. Absence
rozhrani/instance ve vypisu = absence klice.

Replies na obou platformach nenesou zadny XML namespace (zmereno Task 1,
2026-09-02) - `{*}` wildcard iterace je proto jen konzistence se
sousednimi collectory (isis.py), ne obrana proti pozorovanemu junos-routing
prefixu.
"""

from __future__ import annotations

import re
from typing import Any

from lxml import etree

from migration_validator.collectors.base import Collector, CollectorError
from migration_validator.collectors.isis import _localname_text, _seconds_attr
from migration_validator.collectors.registry import register

ASM_SOURCE = "0.0.0.0"
MASTER = "master"

_SENDER_PE = re.compile(r"P2MP:(\d+\.\d+\.\d+\.\d+)")


def route_key(source: str, group: str) -> str:
    """Klic multicast routy - retezec, aby snapshot zustal JSON-safe."""
    return f"{source},{group}"


def parse_sender_pe(tunnel_id: str | None) -> str | None:
    """Sender PE z provider-tunnel-id ('RSVP-TE P2MP:150.0.0.13, 24209,150.0.0.13').

    Porovnava se jen PE adresa - tunnel id uprostred se pri re-signalizaci
    LSP zmeni a neni to zmena sluzby (rozhodnuti 2026-09-02).
    """
    if not tunnel_id or "invalid" in tunnel_id:
        return None
    match = _SENDER_PE.search(tunnel_id)
    return match.group(1) if match else None


def _localname_texts(node: etree._Element, name: str) -> list[str]:
    return [
        text
        for child in node.iter(f"{{*}}{name}")
        if (text := (child.text or "").strip())
    ]


def _int(text: str | None) -> int | None:
    """R2: chybejici element (EVO - multicast-statistics-timed-out) je None,
    nikdy vymyslena nula. Pritomny, ale nenumericky text zustava 0 - to je
    poskozena odpoved, ne chybejici mereni, a takova vetev na zivych
    zarizenich nenastava."""
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return 0


def _vrf_instances(device: Any) -> list[str]:
    """Jmena routing-instances typu vrf z get-instance-information(brief=True):
    instance-information > instance-core > instance-name / instance-type.

    MX nezna `get_multicast_route_information(instance="all")` (zmereno
    2026-09-02 - vraci <output>instance is not running</output>), takze se
    seznam VRF bere ze zarizeni misto z inventory, ktera jmena RI nenese."""
    reply = device.rpc.get_instance_information(brief=True)
    names = []
    for core in reply.iter("{*}instance-core"):
        if (_localname_text(core, "instance-type") or "").lower() == "vrf":
            name = _localname_text(core, "instance-name")
            if name:
                names.append(name)
    return names


class _PerInstanceCollector(Collector):
    """Zaklad pro RPC, ktere MX neumi zavolat pres vsechny instance najednou
    (`instance all` vraci <output>instance is not running</output>, zmereno
    2026-09-02): EVO jedno volani, MX master bez argumentu + jedno volani
    per VRF z get-instance-information. Podtrida dava rpc_name/rpc_kwargs
    a parse(xml) -> {instance: {klic: payload}}."""

    def record_calls(self, device: Any, platform: str) -> tuple[tuple[str, dict[str, Any]], ...]:
        """Na MX se seznam instanci bere ze zarizeni (get-instance-information,
        instance-type vrf) - collector inventory nema a jmena RI hardcodovat nesmi."""
        calls = list(self.rpc_calls(platform))
        if platform == "junos-evo":
            return tuple(calls)
        rpc_name = self.rpc_name(platform)
        base = dict(self.rpc_kwargs(platform))
        for name in _vrf_instances(device):
            calls.append((rpc_name, {**base, "instance": name}))
        return tuple(calls)

    def collect(self, device: Any, platform: str) -> Any:
        if not self.supports(platform):
            raise CollectorError(f"collector '{self.name}' nepodporuje platformu '{platform}'")
        tables: dict[str, dict[str, dict[str, Any]]] = {}
        failures: list[str] = []
        try:
            # record_calls() na MX vola get-instance-information (_vrf_instances)
            # - selhani tohoto volani je stejna chyba jako selhani samotne RPC,
            # ne neosetrena vyjimka co spadne mimo capture.
            calls = self.record_calls(device, platform)
        except Exception as error:  # noqa: BLE001
            raise CollectorError(
                f"collector '{self.name}': zjisteni seznamu instanci selhalo - "
                f"{type(error).__name__}: {error}"
            ) from error
        for rpc_name, kwargs in calls:
            variant = f"{rpc_name}({kwargs})"
            try:
                xml = getattr(device.rpc, rpc_name)(**kwargs)
                parsed = self.parse(xml, platform)
            except Exception as error:  # noqa: BLE001
                failures.append(f"{variant}: {type(error).__name__}: {error}")
                continue
            for instance, entries in parsed.items():
                tables.setdefault(instance, {}).update(entries)
        if failures:
            # Castecna data nesmi vypadat jako zmerena (stejne jako routes.py).
            raise CollectorError(f"collector '{self.name}': RPC selhalo - " + "; ".join(failures))
        return tables


@register
class IgmpGroupCollector(Collector):
    name = "igmp_group"

    def rpc_name(self, platform: str) -> str:
        return "get_igmp_group_information"

    def parse(self, xml: etree._Element, platform: str) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for iface_node in xml.iter("{*}mgm-interface-groups"):
            interface = _localname_text(iface_node, "interface-name")
            if not interface or interface == "local":
                continue
            entries = []
            for group_node in iface_node.iter("{*}mgm-group"):
                group = _localname_text(group_node, "multicast-group-address")
                source = _localname_text(group_node, "multicast-source-address")
                if not group:
                    continue
                entries.append({
                    "source": None if source in (None, ASM_SOURCE) else source,
                    "group": group,
                })
            # Rozhrani s nulou skupin nema klic - absence je absence.
            if entries:
                groups[interface] = entries
        return groups


@register
class MulticastRouteCollector(_PerInstanceCollector):
    name = "multicast_route"

    def rpc_name(self, platform: str) -> str:
        return "get_multicast_route_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        # EVO: jedno volani pres vsechny instance. MX `instance all` nezna
        # (vraci <output>instance is not running</output>, zmereno 2026-09-02):
        # master bez argumentu + per-VRF volani z record_calls/collect.
        if platform == "junos-evo":
            return {"extensive": True, "instance": "all"}
        return {"extensive": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, dict[str, Any]]]:
        tables: dict[str, dict[str, dict[str, Any]]] = {}
        for family_node in xml.iter("{*}route-family"):
            if (_localname_text(family_node, "address-family") or "").upper() != "INET":
                continue
            instance = _localname_text(family_node, "multicast-instance") or MASTER
            routes: dict[str, dict[str, Any]] = {}
            for route_node in family_node.iter("{*}multicast-route"):
                group = _localname_text(route_node, "multicast-group-address")
                source = _localname_text(route_node, "multicast-source-address")
                if not group or not source:
                    continue
                downstream = [
                    name
                    for names_node in route_node.iter("{*}downstream-interface-names")
                    for name in _localname_texts(names_node, "interface-name")
                ]
                uptime = next(route_node.iter("{*}multicast-route-uptime"), None)
                routes[route_key(source, group)] = {
                    "upstream_interface": _localname_text(route_node, "upstream-interface-name"),
                    "downstream_interfaces": downstream,
                    "forwarding_rate_pps": _int(
                        _localname_text(route_node, "forwarding-rate-packets")
                    ),
                    "uptime_seconds": _seconds_attr(uptime),
                    "state": _localname_text(route_node, "multicast-route-state"),
                    "forwarding_state": _localname_text(
                        route_node, "multicast-route-forwarding-state"
                    ),
                }
            if routes:
                tables.setdefault(instance, {}).update(routes)
        return tables


@register
class MvpnInstanceCollector(Collector):
    name = "mvpn_instance"

    def rpc_name(self, platform: str) -> str:
        return "get_mvpn_instance_information"

    def rpc_kwargs(self, platform: str) -> dict[str, Any]:
        return {"inet": True}

    def parse(self, xml: etree._Element, platform: str) -> dict[str, dict[str, Any]]:
        instances: dict[str, dict[str, Any]] = {}
        for entry_node in xml.iter("{*}instance-entry"):
            name = _localname_text(entry_node, "instance-name")
            if not name:
                continue
            c_multicast = []
            for c_node in entry_node.iter("{*}c-multicast-ipv4-entry"):
                address = _localname_text(c_node, "c-multicast-address")
                if not address or ":" not in address:
                    continue
                source_prefix, group_prefix = address.split(":", 1)
                tunnel = _localname_text(c_node, "provider-tunnel-id")
                c_multicast.append({
                    "source_prefix": source_prefix,
                    "group_prefix": group_prefix,
                    "provider_tunnel_id": tunnel,
                    "sender_pe": parse_sender_pe(tunnel),
                })
            # Instance ve vypisu bez c-multicast si klic necha - check
            # rozlisuje "instance neni v mvpn vypisu" od "chybi c-multicast".
            instances[name] = {"c_multicast": c_multicast}
        return instances
